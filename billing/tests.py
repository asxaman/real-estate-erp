"""Checks for receipts and invoices."""
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from leases.models import LeasePayment
from leases.tests import make_lease, make_tenant
from properties.tests import building, unit

from . import seed
from .models import Invoice, Receipt, Status, issue_invoice, issue_receipt
from .words import amount_in_words

P = LeasePayment


class Words(TestCase):
    def test_amounts_in_words(self):
        self.assertEqual(amount_in_words(1), "One dirham only")
        self.assertEqual(amount_in_words(15000), "Fifteen thousand dirhams only")
        self.assertEqual(amount_in_words(1250.5), "One thousand two hundred fifty dirhams and fifty fils only")
        self.assertEqual(amount_in_words(2_000_345), "Two million three hundred forty-five dirhams only")
        self.assertEqual(amount_in_words(0), "Zero dirhams only")


class Setup(TestCase):
    def setUp(self):
        self.tenant = make_tenant("Billing Tenant")
        self.tower = building("BLG")
        self.flat = unit(self.tower, "101")
        self.lease = make_lease(self.tenant, self.flat, rent="40000")
        self.payment = self.lease.payments.filter(kind="rent").order_by("due_date").first()

    def pay(self, payment=None, **extra):
        payment = payment or self.payment
        payment.record(timezone.localdate(), extra.get("method", "cash"))
        return payment


class Receipts(Setup):
    def test_receipt_is_made_when_a_payment_is_paid(self):
        self.assertFalse(Receipt.objects.exists())
        self.pay()
        receipt = Receipt.objects.get()
        self.assertEqual(receipt.code, "RCP-0001")
        self.assertEqual(receipt.total, self.payment.total)
        self.assertEqual(receipt.tenant_name, "Billing Tenant")
        self.assertIn(self.lease.code, receipt.description)
        self.assertEqual(receipt.status, Status.ISSUED)

    def test_saving_a_paid_payment_again_makes_no_second_receipt(self):
        self.pay()
        self.payment.notes = "changed"
        self.payment.save()
        self.assertEqual(Receipt.objects.count(), 1)

    def test_numbers_continue(self):
        first = self.lease.payments.filter(kind="rent").order_by("due_date")
        self.pay(first[0])
        self.pay(first[1])
        self.assertEqual(list(Receipt.objects.order_by("id").values_list("code", flat=True)), ["RCP-0001", "RCP-0002"])

    def test_vat_on_a_vat_lease(self):
        shop = unit(self.tower, "G01", unit_type="shop")
        lease = make_lease(self.tenant, shop, rent="100000", vat_applies=True)
        payment = lease.payments.filter(kind="rent").first()
        payment.record(timezone.localdate(), "transfer")
        receipt = Receipt.objects.get(payment=payment)
        self.assertEqual(receipt.vat, payment.vat)
        self.assertGreater(receipt.vat, 0)
        self.assertEqual(receipt.total, payment.amount + payment.vat)

    def test_a_bounced_cheque_voids_its_receipt(self):
        self.pay(method="cheque")
        self.payment.bounce()
        receipt = Receipt.objects.get()
        self.assertEqual(receipt.status, Status.VOIDED)
        self.assertIn("bounced", receipt.void_reason)

    def test_paying_again_after_a_bounce_makes_a_new_receipt(self):
        self.pay(method="cheque")
        self.payment.bounce()
        self.payment.record(timezone.localdate(), "cash")
        self.assertEqual(Receipt.objects.count(), 2)
        self.assertEqual(Receipt.objects.filter(status=Status.ISSUED).count(), 1)

    def test_changing_the_amount_replaces_the_receipt(self):
        self.pay()
        self.payment.amount = Decimal("9999.00")
        self.payment.save()
        self.assertEqual(Receipt.objects.count(), 2)
        self.assertEqual(Receipt.objects.get(status=Status.ISSUED).amount, Decimal("9999.00"))

    def test_no_receipt_for_money_going_out_or_a_deposit_kept(self):
        self.assertIsNone(issue_receipt(self.payment))  # not paid yet
        refund = P.objects.create(lease=self.lease, kind="refund", part=1, due_date=timezone.localdate(), amount=Decimal("500"),
                                  status="paid", paid_on=timezone.localdate(), method="transfer")
        self.assertIsNone(issue_receipt(refund))
        kept = P.objects.create(lease=self.lease, kind="fine", part=1, due_date=timezone.localdate(), amount=Decimal("100"),
                                status="paid", paid_on=timezone.localdate(), method="deposit")
        self.assertIsNone(issue_receipt(kept))
        self.assertFalse(Receipt.objects.exists())

    def test_receipt_survives_the_lease_and_keeps_its_words(self):
        self.pay()
        self.lease.delete()
        receipt = Receipt.objects.get()
        self.assertIsNone(receipt.lease)
        self.assertEqual(receipt.tenant_name, "Billing Tenant")

    def test_voiding_needs_a_reason_and_cannot_be_undone(self):
        self.pay()
        receipt = Receipt.objects.get()
        receipt.status = Status.VOIDED
        with self.assertRaises(ValidationError):
            receipt.full_clean()
        receipt.void_reason = "Issued to the wrong tenant"
        receipt.full_clean()
        receipt.save()
        self.assertEqual(receipt.voided_on, timezone.localdate())
        receipt.status = Status.ISSUED
        with self.assertRaises(ValidationError):
            receipt.full_clean()

    def test_issue_missing_receipts_for_payments_saved_in_bulk(self):
        P.objects.filter(pk=self.payment.pk).update(status="paid", paid_on=timezone.localdate(), method="cash")
        self.assertFalse(Receipt.objects.exists())
        self.assertEqual(seed.issue_missing_receipts(), 1)
        self.assertEqual(seed.issue_missing_receipts(), 0)


