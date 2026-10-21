"""The automatic postings: how lease invoices, cheques and month-end releases
become journal entries.

How the money moves (AED):

* Lease invoice (once per lease):
    company-owned property:  Dr Tenant receivable (total)
                             Cr Deferred rent revenue (rent)   Cr Output VAT not yet due (VAT)
    third-party property:    Dr Tenant receivable (total)
                             Cr Owner rent - not yet collected (total; any VAT is the owner's)
* Cheque received:           Dr Cheques in hand            Cr Tenant receivable
* Cheque deposited:          Dr Cheques under collection   Cr Cheques in hand
* Cheque cleared:            Dr Bank                       Cr Cheques under collection
    (third-party also:       Dr Owner rent - not yet collected   Cr Owner payable
                             Dr Owner payable (fee + VAT)  Cr Fee income, Cr VAT payable)
* Cheque bounced:            Dr Tenant receivable          Cr Cheques under collection
* Cash / card / transfer:    Dr Bank   Cr Tenant receivable   (no cheque steps)
* VAT on company-owned rent: Dr Output VAT not yet due     Cr VAT payable
    (when the instalment is due or paid, whichever is first)
* Month end:                 Dr Deferred rent revenue      Cr Rent income
    (exact days of the lease that fall in the month)

The postings follow the cheque's status by themselves. They are idempotent: running
them again changes nothing. If a posting cannot be made (for example the month is
locked) the cheque is saved anyway and the item waits on the "Daily postings" page.
"""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from realestate.models import Cheque, CompanySettings, Lease, Owner, Property
from realestate.utils import percent_of, prorate, split_vat

from .context import current_user
from .models import (
    ZERO,
    AccountingSettings,
    AuditLog,
    JournalEntry,
    JournalLine,
    LeaseInvoice,
    NumberSequence,
    ReceiptVoucher,
    posting_account,
)
from .posting import LineSpec, post_entry, reverse_entry

CENT = Decimal("0.01")
CHEQUE_MODEL = "realestate.Cheque"
LEASE_MODEL = "realestate.Lease"
PATH_ORDER = ["received", "deposited", "cleared", "bounced"]


def cutover_date():
    return AccountingSettings.objects.filter(pk=1).values_list("cutover_date", flat=True).first()


def ledger_on():
    """The ledger only starts posting once a cutover date is set."""
    return cutover_date() is not None


def is_third_party(lease):
    return lease.unit.property.ownership_type == Property.Ownership.THIRD_PARTY


# --- finding what is already posted ------------------------------------------


def _active_entries(model_label, object_id, tag=None, tag_prefix=None):
    """Posted system entries for a document that have not been reversed."""
    reversed_ids = JournalEntry.objects.filter(reverses__isnull=False).values("reverses_id")
    qs = JournalEntry.objects.filter(
        source=JournalEntry.Source.SYSTEM,
        status=JournalEntry.Status.POSTED,
        source_model=model_label,
        source_id=object_id,
    ).exclude(pk__in=reversed_ids)
    if tag:
        qs = qs.filter(source_tag=tag)
    if tag_prefix:
        qs = qs.filter(source_tag__startswith=tag_prefix)
    return qs


def _active_path(cheque):
    return [t for t in PATH_ORDER if _active_entries(CHEQUE_MODEL, cheque.pk, tag=t).exists()]


def _desired_path(cheque, active):
    S = Cheque.Status
    status = cheque.status
    if cheque.method == Cheque.Method.CHEQUE:
        if status == S.RECEIVED:
            return ["received"]
        if status == S.DEPOSITED:
            return ["received", "deposited"]
        if status == S.CLEARED:
            return ["received", "deposited", "cleared"]
        if status in (S.RETURNED, S.REPLACED):
            return ["received", "deposited", "bounced"]
        if status == S.CANCELLED:
            return list(active) if "bounced" in active else []
        return []
    # cash, card, transfer: a single step when it is paid
    return ["cleared"] if status == S.CLEARED else []


def _vat_released(cheque):
    return _active_entries(CHEQUE_MODEL, cheque.pk, tag="vat_release").exists()


