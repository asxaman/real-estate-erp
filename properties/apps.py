from django.apps import AppConfig
from django.db.models.signals import post_migrate

from .models_constants import COMPANY_OWNER_NAME


def ensure_company_owner(sender, apps=None, using="default", **kwargs):
    """After `migrate`, make sure the built-in "Company Owned" owner exists."""
    if apps is None:
        return
    try:
        Owner = apps.get_model("properties", "Owner")
    except LookupError:
        return
    # If the new column is not there yet (migrations not created), do nothing.
    if "is_company" not in {f.name for f in Owner._meta.get_fields()}:
        return
    manager = Owner.objects.using(using)
    if not manager.filter(is_company=True).exists():
        manager.create(name=COMPANY_OWNER_NAME, is_company=True)


class PropertiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "properties"
    verbose_name = "Properties"

    def ready(self):
        # Register this module's sample data and eraser with the Data tools.
        from . import seed  # noqa: F401

        post_migrate.connect(ensure_company_owner, sender=self)
