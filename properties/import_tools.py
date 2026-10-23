"""Excel / CSV import for properties and units (owners are in owner_tools.py)."""
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.http import HttpResponse

from .models import Owner, Property, Unit
from .models_constants import normalise_unit_number
from .owner_tools import MAX_IMPORT_ROWS, _read_rows


class RowError(Exception):
    """A problem with one cell or row, in plain words."""


# ---- small helpers -----------------------------------------------------------------------
def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _pick(text, choices, field):
    """Match a typed word to a choice by its stored value or its label (capitals/spaces ignored)."""
    if not text:
        return ""
    wanted = _squash(text)
    for value, label in choices:
        if wanted in (_squash(value), _squash(label)):
            return value
    allowed = ", ".join(label for _, label in choices)
    raise RowError(f'{field} "{text}" is not valid. Use one of: {allowed}.')


def _number(text, field, integer=False):
    if not text:
        return None
    cleaned = text.replace(",", "").replace(" ", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise RowError(f'{field} "{text}" is not a number.')
    if integer:
        if value != value.to_integral_value():
            raise RowError(f'{field} "{text}" must be a whole number.')
        return int(value)
    return value


def _yes_no(text, field):
    if not text:
        return False
    word = text.strip().lower()
    if word in ("yes", "y", "true", "1"):
        return True
    if word in ("no", "n", "false", "0"):
        return False
    raise RowError(f'{field} "{text}" must be Yes or No.')


def _describe(exc):
    if isinstance(exc, RowError):
        return str(exc)
    messages = getattr(exc, "message_dict", None)
    if messages:
        return "; ".join(f"{field}: {' '.join(msgs)}" for field, msgs in messages.items())
    return "; ".join(getattr(exc, "messages", [str(exc)]))


def _load(upload, headers, aliases):
    """Read the file and map its columns. Returns (columns, body_rows, errors)."""
    try:
        rows = _read_rows(upload)
    except ValueError as exc:
        return None, None, [str(exc)]
    except Exception:
        return None, None, ["The file could not be read. Use the template from this page."]
    if not rows:
        return None, None, ["The file is empty."]
    columns = {}
    for index, header in enumerate(rows[0]):
        key = aliases.get(_norm(header))
        if key:
            columns[key] = index
    missing = [h for h, key in headers if key.startswith("*") and key[1:] not in columns]
    if missing:
        return None, None, [
            f'The first row must contain the column(s): {", ".join(missing)}. Use the template from this page.'
        ]
    body = [(n, r) for n, r in enumerate(rows[1:], start=2) if any(c for c in r)]
    if len(body) > MAX_IMPORT_ROWS:
        return None, None, [f"The file has {len(body)} rows. The maximum is {MAX_IMPORT_ROWS} per import."]
    return columns, body, []


def _getter(columns):
    def get(row, key):
        index = columns.get(key)
        return row[index].strip() if index is not None and index < len(row) else ""

    return get


# ---- properties ----------------------------------------------------------------------------
PROPERTY_HEADERS = [
    "Code", "Name", "Type", "Owner", "Emirate", "Address", "Map link", "Year built",
    "Total floors", "Apartment type", "Bathrooms", "Annual rent (AED)", "Status",
    "Fee type", "Fee percent", "Fee fixed (AED)", "Notes",
]
_PROPERTY_ALIASES = {
    "code": "code", "property code": "code",
    "name": "name", "property name": "name",
    "type": "type", "property type": "type",
    "owner": "owner", "owner name": "owner",
    "emirate": "emirate",
    "address": "address",
    "map link": "map_link", "map": "map_link",
    "year built": "year_built", "year": "year_built",
    "total floors": "total_floors", "floors": "total_floors",
    "apartment type": "apartment_type",
    "bathrooms": "bathrooms",
    "annual rent aed": "annual_rent", "annual rent": "annual_rent", "rent": "annual_rent",
    "status": "status",
    "fee type": "fee_type", "management fee type": "fee_type",
    "fee percent": "fee_percent", "fee": "fee_percent", "management fee": "fee_percent",
    "fee fixed aed": "fee_fixed", "fee fixed": "fee_fixed",
    "notes": "notes", "note": "notes",
}
_PROPERTY_REQUIRED = [("Code", "*code"), ("Name", "*name"), ("Type", "*type")]


def parse_property_file(upload):
    """Returns (properties, errors). Nothing is saved; the caller saves when there are no errors."""
    columns, body, errors = _load(upload, _PROPERTY_REQUIRED, _PROPERTY_ALIASES)
    if errors:
        return [], errors
    get = _getter(columns)
    owners = {o.name.lower(): o for o in Owner.objects.all()}
    company = Owner.company()
    type_choices = Property.PropertyType.choices
    emirate_choices = Property.Emirate.choices
    status_choices = Property.Status.choices
    apartment_choices = Property._meta.get_field("apartment_type").choices

    result, seen = [], set()
    for line, row in body:
        try:
            owner_name = get(row, "owner")
            if not owner_name or owner_name.lower() == company.name.lower():
                owner = company
            else:
                owner = owners.get(owner_name.lower())
                if owner is None:
                    raise RowError(f'Owner "{owner_name}" does not exist. Create the owner first (or leave blank for Company Owned).')
            ptype = _pick(get(row, "type"), type_choices, "Type")
            if not ptype:
                raise RowError("Type is required.")
            has_units = ptype in Property.BUILDING_TYPES
            status = _pick(get(row, "status"), status_choices, "Status")
            if not has_units and not status:
                status = Property.Status.VACANT

            fee_text = get(row, "fee_type").lower()
            fee_percent = _number(get(row, "fee_percent"), "Fee percent")
            fee_fixed = _number(get(row, "fee_fixed"), "Fee fixed (AED)")
            if fee_text.startswith(("perc", "%")):
                fee_type = Property.FeeType.PERCENT
            elif fee_text.startswith("fix"):
                fee_type = Property.FeeType.FIXED
            elif fee_text:
                raise RowError(f'Fee type "{get(row, "fee_type")}" is not valid. Use Percent or Fixed.')
            elif fee_fixed:
                fee_type = Property.FeeType.FIXED
            elif fee_percent:
                fee_type = Property.FeeType.PERCENT
            else:
                fee_type = ""

            prop = Property(
                code=get(row, "code").upper(),
                name=get(row, "name"),
                property_type=ptype,
                owner=owner,
                emirate=_pick(get(row, "emirate"), emirate_choices, "Emirate") or Property.Emirate.ABU_DHABI,
                address=get(row, "address"),
                map_link=get(row, "map_link"),
                year_built=_number(get(row, "year_built"), "Year built", integer=True),
                total_floors=_number(get(row, "total_floors"), "Total floors", integer=True),
                apartment_type=_pick(get(row, "apartment_type"), apartment_choices, "Apartment type"),
                bathrooms=_number(get(row, "bathrooms"), "Bathrooms", integer=True),
                annual_rent=_number(get(row, "annual_rent"), "Annual rent (AED)"),
                status=status,
                management_fee_type=fee_type,
                management_fee_percent=fee_percent if fee_percent is not None else 0,
                management_fee_fixed=fee_fixed,
                notes=get(row, "notes"),
            )
            prop.full_clean(exclude=["created_at"])
            if prop.code in seen:
                raise RowError(f'Code "{prop.code}" appears twice in the file.')
            seen.add(prop.code)
            result.append(prop)
        except (RowError, ValidationError) as exc:
            errors.append(f"Row {line}: {_describe(exc)}")
    return result, errors


# ---- units -----------------------------------------------------------------------------------
UNIT_HEADERS = [
    "Property code", "Unit number", "Floor", "Type", "Bathrooms", "Area (sq ft)",
    "Furnished", "Status", "Asking rent (AED / year)", "Notes",
]
_UNIT_ALIASES = {
    "property code": "property", "property": "property", "code": "property", "building": "property",
    "unit number": "number", "unit": "number", "number": "number", "unit no": "number",
    "floor": "floor",
    "type": "type", "unit type": "type",
    "bathrooms": "bathrooms",
    "area sq ft": "area", "area": "area",
    "furnished": "furnished",
    "status": "status",
    "asking rent aed year": "rent", "asking rent": "rent", "rent": "rent",
    "notes": "notes", "note": "notes",
}
_UNIT_REQUIRED = [("Property code", "*property"), ("Unit number", "*number"), ("Type", "*type")]


def parse_unit_file(upload):
    columns, body, errors = _load(upload, _UNIT_REQUIRED, _UNIT_ALIASES)
    if errors:
        return [], errors
    get = _getter(columns)
    properties = {p.code.upper(): p for p in Property.objects.all()}
    type_choices = Unit.UnitType.choices
    status_choices = Unit.Status.choices

    result, seen = [], set()
    for line, row in body:
        try:
            code = get(row, "property").upper()
            prop = properties.get(code)
            if prop is None:
                raise RowError(f'Property code "{get(row, "property")}" does not exist.')
            unit_type = _pick(get(row, "type"), type_choices, "Type")
            if not unit_type:
                raise RowError("Type is required.")
            unit = Unit(
                property=prop,
                unit_number=normalise_unit_number(get(row, "number")),
                floor=get(row, "floor").upper(),
                unit_type=unit_type,
                bathrooms=_number(get(row, "bathrooms"), "Bathrooms", integer=True),
                area_sqft=_number(get(row, "area"), "Area (sq ft)"),
                furnished=_yes_no(get(row, "furnished"), "Furnished"),
                status=_pick(get(row, "status"), status_choices, "Status"),
                asking_rent=_number(get(row, "rent"), "Asking rent"),
                notes=get(row, "notes"),
            )
            unit.full_clean(exclude=["floor_sort", "created_at"])
            key = (prop.pk, unit.unit_number)
            if key in seen:
                raise RowError(f'Unit "{code}-{unit.unit_number}" appears twice in the file.')
            seen.add(key)
            result.append(unit)
        except (RowError, ValidationError) as exc:
            errors.append(f"Row {line}: {_describe(exc)}")
    return result, errors


# ---- templates -------------------------------------------------------------------------------
def _template(title, headers, example, filename, allowed):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append(headers)
    sheet.append(example)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for index in range(1, len(headers) + 1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = max(14, len(headers[index - 1]) + 4)
    help_sheet = workbook.create_sheet("Allowed values")
    for name, values in allowed:
        help_sheet.append([name, ", ".join(values)])
    help_sheet.column_dimensions["A"].width = 18
    help_sheet.column_dimensions["B"].width = 100
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


def property_template_response():
    return _template(
        "Properties", PROPERTY_HEADERS,
        ["RMT", "Rimal Tower", "Residential Building", "", "Abu Dhabi", "Corniche Road", "", 2015, 12,
         "", "", "", "", "", "", "", ""],
        "properties-import-template.xlsx",
        [
            ("Type", [label for _, label in Property.PropertyType.choices]),
            ("Emirate", [label for _, label in Property.Emirate.choices]),
            ("Status", [label for _, label in Property.Status.choices] + ["(standalone only; blank = Vacant)"]),
            ("Fee type", ["Percent", "Fixed", "(not needed for Company Owned)"]),
            ("Owner", ["Exact owner name; blank = Company Owned"]),
        ],
    )


def unit_template_response():
    return _template(
        "Units", UNIT_HEADERS,
        ["RMT", "101", "1", "2BHK", 3, 1200, "No", "Vacant", 95000, ""],
        "units-import-template.xlsx",
        [
            ("Type", [label for _, label in Unit.UnitType.choices]),
            ("Status", [label for _, label in Unit.Status.choices]),
            ("Furnished", ["Yes", "No"]),
            ("Floor", ["G, B1, P1, M, 1, 2, 3 ... (as in the unit form)"]),
        ],
    )
