from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from core.admin_utils import TooltipHelpMixin, format_amount

from . import export_tools, import_tools
from .import_admin import ImportMixin
from .models import Owner, Property, Unit
from .models_constants import normalise_unit_number


class UnitBaseForm(forms.ModelForm):
    """Shared by the Unit page and the units table on the Property page."""

    class Meta:
        model = Unit
        fields = "__all__"

    def clean_unit_number(self):
        # " g01 " and "G01" are the same unit.
        return normalise_unit_number(self.cleaned_data["unit_number"])

    def clean_floor(self):
        return self.cleaned_data["floor"].strip().upper()


class UnitInlineForm(UnitBaseForm):
    """Units table on the Property page: no long help texts in the column headers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.help_text = ""  # the full Unit page still shows them as "i" tooltips
        hints = {"unit_number": "e.g. 304", "floor": "3, G, B1"}
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        if "floor" in self.fields:
            self.fields["floor"].widget.attrs.update(
                {"style": "text-transform: uppercase;", "maxlength": 4}
            )


class UnitInline(TabularInline):
    model = Unit
    form = UnitInlineForm
    extra = 0
    fields = ("unit_number", "floor", "unit_type", "bathrooms", "status", "asking_rent")
    show_change_link = True
    ordering = ("floor_sort", "unit_number")


from . import owner_admin  # noqa: E402,F401  (registers the Owner pages)


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
            "bathrooms": "1 to 10",
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
        if "code" in self.fields:
            self.fields["code"].widget.attrs["maxlength"] = 6  # the browser stops typing at 6
        if "bathrooms" in self.fields:
            self.fields["bathrooms"].widget.attrs.update({"min": 1, "max": 10})
        for field_name in ("address", "notes"):
            if field_name in self.fields:
                self.fields[field_name].widget.attrs["rows"] = 3

    def clean_code(self):
        # Always stored in capitals, so "rmt" and "RMT" are the same code.
        return self.cleaned_data["code"].strip().upper()


@admin.register(Property)
class PropertyAdmin(ImportMixin, TooltipHelpMixin, ModelAdmin):
    form = PropertyAdminForm
    actions = ["export_excel", "export_csv"]
    actions_list = ["import_button"]
    import_noun = "properties"
    import_headers = import_tools.PROPERTY_HEADERS
    import_notes = [
        "Required: Code, Name, Type.",
        "Owner is the exact owner name (blank = Company Owned).",
        "Fee type (Percent or Fixed) and the fee are needed for properties with another owner.",
        "See the \"Allowed values\" sheet in the template for Type, Emirate and Status.",
    ]

    def import_parse(self, upload):
        return import_tools.parse_property_file(upload)

    def import_template(self):
        return import_tools.property_template_response()

    @admin.action(description="Export selected properties to Excel")
    def export_excel(self, request, queryset):
        return export_tools.properties_excel(queryset)

    @admin.action(description="Export selected properties to CSV")
    def export_csv(self, request, queryset):
        return export_tools.properties_csv(queryset)

    compressed_fields = True  # tighter spacing between fields
    # The unsaved-changes warning is our own pop-up (core/static/core/erp.js).
    autocomplete_fields = ["owner"]  # searchable owner dropdown
    list_display = (
        "name",
        "code",
        "property_type",
        "emirate",
        "owner",
        "unit_count",
        "vacant_count",
        "rent_aed",
        "is_active",
    )
    list_filter = ("property_type", "emirate", "is_active", "owner")
    search_fields = ("name", "code", "owner__name")
    list_select_related = ("owner",)
    inlines = [UnitInline]

    def get_changeform_initial_data(self, request):
        # A new property starts with "Company Owned" selected (change it if someone else owns it).
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("owner", Owner.company().pk)
        return initial
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
        # The sections below marked "conditional" appear or disappear depending on the
        # property type and owner (see core/static/core/erp.js).
        (
            "Details",
            {"fields": ("address", "map_link", ("year_built", "total_floors"))},
        ),
        ("Apartment details", {"fields": (("apartment_type", "bathrooms"),)}),  # conditional
        ("Rental", {"fields": (("annual_rent", "status"),)}),  # conditional
        (
            "Management fee",  # conditional
            {"fields": (("management_fee_type", "management_fee_percent", "management_fee_fixed"),)},
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

    @admin.display(description="Annual rent (AED)", ordering="annual_rent")
    def rent_aed(self, obj):
        return format_amount(obj.annual_rent) if not obj.has_units else "-"


class UnitAdminForm(UnitBaseForm):
    """Unit form: placeholders, capital letters for the floor code, active buildings only."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hints = {
            "unit_number": "e.g. 304",
            "floor": "e.g. 3, G, B1",
            "bathrooms": "1 to 10",
            "area_sqft": "e.g. 1200",
            "asking_rent": "e.g. 95000",
            "notes": "Anything worth remembering about this unit",
        }
        for field_name, text in hints.items():
            if field_name in self.fields:
                self.fields[field_name].widget.attrs["placeholder"] = text
        if "floor" in self.fields:
            self.fields["floor"].widget.attrs.update(
                {"style": "text-transform: uppercase;", "autocomplete": "off", "maxlength": 4}
            )
        if "bathrooms" in self.fields:
            self.fields["bathrooms"].widget.attrs.update({"min": 1, "max": 10})
        if "notes" in self.fields:
            self.fields["notes"].widget.attrs["rows"] = 3
        if "property" in self.fields:
            # Buildings only, and inactive ones only if this unit already belongs to one.
            self.fields["property"].queryset = Property.objects.filter(
                property_type__in=Property.BUILDING_TYPES
            ).filter(Q(is_active=True) | Q(pk=self.instance.property_id))


