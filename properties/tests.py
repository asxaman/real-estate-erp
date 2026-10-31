"""Automated checks of the business rules. Run with:  python manage.py test"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .admin import UnitAdminForm, UnitInlineForm
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.models import AuditEntry

from . import import_tools, owner_tools
from .models import (
    Owner, OwnerBankAccount, OwnerContact, OwnerDocument, Property, PropertyDocument, Unit, UnitDocument, UnitPhoto,
)
from .models_constants import floor_sort_key, iban_is_valid

T = Property.PropertyType


def make_owner(name="Test Owner", **kwargs):
    return Owner.objects.get_or_create(name=name, defaults=kwargs)[0]


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
            data={"property": prop.pk, "unit_number": " 7a ", "floor": "3", "unit_type": "studio",
                  "status": "vacant"}
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


class PropertyAddDefaults(TestCase):
    def test_new_property_defaults_to_company_owned(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        user = get_user_model().objects.create_superuser("boss2", "b2@example.com", "pw-12345-xyz")
        self.client.force_login(user)
        response = self.client.get(reverse("admin:properties_property_add"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["adminform"].form.initial["owner"], Owner.company().pk)


class PropertyUnitImport(TestCase):
    def _file(self, text):
        return SimpleUploadedFile("data.csv", text.encode("utf-8"))

    def setUp(self):
        self.owner = Owner.objects.create(name="Alpha")

    def test_good_property_file(self):
        props, errors = import_tools.parse_property_file(self._file(
            "Code,Name,Type,Owner,Emirate,Fee type,Fee percent\n"
            "RMT,Rimal Tower,Residential Building,,Dubai,,\n"
            "VIL1,Palm Villa,Villa,Alpha,Abu Dhabi,Percent,5\n"
        ))
        self.assertEqual(errors, [])
        self.assertEqual([p.code for p in props], ["RMT", "VIL1"])
        self.assertTrue(props[0].owner.is_company)
        self.assertEqual(props[1].status, "vacant")
        for p in props:
            p.save()
        self.assertEqual(Property.objects.count(), 2)

    def test_bad_property_rows(self):
        props, errors = import_tools.parse_property_file(self._file(
            "Code,Name,Type,Owner\n"
            "RMT,One,Residential Building,Nobody\n"
            "X,Two,Villa,Alpha\n"
            "ABC,Three,Spaceship,Alpha\n"
            "VIL1,Four,Villa,Alpha\n"
        ))
        self.assertEqual(len(errors), 4)  # unknown owner, bad code, bad type, missing fee
        self.assertEqual(Property.objects.count(), 0)

    def test_unit_file(self):
        Property.objects.create(
            name="Tower", code="TWR", property_type="residential_building", owner=Owner.company(),
        )
        units, errors = import_tools.parse_unit_file(self._file(
            "Property code,Unit number,Floor,Type,Furnished,Asking rent\n"
            "TWR,101,1,2BHK,Yes,\n"
            "TWR,102,1,Office,,\n"
            "TWR, 101 ,1,1BHK,,\n"
            "ZZZ,1,1,Studio,,\n"
        ))
        self.assertEqual(len(errors), 3)  # wrong type for building, duplicate number, unknown property
        units, errors = import_tools.parse_unit_file(self._file(
            "Property code,Unit number,Floor,Type,Furnished,Asking rent\n"
            'TWR, 101 ,1,2BHK,Yes,"95,000"\n'
        ))
        self.assertEqual(errors, [])
        self.assertEqual(units[0].unit_number, "101")
        self.assertTrue(units[0].furnished)
        self.assertEqual(units[0].asking_rent, Decimal("95000"))

    def test_import_pages_open(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        user = get_user_model().objects.create_superuser("boss3", "b3@example.com", "pw-12345-xyz")
        self.client.force_login(user)
        for model in ("owner", "property", "unit"):
            for suffix in ("", "_template"):
                response = self.client.get(reverse(f"admin:properties_{model}_import{suffix}"))
                self.assertEqual(response.status_code, 200, model + suffix)
            self.assertEqual(self.client.get(reverse(f"admin:properties_{model}_changelist")).status_code, 200)


class CleanupRules(TestCase):
    """Update 13: defaults, limits, document rules, inactive owners, automatic codes."""

    def test_new_unit_is_vacant(self):
        tower = building("TWR")
        self.assertEqual(unit(tower, "101").status, "vacant")

    def test_unit_import_without_status_is_vacant(self):
        Property.objects.create(
            name="Tower", code="TWR", property_type="residential_building", owner=Owner.company(),
        )
        units, errors = import_tools.parse_unit_file(SimpleUploadedFile(
            "u.csv", b"Property code,Unit number,Floor,Type\nTWR,101,1,Studio\n"
        ))
        self.assertEqual(errors, [])
        self.assertEqual(units[0].status, "vacant")

    def test_blank_unit_statuses_are_repaired_after_migrate(self):
        from .apps import ensure_company_owner
        from django.apps import apps

        tower = building("TWR")
        unit(tower, "101")
        Unit.objects.update(status="")
        ensure_company_owner(sender=None, apps=apps)
        self.assertEqual(Unit.objects.get().status, "vacant")

    def test_inactive_owner_needs_no_active_properties(self):
        owner = make_owner("Busy")
        prop = standalone(code="BUSY", owner=owner)
        prop.save()
        owner.is_active = False
        with self.assertRaises(ValidationError) as ctx:
            owner.full_clean()
        self.assertIn("is_active", ctx.exception.message_dict)
        prop.is_active = False
        prop.save()
        owner.full_clean()  # fine once its property is inactive too

    def test_limits(self):
        for field, value in [("year_built", 5), ("year_built", 9999), ("total_floors", 0), ("total_floors", 5000)]:
            tower = Property(
                name="T", code="LIM1", property_type=T.RESIDENTIAL_BUILDING, owner=Owner.company(),
                emirate="dubai", management_fee_type="", **{field: value},
            )
            with self.assertRaises(ValidationError, msg=f"{field}={value}") as ctx:
                tower.full_clean()
            self.assertIn(field, ctx.exception.message_dict)
        ok = Property(
            name="T", code="LIM2", property_type=T.RESIDENTIAL_BUILDING, owner=Owner.company(),
            emirate="dubai", year_built=2020, total_floors=30,
        )
        ok.full_clean()
        tower = building("AREA")
        for area in ("0", "-5"):
            with self.assertRaises(ValidationError) as ctx:
                Unit(property=tower, unit_number="1", floor="1", unit_type="studio",
                     area_sqft=Decimal(area)).full_clean()
            self.assertIn("area_sqft", ctx.exception.message_dict)

    def test_emirates_id_is_checked_and_tidied(self):
        owner = make_owner("Docs")
        future = timezone.localdate() + timedelta(days=400)
        doc = OwnerDocument(owner=owner, doc_type="emirates_id", number="784199012345671", expiry_date=future)
        doc.full_clean()
        self.assertEqual(doc.number, "784-1990-1234567-1")
        for bad in ("12345", "999-1990-1234567-1", "784-1990-12345-1", "ABC"):
            with self.assertRaises(ValidationError, msg=bad) as ctx:
                OwnerDocument(owner=owner, doc_type="emirates_id", number=bad, expiry_date=future).full_clean()
            self.assertIn("number", ctx.exception.message_dict)

    def test_important_documents_need_an_expiry_date(self):
        owner = make_owner("Docs")
        for kind in ("emirates_id", "passport", "trade_licence"):
            with self.assertRaises(ValidationError, msg=kind) as ctx:
                OwnerDocument(owner=owner, doc_type=kind, number="").full_clean()
            self.assertIn("expiry_date", ctx.exception.message_dict)
        OwnerDocument(owner=owner, doc_type="title_deed").full_clean()  # not needed here

    def _document_formset(self, owner, kinds):
        from django.forms import inlineformset_factory

        from .owner_admin import DocumentFormSet

        FormSet = inlineformset_factory(
            Owner, OwnerDocument, formset=DocumentFormSet,
            fields=("doc_type", "number", "expiry_date"), extra=0,
        )
        data = {
            "documents-TOTAL_FORMS": str(len(kinds)), "documents-INITIAL_FORMS": "0",
            "documents-MIN_NUM_FORMS": "0", "documents-MAX_NUM_FORMS": "1000",
        }
        for i, kind in enumerate(kinds):
            data[f"documents-{i}-doc_type"] = kind
            data[f"documents-{i}-expiry_date"] = "2031-01-01"
        return FormSet(data, instance=owner)

    def test_only_one_passport_per_owner(self):
        owner = make_owner("Docs")
        self.assertFalse(self._document_formset(owner, ["passport", "passport"]).is_valid())
        self.assertTrue(self._document_formset(owner, ["passport", "emirates_id"]).is_valid())
        self.assertTrue(self._document_formset(owner, ["other", "other"]).is_valid())

    def _flag_formset(self, formset_class, model, owner, rows, flag):
        from django.forms import inlineformset_factory

        prefix = model._meta.get_field("owner").remote_field.get_accessor_name()
        FormSet = inlineformset_factory(Owner, model, formset=formset_class, fields="__all__", extra=0)
        data = {
            f"{prefix}-TOTAL_FORMS": str(len(rows)), f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for i, row in enumerate(rows):
            for key, value in row.items():
                data[f"{prefix}-{i}-{key}"] = value
        return FormSet(data, instance=owner)

    def test_first_contact_and_first_bank_account_become_primary_and_default(self):
        from .owner_admin import BankFormSet, ContactFormSet

        owner = make_owner("Flags")
        contacts = self._flag_formset(
            ContactFormSet, OwnerContact, owner,
            [{"name": "A", "role": "owner", "language": "en"}, {"name": "B", "role": "owner", "language": "en"}],
            "is_primary",
        )
        self.assertTrue(contacts.is_valid(), contacts.errors)
        contacts.save()
        self.assertEqual(list(OwnerContact.objects.filter(owner=owner, is_primary=True).values_list("name", flat=True)), ["A"])

        banks = self._flag_formset(
            BankFormSet, OwnerBankAccount, owner,
            [{"account_holder": "A", "bank_name": "B", "iban": VALID_IBAN}], "is_default",
        )
        self.assertTrue(banks.is_valid(), banks.errors)
        banks.save()
        self.assertTrue(OwnerBankAccount.objects.get(owner=owner).is_default)

    def test_code_collision_is_retried(self):
        from unittest import mock

        first = make_owner("First")
        with mock.patch("properties.models.next_owner_code", side_effect=[first.code, "OWN-7777"]):
            second = Owner.objects.create(name="Second")
        self.assertEqual(second.code, "OWN-7777")


class OwnerStatsAndExports(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_superuser("boss8", "b8@example.com", "pw-12345-xyz")
        self.client.force_login(self.admin)
        self.owner = make_owner("Stats")
        active = Property.objects.create(
            name="Active", code="ACT1", property_type=T.RESIDENTIAL_BUILDING, owner=self.owner, emirate="dubai",
            management_fee_type="percent", management_fee_percent=Decimal("5"),
        )
        unit(active, "101", status="occupied")
        unit(active, "102")
        dormant = Property.objects.create(
            name="Dormant", code="OLD1", property_type=T.RESIDENTIAL_BUILDING, owner=self.owner, emirate="dubai",
            management_fee_type="percent", management_fee_percent=Decimal("5"), is_active=False,
        )
        unit(dormant, "201")
        self.active, self.dormant = active, dormant

    def test_owner_numbers_leave_out_inactive_properties(self):
        from django.contrib import admin
        from django.test import RequestFactory

        from .owner_admin import OwnerAdmin

        request = RequestFactory().get("/")
        request.user = self.admin
        row = OwnerAdmin(Owner, admin.site).get_queryset(request).get(pk=self.owner.pk)
        self.assertEqual((row._property_count, row._unit_count, row._occupied_units), (1, 2, 1))
        self.assertEqual((row._standalone, row._docs_expired, row._docs_soon), (0, 0, 0))

    def test_property_and_unit_export(self):
        from django.urls import reverse

        for model, pk, expected in [("property", self.active.pk, "ACT1"), ("unit", Unit.objects.first().pk, "101")]:
            url = reverse(f"admin:properties_{model}_changelist")
            for action in ("export_csv", "export_excel"):
                response = self.client.post(url, {"action": action, "index": 0, "_selected_action": [pk]})
                self.assertEqual(response.status_code, 200, f"{model} {action}")
                self.assertIn("attachment", response["Content-Disposition"])
            csv_text = self.client.post(url, {"action": "export_csv", "index": 0, "_selected_action": [pk]}).content.decode("utf-8-sig")
            self.assertIn(expected, csv_text)

    def test_exports_cannot_carry_formulas(self):
        from core.exports import safe_cell

        self.assertEqual(safe_cell("=1+1"), "'=1+1")
        self.assertEqual(safe_cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(safe_cell("-cmd"), "'-cmd")
        self.assertEqual(safe_cell("+971 50 123 4567"), "+971 50 123 4567")
        self.assertEqual(safe_cell("-250.50"), "-250.50")
        self.assertEqual(safe_cell("Normal"), "Normal")
        self.assertEqual(safe_cell(12), 12)

    def test_one_alert_for_many_documents(self):
        from django.urls import reverse

        today = timezone.localdate()
        for kind, days in [("passport", -5), ("emirates_id", 3), ("trade_licence", 10), ("poa", 12)]:
            OwnerDocument.objects.create(owner=self.owner, doc_type=kind, expiry_date=today + timedelta(days=days))
        response = self.client.get(reverse("admin:properties_owner_change", args=[self.owner.pk]))
        html = response.content.decode()
        self.assertIn("4 documents need attention", html)
        self.assertIn("and 1 more", html)
        self.assertEqual(html.count("need attention"), 1)


class PhoneLostZero(TestCase):
    def test_lost_zero_is_spotted(self):
        from .models_constants import lost_zero_problem

        for text in ("501234567", "21234567", "41234567"):
            self.assertTrue(lost_zero_problem(text), text)
        for text in ("0501234567", "+971 50 123 4567", "971501234567", "", "123456"):
            self.assertEqual(lost_zero_problem(text), "", text)

    def test_owner_import_refuses_it(self):
        owners, errors = owner_tools.parse_owner_file(SimpleUploadedFile(
            "o.csv", b"Name,Phone\nAli,501234567\nBo,0501234567\n"
        ))
        self.assertEqual(len(errors), 1)
        self.assertIn("leading zero", errors[0])
        self.assertEqual([o.name for o in owners], ["Bo"])


class OwnerTrnAndReason(TestCase):
    def test_trn_is_checked_and_tidied(self):
        owner = Owner(name="Taxed", trn="100-2003-0040-0003")
        owner.full_clean(exclude=["user", "photo"])
        self.assertEqual(owner.trn, "100200300400003")
        with self.assertRaises(ValidationError) as ctx:
            Owner(name="Taxed 2", trn="123").full_clean(exclude=["user", "photo"])
        self.assertIn("trn", ctx.exception.message_dict)

    def test_inactive_reason_is_kept_only_while_inactive(self):
        owner = Owner(name="Quiet", is_active=False, inactive_reason="Sold everything")
        owner.full_clean(exclude=["user", "photo"])
        self.assertEqual(owner.inactive_reason, "Sold everything")
        owner.is_active = True
        owner.full_clean(exclude=["user", "photo"])
        self.assertEqual(owner.inactive_reason, "")


class PropertyExtraFields(TestCase):
    def test_references_are_tidied_and_checked(self):
        prop = standalone("EXT1", title_deed_number="  12  / 2018 ", makani_number="12345 67890", community=" Al  Reem ")
        prop.full_clean()
        self.assertEqual(prop.title_deed_number, "12 / 2018")
        self.assertEqual(prop.makani_number, "1234567890")
        self.assertEqual(prop.community, "Al Reem")
        with self.assertRaises(ValidationError) as ctx:
            standalone("EXT2", makani_number="123").full_clean()
        self.assertIn("makani_number", ctx.exception.message_dict)

    def test_everything_new_is_optional(self):
        standalone("EXT3").full_clean()
        unit_ = Unit(property=building("EXT4"), unit_number="1", floor="1", unit_type="studio")
        unit_.full_clean()

    def test_inactive_reason_is_kept_only_while_inactive(self):
        prop = standalone("EXT5", is_active=False, inactive_reason="Sold")
        prop.full_clean()
        self.assertEqual(prop.inactive_reason, "Sold")
        prop.is_active = True
        prop.full_clean()
        self.assertEqual(prop.inactive_reason, "")

    def test_parking_and_view_limits(self):
        with self.assertRaises(ValidationError) as ctx:
            standalone("EXT6", parking_spaces=6000).full_clean()
        self.assertIn("parking_spaces", ctx.exception.message_dict)
        bad = Unit(property=building("EXT7"), unit_number="1", floor="1", unit_type="studio", view="moon")
        with self.assertRaises(ValidationError) as ctx:
            bad.full_clean()
        self.assertIn("view", ctx.exception.message_dict)
        good = Unit(property=bad.property, unit_number="1", floor="1", unit_type="studio", view="sea", parking_spaces=2)
        good.full_clean()


class PropertyAndUnitDocuments(TestCase):
    def test_expiry_rules(self):
        prop = building("DOC1")
        with self.assertRaises(ValidationError):
            PropertyDocument(property=prop, doc_type="insurance").full_clean(exclude=["file"])
        PropertyDocument(property=prop, doc_type="title_deed").full_clean(exclude=["file"])
        PropertyDocument(
            property=prop, doc_type="insurance", expiry_date=timezone.localdate() + timedelta(days=90)
        ).full_clean(exclude=["file"])
        unit_ = unit(prop)
        UnitDocument(unit=unit_, doc_type="floor_plan").full_clean(exclude=["file"])

    def test_expiry_status(self):
        prop = building("DOC2")
        today = timezone.localdate()
        for days, expected in [(-1, "expired"), (5, "soon"), (400, "valid")]:
            doc = PropertyDocument(property=prop, doc_type="insurance", expiry_date=today + timedelta(days=days))
            self.assertEqual(doc.expiry_status, expected)
        self.assertEqual(PropertyDocument(property=prop, doc_type="other").expiry_status, "none")

    def test_only_one_title_deed(self):
        from django.forms import inlineformset_factory

        from .owner_admin import DocumentFormSet

        prop = building("DOC3")
        formset_class = inlineformset_factory(
            Property, PropertyDocument, formset=DocumentFormSet, fields=("doc_type", "number"), extra=2
        )
        data = {
            "documents-TOTAL_FORMS": "2", "documents-INITIAL_FORMS": "0",
            "documents-MIN_NUM_FORMS": "0", "documents-MAX_NUM_FORMS": "1000",
            "documents-0-doc_type": "title_deed", "documents-1-doc_type": "title_deed",
        }
        formset = formset_class(data, instance=prop)
        self.assertFalse(formset.is_valid())
        self.assertIn("title deed", " ".join(formset.non_form_errors()).lower())
        data["documents-1-doc_type"] = "other"
        self.assertTrue(formset_class(data, instance=prop).is_valid())


class PropertyAndUnitPages(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_superuser("boss11", "b11@example.com", "pw-12345-xyz")
        self.client.force_login(self.admin)
        self.prop = building("PG1", community="Marina", makani_number="1234567890", parking_spaces=40)
        self.unit = unit(self.prop, "101", status="occupied", asking_rent=90000, view="sea", parking_spaces=1)
        self.villa = standalone("PG2", is_active=False, inactive_reason="Being sold")
        self.villa.save()

    def test_pages_open(self):
        from django.urls import reverse

        for name, args in [
            ("properties_property_changelist", []), ("properties_property_add", []),
            ("properties_property_change", [self.prop.pk]), ("properties_property_change", [self.villa.pk]),
            ("properties_unit_changelist", []), ("properties_unit_add", []),
            ("properties_unit_change", [self.unit.pk]),
        ]:
            response = self.client.get(reverse(f"admin:{name}", args=args))
            self.assertEqual(response.status_code, 200, name)

    def test_headers_and_tabs(self):
        from django.urls import reverse

        page = self.client.get(reverse("admin:properties_property_change", args=[self.prop.pk])).content.decode()
        for text in ("erp-owner-header", "Asking rent, all units", "Marina", "Activity", "Documents", "Units"):
            self.assertIn(text, page, text)
        villa = self.client.get(reverse("admin:properties_property_change", args=[self.villa.pk])).content.decode()
        self.assertIn("Inactive: Being sold", villa)
        unit_page = self.client.get(reverse("admin:properties_unit_change", args=[self.unit.pk])).content.decode()
        for text in ("erp-owner-header", "PG1-101", "Photos", "Documents", "Activity"):
            self.assertIn(text, unit_page, text)

    def test_expiring_document_shows_a_warning(self):
        from django.urls import reverse

        PropertyDocument.objects.create(
            property=self.prop, doc_type="insurance", expiry_date=timezone.localdate() + timedelta(days=3)
        )
        page = self.client.get(reverse("admin:properties_property_change", args=[self.prop.pk])).content.decode()
        self.assertIn("expires on", page)
        self.assertIn("expiring soon", page)

    def test_history_is_linked_to_the_property_and_unit(self):
        from core.audit import acting_as

        with acting_as(self.admin):
            PropertyDocument.objects.create(property=self.prop, doc_type="other")
            doc = UnitDocument.objects.create(unit=self.unit, doc_type="other")
            UnitPhoto.objects.create(unit=self.unit, image="unit_photos/x.jpg")
            self.unit.asking_rent = 95000
            self.unit.save()
        mine = AuditEntry.objects.filter(related_property=self.prop)
        self.assertTrue(mine.filter(model_label="Document").exists())
        self.assertTrue(mine.filter(model_label="Unit", action="changed").exists())
        self.assertTrue(mine.filter(model_label="Photo").exists())
        self.assertEqual(AuditEntry.objects.filter(related_unit=self.unit).count(), mine.filter(related_unit=self.unit).count())
        self.assertTrue(AuditEntry.objects.filter(related_unit=self.unit, object_id=str(doc.pk)).exists())
        # property history also shows up on the owner's Activity tab
        self.assertTrue(AuditEntry.objects.filter(related_owner=self.prop.owner, model_label="Photo").exists())

    def test_old_history_is_attached_after_migrate(self):
        from django.apps import apps

        from .apps import _link_old_history

        entry = AuditEntry.objects.create(
            action="changed", model_label="Unit", object_id=str(self.unit.pk), object_repr="x", username="old"
        )
        other = AuditEntry.objects.create(
            action="changed", model_label="Property", object_id=str(self.prop.pk), object_repr="y", username="old"
        )
        _link_old_history(apps, "default")
        entry.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((entry.related_unit_id, entry.related_property_id), (self.unit.pk, self.prop.pk))
        self.assertEqual(other.related_property_id, self.prop.pk)

    def test_new_fields_are_exported(self):
        from django.urls import reverse

        url = reverse("admin:properties_property_changelist")
        text = self.client.post(
            url, {"action": "export_csv", "index": 0, "_selected_action": [self.prop.pk]}
        ).content.decode("utf-8-sig")
        self.assertIn("Makani no.", text)
        self.assertIn("1234567890", text)
        url = reverse("admin:properties_unit_changelist")
        text = self.client.post(
            url, {"action": "export_csv", "index": 0, "_selected_action": [self.unit.pk]}
        ).content.decode("utf-8-sig")
        self.assertIn("Sea", text)

    def test_deleting_a_unit_removes_its_documents_and_photos(self):
        UnitDocument.objects.create(unit=self.unit, doc_type="other")
        UnitPhoto.objects.create(unit=self.unit, image="unit_photos/x.jpg")
        self.unit.delete()
        self.assertEqual((UnitDocument.objects.count(), UnitPhoto.objects.count()), (0, 0))


class UpdateExistingImport(TestCase):
    def _file(self, text, name="data.csv"):
        return SimpleUploadedFile(name, text.encode("utf-8"))

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_superuser("boss12", "b12@example.com", "pw-12345-xyz")
        self.client.force_login(self.admin)
        self.owner = Owner.objects.create(name="Alpha", phone="+971 50 111 2222")
        self.prop = Property.objects.create(
            name="Tower", code="TWR", property_type=T.RESIDENTIAL_BUILDING, owner=Owner.company(),
            emirate="dubai", address="Old road",
        )
        self.unit = Unit.objects.create(
            property=self.prop, unit_number="101", floor="1", unit_type="studio", status="occupied", asking_rent=90000,
        )

    def test_without_the_tick_an_existing_property_is_refused(self):
        _, errors = import_tools.parse_property_file(self._file("Code,Name,Type\nTWR,Tower,Residential Building\n"))
        self.assertEqual(len(errors), 1)

    def test_property_update_changes_only_filled_cells(self):
        props, errors = import_tools.parse_property_file(
            self._file("Code,Address,Makani no.,Name\nTWR,New road,1234567890,\n"), update=True
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].pk, self.prop.pk)
        props[0].save()
        self.prop.refresh_from_db()
        self.assertEqual((self.prop.address, self.prop.makani_number), ("New road", "1234567890"))
        self.assertEqual((self.prop.name, self.prop.emirate), ("Tower", "dubai"))  # untouched

    def test_update_can_still_create_new_properties(self):
        props, errors = import_tools.parse_property_file(self._file(
            "Code,Name,Type,Community\nTWR,,,Marina\nNEW1,New one,Residential Building,JVC\n"
        ), update=True)
        self.assertEqual(errors, [])
        self.assertEqual([bool(p.pk) for p in props], [True, False])
        self.assertEqual(props[1].community, "JVC")

    def test_new_row_still_needs_a_type_when_updating(self):
        _, errors = import_tools.parse_property_file(self._file("Code,Name\nNEW2,Nameless type\n"), update=True)
        self.assertEqual(len(errors), 1)
        self.assertIn("Type is required", errors[0])

    def test_unit_update(self):
        units, errors = import_tools.parse_unit_file(self._file(
            "Property code,Unit number,Asking rent,View,Parking spaces\nTWR, 101 ,95000,Sea,2\n"
        ), update=True)
        self.assertEqual(errors, [])
        units[0].save()
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.asking_rent, self.unit.view, self.unit.parking_spaces), (95000, "sea", 2))
        self.assertEqual(self.unit.status, "occupied")  # not in the file, so unchanged
        self.assertEqual(Unit.objects.count(), 1)

    def test_owner_update(self):
        owners, errors = owner_tools.parse_owner_file(
            self._file("Name,Email,TRN\n alpha ,a@example.com,100-2003-0040-0003\n"), update=True
        )
        self.assertEqual(errors, [])
        owners[0].save()
        self.owner.refresh_from_db()
        self.assertEqual((self.owner.email, self.owner.trn, self.owner.phone), ("a@example.com", "100200300400003", "+971 50 111 2222"))
        _, errors = owner_tools.parse_owner_file(self._file("Name,Email\nCompany Owned,x@example.com\n"), update=True)
        self.assertEqual(len(errors), 1)

    def test_import_page_has_the_tick_and_reports_counts(self):
        from django.contrib.messages import get_messages
        from django.urls import reverse

        url = reverse("admin:properties_property_import")
        page = self.client.get(url).content.decode()
        self.assertIn("Update existing records", page)
        response = self.client.post(url, {
            "file": self._file("Code,Address\nTWR,Second road\n"), "update_existing": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("Imported 0 new and updated 1 properties.", [str(m) for m in get_messages(response.wsgi_request)])
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.address, "Second road")

    def test_error_report_data_is_on_the_page(self):
        from django.urls import reverse

        response = self.client.post(reverse("admin:properties_property_import"), {
            "file": self._file("Code,Name,Type\nBAD!,One,Villa\n"),
        })
        html = response.content.decode()
        self.assertIn("Download error report", html)
        self.assertIn('id="erp-import-errors"', html)


class BulkActions(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_superuser("boss13", "b13@example.com", "pw-12345-xyz")
        self.client.force_login(self.admin)

    def run_action(self, model, action, pks, **extra):
        from django.urls import reverse

        data = {"action": action, "index": 0, "_selected_action": pks, **extra}
        return self.client.post(reverse(f"admin:properties_{model}_changelist"), data)

    def test_owners_activate_and_deactivate_with_rules(self):
        busy = make_owner("Busy")
        Property.objects.create(
            name="P", code="BSY", property_type=T.VILLA, owner=busy, emirate="dubai", status="vacant",
            management_fee_type="percent", management_fee_percent=5,
        )
        free = make_owner("Free")
        response = self.run_action("owner", "make_inactive", [busy.pk, free.pk, Owner.company().pk])
        self.assertEqual(response.status_code, 302)
        busy.refresh_from_db(); free.refresh_from_db()
        self.assertTrue(busy.is_active)  # still has an active property
        self.assertFalse(free.is_active)
        self.assertTrue(Owner.company().is_active)
        self.run_action("owner", "make_active", [free.pk])
        free.refresh_from_db()
        self.assertTrue(free.is_active)

    def test_properties_cannot_be_activated_under_an_inactive_owner(self):
        owner = make_owner("Gone")
        prop = Property.objects.create(
            name="P", code="GON", property_type=T.VILLA, owner=owner, emirate="dubai", status="vacant",
            management_fee_type="percent", management_fee_percent=5,
        )
        self.run_action("property", "make_inactive", [prop.pk])
        prop.refresh_from_db()
        self.assertFalse(prop.is_active)
        Owner.objects.filter(pk=owner.pk).update(is_active=False)
        self.run_action("property", "make_active", [prop.pk])
        prop.refresh_from_db()
        self.assertFalse(prop.is_active)  # refused
        Owner.objects.filter(pk=owner.pk).update(is_active=True)
        self.run_action("property", "make_active", [prop.pk])
        prop.refresh_from_db()
        self.assertTrue(prop.is_active)

    def _props(self):
        old = make_owner("Old Owner")
        props = [
            Property.objects.create(
                name=f"P{n}", code=f"CH{n}", property_type=T.VILLA, owner=old, emirate="dubai", status="vacant",
                management_fee_type="percent", management_fee_percent=5,
            )
            for n in (1, 2)
        ]
        return old, props

    def test_change_owner_shows_a_page_first(self):
        old, props = self._props()
        response = self.run_action("property", "change_owner", [p.pk for p in props])
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Move properties", html)
        self.assertIn("CH1", html)
        Owner.objects.filter(pk=old.pk).update(is_active=False)
        self.assertNotIn("Old Owner</option>", self.run_action("property", "change_owner", [props[0].pk]).content.decode())

    def test_change_owner_moves_everything_or_nothing(self):
        old, props = self._props()
        new = make_owner("New Owner")
        ids = [p.pk for p in props]
        # no fee for a managed owner is fine here because the current fee is kept
        response = self.run_action("property", "change_owner", ids, apply="1", owner=new.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(Property.objects.filter(pk__in=ids).values_list("owner_id", flat=True)), {new.pk})
        # moving to the company clears the fee
        self.run_action("property", "change_owner", ids, apply="1", owner=Owner.company().pk)
        prop = Property.objects.get(pk=ids[0])
        self.assertTrue(prop.is_company_owned)
        self.assertEqual(prop.management_fee_type, "")
        # company-owned properties have no fee, so moving them to a managed owner needs one: nothing moves
        response = self.run_action("property", "change_owner", ids, apply="1", owner=new.pk)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nothing was changed", response.content.decode())
        self.assertEqual(set(Property.objects.filter(pk__in=ids).values_list("owner_id", flat=True)), {Owner.company().pk})
        # with a fee it works
        response = self.run_action(
            "property", "change_owner", ids, apply="1", owner=new.pk, fee_type="fixed", fee_fixed="6000"
        )
        self.assertEqual(response.status_code, 302)
        prop = Property.objects.get(pk=ids[0])
        self.assertEqual((prop.owner_id, prop.management_fee_type, prop.management_fee_fixed), (new.pk, "fixed", 6000))

    def test_inactive_owner_cannot_be_chosen(self):
        old, props = self._props()
        gone = make_owner("Gone Owner", is_active=False)
        response = self.run_action("property", "change_owner", [props[0].pk], apply="1", owner=gone.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Property.objects.get(pk=props[0].pk).owner_id, old.pk)
