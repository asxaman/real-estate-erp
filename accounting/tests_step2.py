"""Checks for the automatic postings: lease invoice, cheque lifecycle, month end."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from realestate.models import Cheque, Lease, Owner, Property, Tenant, Unit

from . import reports, rules
from .chart import seed_chart
from .models import (
    ZERO,
    Account,
    AccountingSettings,
    AccountMapping,
    FiscalPeriod,
    JournalEntry,
    JournalLine,
    LeaseInvoice,
    ReceiptVoucher,
    unmapped_roles,
)
from .tests import make_user

D = Decimal


class Step2Base(TestCase):
    def setUp(self):
        seed_chart()
        FiscalPeriod.ensure_year(2026)
        FiscalPeriod.ensure_year(2027)
        # status changes made "today" need today's month to exist
        for year in (timezone.localdate().year, timezone.localdate().year + 1):
            FiscalPeriod.ensure_year(year)
        settings_ = AccountingSettings.load()
        settings_.cutover_date = date(2026, 1, 1)
        settings_.legal_name = "Acme Property LLC"
        settings_.trn = "100123456700003"
        settings_.save()
        self.owner = Owner.objects.create(
            name="Owner One", passport_number="P1234567", management_fee_percent=D("10")
        )
        self.own_property = Property.objects.create(name="HQ Tower", ownership_type="company")
        self.tp_property = Property.objects.create(
            name="Marina", ownership_type="third_party", owner=self.owner
        )
        self.own_unit = Unit.objects.create(property=self.own_property, unit_number="101", rent_amount=D("12000"))
        self.tp_unit = Unit.objects.create(property=self.tp_property, unit_number="201", rent_amount=D("12000"))
        self.tenant = Tenant.objects.create(name="Tenant One", emirates_id="784-1990-1234567-1")

    def lease(self, unit, vat="0", start=date(2026, 3, 1), end=date(2027, 2, 28)):
        return Lease.objects.create(
            tenant=self.tenant,
            unit=unit,
            start_date=start,
            end_date=end,
            annual_rent=D("12000"),
            vat_percent=D(vat),
            number_of_payments=4,
        )

    def cheque(self, lease, number, amount, status="pending", **dates):
        return Cheque.objects.create(
            lease=lease,
            method="cheque",
            purpose="rent",
            installment_no=int(number[-1]),
            cheque_number=number,
            bank_name="ENBD",
            amount=D(amount),
            cheque_date=dates.pop("cheque_date", date(2026, 3, 1)),
            status=status,
            **dates,
        )

    def bal(self, code):
        account = Account.objects.get(code=code)
        agg = JournalLine.objects.filter(account=account, entry__status="posted").aggregate(
            d=Sum("debit"), c=Sum("credit")
        )
        return account.signed(agg["d"] or ZERO, agg["c"] or ZERO)

    def set_status(self, cheque, status, **dates):
        cheque.status = status
        for key, value in dates.items():
            setattr(cheque, key, value)
        cheque.save()
        return cheque


class LeaseInvoiceTests(Step2Base):
    def test_company_owned_invoice_defers_the_rent_and_holds_the_vat(self):
        lease = self.lease(self.own_unit, vat="5")
        invoice, errors = rules.create_lease_invoice(lease, date(2026, 3, 1))
        self.assertEqual(errors, [])
        self.assertEqual(invoice.number, "INV-2026-000001")
        self.assertEqual((invoice.rent_amount, invoice.vat_amount, invoice.total_amount), (D("12000.00"), D("600.00"), D("12600.00")))
        self.assertTrue(invoice.is_tax_invoice)
        self.assertEqual(self.bal("1100"), D("12600.00"))
        self.assertEqual(self.bal("2300"), D("12000.00"))
        self.assertEqual(self.bal("2410"), D("600.00"))
        self.assertEqual(self.bal("4010"), D("0.00"))  # nothing is income yet

    def test_third_party_invoice_is_held_for_the_owner_not_income(self):
        lease = self.lease(self.tp_unit)
        invoice, _ = rules.create_lease_invoice(lease, date(2026, 3, 1))
        self.assertFalse(invoice.is_tax_invoice)
        self.assertEqual(self.bal("1100"), D("12000.00"))
        self.assertEqual(self.bal("2110"), D("12000.00"))
        self.assertEqual(self.bal("2300"), D("0.00"))
        self.assertEqual(self.bal("2100"), D("0.00"))

    def test_refused_without_cutover_before_cutover_or_twice(self):
        lease = self.lease(self.own_unit)
        old = self.lease(self.tp_unit, start=date(2025, 6, 1), end=date(2026, 5, 31))
        with self.assertRaises(ValidationError):
            rules.create_lease_invoice(old)
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        with self.assertRaises(ValidationError):
            rules.create_lease_invoice(lease, date(2026, 3, 1))
        AccountingSettings.objects.filter(pk=1).update(cutover_date=None)
        other = self.lease(self.tp_unit, start=date(2026, 6, 1), end=date(2027, 5, 31))
        with self.assertRaises(ValidationError):
            rules.create_lease_invoice(other)

    def test_no_postings_happen_before_a_cutover_date_is_set(self):
        AccountingSettings.objects.filter(pk=1).update(cutover_date=None)
        lease = self.lease(self.own_unit)
        self.cheque(lease, "10001", "3000", status="received", received_date=date(2026, 3, 2))
        self.assertEqual(JournalEntry.objects.count(), 0)


class ChequeLifecycleTests(Step2Base):
    def setUp(self):
        super().setUp()
        self.lease_own = self.lease(self.own_unit, vat="5")
        rules.create_lease_invoice(self.lease_own, date(2026, 3, 1))

    def test_the_full_cheque_chain_for_company_owned_rent(self):
        cheque = self.cheque(self.lease_own, "10001", "3150")
        self.assertEqual(JournalEntry.objects.filter(source_tag="received").count(), 0)  # still expected

        self.set_status(cheque, "received", received_date=date(2026, 3, 2))
        self.assertEqual(self.bal("1110"), D("3150.00"))
        self.assertEqual(self.bal("1100"), D("9450.00"))  # the tenant owes less once the cheque is in hand
        voucher = ReceiptVoucher.objects.get()
        self.assertEqual(voucher.number, "RV-2026-000001")
        self.assertEqual(voucher.amount, D("3150.00"))

        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 5))
        self.assertEqual((self.bal("1110"), self.bal("1120")), (D("0.00"), D("3150.00")))

        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.assertEqual((self.bal("1120"), self.bal("1010")), (D("0.00"), D("3150.00")))
        # VAT for this instalment is now due: 3150 - 3000 = 150
        self.assertEqual((self.bal("2410"), self.bal("2400")), (D("450.00"), D("150.00")))
        self.assertEqual(reports.ledger_health(), [])

    def test_a_bounced_cheque_restores_the_receivable(self):
        cheque = self.cheque(self.lease_own, "10002", "3150", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 5))
        self.set_status(cheque, "returned", returned_date=date(2026, 3, 9), return_reason="insufficient_funds")
        self.assertEqual(self.bal("1100"), D("12600.00"))
        self.assertEqual((self.bal("1110"), self.bal("1120"), self.bal("1010")), (D("0.00"),) * 3)
        self.assertEqual(reports.ledger_health(), [])

    def test_posting_again_changes_nothing(self):
        cheque = self.cheque(self.lease_own, "10003", "3150", status="received", received_date=date(2026, 3, 2))
        before = JournalEntry.objects.count()
        self.assertEqual(rules.sync_cheque(cheque), 0)
        cheque.save()
        self.assertEqual(JournalEntry.objects.count(), before)

    def test_going_back_a_step_reverses_only_that_step(self):
        cheque = self.cheque(self.lease_own, "10004", "3150", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 5))
        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.set_status(cheque, "deposited")
        self.assertEqual((self.bal("1010"), self.bal("1120")), (D("0.00"), D("3150.00")))
        self.assertTrue(JournalEntry.objects.filter(source="reversal").exists())
        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.assertEqual((self.bal("1010"), self.bal("1120")), (D("3150.00"), D("0.00")))
        self.assertEqual(reports.ledger_health(), [])

    def test_cash_payment_goes_straight_to_the_bank_and_gets_a_receipt(self):
        cash = Cheque.objects.create(
            lease=self.lease_own, method="cash", purpose="rent", amount=D("3150"),
            cheque_date=date(2026, 3, 1), status="cleared", cleared_date=date(2026, 3, 3),
        )
        self.assertEqual(self.bal("1010"), D("3150.00"))
        self.assertEqual(self.bal("1100"), D("9450.00"))
        self.assertEqual(ReceiptVoucher.objects.filter(cheque=cash).count(), 1)

    def test_cancelling_a_cheque_in_hand_voids_its_receipt(self):
        cheque = self.cheque(self.lease_own, "10005", "3150", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "cancelled")
        self.assertEqual(self.bal("1100"), D("12600.00"))
        self.assertTrue(ReceiptVoucher.objects.get().is_void)

    def test_a_locked_month_does_not_block_saving_the_cheque(self):
        cheque = self.cheque(self.lease_own, "10006", "3150", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "deposited", deposited_date=date(2026, 4, 5))
        FiscalPeriod.objects.get(name="2026-04").lock()
        self.set_status(cheque, "cleared", cleared_date=date(2026, 4, 10))  # no error
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, "cleared")
        self.assertEqual(self.bal("1010"), D("0.00"))
        waiting = rules.waiting_items()["cheques"]
        self.assertEqual([w["cheque"].pk for w in waiting], [cheque.pk])
        posted, errors = rules.post_all_waiting()
        self.assertEqual(posted, 0)
        self.assertTrue(errors)
        FiscalPeriod.objects.get(name="2026-04").unlock()
        posted, errors = rules.post_all_waiting()
        self.assertEqual(errors, [])
        self.assertEqual(self.bal("1010"), D("3150.00"))
        self.assertEqual(rules.waiting_items()["cheques"], [])

    def test_only_rent_payments_are_posted_in_this_step(self):
        Cheque.objects.create(
            lease=self.lease_own, method="cash", purpose="security_deposit", amount=D("1000"),
            cheque_date=date(2026, 3, 1), status="cleared", cleared_date=date(2026, 3, 3),
        )
        self.assertEqual(self.bal("1010"), D("0.00"))
        self.assertEqual(rules.waiting_items()["other_purposes"], 1)


class ThirdPartyTests(Step2Base):
    def test_clearing_credits_the_owner_and_takes_the_fee_and_its_vat(self):
        lease = self.lease(self.tp_unit)
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        cheque = self.cheque(lease, "20001", "3000", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 5))
        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.assertEqual(self.bal("1020"), D("3000.00"))  # owners' money sits in the trust bank
        self.assertEqual(self.bal("1010"), D("0.00"))
        self.assertEqual(self.bal("2110"), D("9000.00"))
        self.assertEqual(self.bal("2100"), D("2685.00"))  # 3000 - 300 fee - 15 VAT on the fee
        self.assertEqual(self.bal("4020"), D("300.00"))
        self.assertEqual(self.bal("2400"), D("15.00"))
        self.assertEqual(self.bal("4010"), D("0.00"))  # third-party rent is never our income
        balances = {r["party_id"]: r["balance"] for r in reports.party_balances(Account.objects.get(code="2100"))}
        self.assertEqual(balances, {self.owner.pk: D("2685.00")})
        self.assertTrue(all(row["ok"] for row in reports.reconciliation()))
        self.assertEqual(reports.ledger_health(), [])

    def test_a_bounce_after_clearing_undoes_the_owner_credit(self):
        lease = self.lease(self.tp_unit)
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        cheque = self.cheque(lease, "20002", "3000", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 5))
        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.set_status(cheque, "returned", returned_date=date(2026, 3, 20), return_reason="stop_payment")
        self.assertEqual((self.bal("1020"), self.bal("2100"), self.bal("4020"), self.bal("2400")), (D("0.00"),) * 4)
        self.assertEqual(self.bal("1100"), D("12000.00"))
        self.assertEqual(self.bal("2110"), D("12000.00"))
        self.assertEqual(reports.ledger_health(), [])


class MonthEndTests(Step2Base):
    def setUp(self):
        super().setUp()
        self.lease_own = self.lease(self.own_unit, vat="5")
        rules.create_lease_invoice(self.lease_own, date(2026, 3, 1))

    def test_rent_is_released_by_exact_days_and_is_not_repeated(self):
        result = rules.run_month_end(2026, 3)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.bal("4010"), D("1019.18"))  # 12,000 x 31 / 365
        self.assertEqual(self.bal("2300"), D("10980.82"))
        entries = JournalEntry.objects.count()
        rules.run_month_end(2026, 3)
        self.assertEqual(JournalEntry.objects.count(), entries)

    def test_over_the_whole_lease_exactly_the_rent_is_released(self):
        for year, month in [(2026, m) for m in range(3, 13)] + [(2027, 1), (2027, 2)]:
            result = rules.run_month_end(year, month)
            self.assertEqual(result["errors"], [], (year, month))
        self.assertEqual(self.bal("4010"), D("12000.00"))
        self.assertEqual(self.bal("2300"), D("0.00"))
        rules.run_month_end(2027, 3)  # nothing more to release
        self.assertEqual(self.bal("4010"), D("12000.00"))

    def test_skipping_months_catches_up(self):
        rules.run_month_end(2026, 5)
        self.assertEqual(self.bal("4010"), (D("12000") * 92 / 365).quantize(D("0.01")))  # 1 Mar to 31 May

    def test_vat_becomes_due_when_the_instalment_date_arrives(self):
        self.cheque(self.lease_own, "10010", "3150", status="received", received_date=date(2026, 3, 2), cheque_date=date(2026, 3, 1))
        self.cheque(self.lease_own, "10011", "3150", status="received", received_date=date(2026, 3, 2), cheque_date=date(2026, 6, 1))
        result = rules.run_month_end(2026, 3)
        self.assertEqual(result["vat_entries"], 1)
        self.assertEqual(self.bal("2400"), D("150.00"))
        rules.run_month_end(2026, 6)
        self.assertEqual(self.bal("2400"), D("300.00"))
        rules.run_month_end(2026, 6)
        self.assertEqual(self.bal("2400"), D("300.00"))

    def test_third_party_leases_have_no_deferred_release(self):
        tp = self.lease(self.tp_unit)
        rules.create_lease_invoice(tp, date(2026, 3, 1))
        rules.run_month_end(2026, 3)
        self.assertEqual(self.bal("2110"), D("12000.00"))


class MappingAndDocumentTests(Step2Base):
    def test_each_job_points_to_an_account(self):
        self.assertEqual(AccountMapping.objects.count(), 12)
        self.assertEqual(unmapped_roles(), [])
        trust_job = AccountMapping.objects.get(key="bank_trust")
        trust_job.account = Account.objects.get(code="1010")  # the company's own bank: wrong
        with self.assertRaises(ValidationError):
            trust_job.full_clean()

    def test_a_missing_account_is_reported_and_never_breaks_saving_a_cheque(self):
        lease = self.lease(self.own_unit)
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        AccountMapping.objects.filter(key="pdc_in_hand").delete()
        Account.objects.filter(code="1110").update(code="1119")
        self.assertEqual(unmapped_roles(), ["Post-dated cheques in hand"])
        cheque = self.cheque(lease, "40001", "3000", status="received", received_date=date(2026, 3, 2))  # saves fine
        self.assertEqual(Cheque.objects.filter(pk=cheque.pk).count(), 1)
        self.assertEqual(self.bal("1100"), D("12000.00"))  # but nothing was posted
        with self.assertRaises(ValidationError):
            rules.sync_cheque(cheque)
        self.assertEqual(len(rules.waiting_items()["cheques"]), 1)

    def test_invoice_and_receipt_print_pages_carry_the_trn(self):
        lease = self.lease(self.own_unit, vat="5")
        invoice, _ = rules.create_lease_invoice(lease, date(2026, 3, 1))
        cheque = self.cheque(lease, "30001", "3150", status="received", received_date=date(2026, 3, 2))
        self.client.force_login(make_user("boss", superuser=True))
        page = self.client.get(reverse("admin:accounting_leaseinvoice_print", args=[invoice.pk]))
        self.assertContains(page, "TAX INVOICE")
        self.assertContains(page, "100123456700003")
        self.assertContains(page, "INV-2026-000001")
        voucher = ReceiptVoucher.objects.get(cheque=cheque)
        receipt = self.client.get(reverse("admin:accounting_receiptvoucher_print", args=[voucher.pk]))
        self.assertContains(receipt, "RECEIPT VOUCHER")
        self.assertContains(receipt, "100123456700003")
        self.set_status(cheque, "cancelled")
        self.assertContains(self.client.get(reverse("admin:accounting_receiptvoucher_print", args=[voucher.pk])), "VOID")

    def test_issued_invoices_cannot_change(self):
        lease = self.lease(self.own_unit)
        invoice, _ = rules.create_lease_invoice(lease, date(2026, 3, 1))
        invoice.total_amount = D("1")
        with self.assertRaises(ValidationError):
            invoice.save()
        with self.assertRaises(ValidationError):
            invoice.delete()
        self.assertEqual(LeaseInvoice.objects.count(), 1)


class DailyPostingsPageTests(Step2Base):
    def test_accountant_issues_an_invoice_and_runs_month_end(self):
        lease = self.lease(self.own_unit, vat="5")
        self.client.force_login(make_user("amal", "Accountant"))
        url = reverse("admin:accounting_postings")
        page = self.client.get(url)
        self.assertContains(page, "Tenant One")
        response = self.client.post(url, {"action": "invoice", "lease": lease.pk, "date": "2026-03-01"})
        self.assertEqual(response.status_code, 302)
        invoice = LeaseInvoice.objects.get()
        self.assertEqual(invoice.created_by.username, "amal")
        self.client.post(url, {"action": "month_end", "year": "2026", "month": "3"})
        self.assertEqual(self.bal("4010"), D("1019.18"))
        self.assertEqual(self.client.post(url, {"action": "sync"}).status_code, 302)

    def test_a_manager_can_look_but_not_post_and_others_cannot_open_it(self):
        lease = self.lease(self.own_unit)
        url = reverse("admin:accounting_postings")
        self.client.force_login(make_user("mia", "Manager"))
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"action": "invoice", "lease": lease.pk}).status_code, 403)
        self.assertEqual(self.client.post(url, {"action": "month_end", "year": "2026", "month": "3"}).status_code, 403)
        self.assertEqual(LeaseInvoice.objects.count(), 0)
        self.client.force_login(make_user("viv", "Viewer (read only)"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_page_explains_when_no_cutover_is_set(self):
        AccountingSettings.objects.filter(pk=1).update(cutover_date=None)
        self.client.force_login(make_user("boss", superuser=True))
        self.assertContains(self.client.get(reverse("admin:accounting_postings")), "Nothing is posted yet")
