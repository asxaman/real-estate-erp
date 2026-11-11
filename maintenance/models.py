"""Vendors (the outside companies that do the work) and maintenance requests (the jobs)."""
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from core import sysconf
from core.validators import validate_file_content
from properties.models import (
    Property,
    Unit,
    save_with_unique_code,
    validate_document_rules,
    validate_document_size,
    validate_trn,
    clean_trn,
)
from properties.models_constants import PHONE_ERROR, PHONE_REGEX
from tenants.models import Tenant

OPEN_STATUSES = ("new", "awaiting_approval", "approved", "in_progress")
FILE_TYPES = ["pdf", "jpg", "jpeg", "png", "webp"]
_property = property  # MaintenanceRequest has a field called "property", which hides the built-in name
MONEY = [MinValueValidator(Decimal("0"))]


def vendor_document_path(instance, filename):
    return f"vendor_documents/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def maintenance_file_path(instance, filename):
    return f"maintenance_files/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def _next_code(model, prefix, width=4):
    numbers = []
    for code in model.objects.exclude(code__isnull=True).values_list("code", flat=True):
        match = re.fullmatch(rf"{prefix}-(\d+)", code)
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}-{max(numbers, default=0) + 1:0{width}d}"


class Trade(models.TextChoices):
    GENERAL = "general", "General maintenance"
    PLUMBING = "plumbing", "Plumbing"
    ELECTRICAL = "electrical", "Electrical"
    AC = "ac", "Air conditioning"
    PAINTING = "painting", "Painting"
    CARPENTRY = "carpentry", "Carpentry"
    CLEANING = "cleaning", "Cleaning"
    PEST = "pest", "Pest control"
    LANDSCAPING = "landscaping", "Landscaping"
    LIFT = "lift", "Lifts"
    SECURITY = "security", "Security"
    OTHER = "other", "Other"


