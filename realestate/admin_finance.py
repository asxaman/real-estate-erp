"""Admin pages for expenses, maintenance, vendors and owner statements."""

from datetime import date
from decimal import Decimal

from django.contrib import admin, messages
from django.db.models import F
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import TabularInline as UnfoldTabularInline

from .admin import badge, log_change
from .models import (
    CompanySettings,
    Expense,
    MaintenanceRequest,
    OwnerStatement,
    StatementLine,
    Vendor,
)

STATEMENT_COLORS = {"draft": "#757575", "approved": "#ef6c00", "paid": "#2e7d32"}
REQUEST_COLORS = {
    "open": "#c62828",
    "in_progress": "#ef6c00",
    "completed": "#2e7d32",
    "cancelled": "#455a64",
}
PRIORITY_COLORS = {
    "low": "#757575",
    "normal": "#1565c0",
    "urgent": "#ef6c00",
    "emergency": "#c62828",
}
BEARER_COLORS = {"owner": "#1565c0", "company": "#6a1b9a", "tenant": "#ef6c00"}


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------


@admin.register(Vendor)
class VendorAdmin(UnfoldModelAdmin):
    list_display = ("name", "trade", "contact_person", "phone", "is_active")
    list_filter = ("trade", "is_active")
    search_fields = ("name", "contact_person", "phone", "email", "trade_licence_number")
    fieldsets = (
        ("Vendor", {"fields": ("name", "trade", "is_active")}),
        ("Contact", {"fields": ("contact_person", "phone", "email", "address")}),
        ("Company papers", {"fields": ("trade_licence_number", "trn")}),
        ("Notes", {"fields": ("notes",)}),
    )


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


