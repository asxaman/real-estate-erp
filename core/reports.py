"""The Reports page: one place that lists every report. To add a report, add one line to REPORTS."""
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import reverse


# (title, what it shows, icon, url name, permission needed)
REPORTS = [
    ("Rent Roll", "Every lease running today: tenant, rent, what was received, what is overdue and the next payment.",
     "receipt_long", "admin:leases_lease_rentroll", "leases.view_lease"),
    ("Monthly Collection", "For each month of a year: rent expected, rent received, what is still open and the percentage collected.",
     "calendar_month", "admin:leases_lease_monthly", "leases.view_lease"),
]


def reports_view(request):
    available = [
        {"title": title, "text": text, "icon": icon, "url": reverse(url)}
        for title, text, icon, url, permission in REPORTS if request.user.has_perm(permission)
    ]
    return TemplateResponse(request, "core/reports.html", {**admin.site.each_context(request), "title": "Reports", "reports": available})
