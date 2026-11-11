"""Owner agreements: the management contract between the company and a property owner."""
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from core import sysconf
from core.validators import validate_file_content
from properties.models import Owner, Property, save_with_unique_code, validate_document_size


def agreement_document_path(instance, filename):
    return f"agreement_documents/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def next_agreement_code():
    numbers = []
    for code in OwnerAgreement.objects.exclude(code__isnull=True).values_list("code", flat=True):
        match = re.fullmatch(r"AGR-(\d+)", code)
        if match:
            numbers.append(int(match.group(1)))
    return f"AGR-{max(numbers, default=0) + 1:04d}"


PERCENT = [MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal("100"))]
AMOUNT = [MinValueValidator(Decimal("0.01"))]


def _amount(value):
    text = f"{value:,.2f}"
    return text[:-3] if text.endswith(".00") else text


def _percent(value):
    return f"{value.normalize():f}" if value == value.to_integral() else f"{value:f}".rstrip("0").rstrip(".")


class OwnerAgreement(models.Model):
    """One contract with one owner. It covers one or more of that owner's properties (the lines)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        TERMINATED = "terminated", "Terminated"

    class Payout(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Every 3 months"
        HALF_YEARLY = "half_yearly", "Every 6 months"
        YEARLY = "yearly", "Once a year"
        AS_COLLECTED = "as_collected", "As each rent is collected"

    # Assigned automatically (AGR-0001, AGR-0002 ...).
    code = models.CharField("Agreement no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="agreements")
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ACTIVE,
        help_text="Draft: still being agreed, nothing counts yet. Active: signed and in force. "
        "Terminated: ended early (fill in the Ending Early tab).",
    )
    signed_on = models.DateField("Signed on", null=True, blank=True)
    start_date = models.DateField("Starts on")
    end_date = models.DateField(
        "Ends on", null=True, blank=True, help_text="Leave empty when the agreement has no end date.",
    )
    auto_renew = models.BooleanField(
        "Renews automatically", default=False,
        help_text="Tick when the agreement carries on after its end date unless one side gives notice.",
    )
    notice_days = models.PositiveSmallIntegerField(
        "Notice period (days)", null=True, blank=True, validators=[MaxValueValidator(730)],
        help_text="How many days of notice either side must give to end the agreement.",
    )

    # ---- what the company earns ---------------------------------------------------------
    fee_percent_collected = models.DecimalField(
        "% of rent collected", max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENT,
        help_text="For example 5 = the company keeps 5% of every rent payment received.",
    )
    fee_fixed_yearly = models.DecimalField(
        "Fixed fee per year (AED)", max_digits=12, decimal_places=2, null=True, blank=True, validators=AMOUNT,
        help_text="A set yearly management fee for everything this agreement covers, whatever the rent.",
    )
    fee_percent_contract = models.DecimalField(
        "% of the annual contract rent", max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENT,
        help_text="Charged once when a lease is signed or renewed, on that lease's yearly rent.",
    )
    guaranteed_rent_yearly = models.DecimalField(
        "Guaranteed rent to the owner per year (AED)", max_digits=14, decimal_places=2, null=True, blank=True,
        validators=AMOUNT,
        help_text="The company pays the owner this fixed amount and keeps whatever it collects above it. "
        "Cannot be combined with the other fees.",
    )
    payout_frequency = models.CharField(
        "Owner is paid", max_length=14, choices=Payout.choices, default=Payout.MONTHLY,
        help_text="How often the owner receives the money due to them.",
    )

    # ---- ending early -------------------------------------------------------------------
    terminated_on = models.DateField("Terminated on", null=True, blank=True)
    termination_reason = models.CharField("Reason", max_length=200, blank=True)

    document = models.FileField(
        "Signed agreement",
        upload_to=agreement_document_path,
        blank=True,
        validators=[
            FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "webp"]),
            validate_document_size,
            validate_file_content,
        ],
        help_text="A scan of the signed agreement (PDF, JPG, PNG or WebP).",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        verbose_name = "owner agreement"
        verbose_name_plural = "Owner agreements"
        permissions = [("export_owneragreement", "Can export owner agreements to Excel / CSV")]

    def __str__(self):
        return f"{self.code or 'New agreement'} · {self.owner.name}" if self.owner_id else (self.code or "New agreement")

    def save(self, *args, **kwargs):
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(
                self, lambda: next_agreement_code(), lambda: super(OwnerAgreement, self).save(*args, **kwargs)
            )

    # ---- dates ----------------------------------------------------------------------------
    def effective_end(self):
        """The last day the agreement is in force, or None when it has no end (open-ended or renewing)."""
        if self.status == self.Status.TERMINATED and self.terminated_on:
            return self.terminated_on
        if self.auto_renew:
            return None
        return self.end_date

    def state(self, today=None):
        """'draft', 'upcoming', 'active', 'expired' or 'terminated'."""
        today = today or timezone.localdate()
        if self.status == self.Status.DRAFT:
            return "draft"
        if self.status == self.Status.TERMINATED:
            return "terminated"
        if self.start_date > today:
            return "upcoming"
        end = self.effective_end()
        if end is not None and end < today:
            return "expired"
        return "active"

    def ends_soon(self, today=None):
        today = today or timezone.localdate()
        end = self.effective_end()
        return self.state(today) == "active" and end is not None and end <= today + timedelta(days=sysconf.expiry_days())

    def covers(self, day):
        """Is the agreement in force on `day`? (Drafts never are.)"""
        if self.status == self.Status.DRAFT or self.start_date is None or self.start_date > day:
            return False
        end = self.effective_end()
        return end is None or end >= day

    def overlaps(self, other):
        """Do the two agreements have at least one day in common?"""
        mine, theirs = self.effective_end(), other.effective_end()
        return (theirs is None or self.start_date <= theirs) and (mine is None or other.start_date <= mine)

    # ---- fees -----------------------------------------------------------------------------
    @property
    def is_guaranteed_rent(self):
        return self.guaranteed_rent_yearly is not None

    def fee_parts(self):
        parts = []
        if self.guaranteed_rent_yearly is not None:
            parts.append(f"Guaranteed rent AED {_amount(self.guaranteed_rent_yearly)} / year")
        if self.fee_percent_collected is not None:
            parts.append(f"{_percent(self.fee_percent_collected)}% of rent collected")
        if self.fee_fixed_yearly is not None:
            parts.append(f"AED {_amount(self.fee_fixed_yearly)} / year")
        if self.fee_percent_contract is not None:
            parts.append(f"{_percent(self.fee_percent_contract)}% of contract rent")
        return parts

    def fee_text(self):
        return " + ".join(self.fee_parts()) or "-"

    # ---- rules ----------------------------------------------------------------------------
    def clean(self):
        errors = {}
        self.termination_reason = " ".join((self.termination_reason or "").split())
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            errors["end_date"] = "The end date must be after the start date."
        if self.auto_renew and not self.end_date:
            errors["end_date"] = "An agreement that renews automatically needs an end date (the date it first renews)."
        if self.signed_on and self.signed_on > timezone.localdate():
            errors["signed_on"] = "The signing date cannot be in the future."

        fees = [self.fee_percent_collected, self.fee_fixed_yearly, self.fee_percent_contract]
        if self.guaranteed_rent_yearly is not None and any(value is not None for value in fees):
            errors["guaranteed_rent_yearly"] = (
                "With a guaranteed rent the company keeps whatever it collects above it, so leave the other fees empty."
            )
        elif self.guaranteed_rent_yearly is None and all(value is None for value in fees):
            errors["fee_percent_collected"] = "Fill in at least one fee (or the guaranteed rent), on the Fees tab."

        if self.status == self.Status.TERMINATED:
            if not self.terminated_on:
                errors["terminated_on"] = "Say on which date the agreement was terminated."
            elif self.start_date and self.terminated_on < self.start_date:
                errors["terminated_on"] = "This cannot be before the agreement started."
            if not self.termination_reason:
                errors["termination_reason"] = "Say why the agreement was terminated."
        else:
            self.terminated_on = None
            self.termination_reason = ""

        if self.owner_id:
            if self.owner.is_company:
                errors["owner"] = "Properties the company owns itself do not need an owner agreement."
            elif self._state.adding and not self.owner.is_active:
                errors["owner"] = "This owner is inactive. Make the owner active first."
            if self.pk:
                strangers = self.lines.exclude(property__owner_id=self.owner_id).count()
                if strangers:
                    errors["owner"] = (
                        f"This agreement covers {strangers} propert{'y' if strangers == 1 else 'ies'} of another owner. "
                        "Remove them from the Properties Covered tab first."
                    )
        if errors:
            raise ValidationError(errors)

    def conflict_for(self, prop):
        """Another agreement (not a draft) that covers `prop` in the same period, or None."""
        if self.status == self.Status.DRAFT or not self.start_date:
            return None
        others = OwnerAgreement.objects.filter(lines__property=prop).exclude(status=self.Status.DRAFT)
        if self.pk:
            others = others.exclude(pk=self.pk)
        for other in others:
            if self.overlaps(other):
                return other
        return None


@receiver(post_delete, sender=OwnerAgreement)
def remove_agreement_document(sender, instance, **kwargs):
    if instance.document:
        instance.document.delete(save=False)


class AgreementProperty(models.Model):
    """One property covered by an agreement."""

    agreement = models.ForeignKey(OwnerAgreement, on_delete=models.CASCADE, related_name="lines")
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="agreement_lines")

    class Meta:
        ordering = ["property__name"]
        verbose_name = "property covered"
        verbose_name_plural = "Properties covered"
        constraints = [
            models.UniqueConstraint(
                fields=["agreement", "property"], name="unique_agreement_property",
                violation_error_message="This property is already on the agreement.",
            ),
        ]

    def __str__(self):
        return self.property.name if self.property_id else "Property"

    def clean(self):
        if not self.property_id:
            return
        try:
            agreement = self.agreement
        except OwnerAgreement.DoesNotExist:
            return
        if agreement.owner_id and self.property.owner_id != agreement.owner_id:
            raise ValidationError({
                "property": f"{self.property.name} belongs to {self.property.owner.name}, not to the owner of this agreement."
            })
        other = agreement.conflict_for(self.property)
        if other is not None:
            raise ValidationError({
                "property": f"{self.property.name} is already covered by agreement {other.code} in the same period. "
                "End that agreement first, or change the dates."
            })


def agreement_for(prop, day=None):
    """The agreement that covers `prop` on `day` (today when not given), or None."""
    day = day or timezone.localdate()
    found = (
        OwnerAgreement.objects.filter(lines__property=prop, start_date__lte=day)
        .exclude(status=OwnerAgreement.Status.DRAFT)
        .order_by("-start_date", "-id")
    )
    for agreement in found:
        if agreement.covers(day):
            return agreement
    return None


def running_q(today):
    """Agreements in force on `today`."""
    return (
        Q(start_date__lte=today)
        & (
            Q(status=OwnerAgreement.Status.ACTIVE) & (Q(auto_renew=True) | Q(end_date__isnull=True) | Q(end_date__gte=today))
            | Q(status=OwnerAgreement.Status.TERMINATED, terminated_on__gte=today)
        )
    )