def _needs_vat_release(cheque, as_of=None):
    """Company-owned rent with VAT: due once the instalment date has come or it is paid."""
    lease = cheque.lease
    if is_third_party(lease) or not lease.vat_percent or lease.vat_percent <= 0:
        return False
    if cheque.status == Cheque.Status.CANCELLED or _vat_released(cheque):
        return False
    paid = cheque.status == Cheque.Status.CLEARED
    due = as_of is not None and cheque.cheque_date <= as_of
    return paid or due


def plan_cheque(cheque):
    """What still has to be posted for this cheque. A list of (action, tag);
    action is "post" or "reverse". Empty means the ledger is up to date."""
    if not ledger_on() or cheque.purpose != Cheque.Purpose.RENT:
        return []
    lease = cheque.lease
    if not LeaseInvoice.objects.filter(lease=lease).exists():
        return []
    active = _active_path(cheque)
    desired = _desired_path(cheque, active)
    common = 0
    while common < len(active) and common < len(desired) and active[common] == desired[common]:
        common += 1
    steps = [("reverse", tag) for tag in reversed(active[common:])]
    steps += [("post", tag) for tag in desired[common:]]
    if _needs_vat_release(cheque):
        steps.append(("post", "vat_release"))
    return steps


def describe_step(action, tag):
    names = {
        "received": "cheque received",
        "deposited": "cheque deposited",
        "cleared": "payment cleared",
        "bounced": "cheque bounced",
        "vat_release": "VAT now due",
    }
    return ("Undo: " if action == "reverse" else "") + names.get(tag, tag)


# --- helpers for building entries -------------------------------------------


def _stage_date(cheque, tag):
    stage = {
        "received": cheque.received_date,
        "deposited": cheque.deposited_date,
        "cleared": cheque.cleared_date,
        "bounced": cheque.returned_date,
    }.get(tag)
    return stage or timezone.localdate()


def _entry_day(cheque, tag, invoice):
    return max(_stage_date(cheque, tag), invoice.date)


def owner_on(prop, day):
    """The owner who owned a third-party property on a given day."""
    if prop.ownership_type != Property.Ownership.THIRD_PARTY:
        return None
    ids = {prop.owner_id} | {p.owner_id for p in prop.past_owners.all()}
    for owner_id in ids:
        if owner_id and prop.owned_by_on(owner_id, day):
            return Owner.objects.get(pk=owner_id)
    return None


def _fee_for(prop, owner, net_rent, day):
    """(management fee, VAT on the fee) the way owner statements work out the fee."""
    if prop.owner_id == owner.pk and not prop.fee_applies_on(day):
        return ZERO, ZERO
    if prop.owner_id == owner.pk:
        percent = prop.effective_fee_percent or Decimal("0")
    else:
        percent = Decimal(owner.management_fee_percent or 0)
    fee = percent_of(net_rent, percent)
    vat = percent_of(fee, CompanySettings.load().fee_vat_percent) if fee > 0 else ZERO
    return fee, vat


def _label(cheque):
    if cheque.method == Cheque.Method.CHEQUE:
        return f"cheque {cheque.cheque_number or 'no number'}"
    return cheque.get_method_display().lower()


# --- the lease invoice -------------------------------------------------------


def invoice_problems(lease):
    """Why this lease cannot be invoiced yet, or an empty list."""
    problems = []
    cutover = cutover_date()
    if cutover is None:
        problems.append("Set the cutover date in Accounting settings first.")
    elif lease.start_date < cutover:
        problems.append(
            f"The lease started before the cutover date ({cutover:%d %b %Y}). Its balances come in with the opening balances."
        )
    if LeaseInvoice.objects.filter(lease=lease).exists():
        problems.append("This lease already has an invoice.")
    if not lease.annual_rent or lease.annual_rent <= 0:
        problems.append("The rent is zero.")
    return problems


