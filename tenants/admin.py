"""Tenant pages: summary header, tabs (Overview, Contacts, Documents, Activity), list, export / import."""
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin
from unfold.decorators import display

from core.admin_utils import TooltipHelpMixin, badge, circle_avatar, whatsapp_link
from core import sysconf
from core.formats import fmt_date
from core.models import AuditEntry
from core.widgets import use_file_widget
from properties.import_admin import ImportMixin
from properties.owner_admin import (
    ActivityInline,
    ContactInline,
    DocumentInline,
    DocumentsFilter,
    _initials,
    document_alert,
)

from . import tenant_tools
from .models import Tenant, TenantContact, TenantDocument


def _tenant_avatar(tenant, size=32):
    return circle_avatar(tenant.photo.url if tenant.photo else "", _initials(tenant.name), size)


class TenantContactInline(ContactInline):
    model = TenantContact


class TenantDocumentForm(forms.ModelForm):
    class Meta:
        model = TenantDocument
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        use_file_widget(self, "file", compact=True, accept=".pdf,.jpg,.jpeg,.png,.webp")


class TenantDocumentInline(DocumentInline):
    model = TenantDocument
    form = TenantDocumentForm


class TenantActivityInline(ActivityInline):
    fk_name = "related_tenant"


class TenantAdminForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hints = {
            "name": "e.g. Ahmed Hassan",
            "email": "name@example.com",
            "phone": "+971 50 123 4567",
            "emirates_id_number": "784-1990-1234567-1",
            "passport_number": "e.g. A1234567",
            "trade_licence_number": "e.g. CN-1234567",
            "authorised_signatory": "Name of the person who signs",
            "trn": "15 digits",
            "blacklist_reason": "Why this tenant is blacklisted",
            "inactive_reason": "Why this tenant is no longer active",
        }
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        for name in ("address", "notes"):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = 3
        use_file_widget(self, "photo", preview=True, accept="image/png,image/jpeg,image/webp")


class FilesFilter(admin.SimpleListFilter):
    """Tenants whose required documents (Emirates ID and passport, or trade licence) are all on file."""

    title = "Documents on file"
    parameter_name = "files"

    def lookups(self, request, model_admin):
        return (("complete", "Complete"), ("incomplete", "Incomplete"))

    def queryset(self, request, queryset):
        if self.value() not in ("complete", "incomplete"):
            return queryset

        def has(kind):
            return Exists(TenantDocument.objects.filter(tenant=OuterRef("pk"), doc_type=kind))

        queryset = queryset.annotate(_eid=has("emirates_id"), _passport=has("passport"), _licence=has("trade_licence"))
        complete = Q(tenant_type="individual", _eid=True, _passport=True) | Q(tenant_type="company", _licence=True)
        return queryset.filter(complete) if self.value() == "complete" else queryset.exclude(complete)


