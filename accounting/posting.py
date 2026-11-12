"""
Automatic journal entries.

Each business record that moves money (a tenant payment, a vendor bill, an expense, a payment voucher, a payroll, a final settlement,
an owner statement) has at most ONE entry in the ledger. `sync(kind, record)` works out what that entry should be and makes the ledger
match: it is made, replaced or removed. It is safe to run any number of times. Money held for owners is a liability (account 2200);
the company's own income is only its fees, the rent of its own properties and fees it charges.
"""
import contextvars
import logging
from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .models import DEFAULT_NAMES, Account, JournalEntry, JournalLine

log = logging.getLogger(__name__)
ZERO = Decimal("0.00")
_paused = contextvars.ContextVar("accounting_paused", default=False)

CATEGORY_ACCOUNT = {
    "maintenance": "5000", "utilities": "5100", "government": "5200", "insurance": "5300", "cleaning": "5400", "security": "5400",
    "legal": "5500", "marketing": "5600", "office": "5700", "bank": "5750", "other": "5900",
}


@contextmanager
def paused():
    """No automatic entries inside this block (sample data and erase use it; the ledger is rebuilt afterwards)."""
    token = _paused.set(True)
    try:
        yield
    finally:
        _paused.reset(token)


def is_paused():
    return _paused.get()


