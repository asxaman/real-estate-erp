"""The rest of the automatic postings.

* A tenant leaving early: the unused rent is taken off the tenant's account.
* The security deposit refund and what is deducted from it.
* Expenses: the vendor's bill, our markup, paying the vendor, the tenant paying back.
* An owner statement being paid: our fee and the payout to the owner.
* Leases that started before the cutover date: their opening balances.
* The list of everything that is waiting.

How the money moves (AED):

* Early exit, step A (cheques handed back): the payment is cancelled, so
      Dr Tenant receivable   Cr PDC receivable        (done by the payment rules)
* Early exit, step B (unused rent): a credit note
      Dr Owner payable (or Rent income) and Dr VAT output payable   Cr Tenant receivable
* Early exit, step C (penalty): invoiced as a fee
      Dr Tenant receivable   Cr Early-termination penalty income
  and paid like any payment (Dr Cash / Bank   Cr Tenant receivable).
* Repair on a third-party unit, with our markup (say 10%):
      Dr Owner payable (cost + markup)   Cr Vendor payable (cost)   Cr Maintenance markup income
* Owner statement paid:
      Dr Owner payable (fee + VAT on the fee)   Cr Management fee income   Cr VAT output payable
      Dr Owner payable (payout)                 Cr Owner trust bank
* Deposit refund at move-out:
      Dr Tenant security deposits   Cr Trust bank (what goes back)
                                    Cr Owner payable (damages for the owner)
"""

import hashlib
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from realestate.models import Cheque, Expense, Lease, OwnerStatement
from realestate.utils import split_vat

from . import rules
from .context import current_user
from .models import (
    ZERO,
    CreditNote,
    JournalEntry,
    LeaseInvoice,
    NumberSequence,
    ReceiptVoucher,
    posting_account,
)
from .posting import LineSpec, post_entry, reverse_entry
from .rules import (
    LEASE_MODEL,
    _active_entries,
    active_invoice,
    blocking_reason,
    cutover_date,
    is_third_party,
    ledger_on,
    owner_or_error,
)

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
EXPENSE_MODEL = "realestate.Expense"
STATEMENT_MODEL = "realestate.OwnerStatement"


