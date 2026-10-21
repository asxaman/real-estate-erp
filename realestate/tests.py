"""Automatic checks. Run them with:  python manage.py test"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from . import utils
from .models import Cheque, Lease, Owner, Property, Tenant, Unit


class HelperTests(SimpleTestCase):
    def test_emirates_id_is_formatted(self):
        for typed in ("784199012345671", "784 1990 1234567 1", "784-1990-1234567-1"):
            self.assertEqual(utils.normalize_emirates_id(typed), "784-1990-1234567-1")

    def test_iban_check(self):
        self.assertTrue(utils.iban_is_valid("AE07 0331 2345 6789 0123 456"))
        self.assertTrue(utils.iban_is_valid("ae070331234567890123456"))
        self.assertFalse(utils.iban_is_valid("AE08 0331 2345 6789 0123 456"))
        self.assertFalse(utils.iban_is_valid("AE07 0331 2345 6789 0123 45"))
        self.assertFalse(utils.iban_is_valid(""))

    def test_add_months_keeps_month_end(self):
        from datetime import date

        self.assertEqual(utils.add_months(date(2027, 1, 31), 1), date(2027, 2, 28))
        self.assertEqual(utils.add_months(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(utils.add_months(date(2026, 11, 15), 3), date(2027, 2, 15))

    def test_split_amount_adds_up(self):
        for total, parts in (("100000", 3), ("99999.99", 12), ("45000", 1)):
            amounts = utils.split_amount(Decimal(total), parts)
            self.assertEqual(len(amounts), parts)
            self.assertEqual(sum(amounts), Decimal(total))


class BaseCase(TestCase):
    """Creates one owner, property, unit and tenant for every test."""

    def setUp(self):
        self.today = timezone.localdate()
        self.owner = Owner.objects.create(
            name="Owner One",
            passport_number="P1234567",
            passport_expiry=self.today + timedelta(days=400),
        )
        self.property = Property.objects.create(
            name="Marina Tower", ownership_type="third_party", owner=self.owner
        )
        self.unit = Unit.objects.create(
            property=self.property, unit_number="101", rent_amount=40000
        )
        self.tenant = Tenant.objects.create(
            name="Tenant One",
            emirates_id="784-1990-1234567-1",
            emirates_id_expiry=self.today + timedelta(days=365),
        )

    def make_lease(self, **overrides):
        values = dict(
            tenant=self.tenant,
            unit=self.unit,
            start_date=self.today,
            end_date=self.today + timedelta(days=364),
            annual_rent=Decimal("40000"),
            number_of_payments=4,
        )
        values.update(overrides)
        return Lease.objects.create(**values)

    def make_cheque(self, lease, number="000111", **overrides):
        values = dict(
            lease=lease,
            cheque_number=number,
            bank_name="Emirates NBD",
            amount=Decimal("10000"),
            cheque_date=self.today,
            status=Cheque.Status.RECEIVED,
        )
        values.update(overrides)
        return Cheque.objects.create(**values)


class PartyTests(BaseCase):
    def test_individual_needs_identification(self):
        with self.assertRaises(ValidationError) as caught:
            Tenant(name="No ID").full_clean()
        self.assertIn("emirates_id", caught.exception.message_dict)

    def test_company_needs_trade_licence(self):
        with self.assertRaises(ValidationError) as caught:
            Tenant(name="Acme LLC", tenant_type="company").full_clean()
        self.assertIn("trade_licence_number", caught.exception.message_dict)

    def test_id_needs_expiry_date(self):
        with self.assertRaises(ValidationError) as caught:
            Tenant(name="A", emirates_id="784-1990-1234567-1").full_clean()
        self.assertIn("emirates_id_expiry", caught.exception.message_dict)

    def test_emirates_id_is_normalised_on_clean(self):
        tenant = Tenant(
            name="A",
            emirates_id="784199012345671",
            emirates_id_expiry=self.today + timedelta(days=100),
        )
        tenant.full_clean()
        self.assertEqual(tenant.emirates_id, "784-1990-1234567-1")

    def test_bad_emirates_id_is_rejected(self):
        tenant = Tenant(
            name="A", emirates_id="123", emirates_id_expiry=self.today + timedelta(days=100)
        )
        with self.assertRaises(ValidationError) as caught:
            tenant.full_clean()
        self.assertIn("emirates_id", caught.exception.message_dict)

    def test_iban_is_cleaned_and_checked(self):
        owner = Owner(
            name="O",
            passport_number="P7654321",
            passport_expiry=self.today + timedelta(days=100),
            iban="ae07 0331 2345 6789 0123 456",
        )
        owner.full_clean()
        self.assertEqual(owner.iban, "AE070331234567890123456")

        owner.iban = "AE08 0331 2345 6789 0123 456"
        with self.assertRaises(ValidationError) as caught:
            owner.full_clean()
        self.assertIn("iban", caught.exception.message_dict)

    def test_document_states(self):
        self.assertEqual(self.tenant.compliance_state, "ok")

        self.tenant.emirates_id_expiry = self.today + timedelta(days=20)
        self.assertEqual(self.tenant.compliance_state, "expiring")

        self.tenant.emirates_id_expiry = self.today - timedelta(days=1)
        self.assertEqual(self.tenant.compliance_state, "expired")

    def test_cannot_verify_with_expired_document(self):
        tenant = Tenant(
            name="A",
            emirates_id="784-1990-1234567-1",
            emirates_id_expiry=self.today - timedelta(days=5),
            kyc_status="verified",
        )
        with self.assertRaises(ValidationError) as caught:
            tenant.full_clean()
        self.assertIn("kyc_status", caught.exception.message_dict)

    def test_verified_date_is_filled_in(self):
        tenant = Tenant.objects.create(
            name="V",
            emirates_id="784-1991-7654321-2",
            emirates_id_expiry=self.today + timedelta(days=100),
            kyc_status="verified",
        )
        self.assertEqual(tenant.kyc_verified_on, self.today)


class PropertyTests(BaseCase):
    def test_company_property_loses_owner_and_fee(self):
        prop = Property.objects.create(
            name="Own Villa",
            ownership_type="company",
            owner=self.owner,
            management_fee_percent=Decimal("7"),
        )
        prop.refresh_from_db()
        self.assertIsNone(prop.owner)
        self.assertIsNone(prop.management_fee_percent)
        self.assertIsNone(prop.effective_fee_percent)

    def test_fee_falls_back_to_owner_default(self):
        self.assertEqual(self.property.effective_fee_percent, self.owner.management_fee_percent)
        self.property.management_fee_percent = Decimal("7.5")
        self.assertEqual(self.property.effective_fee_percent, Decimal("7.5"))

    def test_third_party_needs_owner(self):
        with self.assertRaises(ValidationError) as caught:
            Property(name="X", ownership_type="third_party").full_clean()
        self.assertIn("owner", caught.exception.message_dict)


class UnitStatusTests(BaseCase):
    def check(self, expected):
        self.assertEqual(self.unit.get_status(), expected)
        annotated = Unit.objects.with_status().get(pk=self.unit.pk)
        self.assertEqual(annotated.status_calc, expected)

    def test_vacant_when_no_lease(self):
        self.check("vacant")

    def test_occupied_with_running_lease(self):
        self.make_lease()
        self.check("occupied")

    def test_reserved_for_future_lease(self):
        self.make_lease(
            start_date=self.today + timedelta(days=30),
            end_date=self.today + timedelta(days=394),
        )
        self.check("reserved")

    def test_vacant_after_termination(self):
        lease = self.make_lease()
        lease.status = Lease.Status.TERMINATED
        lease.save()
        self.check("vacant")

    def test_maintenance_flag(self):
        self.unit.under_maintenance = True
        self.unit.save()
        self.check("maintenance")

    def test_running_lease_beats_maintenance_flag(self):
        self.make_lease()
        self.unit.under_maintenance = True
        self.unit.save()
        self.check("occupied")


class LeaseTests(BaseCase):
    def test_overlapping_active_lease_is_rejected(self):
        self.make_lease()
        clash = Lease(
            tenant=self.tenant,
            unit=self.unit,
            start_date=self.today + timedelta(days=100),
            end_date=self.today + timedelta(days=464),
            annual_rent=Decimal("40000"),
        )
        with self.assertRaises(ValidationError) as caught:
            clash.full_clean()
        self.assertIn("unit", caught.exception.message_dict)

    def test_back_to_back_leases_are_allowed(self):
        first = self.make_lease()
        second = Lease(
            tenant=self.tenant,
            unit=self.unit,
            start_date=first.end_date,
            end_date=first.end_date + timedelta(days=364),
            annual_rent=Decimal("42000"),
        )
        second.full_clean()

    def test_end_must_be_after_start(self):
        bad = Lease(
            tenant=self.tenant,
            unit=self.unit,
            start_date=self.today,
            end_date=self.today,
            annual_rent=Decimal("1000"),
        )
        with self.assertRaises(ValidationError) as caught:
            bad.full_clean()
        self.assertIn("end_date", caught.exception.message_dict)

    def test_vat_and_total(self):
        lease = self.make_lease(vat_percent=Decimal("5"))
        self.assertEqual(lease.vat_amount, Decimal("2000.00"))
        self.assertEqual(lease.contract_total, Decimal("42000.00"))

    def test_schedule_creates_the_right_cheques(self):
        lease = self.make_lease(number_of_payments=4, vat_percent=Decimal("5"))
        self.assertEqual(lease.generate_cheque_schedule(), 4)

        cheques = list(lease.cheques.order_by("installment_no"))
        self.assertEqual(len(cheques), 4)
        self.assertTrue(all(c.status == Cheque.Status.PENDING for c in cheques))
        self.assertEqual(sum(c.amount for c in cheques), lease.contract_total)
        for index, cheque in enumerate(cheques):
            self.assertEqual(cheque.cheque_date, utils.add_months(lease.start_date, 3 * index))

    def test_schedule_is_not_created_twice(self):
        lease = self.make_lease()
        lease.generate_cheque_schedule()
        self.assertEqual(lease.generate_cheque_schedule(), 0)
        self.assertEqual(lease.cheques.count(), 4)

    def test_two_year_lease_schedule(self):
        lease = self.make_lease(
            end_date=self.today + timedelta(days=729), number_of_payments=2
        )
        self.assertEqual(lease.lease_years, 2)
        self.assertEqual(lease.generate_cheque_schedule(), 4)
        self.assertEqual(sum(c.amount for c in lease.cheques.all()), Decimal("80000.00"))


class ChequeTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.lease = self.make_lease()

    def test_received_cheque_needs_number_and_bank(self):
        cheque = Cheque(
            lease=self.lease,
            amount=Decimal("100"),
            cheque_date=self.today,
            status=Cheque.Status.RECEIVED,
        )
        with self.assertRaises(ValidationError) as caught:
            cheque.full_clean()
        self.assertIn("cheque_number", caught.exception.message_dict)
        self.assertIn("bank_name", caught.exception.message_dict)

    def test_expected_cheque_can_be_blank(self):
        Cheque(
            lease=self.lease,
            amount=Decimal("100"),
            cheque_date=self.today,
            status=Cheque.Status.PENDING,
        ).full_clean()

    def test_same_number_same_bank_is_rejected(self):
        self.make_cheque(self.lease, number="555")
        duplicate = Cheque(
            lease=self.lease,
            cheque_number="555",
            bank_name="emirates nbd",
            amount=Decimal("100"),
            cheque_date=self.today,
            status=Cheque.Status.RECEIVED,
        )
        with self.assertRaises(ValidationError) as caught:
            duplicate.full_clean()
        self.assertIn("cheque_number", caught.exception.message_dict)

    def test_same_number_other_bank_is_fine(self):
        self.make_cheque(self.lease, number="555")
        Cheque(
            lease=self.lease,
            cheque_number="555",
            bank_name="Mashreq",
            amount=Decimal("100"),
            cheque_date=self.today,
            status=Cheque.Status.RECEIVED,
        ).full_clean()

    def test_deposit_then_clear(self):
        cheque = self.make_cheque(self.lease)
        cheque.mark_deposited()
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, Cheque.Status.DEPOSITED)
        self.assertEqual(cheque.deposited_date, self.today)

        cheque.mark_cleared()
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, Cheque.Status.CLEARED)
        self.assertEqual(cheque.cleared_date, self.today)
        self.assertEqual(cheque.events.count(), 2)

    def test_cannot_deposit_twice(self):
        cheque = self.make_cheque(self.lease)
        cheque.mark_deposited()
        with self.assertRaises(ValidationError):
            cheque.mark_deposited()

    def test_cannot_bounce_before_deposit(self):
        cheque = self.make_cheque(self.lease)
        with self.assertRaises(ValidationError):
            cheque.mark_returned("insufficient_funds")

    def test_bounce_needs_a_valid_reason(self):
        cheque = self.make_cheque(self.lease)
        cheque.mark_deposited()
        with self.assertRaises(ValidationError):
            cheque.mark_returned("because")

    def test_bounce_and_replace(self):
        cheque = self.make_cheque(self.lease)
        cheque.mark_deposited()
        cheque.mark_returned("insufficient_funds")
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, Cheque.Status.RETURNED)
        self.assertEqual(cheque.return_reason, "insufficient_funds")
        self.assertEqual(self.lease.cheque_totals()["returned"], Decimal("10000"))

        replacement = cheque.create_replacement()
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, Cheque.Status.REPLACED)
        self.assertEqual(replacement.status, Cheque.Status.PENDING)
        self.assertEqual(replacement.replaces, cheque)
        self.assertEqual(replacement.amount, cheque.amount)
        self.assertEqual(self.lease.cheque_totals()["returned"], Decimal("0"))

    def test_only_returned_cheques_can_be_replaced(self):
        cheque = self.make_cheque(self.lease)
        with self.assertRaises(ValidationError):
            cheque.create_replacement()

    def test_lease_totals(self):
        first = self.make_cheque(self.lease, number="1")
        second = self.make_cheque(self.lease, number="2")
        self.make_cheque(self.lease, number="3", status=Cheque.Status.PENDING)
        first.mark_cleared()
        second.mark_deposited()
        totals = self.lease.cheque_totals()
        self.assertEqual(totals["scheduled"], Decimal("30000"))
        self.assertEqual(totals["cleared"], Decimal("10000"))
        self.assertEqual(totals["deposited"], Decimal("10000"))


class AdminPageTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            "boss", "boss@example.com", "a-long-test-password-1"
        )
        self.client.force_login(self.user)
        self.lease = self.make_lease()
        self.cheque = self.make_cheque(self.lease, number="777")

    def test_every_list_and_add_page_opens(self):
        for model in ("owner", "property", "unit", "tenant", "lease", "cheque"):
            with self.subTest(model=model):
                listing = self.client.get(reverse(f"admin:realestate_{model}_changelist"))
                self.assertEqual(listing.status_code, 200)
                add = self.client.get(reverse(f"admin:realestate_{model}_add"))
                self.assertEqual(add.status_code, 200)

    def test_every_edit_page_opens(self):
        objects = {
            "owner": self.owner,
            "property": self.property,
            "unit": self.unit,
            "tenant": self.tenant,
            "lease": self.lease,
            "cheque": self.cheque,
        }
        for model, obj in objects.items():
            with self.subTest(model=model):
                response = self.client.get(
                    reverse(f"admin:realestate_{model}_change", args=[obj.pk])
                )
                self.assertEqual(response.status_code, 200)

    def test_filters_work(self):
        urls = [
            reverse("admin:realestate_tenant_changelist") + "?docs=expired",
            reverse("admin:realestate_tenant_changelist") + "?docs=60",
            reverse("admin:realestate_owner_changelist") + "?docs=90",
            reverse("admin:realestate_lease_changelist") + "?expiry=overdue",
            reverse("admin:realestate_lease_changelist") + "?expiry=30",
            reverse("admin:realestate_unit_changelist") + "?unit_status=occupied",
            reverse("admin:realestate_cheque_changelist") + "?due=today",
            reverse("admin:realestate_cheque_changelist") + "?due=overdue",
            reverse("admin:realestate_cheque_changelist") + "?due=month",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_cheque_desk_shows_todays_cheque(self):
        response = self.client.get(reverse("admin:realestate_cheque_desk"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Due today: deposit these")
        self.assertContains(response, "777")
        self.assertContains(response, "Deposit")

    def test_desk_button_deposits_the_cheque(self):
        desk = reverse("admin:realestate_cheque_desk")
        response = self.client.post(
            reverse("admin:realestate_cheque_workflow", args=[self.cheque.pk]),
            {"action": "deposit", "next": desk},
        )
        self.assertRedirects(response, desk)
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.status, Cheque.Status.DEPOSITED)

    def test_desk_bounce_and_replace_buttons(self):
        self.cheque.mark_deposited()
        url = reverse("admin:realestate_cheque_workflow", args=[self.cheque.pk])
        self.client.post(url, {"action": "return", "reason": "signature"})
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.status, Cheque.Status.RETURNED)

        self.client.post(url, {"action": "replace"})
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.status, Cheque.Status.REPLACED)
        self.assertEqual(Cheque.objects.filter(replaces=self.cheque).count(), 1)

    def test_workflow_rejects_get_and_outside_redirects(self):
        url = reverse("admin:realestate_cheque_workflow", args=[self.cheque.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

        response = self.client.post(url, {"action": "deposit", "next": "https://evil.example/"})
        self.assertRedirects(response, reverse("admin:realestate_cheque_desk"))

    def test_bulk_actions_on_cheque_list(self):
        url = reverse("admin:realestate_cheque_changelist")
        self.client.post(url, {"action": "action_clear", "_selected_action": [self.cheque.pk]})
        self.cheque.refresh_from_db()
        self.assertEqual(self.cheque.status, Cheque.Status.CLEARED)

    def test_generate_schedule_action(self):
        other_unit = Unit.objects.create(property=self.property, unit_number="202")
        lease = self.make_lease(unit=other_unit)
        url = reverse("admin:realestate_lease_changelist")
        self.client.post(url, {"action": "generate_schedule", "_selected_action": [lease.pk]})
        self.assertEqual(lease.cheques.count(), 4)

    def test_renewal_action(self):
        url = reverse("admin:realestate_lease_changelist")
        self.client.post(url, {"action": "create_renewal", "_selected_action": [self.lease.pk]})
        renewal = Lease.objects.get(renewal_of=self.lease)
        self.assertEqual(renewal.start_date, self.lease.end_date + timedelta(days=1))
        self.assertEqual(renewal.annual_rent, self.lease.annual_rent)

    def test_documents_need_login(self):
        self.client.logout()
        response = self.client.get("/media/documents/anything.pdf")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
