"""Sample data, eraser and role permissions for the leases module."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.utils import timezone

from core import sample_lists as lists
from core.data_registry import register_eraser, register_seeder, sample_count
from properties.models import Property, Unit
from tenants.models import Tenant

from .models import Lease, LeaseNote, LeasePayment

MODELS = ("lease", "leasepayment", "leasedocument", "leasenote", "leasededuction")
ACTIONS = ("add", "change", "delete", "view")

# role -> permission codenames
ROLE_PERMISSIONS = {
    "Property Manager": [f"{a}_{m}" for m in MODELS for a in ACTIONS] + ["export_lease", "export_leasepayment"],
    "Accountant": [f"view_{m}" for m in MODELS] + ["change_leasepayment", "add_leasenote", "export_lease", "export_leasepayment"],  # the Accountant records payments
    "Leasing Agent": [f"{a}_{m}" for m in ("lease", "leasedocument", "leasenote", "leasededuction") for a in ("add", "change", "view")]
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
        payment.cheque_date = payment.due_date
        if payment.status == LeasePayment.Status.PAID:
            payment.deposited_on = payment.due_date
        payment.save()
    if keep_last_overdue:
        lease.late_fee_per_day = Decimal("50")
        lease.save(update_fields=["late_fee_per_day"])
        LeaseNote.objects.create(lease=lease, author="Sample data", text="Tenant promised to hand over the last cheque next week.")
    if lease.status == Lease.Status.TERMINATED:  # nothing more is expected after an early termination
        lease.payments.filter(status=LeasePayment.Status.PENDING, due_date__gt=lease.terminated_on).delete()


BULK_PREFIX = "BULK-"  # the Ejari number of every large-test-data lease starts like this (it is also the "already done" marker)


def _plan(k, today):
    """(kind, start, end, terminated_on) for lease number k: a realistic mix of running, ending soon, finished, terminated and future leases."""
    slot = k % 100
    day = timedelta
    if slot < 72:  # running, started 30 to 350 days ago
        start = today - day(days=30 + (k * 37) % 320)
        return "running", start, start + day(days=364), None
    if slot < 80:  # ends within the next 5 to 45 days
        start = today - day(days=364 - (5 + k % 40))
        return "ending", start, start + day(days=364), None
    if slot < 88:  # finished a while ago
        start = today - day(days=400 + k % 200)
        return "expired", start, start + day(days=364), None
    if slot < 92:  # ended early
        start = today - day(days=200 + k % 100)
        return "terminated", start, start + day(days=364), today - day(days=10 + k % 60)
    if slot < 96:  # starts soon
        start = today + day(days=5 + k % 50)
        return "future", start, start + day(days=364), None
    start = today - day(days=60 + (k * 11) % 250)
    return "running", start, start + day(days=364), None


def seed_bulk_leases() -> str:
    """About 2,000 leases on the large-test-data units and properties, with their payment schedules. Safe to run twice."""
    from tenants.models import Tenant as TenantModel

    wanted = sample_count(2000)
    if not wanted or Lease.objects.filter(ejari_number__startswith=BULK_PREFIX).exists():
        return "skipped (large test data is switched off or already there)"
    import re

    today = timezone.localdate()
    S = Property.Status
    targets = list(
        Unit.objects.filter(property__code__regex=r"^B[0-9]+$", status=S.VACANT, leases__isnull=True)
        .select_related("property").order_by("property__code", "floor_sort", "unit_number")
    )
    targets += list(Property.objects.filter(code__regex=r"^S[0-9]+$", status=S.VACANT, leases__isnull=True).order_by("code"))
    targets = targets[:wanted]
    tenants = list(TenantModel.objects.filter(email__regex=r"^tenant[0-9]+@example\.com$", is_blacklisted=False, is_active=True).order_by("email"))
    if not targets or not tenants:
        return "skipped (no large-test-data tenants or units yet)"

    top = 0
    for code in Lease.objects.exclude(code__isnull=True).values_list("code", flat=True):
        match = re.fullmatch(r"LSE-(\d+)", code)
        if match:
            top = max(top, int(match.group(1)))

    leases, plans = [], []
    for k, target in enumerate(targets):
        is_unit = isinstance(target, Unit)
        prop = target.property if is_unit else target
        kind, start, end, ended = _plan(k, today)
        base_rent = (target.asking_rent if is_unit else target.annual_rent) or Decimal("60000")
        rent = Decimal(max(1000, int(base_rent * (95 + k % 11) / 100) // 100 * 100))
        commercial = (target.unit_type in (Unit.UnitType.OFFICE, Unit.UnitType.SHOP)) if is_unit else prop.property_type in (
            Property.PropertyType.SHOP, Property.PropertyType.WAREHOUSE, Property.PropertyType.YARD)
        tenant = tenants[(k - 1 if k % 25 == 24 else k) % len(tenants)]  # every 25th tenant rents a second place
        top += 1
        extra = {}
        if kind == "terminated":
            extra = {"status": Lease.Status.TERMINATED, "terminated_on": ended, "termination_reason": "Tenant moved out early"}
        lease = Lease(
            code=f"LSE-{top:04d}", tenant=tenant, unit=target if is_unit else None, property=prop,
            start_date=start, end_date=end, annual_rent=rent, installments=(4, 4, 4, 2, 1, 12, 6, 4, 2, 4)[k % 10],
            vat_applies=commercial, security_deposit=(rent * 5 / 100).quantize(Decimal("1")), commission=(rent * 5 / 100).quantize(Decimal("1")) if k % 2 == 0 else 0,
            admin_fee=Decimal("500") if k % 3 == 0 else 0, late_fee_per_day=Decimal("25") if k % 3 == 1 else 0,
            ejari_number=f"{BULK_PREFIX}{k + 1:05d}", ejari_expiry=end, **extra,
        )
        leases.append(lease)
        plans.append((kind, target))
    Lease.objects.bulk_create(leases, batch_size=1000)

    # ---- payments ----------------------------------------------------------------
    P = LeasePayment
    payments, notes = [], []
    for k, (lease, (kind, target)) in enumerate(zip(leases, plans)):
        rows = lease.schedule_rows()
        past = [row for row in rows if row[1] < today]
        stuck = None  # one overdue (or bounced) payment on some running leases
        if kind in ("running", "ending") and past and k % 9 == 0:
            stuck = past[-1][0]
        cheque_lease = k % 6 in (0, 1, 2, 3)
        method = P.Method.CHEQUE if cheque_lease else (P.Method.TRANSFER if k % 6 == 4 else P.Method.CASH)
        bank = lists.BANKS[k % len(lists.BANKS)]
        for number, due, amount in rows:
            if lease.status == Lease.Status.TERMINATED and due > lease.terminated_on:
                continue  # unpaid payments after an early termination are cancelled
            paid = due < today and number != stuck
            status = P.Status.PAID if paid else (P.Status.BOUNCED if number == stuck and k % 31 == 0 else P.Status.PENDING)
            payment = P(lease=lease, part=number, due_date=due, amount=amount, method=method, status=status)
            if method == P.Method.CHEQUE:
                payment.cheque_number = f"{100000 + k * 12 + number}"
                payment.bank_name = bank
                payment.cheque_date = due
                if paid:
                    payment.deposited_on = due
            if paid:
                payment.paid_on = min(today, due + timedelta(days=k % 3))
            payments.append(payment)
        if stuck:
            notes.append(LeaseNote(lease=lease, author="Sample data", text="Tenant promised to settle the late payment this week."))
    LeasePayment.objects.bulk_create(payments, batch_size=1000)
    LeaseNote.objects.bulk_create(notes, batch_size=1000)

    # ---- occupancy: running leases make their unit / property Occupied, future ones Reserved ---
    units, props = [], []
    for kind, target in plans:
        if kind in ("running", "ending"):
            target.status = S.OCCUPIED
        elif kind == "future":
            target.status = S.RESERVED
        else:
            continue
        (units if isinstance(target, Unit) else props).append(target)
    Unit.objects.bulk_update(units, ["status"], batch_size=1000)
    Property.objects.bulk_update(props, ["status"], batch_size=1000)
    return f"{len(leases)} leases with {len(payments)} payments added"


def erase_leases() -> str:
    count = Lease.objects.count()
    Lease.objects.all().delete()
    return f"removed {count} leases"


# Seed after tenants (order 20); erase before them (reverse order), so units and tenants are free to delete.
register_seeder("Leases & rent payments", seed_leases, order=30)
register_seeder("Large test data: leases and payments", seed_bulk_leases, order=31)
register_eraser("Leases & rent payments", erase_leases, order=30)
