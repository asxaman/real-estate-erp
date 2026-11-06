"""The home Dashboard and the Expiring documents page."""
from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from . import exports, sysconf
from .formats import fmt_date
from .models import AuditEntry

# (label, model path, parent field, admin change url name, permission)
SOURCES = (
    ("Owner", "properties.OwnerDocument", "owner", "admin:properties_owner_change", "properties.view_ownerdocument"),
    ("Tenant", "tenants.TenantDocument", "tenant", "admin:tenants_tenant_change", "tenants.view_tenantdocument"),
    ("Property", "properties.PropertyDocument", "property", "admin:properties_property_change",
     "properties.view_propertydocument"),
    ("Unit", "properties.UnitDocument", "unit", "admin:properties_unit_change", "properties.view_unitdocument"),
    ("Lease", "leases.LeaseDocument", "lease", "admin:leases_lease_change", "leases.view_leasedocument"),
)
KINDS = {label.lower(): label for label, *_ in SOURCES}
KINDS["lease"] = "Lease"  # lease documents, and the dates leases and Ejari certificates end
WINDOWS = (30, 60, 90, 180)


def collect_documents(request, window=None, kind="", state="attention"):
    """
    Documents with an expiry date, soonest first, from every record type the user may see.
    state: "attention" (expired + due within the window), "expired", "soon", "all".
    """
    from django.apps import apps

    today = timezone.localdate()
    window = window or sysconf.expiry_days()
    limit = today + timedelta(days=window)
    rows = []
    for label, model_path, parent, url_name, permission in SOURCES:
        if kind and kind != label.lower():
            continue
        if not request.user.has_perm(permission):
            continue
        related = {"unit": "unit__property", "lease": "lease__tenant"}.get(parent, parent)  # names include their parents
        queryset = apps.get_model(model_path).objects.filter(expiry_date__isnull=False).select_related(related)
        if state == "expired":
            queryset = queryset.filter(expiry_date__lt=today)
        elif state == "soon":
            queryset = queryset.filter(expiry_date__gte=today, expiry_date__lte=limit)
        elif state == "attention":
            queryset = queryset.filter(expiry_date__lte=limit)
        for doc in queryset:
            owner = getattr(doc, parent)
            days = (doc.expiry_date - today).days
            rows.append(
                {
                    "kind": label,
                    "who": f"{owner} · {owner.tenant.name}" if parent == "lease" else str(owner),
                    "url": reverse(url_name, args=[owner.pk]),
                    "document": doc.get_doc_type_display(),
                    "number": doc.number,
                    "expiry": doc.expiry_date,
                    "days": days,
                    "state": "expired" if days < 0 else ("soon" if doc.expiry_date <= limit else "valid"),
                    "file": doc.file.url if doc.file else "",
                }
            )
    if (not kind or kind == "lease") and request.user.has_perm("leases.view_lease"):
        rows.extend(_lease_rows(today, limit, state))
    rows.sort(key=lambda r: (r["expiry"], r["kind"], r["who"]))
    return rows


def _lease_rows(today, limit, state):
    """The end of each running lease, and each Ejari certificate that expires, as rows of the same shape."""
    from leases.models import Lease

    rows = []
    leases = Lease.objects.filter(status=Lease.Status.ACTIVE, end_date__gte=today - timedelta(days=90)).select_related(
        "tenant", "unit__property", "property"
    ).prefetch_related("renewals")
    for lease in leases:
        who = f"{lease.code} · {lease.tenant.name} · {lease.item_name}"
        url = reverse("admin:leases_lease_change", args=[lease.pk])
        candidates = []
        if lease.start_date <= today and not lease.renewals.all():  # a renewed lease is not a worry
            candidates.append(("Lease ends", "", lease.end_date))
        if lease.ejari_expiry:
            candidates.append(("Ejari certificate", lease.ejari_number, lease.ejari_expiry))
        for document, number, expiry in candidates:
            days = (expiry - today).days
            if state == "expired" and days >= 0:
                continue
            if state == "soon" and not (0 <= days and expiry <= limit):
                continue
            if state == "attention" and expiry > limit:
                continue
            rows.append(
                {
                    "kind": "Lease", "who": who, "url": url, "document": document, "number": number,
                    "expiry": expiry, "days": days,
                    "state": "expired" if days < 0 else ("soon" if expiry <= limit else "valid"), "file": "",
                }
            )
    return rows


