from django.apps import AppConfig
from django.db.models.signals import post_migrate

from .models_constants import COMPANY_OWNER_NAME, floor_sort_key


def ensure_company_owner(sender, apps=None, using="default", **kwargs):
    """After `migrate`: create the built-in "Company Owned" owner and sort existing units."""
    if apps is None:
        return
    try:
        Owner = apps.get_model("properties", "Owner")
        Unit = apps.get_model("properties", "Unit")
    except LookupError:
        return

    # If the new columns are not there yet (migrations not created), do nothing.
    if "is_company" in {f.name for f in Owner._meta.get_fields()}:
        manager = Owner.objects.using(using)
        if not manager.filter(is_company=True).exists():
            manager.create(name=COMPANY_OWNER_NAME, is_company=True)

    if "floor_sort" in {f.name for f in Unit._meta.get_fields()}:
        for unit in Unit.objects.using(using).all():
            key = floor_sort_key(unit.floor)
            if unit.floor_sort != key:
                Unit.objects.using(using).filter(pk=unit.pk).update(floor_sort=key)


class PropertiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "properties"
    verbose_name = "Properties"

    def ready(self):
        # Register this module's sample data and eraser with the Data tools.
        from . import seed  # noqa: F401

        post_migrate.connect(ensure_company_owner, sender=self)
