"""HR & Payroll screens: employees, leave, the daily attendance sheet, payroll runs and salary slips."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin, TabularInline

from billing.words import amount_in_words
from core import banks, exports, sysconf
from core.admin_utils import ExportPermissionMixin, TooltipHelpMixin, badge
from core.formats import fmt_date
from core.paging import RowsPerPageMixin
from core.search import SmartSearchMixin
from core.widgets import use_file_widget
from properties.owner_admin import DocumentFormSet, ReadOnlyInline

from .models import (
    Attendance,
    Bonus,
    Employee,
    EmployeeDocument,
    FinalSettlement,
    LeaveRequest,
    PayrollRun,
    PayrollSlip,
    employee_month_locked,
    month_start,
)

D = Decimal
METHOD_TEXT = dict(Employee.PayMethod.choices)
SETTLE_TONES = {"draft": "warning", "approved": "info", "paid": "success", "cancelled": "neutral"}
BONUS_TONES = {"pending": "warning", "approved": "success", "cancelled": "neutral"}
RUN_TONES = {"draft": "warning", "approved": "info", "paid": "success", "cancelled": "neutral"}
LEAVE_TONES = {"pending": "warning", "approved": "success", "rejected": "danger", "cancelled": "neutral"}
ATT_TONES = {"present": "success", "late": "warning", "half_day": "warning", "absent": "danger", "leave": "info", "holiday": "neutral"}


def money(value):
    return "-" if value is None else f"AED {value:,.2f}"


def select_widget():
    try:
        from unfold.widgets import UnfoldAdminSelectWidget as Select
    except ImportError:  # pragma: no cover
        Select = forms.Select
    return Select()


class ShowHiddenErrors:
    """A model rule may complain about a field that is not on the page (it is read-only there). Show the complaint at the top instead of failing."""

    def _update_errors(self, errors):
        if hasattr(errors, "error_dict"):
            for name in [n for n in errors.error_dict if n != NON_FIELD_ERRORS and n not in self.fields]:
                errors.error_dict.setdefault(NON_FIELD_ERRORS, []).extend(errors.error_dict.pop(name))
        super()._update_errors(errors)


def company():
    return {key: sysconf.get(f"company_{key}") for key in ("name", "trn", "phone", "email", "address")}


# ---------------------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------------------
EMPLOYEE_HEADERS = ["Employee no.", "Name", "Job title", "Department", "Nationality", "Gender", "Date of birth", "Phone", "Email", "Joined on",
                    "Status", "Last working day", "Basic (AED)", "Housing (AED)", "Transport (AED)", "Other allowance (AED)", "Gross (AED)",
                    "Paid by", "Bank", "IBAN", "WPS person ID", "Annual leave (days)", "Notes"]


def employee_rows(queryset):
    for e in queryset:
        yield [e.code, e.full_name, e.job_title, e.get_department_display(), e.nationality, e.gender, e.date_of_birth or "", e.phone, e.email,
               e.join_date, e.get_status_display(), e.end_date or "", e.basic_salary, e.housing_allowance, e.transport_allowance,
               e.other_allowance, e.gross_salary, METHOD_TEXT[e.pay_method], e.bank_name, e.iban, e.wps_person_id, e.annual_leave_days, e.notes]


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.bank_name if self.instance is not None and self.instance.pk else ""
        if "bank_name" in self.fields:
            self.fields["bank_name"] = forms.ChoiceField(
                label=self.fields["bank_name"].label, required=False, widget=select_widget(),
                choices=[("", "Select a bank")] + [(n, n) for n in banks.names(current)],
            )
        hints = {"full_name": "e.g. Rashid Ali Khan", "job_title": "e.g. Leasing consultant", "phone": "+971 50 123 4567",
                 "email": "e.g. rashid@example.com", "iban": "AE00 0000 0000 0000 0000 000", "wps_person_id": "14 digits",
                 "routing_code": "9 digits", "emergency_name": "e.g. Fatima Khan", "emergency_phone": "+971 50 123 4567"}
        for name, text in hints.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text
        for name in ("notes",):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = 3


class EmployeeDocumentForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        use_file_widget(self, "file", compact=True, accept=".pdf,.jpg,.jpeg,.png,.webp")


class EmployeeDocumentInline(TabularInline):
    model = EmployeeDocument
    form = EmployeeDocumentForm
    formset = DocumentFormSet
    tab = True
    extra = 0
    fields = ("doc_type", "number", "expiry_date", "status", "file", "notes")
    readonly_fields = ("status",)

    @admin.display(description="Status")
    def status(self, obj):
        if obj is None or not obj.pk:
            return "-"
        label, tone = {"expired": ("Expired", "danger"), "soon": ("Expiring soon", "warning"),
                       "valid": ("Valid", "success"), "none": ("No expiry", "neutral")}[obj.expiry_status]
        return badge(label, tone)


class EmployeeLeaveInline(ReadOnlyInline):
    """The 'Leave' tab."""

    model = LeaveRequest
    tab = True
    verbose_name_plural = "Leave"
    fields = ("number", "type_text", "period", "days", "state")
    readonly_fields = fields
    ordering = ("-start_date",)

    @admin.display(description="Leave no.")
    def number(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:hr_leaverequest_change", args=[obj.pk]), obj.code)

    @admin.display(description="Type")
    def type_text(self, obj):
        return obj.get_kind_display()

    @admin.display(description="Dates")
    def period(self, obj):
        return f"{fmt_date(obj.start_date)} → {fmt_date(obj.end_date)}"

    @admin.display(description="Status")
    def state(self, obj):
        return badge(obj.get_status_display(), LEAVE_TONES[obj.status])


class EmployeeSlipsInline(ReadOnlyInline):
    """The 'Salary Slips' tab."""

    model = PayrollSlip
    tab = True
    verbose_name_plural = "Salary slips"
    fields = ("month", "days", "gross_text", "net_text", "run_state", "open_slip")
    readonly_fields = fields
    ordering = ("-run__month",)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("hr.view_payrollslip")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("run")

    @admin.display(description="Month")
    def month(self, obj):
        return f"{obj.run.month:%B %Y}"

    @admin.display(description="Days paid")
    def days(self, obj):
        return f"{obj.payable_days:g}"

    @admin.display(description="Gross")
    def gross_text(self, obj):
        return money(obj.gross)

    @admin.display(description="To pay")
    def net_text(self, obj):
        return money(obj.net)

    @admin.display(description="Payroll")
    def run_state(self, obj):
        return badge(obj.run.get_status_display(), RUN_TONES[obj.run.status])

    @admin.display(description="")
    def open_slip(self, obj):
        return format_html('<a class="erp-qbtn erp-qbtn-primary" href="{}">Open</a>', reverse("admin:hr_payrollslip_change", args=[obj.pk]))


@admin.register(Employee)
class EmployeeAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = EmployeeForm
    compressed_fields = True
    list_display = ("code", "full_name", "job_text", "joined", "leave_left", "documents", "state")
    list_display_links = ("code", "full_name")
    list_filter = ("department", "status", "pay_method")
    search_fields = ("code", "full_name", "job_title", "phone", "email")
    search_text = ("code", "full_name", "job_title", "email")
    search_digits = ("phone",)
    search_codes = (("code", "EMP", 4),)
    search_fuzzy = ("full_name",)
    search_hint = "employee number (EMP-12), name, job title, phone or email"
    inlines = [EmployeeDocumentInline, EmployeeLeaveInline, EmployeeSlipsInline]
    actions = ["export_excel", "export_csv"]

    _header = (None, {"fields": ("summary_header",)})
    _personal = ("Personal", {"classes": ["tab"], "fields": (
        ("full_name", "gender"), ("nationality", "date_of_birth"), ("phone", "email"), "address", ("emergency_name", "emergency_phone"))})
    _job = ("Job And Salary", {"classes": ["tab"], "fields": (
        ("job_title", "department"), ("join_date", "contract_type"), "weekly_off", ("status", "end_date"), "end_reason",
        ("basic_salary", "housing_allowance"), ("transport_allowance", "other_allowance"), ("overtime_multiplier", "annual_leave_days"), "notes")})
    _pay = ("How Salary Is Paid", {"classes": ["tab"], "fields": ("pay_method", "bank_name", "iban", ("wps_person_id", "routing_code"))})

    def get_fieldsets(self, request, obj=None):
        body = (self._personal, self._job, self._pay)
        return body if obj is None else (self._header,) + body

    def get_readonly_fields(self, request, obj=None):
        return ("summary_header",)

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if obj is None:
            instances = [i for i in instances if isinstance(i, EmployeeDocumentInline)]
        return instances

    def has_delete_permission(self, request, obj=None):
        # An employee with pay or leave on record stays in the files (mark them as having left instead).
        if obj is not None and (obj.slips.exists() or obj.leaves.exists()):
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def quick_chips(self):
        today = timezone.localdate()
        soon = today + timedelta(days=sysconf.expiry_days())
        return [
            ("active", "Active", "success", Q(status="active")),
            ("away", "On Leave Today", "info", Q(pk__in=LeaveRequest.objects.filter(
                status="approved", start_date__lte=today, end_date__gte=today).values("employee_id"))),
            ("docs", "Documents Need Attention", "warning", Q(status="active", pk__in=EmployeeDocument.objects.filter(
                expiry_date__lte=soon).values("employee_id"))),
            ("left", "Left The Company", "neutral", Q(status="terminated")),
        ]

    @admin.display(description="Job", ordering="job_title")
    def job_text(self, obj):
        return f"{obj.job_title} · {obj.get_department_display()}"

    @admin.display(description="Joined", ordering="join_date")
    def joined(self, obj):
        return fmt_date(obj.join_date)

    @admin.display(description="Annual leave left")
    def leave_left(self, obj):
        return f"{obj.annual_leave_left()} days" if obj.status == "active" else "-"

    @admin.display(description="Documents")
    def documents(self, obj):
        expired, soon = obj.document_counts()
        if expired:
            return badge(f"{expired} expired", "danger")
        if soon:
            return badge(f"{soon} expiring soon", "warning")
        if obj.missing_documents():
            return badge("Incomplete", "warning")
        return badge("OK", "success")

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return badge("Active", "success") if obj.status == "active" else badge("Left", "neutral")

    @admin.display(description="")
    def summary_header(self, obj):
        if obj is None or not obj.pk:
            return ""
        pills = [badge("Active", "success") if obj.status == "active" else badge(f"Left on {fmt_date(obj.end_date)}", "neutral"),
                 badge(obj.get_department_display(), "info"), badge(METHOD_TEXT[obj.pay_method], "neutral")]
        expired, soon = obj.document_counts()
        if expired:
            pills.append(badge(f"{expired} document(s) expired", "danger"))
        if soon:
            pills.append(badge(f"{soon} document(s) expiring soon", "warning"))
        for name in obj.missing_documents():
            pills.append(badge(f"{name} not uploaded", "warning"))
        stats = [(money(obj.gross_salary), "Monthly salary")]
        if obj.status == "active":
            stats.append((f"{obj.annual_leave_left()} of {obj.annual_leave_days}", f"Annual leave left in {timezone.localdate().year}"))
        stats.append((fmt_date(obj.join_date), "Joined"))
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · {}</div>'
            '<div class="erp-oh-pills">{}</div><div class="erp-oh-sub">{}</div></div><div class="erp-oh-stats">{}</div></div>',
            obj.code, obj.full_name, format_html_join("", "{}", ((p,) for p in pills)), " · ".join(x for x in (obj.job_title, obj.phone, obj.email) if x),
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
        )

    @admin.action(description="Export selected employees to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Employees", EMPLOYEE_HEADERS, employee_rows(queryset), "employees")

    @admin.action(description="Export selected employees to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(EMPLOYEE_HEADERS, employee_rows(queryset), "employees")


# ---------------------------------------------------------------------------------------
# Leave requests
# ---------------------------------------------------------------------------------------
LEAVE_HEADERS = ["Leave no.", "Employee", "Type", "From", "To", "Working days", "Status", "Decided by", "Decided on", "Reason", "Decision note"]


def leave_rows(queryset):
    for l in queryset.select_related("employee", "decided_by"):
        yield [l.code, l.employee.full_name, l.get_kind_display(), l.start_date, l.end_date, l.days, l.get_status_display(),
               str(l.decided_by) if l.decided_by_id else "", l.decided_on or "", l.reason, l.decision_note]


class LeaveForm(ShowHiddenErrors, forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "reason" in self.fields:
            self.fields["reason"].widget.attrs["placeholder"] = "e.g. Family trip"
        if "file" in self.fields:
            use_file_widget(self, "file", compact=True, accept=".pdf,.jpg,.jpeg,.png,.webp")
        if "employee" in self.fields:
            self.fields["employee"].queryset = Employee.objects.filter(
                Q(status="active") | Q(pk=self.instance.employee_id if self.instance.pk else None))


@admin.register(LeaveRequest)
class LeaveRequestAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = LeaveForm
    compressed_fields = True
    autocomplete_fields = ["employee"]
    list_display = ("code", "employee_link", "type_text", "period", "days", "state")
    list_display_links = ("code",)
    list_filter = ("kind", "status")
    list_select_related = ("employee",)
    search_fields = ("code", "employee__full_name", "employee__code")
    search_text = ("code", "employee__full_name", "employee__code")
    search_codes = (("code", "LV", 4),)
    search_fuzzy = ("employee__full_name",)
    search_hint = "leave number (LV-12) or employee name"
    date_hierarchy = "start_date"
    actions = ["approve_selected", "export_excel", "export_csv"]

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {"fields": ("employee", "kind", ("start_date", "end_date"), "reason", "file"),
                            "description": "The request is saved as waiting. Someone with permission then approves or rejects it."}),)
        return ((None, {"fields": ("balance_note", "employee", "kind", ("start_date", "end_date"), "days_text", "reason", "file", "status", "decision_note")}),)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        locked = ["balance_note", "employee", "days_text"]
        if obj.status != "pending":
            locked += ["kind", "start_date", "end_date", "reason", "file"]
        if not request.user.has_perm("hr.approve_leaverequest") or obj.status in ("rejected", "cancelled"):
            locked += ["status", "decision_note"]
        return locked

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data and obj.status != "pending":
            obj.decided_by, obj.decided_on = request.user, timezone.localdate()
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False  # keep the record: cancel it instead

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        if not request.user.has_perm("hr.approve_leaverequest"):
            actions.pop("approve_selected", None)
        return actions

    @admin.action(description="Approve selected leave requests")
    def approve_selected(self, request, queryset):
        done = 0
        for leave in queryset.filter(status="pending").select_related("employee"):
            leave.status, leave.decided_by, leave.decided_on = "approved", request.user, timezone.localdate()
            try:
                leave.full_clean(exclude=["file"])
            except ValidationError as error:
                self.message_user(request, f"{leave.code} was not approved: " + " ".join(error.messages), messages.WARNING)
                continue
            leave.save()
            done += 1
        if done:
            self.message_user(request, f"{done} leave request(s) approved.", messages.SUCCESS)

    def quick_chips(self):
        today = timezone.localdate()
        return [
            ("pending", "Waiting For A Decision", "warning", Q(status="pending")),
            ("now", "On Leave Today", "info", Q(status="approved", start_date__lte=today, end_date__gte=today)),
            ("coming", "Coming Up", "success", Q(status="approved", start_date__gt=today)),
            ("closed", "Rejected Or Cancelled", "neutral", Q(status__in=("rejected", "cancelled"))),
        ]

    @admin.display(description="Employee", ordering="employee__full_name")
    def employee_link(self, obj):
        return format_html('<a href="{}" style="font-weight:600">{}</a>', reverse("admin:hr_employee_change", args=[obj.employee_id]), obj.employee.full_name)

    @admin.display(description="Type", ordering="kind")
    def type_text(self, obj):
        return obj.get_kind_display()

    @admin.display(description="Dates", ordering="start_date")
    def period(self, obj):
        return f"{fmt_date(obj.start_date)} → {fmt_date(obj.end_date)}"

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return badge(obj.get_status_display(), LEAVE_TONES[obj.status])

    @admin.display(description="Working days")
    def days_text(self, obj):
        return f"{obj.days} day(s)" if obj and obj.pk else "-"

    @admin.display(description="Annual leave balance")
    def balance_note(self, obj):
        if obj is None or not obj.pk:
            return "-"
        emp = obj.employee
        year = obj.start_date.year
        return f"{emp.annual_leave_days - emp.annual_leave_used(year)} of {emp.annual_leave_days} annual leave days left in {year} (waiting requests included)."

    @admin.action(description="Export selected leave requests to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Leave", LEAVE_HEADERS, leave_rows(queryset), "leave")

    @admin.action(description="Export selected leave requests to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(LEAVE_HEADERS, leave_rows(queryset), "leave")


# ---------------------------------------------------------------------------------------
# Attendance (list + the daily sheet)
# ---------------------------------------------------------------------------------------
ATT_HEADERS = ["Date", "Employee no.", "Employee", "Status", "Overtime (hours)", "Notes"]


def attendance_rows(queryset):
    for a in queryset.select_related("employee"):
        yield [a.date, a.employee.code, a.employee.full_name, a.get_status_display(), a.overtime_hours, a.notes]


class AttendanceForm(ShowHiddenErrors, forms.ModelForm):
    class Meta:
        model = Attendance
        fields = "__all__"


@admin.register(Attendance)
class AttendanceAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = AttendanceForm
    compressed_fields = True
    autocomplete_fields = ["employee"]
    list_display = ("day", "employee_link", "state", "overtime", "notes")
    list_display_links = ("day",)
    list_filter = ("status",)
    list_select_related = ("employee",)
    search_fields = ("employee__full_name", "employee__code", "notes")
    search_text = ("employee__full_name", "employee__code", "notes")
    search_fuzzy = ("employee__full_name",)
    search_hint = "employee name or number"
    date_hierarchy = "date"
    actions = ["export_excel", "export_csv"]
    fields = ("employee", "date", "status", "overtime_hours", "notes")

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        mine = [path("sheet/", self.admin_site.admin_view(self.sheet_view), name="%s_%s_sheet" % info)]
        return mine + super().get_urls()

    def get_readonly_fields(self, request, obj=None):
        return ("employee", "date") if obj is not None else ()

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_locked():
            return False
        return super().has_delete_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_locked():
            return False
        return super().has_change_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def quick_chips(self):
        today = timezone.localdate()
        return [
            ("today", "Today", "info", Q(date=today)),
            ("month", "This Month", "neutral", Q(date__gte=month_start(today))),
            ("absent", "Absent", "danger", Q(status="absent")),
            ("late", "Late", "warning", Q(status="late")),
            ("overtime", "With Overtime", "success", Q(overtime_hours__gt=0)),
        ]

    @admin.display(description="Date", ordering="date")
    def day(self, obj):
        return fmt_date(obj.date)

    @admin.display(description="Employee", ordering="employee__full_name")
    def employee_link(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:hr_employee_change", args=[obj.employee_id]), obj.employee.full_name)

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return badge(obj.get_status_display(), ATT_TONES[obj.status])

    @admin.display(description="Overtime", ordering="overtime_hours")
    def overtime(self, obj):
        return f"{obj.overtime_hours:g} h" if obj.overtime_hours else "-"

    @admin.action(description="Export selected attendance to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Attendance", ATT_HEADERS, attendance_rows(queryset), "attendance")

    @admin.action(description="Export selected attendance to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(ATT_HEADERS, attendance_rows(queryset), "attendance")

    # ---- the daily sheet ----------------------------------------------------------------------------
    def _day(self, request):
        today = timezone.localdate()
        try:
            day = datetime.strptime(request.GET.get("date") or request.POST.get("date") or "", "%Y-%m-%d").date()
        except ValueError:
            day = today
        return min(day, today)

    def _employees(self, day):
        return Employee.objects.filter(join_date__lte=day).filter(Q(end_date__isnull=True) | Q(end_date__gte=day)).order_by("full_name")

    def sheet_view(self, request):
        opts = self.model._meta
        if not (request.user.has_perm("hr.add_attendance") and request.user.has_perm("hr.change_attendance")):
            raise Http404
        day = self._day(request)
        employees = list(self._employees(day))
        if request.method == "POST":
            return self._save_sheet(request, day, employees)
        marks = {a.employee_id: a for a in Attendance.objects.filter(date=day)}
        leaves = set(LeaveRequest.objects.filter(status="approved", start_date__lte=day, end_date__gte=day).values_list("employee_id", flat=True))
        rows = []
        for emp in employees:
            mark = marks.get(emp.pk)
            on_leave = emp.pk in leaves
            rows.append({
                "emp": emp, "status": mark.status if mark else ("leave" if on_leave and not emp.is_day_off(day) else ""),
                "overtime": f"{mark.overtime_hours:g}" if mark and mark.overtime_hours else "", "notes": mark.notes if mark else "",
                "day_off": emp.is_day_off(day), "on_leave": on_leave, "locked": employee_month_locked(emp.pk, day),
            })
        shown = [r["status"] for r in rows if r["status"]]
        context = dict(
            self.admin_site.each_context(request), opts=opts, title="Daily Attendance", day=day, day_text=f"{day:%A}, {fmt_date(day)}",
            prev_url=f"?date={day - timedelta(days=1):%Y-%m-%d}", next_url=f"?date={day + timedelta(days=1):%Y-%m-%d}" if day < timezone.localdate() else "",
            is_today=day == timezone.localdate(), rows=rows, statuses=Attendance.Status.choices, list_url=reverse("admin:hr_attendance_changelist"),
            marked=len(shown), total=len(rows), iso=f"{day:%Y-%m-%d}", today_iso=f"{timezone.localdate():%Y-%m-%d}",
        )
        return TemplateResponse(request, "hr/sheet.html", context)

    @transaction.atomic
    def _save_sheet(self, request, day, employees):
        saved = cleared = 0
        problems = []
        existing = {a.employee_id: a for a in Attendance.objects.filter(date=day)}
        for emp in employees:
            status = (request.POST.get(f"s_{emp.pk}") or "").strip()
            hours = (request.POST.get(f"o_{emp.pk}") or "").strip()
            notes = (request.POST.get(f"n_{emp.pk}") or "").strip()[:200]
            mark = existing.get(emp.pk)
            if employee_month_locked(emp.pk, day):
                continue
            if not status:
                if mark is not None:
                    mark.delete()
                    cleared += 1
                continue
            try:
                overtime = D(hours) if hours else D("0")
                if not overtime.is_finite():
                    raise ValueError
            except Exception:
                problems.append(f"{emp.full_name}: overtime must be a number.")
                continue
            if mark is not None and (mark.status, mark.overtime_hours, mark.notes) == (status, overtime, notes):
                continue
            record = mark or Attendance(employee=emp, date=day)
            record.status, record.overtime_hours, record.notes = status, overtime, notes
            try:
                record.full_clean()
            except ValidationError as error:
                problems.append(f"{emp.full_name}: " + " ".join(error.messages))
                continue
            record.save()
            saved += 1
        if saved or cleared:
            messages.success(request, f"Attendance for {fmt_date(day)} saved ({saved} saved, {cleared} cleared).")
        elif not problems:
            messages.info(request, "Nothing was changed.")
        for text in problems:
            messages.warning(request, text)
        return HttpResponseRedirect(f"{reverse('admin:hr_attendance_sheet')}?date={day:%Y-%m-%d}")


# ---------------------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------------------
def month_choices():
    today = timezone.localdate()
    first = month_start(today)
    out = []
    for back in range(0, 13):
        year, month = first.year, first.month - back
        while month < 1:
            month += 12
            year -= 1
        out.append((f"{year}-{month:02d}-01", f"{['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][month - 1]} {year}"))
    return out


class PayrollForm(ShowHiddenErrors, forms.ModelForm):
    month_pick = forms.ChoiceField(label="Salary month", required=False, help_text="The month the salaries are for.")
    recalculate = forms.BooleanField(
        label="Recalculate the salary slips when saving", required=False,
        help_text="Tick this after changing attendance, leave or an employee's salary. Extra additions and deductions you typed are kept.",
    )

    class Meta:
        model = PayrollRun
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "month_pick" in self.fields:
            self.fields["month_pick"].choices = month_choices()
            self.fields["month_pick"].widget = select_widget()
            self.fields["month_pick"].required = True
        if "month" in self.fields:
            self.fields["month"].required = False
        if self.instance.pk and "status" in self.fields:
            current = self.instance.status
            allowed = {"draft": ("draft", "approved", "cancelled"), "approved": ("approved", "draft", "paid", "cancelled")}.get(current, (current,))
            self.fields["status"].choices = [(k, v) for k, v in PayrollRun.Status.choices if k in allowed]
        if "notes" in self.fields:
            self.fields["notes"].widget.attrs["rows"] = 3
        for name, text in {"employer_id": "e.g. 0000000", "employer_routing": "9 digits"}.items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = text

    def _update_errors(self, errors):
        if hasattr(errors, "error_dict") and "month" in errors.error_dict and "month" not in self.fields and "month_pick" in self.fields:
            errors.error_dict["month_pick"] = errors.error_dict.pop("month")  # the month is chosen in the dropdown
        super()._update_errors(errors)

    def _post_clean(self):
        # the month comes from the dropdown, so it is set before the model checks run
        if not self.instance.pk and self.cleaned_data.get("month_pick"):
            year, month, _day = self.cleaned_data["month_pick"].split("-")
            self.instance.month = date(int(year), int(month), 1)
        super()._post_clean()


class SlipInline(TabularInline):
    """The 'Salary Slips' tab of a payroll: check the days, add extra additions or deductions, enter cheque numbers."""

    model = PayrollSlip
    tab = True
    extra = 0
    max_num = 0
    can_delete = False
    verbose_name_plural = "Salary slips"
    fields = ("employee_text", "days_text", "overtime_text", "other_additions", "additions_note", "other_deductions", "deductions_note",
              "payment_reference", "net_text", "flag")
    _always = ("employee_text", "days_text", "overtime_text", "net_text", "flag")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        status = obj.status if obj is not None else "draft"
        editable = {"draft": ("other_additions", "additions_note", "other_deductions", "deductions_note"), "approved": ("payment_reference",)}.get(status, ())
        return list(self._always) + [f for f in self.fields if f not in self._always and f not in editable]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("employee", "run")

    @admin.display(description="Employee")
    def employee_text(self, obj):
        return format_html('<a href="{}">{}</a><br><small>{}</small>', reverse("admin:hr_payrollslip_change", args=[obj.pk]), obj.employee_name, METHOD_TEXT[obj.pay_method])

    @admin.display(description="Days")
    def days_text(self, obj):
        bits = [f"{obj.payable_days:g} paid"]
        for value, label in ((obj.absent_days, "absent"), (obj.half_days, "half"), (obj.late_days, "late"), (obj.paid_leave_days, "leave"),
                             (obj.unpaid_leave_days, "unpaid leave")):
            if value:
                bits.append(f"{value} {label}")
        return ", ".join(bits)

    @admin.display(description="Overtime")
    def overtime_text(self, obj):
        return f"{obj.overtime_hours:g} h · {money(obj.overtime_amount)}" if obj.overtime_hours else "-"

    @admin.display(description="To pay")
    def net_text(self, obj):
        return money(obj.net)

    @admin.display(description="Check")
    def flag(self, obj):
        return badge("Check", "warning") if obj.warning else ""


@admin.register(PayrollRun)
class PayrollRunAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = PayrollForm
    compressed_fields = True
    list_display = ("code", "month_text", "people", "total", "state")
    list_display_links = ("code", "month_text")
    list_filter = ("status",)
    search_fields = ("code",)
    search_text = ("code",)
    search_codes = (("code", "PAY", 4),)
    search_hint = "payroll number (PAY-12)"
    inlines = [SlipInline]
    actions = ["export_excel", "export_csv"]

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        wrap = self.admin_site.admin_view
        mine = [
            path("<path:object_id>/print/", wrap(self.print_view), name="%s_%s_print" % info),
            path("<path:object_id>/wps/", wrap(self.wps_view), name="%s_%s_wps" % info),
        ]
        return mine + super().get_urls()

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {"fields": ("month_pick", "notes"),
                            "description": "One salary slip is made for every employee who worked in the month. Check the slips, then approve the payroll."}),)
        fieldsets = [(None, {"fields": ("run_view",)})]
        if obj.status == "draft":
            fieldsets.append(("Status", {"classes": ["tab"], "fields": ("recalculate", "status", "cancel_reason", "notes")}))
        elif obj.status == "approved":
            fieldsets.append(("Payment", {"classes": ["tab"], "description": "To record the payment, enter the cheque numbers on the Salary Slips tab (cheque payments only), "
                              "set Status to Paid and enter the date. A payment voucher is made for every salary.",
                              "fields": ("status", "paid_on", "cancel_reason", "notes")}))
        else:
            fieldsets.append(("Details", {"classes": ["tab"], "fields": ("paid_on", "cancel_reason", "notes")}))
        fieldsets.append(("Bank (WPS) Details", {"classes": ["tab"], "fields": ("employer_id", "employer_routing")}))
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        locked = ["run_view"]
        if obj.status in ("paid", "cancelled"):
            locked += ["status", "paid_on", "cancel_reason", "employer_id", "employer_routing", "notes"]
        if not request.user.has_perm("hr.approve_payrollrun"):
            locked += ["status", "paid_on", "cancel_reason"]
        return locked

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data and obj.status == "approved":
            obj.approved_by, obj.approved_on = request.user, timezone.localdate()
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        if change and form.cleaned_data.get("recalculate") and obj.status == "draft":
            obj.rebuild()
            self.message_user(request, "The salary slips were recalculated.", messages.SUCCESS)

    def get_inline_instances(self, request, obj=None):
        return [] if obj is None else super().get_inline_instances(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False  # a payroll is kept: cancel it instead

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def response_post_save_add(self, request, obj):
        self.message_user(request, f"Payroll {obj.code} for {obj.title} was made. Check the salary slips, then approve it.", messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:hr_payrollrun_change", args=[obj.pk]))

    def quick_chips(self):
        return [
            ("draft", "Draft", "warning", Q(status="draft")),
            ("topay", "Approved (To Pay)", "info", Q(status="approved")),
            ("paid", "Paid", "success", Q(status="paid")),
            ("cancelled", "Cancelled", "neutral", Q(status="cancelled")),
        ]

    @admin.display(description="Month", ordering="month")
    def month_text(self, obj):
        return obj.title

    @admin.display(description="Employees", ordering="slip_count")
    def people(self, obj):
        return obj.slip_count

    @admin.display(description="Total to pay", ordering="total_net")
    def total(self, obj):
        return money(obj.total_net)

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return badge(obj.get_status_display(), RUN_TONES[obj.status])

    @admin.display(description="")
    def run_view(self, obj):
        if obj is None or not obj.pk:
            return ""
        by_method = {}
        for slip in obj.slips.all():
            by_method[slip.pay_method] = by_method.get(slip.pay_method, D("0")) + slip.net
        stats = [(obj.slip_count, "Employees"), (money(obj.total_net), "Total to pay")] + [(money(v), METHOD_TEXT[k]) for k, v in sorted(by_method.items())]
        warnings = obj.warnings()
        notes = ""
        if warnings and obj.status in ("draft", "approved"):
            notes = format_html('<ul style="margin:10px 0 0 18px;font-size:13px">{}</ul>', format_html_join(
                "", "<li><b>{}</b>: {}</li>", ((s.employee_name, text) for s, text in warnings[:12])))
        buttons = [format_html('<a class="erp-qbtn erp-qbtn-primary" href="{}" target="_blank">Print all salary slips</a>', reverse("admin:hr_payrollrun_print", args=[obj.pk]))]
        if obj.status in ("approved", "paid") and "transfer" in by_method:
            buttons.append(format_html('<a class="erp-qbtn" href="{}">Download bank (WPS) file</a>', reverse("admin:hr_payrollrun_wps", args=[obj.pk])))
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · {}</div><div class="erp-oh-pills">{}</div>'
            '<div class="erp-oh-sub">{}</div></div><div class="erp-oh-stats">{}</div></div>{}<p style="margin:12px 0">{}</p>',
            obj.code, obj.title, badge(obj.get_status_display(), RUN_TONES[obj.status]),
            f"Paid on {fmt_date(obj.paid_on)}" if obj.paid_on else (f"Approved on {fmt_date(obj.approved_on)}" if obj.approved_on else "Not approved yet"),
            format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats), notes,
            format_html_join(" ", "{}", ((b,) for b in buttons)),
        )

    # ---- print and the bank file ---------------------------------------------------------------------
    def print_view(self, request, object_id):
        run = self.get_object(request, object_id)
        if run is None or not self.has_view_permission(request, run):
            raise Http404
        return slips_page(request, list(run.slips.select_related("employee")), reverse("admin:hr_payrollrun_change", args=[run.pk]), f"Salary slips {run.title}")

    def wps_view(self, request, object_id):
        run = self.get_object(request, object_id)
        if run is None or not self.has_export_permission(request):
            raise Http404
        back = reverse("admin:hr_payrollrun_change", args=[run.pk])
        if run.status not in ("approved", "paid"):
            self.message_user(request, "Approve the payroll before making the bank file.", messages.WARNING)
            return HttpResponseRedirect(back)
        text, problems = build_sif(run)
        if problems:
            self.message_user(request, "The bank file cannot be made yet. " + " ".join(problems), messages.ERROR)
            return HttpResponseRedirect(back)
        stamp = timezone.localtime()
        response = HttpResponse(text, content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{run.employer_id}{stamp:%y%m%d%H%M}.SIF"'
        return response

    @admin.action(description="Export selected payrolls to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Payroll", RUN_HEADERS, run_rows(queryset), "payroll")

    @admin.action(description="Export selected payrolls to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(RUN_HEADERS, run_rows(queryset), "payroll")


RUN_HEADERS = ["Payroll no.", "Month", "Status", "Employee no.", "Employee", "Basic (AED)", "Housing (AED)", "Transport (AED)", "Other allowance (AED)",
               "Days paid", "Unpaid days deduction (AED)", "Overtime hours", "Overtime pay (AED)", "Other additions (AED)", "Other deductions (AED)",
               "To pay (AED)", "Paid by", "Bank", "IBAN"]


def run_rows(queryset):
    for run in queryset:
        for s in run.slips.all():
            yield [run.code, run.title, run.get_status_display(), s.employee_code, s.employee_name, s.basic, s.housing, s.transport, s.other_allowance,
                   s.payable_days, s.absence_deduction, s.overtime_hours, s.overtime_amount, s.other_additions, s.other_deductions, s.net,
                   METHOD_TEXT[s.pay_method], s.bank_name, s.iban]


def build_sif(run):
    """The WPS salary file (SIF): one EDR line per employee paid by bank transfer, then one SCR line. Returns (text, problems)."""
    from calendar import monthrange

    problems = []
    if not run.employer_id:
        problems.append("Enter the Employer ID on the Bank (WPS) Details tab.")
    if not run.employer_routing:
        problems.append("Enter the company bank routing code on the Bank (WPS) Details tab.")
    last = monthrange(run.month.year, run.month.month)[1]
    lines, total, count = [], D("0"), 0
    for slip in run.slips.all():
        if slip.pay_method != "transfer" or slip.net <= 0:
            continue
        missing = [name for name, value in (("WPS person ID", slip.wps_person_id), ("bank routing code", slip.routing_code), ("IBAN", slip.iban)) if not value]
        if missing:
            problems.append(f"{slip.employee_name}: add the {', '.join(missing)}.")
            continue
        fixed = max(D("0"), slip.gross - slip.absence_deduction - slip.other_deductions)
        variable = slip.net - fixed
        lines.append(",".join([
            "EDR", slip.wps_person_id, slip.routing_code, slip.iban, f"{run.month:%Y-%m-%d}", f"{run.month.replace(day=last):%Y-%m-%d}", str(last),
            f"{fixed:.2f}", f"{variable:.2f}", str(slip.paid_leave_days + slip.unpaid_leave_days)]))
        total += slip.net
        count += 1
    if not count and not problems:
        problems.append("No salary in this payroll is paid by bank transfer.")
    stamp = timezone.localtime()
    header = ",".join(["SCR", run.employer_id, run.employer_routing, f"{stamp:%Y-%m-%d}", f"{stamp:%H%M}", f"{run.month:%m%Y}", f"{total:.2f}", str(count), "AED", ""])
    return "\n".join(lines + [header]) + "\n", problems


def slips_page(request, slips, back, title):
    from django.template.loader import render_to_string

    if not slips:
        raise Http404
    pages = [{"slip": s, "words": amount_in_words(s.net), "employee": s.employee, "method": METHOD_TEXT[s.pay_method]} for s in slips]
    html = render_to_string("hr/slip_print.html", {"pages": pages, "back": back, "title": title, "company": company(), "today": fmt_date(timezone.localdate())}, request)
    return HttpResponse(html)


# ---------------------------------------------------------------------------------------
# Salary slips (read-only list and page)
# ---------------------------------------------------------------------------------------
@admin.register(PayrollSlip)
class PayrollSlipAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    compressed_fields = True
    list_display = ("employee_link", "month", "gross_text", "deduction_text", "net_text", "method", "state")
    list_display_links = None
    list_filter = ("run__status", "pay_method")
    list_select_related = ("run", "employee")
    search_fields = ("employee_name", "employee_code", "run__code")
    search_text = ("employee_name", "employee_code", "run__code")
    search_fuzzy = ("employee_name",)
    search_hint = "employee name, employee number or payroll number"
    actions = ["export_excel", "export_csv"]
    fields = ("slip_view",)
    readonly_fields = ("slip_view",)

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        mine = [path("<path:object_id>/print/", self.admin_site.admin_view(self.print_view), name="%s_%s_print" % info)]
        return mine + super().get_urls()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def quick_chips(self):
        return [
            ("unpaid", "Not Paid Yet", "warning", Q(run__status__in=("draft", "approved"))),
            ("paid", "Paid", "success", Q(run__status="paid")),
            ("check", "Needs A Look", "danger", ~Q(warning="") & Q(run__status__in=("draft", "approved"))),
        ]

    @admin.display(description="Employee", ordering="employee_name")
    def employee_link(self, obj):
        return format_html('<a href="{}" style="font-weight:600">{}</a>', reverse("admin:hr_payrollslip_change", args=[obj.pk]), obj.employee_name)

    @admin.display(description="Month", ordering="run__month")
    def month(self, obj):
        return f"{obj.run.month:%B %Y}"

    @admin.display(description="Gross", ordering="basic")
    def gross_text(self, obj):
        return money(obj.gross)

    @admin.display(description="Deductions")
    def deduction_text(self, obj):
        return money(obj.total_deductions)

    @admin.display(description="To pay", ordering="net")
    def net_text(self, obj):
        return money(obj.net)

    @admin.display(description="Paid by", ordering="pay_method")
    def method(self, obj):
        return METHOD_TEXT[obj.pay_method]

    @admin.display(description="Payroll", ordering="run__status")
    def state(self, obj):
        return badge(obj.run.get_status_display(), RUN_TONES[obj.run.status])

    @admin.display(description="")
    def slip_view(self, obj):
        if obj is None or not obj.pk:
            return ""
        rows = [("Basic salary", obj.basic), ("Housing allowance", obj.housing), ("Transport allowance", obj.transport), ("Other allowance", obj.other_allowance)]
        if obj.overtime_amount:
            rows.append((f"Overtime ({obj.overtime_hours:g} h)", obj.overtime_amount))
        if obj.bonus_amount:
            rows.append((f"Bonus: {obj.bonus_note}".strip(": "), obj.bonus_amount))
        if obj.other_additions:
            rows.append((f"Additions: {obj.additions_note}".strip(": "), obj.other_additions))
        lines = [format_html("<tr><td>{}</td><td style='text-align:right'>{}</td></tr>", label, f"{value:,.2f}") for label, value in rows if value]
        cuts = []
        if obj.absence_deduction:
            cuts.append((f"Unpaid days ({obj.unpaid_days:g})", obj.absence_deduction))
        if obj.other_deductions:
            cuts.append((f"Deductions: {obj.deductions_note}".strip(": "), obj.other_deductions))
        cut_lines = [format_html("<tr><td>{}</td><td style='text-align:right'>- {}</td></tr>", label, f"{value:,.2f}") for label, value in cuts]
        stats = [(money(obj.net), "To pay"), (f"{obj.payable_days:g} of 30", "Days paid"), (METHOD_TEXT[obj.pay_method], "Paid by")]
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · <a href="{}">{}</a></div><div class="erp-oh-pills">{}</div>'
            '<div class="erp-oh-sub">{}</div></div><div class="erp-oh-stats">{}</div></div>'
            '<table class="erp-table" style="max-width:520px;margin:12px 0"><tbody>{}{}</tbody></table>'
            '<p style="margin:12px 0"><a class="erp-qbtn erp-qbtn-primary" href="{}" target="_blank">Print / Save as PDF</a></p>',
            obj.run.title, reverse("admin:hr_employee_change", args=[obj.employee_id]), obj.employee_name, badge(obj.run.get_status_display(), RUN_TONES[obj.run.status]),
            obj.warning or "", format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
            format_html_join("", "{}", ((l,) for l in lines)), format_html_join("", "{}", ((l,) for l in cut_lines)),
            reverse("admin:hr_payrollslip_print", args=[obj.pk]),
        )

    def print_view(self, request, object_id):
        slip = self.get_object(request, object_id)
        if slip is None or not self.has_view_permission(request, slip):
            raise Http404
        return slips_page(request, [slip], reverse("admin:hr_payrollslip_change", args=[slip.pk]), f"Salary slip {slip.employee_name}")

    @admin.action(description="Export selected salary slips to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Salary slips", SLIP_HEADERS, slip_rows(queryset), "salary-slips")

    @admin.action(description="Export selected salary slips to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(SLIP_HEADERS, slip_rows(queryset), "salary-slips")


SLIP_HEADERS = RUN_HEADERS


def slip_rows(queryset):
    for s in queryset.select_related("run"):
        yield [s.run.code, s.run.title, s.run.get_status_display(), s.employee_code, s.employee_name, s.basic, s.housing, s.transport, s.other_allowance,
               s.payable_days, s.absence_deduction, s.overtime_hours, s.overtime_amount, s.other_additions, s.other_deductions, s.net,
               METHOD_TEXT[s.pay_method], s.bank_name, s.iban]


# ---------------------------------------------------------------------------------------
# Bonuses
# ---------------------------------------------------------------------------------------
BONUS_HEADERS = ["Bonus no.", "Employee no.", "Employee", "Type", "Paid with salary of", "Amount (AED)", "Reason", "Status", "Approved on"]


def bonus_rows(queryset):
    for b in queryset.select_related("employee"):
        yield [b.code, b.employee.code, b.employee.full_name, b.get_kind_display(), b.pay_month.strftime("%B %Y"), b.amount, b.reason,
               b.get_status_display(), b.approved_on or ""]


def bonus_month_choices(current=None):
    today = timezone.localdate()
    first = month_start(today)
    names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    out = []
    for back in range(-1, 13):
        year, month = first.year, first.month - back
        while month < 1:
            month, year = month + 12, year - 1
        while month > 12:
            month, year = month - 12, year + 1
        out.append((f"{year}-{month:02d}-01", f"{names[month - 1]} {year}"))
    if current and current.isoformat() not in dict(out):
        out.append((current.isoformat(), current.strftime("%B %Y")))
    return out


class BonusForm(ShowHiddenErrors, forms.ModelForm):
    pay_month = forms.TypedChoiceField(label="Paid with the salary of", coerce=date.fromisoformat, widget=None)

    class Meta:
        model = Bonus
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.pay_month if self.instance.pk else None
        field = self.fields["pay_month"]
        field.choices = bonus_month_choices(current)
        field.widget = select_widget()
        field.widget.choices = field.choices
        field.help_text = "The salary month whose slip this is added to."
        if current:
            self.initial["pay_month"] = current.isoformat()
        else:
            self.initial["pay_month"] = f"{timezone.localdate():%Y-%m-01}"
        if "reason" in self.fields:
            self.fields["reason"].widget.attrs["placeholder"] = "e.g. Target achieved in Q3"
        if "employee" in self.fields:
            self.fields["employee"].queryset = Employee.objects.filter(
                Q(status="active") | Q(pk=self.instance.employee_id if self.instance.pk else None))


@admin.register(Bonus)
class BonusAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = BonusForm
    compressed_fields = True
    autocomplete_fields = ["employee"]
    list_display = ("code", "employee_link", "type_text", "month", "amount_text", "state")
    list_display_links = ("code",)
    list_filter = ("kind", "status")
    list_select_related = ("employee",)
    search_fields = ("code", "employee__full_name", "employee__code", "reason")
    search_text = ("code", "employee__full_name", "employee__code", "reason")
    search_codes = (("code", "BON", 4),)
    search_fuzzy = ("employee__full_name",)
    search_hint = "bonus number (BON-12) or employee name"
    actions = ["approve_selected", "export_excel", "export_csv"]

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {"fields": ("employee", "kind", "pay_month", "amount", "reason"),
                            "description": "An approved bonus is added to the employee's salary slip for the chosen month."}),)
        return ((None, {"fields": ("employee", "kind", "pay_month", "amount", "reason", "status")}),)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        locked = ["employee"]
        if obj.status == "cancelled":
            locked += ["kind", "pay_month", "amount", "reason", "status"]
        if not request.user.has_perm("hr.approve_bonus"):
            locked += ["status"]
        return locked

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data and obj.status == "approved":
            obj.approved_by, obj.approved_on = request.user, timezone.localdate()
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        if not request.user.has_perm("hr.approve_bonus"):
            actions.pop("approve_selected", None)
        return actions

    @admin.action(description="Approve selected bonuses")
    def approve_selected(self, request, queryset):
        done = 0
        for bonus in queryset.filter(status="pending").select_related("employee"):
            bonus.status, bonus.approved_by, bonus.approved_on = "approved", request.user, timezone.localdate()
            try:
                bonus.full_clean()
            except ValidationError as error:
                self.message_user(request, f"{bonus.code} was not approved: " + " ".join(error.messages), messages.WARNING)
                continue
            bonus.save()
            done += 1
        if done:
            self.message_user(request, f"{done} bonus(es) approved.", messages.SUCCESS)

    def quick_chips(self):
        return [
            ("pending", "Waiting For Approval", "warning", Q(status="pending")),
            ("approved", "Approved", "success", Q(status="approved")),
            ("month", "This Month", "info", Q(pay_month=month_start(timezone.localdate()))),
            ("cancelled", "Cancelled", "neutral", Q(status="cancelled")),
        ]

    @admin.display(description="Employee", ordering="employee__full_name")
    def employee_link(self, obj):
        return format_html('<a href="{}" style="font-weight:600">{}</a>', reverse("admin:hr_employee_change", args=[obj.employee_id]), obj.employee.full_name)

    @admin.display(description="Type", ordering="kind")
    def type_text(self, obj):
        return obj.get_kind_display()

    @admin.display(description="Salary of", ordering="pay_month")
    def month(self, obj):
        return f"{obj.pay_month:%B %Y}"

    @admin.display(description="Amount", ordering="amount")
    def amount_text(self, obj):
        return money(obj.amount)

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return badge(obj.get_status_display(), BONUS_TONES[obj.status])

    @admin.action(description="Export selected bonuses to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Bonuses", BONUS_HEADERS, bonus_rows(queryset), "bonuses")

    @admin.action(description="Export selected bonuses to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(BONUS_HEADERS, bonus_rows(queryset), "bonuses")


# ---------------------------------------------------------------------------------------
# Final settlements
# ---------------------------------------------------------------------------------------
SETTLE_HEADERS = ["Settlement no.", "Employee no.", "Employee", "Joined", "Last working day", "Service (years)", "Basic (AED)", "Gratuity (AED)",
                  "Unused leave (days)", "Unused leave pay (AED)", "Other additions (AED)", "Other deductions (AED)", "Total to pay (AED)", "Status", "Paid on"]


def settlement_rows(queryset):
    for e in queryset.select_related("employee"):
        yield [e.code, e.employee.code, e.employee.full_name, e.join_date, e.last_day, e.service_years, e.basic_salary, e.gratuity, e.leave_days_due,
               e.leave_pay, e.other_additions, e.other_deductions, e.net, e.get_status_display(), e.paid_on or ""]


class SettlementForm(ShowHiddenErrors, forms.ModelForm):
    class Meta:
        model = FinalSettlement
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "employee" in self.fields and not self.instance.pk:
            self.fields["employee"].queryset = Employee.objects.filter(status="terminated", end_date__isnull=False).exclude(
                pk__in=FinalSettlement.objects.exclude(status="cancelled").values("employee_id"))
        for name in ("notes",):
            if name in self.fields:
                self.fields[name].widget.attrs["rows"] = 3
        if "payment_reference" in self.fields:
            self.fields["payment_reference"].widget.attrs["placeholder"] = "cheque number or transfer reference"


@admin.register(FinalSettlement)
class FinalSettlementAdmin(ExportPermissionMixin, SmartSearchMixin, RowsPerPageMixin, TooltipHelpMixin, ModelAdmin):
    form = SettlementForm
    compressed_fields = True
    autocomplete_fields = ["employee"]
    list_display = ("code", "employee_link", "last", "service", "gratuity_text", "leave_text", "net_text", "state")
    list_display_links = ("code",)
    list_filter = ("status",)
    list_select_related = ("employee",)
    search_fields = ("code", "employee__full_name", "employee__code")
    search_text = ("code", "employee__full_name", "employee__code")
    search_codes = (("code", "EOS", 4),)
    search_fuzzy = ("employee__full_name",)
    search_hint = "settlement number (EOS-12) or employee name"
    actions = ["export_excel", "export_csv"]

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        mine = [path("<path:object_id>/print/", self.admin_site.admin_view(self.print_view), name="%s_%s_print" % info)]
        return mine + super().get_urls()

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {"fields": ("employee", "notes"),
                            "description": "Only employees who have left the company (with a last working day) are listed. The gratuity and the unused leave are worked out for you."}),)
        fieldsets = [(None, {"fields": ("settlement_view",)})]
        if obj.status == "draft":
            fieldsets.append(("Adjustments And Status", {"classes": ["tab"], "fields": (("other_additions", "additions_note"), ("other_deductions", "deductions_note"),
                                                                                       "status", "cancel_reason", "notes")}))
        elif obj.status == "approved":
            fieldsets.append(("Payment", {"classes": ["tab"], "description": "To record the payment, set Status to Paid and fill in how it was paid. A payment voucher is made for you.",
                              "fields": ("pay_method", "payment_reference", "paid_on", "status", "cancel_reason", "notes")}))
        else:
            fieldsets.append(("Details", {"classes": ["tab"], "fields": ("pay_method", "payment_reference", "paid_on", "cancel_reason", "notes")}))
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        locked = ["settlement_view", "employee"]
        if obj.status in ("paid", "cancelled"):
            locked += ["status", "pay_method", "payment_reference", "paid_on", "cancel_reason", "other_additions", "additions_note",
                       "other_deductions", "deductions_note", "notes"]
        if not request.user.has_perm("hr.approve_finalsettlement"):
            locked += ["status", "pay_method", "payment_reference", "paid_on", "cancel_reason"]
        return locked

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data and obj.status == "approved":
            obj.approved_by, obj.approved_on = request.user, timezone.localdate()
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def response_post_save_add(self, request, obj):
        self.message_user(request, f"Settlement {obj.code} was worked out. Check it, then approve it.", messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:hr_finalsettlement_change", args=[obj.pk]))

    def quick_chips(self):
        return [
            ("draft", "Draft", "warning", Q(status="draft")),
            ("topay", "Approved (To Pay)", "info", Q(status="approved")),
            ("paid", "Paid", "success", Q(status="paid")),
            ("cancelled", "Cancelled", "neutral", Q(status="cancelled")),
        ]

    @admin.display(description="Employee", ordering="employee__full_name")
    def employee_link(self, obj):
        return format_html('<a href="{}" style="font-weight:600">{}</a>', reverse("admin:hr_employee_change", args=[obj.employee_id]), obj.employee.full_name)

    @admin.display(description="Last day", ordering="last_day")
    def last(self, obj):
        return fmt_date(obj.last_day)

    @admin.display(description="Service")
    def service(self, obj):
        return obj.service_text

    @admin.display(description="Gratuity", ordering="gratuity")
    def gratuity_text(self, obj):
        return money(obj.gratuity)

    @admin.display(description="Unused leave", ordering="leave_pay")
    def leave_text(self, obj):
        return money(obj.leave_pay)

    @admin.display(description="Total", ordering="net")
    def net_text(self, obj):
        return money(obj.net)

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return badge(obj.get_status_display(), SETTLE_TONES[obj.status])

    def breakdown(self, obj):
        rows = [
            (f"Gratuity ({obj.service_text}, basic AED {obj.basic_salary:,.2f})", obj.gratuity),
            (f"Unused annual leave ({obj.leave_days_due:g} days)", obj.leave_pay),
        ]
        if obj.other_additions:
            rows.append((f"Additions: {obj.additions_note}".strip(": "), obj.other_additions))
        lines = [format_html("<tr><td>{}</td><td style='text-align:right'>{}</td></tr>", label, f"{value:,.2f}") for label, value in rows]
        if obj.other_deductions:
            lines.append(format_html("<tr><td>{}</td><td style='text-align:right'>- {}</td></tr>",
                                     f"Deductions: {obj.deductions_note}".strip(": "), f"{obj.other_deductions:,.2f}"))
        return lines

    @admin.display(description="")
    def settlement_view(self, obj):
        if obj is None or not obj.pk:
            return ""
        stats = [(money(obj.net), "Total to pay"), (obj.service_text, "Service"), (fmt_date(obj.last_day), "Last working day")]
        return format_html(
            '<div class="erp-owner-header"><div class="erp-oh-main"><div class="erp-oh-name">{} · <a href="{}">{}</a></div><div class="erp-oh-pills">{}</div>'
            '<div class="erp-oh-sub">{}</div></div><div class="erp-oh-stats">{}</div></div>'
            '<table class="erp-table" style="max-width:560px;margin:12px 0"><tbody>{}</tbody></table>'
            '<p style="margin:12px 0"><a class="erp-qbtn erp-qbtn-primary" href="{}" target="_blank">Print / Save as PDF</a></p>',
            obj.code, reverse("admin:hr_employee_change", args=[obj.employee_id]), obj.employee.full_name, badge(obj.get_status_display(), SETTLE_TONES[obj.status]),
            "The last month's salary is paid through Payroll, not here.", format_html_join("", '<div class="erp-oh-stat"><b>{}</b><span>{}</span></div>', stats),
            format_html_join("", "{}", ((l,) for l in self.breakdown(obj))), reverse("admin:hr_finalsettlement_print", args=[obj.pk]),
        )

    def print_view(self, request, object_id):
        from django.template.loader import render_to_string

        obj = self.get_object(request, object_id)
        if obj is None or not self.has_view_permission(request, obj):
            raise Http404
        html = render_to_string("hr/settlement_print.html", {
            "s": obj, "back": reverse("admin:hr_finalsettlement_change", args=[obj.pk]), "title": f"Final settlement {obj.employee.full_name}",
            "company": company(), "words": amount_in_words(obj.net), "method": METHOD_TEXT[obj.pay_method], "today": fmt_date(timezone.localdate())}, request)
        return HttpResponse(html)

    @admin.action(description="Export selected settlements to Excel", permissions=["export"])
    def export_excel(self, request, queryset):
        return exports.excel_response("Final settlements", SETTLE_HEADERS, settlement_rows(queryset), "final-settlements")

    @admin.action(description="Export selected settlements to CSV", permissions=["export"])
    def export_csv(self, request, queryset):
        return exports.csv_response(SETTLE_HEADERS, settlement_rows(queryset), "final-settlements")
