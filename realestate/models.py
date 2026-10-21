import hashlib
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.db.models import Case, CharField, Exists, OuterRef, Q, Sum, Value, When
from django.utils import timezone

from . import ledger_hooks
from .fields import EmiratesIDField, IBANField, PassportField
from .utils import (
    add_months,
    installment_count,
    lease_term_months,
    percent_of,
    prorate,
    split_amount,
    split_vat,
)
from .validators import (
    validate_document_file,
    validate_makani,
    validate_phone,
    validate_trn,
)

# A few models below have a field called `property`, which hides Python's own
# `property` inside those classes. They use `_property` for the decorator instead.
_property = property

# Payments are cancelled, never deleted. The demo-data remover is the one
# exception, and it says so with this switch.
_deletion_allowed = [False]


@contextmanager
def allow_payment_deletion():
    _deletion_allowed[0] = True
    try:
        yield
    finally:
        _deletion_allowed[0] = False


# Documents (Emirates ID, passport...) that expire within this many days
# are flagged "Expiring soon".
DOCUMENT_WARNING_DAYS = 60


# ---------------------------------------------------------------------------
# Shared: identity / KYC for owners and tenants
# ---------------------------------------------------------------------------


class KYCStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected / incomplete"


class Party(models.Model):
    """Identity and KYC fields shared by owners and tenants."""

    emirates_id = EmiratesIDField(
        "Emirates ID number",
        blank=True,
        help_text="15 digits, e.g. 784-1990-1234567-1 (dashes are added for you).",
    )
    emirates_id_expiry = models.DateField("Emirates ID expiry", null=True, blank=True)
    passport_number = PassportField(blank=True)
    passport_expiry = models.DateField("passport expiry", null=True, blank=True)
    nationality = models.CharField(max_length=100, blank=True)
    trade_licence_number = models.CharField(
        "trade licence number",
        max_length=100,
        blank=True,
        help_text="Companies only.",
    )
    trade_licence_expiry = models.DateField(
        "trade licence expiry", null=True, blank=True
    )
    trn = models.CharField(
        "VAT TRN",
        max_length=15,
        blank=True,
        validators=[validate_trn],
        help_text="15-digit Tax Registration Number. Companies only.",
    )
    kyc_status = models.CharField(
        "KYC status",
        max_length=20,
        choices=KYCStatus.choices,
        default=KYCStatus.PENDING,
    )
    kyc_verified_on = models.DateField("KYC verified on", null=True, blank=True)

    class Meta:
        abstract = True

    # Subclasses say whether this record is a company.
    @property
    def is_company(self):
        return False

    def extra_expiry_items(self):
        """Extra (label, expiry date) pairs, e.g. a tenant's residence visa."""
        return []

    def _expiry_items(self):
        items = []
        if self.emirates_id:
            items.append(("Emirates ID", self.emirates_id_expiry))
        if self.passport_number:
            items.append(("Passport", self.passport_expiry))
        if self.trade_licence_number:
            items.append(("Trade licence", self.trade_licence_expiry))
        return items + self.extra_expiry_items()

    def has_identification(self):
        if self.is_company:
            return bool(self.trade_licence_number)
        return bool(self.emirates_id or self.passport_number)

    def compliance_issues(self, today=None):
        """List of (severity, message). Severity: missing, expired or expiring."""
        today = today or timezone.localdate()
        issues = []
        if not self.has_identification():
            issues.append(("missing", "No identification recorded."))
        for label, expiry in self._expiry_items():
            if expiry is None:
                issues.append(("missing", f"{label}: expiry date is missing."))
                continue
            days = (expiry - today).days
            if days < 0:
                issues.append(("expired", f"{label} expired {-days} day(s) ago."))
            elif days <= DOCUMENT_WARNING_DAYS:
                issues.append(("expiring", f"{label} expires in {days} day(s)."))
        return issues

    @property
    def compliance_state(self):
        """One word for the worst problem: expired, missing, expiring or ok."""
        severities = {severity for severity, _ in self.compliance_issues()}
        for state in ("expired", "missing", "expiring"):
            if state in severities:
                return state
        return "ok"

    def clean(self):
        # A user who may not see ID details edits the record without them.
        if getattr(self, "_skip_identity_checks", False):
            return
        errors = {}
        if not self.has_identification():
            if self.is_company:
                errors["trade_licence_number"] = "Enter the trade licence number for a company."
            else:
                errors["emirates_id"] = "Enter an Emirates ID or a passport number."
        if self.emirates_id and not self.emirates_id_expiry:
            errors["emirates_id_expiry"] = "Enter the expiry date shown on the Emirates ID."
        if self.passport_number and not self.passport_expiry:
            errors["passport_expiry"] = "Enter the expiry date shown on the passport."
        if self.trade_licence_number and not self.trade_licence_expiry:
            errors["trade_licence_expiry"] = "Enter the expiry date shown on the trade licence."
        if self.kyc_status == KYCStatus.VERIFIED:
            problems = [
                message
                for severity, message in self.compliance_issues()
                if severity in ("expired", "missing")
            ]
            if problems:
                errors["kyc_status"] = "Cannot mark as verified: " + " ".join(problems)
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.kyc_status == KYCStatus.VERIFIED:
            if not self.kyc_verified_on:
                self.kyc_verified_on = timezone.localdate()
        else:
            self.kyc_verified_on = None
        super().save(*args, **kwargs)


def document_upload_path(instance, filename):
    """Where uploaded files go: media/documents/<kind>/<year>/<month>/<file>."""
    return f"documents/{instance._meta.model_name}/{timezone.now():%Y/%m}/{filename}"


class DocumentBase(models.Model):
    """A scanned document (PDF, JPG or PNG) attached to a record."""

    file = models.FileField(
        upload_to=document_upload_path,
        validators=[validate_document_file],
        help_text="PDF, JPG or PNG, up to 10 MB.",
    )
    notes = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-uploaded_at"]


# ---------------------------------------------------------------------------
# Owners
# ---------------------------------------------------------------------------


class Owner(Party):
    """A landlord whose properties we manage for a fee."""

    class OwnerType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"

    owner_type = models.CharField(
        max_length=20,
        choices=OwnerType.choices,
        default=OwnerType.INDIVIDUAL,
    )
    name = models.CharField("full name / company name", max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True, validators=[validate_phone])
    whatsapp = models.CharField(
        "WhatsApp number", max_length=50, blank=True, validators=[validate_phone]
    )
    address = models.TextField(blank=True)
    bank_name = models.CharField(max_length=255, blank=True)
    account_holder_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Name on the bank account, if different from the owner.",
    )
    iban = IBANField("IBAN", blank=True)
    management_fee_percent = models.DecimalField(
        "management fee (%)",
        max_digits=5,
        decimal_places=2,
        default=10,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Default percentage of collected rent kept as our fee.",
    )
    notes = models.TextField("internal notes", blank=True)
    is_active = models.BooleanField(
        "active owner",
        default=True,
        help_text="Turn off for owners we no longer manage.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        permissions = [
            (
                "view_owner_private",
                "Can see owners' bank, ID and KYC details (and their scanned documents)",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_company(self):
        return self.owner_type == self.OwnerType.COMPANY


class OwnerDocument(DocumentBase):
    class DocType(models.TextChoices):
        EMIRATES_ID = "emirates_id", "Emirates ID (front and back)"
        PASSPORT = "passport", "Passport"
        TRADE_LICENCE = "trade_licence", "Trade licence"
        POA = "poa", "Power of attorney"
        MANAGEMENT_AGREEMENT = "management_agreement", "Management agreement"
        OTHER = "other", "Other"

    owner = models.ForeignKey(Owner, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(
        "document type", max_length=30, choices=DocType.choices, default=DocType.EMIRATES_ID
    )

    class Meta(DocumentBase.Meta):
        abstract = False

    def __str__(self):
        return f"{self.get_doc_type_display()} - {self.owner}"


# ---------------------------------------------------------------------------
# Properties and units
# ---------------------------------------------------------------------------


class Property(models.Model):
    """A building, villa, etc. Either company-owned or owned by a third party."""

    class Ownership(models.TextChoices):
        COMPANY = "company", "Company-owned"
        THIRD_PARTY = "third_party", "Third-party"

    class PropertyType(models.TextChoices):
        BUILDING = "building", "Building / Tower"
        VILLA = "villa", "Villa"
        COMMERCIAL = "commercial", "Commercial center"
        MIXED_USE = "mixed_use", "Mixed-use"
        LAND = "land", "Land"

    class Emirate(models.TextChoices):
        ABU_DHABI = "abu_dhabi", "Abu Dhabi"
        DUBAI = "dubai", "Dubai"
        SHARJAH = "sharjah", "Sharjah"
        AJMAN = "ajman", "Ajman"
        UMM_AL_QUWAIN = "umm_al_quwain", "Umm Al Quwain"
        RAS_AL_KHAIMAH = "ras_al_khaimah", "Ras Al Khaimah"
        FUJAIRAH = "fujairah", "Fujairah"

    name = models.CharField("property name", max_length=255)
    property_type = models.CharField(
        max_length=20,
        choices=PropertyType.choices,
        default=PropertyType.BUILDING,
    )
    emirate = models.CharField(max_length=20, choices=Emirate.choices, blank=True)
    city = models.CharField(max_length=255, blank=True)
    community = models.CharField(
        "community / area",
        max_length=255,
        blank=True,
        help_text="e.g. Dubai Marina, Jumeirah Village Circle, Al Nahda.",
    )
    address = models.CharField(max_length=255, blank=True)

    title_deed_number = models.CharField(max_length=100, blank=True)
    plot_number = models.CharField(max_length=100, blank=True)
    makani_number = models.CharField(
        max_length=20, blank=True, validators=[validate_makani]
    )
    municipality_number = models.CharField(
        "municipality / land department number", max_length=100, blank=True
    )
    dewa_premise_number = models.CharField(
        "DEWA / utility premise number", max_length=100, blank=True
    )
    year_built = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1900), MaxValueValidator(2100)],
    )

    ownership_type = models.CharField(
        "who owns it?",
        max_length=20,
        choices=Ownership.choices,
        default=Ownership.COMPANY,
    )
    owner = models.ForeignKey(
        Owner,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="properties",
        help_text="Only for third-party properties.",
    )
    management_fee_percent = models.DecimalField(
        "fee for this property (%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Leave empty to use the owner's default fee.",
    )
    maintenance_markup_percent = models.DecimalField(
        "markup on repairs (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Our markup on repair costs charged to the owner (only if the management "
        "contract allows it). For example 10.",
    )
    management_start = models.DateField(
        "management agreement start", null=True, blank=True
    )
    management_end = models.DateField(
        "management agreement end", null=True, blank=True
    )

    notes = models.TextField("internal notes", blank=True)
    is_active = models.BooleanField("active property", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "properties"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remembered so save() can notice a change of owner.
        self._loaded_owner_id = self.owner_id

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        if self.ownership_type == self.Ownership.THIRD_PARTY and not self.owner_id:
            errors["owner"] = "Choose the owner for a third-party property."
        if (
            self.management_start
            and self.management_end
            and self.management_end <= self.management_start
        ):
            errors["management_end"] = "The agreement end must be after its start."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Company-owned properties never have an owner, a fee or an agreement.
        if self.ownership_type == self.Ownership.COMPANY:
            self.owner = None
            self.management_fee_percent = None
            self.management_start = None
            self.management_end = None
        previous_owner_id = self._loaded_owner_id if self.pk else None
        super().save(*args, **kwargs)
        # A change of owner is written to the ownership history, so old rent
        # and expenses still go to the owner who had the property at the time.
        if previous_owner_id and previous_owner_id != self.owner_id:
            PropertyOwnership.objects.create(
                property=self,
                owner_id=previous_owner_id,
                to_date=timezone.localdate(),
            )
        self._loaded_owner_id = self.owner_id

    def owner_windows(self, owner_id):
        """Date ranges (start, end) in which `owner_id` owned this property.

        `None` means open-ended. Used by owner statements so money is paid to
        the owner who really owned the property on the day it was received.
        """
        past = list(self.past_owners.all())
        windows = [(p.from_date, p.to_date) for p in past if p.owner_id == owner_id]
        if self.owner_id == owner_id and self.ownership_type == self.Ownership.THIRD_PARTY:
            last_end = max((p.to_date for p in past), default=None)
            start = last_end + timedelta(days=1) if last_end else None
            windows.append((start, None))
        return windows

    def owned_by_on(self, owner_id, day):
        """True if `owner_id` owned this property on date `day`."""
        for start, end in self.owner_windows(owner_id):
            if (start is None or day >= start) and (end is None or day <= end):
                return True
        return False

    def fee_applies_on(self, day):
        """False once the management agreement has ended (or before it began)."""
        if self.management_start and day < self.management_start:
            return False
        if self.management_end and day > self.management_end:
            return False
        return True

    @property
    def effective_fee_percent(self):
        """The fee that really applies: this property's own fee, else the owner's default."""
        if self.ownership_type != self.Ownership.THIRD_PARTY:
            return None
        if self.management_fee_percent is not None:
            return self.management_fee_percent
        if self.owner_id:
            return self.owner.management_fee_percent
        return None


class PropertyOwnership(models.Model):
    """A previous owner of a property, and the last day it was theirs.

    Written automatically when the owner of a property is changed. You can
    correct the dates on the property's page.
    """

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="past_owners"
    )
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="+")
    from_date = models.DateField(
        "owned from", null=True, blank=True, help_text="Leave empty if not known."
    )
    to_date = models.DateField(
        "owned until", help_text="The last day this owner owned the property."
    )

    class Meta:
        ordering = ["to_date", "id"]
        verbose_name = "previous owner"
        verbose_name_plural = "previous owners"

    def __str__(self):
        return f"{self.owner} until {self.to_date}"

    def clean(self):
        if self.from_date and self.to_date and self.to_date < self.from_date:
            raise ValidationError({"to_date": "The end date cannot be before the start date."})


