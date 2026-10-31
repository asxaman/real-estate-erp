import re
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import IntegrityError, models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from core import sysconf
from core.models import PHOTO_VALIDATORS

from .models_constants import (
    CODE_ERROR,
    CODE_REGEX,
    COMPANY_OWNER_CODE,
    COMPANY_OWNER_NAME,
    FLOOR_ERROR,
    FLOOR_HELP,
    FLOOR_REGEX,
    PHONE_ERROR,
    PHONE_REGEX,
    SWIFT_ERROR,
    SWIFT_REGEX,
    emirates_id_is_valid,
    floor_sort_key,
    iban_is_valid,
    normalise_emirates_id,
    normalise_iban,
    normalise_unit_number,
)


def owner_photo_path(instance, filename):
    return f"owner_photos/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def owner_document_path(instance, filename):
    return f"owner_documents/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def validate_document_size(file):
    from core import sysconf

    limit = sysconf.get("max_document_mb")  # set on the System settings page
    if file.size > limit * 1024 * 1024:
        raise ValidationError(f"The file is too large. The maximum size is {limit} MB.")


def validate_iban(value):
    if not iban_is_valid(value):
        raise ValidationError("This IBAN is not valid. Check the number (UAE IBANs start with AE and have 23 characters).")


def validate_year_built(value):
    latest = timezone.localdate().year + 5  # buildings still under construction are allowed
    if value < 1900 or value > latest:
        raise ValidationError(f"Enter a year between 1900 and {latest}.")


TOTAL_FLOORS_VALIDATORS = [
    MinValueValidator(1, message="Total floors must be between 1 and 200."),
    MaxValueValidator(200, message="Total floors must be between 1 and 200."),
]
AREA_VALIDATORS = [MinValueValidator(Decimal("0.01"), message="The area must be more than 0.")]

# Documents that must carry an expiry date (so the warnings can work) ...
EXPIRY_REQUIRED_TYPES = ("emirates_id", "passport", "visa", "trade_licence")
# ... and documents of which a person or company has only one at a time.
SINGLE_DOCUMENT_TYPES = ("emirates_id", "passport", "visa", "trade_licence", "vat_certificate")


def validate_document_rules(document):
    """Rules shared by owner and tenant documents (called from their clean())."""
    errors = {}
    document.number = (document.number or "").strip()
    if document.doc_type == "emirates_id" and document.number:
        if emirates_id_is_valid(document.number):
            document.number = normalise_emirates_id(document.number)
        else:
            errors["number"] = "An Emirates ID has 15 digits, for example 784-1990-1234567-1."
    if document.doc_type in EXPIRY_REQUIRED_TYPES and document.expiry_date is None:
        errors["expiry_date"] = "Enter the expiry date."
    if errors:
        raise ValidationError(errors)


def save_with_unique_code(instance, next_code, do_save):
    """
    Save a record whose code (OWN-0001, TEN-0001 ...) is assigned automatically.
    If two people save at the same moment and get the same code, try again with the next one.
    """
    for attempt in range(5):
        instance.code = next_code()
        try:
            with transaction.atomic():
                do_save()
            return
        except IntegrityError:
            if attempt == 4:
                raise
            instance.code = None


def next_owner_code():
    numbers = []
    for code in Owner.objects.exclude(code__isnull=True).values_list("code", flat=True):
        match = re.fullmatch(r"OWN-(\d+)", code)
        if match:
            numbers.append(int(match.group(1)))
    return f"OWN-{max(numbers, default=0) + 1:04d}"


def _changed_in_db(instance, attname):
    """True for a new record, or when `attname` differs from what is saved in the database."""
    if instance._state.adding or instance.pk is None:
        return True
    saved = (
        type(instance).objects.filter(pk=instance.pk).values_list(attname, flat=True).first()
    )
    return saved != getattr(instance, attname)


