"""Excel / CSV export and import for owners."""
import csv
import io
from datetime import date

from django.http import HttpResponse

from .models import Owner

EXPORT_HEADERS = [
    "Code", "Name", "Type", "Active", "Email", "Phone", "Address",
    "Properties", "Units", "Documents", "Notes",
]
IMPORT_HEADERS = ["Name", "Type", "Email", "Phone", "Address", "Notes"]
MAX_IMPORT_ROWS = 1000

_ALIASES = {
    "name": "name", "owner name": "name",
    "type": "type", "owner type": "type",
    "email": "email", "e-mail": "email",
    "phone": "phone", "mobile": "phone", "telephone": "phone",
    "address": "address",
    "notes": "notes", "note": "notes",
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
        yield [
            owner.code or "",
            owner.name,
            owner.get_owner_type_display(),
            "Yes" if owner.is_active else "No",
            owner.email,
            owner.phone,
            owner.address,
            getattr(owner, "_property_count", owner.properties.count()),
            getattr(owner, "_unit_count", 0),
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
        sheet.append(row)
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
        writer.writerow(row)
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
    sheet.append(["Example Owner LLC", "Company", "owner@example.com", "+971 50 123 4567", "Abu Dhabi", ""])
    for column, width in zip("ABCDEF", [30, 12, 28, 18, 34, 36]):
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
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        sheet = load_workbook(upload, read_only=True, data_only=True).active
        return [[_cell_text(c) for c in row] for row in sheet.iter_rows(values_only=True)]
    if name.endswith(".csv"):
        text = upload.read().decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    raise ValueError("Please upload an Excel (.xlsx) or CSV (.csv) file.")


def parse_owner_file(upload):
    """
    Read the file and check every row. Returns (owners, errors).
    Nothing is saved here; the caller saves only when `errors` is empty.
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

    owners, errors, seen = [], [], set()
    for line, row in body:
        raw_type = get(row, "type").lower()
        if raw_type in ("", "individual", "person"):
            owner_type = Owner.OwnerType.INDIVIDUAL
        elif raw_type in ("company", "corporate", "business"):
            owner_type = Owner.OwnerType.COMPANY
        else:
            errors.append(f'Row {line}: type "{get(row, "type")}" is not valid. Use Individual or Company.')
            continue
        owner = Owner(
            name=get(row, "name"), owner_type=owner_type, email=get(row, "email"),
            phone=get(row, "phone"), address=get(row, "address"), notes=get(row, "notes"),
        )
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
