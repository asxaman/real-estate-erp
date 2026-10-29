import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from core.models import PHOTO_VALIDATORS
from properties.models import validate_document_size
from properties.models_constants import EXPIRY_WARNING_DAYS, PHONE_ERROR, PHONE_REGEX


def tenant_photo_path(instance, filename):
    return f"tenant_photos/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def tenant_document_path(instance, filename):
    return f"tenant_documents/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def next_tenant_code():
    numbers = []
    for code in Tenant.objects.exclude(code__isnull=True).values_list("code", flat=True):
        match = re.fullmatch(r"TEN-(\d+)", code)
        if match:
            numbers.append(int(match.group(1)))
    return f"TEN-{max(numbers, default=0) + 1:04d}"


class Tenant(models.Model):
    """A person or company that rents from us (leases come in the next module step)."""

    class TenantType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"

    name = models.CharField("Tenant name", max_length=200)
    tenant_type = models.CharField(
        "Tenant type", max_length=12, choices=TenantType.choices, default=TenantType.INDIVIDUAL
    )
    # Assigned automatically (TEN-0001, TEN-0002 ...).
    code = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    photo = models.ImageField(
        "Photo / logo",
        upload_to=tenant_photo_path,
        blank=True,
        validators=PHOTO_VALIDATORS,
        help_text="Optional. A photo for an individual or the logo of a company "
        "(JPG, PNG or WebP, up to 2 MB).",
    )
    nationality = models.CharField(
        max_length=60, help_text="For a company: the country where it is registered."
    )
    email = models.EmailField()
    phone = models.CharField(
        "Contact number",
        max_length=30,
        validators=[RegexValidator(PHONE_REGEX, PHONE_ERROR)],
        help_text="Example: +971 50 123 4567.",
    )
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join((self.name or "").split())
        if not self.code:
            self.code = next_tenant_code()
        old_photo = ""
        if self.pk:
            old_photo = Tenant.objects.filter(pk=self.pk).values_list("photo", flat=True).first() or ""
        super().save(*args, **kwargs)
        if old_photo and old_photo != (self.photo.name or ""):
            self.photo.storage.delete(old_photo)  # the picture was replaced or removed

    def clean(self):
        self.name = " ".join((self.name or "").split())
        self.email = (self.email or "").strip().lower()

    def document_counts(self):
        """(expired, expiring soon) among this tenant's documents."""
        today = timezone.localdate()
        soon_limit = today + timedelta(days=EXPIRY_WARNING_DAYS)
        expired = self.documents.filter(expiry_date__lt=today).count()
        soon = self.documents.filter(expiry_date__gte=today, expiry_date__lte=soon_limit).count()
        return expired, soon


@receiver(post_delete, sender=Tenant)
def remove_tenant_photo(sender, instance, **kwargs):
    if instance.photo:
        instance.photo.delete(save=False)


class TenantContact(models.Model):
    """A person to talk to about a tenant (the tenant, a PRO, an accountant ...)."""

    class Role(models.TextChoices):
        TENANT = "tenant", "Tenant"
        SIGNATORY = "signatory", "Authorised signatory"
        PRO = "pro", "PRO / representative"
        ACCOUNTANT = "accountant", "Accountant"
        EMERGENCY = "emergency", "Emergency contact"
        OTHER = "other", "Other"

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        ARABIC = "ar", "Arabic"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.TENANT)
    is_primary = models.BooleanField("Primary", default=False)
    mobile = models.CharField(
        max_length=30, blank=True, validators=[RegexValidator(PHONE_REGEX, PHONE_ERROR)]
    )
    whatsapp = models.CharField(
        "WhatsApp", max_length=30, blank=True, validators=[RegexValidator(PHONE_REGEX, PHONE_ERROR)]
    )
    email = models.EmailField(blank=True)
    language = models.CharField(
        "Language", max_length=2, choices=Language.choices, default=Language.ENGLISH
    )

    class Meta:
        ordering = ["-is_primary", "name"]
        verbose_name = "contact"
        verbose_name_plural = "Contacts"

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"

    def clean(self):
        self.name = " ".join((self.name or "").split())
        self.email = (self.email or "").strip().lower()


class TenantDocument(models.Model):
    """A tenant's document (Emirates ID, passport, visa, trade licence ...) with an expiry date."""

    class DocType(models.TextChoices):
        EMIRATES_ID = "emirates_id", "Emirates ID"
        PASSPORT = "passport", "Passport"
        VISA = "visa", "Residence visa"
        TRADE_LICENCE = "trade_licence", "Trade licence"
        VAT_CERTIFICATE = "vat_certificate", "VAT certificate"
        OTHER = "other", "Other"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField("Document", max_length=20, choices=DocType.choices)
    number = models.CharField("Number", max_length=60, blank=True)
    expiry_date = models.DateField("Expires on", null=True, blank=True)
    file = models.FileField(
        "File",
        upload_to=tenant_document_path,
        blank=True,
        validators=[
            FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "webp"]),
            validate_document_size,
        ],
        help_text="PDF, JPG, PNG or WebP, up to 10 MB.",
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
        """'none' (no expiry date), 'valid', 'soon' or 'expired'."""
        if self.expiry_date is None:
            return "none"
        today = timezone.localdate()
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + timedelta(days=EXPIRY_WARNING_DAYS):
            return "soon"
        return "valid"


@receiver(post_delete, sender=TenantDocument)
def remove_tenant_document_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
