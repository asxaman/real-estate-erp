"""Checks for the Dashboard and the Expiring documents page."""
from datetime import timedelta

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from properties.models import Owner, OwnerDocument, Property, PropertyDocument, Unit, UnitDocument
from tenants.models import Tenant, TenantDocument


def _data():
    today = timezone.localdate()
    owner = Owner.objects.create(name="Overview Owner")
    prop = Property.objects.create(
        name="Overview Tower", code="OVT", property_type="residential_building", owner=owner, emirate="dubai",
        management_fee_type="percent", management_fee_percent=5,
    )
    unit = Unit.objects.create(property=prop, unit_number="101", floor="1", unit_type="studio", asking_rent=50000)
    tenant = Tenant.objects.create(name="Overview Tenant", email="ot@example.com", phone="+971 50 111 2222")
    OwnerDocument.objects.create(owner=owner, doc_type="passport", number="P1", expiry_date=today - timedelta(days=4))
    TenantDocument.objects.create(tenant=tenant, doc_type="visa", expiry_date=today + timedelta(days=10))
    PropertyDocument.objects.create(property=prop, doc_type="insurance", expiry_date=today + timedelta(days=50))
    UnitDocument.objects.create(unit=unit, doc_type="other", expiry_date=today + timedelta(days=500))
    UnitDocument.objects.create(unit=unit, doc_type="floor_plan")  # no expiry date: never listed
    return owner, prop, unit, tenant


class DashboardPage(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("dash", "d@example.com", "pw-12345-xyz")
        self.client.force_login(self.boss)
        _data()

    def test_home_page_is_the_dashboard(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for text in ("Welcome back", "Active properties", "Vacant units", "Occupancy", "Expired documents",
                     "Documents that need attention", "Units by status", "Recent activity", "Overview Owner"):
            self.assertIn(text, html, text)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_numbers(self):
        response = self.client.get("/admin/")
        cards = {c["label"]: c["value"] for c in response.context["cards"]}
        self.assertEqual(cards["Active properties"], 1)
        self.assertEqual(cards["Units"], 1)
        self.assertEqual(cards["Vacant units"], 1)
        self.assertEqual(cards["Occupancy"], "0%")
        self.assertEqual(cards["Active owners"], 1)
        self.assertEqual(cards["Active tenants"], 1)
        self.assertEqual(cards["Expired documents"], 1)
        self.assertEqual(cards["Expiring soon"], 1)  # the visa; the insurance is 50 days away

    def test_empty_system_still_opens(self):
        Unit.objects.all().delete()
        Property.objects.all().delete()
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_staff_without_permissions_sees_a_polite_page(self):
        User.objects.create_user("plain", password="pw-12345-xyz", is_staff=True)
        self.client.force_login(User.objects.get(username="plain"))
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("nothing to show yet", response.content.decode())

    def test_sidebar_links(self):
        html = self.client.get(reverse("admin:properties_owner_changelist")).content.decode()
        self.assertIn(reverse("dashboard"), html)
        self.assertIn(reverse("expiring_documents"), html)


class ExpiringDocumentsPage(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("exp", "e@example.com", "pw-12345-xyz")
        self.client.force_login(self.boss)
        _data()
        self.url = reverse("expiring_documents")

    def rows(self, query=""):
        response = self.client.get(self.url + query)
        self.assertEqual(response.status_code, 200, query)
        return response.context["page"].object_list

    def test_default_shows_expired_and_due_within_the_warning_window(self):
        rows = self.rows()
        self.assertEqual([r["document"] for r in rows], ["Passport", "Residence visa"])
        self.assertEqual([r["state"] for r in rows], ["expired", "soon"])  # soonest first

    def test_filters(self):
        self.assertEqual(len(self.rows("?state=expired")), 1)
        self.assertEqual(len(self.rows("?state=soon")), 1)
        self.assertEqual(len(self.rows("?state=all")), 4)  # four have an expiry date
        self.assertEqual(len(self.rows("?kind=tenant&state=all")), 1)
        self.assertEqual(len(self.rows("?kind=unit&state=all")), 1)
        self.assertEqual(len(self.rows("?days=90")), 3)  # the insurance now falls inside the window
        self.assertEqual(len(self.rows("?days=bogus&kind=nonsense&state=nonsense")), 2)  # bad values are ignored
        self.assertEqual([r["who"] for r in self.rows("?state=all&q=visa")], ["Overview Tenant"])

    def test_csv_download(self):
        response = self.client.get(self.url + "?state=all&export=csv")
        self.assertIn("attachment", response["Content-Disposition"])
        text = response.content.decode("utf-8-sig")
        self.assertIn("Overview Owner", text)
        self.assertIn("Expired", text)

    def test_paging(self):
        owner = Owner.objects.get(name="Overview Owner")
        for n in range(60):
            OwnerDocument.objects.create(
                owner=owner, doc_type="other", number=f"N{n}", expiry_date=timezone.localdate() - timedelta(days=1)
            )
        self.assertEqual(len(self.rows("?state=expired")), 50)
        self.assertEqual(len(self.rows("?state=expired&page=2")), 11)

    def test_only_what_the_user_may_see(self):
        user = User.objects.create_user("clerk", password="pw-12345-xyz", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_ownerdocument"))
        self.client.force_login(user)
        rows = self.rows("?state=all")
        self.assertEqual({r["kind"] for r in rows}, {"Owner"})

    def test_links_point_at_the_record(self):
        rows = self.rows("?state=all")
        owner = Owner.objects.get(name="Overview Owner")
        self.assertIn(reverse("admin:properties_owner_change", args=[owner.pk]), [r["url"] for r in rows])


class NewPermissionsForExistingRoles(TestCase):
    def test_roles_get_access_after_migrate(self):
        from django.apps import apps

        from properties.apps import grant_new_permissions

        manager = Group.objects.create(name="Property Manager")
        accountant = Group.objects.create(name="Accountant")
        grant_new_permissions(sender=None, apps=apps)
        self.assertTrue(manager.permissions.filter(codename="change_unitphoto").exists())
        self.assertTrue(manager.permissions.filter(codename="delete_propertydocument").exists())
        self.assertTrue(accountant.permissions.filter(codename="view_unitdocument").exists())
        self.assertFalse(accountant.permissions.filter(codename="change_unitdocument").exists())
