"""Owner pages: summary header, tabs, a richer list, export / import."""
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.forms.models import BaseInlineFormSet
from django.shortcuts import redirect, render
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from core.admin_utils import TooltipHelpMixin, badge, circle_avatar, format_amount
from core.formats import fmt_date
from core.models import AuditEntry
from core.widgets import use_file_widget

from . import owner_tools
from .import_admin import ImportMixin
from .models import (
    Owner,
    OwnerBankAccount,
    OwnerContact,
    OwnerDocument,
    Property,
    Unit,
)
from .models_constants import EXPIRY_WARNING_DAYS

STANDALONE_TYPES = [t for t in Property.PropertyType.values if t not in Property.BUILDING_TYPES]


def _initials(name):
    words = [w for w in (name or "").split() if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _owner_avatar(owner, size=32):
    return circle_avatar(owner.photo.url if owner.photo else "", _initials(owner.name), size)



# ---------------------------------------------------------------------------------------
# Tabs (inlines)
# ---------------------------------------------------------------------------------------
class OnePerOwnerFormSet(BaseInlineFormSet):
    """Base for tables where at most one row may carry a 'main' flag."""

    flag = ""
    flag_message = ""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        flagged = 0
        for form in self.forms:
            data = getattr(form, "cleaned_data", None)
            if data and not data.get("DELETE") and data.get(self.flag):
                flagged += 1
        if flagged > 1:
            raise ValidationError(self.flag_message)


class ContactFormSet(OnePerOwnerFormSet):
    flag = "is_primary"
    flag_message = "Only one contact can be marked as Primary."


class BankFormSet(OnePerOwnerFormSet):
    flag = "is_default"
    flag_message = "Only one bank account can be marked as Default."


class ContactInline(TabularInline):
    model = OwnerContact
    formset = ContactFormSet
    tab = True
    extra = 0
    fields = ("name", "role", "is_primary", "mobile", "whatsapp", "email", "language")


class OwnerDocumentForm(forms.ModelForm):
    class Meta:
        model = OwnerDocument
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        use_file_widget(self, "file", compact=True, accept=".pdf,.jpg,.jpeg,.png,.webp")


class DocumentInline(TabularInline):
    model = OwnerDocument
    form = OwnerDocumentForm
    tab = True
    extra = 0
    fields = ("doc_type", "number", "expiry_date", "status", "file", "notes")
    readonly_fields = ("status",)

    @admin.display(description="Status")
    def status(self, obj):
        if obj is None or not obj.pk:
            return "-"
        label, tone = {
            "expired": ("Expired", "danger"),
            "soon": ("Expiring soon", "warning"),
            "valid": ("Valid", "success"),
            "none": ("No expiry", "neutral"),
        }[obj.expiry_status]
        return badge(label, tone)


class BankInline(TabularInline):
    """The 'Financials' tab. Only roles with bank-account permissions can see it."""

    model = OwnerBankAccount
    formset = BankFormSet
    tab = True
    extra = 0
    fields = ("account_holder", "bank_name", "iban", "swift", "is_default")


class ReadOnlyInline(TabularInline):
    extra = 0
    can_delete = False
    max_num = 0

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PropertyListInline(ReadOnlyInline):
    """The 'Properties' tab: everything this owner owns."""

    model = Property
    tab = True
    verbose_name_plural = "Properties"
    show_change_link = True
    fields = ("name", "code", "property_type", "emirate", "occupancy", "is_active")
    readonly_fields = ("occupancy",)

    @admin.display(description="Units / status")
    def occupancy(self, obj):
        if obj.has_units:
            total = obj.units.count()
            vacant = obj.units.filter(status=Unit.Status.VACANT).count()
            return f"{total} units, {vacant} vacant"
        return obj.get_status_display() or "-"


class ActivityInline(ReadOnlyInline):
    """The 'Activity' tab: who changed what, from the audit log."""

    model = AuditEntry
    fk_name = "related_owner"
    tab = True
    verbose_name_plural = "Activity"
    fields = ("timestamp", "username", "what", "object_repr", "details")
    readonly_fields = fields
    ordering = ("-timestamp", "-id")

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Action")
    def what(self, obj):
        return obj.get_action_display()

    @admin.display(description="Details")
    def details(self, obj):
        return obj.summary()


# ---------------------------------------------------------------------------------------
# List filters
# ---------------------------------------------------------------------------------------
class DocumentsFilter(admin.SimpleListFilter):
    title = "Documents"
    parameter_name = "docs"

    def lookups(self, request, model_admin):
        return (("expired", "Has expired documents"), ("soon", f"Expiring within {EXPIRY_WARNING_DAYS} days"))

    def queryset(self, request, queryset):
        today = timezone.localdate()
        if self.value() == "expired":
            return queryset.filter(_docs_expired__gt=0)
        if self.value() == "soon":
            return queryset.filter(_docs_soon__gt=0)
        return queryset


# ---------------------------------------------------------------------------------------
# The Owner admin
# ---------------------------------------------------------------------------------------
class OwnerAdminForm(forms.ModelForm):
    class Meta:
        model = Owner
        exclude = ("user",)  # owner portal is a later module

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hints = {
            "name": "e.g. Khalifa Al Mansoori",
            "email": "name@example.com",
            "phone": "+971 50 123 4567",
        }
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        for name in ("address", "notes"):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = 3
        use_file_widget(self, "photo", preview=True, accept="image/png,image/jpeg,image/webp")


@admin.register(Owner)
class OwnerAdmin(ImportMixin, TooltipHelpMixin, ModelAdmin):
    form = OwnerAdminForm
    compressed_fields = True
    list_display = (
        "name_with_avatar", "code", "type_badge", "contact", "property_count", "unit_count",
        "occupancy", "docs_badge", "is_active",
    )
    list_display_links = ("name_with_avatar",)
    list_filter = ("owner_type", "is_active", DocumentsFilter, "properties__emirate")
    search_fields = (
        "name", "code", "email", "phone",
        "contacts__name", "contacts__email", "contacts__mobile", "documents__number",
    )
    inlines = [ContactInline, PropertyListInline, DocumentInline, BankInline, ActivityInline]
    actions = ["export_excel", "export_csv"]
    actions_list = ["import_button"]

    # ---- form layout ---------------------------------------------------------------
    _header = (None, {"fields": ("summary_header",)})
    _overview = (
        "Overview",
        {
            "classes": ["tab"],
            "fields": (
                ("name", "owner_type"),
                ("code", "is_active"),
                ("email", "phone"),
                "address",
                "photo",
                "notes",
            ),
        },
    )

    def get_fieldsets(self, request, obj=None):
        return (self._header, self._overview) if obj is not None else (self._overview,)

    def get_readonly_fields(self, request, obj=None):
        fixed = ("code", "summary_header")
        if obj is not None and obj.is_company:  # the built-in owner cannot be changed this way
            return fixed + ("name", "is_active", "owner_type")
        return fixed

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if obj is None:  # a new owner has no properties or history yet
            instances = [i for i in instances if not isinstance(i, (PropertyListInline, ActivityInline))]
        return instances

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_company:
            return False
        return super().has_delete_permission(request, obj)

    # ---- queries ---------------------------------------------------------------------
    def get_queryset(self, request):
        today = timezone.localdate()
        limit = today + timedelta(days=EXPIRY_WARNING_DAYS)
        return super().get_queryset(request).annotate(
            _property_count=Count("properties", distinct=True),
            _unit_count=Count("properties__units", distinct=True),
            _occupied_units=Count(
                "properties__units", filter=Q(properties__units__status="occupied"), distinct=True
            ),
            _standalone=Count(
                "properties", filter=Q(properties__property_type__in=STANDALONE_TYPES), distinct=True
            ),
            _occupied_standalone=Count(
                "properties",
                filter=Q(properties__property_type__in=STANDALONE_TYPES, properties__status="occupied"),
                distinct=True,
            ),
            _docs_expired=Count(
                "documents", filter=Q(documents__expiry_date__lt=today), distinct=True
            ),
            _docs_soon=Count(
                "documents",
                filter=Q(documents__expiry_date__gte=today, documents__expiry_date__lte=limit),
                distinct=True,
            ),
        )

    def get_search_results(self, request, queryset, search_term):
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        # The owner dropdown on the Property form offers active owners only.
        if request.GET.get("model_name") == "property" and request.GET.get("field_name") == "owner":
            queryset = queryset.filter(is_active=True)
        return queryset, may_have_duplicates

    # ---- list columns ------------------------------------------------------------------
    @admin.display(description="Owner", ordering="name")
    def name_with_avatar(self, obj):
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:10px;">{}<span>{}</span></span>',
            _owner_avatar(obj, 30), obj.name,
        )

    @display(
        description="Type",
        ordering="owner_type",
        label={"Individual": "info", "Company": "success"},
    )
    def type_badge(self, obj):
        return obj.get_owner_type_display()

    @admin.display(description="Contact")
    def contact(self, obj):
        parts = []
        if obj.phone:
            parts.append(format_html('<a href="tel:{}">{}</a>', obj.phone.replace(" ", ""), obj.phone))
        if obj.email:
            parts.append(format_html('<a href="mailto:{}">{}</a>', obj.email, obj.email))
        return format_html_join(mark_safe("<br>"), "{}", ((p,) for p in parts)) if parts else "-"

    @admin.display(description="Properties", ordering="_property_count")
    def property_count(self, obj):
        return obj._property_count

    @admin.display(description="Units", ordering="_unit_count")
    def unit_count(self, obj):
        return obj._unit_count

    @admin.display(description="Occupancy")
    def occupancy(self, obj):
        total = obj._unit_count + obj._standalone
        if not total:
            return "-"
        return f"{round(100 * (obj._occupied_units + obj._occupied_standalone) / total)}%"

    @display(
        description="Documents",
        label={"OK": "success", "Expiring soon": "warning", "Expired": "danger"},
    )
    def docs_badge(self, obj):
        if obj._docs_expired:
            return "Expired"
        if obj._docs_soon:
            return "Expiring soon"
        return "OK"

    # ---- summary header ----------------------------------------------------------------
    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        properties = obj.properties.all()
        standalone = properties.filter(property_type__in=STANDALONE_TYPES)
        units = Unit.objects.filter(property__owner=obj)
        unit_total = units.count()
        rentable = unit_total + standalone.count()
        occupied = units.filter(status=Unit.Status.OCCUPIED).count() + standalone.filter(
            status=Property.Status.OCCUPIED
        ).count()
        occupancy = f"{round(100 * occupied / rentable)}%" if rentable else "-"
        rent = (units.aggregate(t=Sum("asking_rent"))["t"] or 0) + (
            standalone.aggregate(t=Sum("annual_rent"))["t"] or 0
        )

        pills = [badge(obj.get_owner_type_display(), "info" if not obj.is_company else "success")]
        pills.append(badge("Active", "success") if obj.is_active else badge("Inactive", "neutral"))
        expired, soon = obj.document_counts()
        if expired:
            pills.append(badge(f"{expired} document(s) expired", "danger"))
        if soon:
            pills.append(badge(f"{soon} document(s) expiring soon", "warning"))

        contact = " · ".join(x for x in (obj.code, obj.phone, obj.email) if x)
        stats = [
            (properties.count(), "Properties"),
            (unit_total, "Units"),
            (occupancy, "Occupancy"),
            (format_amount(rent), "Listed rent (AED / year)"),
        ]
        return format_html(
            '<div class="erp-owner-header">{}<div class="erp-oh-main"><div class="erp-oh-name">{}</div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{}</div></div>'
            '<div class="erp-oh-stats">{}</div></div>',
            _owner_avatar(obj, 64),
            obj.name,
            format_html_join("", "{}", ((p,) for p in pills)),
            contact,
            format_html_join(
                "", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats
            ),
        )

    # ---- warnings on the owner page --------------------------------------------------
    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "GET" and str(object_id).isdigit():
            today = timezone.localdate()
            limit = today + timedelta(days=EXPIRY_WARNING_DAYS)
            for doc in OwnerDocument.objects.filter(owner_id=object_id, expiry_date__lte=limit):
                when = fmt_date(doc.expiry_date)
                if doc.expiry_date < today:
                    messages.error(request, f"{doc} expired on {when}.")
                else:
                    messages.warning(request, f"{doc} expires on {when}.")
        return super().change_view(request, object_id, form_url, extra_context)

    # ---- export / import ---------------------------------------------------------------
    @admin.action(description="Export selected owners to Excel")
    def export_excel(self, request, queryset):
        return owner_tools.excel_response(queryset)

    @admin.action(description="Export selected owners to CSV")
    def export_csv(self, request, queryset):
        return owner_tools.csv_response(queryset)

    # ---- import (button on the list page; shared page in import_admin.py) ---------------
    import_noun = "owners"
    import_headers = owner_tools.IMPORT_HEADERS
    import_notes = [
        "Only Name is required. Type is Individual or Company (blank = Individual).",
    ]

    def import_parse(self, upload):
        return owner_tools.parse_owner_file(upload)

    def import_template(self):
        return owner_tools.template_response()
