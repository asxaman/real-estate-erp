"""Checks for the Owner Agreements module."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from properties.models import Owner, Property

from . import seed
from .models import AgreementProperty, OwnerAgreement, agreement_for


def make_owner(name="Agreement Owner"):
    return Owner.objects.create(name=name)


def make_property(owner, code="AGP", name="Agreement Tower"):
    return Property.objects.create(
        name=name, code=code, property_type="residential_building", owner=owner, emirate="dubai",
        management_fee_type="percent", management_fee_percent=5,
    )


def make_agreement(owner, *properties, **values):
    today = timezone.localdate()
    data = {
        "start_date": today - timedelta(days=30), "end_date": today + timedelta(days=335),
        "fee_percent_collected": Decimal("5"), "status": OwnerAgreement.Status.ACTIVE,
    }
    data.update(values)
    agreement = OwnerAgreement(owner=owner, **data)
    agreement.full_clean()
    agreement.save()
    for prop in properties:
        line = AgreementProperty(agreement=agreement, property=prop)
        line.full_clean()
        line.save()
    return agreement


class AgreementRules(TestCase):
    def setUp(self):
        self.owner = make_owner()
        self.tower = make_property(self.owner)
        self.today = timezone.localdate()

    def errors(self, **values):
        data = {"start_date": self.today, "end_date": self.today + timedelta(days=365), "fee_percent_collected": Decimal("5")}
        data.update(values)
        agreement = OwnerAgreement(owner=self.owner, **data)
        try:
            agreement.full_clean()
        except ValidationError as error:
            return error.message_dict
        return {}

    def test_numbers_are_given_automatically(self):
        first = make_agreement(self.owner, self.tower)
        second = make_agreement(make_owner("Second Owner"))
        self.assertEqual(first.code, "AGR-0001")
        self.assertEqual(second.code, "AGR-0002")

    def test_dates(self):
        self.assertEqual(self.errors(), {})
        self.assertIn("end_date", self.errors(end_date=self.today))
        self.assertEqual(self.errors(end_date=None), {})  # no end date is allowed
        self.assertIn("end_date", self.errors(end_date=None, auto_renew=True))
        self.assertIn("signed_on", self.errors(signed_on=self.today + timedelta(days=1)))

    def test_fees(self):
        self.assertIn("fee_percent_collected", self.errors(fee_percent_collected=None))  # at least one way of earning
        self.assertEqual(self.errors(fee_percent_collected=None, fee_fixed_yearly=Decimal("12000")), {})
        self.assertEqual(self.errors(fee_percent_contract=Decimal("5")), {})  # two fees together are fine
        self.assertEqual(self.errors(fee_percent_collected=None, guaranteed_rent_yearly=Decimal("480000")), {})
        self.assertIn("guaranteed_rent_yearly", self.errors(guaranteed_rent_yearly=Decimal("480000")))  # stands alone
        self.assertIn("fee_percent_collected", self.errors(fee_percent_collected=Decimal("101")))
        self.assertIn("fee_percent_collected", self.errors(fee_percent_collected=Decimal("0")))

    def test_fee_text(self):
        agreement = OwnerAgreement(fee_percent_collected=Decimal("5.00"), fee_percent_contract=Decimal("2.50"))
        self.assertEqual(agreement.fee_text(), "5% of rent collected + 2.5% of contract rent")
        self.assertEqual(OwnerAgreement(fee_fixed_yearly=Decimal("12000")).fee_text(), "AED 12,000 / year")
        self.assertEqual(
            OwnerAgreement(guaranteed_rent_yearly=Decimal("480000")).fee_text(), "Guaranteed rent AED 480,000 / year"
        )

    def test_terminating_needs_a_date_and_a_reason(self):
        found = self.errors(status="terminated")
        self.assertIn("terminated_on", found)
        self.assertIn("termination_reason", found)
        self.assertEqual(self.errors(status="terminated", terminated_on=self.today, termination_reason="Owner sold"), {})
        self.assertIn(
            "terminated_on",
            self.errors(status="terminated", terminated_on=self.today - timedelta(days=1), termination_reason="x"),
        )
        # the two boxes are emptied again when the agreement is not terminated
        agreement = OwnerAgreement(
            owner=self.owner, start_date=self.today, fee_percent_collected=Decimal("5"), terminated_on=self.today,
            termination_reason="left over",
        )
        agreement.full_clean()
        self.assertIsNone(agreement.terminated_on)
        self.assertEqual(agreement.termination_reason, "")

    def test_an_inactive_owner_cannot_get_a_new_agreement(self):
        Owner.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.owner.refresh_from_db()
        self.assertIn("owner", self.errors())

    def test_the_company_itself_needs_no_agreement(self):
        company = Owner.objects.filter(is_company=True).first() or Owner.objects.create(name="Company Owned", is_company=True)
        agreement = OwnerAgreement(owner=company, start_date=self.today, fee_percent_collected=Decimal("5"))
        with self.assertRaises(ValidationError) as caught:
            agreement.full_clean()
        self.assertIn("owner", caught.exception.message_dict)

    def test_states(self):
        day = timedelta(days=1)
        agreement = OwnerAgreement(start_date=self.today - 10 * day, end_date=self.today + 300 * day, status="active")
        self.assertEqual(agreement.state(), "active")
        self.assertFalse(agreement.ends_soon())
        agreement.end_date = self.today + 5 * day
        self.assertTrue(agreement.ends_soon())
        agreement.end_date = self.today - day
        self.assertEqual(agreement.state(), "expired")
        agreement.auto_renew = True  # carries on after its end date
        self.assertEqual(agreement.state(), "active")
        self.assertFalse(agreement.ends_soon())
        self.assertEqual(OwnerAgreement(start_date=self.today + day, status="active").state(), "upcoming")
        self.assertEqual(OwnerAgreement(start_date=self.today, status="draft").state(), "draft")
        self.assertEqual(OwnerAgreement(start_date=self.today, status="terminated", terminated_on=self.today).state(), "terminated")


class PropertiesCovered(TestCase):
    def setUp(self):
        self.owner = make_owner()
        self.tower = make_property(self.owner)
        self.today = timezone.localdate()

    def line_errors(self, agreement, prop):
        try:
            AgreementProperty(agreement=agreement, property=prop).full_clean()
        except ValidationError as error:
            return error.message_dict
        return {}

    def test_the_property_must_belong_to_the_owner(self):
        agreement = make_agreement(self.owner)
        other = make_property(make_owner("Someone Else"), code="OTH", name="Other Tower")
        self.assertIn("property", self.line_errors(agreement, other))
        self.assertEqual(self.line_errors(agreement, self.tower), {})

    def test_one_property_cannot_be_under_two_agreements_at_the_same_time(self):
        make_agreement(self.owner, self.tower)
        second = make_agreement(self.owner)
        found = self.line_errors(second, self.tower)
        self.assertIn("AGR-0001", found["property"][0])
        # a later agreement (after the first one ended) is fine, and so is a draft
        later = make_agreement(self.owner, start_date=self.today + timedelta(days=400), end_date=self.today + timedelta(days=700))
        self.assertEqual(self.line_errors(later, self.tower), {})
        draft = make_agreement(self.owner, status="draft")
        self.assertEqual(self.line_errors(draft, self.tower), {})

    def test_a_terminated_agreement_frees_the_property_from_that_day(self):
        first = make_agreement(self.owner, self.tower)
        first.status, first.terminated_on, first.termination_reason = "terminated", self.today, "Ended"
        first.full_clean()
        first.save()
        after = make_agreement(self.owner, start_date=self.today + timedelta(days=1), end_date=self.today + timedelta(days=366))
        self.assertEqual(self.line_errors(after, self.tower), {})

    def test_the_same_property_twice_on_one_agreement_is_refused(self):
        agreement = make_agreement(self.owner, self.tower)
        self.assertNotEqual(self.line_errors(agreement, self.tower), {})

    def test_agreement_for_a_property(self):
        self.assertIsNone(agreement_for(self.tower))
        draft = make_agreement(self.owner, self.tower, status="draft")
        self.assertIsNone(agreement_for(self.tower))  # a draft does not count
        draft.delete()
        agreement = make_agreement(self.owner, self.tower)
        self.assertEqual(agreement_for(self.tower), agreement)
        self.assertIsNone(agreement_for(self.tower, self.today + timedelta(days=1000)))
        self.assertIsNone(agreement_for(self.tower, self.today - timedelta(days=100)))

    def test_a_covered_property_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        make_agreement(self.owner, self.tower)
        with self.assertRaises(ProtectedError):
            self.tower.delete()

    def test_the_owner_cannot_be_changed_while_his_properties_are_covered(self):
        agreement = make_agreement(self.owner, self.tower)
        agreement.owner = make_owner("New Owner")
        with self.assertRaises(ValidationError) as caught:
            agreement.full_clean()
        self.assertIn("owner", caught.exception.message_dict)


class AgreementPages(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("agr", "agr@example.com", "pw-12345-xyz")
        self.client.force_login(self.boss)
        self.owner = make_owner()
        self.tower = make_property(self.owner)
        self.agreement = make_agreement(self.owner, self.tower)
        self.list_url = reverse("admin:agreements_owneragreement_changelist")

    def test_the_module_has_its_own_plain_address(self):
        self.assertEqual(self.list_url, "/admin/owner-agreements/")

    def test_list_and_chips(self):
        page = self.client.get(self.list_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "AGR-0001")
        self.assertContains(page, "5% of rent collected")
        self.assertContains(page, "data-erp-search-hint")
        chips = {chip["label"]: chip["count"] for chip in page.context["quick_chips"]}
        self.assertEqual(chips["Running"], 1)
        self.assertEqual(chips["Draft"], 0)
        for key in ("running", "soon", "upcoming", "expired", "draft", "terminated"):
            self.assertEqual(self.client.get(self.list_url, {"chip": key}).status_code, 200, key)

    def test_search(self):
        for term in ("AGR-1", "agr1", "Agreement Owner", "Agreement Tower", "AGP"):
            page = self.client.get(self.list_url, {"q": term})
            self.assertContains(page, "AGR-0001", msg_prefix=term)
        self.assertNotContains(self.client.get(self.list_url, {"q": "zzzz-nothing"}), "AGR-0001")

    def test_pages_open(self):
        self.assertEqual(self.client.get(reverse("admin:agreements_owneragreement_add")).status_code, 200)
        page = self.client.get(reverse("admin:agreements_owneragreement_change", args=[self.agreement.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Properties Covered")
        self.assertContains(page, "Ending Early")

    def test_owner_and_property_pages_show_the_agreement(self):
        for name, pk in (("admin:properties_owner_change", self.owner.pk), ("admin:properties_property_change", self.tower.pk)):
            page = self.client.get(reverse(name, args=[pk]))
            self.assertEqual(page.status_code, 200, name)
            self.assertContains(page, "Open agreement", msg_prefix=name)
            self.assertContains(page, "AGR-0001", msg_prefix=name)
        # the pages for a new owner and a new property still open
        self.assertEqual(self.client.get(reverse("admin:properties_owner_add")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:properties_property_add")).status_code, 200)

    def test_only_a_draft_can_be_deleted(self):
        delete = reverse("admin:agreements_owneragreement_delete", args=[self.agreement.pk])
        self.assertEqual(self.client.get(delete).status_code, 403)
        draft = make_agreement(self.owner, status="draft")
        self.assertEqual(self.client.get(reverse("admin:agreements_owneragreement_delete", args=[draft.pk])).status_code, 200)

    def test_export(self):
        response = self.client.post(self.list_url, {"action": "export_csv", "_selected_action": [self.agreement.pk]})
        self.assertIn("attachment", response["Content-Disposition"])
        text = response.content.decode("utf-8-sig")
        self.assertIn("AGR-0001", text)
        self.assertIn("Agreement Tower", text)

    def test_menu_and_search_box(self):
        self.assertContains(self.client.get("/admin/"), self.list_url)


class Roles(TestCase):
    def test_roles_get_their_permissions(self):
        for name in ("Property Manager", "Accountant", "Leasing Agent"):
            Group.objects.create(name=name)
        seed.grant_role_permissions()

        def has(role, codename):
            return Group.objects.get(name=role).permissions.filter(content_type__app_label="agreements", codename=codename).exists()

        self.assertTrue(has("Property Manager", "add_owneragreement"))
        self.assertTrue(has("Property Manager", "export_owneragreement"))
        self.assertTrue(has("Accountant", "view_owneragreement"))
        self.assertFalse(has("Accountant", "change_owneragreement"))
        self.assertTrue(has("Leasing Agent", "view_owneragreement"))
        self.assertFalse(has("Leasing Agent", "export_owneragreement"))

    def test_staff_without_the_permission_cannot_open_the_list(self):
        user = User.objects.create_user("noagr", password="pw-12345-xyz", is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("admin:agreements_owneragreement_changelist")).status_code, 403)
        user.user_permissions.add(Permission.objects.get(content_type__app_label="agreements", codename="view_owneragreement"))
        self.assertEqual(self.client.get(reverse("admin:agreements_owneragreement_changelist")).status_code, 200)


class SampleData(TestCase):
    def test_seed_and_erase(self):
        from unittest import mock

        from core.data_registry import run_erasers, run_seeders

        with mock.patch("core.data_registry.SAMPLE_SCALE", 0):
            run_seeders()
            first = OwnerAgreement.objects.count()
            self.assertGreater(first, 0)
            self.assertFalse(OwnerAgreement.objects.filter(lines__isnull=True).exists())
            run_seeders()  # a second run adds nothing
            self.assertEqual(OwnerAgreement.objects.count(), first)
        run_erasers()
        self.assertEqual(OwnerAgreement.objects.count(), 0)
        self.assertEqual(Property.objects.count(), 0)


class FeeMovedToAgreements(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("fee", "fee@example.com", "pw-12345-xyz")
        self.client.force_login(self.boss)
        self.owner = make_owner()
        self.tower = make_property(self.owner)
        self.today = timezone.localdate()

    def test_the_property_page_has_no_fee_boxes_but_shows_the_agreement_fee(self):
        page = self.client.get(reverse("admin:properties_property_change", args=[self.tower.pk]))
        self.assertEqual(page.status_code, 200)
        for name in ("management_fee_type", "management_fee_percent", "management_fee_fixed"):
            self.assertNotContains(page, f'name="{name}"')
        self.assertContains(page, "No running owner agreement")
        make_agreement(self.owner, self.tower, fee_percent_collected=Decimal("6"))
        page = self.client.get(reverse("admin:properties_property_change", args=[self.tower.pk]))
        self.assertContains(page, "6% of rent collected")
        self.assertEqual(self.client.get(reverse("admin:properties_property_add")).status_code, 200)

    def test_a_property_needs_no_fee_to_be_saved_in_the_form(self):
        from properties.models import Owner as O

        company = O.company()
        prop = Property(name="Plain Villa", code="PLV", property_type="villa", owner=company, emirate="dubai", status="vacant")
        prop.full_clean()

    def test_export_shows_the_agreement_fee(self):
        from properties import export_tools

        rows = list(export_tools.property_rows(Property.objects.all()))
        self.assertEqual(rows[0][13], "")  # no agreement yet
        agreement = make_agreement(self.owner, self.tower, fee_percent_collected=Decimal("6"))
        rows = list(export_tools.property_rows(Property.objects.all()))
        self.assertEqual(rows[0][13], f"{agreement.code}: 6% of rent collected")

    def test_import_no_longer_reads_the_fee(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from properties import import_tools

        self.assertNotIn("Fee type", import_tools.PROPERTY_HEADERS)
        upload = SimpleUploadedFile(
            "p.csv", b"Code,Name,Type,Owner,Emirate,Fee type,Fee percent\nNEW1,New Villa,Villa,Agreement Owner,Dubai,Percent,5\n"
        )
        props, errors = import_tools.parse_property_file(upload)
        self.assertEqual(errors, [])
        props[0].full_clean()

    def test_old_fees_become_agreements_once(self):
        from core.models import AuditEntry

        self.assertEqual(seed.convert_old_fees(), 1)  # the tower carries a 5% fee from before
        agreement = OwnerAgreement.objects.get()
        self.assertEqual(agreement.fee_percent_collected, Decimal("5.00"))
        self.assertEqual(list(agreement.lines.values_list("property_id", flat=True)), [self.tower.pk])
        self.assertTrue(AuditEntry.objects.filter(object_repr=seed.MARKER).exists())
        agreement.status = "draft"
        agreement.save()
        agreement.delete()
        self.assertEqual(seed.convert_old_fees(), 0)  # never again, even after the agreement was deleted
        self.assertEqual(OwnerAgreement.objects.count(), 0)

    def test_company_owned_properties_get_no_agreement_from_old_fees(self):
        from properties.models import Owner as O

        Property.objects.filter(pk=self.tower.pk).update(owner=O.company())
        self.assertEqual(seed.convert_old_fees(), 0)
