"""Receipts (money received) and invoices (money asked for) for the tenant payments."""
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from leases.models import Lease, LeasePayment
from properties.models import save_with_unique_code
from tenants.models import Tenant

MONEY = [MinValueValidator(Decimal("0"))]
NO_RECEIPT_KINDS = (LeasePayment.Kind.REFUND, LeasePayment.Kind.DEDUCTION)  # money going out, or kept from the deposit


def _next_code(model, prefix, width=4):
    """RCP-0001, RCP-0002 ... (one more than the highest so far; fast even with many documents)."""
    from django.db.models.functions import Length

    last = model.objects.exclude(code__isnull=True).order_by(Length("code").desc(), "-code").values_list("code", flat=True).first()
    match = re.fullmatch(rf"{prefix}-(\d+)", last or "")
    return f"{prefix}-{(int(match.group(1)) if match else 0) + 1:0{width}d}"


class Status(models.TextChoices):
    ISSUED = "issued", "Issued"
    VOIDED = "voided", "Voided"


def describe(payment):
    """What the money is for: 'Rent, payment 2 · RMT-101 · Reem Marina Tower (LSE-0004)'."""
    lease = payment.lease
    what = f"Rent, payment {payment.part}" if payment.is_rent else payment.label
    return f"{what} · {lease.item_name} ({lease.code})"


