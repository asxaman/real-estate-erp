"""Automated checks for users, the audit log and the data tools. Run:  python manage.py test"""
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from properties.models import Owner, Property, Unit

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
        self.assertEqual(AuditEntry.objects.filter(model_label__in=["Unit", "Property", "Owner"]).count(), 0)

        run_erasers()
        self.assertEqual(Property.objects.count(), 0)
        self.assertEqual(Unit.objects.count(), 0)
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
        self.assertIsNone(getattr(user, "avatar_url", None))  # no picture: Unfold shows the initial
        UserProfile.objects.create(user=user, photo="profile_photos/x.png")
        user = User.objects.get(pk=user.pk)
        self.assertTrue(user.avatar_url.endswith("profile_photos/x.png"))