class ExpenseStatementFilter(admin.SimpleListFilter):
    title = "owner statement"
    parameter_name = "statement"

    def lookups(self, request, model_admin):
        return [
            ("waiting", "Charged to owner, not on a statement yet"),
            ("draft", "On a draft statement"),
            ("locked", "On an approved / paid statement"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "waiting":
            return queryset.filter(
                bearer=Expense.Bearer.OWNER, statement_line__isnull=True
            )
        if value == "draft":
            return queryset.filter(
                statement_line__statement__status=OwnerStatement.Status.DRAFT
            )
        if value == "locked":
            return queryset.filter(statement_line__isnull=False).exclude(
                statement_line__statement__status=OwnerStatement.Status.DRAFT
            )
        return queryset


class ExpenseRecoveryFilter(admin.SimpleListFilter):
    title = "tenant repayment"
    parameter_name = "recovery"

    def lookups(self, request, model_admin):
        return [
            ("open", "Charged to a tenant, not fully paid back"),
            ("paid", "Charged to a tenant, paid back"),
        ]

    def queryset(self, request, queryset):
        tenant_expenses = queryset.filter(bearer=Expense.Bearer.TENANT)
        if self.value() == "open":
            return tenant_expenses.exclude(recovered_amount__gte=F("amount"))
        if self.value() == "paid":
            return tenant_expenses.filter(recovered_amount__gte=F("amount"))
        return queryset


@admin.register(Expense)
class ExpenseAdmin(UnfoldModelAdmin):
    date_hierarchy = "expense_date"
    conditional_fields = {
        "tenant": "bearer == 'tenant'",
        "recovered_amount": "bearer == 'tenant'",
        "recovered_on": "bearer == 'tenant'",
    }
    list_display = (
        "expense_date",
        "property",
        "unit",
        "description",
        "category",
        "vendor",
        "amount_aed",
        "who_pays",
        "statement_column",
        "owed_by_tenant",
    )
    list_filter = (
        "bearer",
        ExpenseStatementFilter,
        ExpenseRecoveryFilter,
        "category",
        "property",
    )
    list_select_related = ("property", "unit", "vendor", "statement_line__statement")
    search_fields = (
        "description",
        "invoice_number",
        "property__name",
        "unit__unit_number",
        "vendor__name",
    )
    autocomplete_fields = ("property", "unit", "vendor", "tenant")
    raw_id_fields = ("maintenance_request",)
    fieldsets = (
        (
            "What was spent",
            {
                "fields": (
                    "property",
                    "unit",
                    "category",
                    "description",
                    "expense_date",
                    "vendor",
                    "invoice_number",
                    "maintenance_request",
                )
            },
        ),
        (
            "Money",
            {
                "description": (
                    "Choose 'Owner' to deduct this from the owner's next statement. "
                    "Company-owned properties have no owner to charge."
                ),
                "fields": ("amount", "vat_amount", "bearer"),
            },
        ),
        (
            "Paying back (only when the tenant pays)",
            {
                "description": "Record who owes this money and how much they have paid back.",
                "fields": ("tenant", "recovered_amount", "recovered_on"),
            },
        ),
        ("Receipt", {"fields": ("receipt", "notes")}),
    )

    @admin.display(description="Amount (AED)", ordering="amount")
    def amount_aed(self, obj):
        return f"{obj.amount:,.2f}"

    @admin.display(description="Who pays", ordering="bearer")
    def who_pays(self, obj):
        return badge(obj.get_bearer_display().split(" (")[0], BEARER_COLORS[obj.bearer])

    @admin.display(description="Owner statement")
    def statement_column(self, obj):
        if obj.bearer != Expense.Bearer.OWNER:
            return "-"
        line = getattr(obj, "statement_line", None)
        if line is None:
            return badge("Not yet", "#757575")
        return badge(line.statement.number or "draft", STATEMENT_COLORS[line.statement.status])

    @admin.display(description="Tenant still owes")
    def owed_by_tenant(self, obj):
        if obj.bearer != Expense.Bearer.TENANT:
            return "-"
        owed = obj.still_to_recover
        if owed <= 0:
            return badge("Paid back", "#2e7d32")
        return badge(f"AED {owed:,.2f}", "#c62828")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_locked:
            # Money the tenant pays back can still be recorded.
            keep_open = {"tenant", "recovered_amount", "recovered_on"}
            return [
                f.name for f in Expense._meta.fields if f.editable and f.name not in keep_open
            ]
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.statement_status is not None:
            return False
        return super().has_delete_permission(request, obj)


# ---------------------------------------------------------------------------
# Maintenance requests
# ---------------------------------------------------------------------------


class RequestExpenseInline(UnfoldTabularInline):
    """Add the costs of a job right on the request."""

    model = Expense
    extra = 0
    fields = ("category", "vendor", "description", "expense_date", "amount", "bearer", "receipt")
    autocomplete_fields = ("vendor",)
    verbose_name_plural = "Costs of this job"


@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(UnfoldModelAdmin):
    date_hierarchy = "reported_on"
    list_display = (
        "reported_on",
        "title",
        "property",
        "unit",
        "priority_badge",
        "status_badge",
        "vendor",
        "cost_aed",
    )
    list_filter = ("status", "priority", "category", "property")
    list_select_related = ("property", "unit", "vendor")
    search_fields = ("title", "description", "property__name", "unit__unit_number", "vendor__name")
    autocomplete_fields = ("property", "unit", "tenant", "vendor")
    inlines = [RequestExpenseInline]
    actions = ["action_in_progress", "action_completed"]
    fieldsets = (
        (
            "The problem",
            {"fields": ("property", "unit", "tenant", "title", "description", "category", "priority")},
        ),
        (
            "The job",
            {
                "fields": (
                    "status",
                    "reported_on",
                    "scheduled_for",
                    "completed_on",
                    "vendor",
                    "estimated_cost",
                )
            },
        ),
        ("Notes", {"fields": ("notes",)}),
    )
    readonly_fields = ("completed_on",)

    @admin.display(description="Priority", ordering="priority")
    def priority_badge(self, obj):
        return badge(obj.get_priority_display().split(" (")[0], PRIORITY_COLORS[obj.priority])

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), REQUEST_COLORS[obj.status])

    @admin.display(description="Cost so far (AED)")
    def cost_aed(self, obj):
        return f"{obj.total_cost:,.2f}"

    def save_formset(self, request, form, formset, change):
        if formset.model is not Expense:
            return super().save_formset(request, form, formset, change)
        request_obj = form.instance
        for expense in formset.save(commit=False):
            expense.property = request_obj.property
            expense.unit = request_obj.unit
            expense.maintenance_request = request_obj
            if (
                expense.bearer == Expense.Bearer.OWNER
                and request_obj.property.ownership_type != request_obj.property.Ownership.THIRD_PARTY
            ):
                expense.bearer = Expense.Bearer.COMPANY
                self.message_user(
                    request,
                    "A cost on a company-owned property was set to 'Our company' "
                    "because there is no owner to charge.",
                    messages.INFO,
                )
            expense.save()
        for expense in formset.deleted_objects:
            if not expense.statement_status:
                expense.delete()
        formset.save_m2m()

    @admin.action(description="Mark selected requests as in progress")
    def action_in_progress(self, request, queryset):
        count = queryset.filter(status=MaintenanceRequest.Status.OPEN).update(
            status=MaintenanceRequest.Status.IN_PROGRESS
        )
        self.message_user(request, f"{count} request(s) set to in progress.", messages.SUCCESS)

    @admin.action(description="Mark selected requests as completed today")
    def action_completed(self, request, queryset):
        done = 0
        for item in queryset.exclude(status=MaintenanceRequest.Status.COMPLETED):
            item.status = MaintenanceRequest.Status.COMPLETED
            item.save()
            done += 1
        self.message_user(request, f"{done} request(s) completed.", messages.SUCCESS)


