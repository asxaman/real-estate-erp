"""Vendor and maintenance request pages: lists with quick filters, forms (tabs), and the Maintenance tab on other pages."""
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin, TabularInline

from core import exports, sysconf
from core.admin_utils import ExportPermissionMixin, TooltipHelpMixin, add_inline_tab, badge
from core.formats import fmt_date
from core.paging import RowsPerPageMixin
from core.search import SmartSearchMixin
from core.widgets import use_file_widget
from properties.admin import PropertyAdmin, UnitAdmin
from properties.owner_admin import DocumentFormSet, ReadOnlyInline
from tenants.admin import TenantAdmin

from .models import OPEN_STATUSES, MaintenanceRequest, Vendor, VendorDocument

STATUS_TONES = {
    "new": "info", "awaiting_approval": "warning", "approved": "info", "in_progress": "info",
    "completed": "success", "rejected": "danger", "cancelled": "neutral",
}
PRIORITY_TONES = {"low": "neutral", "normal": "info", "high": "warning", "urgent": "danger"}
PAID_BY_TONES = {"owner": "info", "tenant": "warning", "company": "neutral"}


def money(value):
    return "-" if value is None else f"AED {value:,.2f}"


def status_badge(job):
    return badge(job.get_status_display(), STATUS_TONES.get(job.status, "neutral"))


def priority_badge(job):
    return badge(job.get_priority_display(), PRIORITY_TONES.get(job.priority, "neutral"))


def is_overdue(job, today=None):
    today = today or timezone.localdate()
    return job.status in OPEN_STATUSES and job.scheduled_on is not None and job.scheduled_on < today


# ---------------------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------------------
VENDOR_HEADERS = ["Vendor no.", "Name", "Main trade", "Other trades", "Contact person", "Phone", "Email", "Address",
                  "Trade licence number", "TRN", "Active", "Open jobs", "Notes"]


def vendor_export_rows(queryset):
    for v in queryset.annotate(_open=Count("requests", filter=Q(requests__status__in=OPEN_STATUSES))):
        yield [v.code, v.name, v.get_trade_display(), v.other_trades, v.contact_person, v.phone, v.email, v.address,
               v.trade_licence_number, v.trn, "Yes" if v.is_active else "No", v._open, v.notes]


class VendorForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hints = {
            "name": "e.g. Al Noor Plumbing LLC", "other_trades": "e.g. painting, cleaning", "contact_person": "e.g. Rashid Ali",
            "phone": "+971 50 123 4567", "email": "e.g. info@alnoor.ae", "trade_licence_number": "e.g. 123456",
            "trn": "15 digits", "inactive_reason": "e.g. Poor workmanship",
        }
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        for name in ("address", "notes"):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = 3


class VendorDocumentForm(forms.ModelForm):
    class Meta:
        model = VendorDocument
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        use_file_widget(self, "file", compact=True, accept=".pdf,.jpg,.jpeg,.png,.webp")


class VendorDocumentInline(TabularInline):
    model = VendorDocument
    form = VendorDocumentForm
    formset = DocumentFormSet
    tab = True
    extra = 0
    fields = ("doc_type", "number", "expiry_date", "status", "file", "notes")
    readonly_fields = ("status",)

    @admin.display(description="Status")
    def status(self, obj):
        if obj is None or not obj.pk:
            return "-"
        label, tone = {
            "expired": ("Expired", "danger"), "soon": ("Expiring soon", "warning"),
            "valid": ("Valid", "success"), "none": ("No expiry", "neutral"),
        }[obj.expiry_status]
        return badge(label, tone)


class VendorJobsInline(ReadOnlyInline):
    """The 'Jobs' tab: the work this vendor did or is doing."""

    model = MaintenanceRequest
    fk_name = "vendor"
    tab = True
    verbose_name_plural = "Jobs"
    show_change_link = False
    fields = ("job", "where", "title", "cost", "state", "open_job")
    readonly_fields = fields
    ordering = ("-reported_on", "-id")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("maintenance.view_maintenancerequest")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("property", "unit")

    @admin.display(description="Job no.")
    def job(self, obj):
        return obj.code

    @admin.display(description="Where")
    def where(self, obj):
        return obj.where

    @admin.display(description="Cost")
    def cost(self, obj):
        return money(obj.final_cost if obj.final_cost is not None else obj.estimate)

    @admin.display(description="Status")
    def state(self, obj):
        return status_badge(obj)

    @admin.display(description="")
    def open_job(self, obj):
        return _open_button(obj.pk) if obj.pk else ""


