from datetime import timedelta

from django.contrib import admin
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html

from .models import Lease, Owner, Property, Tenant, Unit

admin.site.site_header = "Real Estate ERP"
admin.site.site_title = "Real Estate ERP"
admin.site.index_title = "Dashboard"

STATUS_COLORS = {
    "vacant": "#2e7d32",
    "reserved": "#ef6c00",
    "occupied": "#1565c0",
    "maintenance": "#8d6e63",
}


def status_badge(status):
    """Show a unit status as coloured text."""
    status = Unit.Status(status)
    return format_html(
        '<strong style="color:{};">{}</strong>',
        STATUS_COLORS.get(status.value, "inherit"),
        status.label,
    )


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "management_fee_percent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "email", "phone", "iban")
    fieldsets = (
        ("Owner details", {"fields": ("name", "email", "phone", "address")}),
        (
            "Banking & management fee",
            {"fields": ("bank_name", "iban", "management_fee_percent")},
        ),
        ("Notes & status", {"fields": ("notes", "is_active")}),
    )


class UnitInline(admin.TabularInline):
    """Lets you add and edit units directly on the property page."""

    model = Unit
    extra = 0
    fields = (
        "unit_number",
        "unit_type",
        "floor",
        "bedrooms",
        "rent_amount",
        "under_maintenance",
        "current_status",
    )
    readonly_fields = ("current_status",)

    @admin.display(description="Status")
    def current_status(self, obj):
        if obj is None or not obj.pk:
            return "-"
        return status_badge(obj.get_status())


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "ownership_type",
        "owner",
        "our_fee",
        "unit_count",
        "city",
        "is_active",
    )
    ordering = ("name",)
    list_filter = ("ownership_type", "is_active", "property_type")
    list_select_related = ("owner",)
    search_fields = ("name", "city", "owner__name")
    autocomplete_fields = ("owner",)
    inlines = [UnitInline]
    fieldsets = (
        (
            "Property details",
            {"fields": ("name", "property_type", "city", "address")},
        ),
        (
            "Ownership & fee",
            {
                "description": "Third-party properties belong to a landlord and earn us a management fee.",
                "fields": ("ownership_type", "owner", "management_fee_percent"),
            },
        ),
        ("Notes & status", {"fields": ("notes", "is_active")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_unit_count=Count("units"))

    @admin.display(description="Our fee")
    def our_fee(self, obj):
        value = obj.effective_fee_percent
        return "-" if value is None else f"{value}%"

    @admin.display(description="Units", ordering="_unit_count")
    def unit_count(self, obj):
        return obj._unit_count


class UnitStatusFilter(admin.SimpleListFilter):
    title = "status"
    parameter_name = "unit_status"

    def lookups(self, request, model_admin):
        return Unit.Status.choices

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status_calc=self.value())
        return queryset


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("property", "unit_number", "unit_type", "annual_rent", "unit_status")
    list_filter = (UnitStatusFilter, "unit_type", "property")
    search_fields = ("unit_number", "property__name")
    autocomplete_fields = ("property",)
    readonly_fields = ("current_status",)
    fieldsets = (
        (
            "Unit details",
            {
                "fields": (
                    "property",
                    "unit_number",
                    "unit_type",
                    "floor",
                    "bedrooms",
                    "size_sqft",
                )
            },
        ),
        (
            "Rent & status",
            {
                "description": "Occupied, Reserved and Vacant are worked out automatically from the unit's leases.",
                "fields": ("rent_amount", "current_status", "under_maintenance", "notes"),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).with_status().select_related("property")

    @admin.display(description="Annual rent (AED)", ordering="rent_amount")
    def annual_rent(self, obj):
        return f"{obj.rent_amount:,.2f}"

    @admin.display(description="Status", ordering="status_calc")
    def unit_status(self, obj):
        return status_badge(obj.status_calc)

    @admin.display(description="Current status")
    def current_status(self, obj):
        if obj is None or not obj.pk:
            return "-"
        return status_badge(obj.get_status())


class TenantLeaseInline(admin.TabularInline):
    """Shows a tenant's leases on the tenant page."""

    model = Lease
    extra = 0
    fields = ("unit", "start_date", "end_date", "annual_rent", "status")
    autocomplete_fields = ("unit",)
    show_change_link = True


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "tenant_type",
        "phone",
        "email",
        "active_leases",
        "is_active",
    )
    ordering = ("name",)
    list_filter = ("tenant_type", "is_active")
    search_fields = ("name", "email", "phone", "id_number")
    inlines = [TenantLeaseInline]
    fieldsets = (
        (
            "Tenant details",
            {"fields": ("name", "tenant_type", "email", "phone", "nationality")},
        ),
        ("Identification & address", {"fields": ("id_number", "address")}),
        ("Notes & status", {"fields": ("notes", "is_active")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _active_leases=Count("leases", filter=Q(leases__status=Lease.Status.ACTIVE))
        )

    @admin.display(description="Active leases", ordering="_active_leases")
    def active_leases(self, obj):
        return obj._active_leases


class ExpiryFilter(admin.SimpleListFilter):
    title = "expiry"
    parameter_name = "expiry"

    def lookups(self, request, model_admin):
        return [
            ("30", "Expiring within 30 days"),
            ("60", "Expiring within 60 days"),
            ("90", "Expiring within 90 days"),
            ("overdue", "Past end date but still active"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        today = timezone.localdate()
        if value == "overdue":
            return queryset.filter(status=Lease.Status.ACTIVE, end_date__lt=today)
        if value in ("30", "60", "90"):
            return queryset.filter(
                status=Lease.Status.ACTIVE,
                end_date__gte=today,
                end_date__lte=today + timedelta(days=int(value)),
            )
        return queryset


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = (
        "unit",
        "tenant",
        "start_date",
        "end_date",
        "rent",
        "days_left",
        "status",
    )
    list_filter = ("status", ExpiryFilter, "unit__property")
    list_select_related = ("tenant", "unit", "unit__property")
    search_fields = (
        "tenant__name",
        "unit__unit_number",
        "unit__property__name",
        "ejari_number",
    )
    autocomplete_fields = ("tenant", "unit")
    actions = ["mark_expired"]
    fieldsets = (
        ("Who and what", {"fields": ("tenant", "unit", "ejari_number")}),
        ("Period", {"fields": ("start_date", "end_date")}),
        (
            "Rent",
            {"fields": ("annual_rent", "number_of_payments", "security_deposit")},
        ),
        ("Status & notes", {"fields": ("status", "notes")}),
    )

    @admin.display(description="Annual rent (AED)", ordering="annual_rent")
    def rent(self, obj):
        return f"{obj.annual_rent:,.2f}"

    @admin.display(description="Days left")
    def days_left(self, obj):
        if obj.status != Lease.Status.ACTIVE:
            return "-"
        days = (obj.end_date - timezone.localdate()).days
        return "Overdue" if days < 0 else days

    @admin.action(description="Mark selected active leases as expired")
    def mark_expired(self, request, queryset):
        updated = queryset.filter(status=Lease.Status.ACTIVE).update(
            status=Lease.Status.EXPIRED
        )
        self.message_user(request, f"{updated} lease(s) marked as expired.")
