from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import AuditEntry

from . import tenant_tools
from .models import Tenant, TenantContact, TenantDocument


class TenantRules(TestCase):
    def test_codes_are_assigned_in_order(self):
        a = Tenant.objects.create(name="Alpha")
        b = Tenant.objects.create(name="Beta")
        self.assertRegex(a.code, r"^TEN-\d{4}$")
        self.assertEqual(int(b.code[4:]), int(a.code[4:]) + 1)

    def test_same_name_is_allowed(self):
        Tenant.objects.create(name="Mohamed Ali")
        Tenant.objects.create(name="Mohamed Ali")
        self.assertEqual(Tenant.objects.count(), 2)

    def test_document_status_and_counts(self):
        tenant = Tenant.objects.create(name="Alpha")
        today = timezone.localdate()
        TenantDocument.objects.create(tenant=tenant, doc_type="visa", expiry_date=today - timedelta(days=2))
        TenantDocument.objects.create(tenant=tenant, doc_type="passport", expiry_date=today + timedelta(days=5))
        TenantDocument.objects.create(tenant=tenant, doc_type="emirates_id", expiry_date=today + timedelta(days=400))
        self.assertEqual(tenant.document_counts(), (1, 1))

    def test_audit_links_to_tenant(self):
        tenant = Tenant.objects.create(name="Alpha")
        TenantContact.objects.create(tenant=tenant, name="X", is_primary=True)
        self.assertTrue(AuditEntry.objects.filter(related_tenant_id=tenant.pk).count() >= 2)

    def test_contacts_deleted_with_tenant(self):
        tenant = Tenant.objects.create(name="Alpha")
        TenantContact.objects.create(tenant=tenant, name="X")
        tenant.delete()
        self.assertEqual(TenantContact.objects.count(), 0)


class TenantImport(TestCase):
    def _file(self, text):
        return SimpleUploadedFile("tenants.csv", text.encode("utf-8"))

    def test_good_file(self):
        tenants, errors = tenant_tools.parse_tenant_file(
            self._file(
                "Name,Type,Nationality,Email,Phone\n"
                "Ali,Individual,Egyptian,ali@example.com,+971 50 123 4567\n"
                "Firm LLC,Company,UAE,info@firm.example.com,+971 4 123 4567\n"
            )
        )
        self.assertEqual(errors, [])
        self.assertEqual(tenants[1].tenant_type, Tenant.TenantType.COMPANY)

    def test_bad_rows(self):
        _, errors = tenant_tools.parse_tenant_file(
            self._file(
                "Name,Type,Nationality,Email,Phone\n"
                "Ali,Individual,Egyptian,a@example.com,abc\n"
                "Bo,Alien,Egyptian,b@example.com,+971 50 123 4567\n"
                "Cy,Individual,,c@example.com,+971 50 123 4567\n"
                "Di,Individual,Indian,d@example.com,+971 50 123 4567\n"
                "Di,Individual,Indian,d@example.com,+971 50 123 4567\n"
            )
        )
        self.assertEqual(len(errors), 4)  # bad phone, bad type, missing nationality, duplicate row
        self.assertEqual(Tenant.objects.count(), 0)

    def test_columns_are_required(self):
        _, errors = tenant_tools.parse_tenant_file(self._file("Name,Phone\nAli,+971 50 123 4567\n"))
        self.assertTrue(errors)


class TenantRequiredFields(TestCase):
    def test_email_phone_and_nationality_are_required(self):
        from django.core.exceptions import ValidationError

        tenant = Tenant(name="Alpha")
        with self.assertRaises(ValidationError) as caught:
            tenant.full_clean(exclude=["photo", "code"])
        self.assertTrue({"email", "phone", "nationality"} <= set(caught.exception.message_dict))
        Tenant(
            name="Alpha", email="a@example.com", phone="+971 50 123 4567", nationality="Indian"
        ).full_clean(exclude=["photo", "code"])


