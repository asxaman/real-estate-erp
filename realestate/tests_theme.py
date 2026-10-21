"""Checks for the look and feel and the grouped menu."""

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.urls import reverse

from .tests import BaseCase


class ThemeTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            "boss", "boss@example.com", "a-long-test-password-1"
        )
        self.client.force_login(self.user)

    def test_stylesheet_exists_and_is_linked(self):
        self.assertIsNotNone(finders.find("realestate/admin/theme.css"))
        page = self.client.get(reverse("admin:index"))
        self.assertContains(page, "realestate/admin/theme.css")

    def test_menu_is_grouped_and_nothing_is_lost(self):
        page = self.client.get(reverse("admin:index"))
        groups = [app["name"] for app in page.context["app_list"]]
        for name in ("Daily work", "Properties and people", "Contracts", "Owner payouts"):
            self.assertIn(name, groups)
        shown = {
            model["object_name"]
            for app in page.context["app_list"]
            for model in app["models"]
        }
        for name in (
            "Cheque", "MaintenanceRequest", "Expense", "Property", "Unit",
            "Owner", "Tenant", "Lease", "OwnerStatement", "Vendor",
        ):
            self.assertIn(name, shown)

    def test_login_page_and_app_page_still_work(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin:login")).status_code, 200)
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse("admin:app_list", args=["realestate"])).status_code, 200
        )

    def test_quick_buttons_on_the_dashboard(self):
        page = self.client.get(reverse("admin:index"))
        self.assertContains(page, "+ New lease")
        self.assertContains(page, reverse("admin:realestate_ownerstatement_add"))
