import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

MAX_PHOTO_BYTES = 2 * 1024 * 1024  # 2 MB


def validate_photo_size(file):
    if file.size > MAX_PHOTO_BYTES:
        raise ValidationError("The picture is too large. The maximum size is 2 MB.")


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
        help_text="Optional. JPG, PNG or WebP, up to 2 MB.",
    )

    def __str__(self):
        return f"Profile of {self.user}"


@receiver(post_delete, sender=UserProfile)
def remove_photo_file(sender, instance, **kwargs):
    """When a profile (or its user) is deleted, delete the picture file too."""
    if instance.photo:
        instance.photo.delete(save=False)