class PropertyDocument(DocumentBase):
    class DocType(models.TextChoices):
        TITLE_DEED = "title_deed", "Title deed"
        MANAGEMENT_AGREEMENT = "management_agreement", "Management agreement"
        NOC = "noc", "NOC / permit"
        FLOOR_PLAN = "floor_plan", "Floor plan"
        INSURANCE = "insurance", "Insurance"
        OTHER = "other", "Other"

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="documents"
    )
    doc_type = models.CharField(
        "document type", max_length=30, choices=DocType.choices, default=DocType.TITLE_DEED
    )

    class Meta(DocumentBase.Meta):
        abstract = False

    def __str__(self):
        return f"{self.get_doc_type_display()} - {self.property}"


class UnitQuerySet(models.QuerySet):
    def with_status(self):
        """Add a `status_calc` value to every unit, worked out from its leases.

        Rules, in order:
          1. Has a lease that has started and not ended      -> occupied
          2. Has an "active" lease whose end date has passed -> holdover
             (the lease was never closed: renew it, or mark it expired)
          3. Marked "under maintenance"                      -> maintenance
          4. Has an active lease that starts in the future   -> reserved
          5. Otherwise                                       -> vacant
        """
        today = timezone.localdate()
        leases = Lease.objects.filter(unit=OuterRef("pk"))
        active_leases = leases.filter(status=Lease.Status.ACTIVE)
        return self.annotate(
            has_current_lease=Exists(
                leases.filter(start_date__lte=today).filter(Lease.running_filter(today))
            ),
            has_overdue_lease=Exists(active_leases.filter(end_date__lt=today)),
            has_upcoming_lease=Exists(active_leases.filter(start_date__gt=today)),
        ).annotate(
            status_calc=Case(
                When(has_current_lease=True, then=Value(Unit.Status.OCCUPIED.value)),
                When(has_overdue_lease=True, then=Value(Unit.Status.HOLDOVER.value)),
                When(under_maintenance=True, then=Value(Unit.Status.MAINTENANCE.value)),
                When(has_upcoming_lease=True, then=Value(Unit.Status.RESERVED.value)),
                default=Value(Unit.Status.VACANT.value),
                output_field=CharField(),
            )
        )