@admin.register(Tenant)
class TenantAdmin(ImportMixin, TooltipHelpMixin, ModelAdmin):
    form = TenantAdminForm
    compressed_fields = True
    list_display = (
        "name_with_avatar", "code", "type_badge", "contact", "nationality", "files_badge", "docs_badge", "is_active",
    )
    list_display_links = ("name_with_avatar",)
    list_filter = (
        "tenant_type", "is_active", "is_blacklisted", FilesFilter, DocumentsFilter,
        ("nationality", admin.AllValuesFieldListFilter),
    )
    search_fields = (
        "name", "code", "email", "phone", "nationality", "emirates_id_number", "passport_number",
        "trade_licence_number", "trn", "contacts__name", "contacts__email", "contacts__mobile", "documents__number",
    )
    inlines = [TenantContactInline, TenantDocumentInline, TenantActivityInline]
    actions = ["export_excel", "export_csv"]
    actions_list = ["import_button"]

    _header = (None, {"fields": ("summary_header",)})
    _overview = (
        "Overview",
        {
            "classes": ["tab"],
            "fields": (
                ("name", "tenant_type"),
                ("code", "is_active"),
                "inactive_reason",
                "is_blacklisted",
                "blacklist_reason",
                ("email", "phone"),
                "nationality",
                "address",
                "photo",
                "notes",
            ),
        },
    )
    _identity = (
        "Identity",
        {
            "classes": ["tab"],
            "description": "All optional. Individuals: Emirates ID, passport and date of birth. "
            "Companies: trade licence and authorised signatory.",
            "fields": (
                ("emirates_id_number", "passport_number"),
                "date_of_birth",
                ("trade_licence_number", "authorised_signatory"),
                "trn",
            ),
        },
    )

    def get_fieldsets(self, request, obj=None):
        return (self._header, self._overview, self._identity) if obj is not None else (self._overview, self._identity)

    def get_readonly_fields(self, request, obj=None):
        return ("code", "summary_header")

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if obj is None:  # a new tenant has no history yet
            instances = [i for i in instances if not isinstance(i, TenantActivityInline)]
        return instances

    def get_queryset(self, request):
        today = timezone.localdate()
        limit = today + timedelta(days=sysconf.expiry_days())
        return super().get_queryset(request).prefetch_related("documents").annotate(
            _docs_expired=Count("documents", filter=Q(documents__expiry_date__lt=today), distinct=True),
            _docs_soon=Count(
                "documents",
                filter=Q(documents__expiry_date__gte=today, documents__expiry_date__lte=limit),
                distinct=True,
            ),
        )

    # ---- list columns ----------------------------------------------------------------
    @admin.display(description="Tenant", ordering="name")
    def name_with_avatar(self, obj):
        flag = badge("Blacklisted", "danger") if obj.is_blacklisted else ""
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:10px;">{}<span>{}</span>{}</span>',
            _tenant_avatar(obj, 30), obj.name, flag,
        )

    @display(description="Type", ordering="tenant_type", label={"Individual": "info", "Company": "success"})
    def type_badge(self, obj):
        return obj.get_tenant_type_display()

    @admin.display(description="Contact")
    def contact(self, obj):
        parts = []
        if obj.phone:
            parts.append(
                format_html(
                    '<span class="erp-phone-line"><a href="tel:{}">{}</a>{}</span>',
                    obj.phone.replace(" ", ""), obj.phone, whatsapp_link(obj.phone),
                )
            )
        if obj.email:
            parts.append(format_html('<a href="mailto:{}">{}</a>', obj.email, obj.email))
        return format_html_join(mark_safe("<br>"), "{}", ((p,) for p in parts)) if parts else "-"

    @display(description="On file", label={"Complete": "success", "Incomplete": "warning"})
    def files_badge(self, obj):
        return "Incomplete" if obj.missing_documents() else "Complete"

    @display(
        description="Expiry",
        label={"OK": "success", "Expiring soon": "warning", "Expired": "danger"},
    )
    def docs_badge(self, obj):
        if obj._docs_expired:
            return "Expired"
        if obj._docs_soon:
            return "Expiring soon"
        return "OK"

    # ---- summary header --------------------------------------------------------------
    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        pills = [badge(obj.get_tenant_type_display(), "info" if obj.tenant_type == "individual" else "success")]
        pills.append(badge("Active", "success") if obj.is_active else badge("Inactive", "neutral"))
        if obj.is_blacklisted:
            pills.append(badge("Blacklisted", "danger"))
        missing = obj.missing_documents()
        pills.append(badge("Documents complete", "success") if not missing else badge("Documents incomplete", "warning"))
        expired, soon = obj.document_counts()
        if expired:
            pills.append(badge(f"{expired} document(s) expired", "danger"))
        if soon:
            pills.append(badge(f"{soon} document(s) expiring soon", "warning"))

        bits = [obj.code]
        if obj.phone:
            bits.append(format_html('<span class="erp-phone-line">{}{}</span>', obj.phone, whatsapp_link(obj.phone)))
        if obj.email:
            bits.append(obj.email)
        contact = format_html_join(mark_safe(" · "), "{}", ((b,) for b in bits if b))

        notes = []
        if obj.is_blacklisted:
            notes.append(("#b91c1c", f"Blacklisted: {obj.blacklist_reason}"))
        if not obj.is_active and obj.inactive_reason:
            notes.append(("#6b7280", f"Inactive: {obj.inactive_reason}"))
        if missing:
            notes.append(("#92400e", "Missing: " + ", ".join(missing)))
        extra = format_html_join(
            "", '<div class="erp-oh-sub" style="color:{};font-weight:600;">{}</div>', notes
        )
        stats = [
            (obj.documents.count(), "Documents"),
            (obj.contacts.count(), "Contacts"),
            (fmt_date(timezone.localdate(obj.created_at)), "Tenant since"),
        ]
        return format_html(
            '<div class="erp-owner-header">{}<div class="erp-oh-main"><div class="erp-oh-name">{}</div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{}</div>{}</div>'
            '<div class="erp-oh-stats">{}</div></div>',
            _tenant_avatar(obj, 64),
            obj.name,
            format_html_join("", "{}", ((p,) for p in pills)),
            contact,
            extra,
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
        )

    # ---- warnings on the tenant page ---------------------------------------------------
    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "GET" and str(object_id).isdigit():
            today = timezone.localdate()
            limit = today + timedelta(days=sysconf.expiry_days())
            document_alert(
                request,
                TenantDocument.objects.filter(tenant_id=object_id, expiry_date__lte=limit).order_by("expiry_date"),
            )
        return super().change_view(request, object_id, form_url, extra_context)

    # ---- export / import ----------------------------------------------------------------
    @admin.action(description="Export selected tenants to Excel")
    def export_excel(self, request, queryset):
        return tenant_tools.excel_response(queryset)

    @admin.action(description="Export selected tenants to CSV")
    def export_csv(self, request, queryset):
        return tenant_tools.csv_response(queryset)

    import_noun = "tenants"
    import_headers = tenant_tools.IMPORT_HEADERS
    import_notes = [
        "Required: Name, Nationality, Email and Phone. Type is Individual or Company (blank = Individual).",
        "Nationality: a country name such as Egypt or United Kingdom (a nationality such as Egyptian also works).",
        "A tenant who already exists (same email, or same name and phone) is refused, so importing a file twice does no harm.",
        "Phone numbers: keep the leading + or 0 (the template's Phone column is already set to Text).",
    ]

    def import_parse(self, upload):
        return tenant_tools.parse_tenant_file(upload)

    def import_template(self):
        return tenant_tools.template_response()
