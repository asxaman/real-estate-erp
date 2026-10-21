import calendar
from datetime import date

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.forms.models import BaseInlineFormSet
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import TabularInline as UnfoldTabularInline

from . import reports
from realestate.models import Cheque, Lease

from .chart import seed_chart
from .models import (
    ZERO,
    Account,
    AccountingSettings,
    AccountMapping,
    AuditLog,
    CreditNote,
    FiscalPeriod,
    JournalEntry,
    JournalLine,
    LeaseInvoice,
    ReceiptVoucher,
    check_lines,
    check_posting_date,
    unmapped_roles,
)
from .posting import reverse_entry
from .rules import create_lease_invoice, post_all_waiting, void_lease_invoice, waiting_items
from .rules_more import create_opening_invoice, opening_numbers


def calendar_month_name(number):
    return calendar.month_name[number]


def _day(text, default):
    try:
        return date.fromisoformat(text) if text else default
    except ValueError:
        return default


# --- chart of accounts + reports -----------------------------------------


@admin.register(Account)
class AccountAdmin(UnfoldModelAdmin):
    list_display = ("code", "name", "account_type", "control_kind", "fund", "is_header", "is_active")
    list_filter = ("account_type", "control_kind", "fund", "is_header", "is_active")
    search_fields = ("code", "name")
    ordering = ("code",)
    fieldsets = (
        (None, {"fields": ("code", "name", "account_type", "parent", "is_header", "is_active")}),
        (
            "Special accounts",
            {
                "fields": ("control_kind", "is_bank", "fund"),
                "description": "Tenant, owner and vendor control accounts keep a sub-ledger. "
                "Bank accounts are marked as the company's own money or owners' money.",
            },
        ),
        ("Notes", {"fields": ("notes",)}),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.lines.exists():
            return ("account_type", "control_kind", "is_header", "is_bank", "fund")
        return ()

    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.lines.exists() or obj.children.exists()):
            return False
        return super().has_delete_permission(request, obj)

    def get_search_results(self, request, queryset, search_term):
        queryset, distinct = super().get_search_results(request, queryset, search_term)
        # In the journal voucher, only offer accounts you can really post to.
        if request.GET.get("model_name") == "journalline":
            queryset = queryset.filter(is_header=False, is_active=True)
        return queryset, distinct

    # --- reports ---

    def get_urls(self):
        custom = [
            path("trial-balance/", self.admin_site.admin_view(self.trial_balance_view),
                 name="accounting_account_trial_balance"),
            path("general-ledger/", self.admin_site.admin_view(self.general_ledger_view),
                 name="accounting_account_general_ledger"),
            path("reconciliation/", self.admin_site.admin_view(self.reconciliation_view),
                 name="accounting_account_reconciliation"),
            path("setup/", self.admin_site.admin_view(self.setup_view),
                 name="accounting_setup"),
            path("postings/", self.admin_site.admin_view(self.postings_view),
                 name="accounting_postings"),
        ]
        return custom + super().get_urls()

    def _check_reports(self, request):
        if not request.user.has_perm("accounting.view_journalline"):
            raise PermissionDenied

    def trial_balance_view(self, request):
        self._check_reports(request)
        as_of = _day(request.GET.get("as_of"), timezone.localdate())
        report = reports.trial_balance(as_of, include_zero=bool(request.GET.get("zero")))
        context = dict(self.admin_site.each_context(request), title="Trial balance", report=report,
                       include_zero=bool(request.GET.get("zero")))
        return TemplateResponse(request, "admin/accounting/account/trial_balance.html", context)

    def general_ledger_view(self, request):
        self._check_reports(request)
        accounts = Account.objects.filter(is_header=False).order_by("code")
        account = None
        if request.GET.get("account", "").isdigit():
            account = accounts.filter(pk=int(request.GET["account"])).first()
        today = timezone.localdate()
        date_from = _day(request.GET.get("from"), date(today.year, 1, 1))
        date_to = _day(request.GET.get("to"), today)
        party = None
        party_label = ""
        raw = request.GET.get("party", "")
        if account and account.control_kind and ":" in raw:
            kind, _, pk = raw.partition(":")
            if kind == account.control_kind and pk.isdigit():
                party = (kind, int(pk))
        report = reports.general_ledger(account, date_from, date_to, party) if account else None
        if party and report:
            first = report["rows"][0]["line"].party if report["rows"] else None
            party_label = str(first) if first else f"{party[0]} #{party[1]}"
        context = dict(
            self.admin_site.each_context(request),
            title="General ledger",
            accounts=accounts,
            account=account,
            report=report,
            date_from=date_from,
            date_to=date_to,
            party=raw if party else "",
            party_label=party_label,
        )
        return TemplateResponse(request, "admin/accounting/account/general_ledger.html", context)

    def setup_view(self, request):
        """One page to get the accounting module ready, no command line needed."""
        user = request.user
        if not user.has_perm("accounting.view_account"):
            raise PermissionDenied
        if request.method == "POST":
            action = request.POST.get("action")
            if action == "chart":
                if not user.has_perm("accounting.add_account"):
                    raise PermissionDenied
                made = seed_chart()
                AuditLog.record("setup", user=user, detail=f"Starter chart of accounts: {made} account(s) created.")
                if made:
                    messages.success(request, f"{made} account(s) added to the chart of accounts.")
                else:
                    messages.info(request, "The starter accounts already exist. Nothing was changed.")
            elif action == "periods":
                if not user.has_perm("accounting.add_fiscalperiod"):
                    raise PermissionDenied
                try:
                    year = int(request.POST.get("year", ""))
                    made = FiscalPeriod.ensure_year(year)
                except ValueError:
                    messages.error(request, "Enter a year between 2000 and 2100.")
                except ValidationError as exc:
                    messages.error(request, exc.messages[0])
                else:
                    AuditLog.record("setup", user=user, detail=f"Fiscal periods {year}: {made} created.")
                    if made:
                        messages.success(request, f"Created {made} monthly period(s) for {year}.")
                    else:
                        messages.info(request, f"All the months of {year} already exist.")
            return HttpResponseRedirect(reverse("admin:accounting_setup"))

        settings_ = AccountingSettings.load()
        years = sorted({p.start_date.year for p in FiscalPeriod.objects.all()})
        problems = reports.ledger_health()
        context = dict(
            self.admin_site.each_context(request),
            title="Accounting setup",
            account_count=Account.objects.count(),
            period_years=years,
            period_count=FiscalPeriod.objects.count(),
            settings_obj=settings_,
            settings_url=reverse("admin:accounting_accountingsettings_changelist"),
            entry_count=JournalEntry.objects.filter(status=JournalEntry.Status.POSTED).count(),
            problems=problems,
            unmapped=unmapped_roles(),
            year=timezone.localdate().year,
            can_chart=user.has_perm("accounting.add_account"),
            can_periods=user.has_perm("accounting.add_fiscalperiod"),
        )
        return TemplateResponse(request, "admin/accounting/account/setup.html", context)

    def postings_view(self, request):
        """Daily postings: invoices to issue, payments waiting to post, month end."""
        user = request.user
        if not user.has_perm("accounting.view_journalline"):
            raise PermissionDenied
        here = reverse("admin:accounting_postings")
        if request.method == "POST":
            action = request.POST.get("action")
            if action == "invoice":
                if not user.has_perm("accounting.add_leaseinvoice"):
                    raise PermissionDenied
                lease_id = request.POST.get("lease", "")
                lease = get_object_or_404(Lease, pk=int(lease_id)) if lease_id.isdigit() else None
                if lease is None:
                    raise PermissionDenied
                try:
                    day = date.fromisoformat(request.POST.get("date", "")) if request.POST.get("date") else None
                    invoice, errors = create_lease_invoice(lease, day, user)
                except ValueError:
                    messages.error(request, "Enter a valid invoice date.")
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                else:
                    messages.success(request, f"Invoice {invoice.number} issued for {lease.tenant.name}.")
                    for error in errors:
                        messages.warning(request, f"Not posted yet: {error}")
            elif action == "open":
                if not user.has_perm("accounting.add_leaseinvoice"):
                    raise PermissionDenied
                lease_id = request.POST.get("lease", "")
                lease = get_object_or_404(Lease, pk=int(lease_id)) if lease_id.isdigit() else None
                if lease is None:
                    raise PermissionDenied
                try:
                    invoice, errors = create_opening_invoice(lease, user)
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                else:
                    messages.success(request, f"Opening balances {invoice.number} posted for {lease.tenant.name}.")
                    for error in errors:
                        messages.warning(request, f"Not posted yet: {error}")
            elif action == "sync":
                if not user.has_perm("accounting.post_journalentry"):
                    raise PermissionDenied
                posted, errors = post_all_waiting(user)
                messages.success(request, f"{posted} posting step(s) done.")
                for error in errors:
                    messages.warning(request, error)
            return HttpResponseRedirect(here)

        info = waiting_items()
        cutover = info["cutover"]
        rows = [
            {"lease": lease, "default_date": max(lease.start_date, cutover)} for lease in info["to_invoice"]
        ]
        open_rows = []
        for lease in info["to_open"]:
            figures = opening_numbers(lease, cutover)
            open_rows.append({"lease": lease, "n": figures})
        context = dict(
            self.admin_site.each_context(request),
            title="Daily postings",
            info=info,
            rows=rows,
            open_rows=open_rows,
            can_invoice=user.has_perm("accounting.add_leaseinvoice"),
            can_post=user.has_perm("accounting.post_journalentry"),
            settings_url=reverse("admin:accounting_accountingsettings_changelist"),
        )
        return TemplateResponse(request, "admin/accounting/account/postings.html", context)

    def reconciliation_view(self, request):
        self._check_reports(request)
        rows = reports.reconciliation()
        detail = None
        if request.GET.get("account", "").isdigit():
            account = Account.objects.exclude(control_kind="").filter(pk=int(request.GET["account"])).first()
            if account:
                detail = {"account": account, "parties": reports.party_balances(account)}
        context = dict(
            self.admin_site.each_context(request),
            title="Control accounts and sub-ledgers",
            rows=rows,
            detail=detail,
            all_ok=all(r["ok"] for r in rows),
        )
        return TemplateResponse(request, "admin/accounting/account/reconciliation.html", context)


