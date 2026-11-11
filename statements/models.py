"""Owner statements: for each owner and period, the rent collected, the company's fees, the costs charged to the owner, and what is paid out."""
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Length
from django.utils import timezone

from agreements.models import OwnerAgreement, agreement_for
from leases.models import Lease, LeasePayment
from payables.models import Expense, PaymentVoucher, VendorBill
from properties.models import Owner, Property, save_with_unique_code

CENT = Decimal("0.01")
DAYS_IN_YEAR = Decimal("365")
LIVE = ("draft", "approved", "paid")  # statements that hold on to the items they list (a voided one lets them go)


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _next_code(width=4):
    last = OwnerStatement.objects.exclude(code__isnull=True).order_by(Length("code").desc(), "-code").values_list("code", flat=True).first()
    match = re.fullmatch(r"STM-(\d+)", last or "")
    return f"STM-{(int(match.group(1)) if match else 0) + 1:0{width}d}"


class Section(models.TextChoices):
    INCOME = "income", "Rent received"
    GUARANTEE = "guarantee", "Guaranteed rent"
    FEE = "fee", "Management fees"
    COST = "cost", "Costs charged to the owner"
    INFO = "info", "For information"


# How each section changes what the owner is owed.
SIGN = {"income": 1, "guarantee": 1, "fee": -1, "cost": -1, "info": 0}