class TenantPages(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("boss4", "b4@example.com", "pw-12345-xyz")
        cls.tenant = Tenant.objects.create(name="Alpha", phone="+971 50 123 4567")
        TenantDocument.objects.create(
            tenant=cls.tenant, doc_type="visa", number="V1", expiry_date=timezone.localdate() - timedelta(days=3)
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_pages_open(self):
        for name, args in [
            ("admin:tenants_tenant_changelist", []),
            ("admin:tenants_tenant_add", []),
            ("admin:tenants_tenant_change", [self.tenant.pk]),
            ("admin:tenants_tenant_import", []),
            ("admin:tenants_tenant_import_template", []),
        ]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_search_and_filters(self):
        url = reverse("admin:tenants_tenant_changelist")
        for query in ("?q=Alpha", "?q=V1", "?docs=expired", "?tenant_type__exact=individual"):
            self.assertEqual(self.client.get(url + query).status_code, 200, query)


def _tenant(name="Alpha", email="alpha@example.com", phone="+971 50 123 4567", **kwargs):
    return Tenant(name=name, email=email, phone=phone, nationality="Indian", **kwargs)


class DuplicateTenants(TestCase):
    def setUp(self):
        self.alpha = _tenant()
        self.alpha.full_clean(exclude=["photo", "code"])
        self.alpha.save()

    def _problems(self, tenant):
        from django.core.exceptions import ValidationError

        try:
            tenant.full_clean(exclude=["photo", "code"])
        except ValidationError as exc:
            return exc.message_dict
        return {}

    def test_same_email_is_refused_in_any_case(self):
        problems = self._problems(_tenant(name="Someone Else", email="ALPHA@example.com", phone="+971 55 999 0000"))
        self.assertIn("email", problems)
        self.assertIn(self.alpha.code, problems["email"][0])

    def test_same_name_and_phone_is_refused_however_the_phone_is_written(self):
        for phone in ("050 123 4567", "0501234567", "+971501234567", "00971 50 123 4567"):
            problems = self._problems(_tenant(name=" alpha ", email="other@example.com", phone=phone))
            self.assertIn("phone", problems, phone)

    def test_different_people_are_allowed(self):
        # same name, different email and phone
        self.assertEqual(self._problems(_tenant(email="x@example.com", phone="+971 55 111 2222")), {})
        # same phone (a family number), different name and email
        self.assertEqual(self._problems(_tenant(name="Beta", email="beta@example.com")), {})

    def test_a_tenant_is_not_a_duplicate_of_itself(self):
        again = Tenant.objects.get(pk=self.alpha.pk)
        self.assertEqual(self._problems(again), {})

    def test_importing_the_same_file_twice_is_refused(self):
        text = (
            "Name,Nationality,Email,Phone\n"
            "Omar,Jordanian,omar@example.com,+971 50 777 8888\n"
        ).encode("utf-8")
        tenants, errors = tenant_tools.parse_tenant_file(SimpleUploadedFile("t.csv", text))
        self.assertEqual(errors, [])
        for tenant in tenants:
            tenant.save()
        _, errors = tenant_tools.parse_tenant_file(SimpleUploadedFile("t.csv", text))
        self.assertEqual(len(errors), 1)
        self.assertIn("already", errors[0])

    def test_duplicates_inside_one_file_are_refused(self):
        _, errors = tenant_tools.parse_tenant_file(SimpleUploadedFile("t.csv", (
            "Name,Nationality,Email,Phone\n"
            "Omar,Jordanian,omar@example.com,+971 50 777 8888\n"
            "Omar Two,Jordanian,OMAR@example.com,+971 50 000 0000\n"
        ).encode("utf-8")))
        self.assertEqual(len(errors), 1)
        self.assertIn("Row 3", errors[0])

    def test_import_refuses_phone_that_lost_its_zero(self):
        tenants, errors = tenant_tools.parse_tenant_file(SimpleUploadedFile("t.csv", (
            "Name,Nationality,Email,Phone\n"
            "Omar,Jordanian,omar@example.com,501234567\n"
        ).encode("utf-8")))
        self.assertEqual(tenants, [])
        self.assertIn("leading zero", errors[0])


class TenantDocumentRules(TestCase):
    def setUp(self):
        self.tenant = _tenant()
        self.tenant.save()

    def test_emirates_id_and_expiry(self):
        from django.core.exceptions import ValidationError

        later = timezone.localdate() + timedelta(days=400)
        doc = TenantDocument(tenant=self.tenant, doc_type="emirates_id", number="784199012345671", expiry_date=later)
        doc.full_clean()
        self.assertEqual(doc.number, "784-1990-1234567-1")
        with self.assertRaises(ValidationError) as caught:
            TenantDocument(tenant=self.tenant, doc_type="emirates_id", number="123", expiry_date=later).full_clean()
        self.assertIn("number", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            TenantDocument(tenant=self.tenant, doc_type="visa", number="V1").full_clean()
        self.assertIn("expiry_date", caught.exception.message_dict)
        TenantDocument(tenant=self.tenant, doc_type="other").full_clean()

    def test_only_one_visa_in_the_documents_table(self):
        from django.forms import inlineformset_factory

        from properties.owner_admin import DocumentFormSet

        FormSet = inlineformset_factory(
            Tenant, TenantDocument, formset=DocumentFormSet,
            fields=("doc_type", "number", "expiry_date"), extra=0,
        )
        data = {
            "documents-TOTAL_FORMS": "2", "documents-INITIAL_FORMS": "0",
            "documents-MIN_NUM_FORMS": "0", "documents-MAX_NUM_FORMS": "1000",
            "documents-0-doc_type": "visa", "documents-0-expiry_date": "2031-01-01",
            "documents-1-doc_type": "visa", "documents-1-expiry_date": "2032-01-01",
        }
        self.assertFalse(FormSet(data, instance=self.tenant).is_valid())

    def test_first_tenant_contact_becomes_primary(self):
        from django.forms import inlineformset_factory

        from properties.owner_admin import ContactFormSet

        FormSet = inlineformset_factory(Tenant, TenantContact, formset=ContactFormSet, fields="__all__", extra=0)
        data = {
            "contacts-TOTAL_FORMS": "1", "contacts-INITIAL_FORMS": "0",
            "contacts-MIN_NUM_FORMS": "0", "contacts-MAX_NUM_FORMS": "1000",
            "contacts-0-name": "PRO Person", "contacts-0-role": "pro", "contacts-0-language": "en",
        }
        formset = FormSet(data, instance=self.tenant)
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.assertTrue(TenantContact.objects.get(tenant=self.tenant).is_primary)