# ---------------------------------------------------------------------------
# Owner statements
# ---------------------------------------------------------------------------


def statement_sections(statement):
    """Group the statement lines by property, with a subtotal for each."""
    zero = Decimal("0.00")
    sections = {}
    for line in statement.lines.select_related("property").order_by("date", "id"):
        key = line.property_id
        section = sections.setdefault(
            key,
            {
                "property": line.property,
                "rent": [],
                "fees": [],
                "expenses": [],
                "rent_total": zero,
                "rent_vat": zero,
                "fee_total": zero,
                "fee_vat": zero,
                "expense_total": zero,
            },
        )
        if line.kind == StatementLine.Kind.RENT:
            section["rent"].append(line)
            section["rent_total"] += line.amount
            section["rent_vat"] += line.vat_amount
        elif line.kind == StatementLine.Kind.FEE:
            section["fees"].append(line)
            section["fee_total"] += line.amount
            section["fee_vat"] += line.vat_amount
        else:
            section["expenses"].append(line)
            section["expense_total"] += line.amount
    result = sorted(sections.values(), key=lambda s: s["property"].name if s["property"] else "")
    for section in result:
        vat = section["rent_vat"] if statement.pay_vat_to_owner else zero
        section["net"] = (
            section["rent_total"]
            + vat
            - section["fee_total"]
            - section["fee_vat"]
            - section["expense_total"]
        )
    return result


class StatementLineInline(UnfoldTabularInline):
    model = StatementLine
    extra = 0
    can_delete = False
    fields = ("date", "property", "kind", "description", "amount", "vat_amount")
    readonly_fields = fields
    verbose_name_plural = "Lines on this statement (use Recalculate to refresh)"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OwnerStatement)
