"""Small helpers for Excel / CSV downloads."""
import csv
import re
from datetime import date

from django.http import HttpResponse

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Phone numbers and plain numbers may start with + or - and are fine as they are.
_PLAIN = re.compile(r"[+-]?[0-9][0-9 ()\-.,]*")


def safe_cell(value):
    """
    Stop Excel from treating text typed into the system as a formula: a note that begins with
    = + - or @ would otherwise run when the downloaded file is opened.
    """
    if isinstance(value, str) and value and value[0] in "=+-@\t\r" and not _PLAIN.fullmatch(value):
        return "'" + value
    return value


def filename(stem, extension):
    return f"{stem}-{date.today().isoformat()}.{extension}"


def excel_response(title, headers, rows, stem, widths=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([safe_cell(value) for value in row])
    sheet.freeze_panes = "A2"
    for index, header in enumerate(headers, start=1):
        width = widths[index - 1] if widths and index <= len(widths) else max(12, len(header) + 4)
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    response = HttpResponse(content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="{filename(stem, "xlsx")}"'
    workbook.save(response)
    return response


def csv_response(headers, rows, stem):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename(stem, "csv")}"'
    response.write("﻿")  # so Excel reads Arabic names correctly
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([safe_cell(value) for value in row])
    return response
