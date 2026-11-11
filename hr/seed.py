"""Sample employees, leave, attendance and payroll, the eraser, and the permissions of the roles."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from core.data_registry import register_eraser, register_seeder
from payables.models import PaymentVoucher

from .models import (
    Attendance,
    Employee,
    EmployeeDocument,
    LeaveRequest,
    PayrollRun,
    PayrollSlip,
    each_day,
    month_end,
    month_start,
)

D = Decimal
HR_ROLE = "HR Manager"
HR_DEMO_USER = ("demo.hr", "Noor", "Ibrahim")


def _all(*models):
    return [f"{a}_{m}" for m in models for a in ("add", "change", "view")]


# role -> permission codenames (a role that does not exist is skipped, except the HR Manager, which is made)
ROLE_PERMISSIONS = {
    HR_ROLE: _all("employee", "employeedocument", "leaverequest", "attendance", "payrollrun")
    + ["view_payrollslip", "change_payrollslip", "approve_leaverequest", "export_employee", "export_leaverequest", "export_attendance",
       "export_payrollrun", "export_payrollslip"],
    "Accountant": ["view_employee", "view_employeedocument", "view_leaverequest", "view_attendance", "add_payrollrun", "change_payrollrun",
                   "view_payrollrun", "view_payrollslip", "change_payrollslip", "approve_payrollrun", "export_payrollrun", "export_payrollslip",
                   "export_employee"],
    "Property Manager": [],
    "Leasing Agent": [],
}


def grant_role_permissions():
    """Give the roles their HR permissions. The HR Manager role is made when it is missing; the others are never created."""
    Group.objects.get_or_create(name=HR_ROLE)
    for role, codenames in ROLE_PERMISSIONS.items():
        group = Group.objects.filter(name=role).first()
        if group is None or not codenames:
            continue
        group.permissions.add(*Permission.objects.filter(content_type__app_label="hr", codename__in=codenames))


def make_iban(bank_code, account):
    """A correctly checked UAE IBAN for sample data."""
    bban = f"{bank_code}{account:016d}"
    check = 98 - int(bban + "1014" + "00") % 97
    return f"AE{check:02d}{bban}"


# name, job, department, nationality, basic, housing, transport, other, pay method, bank, bank code, joined (days ago)
SAMPLES = [
    ("Rashid Khan", "Leasing consultant", "leasing", "Pakistan", 6000, 2500, 500, 0, "transfer", "Emirates NBD", "026", 900),
    ("Fatima Noor", "Accountant", "accounts", "India", 8000, 3000, 800, 200, "transfer", "Mashreq Bank", "033", 1500),
    ("Joseph Mathew", "Maintenance technician", "maintenance", "India", 3000, 1000, 300, 0, "cheque", "", "", 600),
    ("Samir Aziz", "Building security", "security", "Egypt", 2200, 800, 0, 0, "cash", "", "", 400),
    ("Hana Saleh", "Receptionist", "admin", "Jordan", 4000, 1500, 400, 0, "transfer", "Abu Dhabi Commercial Bank (ADCB)", "003", 200),
]


def seed_hr() -> str:
    grant_role_permissions()
    User = get_user_model()
    role = Group.objects.get(name=HR_ROLE)
    username, first, last = HR_DEMO_USER
    user, created = User.objects.get_or_create(username=username, defaults={"first_name": first, "last_name": last, "email": f"{username}@example.com", "is_staff": True})
    if created:
        from core.seed import DEMO_PASSWORD

        user.set_password(DEMO_PASSWORD)
        user.save()
    user.groups.add(role)

    if Employee.objects.exists():
        return f"{Employee.objects.count()} employees (nothing new added)"
    today = timezone.localdate()
    D_ = EmployeeDocument.DocType
    people = []
    for index, (name, job, dept, nation, basic, housing, transport, other, method, bank, code, joined) in enumerate(SAMPLES, start=1):
        emp = Employee(
            full_name=name, job_title=job, department=dept, nationality=nation, basic_salary=D(basic), housing_allowance=D(housing),
            transport_allowance=D(transport), other_allowance=D(other), pay_method=method, bank_name=bank,
            iban=make_iban(code, 1000000 + index * 7919) if method == "transfer" else "",
            wps_person_id=f"{10000000000000 + index * 1111}" if method == "transfer" else "", routing_code="033000001" if method == "transfer" else "",
            join_date=today - timedelta(days=joined), phone=f"+971 50 000 20{index:02d}", email=f"{name.split()[0].lower()}@example.com",
            gender="F" if name.split()[0] in ("Fatima", "Hana") else "M",
        )
        emp.full_clean()
        emp.save()
        offsets = {1: (120, -10), 2: (700, 40), 3: (-5, 300), 4: (900, 25), 5: (400, 600)}[index]
        EmployeeDocument.objects.create(employee=emp, doc_type=D_.PASSPORT, number=f"P{6000000 + index * 313}", expiry_date=today + timedelta(days=offsets[0]))
        EmployeeDocument.objects.create(employee=emp, doc_type=D_.EMIRATES_ID, number=f"784-1990-{3000000 + index * 17:07d}-1", expiry_date=today + timedelta(days=offsets[1]))
        people.append(emp)

    # Last month: attendance for every working day
    previous_end = month_start(today) - timedelta(days=1)
    previous = month_start(previous_end)
    rashid, fatima, joseph, samir, hana = people
    sick = [d for d in each_day(previous + timedelta(days=9), previous + timedelta(days=12)) if not joseph.is_day_off(d)][:2]
    leave = LeaveRequest(employee=joseph, kind="sick", start_date=sick[0], end_date=sick[-1], reason="Flu", status="approved",
                         decided_by=user, decided_on=sick[0])
    leave.full_clean(exclude=["file"])
    leave.save()
    absent = [d for d in each_day(previous + timedelta(days=14), previous_end) if not samir.is_day_off(d)][:2]
    rows = []
    for emp in people:
        for day in each_day(max(previous, emp.join_date), previous_end):
            if emp.is_day_off(day) or day > today:
                continue
            status, hours, note = "present", D("0"), ""
            if emp is joseph and day in sick:
                status = "leave"
            elif emp is samir and day in absent:
                status = "absent"
            elif emp is hana and day == previous + timedelta(days=4):
                status = "late"
            elif emp is rashid and day.day in (20, 21):
                hours = D("3")
            rows.append(Attendance(employee=emp, date=day, status=status, overtime_hours=hours, notes=note))
    Attendance.objects.bulk_create(rows)

    # Leave: one coming up (approved), one waiting for a decision
    ahead = today + timedelta(days=21)
    for emp, kind, start, days_, status in ((fatima, "annual", ahead, 5, "approved"), (hana, "annual", today + timedelta(days=7), 2, "pending")):
        while emp.is_day_off(start):
            start += timedelta(days=1)
        stop = start + timedelta(days=days_ - 1)
        leave = LeaveRequest(employee=emp, kind=kind, start_date=start, end_date=stop, reason="Family trip", status=status,
                             decided_by=user if status == "approved" else None, decided_on=today if status == "approved" else None)
        leave.full_clean(exclude=["file"])
        leave.save()

    # Last month's payroll is approved and paid; this month's is a draft (nothing is marked yet)
    run = PayrollRun(month=previous, notes="Sample data", employer_id="0000123", employer_routing="033000001")
    run.full_clean()
    run.save()
    run.slips.filter(pay_method="cheque").update(payment_reference="004501")
    run.status, run.approved_by, run.approved_on = "approved", user, today
    run.save()
    run.status, run.paid_on = "paid", min(today, month_end(previous) + timedelta(days=3))
    run.save()
    draft = PayrollRun(month=month_start(today), notes="Sample data")
    draft.full_clean()
    draft.save()
    return f"{Employee.objects.count()} employees, {LeaveRequest.objects.count()} leave requests, {Attendance.objects.count()} attendance records, {PayrollRun.objects.count()} payroll runs"


def erase_hr() -> str:
    counts = (PayrollRun.objects.count(), LeaveRequest.objects.count(), Attendance.objects.count(), Employee.objects.count())
    salary_vouchers = PayrollSlip.objects.exclude(voucher=None).values_list("voucher_id", flat=True)
    PaymentVoucher.objects.filter(pk__in=list(salary_vouchers)).delete()
    PayrollRun.objects.all().delete()
    LeaveRequest.objects.all().delete()
    Attendance.objects.all().delete()
    Employee.objects.all().delete()
    return "removed {} payroll runs, {} leave requests, {} attendance records and {} employees".format(*counts)


# Seed after the payables (order 60) and statements (70); erase before them (reverse order).
register_seeder("HR & payroll", seed_hr, order=80)
register_eraser("HR & payroll", erase_hr, order=80)
