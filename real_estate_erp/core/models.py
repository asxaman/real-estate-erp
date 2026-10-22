from django.db import models


class CompanySettings(models.Model):
    """The management company's own profile. Only one row ever exists."""

    class Emirate(models.TextChoices):
        ABU_DHABI = "AD", "Abu Dhabi"
        DUBAI = "DU", "Dubai"
        SHARJAH = "SH", "Sharjah"
        AJMAN = "AJ", "Ajman"
        UMM_AL_QUWAIN = "UQ", "Umm Al Quwain"
        RAS_AL_KHAIMAH = "RK", "Ras Al Khaimah"
        FUJAIRAH = "FU", "Fujairah"

    class Month(models.IntegerChoices):
        JANUARY = 1, "January"
        FEBRUARY = 2, "February"
        MARCH = 3, "March"
        APRIL = 4, "April"
        MAY = 5, "May"
        JUNE = 6, "June"
        JULY = 7, "July"
        AUGUST = 8, "August"
        SEPTEMBER = 9, "September"
        OCTOBER = 10, "October"
        NOVEMBER = 11, "November"
        DECEMBER = 12, "December"

    company_name = models.CharField(max_length=150)
    legal_name = models.CharField(max_length=200, blank=True)
    trade_license_number = models.CharField(max_length=50, blank=True)
    tax_registration_number = models.CharField(
        "VAT registration number (TRN)", max_length=20, blank=True
    )
    vat_rate = models.DecimalField("VAT rate (%)", max_digits=5, decimal_places=2, default=5)

    address = models.CharField(max_length=255, blank=True)
    po_box = models.CharField("P.O. Box", max_length=30, blank=True)
    emirate = models.CharField(max_length=2, choices=Emirate.choices, default=Emirate.DUBAI)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    currency_code = models.CharField(max_length=3, default="AED")
    fiscal_year_start_month = models.PositiveSmallIntegerField(
        choices=Month.choices, default=Month.JANUARY
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "company settings"
        verbose_name_plural = "company settings"

    def __str__(self):
        return self.company_name

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.first()