# --- fiscal periods -------------------------------------------------------


@admin.register(FiscalPeriod)
class FiscalPeriodAdmin(UnfoldModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_locked", "locked_at", "locked_by")
    list_filter = ("is_locked",)
    ordering = ("start_date",)
    readonly_fields = ("is_locked", "locked_at", "locked_by")
    fields = ("name", "start_date", "end_date", "is_locked", "locked_at", "locked_by")
    actions = ["lock_selected", "unlock_selected"]

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and (obj.is_locked or obj.entries.exists()):
            base += ["name", "start_date", "end_date"]
        return base

    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.is_locked or obj.entries.exists()):
            return False
        return super().has_delete_permission(request, obj)

    def has_lock_permission(self, request):
        return request.user.has_perm("accounting.lock_fiscalperiod")

    def has_unlock_permission(self, request):
        return request.user.has_perm("accounting.unlock_fiscalperiod")

    @admin.action(description="Lock (close) the selected periods", permissions=["lock"])
    def lock_selected(self, request, queryset):
        done = sum(1 for period in queryset if period.lock(request.user))
        messages.success(request, f"{done} period(s) locked. Nothing can be posted into them now.")

    @admin.action(description="Unlock (re-open) the selected periods", permissions=["unlock"])
    def unlock_selected(self, request, queryset):
        done = sum(1 for period in queryset if period.unlock(request.user, "re-opened from the period list"))
        messages.warning(request, f"{done} period(s) re-opened. This was recorded in the audit log.")

    def get_urls(self):
        custom = [
            path("generate/", self.admin_site.admin_view(self.generate_view),
                 name="accounting_fiscalperiod_generate"),
        ]
        return custom + super().get_urls()

    def generate_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        year = timezone.localdate().year
        if request.method == "POST":
            try:
                year = int(request.POST.get("year", ""))
                made = FiscalPeriod.ensure_year(year)
            except (ValueError, ValidationError) as exc:
                messages.error(request, "Enter a year between 2000 and 2100." if isinstance(exc, ValueError) else exc.messages[0])
            else:
                if made:
                    messages.success(request, f"Created {made} monthly period(s) for {year}.")
                else:
                    messages.info(request, f"All the months of {year} already exist.")
                return HttpResponseRedirect(reverse("admin:accounting_fiscalperiod_changelist"))
        context = dict(self.admin_site.each_context(request), title="Create the periods of a year", year=year)
        return TemplateResponse(request, "admin/accounting/fiscalperiod/generate.html", context)


