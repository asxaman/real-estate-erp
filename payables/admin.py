"""Vendor bills, expenses and payment vouchers."""
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import F, Q
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
from core.widgets import use_file_widget
from maintenance.admin import MaintenanceRequestAdmin, VendorAdmin
from properties.owner_admin import ReadOnlyInline

from billing.words import amount_in_words

from .models import Expense, PaymentVoucher, VendorBill, create_bill_for_job

FILE_ACCEPT = ".pdf,.jpg,.jpeg,.png,.webp"
STATE_TONES = {
    "paid": ("Paid", "success"), "part": ("Part Paid", "info"), "unpaid": ("Not Paid", "warning"),
    "overdue": ("Overdue", "danger"), "cancelled": ("Cancelled", "neutral"),
}


def money(value):
    return f"AED {value:,.2f}"


def where_cell(obj):
    if obj.unit_id:
        return format_html('<b>{}</b><div style="font-size:12px;opacity:.7">{}</div>', str(obj.unit), obj.property.name)
    return obj.property.name if obj.property_id else "Company"


def print_button(voucher, label="Print"):
    return format_html('<a class="erp-qbtn erp-qbtn-primary" href="{}" target="_blank">{}</a>',
                       reverse("admin:payables_paymentvoucher_print", args=[voucher.pk]), label)


class _CostForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("notes",):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = 3
        if "amount" in self.fields:
            self.fields["amount"].widget.attrs["placeholder"] = "e.g. 850"
        use_file_widget(self, "file", accept=FILE_ACCEPT)


class BillForm(_CostForm):
    class Meta:
        model = VendorBill
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "description" in self.fields:
            self.fields["description"].widget.attrs["placeholder"] = "e.g. Plumbing repair, unit 102"
        if "bill_number" in self.fields:
            self.fields["bill_number"].widget.attrs["placeholder"] = "as printed on the vendor's invoice"


class ExpenseForm(_CostForm):
    class Meta:
        model = Expense
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "payee" in self.fields:
            self.fields["payee"].widget.attrs["placeholder"] = "e.g. DEWA"
        if "description" in self.fields:
            self.fields["description"].widget.attrs["placeholder"] = "e.g. Electricity, October"


# ---------------------------------------------------------------------------------------
# Vendor bills
# ---------------------------------------------------------------------------------------
BILL_HEADERS = ["Bill no.", "Bill date", "Due on", "Vendor", "Vendor's invoice no.", "Type", "Property", "Unit", "Description", "Charged to",
                "Amount (AED)", "VAT (AED)", "Total (AED)", "Paid (AED)", "Balance (AED)", "State", "Notes"]


