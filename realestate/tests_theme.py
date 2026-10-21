"""Checks for the Unfold theme, the left menu and the quick buttons."""

from django.conf import settings
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

    def test_theme_is_installed_before_the_admin(self):
        apps = list(settings.INSTALLED_APPS)
        self.assertIn("unfold", apps)
        self.assertLess(apps.index("unfold"), apps.index("django.contrib.admin"))
        self.assertIsNotNone(finders.find("realestate/admin/ui.css"))

    def test_every_menu_link_opens(self):
        for group in settings.UNFOLD["SIDEBAR"]["navigation"]:
            for item in group["items"]:
                with self.subTest(item=item["title"]):
                    self.assertEqual(self.client.get(str(item["link"])).status_code, 200)

    def test_every_model_page_has_a_menu_entry(self):
        linked = {
            str(item["link"])
            for group in settings.UNFOLD["SIDEBAR"]["navigation"]
            for item in group["items"]
        }
        for model in (
            "cheque", "maintenancerequest", "expense", "property", "unit",
            "owner", "tenant", "lease", "ownerstatement", "vendor",
        ):
            with self.subTest(model=model):
                self.assertIn(reverse(f"admin:realestate_{model}_changelist"), linked)

    def test_home_page_has_the_dashboard_and_quick_buttons(self):
        page = self.client.get(reverse("admin:index"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "+ New lease")
        self.assertContains(page, "Deposit today")
        self.assertContains(page, "realestate/admin/ui.css")

    def test_login_and_app_pages_still_work(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin:login")).status_code, 200)
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse("admin:app_list", args=["realestate"])).status_code, 200
        )

    def test_forms_with_hidden_fields_open(self):
        for model, obj in (("property", self.property), ("owner", self.owner), ("tenant", self.tenant)):
            with self.subTest(model=model):
                url = reverse(f"admin:realestate_{model}_change", args=[obj.pk])
                self.assertEqual(self.client.get(url).status_code, 200)
