"""Exports, the rent roll, the monthly collection view and the printable tenancy pages."""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from core import exports
from core.admin_utils import format_amount
from core.formats import fmt_date

from .models import Lease, LeasePayment

OPEN = (LeasePayment.Status.PENDING, LeasePayment.Status.BOUNCED)
ZERO = Decimal("0.00")

LEASE_HEADERS = [
    "Lease no.", "Tenant", "Rented", "Starts", "Ends", "State", "Annual rent (AED)", "VAT", "Payments per year",
    "Received (AED, with VAT)", "Still to receive (AED, with VAT)", "Overdue payments", "Security deposit (AED)",
    "Deposit is", "Commission (AED)", "Admin fee (AED)", "Ejari number", "Ejari expires", "Notes",
]
PAYMENT_HEADERS = [
    "Lease no.", "Tenant", "Rented", "Payment no.", "Due on", "Amount (AED)", "VAT (AED)", "Total (AED)", "Method",
    "Cheque no.", "Bank", "Date on cheque", "Deposited on", "Status", "Paid on", "Days late", "Late fee (AED)", "Notes",
]
ROLL_HEADERS = [
    "Property", "Rented", "Tenant", "Lease no.", "Ends", "Annual rent (AED)", "Monthly rent (AED)", "VAT",
    "Received (AED)", "Overdue (AED)", "Next payment due", "Next payment (AED)",
]


def _state_text(lease, today):
    state = lease.state(today)
    if state == "active" and lease.ends_soon(today):
        return "Ending soon"
    return {"active": "Active", "upcoming": "Not started", "expired": "Expired", "terminated": "Terminated"}[state]


def lease_rows(queryset):
    today = timezone.localdate()
    for lease in queryset.select_related("tenant", "unit__property", "property").prefetch_related("payments"):
        payments = list(lease.payments.all())
        paid = sum((p.total for p in payments if p.status == LeasePayment.Status.PAID), ZERO)
        total = sum((p.total for p in payments), ZERO)
        yield [
            lease.code, lease.tenant.name, lease.item_name, lease.start_date, lease.end_date, _state_text(lease, today),
            lease.annual_rent, "Yes" if lease.vat_applies else "No", lease.installments, paid, max(total - paid, ZERO),
            sum(1 for p in payments if p.is_overdue(today)), lease.security_deposit, lease.get_deposit_status_display(),
            lease.commission, lease.admin_fee, lease.ejari_number, lease.ejari_expiry or "", lease.notes,
        ]


def payment_rows(queryset):
    for p in queryset.select_related("lease__tenant", "lease__unit__property", "lease__property"):
        yield [
            p.lease.code, p.lease.tenant.name, p.lease.item_name, p.part, p.due_date, p.amount, p.vat, p.total,
            p.get_method_display(), p.cheque_number, p.bank_name, p.cheque_date or "", p.deposited_on or "",
            p.get_status_display(), p.paid_on or "", p.days_overdue() or "", p.late_fee() or "", p.notes,
        ]


def roll_rows():
    """One row for every lease that is running today (the rent roll)."""
    today = timezone.localdate()
    leases = (
        Lease.objects.filter(status=Lease.Status.ACTIVE, start_date__lte=today, end_date__gte=today)
        .select_related("tenant", "unit__property", "property").prefetch_related("payments")
    )
    rows = []
    for lease in sorted(leases, key=lambda l: (l.property.code, l.unit.unit_number if l.unit_id else "", l.code)):
        payments = list(lease.payments.all())
        paid = sum((p.total for p in payments if p.status == LeasePayment.Status.PAID), ZERO)
        overdue = sum((p.total for p in payments if p.is_overdue(today)), ZERO)
        upcoming = sorted((p for p in payments if p.status == LeasePayment.Status.PENDING and p.due_date >= today), key=lambda p: p.due_date)
        rows.append({
            "lease": lease, "property": lease.property.name, "rented": lease.item_name, "tenant": lease.tenant.name,
            "ends": lease.end_date, "rent": lease.annual_rent, "monthly": (lease.annual_rent / 12).quantize(Decimal("0.01")),
            "vat": lease.vat_applies, "paid": paid, "overdue": overdue,
            "next_due": upcoming[0].due_date if upcoming else None, "next_amount": upcoming[0].total if upcoming else None,
        })
    return rows


def monthly_rows(year):
    """Expected, received and still-open money for each month of a year, by payment due date."""
    today = timezone.localdate()
    months = {m: {"expected": ZERO, "received": ZERO, "count": 0} for m in range(1, 13)}
    for p in LeasePayment.objects.filter(due_date__year=year).select_related("lease"):
        row = months[p.due_date.month]
        row["count"] += 1
        row["expected"] += p.total
        if p.status == LeasePayment.Status.PAID:
            row["received"] += p.total
    out = []
    for month, row in months.items():
        first = f"{year}-{month:02d}-01"
        last = (date(year, month, calendar.monthrange(year, month)[1]) + timedelta(days=1)).isoformat()
        out.append({
            "name": calendar.month_name[month], "count": row["count"], "expected": row["expected"],
            "received": row["received"], "open": row["expected"] - row["received"],
            "percent": int(row["received"] * 100 / row["expected"]) if row["expected"] else None,
            "current": (today.year, today.month) == (year, month),
            "url": reverse("admin:leases_leasepayment_changelist") + f"?due_date__gte={first}&due_date__lt={last}",
        })
    return out