class UnitType(models.TextChoices):
    # Residential (the stored values keep their original keys, only the labels changed)
    STUDIO = "studio", "Studio"
    ONE_BR = "1br", "1BHK"
    TWO_BR = "2br", "2BHK"
    THREE_BR = "3br", "3BHK"
    FOUR_BR = "4br", "4BHK"
    FIVE_BR = "5br", "5BHK"
    # Commercial
    OFFICE = "office", "Office"
    SHOP = "shop", "Shop"


RESIDENTIAL_UNIT_TYPES = (
    UnitType.STUDIO,
    UnitType.ONE_BR,
    UnitType.TWO_BR,
    UnitType.THREE_BR,
    UnitType.FOUR_BR,
    UnitType.FIVE_BR,
)
COMMERCIAL_UNIT_TYPES = (UnitType.OFFICE, UnitType.SHOP)

BATHROOM_VALIDATORS = [
    MinValueValidator(1, message="Bathrooms must be between 1 and 10."),
    MaxValueValidator(10, message="Bathrooms must be between 1 and 10."),
]


class Owner(models.Model):
    """A property owner (individual or company) whose properties we manage."""

    class OwnerType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"

    name = models.CharField("Owner name", max_length=200)
    owner_type = models.CharField(
        "Owner type", max_length=12, choices=OwnerType.choices, default=OwnerType.INDIVIDUAL
    )
    # Assigned automatically (OWN-0001, OWN-0002 ...).
    code = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    photo = models.ImageField(
        "Photo / logo",
        upload_to=owner_photo_path,
        blank=True,
        validators=PHOTO_VALIDATORS,
        help_text="Optional. A photo for an individual or the logo of a company "
        "(JPG, PNG or WebP).",
    )
    # The one built-in owner that stands for "our own company". It is created
    # automatically, cannot be renamed or deleted, and properties owned by it
    # have no management fee.
    is_company = models.BooleanField(default=False, editable=False)
    email = models.EmailField(blank=True)
    phone = models.CharField(
        max_length=30,
        blank=True,
        validators=[RegexValidator(PHONE_REGEX, PHONE_ERROR)],
        help_text="Optional. Example: +971 50 123 4567.",
    )
    address = models.TextField(blank=True)
    # Used later by the Owner portal: the login account this owner signs in with.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owner_profile",
        help_text="Login account for the owner portal (optional).",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_company", "name"]  # "Company Owned" always comes first

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join((self.name or "").split())
        if not self.code and self.is_company:
            self.code = COMPANY_OWNER_CODE
        old_photo = ""
        if self.pk:
            old_photo = Owner.objects.filter(pk=self.pk).values_list("photo", flat=True).first() or ""
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(
                self, lambda: next_owner_code(), lambda: super(Owner, self).save(*args, **kwargs)
            )
        if old_photo and old_photo != (self.photo.name or ""):
            self.photo.storage.delete(old_photo)  # the picture was replaced or removed

    @property
    def portal_status(self):
        """(key, label): none / never / active / disabled."""
        if self.user_id is None:
            return "none", "No login"
        if not self.user.is_active:
            return "disabled", "Disabled"
        if self.user.last_login is None:
            return "never", "Never logged in"
        return "active", "Active"

    def document_counts(self):
        """(expired, expiring soon) among this owner's documents."""
        today = timezone.localdate()
        soon_limit = today + timedelta(days=sysconf.expiry_days())
        expired = self.documents.filter(expiry_date__lt=today).count()
        soon = self.documents.filter(expiry_date__gte=today, expiry_date__lte=soon_limit).count()
        return expired, soon

    def clean(self):
        self.name = " ".join((self.name or "").split())  # trim and collapse repeated spaces
        self.email = (self.email or "").strip().lower()
        if self.is_company:
            return  # the built-in owner is managed by the system
        errors = {}
        if self.name.lower() == COMPANY_OWNER_NAME.lower():
            errors["name"] = (
                f'"{COMPANY_OWNER_NAME}" is reserved for the company itself. Choose another name.'
            )
        elif self.name and Owner.objects.filter(name__iexact=self.name).exclude(pk=self.pk).exists():
            errors["name"] = "An owner with this name already exists."
        # An owner cannot be switched off while it still has active properties.
        if not self.is_active and self.pk:
            active = self.properties.filter(is_active=True).count()
            if active:
                errors["is_active"] = (
                    f"This owner still has {active} active "
                    f"propert{'y' if active == 1 else 'ies'}. Deactivate "
                    f"{'it' if active == 1 else 'them'} or move "
                    f"{'it' if active == 1 else 'them'} to another owner first."
                )
        if errors:
            raise ValidationError(errors)

    @classmethod
    def company(cls):
        """Return the built-in "Company Owned" owner, creating it if it is missing."""
        owner = cls.objects.filter(is_company=True).first()
        if owner is None:
            owner = cls.objects.create(
                name=COMPANY_OWNER_NAME, is_company=True, owner_type=cls.OwnerType.COMPANY
            )
        return owner


