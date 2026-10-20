from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Case, CharField, Exists, OuterRef, Value, When
from django.utils import timezone


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


class Tenant(models.Model):
    """A person or company renting a unit from us."""

    class TenantType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"

    name = models.CharField("full name / company name", max_length=255)
    tenant_type = models.CharField(
        max_length=20,
        choices=TenantType.choices,
        default=TenantType.INDIVIDUAL,
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    nationality = models.CharField(max_length=100, blank=True)
    id_number = models.CharField(
        "ID / licence number",
        max_length=100,
        blank=True,
        help_text="Emirates ID or passport number for individuals, trade licence number for companies.",
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


class Lease(models.Model):
    """A rental contract: one tenant renting one unit for a period."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        TERMINATED = "terminated", "Terminated early"

    PAYMENT_CHOICES = [
        (1, "1 payment (full year)"),
        (2, "2 payments"),
        (3, "3 payments"),
        (4, "4 payments (quarterly)"),
        (6, "6 payments"),
        (12, "12 payments (monthly)"),
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
    ejari_number = models.CharField(
        "Ejari / contract number", max_length=100, blank=True
    )
    start_date = models.DateField()
    end_date = models.DateField()
    annual_rent = models.DecimalField(
        "annual rent (AED)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    security_deposit = models.DecimalField(
        "security deposit (AED)",
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

        if errors:
            raise ValidationError(errors)
