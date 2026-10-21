"""Checks for 'Erase all data' and for the new-lease payment rows."""

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from accounting.chart import seed_chart
from accounting.models import Account, AuditLog, FiscalPeriod, JournalEntry
from accounting.posting import LineSpec, post_entry

from .models import Owner, Tenant

PASSWORD = "a-long-test-password-1"


class EraseAllDataTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.boss = User.objects.create_superuser("boss", "b@example.com", PASSWORD)
        self.other = User.objects.create_user("clerk", password=PASSWORD, is_staff=True)
        Owner.objects.create(name="Owner One", passport_number="P1234567")
        Tenant.objects.create(name="Tenant One", emirates_id="784-1990-1234567-1")
        seed_chart()
        FiscalPeriod.ensure_year(2026)
        post_entry(
            date=date(2026, 3, 10),
            memo="Test",
            lines=[
                LineSpec("1010", debit=Decimal("100")),
                LineSpec("4010", credit=Decimal("100")),
            ],
        )
        self.url = reverse("admin:realestate_demorecord_erase")

    def test_only_a_superuser_can_open_it(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"phrase": "ERASE ALL DATA", "password": PASSWORD}).status_code, 403)
        self.assertEqual(Owner.objects.count(), 1)

    def test_needs_the_phrase_and_the_password(self):
        self.client.force_login(self.boss)
        page = self.client.get(self.url)
        self.assertContains(page, "ERASE ALL DATA")
        wrong_phrase = self.client.post(self.url, {"phrase": "erase", "password": PASSWORD})
        self.assertContains(wrong_phrase, "exactly")
        wrong_password = self.client.post(self.url, {"phrase": "ERASE ALL DATA", "password": "nope"})
        self.assertContains(wrong_password, "not correct")
        self.assertEqual(Owner.objects.count(), 1)
        self.assertEqual(JournalEntry.objects.count(), 1)

    def test_erases_everything_but_the_superuser_and_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "media"
            (media / "owners").mkdir(parents=True)
            (media / "owners" / "passport.pdf").write_text("x")
            with override_settings(MEDIA_ROOT=media):
                self.client.force_login(self.boss)
                response = self.client.post(self.url, {"phrase": "ERASE ALL DATA", "password": PASSWORD})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(list(media.iterdir()), [])
        self.assertEqual(Owner.objects.count(), 0)
        self.assertEqual(Tenant.objects.count(), 0)
        self.assertEqual(JournalEntry.objects.count(), 0)
        self.assertEqual(Account.objects.count(), 0)
        self.assertEqual(FiscalPeriod.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertEqual(list(get_user_model().objects.values_list("username", flat=True)), ["boss"])
        self.assertTrue(Group.objects.filter(name="Accountant").exists())
        # the admin still works afterwards and the ledger is locked again
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        seed_chart()
        FiscalPeriod.ensure_year(2026)
        entry = post_entry(
            date=date(2026, 3, 10),
            memo="After wipe",
            lines=[LineSpec("1010", debit=Decimal("5")), LineSpec("4010", credit=Decimal("5"))],
        )
        self.assertEqual(entry.number, "JV-2026-000001")  # numbering starts again
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM accounting_journalentry")


class NewLeasePaymentRowsTests(TestCase):
    def test_new_lease_has_no_return_reason_and_no_bounced_status(self):
        boss = get_user_model().objects.create_superuser("boss", "b@example.com", PASSWORD)
        self.client.force_login(boss)
        page = self.client.get(reverse("admin:realestate_lease_add"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "return_reason")
        self.assertNotContains(page, "Returned (bounced)")
