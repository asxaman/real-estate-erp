"""Ledger reports. Every number here is calculated from posted journal lines
and nothing else, so all reports always agree with each other."""

from collections import defaultdict

from django.db.models import Count, Sum

from .models import ZERO, Account, JournalEntry, JournalLine, NumberSequence

PARTY_FIELDS = {"tenant": "tenant_id", "owner": "owner_id", "vendor": "vendor_id"}
LINE_LIMIT = 5000


def posted_lines():
    return JournalLine.objects.filter(entry__status=JournalEntry.Status.POSTED)


def trial_balance(as_of, include_zero=False):
    """All accounts with their balance on `as_of`, split into debit / credit columns."""
    sums = {
        row["account_id"]: (row["d"] or ZERO, row["c"] or ZERO)
        for row in posted_lines()
        .filter(entry__date__lte=as_of)
        .values("account_id")
        .annotate(d=Sum("debit"), c=Sum("credit"))
    }
    rows = []
    total_debit = total_credit = ZERO
    for account in Account.objects.filter(is_header=False).order_by("code"):
        debit, credit = sums.get(account.pk, (ZERO, ZERO))
        net = debit - credit
        if net == 0 and not include_zero and account.pk not in sums:
            continue
        col_debit = net if net > 0 else ZERO
        col_credit = -net if net < 0 else ZERO
        total_debit += col_debit
        total_credit += col_credit
        rows.append({"account": account, "debit": col_debit, "credit": col_credit})
    return {
        "as_of": as_of,
        "rows": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "difference": total_debit - total_credit,
        "balanced": total_debit == total_credit,
    }


def _party_filter(party):
    if not party:
        return {}
    kind, pk = party
    if kind not in PARTY_FIELDS:
        raise ValueError("party kind must be tenant, owner or vendor")
    return {PARTY_FIELDS[kind]: pk}


def general_ledger(account, date_from=None, date_to=None, party=None):
    """Opening balance, every line and a running balance for one account.
    Balances are shown in the account's natural direction."""
    base = posted_lines().filter(account=account, **_party_filter(party))
    opening = ZERO
    if date_from:
        before = base.filter(entry__date__lt=date_from).aggregate(d=Sum("debit"), c=Sum("credit"))
        opening = account.signed(before["d"] or ZERO, before["c"] or ZERO)
    queryset = base
    if date_from:
        queryset = queryset.filter(entry__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(entry__date__lte=date_to)
    queryset = queryset.select_related("entry", "tenant", "owner", "vendor").order_by(
        "entry__date", "entry_id", "id"
    )
    balance = opening
    rows = []
    total_debit = total_credit = ZERO
    for line in queryset:
        balance += account.signed(line.debit, line.credit)
        total_debit += line.debit
        total_credit += line.credit
        rows.append({"line": line, "balance": balance})
    truncated = len(rows) > LINE_LIMIT
    return {
        "account": account,
        "opening": opening,
        "closing": balance,
        "rows": rows[:LINE_LIMIT],
        "truncated": truncated,
        "total_debit": total_debit,
        "total_credit": total_credit,
    }


def party_balances(control_account, as_of=None):
    """The sub-ledger: what each tenant / owner / vendor has on a control account."""
    kind = control_account.control_kind
    if not kind:
        raise ValueError("not a control account")
    field = PARTY_FIELDS[kind]
    qs = posted_lines().filter(account=control_account)
    if as_of:
        qs = qs.filter(entry__date__lte=as_of)
    totals = defaultdict(lambda: ZERO)
    for row in qs.values(field).annotate(d=Sum("debit"), c=Sum("credit")):
        totals[row[field]] += control_account.signed(row["d"] or ZERO, row["c"] or ZERO)
    model = {
        "tenant": JournalLine._meta.get_field("tenant").related_model,
        "owner": JournalLine._meta.get_field("owner").related_model,
        "vendor": JournalLine._meta.get_field("vendor").related_model,
    }[kind]
    names = model.objects.in_bulk([pk for pk in totals if pk is not None])
    result = []
    for pk, balance in totals.items():
        result.append({"party": names.get(pk), "party_id": pk, "balance": balance})
    result.sort(key=lambda r: (r["party"] is None, str(r["party"] or "")))
    return result


def reconciliation(as_of=None):
    """Compare every control account with the sum of its sub-ledger."""
    out = []
    for account in Account.objects.exclude(control_kind="").order_by("code"):
        qs = posted_lines().filter(account=account)
        if as_of:
            qs = qs.filter(entry__date__lte=as_of)
        agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
        gl = account.signed(agg["d"] or ZERO, agg["c"] or ZERO)
        parties = party_balances(account, as_of)
        subtotal = sum((p["balance"] for p in parties if p["party_id"] is not None), ZERO)
        unassigned = sum((p["balance"] for p in parties if p["party_id"] is None), ZERO)
        out.append(
            {
                "account": account,
                "gl_balance": gl,
                "subledger_total": subtotal,
                "unassigned": unassigned,
                "parties": len([p for p in parties if p["balance"] != 0]),
                "ok": gl == subtotal and unassigned == 0,
            }
        )
    return out


def ledger_health():
    """Run every integrity check on the books. Returns a list of problems
    (an empty list means the ledger is healthy)."""
    problems = []

    totals = posted_lines().aggregate(d=Sum("debit"), c=Sum("credit"))
    if (totals["d"] or ZERO) != (totals["c"] or ZERO):
        problems.append(
            f"Total debits ({totals['d'] or ZERO:,.2f}) do not equal total credits ({totals['c'] or ZERO:,.2f})."
        )

    per_entry = (
        posted_lines()
        .values("entry__number")
        .annotate(d=Sum("debit"), c=Sum("credit"), n=Count("id"))
    )
    for row in per_entry:
        if row["d"] != row["c"] or row["n"] < 2:
            problems.append(f"Entry {row['entry__number']} is not balanced or has fewer than two lines.")

    empty = JournalEntry.objects.filter(status=JournalEntry.Status.POSTED, lines__isnull=True)
    for entry in empty:
        problems.append(f"Entry {entry.number} has no lines.")

    for row in reconciliation():
        if not row["ok"]:
            problems.append(
                f"Control account {row['account']}: ledger {row['gl_balance']:,.2f} but "
                f"sub-ledger {row['subledger_total']:,.2f} (unassigned {row['unassigned']:,.2f})."
            )

    from .models import LeaseInvoice, ReceiptVoucher

    document_models = {"INV": LeaseInvoice, "RV": ReceiptVoucher}
    for seq in NumberSequence.objects.all():
        prefix = seq.key.rsplit("-", 1)[0]
        if prefix in document_models:
            used = document_models[prefix].objects.filter(number__startswith=seq.key + "-").count()
        else:
            used = JournalEntry.objects.filter(number__startswith=seq.key + "-").count()
        if used != seq.last_number:
            problems.append(
                f"Numbering {seq.key}: counter is at {seq.last_number} but {used} documents exist (a gap)."
            )

    return problems


__all__ = [
    "trial_balance",
    "general_ledger",
    "party_balances",
    "reconciliation",
    "ledger_health",
]
