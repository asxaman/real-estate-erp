"""Excel / CSV export and import for owners."""
import csv
import io
from datetime import date

from django.http import HttpResponse

from core.exports import safe_cell

from .models import Owner, Unit
from .models_constants import lost_zero_problem

EXPORT_HEADERS = [
    "Code", "Name", "Type", "Active", "Email", "Phone", "Address",
    "Active properties", "Units", "Documents", "Notes",
]
IMPORT_HEADERS = ["Name", "Type", "Email", "Phone", "Address", "Notes", "TRN"]
MAX_IMPORT_ROWS = 1000
MAX_IMPORT_BYTES = 5 * 1024 * 1024  # an import file may be at most 5 MB

_ALIASES = {
    "name": "name", "owner name": "name",
    "type": "type", "owner type": "type",
    "email": "email", "e-mail": "email",
    "phone": "phone", "mobile": "phone", "telephone": "phone",
    "address": "address",
    "notes": "notes", "note": "notes",
    "trn": "trn", "tax registration number": "trn",
}


def _docs_label(owner):
    expired = getattr(owner, "_docs_expired", None)
    soon = getattr(owner, "_docs_soon", None)
    if expired is None:
        expired, soon = owner.document_counts()
    if expired:
        return f"{expired} expired"
    if soon:
        return f"{soon} expiring soon"
    return "OK"


def export_rows(queryset):
    for owner in queryset:
        # Only active properties count (the list page already worked this out; otherwise ask the database).
        if hasattr(owner, "_property_count"):
            properties, units = owner._property_count, owner._unit_count
        else:
            properties = owner.properties.filter(is_active=True).count()
            units = Unit.objects.filter(property__owner=owner, property__is_active=True).count()
        yield [
            owner.code or "",
            owner.name,
            owner.get_owner_type_display(),
            "Yes" if owner.is_active else "No",
            owner.email,
            owner.phone,
            owner.address,
            properties,
            units,
            _docs_label(owner),
            owner.notes,
        ]


def _filename(extension):
    return f"owners-{date.today().isoformat()}.{extension}"


def excel_response(queryset):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Owners"
    sheet.append(EXPORT_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in export_rows(queryset):
        sheet.append([safe_cell(value) for value in row])
    sheet.freeze_panes = "A2"
    for column, width in zip("ABCDEFGHIJKL", [11, 30, 12, 8, 28, 18, 34, 11, 8, 16, 16, 36]):
        sheet.column_dimensions[column].width = width
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{_filename("xlsx")}"'
    workbook.save(response)
    return response


def csv_response(queryset):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_filename("csv")}"'
    response.write("﻿")  # so Excel reads Arabic names correctly
    writer = csv.writer(response)
    writer.writerow(EXPORT_HEADERS)
    for row in export_rows(queryset):
        writer.writerow([safe_cell(value) for value in row])
    return response


def template_response():
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Owners"
    sheet.append(IMPORT_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.append(["Example Owner LLC", "Company", "owner@example.com", "+971 50 123 4567", "Abu Dhabi", "", ""])
    for row in range(2, 1002):  # Phone column as Text, so Excel keeps a leading 0 or +
        sheet.cell(row=row, column=4).number_format = "@"
    for column, width in zip("ABCDEFG", [30, 12, 28, 18, 34, 36, 18]):
        sheet.column_dimensions[column].width = width
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="owners-import-template.xlsx"'
    workbook.save(response)
    return response


# ---- import ------------------------------------------------------------------------------
def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _read_rows(upload):
    """Return a list of lists (first row = headers) from an .xlsx or .csv upload."""
    name = (upload.name or "").lower()
    if getattr(upload, "size", 0) > MAX_IMPORT_BYTES:
        raise ValueError(f"The file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB. Split it into smaller files.")
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        sheet = load_workbook(upload, read_only=True, data_only=True).active
        return [[_cell_text(c) for c in row] for row in sheet.iter_rows(values_only=True)]
    if name.endswith(".csv"):
        text = upload.read().decode("utf-8-sig", errors="replace")
        header = text.split("\n", 1)[0]  # the separator is judged from the header line only
        try:
            dialect = csv.Sniffer().sniff(header, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    raise ValueError("Please upload an Excel (.xlsx) or CSV (.csv) file.")


def parse_owner_file(upload, update=False):
    """
    Read the file and check every row. Returns (owners, errors).
    Nothing is saved here; the caller saves only when `errors` is empty.
    With update=True a row whose Name matches an existing owner changes that owner
    (blank cells are left alone).
    """
    try:
        rows = _read_rows(upload)
    except ValueError as exc:
        return [], [str(exc)]
    except Exception:
        return [], ["The file could not be read. Use the template from this page."]
    if not rows:
        return [], ["The file is empty."]

    columns = {}
    for index, header in enumerate(rows[0]):
        key = _ALIASES.get(header.strip().lower())
        if key:
            columns[key] = index
    if "name" not in columns:
        return [], ['The first row must contain a column called "Name". Use the template from this page.']

    body = [(n, r) for n, r in enumerate(rows[1:], start=2) if any(c for c in r)]
    if len(body) > MAX_IMPORT_ROWS:
        return [], [f"The file has {len(body)} rows. The maximum is {MAX_IMPORT_ROWS} per import."]

    def get(row, key):
        index = columns.get(key)
        return row[index].strip() if index is not None and index < len(row) else ""

    existing = {" ".join(o.name.split()).lower(): o for o in Owner.objects.all()} if update else {}
    owners, errors, seen = [], [], set()
    for line, row in body:
        problem = lost_zero_problem(get(row, "phone"))
        if problem:
            errors.append(f'Row {line}: phone "{get(row, "phone")}" {problem}')
            continue
        raw_type = get(row, "type").lower()
        if raw_type in ("", "individual", "person"):
            owner_type = Owner.OwnerType.INDIVIDUAL if raw_type else None
        elif raw_type in ("company", "corporate", "business"):
            owner_type = Owner.OwnerType.COMPANY
        else:
            errors.append(f'Row {line}: type "{get(row, "type")}" is not valid. Use Individual or Company.')
            continue
        values = {
            attr: get(row, key)
            for key, attr in (("email", "email"), ("phone", "phone"), ("address", "address"),
                              ("notes", "notes"), ("trn", "trn"))
            if get(row, key)
        }
        if owner_type:
            values["owner_type"] = owner_type
        current = existing.get(" ".join(get(row, "name").split()).lower())
        if current is not None:
            if current.is_company:
                errors.append(f'Row {line}: "{current.name}" is the built-in owner and cannot be changed by an import.')
                continue
            owner = current
            for attr, value in values.items():
                setattr(owner, attr, value)
        else:
            values.setdefault("owner_type", Owner.OwnerType.INDIVIDUAL)
            owner = Owner(name=get(row, "name"), **values)
        try:
            owner.full_clean(exclude=["user", "photo"])
        except Exception as exc:  # ValidationError
            messages = getattr(exc, "message_dict", None)
            if messages:
                text = "; ".join(f"{field}: {' '.join(msgs)}" for field, msgs in messages.items())
            else:
                text = "; ".join(getattr(exc, "messages", [str(exc)]))
            errors.append(f"Row {line}: {text}")
            continue
        key = owner.name.lower()
        if key in seen:
            errors.append(f'Row {line}: "{owner.name}" appears twice in the file.')
            continue
        seen.add(key)
        owners.append(owner)
    return owners, errors
