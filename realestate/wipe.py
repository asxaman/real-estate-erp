"""Erase all business data (for a fresh start). Only a superuser may do this.

Everything the system holds is removed: owners, properties, tenants, leases,
payments, expenses, statements, the whole accounting ledger and its audit log,
the admin history and the uploaded documents. What stays: the superuser who
runs it, the user roles (groups) and the system's own settings record.
Every other user account is removed.
"""

import shutil
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction

PHRASE = "ERASE ALL DATA"
KEEP = {"realestate.systemstate"}


def _models():
    found = []
    for label in ("realestate", "accounting"):
        for model in apps.get_app_config(label).get_models(include_auto_created=True):
            if model._meta.proxy or model._meta.label_lower in KEEP:
                continue
            found.append(model)
    found.append(LogEntry)
    return found


def summary():
    """(label, count) for the main kinds of records, for the confirmation page."""
    from accounting.models import JournalEntry
    from realestate.models import Cheque, Expense, Lease, Owner, Property, Tenant, Unit

    pairs = [
        ("Owners", Owner), ("Properties", Property), ("Units", Unit), ("Tenants", Tenant),
        ("Leases", Lease), ("Payments & cheques", Cheque), ("Expenses", Expense),
        ("Journal entries", JournalEntry),
    ]
    return [(label, model.objects.count()) for label, model in pairs]


def _clear_media():
    root = Path(settings.MEDIA_ROOT).resolve()
    # Safety: only ever empty a folder that is really called "media".
    if root.name != "media" or not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
        removed += 1
    return removed


def wipe_everything(keep_user):
    """Delete all data except `keep_user` (who must be an active superuser)."""
    if not (keep_user.is_active and keep_user.is_superuser):
        raise PermissionDenied("Only a superuser can erase the data.")
    from accounting.triggers import install_triggers

    User = get_user_model()
    models = _models()
    with transaction.atomic():
        with connection.cursor() as cursor:
            # The ledger is locked by database triggers. Lift them for this one
            # deliberate action; they are put back straight afterwards.
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'ledger_%'")
            for (name,) in cursor.fetchall():
                cursor.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        for model in models:
            model._base_manager.all()._raw_delete(using="default")
        User.objects.exclude(pk=keep_user.pk).delete()
        with connection.cursor() as cursor:
            tables = [m._meta.db_table for m in models]
            marks = ", ".join(["%s"] * len(tables))
            try:
                cursor.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({marks})", tables)
            except Exception:  # no counters to reset
                pass
        install_triggers()
    return _clear_media()
