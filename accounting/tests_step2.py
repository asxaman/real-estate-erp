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
from .posting_roles import ROLE_DEFAULTS
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
        self.assertEqual(self.bal("1100"), D("12600.00"))  # the tenant owes the whole contract
        self.assertEqual(self.bal("4010"), D("12000.00"))  # company rent is income straight away
        self.assertEqual(self.bal("2400"), D("600.00"))  # VAT output payable

    def test_vat_can_be_inside_the_rent(self):
        lease = self.lease(self.own_unit, vat="5")
        lease.vat_inclusive = True
        lease.save()
        invoice, _ = rules.create_lease_invoice(lease, date(2026, 3, 1))
        self.assertEqual(invoice.total_amount, D("12000.00"))
        self.assertEqual((invoice.rent_amount, invoice.vat_amount), (D("11428.57"), D("571.43")))
        self.assertEqual(self.bal("1100"), D("12000.00"))
        self.assertEqual(self.bal("2400"), D("571.43"))

    def test_third_party_invoice_is_owed_to_the_owner_not_income(self):
        lease = self.lease(self.tp_unit)
        invoice, _ = rules.create_lease_invoice(lease, date(2026, 3, 1))
        self.assertFalse(invoice.is_tax_invoice)
        self.assertEqual(self.bal("1100"), D("12000.00"))
        self.assertEqual(self.bal("2100"), D("12000.00"))  # owner payable
        self.assertEqual(self.bal("4010"), D("0.00"))

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
        self.assertEqual(JournalEntry.objects.filter(source_tag="pdc_held").count(), 0)  # still expected

        self.set_status(cheque, "received", received_date=date(2026, 3, 2))
        self.assertEqual(self.bal("1130"), D("3150.00"))  # PDC receivable
        self.assertEqual(self.bal("1100"), D("9450.00"))  # the tenant owes less once the cheque is recorded
        voucher = ReceiptVoucher.objects.get()
        self.assertEqual(voucher.number, "RV-2026-000001")
        self.assertEqual(voucher.amount, D("3150.00"))

        before = JournalEntry.objects.count()
        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 5))
        self.assertEqual(JournalEntry.objects.count(), before)  # depositing posts nothing
        self.assertEqual(self.bal("1130"), D("3150.00"))

        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.assertEqual((self.bal("1130"), self.bal("1010")), (D("0.00"), D("3150.00")))
        self.assertEqual(self.bal("2400"), D("600.00"))  # the VAT was booked with the invoice
        self.assertEqual(reports.ledger_health(), [])

    def test_a_bounced_cheque_moves_to_the_bounced_account_not_the_tenant(self):
        cheque = self.cheque(self.lease_own, "10002", "3150", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 5))
        self.set_status(cheque, "returned", returned_date=date(2026, 3, 9), return_reason="insufficient_funds")
        self.assertEqual(self.bal("1100"), D("9450.00"))  # tenant receivable stays clean
        self.assertEqual((self.bal("1130"), self.bal("1140"), self.bal("1010")), (D("0.00"), D("3150.00"), D("0.00")))
        self.assertEqual(reports.ledger_health(), [])

    def test_a_cleared_cheque_taken_back_by_the_bank_goes_to_bounced(self):
        cheque = self.cheque(self.lease_own, "10007", "3150", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.assertEqual(self.bal("1010"), D("3150.00"))
        self.set_status(cheque, "returned", returned_date=date(2026, 3, 20), return_reason="insufficient_funds")
        self.assertEqual((self.bal("1010"), self.bal("1130"), self.bal("1140")), (D("0.00"), D("0.00"), D("3150.00")))
        self.assertEqual(self.bal("1100"), D("9450.00"))
        self.assertEqual(reports.ledger_health(), [])

    def test_a_replacement_settles_the_bounced_amount(self):
        cheque = self.cheque(self.lease_own, "10008", "3150", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "returned", returned_date=date(2026, 3, 9), return_reason="insufficient_funds")
        new = Cheque.objects.create(
            lease=self.lease_own, method="cash", purpose="rent", amount=D("3150"),
            cheque_date=date(2026, 3, 12), status="cleared", cleared_date=date(2026, 3, 12),
            replaces=cheque,
        )
        self.assertEqual(self.bal("1140"), D("0.00"))
        self.assertEqual(self.bal("1030"), D("3150.00"))  # cash account
        self.assertEqual(self.bal("1100"), D("9450.00"))
        self.assertTrue(new.pk)

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
        self.assertEqual((self.bal("1010"), self.bal("1130")), (D("0.00"), D("3150.00")))
        self.assertTrue(JournalEntry.objects.filter(source="reversal").exists())
        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.assertEqual((self.bal("1010"), self.bal("1130")), (D("3150.00"), D("0.00")))
        self.assertEqual(reports.ledger_health(), [])

    def test_cash_payment_goes_to_the_cash_account_and_gets_a_receipt(self):
        cash = Cheque.objects.create(
            lease=self.lease_own, method="cash", purpose="rent", amount=D("3150"),
            cheque_date=date(2026, 3, 1), status="cleared", cleared_date=date(2026, 3, 3),
        )
        self.assertEqual(self.bal("1030"), D("3150.00"))  # cash account
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

    def test_a_deposit_is_a_liability_not_income(self):
        Cheque.objects.create(
            lease=self.lease_own, method="cash", purpose="security_deposit", amount=D("1000"),
            cheque_date=date(2026, 3, 1), status="cleared", cleared_date=date(2026, 3, 3),
        )
        self.assertEqual(self.bal("2200"), D("1000.00"))
        self.assertEqual(self.bal("1030"), D("1000.00"))
        self.assertEqual(self.bal("1100"), D("12600.00"))  # the deposit does not reduce the rent owed
        self.assertEqual(ReceiptVoucher.objects.count(), 1)

    def test_card_goes_to_merchant_clearing_until_the_bank_settles(self):
        card = Cheque.objects.create(
            lease=self.lease_own, method="card", purpose="rent", amount=D("3150"),
            cheque_date=date(2026, 3, 1), status="cleared", cleared_date=date(2026, 3, 1),
        )
        self.assertEqual((self.bal("1040"), self.bal("1010")), (D("3150.00"), D("0.00")))
        card.settled_on = date(2026, 3, 4)
        card.save()
        self.assertEqual((self.bal("1040"), self.bal("1010")), (D("0.00"), D("3150.00")))
        self.assertEqual(reports.ledger_health(), [])

    def test_a_bank_transfer_for_a_company_unit_goes_to_the_operating_bank(self):
        Cheque.objects.create(
            lease=self.lease_own, method="transfer", purpose="rent", amount=D("3150"),
            cheque_date=date(2026, 3, 1), status="cleared", cleared_date=date(2026, 3, 1),
        )
        self.assertEqual((self.bal("1010"), self.bal("1020")), (D("3150.00"), D("0.00")))

    def test_a_fee_is_invoiced_then_paid(self):
        fee = Cheque.objects.create(
            lease=self.lease_own, method="cash", purpose="bounce_fee", amount=D("525"),
            cheque_date=date(2026, 3, 1), status="pending",
        )
        self.assertEqual(self.bal("1100"), D("13125.00"))  # 12,600 rent + 525 fee invoiced
        self.assertGreater(self.bal("4070"), D("0.00"))
        fee.status = "cleared"
        fee.cleared_date = date(2026, 3, 2)
        fee.save()
        self.assertEqual(self.bal("1100"), D("12600.00"))
        self.assertEqual(self.bal("1030"), D("525.00"))
        self.assertEqual(reports.ledger_health(), [])


class ThirdPartyTests(Step2Base):
    def test_the_owner_is_credited_on_the_invoice_and_the_bank_gets_the_cash(self):
        lease = self.lease(self.tp_unit)
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        cheque = self.cheque(lease, "20001", "3000", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 5))
        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.assertEqual(self.bal("1020"), D("3000.00"))  # owners' money sits in the trust bank
        self.assertEqual(self.bal("1010"), D("0.00"))
        self.assertEqual(self.bal("2100"), D("12000.00"))  # the owner is owed the full contract
        self.assertEqual(self.bal("4020"), D("0.00"))  # our fee is taken when the statement is paid
        self.assertEqual(self.bal("4010"), D("0.00"))  # third-party rent is never our income
        balances = {r["party_id"]: r["balance"] for r in reports.party_balances(Account.objects.get(code="2100"))}
        self.assertEqual(balances, {self.owner.pk: D("12000.00")})
        self.assertTrue(all(row["ok"] for row in reports.reconciliation()))
        self.assertEqual(reports.ledger_health(), [])

    def test_a_bounce_after_clearing_moves_it_to_bounced_receivable(self):
        lease = self.lease(self.tp_unit)
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        cheque = self.cheque(lease, "20002", "3000", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 5))
        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 10))
        self.set_status(cheque, "returned", returned_date=date(2026, 3, 20), return_reason="stop_payment")
        self.assertEqual((self.bal("1020"), self.bal("1130")), (D("0.00"), D("0.00")))
        self.assertEqual(self.bal("1140"), D("3000.00"))
        self.assertEqual(self.bal("1100"), D("9000.00"))
        self.assertEqual(reports.ledger_health(), [])


