"""
Audit trail: records who created, changed or deleted owners, properties, units and users.

How it works
------------
* `AuditMiddleware` remembers the logged-in user while a web request is handled.
* Django signals (connected in core/apps.py) fire whenever one of the AUDITED_MODELS is
  saved or deleted. They compare the record with what is in the database and write an
  AuditEntry containing the old and new value of every field that changed.
* Sample data and "Erase all data" are not logged record by record (that would flood the
  log); the Data tools page writes a single summary entry instead.
* Limit: bulk operations that skip Django's save (queryset.update) are not seen.
"""
import contextvars
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

AUDITED_MODELS = ("properties.Owner", "properties.Property", "properties.Unit", "auth.User")

# Fields that are technical noise or secret; they never appear in the log.
IGNORED_FIELDS = {"password", "last_login", "created_at", "date_joined", "floor_sort"}

_request = contextvars.ContextVar("audit_request", default=None)
_actor = contextvars.ContextVar("audit_actor", default=None)
_suppressed = contextvars.ContextVar("audit_suppressed", default=False)


# ---- who is acting ---------------------------------------------------------------
class AuditMiddleware:
    """Remember the current request so signal handlers can find out who is logged in."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _request.set(request)
        try:
            return self.get_response(request)
        finally:
            _request.reset(token)


def current_user():
    actor = _actor.get()
    if actor is not None:
        return actor
    request = _request.get()
    user = getattr(request, "user", None) if request is not None else None
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


@contextmanager
def acting_as(user):
    """Treat `user` as the person making changes (used in tests and scripts)."""
    token = _actor.set(user)
    try:
        yield
    finally:
        _actor.reset(token)


@contextmanager
def suppressed():
    """Do not log anything inside this block (sample data and erase use it)."""
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)


# ---- snapshots ---------------------------------------------------------------------
def _plain(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")  # 95000.00 and 95000 are the same amount
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in IGNORED_FIELDS:
            continue
        if field.is_relation:
            if getattr(instance, field.attname) is None:
                data[field.name] = None
            else:
                try:
                    data[field.name] = str(getattr(instance, field.name))
                except Exception:  # related record already gone
                    data[field.name] = str(getattr(instance, field.attname))
        else:
            data[field.name] = _plain(field.value_from_object(instance))
    return data


def _write(action, instance, changes):
    from .models import AuditEntry

    user = current_user()
    AuditEntry.objects.create(
        user_id=user.pk if user is not None else None,
        username=user.get_username() if user is not None else "(system)",
        action=action,
        model_label=instance._meta.verbose_name.capitalize(),
        object_id=str(instance.pk),
        object_repr=str(instance)[:300],
        changes=changes,
    )


# ---- signal handlers -------------------------------------------------------------------
def remember_old(sender, instance, raw=False, **kwargs):
    if raw or _suppressed.get():
        return
    instance._audit_old = None
    if instance.pk is not None and not instance._state.adding:
        old = sender._default_manager.filter(pk=instance.pk).first()
        if old is not None:
            instance._audit_old = snapshot(old)


def log_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or _suppressed.get():
        return
    new = snapshot(instance)
    if created:
        _write("created", instance, {k: [None, v] for k, v in new.items() if v not in (None, "")})
        return
    old = getattr(instance, "_audit_old", None)
    if old is None:
        return
    changes = {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}
    if changes:
        _write("changed", instance, changes)


def log_delete(sender, instance, **kwargs):
    if _suppressed.get():
        return
    _write("deleted", instance, {k: [v, None] for k, v in snapshot(instance).items() if v not in (None, "")})


def log_tool(user, title, lines):
    """One summary entry for the Data tools page (seed / erase)."""
    from .models import AuditEntry

    AuditEntry.objects.create(
        user_id=user.pk if user is not None else None,
        username=user.get_username() if user is not None else "(system)",
        action="tool",
        model_label="Data tool",
        object_repr=title,
        changes={"result": [None, " | ".join(lines)[:1000]]},
    )
