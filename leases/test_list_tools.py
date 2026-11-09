"""Lists: the "go to page" box, page numbers that do not exist, and the pieces of the scrolling helpers (Update 39)."""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse

from .tests import Setup, make_tenant


class ListPaging(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("pagerboss", "pb@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        for number in range(130):
            make_tenant(f"Pager Tenant {number:03d}")
        self.url = reverse("admin:tenants_tenant_changelist")  # 131 tenants: 3 pages of 50

    def test_a_long_list_gets_a_go_to_page_box(self):
        reply = self.client.get(self.url, {"per": 50, "p": 2})
        jump = reply.context["page_jump"]
        self.assertEqual((jump["pages"], jump["page"]), (3, 2))
        self.assertIn("__P__", jump["url"])
        self.assertIn("per=50", jump["url"])
        self.assertContains(reply, 'class="erp-jump"')
        self.assertContains(reply, 'data-pages="3"')

    def test_the_box_keeps_the_filters_and_the_quick_filter(self):
        reply = self.client.get(self.url, {"per": 50, "chip": "active"})
        url = reply.context["page_jump"]["url"]
        self.assertIn("chip=active", url)
        self.assertIn("per=50", url)

    def test_a_list_with_one_page_still_shows_page_1_of_1(self):
        reply = self.client.get(self.url, {"chip": "inactive"})  # nobody is inactive: one (empty) page
        self.assertEqual((reply.context["page_jump"]["pages"], reply.context["page_jump"]["page"]), (1, 1))
        self.assertContains(reply, 'data-pages="1"')

    def test_a_page_past_the_end_shows_the_last_page(self):
        reply = self.client.get(self.url, {"per": 50, "p": 99})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.context["page_jump"]["page"], 3)
        self.assertEqual(len(reply.context["cl"].result_list), 131 - 100)

    def test_a_page_below_one_or_not_a_number_shows_the_first_page(self):
        for wrong in ("0", "-4", "abc"):
            reply = self.client.get(self.url, {"per": 50, "p": wrong})
            self.assertEqual(reply.status_code, 200, wrong)
            self.assertEqual(reply.context["page_jump"]["page"], 1, wrong)
            self.assertEqual(len(reply.context["cl"].result_list), 50, wrong)

    def test_a_search_with_a_page_past_the_end_still_works(self):
        reply = self.client.get(self.url, {"q": "Pager Tenant 12", "p": 5})
        self.assertEqual(reply.status_code, 200)

    def test_every_main_list_opens_with_a_page_number(self):
        for name in ("properties_owner_changelist", "properties_property_changelist", "properties_unit_changelist",
                     "tenants_tenant_changelist", "leases_lease_changelist", "leases_leasepayment_changelist"):
            reply = self.client.get(reverse(f"admin:{name}"), {"p": 3})
            self.assertEqual(reply.status_code, 200, name)


class ListScrollHelpers(Setup):
    def test_the_scripts_and_styles_for_the_sticky_headings_and_the_top_pill_are_shipped(self):
        root = Path(__file__).resolve().parent.parent / "core" / "static" / "core"
        script = (root / "lists.js").read_text(encoding="utf-8")
        css = (root / "erp.css").read_text(encoding="utf-8")
        for text in ("buildHeadingCopy", "erp-totop", "Back to Top", "addPageBox", "erp-jump-go", "window.erpListChrome"):
            self.assertIn(text, script)
        for text in (".erp-sticky", ".erp-totop", ".erp-jump-in"):
            self.assertIn(text, css)

    def test_search_hint_moved_into_an_info_icon_and_the_page_box_moves_to_the_pager(self):
        root = Path(__file__).resolve().parent.parent / "core" / "static" / "core"
        script = (root / "lists.js").read_text(encoding="utf-8")
        for text in ("findPagerRow", "addSearchInfo", "You can search by: "):
            self.assertIn(text, script)
        self.client.force_login(get_user_model().objects.create_superuser("hintboss", "hb@example.com", "Pass-word1!"))
        reply = self.client.get(reverse("admin:leases_leasepayment_changelist"))
        self.assertNotContains(reply, "Search finds")
        self.assertContains(reply, "data-erp-search-hint")

    def test_rows_per_page_is_marked_so_it_can_sit_next_to_the_page_numbers(self):
        root = Path(__file__).resolve().parent.parent / "core" / "static" / "core"
        script = (root / "lists.js").read_text(encoding="utf-8")
        for text in ("erp-per-moved", "erp-pagerline", "findCountRow", "an empty list"):
            self.assertIn(text, script)
        self.client.force_login(get_user_model().objects.create_superuser("perboss", "pr@example.com", "Pass-word1!"))
        for name in ("admin:tenants_tenant_changelist", "admin:leases_leasepayment_changelist"):
            self.assertContains(self.client.get(reverse(name)), 'data-erp-per="1"')

    def test_the_top_of_a_list_has_no_showing_line(self):
        self.client.force_login(get_user_model().objects.create_superuser("showboss", "sb@example.com", "Pass-word1!"))
        for name in ("admin:tenants_tenant_changelist", "admin:leases_lease_changelist", "admin:leases_leasepayment_changelist"):
            self.assertNotContains(self.client.get(reverse(name)), "Showing")

