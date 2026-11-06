"""Update 29: lease number hint, open-lease links, photo uploads, quick dates, bank list, record-payment picker."""
import io
import json
from pathlib import Path

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core import banks
from core.models import Bank
from properties.models import PropertyPhoto, UnitPhoto

from .test_charges import full_lease
from properties.tests import building
from .tests import Setup


def png(name="a.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class Update29(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("u29", "u29@example.com", "Pass-word1!")
        self.client.force_login(self.boss)

    # 1
    def test_new_lease_form_explains_the_automatic_number(self):
        page = self.client.get(reverse("admin:leases_lease_add"))
        self.assertContains(page, "Created automatically when you save")

    # 2
    def test_unit_leases_tab_has_open_button_and_tenant_link(self):
        lease = full_lease(self)
        page = self.client.get(reverse("admin:properties_unit_change", args=[self.flat.pk]))
        self.assertContains(page, "Open lease")
        self.assertContains(page, reverse("admin:leases_lease_change", args=[lease.pk]))
        self.assertContains(page, reverse("admin:tenants_tenant_change", args=[self.tenant.pk]))

    # 3
    def test_several_photos_at_once_on_a_unit_and_a_villa(self):
        url = reverse("admin:properties_unit_photos_upload", args=[self.flat.pk])
        reply = self.client.post(url, {"photos": [png("1.png"), png("2.png"), png("3.png")]})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(UnitPhoto.objects.filter(unit=self.flat).count(), 3)
        url = reverse("admin:properties_property_photos_upload", args=[self.villa.pk])
        reply = self.client.post(url, {"photos": [png("v1.png"), png("v2.png")]})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(PropertyPhoto.objects.filter(property=self.villa).count(), 2)

    def test_a_file_that_is_not_a_picture_is_refused(self):
        url = reverse("admin:properties_property_photos_upload", args=[self.villa.pk])
        bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        reply = self.client.post(url, {"photos": [bad]})
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(PropertyPhoto.objects.count(), 0)

    def test_photos_tab_is_for_standalone_properties_only(self):
        villa = self.client.get(reverse("admin:properties_property_change", args=[self.villa.pk]))
        self.assertContains(villa, "photos-group")
        tower = self.client.get(reverse("admin:properties_property_change", args=[self.tower.pk]))
        self.assertNotContains(tower, "photos-group")

    # 4
    def test_quick_dates(self):
        reply = self.client.get(reverse("admin:leases_lease_dates"), {"start": "2026-11-06"})
        data = reply.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["year_end"], "2027-11-05")
        self.assertTrue(data["today"])

    # 5
    def test_the_bank_list_starts_with_uae_banks_and_is_served(self):
        self.assertTrue(Bank.objects.filter(name__startswith="Emirates NBD").exists())
        data = self.client.get(reverse("banks_json")).json()
        self.assertIn("Mashreq Bank", data["banks"])
        Bank.objects.filter(name="Mashreq Bank").update(is_active=False)
        self.assertNotIn("Mashreq Bank", banks.names())
        self.assertIn("Old Bank", banks.names(include="Old Bank"))  # an old typed name stays usable

    def test_banks_are_managed_in_system_settings(self):
        page = self.client.get(reverse("admin:core_bank_changelist"))
        self.assertEqual(page.status_code, 200)
        settings_page = self.client.get(reverse("admin:core_systemsettings_changelist"), follow=True)
        self.assertContains(settings_page, "Manage banks")

    # 6
    def test_record_payment_on_the_lease_lists_the_payments_to_choose_from(self):
        lease = full_lease(self)
        page = self.client.get(reverse("admin:leases_lease_change", args=[lease.pk]))
        html = page.content.decode()
        self.assertIn("&quot;pick&quot;", html)
        script = (Path(__file__).resolve().parent.parent / "core/static/core/leases.js").read_text(encoding="utf-8")
        self.assertIn("Which payment?", script)
        self.assertIn('when: function (v) { return v.method === "cheque"; }', script)
        self.assertIn(json.dumps("/admin/banks.json"), script)