@admin.register(VendorBill)
class VendorBillAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = BillForm
    compressed_fields = True
    autocomplete_fields = ["vendor", "property", "unit", "tenant", "request"]
    list_display = ("code", "bill_date_text", "vendor_text", "invoice_no", "where_text", "total_text", "paid_text", "due", "state")
    list_display_links = ("code",)
    list_filter = ("status", "category", "charged_to")
    list_select_related = ("vendor", "property", "unit")
    search_fields = ("code", "bill_number", "description", "vendor__name", "property__name", "property__code", "request__code")
    search_text = ("code", "bill_number", "description", "vendor__name", "property__name", "property__code", "request__code")
    search_codes = (("code", "BILL", 4),)
    search_fuzzy = ("vendor__name",)
    search_hint = "bill number (BILL-12), vendor, vendor's invoice number, property or job number"
    actions = ["pay_in_full", "export_excel", "export_csv"]
    readonly_fields = ("summary_header", "code", "vat", "total", "paid_amount")

    _header = (None, {"fields": ("summary_header",)})
    _bill = (
        "Bill",
        {
            "classes": ["tab"],
            "fields": (
                ("vendor", "bill_number"),
                ("bill_date", "due_date"),
                "description",
                ("category", "request"),
                ("amount", "vat_applies"),
                "file",
                "notes",
            ),
        },
    )
    _charge = (
        "Who Pays",
        {
            "classes": ["tab"],
            "description": "Which property the cost belongs to and who bears it. A cost charged to the owner goes on the owner's statement.",
            "fields": (("property", "unit"), ("charged_to", "tenant")),
        },
    )
    _cancel = (
        "Cancelling",
        {"classes": ["tab"], "description": "Only a bill with no payments can be cancelled.", "fields": ("status", "cancel_reason")},
    )

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (self._bill, self._charge)
        return (self._header, self._bill, self._charge, self._cancel)

    def has_delete_permission(self, request, obj=None):
        return False  # a bill that was entered is kept: cancel it instead

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        if not request.user.has_perm("payables.add_paymentvoucher"):
            actions.pop("pay_in_full", None)
        return actions

    def quick_chips(self):
        today = timezone.localdate()
        owing = Q(status="open", paid_amount__lt=F("total"))
        return [
            ("owing", "To Pay", "warning", owing),
            ("overdue", "Overdue", "danger", owing & Q(due_date__lt=today)),
            ("week", "Due In 7 Days", "info", owing & Q(due_date__gte=today, due_date__lte=today + timedelta(days=7))),
            ("part", "Part Paid", "info", owing & Q(paid_amount__gt=0)),
            ("paid", "Paid", "success", Q(status="open", paid_amount__gte=F("total"))),
            ("cancelled", "Cancelled", "neutral", Q(status="cancelled")),
        ]

    # ---- columns -------------------------------------------------------------------------
    @admin.display(description="Date", ordering="bill_date")
    def bill_date_text(self, obj):
        return fmt_date(obj.bill_date)

    @admin.display(description="Vendor", ordering="vendor__name")
    def vendor_text(self, obj):
        return obj.vendor.name

    @admin.display(description="Invoice no.", ordering="bill_number")
    def invoice_no(self, obj):
        return obj.bill_number or "-"

    @admin.display(description="Where", ordering="property__name")
    def where_text(self, obj):
        return where_cell(obj)

    @admin.display(description="Total", ordering="total")
    def total_text(self, obj):
        return format_html('{}<div style="font-size:12px;opacity:.7">{}</div>', money(obj.total), f"incl. VAT {obj.vat:,.2f}" if obj.vat else "no VAT")

    @admin.display(description="Paid", ordering="paid_amount")
    def paid_text(self, obj):
        return format_html('{}<div style="font-size:12px;opacity:.7">left {}</div>', money(obj.paid_amount), f"{obj.balance:,.2f}")

    @admin.display(description="Due on", ordering="due_date")
    def due(self, obj):
        return fmt_date(obj.due_date) if obj.due_date else "-"

    @admin.display(description="State")
    def state(self, obj):
        label, tone = STATE_TONES[obj.payment_state()]
        return badge(label, tone)

    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        label, tone = STATE_TONES[obj.payment_state()]
        pay = ""
        if obj.status == "open" and obj.balance > 0:
            pay = format_html('<a class="erp-qbtn erp-qbtn-primary" href="{}?bill={}">Record a payment</a>',
                              reverse("admin:payables_paymentvoucher_add"), obj.pk)
        pills = [badge(label, tone)]
        if not obj.file:
            pills.append(badge("Invoice scan not uploaded", "warning"))
        stats = [(money(obj.total), "Total"), (money(obj.paid_amount), "Paid"), (money(obj.balance), "Left to pay")]
        from django.utils.html import format_html_join

        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · {}</div><div class="erp-oh-pills">{}</div>'
            '<div class="erp-oh-sub">{}</div></div><div class="erp-oh-stats">{}</div></div><p style="margin:12px 0">{}</p>',
            obj.code, obj.vendor.name, format_html_join("", "{}", ((p,) for p in pills)), obj.description,
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats), pay,
        )

    # ---- actions -------------------------------------------------------------------------
    @admin.action(description="Pay the selected bills in full (today, bank transfer)", permissions=["pay"])
    def pay_in_full(self, request, queryset):
        made = skipped = 0
        for bill in queryset.filter(status="open").select_related("vendor"):
            if bill.balance <= 0:
                skipped += 1
                continue
            voucher = PaymentVoucher(bill=bill, amount=bill.balance, method="transfer", voucher_date=timezone.localdate())
            try:
                voucher.full_clean()
            except ValidationError:
                skipped += 1
                continue
            voucher.save()
            made += 1
        text = f"{made} payment voucher(s) made."
        if skipped:
            text += f" {skipped} bill(s) skipped (already paid or cancelled)."
        self.message_user(request, text, messages.SUCCESS if made else messages.INFO)

    def has_pay_permission(self, request):
        return request.user.has_perm("payables.add_paymentvoucher")

    def export_rows(self, queryset):
        for b in queryset.select_related("vendor", "property", "unit"):
            yield [b.code, b.bill_date, b.due_date or "", b.vendor.name, b.bill_number, b.get_category_display(), b.property.name if b.property_id else "",
                   str(b.unit) if b.unit_id else "", b.description, b.get_charged_to_display(), b.amount, b.vat, b.total, b.paid_amount, b.balance,
                   STATE_TONES[b.payment_state()][0], b.notes]

    @admin.action(description="Export selected bills to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Vendor bills", BILL_HEADERS, self.export_rows(queryset), "vendor-bills")

    @admin.action(description="Export selected bills to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(BILL_HEADERS, self.export_rows(queryset), "vendor-bills")


# ---------------------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------------------
EXPENSE_HEADERS = ["Expense no.", "Paid on", "Paid to", "Type", "Description", "Property", "Unit", "Charged to", "Amount (AED)", "VAT (AED)",
                   "Total (AED)", "Paid by", "Reference", "Cancelled", "Notes"]


@admin.register(Expense)
class ExpenseAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = ExpenseForm
    compressed_fields = True
    autocomplete_fields = ["property", "unit", "tenant"]
    list_display = ("code", "date_text", "payee_text", "category_text", "description", "where_text", "charged_text", "total_text", "state")
    list_display_links = ("code",)
    list_filter = ("category", "charged_to", "method", "cancelled")
    list_select_related = ("property", "unit")
    search_fields = ("code", "payee", "description", "reference", "property__name", "property__code")
    search_text = ("code", "payee", "description", "reference", "property__name", "property__code")
    search_codes = (("code", "EXP", 4),)
    search_fuzzy = ("payee",)
    search_hint = "expense number (EXP-12), who was paid, description, reference or property"
    actions = ["export_excel", "export_csv"]
    readonly_fields = ("summary_header", "code", "vat", "total")

    _header = (None, {"fields": ("summary_header",)})
    _expense = (
        "Expense",
        {
            "classes": ["tab"],
            "fields": (
                ("expense_date", "payee"),
                "description",
                ("category", "method"),
                "reference",
                ("amount", "vat_applies"),
                "file",
                "notes",
            ),
        },
    )
    _charge = (
        "Who Pays",
        {
            "classes": ["tab"],
            "description": "Which property the cost belongs to and who bears it. A cost charged to the owner goes on the owner's statement.",
            "fields": (("property", "unit"), ("charged_to", "tenant")),
        },
    )
    _cancel = (
        "Cancelling",
        {"classes": ["tab"], "description": "A wrong expense is cancelled, not deleted. Its payment voucher is voided.", "fields": ("cancelled", "cancel_reason")},
    )

    def get_fieldsets(self, request, obj=None):
        return (self._expense, self._charge) if obj is None else (self._header, self._expense, self._charge, self._cancel)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def quick_chips(self):
        today = timezone.localdate()
        live = Q(cancelled=False)
        return [
            ("month", "This Month", "info", live & Q(expense_date__year=today.year, expense_date__month=today.month)),
            ("company", "Company Costs", "neutral", live & Q(charged_to="company")),
            ("owner", "Charged To Owners", "warning", live & Q(charged_to="owner")),
            ("tenant", "Charged To Tenants", "warning", live & Q(charged_to="tenant")),
            ("cancelled", "Cancelled", "danger", Q(cancelled=True)),
        ]

    @admin.display(description="Paid on", ordering="expense_date")
    def date_text(self, obj):
        return fmt_date(obj.expense_date)

    @admin.display(description="Paid to", ordering="payee")
    def payee_text(self, obj):
        return obj.payee

    @admin.display(description="Type", ordering="category")
    def category_text(self, obj):
        return obj.get_category_display().split(" (")[0]

    @admin.display(description="Where", ordering="property__name")
    def where_text(self, obj):
        return where_cell(obj)

    @admin.display(description="Charged to", ordering="charged_to")
    def charged_text(self, obj):
        return obj.get_charged_to_display()

    @admin.display(description="Total", ordering="total")
    def total_text(self, obj):
        return format_html('{}<div style="font-size:12px;opacity:.7">{}</div>', money(obj.total), f"incl. VAT {obj.vat:,.2f}" if obj.vat else "no VAT")

    @admin.display(description="State", ordering="cancelled")
    def state(self, obj):
        return badge("Cancelled", "danger") if obj.cancelled else badge("Paid", "success")

    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        voucher = obj.vouchers.filter(status="issued").first()
        link = print_button(voucher, f"Voucher {voucher.code}") if voucher else ""
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · {}</div><div class="erp-oh-pills">{}</div>'
            '<div class="erp-oh-sub">{}</div></div><div class="erp-oh-stats"><div class="erp-oh-stat"><b>{}</b><span>Total</span></div></div></div>'
            '<p style="margin:12px 0">{}</p>',
            obj.code, obj.payee, self.state(obj), obj.description, money(obj.total), link,
        )

    def export_rows(self, queryset):
        for e in queryset.select_related("property", "unit"):
            yield [e.code, e.expense_date, e.payee, e.get_category_display(), e.description, e.property.name if e.property_id else "",
                   str(e.unit) if e.unit_id else "", e.get_charged_to_display(), e.amount, e.vat, e.total, e.get_method_display(), e.reference,
                   "Yes" if e.cancelled else "", e.notes]

    @admin.action(description="Export selected expenses to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Expenses", EXPENSE_HEADERS, self.export_rows(queryset), "expenses")

    @admin.action(description="Export selected expenses to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(EXPENSE_HEADERS, self.export_rows(queryset), "expenses")


# ---------------------------------------------------------------------------------------
# Payment vouchers
# ---------------------------------------------------------------------------------------
VOUCHER_HEADERS = ["Voucher no.", "Date", "Paid to", "For", "Amount (AED)", "Paid by", "Bill", "Expense", "Status", "Reason", "Notes"]
LOCKED = ("code", "voucher_date", "bill", "payee_name", "description", "amount", "method", "cheque_number", "bank_name", "reference")


class VoucherForm(forms.ModelForm):
    class Meta:
        model = PaymentVoucher
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hints = {"payee_name": "Filled in from the bill; type a name when there is no bill", "amount": "e.g. 850", "cheque_number": "for cheques"}
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text


@admin.register(PaymentVoucher)
class PaymentVoucherAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = VoucherForm
    compressed_fields = True
    autocomplete_fields = ["bill"]
    list_display = ("code", "date_text", "payee_text", "for_text", "total_text", "method_text", "state", "print_link")
    list_display_links = ("code",)
    list_filter = ("status", "method")
    list_select_related = ("bill", "expense")
    search_fields = ("code", "payee_name", "description", "cheque_number", "reference", "bill__code", "expense__code")
    search_text = ("code", "payee_name", "description", "cheque_number", "reference", "bill__code", "expense__code")
    search_codes = (("code", "PV", 4),)
    search_fuzzy = ("payee_name",)
    search_hint = "voucher number (PV-12), who was paid, bill or expense number, cheque number"
    actions = ["export_excel", "export_csv"]

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        mine = [path("<path:object_id>/print/", self.admin_site.admin_view(self.print_view), name="%s_%s_print" % info)]
        return mine + super().get_urls()

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("bill", "voucher_date", "payee_name", "description", "amount", "method", "cheque_number", "bank_name", "reference", "notes")
        return ("summary_header", "code", "voucher_date", "bill", "payee_name", "description", "amount", "method", "cheque_number", "bank_name",
                "reference", "status", "void_reason", "voided_on", "notes")

    def get_readonly_fields(self, request, obj=None):
        return ("summary_header", "voided_on") + LOCKED if obj is not None else ()

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        bill = VendorBill.objects.filter(pk=request.GET.get("bill") or 0).select_related("vendor").first()
        if bill is not None and bill.status == "open":
            initial.update(bill=bill.pk, amount=bill.balance, payee_name=bill.vendor.name)
        return initial

    def has_delete_permission(self, request, obj=None):
        return False  # a voucher is kept: void it instead

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def quick_chips(self):
        today = timezone.localdate()
        return [
            ("issued", "Issued", "success", Q(status="issued")),
            ("month", "This Month", "info", Q(status="issued", voucher_date__year=today.year, voucher_date__month=today.month)),
            ("bills", "For Vendor Bills", "neutral", Q(status="issued", bill__isnull=False)),
            ("expenses", "For Expenses", "neutral", Q(status="issued", expense__isnull=False)),
            ("voided", "Voided", "danger", Q(status="voided")),
        ]

    @admin.display(description="Date", ordering="voucher_date")
    def date_text(self, obj):
        return fmt_date(obj.voucher_date)

    @admin.display(description="Paid to", ordering="payee_name")
    def payee_text(self, obj):
        return obj.payee_name

    @admin.display(description="For")
    def for_text(self, obj):
        return obj.description

    @admin.display(description="Amount", ordering="amount")
    def total_text(self, obj):
        return money(obj.amount)

    @admin.display(description="Paid by", ordering="method")
    def method_text(self, obj):
        return obj.method_text

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return badge("Issued", "success") if obj.status == "issued" else badge("Voided", "danger")

    @admin.display(description="")
    def print_link(self, obj):
        return print_button(obj)

    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · {}</div><div class="erp-oh-pills">{}</div>'
            '<div class="erp-oh-sub">{}</div></div><div class="erp-oh-stats"><div class="erp-oh-stat"><b>{}</b><span>Amount</span></div></div></div>'
            '<p style="margin:12px 0">{}</p>',
            obj.code, obj.payee_name, self.state(obj), obj.description, money(obj.amount), print_button(obj, "Print / Save as PDF"),
        )

    def print_view(self, request, object_id):
        voucher = self.get_object(request, object_id)
        if voucher is None or not self.has_view_permission(request, voucher):
            raise Http404
        context = {
            **self.admin_site.each_context(request),
            "doc": voucher, "title": f"Payment voucher {voucher.code}",
            "company": {key: sysconf.get(f"company_{key}") for key in ("name", "trn", "phone", "email", "address")},
            "words": amount_in_words(voucher.amount), "date": fmt_date(voucher.voucher_date),
            "back": reverse("admin:payables_paymentvoucher_change", args=[voucher.pk]),
            "bill": voucher.bill, "expense": voucher.expense,
        }
        return TemplateResponse(request, "payables/print.html", context)

    def export_rows(self, queryset):
        for v in queryset.select_related("bill", "expense"):
            yield [v.code, v.voucher_date, v.payee_name, v.description, v.amount, v.method_text, v.bill.code if v.bill_id else "",
                   v.expense.code if v.expense_id else "", v.get_status_display(), v.void_reason, v.notes]

    @admin.action(description="Export selected vouchers to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Payment vouchers", VOUCHER_HEADERS, self.export_rows(queryset), "payment-vouchers")

    @admin.action(description="Export selected vouchers to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(VOUCHER_HEADERS, self.export_rows(queryset), "payment-vouchers")


# ---------------------------------------------------------------------------------------
# On other pages: "Create vendor bills" on the maintenance list, and a Bills tab on the vendor page
# ---------------------------------------------------------------------------------------
@admin.action(description="Create vendor bills for the selected jobs", permissions=["bill"])
def create_bills_action(modeladmin, request, queryset):
    made = had = refused = 0
    for job in queryset.select_related("vendor", "property", "unit", "tenant"):
        try:
            _, created = create_bill_for_job(job)
        except ValidationError:
            refused += 1
            continue
        made += created
        had += not created
    parts = [f"{made} vendor bill(s) created."]
    if had:
        parts.append(f"{had} already had a bill.")
    if refused:
        parts.append(f"{refused} skipped (a job must be completed, with a vendor and a final cost above zero).")
    modeladmin.message_user(request, " ".join(parts), messages.SUCCESS if made else messages.INFO)


MaintenanceRequestAdmin.has_bill_permission = lambda self, request: request.user.has_perm("payables.add_vendorbill")
if create_bills_action not in MaintenanceRequestAdmin.actions:
    MaintenanceRequestAdmin.actions = list(MaintenanceRequestAdmin.actions) + [create_bills_action]


class VendorBillsInline(ReadOnlyInline):
    model = VendorBill
    fk_name = "vendor"
    tab = True
    verbose_name_plural = "Bills"
    show_change_link = False
    fields = ("number", "bill_date_text", "description", "total_text", "state", "open_bill")
    readonly_fields = fields
    ordering = ("-bill_date", "-id")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("payables.view_vendorbill")

    @admin.display(description="Bill no.")
    def number(self, obj):
        return obj.code

    @admin.display(description="Date")
    def bill_date_text(self, obj):
        return fmt_date(obj.bill_date)

    @admin.display(description="Total")
    def total_text(self, obj):
        return money(obj.total)

    @admin.display(description="State")
    def state(self, obj):
        label, tone = STATE_TONES[obj.payment_state()]
        return badge(label, tone)

    @admin.display(description="")
    def open_bill(self, obj):
        return format_html('<a class="erp-qbtn erp-qbtn-primary" href="{}">Open bill</a>', reverse("admin:payables_vendorbill_change", args=[obj.pk])) if obj.pk else ""


add_inline_tab(VendorAdmin, VendorBillsInline, "")
