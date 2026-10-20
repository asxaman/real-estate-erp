from django.contrib import admin
from django.db.models import Count

from .models import Owner, Property, Unit

admin.site.site_header = "Real Estate ERP"
admin.site.site_title = "Real Estate ERP"
admin.site.index_title = "Dashboard"


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
    fields = ("unit_number", "unit_type", "floor", "bedrooms", "rent_amount", "status")


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


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("property", "unit_number", "unit_type", "annual_rent", "status")
    list_filter = ("status", "unit_type", "property")
    list_select_related = ("property",)
    search_fields = ("unit_number", "property__name")
    autocomplete_fields = ("property",)
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
        ("Rent & status", {"fields": ("rent_amount", "status", "notes")}),
    )

    @admin.display(description="Annual rent (AED)", ordering="rent_amount")
    def annual_rent(self, obj):
        return f"{obj.rent_amount:,.2f}"