def create_lease_invoice(lease, day=None, user=None):
    """Issue the master invoice and post it. Returns (invoice, errors from
    posting the cheques already received)."""
    user = user or current_user()
    problems = invoice_problems(lease)
    if problems:
        raise ValidationError(problems)
    day = day or max(lease.start_date, cutover_date())
    rent_total = prorate(lease.annual_rent, lease.term_months)
    total = lease.contract_total
    vat_total = total - rent_total
    third = is_third_party(lease)
    receivable = posting_account("receivable")

    lines = [LineSpec(receivable, debit=total, tenant=lease.tenant, description="Lease invoice")]
    if third:
        lines.append(LineSpec(posting_account("owner_rent_pending"), credit=total, description="Rent for the owner"))
    else:
        lines.append(LineSpec(posting_account("deferred_rent"), credit=rent_total, description="Rent (deferred)"))
        if vat_total > 0:
            lines.append(LineSpec(posting_account("output_vat_pending"), credit=vat_total, description="VAT (not yet due)"))

    with transaction.atomic():
        number = NumberSequence.next("INV", day.year)
        entry = post_entry(
            date=day,
            memo=f"{'Tax invoice' if vat_total > 0 else 'Invoice'} {number}: {lease.tenant.name}, unit {lease.unit.unit_number}",
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
            rent_amount=rent_total,
            vat_percent=lease.vat_percent or ZERO,
            vat_amount=vat_total,
            total_amount=total,
            entry=entry,
            created_by=user,
        )
    errors = []
    for cheque in lease.cheques.filter(purpose=Cheque.Purpose.RENT):
        try:
            sync_cheque(cheque, user=user)
        except ValidationError as exc:
            errors.append(f"{cheque}: {' '.join(exc.messages)}")
    return invoice, errors


# --- cheque postings ---------------------------------------------------------


def _stage_lines(cheque, tag, invoice):
    lease = cheque.lease
    tenant = lease.tenant
    amount = cheque.amount.quantize(CENT, ROUND_HALF_UP)
    is_cheque = cheque.method == Cheque.Method.CHEQUE
    receivable = posting_account("receivable")

    if tag == "received":
        return [
            LineSpec(posting_account("pdc_in_hand"), debit=amount, description=_label(cheque)),
            LineSpec(receivable, credit=amount, tenant=tenant, description=_label(cheque)),
        ]
    if tag == "deposited":
        return [
            LineSpec(posting_account("pdc_collection"), debit=amount, description=_label(cheque)),
            LineSpec(posting_account("pdc_in_hand"), credit=amount, description=_label(cheque)),
        ]
    if tag == "bounced":
        return [
            LineSpec(receivable, debit=amount, tenant=tenant, description=_label(cheque) + " returned"),
            LineSpec(posting_account("pdc_collection"), credit=amount, description=_label(cheque) + " returned"),
        ]
    if tag == "cleared":
        prop = lease.unit.property
        third = prop.ownership_type == Property.Ownership.THIRD_PARTY
        bank = posting_account("bank_trust" if third else "bank_operating")
        lines = [LineSpec(bank, debit=amount, description=_label(cheque))]
        if is_cheque:
            lines.append(LineSpec(posting_account("pdc_collection"), credit=amount, description=_label(cheque)))
        else:
            lines.append(LineSpec(receivable, credit=amount, tenant=tenant, description=_label(cheque)))
        if third:
            day = _entry_day(cheque, "cleared", invoice)
            owner = owner_on(prop, day)
            if owner is None:
                raise ValidationError(f"No owner is recorded for {prop.name} on {day:%d %b %Y}.")
            net, _vat = split_vat(amount, lease.vat_percent)
            fee, fee_vat = _fee_for(prop, owner, net, day)
            pending = posting_account("owner_rent_pending")
            payable = posting_account("owner_payable")
            lines.append(LineSpec(pending, debit=amount, description="Owner rent collected"))
            lines.append(LineSpec(payable, credit=amount, owner=owner, description="Owner rent collected"))
            if fee > 0:
                lines.append(LineSpec(payable, debit=fee + fee_vat, owner=owner, description="Management fee"))
                lines.append(LineSpec(posting_account("fee_income"), credit=fee, description="Management fee"))
                if fee_vat > 0:
                    lines.append(LineSpec(posting_account("vat_payable"), credit=fee_vat, description="VAT on management fee"))
        return lines
    raise ValueError(f"unknown stage {tag}")


