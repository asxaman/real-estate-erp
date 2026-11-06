"""Automated checks of the lease rules. Run with:  python manage.py test"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import AuditEntry
from properties.models import Owner, Property, Unit
from properties.tests import building, standalone, unit
from tenants.models import Tenant

from . import occupancy
from .models import Lease, LeaseDeduction, LeaseDocument, LeaseNote, LeasePayment, add_months


def make_tenant(name="Test Tenant", **kwargs):
    return Tenant.objects.create(name=name, email=f"{name.replace(' ', '').lower()}@example.com", **kwargs)


def make_lease(tenant, target, start=None, days=364, rent="60000", **kwargs):
    start = start or timezone.localdate() - timedelta(days=10)
    values = dict(
        tenant=tenant, start_date=start, end_date=start + timedelta(days=days), annual_rent=Decimal(rent), installments=4,
    )
    values.update(kwargs)
    if isinstance(target, Unit):
        values["unit"] = target
        values["property"] = target.property
    else:
        values["property"] = target
    lease = Lease(**values)
    lease.clean()
    lease.save()
    return lease


class Setup(TestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.tower = building("LSB")
        self.flat = unit(self.tower, "101")
        self.villa = standalone("LSV1")
        self.villa.save()


class SchedulesAndMoney(Setup):
    def test_months_are_added_safely(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))

    def test_four_payments_split_the_rent_and_the_last_takes_the_rounding(self):
        lease = make_lease(self.tenant, self.flat, rent="100000.01")
        payments = list(lease.payments.order_by("due_date"))
        self.assertEqual(len(payments), 4)
        self.assertEqual(sum(p.amount for p in payments), Decimal("100000.01"))
        self.assertEqual([p.due_date for p in payments][:2], [lease.start_date, add_months(lease.start_date, 3)])
        self.assertEqual([p.part for p in payments], [1, 2, 3, 4])

    def test_one_payment_and_twelve_payments(self):
        one = make_lease(self.tenant, self.flat, installments=1)
        self.assertEqual(one.payments.count(), 1)
        twelve = make_lease(self.tenant, self.villa, installments=12, rent="120000")
        self.assertEqual(twelve.payments.count(), 12)
        self.assertEqual(twelve.payments.first().amount, Decimal("10000.00"))

    def test_saving_again_does_not_create_a_second_schedule(self):
        lease = make_lease(self.tenant, self.flat)
        lease.notes = "changed"
        lease.save()
        self.assertEqual(lease.payments.count(), 4)

    def test_vat_is_five_percent_when_it_applies(self):
        lease = make_lease(self.tenant, self.flat, rent="100000", vat_applies=True)
        self.assertEqual(lease.vat_amount, Decimal("5000.00"))
        self.assertEqual(lease.total_with_vat, Decimal("105000.00"))
        self.assertEqual(lease.payments.first().vat, Decimal("1250.00"))
        plain = make_lease(self.tenant, self.villa, rent="100000")
        self.assertEqual(plain.vat_amount, Decimal("0.00"))

    def test_codes_are_assigned_in_order(self):
        first = make_lease(self.tenant, self.flat)
        second = make_lease(self.tenant, self.villa)
        self.assertRegex(first.code, r"^LSE-\d{4}$")
        self.assertEqual(int(second.code[4:]), int(first.code[4:]) + 1)

    def test_rebuild_is_refused_when_a_payment_was_received(self):
        lease = make_lease(self.tenant, self.flat)
        payment = lease.payments.first()
        payment.status = LeasePayment.Status.PAID
        payment.save()
        self.assertEqual(payment.paid_on, timezone.localdate())  # filled in when marked paid
        self.assertTrue(lease.payments.exclude(status="pending").exists())

    def test_paid_on_is_cleared_when_not_paid_and_cheque_details_only_for_cheques(self):
        lease = make_lease(self.tenant, self.flat)
        payment = lease.payments.first()
        payment.status, payment.paid_on, payment.method, payment.cheque_number = "pending", date(2026, 1, 1), "transfer", "123"
        payment.save()
        payment.refresh_from_db()
        self.assertIsNone(payment.paid_on)
        self.assertEqual(payment.cheque_number, "")


class LeaseRules(Setup):
    def build(self, **kwargs):
        start = timezone.localdate()
        values = dict(tenant=self.tenant, unit=self.flat, property=self.tower, start_date=start,
                      end_date=start + timedelta(days=364), annual_rent=Decimal("50000"), installments=4)
        values.update(kwargs)
        return Lease(**values)

    def test_choosing_a_unit_fills_in_its_property(self):
        lease = self.build(property=None)
        lease.property_id = None
        lease.clean()
        self.assertEqual(lease.property_id, self.tower.pk)

    def test_a_building_needs_a_unit(self):
        with self.assertRaises(ValidationError) as caught:
            self.build(unit=None).clean()
        self.assertIn("unit", caught.exception.message_dict)

    def test_unit_of_another_property_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.build(property=self.villa).clean()
        self.assertIn("unit", caught.exception.message_dict)

    def test_a_standalone_property_is_rented_as_a_whole(self):
        lease = self.build(unit=None, property=self.villa)
        lease.clean()  # no error

    def test_end_must_be_after_start_and_not_too_long(self):
        start = timezone.localdate()
        with self.assertRaises(ValidationError) as caught:
            self.build(end_date=start).clean()
        self.assertIn("end_date", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            self.build(end_date=add_months(start, 12 * 10 + 1)).clean()
        self.assertIn("end_date", caught.exception.message_dict)

    def test_leases_for_the_same_unit_cannot_overlap(self):
        make_lease(self.tenant, self.flat)
        with self.assertRaises(ValidationError) as caught:
            self.build(start_date=timezone.localdate() + timedelta(days=100),
                       end_date=timezone.localdate() + timedelta(days=460)).clean()
        self.assertIn("overlap", " ".join(caught.exception.messages))

    def test_the_day_after_a_lease_ends_is_free(self):
        first = make_lease(self.tenant, self.flat)
        self.build(start_date=first.end_date + timedelta(days=1),
                   end_date=first.end_date + timedelta(days=365)).clean()  # no error

    def test_a_terminated_lease_frees_the_days_after_it_ended(self):
        first = make_lease(self.tenant, self.flat)
        first.status, first.terminated_on, first.termination_reason = "terminated", first.start_date + timedelta(days=30), "Left"
        first.clean()
        first.save()
        self.build(start_date=first.terminated_on + timedelta(days=1),
                   end_date=first.terminated_on + timedelta(days=365)).clean()  # no error

    def test_termination_needs_a_date_and_a_reason(self):
        lease = make_lease(self.tenant, self.flat)
        lease.status = "terminated"
        with self.assertRaises(ValidationError) as caught:
            lease.clean()
        self.assertIn("terminated_on", caught.exception.message_dict)
        self.assertIn("termination_reason", caught.exception.message_dict)

    def test_an_active_lease_keeps_no_termination_details(self):
        lease = make_lease(self.tenant, self.flat)
        lease.terminated_on, lease.termination_reason = date(2026, 1, 1), "x"
        lease.clean()
        self.assertIsNone(lease.terminated_on)
        self.assertEqual(lease.termination_reason, "")

    def test_blacklisted_and_inactive_tenants_cannot_sign_a_new_lease(self):
        self.tenant.is_blacklisted, self.tenant.blacklist_reason = True, "Unpaid rent"
        self.tenant.save()
        with self.assertRaises(ValidationError) as caught:
            self.build().clean()
        self.assertIn("tenant", caught.exception.message_dict)
        other = make_tenant("Quiet Tenant", is_active=False)
        with self.assertRaises(ValidationError):
            self.build(tenant=other).clean()

    def test_an_inactive_property_cannot_be_leased(self):
        Property.objects.filter(pk=self.villa.pk).update(is_active=False)
        self.villa.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.build(unit=None, property=self.villa).clean()

    def test_state_and_days_left(self):
        today = timezone.localdate()
        active = make_lease(self.tenant, self.flat)
        self.assertEqual(active.state(), "active")
        self.assertEqual(make_lease(self.tenant, self.villa, start=today + timedelta(days=5)).state(), "upcoming")
        expired = Lease(tenant=self.tenant, property=self.tower, unit=self.flat, start_date=today - timedelta(days=500),
                        end_date=today - timedelta(days=100), annual_rent=1)
        self.assertEqual(expired.state(), "expired")
        self.assertTrue(make_lease(self.tenant, unit(self.tower, "102"), start=today - timedelta(days=350), days=364).ends_soon())


class RenewalsAndStatus(Setup):
    def test_renewal_starts_the_next_day_and_lasts_as_long(self):
        old = make_lease(self.tenant, self.flat, rent="70000", security_deposit=Decimal("3000"))
        new = old.renew()
        self.assertEqual(new.start_date, old.end_date + timedelta(days=1))
        self.assertEqual(new.end_date - new.start_date, old.end_date - old.start_date)
        self.assertEqual((new.annual_rent, new.renewed_from_id, new.security_deposit), (old.annual_rent, old.pk, old.security_deposit))
        self.assertEqual(new.payments.filter(kind="rent").count(), 4)
        self.assertEqual(new.payments.filter(kind="deposit").count(), 1)  # the deposit is asked for again

    def test_a_lease_can_only_be_renewed_once_and_not_after_termination(self):
        old = make_lease(self.tenant, self.flat)
        old.renew()
        with self.assertRaises(ValidationError):
            old.renew()
        other = make_lease(self.tenant, self.villa)
        other.status, other.terminated_on, other.termination_reason = "terminated", other.start_date + timedelta(days=2), "Left"
        other.save()
        with self.assertRaises(ValidationError):
            other.renew()

    def test_a_running_lease_makes_the_unit_occupied_and_ending_it_makes_it_vacant(self):
        self.assertEqual(Unit.objects.get(pk=self.flat.pk).status, Unit.Status.VACANT)
        lease = make_lease(self.tenant, self.flat)
        self.assertEqual(Unit.objects.get(pk=self.flat.pk).status, Unit.Status.OCCUPIED)
        lease.status, lease.terminated_on, lease.termination_reason = "terminated", timezone.localdate() - timedelta(days=1), "Left"
        lease.save()
        self.assertEqual(Unit.objects.get(pk=self.flat.pk).status, Unit.Status.VACANT)

    def test_deleting_the_lease_frees_the_unit(self):
        lease = make_lease(self.tenant, self.flat)
        lease.delete()
        self.assertEqual(Unit.objects.get(pk=self.flat.pk).status, Unit.Status.VACANT)

    def test_a_whole_property_follows_its_lease_too(self):
        make_lease(self.tenant, self.villa)
        self.assertEqual(Property.objects.get(pk=self.villa.pk).status, Property.Status.OCCUPIED)

    def test_a_future_lease_does_not_occupy_and_the_daily_refresh_catches_up(self):
        today = timezone.localdate()
        make_lease(self.tenant, self.flat, start=today + timedelta(days=3))
        self.assertEqual(Unit.objects.get(pk=self.flat.pk).status, Unit.Status.VACANT)
        self.assertEqual(occupancy.refresh_all(today + timedelta(days=4)), 1)
        self.assertEqual(Unit.objects.get(pk=self.flat.pk).status, Unit.Status.OCCUPIED)

    def test_maintenance_is_left_alone_when_there_is_no_lease_news(self):
        Unit.objects.filter(pk=self.flat.pk).update(status=Unit.Status.MAINTENANCE)
        self.flat.refresh_from_db()
        self.assertFalse(occupancy.refresh_target(self.flat))

    def test_units_with_no_leases_are_never_touched(self):
        Unit.objects.filter(pk=self.flat.pk).update(status=Unit.Status.OCCUPIED)
        self.assertEqual(occupancy.refresh_all(), 0)
        self.assertEqual(Unit.objects.get(pk=self.flat.pk).status, Unit.Status.OCCUPIED)


class DeletingAndHistory(Setup):
    def test_a_tenant_unit_or_property_with_a_lease_cannot_be_deleted(self):
        make_lease(self.tenant, self.flat)
        for record in (self.tenant, self.flat):
            with self.assertRaises(ProtectedError):
                record.delete()
        with self.assertRaises(ProtectedError):
            self.tower.delete()

    def test_deleting_a_lease_removes_its_payments_and_documents(self):
        lease = make_lease(self.tenant, self.flat)
        LeaseDocument.objects.create(lease=lease, doc_type="contract")
        lease.delete()
        self.assertFalse(LeasePayment.objects.exists())
        self.assertFalse(LeaseDocument.objects.exists())

    def test_audit_entries_link_to_the_lease_tenant_unit_and_owner(self):
        lease = make_lease(self.tenant, self.flat)
        entry = AuditEntry.objects.filter(model_label="Lease", object_id=str(lease.pk)).first()
        self.assertEqual(
            (entry.related_lease_id, entry.related_tenant_id, entry.related_unit_id, entry.related_property_id),
            (lease.pk, self.tenant.pk, self.flat.pk, self.tower.pk),
        )
        self.assertEqual(entry.related_owner_id, self.tower.owner_id)
        paid = lease.payments.first()
        paid.status = "paid"
        paid.save()
        entries = AuditEntry.objects.filter(model_label="Payment", related_lease_id=lease.pk)
        self.assertTrue(entries.exists())


class LeasePages(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("leaseboss", "lb@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = make_lease(self.tenant, self.flat)

    def test_pages_open(self):
        for name, args in (
            ("admin:leases_lease_changelist", []), ("admin:leases_lease_add", []),
            ("admin:leases_lease_change", [self.lease.pk]), ("admin:leases_leasepayment_changelist", []),
            ("admin:leases_leasepayment_change", [self.lease.payments.first().pk]),
            ("admin:tenants_tenant_change", [self.tenant.pk]), ("admin:properties_unit_change", [self.flat.pk]),
            ("dashboard", []), ("expiring_documents", []),
        ):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_list_filters_and_search_work(self):
        url = reverse("admin:leases_lease_changelist")
        for query in ("?state=active", "?state=soon", "?state=expired", "?state=upcoming", "?state=terminated",
                      "?payments=overdue", "?payments=bounced", "?q=LSE", f"?q={self.tenant.name}"):
            self.assertEqual(self.client.get(url + query).status_code, 200, query)
        pay_url = reverse("admin:leases_leasepayment_changelist")
        for query in ("?due=overdue", "?due=week", "?due=month", "?status__exact=paid", "?q=" + self.lease.code):
            self.assertEqual(self.client.get(pay_url + query).status_code, 200, query)

    def test_adding_a_lease_through_the_form_creates_the_schedule(self):
        other = unit(self.tower, "202")
        start = timezone.localdate()
        response = self.client.post(reverse("admin:leases_lease_add"), {
            "tenant": self.tenant.pk, "unit": other.pk, "property": "", "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=364)).isoformat(), "annual_rent": "48000", "installments": "4",
            "status": "active", "security_deposit": "0", "deposit_status": "held", "commission": "0", "admin_fee": "0", "ejari_fee": "0",
            "deposit_refund_date": "", "deposit_refund_amount": "0", "late_fee_per_day": "0", "ejari_number": "", "ejari_expiry": "", "terminated_on": "", "termination_reason": "", "notes": "",
            "documents-TOTAL_FORMS": "0", "documents-INITIAL_FORMS": "0", "documents-MIN_NUM_FORMS": "0",
            "documents-MAX_NUM_FORMS": "1000",
        })
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        created = Lease.objects.get(unit=other)
        self.assertEqual(created.property_id, self.tower.pk)
        self.assertEqual(created.payments.count(), 4)

    def test_renew_action_creates_the_renewal_and_opens_it(self):
        response = self.client.post(reverse("admin:leases_lease_changelist"), {
            "action": "renew_lease", "_selected_action": [str(self.lease.pk)],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Lease.objects.filter(renewed_from=self.lease).count(), 1)

    def test_a_lease_with_received_rent_cannot_be_deleted(self):
        payment = self.lease.payments.first()
        payment.status = "paid"
        payment.save()
        url = reverse("admin:leases_lease_delete", args=[self.lease.pk])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(Lease.objects.filter(pk=self.lease.pk).exists())

    def test_mark_paid_action(self):
        ids = [str(p.pk) for p in self.lease.payments.all()[:2]]
        self.client.post(reverse("admin:leases_leasepayment_changelist"), {"action": "mark_paid", "_selected_action": ids})
        self.assertEqual(self.lease.payments.filter(status="paid").count(), 2)

    def test_expiring_page_lists_a_lease_that_ends_soon(self):
        soon = make_lease(self.tenant, self.villa, start=timezone.localdate() - timedelta(days=350), days=364)
        page = self.client.get(reverse("expiring_documents"))
        self.assertContains(page, soon.code)


class SampleData(TestCase):
    def test_sample_leases_and_erase(self):
        from properties import seed as property_seed
        from tenants import seed as tenant_seed
        from . import seed

        property_seed.seed_properties()
        tenant_seed.seed_tenants()
        text = seed.seed_leases()
        self.assertGreaterEqual(Lease.objects.count(), 4, text)
        self.assertTrue(LeasePayment.objects.filter(status="paid").exists())
        self.assertTrue(any(p.is_overdue() for p in LeasePayment.objects.all()))
        seed.seed_leases()  # running it twice adds nothing
        count = Lease.objects.count()
        seed.seed_leases()
        self.assertEqual(Lease.objects.count(), count)
        seed.erase_leases()
        self.assertEqual(Lease.objects.count(), 0)


class AlertHeadings(Setup):
    def test_overdue_notice_has_its_own_heading_not_just_error(self):
        boss = get_user_model().objects.create_superuser("alertboss", "ab@example.com", "Pass-word1!")
        self.client.force_login(boss)
        lease = make_lease(self.tenant, self.flat, start=timezone.localdate() - timedelta(days=200))
        page = self.client.get(reverse("admin:leases_lease_change", args=[lease.pk]))
        self.assertContains(page, "Payments overdue")
        self.assertContains(page, '"level": "error"')


class RenewTerminateAndCheques(Setup):
    def test_renew_can_change_the_rent(self):
        old = make_lease(self.tenant, self.flat, rent="60000")
        new = old.renew(Decimal("63000"))
        self.assertEqual(new.annual_rent, Decimal("63000.00"))
        self.assertEqual(sum(p.amount for p in new.payments.all()), Decimal("63000.00"))

    def test_renew_refuses_a_zero_rent(self):
        with self.assertRaises(ValidationError):
            make_lease(self.tenant, self.flat).renew(Decimal("0"))

    def test_terminate_cancels_only_unpaid_payments_after_the_date(self):
        lease = make_lease(self.tenant, self.flat, start=timezone.localdate() - timedelta(days=100), rent="40000")
        first = lease.payments.order_by("due_date").first()
        first.record(first.due_date, "cheque", "111", "ENBD")
        cancelled = lease.terminate(timezone.localdate() + timedelta(days=5), "Moved abroad")
        self.assertEqual(cancelled, 2)  # the two that fall due after the date; the paid one and the earlier one stay
        self.assertEqual(lease.payments.count(), 2)
        lease.refresh_from_db()
        self.assertEqual((lease.status, lease.termination_reason), ("terminated", "Moved abroad"))

    def test_terminate_can_keep_the_payments_and_set_the_deposit(self):
        lease = make_lease(self.tenant, self.flat, security_deposit=Decimal("2000"))
        self.assertEqual(lease.terminate(lease.start_date + timedelta(days=60), "Left", cancel_unpaid=False, deposit_status="refunded"), 0)
        self.assertEqual(lease.payments.filter(kind="rent").count(), 4)
        self.assertEqual(lease.deposit_status, "refunded")

    def test_terminate_needs_a_reason_a_valid_date_and_works_only_once(self):
        lease = make_lease(self.tenant, self.flat)
        with self.assertRaises(ValidationError):
            lease.terminate(lease.start_date + timedelta(days=5), "")
        lease = Lease.objects.get(pk=lease.pk)
        with self.assertRaises(ValidationError):
            lease.terminate(lease.end_date + timedelta(days=5), "Late")
        lease = Lease.objects.get(pk=lease.pk)
        lease.terminate(lease.start_date + timedelta(days=5), "Left")
        with self.assertRaises(ValidationError):
            lease.terminate(lease.start_date + timedelta(days=6), "Again")

    def test_cheques_are_numbered_in_order_keeping_zeros_and_prefix(self):
        lease = make_lease(self.tenant, self.flat)
        paid = lease.payments.order_by("due_date").first()
        paid.record(paid.due_date, "transfer")
        self.assertEqual(lease.assign_cheques("CQ-0098", "ENBD"), 3)  # the paid one is skipped
        numbers = list(lease.payments.exclude(status="paid").order_by("due_date").values_list("cheque_number", flat=True))
        self.assertEqual(numbers, ["CQ-0098", "CQ-0099", "CQ-0100"])
        self.assertEqual(lease.payments.exclude(status="paid").first().bank_name, "ENBD")

    def test_cheque_start_must_end_in_a_number(self):
        lease = make_lease(self.tenant, self.flat)
        for bad in ("", "abc", "12 34x"):
            with self.assertRaises(ValidationError):
                lease.assign_cheques(bad)

    def test_record_and_bounce(self):
        payment = make_lease(self.tenant, self.flat).payments.first()
        payment.record(date(2026, 3, 1), "cheque", "555", "ENBD")
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.paid_on, payment.cheque_number), ("paid", date(2026, 3, 1), "555"))
        payment.bounce()
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.paid_on), ("bounced", None))
        with self.assertRaises(ValidationError):
            payment.record(date(2026, 3, 1), "gold")


class PopupEndpoints(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("popboss", "pb@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = make_lease(self.tenant, self.flat, rent="60000")

    def url(self, name, *args):
        return reverse(f"admin:leases_{name}", args=args)

    def test_info_fills_rent_vat_and_end_date(self):
        Unit.objects.filter(pk=self.flat.pk).update(asking_rent=Decimal("55000"))
        other = Lease.objects.get(pk=self.lease.pk)
        other.delete()
        data = self.client.get(self.url("lease_info"), {"unit": self.flat.pk, "start": "2027-01-01"}).json()
        self.assertEqual(data["rent"], "55000")
        self.assertFalse(data["vat"])
        self.assertEqual(data["end"], "2027-12-31")
        office = self.client.get(self.url("lease_info"), {"unit": unit(self.tower, "301", unit_type="office").pk}).json()
        self.assertTrue(office["vat"])

    def test_info_warns_about_a_lease_that_is_in_the_way(self):
        start = (self.lease.start_date + timedelta(days=30)).isoformat()
        data = self.client.get(self.url("lease_info"), {"unit": self.flat.pk, "start": start}).json()
        self.assertIn(self.lease.code, data["warning"])
        own = self.client.get(self.url("lease_info"), {"unit": self.flat.pk, "start": start, "lease": self.lease.pk}).json()
        self.assertEqual(own["warning"], "")  # a lease is not in its own way

    def test_info_for_a_standalone_property_and_nothing(self):
        Property.objects.filter(pk=self.villa.pk).update(annual_rent=Decimal("90000"))
        self.assertEqual(self.client.get(self.url("lease_info"), {"property": self.villa.pk}).json()["rent"], "90000")
        self.assertEqual(self.client.get(self.url("lease_info")).json()["rent"], "")

    def test_renew_endpoint(self):
        reply = self.client.post(self.url("lease_renew", self.lease.pk), {"mode": "percent", "value": "5"}).json()
        self.assertTrue(reply["ok"])
        renewal = Lease.objects.get(renewed_from=self.lease)
        self.assertEqual(renewal.annual_rent, Decimal("63000.00"))
        again = self.client.post(self.url("lease_renew", self.lease.pk), {"mode": "same"})
        self.assertEqual(again.status_code, 400)
        self.assertFalse(again.json()["ok"])

    def test_renew_endpoint_refuses_nonsense(self):
        other = make_lease(self.tenant, self.villa)
        self.assertEqual(self.client.post(self.url("lease_renew", other.pk), {"mode": "percent", "value": "abc"}).status_code, 400)
        self.assertEqual(self.client.post(self.url("lease_renew", other.pk), {"mode": "percent", "value": "900"}).status_code, 400)
        self.assertEqual(self.client.post(self.url("lease_renew", other.pk), {"mode": "amount", "value": "0"}).status_code, 400)
        self.assertFalse(Lease.objects.filter(renewed_from=other).exists())

    def test_terminate_endpoint(self):
        day = (self.lease.start_date + timedelta(days=30)).isoformat()
        reply = self.client.post(self.url("lease_terminate", self.lease.pk), {
            "date": day, "reason": "Moved", "deposit_status": "deducted", "cancel_unpaid": "on",
        }).json()
        self.assertTrue(reply["ok"])
        self.lease.refresh_from_db()
        self.assertEqual((self.lease.status, self.lease.deposit_status), ("terminated", "deducted"))
        self.assertEqual(self.lease.payments.count(), 1)  # the first one was due on the start date

    def test_terminate_endpoint_asks_for_a_reason(self):
        reply = self.client.post(self.url("lease_terminate", self.lease.pk), {"date": self.lease.start_date.isoformat(), "reason": ""})
        self.assertEqual(reply.status_code, 400)
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.status, "active")

    def test_cheques_endpoint(self):
        reply = self.client.post(self.url("lease_cheques", self.lease.pk), {"first": "0100", "bank": "ENBD"}).json()
        self.assertTrue(reply["ok"])
        self.assertEqual(self.lease.payments.order_by("due_date").first().cheque_number, "0100")
        self.assertEqual(self.client.post(self.url("lease_cheques", self.lease.pk), {"first": "x"}).status_code, 400)

    def test_record_endpoint_paid_and_bounced(self):
        payment = self.lease.payments.order_by("due_date").first()
        reply = self.client.post(self.url("leasepayment_record", payment.pk), {
            "action": "paid", "date": "2026-02-03", "method": "cheque", "cheque_number": "77", "bank_name": "ENBD",
        }).json()
        self.assertTrue(reply["ok"])
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.paid_on.isoformat()), ("paid", "2026-02-03"))
        self.assertTrue(self.client.post(self.url("leasepayment_record", payment.pk), {"action": "bounced"}).json()["ok"])
        payment.refresh_from_db()
        self.assertEqual(payment.status, "bounced")
        self.assertEqual(self.client.post(self.url("leasepayment_record", payment.pk), {"action": "paid", "date": "nope", "method": "cash"}).status_code, 400)

    def test_endpoints_need_a_form_post_and_permission(self):
        self.assertEqual(self.client.get(self.url("lease_renew", self.lease.pk)).status_code, 405)
        viewer = get_user_model().objects.create_user("lookonly", password="Pass-word1!", is_staff=True)
        from django.contrib.auth.models import Permission

        viewer.user_permissions.add(*Permission.objects.filter(codename__in=["view_lease", "view_leasepayment"]))
        self.client.force_login(viewer)
        payment = self.lease.payments.first()
        for name, args, data in (
            ("lease_renew", [self.lease.pk], {"mode": "same"}),
            ("lease_terminate", [self.lease.pk], {"date": "2026-01-01", "reason": "x"}),
            ("lease_cheques", [self.lease.pk], {"first": "1"}),
            ("leasepayment_record", [payment.pk], {"action": "paid", "date": "2026-01-01", "method": "cash"}),
        ):
            self.assertEqual(self.client.post(self.url(name, *args), data).status_code, 403, name)
        self.assertEqual(Lease.objects.filter(renewed_from=self.lease).count(), 0)

    def test_pages_show_the_buttons_and_the_chips(self):
        page = self.client.get(self.url("lease_change", self.lease.pk))
        for act in ("record", "renew", "terminate", "cheques"):
            self.assertContains(page, f'data-erp-act="{act}"')
        listing = self.client.get(self.url("lease_changelist"))
        self.assertContains(listing, "erp-chips")
        self.assertContains(listing, 'data-erp-act="record"')
        payments = self.client.get(self.url("leasepayment_changelist"))
        self.assertContains(payments, 'data-erp-act="record"')

    def test_chip_counts(self):
        make_lease(self.tenant, self.villa, start=timezone.localdate() + timedelta(days=10))
        chips = {c["label"]: c["count"] for c in self.client.get(self.url("lease_changelist")).context["lease_chips"]}
        self.assertEqual((chips["All"], chips["Active"], chips["Not started"]), (2, 1, 1))


class MoreDetail(Setup):
    """Update 19: late fees, deposit tracking, cheque dates, notes and the extra panels."""

    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("detailboss", "db@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.today = timezone.localdate()
        self.lease = make_lease(
            self.tenant, self.flat, start=self.today - timedelta(days=100), rent="40000", security_deposit=Decimal("4000"),
            late_fee_per_day=Decimal("50"),
        )

    def test_days_overdue_and_late_fee(self):
        first = self.lease.payments.filter(kind="rent").order_by("due_date").first()
        self.assertEqual(first.days_overdue(), 100)
        self.assertEqual(first.late_fee(), Decimal("5000.00"))
        first.record(self.today, "cash")
        self.assertEqual((first.days_overdue(), first.late_fee()), (0, Decimal("0.00")))
        second = self.lease.payments.filter(kind="rent").order_by("due_date")[1]  # also past due, so it keeps adding up
        self.assertGreater(second.late_fee(), 0)
        self.assertEqual(self.lease.late_fees(), second.late_fee())

    def test_no_daily_fee_means_no_late_fee(self):
        Lease.objects.filter(pk=self.lease.pk).update(late_fee_per_day=0)
        lease = Lease.objects.get(pk=self.lease.pk)
        self.assertEqual(lease.late_fees(), Decimal("0.00"))
        self.assertEqual(lease.payments.order_by("due_date").first().late_fee(), Decimal("0.00"))

    def test_cheque_dates_are_checked_and_cleared_for_other_methods(self):
        payment = self.lease.payments.filter(kind="rent").order_by("due_date").first()
        payment.method, payment.cheque_date, payment.deposited_on = "cheque", date(2026, 3, 10), date(2026, 3, 5)
        with self.assertRaises(ValidationError):
            payment.save()
        payment.deposited_on = date(2026, 3, 12)
        payment.save()
        payment.refresh_from_db()
        self.assertEqual(payment.deposited_on, date(2026, 3, 12))
        payment.method = "transfer"
        payment.save()
        payment.refresh_from_db()
        self.assertEqual((payment.cheque_date, payment.deposited_on), (None, None))

    def test_deposit_balance_follows_deductions_and_refund(self):
        self.assertEqual(self.lease.deposit_balance(), Decimal("4000.00"))
        LeaseDeduction.objects.create(lease=self.lease, reason="Repainting", amount=Decimal("1500"))
        self.lease.deposit_refund_amount, self.lease.deposit_refund_date = Decimal("2000"), self.today
        self.lease.save()
        lease = Lease.objects.get(pk=self.lease.pk)
        self.assertEqual((lease.deposit_deducted(), lease.deposit_balance()), (Decimal("1500.00"), Decimal("500.00")))

    def test_deposit_rules(self):
        lease = Lease.objects.get(pk=self.lease.pk)
        lease.deposit_refund_amount = Decimal("5000")
        lease.deposit_refund_date = self.today
        with self.assertRaises(ValidationError):
            lease.clean()
        lease.deposit_refund_amount = Decimal("100")
        lease.deposit_refund_date = None
        with self.assertRaises(ValidationError):
            lease.clean()
        deduction = LeaseDeduction(lease=self.lease, reason="Too much", amount=Decimal("4500"))
        with self.assertRaises(ValidationError):
            deduction.clean()

    def test_renewal_keeps_the_late_fee_rate(self):
        self.assertEqual(self.lease.renew().late_fee_per_day, Decimal("50.00"))

    def test_panels_and_tabs_open(self):
        LeaseNote.objects.create(lease=self.lease, author="Someone", text="Called the tenant")
        page = self.client.get(reverse("admin:leases_lease_change", args=[self.lease.pk]))
        self.assertEqual(page.status_code, 200)
        for marker in ("erp-pbar", "erp-tl", "erp-sum", "Called the tenant"):
            self.assertContains(page, marker)
        self.assertContains(self.client.get(reverse("admin:leases_lease_changelist")), "erp-pbar")
        late = self.client.get(reverse("admin:leases_leasepayment_changelist"))
        self.assertContains(late, "100 days")

    def test_money_summary_flags_a_mismatch(self):
        LeasePayment.objects.filter(pk=self.lease.payments.first().pk).update(amount=Decimal("1"))
        page = self.client.get(reverse("admin:leases_lease_change", args=[self.lease.pk]))
        self.assertContains(page, "payments add up to")

    def test_new_notes_get_the_writer_and_saved_notes_cannot_be_edited(self):
        from django.contrib import admin as django_admin

        from leases.admin import LeaseAdmin, NoteInline

        class FakeFormset:
            model = LeaseNote
            deleted_objects = []

            def save(inner, commit=True):
                return [LeaseNote(lease=self.lease, text="Promised to pay on Friday")]

            def save_m2m(inner):
                pass

        request = type("Request", (), {"user": self.boss})()
        LeaseAdmin(Lease, django_admin.site).save_formset(request, None, FakeFormset(), True)
        note = LeaseNote.objects.get(text="Promised to pay on Friday")
        self.assertEqual(note.author, "detailboss")
        self.assertFalse(NoteInline(Lease, django_admin.site).has_change_permission(request))

    def test_lease_tabs_on_tenant_unit_and_property_pages(self):
        for name, args in (
            ("admin:tenants_tenant_change", [self.tenant.pk]), ("admin:properties_unit_change", [self.flat.pk]),
            ("admin:properties_property_change", [self.tower.pk]),
        ):
            page = self.client.get(reverse(name, args=args))
            self.assertContains(page, self.lease.code, msg_prefix=name)
        for name in ("admin:tenants_tenant_add", "admin:properties_unit_add", "admin:properties_property_add"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_units_list_shows_the_current_tenant_and_end_date(self):
        listing = self.client.get(reverse("admin:properties_unit_changelist"))
        self.assertContains(listing, self.tenant.name)
        self.assertContains(listing, "Lease ends")

    def test_sample_data_has_notes_and_a_late_fee(self):
        from leases import seed

        seed._settle(self.lease, self.today, keep_last_overdue=True)  # what the sample data does to its overdue lease
        self.assertTrue(LeaseNote.objects.filter(lease=self.lease).exists())
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.late_fee_per_day, Decimal("50.00"))
        self.assertTrue(self.lease.payments.filter(status="paid", deposited_on__isnull=False).exists())

    def test_new_models_are_in_the_audit_trail(self):
        from core.audit import AUDITED_MODELS

        self.assertIn("leases.LeaseNote", AUDITED_MODELS)
        self.assertIn("leases.LeaseDeduction", AUDITED_MODELS)


class ReportsAndLook(Setup):
    """Update 20: payment summary strip, due-date colours, exports, rent roll, monthly view, print pages, dashboard."""

    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("repboss", "rb@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.today = timezone.localdate()
        self.lease = make_lease(self.tenant, self.flat, start=self.today - timedelta(days=100), rent="40000")

    def staff(self, *codenames):
        from django.contrib.auth.models import Permission

        self.staff_count = getattr(self, "staff_count", 0) + 1
        user = get_user_model().objects.create_user(f"limited{self.staff_count}", password="Pass-word1!", is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.client.force_login(user)
        return user

    # ---- payments list -------------------------------------------------------------
    def test_payment_strip_and_filters(self):
        url = reverse("admin:leases_leasepayment_changelist")
        page = self.client.get(url)
        for text in ("Due this week", "Overdue", "Collected this month", "Bounced cheques", "Monthly collection"):
            self.assertContains(page, text)
        for query in ("?received=month", "?received=lastmonth", "?due=week", "?due=overdue", "?status__exact=bounced"):
            self.assertEqual(self.client.get(url + query).status_code, 200, query)

    def test_strip_counts_and_amounts(self):
        late = self.client.get(reverse("admin:leases_leasepayment_changelist")).context["pay_strip"]
        overdue = next(c for c in late if c["label"] == "Overdue")
        self.assertEqual(overdue["count"], 2)  # the payments due 100 and about 9 days ago
        self.assertEqual(overdue["amount"], "AED 20,000")
        first = self.lease.payments.filter(kind="rent").order_by("due_date").first()
        first.record(self.today, "cash")
        strip = self.client.get(reverse("admin:leases_leasepayment_changelist")).context["pay_strip"]
        self.assertEqual(next(c for c in strip if c["label"] == "Collected this month")["amount"], "AED 10,000")

    def test_due_dates_are_coloured_with_days(self):
        page = self.client.get(reverse("admin:leases_leasepayment_changelist"))
        self.assertContains(page, "erp-due-bad")
        self.assertContains(page, "100 days overdue")
        self.assertContains(page, "erp-due-mute")

    # ---- exports -------------------------------------------------------------------
    def export(self, name, action, pk):
        return self.client.post(reverse(name), {"action": action, "_selected_action": [pk], "index": 0})

    def test_lease_and_payment_exports(self):
        response = self.export("admin:leases_lease_changelist", "export_csv", self.lease.pk)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn(self.lease.code, response.content.decode("utf-8-sig"))
        response = self.export("admin:leases_lease_changelist", "export_excel", self.lease.pk)
        self.assertIn("spreadsheetml", response["Content-Type"])
        payment = self.lease.payments.first()
        response = self.export("admin:leases_leasepayment_changelist", "export_csv", payment.pk)
        self.assertIn("Cheque no.", response.content.decode("utf-8-sig"))
        response = self.export("admin:leases_leasepayment_changelist", "export_excel", payment.pk)
        self.assertIn("spreadsheetml", response["Content-Type"])

    def test_export_needs_its_own_permission(self):
        self.staff("view_lease", "view_leasepayment")
        for name, action in (("admin:leases_lease_changelist", "export_csv"), ("admin:leases_leasepayment_changelist", "export_excel")):
            response = self.export(name, action, self.lease.pk)
            self.assertNotIn("attachment", response.get("Content-Disposition", ""), name)
        self.staff("view_lease", "export_lease")
        self.assertIn("text/csv", self.export("admin:leases_lease_changelist", "export_csv", self.lease.pk)["Content-Type"])

    def test_managers_and_accountants_get_the_export_permissions(self):
        from django.contrib.auth.models import Group

        from leases import seed

        for role in ("Property Manager", "Accountant"):
            Group.objects.get_or_create(name=role)
        seed.grant_role_permissions()
        for role in ("Property Manager", "Accountant"):
            codes = set(Group.objects.get(name=role).permissions.values_list("codename", flat=True))
            self.assertTrue({"export_lease", "export_leasepayment"} <= codes, role)

    # ---- rent roll -----------------------------------------------------------------
    def test_rent_roll_lists_only_running_leases(self):
        make_lease(self.tenant, self.villa, start=self.today + timedelta(days=30))  # not started
        page = self.client.get(reverse("admin:leases_lease_rentroll"))
        self.assertContains(page, self.lease.code)
        self.assertEqual(len(page.context["rows"]), 1)
        self.assertEqual(page.context["totals"]["rent"], Decimal("40000.00"))
        self.assertEqual(page.context["totals"]["overdue"], Decimal("20000.00"))

    def test_rent_roll_downloads(self):
        url = reverse("admin:leases_lease_rentroll")
        self.assertIn("text/csv", self.client.get(url + "?export=csv")["Content-Type"])
        self.assertIn("spreadsheetml", self.client.get(url + "?export=xlsx")["Content-Type"])
        self.staff("view_lease")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url + "?export=csv").status_code, 404)

    # ---- monthly view --------------------------------------------------------------
    def test_monthly_collection_adds_up(self):
        from leases.reports import monthly_rows

        year = self.today.year
        rows = monthly_rows(year)
        in_year = [p for p in self.lease.payments.all() if p.due_date.year == year]
        self.assertEqual(sum(r["expected"] for r in rows), sum(p.total for p in in_year))
        first = self.lease.payments.order_by("due_date").first()
        first.record(self.today, "cash")
        rows = monthly_rows(first.due_date.year)
        month = rows[first.due_date.month - 1]
        self.assertEqual(month["received"], first.total)
        self.assertEqual(month["percent"], 100)

    def test_monthly_page_and_year_handling(self):
        url = reverse("admin:leases_lease_monthly")
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "January")
        self.assertEqual(self.client.get(url + "?year=2031").context["year"], 2031)
        self.assertEqual(self.client.get(url + "?year=abc").context["year"], self.today.year)
        self.assertEqual(self.client.get(url + "?year=99999").context["year"], self.today.year)

    # ---- print pages ---------------------------------------------------------------
    def test_print_pages(self):
        for kind, marker in (("summary", "Tenancy summary"), ("statement", "Rent payment statement")):
            page = self.client.get(reverse("admin:leases_lease_print", args=[self.lease.pk, kind]))
            self.assertEqual(page.status_code, 200, kind)
            for text in (marker, self.tenant.name, self.lease.code, "window.print()"):
                self.assertContains(page, text)
        self.assertEqual(self.client.get(reverse("admin:leases_lease_print", args=[self.lease.pk, "other"])).status_code, 404)

    def test_print_page_needs_view_permission_and_header_links_exist(self):
        header = self.client.get(reverse("admin:leases_lease_change", args=[self.lease.pk]))
        self.assertContains(header, "Tenancy summary")
        self.assertContains(header, "Payment statement")
        self.staff("add_tenant")
        self.assertEqual(self.client.get(reverse("admin:leases_lease_print", args=[self.lease.pk, "summary"])).status_code, 404)

    # ---- dashboard -----------------------------------------------------------------
    def test_dashboard_panels(self):
        ending = make_lease(self.tenant, self.villa, start=self.today - timedelta(days=350), days=364, rent="90000")
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "Needs attention")
        self.assertContains(page, "Next leases to end")
        self.assertContains(page, ending.code)
        self.assertContains(page, 'data-erp-act="renew"')
        self.assertContains(page, "Overdue payments")
        codes = [r["lease"].code for r in page.context["panels"]["renewals"]]
        self.assertEqual(codes[0], ending.code)  # the one that ends first comes first

    def test_dashboard_leaves_out_renewed_leases_and_hides_renew_without_permission(self):
        self.lease.renew()
        page = self.client.get(reverse("dashboard"))
        self.assertNotIn(self.lease.code, [r["lease"].code for r in page.context["panels"]["renewals"]])
        self.staff("view_lease")
        page = self.client.get(reverse("dashboard"))
        self.assertNotContains(page, 'data-erp-act="renew"')

    def test_dashboard_without_lease_permission_shows_no_lease_panels(self):
        self.staff("view_tenant")
        panels = self.client.get(reverse("dashboard")).context["panels"]
        self.assertNotIn("renewals", panels)
        self.assertNotIn("attention", panels)


class CompactAndPopups(Setup):
    """Update 21: compact payment cells, edit / add / remove pop-ups, filter bar, compact switch."""

    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("compactboss", "cb@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.today = timezone.localdate()
        self.lease = make_lease(self.tenant, self.flat, start=self.today - timedelta(days=40), rent="40000")
        self.payment = self.lease.payments.order_by("due_date").first()

    def url(self, name, *args):
        return reverse(f"admin:leases_{name}", args=args)

    def staff(self, *codenames):
        from django.contrib.auth.models import Permission

        self.staff_count = getattr(self, "staff_count", 0) + 1
        user = get_user_model().objects.create_user(f"compact{self.staff_count}", password="Pass-word1!", is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.client.force_login(user)

    def form(self, **extra):
        data = {
            "due_date": self.payment.due_date.isoformat(), "amount": "10000", "method": "cheque", "cheque_number": "004512",
            "bank_name": "ENBD", "cheque_date": "", "deposited_on": "", "status": "pending", "paid_on": "", "notes": "",
        }
        data.update(extra)
        return data

    # ---- the compact cells -----------------------------------------------------------
    def test_pay_cell_puts_cheque_details_in_one_place(self):
        from leases.admin import pay_cell

        self.payment.method = "cheque"
        self.payment.cheque_number, self.payment.bank_name = "004512", "ENBD"
        self.payment.cheque_date, self.payment.deposited_on = date(2026, 3, 1), date(2026, 3, 3)
        text = str(pay_cell(self.payment))
        self.assertIn("Cheque · 004512 · ENBD", text)
        self.assertIn("dated", text)
        self.assertIn("deposited", text)
        self.payment.method = "cash"
        self.assertIn("Cash", str(pay_cell(self.payment)))
        self.assertNotIn("004512", str(pay_cell(self.payment)))

    def test_state_cell_shows_paid_date_or_days_late(self):
        from leases.admin import state_cell

        self.assertIn("Overdue", str(state_cell(self.payment)))
        self.assertIn("days late", str(state_cell(self.payment)))
        self.payment.record(self.today, "cash")
        self.assertIn("Paid", str(state_cell(self.payment)))
        self.assertIn("on ", str(state_cell(self.payment)))

    def test_notes_show_as_an_icon_with_the_text(self):
        from leases.admin import note_mark

        self.assertEqual(note_mark(self.payment), "")
        self.payment.notes = "Tenant will pay Friday"
        self.assertIn('title="Tenant will pay Friday"', str(note_mark(self.payment)))

    def test_lease_page_has_a_compact_table_without_input_boxes(self):
        page = self.client.get(self.url("lease_change", self.lease.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-erp-act="editpay"')
        self.assertContains(page, 'data-erp-act="addpay"')
        self.assertContains(page, "erp-pay")
        self.assertNotContains(page, 'name="payments-0-amount"')

    def test_payment_list_uses_the_merged_columns(self):
        page = self.client.get(self.url("leasepayment_changelist"))
        self.assertContains(page, 'data-erp-act="editpay"')
        self.assertContains(page, self.tenant.name)
        self.assertNotContains(page, "data-erp-compact")

    # ---- the filter bar --------------------------------------------------------------
    def test_filter_bar_has_drop_downs_and_marks_active_ones(self):
        response = self.client.get(self.url("leasepayment_changelist"))
        titles = [f["title"] for f in response.context["filter_bar"]]
        self.assertTrue(any("tatus" in t for t in titles))
        self.assertContains(response, 'data-erp-nav="1"')
        self.assertFalse(any(f["active"] for f in response.context["filter_bar"]))
        active = self.client.get(self.url("leasepayment_changelist") + "?status__exact=pending").context["filter_bar"]
        self.assertTrue(any(f["active"] for f in active))
        self.assertContains(self.client.get(self.url("lease_changelist")), "erp-fbar")

    def test_filter_bar_options_are_working_links(self):
        for filt in self.client.get(self.url("leasepayment_changelist")).context["filter_bar"]:
            for query, _, _ in filt["options"]:
                self.assertTrue(query.startswith("?"), query)
                self.assertEqual(self.client.get(self.url("leasepayment_changelist") + query).status_code, 200, query)

    # ---- edit pop-up -----------------------------------------------------------------
    def test_edit_form_loads_current_values(self):
        data = self.client.get(self.url("leasepayment_edit", self.payment.pk)).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["values"]["amount"], "10000.00")
        self.assertEqual(data["part"], self.payment.part)
        self.assertTrue(data["can_remove"])

    def test_edit_saves_cheque_details(self):
        reply = self.client.post(self.url("leasepayment_edit", self.payment.pk), self.form(
            cheque_date="2026-03-01", deposited_on="2026-03-04", notes="Post-dated",
        )).json()
        self.assertTrue(reply["ok"])
        self.payment.refresh_from_db()
        self.assertEqual((self.payment.cheque_number, self.payment.bank_name), ("004512", "ENBD"))
        self.assertEqual((self.payment.cheque_date, self.payment.deposited_on), (date(2026, 3, 1), date(2026, 3, 4)))
        self.assertEqual(self.payment.notes, "Post-dated")

    def test_edit_can_mark_paid_and_changes_the_schedule_total(self):
        self.client.post(self.url("leasepayment_edit", self.payment.pk), self.form(status="paid", paid_on="2026-02-10", amount="9000"))
        self.payment.refresh_from_db()
        self.assertEqual((self.payment.status, self.payment.paid_on, self.payment.amount), ("paid", date(2026, 2, 10), Decimal("9000.00")))

    def test_edit_refuses_bad_input(self):
        url = self.url("leasepayment_edit", self.payment.pk)
        for bad in (
            {"amount": "0"}, {"amount": "abc"}, {"due_date": "nope"}, {"method": "gold"}, {"status": "lost"},
            {"cheque_date": "2026-03-10", "deposited_on": "2026-03-05"}, {"paid_on": "31/31/2026"},
        ):
            response = self.client.post(url, self.form(**bad))
            self.assertEqual(response.status_code, 400, bad)
            self.assertFalse(response.json()["ok"], bad)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount, Decimal("10000.00"))

    def test_edit_needs_permission_and_a_form_post(self):
        self.staff("view_leasepayment")
        url = self.url("leasepayment_edit", self.payment.pk)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, self.form(amount="1")).status_code, 403)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount, Decimal("10000.00"))
        self.client.force_login(self.boss)
        self.assertEqual(self.client.put(url).status_code, 405)

    # ---- add / remove ----------------------------------------------------------------
    def test_add_payment_takes_the_next_number(self):
        url = self.url("leasepayment_add_to", self.lease.pk)
        before = self.lease.payments.count()
        reply = self.client.post(url, {k: v for k, v in self.form(amount="500", notes="Parking").items() if k not in ("status", "paid_on")}).json()
        self.assertTrue(reply["ok"])
        self.assertEqual(self.lease.payments.count(), before + 1)
        added = self.lease.payments.order_by("-part").first()
        self.assertEqual((added.part, added.amount, added.status), (before + 1, Decimal("500.00"), "pending"))

    def test_add_payment_refuses_bad_input_and_missing_permission(self):
        url = self.url("leasepayment_add_to", self.lease.pk)
        before = self.lease.payments.count()
        self.assertEqual(self.client.post(url, self.form(amount="")).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.staff("view_leasepayment", "change_leasepayment")
        self.assertEqual(self.client.post(url, self.form()).status_code, 403)
        self.assertEqual(self.lease.payments.count(), before)

    def test_remove_payment_only_when_not_received(self):
        url = self.url("leasepayment_remove", self.payment.pk)
        self.staff("view_leasepayment", "change_leasepayment")
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.boss)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(self.client.post(url).json()["ok"])
        self.assertFalse(LeasePayment.objects.filter(pk=self.payment.pk).exists())
        paid = self.lease.payments.order_by("due_date").first()
        paid.record(self.today, "cash")
        response = self.client.post(self.url("leasepayment_remove", paid.pk))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(LeasePayment.objects.filter(pk=paid.pk).exists())

    def test_paid_payments_cannot_be_removed_through_the_edit_flag(self):
        self.payment.record(self.today, "cash")
        data = self.client.get(self.url("leasepayment_edit", self.payment.pk)).json()
        self.assertFalse(data["can_remove"])


class ScaleTools(Setup):
    """Update 22: search pickers, rows per page, extra filters, rent roll paging, bulk actions."""

    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("scaleboss", "sb@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.today = timezone.localdate()

    def url(self, name, *args):
        return reverse(f"admin:leases_{name}", args=args)

    def staff(self, *codenames):
        from django.contrib.auth.models import Permission

        self.staff_count = getattr(self, "staff_count", 0) + 1
        user = get_user_model().objects.create_user(f"scale{self.staff_count}", password="Pass-word1!", is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.client.force_login(user)

    # ---- pickers ---------------------------------------------------------------------
    def test_lease_form_does_not_list_every_tenant_and_unit(self):
        for number in range(30):
            make_tenant(f"Crowd Tenant {number:02d}")
            unit(self.tower, f"9{number:02d}")
        page = self.client.get(self.url("lease_add"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-erp-search="tenant"')
        self.assertContains(page, 'data-erp-search="unit"')
        self.assertNotContains(page, "Crowd Tenant 17")
        self.assertNotContains(page, "LSB-917")

    def test_chosen_tenant_and_unit_still_show_on_an_existing_lease(self):
        lease = make_lease(self.tenant, self.flat)
        page = self.client.get(self.url("lease_change", lease.pk))
        self.assertContains(page, self.tenant.name)
        self.assertContains(page, "LSB-101")

    def test_form_still_accepts_a_chosen_option_that_was_never_listed(self):
        from leases.admin import LeaseAdminForm

        other = unit(self.tower, "555")
        start = self.today
        form = LeaseAdminForm(data={
            "tenant": self.tenant.pk, "unit": other.pk, "property": "", "start_date": start, "end_date": start + timedelta(days=364),
            "annual_rent": "48000", "installments": "4", "status": "active", "security_deposit": "0", "deposit_status": "held",
            "commission": "0", "admin_fee": "0", "ejari_fee": "0", "deposit_refund_amount": "0", "late_fee_per_day": "0",
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_search_finds_tenants_units_and_properties(self):
        make_tenant("Findable Person", phone="+971501234567")
        url = self.url("lease_search")
        names = [r["label"] for r in self.client.get(url, {"kind": "tenant", "q": "findable"}).json()["results"]]
        self.assertEqual(names, ["Findable Person"])
        self.assertTrue(self.client.get(url, {"kind": "tenant", "q": "501234"}).json()["results"])
        units = self.client.get(url, {"kind": "unit", "q": "LSB"}).json()["results"]
        self.assertIn("LSB-101 · ", units[0]["label"])
        props = [r["label"] for r in self.client.get(url, {"kind": "property", "q": "LSV1"}).json()["results"]]
        self.assertEqual(len(props), 1)
        self.assertEqual(self.client.get(url, {"kind": "property", "q": "LSB"}).json()["results"], [])  # a building is not standalone

    def test_search_hides_people_and_places_that_cannot_sign_a_lease(self):
        make_tenant("Blocked Person", is_blacklisted=True)
        make_tenant("Dormant Person", is_active=False)
        url = self.url("lease_search")
        self.assertEqual(self.client.get(url, {"kind": "tenant", "q": "Person"}).json()["results"], [])

    def test_search_is_limited_refuses_bad_kinds_and_needs_permission(self):
        for number in range(40):
            make_tenant(f"Many Tenant {number:02d}")
        url = self.url("lease_search")
        self.assertEqual(len(self.client.get(url, {"kind": "tenant", "q": "Many"}).json()["results"]), 20)
        self.assertEqual(self.client.get(url, {"kind": "owner"}).status_code, 400)
        self.staff("view_lease")
        self.assertEqual(self.client.get(url, {"kind": "tenant"}).status_code, 403)

    # ---- rows per page and filters ---------------------------------------------------
    def test_rows_per_page_choice(self):
        url = self.url("leasepayment_changelist")
        page = self.client.get(url)
        self.assertEqual(page.context["cl"].list_per_page, 100)
        self.assertEqual([n for n, _, _ in page.context["per_options"]], [50, 100, 200])
        for n in (50, 200):
            reply = self.client.get(url, {"per": n})
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(reply.context["cl"].list_per_page, n)
        self.assertEqual(self.client.get(url, {"per": "7"}).context["cl"].list_per_page, 100)
        self.assertEqual(self.client.get(url, {"per": "abc"}).status_code, 200)
        self.assertEqual(self.client.get(self.url("lease_changelist"), {"per": 100}).context["cl"].list_per_page, 100)

    def test_per_page_is_kept_when_filtering(self):
        page = self.client.get(self.url("leasepayment_changelist"), {"per": 100})
        for filt in page.context["filter_bar"]:
            for query, _, _ in filt["options"][1:]:
                self.assertIn("per=100", query)

    def test_building_owner_and_month_filters(self):
        mine = make_lease(self.tenant, self.flat, start=self.today)
        other_tenant = make_tenant("Other Tenant")
        other = make_lease(other_tenant, self.villa, start=self.today)
        pay_url, lease_url = self.url("leasepayment_changelist"), self.url("lease_changelist")
        rows = self.client.get(lease_url, {"building": self.tower.pk}).context["cl"].result_list
        self.assertEqual([l.pk for l in rows], [mine.pk])
        rows = self.client.get(lease_url, {"owner": self.villa.owner_id}).context["cl"].result_list
        self.assertIn(other.pk, [l.pk for l in rows])
        rows = self.client.get(pay_url, {"building": self.villa.pk}).context["cl"].result_list
        self.assertEqual({p.lease_id for p in rows}, {other.pk})
        month = f"{self.today.year}-{self.today.month:02d}"
        rows = self.client.get(pay_url, {"month": month}).context["cl"].result_list
        self.assertTrue(rows and all((p.due_date.year, p.due_date.month) == (self.today.year, self.today.month) for p in rows))
        ends = f"{mine.end_date.year}-{mine.end_date.month:02d}"
        rows = self.client.get(lease_url, {"ends": ends}).context["cl"].result_list
        self.assertIn(mine.pk, [l.pk for l in rows])

    def test_odd_filter_values_are_ignored_not_crashed(self):
        make_lease(self.tenant, self.flat)
        on_payments = ({"building": "x"}, {"owner": "-1"}, {"month": "2026-13"}, {"month": "nonsense"})
        on_leases = ({"building": "x"}, {"owner": "-1"}, {"ends": "99999-99"}, {"ends": "2026-13"})
        for params in on_payments:
            self.assertEqual(self.client.get(self.url("leasepayment_changelist"), params).status_code, 200, params)
        for params in on_leases:
            self.assertEqual(self.client.get(self.url("lease_changelist"), params).status_code, 200, params)

    # ---- rent roll paging ------------------------------------------------------------
    def test_rent_roll_pages_and_totals_cover_every_lease(self):
        make_lease(self.tenant, self.flat, rent="1000")
        for number in range(54):
            make_lease(make_tenant(f"Roll Tenant {number:02d}"), unit(self.tower, f"8{number:02d}"), rent="1000")
        url = self.url("lease_rentroll")
        self.assertEqual(len(self.client.get(url).context["rows"]), 55)  # the default is 100 per page
        first = self.client.get(url, {"per": 50})
        self.assertEqual(len(first.context["rows"]), 50)
        self.assertEqual(first.context["total_rows"], 55)
        self.assertEqual(first.context["totals"]["rent"], Decimal("55000.00"))
        second = self.client.get(url, {"per": 50, "page": 2})
        self.assertEqual(len(second.context["rows"]), 5)
        self.assertEqual(len(self.client.get(url, {"per": 100}).context["rows"]), 55)
        self.assertEqual(self.client.get(url, {"page": "zzz"}).status_code, 200)

    def test_rent_roll_search_and_export_follow_the_search(self):
        make_lease(self.tenant, self.flat)
        make_lease(make_tenant("Needle Person"), self.villa)
        url = self.url("lease_rentroll")
        found = self.client.get(url, {"q": "needle"})
        self.assertEqual([r["tenant"] for r in found.context["rows"]], ["Needle Person"])
        csv = self.client.get(url, {"q": "needle", "export": "csv"}).content.decode("utf-8-sig")
        self.assertIn("Needle Person", csv)
        self.assertNotIn(self.tenant.name, csv)

    # ---- bulk ------------------------------------------------------------------------
    def bulk(self, ids, **data):
        return self.client.post(self.url("leasepayment_bulk"), {"ids": ",".join(str(i) for i in ids), **data})

    def test_bulk_mark_paid(self):
        lease = make_lease(self.tenant, self.flat, start=self.today - timedelta(days=100))
        first, second, third = list(lease.payments.order_by("due_date")[:3])
        second.record(self.today, "cash")
        reply = self.bulk([first.pk, second.pk, third.pk], action="paid", date=self.today.isoformat(), method="transfer")
        self.assertTrue(reply.json()["ok"])
        for p in (first, third):
            p.refresh_from_db()
            self.assertEqual((p.status, p.paid_on, p.method), ("paid", self.today, "transfer"))
        second.refresh_from_db()
        self.assertEqual(second.method, "cash")  # already received: left alone

    def test_bulk_paid_keeps_the_scheduled_method_when_none_is_chosen(self):
        lease = make_lease(self.tenant, self.flat)
        payment = lease.payments.order_by("due_date").first()
        payment.method, payment.cheque_number = "cheque", "777"
        payment.save()
        self.bulk([payment.pk], action="paid", date=self.today.isoformat(), method="")
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.method, payment.cheque_number), ("paid", "cheque", "777"))

    def test_bulk_deposit_only_touches_undeposited_cheques(self):
        lease = make_lease(self.tenant, self.flat)
        a, b, c, d = list(lease.payments.order_by("due_date"))
        for p in (a, b, d):
            p.method, p.cheque_number = "cheque", f"10{p.part}"
            p.save()
        b.deposited_on = date(2026, 1, 5)
        b.save()
        c.method = "cash"
        c.save()
        reply = self.bulk([a.pk, b.pk, c.pk, d.pk], action="deposited", date="2026-02-01")
        self.assertTrue(reply.json()["ok"])
        for p in (a, b, c, d):
            p.refresh_from_db()
        self.assertEqual((a.deposited_on, d.deposited_on), (date(2026, 2, 1), date(2026, 2, 1)))
        self.assertEqual(b.deposited_on, date(2026, 1, 5))  # kept its own date
        self.assertIsNone(c.deposited_on)

    def test_bulk_deposit_skips_a_date_before_the_cheque_date(self):
        lease = make_lease(self.tenant, self.flat)
        payment = lease.payments.order_by("due_date").first()
        payment.method, payment.cheque_date = "cheque", date(2026, 6, 1)
        payment.save()
        reply = self.bulk([payment.pk], action="deposited", date="2026-05-01")
        self.assertEqual(reply.status_code, 400)
        payment.refresh_from_db()
        self.assertIsNone(payment.deposited_on)

    def test_bulk_refuses_bad_requests(self):
        lease = make_lease(self.tenant, self.flat)
        pk = lease.payments.first().pk
        good = {"action": "paid", "date": self.today.isoformat()}
        self.assertEqual(self.bulk([], **good).status_code, 400)
        self.assertEqual(self.bulk([pk], action="frobnicate", date=good["date"]).status_code, 400)
        self.assertEqual(self.bulk([pk], action="paid", date="soon").status_code, 400)
        self.assertEqual(self.bulk([pk], method="gold", **good).status_code, 400)
        self.assertEqual(self.bulk(range(1, 600), **good).status_code, 400)
        self.assertEqual(self.client.get(self.url("leasepayment_bulk")).status_code, 405)
        self.assertEqual(LeasePayment.objects.filter(status="paid").count(), 0)

    def test_bulk_needs_permission(self):
        lease = make_lease(self.tenant, self.flat)
        pk = lease.payments.first().pk
        self.staff("view_leasepayment")
        self.assertEqual(self.bulk([pk], action="paid", date=self.today.isoformat()).status_code, 403)
        self.assertEqual(LeasePayment.objects.filter(status="paid").count(), 0)

    def test_bulk_buttons_only_show_for_people_who_can_change_payments(self):
        make_lease(self.tenant, self.flat)
        self.assertContains(self.client.get(self.url("leasepayment_changelist")), "data-erp-bulk")
        self.staff("view_leasepayment")
        self.assertNotContains(self.client.get(self.url("leasepayment_changelist")), "data-erp-bulk")

    def test_two_people_recording_the_same_payment_do_not_double_record(self):
        lease = make_lease(self.tenant, self.flat)
        payment = lease.payments.order_by("due_date").first()
        self.bulk([payment.pk], action="paid", date="2026-03-01")
        second = self.bulk([payment.pk], action="paid", date="2026-03-09")
        self.assertEqual(second.status_code, 400)  # nothing left to change
        payment.refresh_from_db()
        self.assertEqual(payment.paid_on, date(2026, 3, 1))


class SameLookEverywhere(Setup):
    """Update 23: filter drop-downs use the system's styled drop-down; browser suggestions are switched off."""

    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("lookboss", "lb@example.com", "Pass-word1!"))

    def url(self, name):
        return reverse(f"admin:leases_{name}")

    def test_filter_bar_drop_downs_are_not_marked_native(self):
        make_lease(self.tenant, self.flat)
        for name in ("lease_changelist", "leasepayment_changelist"):
            html = self.client.get(self.url(name)).content.decode()
            self.assertIn("erp-fbar", html)
            self.assertNotIn('data-erp-native="1" data-erp-nav', html)

    def test_autofill_switch_is_shipped(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "core" / "static" / "core" / "erp.js").read_text(encoding="utf-8")
        self.assertIn("function noAutofill", js)
        self.assertIn("noAutofill();", js)

    def test_title_case_helper_is_shipped(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "core" / "static" / "core" / "erp.js").read_text(encoding="utf-8")
        self.assertIn("function titleCaseHeadings", js)
        self.assertIn("window.erpTitleCase", js)
        self.assertIn("titleCaseHeadings();", js)


