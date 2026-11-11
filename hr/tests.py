"""Checks for HR & Payroll: employees, leave, attendance, payroll and salary slips."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from payables.models import PaymentVoucher
from properties.models_constants import iban_is_valid

from . import seed
from .admin import build_sif, month_choices
from .models import Attendance, Employee, EmployeeDocument, LeaveRequest, PayrollRun, PayrollSlip, employee_month_locked

D = Decimal
JUNE = date(2025, 6, 1)  # a Sunday: Monday 2 June is the first working day, 21 working days in the month
_counter = [0]


def make_employee(**kwargs):
    _counter[0] += 1
    values = dict(full_name=f"Test Employee {_counter[0]}", job_title="Clerk", basic_salary=D("3000"), housing_allowance=D("1000"),
                  transport_allowance=D("500"), pay_method="cash", join_date=date(2024, 1, 1))
    values.update(kwargs)
    employee = Employee(**values)
    employee.full_clean()
    employee.save()
    return employee


def make_leave(employee, start, end, kind="annual", status="pending", **kwargs):
    leave = LeaveRequest(employee=employee, kind=kind, start_date=start, end_date=end, status=status, **kwargs)
    leave.full_clean(exclude=["file"])
    leave.save()
    return leave


def mark(employee, day, status="present", hours="0"):
    record = Attendance(employee=employee, date=day, status=status, overtime_hours=D(hours))
    record.full_clean()
    record.save()
    return record


def make_run(month=JUNE, **kwargs):
    run = PayrollRun(month=month, **kwargs)
    run.full_clean()
    run.save()
    return run


def move(run, status, **fields):
    run.status = status
    for key, value in fields.items():
        setattr(run, key, value)
    run.full_clean()
    run.save()
    return run


def slip_of(run, employee):
    return run.slips.get(employee=employee)


class EmployeeRules(TestCase):
    def test_number_is_assigned(self):
        first = make_employee()
        second = make_employee()
        self.assertEqual(first.code, "EMP-0001")
        self.assertEqual(second.code, "EMP-0002")

    def test_salary_figures(self):
        emp = make_employee()
        self.assertEqual(emp.gross_salary, D("4500"))
        self.assertEqual(emp.overtime_hour_rate, D("15.63"))  # 3000 / 30 / 8 x 1.25 = 15.625

    def test_working_days_skip_days_off(self):
        emp = make_employee()
        self.assertEqual(emp.working_days_between(date(2025, 6, 2), date(2025, 6, 8)), 5)
        friday_saturday = make_employee(weekly_off="fri_sat")
        self.assertEqual(friday_saturday.working_days_between(date(2025, 6, 2), date(2025, 6, 8)), 5)
        self.assertTrue(friday_saturday.is_day_off(date(2025, 6, 6)))

    def test_bank_transfer_needs_a_valid_iban_and_bank(self):
        with self.assertRaises(ValidationError) as caught:
            make_employee(pay_method="transfer")
        self.assertIn("iban", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            make_employee(pay_method="transfer", iban="AE00 1234", bank_name="Emirates NBD")
        self.assertIn("iban", caught.exception.message_dict)
        emp = make_employee(pay_method="transfer", iban=seed.make_iban("026", 12345), bank_name="Emirates NBD")
        self.assertTrue(iban_is_valid(emp.iban))

    def test_sample_ibans_are_valid(self):
        self.assertTrue(iban_is_valid(seed.make_iban("033", 1007919)))

    def test_salary_must_be_positive(self):
        with self.assertRaises(ValidationError):
            make_employee(basic_salary=D("0"))

    def test_leaving_needs_a_last_day_and_active_clears_it(self):
        emp = make_employee()
        emp.status = "terminated"
        with self.assertRaises(ValidationError):
            emp.full_clean()
        emp.end_date = date(2025, 5, 31)
        emp.full_clean()
        emp.status = "active"
        emp.full_clean()
        self.assertIsNone(emp.end_date)

    def test_wps_numbers_have_the_right_length(self):
        with self.assertRaises(ValidationError) as caught:
            make_employee(wps_person_id="123")
        self.assertIn("wps_person_id", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            make_employee(routing_code="12")
        self.assertIn("routing_code", caught.exception.message_dict)

    def test_documents(self):
        emp = make_employee()
        doc = EmployeeDocument(employee=emp, doc_type="emirates_id", number="784-1990-1234567-1")
        with self.assertRaises(ValidationError) as caught:
            doc.full_clean(exclude=["file"])
        self.assertIn("expiry_date", caught.exception.message_dict)  # an expiry date is required
        doc.expiry_date = timezone.localdate() + timedelta(days=10)
        doc.full_clean(exclude=["file"])
        doc.save()
        self.assertEqual(doc.expiry_status, "soon")
        bad = EmployeeDocument(employee=emp, doc_type="emirates_id", number="123", expiry_date=date(2030, 1, 1))
        with self.assertRaises(ValidationError):
            bad.full_clean(exclude=["file"])
        self.assertEqual(emp.document_counts(), (0, 1))
        self.assertEqual(emp.missing_documents(), ["Passport"])

    def test_employee_with_pay_cannot_be_deleted(self):
        emp = make_employee()
        make_run()
        with self.assertRaises(ProtectedError):
            emp.delete()


class LeaveRules(TestCase):
    def setUp(self):
        self.emp = make_employee(annual_leave_days=10)

    def test_days_count_working_days_only(self):
        leave = make_leave(self.emp, date(2025, 6, 5), date(2025, 6, 10))  # Thu, Fri, (Sat, Sun), Mon, Tue
        self.assertEqual(leave.days, 4)
        self.assertEqual(leave.code, "LV-0001")

    def test_only_days_off_is_refused(self):
        with self.assertRaises(ValidationError):
            make_leave(self.emp, date(2025, 6, 7), date(2025, 6, 8))

    def test_end_before_start_is_refused(self):
        with self.assertRaises(ValidationError):
            make_leave(self.emp, date(2025, 6, 10), date(2025, 6, 9))

    def test_overlap_is_refused(self):
        make_leave(self.emp, date(2025, 6, 2), date(2025, 6, 6))
        with self.assertRaises(ValidationError) as caught:
            make_leave(self.emp, date(2025, 6, 5), date(2025, 6, 9))
        self.assertIn("overlaps", " ".join(caught.exception.messages))

    def test_a_cancelled_request_does_not_block_new_dates(self):
        make_leave(self.emp, date(2025, 6, 2), date(2025, 6, 6), status="cancelled")
        make_leave(self.emp, date(2025, 6, 2), date(2025, 6, 6))

    def test_annual_leave_is_limited_to_the_entitlement(self):
        make_leave(self.emp, date(2025, 6, 2), date(2025, 6, 13))  # 10 working days: all of it
        self.assertEqual(self.emp.annual_leave_used(2025), 10)
        with self.assertRaises(ValidationError) as caught:
            make_leave(self.emp, date(2025, 6, 16), date(2025, 6, 16))
        self.assertIn("left", " ".join(caught.exception.messages))
        make_leave(self.emp, date(2025, 6, 16), date(2025, 6, 16), kind="sick")  # sick leave has no limit

    def test_unpaid_and_sick_leave_do_not_use_the_balance(self):
        make_leave(self.emp, date(2025, 6, 2), date(2025, 6, 13), kind="unpaid")
        self.assertEqual(self.emp.annual_leave_left(2025), 10)

    def test_before_joining_or_after_leaving_is_refused(self):
        with self.assertRaises(ValidationError):
            make_leave(self.emp, date(2023, 12, 20), date(2023, 12, 22))
        gone = make_employee(status="terminated", end_date=date(2025, 3, 31))
        with self.assertRaises(ValidationError):
            make_leave(gone, date(2025, 3, 24), date(2025, 4, 2))

    def test_rejecting_needs_a_reason(self):
        leave = make_leave(self.emp, date(2025, 6, 2), date(2025, 6, 3))
        leave.status = "rejected"
        with self.assertRaises(ValidationError) as caught:
            leave.full_clean(exclude=["file"])
        self.assertIn("decision_note", caught.exception.message_dict)
        leave.decision_note = "Busy period"
        leave.full_clean(exclude=["file"])

    def test_closed_requests_stay_closed_and_approved_cannot_wait_again(self):
        leave = make_leave(self.emp, date(2025, 6, 2), date(2025, 6, 3), status="approved")
        leave.status = "pending"
        with self.assertRaises(ValidationError):
            leave.full_clean(exclude=["file"])
        leave.status = "cancelled"
        leave.full_clean(exclude=["file"])
        leave.save()
        leave.status = "approved"
        with self.assertRaises(ValidationError):
            leave.full_clean(exclude=["file"])

    def test_leave_in_an_approved_payroll_month_cannot_change(self):
        pending = make_leave(self.emp, date(2025, 6, 9), date(2025, 6, 10))
        move(make_run(), "approved")
        pending.status = "approved"
        with self.assertRaises(ValidationError) as caught:
            pending.full_clean(exclude=["file"])
        self.assertIn("already approved", " ".join(caught.exception.messages))

    def test_leaving_the_company_needs_waiting_requests_decided(self):
        make_leave(self.emp, date(2025, 6, 9), date(2025, 6, 10))
        self.emp.status, self.emp.end_date = "terminated", date(2025, 6, 30)
        with self.assertRaises(ValidationError):
            self.emp.full_clean()


class AttendanceRules(TestCase):
    def setUp(self):
        self.emp = make_employee()

    def test_one_record_per_day(self):
        mark(self.emp, date(2025, 6, 2))
        with self.assertRaises(ValidationError):
            mark(self.emp, date(2025, 6, 2))

    def test_future_and_before_joining_are_refused(self):
        with self.assertRaises(ValidationError):
            mark(self.emp, timezone.localdate() + timedelta(days=1))
        with self.assertRaises(ValidationError):
            mark(self.emp, date(2023, 12, 29))

    def test_overtime_only_on_a_worked_day(self):
        with self.assertRaises(ValidationError):
            mark(self.emp, date(2025, 6, 2), "absent", "2")
        with self.assertRaises(ValidationError):
            mark(self.emp, date(2025, 6, 3), "present", "17")
        mark(self.emp, date(2025, 6, 4), "late", "2.5")

    def test_approved_payroll_locks_the_month(self):
        mark(self.emp, date(2025, 6, 2))
        move(make_run(), "approved")
        self.assertTrue(employee_month_locked(self.emp.pk, date(2025, 6, 20)))
        with self.assertRaises(ValidationError):
            mark(self.emp, date(2025, 6, 3))
        self.assertTrue(Attendance.objects.get(date=date(2025, 6, 2)).is_locked())
        move(PayrollRun.objects.get(), "draft")  # put back to draft: unlocked again
        mark(self.emp, date(2025, 6, 3))


class PayrollMath(TestCase):
    def setUp(self):
        self.emp = make_employee()  # gross 4,500

    def slip(self, month=JUNE):
        return slip_of(make_run(month), self.emp)

    def test_a_full_month_pays_in_full(self):
        slip = self.slip()
        self.assertEqual((slip.gross, slip.absence_deduction, slip.net, slip.payable_days), (D("4500"), D("0"), D("4500.00"), D("30")))
        self.assertIn("not marked", slip.warning)  # nothing was marked: counted as worked, but flagged

    def test_short_and_long_months_both_pay_in_full(self):
        self.assertEqual(self.slip(date(2025, 2, 1)).net, D("4500.00"))  # 28 days
        PayrollRun.objects.all().delete()
        self.assertEqual(self.slip(date(2025, 7, 1)).net, D("4500.00"))  # 31 days

    def test_absent_days_are_deducted(self):
        mark(self.emp, date(2025, 6, 2), "absent")
        mark(self.emp, date(2025, 6, 3), "absent")
        slip = self.slip()
        self.assertEqual((slip.absent_days, slip.payable_days, slip.absence_deduction, slip.net), (2, D("28"), D("300.00"), D("4200.00")))

    def test_half_day_is_half_a_deduction(self):
        mark(self.emp, date(2025, 6, 2), "half_day")
        slip = self.slip()
        self.assertEqual((slip.absence_deduction, slip.net), (D("75.00"), D("4425.00")))

    def test_late_is_present_and_not_deducted(self):
        mark(self.emp, date(2025, 6, 2), "late")
        slip = self.slip()
        self.assertEqual((slip.late_days, slip.present_days, slip.net), (1, 1, D("4500.00")))

    def test_unpaid_leave_is_deducted_and_paid_leave_is_not(self):
        make_leave(self.emp, date(2025, 6, 9), date(2025, 6, 11), kind="unpaid", status="approved")
        slip = self.slip()
        self.assertEqual((slip.unpaid_leave_days, slip.absence_deduction), (3, D("450.00")))
        PayrollRun.objects.all().delete()
        other = make_employee()
        make_leave(other, date(2025, 6, 9), date(2025, 6, 11), kind="annual", status="approved")
        paid = slip_of(make_run(), other)
        self.assertEqual((paid.paid_leave_days, paid.absence_deduction), (3, D("0.00")))

    def test_pending_leave_changes_nothing(self):
        make_leave(self.emp, date(2025, 6, 9), date(2025, 6, 11), kind="unpaid")
        self.assertEqual(self.slip().absence_deduction, D("0.00"))

    def test_leave_marked_without_an_approved_request_is_unpaid_and_flagged(self):
        mark(self.emp, date(2025, 6, 9), "leave")
        slip = self.slip()
        self.assertEqual((slip.unpaid_leave_days, slip.absence_deduction), (1, D("150.00")))
        self.assertIn("without an approved leave request", slip.warning)

    def test_days_off_inside_unpaid_leave_are_not_deducted(self):
        make_leave(self.emp, date(2025, 6, 5), date(2025, 6, 10), kind="unpaid", status="approved")  # Thu to Tue: 4 working days
        self.assertEqual(self.slip().unpaid_leave_days, 4)

    def test_overtime_is_paid_at_the_hourly_rate(self):
        mark(self.emp, date(2025, 6, 2), "present", "3")
        mark(self.emp, date(2025, 6, 3), "present", "1")
        slip = self.slip()
        self.assertEqual((slip.overtime_hours, slip.overtime_amount, slip.net), (D("4.0"), D("62.52"), D("4562.52")))

    def test_joining_in_the_month_pays_only_the_days_employed(self):
        new = make_employee(join_date=date(2025, 6, 16))
        slip = slip_of(make_run(), new)
        self.assertEqual((slip.payable_days, slip.absence_deduction, slip.net), (D("15"), D("2250.00"), D("2250.00")))

    def test_leaving_in_the_month_pays_only_the_days_employed(self):
        gone = make_employee(status="terminated", end_date=date(2025, 6, 10))
        slip = slip_of(make_run(), gone)
        self.assertEqual((slip.payable_days, slip.net), (D("10"), D("1500.00")))

    def test_people_who_left_before_or_joined_after_get_no_slip(self):
        make_employee(status="terminated", end_date=date(2025, 5, 31))
        make_employee(join_date=date(2025, 7, 1))
        run = make_run()
        self.assertEqual(run.slips.count(), 1)

    def test_extra_deductions_and_additions(self):
        slip = self.slip()
        slip.other_additions, slip.additions_note = D("200"), "Eid gift"
        slip.other_deductions, slip.deductions_note = D("500"), "Salary advance"
        slip.full_clean()
        slip.save()
        self.assertEqual(slip.net, D("4200.00"))
        self.assertEqual(slip.run.total_net, D("4200.00"))

    def test_deductions_cannot_be_more_than_the_pay(self):
        slip = self.slip()
        slip.other_deductions = D("9000")
        with self.assertRaises(ValidationError):
            slip.full_clean()

    def test_recalculating_keeps_the_manual_amounts(self):
        run = make_run()
        slip = slip_of(run, self.emp)
        slip.other_deductions, slip.deductions_note = D("100"), "Fine"
        slip.save()
        mark(self.emp, date(2025, 6, 2), "absent")
        run.rebuild()
        slip = slip_of(run, self.emp)
        self.assertEqual((slip.other_deductions, slip.absence_deduction, slip.net), (D("100"), D("150.00"), D("4250.00")))
        run.refresh_from_db()
        self.assertEqual(run.total_net, D("4250.00"))

    def test_recalculating_picks_up_new_employees_and_drops_gone_ones(self):
        run = make_run()
        newcomer = make_employee()
        run.rebuild()
        self.assertEqual(run.slips.count(), 2)
        Employee.objects.filter(pk=newcomer.pk).update(join_date=date(2025, 8, 1))
        run.rebuild()
        self.assertEqual(run.slips.count(), 1)

    def test_a_slip_keeps_its_figures_when_the_employee_record_changes_later(self):
        run = make_run()
        move(run, "approved")
        Employee.objects.filter(pk=self.emp.pk).update(basic_salary=D("9999"))
        self.assertEqual(slip_of(run, self.emp).basic, D("3000"))
        run.rebuild()  # only a draft is recalculated
        self.assertEqual(slip_of(run, self.emp).basic, D("3000"))


class PayrollRunRules(TestCase):
    def setUp(self):
        self.emp = make_employee()

    def test_one_payroll_per_month_until_it_is_cancelled(self):
        first = make_run()
        self.assertEqual(first.code, "PAY-0001")
        with self.assertRaises(ValidationError) as caught:
            make_run()
        self.assertIn("month", caught.exception.message_dict)
        with self.assertRaises(ValidationError):
            move(first, "cancelled")  # a reason is needed
        move(first, "cancelled", cancel_reason="Wrong month")
        make_run()

    def test_future_months_and_empty_months_are_refused(self):
        future = timezone.localdate().replace(day=1) + timedelta(days=40)
        with self.assertRaises(ValidationError):
            make_run(future)
        with self.assertRaises(ValidationError):
            make_run(date(2023, 1, 1))  # nobody was working yet

    def test_the_status_only_moves_forward(self):
        run = make_run()
        with self.assertRaises(ValidationError):
            move(run, "paid", paid_on=date(2025, 7, 3))
        move(run, "approved")
        move(run, "draft")
        move(run, "approved")
        move(run, "paid", paid_on=date(2025, 7, 3))
        run.status = "draft"
        with self.assertRaises(ValidationError):
            run.full_clean()

    def test_approving_locks_the_amounts(self):
        run = make_run()
        move(run, "approved")
        slip = slip_of(run, self.emp)
        slip.other_deductions = D("50")
        with self.assertRaises(ValidationError):
            slip.full_clean()
        move(run, "draft")
        slip = slip_of(run, self.emp)
        slip.other_deductions = D("50")
        slip.full_clean()

    def test_paying_makes_a_voucher_for_each_salary(self):
        transfer = make_employee(pay_method="transfer", iban=seed.make_iban("026", 555), bank_name="Emirates NBD")
        cheque = make_employee(pay_method="cheque")
        run = make_run()
        move(run, "approved")
        with self.assertRaises(ValidationError) as caught:
            move(run, "paid", paid_on=date(2025, 7, 3))
        self.assertIn("cheque number", " ".join(caught.exception.messages))
        slip = slip_of(run, cheque)
        slip.payment_reference = "C-100"
        slip.save()
        move(run, "paid", paid_on=date(2025, 7, 3))
        self.assertEqual(PaymentVoucher.objects.count(), 3)
        by_method = {v.method: v for v in PaymentVoucher.objects.all()}
        self.assertEqual(set(by_method), {"transfer", "cheque", "cash"})
        self.assertEqual(by_method["cheque"].cheque_number, "C-100")
        self.assertEqual(by_method["cheque"].amount, D("4500.00"))
        self.assertEqual(by_method["transfer"].payee_name, transfer.full_name)
        self.assertEqual(sum(v.amount for v in PaymentVoucher.objects.all()), run.total_net)
        self.assertTrue(all(s.voucher_id for s in run.slips.all()))
        run.save()  # saving again makes no second set of vouchers
        self.assertEqual(PaymentVoucher.objects.count(), 3)

    def test_a_zero_salary_gets_no_voucher(self):
        nothing = make_employee()
        run = make_run()
        slip = slip_of(run, nothing)
        slip.other_deductions = slip.gross
        slip.save()
        self.assertEqual(slip_of(run, nothing).net, D("0.00"))
        move(run, "approved")
        move(run, "paid", paid_on=date(2025, 7, 3))
        self.assertEqual(PaymentVoucher.objects.count(), 1)  # only the other employee

    def test_the_payment_date_cannot_be_in_the_future(self):
        run = make_run()
        move(run, "approved")
        with self.assertRaises(ValidationError):
            move(run, "paid", paid_on=timezone.localdate() + timedelta(days=2))

    def test_empty_payroll_cannot_be_approved(self):
        run = make_run()
        run.slips.all().delete()
        run.status = "approved"
        with self.assertRaises(ValidationError):
            run.full_clean()

    def test_the_month_of_a_payroll_cannot_be_changed(self):
        run = make_run()
        run.month = date(2025, 5, 1)
        with self.assertRaises(ValidationError):
            run.full_clean()

    def test_employer_details_are_copied_to_the_next_payroll(self):
        make_run(employer_id="1234567", employer_routing="033000001")
        later = make_run(date(2025, 7, 1))
        self.assertEqual((later.employer_id, later.employer_routing), ("1234567", "033000001"))


class BankFile(TestCase):
    def test_sif_file(self):
        emp = make_employee(pay_method="transfer", iban=seed.make_iban("026", 777), bank_name="Emirates NBD",
                            wps_person_id="12345678901234", routing_code="033000001")
        make_employee()  # paid in cash: not in the bank file
        run = make_run(employer_id="1234567", employer_routing="033000001")
        move(run, "approved")
        text, problems = build_sif(run)
        self.assertEqual(problems, [])
        lines = text.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], f"EDR,12345678901234,033000001,{emp.iban},2025-06-01,2025-06-30,30,4500.00,0.00,0")
        self.assertTrue(lines[1].startswith("SCR,1234567,033000001,"))
        self.assertIn(",062025,4500.00,1,AED,", lines[1])

    def test_missing_details_are_listed(self):
        make_employee(pay_method="transfer", iban=seed.make_iban("026", 778), bank_name="Emirates NBD")
        run = make_run()
        move(run, "approved")
        _, problems = build_sif(run)
        text = " ".join(problems)
        self.assertIn("Employer ID", text)
        self.assertIn("WPS person ID", text)

    def test_deductions_reduce_the_fixed_part_and_overtime_goes_to_the_variable_part(self):
        emp = make_employee(pay_method="transfer", iban=seed.make_iban("026", 779), bank_name="Emirates NBD",
                            wps_person_id="12345678901235", routing_code="033000001")
        mark(emp, date(2025, 6, 2), "present", "4")
        run = make_run(employer_id="1234567", employer_routing="033000001")
        slip = slip_of(run, emp)
        slip.other_deductions = D("100")
        slip.save()
        move(run, "approved")
        text, _ = build_sif(run)
        self.assertIn(",30,4400.00,62.52,0", text)


class Roles(TestCase):
    def test_the_hr_role_is_made_and_the_accountant_approves_payroll(self):
        Group.objects.create(name="Accountant")
        Group.objects.create(name="Property Manager")
        seed.grant_role_permissions()
        hr = User.objects.create_user("hr", password="pw", is_staff=True)
        hr.groups.add(Group.objects.get(name="HR Manager"))
        accountant = User.objects.create_user("acc", password="pw", is_staff=True)
        accountant.groups.add(Group.objects.get(name="Accountant"))
        manager = User.objects.create_user("pm", password="pw", is_staff=True)
        manager.groups.add(Group.objects.get(name="Property Manager"))
        self.assertTrue(hr.has_perm("hr.add_employee"))
        self.assertTrue(hr.has_perm("hr.approve_leaverequest"))
        self.assertFalse(hr.has_perm("hr.approve_payrollrun"))
        self.assertTrue(accountant.has_perm("hr.approve_payrollrun"))
        self.assertFalse(accountant.has_perm("hr.add_employee"))
        self.assertFalse(manager.has_perm("hr.view_employee"))
        self.assertFalse(manager.has_perm("hr.view_payrollslip"))


class Pages(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("hradmin", "h@example.com", "pw")
        self.client.force_login(self.admin)
        self.emp = make_employee(full_name="Page Employee")

    def test_clean_addresses(self):
        self.assertEqual(reverse("admin:hr_employee_changelist"), "/admin/employees/")
        self.assertEqual(reverse("admin:hr_leaverequest_changelist"), "/admin/leave-requests/")
        self.assertEqual(reverse("admin:hr_attendance_changelist"), "/admin/attendance/")
        self.assertEqual(reverse("admin:hr_payrollrun_changelist"), "/admin/payroll/")
        self.assertEqual(reverse("admin:hr_payrollslip_changelist"), "/admin/salary-slips/")

    def test_lists_chips_and_search(self):
        make_leave(self.emp, date(2025, 6, 9), date(2025, 6, 10))
        mark(self.emp, date(2025, 6, 2))
        make_run()
        for name in ("hr_employee", "hr_leaverequest", "hr_attendance", "hr_payrollrun", "hr_payrollslip"):
            url = reverse(f"admin:{name}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, name)
            for chip in ("active", "away", "docs", "left", "pending", "now", "coming", "closed", "today", "month", "absent", "late", "overtime",
                         "draft", "topay", "paid", "cancelled", "unpaid", "check"):
                self.assertEqual(self.client.get(url, {"chip": chip}).status_code, 200, f"{name} {chip}")
            self.assertEqual(self.client.get(url, {"q": "Page"}).status_code, 200)
        self.assertContains(self.client.get(reverse("admin:hr_employee_changelist"), {"q": "EMP-1"}), "Page Employee")

    def test_pages_open(self):
        leave = make_leave(self.emp, date(2025, 6, 9), date(2025, 6, 10))
        run = make_run()
        slip = slip_of(run, self.emp)
        urls = [
            reverse("admin:hr_employee_add"), reverse("admin:hr_employee_change", args=[self.emp.pk]),
            reverse("admin:hr_leaverequest_add"), reverse("admin:hr_leaverequest_change", args=[leave.pk]),
            reverse("admin:hr_attendance_add"), reverse("admin:hr_payrollrun_add"), reverse("admin:hr_payrollrun_change", args=[run.pk]),
            reverse("admin:hr_payrollslip_change", args=[slip.pk]), reverse("admin:hr_payrollrun_print", args=[run.pk]),
            reverse("admin:hr_payrollslip_print", args=[slip.pk]), reverse("admin:hr_attendance_sheet"),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(reverse("admin:hr_payrollslip_print", args=[slip.pk])), "Salary Slip")
        move(run, "approved")
        self.assertEqual(self.client.get(reverse("admin:hr_payrollrun_change", args=[run.pk])).status_code, 200)
        move(run, "paid", paid_on=date(2025, 7, 3))
        self.assertEqual(self.client.get(reverse("admin:hr_payrollrun_change", args=[run.pk])).status_code, 200)

    def test_adding_a_payroll_from_the_page(self):
        previous = month_choices()[1][0]
        response = self.client.post(reverse("admin:hr_payrollrun_add"), {"month_pick": previous, "notes": ""})
        self.assertEqual(response.status_code, 302, getattr(response, "context_data", ""))
        run = PayrollRun.objects.get()
        self.assertEqual(run.month.isoformat(), previous)
        self.assertEqual(run.slips.count(), 1)
        again = self.client.post(reverse("admin:hr_payrollrun_add"), {"month_pick": previous, "notes": ""})
        self.assertEqual(again.status_code, 200)  # refused: the month already has a payroll
        self.assertEqual(PayrollRun.objects.count(), 1)

    def test_the_daily_sheet_saves_and_clears(self):
        other = make_employee(full_name="Second Page Employee")
        today = timezone.localdate()
        url = reverse("admin:hr_attendance_sheet")
        page = self.client.get(url)
        self.assertContains(page, "Page Employee")
        response = self.client.post(url, {"date": today.isoformat(), f"s_{self.emp.pk}": "present", f"o_{self.emp.pk}": "2",
                                          f"n_{self.emp.pk}": "Stayed late", f"s_{other.pk}": "absent"})
        self.assertEqual(response.status_code, 302)
        saved = Attendance.objects.get(employee=self.emp, date=today)
        self.assertEqual((saved.status, saved.overtime_hours, saved.notes), ("present", D("2.0"), "Stayed late"))
        self.assertEqual(Attendance.objects.get(employee=other, date=today).status, "absent")
        self.client.post(url, {"date": today.isoformat(), f"s_{self.emp.pk}": "", f"s_{other.pk}": "absent"})
        self.assertFalse(Attendance.objects.filter(employee=self.emp, date=today).exists())
        self.assertTrue(Attendance.objects.filter(employee=other, date=today).exists())

    def test_the_daily_sheet_reports_a_bad_row_but_saves_the_rest(self):
        other = make_employee(full_name="Third Page Employee")
        today = timezone.localdate()
        self.client.post(reverse("admin:hr_attendance_sheet"), {"date": today.isoformat(), f"s_{self.emp.pk}": "absent", f"o_{self.emp.pk}": "3",
                                                                f"s_{other.pk}": "present"})
        self.assertFalse(Attendance.objects.filter(employee=self.emp).exists())
        self.assertTrue(Attendance.objects.filter(employee=other).exists())

    def test_the_daily_sheet_needs_permission(self):
        nobody = User.objects.create_user("nobody", password="pw", is_staff=True)
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(reverse("admin:hr_attendance_sheet")).status_code, 404)

    def test_the_sheet_leaves_a_locked_month_alone(self):
        today = timezone.localdate()
        run = make_run(today.replace(day=1))
        move(run, "approved")
        url = reverse("admin:hr_attendance_sheet")
        self.client.post(url, {"date": today.isoformat(), f"s_{self.emp.pk}": "absent"})
        self.assertFalse(Attendance.objects.filter(employee=self.emp).exists())

    def test_bank_file_download(self):
        emp = make_employee(pay_method="transfer", iban=seed.make_iban("026", 901), bank_name="Emirates NBD",
                            wps_person_id="12345678901236", routing_code="033000001")
        run = make_run(employer_id="7654321", employer_routing="033000001")
        url = reverse("admin:hr_payrollrun_wps", args=[run.pk])
        self.assertEqual(self.client.get(url).status_code, 302)  # not approved yet
        move(run, "approved")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(".SIF", response["Content-Disposition"])
        self.assertIn(emp.iban, response.content.decode())
        PayrollRun.objects.filter(pk=run.pk).update(employer_id="")
        self.assertEqual(self.client.get(url).status_code, 302)  # employer details missing

    def test_documents_appear_in_expiring_documents(self):
        EmployeeDocument.objects.create(employee=self.emp, doc_type="passport", number="P1", expiry_date=timezone.localdate() + timedelta(days=5))
        self.assertContains(self.client.get(reverse("expiring_documents")), "Page Employee")


class SampleData(TestCase):
    def test_seed_and_erase(self):
        message = seed.seed_hr()
        self.assertIn("5 employees", message)
        self.assertEqual(Employee.objects.count(), 5)
        self.assertEqual(PayrollRun.objects.filter(status="paid").count(), 1)
        self.assertEqual(PayrollRun.objects.filter(status="draft").count(), 1)
        self.assertTrue(PaymentVoucher.objects.exists())
        self.assertTrue(User.objects.filter(username="demo.hr").exists())
        self.assertIn("nothing new", seed.seed_hr())  # running it again adds nothing
        self.assertEqual(Employee.objects.count(), 5)
        seed.erase_hr()
        self.assertEqual((Employee.objects.count(), PayrollRun.objects.count(), PayrollSlip.objects.count(), PaymentVoucher.objects.count()), (0, 0, 0, 0))
