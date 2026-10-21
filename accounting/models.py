"""The general ledger: chart of accounts, fiscal periods, journal entries.

Rules enforced here (and again by database triggers, see triggers.py):

* an entry must have at least two lines and debits must equal credits;
* a posted entry and its lines can never be changed or deleted
  (mistakes are fixed with a reversing entry);
* nothing can be posted into a locked period, a missing period, or a date
  before the cutover date;
* a line on a tenant / owner / vendor control account must name that party,
  so the sub-ledger always adds up to the control account.
"""

import calendar
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from .posting_roles import ROLE_CHOICES, ROLE_DEFAULTS

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


class ImmutableLedgerError(ValidationError):
    """Raised when something tries to change or delete posted accounting data."""


def money(value, label="Amount"):
    """Turn a value into a Decimal with at most 2 decimal places."""
    if value is None or value == "":
        return ZERO
    try:
        amount = Decimal(str(value))
    except Exception:
        raise ValidationError(f"{label} is not a valid number.")
    if not amount.is_finite():
        raise ValidationError(f"{label} is not a valid number.")
    if amount != amount.quantize(CENT):
        raise ValidationError(f"{label} {amount} has more than 2 decimal places.")
    return amount.quantize(CENT)


# --- settings -------------------------------------------------------------


class AccountingSettings(models.Model):
    """One row: the company's legal details and the cutover date."""

    legal_name = models.CharField("company legal name", max_length=255, blank=True)
    trn = models.CharField(
        "Tax Registration Number (TRN)",
        max_length=15,
        blank=True,
        validators=[RegexValidator(r"^\d{15}$", "A UAE TRN has exactly 15 digits.")],
        help_text="15 digits. Printed on every tax invoice, credit note and voucher.",
    )
    address = models.TextField("company address", blank=True)
    cutover_date = models.DateField(
        "cutover date",
        null=True,
        blank=True,
        help_text="The day the ledger starts. Nothing can be posted before this date. "
        "Opening balances are posted on this date.",
    )
    separate_approver = models.BooleanField(
        "two people for manual journals",
        default=False,
        help_text="Tick when you have more than one accountant: a manual journal or a reversal "
        "must then be done by someone other than the person who made the original. "
        "Leave it unticked if you work alone.",
    )

    class Meta:
        verbose_name = "accounting settings"
        verbose_name_plural = "accounting settings"

    def __str__(self):
        return "Accounting settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("The accounting settings cannot be deleted.")


# --- chart of accounts ----------------------------------------------------


