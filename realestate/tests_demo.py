"""Checks for demo data (load and remove)."""

from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import utils
from .demo import DemoError, has_demo_data, remove_demo, seed_demo
from .models import (
    Cheque,
    DemoRecord,
    Expense,
    Lease,
    Owner,
    OwnerStatement,
    Property,
    Tenant,
    Unit,
    Vendor,
)

ALL_MODELS = (Owner, Property, Unit, Tenant, Lease, Cheque, Expense, Vendor, OwnerStatement)


class IbanMakerTests(SimpleTestCase):
    def test_official_example(self):
        self.assertEqual(utils.make_iban("033", "1234567890123456"), "AE070331234567890123456")

    def test_made_ibans_are_valid(self):
        for bank, account in (("026", "0001234567890123"), ("044", "9999999999999999")):
            self.assertTrue(utils.iban_is_valid(utils.make_iban(bank, account)))


class DemoDataTests(TestCase):
    def test_load_creates_a_full_picture(self):
        result = seed_demo()
        self.assertTrue(has_demo_data())
        self.assertGreater(result["records"], 50)
        self.assertEqual(Lease.objects.count(), 7)
        self.assertEqual(Property.objects.count(), 3)
        self.assertEqual(Tenant.objects.count(), 8)

        statuses = set(Cheque.objects.values_list("status", flat=True))
        for wanted in ("pending", "received", "cleared", "returned"):
            self.assertIn(wanted, statuses)

        for owner in Owner.objects.all():
            self.assertTrue(utils.iban_is_valid(owner.iban), owner.iban)

        numbers = [n for n in Cheque.objects.exclude(cheque_number="").values_list("cheque_number", flat=True)]
        self.assertEqual(len(numbers), len(set(numbers)))

        statement = OwnerStatement.objects.get()
        self.assertEqual(statement.status, "draft")
        self.assertGreater(statement.lines.count(), 0)
        self.assertGreater(statement.net_payout, Decimal("0"))

    def test_cheque_desk_has_something_on_every_list(self):
        seed_demo()
        today = Cheque.objects.filter(status="received").count()
        self.assertGreater(today, 0)
        self.assertTrue(Cheque.objects.filter(status="returned").exists())

    def test_cannot_load_twice(self):
        seed_demo()
        with self.assertRaises(DemoError):
            seed_demo()
        self.assertEqual(Lease.objects.count(), 7)

    def test_remove_deletes_everything_it_made(self):
        seed_demo()
        result = remove_demo()
        self.assertGreater(result["removed"], 50)
        self.assertEqual(result["kept"], [])
        self.assertFalse(has_demo_data())
        for model in ALL_MODELS:
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def test_remove_never_touches_real_records(self):
        real_owner = Owner.objects.create(name="Real Owner", passport_number="R1234567")
        real_property = Property.objects.create(name="Real Tower", ownership_type="company")
        seed_demo()
        remove_demo()
        self.assertTrue(Owner.objects.filter(pk=real_owner.pk).exists())
        self.assertTrue(Property.objects.filter(pk=real_property.pk).exists())
        self.assertEqual(Owner.objects.count(), 1)

    def test_remove_keeps_demo_records_that_real_data_uses(self):
        seed_demo()
        demo_property = Property.objects.get(name__startswith="Marina Heights")
        Expense.objects.create(
            property=demo_property, description="Real invoice", amount=Decimal("100"),
            bearer="company",
        )
        result = remove_demo()
        self.assertTrue(Property.objects.filter(pk=demo_property.pk).exists())
        self.assertTrue(any("Marina Heights" in line for line in result["kept"]))
        self.assertTrue(Expense.objects.filter(description="Real invoice").exists())
        # What could be removed was removed, and the kept record is still tracked.
        self.assertEqual(Lease.objects.count(), 0)
        self.assertTrue(has_demo_data())

    def test_can_load_again_after_removing(self):
        seed_demo()
        remove_demo()
        seed_demo()
        self.assertEqual(Lease.objects.count(), 7)

    def test_commands(self):
        out = StringIO()
        call_command("seed_demo", stdout=out)
        self.assertIn("Demo data loaded", out.getvalue())
        out = StringIO()
        call_command("remove_demo", stdout=out)
        self.assertIn("Removed", out.getvalue())
        self.assertEqual(DemoRecord.objects.count(), 0)


class DemoPageTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "boss", "boss@example.com", "a-long-test-password-1"
        )
        self.staff = get_user_model().objects.create_user(
            "clerk", password="a-long-test-password-1", is_staff=True
        )
        self.url = reverse("admin:realestate_demorecord_changelist")

    def test_page_buttons_work_for_the_superuser(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url)
        self.assertContains(page, "Load demo data")
        self.assertRedirects(self.client.post(self.url, {"action": "seed"}), self.url)
        self.assertTrue(has_demo_data())
        page = self.client.get(self.url)
        self.assertContains(page, "Remove demo data")
        self.assertContains(page, "Payments")
        self.assertRedirects(self.client.post(self.url, {"action": "remove"}), self.url)
        self.assertFalse(has_demo_data())

    def test_demo_pages_open_with_demo_data(self):
        self.client.force_login(self.admin)
        seed_demo()
        for name in ("index", "realestate_cheque_desk", "realestate_cheque_changelist",
                     "realestate_lease_changelist", "realestate_expense_changelist",
                     "realestate_ownerstatement_changelist", "realestate_unit_changelist"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f"admin:{name}")).status_code, 200)
        statement = OwnerStatement.objects.get()
        change = reverse("admin:realestate_ownerstatement_change", args=[statement.pk])
        self.assertEqual(self.client.get(change).status_code, 200)

    def test_other_staff_cannot_use_it(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.post(self.url, {"action": "seed"})
        self.assertFalse(has_demo_data())
