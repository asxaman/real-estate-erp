"""Checks for the drawer handle that opens and closes the side panel."""
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

STATIC = Path(__file__).resolve().parent / "static" / "core"


class DrawerHandle(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("drawer", "d@example.com", "pw-12345-xyz"))
        self.script = (STATIC / "erp.js").read_text(encoding="utf-8")
        self.styles = (STATIC / "erp.css").read_text(encoding="utf-8")

    def test_the_script_builds_the_handle_from_the_top_bar_button(self):
        self.assertIn("erp-drawer", self.script)
        self.assertIn('main.querySelector(\'[x-on\\\\:click="sidebarToggle()"]\')', self.script)  # the top-bar button, not the panel's own
        self.assertIn('[x-show="sidebarOpen"]', self.script)  # follows the side panel, so it can close it too
        self.assertIn("chevron_left", self.script)
        self.assertIn("chevron_right", self.script)

    def test_the_styles_fix_it_to_the_middle_of_the_left_edge(self):
        rule = self.styles[self.styles.index(".erp-drawer {"):]
        rule = rule[:rule.index("}")]
        for part in ("position: fixed", "top: 50%", "z-index: 61"):  # above the side panel (which is 60)
            self.assertIn(part, rule)
        self.assertIn(".erp-drawer-original { display: none !important; }", self.styles)  # the old button is hidden

    def test_pages_still_have_what_the_handle_needs(self):
        for name in ("dashboard", "admin:properties_owner_changelist", "expiring_documents", "reports"):
            html = self.client.get(reverse(name)).content.decode()
            self.assertIn('id="main"', html, name)
            self.assertIn("sidebarToggle()", html, name)
            self.assertIn('x-show="sidebarOpen"', html, name)
            self.assertIn("erp.js", html, name)
