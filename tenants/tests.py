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
