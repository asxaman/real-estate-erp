"""Lease pages: summary header, timeline, tabs (Overview, Money, Fees and Ejari, Termination, Payments, Documents, Notes, Activity), list, rent payments."""
import json
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, Exists, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from core import sysconf
from core import exports
from core.admin_utils import ExportPermissionMixin, TooltipHelpMixin, badge, circle_avatar, format_amount
from core.formats import fmt_date
from core.toasts import titled
from core.widgets import use_file_widget
from properties.models import Property, Unit
from properties.owner_admin import ActivityInline, DocumentInline, _initials, document_alert
from tenants.models import Tenant

from .api import LeaseApiMixin, PaymentApiMixin
from .reports import LEASE_HEADERS, PAYMENT_HEADERS, ReportsMixin, lease_rows, payment_rows
from .models import Lease, LeaseDeduction, LeaseDocument, LeaseNote, LeasePayment

OPEN = (LeasePayment.Status.PENDING, LeasePayment.Status.BOUNCED)
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
        if p.status == LeasePayment.Status.PAID:
            kind, word = "ok", "paid"
        elif p.status == LeasePayment.Status.BOUNCED:
            kind, word = "bad", "bounced"
        elif p.is_overdue(today):
            kind, word = "bad", "overdue"
        else:
            kind, word = "wait", "pending"
        marks.append((_pos(p.due_date, start, end), kind, f"Payment {p.part}: {fmt_date(p.due_date)} · {_money(p.total)} · {word}"))
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
        "code": payment.lease.code, "part": payment.part, "amount": _money(payment.total), "due": fmt_date(payment.due_date),
        "method": payment.method, "cheque": payment.cheque_number, "bank": payment.bank_name,
        "overdue": payment.is_overdue(today), "today": today.isoformat(),
    }


def record_button(payment, label="Record payment", tone="primary"):
    if payment is None or payment.status == LeasePayment.Status.PAID:
        return ""
    return _button(label, "record", reverse("admin:leases_leasepayment_record", args=[payment.pk]), payment_info(payment), tone)


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
        "deposit": lease.deposit_status, "unpaid": sum(1 for p in payments if p.status != LeasePayment.Status.PAID),
        "pending_later": sum(1 for p in payments if p.status == LeasePayment.Status.PENDING and p.due_date > today),
    }


# ---------------------------------------------------------------------------------------
# Tabs (inlines)
# ---------------------------------------------------------------------------------------
class LeasePaymentInline(TabularInline):
    model = LeasePayment
    tab = True
    extra = 0
    fields = ("part", "due_date", "amount", "method", "cheque_number", "bank_name", "cheque_date", "deposited_on", "status", "paid_on", "notes", "quick")
    readonly_fields = ("quick",)
    ordering = ("due_date", "part")

    @admin.display(description="")
    def quick(self, obj):
        if obj is None or not obj.pk:
            return ""
        return record_button(obj, "Record", "primary")


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
    model = LeaseDeduction
    tab = True
    extra = 0
    fields = ("date", "reason", "amount")
    verbose_name_plural = "Deposit deductions"


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
        return timezone.localtime(obj.created_at).strftime("%d %b %Y, %H:%M") if obj and obj.pk else "-"


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
            "active": lambda: queryset.filter(running),
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
        self.fields["unit"].label_from_instance = lambda u: f"{u.property.code}-{u.unit_number} · {u.property.name}"
        standalone = Property.objects.exclude(property_type__in=Property.BUILDING_TYPES).filter(is_active=True)
        if current.pk and current.property_id:
            standalone = Property.objects.exclude(property_type__in=Property.BUILDING_TYPES).filter(
                Q(is_active=True) | Q(pk=current.property_id)
            )
        self.fields["property"].queryset = standalone.order_by("code")
        self.fields["property"].label_from_instance = lambda p: f"{p.code} · {p.name}"
        hints = {
            "annual_rent": "e.g. 65000",
            "security_deposit": "0 if none",
            "ejari_number": "e.g. 10-1234-5678",
            "termination_reason": "Why the lease ended early",
        }
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        if "notes" in self.fields:
            self.fields["notes"].widget.attrs["rows"] = 3


