"""Receipts and invoices: lists, the print pages, and the two actions on the Tenant Payments list."""
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from core import exports, sysconf
from core.admin_utils import ExportPermissionMixin, TooltipHelpMixin, add_inline_tab, badge
from core.formats import fmt_date
from core.paging import RowsPerPageMixin
from core.search import SmartSearchMixin
from leases.admin import LeasePaymentAdmin
from leases.models import LeasePayment
from properties.owner_admin import ReadOnlyInline
from tenants.admin import TenantAdmin

from .models import Invoice, Receipt, Status, issue_invoice
from .seed import issue_missing_receipts
from .words import amount_in_words


def money(value):
    return f"AED {value:,.2f}"


def status_badge(doc):
    return badge("Issued", "success") if doc.status == Status.ISSUED else badge("Voided", "danger")


def print_button(doc, label="Print"):
    name = f"admin:billing_{type(doc)._meta.model_name}_print"
    return format_html('<a class="erp-qbtn erp-qbtn-primary" href="{}" target="_blank">{}</a>', reverse(name, args=[doc.pk]), label)


class _BillingAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    compressed_fields = True
    list_select_related = ("lease",)
    ordering = ("-issued_on", "-id")
    actions = ["export_excel", "export_csv"]
    readonly_fields = ("summary_header", "code", "issued_on", "tenant_name", "description", "amount", "vat", "total", "voided_on")
    kind = ""  # "receipt" or "invoice"

    def has_add_permission(self, request):
        return False  # they are made from the payments

    def has_delete_permission(self, request, obj=None):
        return False  # a document that was issued is kept: void it instead

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        mine = [path("<path:object_id>/print/", self.admin_site.admin_view(self.print_view), name="%s_%s_print" % info)]
        return mine + super().get_urls()

    # ---- the printable page -------------------------------------------------------------
    def print_view(self, request, object_id):
        doc = self.get_object(request, object_id)
        if doc is None or not self.has_view_permission(request, doc):
            raise Http404
        is_receipt = isinstance(doc, Receipt)
        title = "Receipt" if is_receipt else ("Tax Invoice" if doc.is_tax_invoice and sysconf.get("company_trn") else "Invoice")
        context = {
            **self.admin_site.each_context(request),
            "doc": doc, "title": f"{title} {doc.code}", "heading": title, "is_receipt": is_receipt,
            "company": {key: sysconf.get(f"company_{key}") for key in ("name", "trn", "phone", "email", "address")},
            "words": amount_in_words(doc.total),
            "back": reverse(f"admin:billing_{self.model._meta.model_name}_change", args=[doc.pk]),
            "date": fmt_date(doc.issued_on),
            "due": fmt_date(doc.due_date) if getattr(doc, "due_date", None) else "",
        }
        return TemplateResponse(request, "billing/print.html", context)

    # ---- shared columns -------------------------------------------------------------------
    @admin.display(description="Date", ordering="issued_on")
    def date(self, obj):
        return fmt_date(obj.issued_on)

    @admin.display(description="Tenant", ordering="tenant_name")
    def tenant_text(self, obj):
        return obj.tenant_name

    @admin.display(description="For")
    def for_text(self, obj):
        return obj.description

    @admin.display(description="Total", ordering="total")
    def total_text(self, obj):
        return format_html('{}<div style="font-size:12px;opacity:.7">{}</div>', money(obj.total), f"incl. VAT {obj.vat:,.2f}" if obj.vat else "no VAT")

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return status_badge(obj)

    @admin.display(description="")
    def print_link(self, obj):
        return print_button(obj)

    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · {}</div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{}</div></div>'
            '<div class="erp-oh-stats"><div class="erp-oh-stat"><b>{}</b><span>Total</span></div></div></div>'
            '<p style="margin:12px 0">{}</p>',
            obj.code, obj.tenant_name, status_badge(obj), obj.description, money(obj.total), print_button(obj, "Print / Save as PDF"),
        )


# ---------------------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------------------
RECEIPT_HEADERS = ["Receipt no.", "Date", "Tenant", "For", "Amount (AED)", "VAT (AED)", "Total (AED)", "Paid by", "Status", "Voided on", "Reason", "Notes"]


