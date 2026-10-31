from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from core import sysconf
from core.admin_utils import TooltipHelpMixin, badge, circle_avatar, format_amount
from core.widgets import use_file_widget

from . import bulk, export_tools, import_tools
from .import_admin import ImportMixin
from .models import (
    Owner,
    Property,
    PropertyDocument,
    Unit,
    UnitDocument,
    UnitPhoto,
    expiry_counts,
)
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
    tab = True
    extra = 0
    fields = ("unit_number", "floor", "unit_type", "bathrooms", "status", "asking_rent")
    show_change_link = True
    ordering = ("floor_sort", "unit_number")


from . import owner_admin  # noqa: E402,F401  (registers the Owner pages)
from .owner_admin import ActivityInline, DocumentInline, document_alert  # noqa: E402


class PropertyDocumentForm(forms.ModelForm):
    class Meta:
        model = PropertyDocument
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        use_file_widget(self, "file", compact=True, accept=".pdf,.jpg,.jpeg,.png,.webp")


class PropertyDocumentInline(DocumentInline):
    model = PropertyDocument
    form = PropertyDocumentForm


class PropertyActivityInline(ActivityInline):
    fk_name = "related_property"


class UnitDocumentForm(forms.ModelForm):
    class Meta:
        model = UnitDocument
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        use_file_widget(self, "file", compact=True, accept=".pdf,.jpg,.jpeg,.png,.webp")


class UnitDocumentInline(DocumentInline):
    model = UnitDocument
    form = UnitDocumentForm


class UnitActivityInline(ActivityInline):
    fk_name = "related_unit"


class UnitPhotoForm(forms.ModelForm):
    class Meta:
        model = UnitPhoto
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        use_file_widget(self, "image", compact=True, accept="image/png,image/jpeg,image/webp")
        self.fields["caption"].widget.attrs["placeholder"] = "e.g. Living room"


class UnitPhotoInline(TabularInline):
    model = UnitPhoto
    form = UnitPhotoForm
    tab = True
    extra = 0
    max_num = 30
    fields = ("preview", "image", "caption")
    readonly_fields = ("preview",)

    @admin.display(description="Picture")
    def preview(self, obj):
        if obj is None or not obj.pk or not obj.image:
            return "-"
        return format_html(
            '<img src="{}" alt="" style="width:64px;height:48px;object-fit:cover;border-radius:8px;">',
            obj.image.url,
        )


def _expiry_pills(documents):
    expired, soon = expiry_counts(documents)
    pills = []
    if expired:
        pills.append(badge(f"{expired} document(s) expired", "danger"))
    if soon:
        pills.append(badge(f"{soon} document(s) expiring soon", "warning"))
    return pills