def _post_stage(cheque, tag, invoice, user):
    lines = _stage_lines(cheque, tag, invoice)
    day = _entry_day(cheque, tag, invoice)
    words = {
        "received": "received",
        "deposited": "deposited at the bank",
        "cleared": "cleared" if cheque.method == Cheque.Method.CHEQUE else "paid",
        "bounced": "returned by the bank",
    }[tag]
    tenant = cheque.lease.tenant
    entry = post_entry(
        date=day,
        memo=f"{_label(cheque).capitalize()} {words}: {tenant.name}, AED {cheque.amount:,.2f}",
        lines=lines,
        source=JournalEntry.Source.SYSTEM,
        reference=(cheque.cheque_number or cheque.reference or "")[:100],
        source_model=CHEQUE_MODEL,
        source_id=cheque.pk,
        source_tag=tag,
        user=user,
    )
    is_cheque = cheque.method == Cheque.Method.CHEQUE
    if (tag == "received" and is_cheque) or (tag == "cleared" and not is_cheque):
        ReceiptVoucher.objects.create(
            number=NumberSequence.next("RV", day.year),
            cheque=cheque,
            entry=entry,
            date=day,
            amount=cheque.amount,
        )
    return entry


def _reverse_stage(cheque, tag, user):
    entry = _active_entries(CHEQUE_MODEL, cheque.pk, tag=tag).first()
    if entry is None:
        return None
    day = max(timezone.localdate(), entry.date)
    reversal = reverse_entry(entry, date=day, reason="the payment status was changed", user=user)
    ReceiptVoucher.objects.filter(entry=entry).update(is_void=True)
    return reversal


def _post_vat_release(cheque, day, user):
    lease = cheque.lease
    _net, vat = split_vat(cheque.amount, lease.vat_percent)
    if vat <= 0:
        return None
    return post_entry(
        date=day,
        memo=f"VAT now due on {_label(cheque)}: {lease.tenant.name}",
        lines=[
            LineSpec(posting_account("output_vat_pending"), debit=vat, description="VAT now due"),
            LineSpec(posting_account("vat_payable"), credit=vat, description="VAT now due"),
        ],
        source=JournalEntry.Source.SYSTEM,
        source_model=CHEQUE_MODEL,
        source_id=cheque.pk,
        source_tag="vat_release",
        user=user,
    )


def sync_cheque(cheque, user=None):
    """Bring the ledger in line with the cheque's status. Returns how many
    steps were posted. Raises ValidationError if a step cannot be posted."""
    user = user or current_user()
    steps = plan_cheque(cheque)
    if not steps:
        return 0
    invoice = LeaseInvoice.objects.get(lease=cheque.lease)
    done = 0
    for action, tag in steps:
        with transaction.atomic():
            if action == "reverse":
                _reverse_stage(cheque, tag, user)
            elif tag == "vat_release":
                day = max(_stage_date(cheque, "cleared") if cheque.status == Cheque.Status.CLEARED else cheque.cheque_date, invoice.date)
                _post_vat_release(cheque, day, user)
            else:
                _post_stage(cheque, tag, invoice, user)
        done += 1
    return done


# --- month end: release deferred rent and VAT --------------------------------


def month_end(year, month):
    return date(year, month, calendar.monthrange(year, month)[1])


def _released_rent(lease):
    entries = _active_entries(LEASE_MODEL, lease.pk, tag_prefix="release:")
    total = JournalLine.objects.filter(
        entry__in=entries, account=posting_account("deferred_rent")
    ).aggregate(t=Sum("debit"))["t"]
    return total or ZERO


def rent_release_due(invoice, as_of_month_end):
    """How much deferred rent should be released now, counting the exact days of
    the lease up to `as_of_month_end` (company-owned leases only)."""
    lease = invoice.lease
    if is_third_party(lease) or as_of_month_end < invoice.date:
        return ZERO
    total_days = (lease.end_date - lease.start_date).days + 1
    upto = min(as_of_month_end, lease.effective_end_date)
    elapsed = max(0, (upto - lease.start_date).days + 1)
    if elapsed >= total_days:
        target = invoice.rent_amount
    else:
        target = (invoice.rent_amount * elapsed / total_days).quantize(CENT, ROUND_HALF_UP)
    return max(target - _released_rent(lease), ZERO)


