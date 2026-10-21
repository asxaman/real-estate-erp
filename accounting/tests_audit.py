"""Checks for the full accounting cycle (rent, deposit, PDCs, bounce, early exit,
maintenance, deposit refund) and for the locks and controls."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError

from realestate.models import Cheque, Expense, Lease, Property, Vendor

from . import reports, rules, rules_more
from .models import AccountingSettings, JournalEntry, LeaseInvoice, ReceiptVoucher
from .tests_step2 import Step2Base

D = Decimal


class FullCycleTests(Step2Base):
    """The ten phases, on a third-party unit: AED 18,000 a year in 6 payments."""

    def test_the_whole_cycle(self):
        Property.objects.filter(pk=self.tp_property.pk).update(maintenance_markup_percent=D("10"))
        self.tp_property.refresh_from_db()
        lease = Lease.objects.create(
            tenant=self.tenant, unit=self.tp_unit, start_date=date(2026, 3, 1), end_date=date(2027, 2, 28),
            annual_rent=D("18000"), vat_percent=D("0"), number_of_payments=6,
        )
        # Phase 1: master invoice
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        self.assertEqual((self.bal("1100"), self.bal("2100")), (D("18000.00"), D("18000.00")))

        # Phase 2: first instalment by card, deposit by bank transfer
        Cheque.objects.create(
            lease=lease, method="card", purpose="rent", amount=D("3000"),
            cheque_date=date(2026, 3, 1), status="cleared", cleared_date=date(2026, 3, 1),
        )
        Cheque.objects.create(
            lease=lease, method="transfer", purpose="security_deposit", amount=D("1000"),
            cheque_date=date(2026, 3, 1), status="cleared", cleared_date=date(2026, 3, 1),
        )
        self.assertEqual((self.bal("1040"), self.bal("1100")), (D("3000.00"), D("15000.00")))
        self.assertEqual((self.bal("1020"), self.bal("2200")), (D("1000.00"), D("1000.00")))

        # Phase 3: five post-dated cheques: the tenant account goes to zero
        cheques = [
            self.cheque(lease, f"7000{n}", "3000", status="received", received_date=date(2026, 3, 2),
                        cheque_date=date(2026, 3 + n, 1))
            for n in range(1, 6)
        ]
        self.assertEqual((self.bal("1100"), self.bal("1130")), (D("0.00"), D("15000.00")))

        # Phase 4: a cheque clears
        self.set_status(cheques[0], "deposited", deposited_date=date(2026, 4, 1))
        self.set_status(cheques[0], "cleared", cleared_date=date(2026, 4, 3))
        self.assertEqual((self.bal("1130"), self.bal("1020")), (D("12000.00"), D("4000.00")))

        # Phase 5: a cheque bounces
        self.set_status(cheques[1], "deposited", deposited_date=date(2026, 5, 1))
        self.set_status(cheques[1], "returned", returned_date=date(2026, 5, 4), return_reason="insufficient_funds")
        self.assertEqual((self.bal("1130"), self.bal("1140"), self.bal("1100")), (D("9000.00"), D("3000.00"), D("0.00")))

        # Phase 6: replaced by cash plus a bounce penalty
        fee = Cheque.objects.create(
            lease=lease, method="cash", purpose="bounce_fee", amount=D("500"),
            cheque_date=date(2026, 5, 10), status="pending",
        )
        self.assertEqual(self.bal("1100"), D("500.00"))  # the penalty is invoiced to the tenant
        Cheque.objects.create(
            lease=lease, method="cash", purpose="rent", amount=D("3000"), replaces=cheques[1],
            cheque_date=date(2026, 5, 10), status="cleared", cleared_date=date(2026, 5, 10),
        )
        self.set_status(fee, "cleared", cleared_date=date(2026, 5, 10))
        self.assertEqual((self.bal("1140"), self.bal("1100"), self.bal("1030")), (D("0.00"), D("0.00"), D("3500.00")))

        # Phase 7: early exit: two cheques handed back, unused rent credited, penalty invoiced and paid
        Lease.objects.filter(pk=lease.pk).update(status="terminated", exit_date=date(2026, 9, 1))
        lease.refresh_from_db()
        self.set_status(cheques[3], "cancelled")
        self.set_status(cheques[4], "cancelled")
        self.assertEqual(self.bal("1130"), D("3000.00"))
        self.assertEqual(rules_more.sync_termination(lease), 0)  # the cheque saves already did it
        self.assertEqual((self.bal("1100"), self.bal("2100")), (D("0.00"), D("12000.00")))
        penalty = Cheque.objects.create(
            lease=lease, method="cash", purpose="termination_penalty", amount=D("2000"),
            cheque_date=date(2026, 9, 1), status="pending",
        )
        self.assertEqual(self.bal("1100"), D("2000.00"))
        self.set_status(penalty, "cleared", cleared_date=date(2026, 9, 1))
        self.assertEqual((self.bal("1100"), self.bal("1030")), (D("0.00"), D("5500.00")))
        self.assertEqual(self.bal("2100"), D("14000.00"))  # the owner gets the penalty (third-party unit)

        # Phase 8: repair of AED 1,000 with our 10% markup
        vendor = Vendor.objects.create(name="Cool Air AC")
        Expense.objects.create(
            property=self.tp_property, unit=self.tp_unit, description="AC repair", amount=D("1000"),
            expense_date=date(2026, 6, 1), vendor=vendor, bearer="owner",
        )
        self.assertEqual((self.bal("2100"), self.bal("2010"), self.bal("4030")), (D("12900.00"), D("1000.00"), D("100.00")))

        # Phase 10: deposit refund with AED 200 damages
        Lease.objects.filter(pk=lease.pk).update(deposit_deductions=D("200"), deposit_refunded_on=date(2026, 9, 5))
        lease.refresh_from_db()
        self.assertEqual(rules_more.sync_deposit_refund(lease), 1)
        self.assertEqual((self.bal("2200"), self.bal("1020"), self.bal("2100")), (D("0.00"), D("3200.00"), D("13100.00")))
        self.assertEqual(reports.ledger_health(), [])
        self.assertEqual(rules.waiting_items()["total"], 0)


class PdcFlowTests(Step2Base):
    def setUp(self):
        super().setUp()
        self.lease_own = self.lease(self.own_unit, vat="5")
        rules.create_lease_invoice(self.lease_own, date(2026, 3, 1))

    def test_bounced_then_cleared_again_ends_in_the_bank(self):
        cheque = self.cheque(self.lease_own, "30011", "3150", status="received", received_date=date(2026, 3, 2))
        self.set_status(cheque, "returned", returned_date=date(2026, 3, 9), return_reason="insufficient_funds")
        self.assertEqual(self.bal("1140"), D("3150.00"))
        self.set_status(cheque, "cleared", cleared_date=date(2026, 3, 20))
        self.assertEqual((self.bal("1140"), self.bal("1130"), self.bal("1010")), (D("0.00"), D("0.00"), D("3150.00")))
        self.assertEqual(reports.ledger_health(), [])

    def test_the_chart_has_the_pdc_accounts_as_tenant_control_accounts(self):
        from .models import Account

        self.assertEqual(Account.objects.get(code="1130").control_kind, "tenant")
        self.assertEqual(Account.objects.get(code="1140").control_kind, "tenant")

    def test_depositing_a_cheque_posts_nothing(self):
        cheque = self.cheque(self.lease_own, "30012", "3150", status="received", received_date=date(2026, 3, 2))
        before = JournalEntry.objects.count()
        self.set_status(cheque, "deposited", deposited_date=date(2026, 3, 4))
        self.assertEqual(JournalEntry.objects.count(), before)
        self.assertEqual(ReceiptVoucher.objects.count(), 1)


class LockTests(Step2Base):
    def test_an_invoiced_lease_cannot_change_its_rent(self):
        lease = self.lease(self.own_unit)
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        lease.refresh_from_db()
        lease.annual_rent = D("20000")
        with self.assertRaises(ValidationError):
            lease.save()

    def test_a_posted_cheque_cannot_change_its_amount(self):
        lease = self.lease(self.own_unit)
        rules.create_lease_invoice(lease, date(2026, 3, 1))
        cheque = self.cheque(lease, "50001", "3000", status="received", received_date=date(2026, 3, 2))
        cheque.refresh_from_db()
        cheque.amount = D("3100")
        with self.assertRaises(ValidationError):
            cheque.save()

    def test_vat_must_match_the_usage(self):
        lease = self.lease(self.own_unit, vat="5")  # residential by default
        lease.usage = Lease.Usage.RESIDENTIAL
        with self.assertRaises(ValidationError):
            lease.clean()


class InvoiceVoidTests(Step2Base):
    def test_a_fresh_invoice_can_be_cancelled_and_reissued(self):
        lease = self.lease(self.own_unit)
        invoice, _ = rules.create_lease_invoice(lease, date(2026, 3, 1))
        rules.void_lease_invoice(invoice)
        self.assertEqual(self.bal("1100"), D("0.00"))
        invoice, _ = rules.create_lease_invoice(lease, date(2026, 3, 1))
        self.assertFalse(invoice.is_void)
        self.assertEqual(LeaseInvoice.objects.filter(lease=lease, is_void=False).count(), 1)

    def test_an_invoice_with_a_posted_cheque_cannot_be_cancelled(self):
        lease = self.lease(self.own_unit)
        invoice, _ = rules.create_lease_invoice(lease, date(2026, 3, 1))
        self.cheque(lease, "50002", "3000", status="received", received_date=date(2026, 3, 2))
        with self.assertRaises(ValidationError):
            rules.void_lease_invoice(invoice)


class TwoPersonTests(Step2Base):
    def test_drafts_are_not_in_the_ledger(self):
        AccountingSettings.objects.filter(pk=1).update(separate_approver=True)
        self.assertEqual(JournalEntry.objects.filter(status="posted").count(), 0)