def cents(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ensure_chart():
    """Make the standard accounts that are missing (the system accounts heal themselves if one was removed by mistake)."""
    have = set(Account.objects.values_list("code", flat=True))
    for code, (name, kind) in DEFAULT_NAMES.items():
        if code not in have:
            Account.objects.create(code=code, name=name, type=kind, is_system=True)
    Account.objects.filter(code__in=DEFAULT_NAMES).exclude(is_system=True).update(is_system=True)


def account(code):
    found = Account.objects.filter(code=code).first()
    if found is None:
        ensure_chart()
        found = Account.objects.get(code=code)
    return found


def cash_account(method):
    return "1000" if method == "cash" else "1010"


class Draft:
    """The lines of one entry, being worked out."""

    def __init__(self, date, description):
        self.date, self.description, self.lines = date, description, []

    def dr(self, code, amount, text=""):
        if cents(amount):
            self.lines.append((code, cents(amount), ZERO, text))

    def cr(self, code, amount, text=""):
        if cents(amount):
            self.lines.append((code, ZERO, cents(amount), text))

    def balanced(self):
        return bool(self.lines) and sum(l[1] for l in self.lines) == sum(l[2] for l in self.lines)


# ---------------------------------------------------------------------------------------
# What each kind of record becomes
# ---------------------------------------------------------------------------------------
def _rent_credit(payment):
    """Where rent goes: the company's own properties and guaranteed-rent properties are income; any other rent is owed to the owner."""
    from agreements.models import agreement_for

    prop = payment.lease.property
    if prop.owner.is_company:
        return "4000"
    agreement = agreement_for(prop, payment.paid_on or payment.due_date)
    if agreement is not None and agreement.is_guaranteed_rent:
        return "4010"
    return "2200"


def draft_leasepayment(p):
    if p.status != "paid":
        return None
    day = p.paid_on or p.due_date
    d = Draft(day, f"{p.lease.code} · {p.label} · {p.lease.tenant.name}")
    total, vat = p.total, p.vat
    from_deposit = p.method == "deposit"
    if p.kind == "refund":  # money paid back to the tenant
        d.dr("2300", p.amount)
        d.cr(cash_account(p.method), p.amount)
        return d
    d.dr("2300" if from_deposit else cash_account(p.method), total)
    if p.kind == "rent":
        d.cr(_rent_credit(p), p.amount)
    elif p.kind == "deposit":
        d.cr("2300", p.amount)
    elif p.kind in ("office", "agency", "ejari"):
        d.cr("4200", p.amount)
    else:
        d.cr("4900", p.amount)
    d.cr("2100", vat)
    return d


def _cost_debits(d, cost):
    """The debit side of a bill or an expense. Costs charged to an owner reduce what the owner is owed; the rest are the company's cost."""
    if cost.charged_to == "owner":
        d.dr("2200", cost.total)
    else:
        d.dr(CATEGORY_ACCOUNT.get(cost.category, "5900"), cost.amount)
        d.dr("1200", cost.vat)


def draft_vendorbill(b):
    if b.status != "open":
        return None
    d = Draft(b.bill_date, f"{b.code} · {b.vendor.name} · {b.description}")
    _cost_debits(d, b)
    d.cr("2000", b.total)
    return d


def draft_expense(e):
    if e.cancelled:
        return None
    d = Draft(e.expense_date, f"{e.code} · {e.payee} · {e.description}")
    _cost_debits(d, e)
    d.cr(cash_account(e.method), e.total)
    return d


def draft_voucher(v):
    if v.status != "issued" or v.expense_id:
        return None  # an expense is posted by the expense itself
    d = Draft(v.voucher_date, f"{v.code} · {v.payee_name} · {v.description}")
    notes = v.notes or ""
    if v.bill_id:
        debit = "2000"
    elif notes.startswith("Statement "):
        debit = "2200"
    elif notes.startswith("Payroll "):
        debit = "2400"
    elif notes.startswith("Settlement "):
        debit = "2410"
    else:
        debit = "5900"
    d.dr(debit, v.amount)
    d.cr(cash_account(v.method), v.amount)
    return d


def draft_payrollrun(run):
    if run.status not in ("approved", "paid"):
        return None
    day = run.approved_on or run.paid_on or run.month
    d = Draft(day, f"Payroll {run.code} · {run.month:%B %Y}")
    net = deductions = ZERO
    for slip in run.slips.all():
        net += slip.net
        deductions += slip.other_deductions
    d.dr("6000", net + deductions)
    d.cr("1400", deductions)
    d.cr("2400", net)
    return d


def draft_finalsettlement(s):
    if s.status not in ("approved", "paid"):
        return None
    d = Draft(s.approved_on or s.paid_on or s.last_day, f"Final settlement {s.code} · {s.employee.full_name}")
    d.dr("6010", s.net + s.other_deductions)  # what is paid plus what is recovered, so the entry always balances
    d.cr("1400", s.other_deductions)
    d.cr("2410", s.net)
    return d


def draft_ownerstatement(st):
    if st.status not in ("approved", "paid"):
        return None
    d = Draft(st.approved_on or st.period_end, f"Owner statement {st.code} · {st.owner.name}")
    d.dr("2200", st.fee_total)
    d.cr("4100", st.fee_total)
    d.dr("5800", st.guarantee_total)  # the guaranteed rent the company owes the owner whatever it collects
    d.cr("2200", st.guarantee_total)
    return d


def draft_fundtransfer(t):
    if t.cancelled:
        return None
    d = Draft(t.date, f"Transfer {t.code} · {t.from_account.name} to {t.to_account.name}" + (f" · {t.reference}" if t.reference else ""))
    d.dr(t.to_account.code, t.amount)
    d.cr(t.from_account.code, t.amount)
    return d


KINDS = {
    "leasepayment": draft_leasepayment, "vendorbill": draft_vendorbill, "expense": draft_expense, "paymentvoucher": draft_voucher,
    "payrollrun": draft_payrollrun, "finalsettlement": draft_finalsettlement, "ownerstatement": draft_ownerstatement, "fundtransfer": draft_fundtransfer,
}


# ---------------------------------------------------------------------------------------
# Making the ledger match
# ---------------------------------------------------------------------------------------
def remove(kind, pk):
    JournalEntry.objects.filter(source_type=kind, source_id=pk).delete()


def sync(kind, record):
    """Make the ledger entry of `record` right. Returns "made", "removed" or "unchanged"."""
    draft = KINDS[kind](record)
    if draft is None or not draft.lines:
        existed = JournalEntry.objects.filter(source_type=kind, source_id=record.pk).exists()
        remove(kind, record.pk)
        return "removed" if existed else "unchanged"
    if not draft.balanced():
        log.error("Ledger: the entry for %s %s does not balance, so it was not posted.", kind, record.pk)
        return "unchanged"
    with transaction.atomic():
        entry = JournalEntry.objects.filter(source_type=kind, source_id=record.pk).first()
        if entry is None:
            entry = JournalEntry(source_type=kind, source_id=record.pk)
        entry.date, entry.description, entry.status = draft.date or timezone.localdate(), draft.description[:250], "posted"
        entry.save()
        entry.lines.all().delete()
        JournalLine.objects.bulk_create([
            JournalLine(entry=entry, account=account(code), description=text[:250], debit=debit, credit=credit)
            for code, debit, credit, text in draft.lines
        ])
    return "made"


def safe_sync(kind, record):
    """For the live signals: never let an accounting problem stop someone saving a tenant payment or a bill."""
    if is_paused():
        return
    try:
        sync(kind, record)
    except Exception:  # noqa: BLE001
        log.exception("Ledger: could not post %s %s", kind, getattr(record, "pk", None))


def safe_remove(kind, pk):
    if is_paused():
        return
    try:
        remove(kind, pk)
    except Exception:  # noqa: BLE001
        log.exception("Ledger: could not remove the entry of %s %s", kind, pk)


def sources():
    """Every record that can have an entry, by kind."""
    from agreements.models import OwnerAgreement  # noqa: F401  (imported so its tables are loaded)
    from hr.models import FinalSettlement, PayrollRun
    from leases.models import LeasePayment
    from payables.models import Expense, PaymentVoucher, VendorBill
    from statements.models import OwnerStatement

    from .models import FundTransfer

    return {
        "leasepayment": LeasePayment.objects.filter(status="paid").select_related("lease__tenant", "lease__property__owner"),
        "vendorbill": VendorBill.objects.filter(status="open").select_related("vendor"),
        "expense": Expense.objects.filter(cancelled=False),
        "paymentvoucher": PaymentVoucher.objects.filter(status="issued", expense__isnull=True),
        "payrollrun": PayrollRun.objects.filter(status__in=("approved", "paid")),
        "finalsettlement": FinalSettlement.objects.filter(status__in=("approved", "paid")).select_related("employee"),
        "ownerstatement": OwnerStatement.objects.filter(status__in=("approved", "paid")).select_related("owner"),
        "fundtransfer": FundTransfer.objects.filter(cancelled=False).select_related("from_account", "to_account"),
    }


def sync_all():
    """Rebuild every automatic entry from the records, and remove entries whose record is gone. Returns a count by kind."""
    ensure_chart()
    result = {}
    for kind, queryset in sources().items():
        made = 0
        keep = []
        for record in queryset:
            keep.append(record.pk)
            if sync(kind, record) == "made":
                made += 1
        JournalEntry.objects.filter(source_type=kind).exclude(source_id__in=keep).delete()
        result[kind] = made
    return result


# ---------------------------------------------------------------------------------------
# Opening (starting) balances: one entry that the Opening balances page keeps
# ---------------------------------------------------------------------------------------
OPENING = "opening"
OPENING_EQUITY = "3200"


def save_opening_balances(day, balances, user=None):
    """balances: {Account: (debit, credit)}. Replaces the opening entry; any difference goes to Opening Balance Equity (3200)."""
    ensure_chart()
    with transaction.atomic():
        entry = JournalEntry.objects.filter(source_type=OPENING, source_id=1).first()
        if entry is None:
            entry = JournalEntry(source_type=OPENING, source_id=1, created_by=user)
        entry.date, entry.description, entry.status = day, "Opening balances", "posted"
        entry.save()
        entry.lines.all().delete()
        lines, debit, credit = [], ZERO, ZERO
        for acct, (dr, cr) in balances.items():
            if dr or cr:
                lines.append(JournalLine(entry=entry, account=acct, description="Opening balance", debit=dr, credit=cr))
                debit, credit = debit + dr, credit + cr
        if debit != credit:
            plug = account(OPENING_EQUITY)
            lines.append(JournalLine(entry=entry, account=plug, description="Opening balance equity (difference)",
                                     debit=(credit - debit) if credit > debit else ZERO, credit=(debit - credit) if debit > credit else ZERO))
        if not lines:
            entry.delete()
            return None
        JournalLine.objects.bulk_create(lines)
    return entry
