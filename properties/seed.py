"""Sample data, eraser and role permissions for Module 1 (properties)."""
from decimal import Decimal

from django.contrib.auth.models import Group, Permission

from core.data_registry import register_eraser, register_seeder

from .models import Owner, Property, Unit

U = Unit.UnitType

# unit type -> (bedrooms, bathrooms, area sq ft, base yearly rent AED)
UNIT_SPECS = {
    U.STUDIO: (0, 1, 450, 42000),
    U.ONE_BR: (1, 2, 750, 62000),
    U.TWO_BR: (2, 3, 1200, 95000),
    U.THREE_BR: (3, 4, 1700, 140000),
    U.OFFICE: (0, 1, 900, 80000),
    U.SHOP: (0, 1, 650, 120000),
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
    beds, baths, area, rent = UNIT_SPECS[unit_type]
    _, created = Unit.objects.get_or_create(
        property=prop,
        unit_number=number,
        defaults={
            "floor": floor,
            "unit_type": unit_type,
            "bedrooms": beds,
            "bathrooms": baths,
            "area_sqft": Decimal(area),
            "furnished": counter % 6 == 0,
            "status": _status(counter),
            "asking_rent": Decimal(rent + premium * floor),
        },
    )
    return int(created)


def seed_properties() -> str:
    owners = {}
    for name, email, phone in [
        ("Khalifa Al Mansoori", "khalifa@example.com", "+971 50 000 0001"),
        ("Gulf Horizon Investments LLC", "info@gulfhorizon.example.com", "+971 2 000 0002"),
        ("Noura Al Suwaidi", "noura@example.com", "+971 55 000 0003"),
        ("Own Portfolio (Company-owned)", "", ""),
    ]:
        owners[name], _ = Owner.objects.get_or_create(
            name=name, defaults={"email": email, "phone": phone}
        )

    def prop(code, **fields):
        obj, _ = Property.objects.get_or_create(code=code, defaults=fields)
        return obj

    T = Property.PropertyType
    E = Property.Emirate
    own = owners["Own Portfolio (Company-owned)"]

    # Buildings (have units)
    reem = prop(
        "RMT", name="Reem Marina Tower", owner=owners["Khalifa Al Mansoori"],
        property_type=T.RESIDENTIAL_BUILDING, emirate=E.ABU_DHABI,
        address="Al Reem Island, Abu Dhabi", year_built=2017, total_floors=10,
        management_fee_percent=Decimal("5.00"),
    )
    hub = prop(
        "SBH", name="Saadiyat Business Hub", owner=owners["Gulf Horizon Investments LLC"],
        property_type=T.COMMERCIAL_BUILDING, emirate=E.ABU_DHABI,
        address="Saadiyat Island, Abu Dhabi", year_built=2019, total_floors=5,
        management_fee_percent=Decimal("6.00"),
    )
    marina = prop(
        "DMR", name="Marina View Residence", owner=owners["Gulf Horizon Investments LLC"],
        property_type=T.RESIDENTIAL_BUILDING, emirate=E.DUBAI,
        address="Dubai Marina, Dubai", year_built=2014, total_floors=6,
        management_fee_percent=Decimal("5.00"),
    )

    # Standalone properties (no units)
    for i in range(1, 5):
        prop(
            f"ZV{i:02d}", name=f"Zahia Villa {i}", owner=owners["Noura Al Suwaidi"],
            property_type=T.VILLA, emirate=E.SHARJAH, address="Al Zahia, Sharjah",
            year_built=2021, total_floors=2, management_fee_percent=Decimal("4.50"),
        )
    prop(
        "MSW01", name="Mussafah Warehouse 1", owner=own,
        property_type=T.WAREHOUSE, emirate=E.ABU_DHABI, address="Mussafah, Abu Dhabi",
        management_fee_percent=Decimal("0"),
    )
    prop(
        "MSY01", name="Mussafah Storage Yard", owner=own,
        property_type=T.YARD, emirate=E.ABU_DHABI, address="Mussafah, Abu Dhabi",
        management_fee_percent=Decimal("0"),
    )
    prop(
        "CRS01", name="Corniche Retail Shop", owner=owners["Khalifa Al Mansoori"],
        property_type=T.SHOP, emirate=E.ABU_DHABI, address="Corniche Road, Abu Dhabi",
        management_fee_percent=Decimal("5.00"),
    )
    prop(
        "KHA01", name="Khalidiyah Apartment 2B", owner=owners["Khalifa Al Mansoori"],
        property_type=T.APARTMENT, emirate=E.ABU_DHABI, address="Khalidiyah, Abu Dhabi",
        year_built=2010, total_floors=1, management_fee_percent=Decimal("5.00"),
    )
    prop(
        "DMP01", name="Marina Parking Spot P-118", owner=own,
        property_type=T.PARKING_SPOT, emirate=E.DUBAI, address="Dubai Marina, Dubai",
        management_fee_percent=Decimal("0"),
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


def _grant_role_permissions():
    """Give the demo roles sensible permissions on this module's data."""

    def perms(*codenames):
        return Permission.objects.filter(
            content_type__app_label="properties", codename__in=codenames
        )

    models_ = ("owner", "property", "unit")
    everything = [f"{action}_{m}" for m in models_ for action in ("add", "change", "delete", "view")]
    view_only = [f"view_{m}" for m in models_]

    role_perms = {
        "Property Manager": everything,
        "Accountant": view_only,
        "Leasing Agent": view_only + ["change_unit"],
        "Maintenance Supervisor": ["view_property", "view_unit"],
    }
    for role, codenames in role_perms.items():
        group, _ = Group.objects.get_or_create(name=role)
        group.permissions.add(*perms(*codenames))


def erase_properties() -> str:
    units = Unit.objects.count()
    properties = Property.objects.count()
    owners = Owner.objects.count()
    Unit.objects.all().delete()
    Property.objects.all().delete()
    Owner.objects.all().delete()
    return f"removed {units} units, {properties} properties, {owners} owners"


# Seed after core (order 0); erase before core thanks to reverse ordering.
register_seeder("Properties & units", seed_properties, order=10)
register_eraser("Properties & units", erase_properties, order=10)
