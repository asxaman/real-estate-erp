"""Lease pages: summary header, timeline, tabs (Overview, Money, Fees and Ejari, Termination, Payments, Documents, Notes, Activity), list, rent payments."""
import calendar
import contextvars
import json
import re
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, Exists, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce, Concat
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from core import sysconf
from core import exports
from core.admin_utils import ExportPermissionMixin, TooltipHelpMixin, badge, circle_avatar, format_amount
from core.formats import fmt_date, fmt_datetime
from core.paging import RowsPerPageMixin
from core.search import SmartSearchMixin
from core.toasts import titled
from core.widgets import use_file_widget
from properties.models import Owner, Property, Unit
from properties.owner_admin import ActivityInline, DocumentInline, ReadOnlyInline, _initials, document_alert
from tenants.models import Tenant

from .api import LeaseApiMixin, PaymentApiMixin
from .reports import LEASE_HEADERS, PAYMENT_HEADERS, ReportsMixin, lease_rows, payment_rows
from .pickers import SearchSelect, property_label, unit_label
from .models import Lease, LeaseDeduction, LeaseDocument, LeaseNote, LeasePayment, running_q

OPEN = (LeasePayment.Status.PENDING, LeasePayment.Status.BOUNCED)
SETTLED = (LeasePayment.Status.PAID, LeasePayment.Status.HELD, LeasePayment.Status.WAIVED)
_last_lease = contextvars.ContextVar("erp_last_lease", default=None)  # the lease of the row drawn just before
ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=14, decimal_places=2))


def _money(value):
    return "AED " + format_amount(value)


def _button(label, act, url, info, tone=""):
    """A small button that opens one of the pop-ups in core/static/core/leases.js."""
    return format_html(
        '<button type="button" class="erp-qbtn{}" data-erp-act="{}" data-url="{}" data-info="{}">{}</button>',
        " erp-qbtn-" + tone if tone else "", act, url, json.dumps(info), label,
    )


def money_differs(a, b):
    return abs((a or 0) - (b or 0)) >= Decimal("0.01")


def progress_bar(paid, total, overdue=0):
    """A thin bar showing how much of the rent has been received."""
    pct = min(100, int(paid * 100 / total)) if total else 0
    tone = " erp-pbar-bad" if overdue else (" erp-pbar-done" if pct >= 100 else "")
    return format_html(
        '<div class="erp-pbar"><div class="erp-pbar-fill{}" style="width:{}%"></div></div>'
        '<div class="erp-pbar-text">{}% · {} of {}</div>',
        tone, pct, pct, format_amount(paid), format_amount(total),
    )


def _pos(day, start, end):
    span = (end - start).days or 1
    return max(0.0, min(100.0, (day - start).days * 100.0 / span))


def timeline(lease, today=None):
    """start → payments → Ejari expiry → end, with today marked."""
    today = today or timezone.localdate()
    start, end = lease.start_date, lease.end_date
    marks = []
    for p in lease.payments.all():
        if not p.is_rent:
            continue  # the strip shows the rent; the deposit and fees are listed under Payments
        if p.status == LeasePayment.Status.PAID:
            kind, word = "ok", "paid"
        elif p.status == LeasePayment.Status.WAIVED:
            kind, word = "ok", "waived"
        elif p.status == LeasePayment.Status.BOUNCED:
            kind, word = "bad", "bounced"
        elif p.is_overdue(today):
            kind, word = "bad", "overdue"
        else:
            kind, word = "wait", "pending"
        marks.append((_pos(p.due_date, start, end), kind, f"{p.label}: {fmt_date(p.due_date)} · {_money(p.total)} · {word}"))
    if lease.ejari_expiry and start <= lease.ejari_expiry <= end:
        marks.append((_pos(lease.ejari_expiry, start, end), "ejari", f"Ejari expires {fmt_date(lease.ejari_expiry)}"))
    if lease.status == Lease.Status.TERMINATED and lease.terminated_on:
        marks.append((_pos(lease.terminated_on, start, end), "stop", f"Terminated {fmt_date(lease.terminated_on)}"))
    if start <= today <= end:
        marks.append((_pos(today, start, end), "today", f"Today: {fmt_date(today)}"))
    dots = format_html_join(
        "", '<span class="erp-tl-m erp-tl-{}" style="left:{}%" title="{}"></span>',
        ((kind, f"{pos:.2f}", tip) for pos, kind, tip in marks),
    )
    return format_html(
        '<div class="erp-tl"><div class="erp-tl-line">{}</div>'
        '<div class="erp-tl-ends"><span>{}</span><span>{}</span></div>'
        '<div class="erp-tl-key"><span><i class="erp-tl-m erp-tl-ok"></i>Paid</span><span><i class="erp-tl-m erp-tl-wait"></i>Pending</span>'
        '<span><i class="erp-tl-m erp-tl-bad"></i>Overdue / bounced</span><span><i class="erp-tl-m erp-tl-ejari"></i>Ejari expiry</span>'
        '<span><i class="erp-tl-m erp-tl-today"></i>Today</span></div></div>',
        dots, fmt_date(start), fmt_date(end),
    )


def payment_info(payment, today=None):
    today = today or timezone.localdate()
    return {
        "code": payment.lease.code, "part": payment.part, "label": payment.label, "held": payment.status == LeasePayment.Status.HELD,
        "amount": _money(payment.total), "due": fmt_date(payment.due_date),
        "method": payment.method, "cheque": payment.cheque_number, "bank": payment.bank_name,
        "overdue": payment.is_overdue(today), "today": today.isoformat(),
    }


def record_button(payment, label="Record payment", tone="primary", choices=None):
    """`choices`: on a lease page, the lease's other unpaid payments, so the pop-up can switch between them."""
    if payment is None or payment.status in (LeasePayment.Status.PAID, LeasePayment.Status.WAIVED):
        return ""
    if payment.status == LeasePayment.Status.HELD:
        label = "Cash cheque"
    info = payment_info(payment)
    if choices:
        info["pick"] = [
            dict(payment_info(p), url=reverse("admin:leases_leasepayment_record", args=[p.pk]))
            for p in choices
        ]
    return _button(label, "record", reverse("admin:leases_leasepayment_record", args=[payment.pk]), info, tone)


def edit_button(payment, label="Edit"):
    return _button(label, "editpay", reverse("admin:leases_leasepayment_edit", args=[payment.pk]), {"code": payment.lease.code, "part": payment.part, "label": payment.label})


