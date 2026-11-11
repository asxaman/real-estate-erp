"""Update 26 B: deposit and fees as payment lines, held cheques, the per-lease payments page. Run with:  python manage.py test"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from .models import Lease, LeasePayment
from .tests import Setup, make_lease


def full_lease(test, **kwargs):
    values = dict(
        security_deposit=Decimal("5000"), admin_fee=Decimal("700"), commission=Decimal("100"), ejari_fee=Decimal("250"),
    )
    values.update(kwargs)
    return make_lease(test.tenant, test.flat, rent="18000", **values)


class ChargeLines(Setup):
    def test_the_lease_creates_rent_deposit_and_fee_lines(self):
        lease = full_lease(self)
        self.assertEqual(lease.payments.filter(kind="rent").count(), 4)
        kinds = {p.kind: p.amount for p in lease.payments.exclude(kind="rent")}
        self.assertEqual(kinds, {"deposit": Decimal("5000.00"), "office": Decimal("700.00"),
                                 "agency": Decimal("100.00"), "ejari": Decimal("250.00")})
        self.assertTrue(all(p.status == "pending" for p in lease.payments.all()))

    def test_no_fees_means_no_extra_lines(self):
        lease = make_lease(self.tenant, self.flat)
        self.assertEqual(lease.payments.exclude(kind="rent").count(), 0)

    def test_vat_is_added_to_rent_only(self):
        lease = make_lease(self.tenant, self.flat, rent="18000", vat_applies=True, security_deposit=Decimal("5000"))
        deposit = lease.payments.get(kind="deposit")
        rent = lease.payments.filter(kind="rent").first()
        self.assertEqual(deposit.vat, Decimal("0.00"))
        self.assertEqual(deposit.total, Decimal("5000.00"))
        self.assertEqual(rent.vat, Decimal("225.00"))

    def test_rent_totals_ignore_the_deposit_and_fees(self):
        lease = full_lease(self)
        self.assertEqual(lease.scheduled_total(), Decimal("18000.00"))
        lease.payments.get(kind="deposit").record(lease.start_date, "cash")
        self.assertEqual(lease.paid_total(), Decimal("0.00"))
        self.assertEqual(len(lease.charge_lines()), 4)

    def test_a_pending_line_follows_the_lease_and_a_zero_removes_it(self):
        lease = full_lease(self)
        lease.security_deposit = Decimal("6000")
        lease.admin_fee = Decimal("0")
        lease.save()
        self.assertEqual(lease.payments.get(kind="deposit").amount, Decimal("6000.00"))
        self.assertFalse(lease.payments.filter(kind="office").exists())
        lease.commission = Decimal("100")  # unchanged: still one line
        lease.save()
        self.assertEqual(lease.payments.filter(kind="agency").count(), 1)

    def test_a_received_deposit_cannot_be_changed_on_the_lease(self):
        lease = full_lease(self)
        lease.payments.get(kind="deposit").record(lease.start_date, "cash")
        lease = Lease.objects.get(pk=lease.pk)
        lease.security_deposit = Decimal("9000")
        with self.assertRaises(ValidationError) as caught:
            lease.clean()
        self.assertIn("security_deposit", caught.exception.message_dict)

    def test_terminating_keeps_the_deposit_line(self):
        lease = full_lease(self)
        lease.terminate(lease.start_date + timedelta(days=30), "Left", cancel_unpaid=True)
        self.assertTrue(lease.payments.filter(kind="deposit").exists())
        self.assertEqual(lease.payments.filter(kind="rent").count(), 1)  # only rent after the date is cancelled

    def test_cheque_numbers_go_to_rent_only(self):
        lease = full_lease(self)
        lease.assign_cheques("000100", "ENBD")
        self.assertEqual(lease.payments.filter(kind="rent").exclude(cheque_number="").count(), 4)
        self.assertEqual(lease.payments.exclude(kind="rent").exclude(cheque_number="").count(), 0)

    def test_the_renewal_asks_for_the_deposit_and_fees_again(self):
        lease = full_lease(self)
        new = lease.renew()
        self.assertEqual(
            set(new.payments.exclude(kind="rent").values_list("kind", flat=True)), {"deposit", "office", "ejari"},
        )

    def test_a_missing_line_comes_back_after_a_migrate_for_old_leases(self):
        from .apps import add_charge_lines_after_migrate

        lease = full_lease(self)
        LeasePayment.objects.exclude(kind="rent").delete()
        add_charge_lines_after_migrate(None)
        self.assertEqual(lease.payments.exclude(kind="rent").count(), 4)
        add_charge_lines_after_migrate(None)  # running it again adds nothing
        self.assertEqual(lease.payments.exclude(kind="rent").count(), 4)


class HeldCheques(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("chargeboss", "cb@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = full_lease(self)
        self.deposit = self.lease.payments.get(kind="deposit")

    def url(self, name, *args):
        return reverse(f"admin:leases_{name}", args=args)

    def test_a_blank_cheque_is_held_not_counted_as_money(self):
        reply = self.client.post(self.url("leasepayment_record", self.deposit.pk), {
            "action": "held", "date": self.lease.start_date.isoformat(), "cheque_number": "555", "bank_name": "ENBD",
        }).json()
        self.assertTrue(reply["ok"])
        self.deposit.refresh_from_db()
        self.assertEqual((self.deposit.status, self.deposit.method, self.deposit.cheque_number), ("held", "cheque", "555"))
        self.assertFalse(self.deposit.is_overdue(self.lease.start_date + timedelta(days=90)))

    def test_holding_twice_is_refused_and_cashing_it_makes_it_paid(self):
        data = {"action": "held", "date": self.lease.start_date.isoformat(), "cheque_number": "7"}
        self.assertTrue(self.client.post(self.url("leasepayment_record", self.deposit.pk), data).json()["ok"])
        self.assertEqual(self.client.post(self.url("leasepayment_record", self.deposit.pk), data).status_code, 400)
        cashed = self.client.post(self.url("leasepayment_record", self.deposit.pk), {
            "action": "paid", "date": "2026-02-01", "method": "cheque", "cheque_number": "7", "bank_name": "ENBD",
        }).json()
        self.assertTrue(cashed["ok"])
        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.status, "paid")

    def test_cash_deposit_is_received_and_the_lease_page_says_so(self):
        reply = self.client.post(self.url("leasepayment_record", self.deposit.pk), {
            "action": "paid", "date": self.lease.start_date.isoformat(), "method": "cash",
        }).json()
        self.assertTrue(reply["ok"])
        page = self.client.get(self.url("lease_change", self.lease.pk))
        self.assertContains(page, "Received")

    def test_editing_a_deposit_line_updates_the_lease_amount(self):
        values = self.client.get(self.url("leasepayment_edit", self.deposit.pk)).json()["values"]
        values["amount"] = "5500.00"
        self.assertTrue(self.client.post(self.url("leasepayment_edit", self.deposit.pk), values).json()["ok"])
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.security_deposit, Decimal("5500.00"))

    def test_other_lines_can_be_added(self):
        reply = self.client.post(self.url("leasepayment_add_to", self.lease.pk), {
            "kind": "other", "due_date": self.lease.start_date.isoformat(), "amount": "300", "method": "cash",
        }).json()
        self.assertTrue(reply["ok"])
        self.assertEqual(self.lease.payments.filter(kind="other").count(), 1)
        bad = self.client.post(self.url("leasepayment_add_to", self.lease.pk), {"kind": "nonsense"})
        self.assertEqual(bad.status_code, 400)


class LeasePaymentsPage(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("pageboss", "pg@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = full_lease(self)

    def test_the_page_lists_every_line_with_buttons(self):
        page = self.client.get(reverse("admin:leases_lease_payments", args=[self.lease.pk]))
        self.assertEqual(page.status_code, 200)
        for text in ("Security deposit", "Office fee", "Agency fee", "Ejari fee", "Rent", 'data-erp-act="record"'):
            self.assertContains(page, text)
        self.assertEqual(len(page.context["rows"]), 8)

    def test_the_list_links_each_lease_to_its_page(self):
        page = self.client.get(reverse("admin:leases_leasepayment_changelist"))
        self.assertContains(page, reverse("admin:leases_lease_payments", args=[self.lease.pk]))

    def test_the_page_needs_permission(self):
        viewer = get_user_model().objects.create_user("nopay", password="Pass-word1!", is_staff=True)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("admin:leases_lease_payments", args=[self.lease.pk])).status_code, 403)

    def test_the_type_filter_and_exports_still_work(self):
        url = reverse("admin:leases_leasepayment_changelist")
        rows = self.client.get(url, {"kind__exact": "deposit"}).context["cl"].result_list
        self.assertEqual([p.kind for p in rows], ["deposit"])
        csv_reply = self.client.post(url, {"action": "export_csv", "_selected_action": [p.pk for p in self.lease.payments.all()]})
        self.assertIn("Type", csv_reply.content.decode("utf-8-sig").splitlines()[0])


class DataCheck(Setup):
    def test_the_check_command_reports_clean_data(self):
        full_lease(self)
        out = StringIO()
        call_command("check_lease_data", stdout=out)
        self.assertIn("All good", out.getvalue())


class StyledDropdownsEverywhere(Setup):
    """The browser's own drop-down must never come back: nothing may opt out of the styled one except the search boxes."""

    def test_nothing_opts_out_of_the_styled_dropdown(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        found = []
        for path in list(root.glob("*/static/**/*.js")) + list(root.glob("*/templates/**/*.html")) + list(root.glob("*/*.py")):
            if path.name.startswith("test") or path.name in ("tests.py", "erp.js"):  # erp.js reads the attribute itself
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "data-erp-native" in line and "data-erp-search" not in line and "erpSelect" not in line:
                    found.append(f"{path.relative_to(root)}:{number}")
        self.assertEqual(found, [])

    def test_the_open_list_sits_above_pop_up_windows(self):
        from pathlib import Path

        css = (Path(__file__).resolve().parent.parent / "core/static/core/erp.css").read_text(encoding="utf-8")
        import re

        dialog = int(re.search(r"\.erp-dlg-overlay\s*\{[^}]*z-index:\s*(\d+)", css).group(1))
        panel = int(re.search(r"\.erp-select-panel\s*\{[^}]*z-index:\s*(\d+)", css).group(1))
        self.assertGreater(panel, dialog)


class DeductionsFinesRefunds(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("dedboss", "dd@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = full_lease(self)

    def url(self, name, *args):
        return reverse(f"admin:leases_{name}", args=args)

    def test_a_deduction_from_the_deposit_goes_to_the_payments_module(self):
        from .models import LeaseDeduction

        deduction = LeaseDeduction.objects.create(lease=self.lease, reason="Repainting", note="Two rooms", amount=Decimal("1500"))
        line = deduction.payment
        self.assertEqual((line.kind, line.status, line.method, line.amount), ("deduction", "paid", "deposit", Decimal("1500.00")))
        self.assertIn("Repainting", line.notes)
        self.assertEqual(self.lease.deposit_balance(), Decimal("3500.00"))

    def test_a_fine_is_a_pending_line_and_not_taken_from_the_deposit(self):
        from .models import LeaseDeduction

        fine = LeaseDeduction.objects.create(lease=self.lease, kind="fine", reason="Late renewal", amount=Decimal("200"))
        self.assertEqual((fine.payment.kind, fine.payment.status), ("fine", "pending"))
        self.assertEqual(self.lease.deposit_balance(), Decimal("5000.00"))

    def test_changing_or_deleting_a_deduction_follows_in_the_line(self):
        from .models import LeaseDeduction

        deduction = LeaseDeduction.objects.create(lease=self.lease, kind="fine", reason="Noise", amount=Decimal("100"))
        deduction.amount = Decimal("150")
        deduction.save()
        self.assertEqual(LeasePayment.objects.get(pk=deduction.payment_id).amount, Decimal("150.00"))
        line_id = deduction.payment_id
        deduction.delete()
        self.assertFalse(LeasePayment.objects.filter(pk=line_id).exists())

    def test_removing_a_pending_fine_line_removes_the_deduction(self):
        from .models import LeaseDeduction

        fine = LeaseDeduction.objects.create(lease=self.lease, kind="fine", reason="Noise", amount=Decimal("100"))
        self.assertTrue(self.client.post(self.url("leasepayment_remove", fine.payment_id)).json()["ok"])
        self.assertFalse(LeaseDeduction.objects.filter(pk=fine.pk).exists())

    def test_a_deduction_cannot_be_more_than_the_deposit(self):
        from .models import LeaseDeduction

        with self.assertRaises(ValidationError):
            LeaseDeduction(lease=self.lease, reason="Too much", amount=Decimal("5001")).clean()
        LeaseDeduction(lease=self.lease, kind="fine", reason="A big fine", amount=Decimal("9000")).clean()  # fines are not limited

    def test_money_kept_from_the_deposit_is_not_cash_collected(self):
        from .models import LeaseDeduction

        LeaseDeduction.objects.create(lease=self.lease, reason="Cleaning", amount=Decimal("300"))
        from .admin import LeasePaymentAdmin
        from django.contrib import admin as django_admin

        strip = LeasePaymentAdmin(LeasePayment, django_admin.site)._strip()
        collected = next(c for c in strip if c["label"] == "Collected this month")
        self.assertEqual(collected["count"], 0)

    def test_a_refund_line_reduces_the_deposit_and_is_never_overdue(self):
        reply = self.client.post(self.url("leasepayment_add_to", self.lease.pk), {
            "kind": "refund", "due_date": self.lease.start_date.isoformat(), "amount": "2000", "method": "transfer",
        }).json()
        self.assertTrue(reply["ok"])
        refund = self.lease.payments.get(kind="refund")
        self.assertEqual(refund.status, "paid")
        self.assertFalse(refund.is_overdue(self.lease.start_date + timedelta(days=200)))
        lease = Lease.objects.get(pk=self.lease.pk)
        self.assertEqual(lease.deposit_balance(), Decimal("3000.00"))

    def test_the_deduction_type_cannot_be_added_as_a_plain_payment(self):
        reply = self.client.post(self.url("leasepayment_add_to", self.lease.pk), {
            "kind": "deduction", "due_date": self.lease.start_date.isoformat(), "amount": "50", "method": "cash",
        })
        self.assertEqual(reply.status_code, 400)

    def test_a_fine_added_as_a_payment_is_a_normal_pending_line(self):
        reply = self.client.post(self.url("leasepayment_add_to", self.lease.pk), {
            "kind": "fine", "due_date": self.lease.start_date.isoformat(), "amount": "75", "method": "cash",
        }).json()
        self.assertTrue(reply["ok"])
        self.assertEqual(self.lease.payments.get(kind="fine").status, "pending")

    def test_the_lease_form_no_longer_asks_for_the_removed_fields(self):
        page = self.client.get(self.url("lease_change", self.lease.pk))
        for name in ("deposit_status", "deposit_refund_date", "deposit_refund_amount", "late_fee_per_day"):
            self.assertNotContains(page, f'name="{name}"')
        self.assertContains(page, 'name="security_deposit"')
        self.assertContains(page, 'name="ejari_fee"')

    def test_the_module_has_its_new_name(self):
        page = self.client.get(reverse("admin:leases_leasepayment_changelist"))
        self.assertContains(page, "Tenant payments")


class BulkEditAndRemove(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("bulkboss", "bk@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = make_lease(self.tenant, self.flat, rent="40000")
        self.rows = list(self.lease.payments.order_by("due_date"))
        self.url = reverse("admin:leases_leasepayment_bulk")

    def ids(self, items):
        return ",".join(str(p.pk) for p in items)

    def test_edit_cheque_numbers_dates_and_amounts_of_several_payments(self):
        reply = self.client.post(self.url, {
            "action": "edit", "ids": self.ids(self.rows[:3]), "method": "cheque", "bank_name": "ENBD",
            "cheque_first": "000100", "amount": "9000", "due_date": "2027-01-15",
        }).json()
        self.assertTrue(reply["ok"], reply)
        for number, payment in enumerate(sorted(LeasePayment.objects.filter(pk__in=[p.pk for p in self.rows[:3]]), key=lambda p: p.pk)):
            self.assertEqual((payment.bank_name, payment.amount, payment.due_date.isoformat()), ("ENBD", Decimal("9000.00"), "2027-01-15"))
        numbers = sorted(LeasePayment.objects.filter(pk__in=[p.pk for p in self.rows[:3]]).values_list("cheque_number", flat=True))
        self.assertEqual(numbers, ["000100", "000101", "000102"])
        self.assertEqual(LeasePayment.objects.get(pk=self.rows[3].pk).amount, Decimal("10000.00"))  # not ticked: untouched

    def test_received_payments_keep_their_date_and_amount(self):
        self.rows[0].record(self.lease.start_date, "cash")
        reply = self.client.post(self.url, {
            "action": "edit", "ids": self.ids(self.rows[:2]), "amount": "7000", "notes": "checked",
        }).json()
        self.assertTrue(reply["ok"])
        self.rows[0].refresh_from_db()
        self.rows[1].refresh_from_db()
        self.assertEqual(self.rows[0].amount, Decimal("10000.00"))
        self.assertEqual(self.rows[1].amount, Decimal("7000.00"))
        self.assertEqual((self.rows[0].notes, self.rows[1].notes), ("checked", "checked"))

    def test_edit_needs_something_to_change_and_good_values(self):
        self.assertEqual(self.client.post(self.url, {"action": "edit", "ids": self.ids(self.rows[:1])}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {"action": "edit", "ids": self.ids(self.rows[:1]), "amount": "-5"}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {"action": "edit", "ids": self.ids(self.rows[:1]), "cheque_first": "abc"}).status_code, 400)

    def test_remove_ticked_removes_only_what_was_not_received(self):
        self.rows[0].record(self.lease.start_date, "cash")
        reply = self.client.post(self.url, {"action": "delete", "ids": self.ids(self.rows[:3])}).json()
        self.assertTrue(reply["ok"])
        self.assertEqual(self.lease.payments.count(), 2)  # the paid one and the one that was not ticked
        self.assertEqual(self.client.post(self.url, {"action": "delete", "ids": self.ids(self.rows[:1])}).status_code, 400)

    def test_remove_a_deposit_line_clears_the_deposit_on_the_lease(self):
        lease = make_lease(self.tenant, self.villa, rent="18000", security_deposit=Decimal("800"))
        deposit = lease.payments.get(kind="deposit")
        self.assertTrue(self.client.post(self.url, {"action": "delete", "ids": str(deposit.pk)}).json()["ok"])
        lease.refresh_from_db()
        self.assertEqual(lease.security_deposit, Decimal("0.00"))

    def test_remove_needs_the_delete_permission_but_edit_does_not(self):
        from django.contrib.auth.models import Permission

        clerk = get_user_model().objects.create_user("bulkclerk", password="Pass-word1!", is_staff=True)
        clerk.user_permissions.add(*Permission.objects.filter(codename__in=("view_leasepayment", "change_leasepayment")))
        self.client.force_login(clerk)
        self.assertEqual(self.client.post(self.url, {"action": "delete", "ids": self.ids(self.rows[:1])}).status_code, 403)
        self.assertTrue(self.client.post(self.url, {"action": "edit", "ids": self.ids(self.rows[:1]), "notes": "x"}).json()["ok"])

    def test_the_list_offers_the_new_actions(self):
        page = self.client.get(reverse("admin:leases_leasepayment_changelist"))
        self.assertNotContains(page, "With the ticked rows")  # the row of buttons above the list is gone
        self.assertNotContains(page, "data-erp-bulk=")
        self.assertContains(page, 'class="erp-bulk" data-url=')  # the Actions list still knows where to send
        self.assertContains(page, "bulk_deposit_selected")
        self.assertContains(page, "bulk_edit_selected")
        self.assertContains(page, "bulk_delete_selected")


class AddPaymentOnTheLeaseTab(Setup):
    def test_the_lease_page_has_the_button_and_the_script_puts_it_on_the_payments_tab(self):
        from pathlib import Path

        boss = get_user_model().objects.create_superuser("tabboss", "tb@example.com", "Pass-word1!")
        self.client.force_login(boss)
        lease = full_lease(self)
        page = self.client.get(reverse("admin:leases_lease_change", args=[lease.pk]))
        self.assertContains(page, 'data-erp-act="addpay"')
        script = (Path(__file__).resolve().parent.parent / "core/static/core/leases.js").read_text(encoding="utf-8")
        self.assertIn('getElementById("payments-group")', script)


class PaymentsTabDashboard(Setup):
    def test_each_row_carries_the_facts_the_summary_needs(self):
        from pathlib import Path

        boss = get_user_model().objects.create_superuser("dashboss", "db@example.com", "Pass-word1!")
        self.client.force_login(boss)
        lease = full_lease(self)
        page = self.client.get(reverse("admin:leases_lease_change", args=[lease.pk]))
        self.assertContains(page, 'class="erp-rowmeta"')
        self.assertContains(page, 'data-kind="rent"')
        self.assertContains(page, 'data-overdue="')
        script = (Path(__file__).resolve().parent.parent / "core/static/core/leases.js").read_text(encoding="utf-8")
        self.assertIn("paymentsDashboard", script)
