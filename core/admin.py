"""
Admin setup with the Unfold theme.

Only superusers can see or manage Users and Roles (groups). That is how the
superuser creates other users, and it stops ordinary staff from giving
themselves extra permissions.
"""
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import UploadedFile
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.db.models import Count
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from . import formats, sysconf
from .admin_utils import TooltipHelpMixin, avatar_html
from .widgets import use_file_widget
from .models import PHOTO_VALIDATORS, AuditEntry, SystemSettings, UserProfile
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


class SimpleUserCreationForm(UserCreationForm):
    """Add-user form without the "password-based authentication" choice: a password is always set."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("usable_password", None)


class SimpleAdminPasswordChangeForm(AdminPasswordChangeForm):
    """Change-password form without the "password-based authentication" choice."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("usable_password", None)


class SafeUserChangeForm(UserChangeForm):
    """Refuses changes that would lock an administrator (or everyone) out."""

    current_user = None  # set per request in UserAdmin.get_form

    # Optional profile picture (saved to the user's profile in UserAdmin.save_model).
    # After saving: a new file, False (the Delete button was used), or the unchanged current picture.
    photo = forms.ImageField(
        label="Profile picture",
        required=False,
        validators=PHOTO_VALIDATORS,
        help_text="Optional. JPG, PNG or WebP.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        profile = getattr(self.instance, "profile", None) if self.instance.pk else None
        if profile is not None and profile.photo:
            self.initial["photo"] = profile.photo  # lets the control show the current picture
        use_file_widget(
            self, "photo", preview=True, round=True, accept="image/png,image/jpeg,image/webp"
        )

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
    add_form = SimpleUserCreationForm
    change_password_form = SimpleAdminPasswordChangeForm
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("username", "password1", "password2")}),)
    list_display = ("avatar", "username", "first_name", "last_name", "email", "is_staff", "is_active")
    list_display_links = ("avatar", "username")
    list_select_related = ("profile",)
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            "Personal info",
            {"fields": ("photo", "first_name", "last_name", "email")},
        ),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    @admin.display(description="")
    def avatar(self, obj):
        return avatar_html(obj, 32)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        photo = form.cleaned_data.get("photo")
        replace = isinstance(photo, UploadedFile)  # a new picture was chosen
        remove = photo is False  # the Delete button was used
        if not (replace or remove):
            return
        profile, _ = UserProfile.objects.get_or_create(user=obj)
        if profile.photo:  # replacing or removing: delete the old file
            profile.photo.delete(save=False)
            profile.photo = ""
        if replace:
            profile.photo = photo
        profile.save()

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


class SystemSettingsForm(forms.ModelForm):
    date_format = forms.ChoiceField(label="Date format", choices=formats.date_choices())
    time_format = forms.ChoiceField(label="Time format", choices=formats.time_choices())
    timezone = forms.ChoiceField(
        label="Time zone",
        choices=sysconf.TIMEZONES,
        help_text="Times are shown in this time zone.",
    )

    class Meta:
        model = SystemSettings
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Keep the theme's own text box (border, colours); only make it 3 lines high.
        self.fields["company_address"].widget.attrs["rows"] = 3


