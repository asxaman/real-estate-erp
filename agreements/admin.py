"""Owner agreement pages: list with quick filters, the agreement form (tabs), and the tabs shown on the owner and property pages."""
from datetime import timedelta

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.forms.models import BaseInlineFormSet
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin, TabularInline

from core import exports, sysconf
from core.admin_utils import ExportPermissionMixin, TooltipHelpMixin, badge
from core.formats import fmt_date
from core.paging import RowsPerPageMixin
from core.search import SmartSearchMixin
from core.widgets import use_file_widget
from properties.admin import PropertyAdmin
from properties.owner_admin import OwnerAdmin, ReadOnlyInline

from .models import AgreementProperty, OwnerAgreement, running_q

STATES = {
    "draft": ("Draft", "neutral"),
    "upcoming": ("Not started", "info"),
    "active": ("Active", "success"),
    "expired": ("Expired", "danger"),
    "terminated": ("Terminated", "danger"),
}
EXPORT_HEADERS = [
    "Agreement no.", "Owner", "Properties covered", "Starts", "Ends", "Renews automatically", "Notice (days)", "State",
    "% of rent collected", "Fixed fee per year (AED)", "% of contract rent", "Guaranteed rent per year (AED)",
    "Owner is paid", "Signed on", "Terminated on", "Reason", "Notes",
]


def state_badge(agreement, today=None):
    today = today or timezone.localdate()
    if agreement.ends_soon(today):
        return badge("Ending soon", "warning")
    text, tone = STATES[agreement.state(today)]
    return badge(text, tone)


def period_text(agreement):
    if agreement.end_date:
        text = f"{fmt_date(agreement.start_date)} → {fmt_date(agreement.end_date)}"
        return text + " (renews)" if agreement.auto_renew else text
    return f"From {fmt_date(agreement.start_date)} (no end date)"


def export_rows(queryset):
    today = timezone.localdate()
    for a in queryset.select_related("owner").prefetch_related("lines__property"):
        state = "Ending soon" if a.ends_soon(today) else STATES[a.state(today)][0]
        yield [
            a.code, a.owner.name, ", ".join(line.property.name for line in a.lines.all()), a.start_date, a.end_date or "",
            "Yes" if a.auto_renew else "No", a.notice_days or "", state, a.fee_percent_collected or "", a.fee_fixed_yearly or "",
            a.fee_percent_contract or "", a.guaranteed_rent_yearly or "", a.get_payout_frequency_display(), a.signed_on or "",
            a.terminated_on or "", a.termination_reason, a.notes,
        ]


# ---------------------------------------------------------------------------------------
# The agreement form
# ---------------------------------------------------------------------------------------
class AgreementForm(forms.ModelForm):
    class Meta:
        model = OwnerAgreement
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hints = {
            "fee_percent_collected": "e.g. 5",
            "fee_fixed_yearly": "e.g. 12000",
            "fee_percent_contract": "e.g. 5",
            "guaranteed_rent_yearly": "e.g. 480000",
            "notice_days": "e.g. 90",
            "termination_reason": "Why the agreement was ended early",
        }
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        if "notes" in self.fields:
            self.fields["notes"].widget.attrs["rows"] = 3
        use_file_widget(self, "document", accept=".pdf,.jpg,.jpeg,.png,.webp")


class CoveredFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        kept = [f for f in self.forms if f.cleaned_data and f.cleaned_data.get("property") and not f.cleaned_data.get("DELETE")]
        if self.instance.status == OwnerAgreement.Status.ACTIVE and not kept:
            raise ValidationError("An active agreement must cover at least one property. Add it here, or save the agreement as a Draft.")
        # (the same two rules as on the record itself, checked here too so they also hold on a brand-new agreement)
        agreement = self.instance
        for form in kept:
            prop = form.cleaned_data["property"]
            if agreement.owner_id and prop.owner_id != agreement.owner_id:
                form.add_error("property", f"{prop.name} belongs to {prop.owner.name}, not to the owner of this agreement.")
                continue
            other = agreement.conflict_for(prop)
            if other is not None:
                form.add_error(
                    "property",
                    f"{prop.name} is already covered by agreement {other.code} in the same period. "
                    "End that agreement first, or change the dates.",
                )


class CoveredInline(TabularInline):
    model = AgreementProperty
    formset = CoveredFormSet
    tab = True
    extra = 0
    autocomplete_fields = ["property"]
    fields = ("property", "details")
    readonly_fields = ("details",)
    verbose_name_plural = "Properties Covered"

    @admin.display(description="Details")
    def details(self, obj):
        if obj is None or not obj.pk:
            return "-"
        prop = obj.property
        return f"{prop.code} · {prop.get_property_type_display()} · {prop.get_emirate_display()}"