class Unit(models.Model):
    """One rentable unit (apartment, shop, office...) inside a property."""

    class UnitType(models.TextChoices):
        APARTMENT = "apartment", "Apartment"
        STUDIO = "studio", "Studio"
        VILLA = "villa", "Villa"
        OFFICE = "office", "Office"
        SHOP = "shop", "Shop"
        WAREHOUSE = "warehouse", "Warehouse"
        PARKING = "parking", "Parking"

    class Status(models.TextChoices):
        VACANT = "vacant", "Vacant"
        RESERVED = "reserved", "Reserved (lease not started)"
        OCCUPIED = "occupied", "Occupied"
        HOLDOVER = "holdover", "Lease ended - not closed"
        MAINTENANCE = "maintenance", "Under maintenance"

    class Furnishing(models.TextChoices):
        UNFURNISHED = "unfurnished", "Unfurnished"
        SEMI = "semi_furnished", "Semi-furnished"
        FURNISHED = "furnished", "Furnished"

    class ACType(models.TextChoices):
        CENTRAL = "central", "Central A/C"
        SPLIT = "split", "Split units"
        WINDOW = "window", "Window units"
        DISTRICT = "district", "District cooling (chiller)"

    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="units",
    )
    unit_number = models.CharField(max_length=50)
    unit_type = models.CharField(
        max_length=20,
        choices=UnitType.choices,
        default=UnitType.APARTMENT,
    )
    floor = models.PositiveSmallIntegerField(null=True, blank=True)
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    size_sqft = models.DecimalField(
        "size (sq ft)", max_digits=10, decimal_places=2, null=True, blank=True
    )
    dewa_number = models.CharField(
        "DEWA / utility account number", max_length=100, blank=True
    )
    furnishing = models.CharField(
        max_length=20, choices=Furnishing.choices, default=Furnishing.UNFURNISHED
    )
    ac_type = models.CharField(
        "A/C type", max_length=20, choices=ACType.choices, blank=True
    )
    parking_slots = models.PositiveSmallIntegerField(default=0)
    rent_amount = models.DecimalField(
        "default annual rent (AED)",
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    under_maintenance = models.BooleanField(
        "under maintenance",
        default=False,
        help_text="Tick while the unit is being repaired and cannot be rented out.",
    )
    notes = models.TextField("internal notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UnitQuerySet.as_manager()

    class Meta:
        ordering = ["property__name", "unit_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "unit_number"],
                name="unique_unit_number_per_property",
                violation_error_message="This property already has a unit with that number.",
            )
        ]

    def __str__(self):
        return f"{self.property.name} - Unit {self.unit_number}"

    def get_status(self):
        """Work out this unit's status from its leases.

        The status is never stored, so it can never contradict the leases.
        Same rules as UnitQuerySet.with_status().
        """
        if not self.pk:
            if self.under_maintenance:
                return self.Status.MAINTENANCE
            return self.Status.VACANT

        today = timezone.localdate()
        active_leases = self.leases.filter(status=Lease.Status.ACTIVE)
        running = self.leases.filter(start_date__lte=today).filter(
            Lease.running_filter(today)
        )
        if running.exists():
            return self.Status.OCCUPIED
        if active_leases.filter(end_date__lt=today).exists():
            return self.Status.HOLDOVER
        if self.under_maintenance:
            return self.Status.MAINTENANCE
        if active_leases.filter(start_date__gt=today).exists():
            return self.Status.RESERVED
        return self.Status.VACANT


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------


class Tenant(Party):
    """A person or company renting a unit from us."""

    class TenantType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"

    tenant_type = models.CharField(
        max_length=20,
        choices=TenantType.choices,
        default=TenantType.INDIVIDUAL,
    )
    name = models.CharField("full name / company name", max_length=255)
    date_of_birth = models.DateField(null=True, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True, validators=[validate_phone])
    whatsapp = models.CharField(
        "WhatsApp number", max_length=50, blank=True, validators=[validate_phone]
    )
    residence_visa_expiry = models.DateField(
        "residence visa expiry", null=True, blank=True
    )
    employer = models.CharField(
        "employer / sponsor", max_length=255, blank=True
    )
    authorised_signatory = models.CharField(
        max_length=255,
        blank=True,
        help_text="Person who signs for the company. Companies only.",
    )
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(
        max_length=50, blank=True, validators=[validate_phone]
    )
    address = models.TextField(blank=True)
    notes = models.TextField("internal notes", blank=True)
    is_active = models.BooleanField("active tenant", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_company(self):
        return self.tenant_type == self.TenantType.COMPANY

    def extra_expiry_items(self):
        if self.residence_visa_expiry:
            return [("Residence visa", self.residence_visa_expiry)]
        return []


class TenantDocument(DocumentBase):
    class DocType(models.TextChoices):
        EMIRATES_ID = "emirates_id", "Emirates ID (front and back)"
        PASSPORT = "passport", "Passport"
        VISA = "visa", "Residence visa"
        TRADE_LICENCE = "trade_licence", "Trade licence"
        SALARY_CERTIFICATE = "salary_certificate", "Salary certificate / proof of income"
        OTHER = "other", "Other"

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="documents"
    )
    doc_type = models.CharField(
        "document type", max_length=30, choices=DocType.choices, default=DocType.EMIRATES_ID
    )

    class Meta(DocumentBase.Meta):
        abstract = False

    def __str__(self):
        return f"{self.get_doc_type_display()} - {self.tenant}"


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------


class PaymentMethod(models.TextChoices):
    CHEQUE = "cheque", "Cheque"
    CASH = "cash", "Cash"
    CARD = "card", "Card"
    TRANSFER = "transfer", "Bank transfer"


class Lease(models.Model):
    """A rental contract: one tenant renting one unit for a period."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        TERMINATED = "terminated", "Terminated early"

    class Usage(models.TextChoices):
        RESIDENTIAL = "residential", "Residential"
        COMMERCIAL = "commercial", "Commercial"
        INDUSTRIAL = "industrial", "Industrial"

    PAYMENT_CHOICES = [
        (1, "1 payment per year (full year)"),
        (2, "2 payments per year"),
        (3, "3 payments per year"),
        (4, "4 payments per year (quarterly)"),
        (6, "6 payments per year"),
        (12, "12 payments per year (monthly)"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name="leases",
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name="leases",
    )
    contract_number = models.CharField(
        "our contract number", max_length=100, blank=True
    )
    ejari_number = models.CharField(
        "Ejari / Tawtheeq number", max_length=100, blank=True
    )
    usage = models.CharField(
        max_length=20, choices=Usage.choices, default=Usage.RESIDENTIAL
    )
    renewal_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="renewals",
        help_text="The earlier lease this one renews.",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    annual_rent = models.DecimalField(
        "annual rent (AED)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    vat_percent = models.DecimalField(
        "VAT on rent (%)",
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Residential rent has no VAT (leave 0). Commercial rent normally has 5% VAT.",
    )
    vat_inclusive = models.BooleanField(
        "VAT is already inside the rent",
        default=False,
        help_text="Tick if the annual rent already contains the VAT (the tenant pays exactly "
        "the rent). Leave unticked if the VAT is added on top of the rent.",
    )
    security_deposit = models.DecimalField(
        "security deposit (AED)",
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    agency_commission = models.DecimalField(
        "agency commission (AED)",
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    registration_fee = models.DecimalField(
        "Ejari / registration fee (AED)",
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    number_of_payments = models.PositiveSmallIntegerField(
        "rent payments per year",
        choices=PAYMENT_CHOICES,
        default=1,
        help_text="How many cheques or installments the annual rent is split into.",
    )
    payment_method = models.CharField(
        "usual way of paying",
        max_length=10,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CHEQUE,
        help_text="Used for the payment schedule. Each payment can still be changed.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text="Use the buttons on the lease page to end a lease (Mark as expired, "
        "Terminate early). Do not change this by hand.",
    )

    # --- early termination (UAE: exit terms come from the contract) --------
    notice_given_on = models.DateField(
        "written notice given on", null=True, blank=True, editable=False
    )
    notice_days = models.PositiveSmallIntegerField(
        "notice period (days)", null=True, blank=True, editable=False
    )
    exit_date = models.DateField("exit date", null=True, blank=True, editable=False)
    by_mutual_agreement = models.BooleanField(default=False, editable=False)
    termination_reason = models.CharField(max_length=255, blank=True, editable=False)
    termination_penalty = models.DecimalField(
        "early-exit penalty (AED)",
        max_digits=12,
        decimal_places=2,
        default=0,
        editable=False,
    )
    terminated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )

    # --- security deposit settlement ---------------------------------------
    deposit_carried_over = models.BooleanField(
        "deposit carried over from the previous lease",
        default=False,
        help_text="Tick for a renewal where the tenant's existing deposit stays "
        "with us. No new deposit is expected and none is counted twice.",
    )
    handover_date = models.DateField(
        "property handed back on",
        null=True,
        blank=True,
        help_text="The day the tenant returned the keys / the unit was inspected.",
    )
    deposit_deductions = models.DecimalField(
        "deposit deductions (AED)",
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Only for damage beyond normal wear and tear, or unpaid utilities. "
        "The early-exit penalty can NOT be taken from the deposit.",
    )
    deposit_deduction_notes = models.CharField(
        "what the deductions are for", max_length=255, blank=True
    )
    deposit_refunded_on = models.DateField(null=True, blank=True, editable=False)
    deposit_refund_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, editable=False
    )

    notes = models.TextField("internal notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.tenant.name} - {self.unit}"

    @staticmethod
    def running_filter(today, prefix=""):
        """Q for leases that are still running today: active and not past the end
        date, or terminated with an exit date that has not arrived yet."""
        p = prefix
        return Q(**{f"{p}status": "active", f"{p}end_date__gte": today}) | Q(
            **{f"{p}status": "terminated", f"{p}exit_date__gte": today}
        )

    @property
    def effective_end_date(self):
        """The day the tenancy really ends: the exit date if terminated early."""
        if self.status == self.Status.TERMINATED and self.exit_date:
            return self.exit_date
        return self.end_date

    # --- money -------------------------------------------------------------

    @property
    def term_months(self):
        """Length of the lease in months (a 6, 12, 18 or 24 month lease all work)."""
        return lease_term_months(self.start_date, self.end_date)

    @property
    def vat_amount(self):
        """VAT for one year of rent."""
        rent = self.annual_rent or Decimal("0")
        percent = self.vat_percent or Decimal("0")
        if self.vat_inclusive:
            return split_vat(rent, percent)[1]
        return (rent * percent / 100).quantize(Decimal("0.01"))

    @property
    def contract_total(self):
        """Rent plus VAT for the whole lease period (the yearly rent, pro-rated)."""
        rent = self.annual_rent or Decimal("0")
        if self.vat_inclusive:
            return prorate(rent, self.term_months)
        return prorate(rent + self.vat_amount, self.term_months)

    @property
    def monthly_rent(self):
        rent = self.annual_rent or Decimal("0")
        return (rent / 12).quantize(Decimal("0.01"))

    def cheque_totals(self):
        """Totals of this lease's rent payments, by what has happened to them."""
        S = Cheque.Status
        live = [S.PENDING, S.RECEIVED, S.DEPOSITED, S.CLEARED]
        totals = self.cheques.filter(purpose=Cheque.Purpose.RENT).aggregate(
            scheduled=Sum("amount", filter=Q(status__in=live)),
            cleared=Sum("amount", filter=Q(status=S.CLEARED)),
            deposited=Sum("amount", filter=Q(status=S.DEPOSITED)),
            returned=Sum("amount", filter=Q(status=S.RETURNED)),
        )
        zero = Decimal("0")
        return {key: value or zero for key, value in totals.items()}

    def schedule_gap(self):
        """Contract total minus the rent payments recorded (positive = missing)."""
        return self.contract_total - self.cheque_totals()["scheduled"]

    # --- payment schedule --------------------------------------------------

    def _schedule_dates(self):
        """The payment dates for the whole lease, all inside the lease period."""
        count = installment_count(self.term_months, self.number_of_payments)
        step = 12 // self.number_of_payments
        last_day = self.end_date
        dates = []
        for index in range(count):
            day = add_months(self.start_date, step * index)
            dates.append(min(day, last_day))
        return dates

    def generate_cheque_schedule(self):
        """Create one empty "expected" rent payment for every installment.

        Does nothing (returns 0) if the lease already has rent payments.
        Returns how many were created.
        """
        has_rent = (
            self.cheques.filter(purpose=Cheque.Purpose.RENT)
            .exclude(status=Cheque.Status.CANCELLED)
            .exists()
        )
        if has_rent:
            return 0
        return self._create_installments(self._schedule_dates(), self.contract_total, 1)

    def _create_installments(self, dates, total, first_no):
        amounts = split_amount(total, len(dates))
        with transaction.atomic():
            for offset, (day, amount) in enumerate(zip(dates, amounts)):
                Cheque.objects.create(
                    lease=self,
                    purpose=Cheque.Purpose.RENT,
                    method=self.payment_method,
                    installment_no=first_no + offset,
                    amount=amount,
                    cheque_date=day,
                    status=Cheque.Status.PENDING,
                )
        return len(dates)

    def rebuild_schedule(self):
        """Redo the payments that have not been received yet.

        Use after changing the rent or the dates. Payments that are in hand,
        deposited or cleared are kept; the rest of the contract total is spread
        over the remaining dates. Returns (removed, created).
        """
        if self.status != self.Status.ACTIVE:
            raise ValidationError("Only an active lease can have its schedule rebuilt.")
        S = Cheque.Status
        rent = self.cheques.filter(purpose=Cheque.Purpose.RENT)
        kept = list(rent.filter(status__in=[S.RECEIVED, S.DEPOSITED, S.CLEARED, S.RETURNED]))
        # A waiting replacement for a bounced cheque is not touched either.
        kept += list(rent.filter(status=S.PENDING, replaces__isnull=False))
        kept_total = sum(
            (c.amount for c in kept if c.status != S.RETURNED), Decimal("0.00")
        )
        pending = rent.filter(status=S.PENDING, replaces__isnull=True)
        dates = self._schedule_dates()
        # Returned cheques are waiting for a replacement, so they hold their place.
        used = len([c for c in kept if c.status != S.RETURNED])
        replaced_places = len([c for c in kept if c.status == S.RETURNED])
        remaining_dates = dates[used + replaced_places :]
        remaining_total = self.contract_total - kept_total
        for c in kept:
            if c.status == S.RETURNED:
                remaining_total -= c.amount
        with transaction.atomic():
            removed = pending.count()
            pending.delete()
            created = 0
            if remaining_dates and remaining_total > 0:
                created = self._create_installments(
                    remaining_dates, remaining_total, used + replaced_places + 1
                )
        return removed, created

    # --- ending a lease ----------------------------------------------------

    def _cancel_cheque(self, cheque, user, note):
        cheque._move_to(Cheque.Status.CANCELLED, user, note)

    def _rent_period_end(self, cheque):
        """Last day of the rent period a payment pays for."""
        later = (
            self.cheques.filter(
                purpose=Cheque.Purpose.RENT, cheque_date__gt=cheque.cheque_date
            )
            .order_by("cheque_date")
            .first()
        )
        if later:
            return later.cheque_date - timedelta(days=1)
        return self.end_date

    def terminate(
        self,
        exit_date,
        notice_given_on=None,
        notice_days=None,
        penalty=Decimal("0.00"),
        reason="",
        by_mutual_agreement=False,
        user=None,
    ):
        """End the lease early.

        Dubai/UAE practice (confirm the details with your lawyer): a tenant has
        no automatic right to leave early. The contract's early-termination
        clause or a mutual agreement decides. Normally written notice of 60 to
        90 days is needed and rent is payable through the notice period. The
        penalty (often 1 to 2 months of rent) is separate from the security
        deposit, which cannot be used to pay it. Uncashed rent cheques for the
        period after the exit must go back to the tenant.

        Returns a dictionary describing what was done.
        """
        if self.status != self.Status.ACTIVE:
            raise ValidationError("Only an active lease can be terminated.")
        if not exit_date:
            raise ValidationError("Enter the exit date.")
        if exit_date < self.start_date:
            raise ValidationError("The exit date cannot be before the lease started.")
        if exit_date >= self.end_date:
            raise ValidationError(
                "The exit date is not before the lease end date, so this is not an "
                "early exit. Use 'Mark as expired' when the lease ends."
            )
        penalty = Decimal(penalty or 0)
        if penalty < 0:
            raise ValidationError("The penalty cannot be negative.")
        if not by_mutual_agreement:
            if not notice_given_on:
                raise ValidationError(
                    "Enter the date the written notice was given, or tick 'by mutual "
                    "agreement with the tenant'."
                )
            days = notice_days if notice_days is not None else 0
            earliest = notice_given_on + timedelta(days=days)
            if exit_date < earliest:
                raise ValidationError(
                    f"The notice period of {days} days runs until {earliest:%d %b %Y}, "
                    "and rent is payable until then. Choose a later exit date, or tick "
                    "'by mutual agreement with the tenant' if the landlord agreed to "
                    "an earlier exit."
                )

        S = Cheque.Status
        summary = {
            "cancelled": 0,
            "returned_to_tenant": 0,
            "prorated": 0,
            "needs_review": 0,
            "penalty_payment": False,
        }
        with transaction.atomic():
            # Work out the straddling payment first, while all the dates exist.
            rent = list(
                self.cheques.filter(purpose=Cheque.Purpose.RENT).order_by("cheque_date", "id")
            )
            straddling = None
            for cheque in rent:
                if cheque.status in (S.CANCELLED, S.REPLACED, S.RETURNED):
                    continue
                if cheque.cheque_date <= exit_date:
                    period_end = self._rent_period_end(cheque)
                    if exit_date < period_end:
                        straddling = (cheque, period_end)

            for cheque in rent:
                if cheque.cheque_date <= exit_date:
                    continue
                if cheque.status == S.PENDING:
                    self._cancel_cheque(
                        cheque, user, f"Lease ended early on {exit_date:%d %b %Y}: not due"
                    )
                    summary["cancelled"] += 1
                elif cheque.status == S.RECEIVED:
                    self._cancel_cheque(
                        cheque,
                        user,
                        f"Lease ended early on {exit_date:%d %b %Y}: return this "
                        "uncashed cheque to the tenant",
                    )
                    summary["returned_to_tenant"] += 1
                elif cheque.status in (S.DEPOSITED, S.CLEARED):
                    summary["needs_review"] += 1

            if straddling:
                cheque, period_end = straddling
                days_used = (exit_date - cheque.cheque_date).days + 1
                period_days = (period_end - cheque.cheque_date).days + 1
                if cheque.status == S.PENDING:
                    new_amount = (cheque.amount * days_used / period_days).quantize(
                        Decimal("0.01")
                    )
                    old_amount = cheque.amount
                    cheque.amount = new_amount
                    cheque.save()
                    ChequeEvent.objects.create(
                        cheque=cheque,
                        user=user,
                        from_status=cheque.status,
                        to_status=cheque.status,
                        note=f"Amount reduced from {old_amount} to {new_amount} "
                        f"(rent up to the exit date, {days_used} of {period_days} days)",
                    )
                    summary["prorated"] += 1
                else:
                    summary["needs_review"] += 1

            if penalty > 0:
                Cheque.objects.create(
                    lease=self,
                    purpose=Cheque.Purpose.PENALTY,
                    method=self.payment_method,
                    amount=penalty,
                    cheque_date=exit_date,
                    status=S.PENDING,
                    notes="Early-termination penalty",
                )
                summary["penalty_payment"] = True

            self.status = self.Status.TERMINATED
            self.exit_date = exit_date
            self.notice_given_on = notice_given_on
            self.notice_days = notice_days
            self.by_mutual_agreement = bool(by_mutual_agreement)
            self.termination_reason = (reason or "")[:255]
            self.termination_penalty = penalty
            self.terminated_by = user
            self.save()
        return summary

    def mark_expired(self, user=None):
        """Close a lease whose end date has passed.

        Payments dated after the end (there should be none) are cancelled.
        Returns how many payments are still unpaid for the tenant to settle.
        """
        if self.status != self.Status.ACTIVE:
            raise ValidationError("Only an active lease can be marked as expired.")
        if self.end_date >= timezone.localdate():
            raise ValidationError(
                f"This lease runs until {self.end_date:%d %b %Y}. Use 'Terminate early' "
                "if the tenant is leaving before then."
            )
        S = Cheque.Status
        with transaction.atomic():
            for cheque in self.cheques.filter(
                status=S.PENDING, cheque_date__gt=self.end_date
            ):
                self._cancel_cheque(cheque, user, "Lease expired: not due")
            self.status = self.Status.EXPIRED
            self.save()
        return self.unpaid_rent_count()

    def unpaid_rent_count(self):
        S = Cheque.Status
        return self.cheques.filter(
            purpose__in=[Cheque.Purpose.RENT, Cheque.Purpose.PENALTY],
            status__in=[S.PENDING, S.RECEIVED, S.DEPOSITED, S.RETURNED],
        ).count()

    # --- security deposit --------------------------------------------------

    def deposit_received(self):
        """Deposit actually in our hands: cheques in hand, deposited or cleared,
        plus a deposit carried over from the lease this one renews."""
        if self.deposit_carried_over and self.renewal_of_id:
            return self.renewal_of.deposit_received()
        S = Cheque.Status
        total = self.cheques.filter(
            purpose=Cheque.Purpose.SECURITY_DEPOSIT,
            status__in=[S.RECEIVED, S.DEPOSITED, S.CLEARED],
        ).aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0.00")

    def deposit_refundable(self):
        """What goes back to the tenant: the deposit less genuine deductions."""
        if self.deposit_refunded_on:
            return Decimal("0.00")
        return max(self.deposit_received() - (self.deposit_deductions or 0), Decimal("0.00"))

    def penalty_collected(self):
        total = self.cheques.filter(
            purpose=Cheque.Purpose.PENALTY, status=Cheque.Status.CLEARED
        ).aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0.00")

    def deposit_refund_problems(self):
        """Reasons the deposit cannot be refunded yet (empty list = it can)."""
        problems = []
        if self.deposit_refunded_on:
            problems.append("The deposit has already been refunded.")
            return problems
        if self.status == self.Status.ACTIVE:
            problems.append("The lease is still active.")
        if (
            self.status == self.Status.TERMINATED
            and self.exit_date
            and self.exit_date > timezone.localdate()
        ):
            problems.append("The exit date has not arrived yet.")
        if not self.handover_date:
            problems.append(
                "Enter the date the property was handed back (and check its condition)."
            )
        if (self.termination_penalty or 0) > 0 and self.penalty_collected() < self.termination_penalty:
            problems.append(
                "The early-exit penalty has not been paid yet. The deposit cannot be "
                "used for it, but it must be settled before the deposit goes back."
            )
        if self.renewals.filter(
            deposit_carried_over=True, status=self.Status.ACTIVE
        ).exists():
            problems.append("The deposit was carried over to the renewal lease.")
        if (self.deposit_deductions or 0) > self.deposit_received():
            problems.append("The deductions are more than the deposit.")
        if self.deposit_received() <= 0:
            problems.append("No deposit is held for this lease.")
        return problems

    def refund_deposit(self, on=None, user=None):
        """Record the refund. In-hand deposit cheques are returned to the tenant
        when nothing is deducted."""
        problems = self.deposit_refund_problems()
        if problems:
            raise ValidationError(problems)
        amount = self.deposit_refundable()
        with transaction.atomic():
            self.deposit_refunded_on = on or timezone.localdate()
            self.deposit_refund_amount = amount
            self.save()
            if not self.deposit_deductions:
                for cheque in self.cheques.filter(
                    purpose=Cheque.Purpose.SECURITY_DEPOSIT, status=Cheque.Status.RECEIVED
                ):
                    self._cancel_cheque(
                        cheque, user, "Deposit refunded: cheque returned to the tenant"
                    )
        return amount

    # --- checks ------------------------------------------------------------

    def clean(self):
        errors = {}
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            errors["end_date"] = "The end date must be after the start date."

        if (
            not errors
            and self.status == self.Status.ACTIVE
            and self.unit_id
            and self.start_date
            and self.end_date
        ):
            overlapping = Lease.objects.filter(unit_id=self.unit_id).exclude(pk=self.pk)
            overlapping = overlapping.filter(
                Q(status=self.Status.ACTIVE, start_date__lt=self.end_date, end_date__gt=self.start_date)
                | Q(
                    status=self.Status.TERMINATED,
                    start_date__lt=self.end_date,
                    exit_date__gte=self.start_date,
                )
            )
            if overlapping.exists():
                errors["unit"] = "This unit already has an active lease for those dates."

        if self.renewal_of_id and self.pk and self.renewal_of_id == self.pk:
            errors["renewal_of"] = "A lease cannot renew itself."

        percent = self.vat_percent or 0
        if self.usage == self.Usage.RESIDENTIAL and percent > 0:
            errors["vat_percent"] = (
                "Residential rent is exempt from VAT. Leave this at 0 (or change the "
                "usage to Commercial)."
            )
        elif self.usage in (self.Usage.COMMERCIAL, self.Usage.INDUSTRIAL) and percent <= 0:
            errors["vat_percent"] = (
                "Commercial and industrial rent normally carries 5% VAT. Enter the VAT "
                "rate (for example 5), or change the usage to Residential."
            )

        problem = self._ledger_change_problem()
        if problem:
            errors["__all__"] = problem

        if errors:
            raise ValidationError(errors)

    # --- the accounts: no silent changes after the invoice ---------------------

    LEDGER_LOCKED_FIELDS = (
        "tenant_id",
        "unit_id",
        "annual_rent",
        "start_date",
        "end_date",
        "vat_percent",
        "vat_inclusive",
    )

    def _ledger_change_problem(self):
        """Why this lease cannot be changed (it already has an invoice), or ''."""
        if not self.pk or not ledger_hooks.call("lease_invoiced", self.pk, default=False):
            return ""
        old = Lease.objects.filter(pk=self.pk).first()
        if old is None:
            return ""
        if any(getattr(old, name) != getattr(self, name) for name in self.LEDGER_LOCKED_FIELDS):
            return (
                "This lease already has an invoice in the accounts, so the tenant, unit, "
                "rent, VAT and dates cannot be changed. If the invoice was a mistake, ask "
                "the accountant to cancel it (Accounting > Lease invoices) and then "
                "correct the lease."
            )
        return ""

    def save(self, *args, **kwargs):
        problem = self._ledger_change_problem()
        if problem:
            raise ValidationError(problem)
        super().save(*args, **kwargs)


class LeaseDocument(DocumentBase):
    class DocType(models.TextChoices):
        CONTRACT = "contract", "Signed tenancy contract"
        EJARI = "ejari", "Ejari / Tawtheeq certificate"
        CHEQUE_COPIES = "cheque_copies", "Cheque copies"
        NOTICE = "notice", "Notice (renewal, termination...)"
        HANDOVER = "handover", "Handover / inspection report"
        OTHER = "other", "Other"

    lease = models.ForeignKey(
        Lease, on_delete=models.CASCADE, related_name="documents"
    )
    doc_type = models.CharField(
        "document type", max_length=30, choices=DocType.choices, default=DocType.CONTRACT
    )

    class Meta(DocumentBase.Meta):
        abstract = False

    def __str__(self):
        return f"{self.get_doc_type_display()} - {self.lease}"


# ---------------------------------------------------------------------------
# Cheques
# ---------------------------------------------------------------------------


class Cheque(models.Model):
    """One payment from a tenant: a cheque (most of them), cash, card or transfer.

    The model keeps its old name "Cheque". Cheques go through the full bank
    workflow (in hand, deposited, cleared or bounced). Cash, card and transfer
    payments are simply expected and then paid.
    """

    Method = PaymentMethod

    class Purpose(models.TextChoices):
        RENT = "rent", "Rent"
        SECURITY_DEPOSIT = "security_deposit", "Security deposit"
        COMMISSION = "commission", "Agency commission"
        REGISTRATION = "registration", "Ejari / registration fee"
        MAINTENANCE = "maintenance", "Maintenance"
        PENALTY = "termination_penalty", "Early-termination penalty"
        BOUNCE_FEE = "bounce_fee", "Bounced-cheque fee"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Expected (not received yet)"
        RECEIVED = "received", "In hand"
        DEPOSITED = "deposited", "Deposited at bank"
        CLEARED = "cleared", "Cleared / paid"
        RETURNED = "returned", "Returned (bounced)"
        REPLACED = "replaced", "Replaced"
        CANCELLED = "cancelled", "Cancelled"

    class ReturnReason(models.TextChoices):
        INSUFFICIENT_FUNDS = "insufficient_funds", "Insufficient funds"
        SIGNATURE = "signature", "Signature mismatch"
        ACCOUNT_CLOSED = "account_closed", "Account closed"
        STOP_PAYMENT = "stop_payment", "Payment stopped by the drawer"
        STALE = "stale", "Stale / expired cheque"
        DETAILS = "details", "Date, amount or name mismatch"
        OTHER = "other", "Other"

    # Things that may not change once the payment is on an approved statement.
    LOCKED_FIELDS = ("amount", "cheque_date", "lease_id", "purpose", "status", "method")

    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name="cheques")
    method = models.CharField(
        "paid by",
        max_length=10,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CHEQUE,
    )
    purpose = models.CharField(
        max_length=20, choices=Purpose.choices, default=Purpose.RENT
    )
    installment_no = models.PositiveSmallIntegerField(
        "installment no.", null=True, blank=True
    )
    cheque_number = models.CharField(max_length=30, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    reference = models.CharField(
        "reference",
        max_length=100,
        blank=True,
        help_text="Bank transfer reference, card approval code or cash receipt number.",
    )
    drawer_name = models.CharField(
        "name on the cheque",
        max_length=255,
        blank=True,
        help_text="Only if different from the tenant.",
    )
    amount = models.DecimalField(
        "amount (AED)", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    cheque_date = models.DateField(
        "due / cheque date",
        help_text="The date written on the cheque (the day it can be deposited), "
        "or the day a cash, card or transfer payment is due.",
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RECEIVED
    )
    received_date = models.DateField(null=True, blank=True)
    deposited_date = models.DateField(null=True, blank=True)
    cleared_date = models.DateField("cleared / paid date", null=True, blank=True)
    returned_date = models.DateField(null=True, blank=True)
    settled_on = models.DateField(
        "card money reached the bank on",
        null=True,
        blank=True,
        help_text="Card payments only: the day the card company paid the money into the bank.",
    )
    return_reason = models.CharField(
        max_length=30, choices=ReturnReason.choices, blank=True
    )
    replaces = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replacements",
        help_text="The bounced cheque this one replaces.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cheque_date", "id"]
        indexes = [models.Index(fields=["status", "cheque_date"])]
        verbose_name = "payment / cheque"
        verbose_name_plural = "payments & cheques"

    def __str__(self):
        if self.amount is None:
            return f"{self.get_method_display()}"
        if self.method == self.Method.CHEQUE:
            number = self.cheque_number or "no number yet"
            return f"Cheque {number} - AED {self.amount:,.2f}"
        return f"{self.get_method_display()} - AED {self.amount:,.2f}"

    @_property
    def tenant(self):
        return self.lease.tenant

    @_property
    def is_cheque(self):
        return self.method == self.Method.CHEQUE

    @_property
    def statement_status(self):
        """None if on no owner statement, else that statement's status."""
        line = getattr(self, "statement_line", None)
        return line.statement.status if line else None

    @_property
    def is_locked(self):
        """True once an approved or paid owner statement includes this payment."""
        status = self.statement_status
        return status is not None and status != OwnerStatement.Status.DRAFT

    @_property
    def attempt_number(self):
        """1 for the original cheque, 2 for its first replacement, and so on."""
        number, current, seen = 1, self, set()
        while current.replaces_id and current.replaces_id not in seen:
            seen.add(current.replaces_id)
            current = current.replaces
            number += 1
        return number

    def _stored_value(self, field):
        if not self.pk:
            return None
        return (
            Cheque.objects.filter(pk=self.pk).values_list(field, flat=True).first()
        )

    def _changed_from_saved(self):
        """True if a field that must not change on a locked payment was changed."""
        if not self.pk:
            return False
        old = Cheque.objects.filter(pk=self.pk).first()
        if old is None:
            return False
        return any(getattr(old, f) != getattr(self, f) for f in self.LOCKED_FIELDS)

    # Once a payment is in the accounts these may not change; the entries would no
    # longer match. (Its status and dates are fine: the accounts follow them.)
    LEDGER_LOCKED_FIELDS = ("amount", "lease_id", "purpose", "method")

    def _ledger_change_problem(self):
        """Why this payment cannot be changed (it is already in the accounts), or ''."""
        if not self.pk or not ledger_hooks.call("cheque_posted", self.pk, default=False):
            return ""
        old = Cheque.objects.filter(pk=self.pk).first()
        if old is None:
            return ""
        if any(getattr(old, name) != getattr(self, name) for name in self.LEDGER_LOCKED_FIELDS):
            return (
                "This payment is already in the accounts, so its amount, lease, purpose "
                "and way of paying cannot be changed. If it was entered wrongly, cancel "
                "it (only possible before the bank has it) and add a new one, or ask the "
                "accountant to reverse the entries."
            )
        return ""

    # --- checks and automatic dates ---------------------------------------

    def clean(self):
        errors = {}
        S = self.Status
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "The amount must be more than zero."

        number = (self.cheque_number or "").strip()
        bank = (self.bank_name or "").strip()
        reference = (self.reference or "").strip()

        if self.method == self.Method.CHEQUE:
            if self.status not in (S.PENDING, S.CANCELLED):
                if not number:
                    errors["cheque_number"] = "Enter the cheque number."
                if not bank:
                    errors["bank_name"] = "Enter the bank name."
            if number and bank:
                duplicate = Cheque.objects.filter(
                    method=self.Method.CHEQUE,
                    cheque_number__iexact=number,
                    bank_name__iexact=bank,
                ).exclude(pk=self.pk)
                if duplicate.exists():
                    errors["cheque_number"] = (
                        "A cheque with this number from this bank is already recorded."
                    )
        else:
            if self.status not in (S.PENDING, S.CLEARED, S.CANCELLED):
                errors["status"] = (
                    "Cash, card and transfer payments can only be Expected, "
                    "Cleared / paid or Cancelled."
                )
            elif (
                self.status == S.CLEARED
                and self.method in (self.Method.TRANSFER, self.Method.CARD)
                and not reference
            ):
                errors["reference"] = "Enter the transfer reference or card approval code."

        if self.status == S.RETURNED and not self.return_reason:
            errors["return_reason"] = "Choose why the cheque was returned."

        if self.lease_id and self.cheque_date and self.purpose == self.Purpose.RENT:
            moved = self._state.adding or self._stored_value("cheque_date") != self.cheque_date
            if moved:
                lease = self.lease
                if self.cheque_date > lease.effective_end_date:
                    errors["cheque_date"] = (
                        f"This date is after the lease ends on "
                        f"{lease.effective_end_date:%d %b %Y}."
                    )
                elif self.cheque_date < lease.start_date - timedelta(days=45):
                    errors["cheque_date"] = (
                        f"This date is more than 45 days before the lease starts on "
                        f"{lease.start_date:%d %b %Y}."
                    )

        if self.pk and self.is_locked and self._changed_from_saved():
            errors["__all__"] = (
                "This payment is on an approved owner statement and cannot be "
                "changed. Reopen the statement first."
            )
        elif self.pk:
            problem = self._ledger_change_problem()
            if problem:
                errors["__all__"] = problem
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        S = self.Status
        today = timezone.localdate()
        self.cheque_number = (self.cheque_number or "").strip()
        self.bank_name = (self.bank_name or "").strip()
        self.reference = (self.reference or "").strip()
        if self.method != self.Method.CHEQUE:
            # Only cheques have a number, a bank and a bank workflow.
            self.cheque_number = ""
            self.bank_name = ""

        if (
            self.pk
            and self.is_locked
            and not getattr(self, "_allow_locked_change", False)
            and self._changed_from_saved()
        ):
            raise ValidationError(
                "This payment is on an approved owner statement and cannot be changed."
            )
        problem = self._ledger_change_problem()
        if problem:
            raise ValidationError(problem)

        if self.status == S.RECEIVED and not self.received_date:
            self.received_date = today
        if self.status == S.DEPOSITED and not self.deposited_date:
            self.deposited_date = today
        if self.status == S.CLEARED:
            if not self.cleared_date:
                self.cleared_date = today
            if self.method == self.Method.CHEQUE and not self.deposited_date:
                self.deposited_date = self.cleared_date
            if self.method != self.Method.CHEQUE and not self.received_date:
                self.received_date = self.cleared_date
        if self.status == S.RETURNED:
            if not self.returned_date:
                self.returned_date = today
            if not self.deposited_date:
                self.deposited_date = self.returned_date
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._check_deletable()
        return super().delete(*args, **kwargs)

    def _check_deletable(self):
        if _deletion_allowed[0]:
            return
        if self.status != self.Status.PENDING or self.events.exclude(
            note="Created"
        ).exists() or getattr(self, "statement_line", None):
            raise ValidationError(
                "Only a payment that was never received can be deleted. Set it to "
                "Cancelled instead, so the history is kept."
            )

    # --- the workflow ------------------------------------------------------

    def _move_to(self, new_status, user=None, note=""):
        old_status = self.status
        self.status = new_status
        self.save()
        ChequeEvent.objects.create(
            cheque=self,
            user=user,
            from_status=old_status,
            to_status=new_status,
            note=note,
        )

    def _require_unlocked(self):
        if self.is_locked:
            raise ValidationError(
                "This payment is on an approved owner statement. Reopen the "
                "statement before changing it."
            )

    def mark_deposited(self, on=None, user=None):
        self._require_unlocked()
        if not self.is_cheque:
            raise ValidationError("Only cheques are deposited at the bank.")
        if self.status != self.Status.RECEIVED:
            raise ValidationError("Only a cheque that is in hand can be deposited.")
        self.deposited_date = on or timezone.localdate()
        self._move_to(self.Status.DEPOSITED, user)

    def mark_cleared(self, on=None, user=None, reference=""):
        """Cheques: in hand or deposited -> cleared. Others: expected -> paid."""
        self._require_unlocked()
        if self.is_cheque:
            allowed = (self.Status.RECEIVED, self.Status.DEPOSITED)
            message = "Only a cheque that is in hand or deposited can be marked as cleared."
        else:
            allowed = (self.Status.PENDING,)
            message = "Only an expected payment can be marked as paid."
            reference = (reference or self.reference or "").strip()
            if self.method in (self.Method.TRANSFER, self.Method.CARD) and not reference:
                raise ValidationError("Enter the transfer reference or card approval code.")
            self.reference = reference
        if self.status not in allowed:
            raise ValidationError(message)
        self.cleared_date = on or timezone.localdate()
        self._move_to(self.Status.CLEARED, user)

    def mark_returned(self, reason, on=None, user=None, note="", fee=None):
        """Bounce a deposited cheque (or one the bank takes back after it cleared).

        Adds the bounced-cheque fee if one is set. If the cheque had already been
        paid out to the owner on a PAID statement, the owner's share is charged to
        them as an expense on their next statement."""
        S = self.Status
        if not self.is_cheque or self.status not in (S.DEPOSITED, S.CLEARED):
            raise ValidationError(
                "Only a deposited or cleared cheque can be marked as returned."
            )
        statement = self.statement_line.statement if getattr(self, "statement_line", None) else None
        paid_out = statement is not None and statement.status == OwnerStatement.Status.PAID
        if not paid_out:
            self._require_unlocked()
        if reason not in self.ReturnReason.values:
            raise ValidationError("Choose why the cheque was returned.")
        if fee is None:
            fee = CompanySettings.load().bounce_fee
        returned_on = on or timezone.localdate()
        with transaction.atomic():
            share = statement.owner_share_of(self) if paid_out else None
            self.return_reason = reason
            self.returned_date = returned_on
            if paid_out:
                self._allow_locked_change = True
            self._move_to(S.RETURNED, user, note)
            if paid_out and share and share > 0:
                Expense.objects.create(
                    property=self.lease.unit.property,
                    unit=self.lease.unit,
                    category=ExpenseCategory.OTHER,
                    description=(
                        f"Cheque {self.cheque_number or self.pk} bounced after the owner was "
                        f"paid ({statement.number})"
                    ),
                    expense_date=returned_on,
                    amount=share,
                    bearer=Expense.Bearer.OWNER,
                    is_clawback=True,
                    notes="Created automatically: the owner already received this money.",
                )
            if fee and fee > 0:
                Cheque.objects.create(
                    lease=self.lease,
                    purpose=self.Purpose.BOUNCE_FEE,
                    method=self.Method.CASH,
                    amount=fee,
                    cheque_date=returned_on,
                    status=S.PENDING,
                    notes=f"Fee for bounced cheque {self.cheque_number}.",
                )

    def create_replacement(self, user=None):
        """Mark this bounced cheque as replaced and add an empty cheque to collect."""
        self._require_unlocked()
        if self.status != self.Status.RETURNED:
            raise ValidationError("Only a returned cheque can be replaced.")
        with transaction.atomic():
            replacement = Cheque.objects.create(
                lease=self.lease,
                method=self.method,
                purpose=self.purpose,
                installment_no=self.installment_no,
                drawer_name=self.drawer_name,
                amount=self.amount,
                cheque_date=timezone.localdate(),
                status=self.Status.PENDING,
                replaces=self,
                notes=f"Replacement for bounced cheque {self.cheque_number} "
                f"(attempt {self.attempt_number + 1}).",
            )
            ChequeEvent.objects.create(
                cheque=replacement,
                user=user,
                from_status="",
                to_status=self.Status.PENDING,
                note="Created as a replacement",
            )
            self._move_to(self.Status.REPLACED, user, "Replacement cheque created")
        return replacement

    def cancel(self, user=None, note="Cancelled"):
        """Cancel a payment that will never be collected (keeps the history)."""
        self._require_unlocked()
        if self.status in (self.Status.CLEARED, self.Status.DEPOSITED, self.Status.CANCELLED):
            raise ValidationError(
                "A payment that is deposited or already paid cannot be cancelled."
            )
        if self.status in (self.Status.RETURNED, self.Status.REPLACED):
            raise ValidationError(
                "A bounced cheque cannot be cancelled: the tenant still owes the money. "
                "Create a replacement and collect it. If it will never be paid, ask the "
                "accountant to write it off."
            )
        self._move_to(self.Status.CANCELLED, user, note)


@receiver(pre_delete, sender=Cheque)
def _protect_cheque_history(sender, instance, **kwargs):
    """Stops bulk deletes too: a payment is cancelled, not deleted."""
    instance._check_deletable()


class ChequeEvent(models.Model):
    """History of what happened to a cheque, and who did it."""

    cheque = models.ForeignKey(Cheque, on_delete=models.CASCADE, related_name="events")
    at = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["at", "id"]

    def __str__(self):
        return f"{self.cheque_id}: {self.from_status or 'new'} -> {self.to_status}"


# ---------------------------------------------------------------------------
# Expenses and maintenance
# ---------------------------------------------------------------------------


class ExpenseCategory(models.TextChoices):
    REPAIR = "repair", "General repair / maintenance"
    PLUMBING = "plumbing", "Plumbing"
    ELECTRICAL = "electrical", "Electrical"
    AC = "ac", "AC / chiller"
    CLEANING = "cleaning", "Cleaning"
    PEST = "pest", "Pest control"
    PAINTING = "painting", "Painting / fit-out"
    SECURITY = "security", "Security"
    UTILITIES = "utilities", "Utilities (DEWA, chilled water, gas...)"
    INSURANCE = "insurance", "Insurance"
    GOVERNMENT = "government", "Government / municipality fees"
    LEGAL = "legal", "Legal / eviction / RERA case"
    MARKETING = "marketing", "Marketing / listing"
    OTHER = "other", "Other"


class Vendor(models.Model):
    """A contractor or supplier: plumber, AC company, cleaning company..."""

    name = models.CharField(max_length=255)
    trade = models.CharField(
        "main service",
        max_length=20,
        choices=ExpenseCategory.choices,
        default=ExpenseCategory.REPAIR,
    )
    contact_person = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True, validators=[validate_phone])
    email = models.EmailField(blank=True)
    trade_licence_number = models.CharField(max_length=100, blank=True)
    trn = models.CharField(
        "VAT TRN", max_length=15, blank=True, validators=[validate_trn]
    )
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class MaintenanceRequest(models.Model):
    """A repair or job reported for a property or a unit."""

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        URGENT = "urgent", "Urgent"
        EMERGENCY = "emergency", "Emergency (leak, no power...)"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    property = models.ForeignKey(
        "Property", on_delete=models.PROTECT, related_name="maintenance_requests"
    )
    unit = models.ForeignKey(
        "Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="maintenance_requests",
        help_text="Leave empty for common areas or the whole building.",
    )
    tenant = models.ForeignKey(
        "Tenant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintenance_requests",
        help_text="Who reported it, if a tenant did.",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=20, choices=ExpenseCategory.choices, default=ExpenseCategory.REPAIR
    )
    priority = models.CharField(
        max_length=12, choices=Priority.choices, default=Priority.NORMAL
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.OPEN
    )
    reported_on = models.DateField(default=timezone.localdate)
    scheduled_for = models.DateField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    vendor = models.ForeignKey(
        Vendor,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintenance_requests",
    )
    estimated_cost = models.DecimalField(
        "estimated cost (AED)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-reported_on", "-id"]

    def __str__(self):
        return f"{self.title} ({self.property})"

    @_property
    def is_open(self):
        return self.status in (self.Status.OPEN, self.Status.IN_PROGRESS)

    @_property
    def total_cost(self):
        total = self.expenses.aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0.00")

    def clean(self):
        errors = {}
        if self.unit_id and self.property_id and self.unit.property_id != self.property_id:
            errors["unit"] = "This unit does not belong to the selected property."
        if self.completed_on and self.reported_on and self.completed_on < self.reported_on:
            errors["completed_on"] = "Completion cannot be before the report date."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.status == self.Status.COMPLETED and not self.completed_on:
            self.completed_on = timezone.localdate()
        if self.status != self.Status.COMPLETED:
            self.completed_on = None
        super().save(*args, **kwargs)


