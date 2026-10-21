"""Checks for vendors, expenses, maintenance and owner statements."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse

from . import utils
from .models import (
    Cheque,
    Expense,
    MaintenanceRequest,
    OwnerStatement,
    Property,
    Unit,
    Vendor,
)
from .tests import BaseCase

D = Decimal


class MoneyHelperTests(SimpleTestCase):
    def test_split_vat(self):
        self.assertEqual(utils.split_vat(D("10500"), D("5")), (D("10000.00"), D("500.00")))
        self.assertEqual(utils.split_vat(D("10000"), D("0")), (D("10000.00"), D("0.00")))
        net, vat = utils.split_vat(D("333.33"), D("5"))
        self.assertEqual(net + vat, D("333.33"))

    def test_percent_of(self):
        self.assertEqual(utils.percent_of(D("10000"), D("10")), D("1000.00"))
        self.assertEqual(utils.percent_of(D("1000"), D("5")), D("50.00"))


class FinanceCase(BaseCase):
    """A third-party property (owner fee 10%) with one lease paying 5% VAT."""

    def setUp(self):
        super().setUp()
        self.period_start = date(2026, 1, 1)
        self.period_end = date(2026, 1, 31)
        self.lease = self.make_lease(
            vat_percent=D("5"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )

    def cleared_cheque(self, number="C1", amount="10500", on=date(2026, 1, 10)):
        return self.make_cheque(
            self.lease,
            number=number,
            amount=D(amount),
            cheque_date=on,
            status=Cheque.Status.CLEARED,
            cleared_date=on,
            purpose=Cheque.Purpose.RENT,
        )

    def owner_expense(self, amount="300", on=date(2026, 1, 15), **extra):
        return Expense.objects.create(
            property=self.property,
            description="Fix AC",
            amount=D(amount),
            expense_date=on,
            bearer=Expense.Bearer.OWNER,
            **extra,
        )

    def statement(self, start=None, end=None, **extra):
        return OwnerStatement.objects.create(
            owner=self.owner,
            period_start=start or self.period_start,
            period_end=end or self.period_end,
            **extra,
        )


class ExpenseTests(FinanceCase):
    def test_company_property_cannot_charge_an_owner(self):
        own = Property.objects.create(name="Our Building", ownership_type="company")
        expense = Expense(
            property=own, description="Paint", amount=100, bearer=Expense.Bearer.OWNER
        )
        with self.assertRaises(ValidationError) as caught:
            expense.full_clean()
        self.assertIn("bearer", caught.exception.message_dict)

    def test_unit_must_belong_to_property(self):
        other = Property.objects.create(name="Other", ownership_type="company")
        stranger = Unit.objects.create(property=other, unit_number="9", rent_amount=1)
        expense = Expense(
            property=self.property, unit=stranger, description="x", amount=100
        )
        with self.assertRaises(ValidationError) as caught:
            expense.full_clean()
        self.assertIn("unit", caught.exception.message_dict)

    def test_vat_cannot_exceed_total(self):
        expense = Expense(
            property=self.property, description="x", amount=100, vat_amount=200
        )
        with self.assertRaises(ValidationError):
            expense.full_clean()

    def test_maintenance_request_dates_and_cost(self):
        request = MaintenanceRequest.objects.create(
            property=self.property, unit=self.unit, title="Leak"
        )
        request.status = MaintenanceRequest.Status.COMPLETED
        request.save()
        self.assertIsNotNone(request.completed_on)
        self.owner_expense(maintenance_request=request, amount="250")
        self.assertEqual(request.total_cost, D("250.00"))
        request.status = MaintenanceRequest.Status.OPEN
        request.save()
        self.assertIsNone(request.completed_on)

    def test_vendor_saves(self):
        vendor = Vendor.objects.create(name="Cool AC LLC", phone="+971 50 123 4567")
        vendor.full_clean()


class StatementTests(FinanceCase):
    def test_numbers_add_up(self):
        self.cleared_cheque()
        self.owner_expense()
        statement = self.statement()
        statement.recalculate()
        totals = statement.totals()
        self.assertEqual(totals["rent"], D("10000.00"))
        self.assertEqual(totals["rent_vat"], D("500.00"))
        self.assertEqual(totals["fee"], D("1000.00"))  # 10% of rent without VAT
        self.assertEqual(totals["fee_vat"], D("50.00"))  # 5% of the fee
        self.assertEqual(totals["expenses"], D("300.00"))
        self.assertEqual(totals["net_payout"], D("9150.00"))

    def test_vat_can_be_kept_back(self):
        self.cleared_cheque()
        statement = self.statement(pay_vat_to_owner=False)
        statement.recalculate()
        self.assertEqual(statement.totals()["net_payout"], D("8950.00"))

    def test_property_fee_overrides_owner_default(self):
        self.property.management_fee_percent = D("5")
        self.property.save()
        self.cleared_cheque()
        statement = self.statement()
        statement.recalculate()
        self.assertEqual(statement.totals()["fee"], D("500.00"))

    def test_only_cleared_rent_in_the_period_counts(self):
        self.cleared_cheque("A", on=date(2026, 1, 10))
        self.cleared_cheque("B", on=date(2026, 2, 10))  # other month
        self.make_cheque(
            self.lease, number="D", amount=D("999"), cheque_date=date(2026, 1, 5),
            status=Cheque.Status.DEPOSITED,
        )  # not cleared
        self.make_cheque(
            self.lease, number="E", amount=D("888"), cheque_date=date(2026, 1, 6),
            status=Cheque.Status.CLEARED, cleared_date=date(2026, 1, 6),
            purpose=Cheque.Purpose.SECURITY_DEPOSIT,
        )  # a deposit is not rent
        statement = self.statement()
        statement.recalculate()
        self.assertEqual(statement.totals()["rent"], D("10000.00"))

    def test_only_owner_expenses_count(self):
        self.owner_expense("100")
        Expense.objects.create(
            property=self.property, description="ours", amount=D("500"),
            expense_date=date(2026, 1, 12), bearer=Expense.Bearer.COMPANY,
        )
        Expense.objects.create(
            property=self.property, description="tenant", amount=D("700"),
            expense_date=date(2026, 1, 12), bearer=Expense.Bearer.TENANT,
        )
        statement = self.statement()
        statement.recalculate()
        self.assertEqual(statement.totals()["expenses"], D("100.00"))

    def test_company_owned_property_is_never_on_a_statement(self):
        own = Property.objects.create(name="Ours", ownership_type="company")
        Expense.objects.create(
            property=own, description="x", amount=D("50"),
            expense_date=date(2026, 1, 12), bearer=Expense.Bearer.COMPANY,
        )
        statement = self.statement()
        self.assertEqual(statement.recalculate(), 0)

    def test_recalculate_twice_does_not_double_count(self):
        self.cleared_cheque()
        statement = self.statement()
        statement.recalculate()
        statement.recalculate()
        self.assertEqual(statement.totals()["rent"], D("10000.00"))
        self.assertEqual(statement.lines.count(), 2)  # one rent line, one fee line

    def test_an_item_cannot_be_on_two_statements(self):
        self.cleared_cheque()
        first = self.statement()
        first.recalculate()
        second = self.statement(date(2026, 1, 1), date(2026, 3, 31))
        with self.assertRaises(ValidationError):
            second.full_clean()  # overlaps the first statement

    def test_overlapping_periods_are_refused(self):
        self.statement()
        clash = OwnerStatement(
            owner=self.owner, period_start=date(2026, 1, 20), period_end=date(2026, 2, 20)
        )
        with self.assertRaises(ValidationError):
            clash.full_clean()

    def test_statement_gets_a_number(self):
        self.assertTrue(self.statement().number.startswith("OS-2026-"))

    def test_workflow(self):
        self.cleared_cheque()
        statement = self.statement()
        statement.recalculate()
        user = get_user_model().objects.create_user("clerk", password="x" * 12)

        with self.assertRaises(ValidationError):
            statement.mark_paid(method="bank_transfer", reference="R1")  # not approved
        statement.approve(user)
        self.assertEqual(statement.status, "approved")
        with self.assertRaises(ValidationError):
            statement.recalculate()  # locked
        with self.assertRaises(ValidationError):
            statement.mark_paid(method="bank_transfer", reference="")  # needs a reference
        statement.mark_paid(method="bank_transfer", reference="TRX-1", user=user)
        self.assertEqual(statement.status, "paid")
        self.assertEqual(statement.pay_reference, "TRX-1")
        with self.assertRaises(ValidationError):
            statement.reopen()
        with self.assertRaises(ValidationError):
            statement.delete()

    def test_cash_payment_needs_no_reference(self):
        self.cleared_cheque()
        statement = self.statement()
        statement.recalculate()
        statement.approve()
        statement.mark_paid(method="cash")
        self.assertEqual(statement.status, "paid")

    def test_cannot_approve_with_new_items_missing(self):
        statement = self.statement()
        statement.recalculate()
        self.cleared_cheque()  # cleared after the last calculation
        with self.assertRaises(ValidationError):
            statement.approve()
        statement.recalculate()
        statement.approve()

    def test_empty_statement_cannot_be_approved(self):
        statement = self.statement()
        statement.recalculate()
        with self.assertRaises(ValidationError):
            statement.approve()

    def test_reopen_then_delete_frees_the_items(self):
        cheque = self.cleared_cheque()
        statement = self.statement()
        statement.recalculate()
        statement.approve()
        statement.reopen()
        statement.delete()
        cheque.refresh_from_db()
        self.assertFalse(hasattr(cheque, "statement_line"))

    def test_approved_statement_locks_its_expenses(self):
        expense = self.owner_expense()
        statement = self.statement()
        statement.recalculate()
        self.cleared_cheque()
        statement.recalculate()
        statement.approve()
        expense.refresh_from_db()
        self.assertTrue(expense.is_locked)
        expense.amount = D("1")
        with self.assertRaises(ValidationError):
            expense.full_clean()

    def test_negative_balance_is_carried_forward(self):
        self.owner_expense("5000", on=date(2026, 1, 15))
        january = self.statement()
        january.recalculate()
        january.approve()
        self.assertTrue(january.net_payout < 0)
        with self.assertRaises(ValidationError):
            january.mark_paid(method="cash")  # nothing to pay

        self.cleared_cheque("F1", on=date(2026, 2, 10))
        february = self.statement(date(2026, 2, 1), date(2026, 2, 28))
        february.recalculate()
        self.assertEqual(february.brought_forward, january.net_payout)
        # February alone, plus January's shortfall
        this_month_alone = D("10000") + D("500") - D("1000") - D("50")
        self.assertEqual(
            february.net_payout, this_month_alone + january.net_payout
        )

    def test_uncovered_items_are_reported(self):
        self.cleared_cheque("OLD", on=date(2025, 12, 10))
        statement = self.statement()
        self.assertEqual(statement.uncovered_items()["cheques"], 1)


class FinanceAdminTests(FinanceCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            "boss", "boss@example.com", "a-long-test-password-1"
        )
        self.client.force_login(self.user)
        self.cleared_cheque()
        self.expense = self.owner_expense()
        self.vendor = Vendor.objects.create(name="Cool AC LLC")
        self.request = MaintenanceRequest.objects.create(
            property=self.property, unit=self.unit, title="AC not cooling"
        )

    def test_pages_open(self):
        for model in ("vendor", "expense", "maintenancerequest", "ownerstatement"):
            with self.subTest(model=model):
                self.assertEqual(
                    self.client.get(reverse(f"admin:realestate_{model}_changelist")).status_code, 200
                )
                self.assertEqual(
                    self.client.get(reverse(f"admin:realestate_{model}_add")).status_code, 200
                )
        for model, obj in (
            ("vendor", self.vendor),
            ("expense", self.expense),
            ("maintenancerequest", self.request),
        ):
            with self.subTest(model=model, kind="change"):
                self.assertEqual(
                    self.client.get(
                        reverse(f"admin:realestate_{model}_change", args=[obj.pk])
                    ).status_code,
                    200,
                )

    def test_expense_filters(self):
        base = reverse("admin:realestate_expense_changelist")
        for value in ("waiting", "draft", "locked"):
            with self.subTest(value=value):
                self.assertEqual(self.client.get(f"{base}?statement={value}").status_code, 200)

    def test_creating_a_statement_calculates_it(self):
        response = self.client.post(
            reverse("admin:realestate_ownerstatement_add"),
            {
                "owner": self.owner.pk,
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "fee_vat_percent": "5",
                "pay_vat_to_owner": "on",
                "notes": "",
                "lines-TOTAL_FORMS": "0",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(response.status_code, 302)
        statement = OwnerStatement.objects.get()
        self.assertEqual(statement.totals()["net_payout"], D("9150.00"))

    def test_statement_page_buttons_and_print(self):
        statement = self.statement()
        statement.recalculate()
        change = reverse("admin:realestate_ownerstatement_change", args=[statement.pk])
        page = self.client.get(change)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Approve")
        self.assertContains(page, "9,150.00")

        def act(name, data=None):
            return self.client.post(
                reverse("admin:realestate_ownerstatement_action", args=[statement.pk, name]),
                data or {},
            )

        self.assertEqual(self.client.get(
            reverse("admin:realestate_ownerstatement_action", args=[statement.pk, "approve"])
        ).status_code, 405)
        self.assertRedirects(act("approve"), change)
        statement.refresh_from_db()
        self.assertEqual(statement.status, "approved")

        act("pay", {"paid_on": "2026-02-02", "pay_method": "bank_transfer", "pay_reference": "TRX9"})
        statement.refresh_from_db()
        self.assertEqual(statement.status, "paid")
        self.assertEqual(statement.paid_on, date(2026, 2, 2))

        printed = self.client.get(
            reverse("admin:realestate_ownerstatement_print", args=[statement.pk])
        )
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, statement.number)
        self.assertContains(printed, "Marina Tower")
        self.assertContains(printed, "9,150.00")
        self.assertEqual(self.client.get(change).status_code, 200)

    def test_paid_statement_cannot_be_deleted_in_admin(self):
        statement = self.statement()
        statement.recalculate()
        statement.approve()
        url = reverse("admin:realestate_ownerstatement_delete", args=[statement.pk])
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_bulk_actions_on_requests(self):
        url = reverse("admin:realestate_maintenancerequest_changelist")
        for action in ("action_in_progress", "action_completed"):
            response = self.client.post(
                url, {"action": action, "_selected_action": [self.request.pk]}, follow=True
            )
            self.assertEqual(response.status_code, 200)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "completed")

    def test_expense_receipts_stay_private(self):
        self.client.logout()
        self.assertEqual(self.client.get("/media/anything.pdf").status_code, 302)
