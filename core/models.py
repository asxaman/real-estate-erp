import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

def validate_photo_size(file):
    from . import sysconf

    limit = sysconf.get("max_photo_mb")  # set on the System settings page
    if file.size > limit * 1024 * 1024:
        raise ValidationError(f"The picture is too large. The maximum size is {limit} MB.")


PHOTO_VALIDATORS = [FileExtensionValidator(["jpg", "jpeg", "png", "webp"]), validate_photo_size]


def profile_photo_path(instance, filename):
    """Store pictures under a random name so file names never clash or leak personal data."""
    return f"profile_photos/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def _show(value):
    if value in (None, ""):
        return "-"
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return str(value)


class AuditEntry(models.Model):
    """
    One line of the audit trail: who created, changed or deleted which record, and when.
    Entries are written automatically (see core/audit.py) and cannot be edited or deleted
    in the admin.
    """

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        CHANGED = "changed", "Changed"
        DELETED = "deleted", "Deleted"
        TOOL = "tool", "Data tool"

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Kept as text too, so the entry still says who did it after an account is deleted.
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=10, choices=Action.choices)
    model_label = models.CharField("Record type", max_length=100)
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField("Record", max_length=300, blank=True)
    # The owner this entry belongs to (for the Activity tab on the owner page). No database
    # constraint on purpose: entries must survive, and never block, the deletion of an owner.
    related_owner = models.ForeignKey(
        "properties.Owner",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="audit_entries",
    )
    # The tenant this entry belongs to (Activity tab on the tenant page); no database constraint.
    related_tenant = models.ForeignKey(
        "tenants.Tenant",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="audit_entries",
    )
    # The property / unit this entry belongs to (Activity tabs on their pages); no database constraint.
    related_property = models.ForeignKey(
        "properties.Property",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="audit_entries",
    )
    related_unit = models.ForeignKey(
        "properties.Unit",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="audit_entries",
    )
    # {"field name": [old value, new value], ...}
    changes = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp", "-id"]
        verbose_name = "audit entry"
        verbose_name_plural = "audit log"

    def __str__(self):
        return f"{self.get_action_display()} {self.model_label} {self.object_repr}"

    def change_lines(self):
        """[(field label, old, new), ...] ready to display."""
        lines = []
        for field, pair in (self.changes or {}).items():
            old, new = pair
            lines.append((field.replace("_", " ").capitalize(), _show(old), _show(new)))
        return lines

    def summary(self):
        """One short line for the list page."""
        if self.action == self.Action.CREATED:
            return "New record"
        if self.action == self.Action.DELETED:
            return "Record removed"
        lines = self.change_lines()
        if self.action == self.Action.TOOL:
            return lines[0][2] if lines else ""
        return "; ".join(f"{label}: {old} → {new}" for label, old, new in lines)


class UserProfile(models.Model):
    """Optional extras for a login account (for now: the profile picture)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    photo = models.ImageField(
        "Profile picture",
        upload_to=profile_photo_path,
        blank=True,
        validators=PHOTO_VALIDATORS,
        help_text="Optional. JPG, PNG or WebP.",
    )

    def __str__(self):
        return f"Profile of {self.user}"


@receiver(post_delete, sender=UserProfile)
def remove_photo_file(sender, instance, **kwargs):
    """When a profile (or its user) is deleted, delete the picture file too."""
    if instance.photo:
        instance.photo.delete(save=False)


class SystemSettings(models.Model):
    """The system-wide settings (only one row exists). Edited on the System settings page."""

    # Company profile
    company_name = models.CharField(
        "Company name", max_length=120, default="Real Estate ERP",
        help_text="Shown in the sidebar and the browser tab.",
    )
    company_trn = models.CharField(
        "Tax registration number (TRN)", max_length=30, blank=True,
        help_text="Used later on invoices, receipts and agreements.",
    )
    company_phone = models.CharField("Phone", max_length=30, blank=True)
    company_email = models.EmailField("Email", blank=True)
    company_address = models.TextField("Address", blank=True)

    # Date, time and time zone
    date_format = models.CharField(
        "Date format", max_length=12, default="iso",
        help_text="How dates are shown and typed everywhere in the system.",
    )
    time_format = models.CharField(
        "Time format", max_length=4, default="24h", help_text="24-hour or 12-hour clock."
    )
    timezone = models.CharField(
        "Time zone", max_length=40, default="Asia/Dubai",
        help_text="Times are shown in this time zone.",
    )

    # Documents and files
    expiry_warning_days = models.PositiveSmallIntegerField(
        "Warn before a document expires (days)", default=30,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Documents expiring within this many days show a warning.",
    )
    max_photo_mb = models.PositiveSmallIntegerField(
        "Largest photo / logo (MB)", default=2,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    max_document_mb = models.PositiveSmallIntegerField(
        "Largest document file (MB)", default=10,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
    )
    require_complete_documents = models.BooleanField(
        "Tenant documents must be complete before a lease", default=False,
        help_text="When ticked, a lease cannot be made for a tenant whose Emirates ID and passport "
        "(or trade licence, for a company) are not on file. Takes effect when Leases are added.",
    )

    # Notifications
    toast_seconds = models.PositiveSmallIntegerField(
        "Pop-up notification time (seconds)", default=5,
        validators=[MinValueValidator(2), MaxValueValidator(30)],
        help_text="How long success messages stay on screen. Warnings and errors stay twice as long.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "system settings"
        verbose_name_plural = "system settings"

    def __str__(self):
        return "System settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # there is only ever one row
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # the settings row cannot be deleted

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
