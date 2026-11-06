"""Keeps a unit's (or standalone property's) Occupied / Vacant status in step with its leases."""
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_save
from django.utils import timezone

from properties.models import Property, Unit

from .models import Lease


def _running(lease_qs, today):
    """A lease is running from its start date to its end date (or its early-termination date)."""
    active = Q(status=Lease.Status.ACTIVE, end_date__gte=today)
    ending_early = Q(status=Lease.Status.TERMINATED, terminated_on__gte=today)
    return lease_qs.filter(Q(start_date__lte=today) & (active | ending_early)).exists()


def refresh_target(target, today=None, had_leases=False):
    """
    A unit or standalone property with a running lease becomes Occupied; one that was Occupied
    and has no running lease any more becomes Vacant. Reserved / Under maintenance are only
    changed to Occupied by a running lease, and never changed back by this code.
    Returns True when the status changed.
    """
    today = today or timezone.localdate()
    if isinstance(target, Unit):
        leases = Lease.objects.filter(unit=target)
    else:
        leases = Lease.objects.filter(property=target, unit__isnull=True)
    running = _running(leases, today)
    wanted = None
    if running and target.status in (Property.Status.VACANT, Property.Status.RESERVED):
        wanted = Property.Status.OCCUPIED
    elif not running and target.status == Property.Status.OCCUPIED and (had_leases or leases.exists()):
        wanted = Property.Status.VACANT
    if wanted is None:
        return False
    target.status = wanted
    target.save(update_fields=["status"])
    return True


def refresh_for_lease(lease, deleted=False):
    target = lease.unit if lease.unit_id else lease.property
    if target is not None:
        # read the latest status from the database first
        target.refresh_from_db(fields=["status"])
        refresh_target(target, had_leases=deleted)


def refresh_all(today=None):
    """Every unit / property that has leases. Run when someone logs in (see apps.py) and by the refresh command."""
    today = today or timezone.localdate()
    changed = 0
    for unit in Unit.objects.filter(leases__isnull=False).distinct():
        changed += refresh_target(unit, today)
    for prop in Property.objects.filter(leases__isnull=False, leases__unit__isnull=True).distinct():
        changed += refresh_target(prop, today)
    return changed


def _remember_old_place(sender, instance, raw=False, **kwargs):
    """Before a lease is saved, note where it was (the lease may be moved to another unit or property)."""
    instance._old_place = None
    if raw or not instance.pk:
        return
    old = Lease.objects.filter(pk=instance.pk).values_list("unit_id", "property_id").first()
    if old:
        instance._old_place = old


def _after_save(sender, instance, raw=False, **kwargs):
    if raw:
        return
    refresh_for_lease(instance)
    # The lease moved away from a unit or property: that place may be empty now.
    old = getattr(instance, "_old_place", None)
    if old and old != (instance.unit_id, instance.property_id):
        old_unit, old_property = old
        place = Unit.objects.filter(pk=old_unit).first() if old_unit else (
            Property.objects.filter(pk=old_property).first() if old_property else None
        )
        if place is not None:
            refresh_target(place, had_leases=True)
    instance._old_place = None


def _after_delete(sender, instance, **kwargs):
    try:
        refresh_for_lease(instance, deleted=True)
    except Exception:
        pass  # the unit or property is being deleted together with its lease


def connect():
    pre_save.connect(_remember_old_place, sender=Lease, dispatch_uid="lease-occupancy-before-save")
    post_save.connect(_after_save, sender=Lease, dispatch_uid="lease-occupancy-save")
    post_delete.connect(_after_delete, sender=Lease, dispatch_uid="lease-occupancy-delete")