@receiver(post_delete, sender=Owner)
def remove_owner_photo(sender, instance, **kwargs):
    if instance.photo:
        instance.photo.delete(save=False)


class OwnerContact(models.Model):
    """A person to talk to about an owner (the owner, a representative, an accountant ...)."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        SIGNATORY = "signatory", "Authorised signatory"
        REPRESENTATIVE = "representative", "Representative"
        ACCOUNTANT = "accountant", "Accountant"
        ASSISTANT = "assistant", "Assistant"
        OTHER = "other", "Other"

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        ARABIC = "ar", "Arabic"

    owner = models.ForeignKey(Owner, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OWNER)
    is_primary = models.BooleanField("Primary", default=False)
    mobile = models.CharField(
        max_length=30, blank=True, validators=[RegexValidator(PHONE_REGEX, PHONE_ERROR)]
    )
    whatsapp = models.CharField(
        "WhatsApp", max_length=30, blank=True, validators=[RegexValidator(PHONE_REGEX, PHONE_ERROR)]
    )
    email = models.EmailField(blank=True)
    language = models.CharField(
        "Language", max_length=2, choices=Language.choices, default=Language.ENGLISH
    )

    class Meta:
        ordering = ["-is_primary", "name"]
        verbose_name = "contact"
        verbose_name_plural = "Contacts"

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"

    def clean(self):
        self.name = " ".join((self.name or "").split())
        self.email = (self.email or "").strip().lower()


class OwnerDocument(models.Model):
    """An owner's document (Emirates ID, passport, trade licence ...) with an expiry date."""

    class DocType(models.TextChoices):
        EMIRATES_ID = "emirates_id", "Emirates ID"
        PASSPORT = "passport", "Passport"
        TRADE_LICENCE = "trade_licence", "Trade licence"
        VAT_CERTIFICATE = "vat_certificate", "VAT certificate"
        TITLE_DEED = "title_deed", "Title deed"
        POWER_OF_ATTORNEY = "poa", "Power of attorney"
        OTHER = "other", "Other"

    owner = models.ForeignKey(Owner, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField("Document", max_length=20, choices=DocType.choices)
    number = models.CharField("Number", max_length=60, blank=True)
    expiry_date = models.DateField("Expires on", null=True, blank=True)
    file = models.FileField(
        "File",
        upload_to=owner_document_path,
        blank=True,
        validators=[
            FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "webp"]),
            validate_document_size,
        ],
        help_text="PDF, JPG, PNG or WebP.",
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["doc_type", "expiry_date"]
        verbose_name = "document"
        verbose_name_plural = "Documents"

    def __str__(self):
        return f"{self.get_doc_type_display()} {self.number}".strip()

    def clean(self):
        validate_document_rules(self)

    @property
    def expiry_status(self):
        """'none' (no expiry date), 'valid', 'soon' or 'expired'."""
        if self.expiry_date is None:
            return "none"
        today = timezone.localdate()
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + timedelta(days=sysconf.expiry_days()):
            return "soon"
        return "valid"


@receiver(post_delete, sender=OwnerDocument)
def remove_document_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)


