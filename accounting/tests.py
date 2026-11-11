"""Checks for Accounting: chart of accounts, automatic postings, manual entries, ledger and trial balance."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from payables.models import Expense

from . import posting
from .admin import balances
from .models import Account, JournalEntry, JournalLine

D = Decimal


def today():
    return timezone.localdate()


def manual(lines, description="Test entry", date=None):
    entry = JournalEntry.objects.create(date=date or today(), description=description)
    for code, dr, cr in lines:
        JournalLine.objects.create(entry=entry, account=posting.account(code), debit=D(dr), credit=D(cr))
    return entry


def make_expense(**kwargs):
    values = dict(payee="DEWA", description="Electricity", category="utilities", amount=D("500"), expense_date=today(), method="cash")
    values.update(kwargs)
    expense = Expense(**values)
    expense.full_clean(exclude=["file"])
    expense.save()
    return expense


class ChartTests(TestCase):
    def test_default_chart_is_made_and_protected(self):
        posting.ensure_chart()
        self.assertTrue(Account.objects.filter(code="1000", is_system=True).exists())
        cash = Account.objects.get(code="1000")
        cash.code = "1001"
        with self.assertRaises(ValidationError):
            cash.full_clean()

    def test_account_with_lines_cannot_be_deleted(self):
        manual([("1000", "10", "0"), ("3200", "0", "10")])
        with self.assertRaises(ProtectedError):
            posting.account("1000").delete()

    def test_line_needs_one_side(self):
        entry = JournalEntry.objects.create(date=today(), description="x")
        line = JournalLine(entry=entry, account=posting.account("1000"), debit=D("5"), credit=D("5"))
        with self.assertRaises(ValidationError):
            line.full_clean()


class PostingTests(TestCase):
    def test_expense_posts_and_follows_changes(self):
        expense = make_expense()
        entry = JournalEntry.objects.get(source_type="expense", source_id=expense.pk)
        dr = sum(l.debit for l in entry.lines.all())
        cr = sum(l.credit for l in entry.lines.all())
        self.assertEqual(dr, cr)
        self.assertEqual(cr, expense.total)
        self.assertTrue(entry.lines.filter(account__code="1000", credit=expense.total).exists())
        expense.cancelled = True
        expense.cancel_reason = "Duplicate"
        expense.save()
        self.assertFalse(JournalEntry.objects.filter(source_type="expense", source_id=expense.pk).exists())

    def test_paused_then_rebuilt(self):
        with posting.paused():
            expense = make_expense()
        self.assertFalse(JournalEntry.objects.filter(source_type="expense", source_id=expense.pk).exists())
        counts = posting.sync_all()
        self.assertEqual(counts["expense"], 1)
        self.assertTrue(JournalEntry.objects.filter(source_type="expense", source_id=expense.pk).exists())
        posting.sync_all()  # repeating is safe
        self.assertEqual(JournalEntry.objects.filter(source_type="expense").count(), 1)

    def test_trial_balance_is_balanced(self):
        make_expense()
        make_expense(amount=D("120"), vat_applies=True, method="transfer")
        manual([("1010", "1000", "0"), ("3200", "0", "1000")])
        totals = balances()
        self.assertEqual(sum(v[0] for v in totals.values()), sum(v[1] for v in totals.values()))


class ManualEntryTests(TestCase):
    def test_automatic_entry_cannot_be_voided(self):
        make_expense()
        entry = JournalEntry.objects.get(source_type="expense")
        entry.status = "voided"
        entry.void_reason = "x"
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_void_needs_reason_and_removes_from_balances(self):
        entry = manual([("1000", "50", "0"), ("3200", "0", "50")])
        entry.status = "voided"
        with self.assertRaises(ValidationError):
            entry.full_clean()
        entry.void_reason = "Typed twice"
        entry.full_clean()
        entry.save()
        self.assertEqual(balances(), {})


class ScreenTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "b@example.com", "pw")
        self.client.force_login(self.admin)
        make_expense()
        manual([("1010", "1000", "0"), ("3200", "0", "1000")], date=today() - timedelta(days=3))

    def test_pages_open(self):
        for name in ("changelist",):
            self.assertEqual(self.client.get(reverse(f"admin:accounting_account_{name}")).status_code, 200)
            self.assertEqual(self.client.get(reverse(f"admin:accounting_journalentry_{name}")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:accounting_journalentry_add")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:accounting_account_ledger")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:accounting_account_trialbalance")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:accounting_account_rebuild")).status_code, 200)

    def test_ledger_running_balance(self):
        bank = posting.account("1010")
        response = self.client.get(reverse("admin:accounting_account_ledger"), {"account": bank.pk, "from": "", "to": today().isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["closing"], D("1000.00"))

    def test_trial_balance_balanced_flag(self):
        response = self.client.get(reverse("admin:accounting_account_trialbalance"))
        self.assertTrue(response.context["balanced"])

    def test_rebuild_post(self):
        response = self.client.post(reverse("admin:accounting_account_rebuild"))
        self.assertEqual(response.status_code, 302)

    def test_entry_detail_opens(self):
        for entry in JournalEntry.objects.all():
            self.assertEqual(self.client.get(reverse("admin:accounting_journalentry_change", args=[entry.pk])).status_code, 200)

    def test_manual_entry_must_balance(self):
        bank, equity = posting.account("1010"), posting.account("3200")
        data = {
            "date": today().isoformat(), "description": "Unbalanced",
            "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
            "lines-0-account": bank.pk, "lines-0-debit": "100", "lines-0-credit": "0",
            "lines-1-account": equity.pk, "lines-1-debit": "0", "lines-1-credit": "90",
        }
        before = JournalEntry.objects.count()
        response = self.client.post(reverse("admin:accounting_journalentry_add"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(JournalEntry.objects.count(), before)
        data["lines-1-credit"] = "100"
        response = self.client.post(reverse("admin:accounting_journalentry_add"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(JournalEntry.objects.count(), before + 1)

    def test_seed_and_erase(self):
        from . import seed

        seed.seed_accounting()
        self.assertTrue(JournalEntry.objects.filter(description="Opening balances").exists())
        seed.erase_accounting()
        self.assertEqual(JournalEntry.objects.count(), 0)
        self.assertTrue(Account.objects.exists())

    def test_reports(self):
        make_expense(amount=D("200"), vat_applies=True)
        r = self.client.get(reverse("admin:accounting_account_profitloss"), {"from": "", "to": today().isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["expense_total"], D("700.00"))
        self.assertEqual(r.context["profit"], D("-700.00"))
        r = self.client.get(reverse("admin:accounting_account_balancesheet"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["balanced"])
        r = self.client.get(reverse("admin:accounting_account_vatreturn"), {"from": (today() - timedelta(days=30)).isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["input_vat"], D("10.00"))
        self.assertEqual(r.context["purchases_std"], D("200.00"))
        self.assertTrue(r.context["refund"])
