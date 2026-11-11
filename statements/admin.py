"""Owner statements: list, the statement page, approving and paying out, and the printable statement."""
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin

from billing.words import amount_in_words
from core import exports, sysconf
from core.admin_utils import ExportPermissionMixin, TooltipHelpMixin, add_inline_tab, badge
from core.formats import fmt_date
from core.paging import RowsPerPageMixin
from core.search import SmartSearchMixin
from properties.owner_admin import OwnerAdmin, ReadOnlyInline

from .models import Section, OwnerStatement, default_period

STATUS_TONES = {"draft": "warning", "approved": "info", "paid": "success", "voided": "neutral"}
SECTION_ORDER = ("income", "guarantee", "fee", "cost", "info")
HEADERS = ["Statement no.", "Owner", "From", "To", "Rent received (AED)", "Guaranteed rent (AED)", "Management fees (AED)", "Costs charged (AED)",
           "Adjustment (AED)", "Net for the period (AED)", "Brought forward (AED)", "To pay the owner (AED)", "Carried forward (AED)", "Status",
           "Paid on", "Notes"]


def money(value):
    return f"AED {value:,.2f}"


def status_badge(obj):
    return badge(obj.get_status_display(), STATUS_TONES[obj.status])


def period_text(obj):
    return f"{fmt_date(obj.period_start)} → {fmt_date(obj.period_end)}"


class StatementForm(forms.ModelForm):
    class Meta:
        model = OwnerStatement
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk is None:
            start, end = default_period()
            self.fields["period_start"].initial = start
            self.fields["period_end"].initial = end
        for name in ("notes",):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = 3
        if "adjustment_note" in self.fields:
            self.fields["adjustment_note"].widget.attrs["placeholder"] = "e.g. Advance paid on 3 March"
        if "payout_reference" in self.fields:
            self.fields["payout_reference"].widget.attrs["placeholder"] = "cheque number or transfer reference"


