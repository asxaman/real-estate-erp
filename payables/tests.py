"""Checks for vendor bills, expenses and payment vouchers."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.words import amount_in_words
from leases.tests import make_lease, make_tenant
from maintenance.models import MaintenanceRequest, Vendor
from maintenance.tests import make_job, make_vendor
from properties.models import Owner
from properties.tests import building, unit

from . import seed
from .models import Expense, PaymentVoucher, VendorBill, create_bill_for_job

D = Decimal
today = timezone.localdate


def make_bill(vendor, prop=None, **kwargs):
    values = dict(vendor=vendor, description="Plumbing repair", amount=D("1000"), bill_date=today(), property=prop)
    values.update(kwargs)
    bill = VendorBill(**values)
    bill.full_clean(exclude=["file"])
    bill.save()
    return bill


def pay(bill, amount, **kwargs):
    values = dict(bill=bill, amount=D(amount), method="transfer", voucher_date=today())
    values.update(kwargs)
    voucher = PaymentVoucher(**values)
    voucher.full_clean()
    voucher.save()
    bill.refresh_from_db()
    return voucher


def make_expense(**kwargs):
    values = dict(payee="DEWA", description="Electricity", category="utilities", amount=D("500"), expense_date=today())
    values.update(kwargs)
    expense = Expense(**values)
    expense.full_clean(exclude=["file"])
    expense.save()
    return expense


class Setup(TestCase):
    def setUp(self):
        self.tower = building("PAY")
        self.flat = unit(self.tower, "101")
        self.vendor = make_vendor("Bills Plumbing")


class Bills(Setup):
    def test_numbers_totals_and_vat(self):
        bill = make_bill(self.vendor, self.tower, vat_applies=True)
        self.assertEqual(bill.code, "BILL-0001")
        self.assertEqual((bill.vat, bill.total), (D("50.00"), D("1050.00")))
        second = make_bill(self.vendor, self.tower, bill_number="A-2")
        self.assertEqual((second.code, second.vat, second.total), ("BILL-0002", D("0.00"), D("1000.00")))

    def test_vendors_invoice_number_is_unique_while_open(self):
        make_bill(self.vendor, self.tower, bill_number="X-1")
        with self.assertRaises(ValidationError):
            make_bill(self.vendor, self.tower, bill_number="X-1")
        other = make_vendor("Other Vendor")
        make_bill(other, self.tower, bill_number="X-1")  # another vendor may use the same number

    def test_unit_must_belong_to_the_property(self):
        other = building("PAO")
        with self.assertRaises(ValidationError):
            make_bill(self.vendor, other, unit=self.flat)

    def test_owner_charge_needs_a_property_and_company_property_becomes_company(self):
        with self.assertRaises(ValidationError):
            make_bill(self.vendor, None, charged_to="owner")
        company = Owner.objects.get(is_company=True)
        mine = building("PAC")
        mine.owner = company
        mine.save()
        self.assertEqual(make_bill(self.vendor, mine, charged_to="owner").charged_to, "company")

    def test_tenant_charge_needs_a_tenant(self):
        with self.assertRaises(ValidationError):
            make_bill(self.vendor, self.tower, charged_to="tenant")
        make_bill(self.vendor, self.tower, charged_to="tenant", tenant=make_tenant("Charged Tenant"))

    def test_dates(self):
        with self.assertRaises(ValidationError):
            make_bill(self.vendor, self.tower, due_date=today() - timedelta(days=5), bill_date=today())
        with self.assertRaises(ValidationError):
            make_bill(self.vendor, self.tower, bill_date=today() + timedelta(days=10))

    def test_inactive_vendor_gets_no_new_bills(self):
        self.vendor.is_active = False
        self.vendor.save()
        with self.assertRaises(ValidationError):
            make_bill(self.vendor, self.tower)

    def test_states(self):
        bill = make_bill(self.vendor, self.tower, due_date=today() + timedelta(days=5))
        self.assertEqual(bill.payment_state(), "unpaid")
        pay(bill, "400")
        self.assertEqual((bill.payment_state(), bill.balance), ("part", D("600.00")))
        late = make_bill(self.vendor, self.tower, bill_number="L-1", bill_date=today() - timedelta(days=40), due_date=today() - timedelta(days=10))
        self.assertEqual(late.payment_state(), "overdue")
        pay(bill, "600")
        self.assertEqual(bill.payment_state(), "paid")

    def test_total_cannot_go_below_what_was_paid(self):
        bill = make_bill(self.vendor, self.tower)
        pay(bill, "800")
        bill.amount = D("500")
        with self.assertRaises(ValidationError) as caught:
            bill.full_clean(exclude=["file"])
        self.assertIn("amount", caught.exception.message_dict)

    def test_cancelling_needs_a_reason_and_no_payments(self):
        bill = make_bill(self.vendor, self.tower)
        bill.status = "cancelled"
        with self.assertRaises(ValidationError):
            bill.full_clean(exclude=["file"])
        bill.cancel_reason = "Entered twice"
        bill.full_clean(exclude=["file"])
        bill.save()
        paid = make_bill(self.vendor, self.tower, bill_number="P-1")
        pay(paid, "100")
        paid.status, paid.cancel_reason = "cancelled", "x"
        with self.assertRaises(ValidationError):
            paid.full_clean(exclude=["file"])

    def test_vendor_with_bills_cannot_be_deleted(self):
        make_bill(self.vendor, self.tower)
        with self.assertRaises(ProtectedError):
            self.vendor.delete()


class Vouchers(Setup):
    def test_a_payment_makes_a_numbered_voucher_and_updates_the_bill(self):
        bill = make_bill(self.vendor, self.tower, bill_number="V-1")
        voucher = pay(bill, "300", method="cheque", cheque_number="123456")
        self.assertEqual(voucher.code, "PV-0001")
        self.assertEqual(voucher.payee_name, "Bills Plumbing")
        self.assertIn("BILL-0001", voucher.description)
        self.assertEqual(bill.paid_amount, D("300.00"))
        self.assertIn("123456", voucher.method_text)

    def test_cannot_pay_more_than_is_left(self):
        bill = make_bill(self.vendor, self.tower)
        pay(bill, "900")
        with self.assertRaises(ValidationError) as caught:
            pay(bill, "200")
        self.assertIn("amount", caught.exception.message_dict)

    def test_cheque_needs_a_number(self):
        bill = make_bill(self.vendor, self.tower)
        with self.assertRaises(ValidationError):
            pay(bill, "100", method="cheque")

    def test_voiding_gives_the_money_back_to_the_balance_and_is_final(self):
        bill = make_bill(self.vendor, self.tower)
        voucher = pay(bill, "1000")
        self.assertEqual(bill.payment_state(), "paid")
        voucher.status, voucher.void_reason = "voided", ""
        with self.assertRaises(ValidationError):
            voucher.full_clean()
        voucher.void_reason = "Wrong account"
        voucher.full_clean()
        voucher.save()
        bill.refresh_from_db()
        self.assertEqual(bill.paid_amount, D("0.00"))
        voucher.status = "issued"
        with self.assertRaises(ValidationError):
            voucher.full_clean()

    def test_an_issued_voucher_cannot_be_changed(self):
        bill = make_bill(self.vendor, self.tower)
        voucher = pay(bill, "100")
        voucher.amount = D("200")
        with self.assertRaises(ValidationError):
            voucher.full_clean()

    def test_a_voucher_needs_a_payee_when_there_is_no_bill(self):
        with self.assertRaises(ValidationError):
            PaymentVoucher(amount=D("10"), method="cash").full_clean()
        PaymentVoucher(amount=D("10"), method="cash", payee_name="Petrol station").full_clean()

    def test_cancelled_bill_cannot_be_paid(self):
        bill = make_bill(self.vendor, self.tower)
        bill.status, bill.cancel_reason = "cancelled", "Duplicate"
        bill.save()
        with self.assertRaises(ValidationError):
            pay(bill, "100")


class Expenses(Setup):
    def test_expense_makes_its_voucher(self):
        expense = make_expense(vat_applies=True)
        self.assertEqual(expense.code, "EXP-0001")
        self.assertEqual(expense.total, D("525.00"))
        voucher = expense.vouchers.get()
        self.assertEqual((voucher.amount, voucher.payee_name, voucher.status), (D("525.00"), "DEWA", "issued"))

    def test_changing_the_expense_keeps_one_voucher_in_step(self):
        expense = make_expense()
        expense.amount = D("750")
        expense.save()
        self.assertEqual(expense.vouchers.count(), 1)
        self.assertEqual(expense.vouchers.get().amount, D("750.00"))

    def test_cancelling_voids_the_voucher(self):
        expense = make_expense()
        expense.cancelled = True
        with self.assertRaises(ValidationError):
            expense.full_clean(exclude=["file"])
        expense.cancel_reason = "Paid twice"
        expense.full_clean(exclude=["file"])
        expense.save()
        voucher = expense.vouchers.get()
        self.assertEqual(voucher.status, "voided")
        self.assertIn("Paid twice", voucher.void_reason)

    def test_an_expense_voucher_cannot_be_voided_by_hand(self):
        voucher = make_expense().vouchers.get()
        voucher.status, voucher.void_reason = "voided", "x"
        with self.assertRaises(ValidationError):
            voucher.full_clean()

    def test_future_date_and_owner_rules(self):
        with self.assertRaises(ValidationError):
            make_expense(expense_date=today() + timedelta(days=3))
        with self.assertRaises(ValidationError):
            make_expense(charged_to="owner")
        make_expense(charged_to="owner", property=self.tower)


class FromMaintenance(Setup):
    def finished_job(self, cost="450", **kwargs):
        job = make_job(self.tower, self.flat, self.vendor, estimate=D(cost))
        job.status, job.final_cost = "completed", D(cost)
        for key, value in kwargs.items():
            setattr(job, key, value)
        job.full_clean()
        job.save()
        return job

    def test_bill_from_a_completed_job(self):
        job = self.finished_job()
        bill, created = create_bill_for_job(job)
        self.assertTrue(created)
        self.assertEqual((bill.vendor, bill.property, bill.unit, bill.total), (self.vendor, self.tower, self.flat, D("450.00")))
        self.assertEqual(bill.charged_to, "owner")
        self.assertEqual(bill.category, "maintenance")
        self.assertEqual(bill.due_date, bill.bill_date + timedelta(days=30))
        again, created = create_bill_for_job(job)
        self.assertFalse(created)
        self.assertEqual(again.pk, bill.pk)

    def test_tenant_pays_is_carried_over(self):
        make_lease(make_tenant("Job Tenant"), self.flat)
        job = self.finished_job(paid_by="tenant")
        bill, _ = create_bill_for_job(job)
        self.assertEqual((bill.charged_to, bill.tenant.name), ("tenant", "Job Tenant"))

    def test_open_or_free_jobs_are_refused(self):
        job = make_job(self.tower, self.flat, self.vendor, estimate=D("100"))
        with self.assertRaises(ValidationError):
            create_bill_for_job(job)
        free = self.finished_job(cost="0")
        with self.assertRaises(ValidationError):
            create_bill_for_job(free)

    def test_a_cancelled_bill_allows_a_new_one(self):
        job = self.finished_job()
        bill, _ = create_bill_for_job(job)
        bill.status, bill.cancel_reason = "cancelled", "Wrong"
        bill.save()
        new, created = create_bill_for_job(job)
        self.assertTrue(created)
        self.assertNotEqual(new.pk, bill.pk)


class Pages(Setup):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("pay", "p@example.com", "pw")
        self.client.force_login(self.admin)
        self.bill = make_bill(self.vendor, self.tower, bill_number="PG-1", due_date=today() + timedelta(days=3))
        self.voucher = pay(self.bill, "250")
        self.expense = make_expense()

    def test_clean_addresses(self):
        self.assertEqual(reverse("admin:payables_vendorbill_changelist"), "/admin/vendor-bills/")
        self.assertEqual(reverse("admin:payables_expense_changelist"), "/admin/expenses/")
        self.assertEqual(reverse("admin:payables_paymentvoucher_changelist"), "/admin/payment-vouchers/")

    def test_lists_chips_and_search(self):
        chips = {"vendorbill": ("owing", "overdue", "week", "part", "paid", "cancelled"),
                 "expense": ("month", "company", "owner", "tenant", "cancelled"),
                 "paymentvoucher": ("issued", "month", "bills", "expenses", "voided")}
        for name, keys in chips.items():
            url = reverse(f"admin:payables_{name}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, name)
            for key in keys:
                self.assertEqual(self.client.get(url, {"chip": key}).status_code, 200, f"{name} {key}")
        self.assertContains(self.client.get(reverse("admin:payables_vendorbill_changelist"), {"q": "BILL-1"}), "Bills Plumbing")
        self.assertContains(self.client.get(reverse("admin:payables_expense_changelist"), {"q": "EXP-1"}), "DEWA")
        self.assertContains(self.client.get(reverse("admin:payables_paymentvoucher_changelist"), {"q": "PV-1"}), "Bills Plumbing")

    def test_forms_and_print_pages(self):
        urls = [
            reverse("admin:payables_vendorbill_add"), reverse("admin:payables_expense_add"), reverse("admin:payables_paymentvoucher_add"),
            reverse("admin:payables_paymentvoucher_add") + f"?bill={self.bill.pk}",
            reverse("admin:payables_vendorbill_change", args=[self.bill.pk]),
            reverse("admin:payables_expense_change", args=[self.expense.pk]),
            reverse("admin:payables_paymentvoucher_change", args=[self.voucher.pk]),
            reverse("admin:payables_paymentvoucher_print", args=[self.voucher.pk]),
            reverse("admin:maintenance_vendor_change", args=[self.vendor.pk]),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        page = self.client.get(reverse("admin:payables_paymentvoucher_print", args=[self.voucher.pk]))
        self.assertContains(page, "PV-0001")
        self.assertContains(page, "Two hundred fifty dirhams only")
        self.assertContains(self.client.get(f"{reverse('admin:payables_paymentvoucher_add')}?bill={self.bill.pk}"), "750")
        self.assertContains(self.client.get(reverse("admin:maintenance_vendor_change", args=[self.vendor.pk])), "BILL-0001")

    def test_adding_a_payment_from_the_form(self):
        data = {"bill": self.bill.pk, "voucher_date": today().isoformat(), "payee_name": "", "description": "", "amount": "750",
                "method": "transfer", "cheque_number": "", "bank_name": "", "reference": "TT-9", "notes": ""}
        response = self.client.post(reverse("admin:payables_paymentvoucher_add"), data)
        self.assertEqual(response.status_code, 302)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.payment_state(), "paid")
        data["amount"] = "1"
        self.assertEqual(self.client.post(reverse("admin:payables_paymentvoucher_add"), data).status_code, 200)  # nothing left to pay

    def test_voiding_from_the_form(self):
        url = reverse("admin:payables_paymentvoucher_change", args=[self.voucher.pk])
        self.assertEqual(self.client.post(url, {"status": "voided", "void_reason": "", "notes": ""}).status_code, 200)
        self.assertEqual(self.client.post(url, {"status": "voided", "void_reason": "Mistake", "notes": ""}).status_code, 302)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.paid_amount, D("0.00"))

    def test_pay_in_full_action(self):
        url = reverse("admin:payables_vendorbill_changelist")
        response = self.client.post(url, {"action": "pay_in_full", "_selected_action": [self.bill.pk]}, follow=True)
        self.assertContains(response, "payment voucher(s) made")
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.balance, D("0.00"))

    def test_create_bills_action_on_the_maintenance_list(self):
        job = make_job(self.tower, self.flat, self.vendor, estimate=D("300"))
        job.status, job.final_cost = "completed", D("300")
        job.full_clean()
        job.save()
        url = reverse("admin:maintenance_maintenancerequest_changelist")
        response = self.client.post(url, {"action": "create_bills_action", "_selected_action": [job.pk]}, follow=True)
        self.assertContains(response, "1 vendor bill(s) created")
        self.assertTrue(VendorBill.objects.filter(request=job).exists())

    def test_nothing_is_deleted(self):
        for name, obj in (("vendorbill", self.bill), ("expense", self.expense), ("paymentvoucher", self.voucher)):
            response = self.client.post(reverse(f"admin:payables_{name}_delete", args=[obj.pk]), {"post": "yes"})
            self.assertEqual(response.status_code, 403, name)

    def test_exports(self):
        for name, obj, code in (("vendorbill", self.bill, b"BILL-0001"), ("expense", self.expense, b"EXP-0001"), ("paymentvoucher", self.voucher, b"PV-0001")):
            response = self.client.post(reverse(f"admin:payables_{name}_changelist"), {"action": "export_csv", "_selected_action": [obj.pk]})
            self.assertIn(code, response.content, name)

    def test_people_without_permission_see_nothing(self):
        nobody = User.objects.create_user("nobody2", password="pw", is_staff=True)
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(reverse("admin:payables_paymentvoucher_print", args=[self.voucher.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("admin:payables_vendorbill_changelist")).status_code, 403)


class SampleData(TestCase):
    def test_seed_and_erase(self):
        from leases.seed import seed_leases
        from maintenance.seed import seed_maintenance
        from properties.seed import seed_properties
        from tenants.seed import seed_tenants

        seed_properties()
        seed_tenants()
        seed_leases()
        seed_maintenance()
        self.assertIn("vendor bills", seed.seed_payables())
        self.assertTrue(Expense.objects.exists() and VendorBill.objects.exists() and PaymentVoucher.objects.exists())
        counts = (Expense.objects.count(), VendorBill.objects.count())
        seed.seed_payables()
        self.assertEqual((Expense.objects.count(), VendorBill.objects.count()), counts)
        seed.erase_payables()
        self.assertFalse(Expense.objects.exists() or VendorBill.objects.exists() or PaymentVoucher.objects.exists())
        self.assertTrue(Vendor.objects.exists())

    def test_roles(self):
        Group.objects.create(name="Accountant")
        Group.objects.create(name="Property Manager")
        seed.grant_role_permissions()
        self.assertTrue(Group.objects.get(name="Accountant").permissions.filter(codename="add_paymentvoucher").exists())
        self.assertFalse(Group.objects.get(name="Property Manager").permissions.filter(codename="add_paymentvoucher").exists())

    def test_words_helper_is_shared(self):
        self.assertEqual(amount_in_words(500), "Five hundred dirhams only")
        self.assertIsNotNone(MaintenanceRequest)