def due_cell(payment, today=None):
    """The due date, coloured, with 'in 3 days' / '5 days overdue' underneath."""
    today = today or timezone.localdate()
    days = (payment.due_date - today).days
    if payment.status == LeasePayment.Status.PAID:
        tone, note = "ok", "paid"
    elif payment.status == LeasePayment.Status.WAIVED:
        tone, note = "mute", "waived"
    elif payment.status == LeasePayment.Status.BOUNCED:
        tone, note = "bad", "bounced"
    elif payment.status == LeasePayment.Status.HELD:
        tone, note = "mute", "cheque held"
    elif days < 0:
        tone, note = "bad", f"{-days} day{'s' if days != -1 else ''} overdue"
    elif days == 0:
        tone, note = "warn", "due today"
    elif days <= 7:
        tone, note = "warn", f"in {days} day{'s' if days != 1 else ''}"
    else:
        tone, note = "mute", f"in {days} days"
    return format_html('<span class="erp-due erp-due-{}"><b>{}</b><small>{}</small></span>', tone, fmt_date(payment.due_date), note)


def pay_cell(payment):
    """How it is paid in one cell: 'Cheque · 004512 · ENBD' with the cheque and deposit dates in small text."""
    if payment.method == LeasePayment.Method.CHEQUE:
        main = " · ".join(part for part in ("Cheque", payment.cheque_number or "no number yet", payment.bank_name) if part)
        small = []
        if payment.cheque_date:
            small.append(f"dated {fmt_date(payment.cheque_date)}")
        if payment.deposited_on:
            small.append(f"deposited {fmt_date(payment.deposited_on)}")
    else:
        main, small = payment.get_method_display(), []
    return format_html('<span class="erp-pay"><b>{}</b><small>{}</small></span>', main, " · ".join(small))


def state_cell(payment, today=None):
    """Status badge with the paid date, or the days late and the late fee, underneath."""
    today = today or timezone.localdate()
    if payment.status == LeasePayment.Status.PAID:
        label, tone, small = "Paid", "success", f"on {fmt_date(payment.paid_on)}" if payment.paid_on else ""
    elif payment.status == LeasePayment.Status.WAIVED:
        label, tone, small = "Waived", "info", "written off"
    elif payment.status == LeasePayment.Status.BOUNCED:
        label, tone, small = "Bounced", "danger", ""
    elif payment.status == LeasePayment.Status.HELD:
        label, tone, small = "Cheque held", "warning", f"since {fmt_date(payment.paid_on)}" if payment.paid_on else "not cashed"
    elif payment.is_overdue(today):
        days = payment.days_overdue(today)
        fee = payment.late_fee(today)
        label, tone = "Overdue", "danger"
        small = f"{days} day{'s' if days != 1 else ''} late" + (f" · fee {_money(fee)}" if fee else "")
    else:
        label, tone, small = "Pending", "neutral", ""
    return format_html('<span class="erp-state">{}<small>{}</small></span>', badge(label, tone), small)


def note_mark(payment):
    if not payment.notes:
        return ""
    return format_html('<span class="erp-note" title="{}">i</span>', payment.notes)


def lease_info(lease, today=None):
    """What the Renew / Terminate / Cheques pop-ups need to know (read from data-info in the page)."""
    today = today or timezone.localdate()
    length = (lease.end_date - lease.start_date).days
    new_start = lease.end_date + timedelta(days=1)
    payments = list(lease.payments.all())
    return {
        "code": lease.code, "tenant": lease.tenant.name, "rent": str(lease.annual_rent),
        "start": lease.start_date.isoformat(), "end": lease.end_date.isoformat(), "today": today.isoformat(),
        "new_start_text": fmt_date(new_start), "new_end_text": fmt_date(new_start + timedelta(days=length)),
        "deposit": lease.deposit_status, "unpaid": sum(1 for p in payments if p.is_rent and p.status not in SETTLED),
        "pending_later": sum(1 for p in payments if p.is_rent and p.status == LeasePayment.Status.PENDING and p.due_date > today),
    }


# ---------------------------------------------------------------------------------------
# Tabs (inlines)
# ---------------------------------------------------------------------------------------
class LeasePaymentInline(ReadOnlyInline):
    """The payment schedule as a compact table. Each row has Record / Edit buttons that open a pop-up."""

    model = LeasePayment
    tab = True
    verbose_name_plural = "Payments"
    fields = ("no", "due", "amount_cell", "pay", "state", "actions_cell")
    readonly_fields = fields
    ordering = ("due_date", "part")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("lease")

    @admin.display(description="#")
    def no(self, obj):
        # The hidden span carries the facts the page script uses for the summary cards, filters and totals.
        today = timezone.localdate()
        return format_html(
            '<span class="erp-rowmeta" hidden data-kind="{}" data-status="{}" data-method="{}" data-total="{}" '
            'data-due="{}" data-overdue="{}"></span>{}',
            obj.kind, obj.status, obj.method, obj.total, obj.due_date.isoformat(),
            "1" if obj.is_overdue(today) else "0",
            obj.part if obj.is_rent else obj.get_kind_display(),
        )

    @admin.display(description="Due on")
    def due(self, obj):
        return due_cell(obj)

    @admin.display(description="Amount")
    def amount_cell(self, obj):
        if obj.vat:
            return format_html('<span class="erp-pay"><b>{}</b><small>{} + {} VAT</small></span>', _money(obj.total), format_amount(obj.amount), format_amount(obj.vat))
        return _money(obj.amount)

    @admin.display(description="Payment")
    def pay(self, obj):
        return pay_cell(obj)

    @admin.display(description="Status")
    def state(self, obj):
        return state_cell(obj)

    @admin.display(description="")
    def actions_cell(self, obj):
        if obj is None or not obj.pk:
            return ""
        return format_html(
            '<span class="erp-rowacts">{}{}{}</span>', note_mark(obj), record_button(obj, "Record", "primary"), edit_button(obj),
        )


class LeaseDocumentForm(forms.ModelForm):
    class Meta:
        model = LeaseDocument
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        use_file_widget(self, "file", compact=True, accept=".pdf,.jpg,.jpeg,.png,.webp")


class LeaseDocumentInline(DocumentInline):
    model = LeaseDocument
    form = LeaseDocumentForm


class LeaseActivityInline(ActivityInline):
    fk_name = "related_lease"


class DeductionInline(TabularInline):
    """Fines, money kept from the deposit and other charges. Each one also appears in Tenant Payments."""

    model = LeaseDeduction
    tab = True
    extra = 0
    fields = ("date", "kind", "reason", "note", "amount")
    verbose_name_plural = "Deductions"


