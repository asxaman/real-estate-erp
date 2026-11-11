"""Accounting: the chart of accounts, journal entries (manual and automatic) and their lines."""
import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Length
from django.utils import timezone

from properties.models import save_with_unique_code

ZERO = Decimal("0.00")


def _next_code(width=4):
    last = JournalEntry.objects.exclude(code__isnull=True).order_by(Length("code").desc(), "-code").values_list("code", flat=True).first()
    match = re.fullmatch(r"JV-(\d+)", last or "")
    return f"JV-{(int(match.group(1)) if match else 0) + 1:0{width}d}"


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    INCOME = "income", "Income"
    EXPENSE = "expense", "Expense"


DEBIT_TYPES = ("asset", "expense")  # these grow with debits; the others grow with credits

# code, name, type  (the system accounts: the automatic entries post to these codes)
DEFAULT_CHART = [
    ("1000", "Cash on hand", "asset"),
    ("1010", "Bank accounts", "asset"),
    ("1200", "VAT receivable (VAT paid on costs)", "asset"),
    ("1400", "Staff advances and loans", "asset"),
    ("2000", "Accounts payable (vendors)", "liability"),
    ("2100", "VAT payable (VAT charged to tenants)", "liability"),
    ("2200", "Owners' funds payable", "liability"),
    ("2300", "Tenant security deposits held", "liability"),
    ("2400", "Salaries payable", "liability"),
    ("2410", "End of service and leave payable", "liability"),
    ("3000", "Owner's equity / share capital", "equity"),
    ("3200", "Opening balance equity", "equity"),
    ("4000", "Rent income - company-owned properties", "income"),
    ("4010", "Rent income - guaranteed-rent properties", "income"),
    ("4100", "Management fee income", "income"),
    ("4200", "Office, agency and other fee income", "income"),
    ("4900", "Other income", "income"),
    ("5000", "Maintenance and repairs", "expense"),
    ("5100", "Utilities", "expense"),
    ("5200", "Government and municipality fees", "expense"),
    ("5300", "Insurance", "expense"),
    ("5400", "Cleaning, pest control and security", "expense"),
    ("5500", "Legal and professional fees", "expense"),
    ("5600", "Marketing and advertising", "expense"),
    ("5700", "Office and supplies", "expense"),
    ("5750", "Bank charges", "expense"),
    ("5800", "Guaranteed rent paid to owners", "expense"),
    ("5900", "Other operating expenses", "expense"),
    ("6000", "Salaries and wages", "expense"),
    ("6010", "End-of-service benefits", "expense"),
]
DEFAULT_NAMES = {code: (name, kind) for code, name, kind in DEFAULT_CHART}


class Account(models.Model):
    code = models.CharField("Account no.", max_length=10, unique=True)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=10, choices=AccountType.choices)
    is_system = models.BooleanField("Used by automatic entries", default=False, editable=False)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "account"
        verbose_name_plural = "Chart of accounts"
        permissions = [("export_account", "Can export the chart of accounts and the ledger to Excel / CSV")]

    def __str__(self):
        return f"{self.code} · {self.name}"

    @property
    def debit_normal(self):
        return self.type in DEBIT_TYPES

    def clean(self):
        self.code = (self.code or "").strip()
        self.name = " ".join((self.name or "").split())
        errors = {}
        if self.code and not re.fullmatch(r"[0-9]{3,10}", self.code):
            errors["code"] = "Use digits only, for example 5050."
        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            if old.is_system and (old.code != self.code or old.type != self.type):
                errors["code"] = "This account is used by the automatic entries. Its number and type cannot change (you can rename it)."
            if old.type != self.type and old.lines.exists():
                errors["type"] = "This account already has entries, so its type cannot change."
            if not self.is_active and old.is_system:
                errors["is_active"] = "This account is used by the automatic entries and must stay active."
        if errors:
            raise ValidationError(errors)


class JournalEntry(models.Model):
    class Status(models.TextChoices):
        POSTED = "posted", "Posted"
        VOIDED = "voided", "Voided"

    code = models.CharField("Entry no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    date = models.DateField(default=timezone.localdate)
    description = models.CharField(max_length=250)
    # Automatic entries say what they were made from ("leasepayment", 12 ...). Manual entries have no source.
    source_type = models.CharField(max_length=20, blank=True, editable=False)
    source_id = models.PositiveIntegerField(null=True, blank=True, editable=False)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.POSTED)
    void_reason = models.CharField("Reason for voiding", max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "journal entry"
        verbose_name_plural = "Journal entries"
        constraints = [models.UniqueConstraint(fields=["source_type", "source_id"], condition=~Q(source_type=""), name="one_entry_per_source")]
        indexes = [models.Index(fields=["date"], name="journal_date_idx")]
        permissions = [("export_journalentry", "Can export journal entries to Excel / CSV")]

    def __str__(self):
        return f"{self.code or 'New'} · {self.description}"

    @property
    def is_manual(self):
        return not self.source_type

    @property
    def total(self):
        return sum((l.debit for l in self.lines.all()), ZERO)

    def save(self, *args, **kwargs):
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, _next_code, lambda: super(JournalEntry, self).save(*args, **kwargs))

    def clean(self):
        errors = {}
        old = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if old is not None and old.status == self.Status.VOIDED and self.status != self.Status.VOIDED:
            errors["status"] = "A voided entry stays voided. Make a new entry."
        if self.status == self.Status.VOIDED:
            if not self.is_manual:
                errors["status"] = "An automatic entry follows the record it was made from. Cancel or void that record instead."
            elif not (self.void_reason or "").strip():
                errors["void_reason"] = "Write the reason for voiding this entry."
        else:
            self.void_reason = ""
        if errors:
            raise ValidationError(errors)


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=250, blank=True)
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(debit__gte=0) & Q(credit__gte=0), name="journal_line_not_negative"),
            models.CheckConstraint(condition=Q(debit=0) | Q(credit=0), name="journal_line_one_side"),
        ]

    def __str__(self):
        return f"{self.account.code} Dr {self.debit} Cr {self.credit}"

    def clean(self):
        debit, credit = self.debit or ZERO, self.credit or ZERO
        if debit < 0 or credit < 0:
            raise ValidationError("Amounts cannot be negative.")
        if debit and credit:
            raise ValidationError("A line is either a debit or a credit, not both.")
        if not debit and not credit:
            raise ValidationError("Enter a debit or a credit amount.")