@admin.register(OwnerAgreement)
class OwnerAgreementAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = AgreementForm
    compressed_fields = True
    autocomplete_fields = ["owner"]  # searchable owner dropdown
    list_display = ("code", "owner_link", "covered", "period", "fees", "payout", "state")
    list_display_links = ("code",)
    list_filter = ("status", "payout_frequency", "auto_renew")
    list_select_related = ("owner",)
    search_fields = ("code", "owner__name", "owner__code", "lines__property__name", "lines__property__code")
    search_text = ("code", "owner__name", "owner__code")
    search_related = ((AgreementProperty, "agreement", ["property__name", "property__code"], []),)
    search_codes = (("code", "AGR", 4),)
    search_fuzzy = ("owner__name",)
    search_hint = "agreement number (AGR-12), owner name or code, property name or code"
    inlines = [CoveredInline]
    actions = ["export_excel", "export_csv"]

    _header = (None, {"fields": ("summary_header",)})
    _agreement = (
        "Agreement",
        {
            "classes": ["tab"],
            "fields": (
                ("owner", "status"),
                ("start_date", "end_date"),
                ("auto_renew", "notice_days"),
                ("signed_on", "payout_frequency"),
                "document",
                "notes",
            ),
        },
    )
    _fees = (
        "Fees",
        {
            "classes": ["tab"],
            "description": "What the company earns under this agreement. Fill in the ones that apply and leave the rest empty. "
            "A guaranteed rent stands alone: it cannot be combined with the other fees.",
            "fields": (
                ("fee_percent_collected", "fee_fixed_yearly"),
                "fee_percent_contract",
                "guaranteed_rent_yearly",
            ),
        },
    )
    _ending = (
        "Ending Early",
        {
            "classes": ["tab"],
            "description": "Only when the agreement is ended before its end date: set Status to Terminated on the Agreement tab, "
            "then fill in the date and the reason here.",
            "fields": ("terminated_on", "termination_reason"),
        },
    )

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (self._agreement, self._fees)
        return (self._header, self._agreement, self._fees, self._ending)

    def get_readonly_fields(self, request, obj=None):
        return ("summary_header",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_covered=Count("lines", distinct=True))

    def has_delete_permission(self, request, obj=None):
        # A signed agreement is part of the records: end it (Terminated) instead of deleting it. Drafts can go.
        if obj is not None and obj.status != OwnerAgreement.Status.DRAFT:
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)  # deleting goes one by one, and only for drafts
        return actions

    def quick_chips(self):
        today = timezone.localdate()
        soon = today + timedelta(days=sysconf.expiry_days())
        S = OwnerAgreement.Status
        return [
            ("running", "Running", "success", running_q(today)),
            ("soon", f"Ending in {sysconf.expiry_days()} Days", "warning",
             Q(status=S.ACTIVE, start_date__lte=today, auto_renew=False, end_date__gte=today, end_date__lte=soon)),
            ("upcoming", "Not Started", "info", Q(status=S.ACTIVE, start_date__gt=today)),
            ("expired", "Expired", "danger", Q(status=S.ACTIVE, auto_renew=False, end_date__lt=today)),
            ("draft", "Draft", "neutral", Q(status=S.DRAFT)),
            ("terminated", "Terminated", "danger", Q(status=S.TERMINATED)),
        ]

    # ---- list columns ----------------------------------------------------------------
    @admin.display(description="Owner", ordering="owner__name")
    def owner_link(self, obj):
        return format_html(
            '<a href="{}" style="font-weight:600">{}</a>', reverse("admin:properties_owner_change", args=[obj.owner_id]), obj.owner.name,
        )

    @admin.display(description="Properties", ordering="_covered")
    def covered(self, obj):
        count = getattr(obj, "_covered", None)
        if count is None:
            count = obj.lines.count()
        return f"{count} propert{'y' if count == 1 else 'ies'}" if count else badge("None yet", "warning")

    @admin.display(description="Period", ordering="start_date")
    def period(self, obj):
        return period_text(obj)

    @admin.display(description="Company earns")
    def fees(self, obj):
        return obj.fee_text()

    @admin.display(description="Owner is paid", ordering="payout_frequency")
    def payout(self, obj):
        return obj.get_payout_frequency_display()

    @admin.display(description="State")
    def state(self, obj):
        return state_badge(obj)

    # ---- summary header --------------------------------------------------------------
    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        pills = [state_badge(obj)]
        if obj.auto_renew:
            pills.append(badge("Renews automatically", "info"))
        if obj.is_guaranteed_rent:
            pills.append(badge("Guaranteed rent", "info"))
        if not obj.document:
            pills.append(badge("Signed copy not uploaded", "warning"))
        covered = obj.lines.count()
        notes = []
        if obj.status == OwnerAgreement.Status.TERMINATED and obj.terminated_on:
            notes.append(("#b91c1c", f"Terminated on {fmt_date(obj.terminated_on)}: {obj.termination_reason}"))
        if not covered:
            notes.append(("#92400e", "No property is covered yet. Add them on the Properties Covered tab."))
        extra = format_html_join("", '<div class="erp-oh-sub" style="color:{};font-weight:600;">{}</div>', notes)
        stats = [
            (covered, "Propert" + ("y" if covered == 1 else "ies")),
            (obj.get_payout_frequency_display(), "Owner is paid"),
            (f"{obj.notice_days} days" if obj.notice_days else "-", "Notice"),
        ]
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · <a href="{}">{}</a></div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{} · {}</div>{}</div>'
            '<div class="erp-oh-stats">{}</div></div>',
            obj.code,
            reverse("admin:properties_owner_change", args=[obj.owner_id]),
            obj.owner.name,
            format_html_join("", "{}", ((p,) for p in pills)),
            period_text(obj),
            obj.fee_text(),
            extra,
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
        )

    # ---- export ------------------------------------------------------------------------
    @admin.action(description="Export selected agreements to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Owner agreements", EXPORT_HEADERS, export_rows(queryset), "owner-agreements")

    @admin.action(description="Export selected agreements to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(EXPORT_HEADERS, export_rows(queryset), "owner-agreements")


# ---------------------------------------------------------------------------------------
# The "Agreements" tab on the owner page and on the property page (read-only; open an agreement to change it)
# ---------------------------------------------------------------------------------------
class _AgreementsTab:
    """For people who may see owner agreements. (A new owner or property has none yet: see _add_tab.)"""

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("agreements.view_owneragreement")


def _open_button(agreement_id):
    return format_html(
        '<a class="erp-qbtn erp-qbtn-primary" href="{}">Open agreement</a>',
        reverse("admin:agreements_owneragreement_change", args=[agreement_id]),
    )


class OwnerAgreementsInline(_AgreementsTab, ReadOnlyInline):
    model = OwnerAgreement
    tab = True
    verbose_name_plural = "Agreements"
    show_change_link = False
    fields = ("number", "period", "fees", "payout", "state", "open_agreement")
    readonly_fields = fields
    ordering = ("-start_date",)

    @admin.display(description="Agreement no.")
    def number(self, obj):
        return obj.code

    @admin.display(description="Period")
    def period(self, obj):
        return period_text(obj)

    @admin.display(description="Company earns")
    def fees(self, obj):
        return obj.fee_text()

    @admin.display(description="Owner is paid")
    def payout(self, obj):
        return obj.get_payout_frequency_display()

    @admin.display(description="State")
    def state(self, obj):
        return state_badge(obj)

    @admin.display(description="")
    def open_agreement(self, obj):
        return _open_button(obj.pk) if obj.pk else ""


class PropertyAgreementsInline(_AgreementsTab, ReadOnlyInline):
    model = AgreementProperty
    tab = True
    verbose_name_plural = "Agreements"
    show_change_link = False
    fields = ("number", "period", "fees", "payout", "state", "open_agreement")
    readonly_fields = fields
    ordering = ("-agreement__start_date",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("agreement")

    @admin.display(description="Agreement no.")
    def number(self, obj):
        return obj.agreement.code

    @admin.display(description="Period")
    def period(self, obj):
        return period_text(obj.agreement)

    @admin.display(description="Company earns")
    def fees(self, obj):
        return obj.agreement.fee_text()

    @admin.display(description="Owner is paid")
    def payout(self, obj):
        return obj.agreement.get_payout_frequency_display()

    @admin.display(description="State")
    def state(self, obj):
        return state_badge(obj.agreement)

    @admin.display(description="")
    def open_agreement(self, obj):
        return _open_button(obj.agreement_id) if obj.pk else ""


def _add_tab(model_admin_class, inline, before):
    """Put `inline` on another module's page, just before its Activity tab (not on the page for a new record)."""
    current = list(model_admin_class.inlines)
    if inline in current:
        return
    position = next((i for i, item in enumerate(current) if item.__name__ == before), len(current))
    current.insert(position, inline)
    model_admin_class.inlines = current
    original = model_admin_class.get_inline_instances

    def get_inline_instances(self, request, obj=None):
        instances = original(self, request, obj)
        # a new owner or property has no agreements yet, and the built-in "Company Owned" owner (and its properties) never has one
        company = obj is not None and (getattr(obj, "is_company", False) or getattr(getattr(obj, "owner", None), "is_company", False))
        if obj is None or company:
            instances = [i for i in instances if not isinstance(i, inline)]
        return instances

    model_admin_class.get_inline_instances = get_inline_instances


_add_tab(OwnerAdmin, OwnerAgreementsInline, "ActivityInline")
_add_tab(PropertyAdmin, PropertyAgreementsInline, "PropertyActivityInline")
