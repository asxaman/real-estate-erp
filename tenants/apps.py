from django.apps import AppConfig
from django.db.models.signals import post_migrate


def fix_nationalities(sender, apps=None, using="default", **kwargs):
    """After `migrate`: turn old free-text nationalities ("Egyptian", "UAE") into country names."""
    if apps is None:
        return
    try:
        Tenant = apps.get_model("tenants", "Tenant")
    except LookupError:
        return
    from core import countries

    manager = Tenant.objects.using(using)
    for old in manager.exclude(nationality__in=countries.COUNTRIES).values_list("nationality", flat=True).distinct():
        new = countries.match(old)
        if new:
            manager.filter(nationality=old).update(nationality=new)


def grant_export_permission(sender, apps=None, using="default", **kwargs):
    """After `migrate`: the back-office roles may export tenants (the Leasing Agent may not)."""
    if apps is None:
        return
    try:
        Group = apps.get_model("auth", "Group")
        Permission = apps.get_model("auth", "Permission")
    except LookupError:
        return
    found = Permission.objects.using(using).filter(content_type__app_label="tenants", codename="export_tenant")
    for group in Group.objects.using(using).filter(name__in=("Property Manager", "Accountant")):
        group.permissions.add(*found)


class TenantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenants"
    verbose_name = "Tenants"

    def ready(self):
        from . import seed  # noqa: F401  (registers sample data and eraser)

        post_migrate.connect(fix_nationalities, sender=self)
        post_migrate.connect(grant_export_permission, sender=self)
