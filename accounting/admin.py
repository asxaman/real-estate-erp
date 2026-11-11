"""Accounting screens: the chart of accounts, journal entries, the general ledger and the trial balance."""
from datetime import date, datetime
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.forms.models import BaseInlineFormSet
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from unfold.admin import ModelAdmin, TabularInline

from core import exports
from core.admin_utils import ExportPermissionMixin, TooltipHelpMixin, badge
from core.formats import fmt_date
from core.paging import RowsPerPageMixin
from core.search import SmartSearchMixin

from . import posting
from .models import Account, AccountType, JournalEntry, JournalLine

ZERO = Decimal("0.00")
TYPE_TONES = {"asset": "info", "liability": "warning", "equity": "neutral", "income": "success", "expense": "danger"}
SOURCE_TEXT = {
    "": "Manual", "leasepayment": "Tenant payment", "vendorbill": "Vendor bill", "expense": "Expense", "paymentvoucher": "Payment voucher",
    "payrollrun": "Payroll", "finalsettlement": "Final settlement", "ownerstatement": "Owner statement",
}


def money(value):
    return f"{value:,.2f}"


def parse_day(text):
    try:
        return datetime.strptime(text or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def balances(end=None, start=None):
    """{account id: (debit, credit)} from the posted entries between the dates."""
    lines = JournalLine.objects.filter(entry__status="posted")
    if end:
        lines = lines.filter(entry__date__lte=end)
    if start:
        lines = lines.filter(entry__date__gte=start)
    return {row["account_id"]: (row["dr"] or ZERO, row["cr"] or ZERO) for row in lines.values("account_id").annotate(dr=Sum("debit"), cr=Sum("credit"))}


# ---------------------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------------------
ACCOUNT_HEADERS = ["Account no.", "Name", "Type", "Active", "Debits (AED)", "Credits (AED)", "Balance (AED)"]


def account_rows(queryset):
    totals = balances()
    for a in queryset:
        dr, cr = totals.get(a.pk, (ZERO, ZERO))
        yield [a.code, a.name, a.get_type_display(), "Yes" if a.is_active else "No", dr, cr, (dr - cr) if a.debit_normal else (cr - dr)]


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "code" in self.fields:
            self.fields["code"].widget.attrs["placeholder"] = "e.g. 5050"
        if "name" in self.fields:
            self.fields["name"].widget.attrs["placeholder"] = "e.g. Staff visa costs"


@admin.register(Account)
class AccountAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = AccountForm
    compressed_fields = True
    list_display = ("code", "name", "kind", "balance", "state")
    list_display_links = ("code", "name")
    list_filter = ("type", "is_active")
    search_fields = ("code", "name")
    search_text = ("code", "name")
    search_hint = "account number or name"
    actions = ["export_excel", "export_csv"]
    fields = ("code", "name", "type", "is_active", "notes")

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        wrap = self.admin_site.admin_view
        mine = [
            path("ledger/", wrap(self.ledger_view), name="%s_%s_ledger" % info),
            path("trial-balance/", wrap(self.trial_balance_view), name="%s_%s_trialbalance" % info),
            path("rebuild/", wrap(self.rebuild_view), name="%s_%s_rebuild" % info),
            path("profit-and-loss/", wrap(self.profit_loss_view), name="%s_%s_profitloss" % info),
            path("balance-sheet/", wrap(self.balance_sheet_view), name="%s_%s_balancesheet" % info),
            path("vat-return/", wrap(self.vat_return_view), name="%s_%s_vatreturn" % info),
        ]
        return mine + super().get_urls()

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        locked = []
        if obj.is_system:
            locked += ["code", "type", "is_active"]
        elif obj.lines.exists():
            locked += ["type"]
        return locked

    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.is_system or obj.lines.exists()):
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.display(description="Type", ordering="type")
    def kind(self, obj):
        return badge(obj.get_type_display(), TYPE_TONES[obj.type])

    @admin.display(description="Balance")
    def balance(self, obj):
        dr, cr = self._totals.get(obj.pk, (ZERO, ZERO))
        value = (dr - cr) if obj.debit_normal else (cr - dr)
        return money(value) if value else "-"

    @admin.display(description="Status", ordering="is_active")
    def state(self, obj):
        return badge("Active", "success") if obj.is_active else badge("Inactive", "neutral")

    def changelist_view(self, request, extra_context=None):
        self._totals = balances()
        return super().changelist_view(request, extra_context)

    @admin.action(description="Export selected accounts to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Chart of accounts", ACCOUNT_HEADERS, account_rows(queryset), "chart-of-accounts")

    @admin.action(description="Export selected accounts to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(ACCOUNT_HEADERS, account_rows(queryset), "chart-of-accounts")

    # ---- the ledger of one account ---------------------------------------------------------------
    def _may_view(self, request):
        if not request.user.has_perm("accounting.view_journalentry"):
            raise Http404

    def ledger_view(self, request):
        self._may_view(request)
        accounts = list(Account.objects.filter(Q(is_active=True) | Q(lines__isnull=False)).distinct())
        chosen = Account.objects.filter(pk=request.GET.get("account") or 0).first() or (accounts[0] if accounts else None)
        today = timezone.localdate()
        start = parse_day(request.GET.get("from")) if "from" in request.GET else date(today.year, 1, 1)
        end = parse_day(request.GET.get("to")) or today
        rows, opening, closing, total_dr, total_cr = [], ZERO, ZERO, ZERO, ZERO
        if chosen is not None:
            lines = JournalLine.objects.filter(account=chosen, entry__status="posted")
            if start:
                before = lines.filter(entry__date__lt=start).aggregate(dr=Sum("debit"), cr=Sum("credit"))
                opening = (before["dr"] or ZERO) - (before["cr"] or ZERO)
            running = opening
            sign = 1 if chosen.debit_normal else -1
            shown = lines.filter(entry__date__lte=end)
            if start:
                shown = shown.filter(entry__date__gte=start)
            for line in shown.select_related("entry").order_by("entry__date", "entry_id", "id"):
                running += line.debit - line.credit
                total_dr += line.debit
                total_cr += line.credit
                rows.append({"line": line, "entry": line.entry, "balance": running * sign,
                             "url": reverse("admin:accounting_journalentry_change", args=[line.entry_id])})
            closing = running
            opening = opening * sign
            closing = closing * sign
        context = dict(
            self.admin_site.each_context(request), opts=self.model._meta, title="General Ledger", accounts=accounts, chosen=chosen, rows=rows,
            opening=opening, closing=closing, total_dr=total_dr, total_cr=total_cr, start=start.isoformat() if start else "", end=end.isoformat(),
            trial_url=reverse("admin:accounting_account_trialbalance"), chart_url=reverse("admin:accounting_account_changelist"),
        )
        return TemplateResponse(request, "accounting/ledger.html", context)

    # ---- trial balance --------------------------------------------------------------------------
    def trial_balance_view(self, request):
        self._may_view(request)
        today = timezone.localdate()
        end = parse_day(request.GET.get("to")) or today
        totals = balances(end=end)
        groups, grand_dr, grand_cr = [], ZERO, ZERO
        by_type = {}
        for acct in Account.objects.all():
            dr, cr = totals.get(acct.pk, (ZERO, ZERO))
            if not (dr or cr):
                continue
            net = dr - cr
            row = {"account": acct, "debit": net if net > 0 else ZERO, "credit": -net if net < 0 else ZERO,
                   "url": f"{reverse('admin:accounting_account_ledger')}?account={acct.pk}&to={end.isoformat()}&from="}
            by_type.setdefault(acct.type, []).append(row)
            grand_dr += row["debit"]
            grand_cr += row["credit"]
        for key, label in AccountType.choices:
            if key in by_type:
                rows = by_type[key]
                groups.append({"label": label, "rows": rows, "debit": sum((r["debit"] for r in rows), ZERO), "credit": sum((r["credit"] for r in rows), ZERO)})
        context = dict(
            self.admin_site.each_context(request), opts=self.model._meta, title="Trial Balance", groups=groups, grand_dr=grand_dr, grand_cr=grand_cr,
            balanced=grand_dr == grand_cr, difference=abs(grand_dr - grand_cr), end=end.isoformat(), end_text=fmt_date(end),
            ledger_url=reverse("admin:accounting_account_ledger"), rebuild_url=reverse("admin:accounting_account_rebuild"),
            can_rebuild=request.user.has_perm("accounting.add_journalentry"),
        )
        return TemplateResponse(request, "accounting/trial_balance.html", context)

    # ---- profit and loss --------------------------------------------------------------------------
    def _report_links(self):
        return dict(
            links=[("General Ledger", reverse("admin:accounting_account_ledger")), ("Trial Balance", reverse("admin:accounting_account_trialbalance")),
                   ("Profit And Loss", reverse("admin:accounting_account_profitloss")), ("Balance Sheet", reverse("admin:accounting_account_balancesheet")),
                   ("VAT Return", reverse("admin:accounting_account_vatreturn"))],
        )

    def _section(self, kinds, totals, debit_normal):
        rows, total = [], ZERO
        for acct in Account.objects.filter(type__in=kinds):
            dr, cr = totals.get(acct.pk, (ZERO, ZERO))
            value = (dr - cr) if debit_normal else (cr - dr)
            if value:
                rows.append({"account": acct, "value": value, "url": reverse("admin:accounting_account_ledger") + f"?account={acct.pk}"})
                total += value
        return rows, total

    def profit_loss_view(self, request):
        self._may_view(request)
        today = timezone.localdate()
        start = parse_day(request.GET.get("from")) if "from" in request.GET else date(today.year, 1, 1)
        end = parse_day(request.GET.get("to")) or today
        totals = balances(end=end, start=start)
        income, income_total = self._section(["income"], totals, False)
        expenses, expense_total = self._section(["expense"], totals, True)
        profit = income_total - expense_total
        context = dict(
            self.admin_site.each_context(request), opts=self.model._meta, title="Profit And Loss", income=income, income_total=income_total,
            expenses=expenses, expense_total=expense_total, profit=profit, start=start.isoformat() if start else "", end=end.isoformat(),
            start_text=fmt_date(start) if start else "the beginning", end_text=fmt_date(end), **self._report_links(),
        )
        return TemplateResponse(request, "accounting/profit_loss.html", context)

    # ---- balance sheet ----------------------------------------------------------------------------
    def balance_sheet_view(self, request):
        self._may_view(request)
        end = parse_day(request.GET.get("to")) or timezone.localdate()
        totals = balances(end=end)
        assets, asset_total = self._section(["asset"], totals, True)
        liabilities, liability_total = self._section(["liability"], totals, False)
        equity, equity_total = self._section(["equity"], totals, False)
        _, income_total = self._section(["income"], totals, False)
        _, expense_total = self._section(["expense"], totals, True)
        profit = income_total - expense_total
        right_total = liability_total + equity_total + profit
        context = dict(
            self.admin_site.each_context(request), opts=self.model._meta, title="Balance Sheet", assets=assets, asset_total=asset_total,
            liabilities=liabilities, liability_total=liability_total, equity=equity, equity_total=equity_total, profit=profit,
            right_total=right_total, balanced=asset_total == right_total, difference=abs(asset_total - right_total), end=end.isoformat(),
            end_text=fmt_date(end), **self._report_links(),
        )
        return TemplateResponse(request, "accounting/balance_sheet.html", context)

    # ---- UAE VAT return ---------------------------------------------------------------------------
    def vat_return_view(self, request):
        self._may_view(request)
        today = timezone.localdate()
        quarter_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
        start = parse_day(request.GET.get("from")) or quarter_start
        end = parse_day(request.GET.get("to")) or today
        lines = JournalLine.objects.filter(entry__status="posted", entry__date__gte=start, entry__date__lte=end)
        out_entries = set(lines.filter(account__code="2100").values_list("entry_id", flat=True))
        in_entries = set(lines.filter(account__code="1200").values_list("entry_id", flat=True))

        def net(entry_ids, kind, credit_side):
            if not entry_ids:
                return ZERO
            agg = lines.filter(entry_id__in=entry_ids, account__type=kind).aggregate(dr=Sum("debit"), cr=Sum("credit"))
            return ((agg["cr"] or ZERO) - (agg["dr"] or ZERO)) if credit_side else ((agg["dr"] or ZERO) - (agg["cr"] or ZERO))

        sales_std = net(out_entries, "income", True)
        agg = lines.filter(account__type="income").aggregate(dr=Sum("debit"), cr=Sum("credit"))
        sales_all = (agg["cr"] or ZERO) - (agg["dr"] or ZERO)
        sales_other = sales_all - sales_std
        a2100 = lines.filter(account__code="2100").aggregate(dr=Sum("debit"), cr=Sum("credit"))
        a1200 = lines.filter(account__code="1200").aggregate(dr=Sum("debit"), cr=Sum("credit"))
        output_vat = (a2100["cr"] or ZERO) - (a2100["dr"] or ZERO)
        input_vat = (a1200["dr"] or ZERO) - (a1200["cr"] or ZERO)
        purchases_std = net(in_entries, "expense", False)
        due = output_vat - input_vat
        context = dict(
            self.admin_site.each_context(request), opts=self.model._meta, title="VAT Return (UAE 5%)", start=start.isoformat(), end=end.isoformat(),
            start_text=fmt_date(start), end_text=fmt_date(end), sales_std=sales_std, sales_other=sales_other, output_vat=output_vat,
            purchases_std=purchases_std, input_vat=input_vat, due=due, refund=due < 0, due_abs=abs(due), **self._report_links(),
        )
        return TemplateResponse(request, "accounting/vat_return.html", context)

    # ---- rebuild the automatic entries ----------------------------------------------------------
    def rebuild_view(self, request):
        if not request.user.has_perm("accounting.add_journalentry"):
            raise Http404
        if request.method == "POST":
            counts = posting.sync_all()
            labels = {"leasepayment": "tenant payments", "vendorbill": "vendor bills", "expense": "expenses", "paymentvoucher": "payment vouchers",
                      "payrollrun": "payrolls", "finalsettlement": "final settlements", "ownerstatement": "owner statements"}
            text = ", ".join(f"{n} {labels[k]}" for k, n in counts.items() if n) or "nothing to post yet"
            messages.success(request, f"The ledger was refreshed: {text}.")
            return HttpResponseRedirect(reverse("admin:accounting_account_trialbalance"))
        context = dict(self.admin_site.each_context(request), opts=self.model._meta, title="Refresh The Ledger",
                       back=reverse("admin:accounting_account_trialbalance"))
        return TemplateResponse(request, "accounting/rebuild.html", context)


# ---------------------------------------------------------------------------------------
# Journal entries
# ---------------------------------------------------------------------------------------
ENTRY_HEADERS = ["Entry no.", "Date", "Description", "Source", "Status", "Account no.", "Account", "Line description", "Debit (AED)", "Credit (AED)"]


def entry_rows(queryset):
    for e in queryset.prefetch_related("lines__account"):
        for l in e.lines.all():
            yield [e.code, e.date, e.description, SOURCE_TEXT.get(e.source_type, e.source_type), e.get_status_display(), l.account.code, l.account.name,
                   l.description, l.debit, l.credit]


class LineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        debit = credit = ZERO
        used = 0
        for form in self.forms:
            data = getattr(form, "cleaned_data", None)
            if not data or data.get("DELETE") or not data.get("account"):
                continue
            used += 1
            debit += data.get("debit") or ZERO
            credit += data.get("credit") or ZERO
        if used < 2:
            raise ValidationError("An entry needs at least two lines (one debit and one credit).")
        if debit != credit:
            raise ValidationError(f"The entry does not balance: debits are {money(debit)} and credits are {money(credit)} (difference {money(abs(debit - credit))}).")


class LineInline(TabularInline):
    model = JournalLine
    formset = LineFormSet
    extra = 2
    fields = ("account", "description", "debit", "credit")

    def get_extra(self, request, obj=None, **kwargs):
        return 0 if obj is not None else 2

    def has_add_permission(self, request, obj=None):
        return obj is None or (obj.is_manual and obj.status == "posted" and super().has_add_permission(request, obj))

    def has_change_permission(self, request, obj=None):
        return obj is None or (obj.is_manual and obj.status == "posted" and super().has_change_permission(request, obj))

    def has_delete_permission(self, request, obj=None):
        return obj is None or (obj.is_manual and obj.status == "posted" and super().has_delete_permission(request, obj))

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        return formset


class EntryForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "description" in self.fields:
            self.fields["description"].widget.attrs["placeholder"] = "e.g. Opening balances"
        if "status" in self.fields:
            self.fields["status"].choices = [(k, v) for k, v in JournalEntry.Status.choices]


@admin.register(JournalEntry)
class JournalEntryAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = EntryForm
    compressed_fields = True
    list_display = ("code", "day", "description", "origin", "amount", "state")
    list_display_links = ("code", "description")
    list_filter = ("status", "source_type")
    search_fields = ("code", "description", "lines__account__code", "lines__account__name")
    search_text = ("code", "description")
    search_codes = (("code", "JV", 4),)
    search_hint = "entry number (JV-12) or description"
    date_hierarchy = "date"
    inlines = [LineInline]
    actions = ["export_excel", "export_csv"]

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {"fields": ("date", "description"),
                            "description": "Add the lines below. Total debits must equal total credits. Use this for opening balances and adjustments; everything else is posted automatically."}),)
        if obj.is_manual and obj.status == "posted":
            return ((None, {"fields": ("date", "description", "status", "void_reason")}),)
        return ((None, {"fields": ("date", "description", "origin_text", "status", "void_reason")}),)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        if obj.is_manual and obj.status == "posted":
            return ()
        return ["date", "description", "origin_text", "status", "void_reason"]

    def has_change_permission(self, request, obj=None):
        if obj is not None and not obj.is_manual:
            return False  # automatic entries follow their record
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False  # a wrong manual entry is voided, not deleted

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("lines")

    def quick_chips(self):
        today = timezone.localdate()
        return [
            ("manual", "Manual", "info", Q(source_type="")),
            ("auto", "Automatic", "neutral", ~Q(source_type="")),
            ("month", "This Month", "success", Q(date__year=today.year, date__month=today.month)),
            ("voided", "Voided", "warning", Q(status="voided")),
        ]

    @admin.display(description="Date", ordering="date")
    def day(self, obj):
        return fmt_date(obj.date)

    @admin.display(description="Made from")
    def origin(self, obj):
        return badge(SOURCE_TEXT.get(obj.source_type, obj.source_type), "info" if obj.is_manual else "neutral")

    @admin.display(description="Made from")
    def origin_text(self, obj):
        return SOURCE_TEXT.get(obj.source_type, obj.source_type) if obj and obj.pk else "-"

    @admin.display(description="Amount")
    def amount(self, obj):
        return money(sum((l.debit for l in obj.lines.all()), ZERO))

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return badge(obj.get_status_display(), "success" if obj.status == "posted" else "neutral")

    @admin.action(description="Export selected entries to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Journal entries", ENTRY_HEADERS, entry_rows(queryset), "journal-entries")

    @admin.action(description="Export selected entries to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(ENTRY_HEADERS, entry_rows(queryset), "journal-entries")
