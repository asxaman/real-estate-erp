"""The 'Leases' tab shown on the Tenant, Unit and Property pages (read-only; open a lease to change it)."""
from django.contrib import admin
from django.utils import timezone

from core.admin_utils import badge, format_amount
from core.formats import fmt_date
from properties.owner_admin import ReadOnlyInline

from .models import Lease


class LeasesInline(ReadOnlyInline):
    model = Lease
    tab = True
    verbose_name_plural = "Leases"
    show_change_link = True
    fields = ("code", "tenant_name", "rented", "period", "rent", "state")
    readonly_fields = fields
    ordering = ("-start_date",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("tenant", "unit__property", "property")

    @admin.display(description="Lease no.")
    def code(self, obj):
        return obj.code

    @admin.display(description="Tenant")
    def tenant_name(self, obj):
        return obj.tenant.name

    @admin.display(description="Rented")
    def rented(self, obj):
        return obj.item_name

    @admin.display(description="Period")
    def period(self, obj):
        return f"{fmt_date(obj.start_date)} → {fmt_date(obj.end_date)}"

    @admin.display(description="Annual rent")
    def rent(self, obj):
        return "AED " + format_amount(obj.annual_rent)

    @admin.display(description="State")
    def state(self, obj):
        state = obj.state(timezone.localdate())
        text, tone = {
            "active": ("Active", "success"), "upcoming": ("Not started", "info"),
            "expired": ("Expired", "neutral"), "terminated": ("Terminated", "danger"),
        }[state]
        if state == "active" and obj.ends_soon():
            text, tone = "Ending soon", "warning"
        return badge(text, tone)


class TenantLeasesInline(LeasesInline):
    fk_name = "tenant"


class UnitLeasesInline(LeasesInline):
    fk_name = "unit"


class PropertyLeasesInline(LeasesInline):
    fk_name = "property"
