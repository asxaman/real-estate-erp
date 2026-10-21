from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import TabularInline as UnfoldTabularInline

from .models import (
    Cheque,
    ChequeEvent,
    CompanySettings,
    Lease,
    LeaseDocument,
    Owner,
    OwnerDocument,
    Property,
    PropertyDocument,
    PropertyOwnership,
    Tenant,
    TenantDocument,
    Unit,
)
from .utils import add_months


def log_change(request, obj, message):
    """Write a line into the object's History page (who did what, and when)."""
    LogEntry.objects.log_action(
        user_id=request.user.pk,
        content_type_id=ContentType.objects.get_for_model(obj).pk,
        object_id=obj.pk,
        object_repr=str(obj)[:200],
        action_flag=CHANGE,
        change_message=message,
    )


def can_see_owner_private(request):
    """May this user see owners' bank, ID and KYC details?"""
    return request.user.has_perm("realestate.view_owner_private")

admin.site.site_header = "Real Estate ERP"
admin.site.site_title = "Real Estate ERP"
admin.site.index_title = "Dashboard"


# ---------------------------------------------------------------------------
# Small display helpers
# ---------------------------------------------------------------------------

UNIT_STATUS_COLORS = {
    "vacant": "#2e7d32",
    "reserved": "#ef6c00",
    "occupied": "#1565c0",
    "holdover": "#c62828",
    "maintenance": "#8d6e63",
}

CHEQUE_STATUS_COLORS = {
    "pending": "#757575",
    "received": "#1565c0",
    "deposited": "#ef6c00",
    "cleared": "#2e7d32",
    "returned": "#c62828",
    "replaced": "#6a1b9a",
    "cancelled": "#455a64",
}

COMPLIANCE_COLORS = {
    "ok": "#2e7d32",
    "expiring": "#ef6c00",
    "expired": "#c62828",
    "missing": "#ad1457",
}
COMPLIANCE_LABELS = {
    "ok": "Documents OK",
    "expiring": "Expiring soon",
    "expired": "Expired",
    "missing": "Incomplete",
}


def badge(text, color):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 9px;border-radius:10px;'
        'font-size:11px;font-weight:600;white-space:nowrap;">{}</span>',
        color,
        text,
    )


def status_badge(status):
    """Show a unit status as coloured text."""
    status = Unit.Status(status)
    return format_html(
        '<strong style="color:{};">{}</strong>',
        UNIT_STATUS_COLORS.get(status.value, "inherit"),
        status.label,
    )


def compliance_badge(obj):
    state = obj.compliance_state
    return badge(COMPLIANCE_LABELS[state], COMPLIANCE_COLORS[state])


def compliance_report(obj):
    """The list of document problems, shown on an owner's or tenant's page."""
    if obj is None or not obj.pk:
        return "Save first to see the document check."
    issues = obj.compliance_issues()
    if not issues:
        return mark_safe('<strong style="color:#2e7d32;">All documents are valid.</strong>')
    items = format_html_join("", "<li>{}</li>", ((message,) for _, message in issues))
    return format_html('<ul style="margin:0;padding-left:18px;">{}</ul>', items)


def aed(value):
    return f"AED {value:,.2f}"


# ---------------------------------------------------------------------------
# Shared filter: documents expiring
# ---------------------------------------------------------------------------


