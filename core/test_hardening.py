"""Checks for the security and data-safety rules (update 15). Run:  python manage.py test"""
import os
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from properties.models import Owner, OwnerDocument, Property, Unit
from tenants.models import Tenant, TenantDocument

from . import audit, lockout, sysconf
from .data_registry import run_erasers
from .models import AuditEntry, LoginAttempt, SystemSettings
from .validators import ComplexPasswordValidator, validate_file_content

User = get_user_model()
_MEDIA = tempfile.mkdtemp()
PDF = b"%PDF-1.4 sample"


def tearDownModule():
    shutil.rmtree(_MEDIA, ignore_errors=True)


def staff(name, *codenames):
    user = User.objects.create_user(name, password="Good#Pass1", is_staff=True)
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


def run_settings(**env):
    """Import config.settings in a fresh process with these environment variables."""
    code = (
        "import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'; "
        "import config.settings as s; "
        "print(s.DEBUG, getattr(s,'SECURE_SSL_REDIRECT',False), getattr(s,'SESSION_COOKIE_SECURE',False), "
        "s.DATA_UPLOAD_MAX_NUMBER_FIELDS, s.DATABASES['default']['ENGINE'])"
    )
    full = {**os.environ, **env}
    return subprocess.run(
        [sys.executable, "-c", code], cwd=str(settings.BASE_DIR), env=full, capture_output=True, text=True
    )