class DatesFollowSystemSettings(Setup):
    """The date style chosen in System settings is used everywhere, including pop-ups, prints and downloads."""

    def setUp(self):
        super().setUp()
        from core import formats, sysconf
        from core.models import SystemSettings

        self.formats = formats
        row = SystemSettings.load()
        row.date_format, row.time_format = "dmy_dash", "12h"
        row.save()
        sysconf.refresh({name: getattr(row, name) for name in sysconf.DEFAULTS})
        self.addCleanup(sysconf.refresh, None)
        self.boss = get_user_model().objects.create_superuser("fmt", "fmt@example.com", "Pass-word1!")
        self.client.force_login(self.boss)

    def test_the_pop_up_script_is_told_the_style(self):
        data = self.client.get(reverse("formats_json")).json()
        self.assertEqual(data, {"date": "%d-%m-%Y", "label": "DD-MM-YYYY"})

    def test_pop_up_dates_are_text_boxes_in_the_chosen_style_not_browser_date_pickers(self):
        script = (Path(__file__).resolve().parent.parent / "core/static/core/leases.js").read_text(encoding="utf-8")
        self.assertIn('input.type = f.type === "date" ? "text"', script)
        self.assertIn("/admin/formats.json", script)

    def test_the_server_reads_a_date_typed_in_the_chosen_style(self):
        lease = full_lease(self)
        payment = lease.payments.filter(kind="rent").first()
        reply = self.client.post(
            reverse("admin:leases_leasepayment_record", args=[payment.pk]),
            {"action": "paid", "date": payment.due_date.strftime("%d-%m-%Y"), "method": "cash"},
        )
        self.assertTrue(reply.json()["ok"])
        payment.refresh_from_db()
        self.assertEqual(payment.paid_on, payment.due_date)

    def test_printed_lease_and_rent_roll_use_the_chosen_style(self):
        lease = full_lease(self)
        text = lease.start_date.strftime("%d-%m-%Y")
        printed = self.client.get(reverse("admin:leases_lease_change", args=[lease.pk]))
        self.assertEqual(printed.status_code, 200)
        self.assertEqual(self.formats.fmt_date(lease.start_date), text)

    def test_excel_cells_and_csv_text_follow_the_style(self):
        from datetime import date

        from core.exports import csv_response, excel_response

        day = date(2026, 11, 6)
        csv_text = csv_response(["D"], [[day]], "x").content.decode("utf-8-sig")
        self.assertIn("06-11-2026", csv_text)
        from io import BytesIO

        from openpyxl import load_workbook

        book = load_workbook(BytesIO(excel_response("T", ["D"], [[day]], "x").content))
        self.assertEqual(book.active["A2"].number_format, "dd-mm-yyyy")
        self.assertEqual(self.formats.excel_formats()[1], "dd-mm-yyyy h:mm AM/PM")


