"""Checks for the accounting foundation: ledger rules, posting engine, reports."""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.urls import reverse

from realestate.models import Owner, Tenant, Vendor

from . import reports
from .chart import seed_chart
from .models import (
    Account,
    AccountingSettings,
    AuditLog,
    FiscalPeriod,
    ImmutableLedgerError,
    JournalEntry,
    JournalLine,
    NumberSequence,
)
from .posting import LineSpec, post_entry, reverse_entry

D = Decimal


def make_user(name, role=None, superuser=False):
    User = get_user_model()
    if superuser:
        return User.objects.create_superuser(name, f"{name}@example.com", "a-long-test-password-1")
    user = User.objects.create_user(name, password="a-long-test-password-1", is_staff=True)
    if role:
        user.groups.add(Group.objects.get(name=role))
    return user


class LedgerBase(TestCase):
    def setUp(self):
        seed_chart()
        FiscalPeriod.ensure_year(2026)
        self.day = date(2026, 3, 10)
        self.bank = Account.objects.get(code="1010")
        self.trust = Account.objects.get(code="1020")
        self.receivable = Account.objects.get(code="1100")
        self.owner_payable = Account.objects.get(code="2100")
        self.vendor_payable = Account.objects.get(code="2010")
        self.rent = Account.objects.get(code="4010")
        self.expense = Account.objects.get(code="5010")
        self.owner = Owner.objects.create(name="Owner One", passport_number="P1234567")
        self.tenant = Tenant.objects.create(name="Tenant One", emirates_id="784-1990-1234567-1")
        self.tenant2 = Tenant.objects.create(name="Tenant Two", emirates_id="784-1990-7654321-1")
        self.vendor = Vendor.objects.create(name="Cool AC LLC")

    def simple(self, dr, cr, amount, day=None, dr_kw=None, cr_kw=None, memo="Test entry", **extra):
        return post_entry(
            date=day or self.day,
            memo=memo,
            lines=[
                LineSpec(dr, debit=D(amount), **(dr_kw or {})),
                LineSpec(cr, credit=D(amount), **(cr_kw or {})),
            ],
            **extra,
        )


class PostingRulesTests(LedgerBase):
    def test_balanced_entry_is_posted_and_numbered(self):
        entry = self.simple(self.bank, self.rent, "1000")
        self.assertTrue(entry.is_posted)
        self.assertEqual(entry.number, "JV-2026-000001")
        self.assertEqual(entry.period.name, "2026-03")
        self.assertEqual(entry.lines.count(), 2)
        second = self.simple(self.bank, self.rent, "5")
        self.assertEqual(second.number, "JV-2026-000002")

    def test_unbalanced_entry_is_refused_and_leaves_nothing(self):
        with self.assertRaises(ValidationError) as caught:
            post_entry(
                date=self.day,
                memo="Bad",
                lines=[LineSpec(self.bank, debit=D("100")), LineSpec(self.rent, credit=D("99.99"))],
            )
        self.assertIn("does not balance", caught.exception.messages[0])
        self.assertEqual(JournalEntry.objects.count(), 0)
        self.assertEqual(JournalLine.objects.count(), 0)
        # the failed attempt did not use up a number
        self.assertEqual(self.simple(self.bank, self.rent, "1").number, "JV-2026-000001")

    def test_needs_two_lines(self):
        with self.assertRaises(ValidationError):
            post_entry(date=self.day, memo="One", lines=[LineSpec(self.bank, debit=D("5"))])

    def test_a_line_is_one_side_only_and_not_zero(self):
        for spec in (
            LineSpec(self.bank, debit=D("5"), credit=D("5")),
            LineSpec(self.bank, debit=D("0"), credit=D("0")),
            LineSpec(self.bank, debit=D("-5")),
        ):
            with self.assertRaises(ValidationError):
                post_entry(date=self.day, memo="x", lines=[spec, LineSpec(self.rent, credit=D("5"))])

    def test_more_than_two_decimals_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            post_entry(
                date=self.day,
                memo="x",
                lines=[LineSpec(self.bank, debit=D("100.001")), LineSpec(self.rent, credit=D("100.001"))],
            )
        self.assertIn("2 decimal", caught.exception.messages[0])

    def test_narration_is_required(self):
        with self.assertRaises(ValidationError):
            post_entry(date=self.day, memo="  ", lines=[LineSpec(self.bank, debit=D("1")), LineSpec(self.rent, credit=D("1"))])

    def test_cannot_post_to_headings_or_inactive_accounts(self):
        with self.assertRaises(ValidationError):
            self.simple("1000", self.rent, "10")  # "ASSETS" heading
        self.bank.is_active = False
        self.bank.save()
        with self.assertRaises(ValidationError):
            self.simple(self.bank, self.rent, "10")

    def test_unknown_account_code(self):
        with self.assertRaises(ValidationError):
            self.simple("9999", self.rent, "10")

    def test_numbers_restart_each_year_and_use_their_own_prefix(self):
        FiscalPeriod.ensure_year(2027)
        a = self.simple(self.bank, self.rent, "10")
        b = self.simple(self.bank, self.rent, "10", day=date(2027, 1, 5))
        self.assertEqual(a.number, "JV-2026-000001")
        self.assertEqual(b.number, "JV-2027-000001")
        opening = self.simple(self.bank, "3020", "10", source=JournalEntry.Source.OPENING)
        self.assertTrue(opening.number.startswith("OB-2026-"))