class SettingsGuards(TestCase):
    def test_database_must_be_configured(self):
        result = run_settings(DJANGO_DEBUG="1", DB_NAME="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_NAME", result.stderr)

    def test_live_server_needs_a_real_secret_key(self):
        result = run_settings(DJANGO_DEBUG="0", DJANGO_SECRET_KEY="", DB_NAME="x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", result.stderr)
        result = run_settings(DJANGO_DEBUG="0", DJANGO_SECRET_KEY="short", DB_NAME="x", DJANGO_ALLOWED_HOSTS="a.example.com")
        self.assertNotEqual(result.returncode, 0)

    def test_live_server_needs_allowed_hosts(self):
        result = run_settings(DJANGO_DEBUG="0", DJANGO_SECRET_KEY="k" * 40, DB_NAME="x", DJANGO_ALLOWED_HOSTS="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_ALLOWED_HOSTS", result.stderr)

    def test_live_server_gets_strict_settings(self):
        result = run_settings(
            DJANGO_DEBUG="0", DJANGO_SECRET_KEY="k" * 40, DB_NAME="x", DJANGO_ALLOWED_HOSTS="erp.example.com",
            DJANGO_HTTPS="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        debug, redirect, secure, fields, engine = result.stdout.split()
        self.assertEqual((debug, redirect, secure), ("False", "True", "True"))
        self.assertGreaterEqual(int(fields), 20000)
        self.assertIn("postgresql", engine)

    def test_large_buildings_fit_in_one_form(self):
        self.assertGreaterEqual(settings.DATA_UPLOAD_MAX_NUMBER_FIELDS, 20000)


class PasswordRuleLowercase(TestCase):
    def test_small_letter_is_required(self):
        with self.assertRaises(ValidationError):
            ComplexPasswordValidator().validate("ALLCAPS1!")
        ComplexPasswordValidator().validate("Abcdef1!")


class LoginLockout(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("lockme", password="Good#Pass1", is_staff=True)

    def attempt(self, password):
        return self.client.post(reverse("admin:login"), {"username": "lockme", "password": password, "next": "/admin/"})

    def test_five_wrong_passwords_lock_the_account_until_it_is_unlocked(self):
        for _ in range(5):
            self.attempt("wrong")
        self.assertEqual(LoginAttempt.objects.filter(username="lockme").count(), 5)
        self.assertGreater(lockout.minutes_left("lockme", "127.0.0.1"), 0)
        self.attempt("Good#Pass1")  # right password, but locked
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(LoginAttempt.objects.filter(username="lockme").count(), 5)  # not extended
        call_command("unlock_logins")
        self.attempt("Good#Pass1")
        self.assertIn("_auth_user_id", self.client.session)

    def test_four_wrong_passwords_do_not_lock(self):
        for _ in range(4):
            self.attempt("wrong")
        self.assertEqual(lockout.minutes_left("lockme", "127.0.0.1"), 0)
        self.attempt("Good#Pass1")
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(LoginAttempt.objects.filter(username="lockme").count(), 0)  # cleared by the good login

    def test_old_failures_do_not_count(self):
        from datetime import timedelta

        from django.utils import timezone

        for _ in range(5):
            LoginAttempt.objects.create(username="lockme", ip="127.0.0.1")
        LoginAttempt.objects.update(created=timezone.now() - timedelta(minutes=16))
        self.assertEqual(lockout.minutes_left("lockme", "127.0.0.1"), 0)


@override_settings(MEDIA_ROOT=_MEDIA)
class ProtectedDownloads(TestCase):
    def setUp(self):
        owner = Owner.objects.create(name="Files Owner")
        self.doc = OwnerDocument.objects.create(
            owner=owner, doc_type="other", file=SimpleUploadedFile("scan.pdf", PDF)
        )
        self.url = "/" + settings.MEDIA_URL.lstrip("/") + self.doc.file.name

    def test_anonymous_visitors_get_nothing(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_staff_without_the_permission_get_nothing(self):
        self.client.force_login(staff("nobody", "view_tenant"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_staff_with_the_permission_can_open_the_file(self):
        self.client.force_login(staff("reader", "view_ownerdocument"))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), PDF)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", response["Cache-Control"])

    def test_superuser_can_open_and_odd_paths_are_refused(self):
        boss = User.objects.create_superuser("boss", "b@example.com", "Good#Pass1")
        self.client.force_login(boss)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        from .media import media_view

        request = RequestFactory().get("/")
        request.user = boss
        for bad in ("../secret.txt", "owner_documents/../../x", "unknown_folder/a.pdf", "owner_documents/missing.pdf"):
            with self.assertRaises(Exception, msg=bad):
                media_view(request, bad)

    def test_inactive_staff_are_refused(self):
        user = staff("gone", "view_ownerdocument")
        from .media import may_open

        self.assertTrue(may_open(user, "owner_documents/a.pdf"))
        user.is_active = False
        self.assertFalse(may_open(user, "owner_documents/a.pdf"))


class FileContentCheck(TestCase):
    def test_renamed_files_are_refused(self):
        with self.assertRaises(ValidationError):
            validate_file_content(SimpleUploadedFile("evil.pdf", b"MZ\x90\x00 program"))
        with self.assertRaises(ValidationError):
            validate_file_content(SimpleUploadedFile("pic.png", b"%PDF-1.4"))

    def test_real_files_pass(self):
        validate_file_content(SimpleUploadedFile("a.pdf", PDF))
        validate_file_content(SimpleUploadedFile("a.png", b"\x89PNG\r\n\x1a\n0000"))
        validate_file_content(SimpleUploadedFile("a.jpg", b"\xff\xd8\xff\xe0 data"))
        validate_file_content(SimpleUploadedFile("a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "))

    def test_document_field_uses_it(self):
        owner = Owner.objects.create(name="Check Owner")
        doc = OwnerDocument(owner=owner, doc_type="other", file=SimpleUploadedFile("evil.pdf", b"MZ\x90 not a pdf"))
        with self.assertRaises(ValidationError) as ctx:
            doc.full_clean()
        self.assertIn("file", ctx.exception.message_dict)


@override_settings(MEDIA_ROOT=_MEDIA)
class ReplacedFilesAreDeleted(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Replace Owner")
        self.doc = OwnerDocument.objects.create(
            owner=self.owner, doc_type="other", file=SimpleUploadedFile("one.pdf", PDF + b" one")
        )

    def test_replacing_a_file_removes_the_old_one(self):
        old = self.doc.file.name
        storage = self.doc.file.storage
        self.assertTrue(storage.exists(old))
        self.doc.file = SimpleUploadedFile("two.pdf", PDF + b" two")
        with self.captureOnCommitCallbacks(execute=True):
            self.doc.save()
        self.assertFalse(storage.exists(old))
        self.assertTrue(storage.exists(self.doc.file.name))

    def test_removing_a_file_deletes_it(self):
        old = self.doc.file.name
        storage = self.doc.file.storage
        self.doc.file = ""
        with self.captureOnCommitCallbacks(execute=True):
            self.doc.save()
        self.assertFalse(storage.exists(old))

    def test_saving_without_a_new_file_keeps_it(self):
        name = self.doc.file.name
        self.doc.notes = "changed"
        with self.captureOnCommitCallbacks(execute=True):
            self.doc.save()
        self.assertTrue(self.doc.file.storage.exists(name))


class ExportNeedsItsOwnPermission(TestCase):
    def setUp(self):
        Tenant.objects.create(name="Export Me", email="export@example.com", phone="+971 50 123 4567", nationality="India")
        Owner.objects.create(name="Export Owner")

    def export(self, user, url_name, action, model):
        self.client.force_login(user)
        pk = model.objects.exclude(**({"is_company": True} if model is Owner else {})).first().pk
        return self.client.post(
            reverse(url_name), {"action": action, "index": 0, "_selected_action": [pk]}, follow=True
        )

    def test_viewer_cannot_export_tenants(self):
        response = self.export(staff("viewer", "view_tenant"), "admin:tenants_tenant_changelist", "export_csv", Tenant)
        self.assertNotIn("attachment", response.get("Content-Disposition", ""))

    def test_exporter_can_export_tenants(self):
        response = self.export(staff("exporter", "view_tenant", "export_tenant"), "admin:tenants_tenant_changelist", "export_csv", Tenant)
        self.assertIn("attachment", response["Content-Disposition"])

    def test_viewer_cannot_export_owners(self):
        response = self.export(staff("ownerview", "view_owner"), "admin:properties_owner_changelist", "export_excel", Owner)
        self.assertNotIn("attachment", response.get("Content-Disposition", ""))

    def test_expiring_documents_download_needs_an_export_permission(self):
        url = reverse("expiring_documents") + "?export=csv"
        self.client.force_login(staff("lookonly", "view_tenantdocument"))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(staff("lister", "view_tenantdocument", "export_tenant"))
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_back_office_roles_get_the_permission_after_migrate(self):
        from django.apps import apps
        from django.contrib.auth.models import Group

        from properties.apps import grant_export_permissions
        from tenants.apps import grant_export_permission

        manager, _ = Group.objects.get_or_create(name="Property Manager")
        agent, _ = Group.objects.get_or_create(name="Leasing Agent")
        grant_export_permissions(sender=None, apps=apps)
        grant_export_permission(sender=None, apps=apps)
        self.assertTrue(manager.permissions.filter(codename="export_tenant").exists())
        self.assertTrue(manager.permissions.filter(codename="export_owner").exists())
        self.assertFalse(agent.permissions.filter(codename__startswith="export_").exists())


class ImportUpdateNeedsChangePermission(TestCase):
    def upload(self, user, update):
        self.client.force_login(user)
        data = {"file": SimpleUploadedFile("o.csv", b"Name,Phone\nAlpha,+971 55 999 0000\n")}
        if update:
            data["update_existing"] = "on"
        return self.client.post(reverse("admin:properties_owner_import"), data)

    def setUp(self):
        self.owner = Owner.objects.create(name="Alpha", phone="+971 50 123 4567")

    def test_add_only_user_cannot_update(self):
        response = self.upload(staff("adder", "add_owner", "view_owner"), update=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be used")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.phone, "+971 50 123 4567")

    def test_user_who_can_change_may_update(self):
        response = self.upload(staff("changer", "add_owner", "change_owner", "view_owner"), update=True)
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.phone, "+971 55 999 0000")


class ImportFileSize(TestCase):
    def test_huge_files_are_refused_before_reading(self):
        from properties import owner_tools

        big = SimpleUploadedFile("big.csv", b"Name\n" + b"a" * (5 * 1024 * 1024 + 1))
        owners, errors = owner_tools.parse_owner_file(big)
        self.assertEqual(owners, [])
        self.assertIn("larger than 5 MB", errors[0])

    def test_semicolon_csv_is_still_read(self):
        from properties import owner_tools

        text = "Name;Phone;Notes\nAlpha Co;+971 50 123 4567;one, two, three, four\n".encode()
        owners, errors = owner_tools.parse_owner_file(SimpleUploadedFile("o.csv", text))
        self.assertEqual(errors, [])
        self.assertEqual(owners[0].notes, "one, two, three, four")


class AuditHidesPersonalNumbers(TestCase):
    def test_numbers_are_masked_and_dates_of_birth_hidden(self):
        tenant = Tenant.objects.create(
            name="Masked Person", email="masked@example.com", phone="+971 50 123 4567", nationality="India",
            emirates_id_number="784-1988-0000011-1", passport_number="P12345678", trn="100200300400003",
        )
        entry = AuditEntry.objects.filter(model_label="Tenant", object_id=str(tenant.pk)).get()
        text = str(entry.changes)
        for secret in ("784-1988", "P12345678", "100200300400003"):
            self.assertNotIn(secret, text)
        self.assertTrue(entry.changes["emirates_id_number"][1].startswith("****"))
        import datetime

        tenant.date_of_birth = datetime.date(1990, 5, 31)
        tenant.save()
        change = AuditEntry.objects.filter(model_label="Tenant", action="changed").first()
        self.assertEqual(change.changes["date_of_birth"], [None, "(hidden)"])

    def test_document_log_names_leave_out_the_number(self):
        tenant = Tenant.objects.create(name="Doc Person", email="doc@example.com", phone="+971 50 123 4567", nationality="India")
        TenantDocument.objects.create(
            tenant=tenant, doc_type="emirates_id", number="784-1988-0000011-1", expiry_date="2030-01-01"
        )
        entry = AuditEntry.objects.filter(model_label="Document").get()
        self.assertEqual(entry.object_repr, "Emirates ID")
        self.assertNotIn("784-1988", str(entry.changes))

    def test_old_entries_are_cleaned_after_migrate(self):
        from django.apps import apps

        old = AuditEntry.objects.create(
            username="x", action="created", model_label="Document", object_repr="Passport GB1234567",
            changes={"number": [None, "GB1234567"], "date_of_birth": [None, "1990-05-31"], "notes": [None, "keep"]},
        )
        audit.scrub_old_entries(sender=None, apps=apps)
        old.refresh_from_db()
        self.assertEqual(old.object_repr, "Passport")
        self.assertEqual(old.changes["number"], [None, "****4567"])
        self.assertEqual(old.changes["date_of_birth"], [None, "(hidden)"])
        self.assertEqual(old.changes["notes"], [None, "keep"])
        audit.scrub_old_entries(sender=None, apps=apps)  # running it twice changes nothing more
        old.refresh_from_db()
        self.assertEqual(old.changes["number"], [None, "****4567"])

    def test_changing_a_related_record_logs_both_names(self):
        a, b = Owner.objects.create(name="Old Boss"), Owner.objects.create(name="New Boss")
        prop = Property.objects.create(
            name="P", code="MOV", property_type="villa", owner=a, emirate="dubai", status="vacant",
            management_fee_type="percent", management_fee_percent=Decimal("5"),
        )
        prop.owner = b
        prop.save()
        entry = AuditEntry.objects.filter(model_label="Property", action="changed").first()
        self.assertEqual(entry.changes, {"owner": ["Old Boss", "New Boss"]})


class DevelopmentMode(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("devboss", "d@example.com", "Good#Pass1")
        self.client.force_login(self.boss)

    def tearDown(self):
        sysconf.refresh(None)

    def switch(self, on):
        row = SystemSettings.load()
        row.development_mode = on
        row.save()
        sysconf.refresh({name: getattr(row, name) for name in sysconf.DEFAULTS})

    def test_tools_work_while_on(self):
        self.client.post(reverse("data_tools"), {"action": "seed"})
        self.assertGreater(Property.objects.count(), 0)

    def test_tools_are_refused_when_off(self):
        self.switch(False)
        self.client.post(reverse("data_tools"), {"action": "seed"})
        self.assertEqual(Property.objects.count(), 0)
        Owner.objects.create(name="Keep me")
        self.client.post(reverse("data_tools"), {"action": "erase", "confirm_text": "ERASE"})
        self.assertTrue(Owner.objects.filter(name="Keep me").exists())
        self.assertContains(self.client.get(reverse("data_tools")), "switched off")

    def test_switching_off_disables_demo_accounts(self):
        demo = User.objects.create_user("demo.manager", password="Demo@12345", is_staff=True)
        row = SystemSettings.load()
        from .tests import valid_settings_post

        post = valid_settings_post()
        post.pop("development_mode")
        response = self.client.post(reverse("admin:core_systemsettings_change", args=[row.pk]), post)
        self.assertEqual(response.status_code, 302)
        demo.refresh_from_db()
        self.assertFalse(demo.is_active)
        self.assertTrue(User.objects.get(pk=self.boss.pk).is_active)

    def test_erase_also_clears_the_audit_log(self):
        Owner.objects.create(name="History")
        self.assertGreater(AuditEntry.objects.count(), 0)
        run_erasers()
        self.assertEqual(AuditEntry.objects.count(), 0)

    def test_dashboard_shows_the_reminder(self):
        self.assertContains(self.client.get(reverse("dashboard")), "Development mode is ON")
        self.switch(False)
        self.assertNotContains(self.client.get(reverse("dashboard")), "Development mode is ON")


class DatabaseRules(TestCase):
    def test_owner_names_are_unique_whatever_the_capitals(self):
        Owner.objects.create(name="Alpha Holdings")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Owner.objects.create(name="ALPHA HOLDINGS")

    def test_tenant_emails_are_unique_whatever_the_capitals(self):
        base = dict(phone="+971 50 123 4567", nationality="India")
        Tenant.objects.create(name="One", email="same@example.com", **base)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Tenant.objects.create(name="Two", email="SAME@example.com", **base)

    def test_form_message_is_still_the_friendly_one(self):
        Owner.objects.create(name="Alpha Holdings")
        with self.assertRaises(ValidationError) as ctx:
            Owner(name="alpha holdings").full_clean(exclude=["user", "photo"])
        self.assertEqual(ctx.exception.message_dict["name"], ["An owner with this name already exists."])

    def test_units_share_the_property_status_list(self):
        self.assertIs(Unit.Status, Property.Status)


class DeleteConfirmationPageStillHasItsMarkers(TestCase):
    """The delete pop-up (erp.js) reads Django's confirmation page; if a theme upgrade changes it, this fails."""

    def test_markers(self):
        boss = User.objects.create_superuser("deleter", "dl@example.com", "Good#Pass1")
        self.client.force_login(boss)
        owner = Owner.objects.create(name="Delete Me")
        OwnerDocument.objects.create(owner=owner, doc_type="other")
        html = self.client.get(reverse("admin:properties_owner_delete", args=[owner.pk])).content.decode()
        for marker in ("Summary", "Objects"):
            self.assertIn(marker, html)
        self.assertRegex(html, r"(?i)delete the")
