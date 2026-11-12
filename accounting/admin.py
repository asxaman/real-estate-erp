"""Accounting screens: the chart of accounts, journal entries, the general ledger and the trial balance."""
from datetime import date, datetime
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.forms.models import BaseInlineFormSet
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from core import exports
from core.admin_utils import ExportPermissionMixin, TooltipHelpMixin, badge
from core.formats import fmt_date
from core.paging import RowsPerPageMixin
from core.search import SmartSearchMixin

from . import posting
from .models import Account, AccountType, FundTransfer, JournalEntry, JournalLine, Reconciliation

ZERO = Decimal("0.00")
TYPE_TONES = {"asset": "info", "liability": "warning", "equity": "neutral", "income": "success", "expense": "danger"}
SOURCE_TEXT = {
    "": "Manual", "leasepayment": "Tenant payment", "vendorbill": "Vendor bill", "expense": "Expense", "paymentvoucher": "Payment voucher",
    "payrollrun": "Payroll", "finalsettlement": "Final settlement", "ownerstatement": "Owner statement", "fundtransfer": "Fund transfer", "opening": "Opening balances",
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
ACCOUNT_HEADERS = ["Account no.", "Name", "Main account", "Type", "Active", "Debits (AED)", "Credits (AED)", "Balance (AED)"]


def account_rows(queryset):
    totals = balances()
    for a in queryset.select_related("parent"):
        dr, cr = totals.get(a.pk, (ZERO, ZERO))
        yield [a.code, a.name, a.parent.name if a.parent_id else "", a.get_type_display(), "Yes" if a.is_active else "No", dr, cr, (dr - cr) if a.debit_normal else (cr - dr)]


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
    list_display = ("code", "title", "kind", "balance", "state")
    list_display_links = ("code", "title")
    list_filter = ("type", "is_active")
    search_fields = ("code", "name")
    search_text = ("code", "name")
    search_hint = "account number or name"
    actions = ["export_excel", "export_csv"]
    fields = ("code", "name", "parent", "type", "is_active", "notes")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("parent")

    def get_ordering(self, request):
        # In the chart of accounts list: a main account, then its sub-accounts under it. Everywhere else (drop-down lists on other pages) by number.
        match = getattr(request, "resolver_match", None)
        if match is not None and match.url_name == "accounting_account_changelist":
            level = Case(When(parent__isnull=True, then=Value(0)), default=Value(1), output_field=IntegerField())
            return [Coalesce("parent__code", "code").asc(), level.asc(), "code"]
        return ["code"]

    def quick_chips(self):
        return [("main", "Main Accounts", "info", Q(parent__isnull=True)), ("sub", "Sub-Accounts", "neutral", Q(parent__isnull=False))]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "parent":
            kwargs["queryset"] = Account.objects.filter(parent__isnull=True, is_active=True).order_by("code")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        wrap = self.admin_site.admin_view
        mine = [
            path("ledger/", wrap(self.ledger_view), name="%s_%s_ledger" % info),
            path("trial-balance/", wrap(self.trial_balance_view), name="%s_%s_trialbalance" % info),
            path("cash-and-bank/", wrap(self.cash_bank_view), name="%s_%s_cashbank" % info),
            path("reconcile/", wrap(self.reconcile_view), name="%s_%s_reconcile" % info),
            path("rebuild/", wrap(self.rebuild_view), name="%s_%s_rebuild" % info),
            path("opening-balances/", wrap(self.opening_view), name="%s_%s_opening" % info),
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
            locked += ["code", "type", "is_active", "parent"]
        elif obj.lines.exists() or obj.children.exists() or obj.parent_id:
            locked += ["type"]
        return locked

    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.is_system or obj.lines.exists() or obj.children.exists()):
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.display(description="Name", ordering="name")
    def title(self, obj):
        if obj.parent_id:
            return format_html('<span style="padding-left:22px">&#8627; {}</span>', obj.name)
        return format_html("<b>{}</b>", obj.name) if getattr(self, "_mains", None) and obj.pk in self._mains else obj.name

    @admin.display(description="Type", ordering="type")
    def kind(self, obj):
        return badge(obj.get_type_display(), TYPE_TONES[obj.type])

    @admin.display(description="Balance")
    def balance(self, obj):
        dr, cr = self._totals.get(obj.pk, (ZERO, ZERO))
        for child in self._children.get(obj.pk, ()):  # a main account shows its own balance plus its sub-accounts
            c_dr, c_cr = self._totals.get(child, (ZERO, ZERO))
            dr, cr = dr + c_dr, cr + c_cr
        value = (dr - cr) if obj.debit_normal else (cr - dr)
        return money(value) if value else "-"

    @admin.display(description="Status", ordering="is_active")
    def state(self, obj):
        return badge("Active", "success") if obj.is_active else badge("Inactive", "neutral")

    def changelist_view(self, request, extra_context=None):
        self._totals = balances()
        self._children = {}
        for pk, parent in Account.objects.filter(parent__isnull=False).values_list("pk", "parent_id"):
            self._children.setdefault(parent, []).append(pk)
        self._mains = set(self._children)
        return super().changelist_view(request, extra_context)

    @admin.action(description="Export selected accounts to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Chart of accounts", ACCOUNT_HEADERS, account_rows(queryset), "chart-of-accounts")

    @admin.action(description="Export selected accounts to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(ACCOUNT_HEADERS, account_rows(queryset), "chart-of-accounts")

    def report(self, request, template, context, stem, headers, rows):
        """Show a report page, or give it as an Excel / CSV file when asked (?export=excel)."""
        can_export = request.user.has_perm("accounting.export_journalentry")
        kind = request.GET.get("export")
        if kind in ("excel", "csv") and can_export:
            rows = list(rows)
            if kind == "excel":
                return exports.excel_response(context["title"], headers, rows, stem)
            return exports.csv_response(headers, rows, stem)
        query = request.GET.copy()
        query.pop("export", None)
        base = query.urlencode()
        prefix = "?" + base + "&" if base else "?"
        context.update(export_excel=prefix + "export=excel", export_csv=prefix + "export=csv", can_export=can_export)
        return TemplateResponse(request, template, context)

    # ---- the ledger of one account ---------------------------------------------------------------
    def _may_view(self, request):
        if not request.user.has_perm("accounting.view_journalentry"):
            raise Http404

    def ledger_view(self, request):
        self._may_view(request)
        accounts = list(Account.objects.filter(Q(is_active=True) | Q(lines__isnull=False)).distinct().select_related("parent"))
        accounts.sort(key=lambda a: ((a.parent.code if a.parent_id else a.code), a.parent_id is not None, a.code))
        chosen = Account.objects.filter(pk=request.GET.get("account") or 0).first() or (accounts[0] if accounts else None)
        today = timezone.localdate()
        start = parse_day(request.GET.get("from")) if "from" in request.GET else date(today.year, 1, 1)
        end = parse_day(request.GET.get("to")) or today
        rows, opening, closing, total_dr, total_cr = [], ZERO, ZERO, ZERO, ZERO
        combined = False
        if chosen is not None:
            ids = [chosen.pk] + list(chosen.children.values_list("pk", flat=True))  # a main account also shows its sub-accounts
            combined = len(ids) > 1
            lines = JournalLine.objects.filter(account_id__in=ids, entry__status="posted")
            if start:
                before = lines.filter(entry__date__lt=start).aggregate(dr=Sum("debit"), cr=Sum("credit"))
                opening = (before["dr"] or ZERO) - (before["cr"] or ZERO)
            running = opening
            sign = 1 if chosen.debit_normal else -1
            shown = lines.filter(entry__date__lte=end)
            if start:
                shown = shown.filter(entry__date__gte=start)
            for line in shown.select_related("entry", "account").order_by("entry__date", "entry_id", "id"):
                running += line.debit - line.credit
                total_dr += line.debit
                total_cr += line.credit
                rows.append({"line": line, "entry": line.entry, "account": line.account, "balance": running * sign,
                             "url": reverse("admin:accounting_journalentry_change", args=[line.entry_id])})
            closing = running
            opening = opening * sign
            closing = closing * sign
        context = dict(
            self.admin_site.each_context(request), opts=self.model._meta, title="General Ledger", accounts=accounts, chosen=chosen, rows=rows, combined=combined,
            opening=opening, closing=closing, total_dr=total_dr, total_cr=total_cr, start=start.isoformat() if start else "", end=end.isoformat(),
            trial_url=reverse("admin:accounting_account_trialbalance"), chart_url=reverse("admin:accounting_account_changelist"),
        )
        data = [["Opening balance", "", "", "", "", "", opening]] + [
            [r["entry"].date, r["entry"].code, r["account"].code, r["line"].description or r["entry"].description, r["line"].debit, r["line"].credit, r["balance"]]
            for r in rows
        ] + [["Totals / closing balance", "", "", "", total_dr, total_cr, closing]]
        return self.report(request, "accounting/ledger.html", context, "general-ledger",
                           ["Date", "Entry", "Account no.", "Description", "Debit (AED)", "Credit (AED)", "Balance (AED)"], data)

    # ---- trial balance --------------------------------------------------------------------------
    def trial_balance_view(self, request):
        self._may_view(request)
        today = timezone.localdate()
        end = parse_day(request.GET.get("to")) or today
        totals = balances(end=end)
        groups, grand_dr, grand_cr = [], ZERO, ZERO
        by_type = {}
        for acct in Account.objects.select_related("parent"):
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
        data = [[r["account"].code, r["account"].full_name, g["label"], r["debit"], r["credit"]] for g in groups for r in g["rows"]] + [["", "Total", "", grand_dr, grand_cr]]
        return self.report(request, "accounting/trial_balance.html", context, "trial-balance", ["Account no.", "Account", "Type", "Debit (AED)", "Credit (AED)"], data)

    # ---- profit and loss --------------------------------------------------------------------------
    def _report_links(self):
        return dict(
            links=[("General Ledger", reverse("admin:accounting_account_ledger")), ("Trial Balance", reverse("admin:accounting_account_trialbalance")),
                   ("Profit And Loss", reverse("admin:accounting_account_profitloss")), ("Balance Sheet", reverse("admin:accounting_account_balancesheet")),
                   ("VAT Return", reverse("admin:accounting_account_vatreturn")), ("Cash And Bank", reverse("admin:accounting_account_cashbank"))],
        )

    @staticmethod
    def _row_name(r):
        name = r["account"].name + (" (direct)" if r.get("direct") else "")
        return ("    " + name) if r.get("indent") else name

    def _section(self, kinds, totals, debit_normal):
        """Rows for a report: each main account (with its sub-accounts added up under it) and the section total."""
        def own(acct):
            dr, cr = totals.get(acct.pk, (ZERO, ZERO))
            return (dr - cr) if debit_normal else (cr - dr)

        accounts = list(Account.objects.filter(type__in=kinds).order_by("code"))
        subs = {}
        for acct in accounts:
            if acct.parent_id:
                subs.setdefault(acct.parent_id, []).append(acct)
        ledger = reverse("admin:accounting_account_ledger")
        rows, total = [], ZERO
        for acct in accounts:
            if acct.parent_id:
                continue
            children = [(c, own(c)) for c in subs.get(acct.pk, []) if own(c)]
            value = own(acct) + sum((v for _, v in children), ZERO)
            if not value and not children:
                continue
            rows.append({"account": acct, "value": value, "url": f"{ledger}?account={acct.pk}", "indent": False, "bold": bool(children)})
            if children and own(acct):
                rows.append({"account": acct, "value": own(acct), "url": f"{ledger}?account={acct.pk}", "indent": True, "bold": False, "direct": True})
            for child, v in children:
                rows.append({"account": child, "value": v, "url": f"{ledger}?account={child.pk}", "indent": True, "bold": False})
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
        data = [["Income", r["account"].code, self._row_name(r), r["value"]] for r in income] + [["Income", "", "Total income", income_total]]
        data += [["Expenses", r["account"].code, self._row_name(r), r["value"]] for r in expenses] + [["Expenses", "", "Total expenses", expense_total], ["", "", "Net profit / (loss)", profit]]
        return self.report(request, "accounting/profit_loss.html", context, "profit-and-loss", ["Section", "Account no.", "Account", "AED"], data)

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
        data = []
        for label, items, total in (("Assets", assets, asset_total), ("Liabilities", liabilities, liability_total), ("Equity", equity, equity_total)):
            data += [[label, r["account"].code, self._row_name(r), r["value"]] for r in items] + [[label, "", f"Total {label.lower()}", total]]
        data += [["Equity", "", "Profit / (loss) to date", profit], ["", "", "Total liabilities and equity", right_total]]
        return self.report(request, "accounting/balance_sheet.html", context, "balance-sheet", ["Section", "Account no.", "Account", "AED"], data)

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
        data = [["Standard-rated supplies (5%)", sales_std, output_vat], ["Exempt / zero-rated / no VAT", sales_other, ZERO],
                ["Standard-rated expenses (5%)", purchases_std, input_vat], ["VAT refundable to you" if due < 0 else "VAT payable to the FTA", "", abs(due)]]
        return self.report(request, "accounting/vat_return.html", context, "vat-return", ["Item", "Amount (AED)", "VAT (AED)"], data)

    # ---- cash and bank overview -------------------------------------------------------------------
    def cash_bank_view(self, request):
        self._may_view(request)
        totals = balances()
        rows = []
        for acct in Account.objects.filter(type="asset", code__startswith="10", is_active=True):
            dr, cr = totals.get(acct.pk, (ZERO, ZERO))
            last = acct.reconciliations.first()
            open_lines = JournalLine.objects.filter(account=acct, entry__status="posted", reconciliation__isnull=True).count()
            rows.append({"account": acct, "balance": dr - cr, "last": last, "open": open_lines,
                         "ledger": reverse("admin:accounting_account_ledger") + f"?account={acct.pk}",
                         "reconcile": reverse("admin:accounting_account_reconcile") + f"?account={acct.pk}"})
        context = dict(
            self.admin_site.each_context(request), opts=self.model._meta, title="Cash And Bank", rows=rows, total=sum((r["balance"] for r in rows), ZERO),
            transfer_url=reverse("admin:accounting_fundtransfer_add"), can_transfer=request.user.has_perm("accounting.add_fundtransfer"),
            can_reconcile=request.user.has_perm("accounting.add_reconciliation"), history_url=reverse("admin:accounting_reconciliation_changelist"),
            **self._report_links(),
        )
        data = [[r["account"].code, r["account"].name, r["balance"], r["last"].statement_date if r["last"] else "", r["open"]] for r in rows]
        return self.report(request, "accounting/cash_bank.html", context, "cash-and-bank", ["Account no.", "Account", "Ledger balance (AED)", "Last reconciled to", "Lines not yet reconciled"], data)

    # ---- bank reconciliation ----------------------------------------------------------------------
    def reconcile_view(self, request):
        if not request.user.has_perm("accounting.add_reconciliation"):
            raise Http404
        accounts = list(Account.objects.filter(type="asset", is_active=True).order_by("code"))
        chosen = Account.objects.filter(pk=request.GET.get("account") or request.POST.get("account") or 0, type="asset").first()
        if chosen is None:
            chosen = next((a for a in accounts if a.code == "1010"), accounts[0] if accounts else None)
        today = timezone.localdate()
        statement_date = parse_day(request.POST.get("statement_date") or request.GET.get("to")) or today
        lines, opening = [], ZERO
        if chosen is not None:
            done = JournalLine.objects.filter(account=chosen, entry__status="posted", reconciliation__isnull=False).aggregate(dr=Sum("debit"), cr=Sum("credit"))
            opening = (done["dr"] or ZERO) - (done["cr"] or ZERO)
            lines = list(JournalLine.objects.filter(account=chosen, entry__status="posted", reconciliation__isnull=True, entry__date__lte=statement_date)
                         .select_related("entry").order_by("entry__date", "entry_id", "id"))
        if request.method == "POST" and chosen is not None:
            try:
                balance = Decimal((request.POST.get("statement_balance") or "").replace(",", "").strip())
            except Exception:  # noqa: BLE001
                balance = None
            if balance is not None and not balance.is_finite():
                balance = None
            ticked = {int(v) for v in request.POST.getlist("line") if v.isdigit()}
            picked = [l for l in lines if l.pk in ticked]
            cleared = opening + sum((l.debit - l.credit for l in picked), ZERO)
            if balance is None:
                messages.error(request, "Enter the closing balance shown on the bank statement.")
            elif cleared != balance:
                messages.error(request, f"The ticked lines give {money(cleared)} but the statement says {money(balance)} (difference {money(abs(cleared - balance))}). Tick the lines that appear on the statement.")
            elif not picked:
                messages.error(request, "Tick at least one line.")
            else:
                rec = Reconciliation(account=chosen, statement_date=statement_date, statement_balance=balance, opening_balance=opening,
                                     notes=(request.POST.get("notes") or "")[:200], created_by=request.user)
                rec.save()
                JournalLine.objects.filter(pk__in=[l.pk for l in picked]).update(reconciliation=rec)
                messages.success(request, f"{rec.code}: {chosen.name} is reconciled to {fmt_date(statement_date)} with {len(picked)} line(s).")
                return HttpResponseRedirect(reverse("admin:accounting_account_cashbank"))
            posted = {"balance": request.POST.get("statement_balance", ""), "ticked": ticked, "notes": request.POST.get("notes", "")}
        else:
            posted = {"balance": "", "ticked": set(), "notes": ""}
        rows = [{"line": l, "entry": l.entry, "net": l.debit - l.credit, "ticked": l.pk in posted["ticked"],
                 "url": reverse("admin:accounting_journalentry_change", args=[l.entry_id])} for l in lines]
        context = dict(
            self.admin_site.each_context(request), opts=self.model._meta, title="Bank Reconciliation", accounts=accounts, chosen=chosen, rows=rows,
            opening=opening, statement_date=statement_date.isoformat(), posted=posted, cash_url=reverse("admin:accounting_account_cashbank"),
            history_url=reverse("admin:accounting_reconciliation_changelist"),
        )
        return TemplateResponse(request, "accounting/reconcile.html", context)

    # ---- opening (starting) balances --------------------------------------------------------------
    def opening_view(self, request):
        if not request.user.has_perm("accounting.add_journalentry"):
            raise Http404
        entry = JournalEntry.objects.filter(source_type=posting.OPENING, source_id=1).first()
        accounts = list(Account.objects.filter(type__in=("asset", "liability", "equity"), is_active=True).exclude(code=posting.OPENING_EQUITY)
                        .select_related("parent").order_by("code"))
        accounts.sort(key=lambda a: ((a.parent.code if a.parent_id else a.code), a.parent_id is not None, a.code))
        existing = {}
        if entry is not None:
            for line in entry.lines.all():
                existing[line.account_id] = (line.debit, line.credit)
        day = entry.date if entry is not None else date(timezone.localdate().year, 1, 1)
        errors, entered = [], {}
        if request.method == "POST":
            day = parse_day(request.POST.get("as_of")) or None
            if day is None:
                errors.append("Choose the date the balances are as of.")
            for acct in accounts:
                values = []
                for side in ("dr", "cr"):
                    raw = (request.POST.get(f"{side}_{acct.pk}") or "").replace(",", "").strip()
                    try:
                        value = Decimal(raw) if raw else ZERO
                        if not value.is_finite() or value < 0:
                            raise ValueError
                    except Exception:  # noqa: BLE001
                        errors.append(f"{acct.code} {acct.name}: enter a positive amount.")
                        value = ZERO
                    values.append(value.quantize(Decimal("0.01")))
                if values[0] and values[1]:
                    errors.append(f"{acct.code} {acct.name}: use either debit or credit, not both.")
                entered[acct.pk] = tuple(values)
            if not errors:
                posting.save_opening_balances(day, {acct: entered[acct.pk] for acct in accounts if any(entered[acct.pk])}, request.user)
                messages.success(request, "The opening balances were saved. Any difference was put to Opening Balance Equity.")
                return HttpResponseRedirect(reverse("admin:accounting_account_trialbalance"))
            for text in errors[:6]:
                messages.error(request, text)
            existing = entered
        rows = [{"account": a, "dr": existing.get(a.pk, (ZERO, ZERO))[0] or "", "cr": existing.get(a.pk, (ZERO, ZERO))[1] or ""} for a in accounts]
        context = dict(
            self.admin_site.each_context(request), opts=self.model._meta, title="Opening Balances", rows=rows, as_of=day.isoformat() if day else "",
            chart_url=reverse("admin:accounting_account_changelist"), trial_url=reverse("admin:accounting_account_trialbalance"),
            equity=posting.account(posting.OPENING_EQUITY),
        )
        return TemplateResponse(request, "accounting/opening.html", context)

    # ---- rebuild the automatic entries ----------------------------------------------------------
    def rebuild_view(self, request):
        if not request.user.has_perm("accounting.add_journalentry"):
            raise Http404
        if request.method == "POST":
            counts = posting.sync_all()
            labels = {"leasepayment": "tenant payments", "vendorbill": "vendor bills", "expense": "expenses", "paymentvoucher": "payment vouchers",
                      "payrollrun": "payrolls", "finalsettlement": "final settlements", "ownerstatement": "owner statements", "fundtransfer": "fund transfers"}
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



# ---------------------------------------------------------------------------------------
# Fund transfers
# ---------------------------------------------------------------------------------------
TRANSFER_HEADERS = ["Transfer no.", "Date", "From", "To", "Amount (AED)", "Reference", "Status", "Notes"]


def transfer_rows(queryset):
    for t in queryset.select_related("from_account", "to_account"):
        yield [t.code, t.date, f"{t.from_account.code} {t.from_account.name}", f"{t.to_account.code} {t.to_account.name}", t.amount, t.reference,
               "Cancelled" if t.cancelled else "Posted", t.notes]


@admin.register(FundTransfer)
class FundTransferAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    compressed_fields = True
    list_display = ("code", "day", "from_account", "to_account", "money", "reference", "state")
    list_display_links = ("code",)
    list_filter = ("cancelled",)
    search_fields = ("code", "reference", "notes", "from_account__name", "to_account__name")
    search_text = ("code", "reference", "notes", "from_account__name", "to_account__name")
    search_codes = (("code", "TRF", 4),)
    search_hint = "transfer number (TRF-3), reference or account"
    actions = ["export_excel", "export_csv"]
    autocomplete_fields = ()

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {"fields": ("date", "from_account", "to_account", "amount", "reference", "notes"),
                            "description": "Move money between your own cash and bank accounts (for example cash banked, or bank to bank). It is posted to the ledger automatically."}),)
        return ((None, {"fields": ("date", "from_account", "to_account", "amount", "reference", "notes", "cancelled", "cancel_reason")}),)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return ["date", "from_account", "to_account", "amount"]
        return ()

    def has_delete_permission(self, request, obj=None):
        return False  # a wrong transfer is cancelled with a reason

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("from_account", "to_account")

    def quick_chips(self):
        today = timezone.localdate()
        return [("month", "This Month", "success", Q(date__year=today.year, date__month=today.month)), ("cancelled", "Cancelled", "warning", Q(cancelled=True))]

    @admin.display(description="Date", ordering="date")
    def day(self, obj):
        return fmt_date(obj.date)

    @admin.display(description="Amount (AED)", ordering="amount")
    def money(self, obj):
        return money(obj.amount)

    @admin.display(description="Status", ordering="cancelled")
    def state(self, obj):
        return badge("Cancelled", "neutral") if obj.cancelled else badge("Posted", "success")

    @admin.action(description="Export selected transfers to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Fund transfers", TRANSFER_HEADERS, transfer_rows(queryset), "fund-transfers")

    @admin.action(description="Export selected transfers to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(TRANSFER_HEADERS, transfer_rows(queryset), "fund-transfers")


# ---------------------------------------------------------------------------------------
# Reconciliation history
# ---------------------------------------------------------------------------------------
@admin.register(Reconciliation)
class ReconciliationAdmin(ExportPermissionMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    compressed_fields = True
    list_display = ("code", "account", "day", "balance", "count", "created_by")
    list_display_links = ("code",)
    list_filter = ("account",)
    fields = ("code", "account", "statement_date", "statement_balance", "opening_balance", "notes", "created_by", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False  # made from the Bank reconciliation page

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        """Only the latest reconciliation of an account can be undone, so the balances stay in order."""
        if obj is None:
            return super().has_delete_permission(request, obj)
        latest = Reconciliation.objects.filter(account=obj.account).order_by("-statement_date", "-id").first()
        return latest is not None and latest.pk == obj.pk and super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.display(description="Statement date", ordering="statement_date")
    def day(self, obj):
        return fmt_date(obj.statement_date)

    @admin.display(description="Statement balance (AED)", ordering="statement_balance")
    def balance(self, obj):
        return money(obj.statement_balance)

    @admin.display(description="Lines ticked")
    def count(self, obj):
        return obj.lines.count()
