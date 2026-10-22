from django.contrib.auth import get_user_model

from . import roles
from .models import CompanySettings
from .seeding import register

DEMO_PASSWORD = "Demo@12345"

DEMO_STAFF = [
    ("demo_manager", "Amna", "Al Mansoori", roles.PROPERTY_MANAGER),
    ("demo_leasing", "Omar", "Khalid", roles.LEASING_AGENT),
    ("demo_accountant", "Priya", "Nair", roles.ACCOUNTANT),
    ("demo_maintenance", "Rashid", "Al Falasi", roles.MAINTENANCE_SUPERVISOR),
    ("demo_support", "Sara", "Hassan", roles.CUSTOMER_SERVICE),
]


@register("Company profile and demo staff", order=10)
def seed_core():
    from django.contrib.auth.models import Group

    User = get_user_model()

    CompanySettings.objects.get_or_create(
        pk=1,
        defaults=dict(
            company_name="Gulf Horizon Property Management",
            legal_name="Gulf Horizon Property Management L.L.C.",
            trade_license_number="DEMO-123456",
            tax_registration_number="100000000000003",
            address="Business Bay, Dubai",
            po_box="12345",
            phone="+971 4 000 0000",
            email="info@example.com",
        ),
    )

    created = 0
    for username, first, last, role in DEMO_STAFF:
        user, was_created = User.objects.get_or_create(
            username=username,
            defaults=dict(
                first_name=first,
                last_name=last,
                email=f"{username}@example.com",
                is_staff=True,
            ),
        )
        if was_created:
            user.set_password(DEMO_PASSWORD)
            user.save()
            created += 1
        user.groups.add(Group.objects.get(name=role))

    return f"company profile ready, {created} new demo staff users (password: {DEMO_PASSWORD})"