class Expense(models.Model):
    """Money spent on a property. `bearer` says who finally pays for it."""

    Category = ExpenseCategory

    class Bearer(models.TextChoices):
        OWNER = "owner", "Owner (deducted from the owner statement)"
        COMPANY = "company", "Our company"
        TENANT = "tenant", "Tenant (charge back to the tenant)"

    property = models.ForeignKey(
        "Property", on_delete=models.PROTECT, related_name="expenses"
    )
    unit = models.ForeignKey(
        "Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="expenses",
        help_text="Leave empty for common areas or the whole building.",
    )
    maintenance_request = models.ForeignKey(
        MaintenanceRequest,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses",
    )
    vendor = models.ForeignKey(
        Vendor,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    category = models.CharField(
        max_length=20, choices=ExpenseCategory.choices, default=ExpenseCategory.REPAIR
    )
    description = models.CharField(max_length=255)
    expense_date = models.DateField(default=timezone.localdate)
    invoice_number = models.CharField(max_length=60, blank=True)
    amount = models.DecimalField(
        "total paid (AED)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="The full amount including VAT.",
    )
    vat_amount = models.DecimalField(
        "of which VAT (AED)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
        help_text="For your records. Leave 0 if there is no VAT.",
    )
    bearer = models.CharField(
        "who pays?", max_length=10, choices=Bearer.choices, default=Bearer.OWNER
    )
    tenant = models.ForeignKey(
        "Tenant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="charged_expenses",
        verbose_name="tenant to charge",
        help_text="Only when 'Tenant' pays. Who owes us this money?",
    )
    recovered_amount = models.DecimalField(
        "collected from the tenant (AED)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
        help_text="How much of this the tenant has paid back so far.",
    )
    recovered_on = models.DateField(
        "tenant paid back on", null=True, blank=True
    )
    vendor_paid_on = models.DateField(
        "vendor paid on",
        null=True,
        blank=True,
        help_text="The day we paid the vendor's bill. Until then it is owed to the vendor.",
    )
    receipt = models.FileField(
        upload_to=document_upload_path,
        blank=True,
        validators=[validate_document_file],
        help_text="Invoice or receipt: PDF, JPG or PNG, up to 10 MB.",
    )
    notes = models.TextField(blank=True)
    is_clawback = models.BooleanField(
        default=False,
        editable=False,
        help_text="Created automatically when a cheque bounces after the owner was paid. "
        "It only appears on the owner's statement; the accounts were already corrected.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        indexes = [models.Index(fields=["bearer", "expense_date"])]

    def __str__(self):
        return f"{self.description} - AED {self.amount:,.2f}" if self.amount is not None else self.description

    @_property
    def statement_status(self):
        """None if not on any owner statement, else the status of that statement."""
        line = getattr(self, "statement_line", None)
        return line.statement.status if line else None

    @_property
    def is_locked(self):
        """True once an approved or paid owner statement includes this expense."""
        status = self.statement_status
        return status is not None and status != OwnerStatement.Status.DRAFT

    @_property
    def markup_amount(self):
        """Our markup on a repair charged to the owner of a third-party property."""
        if self.bearer != self.Bearer.OWNER or self.is_clawback or self.amount is None:
            return Decimal("0.00")
        prop = self.property
        if prop.ownership_type != Property.Ownership.THIRD_PARTY:
            return Decimal("0.00")
        return percent_of(self.amount, prop.maintenance_markup_percent or Decimal("0"))

    @_property
    def owner_charge(self):
        """What the owner is charged: the repair plus our markup."""
        return (self.amount or Decimal("0.00")) + self.markup_amount

    @_property
    def still_to_recover(self):
        """What a tenant-charged expense still owes us (0 for other expenses)."""
        if self.bearer != self.Bearer.TENANT:
            return Decimal("0.00")
        return max((self.amount or Decimal("0")) - (self.recovered_amount or Decimal("0")), Decimal("0.00"))

    LOCKED_FIELDS = ("amount", "vat_amount", "expense_date", "property_id", "bearer")

    def save(self, *args, **kwargs):
        if self.pk and self.is_locked and self._changed_from_saved():
            raise ValidationError(
                "This expense is on an approved owner statement and cannot be changed."
            )
        super().save(*args, **kwargs)

    def _changed_from_saved(self):
        old = Expense.objects.filter(pk=self.pk).first()
        if old is None:
            return False
        return any(getattr(old, f) != getattr(self, f) for f in self.LOCKED_FIELDS)

    def clean(self):
        errors = {}
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "The amount must be more than zero."
        if (
            self.amount is not None
            and self.vat_amount is not None
            and self.vat_amount > self.amount
        ):
            errors["vat_amount"] = "VAT cannot be more than the total."

        if self.property_id:
            if self.unit_id and self.unit.property_id != self.property_id:
                errors["unit"] = "This unit does not belong to the selected property."
            if (
                self.bearer == self.Bearer.OWNER
                and self.property.ownership_type != Property.Ownership.THIRD_PARTY
            ):
                errors["bearer"] = (
                    "This property belongs to your company, so there is no owner to "
                    "charge. Choose 'Our company' or 'Tenant'."
                )

        if (
            self.amount is not None
            and self.recovered_amount is not None
            and self.recovered_amount > self.amount
        ):
            errors["recovered_amount"] = "The tenant cannot pay back more than the expense."

        if self.pk and self.is_locked and self._changed_from_saved():
            errors["__all__"] = (
                "This expense is on an approved owner statement and cannot be "
                "changed. Reopen the statement first."
            )
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Owner statements and payouts
# ---------------------------------------------------------------------------


def default_fee_vat_percent():
    """The VAT rate on our management fee, from Company settings."""
    try:
        return CompanySettings.load().fee_vat_percent
    except Exception:  # the settings table may not exist yet (during migrate)
        return Decimal("5.00")


class OwnerStatement(models.Model):
    """What we collected for a landlord, what we deduct, and what we pay them.

    While DRAFT the lines can be recalculated from the cleared cheques and
    expenses. APPROVED freezes it. PAID records when and how the owner was paid.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved (ready to pay)"
        PAID = "paid", "Paid"

    class PayMethod(models.TextChoices):
        BANK = "bank_transfer", "Bank transfer"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"

    number = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="statements")
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    fee_vat_percent = models.DecimalField(
        "VAT on our management fee (%)",
        max_digits=5,
        decimal_places=2,
        default=default_fee_vat_percent,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Management fees are normally subject to 5% VAT. "
        "The usual rate is set in Company settings. Confirm with your accountant.",
    )
    pay_vat_to_owner = models.BooleanField(
        "pay the VAT collected on rent to the owner",
        default=True,
        help_text="Tick if the owner is the VAT-registered party and files the VAT "
        "return. Untick if you keep the VAT to pay the tax authority yourselves.",
    )
    brought_forward = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
        help_text="A negative balance from the previous statement.",
    )
    notes = models.TextField(blank=True)
    inputs_fingerprint = models.CharField(max_length=40, blank=True, editable=False)
    calculated_at = models.DateTimeField(null=True, blank=True, editable=False)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)

    paid_on = models.DateField(null=True, blank=True)
    pay_method = models.CharField(
        "payment method", max_length=15, choices=PayMethod.choices, blank=True
    )
    pay_reference = models.CharField(
        "payment reference",
        max_length=100,
        blank=True,
        help_text="Bank transfer reference or cheque number.",
    )
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end", "-id"]

    def __str__(self):
        return self.number or f"Statement for {self.owner}"

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    # --- numbers -----------------------------------------------------------

    def totals(self):
        zero = Decimal("0.00")
        result = {
            "rent": zero,
            "rent_vat": zero,
            "fee": zero,
            "fee_vat": zero,
            "expenses": zero,
        }
        keys = {
            StatementLine.Kind.RENT: ("rent", "rent_vat"),
            StatementLine.Kind.FEE: ("fee", "fee_vat"),
            StatementLine.Kind.EXPENSE: ("expenses", None),
        }
        for line in self.lines.all():
            amount_key, vat_key = keys[line.kind]
            result[amount_key] += line.amount
            if vat_key:
                result[vat_key] += line.vat_amount
        vat_to_owner = result["rent_vat"] if self.pay_vat_to_owner else zero
        result["vat_to_owner"] = vat_to_owner
        result["vat_held"] = result["rent_vat"] - vat_to_owner
        result["brought_forward"] = self.brought_forward
        result["net_payout"] = (
            result["rent"]
            + vat_to_owner
            - result["fee"]
            - result["fee_vat"]
            - result["expenses"]
            + self.brought_forward
        )
        return result

    @property
    def net_payout(self):
        return self.totals()["net_payout"]

    # --- checks ------------------------------------------------------------

    def clean(self):
        errors = {}
        if self.period_start and self.period_end and self.period_end < self.period_start:
            errors["period_end"] = "The end date cannot be before the start date."
        elif self.owner_id and self.period_start and self.period_end:
            overlapping = OwnerStatement.objects.filter(
                owner_id=self.owner_id,
                period_start__lte=self.period_end,
                period_end__gte=self.period_start,
            ).exclude(pk=self.pk)
            if overlapping.exists():
                errors["period_start"] = (
                    "This owner already has a statement that overlaps these dates."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f"OS-{self.period_end.year}-{self.pk:05d}"
            OwnerStatement.objects.filter(pk=self.pk).update(number=self.number)

    def delete(self, *args, **kwargs):
        if not self.is_draft:
            raise ValidationError(
                "Only a draft statement can be deleted. Reopen it first."
            )
        return super().delete(*args, **kwargs)

    # --- workflow ----------------------------------------------------------

    def _require(self, status, message):
        if self.status != status:
            raise ValidationError(message)

    def _properties(self):
        """Third-party properties this owner owns now or owned before."""
        ids = set(
            Property.objects.filter(
                owner_id=self.owner_id, ownership_type=Property.Ownership.THIRD_PARTY
            ).values_list("pk", flat=True)
        )
        ids |= set(
            PropertyOwnership.objects.filter(owner_id=self.owner_id).values_list(
                "property_id", flat=True
            )
        )
        return list(
            Property.objects.filter(pk__in=ids)
            .select_related("owner")
            .prefetch_related("past_owners")
        )

    def _candidates(self, start=None, end=None, only_free=False):
        """Cleared rent / penalty payments and owner expenses that belong here.

        Only items dated inside the owner's period of ownership count. With
        `only_free` they must be on no statement at all; otherwise items already
        on THIS statement are included too.
        """
        properties = self._properties()
        by_id = {p.pk: p for p in properties}
        if only_free or not self.pk:
            on_this = Q(statement_line__isnull=True)
        else:
            on_this = Q(statement_line__isnull=True) | Q(statement_line__statement=self)

        cheque_qs = Cheque.objects.filter(
            on_this,
            purpose__in=[Cheque.Purpose.RENT, Cheque.Purpose.PENALTY],
            status=Cheque.Status.CLEARED,
            lease__unit__property__in=properties,
        )
        expense_qs = Expense.objects.filter(
            on_this, bearer=Expense.Bearer.OWNER, property__in=properties
        )
        if start:
            cheque_qs = cheque_qs.filter(cleared_date__gte=start)
            expense_qs = expense_qs.filter(expense_date__gte=start)
        if end:
            cheque_qs = cheque_qs.filter(cleared_date__lte=end)
            expense_qs = expense_qs.filter(expense_date__lte=end)

        cheques = [
            c
            for c in cheque_qs.select_related("lease__tenant", "lease__unit__property")
            .order_by("cleared_date", "id")
            .distinct()
            if by_id[c.lease.unit.property_id].owned_by_on(self.owner_id, c.cleared_date)
        ]
        expenses = [
            e
            for e in expense_qs.select_related("property", "unit", "vendor")
            .order_by("expense_date", "id")
            .distinct()
            if by_id[e.property_id].owned_by_on(self.owner_id, e.expense_date)
        ]
        return properties, cheques, expenses

    def _carried_forward(self):
        previous = (
            OwnerStatement.objects.filter(
                owner_id=self.owner_id, period_end__lt=self.period_start
            )
            .exclude(status=self.Status.DRAFT)
            .order_by("-period_end")
            .first()
        )
        if previous:
            previous_net = previous.net_payout
            if previous_net < 0:
                return previous_net
        return Decimal("0.00")

    def _build_lines(self):
        """Work out the statement lines from the payments and expenses now on file.

        Returns (unsaved lines, brought-forward amount). Nothing is written.
        """
        properties, cheques, expenses = self._candidates(self.period_start, self.period_end)
        lines = []
        fee_base = {}

        for cheque in cheques:
            lease = cheque.lease
            prop = lease.unit.property
            number = f" - cheque {cheque.cheque_number}" if cheque.cheque_number else ""
            if cheque.purpose == Cheque.Purpose.PENALTY:
                lines.append(
                    StatementLine(
                        statement=self,
                        kind=StatementLine.Kind.RENT,
                        property=prop,
                        date=cheque.cleared_date,
                        description=(
                            f"Early-termination penalty - {lease.tenant.name} - "
                            f"unit {lease.unit.unit_number}"
                        ),
                        amount=cheque.amount,
                        vat_amount=Decimal("0.00"),
                        cheque=cheque,
                    )
                )
                continue
            net, vat = split_vat(cheque.amount, lease.vat_percent)
            # The fee is charged only while the management agreement runs.
            if prop.owner_id != self.owner_id or prop.fee_applies_on(cheque.cleared_date):
                fee_base[prop.pk] = fee_base.get(prop.pk, Decimal("0.00")) + net
            how = "" if cheque.method == cheque.Method.CHEQUE else f" ({cheque.get_method_display().lower()})"
            lines.append(
                StatementLine(
                    statement=self,
                    kind=StatementLine.Kind.RENT,
                    property=prop,
                    date=cheque.cleared_date,
                    description=f"Rent - {lease.tenant.name} - unit {lease.unit.unit_number}{number}{how}",
                    amount=net,
                    vat_amount=vat,
                    cheque=cheque,
                )
            )

        for prop in properties:
            base = fee_base.get(prop.pk, Decimal("0.00"))
            if prop.owner_id == self.owner_id:
                percent = prop.effective_fee_percent or Decimal("0")
            else:  # a property this owner used to own: use their default fee
                percent = Decimal(self.owner.management_fee_percent or 0)
            fee = percent_of(base, percent)
            if fee > 0:
                lines.append(
                    StatementLine(
                        statement=self,
                        kind=StatementLine.Kind.FEE,
                        property=prop,
                        date=self.period_end,
                        description=f"Management fee {percent.normalize():f}% of AED {base:,.2f} rent",
                        amount=fee,
                        vat_amount=percent_of(fee, self.fee_vat_percent),
                    )
                )

        for expense in expenses:
            where = f" - unit {expense.unit.unit_number}" if expense.unit_id else ""
            lines.append(
                StatementLine(
                    statement=self,
                    kind=StatementLine.Kind.EXPENSE,
                    property=expense.property,
                    date=expense.expense_date,
                    description=f"{expense.description}{where}",
                    amount=expense.owner_charge,
                    expense=expense,
                )
            )
        return lines, self._carried_forward()

    def _fingerprint(self, lines, carried):
        """A short code describing exactly what the statement should contain."""
        parts = [str(carried), str(self.fee_vat_percent)]
        for line in lines:
            parts.append(
                "|".join(
                    str(x)
                    for x in (
                        line.kind,
                        line.property_id,
                        line.cheque_id or "",
                        line.expense_id or "",
                        line.date,
                        line.amount,
                        line.vat_amount,
                        line.description,
                    )
                )
            )
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()

    def is_stale(self):
        """True if a draft no longer matches the payments and expenses on file.

        For example: an expense was corrected, a cheque was added, or a fee
        changed after the statement was calculated.
        """
        if not self.is_draft or not self.pk:
            return False
        lines, carried = self._build_lines()
        return self._fingerprint(lines, carried) != self.inputs_fingerprint

    def recalculate(self):
        """Rebuild the lines from the cleared payments and owner-paid expenses."""
        self._require(self.Status.DRAFT, "Only a draft statement can be recalculated.")
        with transaction.atomic():
            lines, carried = self._build_lines()
            self.lines.all().delete()
            StatementLine.objects.bulk_create(lines)
            self.brought_forward = carried
            self.inputs_fingerprint = self._fingerprint(lines, carried)
            self.calculated_at = timezone.now()
            self.save()
        return len(lines)

    def _count_unstatemented(self, start=None, end=None):
        _, cheques, expenses = self._candidates(start, end, only_free=True)
        return {"cheques": len(cheques), "expenses": len(expenses)}

    def uncovered_items(self):
        """Items dated BEFORE this period that are on no statement (easy to miss)."""
        return self._count_unstatemented(end=self.period_start - timedelta(days=1))

    def new_items(self):
        """Items INSIDE this period that are not on the statement yet."""
        return self._count_unstatemented(self.period_start, self.period_end)

    def owner_share_of(self, cheque):
        """What the owner got from one cleared payment on this statement: the rent
        without VAT (plus the VAT when it is paid to the owner) less our fee and the
        VAT on the fee. Penalties go to the owner in full; other payments are ours."""
        if cheque.purpose == Cheque.Purpose.PENALTY:
            return cheque.amount
        if cheque.purpose != Cheque.Purpose.RENT:
            return Decimal("0.00")
        lease = cheque.lease
        prop = lease.unit.property
        net, vat = split_vat(cheque.amount, lease.vat_percent)
        fee = Decimal("0.00")
        day = cheque.cleared_date or timezone.localdate()
        if prop.owner_id != self.owner_id:
            fee = percent_of(net, Decimal(self.owner.management_fee_percent or 0))
        elif prop.fee_applies_on(day):
            fee = percent_of(net, prop.effective_fee_percent or Decimal("0"))
        fee_vat = percent_of(fee, self.fee_vat_percent)
        share = net - fee - fee_vat + (vat if self.pay_vat_to_owner else Decimal("0.00"))
        return max(share, Decimal("0.00"))

    def approve(self, user=None, allow_gaps=False):
        self._require(self.Status.DRAFT, "Only a draft statement can be approved.")
        if not allow_gaps:
            gap = self.uncovered_items()
            if gap["cheques"] or gap["expenses"]:
                raise ValidationError(
                    f"Before {self.period_start:%d %b %Y} there are {gap['cheques']} cleared "
                    f"rent payment(s) and {gap['expenses']} owner expense(s) on no statement, "
                    "so the owner would never be paid for them. Move this statement's start "
                    "date back to include them, or use 'Approve anyway' if they are meant "
                    "to stay out (for example, history from before you started using the system)."
                )
        if self.is_stale():
            raise ValidationError(
                "Something changed since this statement was calculated (a new "
                "cheque, a corrected expense, a fee...). Click Recalculate and check "
                "the numbers again before approving."
            )
        if not self.lines.exists() and not self.brought_forward:
            raise ValidationError(
                "There is nothing on this statement. Click Recalculate first."
            )
        self.status = self.Status.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save()

    def reopen(self):
        self._require(
            self.Status.APPROVED, "Only an approved (unpaid) statement can be reopened."
        )
        self.status = self.Status.DRAFT
        self.approved_by = None
        self.approved_at = None
        self.save()

    def mark_paid(self, on=None, method="", reference="", user=None):
        self._require(self.Status.APPROVED, "Approve the statement before paying it.")
        if self.net_payout <= 0:
            raise ValidationError("There is nothing to pay on this statement.")
        if method not in self.PayMethod.values:
            raise ValidationError("Choose how the owner was paid.")
        reference = (reference or "").strip()
        if method != self.PayMethod.CASH and not reference:
            raise ValidationError("Enter the bank transfer reference or cheque number.")
        self.paid_on = on or timezone.localdate()
        self.pay_method = method
        self.pay_reference = reference
        self.paid_by = user
        self.status = self.Status.PAID
        self.save()


class StatementLine(models.Model):
    """One row on an owner statement. A snapshot taken when it was calculated."""

    class Kind(models.TextChoices):
        RENT = "rent", "Rent collected"
        FEE = "fee", "Management fee"
        EXPENSE = "expense", "Expense"

    statement = models.ForeignKey(
        OwnerStatement, on_delete=models.CASCADE, related_name="lines"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    property = models.ForeignKey(
        Property, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    date = models.DateField()
    description = models.CharField(max_length=255)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, help_text="Always positive; the kind decides the direction."
    )
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cheque = models.OneToOneField(
        Cheque,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="statement_line",
    )
    expense = models.OneToOneField(
        Expense,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="statement_line",
    )

    class Meta:
        ordering = ["property__name", "kind", "date", "id"]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.description}"


# ---------------------------------------------------------------------------
# Demo data bookkeeping
# ---------------------------------------------------------------------------


class DemoRecord(models.Model):
    """Remembers which records were created by "Load demo data".

    Only records listed here are removed by "Remove demo data", so anything
    you typed in yourself is never touched.
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id"], name="unique_demo_record"
            )
        ]
        verbose_name = "demo data"
        verbose_name_plural = "demo data"

    def __str__(self):
        return f"{self.content_type} #{self.object_id}"


