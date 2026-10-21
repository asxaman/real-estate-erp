"""Numbers for the home page (the first page after login)."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from .models import (
    Cheque,
    Expense,
    Lease,
    MaintenanceRequest,
    Owner,
    OwnerStatement,
    Tenant,
    Unit,
)

ZERO = Decimal("0.00")


def _url(name, **query):
    link = reverse(f"admin:{name}")
    return f"{link}?{urlencode(query)}" if query else link


def _month_start(d, back=0):
    """First day of the month, `back` months before the month of `d`."""
    index = d.year * 12 + (d.month - 1) - back
    return d.replace(year=index // 12, month=index % 12 + 1, day=1)


def _next_month(d):
    return _month_start(d, back=-1)


def _sum(queryset):
    return queryset.aggregate(total=Sum("amount"))["total"] or ZERO


def dashboard_context(request):
    today = timezone.localdate()
    month_start = _month_start(today)
    next_month = _next_month(month_start)
    S = Cheque.Status
    rent = Cheque.objects.filter(purpose=Cheque.Purpose.RENT)

    # --- cheques -----------------------------------------------------------
    cheque_tiles = [
        ("Deposit today", rent.filter(status=S.RECEIVED, cheque_date=today).count(), "primary"),
        ("Overdue to deposit", rent.filter(status=S.RECEIVED, cheque_date__lt=today).count(), "warn"),
        ("Not received", rent.filter(status=S.PENDING, cheque_date__lte=today).count(), "warn"),
        ("At the bank", rent.filter(status=S.DEPOSITED).count(), "info"),
        ("Bounced", rent.filter(status=S.RETURNED).count(), "danger"),
    ]

    # --- money this month --------------------------------------------------
    live = [S.PENDING, S.RECEIVED, S.DEPOSITED, S.CLEARED]
    expected = _sum(
        rent.filter(status__in=live, cheque_date__gte=month_start, cheque_date__lt=next_month)
    )
    collected = _sum(
        rent.filter(status=S.CLEARED, cleared_date__gte=month_start, cleared_date__lt=next_month)
    )
    overdue_total = _sum(
        rent.filter(status__in=[S.PENDING, S.RECEIVED, S.RETURNED], cheque_date__lt=today)
    )

    months = []
    for back in range(5, -1, -1):
        start = _month_start(today, back)
        total = _sum(
            rent.filter(status=S.CLEARED, cleared_date__gte=start, cleared_date__lt=_next_month(start))
        )
        months.append({"label": start.strftime("%b %Y"), "total": total})
    biggest = max((m["total"] for m in months), default=ZERO)
    for m in months:
        m["percent"] = int(m["total"] * 100 / biggest) if biggest else 0

    owing = list(
        rent.filter(status__in=[S.PENDING, S.RECEIVED, S.RETURNED], cheque_date__lt=today)
        .values(
            "lease__tenant__name",
            "lease__unit__unit_number",
            "lease__unit__property__name",
        )
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")[:8]
    )

    # --- units -------------------------------------------------------------
    counts = {"vacant": 0, "occupied": 0, "reserved": 0, "maintenance": 0}
    for status in Unit.objects.with_status().values_list("status_calc", flat=True):
        counts[status] = counts.get(status, 0) + 1
    total_units = sum(counts.values())
    occupancy = int(counts["occupied"] * 100 / total_units) if total_units else 0

    # --- leases ------------------------------------------------------------
    active = Lease.objects.filter(status=Lease.Status.ACTIVE)
    leases_30 = active.filter(end_date__gte=today, end_date__lte=today + timedelta(days=30)).count()
    leases_90 = active.filter(end_date__gte=today, end_date__lte=today + timedelta(days=90)).count()
    leases_overdue = active.filter(end_date__lt=today).count()

    # --- documents (Emirates ID, passport, visa, licence) -----------------
    expired = expiring = 0
    for model in (Owner, Tenant):
        for party in model.objects.filter(is_active=True):
            state = party.compliance_state
            if state in ("expired", "missing"):
                expired += 1
            elif state == "expiring":
                expiring += 1

    # --- statements, expenses, maintenance --------------------------------
    drafts = OwnerStatement.objects.filter(status=OwnerStatement.Status.DRAFT).count()
    approved = list(OwnerStatement.objects.filter(status=OwnerStatement.Status.APPROVED))
    payouts = [s.net_payout for s in approved]
    payable_count = len([p for p in payouts if p > 0])
    payable_total = sum((p for p in payouts if p > 0), ZERO)
    waiting_expenses = Expense.objects.filter(
        bearer=Expense.Bearer.OWNER, statement_line__isnull=True
    ).count()
    open_jobs = MaintenanceRequest.objects.filter(
        status__in=[MaintenanceRequest.Status.OPEN, MaintenanceRequest.Status.IN_PROGRESS]
    )
    urgent_jobs = open_jobs.filter(
        priority__in=[MaintenanceRequest.Priority.URGENT, MaintenanceRequest.Priority.EMERGENCY]
    ).count()

    return {
        "dash": {
            "today": today,
            "month_label": today.strftime("%B %Y"),
            "cheque_tiles": cheque_tiles,
            "desk_url": reverse("admin:realestate_cheque_desk"),
            "expected": expected,
            "collected": collected,
            "still_to_come": max(expected - collected, ZERO),
            "overdue_total": overdue_total,
            "months": months,
            "owing": owing,
            "units": counts,
            "total_units": total_units,
            "occupancy": occupancy,
            "vacant_url": _url("realestate_unit_changelist", unit_status="vacant"),
            "leases_30": leases_30,
            "leases_90": leases_90,
            "leases_overdue": leases_overdue,
            "leases_30_url": _url("realestate_lease_changelist", expiry="30"),
            "leases_90_url": _url("realestate_lease_changelist", expiry="90"),
            "leases_overdue_url": _url("realestate_lease_changelist", expiry="overdue"),
            "docs_expired": expired,
            "docs_expiring": expiring,
            "tenants_expired_url": _url("realestate_tenant_changelist", docs="expired"),
            "tenants_expiring_url": _url("realestate_tenant_changelist", docs="60"),
            "owners_expired_url": _url("realestate_owner_changelist", docs="expired"),
            "owners_expiring_url": _url("realestate_owner_changelist", docs="60"),
            "drafts": drafts,
            "drafts_url": _url("realestate_ownerstatement_changelist", status__exact="draft"),
            "payable_count": payable_count,
            "payable_total": payable_total,
            "payable_url": _url("realestate_ownerstatement_changelist", status__exact="approved"),
            "waiting_expenses": waiting_expenses,
            "waiting_expenses_url": _url("realestate_expense_changelist", statement="waiting"),
            "open_jobs": open_jobs.count(),
            "urgent_jobs": urgent_jobs,
            "jobs_url": _url("realestate_maintenancerequest_changelist", status__exact="open"),
        }
    }