def _open_button(job_id):
    return format_html(
        '<a class="erp-qbtn erp-qbtn-primary" href="{}">Open job</a>',
        reverse("admin:maintenance_maintenancerequest_change", args=[job_id]),
    )


@admin.register(Vendor)
class VendorAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = VendorForm
    compressed_fields = True
    list_display = ("code", "name", "trade_text", "contact", "phone", "open_jobs_count", "documents", "state")
    list_display_links = ("code", "name")
    list_filter = ("trade", "is_active")
    search_fields = ("code", "name", "contact_person", "phone", "email", "trade_licence_number")
    search_text = ("code", "name", "contact_person", "email", "trade_licence_number")
    search_digits = ("phone",)
    search_codes = (("code", "VND", 4),)
    search_fuzzy = ("name",)
    search_hint = "vendor number (VND-12), name, contact person, phone, email or licence number"
    inlines = [VendorDocumentInline, VendorJobsInline]
    actions = ["export_excel", "export_csv"]

    _header = (None, {"fields": ("summary_header",)})
    _vendor = (
        "Vendor",
        {
            "classes": ["tab"],
            "fields": (
                ("name", "trade"),
                "other_trades",
                ("contact_person", "phone"),
                ("email", "trade_licence_number"),
                "trn",
                "address",
                ("is_active", "inactive_reason"),
                "notes",
            ),
        },
    )

    def get_fieldsets(self, request, obj=None):
        return (self._vendor,) if obj is None else (self._header, self._vendor)

    def get_readonly_fields(self, request, obj=None):
        return ("summary_header",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _open=Count("requests", filter=Q(requests__status__in=OPEN_STATUSES), distinct=True)
        )  # (the quick filters use "pk__in" so they never repeat this join)

    def has_delete_permission(self, request, obj=None):
        # A vendor who has done work stays in the records (make them inactive instead).
        if obj is not None and obj.requests.exists():
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def quick_chips(self):
        today = timezone.localdate()
        soon = today + timedelta(days=sysconf.expiry_days())
        return [
            ("active", "Active", "success", Q(is_active=True)),
            ("busy", "With Open Jobs", "info",
             Q(pk__in=MaintenanceRequest.objects.filter(status__in=OPEN_STATUSES).values("vendor_id"))),
            ("docs", "Documents Need Attention", "warning",
             Q(is_active=True, pk__in=VendorDocument.objects.filter(expiry_date__lte=soon).values("vendor_id"))),
            ("inactive", "Inactive", "neutral", Q(is_active=False)),
        ]

    @admin.display(description="Main trade", ordering="trade")
    def trade_text(self, obj):
        return obj.get_trade_display()

    @admin.display(description="Contact", ordering="contact_person")
    def contact(self, obj):
        return obj.contact_person or "-"

    @admin.display(description="Open jobs", ordering="_open")
    def open_jobs_count(self, obj):
        return getattr(obj, "_open", 0) or "-"

    @admin.display(description="Documents")
    def documents(self, obj):
        expired, soon = obj.document_counts()
        missing = obj.missing_documents()
        if expired:
            return badge(f"{expired} expired", "danger")
        if soon:
            return badge(f"{soon} expiring soon", "warning")
        if missing:
            return badge("Licence missing", "warning")
        return badge("OK", "success")

    @admin.display(description="State", ordering="is_active")
    def state(self, obj):
        return badge("Active", "success") if obj.is_active else badge("Inactive", "neutral")

    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        pills = [badge("Active", "success") if obj.is_active else badge("Inactive", "neutral"), badge(obj.get_trade_display(), "info")]
        expired, soon = obj.document_counts()
        if expired:
            pills.append(badge(f"{expired} document(s) expired", "danger"))
        if soon:
            pills.append(badge(f"{soon} document(s) expiring soon", "warning"))
        for name in obj.missing_documents():
            pills.append(badge(f"{name} not uploaded", "warning"))
        jobs = obj.requests.all()
        done = jobs.filter(status="completed")
        spent = sum((j.final_cost or Decimal("0")) for j in done)
        stats = [(jobs.filter(status__in=OPEN_STATUSES).count(), "Open jobs"), (done.count(), "Completed"), (money(spent), "Paid in total")]
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · {}</div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{}</div></div><div class="erp-oh-stats">{}</div></div>',
            obj.code, obj.name, format_html_join("", "{}", ((p,) for p in pills)),
            " · ".join(x for x in (obj.contact_person, obj.phone, obj.email) if x),
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
        )

    @admin.action(description="Export selected vendors to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Vendors", VENDOR_HEADERS, vendor_export_rows(queryset), "vendors")

    @admin.action(description="Export selected vendors to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(VENDOR_HEADERS, vendor_export_rows(queryset), "vendors")


# ---------------------------------------------------------------------------------------
# Maintenance requests
# ---------------------------------------------------------------------------------------
JOB_HEADERS = ["Job no.", "Reported on", "Property", "Unit", "Owner", "Tenant", "Problem", "Type of work", "Priority", "Status",
               "Vendor", "Planned for", "Finished on", "Estimate (AED)", "Final cost (AED)", "Paid by", "Approved by",
               "Approved on", "Rejection reason", "Notes"]


def job_export_rows(queryset):
    for j in queryset.select_related("property__owner", "unit", "tenant", "vendor", "approved_by"):
        yield [
            j.code, j.reported_on, j.property.name, str(j.unit) if j.unit_id else "", j.property.owner.name,
            j.tenant.name if j.tenant_id else "", j.title, j.get_category_display(), j.get_priority_display(),
            j.get_status_display(), j.vendor.name if j.vendor_id else "", j.scheduled_on or "", j.completed_on or "",
            j.estimate if j.estimate is not None else "", j.final_cost if j.final_cost is not None else "",
            j.get_paid_by_display(), str(j.approved_by) if j.approved_by_id else "", j.approved_on or "",
            j.rejection_reason, j.notes,
        ]


DECISIONS = [("", "No decision"), ("approve", "Approve this job"), ("reject", "Reject this job")]


class MaintenanceForm(forms.ModelForm):
    decision = forms.ChoiceField(label="Decision", required=False, choices=DECISIONS)
    decision_text = forms.CharField(
        label="Note", required=False, max_length=200,
        help_text="Optional when approving. Required when rejecting: say why.",
    )
    allow_decision = False  # set per request by the admin
    decision_user = None

    class Meta:
        model = MaintenanceRequest
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.allow_decision or self.instance.pk is None:
            self.fields.pop("decision", None)
            self.fields.pop("decision_text", None)
        hints = {
            "title": "e.g. Water leaking from the bathroom ceiling", "estimate": "e.g. 850", "final_cost": "e.g. 850",
        }
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        for name in ("description", "notes"):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = 3
        if "vendor" in self.fields:
            self.fields["vendor"].queryset = Vendor.objects.filter(Q(is_active=True) | Q(pk=self.instance.vendor_id)).order_by("name")
        use_file_widget(self, "invoice", accept=".pdf,.jpg,.jpeg,.png,.webp")

    def _post_clean(self):
        # The decision is applied while the job is being checked, so approving and starting the work in one save works.
        decision = self.cleaned_data.get("decision") if hasattr(self, "cleaned_data") else None
        if decision:
            self.instance._decision = (decision, self.decision_user, self.cleaned_data.get("decision_text", ""))
        super()._post_clean()


@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = MaintenanceForm
    compressed_fields = True
    autocomplete_fields = ["property", "unit", "tenant", "vendor"]
    list_display = ("code", "reported", "where_link", "title", "category_text", "priority_tag", "vendor_text", "cost", "state")
    list_display_links = ("code", "title")
    list_filter = ("status", "priority", "category", "paid_by")
    list_select_related = ("property", "unit", "vendor")
    date_hierarchy = None
    search_fields = ("code", "title", "description", "property__name", "property__code", "unit__unit_number", "vendor__name",
                     "tenant__name")
    search_text = ("code", "title", "description", "property__name", "property__code", "vendor__name", "tenant__name")
    search_codes = (("code", "MNT", 4),)
    search_fuzzy = ("title", "property__name", "vendor__name")
    search_hint = "job number (MNT-12), problem, property, unit, vendor or tenant"
    actions = ["export_excel", "export_csv"]

    _header = (None, {"fields": ("summary_header",)})
    _request = (
        "Request",
        {
            "classes": ["tab"],
            "fields": (
                ("property", "unit"),
                "title",
                "description",
                ("category", "priority"),
                ("reported_on", "tenant"),
                "status",
            ),
        },
    )
    _work = (
        "Work And Cost",
        {
            "classes": ["tab"],
            "description": "Who does the work and what it costs. Above the approval limit the job waits for approval before the work starts.",
            "fields": (
                ("vendor", "paid_by"),
                ("scheduled_on", "completed_on"),
                ("estimate", "final_cost"),
                "invoice",
                "notes",
            ),
        },
    )
    _approval = (
        "Approval",
        {
            "classes": ["tab"],
            "fields": ("approval_summary", "decision", "decision_text"),
        },
    )

    def _can_decide(self, request):
        return request.user.has_perm("maintenance.approve_maintenancerequest")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.allow_decision = self._can_decide(request)
        form.decision_user = request.user
        return form

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (self._request, self._work)
        approval = (
            "Approval",
            {"classes": ["tab"], "fields": ("approval_summary",) + (("decision", "decision_text") if self._can_decide(request) else ())},
        )
        return (self._header, self._request, self._work, approval)

    def get_readonly_fields(self, request, obj=None):
        return ("summary_header", "approval_summary")

    def has_delete_permission(self, request, obj=None):
        # Work that was done is part of the records: cancel the job instead. Only jobs nobody acted on can go.
        if obj is not None and obj.status not in ("new", "awaiting_approval", "cancelled"):
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def quick_chips(self):
        today = timezone.localdate()
        S = MaintenanceRequest.Status
        return [
            ("open", "Open", "info", Q(status__in=OPEN_STATUSES)),
            ("approval", "Awaiting Approval", "warning", Q(status=S.AWAITING)),
            ("progress", "In Progress", "info", Q(status=S.IN_PROGRESS)),
            ("urgent", "Urgent", "danger", Q(status__in=OPEN_STATUSES, priority="urgent")),
            ("overdue", "Overdue", "danger", Q(status__in=OPEN_STATUSES, scheduled_on__lt=today)),
            ("done", "Completed", "success", Q(status=S.COMPLETED)),
            ("closed", "Rejected / Cancelled", "neutral", Q(status__in=(S.REJECTED, S.CANCELLED))),
        ]

    # ---- list columns ----------------------------------------------------------------
    @admin.display(description="Reported", ordering="reported_on")
    def reported(self, obj):
        return fmt_date(obj.reported_on)

    @admin.display(description="Where", ordering="property__name")
    def where_link(self, obj):
        if obj.unit_id:
            return format_html(
                '<a href="{}" style="font-weight:600">{}</a><div style="font-size:12px;opacity:.7">{}</div>',
                reverse("admin:properties_unit_change", args=[obj.unit_id]), str(obj.unit), obj.property.name,
            )
        return format_html(
            '<a href="{}" style="font-weight:600">{}</a>', reverse("admin:properties_property_change", args=[obj.property_id]),
            obj.property.name,
        )

    @admin.display(description="Type", ordering="category")
    def category_text(self, obj):
        return obj.get_category_display()

    @admin.display(description="Priority", ordering="priority")
    def priority_tag(self, obj):
        return priority_badge(obj)

    @admin.display(description="Vendor", ordering="vendor__name")
    def vendor_text(self, obj):
        return obj.vendor.name if obj.vendor_id else badge("Not chosen", "warning")

    @admin.display(description="Cost")
    def cost(self, obj):
        value = obj.final_cost if obj.final_cost is not None else obj.estimate
        if value is None:
            return "-"
        return format_html('{}<div style="font-size:12px;opacity:.7">{} pays{}</div>', money(value), obj.get_paid_by_display(),
                           "" if obj.final_cost is not None else " (estimate)")

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        tag = status_badge(obj)
        if is_overdue(obj):
            return format_html("{} {}", tag, badge("Overdue", "danger"))
        return tag

    # ---- the form header and the approval tab ----------------------------------------------
    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        pills = [status_badge(obj), priority_badge(obj), badge(f"{obj.get_paid_by_display()} pays", PAID_BY_TONES[obj.paid_by])]
        if is_overdue(obj):
            pills.append(badge("Overdue", "danger"))
        if obj.final_cost is not None and not obj.invoice:
            pills.append(badge("Invoice not uploaded", "warning"))
        stats = [
            (money(obj.estimate), "Estimate"),
            (money(obj.final_cost), "Final cost"),
            (f"AED {obj.limit:,.0f}", "Approval limit"),
        ]
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · {}</div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{} · {}</div></div><div class="erp-oh-stats">{}</div></div>',
            obj.code, obj.title, format_html_join("", "{}", ((p,) for p in pills)), obj.where,
            obj.vendor.name if obj.vendor_id else "No vendor yet",
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
        )

    @admin.display(description="Approval")
    def approval_summary(self, obj):
        if obj is None or not obj.pk:
            return "-"
        owner_limit = obj.owner.maintenance_approval_limit if obj.owner is not None else None
        source = "this owner's own limit" if owner_limit is not None else "the company-wide limit (System settings)"
        text = [f"Jobs above AED {obj.limit:,.0f} need approval ({source}). This job costs AED {obj.amount:,.2f}."]
        if obj.status == "rejected":
            text.append(f"Rejected: {obj.rejection_reason}")
        elif obj.approved_on:
            who = str(obj.approved_by) if obj.approved_by_id else "someone"
            text.append(f"Approved by {who} on {fmt_date(obj.approved_on)} for up to AED {obj.approved_amount:,.2f}."
                        + (f" Note: {obj.decision_note}" if obj.decision_note else ""))
        elif obj.waiting_for_approval:
            text.append("Waiting for approval.")
        elif not obj.needs_approval:
            text.append("No approval needed.")
        return " ".join(text)

    # ---- export ------------------------------------------------------------------------
    @admin.action(description="Export selected jobs to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Maintenance requests", JOB_HEADERS, job_export_rows(queryset), "maintenance-requests")

    @admin.action(description="Export selected jobs to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(JOB_HEADERS, job_export_rows(queryset), "maintenance-requests")


# ---------------------------------------------------------------------------------------
# The "Maintenance" tab on the property, unit and tenant pages (read-only; open a job to change it)
# ---------------------------------------------------------------------------------------
class _JobsTab(ReadOnlyInline):
    model = MaintenanceRequest
    tab = True
    verbose_name_plural = "Maintenance"
    show_change_link = False
    fields = ("job", "reported", "title", "vendor_name", "cost", "state", "open_job")
    readonly_fields = fields
    ordering = ("-reported_on", "-id")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("maintenance.view_maintenancerequest")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("vendor")

    @admin.display(description="Job no.")
    def job(self, obj):
        return obj.code

    @admin.display(description="Reported")
    def reported(self, obj):
        return fmt_date(obj.reported_on)

    @admin.display(description="Vendor")
    def vendor_name(self, obj):
        return obj.vendor.name if obj.vendor_id else "-"

    @admin.display(description="Cost")
    def cost(self, obj):
        return money(obj.final_cost if obj.final_cost is not None else obj.estimate)

    @admin.display(description="Status")
    def state(self, obj):
        return status_badge(obj)

    @admin.display(description="")
    def open_job(self, obj):
        return _open_button(obj.pk) if obj.pk else ""


class PropertyJobsInline(_JobsTab):
    fk_name = "property"


class UnitJobsInline(_JobsTab):
    fk_name = "unit"


class TenantJobsInline(_JobsTab):
    fk_name = "tenant"


add_inline_tab(PropertyAdmin, PropertyJobsInline, "PropertyActivityInline")
add_inline_tab(UnitAdmin, UnitJobsInline, "UnitActivityInline")
add_inline_tab(TenantAdmin, TenantJobsInline, "TenantActivityInline")
