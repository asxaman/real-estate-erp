"""The automatic postings: how lease invoices and payments become journal entries.

The cycle (AED). Every payment gets a numbered receipt voucher (RV). Where the
debit goes depends on how the tenant pays:

    Cash                 Dr Cash account
    Bank transfer        Dr Owner trust bank (third-party unit) / Operating bank (company unit)
    Card                 Dr Merchant clearing account (moves to the bank when the card
                         company pays out: enter "card money reached the bank on")
    Post-dated cheque    Dr PDC receivable

1. Lease invoice (once per lease, for the whole contract):
       Dr Tenant receivable (total)
       Cr Owner payable (rent)          third-party unit
       Cr Rent income (rent)            company-owned unit
       Cr VAT output payable (VAT)      when the lease has VAT
   The VAT is either added on top of the rent or already inside it (a lease setting).
2. Payment in cash / transfer / card:   Dr Cash, Bank or Merchant clearing   Cr Tenant receivable
3. PDCs recorded:   Dr PDC receivable   Cr Tenant receivable   (the tenant is paid up on paper)
4. Cheque clears:   Dr Bank   Cr PDC receivable        (depositing posts nothing)
5. Cheque bounces:  Dr PDC bounced receivable   Cr PDC receivable
   (also when the bank takes back a cleared cheque: the clearing is reversed first)
6. Bounced cheque replaced (cash, transfer, card or another cheque):
       the replacement credits "PDC bounced receivable" instead of the tenant receivable.
   Fees and penalties are invoiced first (Dr Tenant receivable  Cr Income) and then
   paid like any other payment.
7. Cheque handed back (the payment is cancelled): the "recorded" step is reversed
   (Dr Tenant receivable  Cr PDC receivable), so the tenant owes it again.
8. A security deposit is not rent: Dr (money account)  Cr Tenant security deposits.

Early exit, deposit refund, maintenance with markup, owner statements and opening
balances are in `rules_more.py`.

The postings follow the payment's status by themselves and are idempotent: running
them again changes nothing. If a posting cannot be made (for example the month is
locked) the payment is still saved and the item waits on the "Daily postings" page.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from realestate.models import Cheque, CompanySettings, Lease, Owner, Property
from realestate.utils import split_vat

from .context import current_user
from .models import (
    ZERO,
    AccountingSettings,
    AuditLog,
    CreditNote,
    JournalEntry,
    LeaseInvoice,
    NumberSequence,
    ReceiptVoucher,
    check_posting_date,
    posting_account,
)
from .posting import LineSpec, post_entry, reverse_entry

CENT = Decimal("0.01")
CHEQUE_MODEL = "realestate.Cheque"
LEASE_MODEL = "realestate.Lease"

# The steps a payment can have in the books, in the order they happen.
#   charge        a fee or penalty is invoiced:        Dr Tenant receivable / Cr Income
#   pdc_held      the cheque is recorded:              Dr PDC receivable / Cr Tenant receivable
#   pdc_cleared   the bank paid it:                    Dr Bank / Cr PDC receivable
#   pdc_bounced   the bank returned it:                Dr PDC bounced receivable / Cr PDC receivable
#   money_in      cash, transfer or card is paid:      Dr Cash / Bank / Merchant clearing / Cr Tenant receivable
#   card_settled  the card company paid the bank:      Dr Bank / Cr Merchant clearing
PATH_ORDER = ["charge", "pdc_held", "pdc_cleared", "pdc_bounced", "money_in", "card_settled"]
# Postings made by earlier designs. If one is found it is reversed and posted again.
LEGACY_TAGS = ["received", "deposited", "cleared", "bounced", "paid", "vat_release"]
ALL_TAGS = LEGACY_TAGS + PATH_ORDER

Purpose = Cheque.Purpose
# These are invoiced to the tenant first, then paid: fees and penalties.
CHARGE_PURPOSES = (
    Purpose.PENALTY,
    Purpose.BOUNCE_FEE,
    Purpose.COMMISSION,
    Purpose.REGISTRATION,
    Purpose.MAINTENANCE,
    Purpose.OTHER,
)


def cutover_date():
    return AccountingSettings.objects.filter(pk=1).values_list("cutover_date", flat=True).first()


def ledger_on():
    """The ledger only starts posting once a cutover date is set."""
    return cutover_date() is not None


def is_third_party(lease):
    return lease.unit.property.ownership_type == Property.Ownership.THIRD_PARTY


def active_invoice(lease):
    """The lease's live (not cancelled) invoice, or None."""
    return LeaseInvoice.objects.filter(lease=lease, is_void=False).first()


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
    """The steps of this payment that are in the books (older designs first)."""
    tags = set(_active_entries(CHEQUE_MODEL, cheque.pk).values_list("source_tag", flat=True))
    return [t for t in ALL_TAGS if t in tags]