@admin.register(Lease)
class LeaseAdmin(LeaseApiMixin, ReportsMixin, ExportPermissionMixin, TooltipHelpMixin, ModelAdmin):
    form = LeaseAdminForm
    compressed_fields = True
    list_display = ("code", "tenant_name", "rented", "period", "rent", "state_badge", "payments_badge", "quick")
    list_before_template = "leases/chips.html"
    list_display_links = ("code",)
    list_filter = (StateFilter, OverdueFilter, "vat_applies", "installments")
    search_fields = (
        "code", "tenant__name", "tenant__code", "tenant__email", "tenant__phone", "property__name", "property__code",
        "unit__unit_number", "ejari_number",
    )
    list_select_related = ("tenant", "unit__property", "property")
    inlines = [LeasePaymentInline, DeductionInline, LeaseDocumentInline, NoteInline, LeaseActivityInline]
    actions = ["renew_lease", "rebuild_schedule", "export_excel", "export_csv"]

    _header = (None, {"fields": ("summary_header", "timeline_strip")})
    _money = ("Money", {"classes": ["tab"], "fields": ("money_summary",)})
    _overview = (
        "Overview",
        {
            "classes": ["tab"],
            "fields": (
                ("code", "status"),
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
            "fields": (
                ("security_deposit", "deposit_status"),
                ("deposit_refund_date", "deposit_refund_amount"),
                "deposit_summary",
                ("commission", "admin_fee"),
                "late_fee_per_day",
                ("ejari_number", "ejari_expiry"),
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

    def get_fieldsets(self, request, obj=None):
        if obj is not None:
            return (self._header, self._overview, self._money, self._fees, self._termination)
        return (self._overview, self._fees, self._termination)

    def get_readonly_fields(self, request, obj=None):
        return ("code", "summary_header", "timeline_strip", "money_summary", "deposit_summary")

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if obj is None:  # the schedule is created when the lease is first saved
            instances = [i for i in instances if not isinstance(i, (LeasePaymentInline, LeaseActivityInline, NoteInline, DeductionInline))]
        return instances

    def get_queryset(self, request):
        today = timezone.localdate()
        return super().get_queryset(request).prefetch_related("payments", "renewals").annotate(
            _paid=Coalesce(Sum("payments__amount", filter=Q(payments__status=LeasePayment.Status.PAID)), ZERO),
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
        bar = progress_bar(obj._paid, obj.annual_rent, obj._overdue)
        if obj._overdue:
            return format_html("{}{}", bar, badge(f"{obj._overdue} overdue", "danger"))
        return bar

    @admin.display(description="Quick actions")
    def quick(self, obj):
        today = timezone.localdate()
        buttons = []
        unpaid = sorted(
            (p for p in obj.payments.all() if p.status != LeasePayment.Status.PAID), key=lambda p: (p.due_date, p.part)
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
            active=Count("pk", filter=running),
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
        paid = sum((p.total for p in payments if p.status == LeasePayment.Status.PAID), Decimal("0"))
        scheduled = sum((p.total for p in payments), Decimal("0"))
        scheduled_rent = sum((p.amount for p in payments), Decimal("0"))
        rows = [
            ("Annual rent", _money(obj.annual_rent), ""),
            ("VAT (5%)" if obj.vat_applies else "VAT (not applied)", _money(obj.vat_amount), ""),
            ("Rent with VAT", _money(obj.total_with_vat), "strong"),
            ("Security deposit", _money(obj.security_deposit), ""),
            ("Agency commission", _money(obj.commission), ""),
            ("Admin / contract fee", _money(obj.admin_fee), ""),
            ("Received so far", _money(paid), "good"),
            ("Still to receive", _money(max(scheduled - paid, Decimal("0"))), "strong"),
        ]
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
        rows = [
            ("Security deposit", _money(obj.security_deposit)),
            ("Deducted (see the Deposit deductions tab)", _money(deducted)),
            ("Refunded", _money(obj.deposit_refund_amount) + (f" on {fmt_date(obj.deposit_refund_date)}" if obj.deposit_refund_date else "")),
            ("Still held", _money(obj.deposit_balance())),
        ]
        table = format_html(
            '<table class="erp-sum">{}</table>',
            format_html_join("", "<tr><th>{}</th><td>{}</td></tr>", rows),
        )
        warn = ""
        if deducted + (obj.deposit_refund_amount or 0) > obj.security_deposit:
            warn = format_html('<div class="erp-sum-warn">Deductions and the refund together are more than the deposit.</div>')
        return format_html("{}{}", table, warn)

    # ---- summary header --------------------------------------------------------------
    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        today = timezone.localdate()
        payments = list(obj.payments.all())
        paid = sum((p.amount for p in payments if p.status == LeasePayment.Status.PAID), Decimal("0"))
        scheduled = sum((p.amount for p in payments), Decimal("0"))
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
            (p for p in payments if p.status != LeasePayment.Status.PAID), key=lambda p: (p.due_date, p.part)
        )), None)
        if next_payment is not None:
            next_payment.lease = obj
            actions.append(record_button(next_payment, "Record payment", "primary"))
            actions.append(_button("Cheque numbers", "cheques", reverse("admin:leases_lease_cheques", args=[obj.pk]), lease_info(obj, today)))
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
            (_money(obj.annual_rent), "Annual rent"),
            (_money(paid), "Paid"),
            (_money(max(scheduled - paid, Decimal("0"))), "Still to pay"),
            (_money(sum((p.amount for p in overdue), Decimal("0"))), "Overdue"),
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
            overdue = LeasePayment.objects.filter(lease_id=object_id, status__in=OPEN, due_date__lt=today)
            count = overdue.count()
            if count:
                total = overdue.aggregate(t=Sum("amount"))["t"] or 0
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
            lease.payments.all().delete()
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
# Rent payments (every instalment of every lease in one list)
# ---------------------------------------------------------------------------------------
@admin.register(LeasePayment)
class LeasePaymentAdmin(PaymentApiMixin, ExportPermissionMixin, TooltipHelpMixin, ModelAdmin):
    compressed_fields = True
    list_before_template = "leases/payment_strip.html"
    list_display = ("due", "lease_link", "tenant_name", "rented", "amount_text", "method", "cheque_number", "state_badge", "days_late", "quick")
    list_display_links = ("due",)
    list_filter = ("status", PaymentOverdueFilter, ReceivedFilter, "method", ("due_date", admin.DateFieldListFilter))
    search_fields = (
        "lease__code", "lease__tenant__name", "lease__tenant__code", "cheque_number", "bank_name",
        "lease__property__name", "lease__unit__unit_number",
    )
    list_select_related = ("lease__tenant", "lease__unit__property", "lease__property")
    actions = ["mark_paid", "mark_bounced", "export_excel", "export_csv"]
    fields = ("lease", "part", "due_date", "amount", "method", "cheque_number", "bank_name", "cheque_date", "deposited_on", "status", "paid_on", "notes")
    readonly_fields = ("lease", "part")
    ordering = ("due_date", "id")

    def has_add_permission(self, request):
        return False  # payments come from the lease's schedule (add more on the lease page)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="")
    def quick(self, obj):
        return record_button(obj, "Record", "primary") or "-"

    @admin.display(description="Lease", ordering="lease__code")
    def lease_link(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:leases_lease_change", args=[obj.lease_id]), obj.lease.code)

    @admin.display(description="Tenant")
    def tenant_name(self, obj):
        return obj.lease.tenant.name

    @admin.display(description="Rented")
    def rented(self, obj):
        return obj.lease.item_name

    @admin.display(description="Amount", ordering="amount")
    def amount_text(self, obj):
        text = _money(obj.amount)
        return f"{text} + VAT" if obj.lease.vat_applies else text

    @admin.display(description="Due on", ordering="due_date")
    def due(self, obj):
        today = timezone.localdate()
        days = (obj.due_date - today).days
        if obj.status == LeasePayment.Status.PAID:
            tone, note = "ok", "paid"
        elif obj.status == LeasePayment.Status.BOUNCED:
            tone, note = "bad", "bounced"
        elif days < 0:
            tone, note = "bad", f"{-days} day{'s' if days != -1 else ''} overdue"
        elif days == 0:
            tone, note = "warn", "due today"
        elif days <= 7:
            tone, note = "warn", f"in {days} day{'s' if days != 1 else ''}"
        else:
            tone, note = "mute", f"in {days} days"
        return format_html('<span class="erp-due erp-due-{}"><b>{}</b><small>{}</small></span>', tone, fmt_date(obj.due_date), note)

    @admin.display(description="Days late")
    def days_late(self, obj):
        days = obj.days_overdue()
        if not days:
            return "-"
        fee = obj.late_fee()
        text = f"{days} day{'s' if days != 1 else ''}"
        return f"{text} · fee {_money(fee)}" if fee else text

    @display(description="Status", label={"Paid": "success", "Pending": "info", "Overdue": "danger", "Bounced": "danger"})
    def state_badge(self, obj):
        if obj.status == LeasePayment.Status.PAID:
            return "Paid"
        if obj.status == LeasePayment.Status.BOUNCED:
            return "Bounced"
        return "Overdue" if obj.is_overdue() else "Pending"

    @admin.action(description="Export selected payments to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Rent payments", PAYMENT_HEADERS, payment_rows(queryset), "rent-payments")

    @admin.action(description="Export selected payments to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(PAYMENT_HEADERS, payment_rows(queryset), "rent-payments")

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context["pay_strip"] = self._strip()
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
        got = total(base.filter(status=LeasePayment.Status.PAID, paid_on__year=today.year, paid_on__month=today.month))
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
        for payment in queryset.exclude(status=LeasePayment.Status.PAID):
            payment.status = LeasePayment.Status.PAID
            payment.paid_on = timezone.localdate()
            payment.save()
            count += 1
        self.message_user(request, f"{count} payment(s) marked as paid (today's date).", messages.SUCCESS)

    @admin.action(description="Mark the selected payments as bounced", permissions=["change"])
    def mark_bounced(self, request, queryset):
        count = 0
        for payment in queryset.exclude(status=LeasePayment.Status.BOUNCED):
            payment.status = LeasePayment.Status.BOUNCED
            payment.save()
            count += 1
        self.message_user(request, f"{count} payment(s) marked as bounced.", messages.WARNING)
