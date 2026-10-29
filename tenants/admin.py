"""Tenant pages: summary header, tabs (Overview, Contacts, Documents, Activity), list, export / import."""
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin
from unfold.decorators import display

from core.admin_utils import TooltipHelpMixin, badge, circle_avatar
from core.formats import fmt_date
from core.models import AuditEntry
from core.widgets import use_file_widget
from properties.import_admin import ImportMixin
from properties.models_constants import EXPIRY_WARNING_DAYS
from properties.owner_admin import (
    ActivityInline,
    ContactInline,
    DocumentInline,
    DocumentsFilter,
    _initials,
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
            "nationality": "e.g. Egyptian",
        }
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        for name in ("address", "notes"):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = 3
        use_file_widget(self, "photo", preview=True, accept="image/png,image/jpeg,image/webp")


@admin.register(Tenant)
class TenantAdmin(ImportMixin, TooltipHelpMixin, ModelAdmin):
    form = TenantAdminForm
    compressed_fields = True
    list_display = ("name_with_avatar", "code", "type_badge", "contact", "nationality", "docs_badge", "is_active")
    list_display_links = ("name_with_avatar",)
    list_filter = ("tenant_type", "is_active", DocumentsFilter)
    search_fields = (
        "name", "code", "email", "phone", "nationality",
        "contacts__name", "contacts__email", "contacts__mobile", "documents__number",
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
                ("email", "phone"),
                "nationality",
                "address",
                "photo",
                "notes",
            ),
        },
    )

    def get_fieldsets(self, request, obj=None):
        return (self._header, self._overview) if obj is not None else (self._overview,)

    def get_readonly_fields(self, request, obj=None):
        return ("code", "summary_header")

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if obj is None:  # a new tenant has no history yet
            instances = [i for i in instances if not isinstance(i, TenantActivityInline)]
        return instances

    def get_queryset(self, request):
        today = timezone.localdate()
        limit = today + timedelta(days=EXPIRY_WARNING_DAYS)
        return super().get_queryset(request).annotate(
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
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:10px;">{}<span>{}</span></span>',
            _tenant_avatar(obj, 30), obj.name,
        )

    @display(description="Type", ordering="tenant_type", label={"Individual": "info", "Company": "success"})
    def type_badge(self, obj):
        return obj.get_tenant_type_display()

    @admin.display(description="Contact")
    def contact(self, obj):
        parts = []
        if obj.phone:
            parts.append(format_html('<a href="tel:{}">{}</a>', obj.phone.replace(" ", ""), obj.phone))
        if obj.email:
            parts.append(format_html('<a href="mailto:{}">{}</a>', obj.email, obj.email))
        return format_html_join(mark_safe("<br>"), "{}", ((p,) for p in parts)) if parts else "-"

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

    # ---- summary header --------------------------------------------------------------
    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        pills = [badge(obj.get_tenant_type_display(), "info" if obj.tenant_type == "individual" else "success")]
        pills.append(badge("Active", "success") if obj.is_active else badge("Inactive", "neutral"))
        expired, soon = obj.document_counts()
        if expired:
            pills.append(badge(f"{expired} document(s) expired", "danger"))
        if soon:
            pills.append(badge(f"{soon} document(s) expiring soon", "warning"))
        contact = " · ".join(x for x in (obj.code, obj.phone, obj.email) if x)
        stats = [
            (obj.documents.count(), "Documents"),
            (obj.contacts.count(), "Contacts"),
            (fmt_date(timezone.localdate(obj.created_at)), "Tenant since"),
        ]
        return format_html(
            '<div class="erp-owner-header">{}<div class="erp-oh-main"><div class="erp-oh-name">{}</div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{}</div></div>'
            '<div class="erp-oh-stats">{}</div></div>',
            _tenant_avatar(obj, 64),
            obj.name,
            format_html_join("", "{}", ((p,) for p in pills)),
            contact,
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
        )

    # ---- warnings on the tenant page ---------------------------------------------------
    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "GET" and str(object_id).isdigit():
            today = timezone.localdate()
            limit = today + timedelta(days=EXPIRY_WARNING_DAYS)
            for doc in TenantDocument.objects.filter(tenant_id=object_id, expiry_date__lte=limit):
                when = fmt_date(doc.expiry_date)
                if doc.expiry_date < today:
                    messages.error(request, f"{doc} expired on {when}.")
                else:
                    messages.warning(request, f"{doc} expires on {when}.")
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
    import_notes = ["Required: Name, Nationality, Email and Phone. Type is Individual or Company (blank = Individual)."]

    def import_parse(self, upload):
        return tenant_tools.parse_tenant_file(upload)

    def import_template(self):
        return tenant_tools.template_response()
