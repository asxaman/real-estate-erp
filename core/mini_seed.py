"""A tiny sample set: 2 owners, 2 properties, 2 units, 2 tenants. No leases and no payments."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .audit import suppressed


def seed_small() -> list[str]:
    from properties.models import Owner, Property, Unit, UnitType
    from tenants.models import Tenant, TenantContact, TenantDocument

    today = timezone.localdate()
    T, E, F = Property.PropertyType, Property.Emirate, Property.FeeType
    D, TT = TenantDocument.DocType, Tenant.TenantType
    added = {"owners": 0, "properties": 0, "units": 0, "tenants": 0}

    with suppressed(), transaction.atomic():
        owners = []
        for name, email, phone, kind in [
            ("Small Set Owner One", "owner.one@example.com", "+971 50 100 0001", Owner.OwnerType.INDIVIDUAL),
            ("Small Set Owner Two LLC", "owner.two@example.com", "+971 4 100 0002", Owner.OwnerType.COMPANY),
        ]:
            owner = Owner.objects.filter(name__iexact=name).first()
            if owner is None:
                owner = Owner.objects.create(name=name, email=email, phone=phone, owner_type=kind)
                added["owners"] += 1
            owners.append(owner)

        fee = {"management_fee_type": F.PERCENT, "management_fee_percent": Decimal("5.00")}
        building = Property.objects.filter(code="SMALL1").first()
        if building is None:
            building = Property.objects.create(
                code="SMALL1", name="Small Set Tower", owner=owners[0], property_type=T.RESIDENTIAL_BUILDING,
                emirate=E.DUBAI, address="Business Bay, Dubai", year_built=2020, total_floors=5, status="", **fee,
            )
            added["properties"] += 1
        villa = Property.objects.filter(code="SMALL2").first()
        if villa is None:
            villa = Property.objects.create(
                code="SMALL2", name="Small Set Villa", owner=owners[1], property_type=T.VILLA,
                emirate=E.SHARJAH, address="Al Zahia, Sharjah", year_built=2021, total_floors=2,
                annual_rent=Decimal("180000"), status=Property.Status.VACANT, **fee,
            )
            added["properties"] += 1

        for number, floor, unit_type, rent in [("101", "1", UnitType.ONE_BR, "62000"), ("102", "1", UnitType.TWO_BR, "95000")]:
            _, new = Unit.objects.get_or_create(
                property=building, unit_number=number,
                defaults={"floor": floor, "unit_type": unit_type, "bathrooms": 2, "area_sqft": Decimal("800"),
                          "status": Unit.Status.VACANT, "asking_rent": Decimal(rent)},
            )
            added["units"] += int(new)

        for name, kind, country, email, phone, doc in [
            ("Small Set Tenant One", TT.INDIVIDUAL, "Egypt", "tenant.one@example.com", "+971 50 200 0001", (D.EMIRATES_ID, "784-1990-0000101-1", 500)),
            ("Small Set Tenant Two LLC", TT.COMPANY, "United Arab Emirates", "tenant.two@example.com", "+971 4 200 0002", (D.TRADE_LICENCE, "TL-200002", 400)),
        ]:
            if Tenant.objects.filter(name__iexact=name).exists() or Tenant.objects.filter(email__iexact=email).exists():
                continue
            tenant = Tenant.objects.create(name=name, tenant_type=kind, nationality=country, email=email, phone=phone)
            TenantDocument.objects.create(tenant=tenant, doc_type=doc[0], number=doc[1], expiry_date=today + timedelta(days=doc[2]))
            TenantContact.objects.create(tenant=tenant, name=name, role=TenantContact.Role.TENANT, is_primary=True, mobile=phone, email=email)
            added["tenants"] += 1

    return [
        f"Small set: {added['owners']} owners, {added['properties']} properties, {added['units']} units, "
        f"{added['tenants']} tenants added (no leases, no payments). Ones already there were not repeated."
    ]