class Invoices(Setup):
    def test_invoice_for_a_payment(self):
        invoice, created = issue_invoice(self.payment)
        self.assertTrue(created)
        self.assertEqual(invoice.code, "INV-0001")
        self.assertEqual(invoice.due_date, self.payment.due_date)
        self.assertEqual(invoice.total, self.payment.total)
        again, created = issue_invoice(self.payment)
        self.assertFalse(created)
        self.assertEqual(again.pk, invoice.pk)

    def test_no_invoice_for_waived_or_money_out(self):
        self.payment.waive()
        with self.assertRaises(ValidationError):
            issue_invoice(self.payment)
        refund = P.objects.create(lease=self.lease, kind="refund", part=1, due_date=timezone.localdate(), amount=Decimal("500"))
        with self.assertRaises(ValidationError):
            issue_invoice(refund)

    def test_payment_state(self):
        invoice, _ = issue_invoice(self.payment)
        self.assertIn(invoice.payment_state, ("open", "overdue"))
        self.pay()
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_state, "paid")

    def test_a_voided_invoice_can_be_replaced(self):
        invoice, _ = issue_invoice(self.payment)
        invoice.void("Wrong amount")
        new, created = issue_invoice(self.payment)
        self.assertTrue(created)
        self.assertNotEqual(new.pk, invoice.pk)