class OwnerStatementAdmin(UnfoldModelAdmin):
    change_form_template = "admin/realestate/ownerstatement/change_form.html"
    date_hierarchy = "period_end"
    list_display = (
        "number",
        "owner",
        "period_start",
        "period_end",
        "status_badge",
        "net_aed",
        "paid_on",
    )
    list_filter = ("status", "owner")
    list_select_related = ("owner",)
    search_fields = ("number", "owner__name", "pay_reference")
    autocomplete_fields = ("owner",)
    inlines = [StatementLineInline]
    fieldsets = (
        (
            "Statement",
            {
                "description": (
                    "Pick the owner and the dates (usually one month). After you "
                    "save, the page fills itself with the rent that cleared and the "
                    "expenses charged to the owner in those dates."
                ),
                "fields": (
                    "owner",
                    "period_start",
                    "period_end",
                    "fee_vat_percent",
                    "pay_vat_to_owner",
                    "notes",
                ),
            },
        ),
        (
            "Payment to the owner",
            {
                "description": "Filled in with the 'Mark as paid' button on this page.",
                "fields": ("paid_on", "pay_method", "pay_reference"),
            },
        ),
    )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display().split(" (")[0], STATEMENT_COLORS[obj.status])

    @admin.display(description="To pay the owner (AED)")
    def net_aed(self, obj):
        return f"{obj.net_payout:,.2f}"

    def get_actions(self, request):
        # Bulk delete would skip the "only drafts can be deleted" rule.
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ("paid_on", "pay_method", "pay_reference")
        if obj.is_draft:
            return ("paid_on", "pay_method", "pay_reference")
        return (
            "owner",
            "period_start",
            "period_end",
            "fee_vat_percent",
            "pay_vat_to_owner",
            "paid_on",
            "pay_method",
            "pay_reference",
        )

    def get_inlines(self, request, obj):
        return [StatementLineInline] if obj else []

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not obj.is_draft:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        rebuild = not change or (
            obj.is_draft
            and {"owner", "period_start", "period_end", "fee_vat_percent"}
            & set(form.changed_data)
        )
        if rebuild:
            count = obj.recalculate()
            self.message_user(
                request,
                f"Statement {obj.number} calculated: {count} line(s) found.",
                messages.SUCCESS,
            )

    # --- the page with buttons ---------------------------------------------

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        if obj is not None:
            extra_context.update(
                {
                    "statement": obj,
                    "totals": obj.totals(),
                    "sections": statement_sections(obj),
                    "stale": obj.is_stale(),
                    "calculated_at": obj.calculated_at,
                    "uncovered": obj.uncovered_items() if obj.is_draft else None,
                    "new_items": obj.new_items() if obj.is_draft else None,
                    "today": timezone.localdate().isoformat(),
                    "pay_methods": OwnerStatement.PayMethod.choices,
                    "can_change": self.has_change_permission(request, obj),
                }
            )
        return super().change_view(request, object_id, form_url, extra_context)

    def get_urls(self):
        custom = [
            path(
                "<int:pk>/do/<str:action>/",
                self.admin_site.admin_view(self.action_view),
                name="realestate_ownerstatement_action",
            ),
            path(
                "<int:pk>/print/",
                self.admin_site.admin_view(self.print_view),
                name="realestate_ownerstatement_print",
            ),
        ]
        return custom + super().get_urls()

    def action_view(self, request, pk, action):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        statement = get_object_or_404(OwnerStatement, pk=pk)
        if not self.has_change_permission(request, statement):
            raise PermissionDenied
        try:
            if action == "recalculate":
                count = statement.recalculate()
                log_change(request, statement, f"Recalculated ({count} lines).")
                messages.success(request, f"Recalculated: {count} line(s).")
            elif action == "approve":
                statement.approve(user=request.user)
                log_change(request, statement, "Approved and locked.")
                messages.success(request, "Statement approved. It is now locked.")
            elif action == "reopen":
                statement.reopen()
                log_change(request, statement, "Reopened as a draft.")
                messages.success(request, "Statement reopened as a draft.")
            elif action == "pay":
                try:
                    paid_on = date.fromisoformat(request.POST.get("paid_on", ""))
                except ValueError:
                    raise ValidationError("Enter the payment date.")
                statement.mark_paid(
                    on=paid_on,
                    method=request.POST.get("pay_method", ""),
                    reference=request.POST.get("pay_reference", ""),
                    user=request.user,
                )
                log_change(
                    request,
                    statement,
                    f"Marked as paid: AED {statement.net_payout:,.2f} by "
                    f"{statement.get_pay_method_display()} {statement.pay_reference}".strip() + ".",
                )
                messages.success(request, "Marked as paid.")
            else:
                messages.error(request, "Unknown action.")
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        return HttpResponseRedirect(
            reverse("admin:realestate_ownerstatement_change", args=[statement.pk])
        )

    def print_view(self, request, pk):
        statement = get_object_or_404(OwnerStatement.objects.select_related("owner"), pk=pk)
        if not self.has_view_permission(request, statement):
            raise PermissionDenied
        context = {
            **self.admin_site.each_context(request),
            "statement": statement,
            "owner": statement.owner,
            "totals": statement.totals(),
            "sections": statement_sections(statement),
            "company_name": admin.site.site_header,
        }
        return TemplateResponse(request, "admin/realestate/ownerstatement/print.html", context)


# ---------------------------------------------------------------------------
# Company settings (one row)
# ---------------------------------------------------------------------------


@admin.register(CompanySettings)
class CompanySettingsAdmin(UnfoldModelAdmin):
    fieldsets = (
        (
            "Fees and VAT",
            {"fields": ("fee_vat_percent", "bounce_fee")},
        ),
        (
            "Early termination of leases",
            {"fields": ("default_notice_days", "penalty_months")},
        ),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # There is only one settings record, so go straight to it.
        if not self.has_view_permission(request):
            raise PermissionDenied
        settings_ = CompanySettings.load()
        return HttpResponseRedirect(
            reverse("admin:realestate_companysettings_change", args=[settings_.pk])
        )