class OwnerBankAccount(models.Model):
    """Where an owner's money is paid. Sensitive: only roles with permission can see it."""

    owner = models.ForeignKey(Owner, on_delete=models.CASCADE, related_name="bank_accounts")
    account_holder = models.CharField(max_length=150)
    bank_name = models.CharField(max_length=100)
    iban = models.CharField("IBAN", max_length=34, validators=[validate_iban])
    swift = models.CharField(
        "SWIFT / BIC", max_length=11, blank=True, validators=[RegexValidator(SWIFT_REGEX, SWIFT_ERROR)]
    )
    is_default = models.BooleanField("Default", default=False)

    class Meta:
        ordering = ["-is_default", "bank_name"]
        verbose_name = "bank account"
        verbose_name_plural = "Financials"

    def __str__(self):
        return f"{self.bank_name} {self.iban[-4:]}"

    def clean(self):
        self.iban = normalise_iban(self.iban)
        self.swift = (self.swift or "").strip().upper()


class Property(models.Model):
    """A building, villa compound, land plot, etc."""

    class PropertyType(models.TextChoices):
        # Buildings: these contain units
        RESIDENTIAL_BUILDING = "residential_building", "Residential Building"
        COMMERCIAL_BUILDING = "commercial_building", "Commercial Building"
        # Standalone properties: no units
        VILLA = "villa", "Villa"
        WAREHOUSE = "warehouse", "Warehouse"
        YARD = "yard", "Yard"
        SHOP = "shop", "Shop"
        APARTMENT = "apartment", "Apartment"
        PARKING_SPOT = "parking_spot", "Parking Spot"

    # Only these types can have units. Every other type is a standalone property.
    BUILDING_TYPES = (
        PropertyType.RESIDENTIAL_BUILDING,
        PropertyType.COMMERCIAL_BUILDING,
    )
    # Types for which "Year built" and "Total floors" make no sense.
    NO_BUILDING_INFO_TYPES = (
        PropertyType.WAREHOUSE,
        PropertyType.YARD,
        PropertyType.SHOP,
        PropertyType.APARTMENT,
        PropertyType.PARKING_SPOT,
    )

    class FeeType(models.TextChoices):
        PERCENT = "percent", "Percentage of collected rent (%)"
        FIXED = "fixed", "Fixed amount (AED per year)"

    class Status(models.TextChoices):
        VACANT = "vacant", "Vacant"
        OCCUPIED = "occupied", "Occupied"
        RESERVED = "reserved", "Reserved"
        MAINTENANCE = "maintenance", "Under maintenance"

    class Emirate(models.TextChoices):
        ABU_DHABI = "abu_dhabi", "Abu Dhabi"
        DUBAI = "dubai", "Dubai"
        SHARJAH = "sharjah", "Sharjah"
        AJMAN = "ajman", "Ajman"
        UAQ = "uaq", "Umm Al Quwain"
        RAK = "rak", "Ras Al Khaimah"
        FUJAIRAH = "fujairah", "Fujairah"

    name = models.CharField(max_length=200)
    code = models.CharField(
        max_length=20,
        unique=True,
        validators=[RegexValidator(CODE_REGEX, CODE_ERROR)],
        help_text="Short unique code: 3 to 6 letters or numbers, starting with a letter "
        "(e.g. RMT). Used in unit references like RMT-101.",
    )
    property_type = models.CharField(
        max_length=20,
        choices=PropertyType.choices,
        default=PropertyType.RESIDENTIAL_BUILDING,
        help_text="Only Residential and Commercial Buildings have units. "
        "All other types are standalone properties.",
    )
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="properties")
    emirate = models.CharField(
        max_length=20, choices=Emirate.choices, default=Emirate.ABU_DHABI
    )
    address = models.TextField(blank=True)
    map_link = models.URLField(
        "Map link", max_length=500, blank=True, help_text="Google Maps link (optional)."
    )
    year_built = models.PositiveIntegerField(null=True, blank=True, validators=[validate_year_built])
    total_floors = models.PositiveIntegerField(null=True, blank=True, validators=TOTAL_FLOORS_VALIDATORS)

    # Apartments only (a standalone apartment has no units, so it describes itself)
    apartment_type = models.CharField(
        "Type",
        max_length=10,
        choices=[(t.value, t.label) for t in RESIDENTIAL_UNIT_TYPES],
        blank=True,
    )
    bathrooms = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=BATHROOM_VALIDATORS,
        help_text="A number from 1 to 10.",
    )

    # Standalone properties only (buildings rent out their units instead)
    annual_rent = models.DecimalField(
        "Annual rent (AED)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        blank=True,
        default=Status.VACANT,
        help_text="Chosen by hand for now. Once leases are added, Occupied and Vacant will "
        "follow the lease automatically.",
    )

    # Management fee - not used when the owner is "Company Owned"
    management_fee_type = models.CharField(
        "Fee type", max_length=10, choices=FeeType.choices, blank=True, default=FeeType.PERCENT
    )
    management_fee_percent = models.DecimalField(
        "Management fee (%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentage of the rent collected that the company charges the owner.",
    )
    management_fee_fixed = models.DecimalField(
        "Management fee (AED / year)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Fixed amount the company charges the owner each year.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "properties"

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def has_units(self):
        return self.property_type in self.BUILDING_TYPES

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    @property
    def is_company_owned(self):
        return bool(self.owner_id and self.owner.is_company)

    def clean(self):
        # Codes are stored in capitals (checked here so "rmt" and "RMT" count as the same code).
        self.code = (self.code or "").strip().upper()
        errors = {}

        if self.pk:
            if not self.has_units:
                # Don't allow switching a building that already has units to a standalone type.
                if self.units.exists():
                    errors["property_type"] = (
                        "This property still has units. Delete its units "
                        "before changing it to a standalone type."
                    )
            else:
                # Residential and commercial buildings take different unit types.
                if self.property_type == self.PropertyType.RESIDENTIAL_BUILDING:
                    allowed = [t.value for t in RESIDENTIAL_UNIT_TYPES]
                    other_kind = "commercial (Office / Shop)"
                else:
                    allowed = [t.value for t in COMMERCIAL_UNIT_TYPES]
                    other_kind = "residential (Studio / 1BHK ... 5BHK)"
                if self.units.exclude(unit_type__in=allowed).exists():
                    errors["property_type"] = (
                        f"This building has {other_kind} units that do not fit the new type. "
                        "Delete or change those units first."
                    )

        # Year built / floors do not apply to these types: clear anything left over.
        if self.property_type in self.NO_BUILDING_INFO_TYPES:
            self.year_built = None
            self.total_floors = None

        # Total floors cannot be lower than a floor that already has units.
        if self.pk and self.has_units and self.total_floors:
            highest = max(
                (int(f) for f in self.units.values_list("floor", flat=True) if f.isdigit()),
                default=0,
            )
            if highest > self.total_floors:
                errors["total_floors"] = (
                    f"This building already has units on floor {highest}, "
                    "so total floors cannot be lower than that."
                )

        # Type and bathrooms are asked for apartments only.
        if self.property_type == self.PropertyType.APARTMENT:
            if not self.apartment_type:
                errors["apartment_type"] = "Choose the apartment type."
        else:
            self.apartment_type = ""
            self.bathrooms = None

        # Annual rent and status belong to standalone properties only.
        if self.has_units:
            self.annual_rent = None
            self.status = ""
        elif not self.status:
            errors["status"] = "Please choose a status."

        # Inactive owners cannot be chosen for a property (an existing property may keep its owner).
        if self.owner_id and not self.owner.is_active and _changed_in_db(self, "owner_id"):
            errors["owner"] = "This owner is inactive. Choose an active owner."

        # Management fee: none for company-owned, required for properties managed for others.
        if self.owner_id:
            if self.owner.is_company:
                self.management_fee_type = ""
                self.management_fee_percent = 0
                self.management_fee_fixed = None
            elif self.management_fee_type == self.FeeType.PERCENT:
                self.management_fee_fixed = None
                if not self.management_fee_percent:
                    errors["management_fee_percent"] = "Enter a fee greater than 0."
            elif self.management_fee_type == self.FeeType.FIXED:
                self.management_fee_percent = 0
                if not self.management_fee_fixed:
                    errors["management_fee_fixed"] = "Enter an amount greater than 0."
            else:
                errors["management_fee_type"] = (
                    "Choose how the owner is charged: a percentage or a fixed amount."
                )

        if errors:
            raise ValidationError(errors)


class Unit(models.Model):
    """A rentable unit inside a building: apartment, office, shop..."""

    UnitType = UnitType  # so the type list can also be reached as Unit.UnitType

    class Status(models.TextChoices):
        VACANT = "vacant", "Vacant"
        OCCUPIED = "occupied", "Occupied"
        RESERVED = "reserved", "Reserved"
        MAINTENANCE = "maintenance", "Under maintenance"

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
    unit_number = models.CharField(max_length=20)
    floor = models.CharField(
        max_length=4,
        default="",
        validators=[RegexValidator(FLOOR_REGEX, FLOOR_ERROR)],
        help_text=FLOOR_HELP,
    )
    # Hidden helper used only to sort units in floor order (G, 1, 2 ... 10, not 1, 10, 2).
    floor_sort = models.IntegerField(default=0, editable=False)
    unit_type = models.CharField(max_length=20, choices=UnitType.choices)
    bathrooms = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=BATHROOM_VALIDATORS,
        help_text="A number from 1 to 10.",
    )
    area_sqft = models.DecimalField(
        "Area (sq ft)", max_digits=10, decimal_places=2, null=True, blank=True,
        validators=AREA_VALIDATORS,
    )
    furnished = models.BooleanField(default=False)
    # Chosen by hand for now: once leases exist, Occupied / Vacant will follow the lease.
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.VACANT,
        help_text="Chosen by hand for now. Once leases are added, Occupied and Vacant will "
        "follow the lease automatically.",
    )
    asking_rent = models.DecimalField(
        "Asking rent (AED / year)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["property__code", "floor_sort", "unit_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "unit_number"], name="unique_unit_number_per_property"
            )
        ]

    def __str__(self):
        return f"{self.property.code}-{self.unit_number}"

    def save(self, *args, **kwargs):
        self.unit_number = normalise_unit_number(self.unit_number)
        self.floor = (self.floor or "").strip().upper()
        self.floor_sort = floor_sort_key(self.floor)
        super().save(*args, **kwargs)

    def clean(self):
        # "g01 " and "G01" are the same unit number: store it trimmed and in capitals.
        self.unit_number = normalise_unit_number(self.unit_number)
        self.floor = (self.floor or "").strip().upper()
        if not self.property_id:
            return
        prop = self.property
        errors = {}
        if not prop.has_units:
            errors["property"] = "Units can only be added to Residential or Commercial Buildings."
        else:
            # Inactive buildings take no new units (existing units stay editable).
            if not prop.is_active and _changed_in_db(self, "property_id"):
                errors[NON_FIELD_ERRORS] = "This property is inactive, so units cannot be added to it."

            if self.unit_type:
                is_residential = prop.property_type == Property.PropertyType.RESIDENTIAL_BUILDING
                if is_residential and self.unit_type not in RESIDENTIAL_UNIT_TYPES:
                    errors["unit_type"] = (
                        "A residential building can only have Studio, 1BHK to 5BHK units."
                    )
                elif not is_residential and self.unit_type not in COMMERCIAL_UNIT_TYPES:
                    errors["unit_type"] = "A commercial building can only have Office or Shop units."

            # A numbered floor cannot be higher than the building's total floors.
            if prop.total_floors and self.floor.isdigit() and int(self.floor) > prop.total_floors:
                errors["floor"] = (
                    f"This building has {prop.total_floors} floors, "
                    f"so floor {self.floor} does not exist."
                )
        if errors:
            raise ValidationError(errors)
