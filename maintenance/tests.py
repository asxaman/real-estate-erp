"""Checks for the Vendors and Maintenance module."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import sysconf
from leases.tests import make_lease, make_tenant
from properties.models import Owner
from properties.tests import building, standalone, unit

from . import seed
from .models import MaintenanceRequest, Vendor, VendorDocument, approval_limit

D = Decimal


def make_vendor(name="Test Plumbing", **kwargs):
    values = dict(name=name, phone="+971 50 123 4567", trade="plumbing")
    values.update(kwargs)
    vendor = Vendor(**values)
    vendor.full_clean()
    vendor.save()
    return vendor


def make_job(prop, unit_=None, vendor=None, **kwargs):
    values = dict(property=prop, unit=unit_, title="Leaking tap", vendor=vendor)
    values.update(kwargs)
    job = MaintenanceRequest(**values)
    job.full_clean()
    job.save()
    return job


class Setup(TestCase):
    def setUp(self):
        self.tower = building("MNB")
        self.flat = unit(self.tower, "101")
        self.vendor = make_vendor()


class VendorRules(Setup):
    def test_codes_are_assigned_in_order(self):
        second = make_vendor("Second Vendor")
        self.assertEqual(self.vendor.code, "VND-0001")
        self.assertEqual(second.code, "VND-0002")

    def test_name_is_unique_ignoring_case(self):
        with self.assertRaises(ValidationError):
            make_vendor("test plumbing")

    def test_bad_phone_is_refused(self):
        with self.assertRaises(ValidationError):
            make_vendor("Bad Phone", phone="abc")

    def test_vendor_with_open_jobs_cannot_be_made_inactive(self):
        make_job(self.tower, self.flat, self.vendor)
        self.vendor.is_active = False
        with self.assertRaises(ValidationError) as caught:
            self.vendor.full_clean()
        self.assertIn("is_active", caught.exception.message_dict)

    def test_vendor_without_open_jobs_can_be_made_inactive(self):
        self.vendor.is_active = False
        self.vendor.inactive_reason = "Poor work"
        self.vendor.full_clean()

    def test_vendor_with_jobs_cannot_be_deleted(self):
        make_job(self.tower, self.flat, self.vendor)
        with self.assertRaises(ProtectedError):
            self.vendor.delete()

    def test_trade_licence_needs_expiry_and_is_single(self):
        with self.assertRaises(ValidationError):
            VendorDocument(vendor=self.vendor, doc_type="trade_licence").full_clean()
        self.assertEqual(self.vendor.missing_documents(), ["Trade licence"])
        VendorDocument.objects.create(vendor=self.vendor, doc_type="trade_licence", expiry_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.vendor.missing_documents(), [])
        self.assertEqual(self.vendor.document_counts(), (1, 0))


class Where(Setup):
    def test_unit_must_belong_to_the_property(self):
        other = building("MNO")
        with self.assertRaises(ValidationError) as caught:
            make_job(other, self.flat)
        self.assertIn("unit", caught.exception.message_dict)

    def test_standalone_property_has_no_unit(self):
        villa = standalone("MNV")
        villa.save()
        make_job(villa)
        with self.assertRaises(ValidationError):
            make_job(villa, self.flat)

    def test_property_is_required(self):
        with self.assertRaises(ValidationError) as caught:
            MaintenanceRequest(unit=self.flat, title="Crack in the wall").full_clean()
        self.assertIn("property", caught.exception.message_dict)

    def test_codes_and_future_dates(self):
        job = make_job(self.tower, self.flat)
        self.assertEqual(job.code, "MNT-0001")
        with self.assertRaises(ValidationError):
            make_job(self.tower, self.flat, reported_on=timezone.localdate() + timedelta(days=1))


class TenantAndPayer(Setup):
    def test_tenant_comes_from_the_running_lease(self):
        tenant = make_tenant("Mona Tenant")
        make_lease(tenant, self.flat)
        job = make_job(self.tower, self.flat)
        self.assertEqual(job.tenant, tenant)

    def test_no_lease_means_no_tenant(self):
        self.assertIsNone(make_job(self.tower, self.flat).tenant)

    def test_tenant_pays_needs_a_tenant(self):
        with self.assertRaises(ValidationError) as caught:
            make_job(self.tower, self.flat, paid_by="tenant")
        self.assertIn("tenant", caught.exception.message_dict)
        make_lease(make_tenant("Payer Tenant"), self.flat)
        make_job(self.tower, self.flat, paid_by="tenant")

    def test_company_owned_property_is_paid_by_the_company(self):
        company = Owner.objects.get(is_company=True)
        mine = building("MNC")
        mine.owner = company
        mine.save()
        job = make_job(mine)
        self.assertEqual(job.paid_by, "company")


class ApprovalRule(Setup):
    def test_company_default_limit_applies(self):
        self.assertEqual(approval_limit(self.tower.owner), D("2000"))

    def test_owner_limit_beats_the_company_default(self):
        self.tower.owner.maintenance_approval_limit = D("500")
        self.tower.owner.save()
        self.assertEqual(approval_limit(self.tower.owner), D("500"))
        job = make_job(self.tower, self.flat, estimate=D("800"))
        self.assertEqual(job.status, "awaiting_approval")

    def test_an_owner_limit_of_zero_means_everything_needs_approval(self):
        self.tower.owner.maintenance_approval_limit = D("0")
        self.tower.owner.save()
        self.assertEqual(make_job(self.tower, self.flat, estimate=D("1")).status, "awaiting_approval")
        self.assertEqual(make_job(self.tower, self.flat).status, "new")  # no cost yet: nothing to approve

    def test_small_job_needs_no_approval_and_can_start(self):
        job = make_job(self.tower, self.flat, self.vendor, estimate=D("1500"))
        self.assertEqual(job.status, "new")
        job.status = "in_progress"
        job.full_clean()

    def test_big_job_waits_and_cannot_start(self):
        job = make_job(self.tower, self.flat, self.vendor, estimate=D("5000"))
        self.assertEqual(job.status, "awaiting_approval")
        job.status = "in_progress"
        with self.assertRaises(ValidationError) as caught:
            job.full_clean()
        self.assertIn("status", caught.exception.message_dict)

    def test_approval_lets_it_start(self):
        user = User.objects.create_user("boss")
        job = make_job(self.tower, self.flat, self.vendor, estimate=D("5000"))
        job.approve(user, "ok")
        job.save()
        self.assertEqual(job.status, "approved")
        job.status = "in_progress"
        job.full_clean()
        job.save()

    def test_a_cost_that_grows_after_approval_needs_approval_again(self):
        user = User.objects.create_user("boss2")
        job = make_job(self.tower, self.flat, self.vendor, estimate=D("5000"))
        job.approve(user)
        job.save()
        job.estimate = D("9000")
        job.save()
        job.refresh_from_db()
        self.assertEqual(job.status, "awaiting_approval")
        self.assertIsNone(job.approved_on)

    def test_a_cost_that_shrinks_keeps_the_approval(self):
        user = User.objects.create_user("boss3")
        job = make_job(self.tower, self.flat, self.vendor, estimate=D("5000"))
        job.approve(user)
        job.save()
        job.estimate = D("4000")
        job.save()
        self.assertEqual(job.status, "approved")

    def test_reject_needs_a_reason_and_can_be_reopened(self):
        user = User.objects.create_user("boss4")
        job = make_job(self.tower, self.flat, self.vendor, estimate=D("5000"))
        job._decision = ("reject", user, "")
        with self.assertRaises(ValidationError):
            job.full_clean()
        job._decision = ("reject", user, "Too expensive")
        job.full_clean()
        job.save()
        self.assertEqual(job.status, "rejected")
        job.status = "new"
        job.full_clean()
        job.save()
        self.assertEqual(job.status, "awaiting_approval")
        self.assertEqual(job.rejection_reason, "")

    def test_nothing_to_approve_within_the_limit(self):
        user = User.objects.create_user("boss5")
        job = make_job(self.tower, self.flat, self.vendor, estimate=D("100"))
        job._decision = ("approve", user, "")
        with self.assertRaises(ValidationError):
            job.full_clean()

    def test_company_limit_can_be_changed_in_settings(self):
        self.addCleanup(sysconf.refresh)
        sysconf.refresh({"maintenance_approval_limit": D("100")})
        self.assertEqual(make_job(self.tower, self.flat, estimate=D("150")).status, "awaiting_approval")


class Completing(Setup):
    def test_completed_needs_vendor_final_cost_and_sets_the_date(self):
        job = make_job(self.tower, self.flat, estimate=D("300"))
        job.status = "completed"
        with self.assertRaises(ValidationError) as caught:
            job.full_clean()
        self.assertIn("vendor", caught.exception.message_dict)
        job.vendor = self.vendor
        with self.assertRaises(ValidationError) as caught:
            job.full_clean()
        self.assertIn("final_cost", caught.exception.message_dict)
        job.final_cost = D("280")
        job.full_clean()
        self.assertEqual(job.completed_on, timezone.localdate())

    def test_finishing_date_only_on_completed_jobs(self):
        job = make_job(self.tower, self.flat)
        job.completed_on = timezone.localdate()
        with self.assertRaises(ValidationError):
            job.full_clean()

    def test_final_cost_needs_a_vendor(self):
        with self.assertRaises(ValidationError):
            make_job(self.tower, self.flat, final_cost=D("100"))

    def test_inactive_vendor_cannot_get_new_jobs(self):
        self.vendor.is_active = False
        self.vendor.save()
        with self.assertRaises(ValidationError):
            make_job(self.tower, self.flat, self.vendor)

    def test_negative_money_is_refused(self):
        with self.assertRaises(ValidationError):
            make_job(self.tower, self.flat, estimate=D("-1"))


class Pages(Setup):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("root", "r@example.com", "pw")
        self.client.force_login(self.admin)
        self.job = make_job(self.tower, self.flat, self.vendor, estimate=D("5000"))

    def test_clean_addresses(self):
        self.assertEqual(reverse("admin:maintenance_vendor_changelist"), "/admin/vendors/")
        self.assertEqual(reverse("admin:maintenance_maintenancerequest_changelist"), "/admin/maintenance-requests/")

    def test_lists_and_chips_open(self):
        for name in ("vendor", "maintenancerequest"):
            url = reverse(f"admin:maintenance_{name}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200)
        url = reverse("admin:maintenance_maintenancerequest_changelist")
        for chip in ("open", "approval", "progress", "urgent", "overdue", "done", "closed"):
            self.assertEqual(self.client.get(url, {"chip": chip}).status_code, 200, chip)
        self.assertContains(self.client.get(url, {"q": "MNT-1"}), "Leaking tap")

    def test_forms_and_tabs_open(self):
        for url in (
            reverse("admin:maintenance_vendor_add"), reverse("admin:maintenance_maintenancerequest_add"),
            reverse("admin:maintenance_vendor_change", args=[self.vendor.pk]),
            reverse("admin:maintenance_maintenancerequest_change", args=[self.job.pk]),
            reverse("admin:properties_property_change", args=[self.tower.pk]),
            reverse("admin:properties_unit_change", args=[self.flat.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(reverse("admin:properties_unit_change", args=[self.flat.pk])), "MNT-0001")

    def post_data(self, **extra):
        data = {
            "property": self.tower.pk, "unit": self.flat.pk, "title": self.job.title, "description": "", "category": "plumbing",
            "priority": "normal", "reported_on": self.job.reported_on.isoformat(), "tenant": "", "status": self.job.status,
            "vendor": self.vendor.pk, "paid_by": "owner", "scheduled_on": "", "completed_on": "", "estimate": "5000",
            "final_cost": "", "notes": "", "decision": "", "decision_text": "",
        }
        data.update(extra)
        return data

    def test_approving_from_the_form(self):
        url = reverse("admin:maintenance_maintenancerequest_change", args=[self.job.pk])
        response = self.client.post(url, self.post_data(decision="approve", decision_text="Go ahead", status="in_progress"))
        self.assertEqual(response.status_code, 302, getattr(response, "context_data", None) and response.context_data["errors"])
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "in_progress")
        self.assertEqual(self.job.approved_by, self.admin)
        self.assertEqual(self.job.approved_amount, D("5000"))

    def test_rejecting_from_the_form_needs_a_reason(self):
        url = reverse("admin:maintenance_maintenancerequest_change", args=[self.job.pk])
        self.assertEqual(self.client.post(url, self.post_data(decision="reject")).status_code, 200)
        response = self.client.post(url, self.post_data(decision="reject", decision_text="Too dear"))
        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "rejected")

    def test_people_without_the_permission_cannot_decide(self):
        group = Group.objects.create(name="Editors")
        group.permissions.add(*Permission.objects.filter(
            content_type__app_label="maintenance", codename__in=["view_maintenancerequest", "change_maintenancerequest"]))
        editor = User.objects.create_user("editor", password="pw", is_staff=True)
        editor.groups.add(group)
        self.client.force_login(editor)
        url = reverse("admin:maintenance_maintenancerequest_change", args=[self.job.pk])
        self.assertNotContains(self.client.get(url), 'name="decision"')
        self.client.post(url, self.post_data(decision="approve"))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "awaiting_approval")

    def test_exports(self):
        url = reverse("admin:maintenance_maintenancerequest_changelist")
        response = self.client.post(url, {"action": "export_csv", "_selected_action": [self.job.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MNT-0001", response.content)
        url = reverse("admin:maintenance_vendor_changelist")
        response = self.client.post(url, {"action": "export_csv", "_selected_action": [self.vendor.pk]})
        self.assertIn(b"VND-0001", response.content)

    def test_global_search_and_expiring_documents(self):
        VendorDocument.objects.create(vendor=self.vendor, doc_type="trade_licence", expiry_date=timezone.localdate() + timedelta(days=5))
        from core.overview import collect_documents

        request = type("R", (), {"user": self.admin})()
        rows = collect_documents(request)
        self.assertTrue(any(r["kind"] == "Vendor" for r in rows))

    def test_owner_limit_on_the_owner_page(self):
        response = self.client.get(reverse("admin:properties_owner_change", args=[self.tower.owner.pk]))
        self.assertContains(response, "maintenance_approval_limit")


class SampleData(TestCase):
    def test_seed_and_erase(self):
        from properties.seed import seed_properties

        seed_properties()
        self.assertIn("vendors", seed.seed_maintenance())
        self.assertTrue(Vendor.objects.exists())
        seed.seed_maintenance()  # running it again adds nothing
        self.assertEqual(Vendor.objects.count(), len(seed.SAMPLE_VENDORS))
        seed.erase_maintenance()
        self.assertFalse(Vendor.objects.exists())
        self.assertFalse(MaintenanceRequest.objects.exists())

    def test_roles_get_permissions(self):
        group = Group.objects.create(name="Property Manager")
        seed.grant_role_permissions()
        self.assertTrue(group.permissions.filter(codename="approve_maintenancerequest").exists())
        accountant = Group.objects.create(name="Accountant")
        seed.grant_role_permissions()
        self.assertFalse(accountant.permissions.filter(codename="approve_maintenancerequest").exists())
