"""Sample data, eraser and role permissions for the tenants module."""
from datetime import timedelta

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from core import sample_lists as lists
from core.data_registry import register_eraser, register_seeder, sample_count

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
        # Names and emails are unique whatever their capitals: skip a sample that is already there.
        tenant = Tenant.objects.filter(name__iexact=name).first() or Tenant.objects.filter(email__iexact=email).first()
        new = tenant is None
        if new:
            tenant = Tenant.objects.create(
                name=name, tenant_type=ttype, nationality=nationality, email=email, phone=phone
            )
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


BULK_MARKER_EMAIL = "tenant0001@example.com"
EXPIRY_OFFSETS = [-20, 10, 45, 300, 500, 700, 90, 900, 150, 365]  # days from today: expired, soon, later ...


def seed_bulk_tenants() -> str:
    """About 2,000 tenants (85% people, 15% companies), each with a contact and documents. Safe to run twice."""
    count = sample_count(2000)
    if not count or Tenant.objects.filter(email=BULK_MARKER_EMAIL).exists():
        return "skipped (large test data is switched off or already there)"
    import re

    today = timezone.localdate()
    D = TenantDocument.DocType
    T = Tenant.TenantType
    top = 0
    for code in Tenant.objects.exclude(code__isnull=True).values_list("code", flat=True):
        match = re.fullmatch(r"TEN-(\d+)", code)
        if match:
            top = max(top, int(match.group(1)))
    taken = {name.lower() for name in Tenant.objects.values_list("name", flat=True)}
    firsts, lasts = len(lists.FIRST_NAMES), len(lists.LAST_NAMES)
    tenants = []
    for i in range(count):
        if i % 7 == 6:  # about one in seven is a company
            kind = T.COMPANY
            base = f"{lists.COMPANY_STEMS[i % len(lists.COMPANY_STEMS)]} {lists.COMPANY_KINDS[(i // len(lists.COMPANY_STEMS)) % len(lists.COMPANY_KINDS)]} LLC"
        else:
            kind = T.INDIVIDUAL
            pair = (i * 37) % (firsts * lasts)  # 37 shares no factor with the list sizes: every pair is different
            base = f"{lists.FIRST_NAMES[pair % firsts]} {lists.LAST_NAMES[pair // firsts]}"
        name, extra = base, 1
        while name.lower() in taken:
            extra += 1
            name = f"{base} {extra}"
        taken.add(name.lower())
        top += 1
        phone = f"+971 {('50', '55', '52', '54', '56')[i % 5]} {100 + (i * 7) % 900} {1000 + (i * 13) % 9000}"
        tenants.append(Tenant(
            name=name, tenant_type=kind, code=f"TEN-{top:04d}", email=f"tenant{i + 1:04d}@example.com", phone=phone,
            nationality="United Arab Emirates" if kind == T.COMPANY else lists.NATIONALITIES[(i * 3) % len(lists.NATIONALITIES)],
        ))
    Tenant.objects.bulk_create(tenants, batch_size=1000)
    TenantContact.objects.bulk_create([
        TenantContact(tenant=t, name=t.name, role=TenantContact.Role.TENANT, is_primary=True, mobile=t.phone, email=t.email)
        for t in tenants
    ], batch_size=1000)
    documents = []
    for i, t in enumerate(tenants):
        expiry = lambda shift=0: today + timedelta(days=EXPIRY_OFFSETS[(i + shift) % len(EXPIRY_OFFSETS)])  # noqa: E731
        if t.tenant_type == T.COMPANY:
            documents.append(TenantDocument(tenant=t, doc_type=D.TRADE_LICENCE, number=f"TL-{500000 + i}", expiry_date=expiry()))
            documents.append(TenantDocument(tenant=t, doc_type=D.VAT_CERTIFICATE, number=f"VAT-{100200300 + i}", expiry_date=expiry(3)))
        else:
            documents.append(TenantDocument(tenant=t, doc_type=D.EMIRATES_ID, number=f"784-1985-{2000000 + i:07d}-1", expiry_date=expiry()))
            documents.append(TenantDocument(tenant=t, doc_type=D.PASSPORT, number=f"P{7000000 + i}", expiry_date=expiry(5)))
            if i % 3 == 0:
                documents.append(TenantDocument(tenant=t, doc_type=D.VISA, number=f"201/2025/{300000 + i}", expiry_date=expiry(2)))
    TenantDocument.objects.bulk_create(documents, batch_size=1000)
    return f"{len(tenants)} tenants with contacts and {len(documents)} documents added"


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
register_seeder("Large test data: tenants", seed_bulk_tenants, order=21)
register_eraser("Tenants", erase_tenants, order=20)
