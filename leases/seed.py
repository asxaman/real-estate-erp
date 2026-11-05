"""Sample data, eraser and role permissions for the leases module."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.data_registry import register_eraser, register_seeder
from properties.models import Property, Unit
from tenants.models import Tenant

from .models import Lease, LeasePayment

MODELS = ("lease", "leasepayment", "leasedocument")
ACTIONS = ("add", "change", "delete", "view")

# role -> permission codenames
ROLE_PERMISSIONS = {
    "Property Manager": [f"{a}_{m}" for m in MODELS for a in ACTIONS],
    "Accountant": [f"view_{m}" for m in MODELS] + ["change_leasepayment"],  # the Accountant records payments
    "Leasing Agent": [f"{a}_{m}" for m in ("lease", "leasedocument") for a in ("add", "change", "view")]
    + ["view_leasepayment", "change_leasepayment"],
}


def grant_role_permissions():
    """Give the roles that already exist their lease permissions (never creates a role)."""
    for role, codenames in ROLE_PERMISSIONS.items():
        group = Group.objects.filter(name=role).first()
        if group is None:
            continue
        group.permissions.add(*Permission.objects.filter(content_type__app_label="leases", codename__in=codenames))


def seed_leases() -> str:
    today = timezone.localdate()
    tenants = {t.name: t for t in Tenant.objects.all()}

    def find_unit(code, number):
        return Unit.objects.filter(property__code=code, unit_number=number).select_related("property").first()

    def find_property(code):
        return Property.objects.filter(code=code).first()

    # (tenant, unit, standalone property, start offset, length in days, rent, payments, vat, extras, overdue demo)
    plans = [
        ("Ahmed Hassan", find_unit("RMT", "102"), None, -300, 364, 62000, 4, False, {}, True),
        ("Sara Thomas", None, find_property("ZV01"), -345, 364, 185000, 4, False, {}, False),
        ("Blue Wave Trading LLC", find_unit("SBH", "G01"), None, -200, 364, 120000, 2, True, {"security_deposit": 10000}, False),
        ("Ahmed Hassan", find_unit("RMT", "101"), None, -500, 364, 42000, 1, False, {}, False),  # an old, finished lease
        ("Blue Wave Trading LLC", find_unit("SBH", "G02"), None, -250, 364, 118000, 4, True,
         {"status": Lease.Status.TERMINATED, "terminated_on": today - timedelta(days=40),
          "termination_reason": "Tenant moved to a larger office"}, False),
    ]
    created, skipped = 0, 0
    for name, unit, prop, offset, length, rent, count, vat, extra, overdue_demo in plans:
        tenant = tenants.get(name)
        if tenant is None or (unit is None and prop is None) or (unit is not None and Lease.objects.filter(unit=unit).exists()) \
                or (prop is not None and Lease.objects.filter(property=prop).exists()):
            skipped += 1
            continue
        start = today + timedelta(days=offset)
        lease = Lease(
            tenant=tenant, unit=unit, property=prop or unit.property, start_date=start, end_date=start + timedelta(days=length),
            annual_rent=Decimal(rent), installments=count, vat_applies=vat,
            ejari_number=f"10-2026-{1000 + created}", ejari_expiry=start + timedelta(days=length), **extra,
        )
        try:
            lease.clean()
        except ValidationError:
            skipped += 1
            continue
        lease.save()  # creates the payment schedule
        _settle(lease, today, keep_last_overdue=overdue_demo)
        created += 1
    grant_role_permissions()
    return f"{Lease.objects.count()} leases ({created} new" + (f", {skipped} skipped" if skipped else "") + ")"


def _settle(lease, today, keep_last_overdue):
    """Mark the payments that fell due in the past as received (one stays unpaid to show an overdue payment)."""
    past = list(lease.payments.filter(due_date__lt=today).order_by("due_date"))
    for index, payment in enumerate(past):
        if keep_last_overdue and index == len(past) - 1:
            continue
        payment.status = LeasePayment.Status.PAID
        payment.paid_on = payment.due_date
        payment.save()
    for index, payment in enumerate(lease.payments.order_by("due_date")):
        payment.cheque_number = f"{lease.pk}00{index + 1}"
        payment.bank_name = "Emirates NBD"
        payment.save()
    if lease.status == Lease.Status.TERMINATED:  # nothing more is expected after an early termination
        lease.payments.filter(status=LeasePayment.Status.PENDING, due_date__gt=lease.terminated_on).delete()


def erase_leases() -> str:
    count = Lease.objects.count()
    Lease.objects.all().delete()
    return f"removed {count} leases"


# Seed after tenants (order 20); erase before them (reverse order), so units and tenants are free to delete.
register_seeder("Leases & rent payments", seed_leases, order=30)
register_eraser("Leases & rent payments", erase_leases, order=30)