def _lease_panels(user, panels):
    """'Next leases to end' (with a Renew button) and 'Needs attention' for the dashboard."""
    from django.db.models import Case, Count, DecimalField, F, Sum, Value, When
    from django.db.models.functions import Coalesce, Round

    from leases.admin import _button, _money, lease_info
    from leases.models import VAT_RATE, Lease, LeasePayment

    today = timezone.localdate()
    limit = today + timedelta(days=sysconf.expiry_days())
    running = Lease.objects.filter(status=Lease.Status.ACTIVE, start_date__lte=today, end_date__gte=today)
    ending = list(
        running.filter(renewals__isnull=True).select_related("tenant", "unit__property", "property")
        .prefetch_related("payments").order_by("end_date", "code")[:5]
    )
    can_renew = user.has_perm("leases.add_lease")
    rows = []
    for lease in ending:
        days = (lease.end_date - today).days
        rows.append({
            "lease": lease, "url": reverse("admin:leases_lease_change", args=[lease.pk]),
            "date_text": fmt_date(lease.end_date), "when": _days_text(days), "soon": lease.end_date <= limit,
            "button": _button("Renew", "renew", reverse("admin:leases_lease_renew", args=[lease.pk]), lease_info(lease, today)) if can_renew else "",
        })
    panels["renewals"] = rows

    items = []
    leases_url = reverse("admin:leases_lease_changelist")
    ending_unrenewed = running.filter(end_date__lte=limit, renewals__isnull=True).count()
    if ending_unrenewed:
        items.append({"label": "Leases ending soon with no renewal", "count": ending_unrenewed, "detail": f"within {sysconf.expiry_days()} days",
                      "url": leases_url + "?state=soon", "tone": "amber"})
    ejari = Lease.objects.filter(status=Lease.Status.ACTIVE, end_date__gte=today, ejari_expiry__isnull=False, ejari_expiry__lte=limit).count()
    if ejari:
        items.append({"label": "Ejari certificates expiring or expired", "count": ejari, "detail": "",
                      "url": reverse("expiring_documents") + "?kind=lease", "tone": "amber"})
    if user.has_perm("leases.view_leasepayment"):
        pay_url = reverse("admin:leases_leasepayment_changelist")
        payments = LeasePayment.objects.all()
        # amount + VAT, worked out by the database (VAT rounded per payment, the same as LeasePayment.total)
        vat = Case(
            When(lease__vat_applies=True, kind=LeasePayment.Kind.RENT, then=Round(F("amount") * Value(VAT_RATE) / 100, 2)),
            When(lease__admin_fee_vat=True, kind=LeasePayment.Kind.OFFICE, then=Round(F("amount") * Value(VAT_RATE) / 100, 2)),
            When(lease__commission_vat=True, kind=LeasePayment.Kind.AGENCY, then=Round(F("amount") * Value(VAT_RATE) / 100, 2)),
            default=Value(0), output_field=DecimalField(max_digits=14, decimal_places=2),
        )

        def add(label, queryset, url, tone):
            totals = queryset.aggregate(
                n=Count("pk"),
                money=Coalesce(Sum(F("amount") + vat, output_field=DecimalField(max_digits=14, decimal_places=2)), Value(0), output_field=DecimalField()),
            )
            if totals["n"]:
                items.append({"label": label, "count": totals["n"], "detail": _money(totals["money"]), "url": url, "tone": tone})

        add("Overdue payments", payments.filter(status__in=(LeasePayment.Status.PENDING, LeasePayment.Status.BOUNCED), due_date__lt=today), pay_url + "?due=overdue", "red")
        add("Bounced cheques", payments.filter(status=LeasePayment.Status.BOUNCED), pay_url + "?status__exact=bounced", "red")
        add("Payments due in the next 7 days",
            payments.filter(status=LeasePayment.Status.PENDING, due_date__gte=today, due_date__lte=today + timedelta(days=7)),
            pay_url + "?due=week", "amber")
    panels["attention"] = items


def _days_text(days):
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} ago"
    if days == 0:
        return "today"
    return f"in {days} day{'s' if days != 1 else ''}"


