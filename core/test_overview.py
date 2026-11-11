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
                     "Documents that need attention", "Units and properties by status", "Recent activity", "Overview Owner"):
            self.assertIn(text, html, text)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_numbers(self):
        response = self.client.get("/admin/")
        cards = {c["label"]: c["value"] for c in response.context["cards"]}
        self.assertEqual(cards["Active properties"], 1)
        self.assertEqual(cards["Units"], 1)
        self.assertEqual(cards["Vacant"], 1)
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

    def test_search_is_like_the_other_lists(self):
        page = self.client.get(self.url)
        self.assertContains(page, "data-erp-search-hint")  # gives the search box its "i" icon and live search
        self.assertContains(page, 'id="changelist"')
        self.assertContains(page, 'id="content-main"')
        self.assertContains(page, 'placeholder="Search…"')

    def test_word_search_any_order_and_typos(self):
        names = lambda q: [r["who"] for r in self.rows("?state=all&q=" + q)]
        self.assertEqual(names("tenant+visa"), ["Overview Tenant"])  # words in any order, across columns
        self.assertEqual(names("visa+tenant"), ["Overview Tenant"])
        self.assertEqual(names("nothingmatches"), [])
        near = self.client.get(self.url, {"state": "all", "q": "Overvew"})  # a typo still finds it
        self.assertEqual(len(near.context["page"].object_list) > 0, True)
        self.assertContains(near, "similar names are shown")

    def test_number_search_ignores_dashes(self):
        owner = Owner.objects.get(name="Overview Owner")
        OwnerDocument.objects.create(
            owner=owner, doc_type="emirates_id", number="784-1990-1234567-1", expiry_date=timezone.localdate() - timedelta(days=2)
        )
        self.assertEqual(len(self.rows("?state=expired&q=784199012345671")), 1)
        self.assertEqual(len(self.rows("?state=expired&q=1234567")), 1)

    def test_chip_counts_and_sorting(self):
        page = self.client.get(self.url)
        self.assertEqual(page.context["state_counts"], {"attention": 2, "expired": 1, "soon": 1, "all": 4})
        self.assertContains(page, "erp-sortlink")
        asc = [r["who"] for r in self.rows("?state=all&o=who")]
        desc = [r["who"] for r in self.rows("?state=all&o=who&d=desc")]
        self.assertEqual(asc, sorted(asc, key=str.lower))
        self.assertEqual(desc, asc[::-1])
        self.assertEqual(len(self.rows("?o=bogus")), 2)  # an unknown sort is ignored

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
        self.assertEqual(len(self.rows("?state=expired&per=50")), 50)
        self.assertEqual(len(self.rows("?state=expired&per=50&page=2")), 11)
        page = self.client.get(self.url, {"state": "expired", "per": "50"})
        self.assertContains(page, "erp-footer-bar")  # the strip sits in the same footer as the admin lists
        self.assertEqual([n["label"] for n in page.context["pager_numbers"]], [1, 2])
        self.assertContains(page, "- 61 documents")
        self.assertNotContains(page, "Showing")  # the count is in the bottom strip only, as on the other lists
        self.assertContains(page, 'id="searchbar"')  # the same search box as the other lists
        self.assertContains(page, "erp-chip is-active")  # the same chip look (purple), not the old blue one

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

    def test_the_page_has_rows_per_page_and_a_go_to_page_box(self):
        page = self.client.get(self.url, {"state": "expired"})
        self.assertContains(page, 'id="result_list"')
        self.assertContains(page, 'data-erp-per="1"')
        self.assertContains(page, 'data-pages="1"')
        everything = self.client.get(self.url, {"state": "expired", "per": "200"})
        self.assertEqual(len(everything.context["page"].object_list), everything.context["total"])


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


class ReportsPage(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("rep", "r@example.com", "pw-12345-xyz")
        self.client.force_login(self.boss)

    def test_reports_page_lists_the_reports(self):
        page = self.client.get(reverse("reports"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Rent Roll")
        self.assertContains(page, "Monthly Collection")
        self.assertContains(page, reverse("admin:leases_lease_rentroll"))

    def test_reports_are_no_longer_in_the_lease_and_payment_lists(self):
        for name in ("admin:leases_lease_changelist", "admin:leases_leasepayment_changelist"):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotIn(reverse("admin:leases_lease_rentroll"), html, name)
            self.assertNotIn(reverse("admin:leases_lease_monthly"), html, name)
            self.assertNotIn("erp-reports", html, name)

    def test_menu_has_reports_and_staff_without_rights_see_none(self):
        self.assertContains(self.client.get("/admin/"), reverse("reports"))
        User.objects.create_user("plain2", password="pw-12345-xyz", is_staff=True)
        self.client.force_login(User.objects.get(username="plain2"))
        self.assertContains(self.client.get(reverse("reports")), "no reports available")


class CleanAddresses(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("addr", "a@example.com", "pw-12345-xyz")
        self.client.force_login(self.boss)

    def test_every_module_has_its_own_plain_address(self):
        expected = {
            "admin:properties_owner_changelist": "/admin/owners/",
            "admin:properties_property_changelist": "/admin/properties/",
            "admin:properties_unit_changelist": "/admin/units/",
            "admin:tenants_tenant_changelist": "/admin/tenants/",
            "admin:leases_lease_changelist": "/admin/leases/",
            "admin:leases_leasepayment_changelist": "/admin/tenant-payments/",
            "admin:auth_user_changelist": "/admin/users/",
            "admin:core_systemsettings_changelist": "/admin/system-settings/",
        }
        for name, address in expected.items():
            self.assertEqual(reverse(name), address)
            if name != "admin:core_systemsettings_changelist":  # (that one goes straight to the settings form)
                self.assertEqual(self.client.get(address).status_code, 200, address)

    def test_old_addresses_still_work(self):
        response = self.client.get("/admin/leases/leasepayment/?status__exact=bounced")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/admin/tenant-payments/?status__exact=bounced")
        self.assertEqual(self.client.get("/admin/properties/owner/add/")["Location"], "/admin/owners/add/")
        self.assertEqual(self.client.get("/admin/properties/property/7/change/")["Location"], "/admin/properties/7/change/")

    def test_the_reports_live_under_reports(self):
        self.assertEqual(reverse("admin:leases_lease_rentroll"), "/admin/reports/rent-roll/")
        self.assertEqual(reverse("admin:leases_lease_monthly"), "/admin/reports/monthly/")
        for name in ("admin:leases_lease_rentroll", "admin:leases_lease_monthly"):
            self.assertContains(self.client.get(reverse(name)), 'href="' + reverse("reports") + '"')  # the Back to Reports button

    def test_lease_reports_keep_working(self):
        self.assertEqual(self.client.get(reverse("admin:leases_lease_rentroll")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:leases_lease_monthly")).status_code, 200)