class MappingAndDocumentTests(Step2Base):
    def test_each_job_points_to_an_account(self):
        self.assertEqual(AccountMapping.objects.count(), len(ROLE_DEFAULTS))
        self.assertEqual(unmapped_roles(), [])
        trust_job = AccountMapping.objects.get(key="bank_trust")
        trust_job.account = Account.objects.get(code="1010")  # the company's own bank: wrong
        with self.assertRaises(ValidationError):
            trust_job.full_clean()

    def test_a_missing_account_is_reported_and_never_breaks_saving_a_cheque(self):
        lease = self.lease(self.own_unit)
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        AccountMapping.objects.filter(key="pdc_ar").delete()
        Account.objects.filter(code="1130").update(code="1139")
        self.assertEqual(unmapped_roles(), ["PDC receivable (cheques held for the tenant)"])
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
    def test_accountant_issues_an_invoice_from_the_page(self):
        lease = self.lease(self.own_unit, vat="5")
        self.client.force_login(make_user("amal", "Accountant"))
        url = reverse("admin:accounting_postings")
        page = self.client.get(url)
        self.assertContains(page, "Tenant One")
        response = self.client.post(url, {"action": "invoice", "lease": lease.pk, "date": "2026-03-01"})
        self.assertEqual(response.status_code, 302)
        invoice = LeaseInvoice.objects.get()
        self.assertEqual(invoice.created_by.username, "amal")
        self.assertEqual(self.bal("4010"), D("12000.00"))
        self.assertEqual(self.client.post(url, {"action": "sync"}).status_code, 302)

    def test_a_manager_can_look_but_not_post_and_others_cannot_open_it(self):
        lease = self.lease(self.own_unit)
        url = reverse("admin:accounting_postings")
        self.client.force_login(make_user("mia", "Manager"))
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"action": "invoice", "lease": lease.pk}).status_code, 403)
        self.assertEqual(LeaseInvoice.objects.count(), 0)
        self.client.force_login(make_user("viv", "Viewer (read only)"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_page_explains_when_no_cutover_is_set(self):
        AccountingSettings.objects.filter(pk=1).update(cutover_date=None)
        self.client.force_login(make_user("boss", superuser=True))
        self.assertContains(self.client.get(reverse("admin:accounting_postings")), "Nothing is posted yet")