def _fingerprint(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _reverse_all(entries, user, reason):
    """Reverse each of these (still active) entries, dated today or later."""
    for entry in list(entries):
        day = max(timezone.localdate(), entry.date)
        reverse_entry(entry, date=day, reason=reason, user=user)
        ReceiptVoucher.objects.filter(entry=entry).update(is_void=True)
        CreditNote.objects.filter(entry=entry).update(is_void=True)


# ---------------------------------------------------------------------------
# Security deposit refund
# ---------------------------------------------------------------------------


def deposit_chain(lease):
    """The lease and the earlier leases its deposit was carried over from."""
    chain, current, seen = [lease], lease, {lease.pk}
    while current.deposit_carried_over and current.renewal_of_id and current.renewal_of_id not in seen:
        current = current.renewal_of
        chain.append(current)
        seen.add(current.pk)
    return chain


def deposit_held_in_books(lease):
    """Deposit money the ledger knows about for this lease: paid on or after the
    cutover date, or paid earlier and brought in by an opening entry."""
    cutover = cutover_date()
    total = ZERO
    for item in deposit_chain(lease):
        opened = LeaseInvoice.objects.filter(lease=item, is_opening=True, is_void=False).exists()
        for cheque in item.cheques.filter(purpose=Cheque.Purpose.SECURITY_DEPOSIT, status=Cheque.Status.CLEARED):
            if cheque.cleared_date and cheque.cleared_date >= cutover:
                total += cheque.amount
            elif opened:
                total += cheque.amount
    return total


def deposit_refund_pending(lease):
    if not ledger_on() or not lease.deposit_refunded_on:
        return False
    if lease.deposit_refunded_on < cutover_date():
        return False
    if _active_entries(LEASE_MODEL, lease.pk, tag="deposit_refund").exists():
        return False
    return deposit_held_in_books(lease) > 0


def sync_deposit_refund(lease, user=None):
    user = user or current_user()
    if not deposit_refund_pending(lease):
        return 0
    day = lease.deposit_refunded_on
    held = deposit_held_in_books(lease)
    deductions = min(lease.deposit_deductions or ZERO, held)
    paid_back = held - deductions
    third = is_third_party(lease)
    lines = [LineSpec(posting_account("security_deposits"), debit=held, description="Deposit settled")]
    if paid_back > 0:
        lines.append(
            LineSpec(rules.bank_account(lease), credit=paid_back, description="Deposit paid back to the tenant")
        )
    if deductions > 0:
        if third:
            owner = owner_or_error(lease.unit.property, day)
            lines.append(
                LineSpec(
                    posting_account("owner_payable"),
                    credit=deductions,
                    owner=owner,
                    description="Damages deducted from the deposit",
                )
            )
        else:
            lines.append(
                LineSpec(posting_account("other_income"), credit=deductions, description="Deducted from the deposit")
            )
    with transaction.atomic():
        post_entry(
            date=day,
            memo=f"Deposit settled: {lease.tenant.name}, unit {lease.unit.unit_number} "
            f"(back to tenant {paid_back:,.2f}, deducted {deductions:,.2f})",
            lines=lines,
            source=JournalEntry.Source.SYSTEM,
            source_model=LEASE_MODEL,
            source_id=lease.pk,
            source_tag="deposit_refund",
            user=user,
        )
    return 1


# ---------------------------------------------------------------------------
# A tenant leaving early: credit note for the unused rent
# ---------------------------------------------------------------------------


def _billable(lease, invoice):
    """Rent payments that still count against the invoice: everything except cancelled
    payments, replacement payments (they settle the one they replace) and history."""
    rows = []
    for cheque in lease.cheques.filter(purpose=Cheque.Purpose.RENT, replaces__isnull=True).exclude(
        status=Cheque.Status.CANCELLED
    ):
        if rules._opening_closed(cheque, invoice):
            continue
        rows.append(cheque)
    return rows


def credit_needed(lease, invoice):
    """(rent part, VAT part) that should be credited to the invoice."""
    billable = sum((c.amount for c in _billable(lease, invoice)), ZERO)
    credit = max(invoice.total_amount - billable, ZERO)
    if credit == 0:
        return ZERO, ZERO
    _net, vat = split_vat(credit, lease.vat_percent)
    return credit - vat, vat


def _credited(invoice):
    notes = CreditNote.objects.filter(invoice=invoice, is_void=False)
    agg = notes.aggregate(t=Sum("total_amount"))
    return notes, agg["t"] or ZERO


def termination_pending(lease):
    if not ledger_on() or lease.status != Lease.Status.TERMINATED or not lease.exit_date:
        return False
    invoice = active_invoice(lease)
    if invoice is None:
        return False
    rent_part, vat_part = credit_needed(lease, invoice)
    _notes, credited = _credited(invoice)
    return credited != rent_part + vat_part


def _issue_credit_note(invoice, rent_part, vat_part, user):
    lease = invoice.lease
    total = rent_part + vat_part
    day = max(timezone.localdate(), invoice.date)
    lines = []
    if is_third_party(lease):
        owner = owner_or_error(lease.unit.property, invoice.date)
        lines.append(
            LineSpec(posting_account("owner_payable"), debit=rent_part, owner=owner, description="Rent no longer due")
        )
    else:
        lines.append(LineSpec(posting_account("rent_income"), debit=rent_part, description="Rent no longer due"))
    if vat_part > 0:
        lines.append(LineSpec(posting_account("vat_payable"), debit=vat_part, description="VAT on rent no longer due"))
    lines.append(LineSpec(posting_account("receivable"), credit=total, tenant=lease.tenant, description="Credit note"))
    number = NumberSequence.next("CN", day.year)
    entry = post_entry(
        date=day,
        memo=f"Credit note {number}: {lease.tenant.name}, unit {lease.unit.unit_number} (left early on {lease.exit_date:%d %b %Y})",
        lines=lines,
        source=JournalEntry.Source.SYSTEM,
        reference=number,
        source_model=LEASE_MODEL,
        source_id=lease.pk,
        source_tag="credit_note",
        user=user,
    )
    return CreditNote.objects.create(
        number=number,
        invoice=invoice,
        lease=lease,
        date=day,
        rent_amount=rent_part,
        vat_amount=vat_part,
        total_amount=total,
        reason=f"Lease ended early on {lease.exit_date:%d %b %Y}",
        entry=entry,
        created_by=user,
    )


def sync_termination(lease, user=None):
    """After an early exit: credit the rent the tenant no longer owes (once the
    cheques that are handed back have been cancelled, the amount follows)."""
    user = user or current_user()
    if not termination_pending(lease):
        return 0
    invoice = active_invoice(lease)
    rent_part, vat_part = credit_needed(lease, invoice)
    notes, _credited_total = _credited(invoice)
    with transaction.atomic():
        _reverse_all([n.entry for n in notes], user, "the early-exit figures changed")
        if rent_part + vat_part > 0:
            _issue_credit_note(invoice, rent_part, vat_part, user)
    return 1


def overpaid_after_exit():
    """Payments already in hand, deposited or cleared for days after a tenant left."""
    S = Cheque.Status
    return list(
        Cheque.objects.filter(
            purpose=Cheque.Purpose.RENT,
            lease__status=Lease.Status.TERMINATED,
            status__in=[S.RECEIVED, S.DEPOSITED, S.CLEARED],
            cheque_date__gt=F("lease__exit_date"),
        )
        .select_related("lease__tenant", "lease__unit")
        .order_by("lease_id", "cheque_date")
    )


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


def _expense_print(expense):
    return _fingerprint(
        expense.bearer,
        expense.amount,
        expense.vat_amount,
        expense.markup_amount,
        expense.expense_date,
        expense.property_id,
        expense.tenant_id,
        expense.vendor_id,
    )


def _vendor_print(expense):
    return _fingerprint("V", expense.vendor_id, expense.amount, expense.vendor_paid_on, expense.bearer)


def _recovery_print(expense):
    return _fingerprint("R", expense.bearer, expense.recovered_amount, expense.recovered_on, expense.tenant_id)


def plan_expense(expense):
    cutover = cutover_date()
    if cutover is None or not expense.pk:
        return []
    wanted = not expense.is_clawback and expense.expense_date >= cutover
    spent = _active_entries(EXPENSE_MODEL, expense.pk, tag="expense").first()
    vendor_paid = _active_entries(EXPENSE_MODEL, expense.pk, tag="vendor_paid").first()
    repaid = _active_entries(EXPENSE_MODEL, expense.pk, tag="recovery").first()
    steps = []
    changed = spent is not None and (not wanted or spent.reference != _expense_print(expense))
    want_vendor = wanted and expense.vendor_id is not None and expense.vendor_paid_on is not None
    if vendor_paid is not None and (changed or not want_vendor or vendor_paid.reference != _vendor_print(expense)):
        steps.append(("reverse", "vendor_paid"))
    want_back = wanted and expense.bearer == Expense.Bearer.TENANT and (expense.recovered_amount or ZERO) > 0
    if repaid is not None and (changed or not want_back or repaid.reference != _recovery_print(expense)):
        steps.append(("reverse", "recovery"))
    if changed:
        steps.append(("reverse", "expense"))
    if wanted and (spent is None or changed):
        steps.append(("post", "expense"))
    if want_vendor and (vendor_paid is None or changed or vendor_paid.reference != _vendor_print(expense)):
        steps.append(("post", "vendor_paid"))
    if want_back and (repaid is None or changed or repaid.reference != _recovery_print(expense)):
        steps.append(("post", "recovery"))
    return steps


def _pay_side(expense, amount, description):
    """Who we owe: the vendor (a bill to pay later) or, with no vendor, the bank."""
    if expense.vendor_id:
        return LineSpec(
            posting_account("vendor_payable"), credit=amount, vendor=expense.vendor, description=description
        )
    key = "bank_trust" if expense.bearer == Expense.Bearer.OWNER else "bank_operating"
    return LineSpec(posting_account(key), credit=amount, description=description)


def _post_expense(expense, user):
    prop = expense.property
    day = expense.expense_date
    amount = expense.amount.quantize(CENT, ROUND_HALF_UP)
    vat = (expense.vat_amount or ZERO).quantize(CENT, ROUND_HALF_UP)
    what = f"{expense.description} ({prop.name})"
    if expense.bearer == Expense.Bearer.OWNER:
        owner = owner_or_error(prop, day)
        markup = expense.markup_amount
        lines = [
            LineSpec(
                posting_account("owner_payable"), debit=amount + markup, owner=owner, description=expense.description
            ),
            _pay_side(expense, amount, expense.description),
        ]
        if markup > 0:
            lines.append(
                LineSpec(posting_account("maintenance_income"), credit=markup, description="Markup on " + expense.description)
            )
    elif expense.bearer == Expense.Bearer.TENANT:
        if expense.tenant_id is None:
            raise ValidationError("Choose the tenant to charge, so the amount can be collected from them.")
        lines = [
            LineSpec(posting_account("receivable"), debit=amount, tenant=expense.tenant, description=expense.description),
            _pay_side(expense, amount, expense.description),
        ]
    else:
        lines = [LineSpec(posting_account("expense_company"), debit=amount - vat, description=expense.description)]
        if vat > 0:
            lines.append(LineSpec(posting_account("input_vat"), debit=vat, description="VAT on " + expense.description))
        lines.append(_pay_side(expense, amount, expense.description))
    post_entry(
        date=day,
        memo=f"Expense: {what}, AED {amount:,.2f}",
        lines=lines,
        source=JournalEntry.Source.SYSTEM,
        reference=_expense_print(expense),
        source_model=EXPENSE_MODEL,
        source_id=expense.pk,
        source_tag="expense",
        user=user,
    )


def _post_vendor_paid(expense, user):
    amount = expense.amount.quantize(CENT, ROUND_HALF_UP)
    day = max(expense.vendor_paid_on, expense.expense_date)
    key = "bank_trust" if expense.bearer == Expense.Bearer.OWNER else "bank_operating"
    post_entry(
        date=day,
        memo=f"Vendor paid: {expense.vendor.name}, {expense.description}, AED {amount:,.2f}",
        lines=[
            LineSpec(posting_account("vendor_payable"), debit=amount, vendor=expense.vendor, description=expense.description),
            LineSpec(posting_account(key), credit=amount, description=expense.description),
        ],
        source=JournalEntry.Source.SYSTEM,
        reference=_vendor_print(expense),
        source_model=EXPENSE_MODEL,
        source_id=expense.pk,
        source_tag="vendor_paid",
        user=user,
    )


def _post_recovery(expense, user):
    amount = expense.recovered_amount.quantize(CENT, ROUND_HALF_UP)
    day = max(expense.recovered_on or timezone.localdate(), expense.expense_date)
    post_entry(
        date=day,
        memo=f"Tenant paid back: {expense.description}, AED {amount:,.2f}",
        lines=[
            LineSpec(posting_account("bank_operating"), debit=amount, description=expense.description),
            LineSpec(posting_account("receivable"), credit=amount, tenant=expense.tenant, description=expense.description),
        ],
        source=JournalEntry.Source.SYSTEM,
        reference=_recovery_print(expense),
        source_model=EXPENSE_MODEL,
        source_id=expense.pk,
        source_tag="recovery",
        user=user,
    )


def sync_expense(expense, user=None):
    user = user or current_user()
    done = 0
    for action, tag in plan_expense(expense):
        with transaction.atomic():
            if action == "reverse":
                _reverse_all(_active_entries(EXPENSE_MODEL, expense.pk, tag=tag), user, "the expense was changed")
            elif tag == "expense":
                _post_expense(expense, user)
            elif tag == "vendor_paid":
                _post_vendor_paid(expense, user)
            else:
                _post_recovery(expense, user)
        done += 1
    return done


def unpost_expense(expense, user=None):
    """Take a deleted expense out of the books. Raises ValidationError if it cannot."""
    user = user or current_user()
    if not expense.pk:
        return
    entries = _active_entries(EXPENSE_MODEL, expense.pk)
    if entries.exists():
        with transaction.atomic():
            _reverse_all(entries, user, "the expense was deleted")


# ---------------------------------------------------------------------------
# Owner statement paid
# ---------------------------------------------------------------------------


def _statement_lines(statement):
    """The lines to post when the statement is paid (empty if there is nothing to move)."""
    owner = statement.owner
    totals = statement.totals()
    payable = posting_account("owner_payable")
    lines = []
    fee, fee_vat = totals["fee"], totals["fee_vat"]
    if fee > 0:
        lines.append(LineSpec(payable, debit=fee + fee_vat, owner=owner, description="Management fee"))
        lines.append(LineSpec(posting_account("fee_income"), credit=fee, description="Management fee"))
        if fee_vat > 0:
            lines.append(LineSpec(posting_account("vat_payable"), credit=fee_vat, description="VAT on management fee"))
    net = totals["net_payout"]
    if net > 0:
        # VAT paid out to the owner was never in the owner's account: it comes from VAT payable.
        vat_out = min(totals["vat_to_owner"], net)
        if net - vat_out > 0:
            lines.append(
                LineSpec(payable, debit=net - vat_out, owner=owner, description=f"Paid on {statement.number}")
            )
        if vat_out > 0:
            lines.append(LineSpec(posting_account("vat_payable"), debit=vat_out, description="VAT paid to the owner"))
        lines.append(LineSpec(posting_account("bank_trust"), credit=net, description=f"Paid on {statement.number}"))
    return lines


def statement_pending(statement):
    if not ledger_on() or statement.status != OwnerStatement.Status.PAID or not statement.paid_on:
        return False
    if statement.paid_on < cutover_date():
        return False
    if _active_entries(STATEMENT_MODEL, statement.pk, tag="paid").exists():
        return False
    return bool(_statement_lines(statement))


def sync_statement(statement, user=None):
    user = user or current_user()
    if not statement_pending(statement):
        return 0
    lines = _statement_lines(statement)
    net = statement.totals()["net_payout"]
    with transaction.atomic():
        post_entry(
            date=statement.paid_on,
            memo=f"Owner statement {statement.number} paid to {statement.owner.name}, AED {max(net, ZERO):,.2f}",
            lines=lines,
            source=JournalEntry.Source.SYSTEM,
            reference=statement.number or "",
            source_model=STATEMENT_MODEL,
            source_id=statement.pk,
            source_tag="paid",
            user=user,
        )
    return 1


# ---------------------------------------------------------------------------
# Leases that started before the cutover date
# ---------------------------------------------------------------------------


def opening_problems(lease):
    problems = []
    cutover = cutover_date()
    if cutover is None:
        problems.append("Set the cutover date in Accounting settings first.")
        return problems
    if lease.start_date >= cutover:
        problems.append("This lease starts on or after the cutover date. Create a normal invoice instead.")
    if lease.status != Lease.Status.ACTIVE or lease.end_date < cutover:
        problems.append("Only a lease that is still running on the cutover date can be brought in.")
    if active_invoice(lease) is not None:
        problems.append("This lease already has an invoice or opening balances.")
    if not lease.annual_rent or lease.annual_rent <= 0:
        problems.append("The rent is zero.")
    return problems


def opening_numbers(lease, day):
    """What a running lease brings into the books on the cutover date.

    Rent payments that cleared before that day are history (their cash is in the
    opening bank balance). Everything else the tenant still has to pay, or has paid
    by a cheque we still hold, is invoiced on that day. A deposit already held is
    brought in against opening balance equity."""
    included = [
        c
        for c in lease.cheques.filter(purpose=Cheque.Purpose.RENT, replaces__isnull=True).exclude(
            status=Cheque.Status.CANCELLED
        )
        if not (c.status == Cheque.Status.CLEARED and c.cleared_date and c.cleared_date < day)
    ]
    total = sum((c.amount for c in included), ZERO)
    vat = sum((split_vat(c.amount, lease.vat_percent)[1] for c in included), ZERO)
    deposit = ZERO
    for item in deposit_chain(lease):
        for cheque in item.cheques.filter(purpose=Cheque.Purpose.SECURITY_DEPOSIT, status=Cheque.Status.CLEARED):
            if cheque.cleared_date and cheque.cleared_date < day:
                deposit += cheque.amount
    return {"total": total, "vat": vat, "net": total - vat, "deposit": deposit, "cheques": len(included)}


def create_opening_invoice(lease, user=None):
    """Bring a running lease into the books on the cutover date. Returns
    (invoice, errors from posting the payments that come after)."""
    user = user or current_user()
    problems = opening_problems(lease)
    if problems:
        raise ValidationError(problems)
    day = cutover_date()
    n = opening_numbers(lease, day)
    if n["total"] <= 0:
        raise ValidationError(
            "There is nothing to bring in for this lease: every payment was settled before the cutover date."
        )
    lines = rules.invoice_lines(lease, n["total"], n["vat"], day, description="Opening balance")
    if n["deposit"] > 0:
        lines.append(LineSpec(posting_account("opening_equity"), debit=n["deposit"], description="Deposit already held"))
        lines.append(LineSpec(posting_account("security_deposits"), credit=n["deposit"], description="Deposit held"))
    with transaction.atomic():
        number = NumberSequence.next("OBI", day.year)
        entry = post_entry(
            date=day,
            memo=f"Opening balances {number}: {lease.tenant.name}, unit {lease.unit.unit_number}",
            lines=lines,
            source=JournalEntry.Source.SYSTEM,
            reference=number,
            source_model=LEASE_MODEL,
            source_id=lease.pk,
            source_tag="invoice",
            user=user,
        )
        invoice = LeaseInvoice.objects.create(
            number=number,
            lease=lease,
            date=day,
            rent_amount=n["net"],
            vat_percent=lease.vat_percent or ZERO,
            vat_amount=n["vat"],
            total_amount=n["total"],
            entry=entry,
            is_opening=True,
            created_by=user,
        )
    errors = []
    for cheque in lease.cheques.all():
        try:
            rules.sync_cheque(cheque, user=user)
        except ValidationError as exc:
            errors.append(f"{cheque}: {' '.join(exc.messages)}")
    return invoice, errors


# ---------------------------------------------------------------------------
# Everything that is waiting
# ---------------------------------------------------------------------------


def collect_waiting(info, limit):
    """Adds early exits, deposits, expenses and statements that still have to be
    posted (with the reason when the date is blocked)."""
    cutover = info["cutover"]
    for lease in Lease.objects.filter(status=Lease.Status.TERMINATED, invoices__is_void=False).select_related(
        "tenant", "unit__property"
    ).distinct():
        if termination_pending(lease):
            info["terminations"].append(
                {"lease": lease, "why": blocking_reason(max(lease.exit_date, timezone.localdate()))}
            )
    for lease in Lease.objects.filter(deposit_refunded_on__isnull=False).select_related("tenant", "unit__property"):
        if deposit_refund_pending(lease):
            info["deposit_refunds"].append({"lease": lease, "why": blocking_reason(lease.deposit_refunded_on)})
    for expense in Expense.objects.filter(is_clawback=False, expense_date__gte=cutover).select_related(
        "property", "tenant", "vendor"
    ):
        steps = plan_expense(expense)
        if steps:
            info["expenses"].append(
                {
                    "expense": expense,
                    "steps": [rules.describe_step(a, t) for a, t in steps],
                    "why": blocking_reason(expense.expense_date),
                }
            )
            if len(info["expenses"]) >= limit:
                break
    for statement in OwnerStatement.objects.filter(
        status=OwnerStatement.Status.PAID, paid_on__gte=cutover
    ).select_related("owner"):
        if statement_pending(statement):
            info["statements"].append({"statement": statement, "why": blocking_reason(statement.paid_on)})
    info["overpaid"] = overpaid_after_exit()
    return info


def post_waiting_more(info, user=None):
    posted, errors = 0, []
    for item in info["terminations"]:
        try:
            posted += sync_termination(item["lease"], user)
        except ValidationError as exc:
            errors.append(f"Early exit of {item['lease']}: {' '.join(exc.messages)}")
    for item in info["deposit_refunds"]:
        try:
            posted += sync_deposit_refund(item["lease"], user)
        except ValidationError as exc:
            errors.append(f"Deposit of {item['lease']}: {' '.join(exc.messages)}")
    for item in info["expenses"]:
        try:
            posted += sync_expense(item["expense"], user)
        except ValidationError as exc:
            errors.append(f"{item['expense']}: {' '.join(exc.messages)}")
    for item in info["statements"]:
        try:
            posted += sync_statement(item["statement"], user)
        except ValidationError as exc:
            errors.append(f"Statement {item['statement']}: {' '.join(exc.messages)}")
    return posted, errors


WAITING_CACHE_KEY = "accounting_waiting_count"


def waiting_count(force=False):
    """How many postings are waiting. Cached for a minute (it is shown on every page)."""
    if not force:
        cached = cache.get(WAITING_CACHE_KEY)
        if cached is not None:
            return cached
    try:
        info = rules.waiting_items()
        count = info["total"]
    except Exception:
        log.exception("Could not count the waiting postings")
        count = 0
    cache.set(WAITING_CACHE_KEY, count, 60)
    return count