def cheque_is_posted(cheque_id):
    """True if any step of this payment is in the books (used to lock its amount)."""
    return _active_entries(CHEQUE_MODEL, cheque_id).exists()


def lease_is_invoiced(lease_id):
    return LeaseInvoice.objects.filter(lease_id=lease_id, is_void=False).exists()


def _opening_closed(cheque, invoice):
    """A payment that cleared before the opening date is history: it is inside the
    opening balances already, so it is never posted again."""
    return bool(
        invoice.is_opening
        and cheque.status == Cheque.Status.CLEARED
        and cheque.cleared_date
        and cheque.cleared_date < invoice.date
    )


# --- who, where, how much ------------------------------------------------------


def owner_on(prop, day):
    """The owner who owned a third-party property on a given day."""
    if prop.ownership_type != Property.Ownership.THIRD_PARTY:
        return None
    ids = {prop.owner_id} | {p.owner_id for p in prop.past_owners.all()}
    for owner_id in ids:
        if owner_id and prop.owned_by_on(owner_id, day):
            return Owner.objects.get(pk=owner_id)
    return None


def owner_or_error(prop, day):
    owner = owner_on(prop, day)
    if owner is None:
        raise ValidationError(f"No owner is recorded for {prop.name} on {day:%d %b %Y}.")
    return owner


def _label(cheque):
    if cheque.method == Cheque.Method.CHEQUE:
        return f"cheque {cheque.cheque_number or 'no number'}"
    return cheque.get_method_display().lower()


def bank_account(lease):
    """The bank the money goes to: the owner trust bank for a third-party unit,
    otherwise the company's operating bank."""
    return posting_account("bank_trust" if is_third_party(lease) else "bank_operating")


def money_account(cheque):
    """Where the money lands when it is paid: cash, bank, or merchant clearing (card)."""
    method = cheque.method
    if method == Cheque.Method.CASH:
        return posting_account("cash_on_hand")
    if method == Cheque.Method.CARD:
        return posting_account("merchant_clearing")
    return bank_account(cheque.lease)


def _credit_line(cheque, amount, description):
    """What a payment settles: the bounced-cheque balance when it replaces a bounced
    cheque, the deposit liability for a deposit, otherwise the tenant receivable."""
    tenant = cheque.lease.tenant
    if cheque.replaces_id:
        return LineSpec(posting_account("pdc_bounced"), credit=amount, tenant=tenant, description=description)
    if cheque.purpose == Purpose.SECURITY_DEPOSIT:
        return LineSpec(posting_account("security_deposits"), credit=amount, description=description)
    return LineSpec(posting_account("receivable"), credit=amount, tenant=tenant, description=description)


def _is_charge(cheque):
    return cheque.purpose in CHARGE_PURPOSES


def _today():
    return timezone.localdate()


def _charge_day(cheque):
    return min(cheque.cheque_date or _today(), _today())


