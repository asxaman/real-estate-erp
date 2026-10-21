"""Checks for the home page dashboard."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from .dashboard import _month_start, dashboard_context
from .models import Cheque
from .tests import BaseCase


class MonthHelperTests(BaseCase):
    def test_month_start(self):
        from datetime import date

        self.assertEqual(_month_start(date(2026, 3, 17)), date(2026, 3, 1))
        self.assertEqual(_month_start(date(2026, 3, 17), back=3), date(2025, 12, 1))
        self.assertEqual(_month_start(date(2026, 12, 5), back=-1), date(2027, 1, 1))


class DashboardTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            "boss", "boss@example.com", "a-long-test-password-1"
        )
        self.client.force_login(self.user)

    def test_empty_system_shows_the_home_page(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deposit today")
        self.assertContains(response, "Nobody owes rent right now.")

    def test_numbers_are_correct(self):
        lease = self.make_lease()
        self.make_cheque(lease, number="T1")  # received, dated today
        self.make_cheque(
            lease, number="O1", amount=Decimal("5000"),
            cheque_date=self.today - timedelta(days=3),
        )  # received, overdue
        self.make_cheque(
            lease, number="B1", status=Cheque.Status.CLEARED,
            cleared_date=self.today, amount=Decimal("7000"),
        )
        context = dashboard_context(None)["dash"]
        tiles = {label: count for label, count, _ in context["cheque_tiles"]}
        self.assertEqual(tiles["Deposit today"], 1)
        self.assertEqual(tiles["Overdue to deposit"], 1)
        self.assertEqual(context["collected"], Decimal("7000.00"))
        self.assertEqual(context["overdue_total"], Decimal("5000.00"))
        self.assertEqual(context["owing"][0]["total"], Decimal("5000.00"))
        self.assertEqual(context["units"]["occupied"], 1)
        self.assertEqual(context["occupancy"], 100)
        self.assertEqual(context["months"][-1]["total"], Decimal("7000.00"))

        page = self.client.get(reverse("admin:index"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Tenant One")

    def test_links_on_the_page_open(self):
        context = dashboard_context(None)["dash"]
        for key, value in context.items():
            if key.endswith("_url"):
                with self.subTest(link=key):
                    self.assertEqual(self.client.get(value).status_code, 200)

    def test_normal_app_list_is_still_there(self):
        self.assertContains(self.client.get(reverse("admin:index")), "Owner statements")