def expiring_documents_view(request):
    window = request.GET.get("days")
    window = int(window) if window and window.isdigit() and int(window) in WINDOWS else sysconf.expiry_days()
    kind = request.GET.get("kind", "").lower()
    kind = kind if kind in KINDS else ""
    state = request.GET.get("state", "attention")
    state = state if state in ("attention", "expired", "soon", "all") else "attention"
    search = request.GET.get("q", "").strip()

    rows = collect_documents(request, window, kind, state)
    if search:
        needle = search.lower()
        rows = [r for r in rows if needle in f'{r["who"]} {r["document"]} {r["number"]}'.lower()]

    can_export = any(
        request.user.has_perm(p) for p in ("properties.export_owner", "properties.export_property", "tenants.export_tenant")
    )
    if request.GET.get("export") == "csv":
        if not can_export:
            raise PermissionDenied
        return exports.csv_response(
            ["Belongs to", "Record", "Document", "Number", "Expires on", "Status"],
            ([r["kind"], r["who"], r["document"], r["number"], r["expiry"],
              {"expired": "Expired", "soon": "Expiring soon", "valid": "Valid"}[r["state"]]] for r in rows),
            "expiring-documents",
        )

    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    for row in page:
        row["when"] = _days_text(row["days"])
        row["date_text"] = fmt_date(row["expiry"])

    def link(**changes):
        params = {"state": state, "kind": kind, "days": window if window != sysconf.expiry_days() else "", "q": search}
        params.update(changes)
        from urllib.parse import urlencode

        query = urlencode({k: v for k, v in params.items() if v not in ("", None)})
        return "?" + query if query else "?"

    available = [
        label for label, _, _, _, permission in SOURCES
        if request.user.has_perm(permission) or (label == "Lease" and request.user.has_perm("leases.view_lease"))
    ]
    context = {
        **admin.site.each_context(request),
        "title": "Expiring documents",
        "page": page,
        "total": len(rows),
        "state": state,
        "kind": kind,
        "window": window,
        "search": search,
        "states": [
            (key, text, link(state=key, page=""))
            for key, text in (("attention", "Needs attention"), ("expired", "Expired"),
                              ("soon", "Expiring soon"), ("all", "All with an expiry date"))
        ],
        "kinds": [("", "Everyone", link(kind="", page=""))]
        + [(label.lower(), f"{label}s", link(kind=label.lower(), page="")) for label in available],
        "windows": [(days, link(days=days, page="")) for days in WINDOWS],
        "export_url": link(export="csv") if can_export else "",
        "prev_url": link(page=page.previous_page_number()) if page.has_previous() else "",
        "next_url": link(page=page.next_page_number()) if page.has_next() else "",
    }
    return render(request, "core/expiring.html", context)


def _bar(items, total):
    return [
        {"label": label, "count": count, "percent": round(100 * count / total) if total else 0, "tone": tone}
        for label, count, tone in items
    ]