@admin.register(SystemSettings)
class SystemSettingsAdmin(SuperuserOnlyMixin, TooltipHelpMixin, ModelAdmin):
    """All system-wide settings in one place (superusers only), one tab per topic."""

    form = SystemSettingsForm
    compressed_fields = True
    fieldsets = (
        (
            "Company",
            {
                "classes": ["tab"],
                "description": "Your company's details. The name appears in the sidebar and the browser "
                "tab; the other details will be used on invoices, receipts and agreements.",
                "fields": ("company_name", "company_trn", ("company_phone", "company_email"), "company_address"),
            },
        ),
        (
            "Date and time",
            {
                "classes": ["tab"],
                "description": "Applies to the whole system: pages, lists, date pickers and what people "
                "type into date fields. Saved data is not changed.",
                "fields": ("date_format", "time_format", "timezone", "preview"),
            },
        ),
        (
            "Documents and files",
            {
                "classes": ["tab"],
                "fields": ("expiry_warning_days", "max_photo_mb", "max_document_mb"),
            },
        ),
        (
            "Notifications",
            {"classes": ["tab"], "fields": ("toast_seconds",)},
        ),
        ("Roles", {"classes": ["tab"], "fields": ("roles_panel",)}),
        ("Audit log", {"classes": ["tab"], "fields": ("audit_panel",)}),
        ("Data tools", {"classes": ["tab"], "fields": ("tools_panel",)}),
    )
    readonly_fields = ("preview", "roles_panel", "audit_panel", "tools_panel")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        SystemSettings.load()  # make sure the single record exists, even on a brand-new database
        return super().change_view(request, object_id, form_url, extra_context)

    def changelist_view(self, request, extra_context=None):
        # There is only one settings record, so go straight to it.
        obj = SystemSettings.load()
        return redirect(reverse("admin:core_systemsettings_change", args=[obj.pk]))

    @admin.display(description="Preview (saved choice)")
    def preview(self, obj):
        if obj is None or not obj.pk:
            return "-"
        try:
            now = timezone.now().astimezone(ZoneInfo(obj.timezone))
        except Exception:
            now = timezone.localtime()
        sample = formats.preview(obj.date_format, obj.time_format, now)
        return f"Date: {sample['date']}   |   Time: {sample['time']}   |   Both: {sample['both']}"

    # ---- tabs that lead to other administration pages --------------------------------------
    @staticmethod
    def _button(url, text, danger=False):
        cls = "erp-fbtn erp-fbtn-danger" if danger else "erp-fbtn"
        return format_html('<a class="{}" href="{}">{}</a>', cls, url, text)

    @admin.display(description="")
    def roles_panel(self, obj):
        roles = Group.objects.annotate(
            people=Count("user", distinct=True), rights=Count("permissions", distinct=True)
        ).order_by("name")
        rows = format_html_join(
            "",
            '<tr><td style="padding:8px 12px"><a href="{}" style="font-weight:600">{}</a></td>'
            '<td style="padding:8px 12px">{}</td><td style="padding:8px 12px">{}</td></tr>',
            (
                (reverse("admin:auth_group_change", args=[g.pk]), g.name, g.people, g.rights)
                for g in roles
            ),
        )
        table = (
            format_html(
                '<table style="width:100%;max-width:640px;border-collapse:collapse;margin-bottom:14px">'
                '<thead><tr style="text-align:left;opacity:.7"><th style="padding:8px 12px">Role</th>'
                '<th style="padding:8px 12px">Users</th><th style="padding:8px 12px">Permissions</th></tr></thead>'
                "<tbody>{}</tbody></table>",
                rows,
            )
            if roles
            else mark_safe("<p>No roles yet.</p>")
        )
        return format_html(
            '<div><p style="margin-bottom:12px;opacity:.8">Roles decide what each user may see and do. '
            "Create a role, tick its permissions, then give it to users.</p>{}"
            '<div style="display:flex;gap:8px;flex-wrap:wrap">{}{}</div></div>',
            table,
            self._button(reverse("admin:auth_group_changelist"), "Manage roles"),
            self._button(reverse("admin:auth_group_add"), "Add a role"),
        )

    @admin.display(description="")
    def audit_panel(self, obj):
        entries = AuditEntry.objects.all()[:8]
        rows = format_html_join(
            "",
            '<tr><td style="padding:8px 12px;white-space:nowrap">{}</td><td style="padding:8px 12px">{}</td>'
            '<td style="padding:8px 12px">{}</td><td style="padding:8px 12px">{}</td></tr>',
            (
                (formats.fmt_datetime(e.timestamp), e.username or "-", e.get_action_display(), e.object_repr)
                for e in entries
            ),
        )
        table = (
            format_html(
                '<table style="width:100%;max-width:860px;border-collapse:collapse;margin-bottom:14px">'
                '<thead><tr style="text-align:left;opacity:.7"><th style="padding:8px 12px">When</th>'
                '<th style="padding:8px 12px">Who</th><th style="padding:8px 12px">Action</th>'
                '<th style="padding:8px 12px">Record</th></tr></thead><tbody>{}</tbody></table>',
                rows,
            )
            if entries
            else mark_safe("<p>Nothing has been recorded yet.</p>")
        )
        return format_html(
            '<div><p style="margin-bottom:12px;opacity:.8">Who created, changed or deleted what. '
            "The latest entries are shown here.</p>{}{}</div>",
            table,
            self._button(reverse("admin:core_auditentry_changelist"), "Open the full audit log"),
        )

    @admin.display(description="")
    def tools_panel(self, obj):
        return format_html(
            '<div><p style="margin-bottom:12px;opacity:.8">Add sample data to try the system out, or erase '
            "all data to start fresh. <strong>Erasing cannot be undone</strong> - make a backup first.</p>"
            '<div style="display:flex;gap:8px;flex-wrap:wrap">{}</div></div>',
            self._button(reverse("data_tools"), "Open Data tools"),
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        sysconf.refresh(
            {name: getattr(obj, name) for name in sysconf.DEFAULTS}
        )  # takes effect at once on this server; other servers pick it up on their next request