class Update31(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("u31", "u31@example.com", "Pass-word1!")
        self.client.force_login(self.boss)

    # 1: the dashboard counts vacant stand-alone properties with vacant units
    def test_dashboard_counts_vacant_villas_with_vacant_units(self):
        from properties.models import Property, Unit

        Property.objects.filter(pk=self.villa.pk).update(status=Property.Status.VACANT, is_active=True)
        Unit.objects.filter(pk=self.flat.pk).update(status=Unit.Status.VACANT)
        response = self.client.get("/admin/")
        cards = {c["label"]: c["value"] for c in response.context["cards"]}
        self.assertEqual(cards["Vacant"], 2)
        self.assertEqual(response.context["panels"]["vacant_total"], 2)
        names = [item["name"] for item in response.context["panels"]["vacant"]]
        self.assertTrue(any(self.villa.code in name for name in names))

    # 2: clear words when a delete is refused
    def test_deleting_a_lease_with_payments_explains_why(self):
        lease = full_lease(self)
        page = self.client.get(reverse("admin:leases_lease_delete", args=[lease.pk]))
        text = page.content.decode()
        self.assertIn(f"{lease.code} has 8 payment lines", text)
        self.assertIn("kept for the records", text)

    def test_a_tenant_with_leases_cannot_be_deleted_and_the_reason_is_a_sentence(self):
        full_lease(self)
        page = self.client.get(reverse("admin:tenants_tenant_delete", args=[self.tenant.pk]))
        self.assertContains(page, "cannot be deleted because other records depend on it")

    # 3: the rent follows the payments and cannot be typed over
    def test_annual_rent_and_payment_count_are_locked_once_payments_exist(self):
        lease = full_lease(self)
        page = self.client.get(reverse("admin:leases_lease_change", args=[lease.pk]))
        self.assertNotContains(page, 'name="annual_rent"')
        self.assertNotContains(page, 'name="installments"')
        self.assertContains(page, "edit the payments themselves")

    def test_a_new_lease_can_still_have_its_rent_typed(self):
        page = self.client.get(reverse("admin:leases_lease_add"))
        self.assertContains(page, 'name="annual_rent"')

    def test_editing_a_rent_payment_updates_the_annual_rent(self):
        lease = full_lease(self)
        payment = lease.payments.filter(kind="rent").order_by("part").first()
        values = {
            "due_date": payment.due_date.isoformat(), "amount": str(payment.amount + 1000), "method": payment.method,
            "status": payment.status, "cheque_number": "", "bank_name": "", "cheque_date": "", "deposited_on": "",
            "paid_on": "", "notes": "",
        }
        reply = self.client.post(reverse("admin:leases_leasepayment_edit", args=[payment.pk]), values)
        self.assertTrue(reply.json()["ok"], reply.json())
        lease.refresh_from_db()
        self.assertEqual(lease.annual_rent, sum(p.amount for p in lease.payments.filter(kind="rent")))
        self.assertEqual(lease.annual_rent, Decimal("19000.00"))

    def test_removing_a_rent_payment_updates_the_annual_rent(self):
        lease = full_lease(self)
        last = lease.payments.filter(kind="rent").order_by("part").last()
        reply = self.client.post(reverse("admin:leases_leasepayment_remove", args=[last.pk]))
        self.assertTrue(reply.json()["ok"])
        lease.refresh_from_db()
        self.assertEqual(lease.annual_rent, Decimal("13500.00"))


class Update32(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("u32", "u32@example.com", "Pass-word1!")
        self.client.force_login(self.boss)

    # dates
    def test_moving_the_start_date_moves_the_pending_payments(self):
        lease = full_lease(self)
        before = {p.pk: p.due_date for p in lease.payments.all()}
        lease.start_date += timedelta(days=10)
        lease.end_date += timedelta(days=10)
        lease.clean()
        lease.save()
        for payment in lease.payments.all():
            if payment.kind == "rent":
                self.assertEqual(payment.due_date, before[payment.pk] + timedelta(days=10))
            else:
                self.assertEqual(payment.due_date, lease.start_date)

    def test_dates_are_fixed_once_money_is_received(self):
        lease = full_lease(self)
        lease.payments.filter(kind="rent").order_by("part").first().record(timezone.localdate(), "cash")
        lease.start_date += timedelta(days=3)
        with self.assertRaises(ValidationError) as caught:
            lease.clean()
        self.assertIn("start_date", caught.exception.message_dict)
        page = self.client.get(reverse("admin:leases_lease_change", args=[lease.pk]))
        self.assertNotContains(page, 'name="start_date"')
        self.assertContains(page, "dates are fixed too")

    # termination
    def test_terminating_updates_the_annual_rent(self):
        lease = full_lease(self)
        lease.terminate(lease.start_date + timedelta(days=100), "Tenant left")
        lease.refresh_from_db()
        self.assertEqual(lease.annual_rent, sum(p.amount for p in lease.payments.filter(kind="rent")))
        self.assertLess(lease.annual_rent, Decimal("18000.00"))

    # renewal and the deposit
    def test_renewal_does_not_ask_for_a_received_deposit_again(self):
        lease = full_lease(self)
        lease.payments.get(kind="deposit").record(timezone.localdate(), "cash")
        new = lease.renew()
        self.assertEqual(new.security_deposit, 0)
        self.assertFalse(new.payments.filter(kind="deposit").exists())

    def test_renewal_still_asks_for_a_deposit_that_was_never_received(self):
        lease = full_lease(self)
        new = lease.renew()
        self.assertEqual(new.payments.get(kind="deposit").amount, Decimal("5000.00"))

    # switching things off
    def test_a_tenant_or_property_with_a_running_lease_cannot_be_made_inactive(self):
        full_lease(self)
        self.tenant.is_active = False
        with self.assertRaises(ValidationError) as caught:
            self.tenant.clean()
        self.assertIn("is_active", caught.exception.message_dict)
        self.tower.is_active = False
        with self.assertRaises(ValidationError) as caught:
            self.tower.clean()
        self.assertIn("is_active", caught.exception.message_dict)

    def test_a_tenant_without_a_running_lease_can_be_made_inactive(self):
        lease = full_lease(self)
        lease.terminate(lease.start_date + timedelta(days=5), "Left")  # ended 5 days after it started: already over
        self.tenant.is_active = False
        self.tenant.inactive_reason = "Moved away"
        self.tenant.clean()

    # waiving
    def test_an_overdue_payment_can_be_waived_and_stops_counting_as_late(self):
        lease = full_lease(self)
        payment = lease.payments.filter(kind="rent").order_by("part").first()
        self.assertTrue(payment.is_overdue())
        reply = self.client.post(reverse("admin:leases_leasepayment_record", args=[payment.pk]), {"action": "waived"})
        self.assertTrue(reply.json()["ok"], reply.json())
        payment.refresh_from_db()
        self.assertEqual(payment.status, "waived")
        self.assertFalse(payment.is_overdue())
        self.assertEqual(payment.late_fee(), Decimal("0.00"))
        self.assertEqual(lease.scheduled_total(), Decimal("13500.00"))

    def test_a_received_payment_cannot_be_waived(self):
        lease = full_lease(self)
        payment = lease.payments.filter(kind="rent").order_by("part").first()
        payment.record(timezone.localdate(), "cash")
        reply = self.client.post(reverse("admin:leases_leasepayment_record", args=[payment.pk]), {"action": "waived"})
        self.assertFalse(reply.json()["ok"])

    def test_pages_still_open_with_a_waived_payment(self):
        lease = full_lease(self)
        lease.payments.filter(kind="rent").order_by("part").first().waive()
        for name, args in (("admin:leases_lease_change", [lease.pk]), ("admin:leases_leasepayment_changelist", []),
                           ("admin:leases_lease_changelist", [])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    # rebuilding keeps hand-made lines
    def test_rebuilding_the_schedule_keeps_a_fine_line(self):
        from .models import LeasePayment

        lease = full_lease(self)
        LeasePayment.objects.create(lease=lease, kind="fine", part=1, due_date=lease.start_date, amount=Decimal("200"))
        self.client.post(reverse("admin:leases_lease_changelist"), {
            "action": "rebuild_schedule", "_selected_action": [lease.pk], "index": 0,
        })
        self.assertTrue(lease.payments.filter(kind="fine").exists())
        self.assertEqual(lease.payments.filter(kind="rent").count(), 4)

    # moving a lease from a unit to a stand-alone property frees the unit
    def test_moving_a_lease_to_another_place_frees_the_old_one(self):
        from properties.models import Property, Unit

        lease = full_lease(self)
        self.flat.refresh_from_db()
        self.assertEqual(self.flat.status, Unit.Status.OCCUPIED)
        lease.unit = None
        lease.property = self.villa
        lease.clean()
        lease.save()
        self.flat.refresh_from_db()
        self.villa.refresh_from_db()
        self.assertEqual(self.flat.status, Unit.Status.VACANT)
        self.assertEqual(self.villa.status, Property.Status.OCCUPIED)

    def test_the_property_list_shows_the_status_of_a_stand_alone_property(self):
        page = self.client.get(reverse("admin:properties_property_changelist"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Vacant")

    def test_a_unit_moved_to_another_building_takes_its_leases_with_it(self):
        lease = full_lease(self)
        other = building("MV2")
        self.flat.property = other
        self.flat.save()
        lease.refresh_from_db()
        self.assertEqual(lease.property_id, other.pk)

    def test_a_terminated_lease_cannot_be_made_active_again(self):
        lease = full_lease(self)
        lease.terminate(lease.start_date + timedelta(days=100), "Left")
        lease.status = "active"
        with self.assertRaises(ValidationError) as caught:
            lease.clean()
        self.assertIn("status", caught.exception.message_dict)

    def test_the_end_date_cannot_be_before_a_payment_that_is_still_due(self):
        lease = full_lease(self)
        lease.end_date = lease.start_date + timedelta(days=120)
        with self.assertRaises(ValidationError) as caught:
            lease.clean()
        self.assertIn("end_date", caught.exception.message_dict)

    def test_a_property_rented_as_a_whole_cannot_become_a_building(self):
        from properties.models import Property

        from .tests import make_lease

        make_lease(self.tenant, self.villa)
        self.villa.property_type = Property.PropertyType.RESIDENTIAL_BUILDING
        with self.assertRaises(ValidationError) as caught:
            self.villa.clean()
        self.assertIn("property_type", caught.exception.message_dict)


class Update33(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("u33", "u33@example.com", "Pass-word1!")
        self.client.force_login(self.boss)

    def post(self, name, pk, data):
        return self.client.post(reverse(name, args=[pk]), data)

    def line_values(self, payment, **change):
        values = {
            "due_date": payment.due_date.isoformat(), "amount": str(payment.amount), "method": payment.method,
            "status": payment.status, "cheque_number": "", "bank_name": "", "cheque_date": "", "deposited_on": "",
            "paid_on": payment.paid_on.isoformat() if payment.paid_on else "", "notes": "",
        }
        values.update(change)
        return values

    # deposit limits
    def test_a_refund_cannot_be_more_than_the_deposit_left(self):
        lease = full_lease(self)  # deposit 5000
        add = reverse("admin:leases_leasepayment_add_to", args=[lease.pk])
        values = {"kind": "refund", "due_date": timezone.localdate().isoformat(), "amount": "5001", "method": "transfer"}
        self.assertFalse(self.client.post(add, values).json()["ok"])
        values["amount"] = "5000"
        self.assertTrue(self.client.post(add, values).json()["ok"])
        values["amount"] = "1"
        self.assertFalse(self.client.post(add, values).json()["ok"])  # nothing is left

    def test_editing_a_deduction_line_cannot_go_over_the_deposit(self):
        from .models import LeaseDeduction

        lease = full_lease(self)
        deduction = LeaseDeduction(lease=lease, kind="deposit", date=timezone.localdate(), reason="Damage", amount=Decimal("2000"))
        deduction.full_clean()
        deduction.save()
        line = lease.payments.get(kind="deduction")
        reply = self.post("admin:leases_leasepayment_edit", line.pk, self.line_values(line, amount="6000"))
        self.assertFalse(reply.json()["ok"])
        reply = self.post("admin:leases_leasepayment_edit", line.pk, self.line_values(line, amount="4000"))
        self.assertTrue(reply.json()["ok"], reply.json())

    def test_the_deposit_is_described_from_the_money_lines(self):
        lease = full_lease(self)
        self.assertEqual(lease.deposit_state(), "Held")
        add = reverse("admin:leases_leasepayment_add_to", args=[lease.pk])
        self.client.post(add, {"kind": "refund", "due_date": timezone.localdate().isoformat(), "amount": "5000", "method": "cash"})
        lease.refresh_from_db()
        self.assertEqual(lease.deposit_state(), "Refunded")

    # a lease that is ending still counts until the end date
    def test_a_lease_ending_in_the_future_is_still_listed_as_running(self):
        from .models import Lease, running_q

        lease = full_lease(self)
        lease.terminate(timezone.localdate() + timedelta(days=20), "Moving out")
        self.assertTrue(Lease.objects.filter(running_q(timezone.localdate()), pk=lease.pk).exists())
        roll = self.client.get(reverse("admin:leases_lease_changelist") + "?state=active")
        self.assertContains(roll, lease.code)
        cards = {c["label"]: c["value"] for c in self.client.get("/admin/").context["cards"]}
        self.assertEqual(cards["Active leases"], 1)

    def test_a_tenant_and_a_property_with_a_lease_ending_in_the_future_cannot_be_switched_off(self):
        lease = full_lease(self)
        lease.terminate(timezone.localdate() + timedelta(days=20), "Moving out")
        self.tenant.is_active = False
        with self.assertRaises(ValidationError):
            self.tenant.clean()
        self.tower.is_active = False
        with self.assertRaises(ValidationError):
            self.tower.clean()

    # waived money is not "still to receive" anywhere
    def test_waived_rent_is_left_out_of_exports_and_the_monthly_report(self):
        from . import reports

        lease = full_lease(self)
        first = lease.payments.filter(kind="rent").order_by("part").first()
        first.waive()
        row = next(reports.lease_rows(type(lease).objects.filter(pk=lease.pk)))
        self.assertEqual(row[reports.LEASE_HEADERS.index("Still to receive (AED, with VAT)")], Decimal("13500.00"))
        year = first.due_date.year
        months = reports.monthly_rows(year) + reports.monthly_rows(year + 1)
        self.assertEqual(sum(m["expected"] for m in months), Decimal("13500.00"))

    def test_the_overdue_numbers_on_the_dashboard_agree(self):
        lease = full_lease(self)
        first = lease.payments.filter(kind="rent").order_by("part").first()
        first.bounce()
        response = self.client.get("/admin/")
        cards = {c["label"]: c["value"] for c in response.context["cards"]}
        panel = {i["label"]: i["count"] for i in response.context["panels"]["attention"]}
        self.assertEqual(cards["Overdue payments"], panel["Overdue payments"])

    def test_adding_the_same_kind_of_line_twice_gives_each_its_own_number(self):
        lease = full_lease(self)
        add = reverse("admin:leases_leasepayment_add_to", args=[lease.pk])
        values = {"kind": "fine", "due_date": timezone.localdate().isoformat(), "amount": "100", "method": "cash"}
        self.assertTrue(self.client.post(add, values).json()["ok"])
        self.assertTrue(self.client.post(add, values).json()["ok"])
        self.assertEqual(sorted(lease.payments.filter(kind="fine").values_list("part", flat=True)), [1, 2])


class Update34(Setup):
    def setUp(self):
        super().setUp()
        self.boss = get_user_model().objects.create_superuser("u34", "u34@example.com", "Pass-word1!")
        self.client.force_login(self.boss)

    def test_vat_on_the_office_and_agency_fee_is_the_users_choice(self):
        lease = full_lease(self)  # office fee 700, agency fee 100, deposit 5000: no VAT by default
        self.assertEqual(lease.payments.get(kind="office").vat, Decimal("0.00"))
        lease.admin_fee_vat = True
        lease.save()
        office = lease.payments.get(kind="office")
        self.assertEqual((office.vat, office.total), (Decimal("35.00"), Decimal("735.00")))
        self.assertEqual(lease.payments.get(kind="agency").vat, Decimal("0.00"))  # its own choice
        lease.commission_vat = True
        lease.save()
        self.assertEqual(lease.payments.get(kind="agency").vat, Decimal("5.00"))
        self.assertEqual(lease.payments.get(kind="deposit").vat, Decimal("0.00"))  # never on a deposit

    def test_the_fee_vat_boxes_are_on_the_lease_form_and_follow_a_renewal(self):
        lease = full_lease(self)
        lease.admin_fee_vat = True
        lease.save()
        page = self.client.get(reverse("admin:leases_lease_change", args=[lease.pk]))
        self.assertContains(page, 'name="admin_fee_vat"')
        self.assertContains(page, 'name="commission_vat"')
        self.assertTrue(lease.renew().admin_fee_vat)

    def test_the_lease_page_and_the_rent_roll_use_the_same_vat_basis(self):
        from . import reports

        lease = full_lease(self, vat_applies=True)  # 18000 + 900 VAT
        page = self.client.get(reverse("admin:leases_lease_change", args=[lease.pk]))
        self.assertContains(page, "Annual rent incl. VAT")
        self.assertContains(page, "18,900")
        row = next(r for r in reports.roll_rows() if r["lease"].pk == lease.pk)
        self.assertEqual(row["monthly"], Decimal("1575.00"))

    def test_the_dashboard_money_includes_fee_vat(self):
        lease = full_lease(self)
        lease.admin_fee_vat = True
        lease.save()
        self.assertEqual(self.client.get("/admin/").status_code, 200)
