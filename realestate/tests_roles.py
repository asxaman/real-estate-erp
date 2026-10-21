"""Checks for user roles: who may open which module."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from .roles import ROLES, ensure_roles


def make_user(name, role=None):
    user = get_user_model().objects.create_user(
        name, password="a-long-test-password-1", is_staff=True
    )
    if role:
        user.groups.add(Group.objects.get(name=role))
    return user


class RoleTests(TestCase):
    def open(self, user, name, *args):
        self.client.force_login(user)
        return self.client.get(reverse(f"admin:{name}", args=args)).status_code

    def test_roles_exist_after_migrate(self):
        for name in ROLES:
            self.assertTrue(Group.objects.filter(name=name).exists(), name)

    def test_accountant(self):
        user = make_user("amal", "Accountant")
        self.assertEqual(self.open(user, "realestate_cheque_changelist"), 200)
        self.assertEqual(self.open(user, "realestate_cheque_desk"), 200)
        self.assertEqual(self.open(user, "realestate_ownerstatement_add"), 200)
        self.assertEqual(self.open(user, "realestate_expense_add"), 200)
        self.assertEqual(self.open(user, "realestate_lease_add"), 403)
        self.assertEqual(self.open(user, "realestate_tenant_add"), 403)

    def test_leasing_agent(self):
        user = make_user("lina", "Leasing agent")
        self.assertEqual(self.open(user, "realestate_lease_add"), 200)
        self.assertEqual(self.open(user, "realestate_tenant_add"), 200)
        self.assertEqual(self.open(user, "realestate_ownerstatement_changelist"), 403)
        self.assertEqual(self.open(user, "realestate_expense_changelist"), 403)
        self.assertEqual(self.open(user, "realestate_vendor_changelist"), 403)

    def test_maintenance_coordinator(self):
        user = make_user("mo", "Maintenance coordinator")
        self.assertEqual(self.open(user, "realestate_maintenancerequest_add"), 200)
        self.assertEqual(self.open(user, "realestate_expense_changelist"), 200)
        self.assertEqual(self.open(user, "realestate_cheque_changelist"), 403)
        self.assertEqual(self.open(user, "realestate_lease_changelist"), 403)

    def test_viewer_can_look_but_not_add(self):
        user = make_user("vic", "Viewer (read only)")
        self.assertEqual(self.open(user, "realestate_lease_changelist"), 200)
        self.assertEqual(self.open(user, "realestate_lease_add"), 403)
        self.assertEqual(self.open(user, "realestate_cheque_add"), 403)

    def test_user_without_a_role_sees_nothing(self):
        user = make_user("nobody")
        self.assertEqual(self.open(user, "index"), 200)
        self.assertEqual(self.open(user, "realestate_lease_changelist"), 403)

    def test_dashboard_hides_what_you_cannot_open(self):
        self.client.force_login(make_user("lina", "Leasing agent"))
        page = self.client.get(reverse("admin:index"))
        self.assertContains(page, "+ New lease")
        self.assertNotContains(page, "+ New owner statement")
        self.assertNotContains(page, "Draft owner statements")

    def test_nobody_but_a_superuser_sees_users_or_demo_pages(self):
        user = make_user("amal", "Manager")
        self.assertEqual(self.open(user, "auth_user_changelist"), 403)
        self.assertEqual(self.open(user, "realestate_demorecord_changelist"), 403)

    def test_changes_to_roles_survive_but_reset_restores(self):
        group = Group.objects.get(name="Accountant")
        before = group.permissions.count()
        group.permissions.clear()
        ensure_roles()
        self.assertEqual(group.permissions.count(), 0)  # your edits are respected
        ensure_roles(reset=True)
        self.assertEqual(group.permissions.count(), before)
