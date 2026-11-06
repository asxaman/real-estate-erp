"""Update 29: lease number hint, open-lease links, photo uploads, quick dates, bank list, record-payment picker."""
import io
import json
from pathlib import Path

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from core import banks
from core.models import Bank
from properties.models import PropertyPhoto, UnitPhoto

from .test_charges import full_lease
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