def dashboard_view(request):
    from properties.models import Owner, Property, Unit
    from tenants.models import Tenant

    user = request.user
    cards, panels = [], {}

    can_properties = user.has_perm("properties.view_property")
    can_units = user.has_perm("properties.view_unit")
    standalone = Q(property_type__in=[t for t in Property.PropertyType.values if t not in Property.BUILDING_TYPES])

    unit_total = vacant_units = vacant_standalone = standalone_count = rentable = occupied = 0
    if can_properties or can_units:
        properties = Property.objects.filter(is_active=True)
        units = Unit.objects.filter(property__is_active=True)
        unit_total = units.count()
        vacant_units = units.filter(status=Unit.Status.VACANT).count()
        standalone_count = properties.filter(standalone).count()
        vacant_standalone = properties.filter(standalone, status=Property.Status.VACANT).count()
        rentable = unit_total + standalone_count
        occupied = units.filter(status=Unit.Status.OCCUPIED).count() + properties.filter(
            standalone, status=Property.Status.OCCUPIED
        ).count()
    if can_properties:
        cards.append({"label": "Active properties", "value": Property.objects.filter(is_active=True).count(),
                      "url": reverse("admin:properties_property_changelist") + "?is_active__exact=1", "tone": "blue"})
    if can_units:
        cards.append({"label": "Units", "value": unit_total,
                      "url": reverse("admin:properties_unit_changelist"), "tone": "blue"})
    if can_units or can_properties:
        # vacant units and vacant stand-alone properties (villas, shops ...) are both space waiting for a tenant
        vacant_cards = (vacant_units if can_units else 0) + (vacant_standalone if can_properties else 0)
        cards.append({"label": "Vacant", "value": vacant_cards,
                      "url": reverse("admin:properties_unit_changelist") + "?status__exact=vacant" if can_units else "",
                      "tone": "amber"})
    if can_properties or can_units:
        cards.append({"label": "Occupancy", "value": f"{round(100 * occupied / rentable)}%" if rentable else "-",
                      "url": "", "tone": "green"})
    if user.has_perm("properties.view_owner"):
        cards.append({"label": "Active owners",
                      "value": Owner.objects.filter(is_active=True, is_company=False).count(),
                      "url": reverse("admin:properties_owner_changelist") + "?is_active__exact=1", "tone": "violet"})
    if user.has_perm("tenants.view_tenant"):
        cards.append({"label": "Active tenants", "value": Tenant.objects.filter(is_active=True).count(),
                      "url": reverse("admin:tenants_tenant_changelist") + "?is_active__exact=1", "tone": "violet"})

    if user.has_perm("leases.view_lease"):
        from leases.models import Lease, LeasePayment, running_q

        today_ = timezone.localdate()
        cards.append({"label": "Active leases",
                      "value": Lease.objects.filter(running_q(today_)).count(),
                      "url": reverse("admin:leases_lease_changelist") + "?state=active", "tone": "green"})
    if user.has_perm("leases.view_leasepayment"):
        from leases.models import LeasePayment

        overdue = LeasePayment.objects.filter(
            status__in=(LeasePayment.Status.PENDING, LeasePayment.Status.BOUNCED), due_date__lt=timezone.localdate()
        )
        cards.append({"label": "Overdue payments", "value": overdue.count(),
                      "url": reverse("admin:leases_leasepayment_changelist") + "?due=overdue", "tone": "red"})

    if user.has_perm("leases.view_lease"):
        _lease_panels(user, panels)

    # Documents that need attention
    documents = collect_documents(request, state="attention")
    expired = [d for d in documents if d["state"] == "expired"]
    soon = [d for d in documents if d["state"] == "soon"]
    if any(user.has_perm(s[4]) for s in SOURCES):
        cards.append({"label": "Expired documents", "value": len(expired),
                      "url": reverse("expiring_documents") + "?state=expired", "tone": "red"})
        cards.append({"label": "Expiring soon", "value": len(soon),
                      "url": reverse("expiring_documents") + "?state=soon", "tone": "amber"})
        for d in documents[:6]:
            d["when"] = _days_text(d["days"])
            d["date_text"] = fmt_date(d["expiry"])
        panels["documents"] = documents[:6]
        panels["documents_total"] = len(documents)

    if (can_units and unit_total) or (can_properties and standalone_count):
        by_status = {}
        if can_units:
            by_status = dict(Unit.objects.filter(property__is_active=True).order_by().values_list("status").annotate(c=Count("pk")))
        total_spaces = unit_total if can_units else 0
        if can_properties:
            for status, count in (
                Property.objects.filter(standalone, is_active=True).order_by().values_list("status").annotate(c=Count("pk"))
            ):
                by_status[status] = by_status.get(status, 0) + count
            total_spaces += standalone_count
        panels["status_bars"] = _bar(
            [
                (label, by_status.get(value, 0), tone)
                for (value, label), tone in zip(Unit.Status.choices, ("blue", "green", "amber", "red"))
            ],
            total_spaces,
        )
        vacant = []
        if can_units:
            for unit in (
                Unit.objects.filter(property__is_active=True, status=Unit.Status.VACANT)
                .select_related("property").order_by("property__code", "floor_sort", "unit_number")[:6]
            ):
                vacant.append({
                    "url": reverse("admin:properties_unit_change", args=[unit.pk]), "name": str(unit),
                    "sub": f"{unit.property.name} · {unit.get_unit_type_display()}", "rent": unit.asking_rent,
                })
        if can_properties:
            for prop in (
                Property.objects.filter(standalone, is_active=True, status=Property.Status.VACANT).order_by("code")[:6]
            ):
                vacant.append({
                    "url": reverse("admin:properties_property_change", args=[prop.pk]), "name": f"{prop.code} · {prop.name}",
                    "sub": f"{prop.get_property_type_display()} · whole property", "rent": prop.annual_rent,
                })
        panels["vacant"] = vacant[:6]
        panels["vacant_total"] = (vacant_units if can_units else 0) + (vacant_standalone if can_properties else 0)
        unit_rent = (
            Unit.objects.filter(property__is_active=True, status=Unit.Status.VACANT).aggregate(t=Sum("asking_rent"))["t"]
            if can_units else 0
        ) or 0
        property_rent = (
            Property.objects.filter(standalone, is_active=True, status=Property.Status.VACANT).aggregate(t=Sum("annual_rent"))["t"]
            if can_properties else 0
        ) or 0
        panels["vacant_rent"] = unit_rent + property_rent

    if user.has_perm("tenants.view_tenant"):
        panels["blacklisted"] = Tenant.objects.filter(is_blacklisted=True).count()

    if user.is_superuser:
        panels["activity"] = list(AuditEntry.objects.exclude(action="tool").order_by("-timestamp", "-id")[:8])

    context = {
        **admin.site.each_context(request),
        "title": "Dashboard",
        "cards": cards,
        "panels": panels,
        "today": timezone.localdate(),
        "greeting_name": user.get_short_name() or user.get_username(),
        "dev_mode": user.is_superuser and sysconf.development_mode(),
        "settings_url": reverse("admin:core_systemsettings_changelist") if user.is_superuser else "",
        "expiring_url": reverse("expiring_documents"),
        "units_url": reverse("admin:properties_unit_changelist"),
        "audit_url": reverse("admin:core_auditentry_changelist") if user.is_superuser else "",
    }
    return render(request, "core/dashboard.html", context)
