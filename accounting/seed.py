"""Sample opening balance, the eraser, and the permissions of the roles."""
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from core.data_registry import register_eraser, register_seeder

from . import posting
from .models import JournalEntry, JournalLine


def _all(*models):
    return [f"{a}_{m}" for m in models for a in ("add", "change", "view")]


ROLE_PERMISSIONS = {
    "Accountant": _all("account", "journalentry") + ["export_account", "export_journalentry"],
    "Property Manager": [],
    "Leasing Agent": [],
    "HR Manager": [],
}


def grant_role_permissions():
    for role, codenames in ROLE_PERMISSIONS.items():
        group = Group.objects.filter(name=role).first()
        if group is None or not codenames:
            continue
        group.permissions.add(*Permission.objects.filter(content_type__app_label="accounting", codename__in=codenames))


def seed_accounting():
    posting.ensure_chart()
    made = 0
    if not JournalEntry.objects.filter(source_type="", description="Opening balances").exists():
        day = timezone.localdate().replace(month=1, day=1)
        entry = JournalEntry.objects.create(date=day, description="Opening balances")
        amount = Decimal("250000.00")
        JournalLine.objects.create(entry=entry, account=posting.account("1010"), description="Bank balance", debit=amount)
        JournalLine.objects.create(entry=entry, account=posting.account("3200"), description="Opening balance equity", credit=amount)
        made = 1
    counts = posting.sync_all()
    return f"chart of accounts ready, {made} opening entry, {sum(counts.values())} automatic entries posted"


def erase_accounting():
    count = JournalEntry.objects.count()
    JournalEntry.objects.all().delete()
    return f"{count} journal entries removed (the chart of accounts is kept)"


register_seeder("Accounting", seed_accounting, order=90)
register_eraser("Accounting", erase_accounting, order=90)