@admin.register(Receipt)
class ReceiptAdmin(_BillingAdmin):
    list_display = ("code", "date", "tenant_text", "for_text", "total_text", "method_text", "state", "print_link")
    list_display_links = ("code",)
    list_filter = ("status", "method")
    search_fields = ("code", "tenant_name", "description", "cheque_number", "lease__code")
    search_text = ("code", "tenant_name", "description", "cheque_number", "lease__code")
    search_codes = (("code", "RCP", 4),)
    search_fuzzy = ("tenant_name",)
    search_hint = "receipt number (RCP-12), tenant name, lease number, cheque number"
    readonly_fields = _BillingAdmin.readonly_fields + ("method_text",)
    fields = ("summary_header", "code", "issued_on", "tenant_name", "description", "amount", "vat", "total", "method_text", "status",
              "void_reason", "voided_on", "notes")

    @admin.display(description="Paid by", ordering="method")
    def method_text(self, obj):
        return obj.method_text

    def quick_chips(self):
        today = timezone.localdate()
        return [
            ("issued", "Issued", "success", Q(status=Status.ISSUED)),
            ("month", "This Month", "info", Q(status=Status.ISSUED, issued_on__year=today.year, issued_on__month=today.month)),
            ("voided", "Voided", "danger", Q(status=Status.VOIDED)),
        ]

    def export_rows(self, queryset):
        for r in queryset:
            yield [r.code, r.issued_on, r.tenant_name, r.description, r.amount, r.vat, r.total, r.method_text, r.get_status_display(),
                   r.voided_on or "", r.void_reason, r.notes]

    @admin.action(description="Export selected receipts to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Receipts", RECEIPT_HEADERS, self.export_rows(queryset), "receipts")

    @admin.action(description="Export selected receipts to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(RECEIPT_HEADERS, self.export_rows(queryset), "receipts")


# ---------------------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------------------
INVOICE_HEADERS = ["Invoice no.", "Date", "Due on", "Tenant", "For", "Amount (AED)", "VAT (AED)", "Total (AED)", "Payment", "Status", "Reason", "Notes"]
PAYMENT_TONES = {"paid": ("Paid", "success"), "overdue": ("Overdue", "danger"), "open": ("Not paid yet", "warning")}


@admin.register(Invoice)
class InvoiceAdmin(_BillingAdmin):
    list_display = ("code", "date", "due", "tenant_text", "for_text", "total_text", "payment_cell", "state", "print_link")
    list_display_links = ("code",)
    list_filter = ("status",)
    list_select_related = ("lease", "payment")
    search_fields = ("code", "tenant_name", "description", "lease__code")
    search_text = ("code", "tenant_name", "description", "lease__code")
    search_codes = (("code", "INV", 4),)
    search_fuzzy = ("tenant_name",)
    search_hint = "invoice number (INV-12), tenant name, lease number"
    readonly_fields = _BillingAdmin.readonly_fields + ("due_date", "payment_cell")
    fields = ("summary_header", "code", "issued_on", "due_date", "tenant_name", "description", "amount", "vat", "total", "payment_cell",
              "status", "void_reason", "voided_on", "notes")

    @admin.display(description="Due on", ordering="due_date")
    def due(self, obj):
        return fmt_date(obj.due_date) if obj.due_date else "-"

    @admin.display(description="Payment")
    def payment_cell(self, obj):
        if obj is None or not obj.pk or obj.status == Status.VOIDED:
            return "-"
        label, tone = PAYMENT_TONES[obj.payment_state]
        return badge(label, tone)

    def quick_chips(self):
        today = timezone.localdate()
        unpaid = Q(status=Status.ISSUED) & ~Q(payment__status__in=("paid", "waived"))
        return [
            ("open", "Not Paid Yet", "warning", unpaid),
            ("overdue", "Overdue", "danger", unpaid & Q(payment__due_date__lt=today)),
            ("paid", "Paid", "success", Q(status=Status.ISSUED, payment__status="paid")),
            ("voided", "Voided", "neutral", Q(status=Status.VOIDED)),
        ]

    def export_rows(self, queryset):
        for i in queryset.select_related("payment"):
            yield [i.code, i.issued_on, i.due_date or "", i.tenant_name, i.description, i.amount, i.vat, i.total,
                   PAYMENT_TONES[i.payment_state][0] if i.status == Status.ISSUED else "", i.get_status_display(), i.void_reason, i.notes]

    @admin.action(description="Export selected invoices to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Invoices", INVOICE_HEADERS, self.export_rows(queryset), "invoices")

    @admin.action(description="Export selected invoices to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(INVOICE_HEADERS, self.export_rows(queryset), "invoices")


# ---------------------------------------------------------------------------------------
# Two actions on the Tenant Payments list: make the receipts / invoices of the ticked payments
# ---------------------------------------------------------------------------------------
@admin.action(description="Issue receipts for the selected payments", permissions=["issue_receipts"])
def issue_receipts_action(modeladmin, request, queryset):
    made = issue_missing_receipts(queryset)
    skipped = queryset.exclude(status=LeasePayment.Status.PAID).count()
    text = f"{made} receipt(s) issued." if made else "Nothing to issue: every paid payment you ticked already has its receipt."
    if skipped:
        text += f" {skipped} ticked payment(s) are not paid yet, so they get no receipt."
    modeladmin.message_user(request, text, messages.SUCCESS if made else messages.INFO)


@admin.action(description="Create invoices for the selected payments", permissions=["issue_invoices"])
def create_invoices_action(modeladmin, request, queryset):
    made, had, refused = 0, 0, 0
    for payment in queryset.select_related("lease__tenant", "lease__unit__property", "lease__property"):
        try:
            _, created = issue_invoice(payment)
        except ValidationError:
            refused += 1
            continue
        made += created
        had += not created
    parts = [f"{made} invoice(s) created."]
    if had:
        parts.append(f"{had} already had an invoice.")
    if refused:
        parts.append(f"{refused} skipped (refunds, deductions and waived payments are not invoiced).")
    modeladmin.message_user(request, " ".join(parts), messages.SUCCESS if made else messages.INFO)


def _can(*codenames):
    return lambda self, request: all(request.user.has_perm(f"billing.{c}") for c in codenames)


LeasePaymentAdmin.has_issue_receipts_permission = _can("add_receipt")
LeasePaymentAdmin.has_issue_invoices_permission = _can("add_invoice")
if issue_receipts_action not in LeasePaymentAdmin.actions:
    LeasePaymentAdmin.actions = list(LeasePaymentAdmin.actions) + [issue_receipts_action, create_invoices_action]


# ---------------------------------------------------------------------------------------
# Tabs on the tenant page (read-only; open the document to print it)
# ---------------------------------------------------------------------------------------
class _DocsTab(ReadOnlyInline):
    tab = True
    show_change_link = False
    fields = ("number", "date", "for_text", "total_text", "state", "open_doc")
    readonly_fields = fields
    ordering = ("-issued_on", "-id")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(f"billing.view_{self.model._meta.model_name}")

    @admin.display(description="No.")
    def number(self, obj):
        return obj.code

    @admin.display(description="Date")
    def date(self, obj):
        return fmt_date(obj.issued_on)

    @admin.display(description="For")
    def for_text(self, obj):
        return obj.description

    @admin.display(description="Total")
    def total_text(self, obj):
        return money(obj.total)

    @admin.display(description="Status")
    def state(self, obj):
        return status_badge(obj)

    @admin.display(description="")
    def open_doc(self, obj):
        return print_button(obj) if obj.pk else ""


class TenantReceiptsInline(_DocsTab):
    model = Receipt
    fk_name = "tenant"
    verbose_name_plural = "Receipts"


class TenantInvoicesInline(_DocsTab):
    model = Invoice
    fk_name = "tenant"
    verbose_name_plural = "Invoices"


add_inline_tab(TenantAdmin, TenantReceiptsInline, "TenantActivityInline")
add_inline_tab(TenantAdmin, TenantInvoicesInline, "TenantActivityInline")
