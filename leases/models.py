"""Leases: who rents which unit (or whole property), for how long, at what rent, and the payment schedule."""
import calendar
import re
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from core import sysconf
from core.validators import validate_file_content
from properties.models import Property, Unit, save_with_unique_code, validate_document_size
from tenants.models import Tenant

VAT_RATE = Decimal("5")  # UAE VAT, in percent
MAX_YEARS = 10
CENT = Decimal("0.01")
# Money other than rent that is asked of the tenant: (payment type, the lease field that holds the amount).
CHARGE_FIELDS = (
    ("deposit", "security_deposit"),
    ("office", "admin_fee"),
    ("agency", "commission"),
    ("ejari", "ejari_fee"),
)
_property = property  # the Lease model has a field called "property", which hides the built-in name


def lease_document_path(instance, filename):
    return f"lease_documents/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def next_lease_code():
    numbers = []
    for code in Lease.objects.exclude(code__isnull=True).values_list("code", flat=True):
        match = re.fullmatch(r"LSE-(\d+)", code)
        if match:
            numbers.append(int(match.group(1)))
    return f"LSE-{max(numbers, default=0) + 1:04d}"


def add_months(start, months):
    """31 Jan + 1 month = 28/29 Feb (the day is kept as far as the month allows)."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class Lease(models.Model):
    """A tenancy contract: one tenant renting one unit, or one whole standalone property."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TERMINATED = "terminated", "Terminated early"

    class DepositStatus(models.TextChoices):
        HELD = "held", "Held"
        REFUNDED = "refunded", "Refunded"
        DEDUCTED = "deducted", "Deducted (kept)"

    INSTALLMENT_CHOICES = [(1, "1 payment"), (2, "2 payments"), (3, "3 payments"),
                           (4, "4 payments"), (6, "6 payments"), (12, "12 payments")]

    code = models.CharField("Lease no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="leases")
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, null=True, blank=True, related_name="leases",
        help_text="For a flat, office or shop inside a building. Leave empty when the whole property is rented.",
    )
    property = models.ForeignKey(
        Property, on_delete=models.PROTECT, blank=True, related_name="leases",
        help_text="For a villa, warehouse, yard and other standalone properties. Filled in by itself when you choose a unit.",
    )
    start_date = models.DateField("Starts on")
    end_date = models.DateField("Ends on")
    annual_rent = models.DecimalField(
        "Annual rent (AED)", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))],
    )
    installments = models.PositiveSmallIntegerField(
        "Payments per year", choices=INSTALLMENT_CHOICES, default=4,
        help_text="The payment schedule is created from this when the lease is first saved.",
    )
    vat_applies = models.BooleanField(
        "VAT (5%) added to the rent", default=False,
        help_text="Commercial leases (offices, shops, warehouses ...) usually add 5% VAT to every payment.",
    )
    security_deposit = models.DecimalField(
        "Security deposit (AED)", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)],
    )
    deposit_status = models.CharField("Deposit is", max_length=10, choices=DepositStatus.choices, default=DepositStatus.HELD)
    commission = models.DecimalField(
        "Agency commission (AED)", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)],
    )
    admin_fee = models.DecimalField(
        "Admin / contract fee (AED)", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)],
    )
    ejari_fee = models.DecimalField(
        "Ejari fee (AED)", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)],
        help_text="Collected from the tenant. Becomes its own payment line.",
    )
    deposit_refund_date = models.DateField("Deposit refunded on", null=True, blank=True)
    deposit_refund_amount = models.DecimalField(
        "Amount refunded (AED)", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)],
        help_text="What was actually paid back to the tenant.",
    )
    late_fee_per_day = models.DecimalField(
        "Late fee per day (AED)", max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)],
        help_text="Optional. When set, every day a payment is overdue adds this amount (shown, not charged automatically).",
    )
    ejari_number = models.CharField("Ejari number", max_length=40, blank=True)
    ejari_expiry = models.DateField("Ejari expires on", null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    terminated_on = models.DateField("Terminated on", null=True, blank=True)
    termination_reason = models.CharField("Reason", max_length=200, blank=True)
    renewed_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="renewals",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        permissions = [("export_lease", "Can export leases to Excel / CSV")]
        constraints = [
            models.CheckConstraint(condition=models.Q(end_date__gt=models.F("start_date")), name="lease_end_after_start"),
            models.CheckConstraint(condition=models.Q(annual_rent__gt=0), name="lease_rent_positive"),
        ]
        indexes = [
            models.Index(fields=["status", "end_date"], name="lease_status_end_idx"),
            models.Index(fields=["unit", "start_date"], name="lease_unit_start_idx"),
        ]

    def __str__(self):
        return self.code or "New lease"

    # ---- what is rented ------------------------------------------------------------
    @_property
    def item_name(self):
        """'RMT-101 · Reem Marina Tower' or the property's name."""
        if self.unit_id:
            return f"{self.unit} · {self.unit.property.name}"
        return self.property.name if self.property_id else "-"

    # ---- dates and state -----------------------------------------------------------
    @_property
    def effective_end(self):
        """The last day the tenant has the place: the end date, or the early-termination date."""
        if self.status == self.Status.TERMINATED and self.terminated_on:
            return self.terminated_on
        return self.end_date

    def state(self, today=None):
        """'upcoming', 'active', 'expired' or 'terminated'."""
        today = today or timezone.localdate()
        if self.status == self.Status.TERMINATED:
            return "terminated"
        if self.start_date > today:
            return "upcoming"
        if self.end_date < today:
            return "expired"
        return "active"

    def days_left(self, today=None):
        return (self.end_date - (today or timezone.localdate())).days

    def ends_soon(self, today=None):
        return self.state(today) == "active" and self.days_left(today) <= sysconf.expiry_days()

    # ---- money ----------------------------------------------------------------------
    @_property
    def vat_amount(self):
        return money(self.annual_rent * VAT_RATE / 100) if self.vat_applies and self.annual_rent else Decimal("0.00")

    @_property
    def total_with_vat(self):
        return money(self.annual_rent or 0) + self.vat_amount

    def paid_total(self):
        """Rent received so far, VAT included (deposit and fees are not rent)."""
        return sum((p.total for p in self.payments.all() if p.is_rent and p.status == LeasePayment.Status.PAID), Decimal("0.00"))

    def scheduled_total(self):
        """All the rent the schedule asks for, VAT included (deposit and fees are not rent)."""
        return sum((p.total for p in self.payments.all() if p.is_rent), Decimal("0.00"))

    def charge_lines(self):
        """The deposit and fee lines (everything that is not rent), in a fixed order."""
        order = {kind: n for n, (kind, _field) in enumerate(CHARGE_FIELDS)}
        lines = [p for p in self.payments.all() if not p.is_rent]
        return sorted(lines, key=lambda p: (order.get(p.kind, 99), p.due_date, p.part))

    def late_fees(self, today=None):
        """Late fees that have built up on overdue rent (0 when no daily fee is set)."""
        return sum((p.late_fee(today) for p in self.payments.all() if p.is_rent), Decimal("0.00"))

    def deposit_deducted(self):
        """Money kept from the deposit (fines and other charges are not taken from it)."""
        return sum((d.amount for d in self.deductions.all() if d.kind == LeaseDeduction.Kind.DEPOSIT), Decimal("0.00"))

    def deposit_refunded(self):
        """What was paid back: the refund payment lines (and the old 'refunded' amount of earlier leases)."""
        lines = sum(
            (p.amount for p in self.payments.all() if p.kind == LeasePayment.Kind.REFUND and p.status == LeasePayment.Status.PAID),
            Decimal("0.00"),
        )
        return lines + (self.deposit_refund_amount or 0)

    def deposit_balance(self):
        """Deposit still held: deposit - deductions - what was refunded (never below zero)."""
        left = (self.security_deposit or 0) - self.deposit_deducted() - self.deposit_refunded()
        return max(money(left), Decimal("0.00"))

    # ---- rules ----------------------------------------------------------------------
    def clean(self):
        errors = {}
        # Choosing a unit fills in its property.
        if self.unit_id:
            unit_property = self.unit.property
            if self.property_id and self.property_id != unit_property.pk:
                errors["unit"] = "This unit belongs to a different property. Leave the property empty or pick the right one."
            else:
                self.property = unit_property
        if not self.property_id:
            errors.setdefault("property", "Choose the property, or a unit inside a building.")
            raise ValidationError(errors)
        prop = self.property
        if not self.unit_id and prop.has_units:
            errors["unit"] = "This property is a building. Choose the unit that is rented."

        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                errors["end_date"] = "The end date must be after the start date."
            elif self.end_date > add_months(self.start_date, 12 * MAX_YEARS):
                errors["end_date"] = f"A lease cannot be longer than {MAX_YEARS} years."

        if self.ejari_expiry and self.start_date and self.ejari_expiry < self.start_date:
            errors["ejari_expiry"] = "The Ejari expiry cannot be before the lease starts."

        if self.deposit_refund_amount and self.deposit_refund_amount > (self.security_deposit or 0):
            errors["deposit_refund_amount"] = "The refund cannot be more than the security deposit."
        if self.deposit_refund_amount and not self.deposit_refund_date:
            errors["deposit_refund_date"] = "Enter the date the deposit was paid back."
        if self.deposit_refund_date and self.start_date and self.deposit_refund_date < self.start_date:
            errors["deposit_refund_date"] = "The refund cannot be before the lease starts."

        # A deposit or fee that was already received is changed on its payment line, not here.
        if self.pk:
            for kind, field in CHARGE_FIELDS:
                line = self.payments.filter(kind=kind).first()
                if line is not None and line.status in (LeasePayment.Status.PAID, LeasePayment.Status.HELD):
                    if money(getattr(self, field) or 0) != line.amount:
                        errors[field] = (
                            f"AED {line.amount:,.2f} was already received. Change the amount on its payment line instead."
                        )

        # Early termination needs a date and a reason; an active lease keeps neither.
        if self.status == self.Status.TERMINATED:
            if not self.terminated_on:
                errors["terminated_on"] = "Enter the date the lease was terminated."
            elif self.start_date and self.end_date and not (self.start_date <= self.terminated_on <= self.end_date):
                errors["terminated_on"] = "The termination date must be between the start and end dates."
            self.termination_reason = (self.termination_reason or "").strip()
            if not self.termination_reason:
                errors["termination_reason"] = "Say why the lease was terminated."
        else:
            self.terminated_on = None
            self.termination_reason = ""

        # Tenant rules apply to a new lease (or when the tenant is changed).
        if self.tenant_id and (self.pk is None or self._tenant_changed()):
            if not self.tenant.is_active:
                errors["tenant"] = "This tenant is inactive. Choose an active tenant."
            elif self.tenant.is_blacklisted:
                errors["tenant"] = "This tenant is blacklisted, so a new lease cannot be made."
        # Inactive properties take no new leases.
        if self.pk is None and not prop.is_active:
            errors["property"] = "This property is inactive, so it cannot be leased."

        # Two leases cannot cover the same place on the same days.
        if (
            "start_date" not in errors and "end_date" not in errors
            and self.start_date and self.end_date and self.status == self.Status.ACTIVE
        ):
            clash = self._overlapping()
            if clash is not None:
                where = "unit" if self.unit_id else "property"
                errors[NON_FIELD_ERRORS] = (
                    f"This {where} already has lease {clash.code} from {clash.start_date:%d %b %Y} "
                    f"to {clash.effective_end:%d %b %Y}. Leases for the same place cannot overlap."
                )
        if errors:
            raise ValidationError(errors)

    def _tenant_changed(self):
        saved = Lease.objects.filter(pk=self.pk).values_list("tenant_id", flat=True).first()
        return saved is not None and saved != self.tenant_id

    def _overlapping(self):
        others = Lease.objects.exclude(pk=self.pk) if self.pk else Lease.objects.all()
        others = others.filter(unit=self.unit) if self.unit_id else others.filter(property=self.property, unit__isnull=True)
        for other in others:
            if other.start_date <= self.end_date and other.effective_end >= self.start_date:
                return other
        return None

    # ---- saving ---------------------------------------------------------------------
    def save(self, *args, **kwargs):
        if self.unit_id and not self.property_id:
            self.property = self.unit.property
        existed = self.pk is not None
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: next_lease_code(), lambda: super(Lease, self).save(*args, **kwargs))
        if existed:
            self.sync_charges()

    # ---- payment schedule -----------------------------------------------------------
    def schedule_rows(self):
        """[(number, due date, amount)] for the chosen number of payments (the last one takes the rounding)."""
        count = self.installments or 1
        each = money(self.annual_rent / count)
        step = 12 // count
        rows, running = [], Decimal("0")
        for number in range(1, count + 1):
            amount = each if number < count else money(self.annual_rent) - running
            running += amount
            rows.append((number, add_months(self.start_date, step * (number - 1)), amount))
        return rows

    def build_schedule(self):
        """Create the payments (only when the lease has none yet). Returns how many were created."""
        if self.payments.exists():
            return 0
        LeasePayment.objects.bulk_create([
            LeasePayment(lease=self, part=number, due_date=due, amount=amount)
            for number, due, amount in self.schedule_rows()
        ])
        self.sync_charges()
        return self.installments or 1

    def sync_charges(self):
        """
        Keep one payment line for each of the deposit and fees typed on the lease (security deposit,
        office fee, agency commission, Ejari fee). A line that was already received is never touched;
        one that is still pending follows the amount on the lease.
        """
        lines = {p.kind: p for p in self.payments.filter(kind__in=[kind for kind, _field in CHARGE_FIELDS])}
        for kind, field in CHARGE_FIELDS:
            amount = money(getattr(self, field) or 0)
            line = lines.get(kind)
            if line is None:
                if amount > 0:
                    LeasePayment.objects.create(lease=self, kind=kind, part=1, due_date=self.start_date, amount=amount)
            elif line.status == LeasePayment.Status.PENDING:
                if amount <= 0:
                    line.delete()
                elif line.amount != amount:
                    line.amount = amount
                    line.save()

    def renew(self, new_rent=None):
        """
        A new lease that starts the day after this one ends and lasts as long. `new_rent` is the
        annual rent of the new lease (the same rent when it is left out). Returns the new lease.
        """
        if self.status == self.Status.TERMINATED:
            raise ValidationError("A terminated lease cannot be renewed. Make a new lease instead.")
        if self.renewals.exists():
            raise ValidationError(f"{self.code} has already been renewed ({self.renewals.first().code}).")
        rent = money(new_rent) if new_rent is not None else self.annual_rent
        if rent <= 0:
            raise ValidationError("The rent of the new lease must be more than zero.")
        length = self.end_date - self.start_date
        start = self.end_date + timedelta(days=1)
        new = Lease(
            tenant=self.tenant, unit=self.unit, property=self.property, start_date=start, end_date=start + length,
            annual_rent=rent, installments=self.installments, vat_applies=self.vat_applies,
            security_deposit=self.security_deposit, deposit_status=self.deposit_status, late_fee_per_day=self.late_fee_per_day,
            admin_fee=self.admin_fee, ejari_fee=self.ejari_fee,
            renewed_from=self,
        )
        new.clean()
        new.save()
        return new

    def terminate(self, on, reason, cancel_unpaid=True, deposit_status=None):
        """
        End the lease early. Unpaid payments that fall due after the termination date are cancelled
        (deleted) when `cancel_unpaid` is true. Returns how many payments were cancelled.
        """
        if self.status == self.Status.TERMINATED:
            raise ValidationError(f"{self.code} has already been terminated.")
        self.status = self.Status.TERMINATED
        self.terminated_on = on
        self.termination_reason = reason
        if deposit_status:
            self.deposit_status = deposit_status
        self.clean()
        self.save()
        if cancel_unpaid:
            return self.payments.filter(
                kind=LeasePayment.Kind.RENT, status=LeasePayment.Status.PENDING, due_date__gt=on,
            ).delete()[0]
        return 0

    def assign_cheques(self, first, bank=""):
        """Number the cheques of every payment that is not paid yet, in due-date order. Returns how many."""
        match = re.fullmatch(r"(\D*)(\d+)", (first or "").strip())
        if not match:
            raise ValidationError("Enter the first cheque number, for example 000123 (letters first are fine: CQ-0042).")
        prefix, digits = match.groups()
        width, number = len(digits), int(digits)
        count = 0
        unpaid = self.payments.filter(kind=LeasePayment.Kind.RENT).exclude(
            status__in=(LeasePayment.Status.PAID, LeasePayment.Status.HELD),
        )
        for payment in unpaid.order_by("due_date", "part"):
            payment.method = LeasePayment.Method.CHEQUE
            payment.cheque_number = f"{prefix}{number:0{width}d}"[:30]
            if bank:
                payment.bank_name = bank.strip()[:60]
            payment.save()
            number += 1
            count += 1
        return count