class _Document(models.Model):
    """What a receipt and an invoice have in common."""

    code = models.CharField("Number", max_length=20, unique=True, null=True, blank=True, editable=False)
    payment = models.ForeignKey(LeasePayment, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    lease = models.ForeignKey(Lease, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    tenant = models.ForeignKey(Tenant, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # Copies, so the document still reads the same after names change or the lease is removed.
    tenant_name = models.CharField(max_length=200, editable=False)
    tenant_trn = models.CharField(max_length=20, blank=True, editable=False)
    description = models.CharField("For", max_length=300, editable=False)
    issued_on = models.DateField("Date", default=timezone.localdate)
    amount = models.DecimalField("Amount (AED)", max_digits=12, decimal_places=2, editable=False, validators=MONEY)
    vat = models.DecimalField("VAT (AED)", max_digits=12, decimal_places=2, default=0, editable=False, validators=MONEY)
    total = models.DecimalField("Total (AED)", max_digits=12, decimal_places=2, editable=False, validators=MONEY)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ISSUED)
    voided_on = models.DateField(null=True, blank=True, editable=False)
    void_reason = models.CharField("Reason for voiding", max_length=200, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    PREFIX = ""

    class Meta:
        abstract = True
        ordering = ["-issued_on", "-id"]

    def __str__(self):
        return f"{self.code or 'New'} · {self.tenant_name}"

    def fill_from(self, payment):
        self.payment = payment
        self.lease = payment.lease
        self.tenant = payment.lease.tenant
        self.tenant_name = payment.lease.tenant.name
        self.tenant_trn = getattr(payment.lease.tenant, "trn", "") or ""
        self.description = describe(payment)[:300]
        self.amount = payment.amount
        self.vat = payment.vat
        self.total = payment.total

    def save(self, *args, **kwargs):
        cls = type(self)
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: _next_code(cls, self.PREFIX), lambda: super(_Document, self).save(*args, **kwargs))

    def clean(self):
        errors = {}
        if self.status == Status.VOIDED:
            if not (self.void_reason or "").strip():
                errors["void_reason"] = "Write the reason for voiding this document."
            elif not self.voided_on:
                self.voided_on = timezone.localdate()
        else:
            self.void_reason = ""
            self.voided_on = None
            if self.pk and type(self).objects.filter(pk=self.pk, status=Status.VOIDED).exists():
                errors["status"] = "A voided document cannot be issued again. Issue a new one from the payment."
        if errors:
            raise ValidationError(errors)

    def void(self, reason):
        self.status = Status.VOIDED
        self.void_reason = reason[:200]
        self.voided_on = timezone.localdate()
        self.save()


class Receipt(_Document):
    """Proof that a payment was received. Made by itself when a payment is marked as paid."""

    PREFIX = "RCP"
    method = models.CharField(max_length=10, choices=LeasePayment.Method.choices, default=LeasePayment.Method.CASH, editable=False)
    cheque_number = models.CharField("Cheque no.", max_length=30, blank=True, editable=False)
    bank_name = models.CharField("Bank", max_length=60, blank=True, editable=False)

    class Meta(_Document.Meta):
        verbose_name = "receipt"
        verbose_name_plural = "Receipts"
        permissions = [("export_receipt", "Can export receipts to Excel / CSV")]
        constraints = [
            models.UniqueConstraint(
                fields=["payment"], condition=models.Q(status="issued"), name="one_issued_receipt_per_payment",
            ),
        ]

    def fill_from(self, payment):
        super().fill_from(payment)
        self.issued_on = payment.paid_on or timezone.localdate()
        self.method = payment.method
        self.cheque_number = payment.cheque_number
        self.bank_name = payment.bank_name

    @property
    def method_text(self):
        text = dict(LeasePayment.Method.choices).get(self.method, self.method)
        extra = " ".join(x for x in (f"no. {self.cheque_number}" if self.cheque_number else "", self.bank_name) if x)
        return f"{text} ({extra})" if extra else text


class Invoice(_Document):
    """A request for payment, with VAT where it applies."""

    PREFIX = "INV"
    due_date = models.DateField("Due on", null=True, blank=True, editable=False)

    class Meta(_Document.Meta):
        verbose_name = "invoice"
        verbose_name_plural = "Invoices"
        permissions = [("export_invoice", "Can export invoices to Excel / CSV")]
        constraints = [
            models.UniqueConstraint(
                fields=["payment"], condition=models.Q(status="issued"), name="one_issued_invoice_per_payment",
            ),
        ]

    def fill_from(self, payment):
        super().fill_from(payment)
        self.due_date = payment.due_date
        self.issued_on = timezone.localdate()

    @property
    def is_tax_invoice(self):
        return self.vat > 0

    @property
    def payment_state(self):
        """'paid', 'overdue' or 'open' (read from the payment)."""
        payment = self.payment
        if payment is None:
            return "open"
        if payment.status == LeasePayment.Status.PAID:
            return "paid"
        return "overdue" if payment.is_overdue() else "open"


# ---------------------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------------------
def issue_receipt(payment):
    """
    Make the receipt for a paid payment (nothing is made twice). If the payment changed after its receipt was issued
    (a different amount), the old receipt is voided and a new one made. Returns the receipt, or None when none is due.
    """
    from core.audit import suppressed

    if payment.status != LeasePayment.Status.PAID or payment.kind in NO_RECEIPT_KINDS or payment.method == LeasePayment.Method.FROM_DEPOSIT:
        return None
    current = Receipt.objects.filter(payment=payment, status=Status.ISSUED).first()
    if current is not None:
        if (current.amount, current.vat) == (payment.amount, payment.vat):
            return current
        current.void("The payment was changed. A new receipt was issued.")
    receipt = Receipt()
    receipt.fill_from(payment)
    with suppressed():  # making a receipt is not a person's edit; voiding or changing one is recorded
        receipt.save()
    return receipt


def void_receipt_if_not_paid(payment):
    """A payment that is no longer paid (a cheque that bounced ...) cannot keep its receipt."""
    if payment.status == LeasePayment.Status.PAID:
        return
    for receipt in Receipt.objects.filter(payment=payment, status=Status.ISSUED):
        receipt.void(f"The payment is now marked as {payment.get_status_display().lower()}.")


def issue_invoice(payment):
    """Make the invoice for a payment that is asked for (not for money going out, and not for a payment that is already paid or waived)."""
    from core.audit import suppressed

    if payment.kind in NO_RECEIPT_KINDS:
        raise ValidationError("An invoice is not made for a refund or a deduction.")
    if payment.status in (LeasePayment.Status.WAIVED,):
        raise ValidationError("This payment was waived.")
    current = Invoice.objects.filter(payment=payment, status=Status.ISSUED).first()
    if current is not None:
        return current, False
    invoice = Invoice()
    invoice.fill_from(payment)
    with suppressed():
        invoice.save()
    return invoice, True