class PeriodTests(LedgerBase):
    def test_locked_period_refuses_posting_and_unlock_allows_it(self):
        user = make_user("amal", "Accountant")
        period = FiscalPeriod.for_date(self.day)
        period.lock(user)
        with self.assertRaises(ValidationError) as caught:
            self.simple(self.bank, self.rent, "10")
        self.assertIn("locked", caught.exception.messages[0])
        self.simple(self.bank, self.rent, "10", day=date(2026, 4, 2))  # other months stay open
        period.unlock(user, "test")
        self.simple(self.bank, self.rent, "10")
        actions = list(AuditLog.objects.filter(action__in=["lock", "unlock"]).values_list("action", flat=True))
        self.assertCountEqual(actions, ["lock", "unlock"])

    def test_no_period_no_posting(self):
        with self.assertRaises(ValidationError) as caught:
            self.simple(self.bank, self.rent, "10", day=date(2030, 1, 1))
        self.assertIn("no fiscal period", caught.exception.messages[0])

    def test_cutover_date_blocks_earlier_postings(self):
        settings_ = AccountingSettings.load()
        settings_.cutover_date = date(2026, 3, 1)
        settings_.save()
        with self.assertRaises(ValidationError):
            self.simple(self.bank, self.rent, "10", day=date(2026, 2, 20))
        self.simple(self.bank, self.rent, "10", day=date(2026, 3, 1))

    def test_periods_cannot_overlap_and_year_is_created_once(self):
        self.assertEqual(FiscalPeriod.ensure_year(2026), 0)
        self.assertEqual(FiscalPeriod.objects.filter(start_date__year=2026).count(), 12)
        clash = FiscalPeriod(name="odd", start_date=date(2026, 3, 15), end_date=date(2026, 4, 15))
        with self.assertRaises(ValidationError):
            clash.full_clean()


class ImmutabilityTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.entry = self.simple(self.bank, self.rent, "250")
        self.line = self.entry.lines.first()

    def test_python_refuses_changes_and_deletes(self):
        self.entry.memo = "changed"
        with self.assertRaises(ImmutableLedgerError):
            self.entry.save()
        self.line.debit = D("1")
        with self.assertRaises(ImmutableLedgerError):
            self.line.save()
        with self.assertRaises(ImmutableLedgerError):
            self.line.delete()
        with self.assertRaises(ImmutableLedgerError):
            JournalEntry.objects.get(pk=self.entry.pk).delete()
        with self.assertRaises(ImmutableLedgerError):
            JournalEntry.objects.all().delete()
        with self.assertRaises(ImmutableLedgerError):
            JournalEntry.objects.all().update(memo="x")
        with self.assertRaises(ImmutableLedgerError):
            JournalLine.objects.all().update(description="x")
        with self.assertRaises(ImmutableLedgerError):
            JournalLine.objects.all().delete()
        with self.assertRaises(ImmutableLedgerError):
            JournalLine.objects.create(entry=self.entry, account=self.bank, debit=D("1"))

    def test_the_database_itself_refuses_raw_sql(self):
        statements = [
            "UPDATE accounting_journalline SET description = 'x'",
            "DELETE FROM accounting_journalline",
            "UPDATE accounting_journalentry SET memo = 'x'",
            "DELETE FROM accounting_journalentry",
            "UPDATE accounting_auditlog SET detail = 'x'",
            "DELETE FROM accounting_auditlog",
        ]
        for sql in statements:
            with self.assertRaises(DatabaseError, msg=sql):
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(sql)
        self.assertEqual(JournalEntry.objects.get(pk=self.entry.pk).memo, "Test entry")

    def test_the_database_refuses_to_post_an_unbalanced_entry(self):
        draft = JournalEntry.objects.create(date=self.day, memo="draft")
        JournalLine.objects.create(entry=draft, account=self.bank, debit=D("100"))
        JournalLine.objects.create(entry=draft, account=self.expense, debit=D("50"))
        with self.assertRaises(ValidationError):
            draft.post()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE accounting_journalentry SET status = 'posted' WHERE id = %s", [draft.pk])
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")

    def test_locks_can_be_lifted_for_a_migration_and_put_back(self):
        from .triggers import drop_triggers, install_triggers

        def count():
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'ledger_%'")
                return cursor.fetchone()[0]

        installed = count()
        self.assertGreater(installed, 0)
        drop_triggers()
        self.assertEqual(count(), 0)
        install_triggers()
        self.assertEqual(count(), installed)

    def test_audit_log_cannot_be_changed(self):
        record = AuditLog.objects.first()
        with self.assertRaises(ImmutableLedgerError):
            record.save()
        with self.assertRaises(ImmutableLedgerError):
            record.delete()


