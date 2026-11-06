"""Checks for the audit fixes (Update 26, part A). Run with:  python manage.py test"""
from decimal import Decimal

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core import exports, lockout
from core.audit import acting_as
from core.data_registry import tools_allowed
from core.models import AuditEntry
from core.overview import _lease_panels

from .models import LeasePayment
from django.contrib.auth import get_user_model

from .tests import Setup, make_lease


class LockoutAddress(TestCase):
    def test_direct_connection_uses_the_connection_address(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="1.2.3.4")
        with override_settings(BEHIND_PROXY=False):
            self.assertEqual(lockout._ip(request), "10.0.0.5")

    def test_behind_a_proxy_uses_the_visitor_address(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="9.9.9.9, 1.2.3.4")
        with override_settings(BEHIND_PROXY=True):
            self.assertEqual(lockout._ip(request), "1.2.3.4")  # the address the proxy itself saw


class DevToolsOnLiveServer(TestCase):
    def test_a_live_server_refuses_the_tools(self):
        with override_settings(ALLOW_DEV_TOOLS=False):
            self.assertFalse(tools_allowed())
        with override_settings(ALLOW_DEV_TOOLS=True):
            self.assertTrue(tools_allowed())  # development mode is still on in the test database


class LogOfDownloadsAndLogins(TestCase):
    def test_login_and_download_are_logged(self):
        user = get_user_model().objects.create_user("clerk1", password="Pass-word1!", is_staff=True)
        self.client.login(username="clerk1", password="Pass-word1!")
        self.assertTrue(AuditEntry.objects.filter(object_repr="Signed in", username="clerk1").exists())
        with acting_as(user):
            exports.csv_response(["A"], [["x"]], "rent-roll")
        entry = AuditEntry.objects.filter(object_repr__startswith="Downloaded").get()
        self.assertEqual(entry.username, "clerk1")


class DoubleClickProtection(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("boss26", "b26@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = make_lease(self.tenant, self.flat, rent="60000")

    def url(self, name, *args):
        return reverse(f"admin:leases_{name}", args=args)

    def test_a_lease_cannot_be_renewed_twice(self):
        self.assertTrue(self.client.post(self.url("lease_renew", self.lease.pk), {"mode": "same"}).json()["ok"])
        self.assertEqual(self.client.post(self.url("lease_renew", self.lease.pk), {"mode": "same"}).status_code, 400)

    def test_a_payment_cannot_be_recorded_twice(self):
        payment = self.lease.payments.order_by("due_date").first()
        data = {"action": "paid", "date": "2026-02-03", "method": "cash"}
        self.assertTrue(self.client.post(self.url("leasepayment_record", payment.pk), data).json()["ok"])
        second = self.client.post(self.url("leasepayment_record", payment.pk), {**data, "date": "2026-03-09"})
        self.assertEqual(second.status_code, 400)
        payment.refresh_from_db()
        self.assertEqual(payment.paid_on.isoformat(), "2026-02-03")  # the first record stands

    def test_dashboard_totals_match_the_payment_totals(self):
        self.lease.payments.update(status=LeasePayment.Status.BOUNCED)
        panels = {}
        _lease_panels(self.boss, panels)
        row = next(i for i in panels["attention"] if i["label"] == "Bounced cheques")
        self.assertEqual(row["count"], self.lease.payments.count())
        from leases.admin import _money

        self.assertEqual(row["detail"], _money(sum((p.total for p in self.lease.payments.all()), Decimal("0"))))
