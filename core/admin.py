from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.urls import path
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import CompanySettings
from .views import DataToolsView

admin.site.unregister(User)
admin.site.unregister(Group)


class SuperuserOnlyMixin:
    """Only superusers may see or change this model."""

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


@admin.register(User)
class UserAdmin(SuperuserOnlyMixin, BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "first_name", "last_name", "email", "is_staff", "is_superuser", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")


@admin.register(Group)
class GroupAdmin(SuperuserOnlyMixin, BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(CompanySettings)
class CompanySettingsAdmin(ModelAdmin):
    list_display = ("company_name", "trade_license_number", "tax_registration_number", "emirate")
    fieldsets = (
        ("Company", {"fields": ("company_name", "legal_name", "trade_license_number")}),
        ("Tax", {"fields": ("tax_registration_number", "vat_rate")}),
        ("Contact", {"fields": ("address", "po_box", "emirate", "phone", "email", "website")}),
        ("Accounting defaults", {"fields": ("currency_code", "fiscal_year_start_month")}),
    )

    def has_add_permission(self, request):
        # only one company profile is allowed
        return request.user.is_superuser and not CompanySettings.objects.exists()

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        custom = [
            path(
                "data-tools/",
                self.admin_site.admin_view(DataToolsView.as_view(model_admin=self)),
                name="core_data_tools",
            )
        ]
        return custom + super().get_urls()