@admin.register(OwnerStatement)
class OwnerStatementAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = StatementForm
    compressed_fields = True
    autocomplete_fields = ["owner"]
    list_display = ("code", "owner_link", "period", "rent_text", "fees_text", "costs_text", "payout_text", "state")
    list_display_links = ("code",)
    list_filter = ("status",)
    list_select_related = ("owner",)
    search_fields = ("code", "owner__name", "owner__code")
    search_text = ("code", "owner__name", "owner__code")
    search_codes = (("code", "STM", 4),)
    search_fuzzy = ("owner__name",)
    search_hint = "statement number (STM-12), owner name or code"
    actions = ["approve_selected", "rebuild_selected", "export_excel", "export_csv"]

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        mine = [path("<path:object_id>/print/", self.admin_site.admin_view(self.print_view), name="%s_%s_print" % info)]
        return mine + super().get_urls()

    # ---- the form ---------------------------------------------------------------------------------
    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {"fields": ("owner", ("period_start", "period_end"), "notes"),
                            "description": "Choose the owner and the period. The statement is built from the rent received, the management "
                            "fees and the costs charged to the owner that are not on an earlier statement."}),)
        fieldsets = [(None, {"fields": ("statement_view",)})]
        if obj.status == "draft":
            fieldsets.append(("Adjustment And Status", {"classes": ["tab"], "fields": (("adjustment", "adjustment_note"), "status", "void_reason", "notes")}))
        elif obj.status == "approved":
            fieldsets.append(("Payout", {"classes": ["tab"], "description": "To record the payout, set Status to Paid and fill in how it was paid. "
                              "A payment voucher is made for you.", "fields": (("payout_method", "payout_reference"), "paid_on", "status", "void_reason", "notes")}))
        else:
            fieldsets.append(("Details", {"classes": ["tab"], "fields": ("payout_method", "payout_reference", "paid_on", "void_reason", "notes")}))
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        locked = ["statement_view", "owner", "period_start", "period_end"]
        if obj.status != "draft":
            locked += ["adjustment", "adjustment_note"]
        if obj.status in ("paid", "voided"):
            locked += ["status", "payout_method", "payout_reference", "paid_on", "void_reason"]
        if not request.user.has_perm("statements.approve_ownerstatement"):
            locked += ["status", "payout_method", "payout_reference", "paid_on", "void_reason"]  # approving, paying and voiding need the permission
        return locked

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data and obj.status == "approved":
            obj.approved_by, obj.approved_on = request.user, timezone.localdate()
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False  # a statement is kept: void it instead

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        if not request.user.has_perm("statements.approve_ownerstatement"):
            actions.pop("approve_selected", None)
        return actions

    def response_post_save_add(self, request, obj):
        # a new statement opens straight away so it can be checked
        from django.http import HttpResponseRedirect

        self.message_user(request, f"Statement {obj.code} was built. Check it, then approve it.", messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:statements_ownerstatement_change", args=[obj.pk]))

    # ---- chips and columns ------------------------------------------------------------------------
    def quick_chips(self):
        return [
            ("draft", "Draft", "warning", Q(status="draft")),
            ("topay", "Approved (To Pay)", "info", Q(status="approved")),
            ("paid", "Paid", "success", Q(status="paid")),
            ("voided", "Voided", "neutral", Q(status="voided")),
        ]

    @admin.display(description="Owner", ordering="owner__name")
    def owner_link(self, obj):
        return format_html('<a href="{}" style="font-weight:600">{}</a>', reverse("admin:properties_owner_change", args=[obj.owner_id]), obj.owner.name)

    @admin.display(description="Period", ordering="period_end")
    def period(self, obj):
        return period_text(obj)

    @admin.display(description="Rent", ordering="rent_total")
    def rent_text(self, obj):
        return money(obj.rent_total + obj.guarantee_total)

    @admin.display(description="Fees", ordering="fee_total")
    def fees_text(self, obj):
        return money(obj.fee_total)

    @admin.display(description="Costs", ordering="cost_total")
    def costs_text(self, obj):
        return money(obj.cost_total)

    @admin.display(description="To pay the owner", ordering="payout_due")
    def payout_text(self, obj):
        text = money(obj.payout_due)
        if obj.carried_forward < 0:
            return format_html('{}<div style="font-size:12px;color:#b91c1c">owner owes AED {}</div>', text, f"{-obj.carried_forward:,.2f}")
        return text

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return status_badge(obj)

    # ---- the statement on the page --------------------------------------------------------------------
    @admin.display(description="")
    def statement_view(self, obj):
        if obj is None or not obj.pk:
            return ""
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · <a href="{}">{}</a></div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{}</div></div><div class="erp-oh-stats">{}</div></div>{}'
            '<p style="margin:12px 0"><a class="erp-qbtn erp-qbtn-primary" href="{}" target="_blank">Print / Save as PDF</a></p>',
            obj.code, reverse("admin:properties_owner_change", args=[obj.owner_id]), obj.owner.name, status_badge(obj), period_text(obj),
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', [
                (money(obj.rent_total + obj.guarantee_total), "Income"), (money(obj.fee_total + obj.cost_total), "Deductions"),
                (money(obj.payout_due), "To pay the owner")]),
            self.lines_table(obj), reverse("admin:statements_ownerstatement_print", args=[obj.pk]),
        )

    def lines_table(self, obj):
        sections = {key: [] for key in SECTION_ORDER}
        for line in obj.lines.all():
            sections[line.section].append(line)
        rows = []
        for key in SECTION_ORDER:
            lines = sections[key]
            if not lines:
                continue
            total = sum((l.amount for l in lines), 0)
            rows.append(format_html('<tr style="background:rgba(127,127,127,.12)"><td colspan="3"><b>{}</b></td><td style="text-align:right"><b>{}</b></td></tr>',
                                    dict(Section.choices)[key], "" if key == "info" else f"{total:,.2f}"))
            rows.extend(format_html('<tr><td style="white-space:nowrap">{}</td><td>{}</td><td style="text-align:right;opacity:.6">{}</td><td style="text-align:right">{}</td></tr>',
                                    fmt_date(l.line_date) if l.line_date else "", l.description, "" if key != "info" else "memo", f"{l.amount:,.2f}") for l in lines)
        if obj.adjustment:
            rows.append(format_html('<tr><td></td><td>Adjustment: {}</td><td></td><td style="text-align:right">{}</td></tr>', obj.adjustment_note, f"{obj.adjustment:,.2f}"))
        if obj.brought_forward:
            rows.append(format_html('<tr><td></td><td>Brought forward from the previous statement</td><td></td><td style="text-align:right">{}</td></tr>', obj.brought_forward and f"{obj.brought_forward:,.2f}"))
        if not rows:
            return format_html('<p style="margin:12px 0;opacity:.7">Nothing to list for this period yet.</p>')
        footer = format_html(
            '<tr style="border-top:2px solid"><td colspan="3"><b>To pay the owner</b></td><td style="text-align:right"><b>{}</b></td></tr>', f"{obj.payout_due:,.2f}")
        if obj.carried_forward < 0:
            footer += format_html('<tr><td colspan="3">The owner owes the company (carried to the next statement)</td><td style="text-align:right">{}</td></tr>', f"{obj.carried_forward:,.2f}")
        return format_html('<table style="width:100%;margin-top:12px;border-collapse:collapse"><tbody>{}{}</tbody></table>',
                           format_html_join("", "{}", ((r,) for r in rows)), footer)

    # ---- actions ------------------------------------------------------------------------------------
    @admin.action(description="Approve the selected draft statements", permissions=["approve"])
    def approve_selected(self, request, queryset):
        done = skipped = 0
        for statement in queryset.filter(status="draft"):
            statement.status = "approved"
            try:
                statement.full_clean()
            except ValidationError:
                skipped += 1
                continue
            statement.approved_by, statement.approved_on = request.user, timezone.localdate()
            statement.save()
            done += 1
        self.message_user(request, f"{done} statement(s) approved." + (f" {skipped} skipped (empty or not drafts)." if skipped else ""),
                          messages.SUCCESS if done else messages.INFO)

    def has_approve_permission(self, request):
        return request.user.has_perm("statements.approve_ownerstatement")

    @admin.action(description="Rebuild the selected draft statements (pick up new payments, bills and expenses)", permissions=["change"])
    def rebuild_selected(self, request, queryset):
        done = 0
        for statement in queryset.filter(status="draft"):
            statement.generate()
            done += 1
        self.message_user(request, f"{done} draft statement(s) rebuilt.", messages.SUCCESS if done else messages.INFO)

    def export_rows(self, queryset):
        for s in queryset.select_related("owner"):
            yield [s.code, s.owner.name, s.period_start, s.period_end, s.rent_total, s.guarantee_total, s.fee_total, s.cost_total, s.adjustment,
                   s.net_amount, s.brought_forward, s.payout_due, s.carried_forward, s.get_status_display(), s.paid_on or "", s.notes]

    @admin.action(description="Export selected statements to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Owner statements", HEADERS, self.export_rows(queryset), "owner-statements")

    @admin.action(description="Export selected statements to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(HEADERS, self.export_rows(queryset), "owner-statements")

    # ---- print ------------------------------------------------------------------------------------------
    def print_view(self, request, object_id):
        statement = self.get_object(request, object_id)
        if statement is None or not self.has_view_permission(request, statement):
            raise Http404
        sections = []
        for key in SECTION_ORDER:
            lines = [l for l in statement.lines.all() if l.section == key]
            if lines:
                sections.append({"title": dict(Section.choices)[key], "lines": lines, "total": None if key == "info" else sum((l.amount for l in lines), 0),
                                 "info": key == "info"})
        context = {
            **self.admin_site.each_context(request),
            "doc": statement, "title": f"Owner statement {statement.code}", "sections": sections,
            "company": {key: sysconf.get(f"company_{key}") for key in ("name", "trn", "phone", "email", "address")},
            "period": period_text(statement), "words": amount_in_words(statement.payout_due),
            "back": reverse("admin:statements_ownerstatement_change", args=[statement.pk]),
            "deductions": statement.fee_total + statement.cost_total,
        }
        return TemplateResponse(request, "statements/print.html", context)


# ---------------------------------------------------------------------------------------
# The "Statements" tab on the owner page
# ---------------------------------------------------------------------------------------
class OwnerStatementsInline(ReadOnlyInline):
    model = OwnerStatement
    tab = True
    verbose_name_plural = "Statements"
    show_change_link = False
    fields = ("number", "period", "payout", "state", "open_statement")
    readonly_fields = fields
    ordering = ("-period_end",)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("statements.view_ownerstatement")

    @admin.display(description="Statement no.")
    def number(self, obj):
        return obj.code

    @admin.display(description="Period")
    def period(self, obj):
        return period_text(obj)

    @admin.display(description="To pay the owner")
    def payout(self, obj):
        return money(obj.payout_due)

    @admin.display(description="Status")
    def state(self, obj):
        return status_badge(obj)

    @admin.display(description="")
    def open_statement(self, obj):
        return format_html('<a class="erp-qbtn erp-qbtn-primary" href="{}">Open statement</a>', reverse("admin:statements_ownerstatement_change", args=[obj.pk])) if obj.pk else ""


add_inline_tab(OwnerAdmin, OwnerStatementsInline, "ActivityInline")
