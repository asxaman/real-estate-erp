"""Sample data, eraser and role permissions for the tenants module."""
from datetime import timedelta

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from core.data_registry import register_eraser, register_seeder

from .models import Tenant, TenantContact, TenantDocument


def seed_tenants() -> str:
    today = timezone.localdate()
    D = TenantDocument.DocType
    T = Tenant.TenantType
    samples = [
        ("Ahmed Hassan", T.INDIVIDUAL, "Egypt", "ahmed.hassan@example.com", "+971 50 000 1001",
         [(D.EMIRATES_ID, "784-1988-0000011-1", 500), (D.VISA, "201/2024/123456", 15)]),
        ("Sara Thomas", T.INDIVIDUAL, "United Kingdom", "sara.thomas@example.com", "+971 55 000 1002",
         [(D.EMIRATES_ID, "784-1991-0000022-2", 900), (D.PASSPORT, "GB1234567", 700)]),
        ("Blue Wave Trading LLC", T.COMPANY, "United Arab Emirates", "accounts@bluewave.example.com", "+971 4 000 1003",
         [(D.TRADE_LICENCE, "TL-778899", -12), (D.VAT_CERTIFICATE, "VAT-100200300", 300)]),
    ]
    created = 0
    for name, ttype, nationality, email, phone, docs in samples:
        tenant, new = Tenant.objects.get_or_create(
            name=name,
            defaults={"tenant_type": ttype, "nationality": nationality, "email": email, "phone": phone},
        )
        if new:
            created += 1
            for doc_type, number, days in docs:
                TenantDocument.objects.create(
                    tenant=tenant, doc_type=doc_type, number=number, expiry_date=today + timedelta(days=days)
                )
            TenantContact.objects.create(
                tenant=tenant, name=name, role=TenantContact.Role.TENANT, is_primary=True,
                mobile=phone, email=email,
            )
    _grant_role_permissions()
    return f"{Tenant.objects.count()} tenants ({created} new)"


def _grant_role_permissions():
    def perms(*codenames):
        return Permission.objects.filter(content_type__app_label="tenants", codename__in=codenames)

    models_ = ("tenant", "tenantcontact", "tenantdocument")
    everything = [f"{a}_{m}" for m in models_ for a in ("add", "change", "delete", "view")]
    view_only = [f"view_{m}" for m in models_]
    editing = [f"{a}_{m}" for m in models_ for a in ("add", "change", "view")]
    role_perms = {
        "Property Manager": everything + ["export_tenant"],
        "Accountant": view_only + ["export_tenant"],
        "Leasing Agent": editing,
    }
    for role, codenames in role_perms.items():
        group, _ = Group.objects.get_or_create(name=role)
        group.permissions.add(*perms(*codenames))


def erase_tenants() -> str:
    count = Tenant.objects.count()
    Tenant.objects.all().delete()
    return f"removed {count} tenants"


# Seed after properties (order 10); erase before it (reverse order).
register_seeder("Tenants", seed_tenants, order=20)
register_eraser("Tenants", erase_tenants, order=20)
