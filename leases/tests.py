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
from properties.models import Property, Unit
from properties.tests import building, make_owner, standalone, unit
from tenants.models import Tenant

from . import occupancy
from .models import Lease, LeaseDocument, LeasePayment, add_months


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
        self.assertEqual(new.payments.count(), 4)

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
            "status": "active", "security_deposit": "0", "deposit_status": "held", "commission": "0", "admin_fee": "0",
            "ejari_number": "", "ejari_expiry": "", "terminated_on": "", "termination_reason": "", "notes": "",
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
