"""Excel / CSV export and import for tenants."""
import csv
from datetime import date

from django.core.exceptions import ValidationError
from django.http import HttpResponse

from properties.import_tools import RowError, _describe, _getter, _load, _pick

from .models import Tenant

EXPORT_HEADERS = [
    "Code", "Name", "Type", "Active", "Nationality", "Email", "Phone", "Address", "Documents", "Notes",
]
IMPORT_HEADERS = ["Name", "Type", "Nationality", "Email", "Phone", "Address", "Notes"]
_REQUIRED = [("Name", "*name"), ("Nationality", "*nationality"), ("Email", "*email"), ("Phone", "*phone")]
_ALIASES = {
    "name": "name", "tenant name": "name",
    "type": "type", "tenant type": "type",
    "nationality": "nationality",
    "email": "email", "e mail": "email",
    "phone": "phone", "mobile": "phone", "telephone": "phone",
    "address": "address",
    "notes": "notes", "note": "notes",
}
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _docs_label(tenant):
    expired = getattr(tenant, "_docs_expired", None)
    soon = getattr(tenant, "_docs_soon", None)
    if expired is None:
        expired, soon = tenant.document_counts()
    if expired:
        return f"{expired} expired"
    if soon:
        return f"{soon} expiring soon"
    return "OK"


def export_rows(queryset):
    for t in queryset:
        yield [
            t.code or "", t.name, t.get_tenant_type_display(), "Yes" if t.is_active else "No",
            t.nationality, t.email, t.phone, t.address, _docs_label(t), t.notes,
        ]


def _filename(extension):
    return f"tenants-{date.today().isoformat()}.{extension}"


def excel_response(queryset):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Tenants"
    sheet.append(EXPORT_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in export_rows(queryset):
        sheet.append(row)
    sheet.freeze_panes = "A2"
    for column, width in zip("ABCDEFGHIJ", [11, 30, 12, 8, 16, 28, 18, 34, 16, 36]):
        sheet.column_dimensions[column].width = width
    response = HttpResponse(content_type=_XLSX)
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
    sheet.title = "Tenants"
    sheet.append(IMPORT_HEADERS)
    sheet.append(["Example Tenant", "Individual", "Egyptian", "tenant@example.com", "+971 50 123 4567", "Abu Dhabi", ""])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column, width in zip("ABCDEFG", [30, 12, 16, 28, 18, 34, 36]):
        sheet.column_dimensions[column].width = width
    response = HttpResponse(content_type=_XLSX)
    response["Content-Disposition"] = 'attachment; filename="tenants-import-template.xlsx"'
    workbook.save(response)
    return response


def parse_tenant_file(upload):
    """Returns (tenants, errors). Nothing is saved; the caller saves when there are no errors."""
    columns, body, errors = _load(upload, _REQUIRED, _ALIASES)
    if errors:
        return [], errors
    get = _getter(columns)
    types = Tenant.TenantType.choices
    result, seen = [], set()
    for line, row in body:
        try:
            tenant = Tenant(
                name=get(row, "name"),
                tenant_type=_pick(get(row, "type"), types, "Type") or Tenant.TenantType.INDIVIDUAL,
                nationality=get(row, "nationality"),
                email=get(row, "email"),
                phone=get(row, "phone"),
                address=get(row, "address"),
                notes=get(row, "notes"),
            )
            tenant.full_clean(exclude=["photo", "code"])
            key = (tenant.name.lower(), tenant.email, tenant.phone)
            if key in seen:
                raise RowError(f'"{tenant.name}" appears twice in the file with the same email and phone.')
            seen.add(key)
            result.append(tenant)
        except (RowError, ValidationError) as exc:
            errors.append(f"Row {line}: {_describe(exc)}")
    return result, errors
