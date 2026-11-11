"""Money going out: vendor bills (what vendors ask for), expenses (costs paid directly) and payment vouchers (proof of payment)."""
import re
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Length
from django.utils import timezone

from core.validators import validate_file_content
from maintenance.models import MaintenanceRequest, Vendor
from properties.models import Property, Unit, save_with_unique_code, validate_document_size
from tenants.models import Tenant

_property = property  # the models below have a field called "property", which hides the built-in name
VAT_RATE = Decimal("5")
FILE_TYPES = ["pdf", "jpg", "jpeg", "png", "webp"]
CENT = Decimal("0.01")
POSITIVE = [MinValueValidator(Decimal("0.01"))]


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def bill_file_path(instance, filename):
    return f"bill_files/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def expense_file_path(instance, filename):
    return f"expense_files/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def _next_code(model, prefix, width=4):
    last = model.objects.exclude(code__isnull=True).order_by(Length("code").desc(), "-code").values_list("code", flat=True).first()
    match = re.fullmatch(rf"{prefix}-(\d+)", last or "")
    return f"{prefix}-{(int(match.group(1)) if match else 0) + 1:0{width}d}"


def _file(upload_to):
    return models.FileField(
        upload_to=upload_to, blank=True, validators=[FileExtensionValidator(FILE_TYPES), validate_document_size, validate_file_content],
        help_text="A scan or photo (PDF, JPG, PNG or WebP).",
    )


class Category(models.TextChoices):
    MAINTENANCE = "maintenance", "Maintenance & repairs"
    UTILITIES = "utilities", "Utilities (DEWA, gas, chilled water)"
    GOVERNMENT = "government", "Government & municipality fees"
    INSURANCE = "insurance", "Insurance"
    CLEANING = "cleaning", "Cleaning & pest control"
    SECURITY = "security", "Security"
    LEGAL = "legal", "Legal & professional fees"
    MARKETING = "marketing", "Marketing & advertising"
    OFFICE = "office", "Office & supplies"
    BANK = "bank", "Bank charges"
    OTHER = "other", "Other"


class ChargedTo(models.TextChoices):
    COMPANY = "company", "Company"
    OWNER = "owner", "Owner"
    TENANT = "tenant", "Tenant"


class PayMethod(models.TextChoices):
    TRANSFER = "transfer", "Bank transfer"
    CHEQUE = "cheque", "Cheque"
    CASH = "cash", "Cash"
    CARD = "card", "Card"
    OTHER = "other", "Other"


class _Cost(models.Model):
    """What a bill and an expense have in common: where it belongs, who bears it, the amount and the VAT."""

    code = models.CharField("Number", max_length=20, unique=True, null=True, blank=True, editable=False)
    category = models.CharField(max_length=12, choices=Category.choices, default=Category.OTHER)
    description = models.CharField("What is it for?", max_length=200)
    property = models.ForeignKey(
        Property, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="Leave empty for a cost of the company itself (office rent, licences ...).",
    )
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    tenant = models.ForeignKey(Tenant, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    charged_to = models.CharField("Charged to", max_length=8, choices=ChargedTo.choices, default=ChargedTo.COMPANY)
    amount = models.DecimalField("Amount before VAT (AED)", max_digits=12, decimal_places=2, validators=POSITIVE)
    vat_applies = models.BooleanField("Add 5% VAT", default=False)
    vat = models.DecimalField("VAT (AED)", max_digits=12, decimal_places=2, default=0, editable=False)
    total = models.DecimalField("Total (AED)", max_digits=12, decimal_places=2, default=0, editable=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    PREFIX = ""

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.code or 'New'} · {self.description}"

    def compute_totals(self):
        if self.amount is not None:
            self.vat = money(self.amount * VAT_RATE / 100) if self.vat_applies else Decimal("0.00")
            self.total = money(self.amount) + self.vat

    @_property
    def where(self):
        if self.unit_id:
            return f"{self.unit} · {self.property.name}"
        return self.property.name if self.property_id else "Company"

    def clean_cost(self):
        """The rules both share. Returns a dict of errors."""
        errors = {}
        if self.unit_id:
            if self.property_id and self.unit.property_id != self.property_id:
                errors["unit"] = f"Unit {self.unit} is not in {self.property.name}."
            elif not self.property_id:
                self.property = self.unit.property
        if self.property_id and self.unit_id and not self.property.has_units:
            errors["unit"] = "This property has no units. Leave the unit empty."
        if self.charged_to == ChargedTo.OWNER:
            if not self.property_id:
                errors["charged_to"] = "Choose the property: the owner is found from it."
            elif self.property.owner.is_company:
                self.charged_to = ChargedTo.COMPANY  # the company owns it: there is no other owner to charge
        if self.charged_to == ChargedTo.TENANT and not self.tenant_id:
            errors["tenant"] = "Choose the tenant who pays."
        if self.charged_to != ChargedTo.TENANT and self.tenant_id and "tenant" not in errors:
            self.tenant = None
        return errors

    def save(self, *args, **kwargs):
        self.compute_totals()
        cls = type(self)
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: _next_code(cls, self.PREFIX), lambda: super(_Cost, self).save(*args, **kwargs))


