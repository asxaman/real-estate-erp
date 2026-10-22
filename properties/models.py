from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.models import TimeStampedModel


class Owner(TimeStampedModel):
    """A person or company that owns one or more properties we manage."""

    class OwnerType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"

    name = models.CharField(max_length=200)
    owner_type = models.CharField(
        max_length=20, choices=OwnerType.choices, default=OwnerType.INDIVIDUAL
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    id_number = models.CharField(
        "Emirates ID / Trade licence no.", max_length=50, blank=True
    )
    nationality = models.CharField(max_length=60, blank=True)
    address = models.TextField(blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    iban = models.CharField("IBAN", max_length=40, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Property(TimeStampedModel):
    """A building, villa, compound or plot. Contains one or more units."""

    class PropertyType(models.TextChoices):
        BUILDING = "building", "Building"
        VILLA = "villa", "Villa"
        COMPOUND = "compound", "Villa Compound"
        COMMERCIAL = "commercial", "Commercial Building"
        LAND = "land", "Land"

    class Emirate(models.TextChoices):
        ABU_DHABI = "abu_dhabi", "Abu Dhabi"
        DUBAI = "dubai", "Dubai"
        SHARJAH = "sharjah", "Sharjah"
        AJMAN = "ajman", "Ajman"
        UAQ = "uaq", "Umm Al Quwain"
        RAK = "rak", "Ras Al Khaimah"
        FUJAIRAH = "fujairah", "Fujairah"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    code = models.CharField(
        max_length=20, unique=True, help_text="Short unique code, e.g. BLD-001"
    )
    name = models.CharField(max_length=200)
    property_type = models.CharField(
        max_length=20, choices=PropertyType.choices, default=PropertyType.BUILDING
    )
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="properties")
    emirate = models.CharField(
        max_length=20, choices=Emirate.choices, default=Emirate.ABU_DHABI
    )
    area = models.CharField(
        "Area / District", max_length=100, blank=True, help_text="e.g. Al Reem Island"
    )
    address = models.TextField(blank=True)
    plot_number = models.CharField(max_length=50, blank=True)
    title_deed_number = models.CharField(max_length=50, blank=True)
    year_built = models.PositiveIntegerField(null=True, blank=True)
    number_of_floors = models.PositiveIntegerField(null=True, blank=True)
    management_fee_percent = models.DecimalField(
        "Management fee (%)",
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentage of collected rent charged to the owner",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "properties"

    def __str__(self):
        return f"{self.code} - {self.name}"


class Unit(TimeStampedModel):
    """A rentable space inside a property: apartment, shop, office, room..."""

    class UnitType(models.TextChoices):
        STUDIO = "studio", "Studio"
        ONE_BR = "1br", "1 Bedroom"
        TWO_BR = "2br", "2 Bedrooms"
        THREE_BR = "3br", "3 Bedrooms"
        FOUR_PLUS_BR = "4br_plus", "4+ Bedrooms"
        VILLA = "villa", "Villa"
        OFFICE = "office", "Office"
        SHOP = "shop", "Shop"
        WAREHOUSE = "warehouse", "Warehouse"
        PARKING = "parking", "Parking"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        VACANT = "vacant", "Vacant"
        OCCUPIED = "occupied", "Occupied"
        MAINTENANCE = "maintenance", "Under Maintenance"

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="units"
    )
    unit_number = models.CharField(max_length=30)
    floor = models.CharField(max_length=20, blank=True)
    unit_type = models.CharField(
        max_length=20, choices=UnitType.choices, default=UnitType.ONE_BR
    )
    bedrooms = models.PositiveIntegerField(default=0)
    bathrooms = models.PositiveIntegerField(default=0)
    size_sqft = models.DecimalField(
        "Size (sq ft)", max_digits=10, decimal_places=2, null=True, blank=True
    )
    asking_annual_rent = models.DecimalField(
        "Asking annual rent (AED)", max_digits=12, decimal_places=2, default=0
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.VACANT
    )
    furnished = models.BooleanField(default=False)
    electricity_account = models.CharField(
        "Electricity / water account no.", max_length=50, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["property__name", "unit_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "unit_number"], name="unique_unit_per_property"
            )
        ]

    def __str__(self):
        return f"{self.property.name} - Unit {self.unit_number}"