def run_month_end(year, month, user=None):
    """Release the rent earned up to the end of the month, and mark VAT as due
    for instalments whose date has come. Safe to run again."""
    user = user or current_user()
    end = month_end(year, month)
    tag = f"release:{year}-{month:02d}"
    result = {"rent_entries": 0, "rent_total": ZERO, "vat_entries": 0, "errors": []}
    invoices = LeaseInvoice.objects.select_related("lease__unit__property", "lease__tenant")
    for invoice in invoices:
        lease = invoice.lease
        if is_third_party(lease):
            continue
        try:
            with transaction.atomic():
                amount = ZERO
                if not _active_entries(LEASE_MODEL, lease.pk, tag=tag).exists():
                    amount = rent_release_due(invoice, end)
                if amount > 0:
                    post_entry(
                        date=end,
                        memo=f"Rent earned in {end:%B %Y}: {lease.tenant.name}, unit {lease.unit.unit_number}",
                        lines=[
                            LineSpec(posting_account("deferred_rent"), debit=amount, description=f"Rent {end:%b %Y}"),
                            LineSpec(posting_account("rent_income"), credit=amount, description=f"Rent {end:%b %Y}"),
                        ],
                        source=JournalEntry.Source.SYSTEM,
                        source_model=LEASE_MODEL,
                        source_id=lease.pk,
                        source_tag=tag,
                        user=user,
                    )
                    result["rent_entries"] += 1
                    result["rent_total"] += amount
        except ValidationError as exc:
            result["errors"].append(f"{lease}: {' '.join(exc.messages)}")
        for cheque in lease.cheques.filter(purpose=Cheque.Purpose.RENT):
            if _needs_vat_release(cheque, as_of=end):
                try:
                    with transaction.atomic():
                        _post_vat_release(cheque, max(end, invoice.date), user)
                        result["vat_entries"] += 1
                except ValidationError as exc:
                    result["errors"].append(f"{cheque}: {' '.join(exc.messages)}")
    AuditLog.record(
        "month_end",
        user=user,
        detail=f"Month-end run for {end:%B %Y}: {result['rent_entries']} rent release(s) "
        f"totalling {result['rent_total']:,.2f}, {result['vat_entries']} VAT release(s), {len(result['errors'])} problem(s).",
    )
    return result


# --- what is waiting -----------------------------------------------------------


def waiting_items(limit=500):
    """Everything that is not posted yet, for the Daily postings page."""
    cutover = cutover_date()
    info = {"cutover": cutover, "to_invoice": [], "before_cutover": 0, "cheques": [], "other_purposes": 0}
    if cutover is None:
        return info
    leases = Lease.objects.filter(status=Lease.Status.ACTIVE, invoice__isnull=True, annual_rent__gt=0)
    info["to_invoice"] = list(
        leases.filter(start_date__gte=cutover).select_related("tenant", "unit__property").order_by("start_date")[:limit]
    )
    info["before_cutover"] = leases.filter(start_date__lt=cutover).count()
    cheques = Cheque.objects.filter(
        purpose=Cheque.Purpose.RENT, lease__invoice__isnull=False
    ).select_related("lease__tenant", "lease__unit__property")
    for cheque in cheques.order_by("cheque_date", "id"):
        steps = plan_cheque(cheque)
        if steps:
            info["cheques"].append({"cheque": cheque, "steps": [describe_step(a, t) for a, t in steps]})
            if len(info["cheques"]) >= limit:
                break
    info["other_purposes"] = (
        Cheque.objects.exclude(purpose=Cheque.Purpose.RENT)
        .filter(
            status__in=[Cheque.Status.RECEIVED, Cheque.Status.DEPOSITED, Cheque.Status.CLEARED],
            lease__start_date__gte=cutover,
        )
        .count()
    )
    return info


def post_all_waiting(user=None):
    """Try to post every waiting cheque step. Returns (posted, errors)."""
    posted, errors = 0, []
    for item in waiting_items()["cheques"]:
        try:
            posted += sync_cheque(item["cheque"], user=user)
        except ValidationError as exc:
            errors.append(f"{item['cheque']}: {' '.join(exc.messages)}")
    return posted, errors