def _header(avatar, name, pills, sub, stats, extra=""):
    return format_html(
        '<div class="erp-owner-header">{}<div class="erp-oh-main"><div class="erp-oh-name">{}</div>'
        '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{}</div>{}</div>'
        '<div class="erp-oh-stats">{}</div></div>',
        avatar,
        name,
        format_html_join("", "{}", ((p,) for p in pills)),
        sub,
        extra,
        format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
    )


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
            "community": "e.g. Al Reem Island",
            "parking_spaces": "e.g. 120",
            "title_deed_number": "e.g. 1234/2018",
            "plot_number": "e.g. 456",
            "makani_number": "10 digits",
            "dewa_premise_number": "e.g. 1234567890",
            "inactive_reason": "Why this property is no longer active",
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
    make_active, make_inactive = bulk.make_actions("properties")
    actions = ["export_excel", "export_csv", "make_active", "make_inactive", "change_owner"]

    @admin.action(description="Change owner of selected properties", permissions=["change"])
    def change_owner(self, request, queryset):
        return bulk.change_owner(self, request, queryset)
    actions_list = ["import_button"]
    import_noun = "properties"
    import_update_hint = "an existing property is found by its Code"
    import_headers = import_tools.PROPERTY_HEADERS
    import_notes = [
        "Required: Code, Name, Type.",
        "Owner is the exact owner name (blank = Company Owned).",
        "Fee type (Percent or Fixed) and the fee are needed for properties with another owner.",
        "See the \"Allowed values\" sheet in the template for Type, Emirate and Status.",
        "Optional columns: Area / community, Parking spaces, Title deed no., Plot no., Makani no. (10 digits), Utility premise no.",
        "To change existing properties, tick \"Update existing records\": only Code is needed, plus the columns you want to change.",
    ]

    def import_parse(self, upload, update=False):
        return import_tools.parse_property_file(upload, update=update)

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
        "community",
        "owner",
        "unit_count",
        "vacant_count",
        "rent_aed",
        "is_active",
    )
    list_filter = ("property_type", "emirate", "is_active", "owner")
    search_fields = (
        "name", "code", "owner__name", "community", "title_deed_number", "plot_number",
        "makani_number", "dewa_premise_number",
    )
    list_select_related = ("owner",)
    inlines = [UnitInline, PropertyDocumentInline, PropertyActivityInline]

    def get_changeform_initial_data(self, request):
        # A new property starts with "Company Owned" selected (change it if someone else owns it).
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("owner", Owner.company().pk)
        return initial
    _header = (None, {"fields": ("summary_header",)})
    # Fields marked "conditional" appear or disappear with the property type, owner and
    # Active box (see core/static/core/erp.js).
    _overview = (
        "Overview",
        {
            "classes": ["tab"],
            "fields": (
                ("name", "code"),
                ("property_type", "owner"),
                ("emirate", "community"),
                "is_active",
                "inactive_reason",  # conditional
                "address",
                "map_link",
                "notes",
            ),
            "description": "Residential and Commercial Buildings have units - after "
            "saving a building you will land on a page where you can add them.",
        },
    )
    _details = (
        "Details",
        {
            "classes": ["tab"],
            "fields": (
                ("year_built", "total_floors"),  # conditional
                "parking_spaces",
                ("title_deed_number", "plot_number"),
                ("makani_number", "dewa_premise_number"),
                ("apartment_type", "bathrooms"),  # conditional
                ("annual_rent", "status"),  # conditional
                ("management_fee_type", "management_fee_percent", "management_fee_fixed"),  # conditional
            ),
        },
    )

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (self._overview, self._details)
        return (self._header, self._overview, self._details)

    def get_readonly_fields(self, request, obj=None):
        return ("summary_header",)

    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        pills = [
            badge(obj.get_property_type_display(), "info"),
            badge("Active", "success") if obj.is_active else badge("Inactive", "neutral"),
        ]
        if obj.has_units:
            units = obj.units.all()
            total = units.count()
            vacant = units.filter(status=Unit.Status.VACANT).count()
            occupied = units.filter(status=Unit.Status.OCCUPIED).count()
            rent = units.aggregate(t=Sum("asking_rent"))["t"] or 0
            stats = [
                (total, "Units"),
                (vacant, "Vacant"),
                (f"{round(100 * occupied / total)}%" if total else "-", "Occupancy"),
                (format_amount(rent), "Asking rent, all units (AED / year)"),
            ]
        else:
            if obj.status:
                tone = {"vacant": "info", "occupied": "success", "reserved": "warning", "maintenance": "danger"}
                pills.append(badge(obj.get_status_display(), tone.get(obj.status, "neutral")))
            stats = [
                (obj.get_status_display() or "-", "Status"),
                (format_amount(obj.annual_rent), "Annual rent (AED)"),
            ]
        pills += _expiry_pills(obj.documents.all())
        bits = [obj.code, obj.get_emirate_display()]
        if obj.community:
            bits.append(obj.community)
        bits.append(f"Owner: {obj.owner.name}")
        sub = format_html_join(mark_safe(" · "), "{}", ((b,) for b in bits))
        extra = (
            format_html(
                '<div class="erp-oh-sub" style="font-weight:600;">Inactive: {}</div>', obj.inactive_reason
            )
            if (not obj.is_active and obj.inactive_reason)
            else ""
        )
        initials = obj.code[:2]
        return _header(circle_avatar("", initials, 64), obj.name, pills, sub, stats, extra)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "GET" and str(object_id).isdigit():
            limit = timezone.localdate() + timedelta(days=sysconf.expiry_days())
            document_alert(
                request,
                PropertyDocument.objects.filter(property_id=object_id, expiry_date__lte=limit).order_by(
                    "expiry_date"
                ),
            )
        return super().change_view(request, object_id, form_url, extra_context)

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
        instances = super().get_inline_instances(request, obj)
        # Units are shown only for saved properties that are buildings; history only once saved.
        keep = []
        for inline in instances:
            if isinstance(inline, UnitInline) and (obj is None or not obj.has_units):
                continue
            if isinstance(inline, PropertyActivityInline) and obj is None:
                continue
            keep.append(inline)
        return keep

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
            "parking_spaces": "e.g. 1",
            "dewa_account_number": "e.g. 1234567890",
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
    import_update_hint = "an existing unit is found by its Property code and Unit number"
    import_headers = import_tools.UNIT_HEADERS
    import_notes = [
        "Required: Property code, Unit number, Type. The property must already exist and be a building.",
        "Type must fit the building (residential: Studio, 1BHK-5BHK; commercial: Office, Shop).",
        "See the \"Allowed values\" sheet in the template.",
        "Optional columns: Parking spaces, View (Sea, City, Garden ...), Utility account no.",
        "To change existing units, tick \"Update existing records\": only Property code and Unit number are needed, plus the columns you want to change.",
    ]

    def import_parse(self, upload, update=False):
        return import_tools.parse_unit_file(upload, update=update)

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
    list_filter = ("status", "unit_type", ("property", BuildingFilter), "furnished", "view")
    search_fields = ("unit_number", "property__name", "property__code", "dewa_account_number")
    list_select_related = ("property",)
    inlines = [UnitPhotoInline, UnitDocumentInline, UnitActivityInline]

    _header = (None, {"fields": ("summary_header",)})
    _overview = (
        "Overview",
        {
            "classes": ["tab"],
            "fields": (
                ("property", "unit_number"),
                ("floor", "unit_type"),
                ("status", "furnished"),
                ("bathrooms", "area_sqft", "asking_rent"),
                ("parking_spaces", "view"),
                "dewa_account_number",
                "notes",
            ),
        },
    )

    def get_fieldsets(self, request, obj=None):
        return (self._header, self._overview) if obj is not None else (self._overview,)

    def get_readonly_fields(self, request, obj=None):
        return ("summary_header",)

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if obj is None:  # a new unit has no history yet
            instances = [i for i in instances if not isinstance(i, UnitActivityInline)]
        return instances

    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        tone = {"vacant": "info", "occupied": "success", "reserved": "warning", "maintenance": "danger"}
        pills = [
            badge(obj.get_unit_type_display(), "info"),
            badge(obj.get_status_display(), tone.get(obj.status, "neutral")),
        ]
        if obj.furnished:
            pills.append(badge("Furnished", "neutral"))
        if not obj.property.is_active:
            pills.append(badge("Building inactive", "neutral"))
        pills += _expiry_pills(obj.documents.all())
        sub = format_html("{} · Floor {}", obj.property.name, obj.floor or "-")
        stats = [
            (format_amount(obj.asking_rent), "Asking rent (AED / year)"),
            (format_amount(obj.area_sqft), "Area (sq ft)"),
            (obj.bathrooms or "-", "Bathrooms"),
            (obj.parking_spaces if obj.parking_spaces is not None else "-", "Parking"),
        ]
        first = obj.photos.first()
        avatar = circle_avatar(first.image.url if first else "", obj.unit_number[:3], 64)
        return _header(avatar, str(obj), pills, sub, stats)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "GET" and str(object_id).isdigit():
            limit = timezone.localdate() + timedelta(days=sysconf.expiry_days())
            document_alert(
                request,
                UnitDocument.objects.filter(unit_id=object_id, expiry_date__lte=limit).order_by("expiry_date"),
            )
        return super().change_view(request, object_id, form_url, extra_context)

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