class ReversalTests(LedgerBase):
    def test_reversal_cancels_the_entry_and_keeps_both(self):
        user = make_user("amal", "Accountant")
        entry = self.simple(self.bank, self.rent, "800")
        reversal = reverse_entry(entry, date=date(2026, 3, 20), reason="Wrong amount", user=user)
        self.assertTrue(reversal.number.startswith("REV-2026-"))
        self.assertEqual(reversal.reverses_id, entry.pk)
        self.assertTrue(JournalEntry.objects.get(pk=entry.pk).is_reversed)
        report = reports.trial_balance(date(2026, 12, 31))
        self.assertEqual(report["total_debit"], D("0.00"))
        self.assertEqual(report["total_credit"], D("0.00"))
        self.assertTrue(AuditLog.objects.filter(action="reverse", entry=reversal, user=user).exists())

    def test_cannot_reverse_twice_or_reverse_a_reversal_or_backdate(self):
        entry = self.simple(self.bank, self.rent, "800")
        with self.assertRaises(ValidationError):
            reverse_entry(entry, date=date(2026, 3, 1), reason="too early")
        reversal = reverse_entry(entry, date=self.day, reason="once")
        with self.assertRaises(ValidationError):
            reverse_entry(entry, date=self.day, reason="twice")
        with self.assertRaises(ValidationError):
            reverse_entry(reversal, date=self.day, reason="reverse the reversal")

    def test_reversal_into_a_locked_period_is_refused(self):
        entry = self.simple(self.bank, self.rent, "800")
        FiscalPeriod.for_date(self.day).lock()
        with self.assertRaises(ValidationError):
            reverse_entry(entry, date=date(2026, 3, 20), reason="locked")
        reverse_entry(entry, date=date(2026, 4, 2), reason="next month")

    def test_reversal_keeps_the_sub_ledger_party(self):
        entry = self.simple(self.receivable, self.rent, "300", dr_kw={"tenant": self.tenant})
        reverse_entry(entry, date=self.day, reason="cancel")
        balances = {r["party_id"]: r["balance"] for r in reports.party_balances(self.receivable)}
        self.assertEqual(balances[self.tenant.pk], D("0.00"))