class Account(models.Model):
    """One account in the chart of accounts. The chart is data: add or
    rename accounts in the admin, no code change needed."""

    class Type(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    class Control(models.TextChoices):
        NONE = "", "Not a control account"
        TENANT = "tenant", "Tenant sub-ledger"
        OWNER = "owner", "Owner sub-ledger"
        VENDOR = "vendor", "Vendor sub-ledger"

    class Fund(models.TextChoices):
        NONE = "", "Not a bank account"
        OPERATING = "operating", "Operating (the company's own money)"
        TRUST = "trust", "Owner trust (owners' money)"

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=10, choices=Type.choices)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        limit_choices_to={"is_header": True},
        help_text="The group heading this account sits under (for reports).",
    )
    is_header = models.BooleanField(
        "group heading",
        default=False,
        help_text="A heading only groups other accounts. Nothing can be posted to it.",
    )
    control_kind = models.CharField(
        "control account for",
        max_length=10,
        choices=Control.choices,
        blank=True,
        default="",
        help_text="Tenant, owner and vendor control accounts keep a sub-ledger: every line "
        "posted to them must name the tenant, owner or vendor.",
    )
    is_bank = models.BooleanField("bank account", default=False)
    fund = models.CharField(
        "whose money",
        max_length=10,
        choices=Fund.choices,
        blank=True,
        default="",
        help_text="For bank accounts: the company's own money, or owners' money held in trust.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def is_debit_normal(self):
        return self.account_type in (self.Type.ASSET, self.Type.EXPENSE)

    def signed(self, debit, credit):
        """A balance in this account's natural direction (positive = normal side)."""
        return (debit - credit) if self.is_debit_normal else (credit - debit)

    def clean(self):
        # Plain (non-field) errors on purpose: the admin hides some fields.
        if self.is_header and (self.control_kind or self.is_bank):
            raise ValidationError("A group heading cannot be a control or bank account.")
        if self.control_kind and self.is_bank:
            raise ValidationError("A bank account cannot also be a control account.")
        if self.fund and not self.is_bank:
            raise ValidationError("Only a bank account can be marked as operating or trust money.")
        if self.is_bank and not self.fund:
            raise ValidationError(
                "Say whose money this bank account holds: the company's own (operating) or owners' (trust)."
            )
        if self.control_kind and self.account_type not in (self.Type.ASSET, self.Type.LIABILITY):
            raise ValidationError("A control account must be an asset or a liability.")
        if self.is_bank and self.account_type != self.Type.ASSET:
            raise ValidationError("A bank account must be an asset.")
        if self.parent_id:
            parent = self.parent
            if not parent.is_header:
                raise ValidationError("The parent must be a group heading.")
            if parent.account_type != self.account_type:
                raise ValidationError("The parent heading must be of the same type.")
            seen = set()
            node = parent
            while node is not None:
                if node.pk == self.pk and self.pk is not None:
                    raise ValidationError("An account cannot sit under itself.")
                if node.pk in seen:
                    break
                seen.add(node.pk)
                node = node.parent

    def save(self, *args, **kwargs):
        if self.pk:
            old = Account.objects.filter(pk=self.pk).first()
            if old and JournalLine.objects.filter(account_id=self.pk).exists():
                for field in ("account_type", "control_kind", "is_header", "is_bank", "fund"):
                    if getattr(old, field) != getattr(self, field):
                        raise ValidationError(
                            "This account already has postings, so its type, control, "
                            "heading and bank settings can no longer be changed."
                        )
        super().save(*args, **kwargs)


# --- fiscal periods -------------------------------------------------------


class FiscalPeriod(models.Model):
    """A month of the financial year (which ends on 31 December).
    A locked period refuses all postings."""

    name = models.CharField(max_length=50, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_locked = models.BooleanField(default=False)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["start_date"]
        permissions = [
            ("lock_fiscalperiod", "Can lock (close) fiscal periods"),
            ("unlock_fiscalperiod", "Can unlock (re-open) fiscal periods"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")), name="period_end_after_start"
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def fiscal_year(self):
        return self.start_date.year

    def clean(self):
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValidationError("The end date cannot be before the start date.")
            overlap = FiscalPeriod.objects.filter(
                start_date__lte=self.end_date, end_date__gte=self.start_date
            ).exclude(pk=self.pk)
            if overlap.exists():
                raise ValidationError(f"These dates overlap the period {overlap.first().name}.")

    @classmethod
    def for_date(cls, day):
        return cls.objects.filter(start_date__lte=day, end_date__gte=day).first()

    @classmethod
    def ensure_year(cls, year):
        """Create the 12 monthly periods of a year that do not exist yet.
        Returns how many were created."""
        if not 2000 <= year <= 2100:
            raise ValidationError("Choose a year between 2000 and 2100.")
        created = 0
        for month in range(1, 13):
            last = calendar.monthrange(year, month)[1]
            start, end = date(year, month, 1), date(year, month, last)
            if cls.objects.filter(start_date__lte=end, end_date__gte=start).exists():
                continue
            cls.objects.create(name=f"{year}-{month:02d}", start_date=start, end_date=end)
            created += 1
        return created

    def lock(self, user=None):
        if self.is_locked:
            return False
        with transaction.atomic():
            self.is_locked = True
            self.locked_at = timezone.now()
            self.locked_by = user
            self.save(update_fields=["is_locked", "locked_at", "locked_by"])
            AuditLog.record("lock", user=user, detail=f"Locked period {self.name}.")
        return True

    def unlock(self, user=None, reason=""):
        if not self.is_locked:
            return False
        with transaction.atomic():
            self.is_locked = False
            self.locked_at = None
            self.locked_by = None
            self.save(update_fields=["is_locked", "locked_at", "locked_by"])
            AuditLog.record(
                "unlock", user=user, detail=f"Re-opened period {self.name}. Reason: {reason or 'not given'}"
            )
        return True


# --- numbering ------------------------------------------------------------


class NumberSequence(models.Model):
    """Gap-free counters, one per prefix and year (for example JV-2026).

    The counter moves inside the same database transaction as the document
    that uses it, so a failed posting never leaves a hole in the numbers."""

    key = models.CharField(max_length=40, unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.key}: {self.last_number}"

    @classmethod
    def next(cls, prefix, year):
        key = f"{prefix}-{year}"
        with transaction.atomic():
            cls.objects.get_or_create(key=key)
            cls.objects.filter(key=key).update(last_number=F("last_number") + 1)
            number = cls.objects.get(key=key).last_number
        return f"{key}-{number:06d}"


# --- journal --------------------------------------------------------------


class _EntryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableLedgerError("Journal entries cannot be bulk-updated.")

    def delete(self):
        raise ImmutableLedgerError("Journal entries cannot be deleted. Post a reversing entry instead.")


class _LineQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableLedgerError("Journal lines cannot be bulk-updated.")

    def delete(self):
        raise ImmutableLedgerError("Journal lines cannot be deleted. Post a reversing entry instead.")


class JournalEntry(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual journal"
        OPENING = "opening", "Opening balances"
        SYSTEM = "system", "System posting"
        REVERSAL = "reversal", "Reversal"

    PREFIX = {
        Source.MANUAL: "JV",
        Source.OPENING: "OB",
        Source.SYSTEM: "SJ",
        Source.REVERSAL: "REV",
    }

    number = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    date = models.DateField()
    period = models.ForeignKey(
        FiscalPeriod, null=True, blank=True, on_delete=models.PROTECT, related_name="entries", editable=False
    )
    memo = models.CharField("narration", max_length=500)
    reference = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    # Which document created this entry (filled by the automatic postings).
    source_model = models.CharField(max_length=100, blank=True)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_tag = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    reverses = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversed_by_entry",
        editable=False,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    objects = _EntryQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "journal entry"
        verbose_name_plural = "journal entries"
        permissions = [
            ("post_journalentry", "Can post manual journal vouchers"),
            ("reverse_journalentry", "Can reverse posted journal entries"),
        ]
        indexes = [models.Index(fields=["source_model", "source_id", "source_tag"])]

    def __str__(self):
        return self.number or f"Draft entry {self.pk}"

    @property
    def is_posted(self):
        return self.status == self.Status.POSTED

    @property
    def is_reversed(self):
        return bool(self.pk) and JournalEntry.objects.filter(reverses_id=self.pk).exists()

    def totals(self):
        agg = self.lines.aggregate(d=models.Sum("debit"), c=models.Sum("credit"), n=models.Count("id"))
        return (agg["d"] or ZERO, agg["c"] or ZERO, agg["n"] or 0)

    def save(self, *args, **kwargs):
        if self.pk and JournalEntry.objects.filter(pk=self.pk, status=self.Status.POSTED).exists():
            raise ImmutableLedgerError(
                f"{self.number} is posted and cannot be changed. Post a reversing entry instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.POSTED or (
            self.pk and JournalEntry.objects.filter(pk=self.pk, status=self.Status.POSTED).exists()
        ):
            raise ImmutableLedgerError(
                f"{self.number} is posted and cannot be deleted. Post a reversing entry instead."
            )
        return super().delete(*args, **kwargs)

    def check_ready_to_post(self):
        """Raise ValidationError if this draft could not be posted (used to refuse a
        bad draft at once, even when someone else will do the posting later)."""
        lines = list(self.lines.select_related("account"))
        check_lines(
            [(ln.account, ln.debit, ln.credit, ln.tenant_id, ln.owner_id, ln.vendor_id) for ln in lines]
        )
        check_posting_date(self.date)

    def post(self, user=None, allow_inactive_accounts=False):
        """Check everything, give the entry its number and make it permanent."""
        with transaction.atomic():
            fresh = JournalEntry.objects.get(pk=self.pk)
            if fresh.status == self.Status.POSTED:
                raise ImmutableLedgerError(f"{fresh.number} is already posted.")
            lines = list(self.lines.select_related("account"))
            check_lines(
                [
                    (ln.account, ln.debit, ln.credit, ln.tenant_id, ln.owner_id, ln.vendor_id)
                    for ln in lines
                ],
                allow_inactive=allow_inactive_accounts,
            )
            period = check_posting_date(self.date)
            number = NumberSequence.next(self.PREFIX.get(self.source, "JV"), self.date.year)
            now = timezone.now()
            models.QuerySet.update(
                JournalEntry.objects.filter(pk=self.pk),
                number=number,
                period=period,
                status=self.Status.POSTED,
                posted_at=now,
            )
            self.number, self.period, self.status, self.posted_at = number, period, self.Status.POSTED, now
            AuditLog.record(
                "reverse" if self.source == self.Source.REVERSAL else "post",
                user=user,
                entry=self,
                detail=self.memo,
            )
        return self


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    # Sub-ledger party: required on a control account, forbidden elsewhere.
    tenant = models.ForeignKey(
        "realestate.Tenant", null=True, blank=True, on_delete=models.PROTECT, related_name="journal_lines"
    )
    owner = models.ForeignKey(
        "realestate.Owner", null=True, blank=True, on_delete=models.PROTECT, related_name="journal_lines"
    )
    vendor = models.ForeignKey(
        "realestate.Vendor", null=True, blank=True, on_delete=models.PROTECT, related_name="journal_lines"
    )

    objects = _LineQuerySet.as_manager()

    class Meta:
        ordering = ["entry_id", "id"]
        verbose_name = "journal line"
        constraints = [
            models.CheckConstraint(
                condition=(Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0)),
                name="line_one_side_only",
                violation_error_message="Enter either a debit or a credit amount (not both, and not zero).",
            ),
            models.CheckConstraint(
                condition=(
                    Q(tenant__isnull=True, owner__isnull=True)
                    | Q(tenant__isnull=True, vendor__isnull=True)
                    | Q(owner__isnull=True, vendor__isnull=True)
                ),
                name="line_one_party_only",
                violation_error_message="Choose only one of tenant, owner or vendor.",
            ),
        ]
        indexes = [
            models.Index(fields=["account", "entry"]),
            models.Index(fields=["tenant"]),
            models.Index(fields=["owner"]),
            models.Index(fields=["vendor"]),
        ]

    def __str__(self):
        return f"{self.account.code} Dr {self.debit} Cr {self.credit}"

    @property
    def party(self):
        return self.tenant or self.owner or self.vendor

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableLedgerError("A journal line cannot be changed once saved.")
        if JournalEntry.objects.filter(pk=self.entry_id, status=JournalEntry.Status.POSTED).exists():
            raise ImmutableLedgerError("Lines cannot be added to a posted journal entry.")
        check_lines(
            [(self.account, self.debit, self.credit, self.tenant_id, self.owner_id, self.vendor_id)],
            require_balance=False,
            allow_inactive=True,
        )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerError("A journal line cannot be deleted.")


class AuditLog(models.Model):
    """Who did what to the books. Rows are never changed or deleted."""

    when = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    action = models.CharField(max_length=20)
    entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-when", "-id"]
        verbose_name = "audit log entry"
        verbose_name_plural = "audit log"

    def __str__(self):
        return f"{self.when:%Y-%m-%d %H:%M} {self.action}"

    @classmethod
    def record(cls, action, user=None, entry=None, detail=""):
        return cls.objects.create(
            action=action,
            user=user if (user is not None and getattr(user, "pk", None)) else None,
            entry=entry,
            detail=detail,
        )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableLedgerError("The audit log cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerError("The audit log cannot be deleted.")


# --- which account each automatic posting uses ----------------------------


class AccountMapping(models.Model):
    """Points a job (for example "PDCs in hand") at an account of your chart."""

    key = models.CharField(max_length=30, unique=True, choices=ROLE_CHOICES, editable=False)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="mappings")

    class Meta:
        ordering = ["key"]
        verbose_name = "posting account"
        verbose_name_plural = "posting accounts"

    def __str__(self):
        return dict(ROLE_CHOICES).get(self.key, self.key)

    @property
    def label(self):
        return dict(ROLE_CHOICES).get(self.key, self.key)

    def clean(self):
        if not self.account_id:
            return
        account = self.account
        if account.is_header or not account.is_active:
            raise ValidationError(f"{self.label}: choose a real, active account.")
        if self.key == "bank_trust" and account.fund != Account.Fund.TRUST:
            raise ValidationError("The owner trust bank must be a bank account marked as owners' money (trust).")
        if self.key == "bank_operating" and account.fund != Account.Fund.OPERATING:
            raise ValidationError("The operating bank must be a bank account marked as the company's own money.")
        expected = ROLE_DEFAULTS.get(self.key, ("", "", ""))[2]
        if expected and account.control_kind != expected:
            raise ValidationError(f"{self.label} must be the {expected} control account.")


def posting_account(key):
    """The account for a job: your choice, else the starter-chart account."""
    mapped = AccountMapping.objects.filter(key=key).select_related("account").first()
    if mapped:
        account = mapped.account
    else:
        code = ROLE_DEFAULTS[key][0]
        account = Account.objects.filter(code=code).first()
    label = ROLE_DEFAULTS[key][1]
    if account is None:
        raise ValidationError(
            f"The account for '{label}' is not set up. Open Accounting > Accounting setup and create the starter chart."
        )
    expected = ROLE_DEFAULTS[key][2]
    if expected and account.control_kind != expected:
        raise ValidationError(f"The account for '{label}' must be the {expected} control account.")
    return account


def unmapped_roles():
    """Labels of the jobs whose account is missing or wrong (empty = all set)."""
    missing = []
    for key, (_code, label, _control) in ROLE_DEFAULTS.items():
        try:
            posting_account(key)
        except ValidationError:
            missing.append(label)
    return missing


def ensure_mappings():
    """Create the job -> account links that do not exist yet, using the starter
    chart's account codes. Returns how many were created."""
    made = 0
    for key, (code, _label, _control) in ROLE_DEFAULTS.items():
        if AccountMapping.objects.filter(key=key).exists():
            continue
        account = Account.objects.filter(code=code, is_header=False).first()
        if account:
            AccountMapping.objects.create(key=key, account=account)
            made += 1
    return made


# --- documents -------------------------------------------------------------


class LeaseInvoice(models.Model):
    """The master invoice for a whole lease (a tax invoice when VAT applies).

    A lease has one live invoice at a time. If it was a mistake it is cancelled
    (`is_void`) before any money has moved, and a new one is issued."""

    number = models.CharField(max_length=30, unique=True, editable=False)
    lease = models.ForeignKey("realestate.Lease", on_delete=models.PROTECT, related_name="invoices")
    date = models.DateField()
    rent_amount = models.DecimalField(max_digits=14, decimal_places=2)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="lease_invoice")
    is_opening = models.BooleanField(
        default=False,
        help_text="Brings a lease that started before the cutover date into the books. "
        "It is not a new tax invoice.",
    )
    is_void = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "lease invoice"

    def __str__(self):
        return self.number

    @property
    def is_tax_invoice(self):
        return self.vat_amount > 0 and not self.is_opening

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableLedgerError("An issued invoice cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerError("An issued invoice cannot be deleted.")


class CreditNote(models.Model):
    """Reduces a lease invoice, for example when the tenant leaves early."""

    number = models.CharField(max_length=30, unique=True, editable=False)
    invoice = models.ForeignKey(LeaseInvoice, on_delete=models.PROTECT, related_name="credit_notes")
    lease = models.ForeignKey("realestate.Lease", on_delete=models.PROTECT, related_name="credit_notes")
    date = models.DateField()
    rent_amount = models.DecimalField(max_digits=14, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True)
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="credit_note")
    is_void = models.BooleanField(default=False, help_text="Set automatically when the credit note is reversed.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "credit note"

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableLedgerError("A credit note cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerError("A credit note cannot be deleted.")


class ReceiptVoucher(models.Model):
    """Proof of a payment received from a tenant."""

    number = models.CharField(max_length=30, unique=True, editable=False)
    cheque = models.ForeignKey("realestate.Cheque", on_delete=models.PROTECT, related_name="receipt_vouchers")
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="receipt_voucher")
    date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_void = models.BooleanField(
        default=False, help_text="Set automatically when the receipt is reversed (for example a cheque handed back)."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "receipt voucher"

    def __str__(self):
        return self.number

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerError("A receipt voucher cannot be deleted.")


# --- checks shared by the engine and the admin ----------------------------


def check_posting_date(day):
    """Return the open fiscal period for a date, or raise a clear error."""
    cutover = AccountingSettings.load().cutover_date
    if cutover and day < cutover:
        raise ValidationError(
            f"{day:%d %b %Y} is before the cutover date ({cutover:%d %b %Y}). Nothing can be posted before it."
        )
    period = FiscalPeriod.for_date(day)
    if period is None:
        raise ValidationError(
            f"There is no fiscal period for {day:%d %b %Y}. Create the periods for that year first."
        )
    if period.is_locked:
        raise ValidationError(f"Period {period.name} is locked. Nothing can be posted into it.")
    return period


def check_lines(rows, require_balance=True, allow_inactive=False):
    """Validate lines given as (account, debit, credit, tenant_id, owner_id, vendor_id).

    Raises ValidationError with a plain-language message. Returns the totals."""
    total_debit = total_credit = ZERO
    for number, (account, debit, credit, tenant_id, owner_id, vendor_id) in enumerate(rows, start=1):
        where = f"Line {number} ({account.code} {account.name}): "
        debit = money(debit, where + "debit")
        credit = money(credit, where + "credit")
        if debit < 0 or credit < 0:
            raise ValidationError(where + "amounts cannot be negative.")
        if debit > 0 and credit > 0:
            raise ValidationError(where + "a line is either a debit or a credit, not both.")
        if debit == 0 and credit == 0:
            raise ValidationError(where + "enter a debit or a credit amount.")
        if account.is_header:
            raise ValidationError(where + "this is a group heading. Choose a real account.")
        if not account.is_active and not allow_inactive:
            raise ValidationError(where + "this account is inactive.")
        parties = [kind for kind, value in (("tenant", tenant_id), ("owner", owner_id), ("vendor", vendor_id)) if value]
        if len(parties) > 1:
            raise ValidationError(where + "choose only one of tenant, owner or vendor.")
        if account.control_kind:
            if parties != [account.control_kind]:
                raise ValidationError(
                    where + f"this is a control account, so the line must name the {account.control_kind}."
                )
        elif parties:
            raise ValidationError(
                where + "only tenant, owner and vendor control accounts take a tenant, owner or vendor."
            )
        total_debit += debit
        total_credit += credit
    if require_balance:
        if len(rows) < 2:
            raise ValidationError("A journal entry needs at least two lines.")
        if total_debit != total_credit:
            raise ValidationError(
                f"The entry does not balance: debits {total_debit:,.2f}, credits {total_credit:,.2f} "
                f"(difference {abs(total_debit - total_credit):,.2f})."
            )
    return total_debit, total_credit
