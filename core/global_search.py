"""Record search for the Ctrl+K box (Unfold's command palette): finds tenants, units, properties, owners, leases and payments."""
from django.contrib import admin
from django.urls import reverse
from unfold.dataclasses import SearchResult

from .formats import fmt_date

PER_KIND = 5


def _sources():
    from leases.models import Lease, LeasePayment
    from properties.models import Owner, Property, Unit
    from tenants.models import Tenant

    return [
        (Tenant, "person", "Tenant", (), lambda o: o.name, lambda o: f"{o.code or ''} · {o.phone}".strip(" ·")),
        (Unit, "meeting_room", "Unit", ("property",), lambda o: str(o),
         lambda o: f"{o.property.name} · {o.get_unit_type_display()} · {o.get_status_display()}"),
        (Property, "apartment", "Property", ("owner",), lambda o: o.name, lambda o: f"{o.code} · {o.owner.name}"),
        (Owner, "account_circle", "Owner", (), lambda o: o.name, lambda o: f"{o.code or ''} · {o.phone}".strip(" ·")),
        (Lease, "description", "Lease", ("tenant", "unit__property", "property"), lambda o: f"{o.code} · {o.tenant.name}",
         lambda o: f"{o.item_name} · {o.get_status_display()}"),
        (LeasePayment, "payments", "Payment", ("lease__tenant",), lambda o: f"{o.lease.code} {o.label.lower()} · {o.lease.tenant.name}",
         lambda o: f"Due {fmt_date(o.due_date)} · {o.amount:,.0f} AED · {o.get_status_display()}"),
    ]


def search_callback(request, search_term):
    term = (search_term or "").strip()
    if len(term) < 2 or not getattr(request.user, "is_staff", False):
        return []
    results = []
    for model, icon, label, related, title, describe in _sources():
        model_admin = admin.site._registry.get(model)
        if model_admin is None or not model_admin.has_view_permission(request):
            continue
        queryset = model._default_manager.all()
        if related:
            queryset = queryset.select_related(*related)
        queryset, _ = model_admin.get_search_results(request, queryset, term)
        opts = model._meta
        for obj in queryset[:PER_KIND]:
            results.append(SearchResult(
                title=title(obj), description=f"{label} · {describe(obj)}",
                link=reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk]), icon=icon,
            ))
    return results