class Vendor(models.Model):
    """An outside company or contractor that does maintenance work."""

    name = models.CharField("Vendor name", max_length=200)
    # Assigned automatically (VND-0001, VND-0002 ...).
    code = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    trade = models.CharField("Main trade", max_length=14, choices=Trade.choices, default=Trade.GENERAL)
    other_trades = models.CharField(
        "Other trades", max_length=200, blank=True, help_text="Optional. Anything else they do, for example: painting, cleaning.",
    )
    contact_person = models.CharField("Contact person", max_length=150, blank=True)
    phone = models.CharField(
        "Phone", max_length=30, validators=[RegexValidator(PHONE_REGEX, PHONE_ERROR)], help_text="Example: +971 50 123 4567.",
    )
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    trade_licence_number = models.CharField("Trade licence number", max_length=60, blank=True)
    trn = models.CharField(
        "Tax registration number (TRN)", max_length=20, blank=True, validators=[validate_trn],
        help_text="Optional. 15 digits. Needed to receive their VAT invoices.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    inactive_reason = models.CharField(
        "Reason for being inactive", max_length=200, blank=True,
        help_text="Optional. Why this vendor is no longer used.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    REQUIRED_DOCUMENTS = ("trade_licence",)

    class Meta:
        ordering = ["name"]
        permissions = [("export_vendor", "Can export vendors to Excel / CSV")]
        constraints = [
            models.UniqueConstraint(
                Lower("name"), name="unique_vendor_name_ci",
                violation_error_message="A vendor with this name already exists.",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join((self.name or "").split())
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: _next_code(Vendor, "VND"), lambda: super(Vendor, self).save(*args, **kwargs))

    def open_jobs(self):
        return self.requests.filter(status__in=OPEN_STATUSES)

    def clean(self):
        self.name = " ".join((self.name or "").split())
        self.email = (self.email or "").strip().lower()
        self.trn = clean_trn(self.trn)
        errors = {}
        if self.is_active:
            self.inactive_reason = ""
        elif self.pk:
            open_jobs = self.open_jobs().count()
            if open_jobs:
                errors["is_active"] = (
                    f"This vendor has {open_jobs} open maintenance job{'' if open_jobs == 1 else 's'}. "
                    "Finish, cancel or give those jobs to another vendor first."
                )
        if errors:
            raise ValidationError(errors)

    def missing_documents(self):
        have = {doc.doc_type for doc in self.documents.all()}
        labels = dict(VendorDocument.DocType.choices)
        return [labels[kind] for kind in self.REQUIRED_DOCUMENTS if kind not in have]

    def document_counts(self):
        """(expired, expiring soon) among this vendor's documents."""
        today = timezone.localdate()
        soon_limit = today + timedelta(days=sysconf.expiry_days())
        expired = self.documents.filter(expiry_date__lt=today).count()
        soon = self.documents.filter(expiry_date__gte=today, expiry_date__lte=soon_limit).count()
        return expired, soon


class VendorDocument(models.Model):
    """A vendor's document (trade licence, insurance ...) with an expiry date."""

    class DocType(models.TextChoices):
        TRADE_LICENCE = "trade_licence", "Trade licence"
        INSURANCE = "insurance", "Insurance"
        VAT_CERTIFICATE = "vat_certificate", "VAT certificate"
        CONTRACT = "contract", "Contract / rate agreement"
        OTHER = "other", "Other"

    SINGLE = ("trade_licence", "vat_certificate")

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField("Document", max_length=20, choices=DocType.choices)
    number = models.CharField("Number", max_length=60, blank=True)
    expiry_date = models.DateField("Expires on", null=True, blank=True)
    file = models.FileField(
        "File", upload_to=vendor_document_path, blank=True,
        validators=[FileExtensionValidator(FILE_TYPES), validate_document_size, validate_file_content],
        help_text="PDF, JPG, PNG or WebP.",
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["doc_type", "expiry_date"]
        verbose_name = "document"
        verbose_name_plural = "Documents"

    def __str__(self):
        return f"{self.get_doc_type_display()} {self.number}".strip()

    def clean(self):
        validate_document_rules(self)
        if self.doc_type in ("trade_licence", "insurance") and self.expiry_date is None:
            raise ValidationError({"expiry_date": "Enter the expiry date."})

    @property
    def expiry_status(self):
        """'none' (no expiry date), 'valid', 'soon' or 'expired'."""
        if self.expiry_date is None:
            return "none"
        today = timezone.localdate()
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + timedelta(days=sysconf.expiry_days()):
            return "soon"
        return "valid"


@receiver(post_delete, sender=VendorDocument)
def remove_vendor_document_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)


# ---------------------------------------------------------------------------------------
# Maintenance requests
# ---------------------------------------------------------------------------------------
def approval_limit(owner):
    """The amount above which a job needs approval: the owner's own limit, else the company-wide one (System settings)."""
    if owner is not None and owner.maintenance_approval_limit is not None:
        return owner.maintenance_approval_limit
    return Decimal(str(sysconf.get("maintenance_approval_limit") or 0))


def current_tenant(unit=None, prop=None, day=None):
    """Who rents the unit (or the whole property) on `day` (today when not given), or None."""
    from leases.models import Lease, running_q

    day = day or timezone.localdate()
    leases = Lease.objects.filter(running_q(day)).select_related("tenant")
    if unit is not None:
        lease = leases.filter(unit=unit).order_by("-start_date").first()
    elif prop is not None:
        lease = leases.filter(property=prop, unit__isnull=True).order_by("-start_date").first()
    else:
        return None
    return lease.tenant if lease else None


class MaintenanceRequest(models.Model):
    """One maintenance job, from the report of the problem to the finished work."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        AWAITING = "awaiting_approval", "Awaiting approval"
        APPROVED = "approved", "Approved"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class PaidBy(models.TextChoices):
        OWNER = "owner", "Owner"
        TENANT = "tenant", "Tenant"
        COMPANY = "company", "Company"

    # Assigned automatically (MNT-0001, MNT-0002 ...).
    code = models.CharField("Job no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="maintenance_requests")
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, null=True, blank=True, related_name="maintenance_requests",
        help_text="Leave empty for a problem in the building itself (lift, roof, common areas) or a standalone property.",
    )
    title = models.CharField("What is the problem?", max_length=150)
    description = models.TextField("Details", blank=True)
    category = models.CharField("Type of work", max_length=14, choices=Trade.choices, default=Trade.GENERAL)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    reported_on = models.DateField("Reported on", default=timezone.localdate)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.SET_NULL, null=True, blank=True, related_name="maintenance_requests",
        help_text="The tenant concerned. Filled in by itself from the running lease when it is left empty.",
    )

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, null=True, blank=True, related_name="requests")
    scheduled_on = models.DateField("Work planned for", null=True, blank=True)
    completed_on = models.DateField("Work finished on", null=True, blank=True)
    estimate = models.DecimalField(
        "Estimate (AED)", max_digits=10, decimal_places=2, null=True, blank=True, validators=MONEY,
        help_text="The vendor's quotation. Above the approval limit the job waits for approval.",
    )
    final_cost = models.DecimalField(
        "Final cost (AED)", max_digits=10, decimal_places=2, null=True, blank=True, validators=MONEY,
        help_text="What the vendor charged in the end (0 when the work was free).",
    )
    paid_by = models.CharField("Cost is paid by", max_length=8, choices=PaidBy.choices, default=PaidBy.OWNER)
    invoice = models.FileField(
        "Vendor invoice", upload_to=maintenance_file_path, blank=True,
        validators=[FileExtensionValidator(FILE_TYPES), validate_document_size, validate_file_content],
        help_text="A scan of the invoice (PDF, JPG, PNG or WebP).",
    )

    # Approval (set only by the Decision on the Approval tab)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="+",
    )
    approved_on = models.DateField(null=True, blank=True, editable=False)
    approved_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, editable=False)
    decision_note = models.CharField("Note", max_length=200, blank=True, editable=False)
    rejection_reason = models.CharField("Rejection reason", max_length=200, blank=True, editable=False)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reported_on", "-id"]
        verbose_name = "maintenance request"
        verbose_name_plural = "Maintenance requests"
        permissions = [
            ("export_maintenancerequest", "Can export maintenance requests to Excel / CSV"),
            ("approve_maintenancerequest", "Can approve or reject maintenance jobs"),
        ]
        indexes = [models.Index(fields=["status", "priority"], name="maint_status_priority_idx")]

    def __str__(self):
        return f"{self.code or 'New job'} · {self.title}"

    def save(self, *args, **kwargs):
        self.settle_status()
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(
                self, lambda: _next_code(MaintenanceRequest, "MNT"), lambda: super(MaintenanceRequest, self).save(*args, **kwargs)
            )

    # ---- what the job is about -----------------------------------------------------------
    @_property
    def where(self):
        """'RMT-101 · Reem Marina Tower', or the property's name."""
        if self.unit_id:
            return f"{self.unit} · {self.property.name}"
        return self.property.name if self.property_id else "-"

    @_property
    def owner(self):
        return self.property.owner if self.property_id else None

    # ---- money and approval --------------------------------------------------------------
    @_property
    def amount(self):
        """The cost the approval rule looks at: the final cost once known, else the estimate."""
        for value in (self.final_cost, self.estimate):
            if value is not None:
                return value
        return Decimal("0")

    @_property
    def limit(self):
        return approval_limit(self.owner)

    @_property
    def needs_approval(self):
        return self.amount > self.limit

    @_property
    def approval_current(self):
        """Has someone approved this job, for at least the amount it now costs?"""
        return self.approved_on is not None and (self.approved_amount is None or self.amount <= self.approved_amount)

    @_property
    def waiting_for_approval(self):
        return self.needs_approval and not self.approval_current

    def settle_status(self):
        """Keep the status honest about approval: New <-> Awaiting approval, and a stale approval is withdrawn."""
        if self.status == self.Status.APPROVED and not self.approval_current:
            self.approved_on = None
            self.approved_by = None
            self.approved_amount = None
            self.status = self.Status.NEW
        if self.status in (self.Status.NEW, self.Status.AWAITING):
            self.status = self.Status.AWAITING if self.waiting_for_approval else self.Status.NEW

    def approve(self, user, note=""):
        self.approved_by = user
        self.approved_on = timezone.localdate()
        self.approved_amount = self.amount
        self.decision_note = (note or "")[:200]
        self.rejection_reason = ""
        if self.status in (self.Status.NEW, self.Status.AWAITING, self.Status.REJECTED):
            self.status = self.Status.APPROVED

    def reject(self, user, reason):
        self.approved_by = user
        self.approved_on = None
        self.approved_amount = None
        self.rejection_reason = (reason or "")[:200]
        self.status = self.Status.REJECTED

    # ---- rules ---------------------------------------------------------------------------
    def clean(self):
        errors = {}
        today = timezone.localdate()
        S = self.Status

        # A decision made on the Approval tab (set by the form: ("approve" | "reject", user, note)).
        decision = getattr(self, "_decision", None)
        if decision:
            kind, user, note = decision
            if kind == "approve":
                if not self.needs_approval:
                    errors["status"] = "This job is within the approval limit, so there is nothing to approve."
                elif self.status in (S.COMPLETED, S.CANCELLED):
                    errors["status"] = "A completed or cancelled job cannot be approved."
                else:
                    self.approve(user, note)
            elif kind == "reject":
                if self.status in (S.IN_PROGRESS, S.COMPLETED, S.CANCELLED):
                    errors["status"] = "Only a job that has not started can be rejected. Cancel it instead."
                elif not (note or "").strip():
                    errors["status"] = "Write the reason for rejecting this job."
                else:
                    self.reject(user, note.strip())
            self._decision = None
        if self.status != S.REJECTED:
            self.rejection_reason = ""

        # where
        if self.unit_id and not self.property_id:
            self.property = self.unit.property
        if self.property_id and self.unit_id and self.unit.property_id != self.property_id:
            errors["unit"] = f"Unit {self.unit} is not in {self.property.name}."
        if self.property_id and self.unit_id and not self.property.has_units:
            errors["unit"] = "This property has no units. Leave the unit empty."

        # dates
        if self.reported_on and self.reported_on > today:
            errors["reported_on"] = "The report date cannot be in the future."
        if self.reported_on and self.scheduled_on and self.scheduled_on < self.reported_on:
            errors["scheduled_on"] = "The work cannot be planned before the problem was reported."
        if self.completed_on and self.reported_on and self.completed_on < self.reported_on:
            errors["completed_on"] = "The work cannot be finished before the problem was reported."
        if self.completed_on and self.completed_on > today:
            errors["completed_on"] = "The finishing date cannot be in the future."

        # the tenant concerned, and who pays
        if self.property_id and not self.tenant_id:
            found = current_tenant(self.unit if self.unit_id else None, self.property if not self.unit_id else None)
            if found is not None:
                self.tenant = found
        if self.property_id and self.owner is not None and self.owner.is_company and self.paid_by == self.PaidBy.OWNER:
            self.paid_by = self.PaidBy.COMPANY  # the company owns it: there is no other owner to charge
        if self.paid_by == self.PaidBy.TENANT and not self.tenant_id:
            errors["tenant"] = "Choose the tenant who pays (nobody is renting this place right now)."

        # vendor
        if self.vendor_id and not self.vendor.is_active and (self._state.adding or _vendor_changed(self)):
            errors["vendor"] = "This vendor is inactive. Choose another vendor."
        if self.final_cost is not None and not self.vendor_id and self.status not in (S.CANCELLED, S.REJECTED):
            errors["vendor"] = "Choose the vendor who did the work."

        # status
        self.settle_status()
        if self.status == S.APPROVED and not self.approved_on:
            errors["status"] = "A job is approved with the Decision on the Approval tab."
        if self.status == S.REJECTED and not self.rejection_reason:
            errors["status"] = "A job is rejected with the Decision on the Approval tab (with the reason)."
        if self.status in (S.IN_PROGRESS, S.COMPLETED):
            if not self.vendor_id:
                errors["vendor"] = "Choose the vendor before the work starts."
            if self.waiting_for_approval:
                errors["status"] = (
                    f"This job is above the approval limit (AED {self.limit:,.0f}). It needs approval before the work starts: "
                    "use the Decision on the Approval tab."
                )
        if self.status == S.COMPLETED:
            if self.final_cost is None:
                errors["final_cost"] = "Enter the final cost (0 when the work was free)."
            if not self.completed_on:
                self.completed_on = today
        elif self.status != S.COMPLETED and self.completed_on and "completed_on" not in errors:
            errors["completed_on"] = "Only a completed job has a finishing date. Set the status to Completed, or clear the date."
        if errors:
            raise ValidationError(errors)


def _vendor_changed(request_obj):
    if not request_obj.pk:
        return True
    return not MaintenanceRequest.objects.filter(pk=request_obj.pk, vendor_id=request_obj.vendor_id).exists()


@receiver(post_delete, sender=MaintenanceRequest)
def remove_invoice_file(sender, instance, **kwargs):
    if instance.invoice:
        instance.invoice.delete(save=False)