class SubLedgerTests(LedgerBase):
    def test_control_accounts_need_the_right_party(self):
        with self.assertRaises(ValidationError):  # no tenant
            self.simple(self.receivable, self.rent, "10")
        with self.assertRaises(ValidationError):  # an owner on a tenant account
            self.simple(self.receivable, self.rent, "10", dr_kw={"owner": self.owner})
        with self.assertRaises(ValidationError):  # a party on a normal account
            self.simple(self.bank, self.rent, "10", dr_kw={"tenant": self.tenant})
        with self.assertRaises(ValidationError):  # two parties
            self.simple(self.receivable, self.rent, "10", dr_kw={"tenant": self.tenant, "owner": self.owner})
        self.simple(self.owner_payable, self.rent, "10", dr_kw={"owner": self.owner})
        self.simple(self.expense, self.vendor_payable, "10", cr_kw={"vendor": self.vendor})

    def test_sub_ledger_always_equals_the_control_account(self):
        self.simple(self.receivable, self.rent, "1000", dr_kw={"tenant": self.tenant})
        self.simple(self.receivable, self.rent, "500", dr_kw={"tenant": self.tenant2})
        self.simple(self.bank, self.receivable, "400", cr_kw={"tenant": self.tenant})
        rows = {r["account"].code: r for r in reports.reconciliation()}
        row = rows["1100"]
        self.assertEqual(row["gl_balance"], D("1100.00"))
        self.assertEqual(row["subledger_total"], D("1100.00"))
        self.assertTrue(row["ok"])
        balances = {r["party_id"]: r["balance"] for r in reports.party_balances(self.receivable)}
        self.assertEqual(balances, {self.tenant.pk: D("600.00"), self.tenant2.pk: D("500.00")})
        self.assertEqual(reports.ledger_health(), [])

    def test_party_statement_is_the_ledger_filtered_by_party(self):
        self.simple(self.receivable, self.rent, "1000", dr_kw={"tenant": self.tenant})
        self.simple(self.receivable, self.rent, "500", dr_kw={"tenant": self.tenant2})
        report = reports.general_ledger(self.receivable, party=("tenant", self.tenant.pk))
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["closing"], D("1000.00"))


class ReportTests(LedgerBase):
    def test_trial_balance_balances_and_respects_the_date(self):
        self.simple(self.bank, self.rent, "1000", day=date(2026, 3, 1))
        self.simple(self.expense, self.bank, "200", day=date(2026, 5, 1))
        march = reports.trial_balance(date(2026, 3, 31))
        self.assertTrue(march["balanced"])
        self.assertEqual(march["total_debit"], D("1000.00"))
        full = reports.trial_balance(date(2026, 12, 31))
        self.assertTrue(full["balanced"])
        by_code = {r["account"].code: r for r in full["rows"]}
        self.assertEqual(by_code["1010"]["debit"], D("800.00"))
        self.assertEqual(by_code["4010"]["credit"], D("1000.00"))
        self.assertEqual(by_code["5010"]["debit"], D("200.00"))

    def test_general_ledger_opening_running_and_closing_balance(self):
        self.simple(self.bank, self.rent, "500", day=date(2026, 3, 1))
        self.simple(self.expense, self.bank, "200", day=date(2026, 3, 5))
        self.simple(self.bank, self.rent, "100", day=date(2026, 4, 2))
        report = reports.general_ledger(self.bank, date(2026, 3, 10), date(2026, 12, 31))
        self.assertEqual(report["opening"], D("300.00"))
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["closing"], D("400.00"))
        everything = reports.general_ledger(self.bank)
        self.assertEqual([r["balance"] for r in everything["rows"]], [D("500.00"), D("300.00"), D("400.00")])

    def test_health_check_finds_a_numbering_gap(self):
        self.simple(self.bank, self.rent, "10")
        self.assertEqual(reports.ledger_health(), [])
        NumberSequence.objects.filter(key="JV-2026").update(last_number=5)
        problems = reports.ledger_health()
        self.assertTrue(any("gap" in p for p in problems))


