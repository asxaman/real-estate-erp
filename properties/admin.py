from django.contrib import admin
from django.db.models import Count, Q
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import Owner, Property, Unit


class UnitInline(TabularInline):
    model = Unit
    extra = 0
    fields = ("unit_number", "floor", "unit_type", "bedrooms", "status", "asking_rent")
    show_change_link = True
    ordering = ("floor", "unit_number")


@admin.register(Owner)
class OwnerAdmin(ModelAdmin):
    list_display = ("name", "email", "phone", "property_count", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "email", "phone")
    fieldsets = (
        (None, {"fields": ("name", "is_active")}),
        ("Contact", {"fields": ("email", "phone", "address")}),
        ("Portal access", {"fields": ("user",)}),
        ("Other", {"fields": ("notes",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_property_count=Count("properties"))

    @admin.display(description="Properties", ordering="_property_count")
    def property_count(self, obj):
        return obj._property_count


@admin.register(Property)
class PropertyAdmin(ModelAdmin):
    list_display = (
        "name",
        "code",
        "property_type",
        "emirate",
        "owner",
        "unit_count",
        "vacant_count",
        "is_active",
    )
    list_filter = ("property_type", "emirate", "is_active", "owner")
    search_fields = ("name", "code", "owner__name")
    list_select_related = ("owner",)
    inlines = [UnitInline]
    fieldsets = (
        (
            None,
            {
                "fields": ("name", "code", "property_type", "owner", "is_active"),
                "description": "Units can be added after saving, and only for "
                "Residential and Commercial Buildings.",
            },
        ),
        ("Location", {"fields": ("emirate", "address")}),
        ("Building details", {"fields": ("year_built", "total_floors", "management_fee_percent")}),
        ("Other", {"fields": ("notes",)}),
    )

    def get_inline_instances(self, request, obj=None):
        # Units are shown only for saved properties that are buildings.
        if obj is None or not obj.has_units:
            return []
        return super().get_inline_instances(request, obj)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _unit_count=Count("units"),
            _vacant_count=Count("units", filter=Q(units__status=Unit.Status.VACANT)),
        )

    @admin.display(description="Units", ordering="_unit_count")
    def unit_count(self, obj):
        return obj._unit_count if obj.has_units else "-"

    @admin.display(description="Vacant", ordering="_vacant_count")
    def vacant_count(self, obj):
        return obj._vacant_count if obj.has_units else "-"


@admin.register(Unit)
class UnitAdmin(ModelAdmin):
    list_display = (
        "reference",
        "property",
        "floor",
        "unit_type",
        "area_sqft",
        "asking_rent",
        "show_status",
    )
    list_filter = ("status", "unit_type", "property", "furnished")
    search_fields = ("unit_number", "property__name", "property__code")
    list_select_related = ("property",)
    fieldsets = (
        (None, {"fields": ("property", "unit_number", "floor", "status")}),
        ("Details", {"fields": ("unit_type", "bedrooms", "bathrooms", "area_sqft", "furnished")}),
        ("Rent", {"fields": ("asking_rent",)}),
        ("Other", {"fields": ("notes",)}),
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Only buildings can contain units.
        if db_field.name == "property":
            kwargs["queryset"] = Property.objects.filter(
                property_type__in=Property.BUILDING_TYPES
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Unit", ordering="unit_number")
    def reference(self, obj):
        return str(obj)

    @display(
        description="Status",
        ordering="status",
        label={
            Unit.Status.VACANT.label: "info",
            Unit.Status.OCCUPIED.label: "success",
            Unit.Status.RESERVED.label: "warning",
            Unit.Status.MAINTENANCE.label: "danger",
        },
    )
    def show_status(self, obj):
        return obj.get_status_display()