class NoteForm(forms.ModelForm):
    class Meta:
        model = LeaseNote
        fields = ("text",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["text"].widget.attrs.update({"rows": 2, "placeholder": "e.g. Tenant called, will pay on Friday"})


class NoteInline(TabularInline):
    """Dated notes, newest first. New notes are stamped with who wrote them and when; saved notes cannot be edited."""

    model = LeaseNote
    form = NoteForm
    tab = True
    extra = 1
    fields = ("text", "written_by", "written_on")
    readonly_fields = ("written_by", "written_on")
    verbose_name_plural = "Notes"

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Written by")
    def written_by(self, obj):
        return obj.author if obj and obj.pk else "-"

    @admin.display(description="When")
    def written_on(self, obj):
        return fmt_datetime(obj.created_at) if obj and obj.pk else "-"


# ---------------------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------------------
class StateFilter(admin.SimpleListFilter):
    title = "State"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return (
            ("active", "Active"), ("soon", f"Ending within {sysconf.expiry_days()} days"),
            ("upcoming", "Not started yet"), ("expired", "Expired"), ("terminated", "Terminated early"),
        )

    def queryset(self, request, queryset):
        today = timezone.localdate()
        running = Q(status=Lease.Status.ACTIVE, start_date__lte=today, end_date__gte=today)
        return {
            "active": lambda: queryset.filter(running_q(today)),
            "soon": lambda: queryset.filter(running, end_date__lte=today + timedelta(days=sysconf.expiry_days())),
            "upcoming": lambda: queryset.filter(status=Lease.Status.ACTIVE, start_date__gt=today),
            "expired": lambda: queryset.filter(status=Lease.Status.ACTIVE, end_date__lt=today),
            "terminated": lambda: queryset.filter(status=Lease.Status.TERMINATED),
        }.get(self.value(), lambda: queryset)()


class OverdueFilter(admin.SimpleListFilter):
    title = "Payments"
    parameter_name = "payments"

    def lookups(self, request, model_admin):
        return (("overdue", "Has overdue payments"), ("bounced", "Has a bounced cheque"))

    def queryset(self, request, queryset):
        today = timezone.localdate()
        payments = LeasePayment.objects.filter(lease=OuterRef("pk"))
        if self.value() == "overdue":
            return queryset.filter(Exists(payments.filter(status__in=OPEN, due_date__lt=today)))
        if self.value() == "bounced":
            return queryset.filter(Exists(payments.filter(status=LeasePayment.Status.BOUNCED)))
        return queryset


class PaymentOverdueFilter(admin.SimpleListFilter):
    title = "Due"
    parameter_name = "due"

    def lookups(self, request, model_admin):
        return (("overdue", "Overdue"), ("week", "Due in the next 7 days"), ("month", "Due in the next 30 days"))

    def queryset(self, request, queryset):
        today = timezone.localdate()
        open_ = queryset.filter(status__in=OPEN)
        if self.value() == "overdue":
            return open_.filter(due_date__lt=today)
        if self.value() == "week":
            return open_.filter(due_date__gte=today, due_date__lte=today + timedelta(days=7))
        if self.value() == "month":
            return open_.filter(due_date__gte=today, due_date__lte=today + timedelta(days=30))
        return queryset


def month_choices(back=6, ahead=12):
    """('2026-11', 'November 2026') for a few months either side of today."""
    today = timezone.localdate()
    out = []
    for offset in range(-back, ahead + 1):
        year, month = divmod(today.year * 12 + today.month - 1 + offset, 12)
        out.append((f"{year}-{month + 1:02d}", f"{calendar.month_name[month + 1]} {year}"))
    return out


class BuildingFilter(admin.SimpleListFilter):
    title = "Building"
    parameter_name = "building"
    field = "property_id"  # where a lease or payment keeps its building
    unit_field = "unit__property_id"

    def lookups(self, request, model_admin):
        return list(Property.objects.filter(leases__isnull=False).distinct().order_by("name").values_list("pk", "name"))

    def queryset(self, request, queryset):
        value = self.value() or ""
        if not value.isdigit():
            return queryset
        return queryset.filter(Q(**{self.field: int(value)}) | Q(**{self.unit_field: int(value)}))


class OwnerFilter(admin.SimpleListFilter):
    title = "Owner"
    parameter_name = "owner"
    field = "property__owner_id"

    def lookups(self, request, model_admin):
        return list(Owner.objects.filter(properties__leases__isnull=False).distinct().order_by("name").values_list("pk", "name"))

    def queryset(self, request, queryset):
        return queryset.filter(**{self.field: int(self.value())}) if (self.value() or "").isdigit() else queryset


class MonthFilter(admin.SimpleListFilter):
    field = "due_date"

    def lookups(self, request, model_admin):
        return month_choices()

    def queryset(self, request, queryset):
        match = re.fullmatch(r"(\d{4})-(\d{2})", self.value() or "")
        if not match or not 1 <= int(match.group(2)) <= 12:
            return queryset
        return queryset.filter(**{f"{self.field}__year": int(match.group(1)), f"{self.field}__month": int(match.group(2))})


class LeaseBuildingFilter(BuildingFilter):
    field = "property_id"


class PaymentBuildingFilter(BuildingFilter):
    field = "lease__property_id"
    unit_field = "lease__unit__property_id"


class LeaseOwnerFilter(OwnerFilter):
    field = "property__owner_id"


class PaymentOwnerFilter(OwnerFilter):
    field = "lease__property__owner_id"


class EndMonthFilter(MonthFilter):
    title = "Ends in"
    parameter_name = "ends"
    field = "end_date"


class DueMonthFilter(MonthFilter):
    title = "Due in"
    parameter_name = "month"
    field = "due_date"


def build_filter_bar(cl):
    """The list's filters as drop-downs for the bar above the table (read from the change list's own filters)."""
    bar = []
    for spec in cl.filter_specs:
        choices = list(spec.choices(cl))
        if len(choices) < 2:
            continue
        options = [(c["query_string"], c["display"], bool(c["selected"])) for c in choices]
        bar.append({"title": spec.title, "options": options, "active": any(sel for _, _, sel in options[1:])})
    return bar


class FilterBarMixin(SmartSearchMixin, RowsPerPageMixin):
    """Drop-down filters above the table and a 'rows per page' choice (50 / 100 / 200, default 100)."""

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        data = getattr(response, "context_data", None)
        if data is not None and data.get("cl") is not None:
            data["filter_bar"] = build_filter_bar(data["cl"])
        return response


class ReceivedFilter(admin.SimpleListFilter):
    title = "Received"
    parameter_name = "received"

    def lookups(self, request, model_admin):
        return (("month", "This month"), ("lastmonth", "Last month"))

    def queryset(self, request, queryset):
        today = timezone.localdate()
        first = today.replace(day=1)
        if self.value() == "month":
            return queryset.filter(status=LeasePayment.Status.PAID, paid_on__year=first.year, paid_on__month=first.month)
        if self.value() == "lastmonth":
            last = first - timedelta(days=1)
            return queryset.filter(status=LeasePayment.Status.PAID, paid_on__year=last.year, paid_on__month=last.month)
        return queryset


# ---------------------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------------------
class LeaseAdminForm(forms.ModelForm):
    class Meta:
        model = Lease
        fields = "__all__"
        widgets = {
            "tenant": SearchSelect(attrs={"data-erp-native": "1", "data-erp-search": "tenant", "data-placeholder": "Type a name, phone or email"}),
            "unit": SearchSelect(attrs={"data-erp-native": "1", "data-erp-search": "unit", "data-placeholder": "Type a unit or building"}),
            "property": SearchSelect(attrs={"data-erp-native": "1", "data-erp-search": "property", "data-placeholder": "Type a property name or code"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance
        # Only tenants who can sign a new lease; the lease's own tenant stays in the list.
        tenants = Q(is_active=True, is_blacklisted=False)
        if current.pk:
            tenants |= Q(pk=current.tenant_id)
        self.fields["tenant"].queryset = Tenant.objects.filter(tenants).order_by("name")
        units = Unit.objects.select_related("property").filter(property__is_active=True)
        if current.pk and current.unit_id:
            units = Unit.objects.select_related("property").filter(Q(property__is_active=True) | Q(pk=current.unit_id))
        self.fields["unit"].queryset = units.order_by("property__code", "floor_sort", "unit_number")
        self.fields["unit"].label_from_instance = unit_label
        standalone = Property.objects.exclude(property_type__in=Property.BUILDING_TYPES).filter(is_active=True)
        if current.pk and current.property_id:
            standalone = Property.objects.exclude(property_type__in=Property.BUILDING_TYPES).filter(
                Q(is_active=True) | Q(pk=current.property_id)
            )
        self.fields["property"].queryset = standalone.order_by("code")
        self.fields["property"].label_from_instance = property_label
        hints = {
            "annual_rent": "e.g. 65000",
            "security_deposit": "0 if none",
            "ejari_fee": "0 if none",
            "ejari_number": "e.g. 10-1234-5678",
            "termination_reason": "Why the lease ended early",
        }
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        if "notes" in self.fields:
            self.fields["notes"].widget.attrs["rows"] = 3


@admin.register(Lease)
class LeaseAdmin(LeaseApiMixin, ReportsMixin, ExportPermissionMixin, FilterBarMixin, TooltipHelpMixin, ModelAdmin):
    form = LeaseAdminForm
    compressed_fields = True
    list_display = ("code", "tenant_name", "rented", "period", "rent", "state_badge", "payments_badge", "quick")
    list_before_template = "leases/chips.html"
    list_display_links = ("code",)
    list_filter = (StateFilter, OverdueFilter, LeaseBuildingFilter, LeaseOwnerFilter, EndMonthFilter, "vat_applies", "installments")
    search_fields = (
        "code", "tenant__name", "tenant__code", "tenant__email", "tenant__phone", "property__name", "property__code",
        "unit__unit_number", "ejari_number",
    )
    list_select_related = ("tenant", "unit__property", "property")
    search_text = (
        "code", "tenant__name", "tenant__code", "tenant__email", "tenant__phone", "property__name", "property__code",
        "unit__unit_number", "ejari_number",
    )
    search_digits = ("tenant__phone",)
    search_aliases = {"_ref": Concat("unit__property__code", Value("-"), "unit__unit_number")}
    search_alias_fields = ("_ref",)
    search_codes = (("code", "LSE", 4),)
    search_fuzzy = ("tenant__name", "property__name")
    search_hint = "lease number (LSE-12), tenant name or phone, unit (RMT-101), building, Ejari number"
    inlines = [LeasePaymentInline, DeductionInline, LeaseDocumentInline, NoteInline, LeaseActivityInline]
    actions = ["renew_lease", "rebuild_schedule", "export_excel", "export_csv"]

    _header = (None, {"fields": ("summary_header", "timeline_strip")})
    _money = ("Money", {"classes": ["tab"], "fields": ("money_summary",)})
    _overview = (
        "Overview",
        {
            "classes": ["tab"],
            "fields": (
                ("lease_no", "status"),
                "tenant",
                ("unit", "property"),
                ("start_date", "end_date"),
                ("annual_rent", "installments"),
                "vat_applies",
                "notes",
            ),
        },
    )
    _fees = (
        "Deposit, fees and Ejari",
        {
            "classes": ["tab"],
            "description": "The deposit and the fees become payment lines (see the Payments tab). Record when and how they "
            "are received there or in Tenant Payments: cash, cheque cashed, or a cheque held and not cashed. "
            "Fines, deductions and refunds are added on the Payments and Deductions tabs.",
            "fields": (
                "security_deposit",
                ("commission", "commission_vat"),
                ("admin_fee", "admin_fee_vat"),
                "ejari_fee",
                ("ejari_number", "ejari_expiry"),
                "deposit_summary",
            ),
        },
    )
    _termination = (
        "Termination",
        {
            "classes": ["tab"],
            "description": "Only when the lease ends before its end date: set Status to \"Terminated early\" on the "
            "Overview tab, then enter the date and the reason here.",
            "fields": (("terminated_on", "termination_reason"),),
        },
    )

    LOCKED_NOTE = (
        "The annual rent and the number of payments are fixed once the payments exist, so the two can never "
        "disagree. To change the rent, edit the payments themselves on the Payments tab (Edit on each line, or "
        "Add Payment); the annual rent then becomes the total of the rent payments."
    )
    DATES_LOCKED_NOTE = (
        " A payment has been received, so the start and end dates are fixed too. For a new period, renew the lease."
    )

    def get_fieldsets(self, request, obj=None):
        if obj is not None:
            overview = self._overview
            if obj.has_rent_schedule():
                note = self.LOCKED_NOTE + (self.DATES_LOCKED_NOTE if obj.has_received_money() else "")
                overview = (overview[0], dict(overview[1], description=note))
            return (self._header, overview, self._money, self._fees, self._termination)
        return (self._overview, self._fees, self._termination)

    def get_readonly_fields(self, request, obj=None):
        fixed = ("lease_no", "summary_header", "timeline_strip", "money_summary", "deposit_summary")
        if obj is not None and obj.has_rent_schedule():
            fixed += ("annual_rent", "installments")
            if obj.has_received_money():
                fixed += ("start_date", "end_date")
        return fixed

    @admin.display(description="Lease no.")
    def lease_no(self, obj):
        if obj is not None and obj.pk and obj.code:
            return obj.code
        return mark_safe(
            '<span class="erp-autonote"><span class="erp-autoicon" title="The system numbers every lease by itself, '
            'in order (LSE-0001, LSE-0002 ...). You cannot type it.">i</span>'
            "Created automatically when you save</span>"
        )

    # ---- one lease's payments on a page of their own ------------------------------------
    def get_urls(self):
        mine = [path("<int:pk>/payments/", self.admin_site.admin_view(self.payments_view), name="leases_lease_payments")]
        return mine + super().get_urls()

    def payments_view(self, request, pk):
        """Every payment line of one lease (rent, deposit, fees) with totals, and the buttons to receive them."""
        lease = get_object_or_404(Lease.objects.select_related("tenant", "unit__property", "property"), pk=pk)
        if not (self.has_view_permission(request, lease) and request.user.has_perm("leases.view_leasepayment")):
            raise PermissionDenied
        today = timezone.localdate()
        can_change = self.has_change_permission(request, lease)
        payments = list(lease.payments.all())
        done = (LeasePayment.Status.PAID,)
        rows = []
        for p in payments:
            p.lease = lease  # (already loaded: no extra query)
            buttons = ""
            if can_change:
                buttons = format_html("{}{}", record_button(p, "Receive", "primary"), edit_button(p))
            rows.append({
                "kind": p.get_kind_display(), "no": p.part if p.is_rent else "", "due": due_cell(p, today),
                "amount": format_html("<b>{}</b>", _money(p.total)), "vat": p.vat, "pay": pay_cell(p),
                "state": state_cell(p, today), "buttons": buttons, "note": note_mark(p), "rent": p.is_rent,
            })

        def total(items):
            return sum((p.total for p in items), Decimal("0"))

        rent = [p for p in payments if p.is_rent]
        other = [  # money asked of the tenant: not a refund (it goes out) and not money kept from the deposit
            p for p in payments
            if not p.is_rent and p.kind != LeasePayment.Kind.REFUND and p.method != LeasePayment.Method.FROM_DEPOSIT
        ]
        cards = []
        for title, items in (("Rent", rent), ("Deposit and Fees", other)):
            got = total(p for p in items if p.status in done)
            held = total(p for p in items if p.status == LeasePayment.Status.HELD)
            cards.append({
                "title": title, "due": _money(total(items)), "got": _money(got), "held": _money(held) if held else "",
                "left": _money(max(total(items) - got - held, Decimal("0"))), "any": bool(items),
            })
        add_button = ""
        if request.user.has_perm("leases.add_leasepayment"):
            add_button = _button(
                "Add Payment", "addpay", reverse("admin:leases_leasepayment_add_to", args=[lease.pk]),
                {"code": lease.code, "today": today.isoformat()},
            )
        context = {
            **self.admin_site.each_context(request),
            "title": f"{lease.code} Payments", "lease": lease, "rows": rows, "cards": cards, "add_button": add_button,
            "lease_url": reverse("admin:leases_lease_change", args=[lease.pk]),
            "list_url": reverse("admin:leases_leasepayment_changelist"),
            "tenant_url": reverse("admin:tenants_tenant_change", args=[lease.tenant_id]),
            "period": f"{fmt_date(lease.start_date)} → {fmt_date(lease.end_date)}",
        }
        return TemplateResponse(request, "leases/lease_payments.html", context)

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if obj is None:  # the schedule is created when the lease is first saved
            instances = [i for i in instances if not isinstance(i, (LeasePaymentInline, LeaseActivityInline, NoteInline, DeductionInline))]
        return instances

    def get_queryset(self, request):
        today = timezone.localdate()
        return super().get_queryset(request).prefetch_related("payments", "renewals").annotate(
            _paid=Coalesce(Sum("payments__amount", filter=Q(payments__status=LeasePayment.Status.PAID, payments__kind=LeasePayment.Kind.RENT)), ZERO),
            _overdue=Count("payments", filter=Q(payments__status__in=OPEN, payments__due_date__lt=today), distinct=True),
        )

    def save_formset(self, request, form, formset, change):
        if formset.model is not LeaseNote:
            return super().save_formset(request, form, formset, change)
        for note in formset.save(commit=False):
            if not note.pk:
                note.author = request.user.get_full_name() or request.user.get_username()
            note.save()
        for note in formset.deleted_objects:
            note.delete()
        formset.save_m2m()

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.payments.filter(status=LeasePayment.Status.PAID).exists():
            return False  # a lease with received rent is kept for the records: terminate it instead
        return super().has_delete_permission(request, obj)

    def get_deleted_objects(self, objs, request):
        """Says in plain words why a lease with payments is kept (the page shows this sentence)."""
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        leases = list(objs)
        lines = LeasePayment.objects.filter(lease__in=leases)
        count = lines.count()
        if count and perms_needed:
            received = lines.filter(status__in=(LeasePayment.Status.PAID, LeasePayment.Status.HELD)).count()
            who = leases[0].code if len(leases) == 1 else f"The {len(leases)} selected leases"
            verb = "has" if len(leases) == 1 else "have"
            perms_needed = {p for p in perms_needed if str(p).lower() != str(LeasePayment._meta.verbose_name).lower()}
            perms_needed.add(
                f"{who} {verb} {count} payment line{'s' if count != 1 else ''}"
                + (f", {received} already received or held" if received else "")
                + ". A lease with payments is kept for the records. If it ended early, terminate it instead. "
                "If it was made by mistake, remove its unreceived payments first (Tenant payments: tick them, then Remove Ticked); "
                "received payments can never be removed."
            )
        return to_delete, model_count, perms_needed, protected

    # ---- list columns ----------------------------------------------------------------
    @admin.display(description="Tenant", ordering="tenant__name")
    def tenant_name(self, obj):
        return obj.tenant.name

    @admin.display(description="Rented")
    def rented(self, obj):
        return obj.item_name

    @admin.display(description="Period", ordering="start_date")
    def period(self, obj):
        return f"{fmt_date(obj.start_date)} → {fmt_date(obj.end_date)}"

    @admin.display(description="Annual rent", ordering="annual_rent")
    def rent(self, obj):
        return _money(obj.annual_rent)

    @display(
        description="State",
        label={"Active": "success", "Ending soon": "warning", "Not started": "info", "Expired": "danger",
               "Terminated": "danger"},
    )
    def state_badge(self, obj):
        state = obj.state()
        if state == "active":
            return "Ending soon" if obj.ends_soon() else "Active"
        return {"upcoming": "Not started", "expired": "Expired", "terminated": "Terminated"}[state]

    @admin.display(description="Payments")
    def payments_badge(self, obj):
        rent = [p for p in obj.payments.all() if p.is_rent]
        paid = sum((p.total for p in rent if p.status == LeasePayment.Status.PAID), Decimal("0"))
        scheduled = sum((p.total for p in rent if p.status != LeasePayment.Status.WAIVED), Decimal("0"))
        bar = progress_bar(paid, scheduled, obj._overdue)
        if obj._overdue:
            return format_html("{}{}", bar, badge(f"{obj._overdue} overdue", "danger"))
        return bar

    @admin.display(description="Quick actions")
    def quick(self, obj):
        today = timezone.localdate()
        buttons = []
        unpaid = sorted(
            (p for p in obj.payments.all() if p.status not in SETTLED),
            key=lambda p: (p.due_date, p.part),
        )
        if unpaid:
            first = unpaid[0]
            first.lease = obj  # (already loaded: no extra query)
            buttons.append(record_button(first, "Record payment", "primary"))
        if obj.status == Lease.Status.ACTIVE and not obj.renewals.all():
            buttons.append(_button("Renew", "renew", reverse("admin:leases_lease_renew", args=[obj.pk]), lease_info(obj, today)))
        if not buttons:
            return "-"
        return format_html_join(" ", "{}", ((b,) for b in buttons))

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context["lease_chips"] = self._chips(request)
        extra_context["report_links"] = self.report_links()
        return super().changelist_view(request, extra_context)

    def _chips(self, request):
        """The quick-filter buttons above the list, each with how many leases it shows."""
        today = timezone.localdate()
        running = Q(status=Lease.Status.ACTIVE, start_date__lte=today, end_date__gte=today)
        counts = Lease.objects.aggregate(
            all=Count("pk"),
            active=Count("pk", filter=running_q(today)),
            soon=Count("pk", filter=running & Q(end_date__lte=today + timedelta(days=sysconf.expiry_days()))),
            upcoming=Count("pk", filter=Q(status=Lease.Status.ACTIVE, start_date__gt=today)),
            expired=Count("pk", filter=Q(status=Lease.Status.ACTIVE, end_date__lt=today)),
            terminated=Count("pk", filter=Q(status=Lease.Status.TERMINATED)),
        )
        overdue = Lease.objects.filter(
            Exists(LeasePayment.objects.filter(lease=OuterRef("pk"), status__in=OPEN, due_date__lt=today))
        ).count()
        state, payments = request.GET.get("state", ""), request.GET.get("payments", "")
        current = "overdue" if payments == "overdue" else state
        chips = [
            ("", "All", counts["all"], "neutral", "?"),
            ("active", "Active", counts["active"], "success", "?state=active"),
            ("soon", f"Ending in {sysconf.expiry_days()} days", counts["soon"], "warning", "?state=soon"),
            ("overdue", "Overdue payments", overdue, "danger", "?payments=overdue"),
            ("upcoming", "Not started", counts["upcoming"], "info", "?state=upcoming"),
            ("expired", "Expired", counts["expired"], "neutral", "?state=expired"),
            ("terminated", "Terminated", counts["terminated"], "neutral", "?state=terminated"),
        ]
        return [
            {"label": label, "count": count, "tone": tone, "url": url, "active": key == current}
            for key, label, count, tone, url in chips
        ]

    # ---- timeline, money and deposit panels ----------------------------------------------
    @admin.display(description="")
    def timeline_strip(self, obj):
        return timeline(obj) if obj is not None and obj.pk else ""

    @admin.display(description="")
    def money_summary(self, obj):
        if obj is None or not obj.pk:
            return ""
        today = timezone.localdate()
        payments = list(obj.payments.all())
        rent_lines = [p for p in payments if p.is_rent]
        paid = sum((p.total for p in rent_lines if p.status == LeasePayment.Status.PAID), Decimal("0"))
        scheduled = sum((p.total for p in rent_lines if p.status != LeasePayment.Status.WAIVED), Decimal("0"))
        scheduled_rent = sum((p.amount for p in rent_lines), Decimal("0"))
        rows = [
            ("Annual rent", _money(obj.annual_rent), ""),
            ("VAT (5%)" if obj.vat_applies else "VAT (not applied)", _money(obj.vat_amount), ""),
            ("Rent with VAT", _money(obj.total_with_vat), "strong"),
            ("Rent received so far", _money(paid), "good"),
            ("Rent still to receive", _money(max(scheduled - paid, Decimal("0"))), "strong"),
        ]
        for line in obj.charge_lines():
            word = {"paid": "received", "held": "cheque held", "pending": "not received yet", "bounced": "bounced", "waived": "waived"}[line.status]
            rows.append((line.get_kind_display(), f"{_money(line.amount)} · {word}", "good" if line.status == LeasePayment.Status.PAID else ""))
        late = obj.late_fees(today)
        if late:
            rows.append(("Late fees built up", _money(late), "bad"))
        table = format_html(
            '<table class="erp-sum">{}</table>',
            format_html_join("", '<tr class="{}"><th>{}</th><td>{}</td></tr>', ((tone, label, value) for label, value, tone in rows)),
        )
        note = ""
        if payments and money_differs(scheduled_rent, obj.annual_rent):
            note = format_html(
                '<div class="erp-sum-warn">The payments add up to {} but the annual rent is {}. '
                "Check the payment amounts, or rebuild the schedule.</div>",
                _money(scheduled_rent), _money(obj.annual_rent),
            )
        return format_html("{}{}", table, note)

    @admin.display(description="Deposit")
    def deposit_summary(self, obj):
        if obj is None or not obj.pk or not obj.security_deposit:
            return "-"
        deducted = obj.deposit_deducted()
        line = next((p for p in obj.payments.all() if p.kind == LeasePayment.Kind.DEPOSIT), None)
        received = {"paid": "Received", "held": "Cheque held, not cashed", "pending": "Not received yet", "bounced": "Cheque bounced", "waived": "Waived"}
        rows = [
            ("Security deposit", _money(obj.security_deposit)),
            ("Received?", received.get(line.status, "-") if line else "No payment line"),
            ("Kept from it (see the Deductions tab)", _money(deducted)),
            ("Refunded (Deposit Refund payments)", _money(obj.deposit_refunded())),
            ("Still held", _money(obj.deposit_balance())),
        ]
        table = format_html(
            '<table class="erp-sum">{}</table>',
            format_html_join("", "<tr><th>{}</th><td>{}</td></tr>", rows),
        )
        warn = ""
        if deducted + obj.deposit_refunded() > obj.security_deposit:
            warn = format_html('<div class="erp-sum-warn">Deductions and the refund together are more than the deposit.</div>')
        return format_html("{}{}", table, warn)

    # ---- summary header --------------------------------------------------------------
    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        today = timezone.localdate()
        payments = list(obj.payments.all())
        paid = sum((p.total for p in payments if p.is_rent and p.status == LeasePayment.Status.PAID), Decimal("0"))
        scheduled = sum((p.total for p in payments if p.is_rent and p.status != LeasePayment.Status.WAIVED), Decimal("0"))
        overdue = [p for p in payments if p.is_overdue(today)]
        upcoming = [p for p in payments if p.status == LeasePayment.Status.PENDING and p.due_date >= today]

        state = obj.state(today)
        tone = {"active": "success", "upcoming": "info", "expired": "danger", "terminated": "danger"}[state]
        label = {"active": "Active", "upcoming": "Not started yet", "expired": "Expired", "terminated": "Terminated early"}[state]
        pills = [badge(label, tone)]
        if obj.ends_soon(today):
            pills.append(badge(f"Ends in {obj.days_left(today)} days", "warning"))
        if obj.vat_applies:
            pills.append(badge("VAT 5%", "info"))
        if overdue:
            pills.append(badge(f"{len(overdue)} payment(s) overdue", "danger"))
        if obj.ejari_expiry and obj.ejari_expiry < today:
            pills.append(badge("Ejari expired", "danger"))

        tenant_url = reverse("admin:tenants_tenant_change", args=[obj.tenant_id])
        sub = format_html(
            '<a href="{}">{}</a> · {} · {} → {}',
            tenant_url, obj.tenant.name, obj.item_name, fmt_date(obj.start_date), fmt_date(obj.end_date),
        )
        notes = []
        if obj.status == Lease.Status.TERMINATED:
            notes.append(("#b91c1c", f"Terminated on {fmt_date(obj.terminated_on)}: {obj.termination_reason}"))
        if obj.renewed_from_id:
            notes.append(("#6b7280", f"Renewal of {obj.renewed_from.code}"))
        renewal = obj.renewals.first()
        if renewal:
            notes.append(("#6b7280", f"Renewed as {renewal.code}"))
        if upcoming:
            notes.append(("#6b7280", f"Next payment: {fmt_date(upcoming[0].due_date)} · {_money(upcoming[0].total)}"))
        extra = format_html_join("", '<div class="erp-oh-sub" style="color:{};font-weight:600;">{}</div>', notes)
        bar = progress_bar(paid, scheduled, len(overdue))
        actions = []
        next_payment = next(iter(sorted(
            (p for p in payments if p.status not in SETTLED),
            key=lambda p: (p.due_date, p.part),
        )), None)
        if next_payment is not None:
            next_payment.lease = obj
            to_pick = sorted((p for p in payments if p.status not in (LeasePayment.Status.PAID, LeasePayment.Status.WAIVED)), key=lambda p: (p.due_date, p.part, p.pk))
            for p in to_pick:
                p.lease = obj
            actions.append(record_button(next_payment, "Record payment", "primary", choices=to_pick))
            actions.append(_button("Cheque numbers", "cheques", reverse("admin:leases_lease_cheques", args=[obj.pk]), lease_info(obj, today)))
        actions.append(_button(
            "Add payment", "addpay", reverse("admin:leases_leasepayment_add_to", args=[obj.pk]),
            {"code": obj.code, "today": today.isoformat()},
        ))
        if obj.status == Lease.Status.ACTIVE:
            if not obj.renewals.exists():
                actions.append(_button("Renew", "renew", reverse("admin:leases_lease_renew", args=[obj.pk]), lease_info(obj, today)))
            actions.append(_button("Terminate", "terminate", reverse("admin:leases_lease_terminate", args=[obj.pk]), lease_info(obj, today), "danger"))
        for label, kind in (("Tenancy summary", "summary"), ("Payment statement", "statement")):
            actions.append(format_html(
                '<a class="erp-qbtn" href="{}" target="_blank" rel="noopener">{}</a>',
                reverse("admin:leases_lease_print", args=[obj.pk, kind]), label,
            ))
        buttons = format_html('<div class="erp-oh-actions">{}</div>', format_html_join(" ", "{}", ((a,) for a in actions)))
        stats = [
            (_money(obj.total_with_vat), "Annual rent incl. VAT" if obj.vat_applies else "Annual rent"),
            (_money(paid), "Paid"),
            (_money(max(scheduled - paid, Decimal("0"))), "Still to pay"),
            (_money(sum((p.total for p in overdue), Decimal("0"))), "Overdue"),
        ]
        avatar = circle_avatar(obj.tenant.photo.url if obj.tenant.photo else "", _initials(obj.tenant.name), 64)
        return format_html(
            '<div class="erp-owner-header">{}<div class="erp-oh-main"><div class="erp-oh-name">{} · {}</div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{}</div>{}<div class="erp-oh-bar">{}</div>{}</div>'
            '<div class="erp-oh-stats">{}</div></div>',
            avatar, obj.code, obj.tenant.name,
            format_html_join("", "{}", ((p,) for p in pills)),
            sub, extra, bar, buttons,
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
        )

    # ---- warnings on the lease page ----------------------------------------------------
    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method == "GET" and str(object_id).isdigit():
            today = timezone.localdate()
            limit = today + timedelta(days=sysconf.expiry_days())
            document_alert(
                request,
                LeaseDocument.objects.filter(lease_id=object_id, expiry_date__lte=limit).order_by("expiry_date"),
            )
            overdue = list(LeasePayment.objects.select_related("lease").filter(
                lease_id=object_id, status__in=OPEN, due_date__lt=today))
            count = len(overdue)
            if count:
                total = sum((p.total for p in overdue), Decimal("0"))
                messages.error(
                    request, f"{count} payment(s) overdue, {_money(total)} in total.", extra_tags=titled("Payments overdue")
                )
        return super().change_view(request, object_id, form_url, extra_context)

    # ---- actions --------------------------------------------------------------------------
    @admin.action(description="Export selected leases to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Leases", LEASE_HEADERS, lease_rows(queryset), "leases")

    @admin.action(description="Export selected leases to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(LEASE_HEADERS, lease_rows(queryset), "leases")

    @admin.action(description="Renew the selected lease", permissions=["add"])
    def renew_lease(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one lease to renew.", messages.ERROR)
            return None
        lease = queryset.first()
        try:
            new = lease.renew()
        except ValidationError as exc:
            self.message_user(request, " ".join(exc.messages), messages.ERROR)
            return None
        self.message_user(
            request,
            f"{new.code} was created as the renewal of {lease.code}. Check the rent and dates, then save any changes.",
            messages.SUCCESS,
        )
        return redirect(reverse("admin:leases_lease_change", args=[new.pk]))

    @admin.action(description="Rebuild the payment schedule of the selected leases", permissions=["change"])
    def rebuild_schedule(self, request, queryset):
        rebuilt, skipped = 0, []
        for lease in queryset:
            if lease.payments.exclude(status=LeasePayment.Status.PENDING).exists():
                skipped.append(lease.code)
                continue
            lease.payments.filter(kind=LeasePayment.Kind.RENT).delete()  # fines and other lines are kept
            lease.build_schedule()
            rebuilt += 1
        if rebuilt:
            self.message_user(request, f"The payment schedule was rebuilt for {rebuilt} lease(s).", messages.SUCCESS)
        if skipped:
            self.message_user(
                request,
                "Not rebuilt (some payments are already paid or bounced): " + ", ".join(skipped) + ".",
                messages.WARNING,
            )


# ---------------------------------------------------------------------------------------
# Tenant payments (every payment line of every lease in one list)
# ---------------------------------------------------------------------------------------
@admin.register(LeasePayment)
class LeasePaymentAdmin(PaymentApiMixin, ExportPermissionMixin, FilterBarMixin, TooltipHelpMixin, ModelAdmin):
    compressed_fields = True
    list_before_template = "leases/payment_strip.html"
    list_display = ("due", "lease_link", "kind_cell", "party", "amount_text", "pay_details", "status_cell", "quick")
    list_display_links = ("due",)
    list_filter = ("kind", "status", PaymentOverdueFilter, DueMonthFilter, PaymentBuildingFilter, PaymentOwnerFilter, ReceivedFilter, "method", ("due_date", admin.DateFieldListFilter))
    search_fields = (
        "lease__code", "lease__tenant__name", "lease__tenant__code", "cheque_number", "bank_name",
        "lease__property__name", "lease__unit__unit_number",
    )
    list_select_related = ("lease__tenant", "lease__unit__property", "lease__property")
    search_text = (
        "lease__code", "lease__tenant__name", "lease__tenant__code", "cheque_number", "bank_name",
        "lease__property__name", "lease__unit__unit_number",
    )
    search_digits = ("lease__tenant__phone",)
    search_aliases = {"_ref": Concat("lease__unit__property__code", Value("-"), "lease__unit__unit_number")}
    search_alias_fields = ("_ref",)
    search_codes = (("lease__code", "LSE", 4),)
    search_fuzzy = ("lease__tenant__name",)
    search_hint = "lease number (LSE-12), tenant name or phone, cheque number, bank, unit (RMT-101), building"
    actions = ["mark_paid", "mark_bounced", "bulk_edit_selected", "bulk_delete_selected", "export_excel", "export_csv"]
    fields = ("lease", "kind", "part", "due_date", "amount", "method", "cheque_number", "bank_name", "cheque_date", "deposited_on", "status", "paid_on", "notes")
    readonly_fields = ("lease", "kind", "part")
    # Payments of one lease sit together (by lease number); click "Due on" to see everything by date instead.
    ordering = ("lease__code", "due_date", "part", "id")

    def has_add_permission(self, request):
        return False  # payments come from the lease's schedule (add more on the lease page)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="")
    def quick(self, obj):
        return format_html('<span class="erp-rowacts">{}{}{}</span>', note_mark(obj), record_button(obj, "Record", "primary"), edit_button(obj))

    @admin.display(description="Tenant", ordering="lease__tenant__name")
    def party(self, obj):
        return format_html('<span class="erp-pay"><b>{}</b><small>{}</small></span>', obj.lease.tenant.name, obj.lease.item_name)

    @admin.display(description="Payment")
    def pay_details(self, obj):
        return pay_cell(obj)

    @admin.display(description="Status")
    def status_cell(self, obj):
        return state_cell(obj)

    @admin.display(description="Lease", ordering="lease__code")
    def lease_link(self, obj):
        """The first row of a lease shows its number (a link to its own payments page); the rest only a dash."""
        same = _last_lease.get() == obj.lease_id
        _last_lease.set(obj.lease_id)
        if same:
            return format_html('<span class="erp-lease-more" title="{}">&#8627;</span>', obj.lease.code)
        return format_html(
            '<a class="erp-lease-first" href="{}" title="All payments of this lease">{}</a>',
            reverse("admin:leases_lease_payments", args=[obj.lease_id]), obj.lease.code,
        )

    @admin.display(description="Type", ordering="kind")
    def kind_cell(self, obj):
        if obj.is_rent:
            return format_html('<span class="erp-pay"><b>Rent</b><small>payment {}</small></span>', obj.part)
        return format_html('<span class="erp-pay"><b>{}</b><small>not rent</small></span>', obj.get_kind_display())

    @admin.display(description="Amount", ordering="amount")
    def amount_text(self, obj):
        text = _money(obj.amount)
        return f"{text} + VAT" if obj.vat else text

    @admin.display(description="Due on", ordering="due_date")
    def due(self, obj):
        return due_cell(obj)

    @admin.action(description="Export selected payments to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Tenant payments", PAYMENT_HEADERS, payment_rows(queryset), "tenant-payments")

    @admin.action(description="Export selected payments to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(PAYMENT_HEADERS, payment_rows(queryset), "tenant-payments")

    def changelist_view(self, request, extra_context=None):
        _last_lease.set(None)  # the grouping of rows by lease starts again for every page
        extra_context = dict(extra_context or {})
        extra_context["pay_strip"] = self._strip()
        if self.has_change_permission(request):
            extra_context["bulk_url"] = reverse("admin:leases_leasepayment_bulk")
            extra_context["bulk_remove"] = request.user.has_perm("leases.delete_leasepayment")
        extra_context["report_links"] = [
            ("Monthly collection", reverse("admin:leases_lease_monthly")), ("Rent roll", reverse("admin:leases_lease_rentroll")),
        ]
        return super().changelist_view(request, extra_context)

    def _strip(self):
        """The four summary cards above the payments list."""
        today = timezone.localdate()
        base = LeasePayment.objects.select_related("lease")

        def total(queryset):
            rows = list(queryset)
            return len(rows), sum((p.total for p in rows), Decimal("0"))

        week = total(base.filter(status=LeasePayment.Status.PENDING, due_date__gte=today, due_date__lte=today + timedelta(days=7)))
        late = total(base.filter(status__in=OPEN, due_date__lt=today))
        got = total(
            base.filter(status=LeasePayment.Status.PAID, paid_on__year=today.year, paid_on__month=today.month)
            .exclude(method=LeasePayment.Method.FROM_DEPOSIT).exclude(kind=LeasePayment.Kind.REFUND)  # cash actually received
        )
        bounced = total(base.filter(status=LeasePayment.Status.BOUNCED))
        url = reverse("admin:leases_leasepayment_changelist")
        return [
            {"label": "Due this week", "count": week[0], "amount": _money(week[1]), "tone": "warning", "url": url + "?due=week"},
            {"label": "Overdue", "count": late[0], "amount": _money(late[1]), "tone": "danger", "url": url + "?due=overdue"},
            {"label": "Collected this month", "count": got[0], "amount": _money(got[1]), "tone": "success", "url": url + "?received=month"},
            {"label": "Bounced cheques", "count": bounced[0], "amount": _money(bounced[1]), "tone": "danger", "url": url + "?status__exact=bounced"},
        ]

    @admin.action(description="Mark the selected payments as paid", permissions=["change"])
    def mark_paid(self, request, queryset):
        count = 0
        for payment in queryset.exclude(status__in=(LeasePayment.Status.PAID, LeasePayment.Status.WAIVED)):
            payment.status = LeasePayment.Status.PAID
            payment.paid_on = timezone.localdate()
            payment.save()
            count += 1
        self.message_user(request, f"{count} payment(s) marked as paid (today's date).", messages.SUCCESS)

    @admin.action(description="Edit the selected payments (dates, amount, cheque numbers ...)", permissions=["change"])
    def bulk_edit_selected(self, request, queryset):
        # The list page opens a pop-up for this choice (leases.js); this only runs when scripts are switched off.
        self.message_user(request, "Use the “Edit ticked” button above the list.", messages.INFO)

    @admin.action(description="Remove the selected payments", permissions=["bulk_remove"])
    def bulk_delete_selected(self, request, queryset):
        self.message_user(request, "Use the “Remove ticked” button above the list.", messages.INFO)

    def has_bulk_remove_permission(self, request):
        return request.user.has_perm("leases.delete_leasepayment")

    @admin.action(description="Mark the selected payments as bounced", permissions=["change"])
    def mark_bounced(self, request, queryset):
        count = 0
        for payment in queryset.exclude(status__in=(LeasePayment.Status.BOUNCED, LeasePayment.Status.WAIVED)):
            payment.status = LeasePayment.Status.BOUNCED
            payment.save()
            count += 1
        self.message_user(request, f"{count} payment(s) marked as bounced.", messages.WARNING)