class ChartTests(LedgerBase):
    def test_starter_chart_is_created_once_and_never_overwrites(self):
        self.assertEqual(seed_chart(), 0)
        account = Account.objects.get(code="1010")
        account.name = "Emirates NBD Operating"
        account.save()
        seed_chart()
        self.assertEqual(Account.objects.get(code="1010").name, "Emirates NBD Operating")

    def test_the_chart_has_the_required_accounts(self):
        self.assertEqual(self.trust.fund, "trust")
        self.assertEqual(self.bank.fund, "operating")
        self.assertEqual(self.receivable.control_kind, "tenant")
        self.assertEqual(self.owner_payable.control_kind, "owner")
        self.assertEqual(self.vendor_payable.control_kind, "vendor")
        for code in ("1110", "1120", "2200", "2300", "2400", "3020", "4020", "5100"):
            self.assertTrue(Account.objects.filter(code=code).exists(), code)

    def test_invalid_account_setups_are_refused(self):
        heading_control = Account(code="1900", name="x", account_type="asset", is_header=True, control_kind="tenant")
        bank_no_fund = Account(code="1901", name="x", account_type="asset", is_bank=True)
        fund_no_bank = Account(code="1902", name="x", account_type="asset", fund="trust")
        income_control = Account(code="4901", name="x", account_type="income", control_kind="tenant")
        bad_parent = Account(code="1903", name="x", account_type="asset", parent=self.bank)
        for account in (heading_control, bank_no_fund, fund_no_bank, income_control, bad_parent):
            with self.assertRaises(ValidationError, msg=account.code):
                account.full_clean()

    def test_an_account_with_postings_cannot_change_its_nature(self):
        self.simple(self.bank, self.rent, "10")
        account = Account.objects.get(code="4010")
        account.account_type = "liability"
        with self.assertRaises(ValidationError):
            account.save()
        account = Account.objects.get(code="4010")
        account.name = "Rent income (renamed)"
        account.save()  # renaming is fine

    def test_setup_command_and_check_command(self):
        out = StringIO()
        call_command("setup_accounting", "--years", "2027", stdout=out)
        self.assertEqual(FiscalPeriod.objects.filter(start_date__year=2027).count(), 12)
        out = StringIO()
        self.simple(self.bank, self.rent, "10")
        call_command("check_ledger", stdout=out)
        self.assertIn("healthy", out.getvalue())

    def test_settings_validation(self):
        settings_ = AccountingSettings.load()
        settings_.trn = "12345"
        with self.assertRaises(ValidationError):
            settings_.full_clean()
        settings_.trn = "100123456700003"
        settings_.full_clean()
        with self.assertRaises(ValidationError):
            settings_.delete()


