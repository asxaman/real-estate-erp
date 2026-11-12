"""Sample opening balance, the eraser, and the permissions of the roles."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from core.data_registry import register_eraser, register_seeder

from . import posting
from .models import Account, FundTransfer, JournalEntry, Reconciliation


def _all(*models):
    return [f"{a}_{m}" for m in models for a in ("add", "change", "view")]


ROLE_PERMISSIONS = {
    "Accountant": _all("account", "journalentry", "fundtransfer") + ["view_reconciliation", "add_reconciliation", "delete_reconciliation", "export_account", "export_journalentry",
                   "export_fundtransfer"],
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
    if not JournalEntry.objects.filter(source_type=posting.OPENING).exists():
        day = timezone.localdate().replace(month=1, day=1)
        main = posting.account("1010")
        subs = {}
        for code, name, amount in (("1011", "Emirates NBD - Current", "150000.00"), ("1012", "ADCB - Savings", "100000.00")):
            acct, _ = Account.objects.get_or_create(code=code, defaults={"name": name, "type": "asset", "parent": main})
            subs[acct] = (Decimal(amount), Decimal("0.00"))
        posting.save_opening_balances(day, subs)
        made = 1
    if not FundTransfer.objects.exists():
        FundTransfer.objects.create(date=timezone.localdate() - timedelta(days=2), from_account=posting.account("1010"), to_account=posting.account("1000"),
                                    amount=Decimal("5000.00"), reference="Cash withdrawal", notes="Sample: cash for the office")
    counts = posting.sync_all()
    return f"chart of accounts ready, {made} opening entry, a sample transfer, {sum(counts.values())} automatic entries posted"


def erase_accounting():
    Reconciliation.objects.all().delete()
    FundTransfer.objects.all().delete()
    count = JournalEntry.objects.count()
    JournalEntry.objects.all().delete()
    return f"{count} journal entries removed (the chart of accounts is kept)"


register_seeder("Accounting", seed_accounting, order=90)
register_eraser("Accounting", erase_accounting, order=90)