def _start_day(cheque):
    """The first day this payment would be posted on."""
    if _is_charge(cheque):
        return _charge_day(cheque)
    return cheque.received_date or cheque.cleared_date or _today()


def _stage_date(cheque, tag):
    stage = {
        "charge": _charge_day(cheque),
        "pdc_held": cheque.received_date or cheque.cleared_date,
        "pdc_cleared": cheque.cleared_date,
        "pdc_bounced": cheque.returned_date,
        "money_in": cheque.cleared_date,
        "card_settled": cheque.settled_on,
    }.get(tag)
    return stage or _today()


def _floor_day(cheque, invoice):
    if cheque.purpose == Purpose.RENT:
        return invoice.date
    return cutover_date()


def _entry_day(cheque, tag, invoice):
    return max(_stage_date(cheque, tag), _floor_day(cheque, invoice))


# --- what should be in the books ------------------------------------------------


def _desired_path(cheque):
    S = Cheque.Status
    status = cheque.status
    if status == S.CANCELLED:
        return []
    first = ["charge"] if _is_charge(cheque) else []
    if cheque.method == Cheque.Method.CHEQUE:
        if status in (S.RECEIVED, S.DEPOSITED):
            return first + ["pdc_held"]
        if status == S.CLEARED:
            return first + ["pdc_held", "pdc_cleared"]
        if status in (S.RETURNED, S.REPLACED):
            return first + ["pdc_held", "pdc_bounced"]
        return first
    if status == S.CLEARED:
        path = first + ["money_in"]
        if cheque.method == Cheque.Method.CARD and cheque.settled_on:
            path.append("card_settled")
        return path
    return first


def plan_cheque(cheque):
    """What still has to be posted for this payment. A list of (action, tag);
    action is "post" or "reverse". Empty means the ledger is up to date."""
    if not ledger_on() or not cheque.pk:
        return []
    invoice = None
    if cheque.purpose == Purpose.RENT:
        invoice = active_invoice(cheque.lease)
        if invoice is None or _opening_closed(cheque, invoice):
            return []
    elif _start_day(cheque) < cutover_date():
        return []  # it happened before the ledger started: it is inside the opening balances
    active = _active_path(cheque)
    desired = _desired_path(cheque)
    common = 0
    while common < len(active) and common < len(desired) and active[common] == desired[common]:
        common += 1
    steps = [("reverse", tag) for tag in reversed(active[common:])]
    steps += [("post", tag) for tag in desired[common:]]
    return steps


def describe_step(action, tag):
    names = {
        "charge": "invoice the fee",
        "pdc_held": "cheque recorded",
        "pdc_cleared": "cheque cleared",
        "pdc_bounced": "cheque bounced",
        "money_in": "payment received",
        "card_settled": "card money reached the bank",
        "received": "old posting (cheque received)",
        "deposited": "old posting (cheque deposited)",
        "cleared": "old posting (payment cleared)",
        "bounced": "old posting (cheque bounced)",
        "paid": "old posting (payment received)",
        "vat_release": "old posting (VAT release)",
        "expense": "expense",
        "vendor_paid": "vendor paid",
        "recovery": "expense recovered",
    }
    return ("Undo: " if action == "reverse" else "") + names.get(tag, tag)


# --- the lease invoice -------------------------------------------------------


def invoice_problems(lease):
    """Why this lease cannot be invoiced yet, or an empty list."""
    problems = []
    cutover = cutover_date()
    if cutover is None:
        problems.append("Set the cutover date in Accounting settings first.")
    elif lease.start_date < cutover:
        problems.append(
            f"The lease started before the cutover date ({cutover:%d %b %Y}). "
            "Bring it in with 'Create opening balances' instead."
        )
    if active_invoice(lease) is not None:
        problems.append("This lease already has an invoice.")
    if not lease.annual_rent or lease.annual_rent <= 0:
        problems.append("The rent is zero.")
    return problems