class LargeTestData(TestCase):
    """Update 24: the large test-data seeder (run here at a tenth of its size) and 100 rows per page everywhere."""

    def setUp(self):
        from unittest import mock

        patcher = mock.patch("core.data_registry.SAMPLE_SCALE", 0.1)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.boss = get_user_model().objects.create_superuser("bigboss", "bb@example.com", "Pass-word1!")
        self.client.force_login(self.boss)

    def test_seed_builds_consistent_data_and_runs_twice_without_doubling(self):
        from core.data_registry import run_seeders

        run_seeders()
        counts = (Owner.objects.count(), Property.objects.count(), Unit.objects.count(), Tenant.objects.count(),
                  Lease.objects.count(), LeasePayment.objects.count())
        owners, properties, units, tenants, leases, payments = counts
        self.assertGreater(owners, 4)
        self.assertGreater(properties, 30)
        self.assertGreater(units, 150)
        self.assertGreater(tenants, 150)
        self.assertGreater(leases, 150)
        self.assertGreater(payments, leases)
        run_seeders()
        self.assertEqual(counts, (Owner.objects.count(), Property.objects.count(), Unit.objects.count(),
                                  Tenant.objects.count(), Lease.objects.count(), LeasePayment.objects.count()))
        # nobody rents the same unit twice, every schedule adds up, and statuses already agree with the leases
        self.assertEqual(Lease.objects.filter(unit__isnull=False).values("unit").distinct().count(),
                         Lease.objects.filter(unit__isnull=False).count())
        for lease in Lease.objects.filter(ejari_number__startswith="BULK-", status=Lease.Status.ACTIVE)[:40]:
            self.assertEqual(sum(p.amount for p in lease.payments.filter(kind="rent")), lease.annual_rent)
        self.assertEqual(occupancy.refresh_all(), 0)
        self.assertTrue(LeasePayment.objects.filter(status=LeasePayment.Status.PENDING, due_date__lt=timezone.localdate()).exists())
        self.assertTrue(Unit.objects.filter(status=Unit.Status.OCCUPIED).exists())
        self.assertTrue(Unit.objects.filter(status=Unit.Status.VACANT).exists())

    def test_seed_is_off_at_scale_zero(self):
        from unittest import mock

        from core.data_registry import run_seeders

        with mock.patch("core.data_registry.SAMPLE_SCALE", 0):
            run_seeders()
        self.assertEqual(Property.objects.count(), 12)
        self.assertEqual(Lease.objects.filter(ejari_number__startswith="BULK-").count(), 0)

    def test_every_list_shows_100_rows_by_default_with_a_rows_per_page_choice(self):
        for name in ("properties_owner_changelist", "properties_property_changelist", "properties_unit_changelist",
                     "tenants_tenant_changelist", "leases_lease_changelist", "leases_leasepayment_changelist",
                     "core_auditentry_changelist", "auth_user_changelist"):
            url = reverse(f"admin:{name}")
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200, name)
            self.assertEqual(page.context["cl"].list_per_page, 100, name)
            self.assertEqual([n for n, _, _ in page.context["per_options"]], [50, 100, 200], name)
            self.assertEqual(self.client.get(url, {"per": 200}).context["cl"].list_per_page, 200, name)
            self.assertContains(page, "Rows per page")