class OwnerStatement(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved (to pay)"
        PAID = "paid", "Paid"
        VOIDED = "voided", "Voided"

    class Method(models.TextChoices):
        TRANSFER = "transfer", "Bank transfer"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"
        OTHER = "other", "Other"

    code = models.CharField("Statement no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="statements")
    period_start = models.DateField("From")
    period_end = models.DateField("To")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    rent_total = models.DecimalField("Rent received", max_digits=14, decimal_places=2, default=0, editable=False)
    guarantee_total = models.DecimalField("Guaranteed rent", max_digits=14, decimal_places=2, default=0, editable=False)
    fee_total = models.DecimalField("Management fees", max_digits=14, decimal_places=2, default=0, editable=False)
    cost_total = models.DecimalField("Costs charged", max_digits=14, decimal_places=2, default=0, editable=False)
    adjustment = models.DecimalField(
        "Adjustment (AED)", max_digits=14, decimal_places=2, default=0,
        help_text="Anything else to add (+) or take off (-) this statement, for example an advance paid earlier. Explain it below.",
    )
    adjustment_note = models.CharField("Adjustment note", max_length=200, blank=True)
    brought_forward = models.DecimalField("Brought forward", max_digits=14, decimal_places=2, default=0, editable=False)
    net_amount = models.DecimalField("Net for the period", max_digits=14, decimal_places=2, default=0, editable=False)
    payout_due = models.DecimalField("To pay the owner", max_digits=14, decimal_places=2, default=0, editable=False)
    carried_forward = models.DecimalField("Carried forward", max_digits=14, decimal_places=2, default=0, editable=False)

    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="+")
    approved_on = models.DateField(null=True, blank=True, editable=False)
    paid_on = models.DateField("Paid on", null=True, blank=True)
    payout_method = models.CharField("Paid by", max_length=10, choices=Method.choices, default=Method.TRANSFER)
    payout_reference = models.CharField("Cheque / transfer reference", max_length=60, blank=True)
    voucher = models.ForeignKey(PaymentVoucher, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="+")
    void_reason = models.CharField("Reason for voiding", max_length=200, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-id"]
        verbose_name = "owner statement"
        verbose_name_plural = "Owner statements"
        permissions = [
            ("export_ownerstatement", "Can export owner statements to Excel / CSV"),
            ("approve_ownerstatement", "Can approve owner statements and record the payout"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(period_end__gte=models.F("period_start")), name="statement_period_ordered"),
        ]

    def __str__(self):
        return f"{self.code or 'New statement'} · {self.owner.name}" if self.owner_id else (self.code or "New statement")

    # ---- the neighbours ---------------------------------------------------------------------------
    def previous(self):
        """The owner's latest earlier statement that is still in force (not voided)."""
        others = OwnerStatement.objects.filter(owner_id=self.owner_id, status__in=LIVE, period_end__lt=self.period_start)
        return others.order_by("-period_end").first()

    def is_first(self):
        """The owner's first statement ever (nothing in force before it): it looks only inside its own period."""
        return not OwnerStatement.objects.filter(owner_id=self.owner_id, status__in=LIVE).exclude(pk=self.pk).filter(
            period_end__lt=self.period_start).exists()

    @property
    def is_locked(self):
        return self.status != self.Status.DRAFT

    # ---- the numbers ----------------------------------------------------------------------------
    def recalculate(self, save=True):
        sums = {key: Decimal("0.00") for key in Section.values}
        for line in self.lines.all():
            sums[line.section] += line.amount
        self.rent_total, self.guarantee_total = sums["income"], sums["guarantee"]
        self.fee_total, self.cost_total = sums["fee"], sums["cost"]
        previous = self.previous()
        self.brought_forward = previous.carried_forward if previous is not None else Decimal("0.00")
        self.net_amount = money(self.rent_total + self.guarantee_total - self.fee_total - self.cost_total + (self.adjustment or 0))
        due = self.net_amount + self.brought_forward
        self.payout_due = max(money(due), Decimal("0.00"))
        self.carried_forward = min(money(due), Decimal("0.00"))
        if save and self.pk:
            type(self).objects.filter(pk=self.pk).update(
                rent_total=self.rent_total, guarantee_total=self.guarantee_total, fee_total=self.fee_total, cost_total=self.cost_total,
                brought_forward=self.brought_forward, net_amount=self.net_amount, payout_due=self.payout_due,
                carried_forward=self.carried_forward,
            )

    # ---- building the lines -------------------------------------------------------------------------
    def generate(self):
        """(Re)build the lines from the payments, bills and expenses. Only for a draft."""
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only a draft statement can be rebuilt.")
        self.lines.all().delete()
        build_lines(self)
        self.recalculate()

    def clean(self):
        errors = {}
        if self.owner_id and self.owner.is_company:
            errors["owner"] = "The company's own properties have no statement."
        if self.period_start and self.period_end:
            if self.period_end < self.period_start:
                errors["period_end"] = "The end of the period cannot be before its start."
            elif self.period_end > timezone.localdate():
                errors["period_end"] = "A statement cannot run past today."
        if self.owner_id and self.period_start and self.period_end and "period_end" not in errors and self._state.adding:
            live = OwnerStatement.objects.filter(owner_id=self.owner_id, status__in=LIVE)
            clash = live.filter(period_start__lte=self.period_end, period_end__gte=self.period_start).first()
            last = live.order_by("-period_end").first()
            if clash is not None:
                errors["period_start"] = f"This period overlaps statement {clash.code} ({clash.period_start:%d %b %Y} to {clash.period_end:%d %b %Y})."
            elif last is not None and last.period_end < self.period_start and self.period_start != last.period_end + timedelta(days=1):
                errors["period_start"] = (
                    f"Statements follow each other without gaps: this one must start on {last.period_end + timedelta(days=1):%d %b %Y}, "
                    f"the day after statement {last.code}."
                )
            elif last is not None and last.status == self.Status.DRAFT and last.period_end < self.period_start:
                errors["owner"] = f"Approve or void the draft statement {last.code} first."
        if self.adjustment and not (self.adjustment_note or "").strip():
            errors["adjustment_note"] = "Explain the adjustment."
        if not self.adjustment:
            self.adjustment_note = ""
        if self.pk:
            old = OwnerStatement.objects.filter(pk=self.pk).first()
            if old is not None:
                errors.update(self._transition_errors(old))
        if errors:
            raise ValidationError(errors)

    def _transition_errors(self, old):
        S = self.Status
        errors = {}
        if old.status != S.DRAFT:
            for field in ("adjustment", "adjustment_note"):
                if getattr(old, field) != getattr(self, field):
                    errors[field] = "This statement is approved. It can no longer be changed."
        allowed = {S.DRAFT: (S.DRAFT, S.APPROVED, S.VOIDED), S.APPROVED: (S.APPROVED, S.PAID, S.VOIDED), S.PAID: (S.PAID,), S.VOIDED: (S.VOIDED,)}
        if self.status not in allowed[S(old.status)]:
            errors["status"] = {
                S.PAID: "A paid statement cannot be changed. Void its payment voucher and make a correction in the next statement.",
                S.VOIDED: "A voided statement stays voided. Generate a new one.",
            }.get(S(old.status), f"A statement cannot go from {old.get_status_display()} to {self.get_status_display()}.")
        if self.status == S.VOIDED and not (self.void_reason or "").strip() and "status" not in errors:
            errors["void_reason"] = "Write the reason for voiding this statement."
        if self.status == S.PAID and "status" not in errors:
            if old.status != S.PAID and self.payout_method == self.Method.CHEQUE and not (self.payout_reference or "").strip():
                errors["payout_reference"] = "Enter the cheque number."
        if self.status == S.APPROVED and old.status == S.DRAFT and not self.lines.exists() and not self.adjustment:
            errors["status"] = "This statement is empty: there is nothing to approve."
        if self.status == S.PAID and old.status != S.PAID and self.paid_on and self.paid_on > timezone.localdate():
            errors["paid_on"] = "The payment date cannot be in the future."
        return errors

    def save(self, *args, **kwargs):
        adding = self._state.adding
        old = None if adding else OwnerStatement.objects.filter(pk=self.pk).first()
        if self.status != self.Status.VOIDED:
            self.void_reason = ""
        if self.status == self.Status.PAID and not self.paid_on:
            self.paid_on = timezone.localdate()
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, _next_code, lambda: super(OwnerStatement, self).save(*args, **kwargs))
        if adding:
            build_lines(self)
        if self.status in (self.Status.DRAFT, self.Status.APPROVED) or adding:
            self.recalculate()
        if old is not None and old.status == self.Status.APPROVED and self.status == self.Status.PAID:
            self._pay_out()

    def _pay_out(self):
        """Record the money going to the owner as a payment voucher (nothing is made for a statement with nothing to pay)."""
        if self.voucher_id or self.payout_due <= 0:
            return
        from core.audit import suppressed

        voucher = PaymentVoucher(
            payee_name=self.owner.name, description=f"Owner payout, statement {self.code} ({self.period_start:%d %b %Y} to {self.period_end:%d %b %Y})"[:250],
            amount=self.payout_due, method=self.payout_method,
            cheque_number=(self.payout_reference or "")[:30] if self.payout_method == self.Method.CHEQUE else "",
            reference=(self.payout_reference or "")[:60] if self.payout_method != self.Method.CHEQUE else "",
            voucher_date=self.paid_on or timezone.localdate(), notes=f"Statement {self.code}",
        )
        with suppressed():
            voucher.save()
        type(self).objects.filter(pk=self.pk).update(voucher=voucher)
        self.voucher = voucher


class StatementLine(models.Model):
    """One line of a statement. Lines are copies made when the statement is built, so a statement keeps reading the same later."""

    statement = models.ForeignKey(OwnerStatement, on_delete=models.CASCADE, related_name="lines")
    section = models.CharField(max_length=10, choices=Section.choices)
    description = models.CharField(max_length=300)
    property = models.ForeignKey(Property, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    property_name = models.CharField(max_length=200, blank=True)
    line_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    # What the line was made from (so the same payment, bill or expense is never listed twice).
    payment = models.ForeignKey(LeasePayment, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    bill = models.ForeignKey(VendorBill, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    expense = models.ForeignKey(Expense, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    lease = models.ForeignKey(Lease, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    sort = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort", "id"]

    def __str__(self):
        return f"{self.get_section_display()}: {self.description}"

    def signed(self):
        return self.amount * SIGN[self.section]


def _live_ids(field, owner_id):
    return set(
        StatementLine.objects.filter(statement__owner_id=owner_id, statement__status__in=LIVE).exclude(**{f"{field}__isnull": True})
        .values_list(field, flat=True)
    )


def _overlap_days(start, end, agreement):
    """How many days of [start, end] the agreement is in force."""
    last = agreement.effective_end()
    first, final = max(start, agreement.start_date), min(end, last) if last else end
    return max(0, (final - first).days + 1)


def build_lines(statement):
    """Make the lines of `statement` from what has not been on an earlier statement of this owner."""
    owner_id, start, end = statement.owner_id, statement.period_start, statement.period_end
    first = statement.is_first()
    props = {p.pk: p for p in Property.objects.filter(owner_id=owner_id)}
    lines, order = [], 0

    def add(section, description, amount, prop=None, day=None, **source):
        nonlocal order
        order += 1
        lines.append(StatementLine(
            statement=statement, section=section, description=description[:300], amount=money(amount), property=prop,
            property_name=prop.name if prop is not None else "", line_date=day, sort=order, **source,
        ))

    def in_scope(day):
        return day is not None and day <= end and (not first or day >= start)

    # Rent received (and, per agreement, the fee on it)
    taken = _live_ids("payment_id", owner_id)
    collected = {}  # agreement -> total collected, for the "% of rent collected" fee
    payments = (
        LeasePayment.objects.filter(kind=LeasePayment.Kind.RENT, status=LeasePayment.Status.PAID, lease__property_id__in=props.keys())
        .exclude(pk__in=taken).select_related("lease__tenant", "lease__unit__property", "lease__property").order_by("paid_on", "id")
    )
    for payment in payments:
        if not in_scope(payment.paid_on):
            continue
        prop = props[payment.lease.property_id]
        agreement = agreement_for(prop, payment.paid_on)
        text = f"Rent, payment {payment.part} · {payment.lease.item_name} · {payment.lease.tenant.name} ({payment.lease.code})"
        if agreement is not None and agreement.is_guaranteed_rent:
            add("info", f"{text}: kept by the company (guaranteed rent {agreement.code})", payment.amount, prop, payment.paid_on, payment=payment)
            continue
        add("income", text, payment.amount, prop, payment.paid_on, payment=payment)
        if agreement is not None and agreement.fee_percent_collected is not None:
            collected[agreement] = collected.get(agreement, Decimal("0.00")) + payment.amount

    for agreement, total in collected.items():
        fee = money(total * agreement.fee_percent_collected / 100)
        add("fee", f"{agreement.code} · {agreement.fee_percent_collected.normalize():f}% of rent collected (AED {total:,.2f})", fee)

    # Fees and guaranteed rent that run with time, prorated by the days of the period the agreement was in force
    for agreement in OwnerAgreement.objects.filter(owner_id=owner_id).exclude(status=OwnerAgreement.Status.DRAFT).order_by("start_date", "id"):
        days = _overlap_days(start, end, agreement)
        if not days:
            continue
        share = Decimal(days) / DAYS_IN_YEAR
        if agreement.guaranteed_rent_yearly is not None:
            add("guarantee", f"{agreement.code} · Guaranteed rent for {days} day(s)", agreement.guaranteed_rent_yearly * share)
        if agreement.fee_fixed_yearly is not None:
            add("fee", f"{agreement.code} · Fixed fee AED {agreement.fee_fixed_yearly:,.2f} / year, for {days} day(s)", agreement.fee_fixed_yearly * share)

    # The fee charged once on a lease that was signed or renewed
    charged = _live_ids("lease_id", owner_id)
    for lease in Lease.objects.filter(property_id__in=props.keys()).exclude(pk__in=charged).select_related("tenant", "unit__property", "property"):
        if not in_scope(lease.start_date):
            continue
        agreement = agreement_for(props[lease.property_id], lease.start_date)
        if agreement is not None and agreement.fee_percent_contract is not None and not agreement.is_guaranteed_rent:
            add("fee", f"{agreement.code} · {agreement.fee_percent_contract.normalize():f}% of the yearly rent of lease {lease.code} · {lease.item_name} (AED {lease.annual_rent:,.2f})",
                lease.annual_rent * agreement.fee_percent_contract / 100, props[lease.property_id], lease.start_date, lease=lease)

    # Costs charged to the owner
    taken_bills, taken_expenses = _live_ids("bill_id", owner_id), _live_ids("expense_id", owner_id)
    bills = VendorBill.objects.filter(property_id__in=props.keys(), charged_to="owner", status=VendorBill.Status.OPEN).exclude(pk__in=taken_bills)
    for bill in bills.select_related("vendor", "unit", "property").order_by("bill_date", "id"):
        if in_scope(bill.bill_date):
            add("cost", f"{bill.description} · {bill.where} · {bill.vendor.name} ({bill.code})", bill.total, props[bill.property_id], bill.bill_date, bill=bill)
    expenses = Expense.objects.filter(property_id__in=props.keys(), charged_to="owner", cancelled=False).exclude(pk__in=taken_expenses)
    for expense in expenses.select_related("unit", "property").order_by("expense_date", "id"):
        if in_scope(expense.expense_date):
            add("cost", f"{expense.description} · {expense.where} · {expense.payee} ({expense.code})", expense.total, props[expense.property_id], expense.expense_date, expense=expense)

    StatementLine.objects.bulk_create(lines)


def default_period(today=None):
    """The last full calendar month."""
    today = today or timezone.localdate()
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def month_of(day):
    return date(day.year, day.month, 1)