def invoice_lines(lease, total, vat, day, description="Lease invoice"):
    """Dr Tenant receivable; Cr Owner payable or Rent income; Cr VAT output payable."""
    net = total - vat
    lines = [
        LineSpec(posting_account("receivable"), debit=total, tenant=lease.tenant, description=description)
    ]
    if is_third_party(lease):
        owner = owner_or_error(lease.unit.property, day)
        lines.append(
            LineSpec(posting_account("owner_payable"), credit=net, owner=owner, description="Rent for the owner")
        )
    else:
        lines.append(LineSpec(posting_account("rent_income"), credit=net, description="Rent"))
    if vat > 0:
        lines.append(LineSpec(posting_account("vat_payable"), credit=vat, description="VAT on rent"))
    return lines


def create_lease_invoice(lease, day=None, user=None):
    """Issue the master invoice for the whole lease and post it. Returns (invoice,
    errors from posting the payments already received)."""
    user = user or current_user()
    problems = invoice_problems(lease)
    if problems:
        raise ValidationError(problems)
    day = day or max(lease.start_date, cutover_date())
    total = lease.contract_total
    net, vat = split_vat(total, lease.vat_percent)
    lines = invoice_lines(lease, total, vat, day)
    with transaction.atomic():
        number = NumberSequence.next("INV", day.year)
        entry = post_entry(
            date=day,
            memo=f"{'Tax invoice' if vat > 0 else 'Invoice'} {number}: {lease.tenant.name}, unit {lease.unit.unit_number}",
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
            rent_amount=net,
            vat_percent=lease.vat_percent or ZERO,
            vat_amount=vat,
            total_amount=total,
            entry=entry,
            created_by=user,
        )
    errors = []
    for cheque in lease.cheques.all():
        try:
            sync_cheque(cheque, user=user)
        except ValidationError as exc:
            errors.append(f"{cheque}: {' '.join(exc.messages)}")
    return invoice, errors


def void_lease_invoice(invoice, user=None, reason="the invoice was cancelled"):
    """Cancel an invoice that was made by mistake, so the lease can be corrected and
    invoiced again. Only possible while no payment or credit note depends on it."""
    user = user or current_user()
    if invoice.is_void:
        raise ValidationError("This invoice is already cancelled.")
    lease = invoice.lease
    blockers = []
    for cheque in lease.cheques.filter(purpose=Purpose.RENT):
        if _active_path(cheque):
            blockers.append(
                f"{cheque} is already in the accounts (cancel it first, only possible before "
                "the bank has it)."
            )
    if CreditNote.objects.filter(invoice=invoice, is_void=False).exists():
        blockers.append("A credit note was issued against it.")
    if blockers:
        raise ValidationError(blockers)
    with transaction.atomic():
        day = max(_today(), invoice.entry.date)
        reverse_entry(invoice.entry, date=day, reason=reason, user=user)
        LeaseInvoice.objects.filter(pk=invoice.pk).update(is_void=True, voided_at=timezone.now())
        AuditLog.record("invoice_void", user=user, entry=invoice.entry, detail=f"Invoice {invoice.number} cancelled.")


# --- payment postings ---------------------------------------------------------


