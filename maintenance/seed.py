"""Sample vendors and maintenance jobs, the eraser, and the permissions of the roles."""
from datetime import timedelta

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from core.data_registry import register_eraser, register_seeder
from properties.models import Property, Unit

from .models import MaintenanceRequest, Vendor, VendorDocument, current_tenant

MODELS = ("vendor", "vendordocument", "maintenancerequest")
ACTIONS = ("add", "change", "delete", "view")

# role -> permission codenames
ROLE_PERMISSIONS = {
    "Property Manager": [f"{a}_{m}" for m in MODELS for a in ACTIONS]
    + ["export_vendor", "export_maintenancerequest", "approve_maintenancerequest"],
    "Accountant": [f"view_{m}" for m in MODELS] + ["export_vendor", "export_maintenancerequest"],
    "Leasing Agent": ["view_vendor", "view_vendordocument", "add_maintenancerequest", "change_maintenancerequest", "view_maintenancerequest"],
}


def grant_role_permissions():
    """Give the roles that already exist their maintenance permissions (never creates a role)."""
    for role, codenames in ROLE_PERMISSIONS.items():
        group = Group.objects.filter(name=role).first()
        if group is None:
            continue
        group.permissions.add(*Permission.objects.filter(content_type__app_label="maintenance", codename__in=codenames))


SAMPLE_VENDORS = [
    ("Al Noor Plumbing LLC", "plumbing", "Rashid Ali", "+971 50 111 2201", "info@alnoor-plumbing.example", "painting"),
    ("Bright Spark Electrical", "electrical", "Imran Khan", "+971 50 111 2202", "jobs@brightspark.example", ""),
    ("CoolBreeze AC Services", "ac", "Joseph Mathew", "+971 50 111 2203", "service@coolbreeze.example", "general"),
    ("Gulf Colours Painting", "painting", "Nasser Saleh", "+971 50 111 2204", "hello@gulfcolours.example", "cleaning"),
    ("FixIt All Maintenance", "general", "Ravi Kumar", "+971 50 111 2205", "office@fixitall.example", "carpentry"),
]


def seed_maintenance() -> str:
    grant_role_permissions()
    today = timezone.localdate()
    made_vendors = 0
    for index, (name, trade, contact, phone, email, other) in enumerate(SAMPLE_VENDORS):
        vendor, created = Vendor.objects.get_or_create(
            name=name, defaults=dict(trade=trade, contact_person=contact, phone=phone, email=email, other_trades=other),
        )
        if created:
            made_vendors += 1
            # One licence that is fine, one about to expire and one already expired, so the warnings can be seen.
            expiry = today + timedelta(days=(300, 20, -15, 200, 120)[index])
            VendorDocument.objects.create(vendor=vendor, doc_type="trade_licence", number=f"TL-{410000 + index}", expiry_date=expiry)
    vendors = {v.name: v for v in Vendor.objects.all()}
    made_jobs = 0
    if not MaintenanceRequest.objects.exists():
        def unit(code, number):
            return Unit.objects.filter(property__code=code, unit_number=number).select_related("property").first()

        villa = Property.objects.filter(code="ZV01").first()
        plans = [
            (unit("RMT", "102"), None, "Water leaking under the kitchen sink", "plumbing", "high", "Al Noor Plumbing LLC", 350, 350, "tenant", "completed", -25),
            (unit("RMT", "101"), None, "AC not cooling", "ac", "urgent", "CoolBreeze AC Services", 1200, None, "owner", "in_progress", -4),
            (unit("SBH", "G01"), None, "Repaint the office after the tenant moved", "painting", "normal", "Gulf Colours Painting", 6500, None, "owner", "new", -2),
            (None, villa, "Replace the garden lights", "electrical", "low", "Bright Spark Electrical", 900, None, "owner", "new", -1),
        ]
        for where_unit, where_property, title, category, priority, vendor_name, estimate, final, paid_by, status, offset in plans:
            target = where_unit.property if where_unit is not None else where_property
            if target is None:
                continue
            if paid_by == "tenant" and current_tenant(where_unit, None if where_unit is not None else target) is None:
                paid_by = "owner"  # nobody is renting it (yet), so the tenant cannot be asked to pay
            job = MaintenanceRequest(
                property=target, unit=where_unit, title=title, category=category, priority=priority,
                vendor=vendors.get(vendor_name), estimate=estimate, final_cost=final, paid_by=paid_by,
                reported_on=today + timedelta(days=offset),
            )
            job.full_clean()
            job.save()
            if status in ("in_progress", "completed"):
                # Above the approval limit (company default AED 2,000) a job needs approval; these are small.
                job.status = status
                if status == "completed":
                    job.completed_on = today + timedelta(days=offset + 3)
                job.full_clean()
                job.save()
            made_jobs += 1
    return f"{Vendor.objects.count()} vendors ({made_vendors} new), {MaintenanceRequest.objects.count()} maintenance jobs ({made_jobs} new)"


def erase_maintenance() -> str:
    jobs = MaintenanceRequest.objects.count()
    vendors = Vendor.objects.count()
    MaintenanceRequest.objects.all().delete()
    Vendor.objects.all().delete()
    return f"removed {jobs} maintenance jobs and {vendors} vendors"


# Seed after properties, tenants and leases (so the tenant on a job is found); erase before them (reverse order).
register_seeder("Vendors & maintenance", seed_maintenance, order=40)
register_eraser("Vendors & maintenance", erase_maintenance, order=40)
