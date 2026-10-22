from django.conf import settings
from django.db import models


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