def _charge_lines(cheque, day):
    """The invoice for a fee or penalty: Dr Tenant receivable, Cr the income."""
    lease = cheque.lease
    amount = cheque.amount.quantize(CENT, ROUND_HALF_UP)
    tenant = lease.tenant
    purpose = cheque.purpose
    lines = [
        LineSpec(posting_account("receivable"), debit=amount, tenant=tenant, description=cheque.get_purpose_display())
    ]
    if purpose == Purpose.PENALTY:
        if is_third_party(lease):
            owner = owner_or_error(lease.unit.property, day)
            lines.append(
                LineSpec(posting_account("owner_payable"), credit=amount, owner=owner, description="Early-exit penalty")
            )
        else:
            lines.append(LineSpec(posting_account("penalty_income"), credit=amount, description="Early-exit penalty"))
    elif purpose in (Purpose.COMMISSION, Purpose.BOUNCE_FEE):
        key = "commission_income" if purpose == Purpose.COMMISSION else "bounce_fee_income"
        net, vat = split_vat(amount, CompanySettings.load().fee_vat_percent)
        lines.append(LineSpec(posting_account(key), credit=net, description=cheque.get_purpose_display()))
        if vat > 0:
            lines.append(LineSpec(posting_account("vat_payable"), credit=vat, description="VAT on fee"))
    elif purpose == Purpose.REGISTRATION:
        lines.append(LineSpec(posting_account("registration_income"), credit=amount, description="Registration fee"))
    elif purpose == Purpose.MAINTENANCE:
        lines.append(LineSpec(posting_account("maintenance_income"), credit=amount, description="Maintenance charge"))
    else:
        lines.append(LineSpec(posting_account("other_income"), credit=amount, description=cheque.get_purpose_display()))
    return lines


def _stage_lines(cheque, tag, day):
    lease = cheque.lease
    tenant = lease.tenant
    amount = cheque.amount.quantize(CENT, ROUND_HALF_UP)
    label = _label(cheque)
    if tag == "charge":
        return _charge_lines(cheque, day)
    if tag == "pdc_held":
        return [
            LineSpec(posting_account("pdc_ar"), debit=amount, tenant=tenant, description=label),
            _credit_line(cheque, amount, label),
        ]
    if tag == "pdc_cleared":
        return [
            LineSpec(bank_account(lease), debit=amount, description=label),
            LineSpec(posting_account("pdc_ar"), credit=amount, tenant=tenant, description=label),
        ]
    if tag == "pdc_bounced":
        return [
            LineSpec(posting_account("pdc_bounced"), debit=amount, tenant=tenant, description=label + " returned"),
            LineSpec(posting_account("pdc_ar"), credit=amount, tenant=tenant, description=label + " returned"),
        ]
    if tag == "money_in":
        return [
            LineSpec(money_account(cheque), debit=amount, description=label),
            _credit_line(cheque, amount, label),
        ]
    if tag == "card_settled":
        return [
            LineSpec(bank_account(lease), debit=amount, description="Card payment settled"),
            LineSpec(posting_account("merchant_clearing"), credit=amount, description="Card payment settled"),
        ]
    raise ValueError(f"unknown stage {tag}")


def _post_stage(cheque, tag, invoice, user):
    day = _entry_day(cheque, tag, invoice)
    lines = _stage_lines(cheque, tag, day)
    words = {
        "charge": "invoiced",
        "pdc_held": "recorded (held for the tenant)",
        "pdc_cleared": "cleared",
        "pdc_bounced": "returned by the bank",
        "money_in": "paid",
        "card_settled": "settled to the bank",
    }[tag]
    tenant = cheque.lease.tenant
    what = cheque.get_purpose_display()
    entry = post_entry(
        date=day,
        memo=f"{what}, {_label(cheque)} {words}: {tenant.name}, AED {cheque.amount:,.2f}",
        lines=lines,
        source=JournalEntry.Source.SYSTEM,
        reference=(cheque.cheque_number or cheque.reference or "")[:100],
        source_model=CHEQUE_MODEL,
        source_id=cheque.pk,
        source_tag=tag,
        user=user,
    )
    if tag in ("pdc_held", "money_in"):
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
    day = max(_today(), entry.date)
    reversal = reverse_entry(entry, date=day, reason="the payment status was changed", user=user)
    ReceiptVoucher.objects.filter(entry=entry).update(is_void=True)
    return reversal


