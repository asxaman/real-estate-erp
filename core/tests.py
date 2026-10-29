"""Automated checks for users, the audit log and the data tools. Run:  python manage.py test"""
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from properties.models import Owner, Property, Unit
from tenants.models import Tenant

from . import audit
from .data_registry import run_erasers, run_seeders
from .admin_utils import avatar_html
from .models import MAX_PHOTO_BYTES, PHOTO_VALIDATORS, AuditEntry
from .safety import superuser_change_problem, user_delete_problem


class SuperuserSafety(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("boss", "boss@example.com", "Pass12345!")
        self.second = User.objects.create_superuser("second", "second@example.com", "Pass12345!")

    def test_cannot_demote_deactivate_or_unstaff_yourself(self):
        self.assertEqual(superuser_change_problem(self.boss, self.boss, is_superuser=False)[0], "is_superuser")
        self.assertEqual(superuser_change_problem(self.boss, self.boss, is_active=False)[0], "is_active")
        self.assertEqual(superuser_change_problem(self.boss, self.boss, is_staff=False)[0], "is_staff")

    def test_can_demote_another_superuser_while_one_remains(self):
        self.assertIsNone(superuser_change_problem(self.boss, self.second, is_superuser=False))
        self.assertIsNone(superuser_change_problem(self.boss, self.second, is_active=False))

    def test_unchanged_flags_are_fine(self):
        self.assertIsNone(superuser_change_problem(self.boss, self.boss, is_superuser=True, is_active=True, is_staff=True))
        self.assertIsNone(superuser_change_problem(self.boss, self.boss))

    def test_last_active_superuser_is_protected(self):
        self.second.is_active = False
        self.second.save()
        self.assertIsNotNone(superuser_change_problem(None, self.boss, is_superuser=False))
        self.assertIsNotNone(superuser_change_problem(None, self.boss, is_active=False))
        self.assertIsNotNone(user_delete_problem(None, self.boss))

    def test_delete_rules(self):
        self.assertIsNotNone(user_delete_problem(self.boss, self.boss))
        self.assertIsNone(user_delete_problem(self.boss, self.second))

    def test_admin_hides_delete_button(self):
        model_admin = admin.site._registry[User]
        request = RequestFactory().get("/")
        request.user = self.boss
        self.assertFalse(model_admin.has_delete_permission(request, self.boss))
        self.assertTrue(model_admin.has_delete_permission(request, self.second))
        staff = User.objects.create_user("clerk", password="Pass12345!", is_staff=True)
        request.user = staff
        self.assertFalse(model_admin.has_delete_permission(request, self.second))


class AuditTrail(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("boss", "boss@example.com", "Pass12345!")
        owner = Owner.objects.create(name="Audit Owner")
        self.tower = Property.objects.create(
            name="Audit Tower", code="AUD", property_type="residential_building", owner=owner,
            emirate="dubai", management_fee_type="percent", management_fee_percent=Decimal("5"),
        )

    def units(self):
        return AuditEntry.objects.filter(model_label="Unit")

    def test_creating_a_unit_is_logged_with_the_user(self):
        with audit.acting_as(self.boss):
            Unit.objects.create(property=self.tower, unit_number="101", floor="1", unit_type="studio")
        entry = self.units().get()
        self.assertEqual((entry.action, entry.username, entry.user_id), ("created", "boss", self.boss.pk))
        self.assertEqual(entry.object_repr, "AUD-101")

    def test_changing_the_rent_records_old_and_new_value(self):
        unit = Unit.objects.create(
            property=self.tower, unit_number="101", floor="1", unit_type="studio",
            asking_rent=Decimal("60000"),
        )
        before = self.units().count()
        with audit.acting_as(self.boss):
            unit.asking_rent = Decimal("90000")
            unit.save()
        entry = self.units().first()
        self.assertEqual(self.units().count(), before + 1)
        self.assertEqual(entry.action, "changed")
        self.assertEqual(entry.changes, {"asking_rent": ["60000", "90000"]})

    def test_saving_without_changes_logs_nothing(self):
        unit = Unit.objects.create(property=self.tower, unit_number="101", floor="1", unit_type="studio")
        before = self.units().count()
        unit = Unit.objects.get(pk=unit.pk)
        unit.save()
        self.assertEqual(self.units().count(), before)

    def test_deleting_is_logged(self):
        unit = Unit.objects.create(property=self.tower, unit_number="101", floor="1", unit_type="studio")
        unit.delete()
        self.assertEqual(self.units().first().action, "deleted")

    def test_no_user_is_recorded_as_system(self):
        Owner.objects.create(name="Someone")
        entry = AuditEntry.objects.filter(model_label="Owner", object_repr="Someone").get()
        self.assertEqual(entry.username, "(system)")
        self.assertIsNone(entry.user_id)

    def test_suppressed_block_logs_nothing(self):
        before = AuditEntry.objects.count()
        with audit.suppressed():
            Owner.objects.create(name="Quiet owner")
        self.assertEqual(AuditEntry.objects.count(), before)

    def test_password_changes_are_not_logged(self):
        before = AuditEntry.objects.filter(model_label="User").count()
        self.boss.set_password("Another12345!")
        self.boss.save()
        self.assertEqual(AuditEntry.objects.filter(model_label="User").count(), before)


class DataTools(TestCase):
    def test_seed_then_erase_keeps_company_owner_and_does_not_flood_the_log(self):
        run_seeders()
        self.assertEqual(Property.objects.count(), 12)
        self.assertEqual(Unit.objects.count(), 80)
        self.assertEqual(Owner.objects.count(), 4)  # three owners + Company Owned
        self.assertEqual(Tenant.objects.count(), 3)
        self.assertEqual(AuditEntry.objects.filter(model_label__in=["Unit", "Property", "Owner"]).count(), 0)

        run_erasers()
        self.assertEqual(Property.objects.count(), 0)
        self.assertEqual(Unit.objects.count(), 0)
        self.assertEqual(Tenant.objects.count(), 0)
        self.assertEqual(list(Owner.objects.values_list("is_company", flat=True)), [True])


class ProfilePicture(TestCase):
    def check(self, upload):
        for validator in PHOTO_VALIDATORS:
            validator(upload)

    def test_small_png_is_accepted(self):
        self.check(SimpleUploadedFile("me.PNG", b"x" * 100, content_type="image/png"))

    def test_wrong_type_is_refused(self):
        with self.assertRaises(ValidationError):
            self.check(SimpleUploadedFile("me.pdf", b"x" * 100))

    def test_big_file_is_refused(self):
        with self.assertRaises(ValidationError):
            self.check(SimpleUploadedFile("me.jpg", b"x" * (MAX_PHOTO_BYTES + 1)))

    def test_avatar_shows_initials_without_a_picture(self):
        user = User.objects.create_user("asad", first_name="Asad", last_name="Zaman")
        self.assertIn("AZ", avatar_html(user))
        self.assertIn("AS", avatar_html(User.objects.create_user("asif")))

    def test_sidebar_avatar_url(self):
        from .models import UserProfile

        user = User.objects.create_user("pic")
        self.assertEqual(user.avatar_url, "")  # no picture: Unfold shows the initial
        UserProfile.objects.create(user=user, photo="profile_photos/x.png")
        user = User.objects.get(pk=user.pk)
        self.assertTrue(user.avatar_url.endswith("profile_photos/x.png"))


class ToastMessages(TestCase):
    """Messages become pop-ups: the page gets no message bar, and the middleware adds the data."""

    def test_messages_move_into_the_page_script(self):
        from django.contrib import messages
        from django.contrib.messages.storage.session import SessionStorage
        from django.http import HttpResponse
        from django.test import RequestFactory

        from .toasts import ToastMiddleware, toast_messages

        request = RequestFactory().get("/")
        request.session = self.client.session
        request._messages = SessionStorage(request)
        from django.utils.html import format_html

        messages.success(request, 'The unit "A<b>" was added.')
        messages.success(request, format_html('The unit <a href="{}">{}</a> was added.', "/admin/x/1/", "SHJ3-302"))
        messages.success(request, format_html('Bad <a href="{}">{}</a> <b>bold</b>', "//evil.example", "link"))

        context = toast_messages(request)
        self.assertEqual(context["messages"], [])  # nothing left for Unfold to draw as a bar

        def view(req):
            return HttpResponse("<html><body>page</body></html>")

        response = ToastMiddleware(view)(request)
        html = response.content.decode()
        self.assertIn("window.__erpToasts=", html)
        self.assertIn("was added", html)
        self.assertNotIn("<b>", html)  # escaped inside the script
        self.assertNotIn("<a href", html)  # the link is sent as data, not as raw HTML
        self.assertIn('"href": "/admin/x/1/"', html)  # a link inside the site is kept
        self.assertNotIn("evil.example", html)  # a link to another site is dropped
        self.assertLess(html.index("window.__erpToasts"), html.index("</body>"))


class FileWidget(TestCase):
    def test_buttons_are_rendered(self):
        from .widgets import FileActionsWidget

        class Stub:
            name = "owner_photos/abc.jpg"
            url = "/media/owner_photos/abc.jpg"

        widget = FileActionsWidget(preview=True)
        widget.is_required = False
        html = widget.render("photo", Stub(), attrs={"id": "id_photo"})
        for needle in ("data-erp-view", "data-erp-edit", "data-erp-delete", "/media/owner_photos/abc.jpg",
                       'name="photo-clear"'):
            self.assertIn(needle, html)
        self.assertNotIn("abc.jpg</span>", html)  # the random file name is not shown
        empty = widget.render("photo", None, attrs={"id": "id_photo"})
        self.assertIn("Upload", empty)
        self.assertNotIn("data-erp-view", empty)

    def test_clear_box_deletes_the_file(self):
        from .widgets import FileActionsWidget

        widget = FileActionsWidget()
        widget.is_required = False
        self.assertIs(widget.value_from_datadict({"photo-clear": "on"}, {}, "photo"), False)


class DateTimeFormats(TestCase):
    def tearDown(self):
        from . import formats

        formats.apply(formats.DEFAULT_DATE, formats.DEFAULT_TIME)

    def test_chosen_format_is_used_for_display_and_input(self):
        from datetime import date, datetime

        from django import forms
        from django.utils import formats as dj_formats

        from . import formats

        formats.apply("dmy_dash", "12h")
        self.assertEqual(dj_formats.date_format(date(2028, 6, 20)), "20-06-2028")
        self.assertEqual(formats.fmt_date(date(2028, 6, 20)), "20-06-2028")
        field = forms.DateField()
        self.assertEqual(field.clean("20-06-2028"), date(2028, 6, 20))
        self.assertEqual(field.clean("2028-06-20"), date(2028, 6, 20))  # ISO is always accepted
        self.assertEqual(dj_formats.get_format("DATE_INPUT_FORMATS")[0], "%d-%m-%Y")
        formats.apply("dmy_text", "24h")
        self.assertEqual(dj_formats.date_format(date(2028, 6, 20)), "20 Jun 2028")
        self.assertEqual(dj_formats.time_format(datetime(2028, 6, 20, 17, 30)), "17:30")

    def test_settings_page_saves_and_applies(self):
        from datetime import date

        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from . import formats
        from .models import SystemSettings

        admin_user = get_user_model().objects.create_superuser("boss5", "b5@example.com", "pw-12345-xyz")
        self.client.force_login(admin_user)
        url = reverse("admin:core_systemsettings_changelist")
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        change_url = reverse("admin:core_systemsettings_change", args=[1])
        response = self.client.post(change_url, {"date_format": "dmy_slash", "time_format": "12h"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SystemSettings.load().date_format, "dmy_slash")
        self.client.get(url)  # the next request picks the saved choice up
        self.assertEqual(formats.fmt_date(date(2028, 6, 20)), "20/06/2028")

    def test_only_superusers_and_one_row(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from .models import SystemSettings

        staff = get_user_model().objects.create_user("clerk", "c@example.com", "pw-12345-xyz", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("admin:core_systemsettings_change", args=[1])).status_code, 403)
        SystemSettings.load().delete()  # cannot be deleted
        self.assertEqual(SystemSettings.objects.count(), 1)