class BuildingFilter(admin.RelatedFieldListFilter):
    """Property filter on the Units list: buildings only (standalone properties have no units)."""

    def field_choices(self, field, request, model_admin):
        buildings = Property.objects.filter(property_type__in=Property.BUILDING_TYPES)
        return [(p.pk, str(p)) for p in buildings]


@admin.register(Unit)
class UnitAdmin(ImportMixin, TooltipHelpMixin, ModelAdmin):
    actions = ["export_excel", "export_csv"]
    actions_list = ["import_button"]
    import_noun = "units"
    import_headers = import_tools.UNIT_HEADERS
    import_notes = [
        "Required: Property code, Unit number, Type. The property must already exist and be a building.",
        "Type must fit the building (residential: Studio, 1BHK-5BHK; commercial: Office, Shop).",
        "See the \"Allowed values\" sheet in the template.",
    ]

    def import_parse(self, upload):
        return import_tools.parse_unit_file(upload)

    def import_template(self):
        return import_tools.unit_template_response()

    @admin.action(description="Export selected units to Excel")
    def export_excel(self, request, queryset):
        return export_tools.units_excel(queryset)

    @admin.action(description="Export selected units to CSV")
    def export_csv(self, request, queryset):
        return export_tools.units_csv(queryset)

    form = UnitAdminForm
    compressed_fields = True
    list_display = (
        "reference",
        "property",
        "floor",
        "unit_type",
        "bathrooms",
        "area",
        "rent_aed",
        "show_status",
    )
    list_filter = ("status", "unit_type", ("property", BuildingFilter), "furnished")
    search_fields = ("unit_number", "property__name", "property__code")
    list_select_related = ("property",)
    fieldsets = (
        (
            "Unit",
            {
                "fields": (
                    ("property", "unit_number"),
                    ("floor", "unit_type"),
                    ("status", "furnished"),
                ),
            },
        ),
        ("Details", {"fields": (("bathrooms", "area_sqft", "asking_rent"),)}),
        ("Notes", {"fields": ("notes",)}),
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

    @admin.display(description="Area (sq ft)", ordering="area_sqft")
    def area(self, obj):
        return format_amount(obj.area_sqft)

    @admin.display(description="Asking rent (AED / year)", ordering="asking_rent")
    def rent_aed(self, obj):
        return format_amount(obj.asking_rent)

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
        return obj.get_status_display() or "-"