def sync_cheque(cheque, user=None):
    """Bring the ledger in line with the payment's status. Returns how many steps
    were posted. Raises ValidationError if a step cannot be posted."""
    user = user or current_user()
    steps = plan_cheque(cheque)
    if not steps:
        return 0
    invoice = active_invoice(cheque.lease) if cheque.purpose == Purpose.RENT else None
    done = 0
    for action, tag in steps:
        with transaction.atomic():
            if action == "reverse":
                _reverse_stage(cheque, tag, user)
            else:
                _post_stage(cheque, tag, invoice, user)
        done += 1
    return done


# --- what is waiting -----------------------------------------------------------


def blocking_reason(day):
    """Why an entry dated `day` cannot be posted right now, or ''."""
    try:
        check_posting_date(day)
    except ValidationError as exc:
        return " ".join(exc.messages)
    return ""


def _wait_day(cheque):
    """The date the next waiting step of this payment would be posted on."""
    S = Cheque.Status
    if cheque.status in (S.RECEIVED, S.DEPOSITED):
        return _stage_date(cheque, "pdc_held")
    if cheque.status == S.CLEARED:
        return _stage_date(cheque, "pdc_cleared" if cheque.method == Cheque.Method.CHEQUE else "money_in")
    if cheque.status in (S.RETURNED, S.REPLACED):
        return _stage_date(cheque, "pdc_bounced")
    return _stage_date(cheque, "charge")


def waiting_items(limit=500):
    """Everything that is not posted yet, for the Daily postings page."""
    from . import rules_more

    cutover = cutover_date()
    info = {
        "cutover": cutover,
        "to_invoice": [],
        "to_open": [],
        "before_cutover": 0,
        "cheques": [],
        "payments": [],
        "terminations": [],
        "deposit_refunds": [],
        "expenses": [],
        "statements": [],
        "overpaid": [],
        "total_other": 0,
        "total": 0,
    }
    if cutover is None:
        return info
    invoiced = LeaseInvoice.objects.filter(is_void=False).values("lease_id")
    leases = Lease.objects.filter(status=Lease.Status.ACTIVE, annual_rent__gt=0).exclude(pk__in=invoiced)
    info["to_invoice"] = list(
        leases.filter(start_date__gte=cutover).select_related("tenant", "unit__property").order_by("start_date")[:limit]
    )
    info["to_open"] = list(
        leases.filter(start_date__lt=cutover, end_date__gte=cutover)
        .select_related("tenant", "unit__property")
        .order_by("start_date")[:limit]
    )
    info["before_cutover"] = len(info["to_open"])

    def collect(queryset, key):
        for cheque in queryset.select_related("lease__tenant", "lease__unit__property").order_by("cheque_date", "id"):
            steps = plan_cheque(cheque)
            if steps:
                info[key].append(
                    {
                        "cheque": cheque,
                        "steps": [describe_step(a, t) for a, t in steps],
                        "why": blocking_reason(_wait_day(cheque)),
                    }
                )
                if len(info[key]) >= limit:
                    break

    collect(Cheque.objects.filter(purpose=Purpose.RENT, lease__invoices__is_void=False).distinct(), "cheques")
    collect(Cheque.objects.exclude(purpose=Purpose.RENT), "payments")
    rules_more.collect_waiting(info, limit)
    info["total_other"] = (
        len(info["payments"])
        + len(info["terminations"])
        + len(info["deposit_refunds"])
        + len(info["expenses"])
        + len(info["statements"])
        + len(info["overpaid"])
    )
    info["total"] = len(info["to_invoice"]) + len(info["to_open"]) + len(info["cheques"]) + info["total_other"]
    return info


def post_all_waiting(user=None):
    """Try to post everything that is waiting. Returns (posted, errors)."""
    from . import rules_more

    posted, errors = 0, []
    info = waiting_items()
    for item in info["cheques"] + info["payments"]:
        try:
            posted += sync_cheque(item["cheque"], user=user)
        except ValidationError as exc:
            errors.append(f"{item['cheque']}: {' '.join(exc.messages)}")
    more_posted, more_errors = rules_more.post_waiting_more(info, user)
    return posted + more_posted, errors + more_errors

