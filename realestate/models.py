from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import Case, CharField, Exists, OuterRef, Q, Sum, Value, When
from django.utils import timezone

from .fields import EmiratesIDField, IBANField, PassportField
from .utils import add_months, percent_of, split_amount, split_vat
from .validators import (
    validate_document_file,
    validate_makani,
    validate_phone,
    validate_trn,
)

# A few models below have a field called `property`, which hides Python's own
# `property` inside those classes. They use `_property` for the decorator instead.
_property = property

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
        super().save(*args, **kwargs)

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
          1. Has an active lease that has already started  -> occupied
          2. Marked "under maintenance"                     -> maintenance
          3. Has an active lease that starts in the future  -> reserved
          4. Otherwise                                      -> vacant
        """
        today = timezone.localdate()
        active_leases = Lease.objects.filter(
            unit=OuterRef("pk"), status=Lease.Status.ACTIVE
        )
        return self.annotate(
            has_current_lease=Exists(active_leases.filter(start_date__lte=today)),
            has_upcoming_lease=Exists(active_leases.filter(start_date__gt=today)),
        ).annotate(
            status_calc=Case(
                When(has_current_lease=True, then=Value(Unit.Status.OCCUPIED.value)),
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
        if active_leases.filter(start_date__lte=today).exists():
            return self.Status.OCCUPIED
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
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text="A lease stays Active (and its unit Occupied) until you mark it Expired or Terminated.",
    )
    notes = models.TextField("internal notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.tenant.name} - {self.unit}"

    # --- money -------------------------------------------------------------

    @property
    def lease_years(self):
        """Length of the lease in whole years (at least 1)."""
        if not (self.start_date and self.end_date):
            return 1
        return max(1, round((self.end_date - self.start_date).days / 365))

    @property
    def vat_amount(self):
        """VAT for one year of rent."""
        rent = self.annual_rent or Decimal("0")
        percent = self.vat_percent or Decimal("0")
        return (rent * percent / 100).quantize(Decimal("0.01"))

    @property
    def contract_total(self):
        """Rent plus VAT for the whole lease period."""
        rent = self.annual_rent or Decimal("0")
        return (rent + self.vat_amount) * self.lease_years

    def cheque_totals(self):
        """Totals of this lease's rent cheques, by what has happened to them."""
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

    def generate_cheque_schedule(self):
        """Create one empty "expected" rent cheque for every installment.

        Does nothing (returns 0) if the lease already has rent cheques.
        Returns how many cheques were created.
        """
        has_rent_cheques = (
            self.cheques.filter(purpose=Cheque.Purpose.RENT)
            .exclude(status=Cheque.Status.CANCELLED)
            .exists()
        )
        if has_rent_cheques:
            return 0

        count = self.number_of_payments * self.lease_years
        amounts = split_amount(self.contract_total, count)
        months_between = 12 // self.number_of_payments
        with transaction.atomic():
            for index, amount in enumerate(amounts):
                Cheque.objects.create(
                    lease=self,
                    purpose=Cheque.Purpose.RENT,
                    installment_no=index + 1,
                    amount=amount,
                    cheque_date=add_months(self.start_date, months_between * index),
                    status=Cheque.Status.PENDING,
                )
        return count

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
            overlapping = Lease.objects.filter(
                unit_id=self.unit_id,
                status=self.Status.ACTIVE,
                start_date__lt=self.end_date,
                end_date__gt=self.start_date,
            ).exclude(pk=self.pk)
            if overlapping.exists():
                errors["unit"] = "This unit already has an active lease for those dates."

        if self.renewal_of_id and self.pk and self.renewal_of_id == self.pk:
            errors["renewal_of"] = "A lease cannot renew itself."

        if errors:
            raise ValidationError(errors)


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
    """A cheque received from a tenant, tracked from receipt to bank clearing."""

    class Purpose(models.TextChoices):
        RENT = "rent", "Rent"
        SECURITY_DEPOSIT = "security_deposit", "Security deposit"
        COMMISSION = "commission", "Agency commission"
        REGISTRATION = "registration", "Ejari / registration fee"
        MAINTENANCE = "maintenance", "Maintenance"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Expected (not received yet)"
        RECEIVED = "received", "In hand"
        DEPOSITED = "deposited", "Deposited at bank"
        CLEARED = "cleared", "Cleared (cashed)"
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

    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name="cheques")
    purpose = models.CharField(
        max_length=20, choices=Purpose.choices, default=Purpose.RENT
    )
    installment_no = models.PositiveSmallIntegerField(
        "installment no.", null=True, blank=True
    )
    cheque_number = models.CharField(max_length=30, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
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
        help_text="The date written on the cheque (the day it can be deposited)."
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RECEIVED
    )
    received_date = models.DateField(null=True, blank=True)
    deposited_date = models.DateField(null=True, blank=True)
    cleared_date = models.DateField(null=True, blank=True)
    returned_date = models.DateField(null=True, blank=True)
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

    def __str__(self):
        number = self.cheque_number or "no number yet"
        if self.amount is None:
            return f"Cheque {number}"
        return f"Cheque {number} - AED {self.amount:,.2f}"

    @property
    def tenant(self):
        return self.lease.tenant

    # --- checks and automatic dates ---------------------------------------

    def clean(self):
        errors = {}
        S = self.Status
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "The amount must be more than zero."

        number = (self.cheque_number or "").strip()
        bank = (self.bank_name or "").strip()
        if self.status not in (S.PENDING, S.CANCELLED):
            if not number:
                errors["cheque_number"] = "Enter the cheque number."
            if not bank:
                errors["bank_name"] = "Enter the bank name."

        if number and bank:
            duplicate = Cheque.objects.filter(
                cheque_number__iexact=number, bank_name__iexact=bank
            ).exclude(pk=self.pk)
            if duplicate.exists():
                errors["cheque_number"] = (
                    "A cheque with this number from this bank is already recorded."
                )

        if self.status == S.RETURNED and not self.return_reason:
            errors["return_reason"] = "Choose why the cheque was returned."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        S = self.Status
        today = timezone.localdate()
        self.cheque_number = (self.cheque_number or "").strip()
        self.bank_name = (self.bank_name or "").strip()

        if self.status == S.RECEIVED and not self.received_date:
            self.received_date = today
        if self.status == S.DEPOSITED and not self.deposited_date:
            self.deposited_date = today
        if self.status == S.CLEARED:
            if not self.cleared_date:
                self.cleared_date = today
            if not self.deposited_date:
                self.deposited_date = self.cleared_date
        if self.status == S.RETURNED:
            if not self.returned_date:
                self.returned_date = today
            if not self.deposited_date:
                self.deposited_date = self.returned_date
        super().save(*args, **kwargs)

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

    def mark_deposited(self, on=None, user=None):
        if self.status != self.Status.RECEIVED:
            raise ValidationError("Only a cheque that is in hand can be deposited.")
        self.deposited_date = on or timezone.localdate()
        self._move_to(self.Status.DEPOSITED, user)

    def mark_cleared(self, on=None, user=None):
        if self.status not in (self.Status.RECEIVED, self.Status.DEPOSITED):
            raise ValidationError(
                "Only a cheque that is in hand or deposited can be marked as cleared."
            )
        self.cleared_date = on or timezone.localdate()
        self._move_to(self.Status.CLEARED, user)

    def mark_returned(self, reason, on=None, user=None, note=""):
        if self.status != self.Status.DEPOSITED:
            raise ValidationError("Only a deposited cheque can be marked as returned.")
        if reason not in self.ReturnReason.values:
            raise ValidationError("Choose why the cheque was returned.")
        self.return_reason = reason
        self.returned_date = on or timezone.localdate()
        self._move_to(self.Status.RETURNED, user, note)

    def create_replacement(self, user=None):
        """Mark this bounced cheque as replaced and add an empty cheque to collect."""
        if self.status != self.Status.RETURNED:
            raise ValidationError("Only a returned cheque can be replaced.")
        with transaction.atomic():
            replacement = Cheque.objects.create(
                lease=self.lease,
                purpose=self.purpose,
                installment_no=self.installment_no,
                drawer_name=self.drawer_name,
                amount=self.amount,
                cheque_date=timezone.localdate(),
                status=self.Status.PENDING,
                replaces=self,
                notes=f"Replacement for bounced cheque {self.cheque_number}.",
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
    receipt = models.FileField(
        upload_to=document_upload_path,
        blank=True,
        validators=[validate_document_file],
        help_text="Invoice or receipt: PDF, JPG or PNG, up to 10 MB.",
    )
    notes = models.TextField(blank=True)
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

        if self.pk and self.is_locked:
            errors["__all__"] = (
                "This expense is on an approved owner statement and cannot be "
                "changed. Reopen the statement first."
            )
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Owner statements and payouts
# ---------------------------------------------------------------------------


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
        default=Decimal("5.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Management fees are normally subject to 5% VAT. "
        "Confirm with your accountant.",
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

    def recalculate(self):
        """Rebuild the lines from cleared rent cheques and owner-paid expenses."""
        self._require(self.Status.DRAFT, "Only a draft statement can be recalculated.")
        S = Cheque.Status
        properties = list(
            Property.objects.filter(
                owner_id=self.owner_id, ownership_type=Property.Ownership.THIRD_PARTY
            ).select_related("owner")
        )
        with transaction.atomic():
            self.lines.all().delete()
            lines = []
            rent_by_property = {}

            cheques = (
                Cheque.objects.filter(
                    purpose=Cheque.Purpose.RENT,
                    status=S.CLEARED,
                    cleared_date__range=(self.period_start, self.period_end),
                    lease__unit__property__in=properties,
                    statement_line__isnull=True,
                )
                .select_related("lease__tenant", "lease__unit__property")
                .order_by("cleared_date", "id")
            )
            for cheque in cheques:
                lease = cheque.lease
                prop = lease.unit.property
                net, vat = split_vat(cheque.amount, lease.vat_percent)
                rent_by_property[prop.pk] = rent_by_property.get(prop.pk, Decimal("0.00")) + net
                number = f" - cheque {cheque.cheque_number}" if cheque.cheque_number else ""
                lines.append(
                    StatementLine(
                        statement=self,
                        kind=StatementLine.Kind.RENT,
                        property=prop,
                        date=cheque.cleared_date,
                        description=f"Rent - {lease.tenant.name} - unit {lease.unit.unit_number}{number}",
                        amount=net,
                        vat_amount=vat,
                        cheque=cheque,
                    )
                )

            for prop in properties:
                base = rent_by_property.get(prop.pk, Decimal("0.00"))
                percent = prop.effective_fee_percent or Decimal("0")
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

            expenses = (
                Expense.objects.filter(
                    bearer=Expense.Bearer.OWNER,
                    property__in=properties,
                    expense_date__range=(self.period_start, self.period_end),
                    statement_line__isnull=True,
                )
                .select_related("property", "unit", "vendor")
                .order_by("expense_date", "id")
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
                        amount=expense.amount,
                        expense=expense,
                    )
                )
            StatementLine.objects.bulk_create(lines)

            previous = (
                OwnerStatement.objects.filter(
                    owner_id=self.owner_id, period_end__lt=self.period_start
                )
                .exclude(status=self.Status.DRAFT)
                .order_by("-period_end")
                .first()
            )
            carried = Decimal("0.00")
            if previous:
                previous_net = previous.net_payout
                if previous_net < 0:
                    carried = previous_net
            self.brought_forward = carried
            self.save()
        return len(lines)

    def _count_unstatemented(self, **date_filters):
        """Cleared rent cheques and owner expenses, on no statement, matching the date filters.

        `date_filters` use the names "cheque_date" and "expense_date" as keys with
        a lookup suffix, e.g. cleared_date__lt / expense_date__lt.
        """
        properties = Property.objects.filter(
            owner_id=self.owner_id, ownership_type=Property.Ownership.THIRD_PARTY
        )
        cheque_filter = {k: v for k, v in date_filters.items() if k.startswith("cleared_date")}
        expense_filter = {k: v for k, v in date_filters.items() if k.startswith("expense_date")}
        cheques = Cheque.objects.filter(
            purpose=Cheque.Purpose.RENT,
            status=Cheque.Status.CLEARED,
            lease__unit__property__in=properties,
            statement_line__isnull=True,
            **cheque_filter,
        ).count()
        expenses = Expense.objects.filter(
            bearer=Expense.Bearer.OWNER,
            property__in=properties,
            statement_line__isnull=True,
            **expense_filter,
        ).count()
        return {"cheques": cheques, "expenses": expenses}

    def uncovered_items(self):
        """Items dated BEFORE this period that are on no statement (easy to miss)."""
        return self._count_unstatemented(
            cleared_date__lt=self.period_start, expense_date__lt=self.period_start
        )

    def new_items(self):
        """Items INSIDE this period that are not on the statement yet."""
        return self._count_unstatemented(
            cleared_date__range=(self.period_start, self.period_end),
            expense_date__range=(self.period_start, self.period_end),
        )

    def approve(self, user=None):
        self._require(self.Status.DRAFT, "Only a draft statement can be approved.")
        new = self.new_items()
        if new["cheques"] or new["expenses"]:
            raise ValidationError(
                f"{new['cheques']} cheque(s) and {new['expenses']} expense(s) in this "
                "period are not on the statement yet. Click Recalculate and check the "
                "numbers again."
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