# --- journal vouchers -----------------------------------------------------


class JournalEntryForm(forms.ModelForm):
    source = forms.ChoiceField(
        label="Type",
        choices=[
            (JournalEntry.Source.MANUAL, "Manual journal (adjustment or correction)"),
            (JournalEntry.Source.OPENING, "Opening balances (on the cutover date)"),
        ],
        initial=JournalEntry.Source.MANUAL,
    )

    class Meta:
        model = JournalEntry
        fields = ("date", "source", "memo", "reference")

    def clean(self):
        cleaned = super().clean()
        day = cleaned.get("date")
        if day:
            try:
                check_posting_date(day)
            except ValidationError as exc:
                self.add_error("date", exc)
            else:
                if cleaned.get("source") == JournalEntry.Source.OPENING:
                    cutover = AccountingSettings.load().cutover_date
                    if not cutover:
                        self.add_error("source", "Set the cutover date in Accounting settings first.")
                    elif day != cutover:
                        self.add_error("date", f"Opening balances must be dated the cutover date ({cutover:%d %b %Y}).")
        return cleaned


class JournalLineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        rows = []
        for form in self.forms:
            data = getattr(form, "cleaned_data", None)
            if not data or data.get("DELETE") or not data.get("account"):
                continue
            rows.append(
                (
                    data["account"],
                    data.get("debit") or ZERO,
                    data.get("credit") or ZERO,
                    data["tenant"].pk if data.get("tenant") else None,
                    data["owner"].pk if data.get("owner") else None,
                    data["vendor"].pk if data.get("vendor") else None,
                )
            )
        try:
            check_lines(rows)
        except ValidationError as exc:
            # Show the message on the first filled line as well, so it cannot be missed.
            for form in self.forms:
                data = getattr(form, "cleaned_data", None)
                if data and data.get("account"):
                    form.add_error(None, exc)
                    break
            raise