class SmartSearchTools(Setup):
    """Update 24: smart search (phones, IDs, codes, unit references, typos), quick-filter chips, summary line, Ctrl+K search."""

    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("searchboss", "sb2@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.today = timezone.localdate()

    def url(self, name):
        return reverse(f"admin:{name}")

    def names(self, name, **params):
        reply = self.client.get(self.url(name), params)
        self.assertEqual(reply.status_code, 200, params)
        return reply, [str(obj) for obj in reply.context["cl"].result_list]

    def test_phone_numbers_are_found_in_any_format(self):
        from tenants.models import Tenant

        Tenant.objects.create(name="Phone Person", email="pp@example.com", phone="+971 50 123 4567")
        Tenant.objects.create(name="Other Person", email="op@example.com", phone="+971 55 999 8888")
        for query in ("0501234567", "+971501234567", "971-50-123-4567", "123 4567", "501234567"):
            self.assertEqual(self.names("tenants_tenant_changelist", q=query)[1], ["Phone Person"], query)

    def test_id_numbers_codes_and_contacts_are_found_without_duplicates(self):
        from tenants.models import Tenant, TenantContact, TenantDocument

        person = Tenant.objects.create(name="Id Person", email="id@example.com", phone="+971 50 000 0000")
        TenantDocument.objects.create(tenant=person, doc_type="emirates_id", number="784-1985-1234567-1")
        for _ in range(2):
            TenantContact.objects.create(tenant=person, name="Same Contact", mobile="+971 52 111 2222")
        self.assertEqual(self.names("tenants_tenant_changelist", q="784198512")[1], ["Id Person"])
        self.assertEqual(self.names("tenants_tenant_changelist", q="Same Contact")[1], ["Id Person"])  # two matching contacts, one row
        self.assertEqual(self.names("tenants_tenant_changelist", q="0521112222")[1], ["Id Person"])
        number = int(person.code.split("-")[1])
        self.assertEqual(self.names("tenants_tenant_changelist", q=f"ten{number}")[1], ["Id Person"])

    def test_unit_reference_lease_code_and_payments_search(self):
        other = unit(building("LSX"), "202")
        lease = make_lease(self.tenant, self.flat)
        self.assertEqual(self.names("properties_unit_changelist", q="LSB-101")[1], [str(self.flat)])
        self.assertEqual(self.names("properties_unit_changelist", q="lsb-1")[1], [str(self.flat)])
        self.assertEqual(self.names("properties_unit_changelist", q="LSX 202")[1], [str(other)])
        number = int(lease.code.split("-")[1])
        self.assertEqual(len(self.names("leases_lease_changelist", q=f"lse{number}")[1]), 1)
        self.assertEqual(len(self.names("leases_leasepayment_changelist", q=f"LSE-{number}")[1]), 4)
        self.assertEqual(len(self.names("leases_lease_changelist", q="LSB-101")[1]), 1)

    def test_typos_show_similar_names_with_a_note(self):
        from tenants.models import Tenant

        Tenant.objects.create(name="Khalid Al Mansoori", email="km@example.com", phone="+971 50 000 0001")
        Tenant.objects.create(name="Someone Else", email="se@example.com", phone="+971 50 000 0002")
        reply, found = self.names("tenants_tenant_changelist", q="Mansoury")
        self.assertEqual(found, ["Khalid Al Mansoori"])
        self.assertIn("similar", reply.context["search_note"])
        reply, found = self.names("tenants_tenant_changelist", q="Mansoori")
        self.assertEqual(found, ["Khalid Al Mansoori"])
        self.assertEqual(reply.context["search_note"], "")
        self.assertEqual(self.names("tenants_tenant_changelist", q="zzzzzzzz")[1], [])

    def test_quick_chips_count_and_filter(self):
        from tenants.models import Tenant, TenantDocument

        expired = Tenant.objects.create(name="Expired Doc", email="ed@example.com", phone="+971 50 000 0003")
        TenantDocument.objects.create(tenant=expired, doc_type="emirates_id", number="1", expiry_date=self.today - timedelta(days=5))
        make_lease(self.tenant, self.flat)
        reply, found = self.names("tenants_tenant_changelist")
        chips = {c["label"]: c for c in reply.context["quick_chips"]}
        self.assertEqual(chips["Expired Documents"]["count"], 1)
        self.assertEqual(chips["Has a Running Lease"]["count"], 1)
        self.assertEqual(self.names("tenants_tenant_changelist", chip="expired")[1], ["Expired Doc"])
        self.assertEqual(self.names("tenants_tenant_changelist", chip="renting")[1], [self.tenant.name])
        self.assertEqual(len(self.names("tenants_tenant_changelist", chip="nonsense")[1]), 2)
        for name, chip in (("properties_unit_changelist", "vacant"), ("properties_unit_changelist", "ending"),
                           ("properties_property_changelist", "vacancies"), ("properties_owner_changelist", "vacancies"),
                           ("properties_owner_changelist", "soon"), ("properties_property_changelist", "docs")):
            self.assertEqual(self.client.get(self.url(name), {"chip": chip}).status_code, 200, (name, chip))
        # the chip is kept in the links of the page (paging, rows per page, search)
        page = self.client.get(self.url("tenants_tenant_changelist"), {"chip": "expired", "per": 50})
        self.assertTrue(all("chip=expired" in url for _n, url, _sel in page.context["per_options"]))

    def test_summary_line_shows_what_is_narrowing_the_list(self):
        make_lease(self.tenant, self.flat)
        reply = self.client.get(self.url("properties_unit_changelist"), {"q": "LSB", "chip": "occupied"})
        summary = reply.context["list_summary"]
        self.assertTrue(summary["narrowed"])
        labels = [t["label"] for t in summary["tags"]]
        self.assertIn("Occupied", labels)
        self.assertTrue(any(label.startswith("Search") for label in labels))
        self.assertEqual(summary["total"], Unit.objects.count())
        plain = self.client.get(self.url("properties_unit_changelist")).context["list_summary"]
        self.assertFalse(plain["narrowed"])
        self.assertContains(reply, "Clear all")
        self.assertContains(reply, "data-erp-search-hint")

    def test_global_search_finds_records_and_respects_permissions(self):
        from django.test import RequestFactory

        from core.global_search import search_callback

        make_lease(self.tenant, self.flat)
        request = RequestFactory().get("/")
        request.user = self.boss
        results = search_callback(request, "LSB-101")
        self.assertTrue(any("LSB-101" in r.title for r in results))
        self.assertTrue(all(r.link.startswith("/") for r in results))
        self.assertEqual(search_callback(request, "x"), [])
        nobody = get_user_model().objects.create_user("nobody", password="Pass-word1!", is_staff=True)
        request.user = nobody
        self.assertEqual(search_callback(request, "LSB-101"), [])

    def test_pieces_are_shipped(self):
        from pathlib import Path

        from django.conf import settings

        js = (Path(__file__).resolve().parent.parent / "core" / "static" / "core" / "lists.js").read_text(encoding="utf-8")
        self.assertIn("erp-hl", js)
        self.assertEqual(settings.UNFOLD["COMMAND"]["search_callback"], "core.global_search.search_callback")
