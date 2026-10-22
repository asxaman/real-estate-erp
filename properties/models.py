from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Owner(models.Model):
    """A property owner (individual or company) whose properties we manage."""

    name = models.CharField("Owner name", max_length=200)
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
        ordering = ["name"]

    def __str__(self):
        return self.name


class Property(models.Model):
    """A building, villa compound, land plot, etc."""

    class PropertyType(models.TextChoices):
        RESIDENTIAL = "residential", "Residential building"
        COMMERCIAL = "commercial", "Commercial building"
        VILLA_COMPOUND = "villa_compound", "Villa compound"
        MIXED_USE = "mixed_use", "Mixed use"
        LAND = "land", "Land"

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
        max_length=20, choices=PropertyType.choices, default=PropertyType.RESIDENTIAL
    )
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="properties")
    emirate = models.CharField(
        max_length=20, choices=Emirate.choices, default=Emirate.ABU_DHABI
    )
    area = models.CharField(
        "Area / community", max_length=120, blank=True, help_text="e.g. Al Reem Island"
    )
    address = models.TextField(blank=True)
    year_built = models.PositiveIntegerField(null=True, blank=True)
    total_floors = models.PositiveIntegerField(null=True, blank=True)
    management_fee_percent = models.DecimalField(
        "Management fee (%)",
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Percentage of collected rent charged to the owner.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "properties"

    def __str__(self):
        return f"{self.name} ({self.code})"


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