class JournalLineInline(UnfoldTabularInline):
    model = JournalLine
    formset = JournalLineFormSet
    fields = ("account", "description", "debit", "credit", "tenant", "owner", "vendor")
    autocomplete_fields = ("account", "tenant", "owner", "vendor")
    extra = 4

    def has_add_permission(self, request, obj=None):
        return obj is None and request.user.has_perm("accounting.post_journalentry")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(JournalEntry)
class JournalEntryAdmin(UnfoldModelAdmin):
    form = JournalEntryForm
    inlines = [JournalLineInline]
    list_display = ("number", "date", "memo", "source", "amount", "status")
    list_filter = ("source", "status", ("date", admin.DateFieldListFilter), "period")
    search_fields = ("number", "memo", "reference")
    date_hierarchy = "date"
    ordering = ("-date", "-id")
    readonly_fields = ("totals_display",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_total=Sum("lines__debit"))

    @admin.display(description="Amount", ordering="_total")
    def amount(self, obj):
        return f"{(obj._total or ZERO):,.2f}"

    @admin.display(description="Total debits = total credits")
    def totals_display(self, obj):
        debit, credit, _ = obj.totals()
        return f"{debit:,.2f} = {credit:,.2f}"

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("date", "source", "memo", "reference")
        return (
            "number", "date", "period", "source", "memo", "reference", "status",
            "reverses", "totals_display", "created_by", "created_at", "posted_at",
        )

    # Nobody edits or deletes a posted entry. Mistakes are reversed.
    def has_add_permission(self, request):
        return request.user.has_perm("accounting.post_journalentry")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            obj.status = JournalEntry.Status.DRAFT
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not change:
            if AccountingSettings.load().separate_approver:
                # Two-person rule: someone else must approve it before it counts.
                form.instance.check_ready_to_post()
                messages.info(
                    request,
                    "Saved as a draft. A second person must open it and click 'Approve and post'.",
                )
                return
            # Lines are saved: check everything once more and make it permanent.
            form.instance.post(user=request.user)

    def get_urls(self):
        custom = [
            path("<int:pk>/reverse/", self.admin_site.admin_view(self.reverse_view),
                 name="accounting_journalentry_reverse"),
            path("<int:pk>/approve/", self.admin_site.admin_view(self.approve_view),
                 name="accounting_journalentry_approve"),
            path("<int:pk>/discard/", self.admin_site.admin_view(self.discard_view),
                 name="accounting_journalentry_discard"),
        ]
        return custom + super().get_urls()

    def approve_view(self, request, pk):
        """Second person: check and post a draft journal."""
        if request.method != "POST" or not request.user.has_perm("accounting.post_journalentry"):
            raise PermissionDenied
        entry = get_object_or_404(JournalEntry, pk=pk)
        back = HttpResponseRedirect(reverse("admin:accounting_journalentry_change", args=[entry.pk]))
        if entry.is_posted:
            messages.info(request, f"{entry.number} is already posted.")
            return back
        if AccountingSettings.load().separate_approver and entry.created_by_id == request.user.pk:
            messages.error(request, "You made this entry, so someone else must approve it.")
            return back
        try:
            entry.post(user=request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return back
        messages.success(request, f"{entry.number} was approved and posted.")
        return back

    def discard_view(self, request, pk):
        """Throw away a draft that was never posted."""
        if request.method != "POST" or not request.user.has_perm("accounting.post_journalentry"):
            raise PermissionDenied
        entry = get_object_or_404(JournalEntry, pk=pk)
        if entry.is_posted:
            messages.error(request, "A posted entry cannot be deleted. Reverse it instead.")
            return HttpResponseRedirect(reverse("admin:accounting_journalentry_change", args=[entry.pk]))
        entry.lines.all()._raw_delete(entry.lines.db)
        entry.delete()
        AuditLog.record("discard", user=request.user, detail=f"A draft journal was discarded: {entry.memo}")
        messages.success(request, "The draft was discarded.")
        return HttpResponseRedirect(reverse("admin:accounting_journalentry_changelist"))

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra = dict(extra_context or {})
        entry = JournalEntry.objects.filter(pk=object_id).first()
        if entry and not entry.is_posted and request.user.has_perm("accounting.post_journalentry"):
            extra["approve_url"] = reverse("admin:accounting_journalentry_approve", args=[entry.pk])
            extra["discard_url"] = reverse("admin:accounting_journalentry_discard", args=[entry.pk])
        if entry and entry.is_posted:
            reversal = JournalEntry.objects.filter(reverses=entry).first()
            extra["reversed_by"] = reversal
            if (
                not reversal
                and entry.source != JournalEntry.Source.REVERSAL
                and request.user.has_perm("accounting.reverse_journalentry")
            ):
                extra["reverse_url"] = reverse("admin:accounting_journalentry_reverse", args=[entry.pk])
        return super().change_view(request, object_id, form_url, extra)

    def reverse_view(self, request, pk):
        if not request.user.has_perm("accounting.reverse_journalentry"):
            raise PermissionDenied
        entry = get_object_or_404(JournalEntry, pk=pk)
        error = ""
        day = timezone.localdate()
        reason = ""
        if request.method == "POST" and AccountingSettings.load().separate_approver and entry.created_by_id == request.user.pk:
            messages.error(request, "You made this entry, so someone else must reverse it.")
            return HttpResponseRedirect(reverse("admin:accounting_journalentry_change", args=[entry.pk]))
        if request.method == "POST":
            reason = request.POST.get("reason", "").strip()
            try:
                day = date.fromisoformat(request.POST.get("date", ""))
            except ValueError:
                error = "Enter a valid date."
            else:
                if not reason:
                    error = "Write the reason for the reversal."
                else:
                    try:
                        new = reverse_entry(entry, date=day, reason=reason, user=request.user)
                    except ValidationError as exc:
                        error = " ".join(exc.messages)
                    else:
                        messages.success(request, f"{entry.number} was reversed by {new.number}.")
                        return HttpResponseRedirect(reverse("admin:accounting_journalentry_change", args=[new.pk]))
        context = dict(
            self.admin_site.each_context(request),
            title=f"Reverse {entry.number}",
            entry=entry,
            lines=entry.lines.select_related("account", "tenant", "owner", "vendor"),
            error=error,
            day=day.isoformat() if isinstance(day, date) else "",
            reason=reason,
        )
        return TemplateResponse(request, "admin/accounting/journalentry/reverse.html", context)


# --- settings and audit log ----------------------------------------------


@admin.register(AccountingSettings)
class AccountingSettingsAdmin(UnfoldModelAdmin):
    fields = ("legal_name", "trn", "address", "cutover_date", "separate_approver")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        settings_ = AccountingSettings.load()
        return HttpResponseRedirect(reverse("admin:accounting_accountingsettings_change", args=[settings_.pk]))

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditLog.record(
            "settings", user=request.user,
            detail=f"Accounting settings saved. TRN: {obj.trn or 'not set'}; cutover: {obj.cutover_date or 'not set'}.",
        )


@admin.register(AuditLog)
class AuditLogAdmin(UnfoldModelAdmin):
    list_display = ("when", "user", "action", "entry", "detail")
    list_filter = ("action",)
    search_fields = ("detail", "entry__number")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# --- documents: invoices and receipt vouchers -----------------------------------


class _ReadOnlyAdmin(UnfoldModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LeaseInvoice)
class LeaseInvoiceAdmin(_ReadOnlyAdmin):
    list_display = ("number", "date", "lease", "total_amount", "vat_amount", "is_opening", "is_void", "print_link")
    list_filter = ("is_opening", "is_void")
    search_fields = ("number", "lease__tenant__name")
    date_hierarchy = "date"
    actions = ["cancel_invoices"]

    @admin.display(description="Print")
    def print_link(self, obj):
        if obj.is_opening:
            return "Opening balances (no tax invoice)"
        return format_html(
            '<a href="{}" target="_blank">Open printable invoice</a>',
            reverse("admin:accounting_leaseinvoice_print", args=[obj.pk]),
        )

    def has_cancel_permission(self, request):
        return request.user.has_perm("accounting.reverse_journalentry")

    @admin.action(
        description="Cancel the selected invoices (to correct the lease and invoice again)",
        permissions=["cancel"],
    )
    def cancel_invoices(self, request, queryset):
        done = 0
        for invoice in queryset.filter(is_void=False):
            try:
                void_lease_invoice(invoice, user=request.user)
            except ValidationError as exc:
                self.message_user(request, f"{invoice.number}: {' '.join(exc.messages)}", messages.ERROR)
            else:
                done += 1
        if done:
            self.message_user(request, f"{done} invoice(s) cancelled. You can now correct the lease and invoice it again.", messages.SUCCESS)

    def get_urls(self):
        custom = [
            path("<int:pk>/print/", self.admin_site.admin_view(self.print_view),
                 name="accounting_leaseinvoice_print"),
        ]
        return custom + super().get_urls()

    def print_view(self, request, pk):
        if not self.has_view_permission(request):
            raise PermissionDenied
        invoice = get_object_or_404(LeaseInvoice.objects.select_related("lease__tenant", "lease__unit__property"), pk=pk)
        if invoice.is_opening:
            raise Http404("Opening balances are not an invoice to the tenant.")
        lease = invoice.lease
        context = dict(
            self.admin_site.each_context(request),
            invoice=invoice,
            lease=lease,
            company=AccountingSettings.load(),
            schedule=lease.cheques.filter(purpose=Cheque.Purpose.RENT).order_by("cheque_date", "id"),
        )
        return TemplateResponse(request, "admin/accounting/leaseinvoice/print.html", context)


@admin.register(CreditNote)
class CreditNoteAdmin(_ReadOnlyAdmin):
    list_display = ("number", "date", "lease", "total_amount", "vat_amount", "is_void", "print_link")
    list_filter = ("is_void",)
    search_fields = ("number", "lease__tenant__name")
    date_hierarchy = "date"

    @admin.display(description="Print")
    def print_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank">Open printable credit note</a>',
            reverse("admin:accounting_creditnote_print", args=[obj.pk]),
        )

    def get_urls(self):
        custom = [
            path("<int:pk>/print/", self.admin_site.admin_view(self.print_view),
                 name="accounting_creditnote_print"),
        ]
        return custom + super().get_urls()

    def print_view(self, request, pk):
        if not self.has_view_permission(request):
            raise PermissionDenied
        note = get_object_or_404(
            CreditNote.objects.select_related("invoice", "lease__tenant", "lease__unit__property"), pk=pk
        )
        context = dict(
            self.admin_site.each_context(request),
            note=note,
            invoice=note.invoice,
            lease=note.lease,
            company=AccountingSettings.load(),
        )
        return TemplateResponse(request, "admin/accounting/creditnote/print.html", context)


