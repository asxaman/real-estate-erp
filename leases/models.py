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
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: next_lease_code(), lambda: super(Lease, self).save(*args, **kwargs))

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
        return self.installments or 1

    def renew(self):
        """A new lease that starts the day after this one ends and lasts as long. Returns it."""
        if self.status == self.Status.TERMINATED:
            raise ValidationError("A terminated lease cannot be renewed. Make a new lease instead.")
        if self.renewals.exists():
            raise ValidationError(f"{self.code} has already been renewed ({self.renewals.first().code}).")
        length = self.end_date - self.start_date
        start = self.end_date + timedelta(days=1)
        new = Lease(
            tenant=self.tenant, unit=self.unit, property=self.property, start_date=start, end_date=start + length,
            annual_rent=self.annual_rent, installments=self.installments, vat_applies=self.vat_applies,
            security_deposit=self.security_deposit, deposit_status=self.deposit_status, renewed_from=self,
        )
        new.clean()
        new.save()
        return new



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
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        BOUNCED = "bounced", "Bounced"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="payments")
    part = models.PositiveSmallIntegerField("No.", default=1)
    due_date = models.DateField("Due on")
    amount = models.DecimalField("Amount (AED)", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CHEQUE)
    cheque_number = models.CharField("Cheque no.", max_length=30, blank=True)
    bank_name = models.CharField("Bank", max_length=60, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    paid_on = models.DateField("Paid on", null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["due_date", "part"]
        verbose_name = "payment"
        verbose_name_plural = "Payments"

    def __str__(self):
        return f"{self.lease.code} payment {self.part}"

    @property
    def vat(self):
        return money(self.amount * VAT_RATE / 100) if self.lease.vat_applies else Decimal("0.00")

    @property
    def total(self):
        return money(self.amount) + self.vat

    def is_overdue(self, today=None):
        return self.status != self.Status.PAID and self.due_date < (today or timezone.localdate())

    def clean(self):
        if self.status == self.Status.PAID:
            if not self.paid_on:
                self.paid_on = timezone.localdate()
        else:
            self.paid_on = None
        if self.method != self.Method.CHEQUE:
            self.cheque_number = ""
            self.bank_name = ""

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