class ReportsMixin:
    """Added to LeaseAdmin: exports, rent roll, monthly collection and printable pages."""

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("rent-roll/", wrap(self.rent_roll_view), name="leases_lease_rentroll"),
            path("monthly/", wrap(self.monthly_view), name="leases_lease_monthly"),
            path("<int:pk>/print/<str:kind>/", wrap(self.print_view), name="leases_lease_print"),
        ]
        return custom + super().get_urls()

    def report_links(self):
        return [
            ("Rent roll", reverse("admin:leases_lease_rentroll")),
            ("Monthly collection", reverse("admin:leases_lease_monthly")),
        ]

    def _page(self, request, template, title, **context):
        return TemplateResponse(request, template, {**self.admin_site.each_context(request), "title": title, **context})

    def rent_roll_view(self, request):
        if not self.has_view_permission(request):
            raise Http404
        rows = roll_rows()
        kind = request.GET.get("export", "")
        if kind in ("xlsx", "csv"):
            if not self.has_export_permission(request):
                raise Http404
            data = [
                [r["property"], r["rented"], r["tenant"], r["lease"].code, r["ends"], r["rent"], r["monthly"],
                 "Yes" if r["vat"] else "No", r["paid"], r["overdue"], r["next_due"] or "", r["next_amount"] or ""]
                for r in rows
            ]
            if kind == "csv":
                return exports.csv_response(ROLL_HEADERS, data, "rent-roll")
            return exports.excel_response("Rent roll", ROLL_HEADERS, data, "rent-roll")
        totals = {
            "rent": sum((r["rent"] for r in rows), ZERO), "monthly": sum((r["monthly"] for r in rows), ZERO),
            "paid": sum((r["paid"] for r in rows), ZERO), "overdue": sum((r["overdue"] for r in rows), ZERO),
        }
        return self._page(
            request, "leases/rent_roll.html", "Rent roll", rows=rows, totals=totals, today=timezone.localdate(),
            can_export=self.has_export_permission(request),
            list_url=reverse("admin:leases_lease_changelist"),
        )

    def monthly_view(self, request):
        if not self.has_view_permission(request):
            raise Http404
        this_year = timezone.localdate().year
        text = request.GET.get("year", "")
        year = int(text) if text.isdigit() and 2000 <= int(text) <= 2100 else this_year
        rows = monthly_rows(year)
        total = {
            "expected": sum((r["expected"] for r in rows), ZERO), "received": sum((r["received"] for r in rows), ZERO),
        }
        total["open"] = total["expected"] - total["received"]
        total["percent"] = int(total["received"] * 100 / total["expected"]) if total["expected"] else None
        base = reverse("admin:leases_lease_monthly")
        return self._page(
            request, "leases/monthly.html", f"Monthly collection {year}", rows=rows, total=total, year=year,
            prev_url=f"{base}?year={year - 1}", next_url=f"{base}?year={year + 1}",
            list_url=reverse("admin:leases_lease_changelist"),
        )

    def print_view(self, request, pk, kind):
        if not self.has_view_permission(request):
            raise Http404
        if kind not in ("summary", "statement"):
            raise Http404
        lease = get_object_or_404(Lease.objects.select_related("tenant", "unit__property", "property"), pk=pk)
        today = timezone.localdate()
        payments = list(lease.payments.all())
        rows, balance = [], ZERO
        for p in payments:
            if p.status == LeasePayment.Status.PAID:
                balance += p.total
            rows.append({
                "p": p, "late": p.days_overdue(today), "state": (
                    "Paid" if p.status == LeasePayment.Status.PAID else "Bounced" if p.status == LeasePayment.Status.BOUNCED
                    else "Overdue" if p.is_overdue(today) else "Pending"
                ),
                "received_so_far": balance if p.status == LeasePayment.Status.PAID else None,
            })
        paid = lease.paid_total()
        total = lease.scheduled_total()
        context = {
            "lease": lease, "kind": kind, "rows": rows, "today": today, "paid": paid, "total": total,
            "outstanding": max(total - paid, ZERO), "late_fees": lease.late_fees(today),
            "deducted": lease.deposit_deducted(), "deposit_left": lease.deposit_balance(),
            "state": _state_text(lease, today),
            "title": ("Tenancy summary " if kind == "summary" else "Payment statement ") + lease.code,
        }
        return TemplateResponse(request, "leases/print.html", context)
