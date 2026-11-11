"""HR & Payroll: employees and their documents, leave, daily attendance, and monthly payroll with salary slips."""
import calendar
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Length
from django.utils import timezone

from core import countries, sysconf
from core.validators import validate_file_content
from payables.models import PaymentVoucher
from properties.models import save_with_unique_code, validate_document_size
from properties.models_constants import (
    PHONE_ERROR,
    PHONE_REGEX,
    emirates_id_is_valid,
    iban_is_valid,
    normalise_emirates_id,
    normalise_iban,
)

D = Decimal
FILE_TYPES = ["pdf", "jpg", "jpeg", "png", "webp"]
MONEY = [MinValueValidator(D("0"))]
SALARY_DAYS = 30  # a month's pay is always worked out as 30 days (UAE practice)
WEEKLY_OFF = {"sat_sun": (5, 6), "fri_sat": (4, 5), "sun": (6,)}  # Monday is 0


def cents(value):
    return D(value).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def month_start(day):
    return day.replace(day=1)


def month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def each_day(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def employee_document_path(instance, filename):
    return f"employee_documents/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def leave_file_path(instance, filename):
    return f"leave_files/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def _next_code(model, prefix, width=4):
    import re

    last = model.objects.exclude(code__isnull=True).order_by(Length("code").desc(), "-code").values_list("code", flat=True).first()
    match = re.fullmatch(rf"{prefix}-(\d+)", last or "")
    return f"{prefix}-{(int(match.group(1)) if match else 0) + 1:0{width}d}"


# ---------------------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------------------
class Employee(models.Model):
    class Department(models.TextChoices):
        MANAGEMENT = "management", "Management"
        LEASING = "leasing", "Leasing & sales"
        ACCOUNTS = "accounts", "Accounts"
        MAINTENANCE = "maintenance", "Maintenance & technical"
        ADMIN = "admin", "Administration"
        SECURITY = "security", "Security & facilities"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TERMINATED = "terminated", "Left the company"

    class Contract(models.TextChoices):
        UNLIMITED = "unlimited", "Unlimited"
        LIMITED = "limited", "Fixed term"

    class WeeklyOff(models.TextChoices):
        SAT_SUN = "sat_sun", "Saturday and Sunday"
        FRI_SAT = "fri_sat", "Friday and Saturday"
        SUN = "sun", "Sunday only"

    class PayMethod(models.TextChoices):
        TRANSFER = "transfer", "Bank transfer (WPS)"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"

    code = models.CharField("Employee no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    full_name = models.CharField(max_length=150)
    gender = models.CharField(max_length=1, choices=[("M", "Male"), ("F", "Female")], blank=True)
    nationality = models.CharField(max_length=60, choices=countries.CHOICES, blank=True)
    date_of_birth = models.DateField("Date of birth", null=True, blank=True)
    phone = models.CharField("Phone", max_length=30, validators=[RegexValidator(PHONE_REGEX, PHONE_ERROR)], blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField("Home address", max_length=250, blank=True)
    emergency_name = models.CharField("Emergency contact", max_length=120, blank=True)
    emergency_phone = models.CharField(
        "Emergency phone", max_length=30, blank=True, validators=[RegexValidator(PHONE_REGEX, PHONE_ERROR)],
    )

    job_title = models.CharField(max_length=100)
    department = models.CharField(max_length=12, choices=Department.choices, default=Department.OTHER)
    join_date = models.DateField("Joined on", default=timezone.localdate)
    contract_type = models.CharField(max_length=10, choices=Contract.choices, default=Contract.UNLIMITED)
    weekly_off = models.CharField("Weekly days off", max_length=8, choices=WeeklyOff.choices, default=WeeklyOff.SAT_SUN)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    end_date = models.DateField("Last working day", null=True, blank=True)
    end_reason = models.CharField("Reason for leaving", max_length=200, blank=True)

    basic_salary = models.DecimalField("Basic salary (AED / month)", max_digits=10, decimal_places=2, validators=MONEY)
    housing_allowance = models.DecimalField("Housing allowance", max_digits=10, decimal_places=2, default=0, validators=MONEY)
    transport_allowance = models.DecimalField("Transport allowance", max_digits=10, decimal_places=2, default=0, validators=MONEY)
    other_allowance = models.DecimalField("Other allowance", max_digits=10, decimal_places=2, default=0, validators=MONEY)
    overtime_multiplier = models.DecimalField(
        "Overtime rate", max_digits=3, decimal_places=2, default=D("1.25"), validators=[MinValueValidator(D("1"))],
        help_text="1.25 means each overtime hour is paid at 125% of the normal hourly rate (basic salary ÷ 30 ÷ 8).",
    )
    annual_leave_days = models.PositiveSmallIntegerField(
        "Annual leave (working days / year)", default=30,
        help_text="The yearly leave entitlement. Working days only: the employee's weekly days off are not counted.",
    )

    pay_method = models.CharField("Salary is paid by", max_length=10, choices=PayMethod.choices, default=PayMethod.TRANSFER)
    bank_name = models.CharField("Bank", max_length=60, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    wps_person_id = models.CharField(
        "Labour card / WPS person ID", max_length=14, blank=True,
        help_text="The 14-digit number on the labour card. Needed for the bank (WPS) salary file.",
    )
    routing_code = models.CharField(
        "Bank routing code", max_length=9, blank=True, help_text="9 digits. Your bank can give it to you. Needed for the bank (WPS) salary file.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    REQUIRED_DOCUMENTS = ("passport", "emirates_id")

    class Meta:
        ordering = ["full_name"]
        verbose_name = "employee"
        verbose_name_plural = "Employees"
        permissions = [("export_employee", "Can export employees to Excel / CSV")]

    def __str__(self):
        return f"{self.code or 'New'} · {self.full_name}"

    # ---- numbers ---------------------------------------------------------------------------------
    @property
    def gross_salary(self):
        return (self.basic_salary or 0) + (self.housing_allowance or 0) + (self.transport_allowance or 0) + (self.other_allowance or 0)

    @property
    def overtime_hour_rate(self):
        return cents((self.basic_salary or 0) / SALARY_DAYS / 8 * (self.overtime_multiplier or 1))

    # ---- days ------------------------------------------------------------------------------------
    def is_day_off(self, day):
        return day.weekday() in WEEKLY_OFF[self.weekly_off]

    def working_days_between(self, start, end):
        return sum(1 for day in each_day(start, end) if not self.is_day_off(day))

    def annual_leave_used(self, year, exclude_pk=None):
        """Working days of annual leave (approved or waiting for a decision) that fall in `year`."""
        first, last = date(year, 1, 1), date(year, 12, 31)
        leaves = self.leaves.filter(kind="annual", status__in=("pending", "approved"), start_date__lte=last, end_date__gte=first)
        if exclude_pk:
            leaves = leaves.exclude(pk=exclude_pk)
        return sum(self.working_days_between(max(l.start_date, first), min(l.end_date, last)) for l in leaves)

    def annual_leave_left(self, year=None):
        year = year or timezone.localdate().year
        return self.annual_leave_days - self.annual_leave_used(year)

    # ---- rules -----------------------------------------------------------------------------------
    def save(self, *args, **kwargs):
        self.full_name = " ".join((self.full_name or "").split())
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: _next_code(Employee, "EMP"), lambda: super(Employee, self).save(*args, **kwargs))

    def clean(self):
        self.full_name = " ".join((self.full_name or "").split())
        self.email = (self.email or "").strip().lower()
        self.iban = normalise_iban(self.iban)
        self.wps_person_id = (self.wps_person_id or "").strip()
        self.routing_code = (self.routing_code or "").strip()
        errors = {}
        if self.basic_salary is not None and self.basic_salary <= 0:
            errors["basic_salary"] = "The basic salary must be more than zero."
        if self.iban and not iban_is_valid(self.iban):
            errors["iban"] = "This IBAN is not valid. Check the number (UAE IBANs start with AE and have 23 characters)."
        if self.pay_method == self.PayMethod.TRANSFER:
            if not self.iban:
                errors.setdefault("iban", "Enter the IBAN: this employee is paid by bank transfer.")
            if not self.bank_name.strip():
                errors["bank_name"] = "Choose or enter the bank."
        if self.wps_person_id and not (self.wps_person_id.isdigit() and len(self.wps_person_id) == 14):
            errors["wps_person_id"] = "The labour card / WPS person ID has 14 digits."
        if self.routing_code and not (self.routing_code.isdigit() and len(self.routing_code) == 9):
            errors["routing_code"] = "A bank routing code has 9 digits."
        if self.status == self.Status.TERMINATED:
            if self.end_date is None:
                errors["end_date"] = "Enter the last working day."
            if self.pk and self.leaves.filter(status="pending").exists():
                errors["status"] = "This employee still has leave requests waiting for a decision. Decide those first."
        else:
            self.end_date, self.end_reason = None, ""
        if self.end_date and self.join_date and self.end_date < self.join_date:
            errors["end_date"] = "The last working day cannot be before the joining date."
        if self.date_of_birth and self.join_date and self.date_of_birth >= self.join_date:
            errors["date_of_birth"] = "Check the date of birth: it is not before the joining date."
        if self.pk and self.join_date:
            first_mark = self.attendance.order_by("date").values_list("date", flat=True).first()
            if first_mark and first_mark < self.join_date:
                errors["join_date"] = f"Attendance is already marked from {first_mark:%d %b %Y}. Remove it before moving the joining date later."
        if errors:
            raise ValidationError(errors)

    def missing_documents(self):
        have = {doc.doc_type for doc in self.documents.all()}
        labels = dict(EmployeeDocument.DocType.choices)
        return [labels[kind] for kind in self.REQUIRED_DOCUMENTS if kind not in have]

    def document_counts(self):
        """(expired, expiring soon) among this employee's documents."""
        today = timezone.localdate()
        limit = today + timedelta(days=sysconf.expiry_days())
        return (
            self.documents.filter(expiry_date__lt=today).count(),
            self.documents.filter(expiry_date__gte=today, expiry_date__lte=limit).count(),
        )


class EmployeeDocument(models.Model):
    """A document of an employee (passport, Emirates ID, visa ...) with an expiry date."""

    class DocType(models.TextChoices):
        PASSPORT = "passport", "Passport"
        EMIRATES_ID = "emirates_id", "Emirates ID"
        VISA = "visa", "Residence visa"
        LABOUR_CARD = "labour_card", "Labour card"
        INSURANCE = "insurance", "Health insurance"
        CONTRACT = "contract", "Employment contract"
        OTHER = "other", "Other"

    SINGLE = ("passport", "emirates_id", "visa", "labour_card", "insurance", "contract")
    EXPIRY_REQUIRED = ("passport", "emirates_id", "visa", "labour_card", "insurance")

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField("Document", max_length=12, choices=DocType.choices)
    number = models.CharField("Number", max_length=60, blank=True)
    expiry_date = models.DateField("Expires on", null=True, blank=True)
    file = models.FileField(
        "File", upload_to=employee_document_path, blank=True,
        validators=[FileExtensionValidator(FILE_TYPES), validate_document_size, validate_file_content],
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
        errors = {}
        self.number = (self.number or "").strip()
        if self.doc_type == "emirates_id" and self.number:
            if emirates_id_is_valid(self.number):
                self.number = normalise_emirates_id(self.number)
            else:
                errors["number"] = "An Emirates ID has 15 digits, for example 784-1990-1234567-1."
        if self.doc_type in self.EXPIRY_REQUIRED and self.expiry_date is None:
            errors["expiry_date"] = "Enter the expiry date."
        if errors:
            raise ValidationError(errors)

    @property
    def expiry_status(self):
        """'none', 'valid', 'soon' or 'expired'."""
        if self.expiry_date is None:
            return "none"
        today = timezone.localdate()
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + timedelta(days=sysconf.expiry_days()):
            return "soon"
        return "valid"


# ---------------------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------------------
class LeaveRequest(models.Model):
    class Kind(models.TextChoices):
        ANNUAL = "annual", "Annual leave"
        SICK = "sick", "Sick leave"
        COMPASSIONATE = "compassionate", "Compassionate leave"
        PARENTAL = "parental", "Maternity / paternity leave"
        UNPAID = "unpaid", "Unpaid leave"
        OTHER = "other", "Other paid leave"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for a decision"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    code = models.CharField("Leave no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leaves")
    kind = models.CharField("Type of leave", max_length=14, choices=Kind.choices, default=Kind.ANNUAL)
    start_date = models.DateField("From")
    end_date = models.DateField("To")
    days = models.PositiveSmallIntegerField("Working days", default=0, editable=False)
    reason = models.CharField(max_length=200, blank=True)
    file = models.FileField(
        "Supporting file", upload_to=leave_file_path, blank=True,
        validators=[FileExtensionValidator(FILE_TYPES), validate_document_size, validate_file_content],
        help_text="Optional. For example a doctor's certificate (PDF, JPG, PNG or WebP).",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    decided_on = models.DateField(null=True, blank=True, editable=False)
    decision_note = models.CharField("Decision note", max_length=200, blank=True, help_text="Required when rejecting: say why.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        verbose_name = "leave request"
        verbose_name_plural = "Leave requests"
        permissions = [
            ("approve_leaverequest", "Can approve or reject leave requests"),
            ("export_leaverequest", "Can export leave requests to Excel / CSV"),
        ]

    def __str__(self):
        return f"{self.code or 'New'} · {self.employee.full_name}"

    @property
    def is_paid(self):
        return self.kind != self.Kind.UNPAID

    def save(self, *args, **kwargs):
        if self.employee_id and self.start_date and self.end_date and self.end_date >= self.start_date:
            self.days = self.employee.working_days_between(self.start_date, self.end_date)
        if self.status == self.Status.PENDING:
            self.decided_by, self.decided_on = None, None
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: _next_code(LeaveRequest, "LV"), lambda: super(LeaveRequest, self).save(*args, **kwargs))

    def clean(self):
        errors = {}
        old = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        S = self.Status
        if self.employee_id and self.start_date and self.end_date:
            emp = self.employee
            if self.end_date < self.start_date:
                errors["end_date"] = "The last day cannot be before the first day."
            else:
                self.days = emp.working_days_between(self.start_date, self.end_date)
                if self.days == 0:
                    errors["end_date"] = "All of these days are days off for this employee. Choose working days."
                if self.start_date < emp.join_date:
                    errors["start_date"] = f"This employee joined on {emp.join_date:%d %b %Y}."
                if emp.end_date and self.end_date > emp.end_date:
                    errors["end_date"] = f"This employee's last working day is {emp.end_date:%d %b %Y}."
                if old is None and emp.status != Employee.Status.ACTIVE:
                    errors["employee"] = "This employee has left the company."
                if self.status in (S.PENDING, S.APPROVED) and "end_date" not in errors:
                    clash = LeaveRequest.objects.filter(
                        employee=emp, status__in=(S.PENDING, S.APPROVED), start_date__lte=self.end_date, end_date__gte=self.start_date,
                    ).exclude(pk=self.pk).first()
                    if clash is not None:
                        errors["start_date"] = f"This overlaps {clash.code} ({clash.start_date:%d %b} to {clash.end_date:%d %b %Y})."
                if self.kind == self.Kind.ANNUAL and self.status in (S.PENDING, S.APPROVED) and not errors:
                    for year in range(self.start_date.year, self.end_date.year + 1):
                        part = emp.working_days_between(max(self.start_date, date(year, 1, 1)), min(self.end_date, date(year, 12, 31)))
                        left = emp.annual_leave_days - emp.annual_leave_used(year, exclude_pk=self.pk)
                        if part > left:
                            errors["end_date"] = f"Only {max(left, 0)} day(s) of annual leave are left for {year}; this asks for {part}."
                            break
                if not errors and (self.status == S.APPROVED or (old is not None and old.status == S.APPROVED)):
                    locked = PayrollSlip.objects.filter(
                        employee=emp, run__status__in=("approved", "paid"), run__month__gte=month_start(self.start_date), run__month__lte=self.end_date,
                    ).select_related("run").first()
                    if locked is not None and (old is None or old.status != self.status or (old.start_date, old.end_date) != (self.start_date, self.end_date)):
                        errors["status"] = f"Payroll for {locked.run.month:%B %Y} is already approved, so leave in that month can no longer change."
        if self.status == S.REJECTED and not (self.decision_note or "").strip():
            errors["decision_note"] = "Write the reason for rejecting this request."
        if old is not None and old.status in (S.REJECTED, S.CANCELLED) and self.status != old.status:
            errors["status"] = "A rejected or cancelled request stays as it is. Make a new one."
        if old is not None and old.status == S.APPROVED and self.status == S.PENDING:
            errors["status"] = "An approved request cannot go back to waiting. Cancel it and make a new one."
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------------------
# Daily attendance
# ---------------------------------------------------------------------------------------
class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        LATE = "late", "Late"
        HALF_DAY = "half_day", "Half day"
        ABSENT = "absent", "Absent"
        LEAVE = "leave", "On leave"
        HOLIDAY = "holiday", "Public holiday"

    EARNS_OVERTIME = ("present", "late", "half_day")

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PRESENT)
    overtime_hours = models.DecimalField("Overtime (hours)", max_digits=4, decimal_places=1, default=0, validators=MONEY)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-date", "employee__full_name"]
        verbose_name = "attendance record"
        verbose_name_plural = "Attendance"
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="one_attendance_per_employee_per_day",
                                               violation_error_message="Attendance is already marked for this employee on this day.")]
        permissions = [("export_attendance", "Can export attendance to Excel / CSV")]

    def __str__(self):
        return f"{self.employee.full_name} · {self.date:%d %b %Y} · {self.get_status_display()}"

    def is_locked(self):
        return employee_month_locked(self.employee_id, self.date)

    def clean(self):
        errors = {}
        today = timezone.localdate()
        if self.employee_id and self.date:
            emp = self.employee
            if self.date > today:
                errors["date"] = "Attendance cannot be marked for a day that has not come yet."
            if self.date < emp.join_date:
                errors["date"] = f"This employee joined on {emp.join_date:%d %b %Y}."
            if emp.end_date and self.date > emp.end_date:
                errors["date"] = f"This employee's last working day was {emp.end_date:%d %b %Y}."
            if self._state.adding and emp.status != Employee.Status.ACTIVE and not errors:
                errors["employee"] = "This employee has left the company."
            if "date" not in errors and employee_month_locked(emp.pk, self.date):
                errors["date"] = f"Payroll for {self.date:%B %Y} is already approved, so its attendance can no longer change."
        if self.overtime_hours and self.status not in self.EARNS_OVERTIME:
            errors["overtime_hours"] = "Overtime can only be added to a day the employee worked."
        if self.overtime_hours and self.overtime_hours > 16:
            errors["overtime_hours"] = "That is more than 16 hours. Check the number."
        if errors:
            raise ValidationError(errors)


def employee_month_locked(employee_id, day):
    """Is this employee's pay for the month of `day` already approved (so attendance and leave must not change)?"""
    return PayrollSlip.objects.filter(employee_id=employee_id, run__month=month_start(day), run__status__in=("approved", "paid")).exists()


# ---------------------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------------------
def eligible_employees(month):
    """Everyone who was employed for at least one day of the month."""
    return Employee.objects.filter(join_date__lte=month_end(month)).filter(Q(end_date__isnull=True) | Q(end_date__gte=month_start(month)))


class PayrollRun(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    code = models.CharField("Payroll no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    month = models.DateField("Salary month", help_text="The month the salaries are for.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    slip_count = models.PositiveIntegerField(default=0, editable=False)
    total_net = models.DecimalField("Total to pay (AED)", max_digits=14, decimal_places=2, default=0, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    approved_on = models.DateField(null=True, blank=True, editable=False)
    paid_on = models.DateField("Paid on", null=True, blank=True)
    cancel_reason = models.CharField("Reason for cancelling", max_length=200, blank=True)
    employer_id = models.CharField(
        "Employer ID (WPS)", max_length=20, blank=True, help_text="Your establishment's number on the labour card. Needed for the bank (WPS) salary file.",
    )
    employer_routing = models.CharField(
        "Company bank routing code", max_length=9, blank=True, help_text="9 digits, from your bank. Needed for the bank (WPS) salary file.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month", "-id"]
        verbose_name = "payroll run"
        verbose_name_plural = "Payroll runs"
        permissions = [
            ("approve_payrollrun", "Can approve payroll and record its payment"),
            ("export_payrollrun", "Can export payroll to Excel / CSV"),
        ]

    def __str__(self):
        return f"{self.code or 'New'} · {self.month:%B %Y}"

    @property
    def title(self):
        return f"{self.month:%B %Y}"

    # ---- rules -----------------------------------------------------------------------------------
    def clean(self):
        errors = {}
        S = self.Status
        old = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if self.month:
            self.month = month_start(self.month)
            today = timezone.localdate()
            if old is None and self.month > month_start(today):
                errors["month"] = "Payroll cannot be made for a month that has not started."
            clash = PayrollRun.objects.exclude(status=S.CANCELLED).exclude(pk=self.pk).filter(month=self.month).first()
            if clash is not None and self.status != S.CANCELLED:
                errors["month"] = f"There is already a payroll for {self.month:%B %Y} ({clash.code}). Cancel it first to start again."
            if old is None and not eligible_employees(self.month).exists():
                errors["month"] = "No employee was working in this month."
        if self.employer_routing and not (self.employer_routing.isdigit() and len(self.employer_routing) == 9):
            errors["employer_routing"] = "A bank routing code has 9 digits."
        if old is not None:
            errors.update(self._transition_errors(old))
        if errors:
            raise ValidationError(errors)

    def _transition_errors(self, old):
        S = self.Status
        errors = {}
        allowed = {S.DRAFT: (S.DRAFT, S.APPROVED, S.CANCELLED), S.APPROVED: (S.APPROVED, S.DRAFT, S.PAID, S.CANCELLED),
                   S.PAID: (S.PAID,), S.CANCELLED: (S.CANCELLED,)}
        if old.month != self.month:
            errors["month"] = "The month of a payroll cannot be changed. Cancel it and start a new one."
        if self.status not in allowed[S(old.status)]:
            errors["status"] = {
                S.PAID: "A paid payroll cannot be changed. Void a salary voucher in Payment vouchers if a payment was wrong.",
                S.CANCELLED: "A cancelled payroll stays cancelled. Start a new one.",
            }.get(S(old.status), "This status change is not allowed.")
            return errors
        slips = list(self.slips.all())
        if self.status == S.APPROVED and old.status == S.DRAFT:
            if not slips:
                errors["status"] = "There are no salary slips to approve."
            elif any(s.net < 0 for s in slips):
                errors["status"] = "A salary slip has a negative amount to pay. Reduce its deductions first."
        if self.status == S.PAID and old.status != S.PAID:
            if self.paid_on is None:
                errors["paid_on"] = "Enter the date the salaries were paid."
            elif self.paid_on > timezone.localdate():
                errors["paid_on"] = "The payment date cannot be in the future."
            missing = [s.employee_name for s in slips if s.net > 0 and s.pay_method == "cheque" and not s.payment_reference.strip()]
            if missing:
                errors["status"] = "Enter the cheque number on the salary slip of: " + ", ".join(missing[:5]) + ("..." if len(missing) > 5 else ".")
        if self.status == S.CANCELLED and old.status != S.CANCELLED and not (self.cancel_reason or "").strip():
            errors["cancel_reason"] = "Write the reason for cancelling this payroll."
        return errors

    def save(self, *args, **kwargs):
        adding = self._state.adding
        old = None if adding else PayrollRun.objects.filter(pk=self.pk).first()
        if self.status != self.Status.CANCELLED:
            self.cancel_reason = ""
        if self.status == self.Status.PAID and not self.paid_on:
            self.paid_on = timezone.localdate()
        if adding and not self.employer_id:
            last = PayrollRun.objects.exclude(employer_id="").order_by("-month", "-id").first()
            if last is not None:
                self.employer_id, self.employer_routing = last.employer_id, last.employer_routing
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: _next_code(PayrollRun, "PAY"), lambda: super(PayrollRun, self).save(*args, **kwargs))
        if adding:
            self.rebuild()
        if old is not None and old.status == self.Status.APPROVED and self.status == self.Status.PAID:
            self._record_payments()

    # ---- slips -----------------------------------------------------------------------------------
    def rebuild(self):
        """(Re)calculate every slip from the employee records, the attendance and the leave. Manual additions and deductions stay."""
        if self.status != self.Status.DRAFT:
            return
        keep = {s.employee_id: s for s in self.slips.all()}
        wanted = set()
        for employee in eligible_employees(self.month).order_by("full_name"):
            wanted.add(employee.pk)
            slip = keep.get(employee.pk) or PayrollSlip(run=self, employee=employee)
            slip.fill(employee, self.month)
            slip.save()
        self.slips.exclude(employee_id__in=wanted).delete()
        self.refresh_totals()

    def refresh_totals(self):
        totals = self.slips.aggregate(n=models.Count("pk"), total=Sum("net"))
        PayrollRun.objects.filter(pk=self.pk).update(slip_count=totals["n"] or 0, total_net=totals["total"] or 0)
        self.slip_count, self.total_net = totals["n"] or 0, totals["total"] or D("0")

    def warnings(self):
        """[(slip, text)] for slips that need a second look."""
        return [(s, s.warning) for s in self.slips.all() if s.warning]

    def _record_payments(self):
        """One payment voucher for every salary that is paid."""
        from core.audit import suppressed

        method_of = {"transfer": "transfer", "cheque": "cheque", "cash": "cash"}
        for slip in self.slips.select_related("voucher"):
            if slip.voucher_id or slip.net <= 0:
                continue
            voucher = PaymentVoucher(
                payee_name=slip.employee_name, description=f"Salary {self.month:%B %Y}, {slip.employee_code} ({self.code})"[:250],
                amount=slip.net, method=method_of[slip.pay_method], voucher_date=self.paid_on or timezone.localdate(),
                cheque_number=slip.payment_reference[:30] if slip.pay_method == "cheque" else "",
                reference=f"WPS {self.code}" if slip.pay_method == "transfer" else "",
                bank_name=slip.bank_name[:60] if slip.pay_method == "transfer" else "", notes=f"Payroll {self.code}",
            )
            with suppressed():
                voucher.save()
            PayrollSlip.objects.filter(pk=slip.pk).update(voucher=voucher)


class PayrollSlip(models.Model):
    """One employee's pay for one month. Everything is copied when the slip is made, so it reads the same later."""

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="slips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="slips")
    employee_code = models.CharField(max_length=20, blank=True)
    employee_name = models.CharField(max_length=150)
    job_title = models.CharField(max_length=100, blank=True)

    basic = models.DecimalField("Basic salary", max_digits=10, decimal_places=2, default=0)
    housing = models.DecimalField("Housing allowance", max_digits=10, decimal_places=2, default=0)
    transport = models.DecimalField("Transport allowance", max_digits=10, decimal_places=2, default=0)
    other_allowance = models.DecimalField("Other allowance", max_digits=10, decimal_places=2, default=0)

    present_days = models.PositiveSmallIntegerField(default=0)
    late_days = models.PositiveSmallIntegerField(default=0)
    half_days = models.PositiveSmallIntegerField(default=0)
    absent_days = models.PositiveSmallIntegerField(default=0)
    paid_leave_days = models.PositiveSmallIntegerField(default=0)
    unpaid_leave_days = models.PositiveSmallIntegerField(default=0)
    unmarked_days = models.PositiveSmallIntegerField(default=0)
    payable_days = models.DecimalField("Days paid", max_digits=4, decimal_places=1, default=SALARY_DAYS)
    absence_deduction = models.DecimalField("Unpaid days deduction", max_digits=10, decimal_places=2, default=0)

    overtime_hours = models.DecimalField("Overtime (hours)", max_digits=6, decimal_places=1, default=0)
    overtime_amount = models.DecimalField("Overtime pay", max_digits=10, decimal_places=2, default=0)
    bonus_amount = models.DecimalField("Bonuses", max_digits=10, decimal_places=2, default=0, editable=False)
    bonus_note = models.CharField(max_length=200, blank=True, editable=False)
    other_additions = models.DecimalField("Other additions", max_digits=10, decimal_places=2, default=0, validators=MONEY)
    additions_note = models.CharField("Additions: what for", max_length=120, blank=True)
    other_deductions = models.DecimalField("Other deductions", max_digits=10, decimal_places=2, default=0, validators=MONEY)
    deductions_note = models.CharField("Deductions: what for", max_length=120, blank=True)
    net = models.DecimalField("To pay (AED)", max_digits=10, decimal_places=2, default=0, editable=False)

    pay_method = models.CharField(max_length=10, choices=Employee.PayMethod.choices, default=Employee.PayMethod.TRANSFER)
    bank_name = models.CharField(max_length=60, blank=True)
    iban = models.CharField(max_length=34, blank=True)
    wps_person_id = models.CharField(max_length=14, blank=True)
    routing_code = models.CharField(max_length=9, blank=True)
    payment_reference = models.CharField("Cheque no.", max_length=30, blank=True)
    voucher = models.ForeignKey(PaymentVoucher, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", editable=False)
    warning = models.CharField(max_length=250, blank=True, editable=False)

    class Meta:
        ordering = ["employee_name", "id"]
        verbose_name = "salary slip"
        verbose_name_plural = "Salary slips"
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="one_slip_per_employee_per_run")]
        permissions = [("export_payrollslip", "Can export salary slips to Excel / CSV")]

    def __str__(self):
        return f"{self.employee_name} · {self.run.month:%b %Y}"

    @property
    def gross(self):
        return self.basic + self.housing + self.transport + self.other_allowance

    @property
    def unpaid_days(self):
        return D(SALARY_DAYS) - self.payable_days

    @property
    def total_additions(self):
        return self.gross + self.overtime_amount + self.bonus_amount + self.other_additions

    @property
    def total_deductions(self):
        return self.absence_deduction + self.other_deductions

    def recalculate(self):
        self.net = cents(self.gross + self.overtime_amount + self.bonus_amount + self.other_additions - self.absence_deduction - self.other_deductions)

    def clean(self):
        self.recalculate()
        if self.net < 0:
            raise ValidationError({"other_deductions": f"The deductions are more than the salary (AED {self.gross + self.overtime_amount + self.other_additions - self.absence_deduction:,.2f})."})
        old = type(self).objects.filter(pk=self.pk).values("run__status", "other_additions", "other_deductions").first() if self.pk else None
        if old is not None:
            if old["run__status"] in ("paid", "cancelled"):
                raise ValidationError("This payroll is closed. Its salary slips can no longer be changed.")
            if old["run__status"] == "approved" and (old["other_additions"], old["other_deductions"]) != (self.other_additions, self.other_deductions):
                raise ValidationError("This payroll is approved, so the amounts can no longer be changed. Put it back to Draft first.")

    def save(self, *args, **kwargs):
        self.recalculate()
        super().save(*args, **kwargs)
        if not kwargs.get("update_fields"):
            self.run.refresh_totals()

    def fill(self, employee, month):
        """Work everything out for `month` from the employee record, the attendance and approved leave."""
        today = timezone.localdate()
        first, last = month_start(month), month_end(month)
        total_days = (last - first).days + 1
        self.employee, self.employee_code, self.employee_name, self.job_title = employee, employee.code or "", employee.full_name, employee.job_title
        self.basic, self.housing, self.transport, self.other_allowance = (
            employee.basic_salary, employee.housing_allowance, employee.transport_allowance, employee.other_allowance)
        self.pay_method, self.bank_name, self.iban = employee.pay_method, employee.bank_name, employee.iban
        self.wps_person_id, self.routing_code = employee.wps_person_id, employee.routing_code

        start = max(first, employee.join_date)
        end = min(last, employee.end_date) if employee.end_date else last
        marks = {a.date: a for a in employee.attendance.filter(date__gte=first, date__lte=last)}
        leaves = list(employee.leaves.filter(status="approved", start_date__lte=last, end_date__gte=first))

        counts = dict(present=0, late=0, half=0, absent=0, paid_leave=0, unpaid_leave=0, unmarked=0, unapproved=0)
        overtime = D("0")
        employed = 0
        for day in each_day(start, end):
            employed += 1
            mark = marks.get(day)
            if mark is not None:
                status = mark.status
                if status in ("present", "late", "half_day"):
                    overtime += mark.overtime_hours
                if status == "present":
                    counts["present"] += 1
                elif status == "late":
                    counts["present"] += 1
                    counts["late"] += 1
                elif status == "half_day":
                    counts["half"] += 1
                elif status == "absent":
                    counts["absent"] += 1
                elif status == "leave":
                    cover = next((l for l in leaves if l.start_date <= day <= l.end_date), None)
                    if cover is None:
                        counts["unpaid_leave"] += 1
                        counts["unapproved"] += 1
                    elif cover.is_paid:
                        counts["paid_leave"] += 1
                    else:
                        counts["unpaid_leave"] += 1
                continue
            if employee.is_day_off(day):
                continue
            cover = next((l for l in leaves if l.start_date <= day <= l.end_date), None)
            if cover is not None:
                counts["paid_leave" if cover.is_paid else "unpaid_leave"] += 1
            elif day <= today:
                counts["unmarked"] += 1

        base = SALARY_DAYS if employed == total_days else min(SALARY_DAYS, employed)
        unpaid = D(counts["absent"]) + D(counts["half"]) / 2 + counts["unpaid_leave"]
        payable = max(D("0"), D(base) - unpaid)
        self.present_days, self.late_days, self.half_days, self.absent_days = counts["present"], counts["late"], counts["half"], counts["absent"]
        self.paid_leave_days, self.unpaid_leave_days, self.unmarked_days = counts["paid_leave"], counts["unpaid_leave"], counts["unmarked"]
        self.payable_days = payable
        self.absence_deduction = cents(self.gross * (D(SALARY_DAYS) - payable) / SALARY_DAYS)
        self.overtime_hours = overtime
        self.overtime_amount = cents(overtime * employee.overtime_hour_rate)
        bonuses = list(employee.bonuses.filter(pay_month=first, status="approved"))
        self.bonus_amount = sum((b.amount for b in bonuses), D("0"))
        self.bonus_note = ", ".join(f"{b.get_kind_display()}" + (f" ({b.reason})" if b.reason else "") for b in bonuses)[:200]
        notes = []
        if counts["unmarked"]:
            notes.append(f"{counts['unmarked']} working day(s) not marked in attendance (counted as worked)")
        if counts["unapproved"]:
            notes.append(f"{counts['unapproved']} day(s) marked On leave without an approved leave request (counted unpaid)")
        if employee.pay_method == Employee.PayMethod.TRANSFER and not employee.iban:
            notes.append("no IBAN")
        self.warning = "; ".join(notes)[:250]
        self.recalculate()


# ---------------------------------------------------------------------------------------
# Bonuses
# ---------------------------------------------------------------------------------------
class Bonus(models.Model):
    """A one-off extra payment (bonus, commission ...) that is added to the salary slip of the month it is paid with."""

    class Kind(models.TextChoices):
        BONUS = "bonus", "Bonus"
        COMMISSION = "commission", "Commission"
        INCENTIVE = "incentive", "Incentive"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        CANCELLED = "cancelled", "Cancelled"

    code = models.CharField("Bonus no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="bonuses")
    kind = models.CharField("Type", max_length=10, choices=Kind.choices, default=Kind.BONUS)
    pay_month = models.DateField("Paid with the salary of", help_text="The salary month whose slip this is added to.")
    amount = models.DecimalField("Amount (AED)", max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    approved_on = models.DateField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pay_month", "-id"]
        verbose_name = "bonus"
        verbose_name_plural = "Bonuses"
        permissions = [
            ("approve_bonus", "Can approve bonuses"),
            ("export_bonus", "Can export bonuses to Excel / CSV"),
        ]

    def __str__(self):
        return f"{self.code or 'New'} · {self.employee.full_name}"

    def _locked(self, month):
        return PayrollSlip.objects.filter(employee_id=self.employee_id, run__month=month, run__status__in=("approved", "paid")).exists()

    def clean(self):
        errors = {}
        S = self.Status
        old = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if self.pay_month:
            self.pay_month = month_start(self.pay_month)
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "The amount must be more than zero."
        if self.employee_id and self.pay_month:
            emp = self.employee
            if emp.join_date > month_end(self.pay_month):
                errors["pay_month"] = f"This employee joined on {emp.join_date:%d %b %Y}."
            elif emp.end_date and emp.end_date < self.pay_month:
                errors["pay_month"] = f"This employee left on {emp.end_date:%d %b %Y}."
            same = old is not None and (old.pay_month, old.amount, old.status, old.kind) == (self.pay_month, self.amount, self.status, self.kind)
            if not errors and not same and (self._locked(self.pay_month) or (old is not None and self._locked(old.pay_month))):
                errors["pay_month"] = f"Payroll for {self.pay_month:%B %Y} is already approved. Choose a later month."
        if old is not None:
            if old.status == S.CANCELLED and self.status != S.CANCELLED:
                errors["status"] = "A cancelled bonus stays cancelled. Make a new one."
            elif old.status == S.APPROVED and self.status == S.PENDING:
                errors["status"] = "An approved bonus cannot go back to waiting. Cancel it and make a new one."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        old = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if self.pay_month:
            self.pay_month = month_start(self.pay_month)
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: _next_code(Bonus, "BON"), lambda: super(Bonus, self).save(*args, **kwargs))
        # a draft payroll for the month picks the change up by itself
        months = {self.pay_month} | ({old.pay_month} if old is not None else set())
        for run in PayrollRun.objects.filter(month__in=months, status="draft"):
            run.rebuild()


# ---------------------------------------------------------------------------------------
# Final settlement (end of service)
# ---------------------------------------------------------------------------------------
class FinalSettlement(models.Model):
    """What an employee who leaves is owed: gratuity (UAE labour law) and unused annual leave, less any deductions."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    code = models.CharField("Settlement no.", max_length=20, unique=True, null=True, blank=True, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="settlements")
    join_date = models.DateField(null=True, blank=True, editable=False)
    last_day = models.DateField(null=True, blank=True, editable=False)
    service_days = models.PositiveIntegerField(default=0, editable=False)
    unpaid_leave_days = models.PositiveIntegerField(default=0, editable=False)
    service_years = models.DecimalField(max_digits=6, decimal_places=2, default=0, editable=False)
    basic_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0, editable=False)
    gratuity = models.DecimalField("End-of-service gratuity", max_digits=12, decimal_places=2, default=0, editable=False)
    leave_days_due = models.DecimalField(max_digits=6, decimal_places=1, default=0, editable=False)
    leave_pay = models.DecimalField("Unused leave pay", max_digits=12, decimal_places=2, default=0, editable=False)
    other_additions = models.DecimalField(
        "Other additions", max_digits=12, decimal_places=2, default=0, validators=MONEY,
        help_text="For example pay in place of notice, or the last month's salary if it is not in a payroll.",
    )
    additions_note = models.CharField("Additions: what for", max_length=120, blank=True)
    other_deductions = models.DecimalField("Other deductions", max_digits=12, decimal_places=2, default=0, validators=MONEY)
    deductions_note = models.CharField("Deductions: what for", max_length=120, blank=True)
    net = models.DecimalField("Total to pay (AED)", max_digits=12, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    approved_on = models.DateField(null=True, blank=True, editable=False)
    pay_method = models.CharField("Paid by", max_length=10, choices=Employee.PayMethod.choices, default=Employee.PayMethod.TRANSFER)
    payment_reference = models.CharField("Cheque no. / transfer reference", max_length=60, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    voucher = models.ForeignKey(PaymentVoucher, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", editable=False)
    cancel_reason = models.CharField("Reason for cancelling", max_length=200, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "final settlement"
        verbose_name_plural = "Final settlements"
        constraints = [models.UniqueConstraint(fields=["employee"], condition=~Q(status="cancelled"), name="one_open_settlement_per_employee",
                                               violation_error_message="This employee already has a final settlement.")]
        permissions = [
            ("approve_finalsettlement", "Can approve final settlements and record their payment"),
            ("export_finalsettlement", "Can export final settlements to Excel / CSV"),
        ]

    def __str__(self):
        return f"{self.code or 'New'} · {self.employee.full_name}"

    @property
    def service_text(self):
        years, days = divmod(self.service_days, 365)
        return f"{years} year(s) {days} day(s)"

    # ---- the calculation -------------------------------------------------------------------------
    def calculate(self):
        emp = self.employee
        self.join_date, self.last_day = emp.join_date, emp.end_date
        if not self.last_day:
            return
        total = (self.last_day - self.join_date).days + 1
        unpaid = 0
        for leave in emp.leaves.filter(status="approved", kind="unpaid", start_date__lte=self.last_day):
            start, end = max(leave.start_date, self.join_date), min(leave.end_date, self.last_day)
            unpaid += max(0, (end - start).days + 1)
        self.unpaid_leave_days = unpaid
        self.service_days = max(0, total - unpaid)
        years = D(self.service_days) / D(365)
        self.service_years = years.quantize(D("0.01"), rounding=ROUND_HALF_UP)
        self.basic_salary = emp.basic_salary
        daily = emp.basic_salary / SALARY_DAYS
        if years < 1:
            gratuity = D("0")
        else:
            # 21 days of basic pay for each of the first 5 years, 30 days for each year after; never more than 2 years' basic pay
            gratuity = min(years, D(5)) * 21 * daily + max(years - 5, D("0")) * 30 * daily
            gratuity = min(gratuity, emp.basic_salary * 24)
        self.gratuity = cents(gratuity)
        accrued = D(emp.annual_leave_days) * self.service_days / D(365)
        taken = sum(
            emp.working_days_between(l.start_date, min(l.end_date, self.last_day))
            for l in emp.leaves.filter(status="approved", kind="annual", start_date__lte=self.last_day)
        )
        self.leave_days_due = max(D("0"), accrued - taken).quantize(D("0.1"), rounding=ROUND_HALF_UP)
        self.leave_pay = cents(self.leave_days_due * daily)
        self.recalculate()

    def recalculate(self):
        self.net = cents(self.gratuity + self.leave_pay + self.other_additions - self.other_deductions)

    # ---- rules -----------------------------------------------------------------------------------
    def clean(self):
        errors = {}
        S = self.Status
        old = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if self.employee_id:
            emp = self.employee
            if old is None and (emp.status != Employee.Status.TERMINATED or not emp.end_date):
                errors["employee"] = "Only an employee who has left can have a final settlement. Set them to 'Left the company' with the last working day first."
            elif self.status in (S.DRAFT, S.APPROVED) or old is None:
                self.calculate()
                if self.net < 0:
                    errors["other_deductions"] = "The deductions are more than the amount due."
        if old is not None:
            errors.update(self._transition_errors(old))
        if errors:
            raise ValidationError(errors)

    def _transition_errors(self, old):
        S = self.Status
        errors = {}
        allowed = {S.DRAFT: (S.DRAFT, S.APPROVED, S.CANCELLED), S.APPROVED: (S.APPROVED, S.DRAFT, S.PAID, S.CANCELLED),
                   S.PAID: (S.PAID,), S.CANCELLED: (S.CANCELLED,)}
        if self.status not in allowed[S(old.status)]:
            errors["status"] = {S.PAID: "A paid settlement cannot be changed. Void its payment voucher if the payment was wrong.",
                                S.CANCELLED: "A cancelled settlement stays cancelled. Make a new one."}.get(S(old.status), "This status change is not allowed.")
            return errors
        if old.status != S.DRAFT and (old.other_additions, old.other_deductions) != (self.other_additions, self.other_deductions):
            errors["other_deductions"] = "This settlement is approved. Put it back to Draft before changing the amounts."
        if self.status == S.PAID and old.status != S.PAID:
            if self.paid_on is None:
                errors["paid_on"] = "Enter the date it was paid."
            elif self.paid_on > timezone.localdate():
                errors["paid_on"] = "The payment date cannot be in the future."
            if self.pay_method == "cheque" and not self.payment_reference.strip():
                errors["payment_reference"] = "Enter the cheque number."
        if self.status == S.CANCELLED and old.status != S.CANCELLED and not (self.cancel_reason or "").strip():
            errors["cancel_reason"] = "Write the reason for cancelling this settlement."
        return errors

    def save(self, *args, **kwargs):
        adding = self._state.adding
        old = None if adding else FinalSettlement.objects.filter(pk=self.pk).first()
        if adding or old.status == self.Status.DRAFT:
            self.calculate()
        if self.status != self.Status.CANCELLED:
            self.cancel_reason = ""
        if self.status == self.Status.PAID and not self.paid_on:
            self.paid_on = timezone.localdate()
        if self.code:
            super().save(*args, **kwargs)
        else:
            save_with_unique_code(self, lambda: _next_code(FinalSettlement, "EOS"), lambda: super(FinalSettlement, self).save(*args, **kwargs))
        if old is not None and old.status == self.Status.APPROVED and self.status == self.Status.PAID:
            self._record_payment()

    def _record_payment(self):
        from core.audit import suppressed

        if self.voucher_id or self.net <= 0:
            return
        voucher = PaymentVoucher(
            payee_name=self.employee.full_name, description=f"End of service settlement {self.code}, {self.employee.code}"[:250], amount=self.net,
            method=self.pay_method, voucher_date=self.paid_on or timezone.localdate(),
            cheque_number=self.payment_reference[:30] if self.pay_method == "cheque" else "",
            reference=self.payment_reference[:60] if self.pay_method != "cheque" else "", notes=f"Settlement {self.code}",
        )
        with suppressed():
            voucher.save()
        type(self).objects.filter(pk=self.pk).update(voucher=voucher)
        self.voucher = voucher
