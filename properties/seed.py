"""Sample data, eraser and role permissions for Module 1 (properties)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from core import sample_lists as lists
from core.data_registry import register_eraser, register_seeder, sample_count

from .models import Owner, OwnerBankAccount, OwnerContact, OwnerDocument, Property, Unit
from .models_constants import floor_sort_key

U = Unit.UnitType

# unit type -> (bathrooms, area sq ft, base yearly rent AED)
UNIT_SPECS = {
    U.STUDIO: (1, 450, 42000),
    U.ONE_BR: (2, 750, 62000),
    U.TWO_BR: (3, 1200, 95000),
    U.THREE_BR: (4, 1700, 140000),
    U.OFFICE: (1, 900, 80000),
    U.SHOP: (1, 650, 120000),
}


def _status(counter: int) -> str:
    """Deterministic mix of statuses so sample data looks realistic."""
    if counter % 23 == 22:
        return Unit.Status.MAINTENANCE
    slot = counter % 10
    if slot <= 6:
        return Unit.Status.OCCUPIED
    if slot <= 8:
        return Unit.Status.VACANT
    return Unit.Status.RESERVED


def _make_unit(prop, number, floor, unit_type, counter, premium=0):
    baths, area, rent = UNIT_SPECS[unit_type]
    _, created = Unit.objects.get_or_create(
        property=prop,
        unit_number=number,
        defaults={
            "floor": "G" if floor == 0 else str(floor),  # floor 0 is the ground floor
            "unit_type": unit_type,
            "bathrooms": baths,
            "area_sqft": Decimal(area),
            "furnished": counter % 6 == 0,
            "status": _status(counter),
            "asking_rent": Decimal(rent + premium * floor),
        },
    )
    return int(created)


def seed_properties() -> str:
    company = Owner.company()  # the built-in "Company Owned" owner
    owners = {}
    for name, email, phone, owner_type in [
        ("Khalifa Al Mansoori", "khalifa@example.com", "+971 50 000 0001", Owner.OwnerType.INDIVIDUAL),
        ("Gulf Horizon Investments LLC", "info@gulfhorizon.example.com", "+971 2 000 0002", Owner.OwnerType.COMPANY),
        ("Noura Al Suwaidi", "noura@example.com", "+971 55 000 0003", Owner.OwnerType.INDIVIDUAL),
    ]:
        # Names are unique whatever their capitals, so look for an existing one the same way.
        owners[name] = Owner.objects.filter(name__iexact=name).first() or Owner.objects.create(
            name=name, email=email, phone=phone, owner_type=owner_type
        )
    _seed_owner_details(owners)

    def prop(code, **fields):
        obj, _ = Property.objects.get_or_create(code=code, defaults=fields)
        return obj

    T = Property.PropertyType
    E = Property.Emirate
    F = Property.FeeType
    S = Property.Status

    def percent_fee(value):
        return {"management_fee_type": F.PERCENT, "management_fee_percent": Decimal(value)}

    # Company-owned properties have no management fee
    no_fee = {"management_fee_type": "", "management_fee_percent": Decimal("0")}

    # Buildings (have units; rent and status live on the units)
    reem = prop(
        "RMT", name="Reem Marina Tower", owner=owners["Khalifa Al Mansoori"],
        property_type=T.RESIDENTIAL_BUILDING, emirate=E.ABU_DHABI,
        address="Al Reem Island, Abu Dhabi", year_built=2017, total_floors=10,
        status="", **percent_fee("5.00"),
    )
    hub = prop(
        "SBH", name="Saadiyat Business Hub", owner=owners["Gulf Horizon Investments LLC"],
        property_type=T.COMMERCIAL_BUILDING, emirate=E.ABU_DHABI,
        address="Saadiyat Island, Abu Dhabi", year_built=2019, total_floors=5,
        status="", **percent_fee("6.00"),
    )
    marina = prop(
        "DMR", name="Marina View Residence", owner=owners["Gulf Horizon Investments LLC"],
        property_type=T.RESIDENTIAL_BUILDING, emirate=E.DUBAI,
        address="Dubai Marina, Dubai", year_built=2014, total_floors=6,
        status="", **percent_fee("5.00"),
    )

    # Standalone properties (no units; they carry their own annual rent and status)
    villa_status = [S.OCCUPIED, S.OCCUPIED, S.VACANT, S.RESERVED]
    for i in range(1, 5):
        fee = (
            percent_fee("4.50")
            if i < 4
            else {  # one villa shows the "fixed amount" fee type
                "management_fee_type": F.FIXED,
                "management_fee_percent": Decimal("0"),
                "management_fee_fixed": Decimal("6000"),
            }
        )
        prop(
            f"ZV{i:02d}", name=f"Zahia Villa {i}", owner=owners["Noura Al Suwaidi"],
            property_type=T.VILLA, emirate=E.SHARJAH, address="Al Zahia, Sharjah",
            year_built=2021, total_floors=2,
            annual_rent=Decimal(180000 + i * 5000), status=villa_status[i - 1], **fee,
        )
    prop(
        "MSW01", name="Mussafah Warehouse 1", owner=company,
        property_type=T.WAREHOUSE, emirate=E.ABU_DHABI, address="Mussafah, Abu Dhabi",
        annual_rent=Decimal("150000"), status=S.OCCUPIED, **no_fee,
    )
    prop(
        "MSY01", name="Mussafah Storage Yard", owner=company,
        property_type=T.YARD, emirate=E.ABU_DHABI, address="Mussafah, Abu Dhabi",
        annual_rent=Decimal("90000"), status=S.VACANT, **no_fee,
    )
    prop(
        "CRS01", name="Corniche Retail Shop", owner=owners["Khalifa Al Mansoori"],
        property_type=T.SHOP, emirate=E.ABU_DHABI, address="Corniche Road, Abu Dhabi",
        annual_rent=Decimal("110000"), status=S.OCCUPIED, **percent_fee("5.00"),
    )
    prop(
        "KHA01", name="Khalidiyah Apartment 2B", owner=owners["Khalifa Al Mansoori"],
        property_type=T.APARTMENT, emirate=E.ABU_DHABI, address="Khalidiyah, Abu Dhabi",
        apartment_type="2br", bathrooms=2,
        annual_rent=Decimal("72000"), status=S.VACANT, **percent_fee("5.00"),
    )
    prop(
        "DMP01", name="Marina Parking Spot P-118", owner=company,
        property_type=T.PARKING_SPOT, emirate=E.DUBAI, address="Dubai Marina, Dubai",
        annual_rent=Decimal("12000"), status=S.OCCUPIED, **no_fee,
    )

    new_units = 0
    counter = 0

    # Reem Marina Tower: floors 1-10, four apartments per floor
    for floor in range(1, 11):
        for i, utype in enumerate([U.STUDIO, U.ONE_BR, U.TWO_BR, U.THREE_BR], start=1):
            counter += 1
            new_units += _make_unit(reem, f"{floor}0{i}", floor, utype, counter, premium=800)

    # Saadiyat Business Hub: 4 shops on the ground floor, 3 offices on floors 1-4
    for i in range(1, 5):
        counter += 1
        new_units += _make_unit(hub, f"G0{i}", 0, U.SHOP, counter)
    for floor in range(1, 5):
        for i in range(1, 4):
            counter += 1
            new_units += _make_unit(hub, f"{floor}0{i}", floor, U.OFFICE, counter, premium=1500)

    # Marina View Residence: floors 1-6, four apartments per floor
    for floor in range(1, 7):
        for i, utype in enumerate([U.STUDIO, U.ONE_BR, U.ONE_BR, U.TWO_BR], start=1):
            counter += 1
            new_units += _make_unit(marina, f"{floor}0{i}", floor, utype, counter, premium=1200)

    _grant_role_permissions()
    return (
        f"{Owner.objects.count()} owners, {Property.objects.count()} properties, "
        f"{Unit.objects.count()} units in total ({new_units} new units added)"
    )


# ---------------------------------------------------------------------------------------
# Large test data: about 50 owners, 200+ properties and 2,000+ units (for testing speed and paging).
# It is made with bulk inserts, so it takes seconds, not minutes. Safe to run twice (it checks a marker).
# ---------------------------------------------------------------------------------------
BULK_MARKER_EMAIL = "owner001@example.com"
# (floors, units per floor) for the sample buildings: 24 to 40 units each
BUILDING_SHAPES = [(6, 4), (8, 4), (10, 4), (12, 3), (5, 5), (7, 5), (9, 4), (6, 6)]
EXPIRY_OFFSETS = [-30, 15, 40, 200, 400, 800, 120, 600]  # days from today: expired, soon, later ...


def _next_number(model, prefix):
    import re

    top = 0
    for code in model.objects.exclude(code__isnull=True).values_list("code", flat=True):
        match = re.fullmatch(prefix + r"-(\d+)", code)
        if match:
            top = max(top, int(match.group(1)))
    return top


def _phone(n):
    return f"+971 {('50', '55', '52', '54', '56')[n % 5]} {100 + (n * 7) % 900} {1000 + (n * 13) % 9000}"


def seed_bulk_properties() -> str:
    owner_count = sample_count(52)
    if not owner_count or Owner.objects.filter(email=BULK_MARKER_EMAIL).exists():
        return "skipped (large test data is switched off or already there)"
    today = timezone.localdate()
    S = Property.Status
    T = Property.PropertyType
    F = Property.FeeType
    taken = {name.lower() for name in Owner.objects.values_list("name", flat=True)}

    # ---- owners ----------------------------------------------------------------
    owners, number = [], _next_number(Owner, "OWN")
    for i in range(owner_count):
        if i % 4 == 3:  # every fourth owner is a company
            kind = Owner.OwnerType.COMPANY
            base = f"{lists.COMPANY_STEMS[i % len(lists.COMPANY_STEMS)]} {lists.COMPANY_KINDS[(i // 4) % len(lists.COMPANY_KINDS)]} LLC"
        else:
            kind = Owner.OwnerType.INDIVIDUAL
            base = f"{lists.FIRST_NAMES[(i * 7) % len(lists.FIRST_NAMES)]} {lists.LAST_NAMES[(i * 5 + i // 40) % len(lists.LAST_NAMES)]}"
        name, extra = base, 1
        while name.lower() in taken:
            extra += 1
            name = f"{base} {extra}"
        taken.add(name.lower())
        number += 1
        owners.append(Owner(
            name=name, owner_type=kind, code=f"OWN-{number:04d}", email=f"owner{i + 1:03d}@example.com", phone=_phone(i),
            address="Abu Dhabi, United Arab Emirates" if i % 2 == 0 else "Dubai, United Arab Emirates",
        ))
    Owner.objects.bulk_create(owners)
    D = OwnerDocument.DocType
    OwnerContact.objects.bulk_create([
        OwnerContact(owner=o, name=o.name, role=OwnerContact.Role.OWNER, is_primary=True, mobile=o.phone, email=o.email)
        for o in owners
    ])
    OwnerDocument.objects.bulk_create([
        OwnerDocument(
            owner=o, doc_type=D.TRADE_LICENCE if o.owner_type == Owner.OwnerType.COMPANY else D.EMIRATES_ID,
            number=f"CN-{2000000 + i}" if o.owner_type == Owner.OwnerType.COMPANY else f"784-1975-{1000000 + i:07d}-1",
            expiry_date=today + timedelta(days=EXPIRY_OFFSETS[i % len(EXPIRY_OFFSETS)]),
        )
        for i, o in enumerate(owners)
    ])

    # ---- properties --------------------------------------------------------------
    building_count = sample_count(70)
    standalone_count = sample_count(140)
    names_taken = set()

    def fee(owner):
        return {"management_fee_type": F.PERCENT, "management_fee_percent": Decimal(4 + len(owner.name) % 4)}

    props = []
    shapes = {}
    for i in range(building_count):
        owner = owners[i % len(owners)]
        commercial = i % 5 == 4
        area, emirate = lists.AREAS[i % len(lists.AREAS)]
        kinds = lists.COMMERCIAL_KINDS if commercial else lists.BUILDING_KINDS
        base = f"{lists.BUILDING_STEMS[i % len(lists.BUILDING_STEMS)]} {kinds[(i // len(lists.BUILDING_STEMS)) % len(kinds)]}"
        name = base if base not in names_taken else f"{base} {i + 1}"
        names_taken.add(name)
        floors, per_floor = (5, 5) if commercial else BUILDING_SHAPES[i % len(BUILDING_SHAPES)]
        code = f"B{i + 1:03d}"
        shapes[code] = (floors, per_floor, commercial)
        props.append(Property(
            code=code, name=name, owner=owner, emirate=emirate, address=f"{area}, {emirate.replace('_', ' ').title()}",
            property_type=T.COMMERCIAL_BUILDING if commercial else T.RESIDENTIAL_BUILDING,
            year_built=2005 + i % 19, total_floors=floors - 1 if commercial else floors, status="", **fee(owner),
        ))
    standalone_types = [
        (T.VILLA, 190000, "Villa"), (T.SHOP, 110000, "Retail Shop"), (T.WAREHOUSE, 150000, "Warehouse"),
        (T.APARTMENT, 70000, "Apartment"), (T.PARKING_SPOT, 12000, "Parking Spot"), (T.YARD, 90000, "Storage Yard"),
    ]
    for i in range(standalone_count):
        owner = owners[(i * 3) % len(owners)]
        kind, rent, label = standalone_types[i % len(standalone_types)]
        area, emirate = lists.AREAS[(i * 5) % len(lists.AREAS)]
        extra = {}
        if kind == T.VILLA:
            extra = {"year_built": 2015 + i % 9, "total_floors": 2}
        elif kind == T.APARTMENT:
            extra = {"apartment_type": "1br" if i % 2 else "2br", "bathrooms": 2}
        props.append(Property(
            code=f"S{i + 1:04d}", name=f"{label} {i + 1:03d} - {area}", owner=owner, emirate=emirate, address=f"{area}, {emirate.replace('_', ' ').title()}",
            property_type=kind, annual_rent=Decimal(rent + (i % 9) * 3000), status=S.VACANT, **extra, **fee(owner),
        ))
    Property.objects.bulk_create(props)

    # ---- units -------------------------------------------------------------------
    units = []
    residential = [U.STUDIO, U.ONE_BR, U.ONE_BR, U.TWO_BR, U.TWO_BR, U.THREE_BR]
    counter = 0
    for prop in props:
        if prop.code not in shapes:
            continue
        floors, per_floor, commercial = shapes[prop.code]
        floor_numbers = range(0, floors) if commercial else range(1, floors + 1)
        for floor in floor_numbers:
            for k in range(1, per_floor + 1):
                counter += 1
                if commercial:
                    unit_type = U.SHOP if floor == 0 else U.OFFICE
                    number = f"G{k:02d}" if floor == 0 else f"{floor}{k:02d}"
                else:
                    unit_type = residential[(k + floor) % len(residential)]
                    number = f"{floor}{k:02d}"
                baths, area_sqft, rent = UNIT_SPECS[unit_type]
                # Under maintenance / reserved are fixed here; the leases step marks the rest Occupied or Vacant.
                state = S.MAINTENANCE if counter % 23 == 22 else S.RESERVED if counter % 31 == 30 else S.VACANT
                floor_code = "G" if floor == 0 else str(floor)
                units.append(Unit(
                    property=prop, unit_number=number, floor=floor_code, floor_sort=floor_sort_key(floor_code),
                    unit_type=unit_type, bathrooms=baths, area_sqft=Decimal(area_sqft + (counter % 7) * 25),
                    furnished=counter % 6 == 0, status=state, asking_rent=Decimal(rent + floor * 900 + (counter % 5) * 1000),
                ))
    Unit.objects.bulk_create(units, batch_size=1000)
    return (
        f"{len(owners)} owners, {len(props)} properties ({building_count} buildings, {standalone_count} standalone), "
        f"{len(units)} units added"
    )


def _seed_owner_details(owners):
    """Sample contacts, documents (valid / expiring / expired) and a bank account."""
    today = timezone.localdate()
    khalifa = owners["Khalifa Al Mansoori"]
    gulf = owners["Gulf Horizon Investments LLC"]
    noura = owners["Noura Al Suwaidi"]
    D = OwnerDocument.DocType
    if not khalifa.contacts.exists():
        OwnerContact.objects.create(owner=khalifa, name="Khalifa Al Mansoori", role="Owner",
                                    is_primary=True, mobile="+971 50 000 0001")
        OwnerContact.objects.create(owner=gulf, name="Mariam Hassan", role="Finance Manager",
                                    is_primary=True, mobile="+971 50 000 0010", email="mariam@gulfhorizon.example.com")
    if not khalifa.documents.exists():
        OwnerDocument.objects.create(owner=khalifa, doc_type=D.EMIRATES_ID, number="784-1980-0000001-1",
                                     expiry_date=today + timedelta(days=400))
        OwnerDocument.objects.create(owner=khalifa, doc_type=D.PASSPORT, number="P1234567",
                                     expiry_date=today + timedelta(days=20))
        OwnerDocument.objects.create(owner=gulf, doc_type=D.TRADE_LICENCE, number="CN-1234567",
                                     expiry_date=today - timedelta(days=10))
        OwnerDocument.objects.create(owner=noura, doc_type=D.EMIRATES_ID, number="784-1990-0000002-2",
                                     expiry_date=today + timedelta(days=700))
    if not khalifa.bank_accounts.exists():
        OwnerBankAccount.objects.create(owner=khalifa, account_holder="Khalifa Al Mansoori",
                                        bank_name="Emirates NBD", iban="AE070331234567890123456",
                                        swift="EBILAEAD", is_default=True)


def _grant_role_permissions():
    """Give the demo roles sensible permissions on this module's data."""

    def perms(*codenames):
        return Permission.objects.filter(
            content_type__app_label="properties", codename__in=codenames
        )

    models_ = (
        "owner", "property", "unit", "ownercontact", "ownerdocument", "propertydocument", "unitdocument", "unitphoto",
        "propertyphoto",
    )
    everything = [f"{action}_{m}" for m in models_ for action in ("add", "change", "delete", "view")]
    everything += [f"{a}_ownerbankaccount" for a in ("add", "change", "delete", "view")]  # bank details
    exports = ["export_owner", "export_property", "export_unit"]  # downloading lists to Excel / CSV
    everything += exports
    view_only = [f"view_{m}" for m in models_]
    view_only_money = view_only + ["view_ownerbankaccount"] + exports  # only the Accountant sees bank accounts

    role_perms = {
        "Property Manager": everything,
        "Accountant": view_only_money,
        "Leasing Agent": view_only + ["change_unit"],
        "Maintenance Supervisor": ["view_property", "view_unit"],
    }
    for role, codenames in role_perms.items():
        group, _ = Group.objects.get_or_create(name=role)
        group.permissions.add(*perms(*codenames))


def erase_properties() -> str:
    units = Unit.objects.count()
    properties = Property.objects.count()
    owners = Owner.objects.filter(is_company=False).count()
    Unit.objects.all().delete()
    Property.objects.all().delete()
    # The built-in "Company Owned" owner is kept.
    Owner.objects.filter(is_company=False).delete()
    return f"removed {units} units, {properties} properties, {owners} owners"


# Seed after core (order 0); erase before core thanks to reverse ordering.
register_seeder("Properties & units", seed_properties, order=10)
register_seeder("Large test data: owners, properties and units", seed_bulk_properties, order=11)
register_eraser("Properties & units", erase_properties, order=10)
