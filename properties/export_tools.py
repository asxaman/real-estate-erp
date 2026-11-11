"""Excel / CSV export for properties and units."""
from django.utils import timezone

from core import exports

from .models import Unit

PROPERTY_EXPORT_HEADERS = [
    "Code", "Name", "Type", "Owner", "Emirate", "Active", "Address", "Year built", "Total floors",
    "Units", "Vacant units", "Annual rent (AED)", "Status", "Management fee", "Notes",
    "Area / community", "Parking spaces", "Title deed no.", "Plot no.", "Makani no.", "Utility premise no.",
    "Inactive reason",
]
UNIT_EXPORT_HEADERS = [
    "Property code", "Property", "Unit number", "Floor", "Type", "Bathrooms", "Area (sq ft)",
    "Furnished", "Status", "Asking rent (AED / year)", "Notes",
    "Parking spaces", "View", "Utility account no.",
]


def _fee_texts(queryset):
    """property id -> the fee of the owner agreement that covers it today (one query for the whole list)."""
    from agreements.models import AgreementProperty, OwnerAgreement, running_q

    today = timezone.localdate()
    found = {}
    lines = (
        AgreementProperty.objects.filter(property__in=queryset.values("pk"))
        .filter(agreement__in=OwnerAgreement.objects.filter(running_q(today)))
        .select_related("agreement").order_by("agreement__start_date", "agreement_id")
    )
    for line in lines:
        found[line.property_id] = f"{line.agreement.code}: {line.agreement.fee_text()}"  # the latest start wins
    return found


def property_rows(queryset):
    fees = _fee_texts(queryset)
    for prop in queryset.select_related("owner"):
        if prop.has_units:
            if hasattr(prop, "_unit_count"):  # already counted by the list page
                units, vacant = prop._unit_count, prop._vacant_count
            else:
                units = prop.units.count()
                vacant = prop.units.filter(status=Unit.Status.VACANT).count()
        else:
            units = vacant = ""
        yield [
            prop.code, prop.name, prop.get_property_type_display(), prop.owner.name,
            prop.get_emirate_display(), "Yes" if prop.is_active else "No", prop.address,
            prop.year_built or "", prop.total_floors or "", units, vacant,
            prop.annual_rent if prop.annual_rent is not None else "",
            prop.get_status_display() if prop.status else "", "None (company owned)" if prop.is_company_owned else fees.get(prop.pk, ""), prop.notes,
            prop.community, prop.parking_spaces if prop.parking_spaces is not None else "",
            prop.title_deed_number, prop.plot_number, prop.makani_number, prop.dewa_premise_number,
            prop.inactive_reason,
        ]


def unit_rows(queryset):
    for unit in queryset.select_related("property"):
        yield [
            unit.property.code, unit.property.name, unit.unit_number, unit.floor,
            unit.get_unit_type_display(), unit.bathrooms or "",
            unit.area_sqft if unit.area_sqft is not None else "",
            "Yes" if unit.furnished else "No", unit.get_status_display(),
            unit.asking_rent if unit.asking_rent is not None else "", unit.notes,
            unit.parking_spaces if unit.parking_spaces is not None else "",
            unit.get_view_display(), unit.dewa_account_number,
        ]


def properties_excel(queryset):
    return exports.excel_response(
        "Properties", PROPERTY_EXPORT_HEADERS, property_rows(queryset), "properties",
        [10, 28, 22, 26, 14, 8, 34, 11, 12, 8, 13, 16, 14, 20, 36, 20, 10, 16, 12, 14, 18, 24],
    )


def properties_csv(queryset):
    return exports.csv_response(PROPERTY_EXPORT_HEADERS, property_rows(queryset), "properties")


def units_excel(queryset):
    return exports.excel_response(
        "Units", UNIT_EXPORT_HEADERS, unit_rows(queryset), "units",
        [14, 28, 13, 8, 12, 11, 13, 10, 16, 22, 36, 10, 12, 18],
    )


def units_csv(queryset):
    return exports.csv_response(UNIT_EXPORT_HEADERS, unit_rows(queryset), "units")
