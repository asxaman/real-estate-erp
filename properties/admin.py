from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from core.admin_utils import TooltipHelpMixin

from .models import Owner, Property, Unit


class UnitInline(TabularInline):
    model = Unit
    extra = 0
    fields = ("unit_number", "floor", "unit_type", "bedrooms", "status", "asking_rent")
    show_change_link = True
    ordering = ("floor", "unit_number")


@admin.register(Owner)
class OwnerAdmin(TooltipHelpMixin, ModelAdmin):
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

    def get_readonly_fields(self, request, obj=None):
        # The built-in "Company Owned" owner cannot be renamed or deactivated.
        if obj is not None and obj.is_company:
            return ("name", "is_active")
        return ()

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_company:
            return False
        return super().has_delete_permission(request, obj)

    @admin.display(description="Properties", ordering="_property_count")
    def property_count(self, obj):
        return obj._property_count


class PropertyAdminForm(forms.ModelForm):
    """Same fields as before - only friendlier: placeholders and shorter text boxes."""

    class Meta:
        model = Property
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hints = {
            "name": "e.g. Reem Marina Tower",
            "code": "e.g. RMT",
            "year_built": "e.g. 2018",
            "total_floors": "e.g. 10",
            "annual_rent": "e.g. 85000",
            "management_fee_percent": "e.g. 5",
            "management_fee_fixed": "e.g. 6000",
            "map_link": "https://maps.google.com/...",
            "address": "Street, area, city",
            "notes": "Anything worth remembering about this property",
        }
        for field_name, text in hints.items():
            if field_name in self.fields:
                self.fields[field_name].widget.attrs["placeholder"] = text
        if "owner" in self.fields:
            # The page script uses this to know which owner means "Company Owned".
            self.fields["owner"].widget.attrs["data-company-owner-id"] = str(Owner.company().pk)
        if "code" in self.fields:
            # Codes are always stored in capitals; show them that way while typing.
            self.fields["code"].widget.attrs.update(
                {"style": "text-transform: uppercase;", "autocomplete": "off"}
            )
        for field_name in ("address", "notes"):
            if field_name in self.fields:
                self.fields[field_name].widget.attrs["rows"] = 3


@admin.register(Property)
class PropertyAdmin(TooltipHelpMixin, ModelAdmin):
    form = PropertyAdminForm
    compressed_fields = True  # tighter spacing between fields
    warn_unsaved_form = True  # warns before leaving the page with unsaved changes
    autocomplete_fields = ["owner"]  # searchable owner dropdown
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
            "Basic information",
            {
                "fields": (
                    ("name", "code"),
                    ("property_type", "owner"),
                    ("emirate", "is_active"),
                ),
                "description": "Residential and Commercial Buildings have units - after "
                "saving a building you will land on a page where you can add them.",
            },
        ),
        (
            "Details",
            {
                # Some of these rows appear or disappear depending on the property type
                # and owner (see core/static/core/erp.js).
                "fields": (
                    "address",
                    "map_link",
                    ("year_built", "total_floors"),
                    ("annual_rent", "status"),
                    ("management_fee_type", "management_fee_percent", "management_fee_fixed"),
                ),
            },
        ),
        ("Notes", {"fields": ("notes",)}),
    )

    def response_add(self, request, obj, post_url_continue=None):
        # After adding a building, stay on the page so its units can be added right away.
        if (
            obj.has_units
            and "_popup" not in request.POST
            and "_addanother" not in request.POST
            and "_continue" not in request.POST
        ):
            post = request.POST.copy()
            post["_continue"] = "1"
            request.POST = post
            messages.info(request, "Now add this building's units in the section below.")
        return super().response_add(request, obj, post_url_continue)

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
class UnitAdmin(TooltipHelpMixin, ModelAdmin):
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