class AdminTests(LedgerBase):
    def lines_post(self, *rows, **extra):
        data = {
            "date": "2026-03-10",
            "source": "manual",
            "memo": "Adjustment",
            "reference": "",
            "lines-TOTAL_FORMS": str(len(rows)),
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
        }
        for i, (account, debit, credit) in enumerate(rows):
            data.update(
                {
                    f"lines-{i}-account": str(account.pk),
                    f"lines-{i}-description": "",
                    f"lines-{i}-debit": debit,
                    f"lines-{i}-credit": credit,
                    f"lines-{i}-tenant": "",
                    f"lines-{i}-owner": "",
                    f"lines-{i}-vendor": "",
                }
            )
        data.update(extra)
        return data

    def test_accountant_posts_a_manual_journal_and_it_is_numbered(self):
        self.client.force_login(make_user("amal", "Accountant"))
        url = reverse("admin:accounting_journalentry_add")
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url, self.lines_post((self.bank, "250.00", "0.00"), (self.rent, "0.00", "250.00"))
        )
        self.assertEqual(response.status_code, 302)
        entry = JournalEntry.objects.get()
        self.assertTrue(entry.is_posted)
        self.assertEqual(entry.number, "JV-2026-000001")
        self.assertEqual(entry.created_by.username, "amal")

    def test_unbalanced_manual_journal_is_refused(self):
        self.client.force_login(make_user("amal", "Accountant"))
        response = self.client.post(
            reverse("admin:accounting_journalentry_add"),
            self.lines_post((self.bank, "250.00", "0.00"), (self.rent, "0.00", "249.00")),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not balance")
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_locked_period_is_refused_in_the_form(self):
        FiscalPeriod.for_date(self.day).lock()
        self.client.force_login(make_user("amal", "Accountant"))
        response = self.client.post(
            reverse("admin:accounting_journalentry_add"),
            self.lines_post((self.bank, "5.00", "0.00"), (self.rent, "0.00", "5.00")),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "locked")
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_posted_entry_is_read_only_and_can_be_reversed(self):
        entry = self.simple(self.bank, self.rent, "75")
        self.client.force_login(make_user("amal", "Accountant"))
        change = reverse("admin:accounting_journalentry_change", args=[entry.pk])
        page = self.client.get(change)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Reverse this entry")
        self.assertEqual(self.client.post(change, {"memo": "hacked"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:accounting_journalentry_delete", args=[entry.pk])).status_code, 403)
        url = reverse("admin:accounting_journalentry_reverse", args=[entry.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        missing = self.client.post(url, {"date": "2026-03-12", "reason": ""})
        self.assertContains(missing, "reason")
        done = self.client.post(url, {"date": "2026-03-12", "reason": "Entered twice"})
        self.assertEqual(done.status_code, 302)
        self.assertTrue(JournalEntry.objects.get(pk=entry.pk).is_reversed)
        self.assertContains(self.client.get(change), "was reversed by")

    def test_who_can_do_what(self):
        pages = {
            "trial": reverse("admin:accounting_account_trial_balance"),
            "ledger": reverse("admin:accounting_account_general_ledger"),
            "control": reverse("admin:accounting_account_reconciliation"),
            "chart": reverse("admin:accounting_account_changelist"),
            "journals": reverse("admin:accounting_journalentry_changelist"),
            "add": reverse("admin:accounting_journalentry_add"),
            "periods": reverse("admin:accounting_fiscalperiod_changelist"),
            "generate": reverse("admin:accounting_fiscalperiod_generate"),
            "audit": reverse("admin:accounting_auditlog_changelist"),
        }
        expectations = {
            "Accountant": dict.fromkeys(pages, 200),
            "Manager": {**dict.fromkeys(pages, 200), "add": 403, "generate": 403},
            "Viewer (read only)": dict.fromkeys(pages, 403),
            "Leasing agent": dict.fromkeys(pages, 403),
        }
        for role, expected in expectations.items():
            self.client.force_login(make_user("u-" + role.split()[0].lower(), role))
            for key, url in pages.items():
                self.assertEqual(self.client.get(url).status_code, expected[key], f"{role}: {key}")

    def test_reports_render_with_data(self):
        self.simple(self.receivable, self.rent, "900", dr_kw={"tenant": self.tenant})
        self.client.force_login(make_user("boss", superuser=True))
        trial = self.client.get(reverse("admin:accounting_account_trial_balance") + "?as_of=2026-12-31")
        self.assertContains(trial, "Balanced")
        ledger_url = reverse("admin:accounting_account_general_ledger")
        ledger = self.client.get(f"{ledger_url}?account={self.receivable.pk}&from=2026-01-01&to=2026-12-31")
        self.assertContains(ledger, "JV-2026-000001")
        party = self.client.get(
            f"{ledger_url}?account={self.receivable.pk}&party=tenant:{self.tenant.pk}&from=2026-01-01&to=2026-12-31"
        )
        self.assertContains(party, "Tenant One")
        control = reverse("admin:accounting_account_reconciliation")
        self.assertContains(self.client.get(control), "Agrees")
        self.assertContains(self.client.get(f"{control}?account={self.receivable.pk}"), "Tenant One")

    def test_only_those_allowed_can_lock_and_unlock_periods(self):
        period = FiscalPeriod.for_date(self.day)
        url = reverse("admin:accounting_fiscalperiod_changelist")
        self.client.force_login(make_user("amal", "Accountant"))
        self.client.post(url, {"action": "lock_selected", "_selected_action": [period.pk]})
        period.refresh_from_db()
        self.assertTrue(period.is_locked)
        # an Accountant may not re-open a locked period (superuser only)
        self.client.post(url, {"action": "unlock_selected", "_selected_action": [period.pk]})
        period.refresh_from_db()
        self.assertTrue(period.is_locked)
        self.client.force_login(make_user("boss", superuser=True))
        self.client.post(url, {"action": "unlock_selected", "_selected_action": [period.pk]})
        period.refresh_from_db()
        self.assertFalse(period.is_locked)

    def test_generate_periods_page(self):
        self.client.force_login(make_user("amal", "Accountant"))
        url = reverse("admin:accounting_fiscalperiod_generate")
        self.assertEqual(self.client.post(url, {"year": "2028"}).status_code, 302)
        self.assertEqual(FiscalPeriod.objects.filter(start_date__year=2028).count(), 12)

    def test_setup_page_creates_chart_and_periods_without_the_command_line(self):
        Account.objects.filter(code__in=["5100"]).delete()
        self.client.force_login(make_user("amal", "Accountant"))
        url = reverse("admin:accounting_setup")
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Accounting setup")
        self.assertEqual(self.client.post(url, {"action": "chart"}).status_code, 302)
        self.assertTrue(Account.objects.filter(code="5100").exists())
        self.assertEqual(self.client.post(url, {"action": "periods", "year": "2029"}).status_code, 302)
        self.assertEqual(FiscalPeriod.objects.filter(start_date__year=2029).count(), 12)
        # a role without the permission can neither open nor use it
        self.client.force_login(make_user("viv", "Viewer (read only)"))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(make_user("mia", "Manager"))
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"action": "chart"}).status_code, 403)
