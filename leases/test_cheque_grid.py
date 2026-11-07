"""The cheque grid on the lease's Payments tab (Update 36). Run with:  python manage.py test"""
import json
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from .models import LeasePayment
from .test_charges import full_lease
from .tests import Setup, make_lease


class ChequeGrid(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("gridboss", "gb@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = full_lease(self)  # 4 rent lines + deposit, office, agency and Ejari lines
        self.rent = list(self.lease.payments.filter(kind="rent").order_by("part"))

    def url(self, lease=None):
        return reverse("admin:leases_leasepayment_cheque_grid", args=[(lease or self.lease).pk])

    def save(self, rows, lease=None):
        return self.client.post(self.url(lease), {"rows": json.dumps(rows)})

    def row(self, payment, number="", bank="", day=""):
        return {"id": payment.pk, "cheque_number": number, "bank_name": bank, "cheque_date": day}

    def fresh(self, payment):
        return LeasePayment.objects.get(pk=payment.pk)

    # ---- reading ---------------------------------------------------------------------
    def test_the_grid_lists_every_line_except_waived_ones(self):
        self.rent[0].waive()
        data = self.client.get(self.url()).json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["rows"]), 7)
        self.assertNotIn(self.rent[0].pk, [r["id"] for r in data["rows"]])
        self.assertIn("Mashreq Bank", data["banks"])
        first = data["rows"][0]
        for key in ("id", "label", "due", "amount", "status", "method", "cheque_number", "bank_name", "cheque_date", "editable"):
            self.assertIn(key, first)

    def test_a_line_received_in_cash_is_locked_in_the_grid(self):
        deposit = self.lease.payments.get(kind="deposit")
        deposit.record(self.lease.start_date, "cash")
        rows = {r["id"]: r for r in self.client.get(self.url()).json()["rows"]}
        self.assertTrue(rows[deposit.pk]["cheque_locked"])  # its cheque cells start locked
        self.assertTrue(rows[deposit.pk]["why"])
        self.assertTrue(rows[deposit.pk]["method_editable"])  # but Paid By can be corrected
        self.assertFalse(rows[self.rent[0].pk]["cheque_locked"])

    # ---- saving ----------------------------------------------------------------------
    def test_several_lines_are_saved_in_one_go(self):
        rows = [
            self.row(self.rent[0], "000101", "mashreq bank", "2026-12-06"),
            self.row(self.rent[1], "000102", "Mashreq Bank", "2027-03-06"),
            self.row(self.rent[2], "000103", "Emirates NBD", ""),
        ]
        reply = self.save(rows).json()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["saved"], 3)
        first = self.fresh(self.rent[0])
        self.assertEqual((first.cheque_number, first.bank_name, first.cheque_date), ("000101", "Mashreq Bank", date(2026, 12, 6)))
        self.assertEqual(self.fresh(self.rent[2]).cheque_date, None)
        self.assertEqual(self.fresh(self.rent[3]).cheque_number, "")  # not in the request: untouched

    def test_lines_that_did_not_change_are_skipped(self):
        self.save([self.row(self.rent[0], "000101", "Mashreq Bank", "2026-12-06")])
        reply = self.save([self.row(self.rent[0], "000101", "Mashreq Bank", "2026-12-06")]).json()
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["saved"], 0)

    def test_details_can_be_cleared(self):
        self.save([self.row(self.rent[0], "000101", "Mashreq Bank", "2026-12-06")])
        reply = self.save([self.row(self.rent[0])]).json()
        self.assertEqual(reply["saved"], 1)
        line = self.fresh(self.rent[0])
        self.assertEqual((line.cheque_number, line.bank_name, line.cheque_date), ("", "", None))

    def test_typing_cheque_details_on_a_cash_line_turns_it_into_a_cheque(self):
        office = self.lease.payments.get(kind="office")
        LeasePayment.objects.filter(pk=office.pk).update(method="cash")
        reply = self.save([self.row(office, "000555", "Mashreq Bank", "")]).json()
        self.assertTrue(reply["ok"], reply)
        line = self.fresh(office)
        self.assertEqual((line.method, line.cheque_number), ("cheque", "000555"))

    def test_a_cheque_that_was_received_can_still_have_a_typing_mistake_fixed(self):
        line = self.rent[0]
        line.record(self.lease.start_date, "cheque", "000999", "Mashreq Bank")
        reply = self.save([self.row(line, "000998", "Mashreq Bank", "")]).json()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(self.fresh(line).cheque_number, "000998")
        self.assertEqual(self.fresh(line).status, "paid")

    # ---- refusing --------------------------------------------------------------------
    def test_a_line_received_in_cash_cannot_get_cheque_details(self):
        deposit = self.lease.payments.get(kind="deposit")
        deposit.record(self.lease.start_date, "cash")
        reply = self.save([self.row(deposit, "000777", "Mashreq Bank", "")])
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.fresh(deposit).cheque_number, "")

    def test_one_wrong_row_means_nothing_is_saved(self):
        rows = [
            self.row(self.rent[0], "000101", "Mashreq Bank", ""),
            self.row(self.rent[1], "000102", "Nowhere Bank", ""),
        ]
        reply = self.save(rows)
        self.assertEqual(reply.status_code, 400)
        self.assertIn("Nowhere Bank", " ".join(reply.json()["errors"]))
        self.assertEqual(self.fresh(self.rent[0]).cheque_number, "")  # the good row was not kept either

    def test_a_date_that_cannot_be_read_is_refused(self):
        reply = self.save([self.row(self.rent[0], "000101", "", "not a date")])
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.fresh(self.rent[0]).cheque_number, "")

    def test_the_same_cheque_number_twice_is_refused(self):
        twice = self.save([self.row(self.rent[0], "000101", "Mashreq Bank", ""), self.row(self.rent[1], "000101", "Mashreq Bank", "")])
        self.assertEqual(twice.status_code, 400)
        self.assertIn("used twice", " ".join(twice.json()["errors"]))
        self.save([self.row(self.rent[0], "000101", "Mashreq Bank", "")])
        later = self.save([self.row(self.rent[1], "000101", "Mashreq Bank", "")])
        self.assertEqual(later.status_code, 400)
        different_bank = self.save([self.row(self.rent[1], "000101", "Emirates NBD", "")])
        self.assertTrue(different_bank.json()["ok"])  # another bank can have the same number

    def test_a_cheque_cannot_be_dated_after_its_deposit_date(self):
        LeasePayment.objects.filter(pk=self.rent[0].pk).update(method="cheque", deposited_on=date(2026, 1, 1))
        reply = self.save([self.row(self.rent[0], "000101", "Mashreq Bank", "2026-02-01")])
        self.assertEqual(reply.status_code, 400)
        self.assertIn("deposited", " ".join(reply.json()["errors"]).lower())

    def test_a_payment_of_another_lease_is_refused(self):
        other = make_lease(self.tenant, self.villa)
        stranger = other.payments.first()
        reply = self.save([self.row(stranger, "000101", "Mashreq Bank", "")])
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.fresh(stranger).cheque_number, "")

    def test_bad_requests_are_refused(self):
        post = lambda body: self.client.post(self.url(), body)  # noqa: E731
        self.assertEqual(post({"rows": "not json"}).status_code, 400)
        self.assertEqual(post({"rows": "[]"}).status_code, 400)
        self.assertEqual(post({}).status_code, 400)
        self.assertEqual(self.client.put(self.url()).status_code, 405)
        self.assertEqual(self.save([{"id": "x"}]).status_code, 400)
        long_number = self.save([self.row(self.rent[0], "1" * 31, "", "")])
        self.assertEqual(long_number.status_code, 400)

    # ---- who may use it --------------------------------------------------------------
    def test_it_needs_the_permission_to_change_payments(self):
        viewer = get_user_model().objects.create_user("gridviewer", password="Pass-word1!", is_staff=True)
        viewer.user_permissions.add(*Permission.objects.filter(codename__in=("view_leasepayment", "view_lease")))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.save([self.row(self.rent[0], "000101", "Mashreq Bank", "")]).status_code, 403)
        self.assertEqual(self.fresh(self.rent[0]).cheque_number, "")

    # ---- the page --------------------------------------------------------------------
    def test_the_lease_page_has_the_button_and_the_script_has_the_grid(self):
        page = self.client.get(reverse("admin:leases_lease_change", args=[self.lease.pk]))
        self.assertContains(page, 'data-erp-act="chequegrid"')
        self.assertContains(page, self.url())
        script = (Path(__file__).resolve().parent.parent / "core/static/core/leases.js").read_text(encoding="utf-8")
        self.assertIn("chequeGridDialog", script)
        self.assertIn("Save Cheque Details", script)

    def test_the_general_tab_cheque_numbering_is_still_there(self):
        page = self.client.get(reverse("admin:leases_lease_change", args=[self.lease.pk]))
        self.assertContains(page, 'data-erp-act="cheques"')


