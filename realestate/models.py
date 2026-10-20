from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Owner(models.Model):
    """A landlord whose properties we manage for a fee."""

    name = models.CharField("full name", max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    bank_name = models.CharField(max_length=255, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
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


class Property(models.Model):
    """A building, villa, etc. Either company-owned or owned by a third party."""

    class Ownership(models.TextChoices):
        COMPANY = "company", "Company-owned"
        THIRD_PARTY = "third_party", "Third-party"

    class PropertyType(models.TextChoices):
        BUILDING = "building", "Building / Tower"
        VILLA = "villa", "Villa"
        COMMERCIAL = "commercial", "Commercial center"
        LAND = "land", "Land"

    name = models.CharField("property name", max_length=255)
    property_type = models.CharField(
        max_length=20,
        choices=PropertyType.choices,
        default=PropertyType.BUILDING,
    )
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=255, blank=True)

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
        if self.ownership_type == self.Ownership.THIRD_PARTY and not self.owner_id:
            raise ValidationError(
                {"owner": "Choose the owner for a third-party property."}
            )

    def save(self, *args, **kwargs):
        # Company-owned properties never have an owner or a fee.
        if self.ownership_type == self.Ownership.COMPANY:
            self.owner = None
            self.management_fee_percent = None
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
        OCCUPIED = "occupied", "Occupied"
        MAINTENANCE = "maintenance", "Under maintenance"

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
    size_sqft = models.DecimalField(
        "size (sq ft)", max_digits=10, decimal_places=2, null=True, blank=True
    )
    rent_amount = models.DecimalField(
        "default annual rent (AED)",
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.VACANT,
    )
    notes = models.TextField("internal notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