# ---------------------------------------------------------------------------------------
# Vendor bills
# ---------------------------------------------------------------------------------------
class VendorBill(_Cost):
    """An invoice received from a vendor. It can be paid in one go or in parts (each part has a payment voucher)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CANCELLED = "cancelled", "Cancelled"

    PREFIX = "BILL"
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="bills")
    bill_number = models.CharField("Vendor's invoice no.", max_length=60, blank=True)
    bill_date = models.DateField("Bill date", default=timezone.localdate)
    due_date = models.DateField("Due on", null=True, blank=True)
    request = models.ForeignKey(
        MaintenanceRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="bills", verbose_name="Maintenance job",
    )
    file = _file(bill_file_path)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    cancel_reason = models.CharField("Reason for cancelling", max_length=200, blank=True)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)

    class Meta:
        ordering = ["-bill_date", "-id"]
        verbose_name = "vendor bill"
        verbose_name_plural = "Vendor bills"
        permissions = [("export_vendorbill", "Can export vendor bills to Excel / CSV")]
        constraints = [
            models.UniqueConstraint(fields=["request"], condition=models.Q(status="open"), name="one_open_bill_per_job"),
            models.UniqueConstraint(
                fields=["vendor", "bill_number"], condition=~models.Q(bill_number="") & models.Q(status="open"),
                name="unique_open_bill_number_per_vendor",
                violation_error_message="This vendor's invoice number is already entered.",
            ),
        ]
        indexes = [models.Index(fields=["status", "due_date"], name="bill_status_due_idx")]

    @_property
    def balance(self):
        return money(self.total - self.paid_amount)

    def payment_state(self, today=None):
        """'cancelled', 'paid', 'part', 'overdue' or 'unpaid'."""
        today = today or timezone.localdate()
        if self.status == self.Status.CANCELLED:
            return "cancelled"
        if self.balance <= 0:
            return "paid"
        if self.due_date and self.due_date < today:
            return "overdue"
        return "part" if self.paid_amount > 0 else "unpaid"

    def refresh_paid(self):
        paid = sum((v.amount for v in self.vouchers.filter(status="issued")), Decimal("0.00"))
        type(self).objects.filter(pk=self.pk).update(paid_amount=paid)
        self.paid_amount = paid

    def clean(self):
        errors = self.clean_cost()
        today = timezone.localdate()
        self.compute_totals()
        if self.bill_date and self.bill_date > today + timedelta(days=1):
            errors["bill_date"] = "The bill date cannot be in the future."
        if self.due_date and self.bill_date and self.due_date < self.bill_date:
            errors["due_date"] = "The due date cannot be before the bill date."
        if self.vendor_id and not self.vendor.is_active and (self._state.adding or not type(self).objects.filter(pk=self.pk, vendor_id=self.vendor_id).exists()):
            errors["vendor"] = "This vendor is inactive."
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).first()
            if self.total is not None and self.total < self.paid_amount:
                errors["amount"] = f"AED {self.paid_amount:,.2f} is already paid on this bill. The total cannot be lower."
            if old is not None and old.paid_amount > 0 and old.vendor_id != self.vendor_id:
                errors["vendor"] = "Payments were made to this vendor. The vendor cannot be changed."
            if self.status == self.Status.CANCELLED and self.paid_amount > 0:
                errors["status"] = "Payments were made on this bill. Void those payment vouchers first."
        if self.status == self.Status.CANCELLED and not (self.cancel_reason or "").strip() and "status" not in errors:
            errors["cancel_reason"] = "Write the reason for cancelling this bill."
        if self.status != self.Status.CANCELLED:
            self.cancel_reason = ""
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------------------
class Expense(_Cost):
    """A cost paid directly, with no vendor bill (DEWA, a government fee, insurance ...). Its payment voucher is made by itself."""

    PREFIX = "EXP"
    expense_date = models.DateField("Paid on", default=timezone.localdate)
    payee = models.CharField("Paid to", max_length=150)
    method = models.CharField(max_length=10, choices=PayMethod.choices, default=PayMethod.TRANSFER)
    reference = models.CharField("Cheque / reference no.", max_length=60, blank=True)
    file = _file(expense_file_path)
    cancelled = models.BooleanField(default=False)
    cancel_reason = models.CharField("Reason for cancelling", max_length=200, blank=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        verbose_name = "expense"
        verbose_name_plural = "Expenses"
        permissions = [("export_expense", "Can export expenses to Excel / CSV")]

    def clean(self):
        errors = self.clean_cost()
        self.compute_totals()
        self.payee = " ".join((self.payee or "").split())
        if self.expense_date and self.expense_date > timezone.localdate():
            errors["expense_date"] = "The payment date cannot be in the future."
        if self.cancelled and not (self.cancel_reason or "").strip():
            errors["cancel_reason"] = "Write the reason for cancelling this expense."
        if not self.cancelled:
            self.cancel_reason = ""
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        sync_expense_voucher(self)


# ---------------------------------------------------------------------------------------
# Payment vouchers
# ---------------------------------------------------------------------------------------
class PaymentVoucher(models.Model):
    """Proof that money was paid out: for a vendor bill (in full or in part), for an expense, or for something else."""

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        VOIDED = "voided", "Voided"

    code = models.CharField("Voucher no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    voucher_date = models.DateField("Date", default=timezone.localdate)
    bill = models.ForeignKey(VendorBill, on_delete=models.PROTECT, null=True, blank=True, related_name="vouchers")
    expense = models.ForeignKey(Expense, on_delete=models.PROTECT, null=True, blank=True, related_name="vouchers", editable=False)
    payee_name = models.CharField("Paid to", max_length=200, blank=True)
    description = models.CharField("For", max_length=250, blank=True)
    amount = models.DecimalField("Amount (AED)", max_digits=12, decimal_places=2, validators=POSITIVE)
    method = models.CharField(max_length=10, choices=PayMethod.choices, default=PayMethod.TRANSFER)
    cheque_number = models.CharField("Cheque no.", max_length=30, blank=True)
    bank_name = models.CharField("Bank", max_length=60, blank=True)
    reference = models.CharField("Transfer reference", max_length=60, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ISSUED)
    voided_on = models.DateField(null=True, blank=True, editable=False)
    void_reason = models.CharField("Reason for voiding", max_length=200, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-voucher_date", "-id"]
        verbose_name = "payment voucher"
        verbose_name_plural = "Payment vouchers"
        permissions = [("export_paymentvoucher", "Can export payment vouchers to Excel / CSV")]

    def __str__(self):
        return f"{self.code or 'New'} · {self.payee_name}"

    @_property
    def method_text(self):
        text = dict(PayMethod.choices).get(self.method, self.method)
        extra = " ".join(x for x in (f"no. {self.cheque_number}" if self.cheque_number else "", self.bank_name, self.reference) if x)
        return f"{text} ({extra})" if extra else text

    def save(self, *args, **kwargs):
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: _next_code(PaymentVoucher, "PV"), lambda: super(PaymentVoucher, self).save(*args, **kwargs))
        if self.bill_id:
            self.bill.refresh_paid()

    def clean(self):
        errors = {}
        today = timezone.localdate()
        old = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if self.bill_id:
            if self.bill.status == VendorBill.Status.CANCELLED:
                errors["bill"] = "This bill is cancelled."
            if not self.payee_name:
                self.payee_name = self.bill.vendor.name
            if not self.description:
                ref = f" {self.bill.bill_number}" if self.bill.bill_number else ""
                self.description = f"Payment of bill {self.bill.code}{ref} · {self.bill.description}"[:250]
            if self.amount is not None and self.status == self.Status.ISSUED:
                already = sum(
                    (v.amount for v in self.bill.vouchers.filter(status="issued").exclude(pk=self.pk)), Decimal("0.00"))
                left = self.bill.total - already
                if self.amount > left:
                    errors["amount"] = f"Only AED {left:,.2f} is left to pay on this bill."
        elif not self.payee_name.strip():
            errors["payee_name"] = "Enter who is paid."
        if old is not None and old.status == self.Status.VOIDED and self.status == self.Status.ISSUED:
            errors["status"] = "A voided voucher cannot be issued again. Make a new one."
        if old is not None and self.status == self.Status.ISSUED and (old.amount, old.bill_id) != (self.amount, self.bill_id):
            errors["amount"] = "An issued voucher cannot be changed. Void it and make a new one."
        if self.voucher_date and self.voucher_date > today:
            errors["voucher_date"] = "The date cannot be in the future."
        if self.method == PayMethod.CHEQUE and not self.cheque_number.strip() and "cheque_number" not in errors:
            errors["cheque_number"] = "Enter the cheque number."
        if self.status == self.Status.VOIDED:
            if self.expense_id and not self.expense.cancelled:
                errors["status"] = "This voucher belongs to an expense. Cancel the expense instead (its voucher is voided with it)."
            if not (self.void_reason or "").strip():
                errors["void_reason"] = "Write the reason for voiding this voucher."
            elif not self.voided_on:
                self.voided_on = today
        else:
            self.void_reason = ""
            self.voided_on = None
        if errors:
            raise ValidationError(errors)

    def void(self, reason):
        self.status = self.Status.VOIDED
        self.void_reason = reason[:200]
        self.voided_on = timezone.localdate()
        self.save()


def sync_expense_voucher(expense):
    """Every expense has one payment voucher; a cancelled expense has its voucher voided."""
    from core.audit import suppressed

    voucher = expense.vouchers.filter(status="issued").first()
    if expense.cancelled:
        if voucher is not None:
            voucher.void(f"The expense was cancelled: {expense.cancel_reason}")
        return
    wanted = dict(
        voucher_date=expense.expense_date, payee_name=expense.payee, description=expense.description[:250], amount=expense.total,
        method=expense.method, reference=expense.reference if expense.method != PayMethod.CHEQUE else "",
        cheque_number=expense.reference[:30] if expense.method == PayMethod.CHEQUE else "",
    )
    if voucher is None:
        with suppressed():
            PaymentVoucher(expense=expense, **wanted).save()
        return
    if any(getattr(voucher, key) != value for key, value in wanted.items()):
        for key, value in wanted.items():
            setattr(voucher, key, value)
        PaymentVoucher.objects.filter(pk=voucher.pk).update(**wanted)  # kept in step with the expense (the expense is the record)


def create_bill_for_job(job, today=None):
    """The vendor bill for a finished maintenance job. Returns (bill, created)."""
    today = today or timezone.localdate()
    if job.status != MaintenanceRequest.Status.COMPLETED or not job.vendor_id or job.final_cost is None:
        raise ValidationError("Only a completed job with a vendor and a final cost can be billed.")
    if job.final_cost <= 0:
        raise ValidationError("The job was free: there is nothing to pay.")
    existing = VendorBill.objects.filter(request=job, status=VendorBill.Status.OPEN).first()
    if existing is not None:
        return existing, False
    charged = {"owner": ChargedTo.OWNER, "tenant": ChargedTo.TENANT, "company": ChargedTo.COMPANY}[job.paid_by]
    bill = VendorBill(
        vendor=job.vendor, request=job, property=job.property, unit=job.unit, tenant=job.tenant if charged == ChargedTo.TENANT else None,
        category=Category.MAINTENANCE, description=f"{job.code} · {job.title}"[:200], amount=job.final_cost, charged_to=charged,
        bill_date=job.completed_on or today, due_date=(job.completed_on or today) + timedelta(days=30),
    )
    bill.full_clean(exclude=["file"])
    bill.save()
    return bill, True
