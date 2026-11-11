"""Sample owner statements, the eraser, and the permissions of the roles."""
from datetime import timedelta

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from agreements.models import OwnerAgreement
from core.data_registry import register_eraser, register_seeder

from .models import OwnerStatement, default_period

ROLE_PERMISSIONS = {
    "Accountant": ["add_ownerstatement", "change_ownerstatement", "view_ownerstatement", "export_ownerstatement", "approve_ownerstatement"],
    "Property Manager": ["add_ownerstatement", "change_ownerstatement", "view_ownerstatement", "export_ownerstatement"],
    "Leasing Agent": [],
}


def grant_role_permissions():
    """Give the roles that already exist their statement permissions (never creates a role)."""
    for role, codenames in ROLE_PERMISSIONS.items():
        group = Group.objects.filter(name=role).first()
        if group is None or not codenames:
            continue
        group.permissions.add(*Permission.objects.filter(content_type__app_label="statements", codename__in=codenames))


def seed_statements() -> str:
    """A statement for each owner with an agreement: from the start of six months ago to the end of last month (the first one is approved)."""
    grant_role_permissions()
    today = timezone.localdate()
    _, end = default_period(today)
    start = (end.replace(day=1) - timedelta(days=150)).replace(day=1)
    made = 0
    owner_ids = OwnerAgreement.objects.exclude(status="draft").values_list("owner_id", flat=True).distinct().order_by("owner_id")
    for index, owner_id in enumerate(owner_ids):
        if OwnerStatement.objects.filter(owner_id=owner_id).exists():
            continue
        statement = OwnerStatement(owner_id=owner_id, period_start=start, period_end=end, notes="Sample data")
        statement.full_clean()
        statement.save()
        if index == 0 and statement.lines.exists():
            statement.status, statement.approved_on = "approved", today
            statement.save()
        made += 1
    return f"{OwnerStatement.objects.count()} owner statements ({made} new)"


def erase_statements() -> str:
    count = OwnerStatement.objects.count()
    OwnerStatement.objects.all().delete()
    return f"removed {count} owner statements"


# Seed after bills and expenses (order 60); erase before them (reverse order).
register_seeder("Owner statements", seed_statements, order=70)
register_eraser("Owner statements", erase_statements, order=70)
