"""Checks for owner statements."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from agreements.tests import make_agreement
from leases.tests import make_lease, make_tenant
from payables.models import Expense, VendorBill
from payables.tests import make_bill, make_expense
from maintenance.tests import make_vendor
from properties.models import Owner
from properties.tests import building, unit

from . import seed
from .models import OwnerStatement, StatementLine

D = Decimal
today = timezone.localdate


def make_statement(owner, start, end, **kwargs):
    statement = OwnerStatement(owner=owner, period_start=start, period_end=end, **kwargs)
    statement.full_clean()
    statement.save()
    return statement


def move(statement, status, **kwargs):
    """Change the status the way the form does (checks first)."""
    statement.refresh_from_db()
    statement.status = status
    for key, value in kwargs.items():
        setattr(statement, key, value)
    statement.full_clean()
    statement.save()
    return statement


class Setup(TestCase):
    def setUp(self):
        self.tower = building("STB")
        self.owner = self.tower.owner
        self.flat = unit(self.tower, "101")
        self.tenant = make_tenant("Statement Tenant")
        self.lease = make_lease(self.tenant, self.flat, rent="40000")  # 4 payments of 10,000
        self.payments = list(self.lease.payments.filter(kind="rent").order_by("due_date"))
        self.vendor = make_vendor("Statement Vendor")
        self.start = today() - timedelta(days=30)

    def agreement(self, **values):
        values.setdefault("fee_percent_collected", D("5"))
        return make_agreement(self.owner, self.tower, **values)

    def pay(self, index=0):
        self.payments[index].record(today(), "cash")


class Numbers(Setup):
    def test_rent_and_percentage_fee(self):
        self.agreement()
        self.pay(0)
        statement = make_statement(self.owner, self.start, today())
        self.assertEqual(statement.code, "STM-0001")
        income = statement.lines.get(section="income")
        self.assertEqual(income.amount, D("10000.00"))
        self.assertIn(self.lease.code, income.description)
        self.assertEqual(statement.lines.get(section="fee").amount, D("500.00"))
        statement.refresh_from_db()
        self.assertEqual((statement.rent_total, statement.fee_total, statement.net_amount, statement.payout_due), (D("10000.00"), D("500.00"), D("9500.00"), D("9500.00")))

    def test_unpaid_payments_are_not_income(self):
        self.agreement()
        statement = make_statement(self.owner, self.start, today(), )
        self.assertFalse(statement.lines.filter(section="income").exists())

    def test_properties_of_other_owners_are_left_out(self):
        other = building("STO")
        other_unit = unit(other, "201")
        make_lease(make_tenant("Other Tenant"), other_unit, rent="20000").payments.first().record(today(), "cash")
        self.agreement()
        self.pay(0)
        statement = make_statement(self.owner, self.start, today())
        self.assertEqual(statement.lines.filter(section="income").count(), 1)

    def test_guaranteed_rent_replaces_the_rent(self):
        self.agreement(fee_percent_collected=None, guaranteed_rent_yearly=D("365000"))
        self.pay(0)
        statement = make_statement(self.owner, today() - timedelta(days=9), today())
        self.assertEqual(statement.lines.get(section="guarantee").amount, D("10000.00"))  # 10 days of 1,000
        self.assertFalse(statement.lines.filter(section="income").exists())
        self.assertEqual(statement.lines.get(section="info").amount, D("10000.00"))  # collected rent: for information only
        statement.refresh_from_db()
        self.assertEqual((statement.fee_total, statement.payout_due), (D("0.00"), D("10000.00")))

    def test_fixed_fee_is_prorated_by_days(self):
        self.agreement(fee_percent_collected=None, fee_fixed_yearly=D("36500"))
        statement = make_statement(self.owner, today() - timedelta(days=9), today())
        self.assertEqual(statement.lines.get(section="fee").amount, D("1000.00"))  # 10 days x 100

    def test_fee_on_the_contract_rent_is_charged_once_on_the_lease_start(self):
        self.agreement(fee_percent_collected=None, fee_percent_contract=D("2"))
        statement = make_statement(self.owner, self.start, today())
        self.assertEqual(statement.lines.get(section="fee").amount, D("800.00"))
        move(statement, "voided", void_reason="Test")
        again = make_statement(self.owner, self.start, today())
        self.assertEqual(again.lines.get(section="fee").amount, D("800.00"))

    def test_no_agreement_means_no_fee(self):
        self.pay(0)
        statement = make_statement(self.owner, self.start, today())
        self.assertEqual(statement.lines.filter(section="fee").count(), 0)
        self.assertEqual(statement.lines.get(section="income").amount, D("10000.00"))


class Costs(Setup):
    def setUp(self):
        super().setUp()
        self.agreement()

    def test_costs_charged_to_the_owner_are_deducted(self):
        make_bill(self.vendor, self.tower, amount=D("400"), charged_to="owner", bill_date=today() - timedelta(days=3))
        make_expense(property=self.tower, charged_to="owner", amount=D("100"), vat_applies=True)
        make_bill(self.vendor, self.tower, bill_number="CO", amount=D("999"), charged_to="company")
        cancelled = make_expense(property=self.tower, charged_to="owner", amount=D("55"))
        cancelled.cancelled, cancelled.cancel_reason = True, "Mistake"
        cancelled.save()
        gone = make_bill(self.vendor, self.tower, bill_number="CX", amount=D("77"), charged_to="owner")
        gone.status, gone.cancel_reason = "cancelled", "Duplicate"
        gone.save()
        statement = make_statement(self.owner, self.start, today())
        costs = statement.lines.filter(section="cost")
        self.assertEqual(sorted(c.amount for c in costs), [D("105.00"), D("400.00")])
        statement.refresh_from_db()
        self.assertEqual(statement.cost_total, D("505.00"))
        self.assertEqual(statement.payout_due, D("0.00"))
        self.assertEqual(statement.carried_forward, D("-505.00"))

    def test_a_bill_entered_late_is_picked_up_by_the_next_statement(self):
        self.pay(0)
        first = make_statement(self.owner, self.start, today() - timedelta(days=5), )
        move(first, "approved", adjustment=D("1"), adjustment_note="Opening")  # (it is empty otherwise)
        make_bill(self.vendor, self.tower, amount=D("300"), charged_to="owner", bill_date=today() - timedelta(days=20))  # dated inside the first period
        second = make_statement(self.owner, today() - timedelta(days=4), today())
        self.assertEqual(second.lines.get(section="cost").amount, D("300.00"))
        self.assertEqual(second.lines.get(section="income").amount, D("10000.00"))

    def test_nothing_is_counted_twice(self):
        make_bill(self.vendor, self.tower, amount=D("300"), charged_to="owner", bill_date=today() - timedelta(days=2))
        first = make_statement(self.owner, self.start, today() - timedelta(days=1))
        self.assertEqual(first.lines.filter(section="cost").count(), 1)
        move(first, "approved")
        later = make_statement(self.owner, today(), today())
        self.assertEqual(later.lines.filter(section="cost").count(), 0)

    def test_voiding_lets_the_items_go(self):
        make_bill(self.vendor, self.tower, amount=D("300"), charged_to="owner")
        first = make_statement(self.owner, self.start, today())
        move(first, "voided", void_reason="Wrong period")
        again = make_statement(self.owner, self.start, today())
        self.assertEqual(again.lines.filter(section="cost").count(), 1)


class Rules(Setup):
    def test_period_rules(self):
        with self.assertRaises(ValidationError):
            make_statement(self.owner, today(), today() - timedelta(days=1))
        with self.assertRaises(ValidationError):
            make_statement(self.owner, today(), today() + timedelta(days=1))
        with self.assertRaises(ValidationError):
            make_statement(Owner.objects.get(is_company=True), self.start, today())

    def test_statements_follow_each_other_without_gaps_or_overlaps(self):
        self.agreement()
        first = make_statement(self.owner, self.start, today() - timedelta(days=10))
        move(first, "approved", adjustment=D("1"), adjustment_note="x")
        with self.assertRaises(ValidationError) as caught:
            make_statement(self.owner, today() - timedelta(days=15), today())
        self.assertIn("period_start", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            make_statement(self.owner, today() - timedelta(days=5), today())  # leaves a gap
        self.assertIn("period_start", caught.exception.message_dict)
        make_statement(self.owner, today() - timedelta(days=9), today())

    def test_a_draft_must_be_settled_before_the_next(self):
        self.agreement()
        make_statement(self.owner, self.start, today() - timedelta(days=10))
        with self.assertRaises(ValidationError) as caught:
            make_statement(self.owner, today() - timedelta(days=9), today())
        self.assertIn("owner", caught.exception.message_dict)

    def test_adjustment_needs_a_note_and_changes_the_net(self):
        self.agreement()
        self.pay(0)
        statement = make_statement(self.owner, self.start, today())
        statement.adjustment = D("-250")
        with self.assertRaises(ValidationError):
            statement.full_clean()
        statement.adjustment_note = "Advance paid in March"
        statement.full_clean()
        statement.save()
        statement.refresh_from_db()
        self.assertEqual(statement.payout_due, D("9250.00"))

    def test_an_empty_statement_cannot_be_approved(self):
        self.agreement()
        statement = make_statement(self.owner, self.start, today())
        statement.status = "approved"
        with self.assertRaises(ValidationError):
            statement.full_clean()

    def test_approved_statement_is_locked(self):
        self.agreement()
        self.pay(0)
        statement = move(make_statement(self.owner, self.start, today()), "approved")
        statement.adjustment, statement.adjustment_note = D("5"), "late"
        with self.assertRaises(ValidationError):
            statement.full_clean()
        statement.refresh_from_db()
        statement.status = "draft"
        with self.assertRaises(ValidationError):
            statement.full_clean()

    def test_voiding_needs_a_reason_and_is_final(self):
        self.agreement()
        statement = make_statement(self.owner, self.start, today())
        statement.status = "voided"
        with self.assertRaises(ValidationError):
            statement.full_clean()
        move(statement, "voided", void_reason="Made by mistake")
        statement.status = "draft"
        with self.assertRaises(ValidationError):
            statement.full_clean()


class Payout(Setup):
    def test_paying_makes_a_voucher(self):
        self.agreement()
        self.pay(0)
        statement = move(make_statement(self.owner, self.start, today()), "approved")
        paid = move(statement, "paid", payout_method="transfer", payout_reference="TT-77")
        paid.refresh_from_db()
        voucher = paid.voucher
        self.assertEqual((voucher.amount, voucher.payee_name, voucher.reference), (D("9500.00"), self.owner.name, "TT-77"))
        self.assertIn(paid.code, voucher.description)
        self.assertEqual(paid.paid_on, today())

    def test_cheque_needs_a_number_and_a_paid_statement_cannot_change(self):
        self.agreement()
        self.pay(0)
        statement = move(make_statement(self.owner, self.start, today()), "approved")
        statement.status, statement.payout_method = "paid", "cheque"
        with self.assertRaises(ValidationError):
            statement.full_clean()
        move(statement, "paid", payout_method="cheque", payout_reference="000123")
        for status in ("draft", "voided"):
            statement.status = status
            with self.assertRaises(ValidationError):
                statement.full_clean()

    def test_a_statement_owing_money_pays_nothing_and_carries_forward(self):
        self.agreement(fee_percent_collected=None, fee_fixed_yearly=D("3650"))
        make_expense(property=self.tower, charged_to="owner", amount=D("500"), expense_date=today() - timedelta(days=12))
        first = make_statement(self.owner, self.start, today() - timedelta(days=10))
        first.refresh_from_db()
        self.assertEqual(first.payout_due, D("0.00"))
        self.assertLess(first.carried_forward, 0)
        move(first, "approved")
        move(first, "paid")
        self.assertIsNone(OwnerStatement.objects.get(pk=first.pk).voucher)
        self.pay(0)
        second = make_statement(self.owner, today() - timedelta(days=9), today())
        second.refresh_from_db()
        self.assertEqual(second.brought_forward, first.carried_forward)
        self.assertEqual((second.payout_due, second.carried_forward), (D("9190.00"), D("0.00")))  # 10,000 - 100 fee - 710 owed


class Pages(Setup):
    def setUp(self):
        super().setUp()
        self.agreement()
        self.pay(0)
        self.admin = User.objects.create_superuser("stm", "s@example.com", "pw")
        self.client.force_login(self.admin)
        self.statement = make_statement(self.owner, self.start, today())

    def test_clean_address_lists_and_chips(self):
        self.assertEqual(reverse("admin:statements_ownerstatement_changelist"), "/admin/owner-statements/")
        url = reverse("admin:statements_ownerstatement_changelist")
        self.assertContains(self.client.get(url), "STM-0001")
        for chip in ("draft", "topay", "paid", "voided"):
            self.assertEqual(self.client.get(url, {"chip": chip}).status_code, 200, chip)
        self.assertContains(self.client.get(url, {"q": "STM-1"}), "STM-0001")

    def test_pages_open(self):
        for url in (
            reverse("admin:statements_ownerstatement_add"),
            reverse("admin:statements_ownerstatement_change", args=[self.statement.pk]),
            reverse("admin:statements_ownerstatement_print", args=[self.statement.pk]),
            reverse("admin:properties_owner_change", args=[self.owner.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        page = self.client.get(reverse("admin:statements_ownerstatement_print", args=[self.statement.pk]))
        self.assertContains(page, "Owner Statement")
        self.assertContains(page, "Nine thousand five hundred dirhams only")
        self.assertContains(self.client.get(reverse("admin:statements_ownerstatement_change", args=[self.statement.pk])), "10,000.00")
        self.assertContains(self.client.get(reverse("admin:properties_owner_change", args=[self.owner.pk])), "STM-0001")

    def test_generating_from_the_add_form(self):
        move(self.statement, "voided", void_reason="Redo")
        response = self.client.post(reverse("admin:statements_ownerstatement_add"), {
            "owner": self.owner.pk, "period_start": self.start.isoformat(), "period_end": today().isoformat(), "notes": ""})
        self.assertEqual(response.status_code, 302)
        new = OwnerStatement.objects.exclude(pk=self.statement.pk).get()
        self.assertEqual(new.lines.filter(section="income").count(), 1)
        self.assertTrue(response["Location"].endswith(f"/{new.pk}/change/"))

    def test_approving_and_paying_from_the_form(self):
        url = reverse("admin:statements_ownerstatement_change", args=[self.statement.pk])
        draft = {"adjustment": "0", "adjustment_note": "", "status": "approved", "void_reason": "", "notes": ""}
        self.assertEqual(self.client.post(url, draft).status_code, 302)
        self.statement.refresh_from_db()
        self.assertEqual((self.statement.status, self.statement.approved_by), ("approved", self.admin))
        pay = {"payout_method": "transfer", "payout_reference": "TT-1", "paid_on": today().isoformat(), "status": "paid", "void_reason": "", "notes": ""}
        self.assertEqual(self.client.post(url, pay).status_code, 302)
        self.statement.refresh_from_db()
        self.assertEqual(self.statement.status, "paid")
        self.assertIsNotNone(self.statement.voucher)

    def test_actions(self):
        url = reverse("admin:statements_ownerstatement_changelist")
        response = self.client.post(url, {"action": "approve_selected", "_selected_action": [self.statement.pk]}, follow=True)
        self.assertContains(response, "1 statement(s) approved")
        self.statement.refresh_from_db()
        self.assertEqual(self.statement.status, "approved")

    def test_rebuild_picks_up_new_items(self):
        make_expense(property=self.tower, charged_to="owner", amount=D("80"))
        self.assertFalse(self.statement.lines.filter(section="cost").exists())
        response = self.client.post(reverse("admin:statements_ownerstatement_changelist"),
                                    {"action": "rebuild_selected", "_selected_action": [self.statement.pk]}, follow=True)
        self.assertContains(response, "rebuilt")
        self.assertEqual(self.statement.lines.filter(section="cost").count(), 1)

    def test_people_without_the_approve_permission_cannot_change_the_status(self):
        group = Group.objects.create(name="Statement editors")
        group.permissions.add(*Permission.objects.filter(content_type__app_label="statements", codename__in=["view_ownerstatement", "change_ownerstatement"]))
        editor = User.objects.create_user("stm_editor", password="pw", is_staff=True)
        editor.groups.add(group)
        self.client.force_login(editor)
        url = reverse("admin:statements_ownerstatement_change", args=[self.statement.pk])
        self.assertNotContains(self.client.get(url), 'name="status"')
        self.client.post(url, {"adjustment": "0", "adjustment_note": "", "status": "approved", "void_reason": "", "notes": ""})
        self.statement.refresh_from_db()
        self.assertEqual(self.statement.status, "draft")

    def test_nothing_is_deleted_and_export_works(self):
        response = self.client.post(reverse("admin:statements_ownerstatement_delete", args=[self.statement.pk]), {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("admin:statements_ownerstatement_changelist"), {"action": "export_csv", "_selected_action": [self.statement.pk]})
        self.assertIn(b"STM-0001", response.content)


class SampleData(TestCase):
    def test_seed_and_erase(self):
        from agreements.seed import seed_agreements
        from leases.seed import seed_leases
        from maintenance.seed import seed_maintenance
        from payables.seed import seed_payables
        from properties.seed import seed_properties
        from tenants.seed import seed_tenants

        seed_properties()
        seed_agreements()
        seed_tenants()
        seed_leases()
        seed_maintenance()
        seed_payables()
        self.assertIn("owner statements", seed.seed_statements())
        count = OwnerStatement.objects.count()
        self.assertGreater(count, 0)
        seed.seed_statements()
        self.assertEqual(OwnerStatement.objects.count(), count)
        seed.erase_statements()
        self.assertFalse(OwnerStatement.objects.exists() or StatementLine.objects.exists())
        self.assertTrue(VendorBill.objects.exists() or Expense.objects.exists())

    def test_roles(self):
        Group.objects.create(name="Accountant")
        Group.objects.create(name="Property Manager")
        seed.grant_role_permissions()
        self.assertTrue(Group.objects.get(name="Accountant").permissions.filter(codename="approve_ownerstatement").exists())
        self.assertFalse(Group.objects.get(name="Property Manager").permissions.filter(codename="approve_ownerstatement").exists())