@admin.register(ReceiptVoucher)
class ReceiptVoucherAdmin(_ReadOnlyAdmin):
    list_display = ("number", "date", "cheque", "amount", "is_void", "print_link")
    search_fields = ("number", "cheque__lease__tenant__name", "cheque__cheque_number")
    list_filter = ("is_void",)
    date_hierarchy = "date"

    @admin.display(description="Print")
    def print_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank">Open printable receipt</a>',
            reverse("admin:accounting_receiptvoucher_print", args=[obj.pk]),
        )

    def get_urls(self):
        custom = [
            path("<int:pk>/print/", self.admin_site.admin_view(self.print_view),
                 name="accounting_receiptvoucher_print"),
        ]
        return custom + super().get_urls()

    def print_view(self, request, pk):
        if not self.has_view_permission(request):
            raise PermissionDenied
        voucher = get_object_or_404(
            ReceiptVoucher.objects.select_related("cheque__lease__tenant", "cheque__lease__unit__property"), pk=pk
        )
        context = dict(
            self.admin_site.each_context(request),
            voucher=voucher,
            cheque=voucher.cheque,
            lease=voucher.cheque.lease,
            company=AccountingSettings.load(),
        )
        return TemplateResponse(request, "admin/accounting/receiptvoucher/print.html", context)


@admin.register(AccountMapping)
class AccountMappingAdmin(UnfoldModelAdmin):
    list_display = ("label", "account")
    list_editable = ("account",)
    list_display_links = None
    ordering = ("key",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditLog.record("mapping", user=request.user, detail=f"Posting account '{obj.label}' set to {obj.account}.")
