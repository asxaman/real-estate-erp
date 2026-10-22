from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .models_constants import COMPANY_OWNER_NAME


class Owner(models.Model):
    """A property owner (individual or company) whose properties we manage."""

    name = models.CharField("Owner name", max_length=200)
    # The one built-in owner that stands for "our own company". It is created
    # automatically, cannot be renamed or deleted, and properties owned by it
    # have no management fee.
    is_company = models.BooleanField(default=False, editable=False)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
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

    @classmethod
    def company(cls):
        """Return the built-in "Company Owned" owner, creating it if it is missing."""
        owner = cls.objects.filter(is_company=True).first()
        if owner is None:
            owner = cls.objects.create(name=COMPANY_OWNER_NAME, is_company=True)
        return owner


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
        help_text="Short unique code, e.g. RMT. Used in unit references like RMT-101.",
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
    year_built = models.PositiveIntegerField(null=True, blank=True)
    total_floors = models.PositiveIntegerField(null=True, blank=True)

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
        max_length=20, choices=Status.choices, blank=True, default=Status.VACANT
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

        # Don't allow switching a building that already has units to a standalone type.
        if self.pk and not self.has_units and self.units.exists():
            errors["property_type"] = (
                "This property still has units. Delete its units "
                "before changing it to a standalone type."
            )

        # Year built / floors do not apply to these types: clear anything left over.
        if self.property_type in self.NO_BUILDING_INFO_TYPES:
            self.year_built = None
            self.total_floors = None

        # Annual rent and status belong to standalone properties only.
        if self.has_units:
            self.annual_rent = None
            self.status = ""
        elif not self.status:
            errors["status"] = "Please choose a status."

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
    """A rentable unit inside a property: apartment, villa, office, shop..."""

    class UnitType(models.TextChoices):
        STUDIO = "studio", "Studio"
        ONE_BR = "1br", "1 bedroom"
        TWO_BR = "2br", "2 bedrooms"
        THREE_BR = "3br", "3 bedrooms"
        FOUR_BR = "4br", "4+ bedrooms"
        VILLA = "villa", "Villa"
        OFFICE = "office", "Office"
        SHOP = "shop", "Shop"
        WAREHOUSE = "warehouse", "Warehouse"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        VACANT = "vacant", "Vacant"
        OCCUPIED = "occupied", "Occupied"
        RESERVED = "reserved", "Reserved"
        MAINTENANCE = "maintenance", "Under maintenance"

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
    unit_number = models.CharField(max_length=20)
    floor = models.IntegerField(null=True, blank=True, help_text="0 = ground floor")
    unit_type = models.CharField(max_length=20, choices=UnitType.choices, default=UnitType.ONE_BR)
    bedrooms = models.PositiveIntegerField(default=0)
    bathrooms = models.PositiveIntegerField(default=1)
    area_sqft = models.DecimalField(
        "Area (sq ft)", max_digits=10, decimal_places=2, null=True, blank=True
    )
    furnished = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.VACANT)
    asking_rent = models.DecimalField(
        "Asking rent (AED / year)",
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["property__code", "floor", "unit_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "unit_number"], name="unique_unit_number_per_property"
            )
        ]

    def __str__(self):
        return f"{self.property.code}-{self.unit_number}"

    def clean(self):
        if self.property_id and not self.property.has_units:
            raise ValidationError(
                {"property": "Units can only be added to Residential or Commercial Buildings."}
            )