# ---------------------------------------------------------------------------
# Company-wide settings
# ---------------------------------------------------------------------------


class CompanySettings(models.Model):
    """One row of settings for the whole company."""

    fee_vat_percent = models.DecimalField(
        "VAT on our management fee (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("5.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Used as the starting value on every new owner statement.",
    )
    bounce_fee = models.DecimalField(
        "bounced-cheque fee (AED)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
        help_text="Charged to the tenant each time a cheque bounces. 0 = no fee.",
    )
    default_notice_days = models.PositiveSmallIntegerField(
        "notice period for early termination (days)",
        default=90,
        help_text="UAE contracts usually require 60 to 90 days of written notice. "
        "Used as the starting value when you terminate a lease.",
    )
    penalty_months = models.DecimalField(
        "early-exit penalty (months of rent)",
        max_digits=4,
        decimal_places=1,
        default=Decimal("2.0"),
        validators=[MinValueValidator(0)],
        help_text="Usually 1 to 2 months of rent, if the contract has an early-exit "
        "clause. Used to suggest the penalty; you can change it for each lease.",
    )

    class Meta:
        verbose_name = "company settings"
        verbose_name_plural = "company settings"

    def __str__(self):
        return "Company settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("The company settings cannot be deleted.")


class SystemState(models.Model):
    """Small facts the system remembers about itself (for example, which
    version of the ready-made roles was last applied)."""

    key = models.CharField(max_length=60, unique=True)
    value = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.key