class ChequeGridDueDates(Setup):
    """The grid starts each unpaid cheque line's date at its due date (a suggestion, saved with the row)."""

    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("gridsuggest", "gs@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = full_lease(self)

    def test_the_grid_script_suggests_the_due_date_and_only_saves_edited_rows(self):
        script = (Path(__file__).resolve().parent.parent / "core/static/core/leases.js").read_text(encoding="utf-8")
        self.assertIn("st.suggest", script)
        self.assertIn("st.baseDate", script)
        self.assertIn("is-suggested", script)

    def test_every_line_carries_its_due_date_for_the_suggestion(self):
        rows = self.client.get(reverse("admin:leases_leasepayment_cheque_grid", args=[self.lease.pk])).json()["rows"]
        rent = [r for r in rows if r["label"].startswith("Payment")]
        self.assertEqual(len(rent), 4)
        self.assertTrue(all(r["due"] and r["status"] == "pending" and r["method"] == "cheque" and not r["cheque_date"] for r in rent))
        first = self.lease.payments.filter(kind="rent").order_by("part").first()
        self.assertEqual(rent[0]["due"], first.due_date.isoformat())

    def test_saving_a_row_stores_the_date_the_grid_showed(self):
        first = self.lease.payments.filter(kind="rent").order_by("part").first()
        rows = [{"id": first.pk, "cheque_number": "000201", "bank_name": "Mashreq Bank", "cheque_date": first.due_date.isoformat()}]
        reply = self.client.post(
            reverse("admin:leases_leasepayment_cheque_grid", args=[self.lease.pk]), {"rows": json.dumps(rows)}
        ).json()
        self.assertTrue(reply["ok"], reply)
        first.refresh_from_db()
        self.assertEqual(first.cheque_date, first.due_date)


class ChequeGridPaidBy(Setup):
    """'Paid By' (cheque, cash, bank transfer, card, other) can be changed in the grid."""

    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("gridpaidby", "gp@example.com", "Pass-word1!")
        self.client.force_login(self.boss)
        self.lease = full_lease(self)
        self.rent = list(self.lease.payments.filter(kind="rent").order_by("part"))
        self.url = reverse("admin:leases_leasepayment_cheque_grid", args=[self.lease.pk])

    def save(self, rows):
        return self.client.post(self.url, {"rows": json.dumps(rows)})

    def row(self, payment, method, number="", bank="", day=""):
        return {"id": payment.pk, "method": method, "cheque_number": number, "bank_name": bank, "cheque_date": day}

    def fresh(self, payment):
        return LeasePayment.objects.get(pk=payment.pk)

    def test_the_grid_lists_the_ways_of_paying_without_the_deposit_one(self):
        data = self.client.get(self.url).json()
        self.assertEqual([v for v, _l in data["methods"]], ["cheque", "transfer", "cash", "card", "other"])
        self.assertTrue(all(r["method_editable"] for r in data["rows"]))

    def test_several_payments_can_be_switched_at_once(self):
        rows = [self.row(self.rent[0], "cash"), self.row(self.rent[1], "transfer"), self.row(self.rent[2], "card")]
        reply = self.save(rows).json()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["saved"], 3)
        self.assertEqual([self.fresh(p).method for p in self.rent[:3]], ["cash", "transfer", "card"])
        self.assertEqual(self.fresh(self.rent[3]).method, "cheque")  # not in the request

    def test_switching_away_from_cheque_clears_the_cheque_details(self):
        self.save([self.row(self.rent[0], "cheque", "000301", "Mashreq Bank", "2026-12-06")])
        reply = self.save([self.row(self.rent[0], "cash")]).json()
        self.assertEqual(reply["saved"], 1)
        line = self.fresh(self.rent[0])
        self.assertEqual((line.method, line.cheque_number, line.bank_name, line.cheque_date), ("cash", "", "", None))

    def test_cheque_details_do_not_go_with_cash(self):
        reply = self.save([self.row(self.rent[0], "cash", "000301")])
        self.assertEqual(reply.status_code, 400)
        self.assertIn("only go with Cheque", " ".join(reply.json()["errors"]))
        self.assertEqual(self.fresh(self.rent[0]).method, "cheque")

    def test_a_received_payment_can_be_corrected_to_another_way(self):
        deposit = self.lease.payments.get(kind="deposit")
        deposit.record(self.lease.start_date, "cash")
        reply = self.save([self.row(deposit, "transfer")]).json()
        self.assertTrue(reply["ok"], reply)
        line = self.fresh(deposit)
        self.assertEqual((line.method, line.status), ("transfer", "paid"))
        reply = self.save([self.row(deposit, "cheque", "000401", "Emirates NBD")]).json()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual((self.fresh(deposit).method, self.fresh(deposit).cheque_number, self.fresh(deposit).status), ("cheque", "000401", "paid"))

    def test_a_received_cheque_switched_to_cash_loses_its_cheque_and_deposit_details(self):
        line = self.rent[0]
        line.record(self.lease.start_date, "cheque", "000501", "Mashreq Bank")
        LeasePayment.objects.filter(pk=line.pk).update(deposited_on=self.lease.start_date)
        self.assertTrue(self.save([self.row(line, "cash")]).json()["ok"])
        fresh = self.fresh(line)
        self.assertEqual((fresh.method, fresh.cheque_number, fresh.deposited_on, fresh.status), ("cash", "", None, "paid"))

    def test_a_held_or_bounced_cheque_stays_a_cheque(self):
        self.rent[0].hold(self.lease.start_date, "000601", "Mashreq Bank")
        reply = self.save([self.row(self.rent[0], "cash")])
        self.assertEqual(reply.status_code, 400)
        self.rent[1].record(self.lease.start_date, "cheque", "000602", "Mashreq Bank")
        self.rent[1].bounce()
        reply = self.save([self.row(self.rent[1], "cash")])
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.fresh(self.rent[0]).method, "cheque")

    def test_money_kept_from_the_deposit_cannot_be_switched(self):
        from decimal import Decimal

        from .models import LeaseDeduction

        deduction = LeaseDeduction(lease=self.lease, kind="deposit", date=self.lease.start_date, reason="Damage", amount=Decimal("100"))
        deduction.full_clean()
        deduction.save()
        line = deduction.payment
        rows = {r["id"]: r for r in self.client.get(self.url).json()["rows"]}
        self.assertFalse(rows[line.pk]["editable"])
        reply = self.save([self.row(line, "cash")])
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.fresh(line).method, "deposit")

    def test_an_unknown_way_of_paying_is_refused_and_nothing_is_saved(self):
        reply = self.save([self.row(self.rent[0], "cash"), self.row(self.rent[1], "gold")])
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.fresh(self.rent[0]).method, "cheque")
        reply = self.save([self.row(self.rent[0], "deposit")])  # the "kept from the deposit" way cannot be chosen
        self.assertEqual(reply.status_code, 400)

    def test_an_unchanged_way_of_paying_is_not_saved_again(self):
        reply = self.save([self.row(self.rent[0], "cheque")]).json()
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["saved"], 0)

    def test_the_grid_script_has_the_paid_by_cell(self):
        script = (Path(__file__).resolve().parent.parent / "core/static/core/leases.js").read_text(encoding="utf-8")
        for text in ("attachCombo", "matchMethod", "Set Paid By for Unpaid", "becomeCheque"):
            self.assertIn(text, script)
