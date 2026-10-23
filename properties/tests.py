"""Automated checks of the business rules. Run with:  python manage.py test"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .admin import UnitAdminForm, UnitInlineForm
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.models import AuditEntry

from . import owner_tools
from .models import Owner, OwnerBankAccount, OwnerContact, OwnerDocument, Property, Unit
from .models_constants import floor_sort_key, iban_is_valid

T = Property.PropertyType


def make_owner(name="Test Owner", **kwargs):
    return Owner.objects.create(name=name, **kwargs)


def standalone(code="TST1", owner=None, **kwargs):
    """An unsaved villa that passes every rule (change it with keyword arguments)."""
    values = dict(
        name="Test property", code=code, property_type=T.VILLA, emirate="dubai",
        status="vacant", management_fee_type="percent", management_fee_percent=Decimal("5"),
    )
    values.update(kwargs)
    return Property(owner=owner or make_owner(f"Owner {code}"), **values)


def building(code="RMT", ptype=T.RESIDENTIAL_BUILDING, **kwargs):
    values = dict(
        name=f"Building {code}", code=code, property_type=ptype, emirate="dubai",
        management_fee_type="percent", management_fee_percent=Decimal("5"),
    )
    values.update(kwargs)
    return Property.objects.create(owner=make_owner(f"Owner {code}"), **values)


def unit(prop, number="101", floor="1", unit_type="studio", **kwargs):
    return Unit.objects.create(
        property=prop, unit_number=number, floor=floor, unit_type=unit_type, **kwargs
    )


class OwnerRules(TestCase):
    def test_company_owner_exists_once(self):
        self.assertEqual(Owner.objects.filter(is_company=True).count(), 1)

    def test_reserved_name_is_refused(self):
        for name in ["Company Owned", "company owned", "  COMPANY   OWNED "]:
            with self.assertRaises(ValidationError, msg=name):
                Owner(name=name).full_clean()

    def test_duplicate_name_is_refused_ignoring_case(self):
        make_owner("Khalifa Al Mansoori")
        with self.assertRaises(ValidationError):
            Owner(name="khalifa  al mansoori ").full_clean()

    def test_saving_an_owner_again_is_fine(self):
        owner = make_owner("Noura")
        owner.full_clean()

    def test_phone_format(self):
        Owner(name="Good phone", phone="+971 50 123 4567").full_clean()
        with self.assertRaises(ValidationError):
            Owner(name="Bad phone", phone="call me").full_clean()


class PropertyRules(TestCase):
    def test_bad_codes_are_refused(self):
        for code in ["ASDASDASDASD", "AB", "1ABC", "AB-C"]:
            with self.assertRaises(ValidationError, msg=code):
                standalone(code=code).full_clean()

    def test_code_is_stored_in_capitals(self):
        prop = standalone(code="rmt")
        prop.full_clean()
        prop.save()
        self.assertEqual(Property.objects.get(pk=prop.pk).code, "RMT")

    def test_company_owned_has_no_fee(self):
        prop = standalone(owner=Owner.company(), management_fee_percent=Decimal("7"))
        prop.full_clean()
        self.assertEqual(prop.management_fee_type, "")
        self.assertEqual(prop.management_fee_percent, 0)

    def test_other_owner_needs_a_fee(self):
        with self.assertRaises(ValidationError) as ctx:
            standalone(management_fee_type="").full_clean()
        self.assertIn("management_fee_type", ctx.exception.message_dict)
        with self.assertRaises(ValidationError) as ctx:
            standalone(management_fee_percent=Decimal("0")).full_clean()
        self.assertIn("management_fee_percent", ctx.exception.message_dict)

    def test_standalone_needs_a_status(self):
        with self.assertRaises(ValidationError) as ctx:
            standalone(status="").full_clean()
        self.assertIn("status", ctx.exception.message_dict)

    def test_building_has_no_rent_or_status(self):
        prop = standalone(property_type=T.RESIDENTIAL_BUILDING, annual_rent=Decimal("5000"))
        prop.full_clean()
        self.assertIsNone(prop.annual_rent)
        self.assertEqual(prop.status, "")

    def test_year_and_floors_cleared_for_warehouse(self):
        prop = standalone(property_type=T.WAREHOUSE, year_built=2000, total_floors=3)
        prop.full_clean()
        self.assertIsNone(prop.year_built)
        self.assertIsNone(prop.total_floors)

    def test_apartment_needs_a_type_and_valid_bathrooms(self):
        with self.assertRaises(ValidationError) as ctx:
            standalone(property_type=T.APARTMENT).full_clean()
        self.assertIn("apartment_type", ctx.exception.message_dict)
        with self.assertRaises(ValidationError) as ctx:
            standalone(property_type=T.APARTMENT, apartment_type="2br", bathrooms=11).full_clean()
        self.assertIn("bathrooms", ctx.exception.message_dict)
        standalone(property_type=T.APARTMENT, apartment_type="2br", bathrooms=2).full_clean()

    def test_apartment_fields_cleared_for_other_types(self):
        prop = standalone(apartment_type="2br", bathrooms=3)
        prop.full_clean()
        self.assertEqual(prop.apartment_type, "")
        self.assertIsNone(prop.bathrooms)

    def test_building_with_units_cannot_become_standalone(self):
        prop = building()
        unit(prop)
        prop.property_type = T.VILLA
        prop.status = "vacant"
        with self.assertRaises(ValidationError) as ctx:
            prop.full_clean()
        self.assertIn("property_type", ctx.exception.message_dict)

    def test_building_kind_cannot_switch_when_unit_types_do_not_fit(self):
        residential = building("RES")
        unit(residential, unit_type="studio")
        residential.property_type = T.COMMERCIAL_BUILDING
        with self.assertRaises(ValidationError):
            residential.full_clean()

        commercial = building("COM", ptype=T.COMMERCIAL_BUILDING)
        unit(commercial, unit_type="office")
        commercial.property_type = T.RESIDENTIAL_BUILDING
        with self.assertRaises(ValidationError):
            commercial.full_clean()

    def test_empty_building_can_switch_kind(self):
        prop = building("EMP")
        prop.property_type = T.COMMERCIAL_BUILDING
        prop.full_clean()

    def test_total_floors_cannot_drop_below_existing_units(self):
        prop = building("TWR", total_floors=10)
        unit(prop, "801", "8")
        unit(prop, "G01", "G")
        unit(prop, "B01", "B1")
        prop.total_floors = 5
        with self.assertRaises(ValidationError) as ctx:
            prop.full_clean()
        self.assertIn("total_floors", ctx.exception.message_dict)
        prop.total_floors = 8
        prop.full_clean()

    def test_inactive_owner_cannot_be_chosen_but_existing_owner_stays(self):
        dormant = make_owner("Dormant", is_active=False)
        with self.assertRaises(ValidationError) as ctx:
            standalone(owner=dormant).full_clean()
        self.assertIn("owner", ctx.exception.message_dict)

        prop = standalone(code="KEEP")
        prop.save()
        prop.owner.is_active = False
        prop.owner.save()
        prop = Property.objects.get(pk=prop.pk)
        prop.full_clean()  # unchanged owner is still allowed


class UnitRules(TestCase):
    def setUp(self):
        self.tower = building("TWR")

    def test_number_and_floor_are_normalised(self):
        new = Unit(property=self.tower, unit_number=" g01 ", floor="g", unit_type="studio")
        new.full_clean()
        self.assertEqual((new.unit_number, new.floor), ("G01", "G"))
        new.save()
        self.assertEqual(Unit.objects.get(pk=new.pk).unit_number, "G01")

    def test_duplicate_number_is_refused_ignoring_case(self):
        unit(self.tower, "A101")
        with self.assertRaises(ValidationError):
            Unit(property=self.tower, unit_number="a101", floor="1", unit_type="studio").full_clean()

    def test_same_number_in_another_building_is_fine(self):
        unit(self.tower, "101")
        other = building("OTH")
        Unit(property=other, unit_number="101", floor="1", unit_type="studio").full_clean()

    def test_floor_codes(self):
        for floor in ["0", "B", "P", "1000", "X1", "-1", " "]:
            with self.assertRaises(ValidationError, msg=repr(floor)):
                Unit(property=self.tower, unit_number="U1", floor=floor, unit_type="studio").full_clean()
        for floor in ["G", "LG", "b2", "P3", "12", "RF", "ph"]:
            Unit(property=self.tower, unit_number="U1", floor=floor, unit_type="studio").full_clean()

    def test_floor_cannot_exceed_total_floors(self):
        self.tower.total_floors = 5
        self.tower.save()
        with self.assertRaises(ValidationError) as ctx:
            Unit(property=self.tower, unit_number="U6", floor="6", unit_type="studio").full_clean()
        self.assertIn("floor", ctx.exception.message_dict)
        for floor in ["5", "B3", "RF"]:
            Unit(property=self.tower, unit_number="U5", floor=floor, unit_type="studio").full_clean()

    def test_unit_type_must_fit_the_building(self):
        with self.assertRaises(ValidationError) as ctx:
            Unit(property=self.tower, unit_number="U1", floor="1", unit_type="office").full_clean()
        self.assertIn("unit_type", ctx.exception.message_dict)
        offices = building("OFC", ptype=T.COMMERCIAL_BUILDING)
        with self.assertRaises(ValidationError):
            Unit(property=offices, unit_number="U1", floor="1", unit_type="studio").full_clean()
        Unit(property=offices, unit_number="U1", floor="1", unit_type="office").full_clean()

    def test_standalone_property_takes_no_units(self):
        villa = standalone(code="VIL1")
        villa.save()
        with self.assertRaises(ValidationError) as ctx:
            Unit(property=villa, unit_number="1", floor="1", unit_type="studio").full_clean()
        self.assertIn("property", ctx.exception.message_dict)

    def test_inactive_building_takes_no_new_units_but_old_ones_stay_editable(self):
        old = unit(self.tower, "101")
        self.tower.is_active = False
        self.tower.save()
        with self.assertRaises(ValidationError):
            Unit(property=self.tower, unit_number="102", floor="1", unit_type="studio").full_clean()
        old = Unit.objects.get(pk=old.pk)
        old.asking_rent = Decimal("50000")
        old.full_clean()

    def test_units_sort_by_floor(self):
        for number, floor in [("1", "PH"), ("2", "10"), ("3", "2"), ("4", "G"), ("5", "B1"), ("6", "RF")]:
            unit(self.tower, number, floor)
        floors = list(Unit.objects.filter(property=self.tower).values_list("floor", flat=True))
        self.assertEqual(floors, ["B1", "G", "2", "10", "RF", "PH"])

    def test_floor_sort_key(self):
        self.assertLess(floor_sort_key("B2"), floor_sort_key("B1"))
        self.assertLess(floor_sort_key("B1"), floor_sort_key("G"))
        self.assertLess(floor_sort_key("9"), floor_sort_key("10"))


class FormBehaviour(TestCase):
    def test_unit_form_offers_active_buildings_only(self):
        active = building("ACT")
        inactive = building("OLD", is_active=False)
        villa = standalone(code="VIL2")
        villa.save()
        choices = UnitAdminForm().fields["property"].queryset
        self.assertIn(active, choices)
        self.assertNotIn(inactive, choices)
        self.assertNotIn(villa, choices)

    def test_unit_form_normalises_the_number(self):
        prop = building("FRM")
        form = UnitAdminForm(
            data={"property": prop.pk, "unit_number": " 7a ", "floor": "3", "unit_type": "studio"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["unit_number"], "7A")

    def test_inline_units_table_has_no_long_help_texts(self):
        self.assertTrue(all(f.help_text == "" for f in UnitInlineForm().fields.values()))


VALID_IBAN = "AE070331234567890123456"


class OwnerProfileRules(TestCase):
    def test_codes_are_assigned_in_order(self):
        a = Owner.objects.create(name="Alpha")
        b = Owner.objects.create(name="Beta")
        self.assertRegex(a.code, r"^OWN-\d{4}$")
        self.assertEqual(int(b.code[4:]), int(a.code[4:]) + 1)
        self.assertEqual(Owner.company().code, "OWN-0000")

    def test_iban_check(self):
        self.assertTrue(iban_is_valid(VALID_IBAN))
        self.assertTrue(iban_is_valid("ae07 0331 2345 6789 0123 456"))
        self.assertFalse(iban_is_valid("AE070331234567890123457"))
        self.assertFalse(iban_is_valid("123"))

    def test_bad_iban_is_refused(self):
        owner = Owner.objects.create(name="Alpha")
        account = OwnerBankAccount(owner=owner, account_holder="A", bank_name="B", iban="AE0000")
        with self.assertRaises(ValidationError):
            account.full_clean()

    def test_document_expiry_status(self):
        owner = Owner.objects.create(name="Alpha")
        today = timezone.localdate()
        D = OwnerDocument.DocType
        cases = {
            "none": None,
            "expired": today - timedelta(days=1),
            "soon": today + timedelta(days=5),
            "valid": today + timedelta(days=365),
        }
        for expected, when in cases.items():
            doc = OwnerDocument(owner=owner, doc_type=D.PASSPORT, expiry_date=when)
            self.assertEqual(doc.expiry_status, expected)

    def test_document_counts(self):
        owner = Owner.objects.create(name="Alpha")
        OwnerDocument.objects.create(
            owner=owner, doc_type="passport", expiry_date=timezone.localdate() - timedelta(days=2)
        )
        self.assertEqual(owner.document_counts(), (1, 0))

    def test_audit_masks_iban_and_links_owner(self):
        owner = Owner.objects.create(name="Alpha")
        OwnerBankAccount.objects.create(
            owner=owner, account_holder="A", bank_name="B", iban=VALID_IBAN
        )
        entry = AuditEntry.objects.filter(model_label__icontains="bank").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.related_owner_id, owner.pk)
        self.assertNotIn(VALID_IBAN, str(entry.changes))
        self.assertIn("3456", str(entry.changes))

    def test_contacts_are_deleted_with_owner(self):
        owner = Owner.objects.create(name="Alpha")
        OwnerContact.objects.create(owner=owner, name="X", is_primary=True)
        owner.delete()
        self.assertEqual(OwnerContact.objects.count(), 0)


class OwnerImport(TestCase):
    def _file(self, text, name="owners.csv"):
        return SimpleUploadedFile(name, text.encode("utf-8"))

    def test_good_file(self):
        owners, errors = owner_tools.parse_owner_file(
            self._file("Name,Type,Email,Phone\nAli Test,Individual,ali@example.com,+971 50 123 4567\nFirm LLC,Company,,\n")
        )
        self.assertEqual(errors, [])
        self.assertEqual([o.name for o in owners], ["Ali Test", "Firm LLC"])
        self.assertEqual(owners[1].owner_type, Owner.OwnerType.COMPANY)

    def test_bad_rows_are_reported_and_nothing_is_saved(self):
        owners, errors = owner_tools.parse_owner_file(
            self._file("Name,Type,Phone\nAli,Individual,abc\nBo,Alien,\nAli,Individual,\n")
        )
        self.assertTrue(errors)
        self.assertEqual(Owner.objects.filter(name__in=["Ali", "Bo"]).count(), 0)

    def test_missing_name_column(self):
        _, errors = owner_tools.parse_owner_file(self._file("Email\nx@example.com\n"))
        self.assertTrue(errors)


class OwnerPages(TestCase):
    """The owner pages open without errors (list, add, change, import)."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        cls.admin = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-xyz")
        cls.owner = Owner.objects.create(name="Alpha", email="a@example.com", phone="+971 50 123 4567")
        OwnerDocument.objects.create(
            owner=cls.owner, doc_type="passport", expiry_date=timezone.localdate() - timedelta(days=3)
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_pages_open(self):
        from django.urls import reverse

        for name, args in [
            ("admin:properties_owner_changelist", []),
            ("admin:properties_owner_add", []),
            ("admin:properties_owner_change", [self.owner.pk]),
            ("admin:properties_owner_import", []),
            ("admin:properties_owner_import_template", []),
        ]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200, name)

    def test_search_filters_and_export(self):
        from django.urls import reverse

        url = reverse("admin:properties_owner_changelist")
        for query in ("?q=Alpha", "?q=passport", "?docs=expired", "?owner_type__exact=individual"):
            self.assertEqual(self.client.get(url + query).status_code, 200, query)