@receiver(post_save, sender=Lease, dispatch_uid="lease-schedule")
def create_schedule(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        instance.build_schedule()


class LeasePayment(models.Model):
    """One instalment of the rent: when it is due, how it is paid and whether it has arrived."""

    class Method(models.TextChoices):
        CHEQUE = "cheque", "Cheque"
        TRANSFER = "transfer", "Bank transfer"
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        FROM_DEPOSIT = "deposit", "Kept from the deposit"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        BOUNCED = "bounced", "Bounced"
        HELD = "held", "Held (cheque not cashed)"

    class Kind(models.TextChoices):
        RENT = "rent", "Rent"
        DEPOSIT = "deposit", "Security deposit"
        OFFICE = "office", "Office fee"
        AGENCY = "agency", "Agency fee"
        EJARI = "ejari", "Ejari fee"
        FINE = "fine", "Fine"
        DEDUCTION = "deduction", "Deduction"
        REFUND = "refund", "Deposit refund"
        OTHER = "other", "Other"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="payments")
    kind = models.CharField("Type", max_length=10, choices=Kind.choices, default=Kind.RENT)
    part = models.PositiveSmallIntegerField("No.", default=1)
    due_date = models.DateField("Due on")
    amount = models.DecimalField("Amount (AED)", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CHEQUE)
    cheque_number = models.CharField("Cheque no.", max_length=30, blank=True)
    bank_name = models.CharField("Bank", max_length=60, blank=True)
    cheque_date = models.DateField("Date on cheque", null=True, blank=True)
    deposited_on = models.DateField("Deposited in bank on", null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    paid_on = models.DateField("Paid on", null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["due_date", "part", "id"]
        verbose_name = "payment"
        verbose_name_plural = "Tenant payments"
        permissions = [("export_leasepayment", "Can export tenant payments to Excel / CSV")]
        constraints = [
            models.UniqueConstraint(fields=["lease", "kind", "part"], name="payment_unique_part"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["status", "due_date"], name="payment_status_due_idx"),
            models.Index(fields=["due_date"], name="payment_due_idx"),
            models.Index(fields=["kind", "status"], name="payment_kind_status_idx"),
        ]

    def __str__(self):
        return f"{self.lease.code} {self.label.lower()}"

    @property
    def is_rent(self):
        return self.kind == self.Kind.RENT

    @property
    def label(self):
        """'Payment 2' for rent, otherwise the type: 'Security deposit'."""
        return f"Payment {self.part}" if self.is_rent else self.get_kind_display() + (f" {self.part}" if self.part > 1 else "")

    @property
    def vat(self):
        """5% VAT is added to the rent of commercial leases only (not to a deposit or a fee)."""
        if self.is_rent and self.lease.vat_applies:
            return money(self.amount * VAT_RATE / 100)
        return Decimal("0.00")

    @property
    def total(self):
        return money(self.amount) + self.vat

    def is_overdue(self, today=None):
        """Late: not received, no cheque in hand, and the due date has passed."""
        if self.kind == self.Kind.REFUND:
            return False
        return self.status not in (self.Status.PAID, self.Status.HELD) and self.due_date < (today or timezone.localdate())

    def days_overdue(self, today=None):
        """How many days late (0 when paid or not yet due)."""
        today = today or timezone.localdate()
        return (today - self.due_date).days if self.is_overdue(today) else 0

    def late_fee(self, today=None):
        rate = self.lease.late_fee_per_day or 0
        return money(rate * self.days_overdue(today)) if rate and self.is_rent else Decimal("0.00")

    def record(self, on, method, cheque_number="", bank_name=""):
        """The money has arrived."""
        if method not in self.Method.values:
            raise ValidationError("Choose how the payment was made.")
        self.status = self.Status.PAID
        self.paid_on = on
        self.method = method
        self.cheque_number = (cheque_number or "").strip()[:30]
        self.bank_name = (bank_name or "").strip()[:60]
        self.save()

    def hold(self, on, cheque_number="", bank_name=""):
        """A cheque was received but is kept, not cashed (a blank or post-dated cheque)."""
        self.status = self.Status.HELD
        self.paid_on = on
        self.method = self.Method.CHEQUE
        self.cheque_number = (cheque_number or "").strip()[:30]
        self.bank_name = (bank_name or "").strip()[:60]
        self.save()

    def bounce(self):
        """The cheque came back unpaid."""
        self.status = self.Status.BOUNCED
        self.save()

    def clean(self):
        if self.status in (self.Status.PAID, self.Status.HELD):
            if not self.paid_on:
                self.paid_on = timezone.localdate()
        else:
            self.paid_on = None
        if self.method != self.Method.CHEQUE:
            self.cheque_number = ""
            self.bank_name = ""
            self.cheque_date = None
            self.deposited_on = None
        elif self.cheque_date and self.deposited_on and self.deposited_on < self.cheque_date:
            raise ValidationError({"deposited_on": "A cheque cannot be deposited before the date written on it."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class LeaseDocument(models.Model):
    """The signed contract, Ejari certificate, cheque copies ... with an optional expiry date."""

    class DocType(models.TextChoices):
        CONTRACT = "contract", "Tenancy contract"
        EJARI = "ejari", "Ejari certificate"
        CHEQUES = "cheques", "Cheque copies"
        RECEIPT = "receipt", "Receipt"
        OTHER = "other", "Other"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField("Document", max_length=20, choices=DocType.choices)
    number = models.CharField("Number", max_length=60, blank=True)
    expiry_date = models.DateField("Expires on", null=True, blank=True)
    file = models.FileField(
        "File", upload_to=lease_document_path, blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "webp"]), validate_document_size, validate_file_content],
        help_text="PDF, JPG, PNG or WebP.",
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["doc_type", "expiry_date"]
        verbose_name = "document"
        verbose_name_plural = "Documents"

    def __str__(self):
        return f"{self.get_doc_type_display()} {self.number}".strip()

    @property
    def expiry_status(self):
        if self.expiry_date is None:
            return "none"
        today = timezone.localdate()
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + timedelta(days=sysconf.expiry_days()):
            return "soon"
        return "valid"


@receiver(post_delete, sender=LeaseDocument)
def remove_lease_document_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)


class LeaseDeduction(models.Model):
    """
    Money kept from the tenant, with the date and the reason: taken from the security deposit, a fine, or
    another charge. Saving one also puts a line in the Payments module against this lease.
    """

    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "Taken from the deposit"
        FINE = "fine", "Fine"
        OTHER = "other", "Other charge"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="deductions")
    kind = models.CharField("Type", max_length=10, choices=Kind.choices, default=Kind.DEPOSIT)
    date = models.DateField("Date", default=timezone.localdate)
    reason = models.CharField("Reason", max_length=200)
    note = models.CharField("Note", max_length=200, blank=True)
    amount = models.DecimalField("Amount (AED)", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    payment = models.OneToOneField(
        "LeasePayment", on_delete=models.CASCADE, null=True, blank=True, editable=False, related_name="deduction",
    )

    class Meta:
        ordering = ["date", "id"]
        verbose_name = "deduction"
        verbose_name_plural = "Deductions"

    def __str__(self):
        return f"{self.reason} ({self.amount})"

    def clean(self):
        lease = self.lease if self.lease_id else None
        if lease is not None and self.amount and self.kind == self.Kind.DEPOSIT:
            others = sum((d.amount for d in lease.deductions.exclude(pk=self.pk) if d.kind == self.Kind.DEPOSIT), Decimal("0"))
            if others + self.amount > (lease.security_deposit or 0):
                raise ValidationError({"amount": "The deductions cannot be more than the security deposit."})

    # ---- the matching line in the Payments module ---------------------------------------------
    def _line_values(self):
        """(payment type, status, method) of the payment line this deduction makes."""
        if self.kind == self.Kind.DEPOSIT:
            return LeasePayment.Kind.DEDUCTION, LeasePayment.Status.PAID, LeasePayment.Method.FROM_DEPOSIT
        if self.kind == self.Kind.FINE:
            return LeasePayment.Kind.FINE, LeasePayment.Status.PENDING, LeasePayment.Method.CASH
        return LeasePayment.Kind.OTHER, LeasePayment.Status.PENDING, LeasePayment.Method.CASH

    def sync_payment(self):
        kind, status, method = self._line_values()
        text = (self.reason + (f" - {self.note}" if self.note else ""))[:200]
        line = self.payment
        if line is None:
            part = (self.lease.payments.filter(kind=kind).aggregate(m=models.Max("part"))["m"] or 0) + 1
            line = LeasePayment(lease=self.lease, kind=kind, part=part, status=status, method=method)
        elif line.kind != kind:  # the type was changed: start the line over as the new type
            line.kind = kind
            line.part = (self.lease.payments.filter(kind=kind).exclude(pk=line.pk).aggregate(m=models.Max("part"))["m"] or 0) + 1
            line.status, line.method = status, method
        line.amount, line.due_date, line.notes = self.amount, self.date, text
        if line.status == LeasePayment.Status.PAID and kind == LeasePayment.Kind.DEDUCTION:
            line.paid_on = self.date
        line.save()
        if self.payment_id != line.pk:
            self.payment = line
            LeaseDeduction.objects.filter(pk=self.pk).update(payment=line)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.sync_payment()

    def delete(self, *args, **kwargs):
        line = self.payment
        result = super().delete(*args, **kwargs)
        if line is not None and line.pk:
            line.delete()
        return result


class LeaseNote(models.Model):
    """A dated note on the lease (a phone call, a promise to pay, a complaint ...)."""

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="lease_notes")
    text = models.TextField("Note")
    author = models.CharField("Written by", max_length=150, blank=True, editable=False)
    created_at = models.DateTimeField("When", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "note"
        verbose_name_plural = "Notes"

    def __str__(self):
        return self.text[:40]
