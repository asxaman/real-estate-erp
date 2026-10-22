"""Erase-all-data logic and a summary of what is currently stored."""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import ProtectedError, RestrictedError

# Apps whose data is NEVER wiped by "Erase all data" (users are handled separately).
SYSTEM_APPS = {"auth", "contenttypes", "sessions", "admin", "messages", "staticfiles", "unfold"}


def business_models():
    return [
        m
        for m in apps.get_models()
        if m._meta.app_label not in SYSTEM_APPS and not m._meta.auto_created
    ]


def data_summary():
    """[(label, row count), ...] for every business table, biggest first."""
    rows = [(m._meta.verbose_name_plural.title(), m._base_manager.count()) for m in business_models()]
    return sorted(rows, key=lambda r: (-r[1], r[0]))


def erase_all_data(*, include_users=False, keep_user=None):
    """
    Delete every row of every business model. User accounts and roles are kept
    unless include_users=True (the `keep_user` account is always kept).
    Returns {label: rows_deleted}. All-or-nothing.
    """
    models = list(business_models())
    deleted = {}

    with transaction.atomic():
        pending = models
        # Related tables may block each other (PROTECT); retry until everything is gone.
        for _ in range(len(models) + 3):
            if not pending:
                break
            blocked = []
            for model in pending:
                try:
                    _total, per_model = model._base_manager.all().delete()
                except (ProtectedError, RestrictedError):
                    blocked.append(model)
                    continue
                for label, count in per_model.items():
                    deleted[label] = deleted.get(label, 0) + count
            pending = blocked
        if pending:
            raise RuntimeError(
                "Could not erase: " + ", ".join(m._meta.label for m in pending)
            )

        tables = [m._meta.db_table for m in models]

        if include_users:
            User = get_user_model()
            users = User.objects.all()
            if keep_user is not None:
                users = users.exclude(pk=keep_user.pk)
            count = users.count()
            users.delete()
            deleted["auth.User"] = count
            tables.append(User._meta.db_table)

        if connection.vendor == "sqlite" and tables:
            # restart ID numbering from 1
            with connection.cursor() as cursor:
                cursor.executemany(
                    "DELETE FROM sqlite_sequence WHERE name = %s", [[t] for t in tables]
                )

    return deleted