class Pages(Setup):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("bill", "b@example.com", "pw")
        self.client.force_login(self.admin)
        self.pay()
        self.receipt = Receipt.objects.get()
        self.invoice, _ = issue_invoice(self.lease.payments.filter(kind="rent").order_by("due_date")[1])

    def test_clean_addresses(self):
        self.assertEqual(reverse("admin:billing_receipt_changelist"), "/admin/receipts/")
        self.assertEqual(reverse("admin:billing_invoice_changelist"), "/admin/invoices/")

    def test_lists_chips_and_search(self):
        for name in ("receipt", "invoice"):
            url = reverse(f"admin:billing_{name}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200)
        for chip in ("issued", "month", "voided"):
            self.assertEqual(self.client.get(reverse("admin:billing_receipt_changelist"), {"chip": chip}).status_code, 200, chip)
        for chip in ("open", "overdue", "paid", "voided"):
            self.assertEqual(self.client.get(reverse("admin:billing_invoice_changelist"), {"chip": chip}).status_code, 200, chip)
        self.assertContains(self.client.get(reverse("admin:billing_receipt_changelist"), {"q": "RCP-1"}), "Billing Tenant")
        self.assertContains(self.client.get(reverse("admin:billing_invoice_changelist"), {"q": "INV-1"}), "Billing Tenant")

    def test_forms_and_print_pages(self):
        for url in (
            reverse("admin:billing_receipt_change", args=[self.receipt.pk]),
            reverse("admin:billing_invoice_change", args=[self.invoice.pk]),
            reverse("admin:billing_receipt_print", args=[self.receipt.pk]),
            reverse("admin:billing_invoice_print", args=[self.invoice.pk]),
            reverse("admin:tenants_tenant_change", args=[self.tenant.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        page = self.client.get(reverse("admin:billing_receipt_print", args=[self.receipt.pk]))
        self.assertContains(page, "RCP-0001")
        self.assertContains(page, "dirhams only")
        self.assertContains(page, "Received from")
        self.assertContains(self.client.get(reverse("admin:billing_invoice_print", args=[self.invoice.pk])), "Billed to")
        self.assertContains(self.client.get(reverse("admin:tenants_tenant_change", args=[self.tenant.pk])), "RCP-0001")

    def test_nothing_can_be_added_or_deleted(self):
        self.assertEqual(self.client.get("/admin/receipts/add/").status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:billing_receipt_delete", args=[self.receipt.pk]), {"post": "yes"}).status_code, 403)

    def test_voiding_from_the_form(self):
        url = reverse("admin:billing_receipt_change", args=[self.receipt.pk])
        self.assertEqual(self.client.post(url, {"status": "voided", "void_reason": "", "notes": ""}).status_code, 200)
        self.assertEqual(self.client.post(url, {"status": "voided", "void_reason": "Mistake", "notes": ""}).status_code, 302)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, Status.VOIDED)

    def test_actions_on_the_payments_list(self):
        pending = self.lease.payments.filter(kind="rent", status="pending").order_by("due_date")
        url = reverse("admin:leases_leasepayment_changelist")
        response = self.client.post(url, {"action": "create_invoices_action", "_selected_action": [p.pk for p in pending[:2]]}, follow=True)
        self.assertContains(response, "invoice(s) created")
        self.assertEqual(Invoice.objects.count(), 2)
        third = pending[2].pk
        P.objects.filter(pk=third).update(status="paid", paid_on=timezone.localdate(), method="cash")
        response = self.client.post(url, {"action": "issue_receipts_action", "_selected_action": [third]}, follow=True)
        self.assertContains(response, "1 receipt(s) issued")

    def test_exports(self):
        response = self.client.post(reverse("admin:billing_receipt_changelist"), {"action": "export_csv", "_selected_action": [self.receipt.pk]})
        self.assertIn(b"RCP-0001", response.content)
        response = self.client.post(reverse("admin:billing_invoice_changelist"), {"action": "export_csv", "_selected_action": [self.invoice.pk]})
        self.assertIn(b"INV-0001", response.content)

    def test_people_without_permission_see_nothing(self):
        nobody = User.objects.create_user("nobody", password="pw", is_staff=True)
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(reverse("admin:billing_receipt_print", args=[self.receipt.pk])).status_code, 404)


class SampleData(TestCase):
    def test_seed_and_erase(self):
        from leases.seed import seed_leases
        from properties.seed import seed_properties
        from tenants.seed import seed_tenants

        seed_properties()
        seed_tenants()
        seed_leases()
        self.assertIn("receipts", seed.seed_billing())
        self.assertTrue(Receipt.objects.exists())
        count = Receipt.objects.count()
        seed.seed_billing()
        self.assertEqual(Receipt.objects.count(), count)
        seed.erase_billing()
        self.assertFalse(Receipt.objects.exists() or Invoice.objects.exists())

    def test_roles_get_permissions(self):
        Group.objects.create(name="Accountant")
        seed.grant_role_permissions()
        self.assertTrue(Group.objects.get(name="Accountant").permissions.filter(codename="add_invoice").exists())
        Group.objects.create(name="Leasing Agent")
        seed.grant_role_permissions()
        self.assertFalse(Group.objects.get(name="Leasing Agent").permissions.filter(codename="change_receipt").exists())
