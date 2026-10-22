"""Sample data for Module 0 (foundation): staff roles and demo staff users."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from .data_registry import register_seeder

DEMO_PASSWORD = "Demo@12345"

ROLES = [
    "Property Manager",
    "Accountant",
    "Leasing Agent",
    "Maintenance Supervisor",
]

# (username, first name, last name, role)
DEMO_USERS = [
    ("demo.manager", "Layla", "Hassan", "Property Manager"),
    ("demo.accountant", "Omar", "Khalid", "Accountant"),
    ("demo.leasing", "Mariam", "Saeed", "Leasing Agent"),
    ("demo.maintenance", "Yusuf", "Rahman", "Maintenance Supervisor"),
]


def seed_roles_and_users() -> str:
    User = get_user_model()

    new_groups = 0
    groups = {}
    for name in ROLES:
        group, created = Group.objects.get_or_create(name=name)
        groups[name] = group
        new_groups += int(created)

    new_users = 0
    for username, first, last, role in DEMO_USERS:
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": first,
                "last_name": last,
                "email": f"{username}@example.com",
                "is_staff": True,
            },
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
            new_users += 1
        user.groups.add(groups[role])

    return (
        f"{new_groups} new role(s), {new_users} new demo user(s) "
        f"(demo password: {DEMO_PASSWORD})"
    )


register_seeder("Roles & demo users", seed_roles_and_users, order=0)
