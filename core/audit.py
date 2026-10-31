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

AUDITED_MODELS = (
    "properties.Owner",
    "properties.Property",
    "properties.Unit",
    "properties.OwnerContact",
    "properties.OwnerDocument",
    "properties.OwnerBankAccount",
    "properties.PropertyDocument",
    "properties.UnitDocument",
    "properties.UnitPhoto",
    "tenants.Tenant",
    "tenants.TenantContact",
    "tenants.TenantDocument",
    "core.SystemSettings",
    "auth.User",
)
OWNER_CHILD_LABELS = {
    "properties.OwnerContact",
    "properties.OwnerDocument",
    "properties.OwnerBankAccount",
}
TENANT_CHILD_LABELS = {"tenants.TenantContact", "tenants.TenantDocument"}
# Fields whose full value must never appear in the log (only the last 4 characters).
MASKED_FIELDS = {
    "iban", "emirates_id_number", "passport_number", "trn", "trade_licence_number", "number",
}
# Fields whose value is not shown at all (the log still says that it changed).
HIDDEN_FIELDS = {"date_of_birth"}
# Document records are named by their type only; the number stays out of the record's name.
DOCUMENT_LABELS = {
    "properties.OwnerDocument",
    "properties.PropertyDocument",
    "properties.UnitDocument",
    "tenants.TenantDocument",
}

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


def _mask_one(value):
    text = str(value)
    return text if text.startswith("****") else "****" + text[-4:]


def _mask(changes):
    masked = {}
    for field, pair in changes.items():
        if field in MASKED_FIELDS:
            pair = [_mask_one(v) if v else v for v in pair]
        elif field in HIDDEN_FIELDS:
            pair = ["(hidden)" if v else v for v in pair]
        masked[field] = pair
    return masked


def related_owner_id(instance):
    """Which owner does this record belong to? (None when it is not owner-related.)"""
    label = instance._meta.label
    try:
        if label == "properties.Owner":
            return instance.pk
        if label == "properties.Property":
            return instance.owner_id
        if label == "properties.Unit":
            return instance.property.owner_id
        if label in OWNER_CHILD_LABELS:
            return instance.owner_id
        if label in ("properties.PropertyDocument",):
            return instance.property.owner_id
        if label in ("properties.UnitDocument", "properties.UnitPhoto"):
            return instance.unit.property.owner_id
    except Exception:
        return None
    return None


def related_property_id(instance):
    """Which property does this record belong to? (None when it is not property-related.)"""
    label = instance._meta.label
    try:
        if label == "properties.Property":
            return instance.pk
        if label in ("properties.Unit", "properties.PropertyDocument"):
            return instance.property_id
        if label in ("properties.UnitDocument", "properties.UnitPhoto"):
            return instance.unit.property_id
    except Exception:
        return None
    return None


def related_unit_id(instance):
    """Which unit does this record belong to? (None when it is not unit-related.)"""
    label = instance._meta.label
    if label == "properties.Unit":
        return instance.pk
    if label in ("properties.UnitDocument", "properties.UnitPhoto"):
        return instance.unit_id
    return None


def related_tenant_id(instance):
    """Which tenant does this record belong to? (None when it is not tenant-related.)"""
    label = instance._meta.label
    if label == "tenants.Tenant":
        return instance.pk
    if label in TENANT_CHILD_LABELS:
        return instance.tenant_id
    return None


def _record_name(instance):
    if instance._meta.label in DOCUMENT_LABELS:
        return str(instance.get_doc_type_display())  # not the document number
    return str(instance)


def _write(action, instance, changes):
    from .models import AuditEntry

    user = current_user()
    changes = _mask(changes)
    AuditEntry.objects.create(
        related_owner_id=related_owner_id(instance),
        related_tenant_id=related_tenant_id(instance),
        related_property_id=related_property_id(instance),
        related_unit_id=related_unit_id(instance),
        user_id=user.pk if user is not None else None,
        username=user.get_username() if user is not None else "(system)",
        action=action,
        model_label=instance._meta.verbose_name.capitalize(),
        object_id=str(instance.pk),
        object_repr=_record_name(instance)[:300],
        changes=changes,
    )


# ---- signal handlers -------------------------------------------------------------------
def _tracked_fields(model):
    return [f for f in model._meta.concrete_fields if f.name not in IGNORED_FIELDS]


def remember_old(sender, instance, raw=False, **kwargs):
    """Before an update: read the saved row once (plain values, no related records)."""
    if raw or _suppressed.get():
        return
    instance._audit_old_row = None
    if instance.pk is not None and not instance._state.adding:
        fields = _tracked_fields(sender)
        instance._audit_old_row = (
            sender._default_manager.filter(pk=instance.pk).values(*[f.attname for f in fields]).first()
        )


def _related_text(field, instance, value):
    """What a related record looks like in the log (its name), looked up only when it changed."""
    if value is None:
        return None
    if instance is not None:
        try:
            return str(getattr(instance, field.name))
        except Exception:
            return str(value)
    try:
        other = field.related_model._default_manager.filter(pk=value).first()
    except Exception:
        other = None
    return str(other) if other is not None else str(value)


def log_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or _suppressed.get():
        return
    if created:
        new = snapshot(instance)
        _write("created", instance, {k: [None, v] for k, v in new.items() if v not in (None, "")})
        return
    old_row = getattr(instance, "_audit_old_row", None)
    if old_row is None:
        return
    changes = {}
    for field in _tracked_fields(sender):
        before, after = old_row.get(field.attname), field.value_from_object(instance)
        if field.is_relation:
            if before != after:
                changes[field.name] = [_related_text(field, None, before), _related_text(field, instance, after)]
        else:
            before, after = _plain(before), _plain(after)
            if before != after:
                changes[field.name] = [before, after]
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


def scrub_old_entries(sender, apps=None, using="default", **kwargs):
    """After `migrate`: hide personal numbers that older log entries still contain."""
    if apps is None:
        return
    import re

    try:
        Entry = apps.get_model("core", "AuditEntry")
        keys = sorted(MASKED_FIELDS | HIDDEN_FIELDS)
        entries = Entry.objects.using(using)
        for entry in entries.filter(changes__has_any_keys=keys).iterator():
            masked = _mask(entry.changes)
            if masked != entry.changes:
                entries.filter(pk=entry.pk).update(changes=masked)
        for entry in entries.filter(model_label="Document").iterator():
            name = re.sub(r"\s+\S*\d\S*$", "", entry.object_repr or "")
            if name != entry.object_repr:
                entries.filter(pk=entry.pk).update(object_repr=name)
    except Exception:  # best effort: never stop `migrate`
        return