class DocumentExpiryFilter(admin.SimpleListFilter):
    """Find owners / tenants whose ID documents have expired or will soon."""

    title = "documents"
    parameter_name = "docs"

    def __init__(self, request, params, model, model_admin):
        self.date_fields = model_admin.compliance_date_fields
        super().__init__(request, params, model, model_admin)

    def lookups(self, request, model_admin):
        return [
            ("expired", "Has an expired document"),
            ("30", "Expiring within 30 days"),
            ("60", "Expiring within 60 days"),
            ("90", "Expiring within 90 days"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        today = timezone.localdate()
        condition = Q()
        for field in self.date_fields:
            if value == "expired":
                condition |= Q(**{f"{field}__lt": today})
            else:
                condition |= Q(
                    **{
                        f"{field}__gte": today,
                        f"{field}__lte": today + timedelta(days=int(value)),
                    }
                )
        return queryset.filter(condition)


class DocumentInline(UnfoldTabularInline):
    """Upload scans (Emirates ID, passport, contract...) on the record's page."""

    extra = 0
    fields = ("doc_type", "file", "notes")


class OwnerDocumentInline(DocumentInline):
    model = OwnerDocument


class TenantDocumentInline(DocumentInline):
    model = TenantDocument


class PropertyDocumentInline(DocumentInline):
    model = PropertyDocument


class LeaseDocumentInline(DocumentInline):
    model = LeaseDocument


# ---------------------------------------------------------------------------
# Owners
# ---------------------------------------------------------------------------


@admin.register(Owner)
class OwnerAdmin(UnfoldModelAdmin):
    # Company-only boxes appear only when the owner is a company.
    conditional_fields = {
        "trade_licence_number": "owner_type == 'company'",
        "trade_licence_expiry": "owner_type == 'company'",
        "trn": "owner_type == 'company'",
    }
    compliance_date_fields = (
        "emirates_id_expiry",
        "passport_expiry",
        "trade_licence_expiry",
    )
    list_display = (
        "name",
        "owner_type",
        "phone",
        "management_fee_percent",
        "kyc_status",
        "documents_check",
        "is_active",
    )
    list_filter = ("owner_type", "kyc_status", DocumentExpiryFilter, "is_active")
    search_fields = ("name", "email", "phone", "emirates_id", "passport_number", "iban")
    readonly_fields = ("compliance_check", "kyc_verified_on")
    inlines = [OwnerDocumentInline]
    fieldsets = (
        (
            "Owner details",
            {
                "fields": (
                    "owner_type",
                    "name",
                    "nationality",
                    "email",
                    "phone",
                    "whatsapp",
                    "address",
                )
            },
        ),
        (
            "Identification",
            {
                "description": "Individuals: Emirates ID and/or passport. Companies: trade licence.",
                "fields": (
                    "emirates_id",
                    "emirates_id_expiry",
                    "passport_number",
                    "passport_expiry",
                    "trade_licence_number",
                    "trade_licence_expiry",
                    "trn",
                ),
            },
        ),
        ("KYC review", {"fields": ("compliance_check", "kyc_status", "kyc_verified_on")}),
        (
            "Banking & management fee",
            {
                "fields": (
                    "bank_name",
                    "account_holder_name",
                    "iban",
                    "management_fee_percent",
                )
            },
        ),
        ("Notes & status", {"fields": ("notes", "is_active")}),
    )

    @admin.display(description="Documents")
    def documents_check(self, obj):
        return compliance_badge(obj)

    @admin.display(description="Document check")
    def compliance_check(self, obj):
        return compliance_report(obj)

    # --- privacy: only users with "view_owner_private" see IDs and bank details

    PRIVATE_SECTIONS = ("Identification", "KYC review")

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if can_see_owner_private(request):
            return fieldsets
        shown = []
        for name, options in fieldsets:
            if name in self.PRIVATE_SECTIONS:
                continue
            if name == "Banking & management fee":
                shown.append(("Management fee", {"fields": ("management_fee_percent",)}))
                continue
            shown.append((name, options))
        return shown

    def get_inlines(self, request, obj):
        return super().get_inlines(request, obj) if can_see_owner_private(request) else []

    def get_list_display(self, request):
        columns = super().get_list_display(request)
        if can_see_owner_private(request):
            return columns
        return [c for c in columns if c not in ("kyc_status", "documents_check")]

    def get_list_filter(self, request):
        filters = super().get_list_filter(request)
        if can_see_owner_private(request):
            return filters
        return [f for f in filters if f not in ("kyc_status", DocumentExpiryFilter)]

    def get_search_fields(self, request):
        if can_see_owner_private(request):
            return super().get_search_fields(request)
        return ("name", "email", "phone")

    def has_add_permission(self, request):
        # A new owner needs an Emirates ID / passport / licence, so only people
        # who may handle those can add one.
        return super().has_add_permission(request) and can_see_owner_private(request)

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj, change=change, **kwargs)
        if can_see_owner_private(request):
            return form

        class LimitedOwnerForm(form):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.instance._skip_identity_checks = True

        return LimitedOwnerForm


# ---------------------------------------------------------------------------
# Properties and units
# ---------------------------------------------------------------------------


class UnitInline(UnfoldTabularInline):
    """Lets you add and edit units directly on the property page."""

    model = Unit
    extra = 0
    fields = (
        "unit_number",
        "unit_type",
        "floor",
        "bedrooms",
        "rent_amount",
        "under_maintenance",
        "current_status",
    )
    readonly_fields = ("current_status",)

    @admin.display(description="Status")
    def current_status(self, obj):
        if obj is None or not obj.pk:
            return "-"
        return status_badge(obj.get_status())


class PropertyOwnershipInline(UnfoldTabularInline):
    """Who owned this property before. Filled in automatically when you change
    the owner; correct the dates here if needed."""

    model = PropertyOwnership
    extra = 0
    fields = ("owner", "from_date", "to_date")
    autocomplete_fields = ("owner",)
    verbose_name_plural = "Previous owners (rent before the change goes to them)"


@admin.register(Property)
class PropertyAdmin(UnfoldModelAdmin):
    # Owner, fee and agreement dates appear only for third-party properties.
    conditional_fields = {
        "owner": "ownership_type == 'third_party'",
        "management_fee_percent": "ownership_type == 'third_party'",
        "management_start": "ownership_type == 'third_party'",
        "management_end": "ownership_type == 'third_party'",
    }
    list_display = (
        "name",
        "emirate",
        "community",
        "ownership_type",
        "owner",
        "our_fee",
        "unit_count",
        "is_active",
    )
    ordering = ("name",)
    list_filter = ("ownership_type", "emirate", "property_type", "is_active")
    list_select_related = ("owner",)
    search_fields = (
        "name",
        "city",
        "community",
        "title_deed_number",
        "makani_number",
        "owner__name",
    )
    autocomplete_fields = ("owner",)
    inlines = [UnitInline, PropertyOwnershipInline, PropertyDocumentInline]
    fieldsets = (
        (
            "Property details",
            {
                "fields": (
                    "name",
                    "property_type",
                    "emirate",
                    "city",
                    "community",
                    "address",
                    "year_built",
                )
            },
        ),
        (
            "Title & registration",
            {
                "fields": (
                    "title_deed_number",
                    "plot_number",
                    "makani_number",
                    "municipality_number",
                    "dewa_premise_number",
                )
            },
        ),
        (
            "Ownership & fee",
            {
                "description": "Third-party properties belong to a landlord and earn us a management fee.",
                "fields": (
                    "ownership_type",
                    "owner",
                    "management_fee_percent",
                    "management_start",
                    "management_end",
                ),
            },
        ),
        ("Notes & status", {"fields": ("notes", "is_active")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_unit_count=Count("units"))

    @admin.display(description="Our fee")
    def our_fee(self, obj):
        value = obj.effective_fee_percent
        return "-" if value is None else f"{value}%"

    @admin.display(description="Units", ordering="_unit_count")
    def unit_count(self, obj):
        return obj._unit_count


class UnitStatusFilter(admin.SimpleListFilter):
    title = "status"
    parameter_name = "unit_status"

    def lookups(self, request, model_admin):
        return Unit.Status.choices

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status_calc=self.value())
        return queryset


@admin.register(Unit)
class UnitAdmin(UnfoldModelAdmin):
    list_display = ("property", "unit_number", "unit_type", "annual_rent", "unit_status")
    list_filter = (UnitStatusFilter, "unit_type", "furnishing", "property")
    search_fields = ("unit_number", "property__name", "dewa_number")
    autocomplete_fields = ("property",)
    readonly_fields = ("current_status",)
    fieldsets = (
        (
            "Unit details",
            {
                "fields": (
                    "property",
                    "unit_number",
                    "unit_type",
                    "floor",
                    "bedrooms",
                    "bathrooms",
                    "size_sqft",
                )
            },
        ),
        (
            "Features & utilities",
            {"fields": ("furnishing", "ac_type", "parking_slots", "dewa_number")},
        ),
        (
            "Rent & status",
            {
                "description": "Occupied, Reserved and Vacant are worked out automatically from the unit's leases.",
                "fields": ("rent_amount", "current_status", "under_maintenance", "notes"),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).with_status().select_related("property")

    @admin.display(description="Annual rent (AED)", ordering="rent_amount")
    def annual_rent(self, obj):
        return f"{obj.rent_amount:,.2f}"

    @admin.display(description="Status", ordering="status_calc")
    def unit_status(self, obj):
        return status_badge(obj.status_calc)

    @admin.display(description="Current status")
    def current_status(self, obj):
        if obj is None or not obj.pk:
            return "-"
        return status_badge(obj.get_status())


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------


class TenantLeaseInline(UnfoldTabularInline):
    """Shows a tenant's leases on the tenant page."""

    model = Lease
    extra = 0
    fields = ("unit", "start_date", "end_date", "annual_rent", "status")
    autocomplete_fields = ("unit",)
    show_change_link = True


@admin.register(Tenant)
class TenantAdmin(UnfoldModelAdmin):
    # Company-only boxes appear only when the tenant is a company.
    conditional_fields = {
        "trade_licence_number": "tenant_type == 'company'",
        "trade_licence_expiry": "tenant_type == 'company'",
        "trn": "tenant_type == 'company'",
        "authorised_signatory": "tenant_type == 'company'",
    }
    compliance_date_fields = (
        "emirates_id_expiry",
        "passport_expiry",
        "residence_visa_expiry",
        "trade_licence_expiry",
    )
    list_display = (
        "name",
        "tenant_type",
        "phone",
        "kyc_status",
        "documents_check",
        "active_leases",
        "bounced_cheques",
        "is_active",
    )
    ordering = ("name",)
    list_filter = ("tenant_type", "kyc_status", DocumentExpiryFilter, "is_active")
    search_fields = (
        "name",
        "email",
        "phone",
        "emirates_id",
        "passport_number",
        "trade_licence_number",
    )
    readonly_fields = ("compliance_check", "kyc_verified_on")
    inlines = [TenantDocumentInline, TenantLeaseInline]
    fieldsets = (
        (
            "Tenant details",
            {
                "fields": (
                    "tenant_type",
                    "name",
                    "date_of_birth",
                    "nationality",
                    "email",
                    "phone",
                    "whatsapp",
                    "address",
                )
            },
        ),
        (
            "Identification",
            {
                "description": "Individuals: Emirates ID and/or passport. Companies: trade licence.",
                "fields": (
                    "emirates_id",
                    "emirates_id_expiry",
                    "passport_number",
                    "passport_expiry",
                    "residence_visa_expiry",
                    "trade_licence_number",
                    "trade_licence_expiry",
                    "trn",
                    "authorised_signatory",
                ),
            },
        ),
        (
            "Employer & emergency contact",
            {
                "fields": (
                    "employer",
                    "emergency_contact_name",
                    "emergency_contact_phone",
                )
            },
        ),
        ("KYC review", {"fields": ("compliance_check", "kyc_status", "kyc_verified_on")}),
        ("Notes & status", {"fields": ("notes", "is_active")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _active_leases=Count(
                "leases", filter=Q(leases__status=Lease.Status.ACTIVE), distinct=True
            ),
            _bounced=Count(
                "leases__cheques",
                filter=Q(leases__cheques__returned_date__isnull=False),
                distinct=True,
            ),
        )

    @admin.display(description="Documents")
    def documents_check(self, obj):
        return compliance_badge(obj)

    @admin.display(description="Document check")
    def compliance_check(self, obj):
        return compliance_report(obj)

    @admin.display(description="Active leases", ordering="_active_leases")
    def active_leases(self, obj):
        return obj._active_leases

    @admin.display(description="Bounced cheques (ever)", ordering="_bounced")
    def bounced_cheques(self, obj):
        if obj._bounced:
            return format_html('<strong style="color:#c62828;">{}</strong>', obj._bounced)
        return 0


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------


class ChequeInline(UnfoldTabularInline):
    """The payments (cheques, cash, card, transfers) for this lease."""

    model = Cheque
    fk_name = "lease"
    extra = 0
    can_delete = False  # payments are cancelled, never deleted
    fields = (
        "purpose",
        "method",
        "installment_no",
        "cheque_number",
        "bank_name",
        "reference",
        "cheque_date",
        "amount",
        "status",
        "return_reason",
    )
    show_change_link = True
    ordering = ("cheque_date", "id")
    verbose_name_plural = "Payments & cheques for this lease"


class ExpiryFilter(admin.SimpleListFilter):
    title = "expiry"
    parameter_name = "expiry"

    def lookups(self, request, model_admin):
        return [
            ("30", "Expiring within 30 days"),
            ("60", "Expiring within 60 days"),
            ("90", "Expiring within 90 days"),
            ("overdue", "Past end date but still active"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        today = timezone.localdate()
        if value == "overdue":
            return queryset.filter(status=Lease.Status.ACTIVE, end_date__lt=today)
        if value in ("30", "60", "90"):
            return queryset.filter(
                status=Lease.Status.ACTIVE,
                end_date__gte=today,
                end_date__lte=today + timedelta(days=int(value)),
            )
        return queryset


class LeaseAdminForm(forms.ModelForm):
    create_cheque_schedule = forms.BooleanField(
        required=False,
        label="Create the payment schedule automatically",
        help_text=(
            "Tick to add one empty 'Expected' rent payment for every installment, "
            "with the right dates and amounts. Fill in the cheque numbers later. "
            "Ignored if the lease already has rent payments."
        ),
    )

    class Meta:
        model = Lease
        fields = "__all__"


class TerminateForm(forms.Form):
    """The 'Terminate early' page. Follows how UAE tenancy exits normally work."""

    notice_given_on = forms.DateField(
        required=False,
        label="Written notice given on",
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="The date the tenant (or landlord) gave formal written notice.",
    )
    notice_days = forms.IntegerField(
        min_value=0,
        max_value=365,
        required=False,
        label="Notice period (days)",
        help_text="Usually 60 to 90 days in the contract. Rent is payable through it.",
    )
    exit_date = forms.DateField(
        label="Last day of the tenancy (exit date)",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    by_mutual_agreement = forms.BooleanField(
        required=False,
        label="Agreed with the tenant (mutual agreement)",
        help_text="Tick if both sides agreed an exit date, even if it is shorter than "
        "the notice period.",
    )
    penalty = forms.DecimalField(
        min_value=0,
        max_digits=12,
        decimal_places=2,
        initial=0,
        label="Early-exit penalty (AED)",
        help_text="From the contract's early-termination clause (usually 1 to 2 months "
        "of rent). Enter 0 if there is none. It is NOT taken from the deposit.",
    )
    reason = forms.CharField(max_length=255, required=False, label="Reason (for the record)")


@admin.register(Lease)
class LeaseAdmin(UnfoldModelAdmin):
    form = LeaseAdminForm
    list_display = (
        "unit",
        "tenant",
        "start_date",
        "end_date",
        "rent",
        "days_left",
        "cheque_progress",
        "status",
    )
    list_filter = ("status", ExpiryFilter, "usage", "unit__property")
    list_select_related = ("tenant", "unit", "unit__property")
    search_fields = (
        "tenant__name",
        "unit__unit_number",
        "unit__property__name",
        "ejari_number",
        "contract_number",
    )
    autocomplete_fields = ("tenant", "unit", "renewal_of")
    inlines = [ChequeInline, LeaseDocumentInline]
    actions = ["mark_expired", "generate_schedule", "rebuild_unreceived", "create_renewal"]
    fieldsets = (
        (
            "Who and what",
            {
                "fields": (
                    "tenant",
                    "unit",
                    "contract_number",
                    "ejari_number",
                    "usage",
                    "renewal_of",
                )
            },
        ),
        (
            "Period",
            {
                "description": "Any length works: 6 months, 18 months, 3 years... "
                "The rent below is a yearly figure and is pro-rated to the lease length.",
                "fields": ("start_date", "end_date"),
            },
        ),
        (
            "Rent & fees",
            {
                "fields": (
                    "annual_rent",
                    "vat_percent",
                    "number_of_payments",
                    "payment_method",
                    "agency_commission",
                    "registration_fee",
                    "financial_summary",
                    "create_cheque_schedule",
                )
            },
        ),
        (
            "Security deposit",
            {
                "description": "Record the deposit itself as a payment with purpose "
                "'Security deposit'. Refunding is done with the button below.",
                "fields": (
                    "security_deposit",
                    "deposit_carried_over",
                    "handover_date",
                    "deposit_deductions",
                    "deposit_deduction_notes",
                    "deposit_panel",
                ),
            },
        ),
        ("Ending the lease", {"fields": ("status", "ending_panel")}),
        ("Notes", {"fields": ("notes",)}),
    )

    def get_readonly_fields(self, request, obj=None):
        fixed = ["financial_summary", "deposit_panel", "ending_panel", "status"]
        if obj is not None and obj.deposit_refunded_on:
            fixed += [
                "deposit_carried_over",
                "handover_date",
                "deposit_deductions",
                "deposit_deduction_notes",
            ]
        return fixed

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("cheques")

    # --- columns -----------------------------------------------------------

    @admin.display(description="Annual rent (AED)", ordering="annual_rent")
    def rent(self, obj):
        return f"{obj.annual_rent:,.2f}"

    @admin.display(description="Days left")
    def days_left(self, obj):
        if obj.status != Lease.Status.ACTIVE:
            return "-"
        days = (obj.end_date - timezone.localdate()).days
        return "Overdue" if days < 0 else days

    @admin.display(description="Payments")
    def cheque_progress(self, obj):
        S = Cheque.Status
        live = [
            c
            for c in obj.cheques.all()
            if c.status not in (S.CANCELLED, S.REPLACED) and c.purpose == Cheque.Purpose.RENT
        ]
        if not live:
            return "-"
        cleared = sum(1 for c in live if c.status == S.CLEARED)
        bounced = sum(1 for c in live if c.status == S.RETURNED)
        text = f"{cleared} of {len(live)} paid"
        if bounced:
            return format_html(
                '{} <strong style="color:#c62828;">- {} bounced</strong>', text, bounced
            )
        return text

    # --- panels on the lease page -----------------------------------------

    def _action_button(self, obj, action, label, confirm):
        # A submit button that posts to our page. (Forms cannot be nested.)
        return format_html(
            '<button type="submit" formmethod="post" formnovalidate '
            'formaction="{}" class="button" style="margin:2px 8px 2px 0;" '
            "onclick=\"return confirm('{}');\">{}</button>",
            reverse("admin:realestate_lease_do", args=[obj.pk, action]),
            confirm,
            label,
        )

    @admin.display(description="Rent summary")
    def financial_summary(self, obj):
        if obj is None or not obj.pk:
            return "Save the lease to see the totals."
        totals = obj.cheque_totals()
        gap = obj.schedule_gap()
        rows = [
            (f"Contract total for {obj.term_months} months (rent + VAT)", obj.contract_total),
            ("Rent payments recorded", totals["scheduled"]),
            ("Paid (cleared)", totals["cleared"]),
            ("Deposited, waiting to clear", totals["deposited"]),
            ("Bounced, not yet replaced", totals["returned"]),
            ("Not yet covered by a payment" if gap >= 0 else "Recorded MORE than the contract", abs(gap)),
        ]
        body = format_html_join(
            "",
            '<tr><td style="padding:2px 18px 2px 0;">{}</td>'
            '<td style="text-align:right;"><strong>{}</strong></td></tr>',
            ((label, aed(value)) for label, value in rows),
        )
        extra = ""
        if totals["scheduled"] and gap != 0 and obj.status == Lease.Status.ACTIVE:
            extra = format_html(
                '<p style="color:#c62828;margin:8px 0 0;"><strong>The rent payments do not '
                "match the contract total.</strong> If you changed the rent or the dates, "
                "rebuild the payments that are not received yet: {}</p>",
                self._action_button(
                    obj,
                    "rebuild",
                    "Rebuild unreceived payments",
                    "Delete the expected (not received) rent payments and recreate them "
                    "from the current rent and dates?",
                ),
            )
        return format_html("<table>{}</table>{}", body, extra)

    @admin.display(description="Deposit")
    def deposit_panel(self, obj):
        if obj is None or not obj.pk:
            return "Save the lease first."
        rows = [
            ("Deposit held", obj.deposit_received()),
            ("Deductions (damage / utilities only)", obj.deposit_deductions or 0),
            ("To refund to the tenant", obj.deposit_refundable()),
        ]
        body = format_html_join(
            "",
            '<tr><td style="padding:2px 18px 2px 0;">{}</td>'
            '<td style="text-align:right;"><strong>{}</strong></td></tr>',
            ((label, aed(value)) for label, value in rows),
        )
        if obj.deposit_refunded_on:
            note = format_html(
                '<p style="color:#2e7d32;"><strong>Refunded {} on {}.</strong></p>',
                aed(obj.deposit_refund_amount or 0),
                obj.deposit_refunded_on.strftime("%d %b %Y"),
            )
        elif obj.status == Lease.Status.ACTIVE:
            note = format_html("<p>The deposit is refunded after the lease ends.</p>")
        else:
            problems = obj.deposit_refund_problems()
            if problems:
                items = format_html_join("", "<li>{}</li>", ((p,) for p in problems))
                note = format_html(
                    '<p style="margin:8px 0 2px;"><strong>Not ready to refund:</strong></p>'
                    '<ul style="margin:0;padding-left:18px;">{}</ul>',
                    items,
                )
            else:
                note = self._action_button(
                    obj,
                    "refund",
                    "Record deposit refund",
                    "Record that the deposit was refunded to the tenant? Save any "
                    "changes to this form first.",
                )
        return format_html("<table>{}</table>{}", body, note)

    @admin.display(description="Lease ending")
    def ending_panel(self, obj):
        if obj is None or not obj.pk:
            return "Save the lease first."
        if obj.status == Lease.Status.ACTIVE:
            parts = [
                format_html(
                    '<a class="button" style="margin:2px 8px 2px 0;" href="{}">Terminate early...</a>',
                    reverse("admin:realestate_lease_terminate", args=[obj.pk]),
                )
            ]
            if obj.end_date < timezone.localdate():
                parts.append(
                    self._action_button(
                        obj,
                        "expire",
                        "Mark as expired",
                        "The lease has ended. Mark it as expired? Save any changes first.",
                    )
                )
            return format_html_join("", "{}", ((p,) for p in parts))
        if obj.status == Lease.Status.EXPIRED:
            return "This lease ended on its end date."
        lines = [
            f"Terminated early. Last day of the tenancy: {obj.exit_date:%d %b %Y}."
            if obj.exit_date
            else "Terminated early.",
        ]
        if obj.by_mutual_agreement:
            lines.append("Agreed with the tenant (mutual agreement).")
        elif obj.notice_given_on:
            lines.append(
                f"Notice given {obj.notice_given_on:%d %b %Y}, {obj.notice_days or 0} days."
            )
        if obj.termination_penalty:
            lines.append(
                f"Penalty {aed(obj.termination_penalty)} "
                f"(collected so far: {aed(obj.penalty_collected())})."
            )
        if obj.termination_reason:
            lines.append(f"Reason: {obj.termination_reason}")
        return format_html_join("", "<div>{}</div>", ((line,) for line in lines))

    # --- saving ------------------------------------------------------------

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        state = obj.tenant.compliance_state
        if state in ("expired", "missing"):
            self.message_user(
                request,
                f"Warning: {obj.tenant.name} has expired or incomplete papers "
                f"({'; '.join(m for _, m in obj.tenant.compliance_issues())}). "
                "Ask for updated documents.",
                messages.WARNING,
            )
        elif obj.tenant.kyc_status != "verified":
            self.message_user(
                request,
                f"Note: {obj.tenant.name} is not marked KYC verified yet.",
                messages.WARNING,
            )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        lease = form.instance
        if form.cleaned_data.get("create_cheque_schedule"):
            created = lease.generate_cheque_schedule()
            if created:
                self.message_user(
                    request,
                    f"{created} expected rent payments were created. "
                    "Open the lease to fill in the cheque numbers.",
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    "No schedule was created because this lease already has rent payments.",
                    messages.WARNING,
                )
        elif lease.status == Lease.Status.ACTIVE and lease.cheque_totals()["scheduled"]:
            gap = lease.schedule_gap()
            if gap != 0:
                word = "less" if gap > 0 else "more"
                self.message_user(
                    request,
                    f"The rent payments on file are {aed(abs(gap))} {word} than the "
                    f"contract total of {aed(lease.contract_total)}. Use 'Rebuild "
                    "unreceived payments' if the rent or the dates changed.",
                    messages.WARNING,
                )

    # --- our own pages -----------------------------------------------------

    def get_urls(self):
        custom = [
            path(
                "<int:pk>/terminate/",
                self.admin_site.admin_view(self.terminate_view),
                name="realestate_lease_terminate",
            ),
            path(
                "<int:pk>/do/<str:action>/",
                self.admin_site.admin_view(self.action_view),
                name="realestate_lease_do",
            ),
        ]
        return custom + super().get_urls()

    def _back(self, lease):
        return HttpResponseRedirect(reverse("admin:realestate_lease_change", args=[lease.pk]))

    def action_view(self, request, pk, action):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        lease = get_object_or_404(Lease, pk=pk)
        if not self.has_change_permission(request, lease):
            raise PermissionDenied
        try:
            if action == "expire":
                unpaid = lease.mark_expired(user=request.user)
                log_change(request, lease, "Marked as expired.")
                messages.success(request, "The lease was marked as expired.")
                if unpaid:
                    messages.warning(
                        request,
                        f"{unpaid} payment(s) for this lease are still unpaid. They stay on "
                        "the cheque desk until collected or cancelled.",
                    )
            elif action == "rebuild":
                removed, created = lease.rebuild_schedule()
                log_change(request, lease, f"Payment schedule rebuilt ({removed} removed, {created} created).")
                messages.success(
                    request,
                    f"Rebuilt: {removed} expected payment(s) removed, {created} created.",
                )
            elif action == "refund":
                amount = lease.refund_deposit(user=request.user)
                log_change(request, lease, f"Deposit refund recorded: AED {amount}.")
                messages.success(request, f"Deposit refund of {aed(amount)} recorded.")
            else:
                messages.error(request, "Unknown action.")
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        return self._back(lease)

    def terminate_view(self, request, pk):
        lease = get_object_or_404(Lease.objects.select_related("tenant", "unit__property"), pk=pk)
        if not self.has_change_permission(request, lease):
            raise PermissionDenied
        if lease.status != Lease.Status.ACTIVE:
            messages.error(request, "Only an active lease can be terminated.")
            return self._back(lease)

        settings_ = CompanySettings.load()
        suggested_penalty = (lease.monthly_rent * settings_.penalty_months).quantize(Decimal("0.01"))
        if request.method == "POST":
            form = TerminateForm(request.POST)
            if form.is_valid():
                data = form.cleaned_data
                try:
                    summary = lease.terminate(
                        exit_date=data["exit_date"],
                        notice_given_on=data["notice_given_on"],
                        notice_days=data["notice_days"],
                        penalty=data["penalty"],
                        reason=data["reason"],
                        by_mutual_agreement=data["by_mutual_agreement"],
                        user=request.user,
                    )
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    log_change(
                        request,
                        lease,
                        f"Terminated early, exit {data['exit_date']:%d %b %Y}. "
                        f"Penalty AED {data['penalty']}.",
                    )
                    messages.success(
                        request,
                        f"Lease terminated. Last day of the tenancy: {data['exit_date']:%d %b %Y}.",
                    )
                    if summary["cancelled"]:
                        messages.info(
                            request,
                            f"{summary['cancelled']} expected payment(s) after the exit date were cancelled.",
                        )
                    if summary["returned_to_tenant"]:
                        messages.warning(
                            request,
                            f"{summary['returned_to_tenant']} uncashed cheque(s) dated after the "
                            "exit must be handed back to the tenant. See the lease's payments.",
                        )
                    if summary["prorated"]:
                        messages.info(
                            request,
                            "The rent payment covering the exit date was reduced to the days used.",
                        )
                    if summary["needs_review"]:
                        messages.warning(
                            request,
                            f"{summary['needs_review']} payment(s) already deposited or cleared cover "
                            "time after the exit date. Check them and refund any overpayment.",
                        )
                    if summary["penalty_payment"]:
                        messages.info(
                            request,
                            "A payment for the penalty was added. Collect it like any other payment.",
                        )
                    return self._back(lease)
        else:
            form = TerminateForm(
                initial={
                    "notice_given_on": timezone.localdate(),
                    "notice_days": settings_.default_notice_days,
                    "exit_date": timezone.localdate() + timedelta(days=settings_.default_notice_days),
                    "penalty": suggested_penalty,
                }
            )
        context = {
            **self.admin_site.each_context(request),
            "title": "Terminate lease early",
            "opts": self.model._meta,
            "lease": lease,
            "form": form,
            "suggested_penalty": suggested_penalty,
            "penalty_months": settings_.penalty_months,
        }
        return TemplateResponse(request, "admin/realestate/lease/terminate.html", context)

    # --- actions -----------------------------------------------------------

    @admin.action(description="Mark selected ended leases as expired")
    def mark_expired(self, request, queryset):
        done = 0
        problems = []
        for lease in queryset:
            try:
                lease.mark_expired(user=request.user)
                log_change(request, lease, "Marked as expired.")
                done += 1
            except ValidationError as error:
                problems.append(f"{lease}: {' '.join(error.messages)}")
        if done:
            self.message_user(request, f"{done} lease(s) marked as expired.", messages.SUCCESS)
        for line in problems:
            self.message_user(request, f"Skipped - {line}", messages.WARNING)

    @admin.action(description="Create the payment schedule for selected leases")
    def generate_schedule(self, request, queryset):
        made = skipped = 0
        for lease in queryset:
            if lease.generate_cheque_schedule():
                made += 1
            else:
                skipped += 1
        if made:
            self.message_user(
                request, f"Payment schedules created for {made} lease(s).", messages.SUCCESS
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} lease(s) skipped because they already have rent payments.",
                messages.WARNING,
            )

    @admin.action(description="Rebuild the expected (not received) payments of selected leases")
    def rebuild_unreceived(self, request, queryset):
        done = 0
        for lease in queryset:
            try:
                lease.rebuild_schedule()
                log_change(request, lease, "Payment schedule rebuilt.")
                done += 1
            except ValidationError as error:
                self.message_user(request, f"{lease}: {' '.join(error.messages)}", messages.WARNING)
        if done:
            self.message_user(request, f"{done} lease(s) rebuilt.", messages.SUCCESS)

    @admin.action(description="Create a renewal for selected leases (same length)")
    def create_renewal(self, request, queryset):
        created = skipped = 0
        for old in queryset.filter(status=Lease.Status.ACTIVE):
            if old.renewals.exists():
                skipped += 1
                continue
            start = old.end_date + timedelta(days=1)
            new = Lease(
                tenant=old.tenant,
                unit=old.unit,
                contract_number="",
                ejari_number="",
                usage=old.usage,
                renewal_of=old,
                start_date=start,
                end_date=add_months(start, old.term_months) - timedelta(days=1),
                annual_rent=old.annual_rent,
                vat_percent=old.vat_percent,
                # The tenant's existing deposit stays with us: it is not new money.
                security_deposit=old.security_deposit,
                deposit_carried_over=old.deposit_received() > 0,
                number_of_payments=old.number_of_payments,
                payment_method=old.payment_method,
                status=Lease.Status.ACTIVE,
            )
            try:
                new.full_clean()
            except ValidationError:
                skipped += 1
                continue
            new.save()
            created += 1
        if created:
            self.message_user(
                request,
                f"{created} renewal(s) created. Open each one to update the rent, "
                "the Ejari number and the payments. The existing deposit is carried "
                "over, not counted again.",
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} lease(s) skipped (already renewed, or the dates clash).",
                messages.WARNING,
            )


# ---------------------------------------------------------------------------
# Cheques
# ---------------------------------------------------------------------------


class ChequeDueFilter(admin.SimpleListFilter):
    title = "due date"
    parameter_name = "due"

    def lookups(self, request, model_admin):
        return [
            ("today", "Due today (not deposited yet)"),
            ("tomorrow", "Due tomorrow"),
            ("week", "Due in the next 7 days"),
            ("overdue", "Overdue (not deposited yet)"),
            ("month", "Due this month"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        today = timezone.localdate()
        not_deposited = queryset.filter(
            status__in=[Cheque.Status.PENDING, Cheque.Status.RECEIVED]
        )
        if value == "today":
            return not_deposited.filter(cheque_date=today)
        if value == "tomorrow":
            return not_deposited.filter(cheque_date=today + timedelta(days=1))
        if value == "week":
            return not_deposited.filter(
                cheque_date__gte=today, cheque_date__lte=today + timedelta(days=7)
            )
        if value == "overdue":
            return not_deposited.filter(cheque_date__lt=today)
        if value == "month":
            return queryset.filter(cheque_date__year=today.year, cheque_date__month=today.month)
        return queryset


class ChequeEventInline(UnfoldTabularInline):
    """Read-only history of the cheque."""

    model = ChequeEvent
    extra = 0
    can_delete = False
    fields = ("at", "user", "from_status", "to_status", "note")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Cheque)
class ChequeAdmin(UnfoldModelAdmin):
    date_hierarchy = "cheque_date"
    conditional_fields = {
        "cheque_number": "method == 'cheque'",
        "bank_name": "method == 'cheque'",
        "drawer_name": "method == 'cheque'",
    }
    list_display = (
        "cheque_date",
        "tenant_name",
        "unit_label",
        "method",
        "cheque_number",
        "bank_name",
        "amount_aed",
        "purpose",
        "cheque_status",
    )
    list_filter = (
        "status",
        ChequeDueFilter,
        "method",
        "purpose",
        "lease__unit__property",
    )
    list_select_related = ("lease__tenant", "lease__unit__property")
    search_fields = (
        "cheque_number",
        "bank_name",
        "reference",
        "drawer_name",
        "lease__tenant__name",
        "lease__unit__unit_number",
        "lease__unit__property__name",
    )
    autocomplete_fields = ("lease",)
    raw_id_fields = ("replaces",)
    inlines = [ChequeEventInline]
    actions = ["action_deposit", "action_clear", "action_cancel"]
    fieldsets = (
        (
            "Payment",
            {
                "fields": (
                    "lease",
                    "purpose",
                    "method",
                    "installment_no",
                    "cheque_number",
                    "bank_name",
                    "reference",
                    "drawer_name",
                    "amount",
                    "cheque_date",
                )
            },
        ),
        (
            "Status",
            {
                "description": (
                    "The daily cheque desk changes these for you with one click. "
                    "Edit them here only to correct a mistake."
                ),
                "fields": (
                    "status",
                    "received_date",
                    "deposited_date",
                    "cleared_date",
                    "returned_date",
                    "return_reason",
                    "replaces",
                ),
            },
        ),
        ("Notes", {"fields": ("notes",)}),
    )

    # --- locking -----------------------------------------------------------

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_locked:
            return [f.name for f in Cheque._meta.fields if f.editable]
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None:
            try:
                obj._check_deletable()
            except ValidationError:
                return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        # Bulk delete would skip our rule: payments are cancelled, not deleted.
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    # --- columns -----------------------------------------------------------

    @admin.display(description="Tenant")
    def tenant_name(self, obj):
        return obj.lease.tenant

    @admin.display(description="Unit")
    def unit_label(self, obj):
        return obj.lease.unit

    @admin.display(description="Amount (AED)", ordering="amount")
    def amount_aed(self, obj):
        return f"{obj.amount:,.2f}"

    @admin.display(description="Status", ordering="status")
    def cheque_status(self, obj):
        return badge(obj.get_status_display(), CHEQUE_STATUS_COLORS.get(obj.status, "#555"))

    # --- history -----------------------------------------------------------

    def save_model(self, request, obj, form, change):
        old_status = form.initial.get("status", "") if change else ""
        super().save_model(request, obj, form, change)
        if not change or old_status != obj.status:
            ChequeEvent.objects.create(
                cheque=obj,
                user=request.user,
                from_status=old_status or "",
                to_status=obj.status,
                note="Changed in the cheque form" if change else "Created",
            )

    # --- bulk actions ------------------------------------------------------

    def _run_bulk(self, request, queryset, method_name, done_text):
        done = skipped = 0
        for cheque in queryset:
            try:
                getattr(cheque, method_name)(user=request.user)
                done += 1
            except ValidationError:
                skipped += 1
        if done:
            self.message_user(request, f"{done} cheque(s) {done_text}.", messages.SUCCESS)
        if skipped:
            self.message_user(
                request,
                f"{skipped} cheque(s) skipped because their status does not allow this.",
                messages.WARNING,
            )

    @admin.action(description="Mark selected cheques as deposited today")
    def action_deposit(self, request, queryset):
        self._run_bulk(request, queryset, "mark_deposited", "marked as deposited")

    @admin.action(description="Mark selected cheques / payments as cleared (paid) today")
    def action_clear(self, request, queryset):
        self._run_bulk(request, queryset, "mark_cleared", "marked as cleared")

    @admin.action(description="Cancel selected payments (never collected)")
    def action_cancel(self, request, queryset):
        self._run_bulk(request, queryset, "cancel", "cancelled")

    # --- the daily desk ----------------------------------------------------

    def get_urls(self):
        custom = [
            path(
                "desk/",
                self.admin_site.admin_view(self.desk_view),
                name="realestate_cheque_desk",
            ),
            path(
                "<int:pk>/workflow/",
                self.admin_site.admin_view(self.workflow_view),
                name="realestate_cheque_workflow",
            ),
        ]
        # Our own pages must come before Django's catch-all ones.
        return custom + super().get_urls()

    def desk_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied

        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        S = Cheque.Status
        base = Cheque.objects.select_related("lease__tenant", "lease__unit__property")
        limit = 300

        definitions = [
            (
                "bounced",
                "Bounced",
                "Bounced cheques: action needed",
                "danger",
                "Returned by the bank and not replaced yet. Create a replacement, or open the cheque to add notes.",
                base.filter(status=S.RETURNED).order_by("-returned_date", "id"),
            ),
            (
                "today",
                "Deposit today",
                "Due today: deposit these",
                "primary",
                "The date on these cheques is today. Take them to the bank, then click Deposit.",
                base.filter(status=S.RECEIVED, cheque_date=today).order_by("id"),
            ),
            (
                "overdue",
                "Overdue",
                "Overdue: date has passed, not deposited",
                "warn",
                "Deposit these as soon as possible. Cheques are commonly valid for 6 months from the "
                "date on them, so confirm the exact rule with your bank.",
                base.filter(status=S.RECEIVED, cheque_date__lt=today).order_by("cheque_date", "id"),
            ),
            (
                "missing",
                "Not received",
                "Expected but not received",
                "warn",
                "The date has arrived but nothing has been received. Cheques: open it and fill in the "
                "number and bank when it arrives. Cash, card or transfer: click Paid when the money is in.",
                base.filter(status=S.PENDING, cheque_date__lte=today).order_by("cheque_date", "id"),
            ),
            (
                "waiting",
                "At the bank",
                "Deposited: waiting to clear",
                "info",
                "Click Cleared when the money is in the account, or Bounced (with the reason) if the bank returns it.",
                base.filter(status=S.DEPOSITED).order_by("deposited_date", "id"),
            ),
            (
                "tomorrow",
                "Due tomorrow",
                "Due tomorrow",
                "muted",
                "Get these ready for tomorrow.",
                base.filter(status=S.RECEIVED, cheque_date=tomorrow).order_by("id"),
            ),
            (
                "week",
                "Next 7 days",
                "Coming up in the next 7 days",
                "muted",
                "Due after tomorrow, up to a week from today.",
                base.filter(
                    status=S.RECEIVED,
                    cheque_date__gt=tomorrow,
                    cheque_date__lte=today + timedelta(days=7),
                ).order_by("cheque_date", "id"),
            ),
            (
                "cleared",
                "Cleared (7 days)",
                "Cleared in the last 7 days",
                "good",
                "Money received recently.",
                base.filter(
                    status=S.CLEARED, cleared_date__gte=today - timedelta(days=7)
                ).order_by("-cleared_date", "id"),
            ),
        ]

        sections = []
        for key, short, title, tone, note, queryset in definitions:
            rows = list(queryset[:limit])
            sections.append(
                {
                    "key": key,
                    "short": short,
                    "title": title,
                    "tone": tone,
                    "note": note,
                    "cheques": rows,
                    "count": len(rows),
                    "capped": len(rows) == limit,
                    "total": sum((c.amount for c in rows), Decimal("0")),
                }
            )

        context = {
            **self.admin_site.each_context(request),
            "title": "Daily cheque desk",
            "opts": self.model._meta,
            "today": today,
            "sections": sections,
            "return_reasons": Cheque.ReturnReason.choices,
            "can_change": self.has_change_permission(request),
        }
        return TemplateResponse(request, "admin/realestate/cheque/desk.html", context)

    def workflow_view(self, request, pk):
        """Handles the one-click buttons on the cheque desk."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        cheque = get_object_or_404(Cheque.objects.select_related("lease__tenant"), pk=pk)
        if not self.has_change_permission(request, cheque):
            raise PermissionDenied

        action = request.POST.get("action", "")
        who = cheque.lease.tenant.name
        label = cheque.cheque_number or "(no number)"
        if not cheque.is_cheque:
            label = cheque.get_method_display().lower()
        try:
            if action == "deposit":
                cheque.mark_deposited(user=request.user)
                messages.success(request, f"Cheque {label} from {who} marked as deposited.")
            elif action == "clear":
                cheque.mark_cleared(user=request.user)
                messages.success(request, f"Cheque {label} from {who} marked as cleared.")
            elif action == "pay":
                cheque.mark_cleared(
                    user=request.user, reference=request.POST.get("reference", "")
                )
                messages.success(
                    request,
                    f"{cheque.get_method_display()} payment of AED {cheque.amount:,.2f} "
                    f"from {who} marked as paid.",
                )
            elif action == "return":
                cheque.mark_returned(request.POST.get("reason", ""), user=request.user)
                messages.warning(
                    request,
                    f"Cheque {label} from {who} marked as bounced. "
                    "Create a replacement from the Bounced section.",
                )
            elif action == "replace":
                cheque.create_replacement(user=request.user)
                messages.success(
                    request,
                    f"A replacement cheque was created for {who}. "
                    "Open it when the new cheque arrives to enter its number and bank.",
                )
            else:
                messages.error(request, "Unknown action.")
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))

        next_url = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            next_url = reverse("admin:realestate_cheque_desk")
        return HttpResponseRedirect(next_url)


# Expenses, maintenance and owner statements live in their own file.
from . import admin_finance  # noqa: E402,F401


# The home page: replace the plain "index" view with one that also shows the dashboard.
from .dashboard import dashboard_context  # noqa: E402

_plain_index = admin.site.index


def _dashboard_index(request, extra_context=None):
    context = dict(extra_context or {})
    context.update(dashboard_context(request))
    return _plain_index(request, context)


admin.site.index = _dashboard_index
admin.site.index_template = "realestate/dashboard.html"



# Users and Groups in the same look.
from . import admin_auth  # noqa: E402,F401


# Demo data page (superuser only).
from . import admin_demo  # noqa: E402,F401
