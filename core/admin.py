"""
Admin setup with the Unfold theme.

Only superusers can see or manage Users and Roles (groups). That is how the
superuser creates other users, and it stops ordinary staff from giving
themselves extra permissions.
"""
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import AuditEntry
from .safety import superuser_change_problem, user_delete_problem

admin.site.unregister(User)
admin.site.unregister(Group)


class SuperuserOnlyMixin:
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class SafeUserChangeForm(UserChangeForm):
    """Refuses changes that would lock an administrator (or everyone) out."""

    current_user = None  # set per request in UserAdmin.get_form

    def clean(self):
        cleaned = super().clean() or self.cleaned_data
        problem = superuser_change_problem(
            self.current_user,
            self.instance,  # still the saved version at this point
            is_superuser=cleaned.get("is_superuser"),
            is_active=cleaned.get("is_active"),
            is_staff=cleaned.get("is_staff"),
        )
        if problem:
            self.add_error(*problem)
        return cleaned


@admin.register(User)
class UserAdmin(SuperuserOnlyMixin, BaseUserAdmin, ModelAdmin):
    form = SafeUserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "first_name", "last_name", "email", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.current_user = request.user  # a fresh form class is built for every request
        return form

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        # No Delete button for yourself or for the last active superuser.
        return obj is None or user_delete_problem(request.user, obj) is None


@admin.register(Group)
class GroupAdmin(SuperuserOnlyMixin, BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(AuditEntry)
class AuditEntryAdmin(ModelAdmin):
    """Read-only audit log (superusers only). Nobody can edit or delete entries."""

    list_display = ("timestamp", "username", "show_action", "model_label", "object_repr", "what_changed")
    list_filter = ("action", "model_label", "username")
    search_fields = ("object_repr", "username")
    date_hierarchy = "timestamp"
    list_per_page = 50
    fields = ("timestamp", "username", "action", "model_label", "object_repr", "object_id", "changes_detail")
    readonly_fields = fields

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(
        description="Action",
        ordering="action",
        label={"Created": "success", "Changed": "info", "Deleted": "danger", "Data tool": "warning"},
    )
    def show_action(self, obj):
        return obj.get_action_display()

    @admin.display(description="What changed")
    def what_changed(self, obj):
        return obj.summary()

    @admin.display(description="Changes (old → new)")
    def changes_detail(self, obj):
        return format_html_join(
            mark_safe("<br>"), "<b>{}</b>: {} &rarr; {}", obj.change_lines()
        ) or "-"
