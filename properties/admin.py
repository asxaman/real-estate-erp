from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import Owner, Property, Unit


class UnitInline(TabularInline):
    model = Unit
    extra = 0
    fields = (
        "unit_number",
        "floor",
        "unit_type",
        "bedrooms",
        "bathrooms",
        "size_sqft",
        "asking_annual_rent",
        "status",
    )
    show_change_link = True


@admin.register(Owner)
class OwnerAdmin(ModelAdmin):
    list_display = ("name", "owner_type", "phone", "email", "property_count", "is_active")
    list_filter = ("owner_type", "is_active")
    search_fields = ("name", "email", "phone", "id_number")
    fieldsets = (
        (_("Basic details"), {"fields": ("name", "owner_type", "is_active")}),
        (_("Contact"), {"fields": ("email", "phone", "address")}),
        (_("Identification"), {"fields": ("id_number", "nationality")}),
        (_("Bank details (for owner payouts)"), {"fields": ("bank_name", "iban")}),
        (_("Notes"), {"fields": ("notes",)}),
    )

    @display(description=_("Properties"))
    def property_count(self, obj):
        return obj.properties.count()


@admin.register(Property)
class PropertyAdmin(ModelAdmin):
    list_display = (
        "code",
        "name",
        "property_type",
        "owner",
        "emirate",
        "area",
        "unit_count",
        "management_fee_percent",
        "status_label",
    )
    list_filter = ("emirate", "property_type", "status")
    search_fields = ("code", "name", "area", "owner__name", "title_deed_number")
    autocomplete_fields = ("owner",)
    inlines = [UnitInline]
    fieldsets = (
        (_("Basic details"), {"fields": ("code", "name", "property_type", "owner", "status")}),
        (_("Location"), {"fields": ("emirate", "area", "address", "plot_number")}),
        (
            _("Building information"),
            {"fields": ("title_deed_number", "year_built", "number_of_floors")},
        ),
        (_("Management"), {"fields": ("management_fee_percent",)}),
        (_("Notes"), {"fields": ("notes",)}),
    )

    @display(description=_("Units"))
    def unit_count(self, obj):
        return obj.units.count()

    @display(
        description=_("Status"),
        ordering="status",
        label={"Active": "success", "Inactive": "danger"},
    )
    def status_label(self, obj):
        return obj.get_status_display()


@admin.register(Unit)
class UnitAdmin(ModelAdmin):
    list_display = (
        "unit_number",
        "property",
        "unit_type",
        "floor",
        "bedrooms",
        "size_sqft",
        "asking_annual_rent",
        "status_label",
    )
    list_filter = ("status", "unit_type", "property", "furnished")
    search_fields = ("unit_number", "property__name", "property__code")
    autocomplete_fields = ("property",)
    list_select_related = ("property",)

    @display(
        description=_("Status"),
        ordering="status",
        label={
            "Vacant": "info",
            "Occupied": "success",
            "Under Maintenance": "warning",
        },
    )
    def status_label(self, obj):
        return obj.get_status_display()
