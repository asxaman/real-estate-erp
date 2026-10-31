from django.apps import AppConfig
from django.db.models.signals import post_migrate

import re

from .models_constants import COMPANY_OWNER_CODE, COMPANY_OWNER_NAME, floor_sort_key


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

    # Owner codes (OWN-0001 ...) and the company owner's type, for owners created earlier.
    owner_fields = {f.name for f in Owner._meta.get_fields()}
    if {"code", "owner_type"} <= owner_fields:
        manager = Owner.objects.using(using)
        manager.filter(is_company=True).update(owner_type="company")
        manager.filter(is_company=True, code__isnull=True).update(code=COMPANY_OWNER_CODE)
        used = [
            int(m.group(1))
            for code in manager.exclude(code__isnull=True).values_list("code", flat=True)
            if (m := re.fullmatch(r"OWN-(\d+)", code))
        ]
        number = max(used, default=0)
        for owner in manager.filter(code__isnull=True).order_by("id"):
            number += 1
            manager.filter(pk=owner.pk).update(code=f"OWN-{number:04d}")

    if "floor_sort" in {f.name for f in Unit._meta.get_fields()}:
        for unit in Unit.objects.using(using).all():
            key = floor_sort_key(unit.floor)
            if unit.floor_sort != key:
                Unit.objects.using(using).filter(pk=unit.pk).update(floor_sort=key)


    # Units used to be allowed without a status; a new unit is Vacant, so give old ones the same.
    if "status" in {f.name for f in Unit._meta.get_fields()}:
        Unit.objects.using(using).filter(status="").update(status="vacant")


class PropertiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "properties"
    verbose_name = "Properties"

    def ready(self):
        # Register this module's sample data and eraser with the Data tools.
        from . import seed  # noqa: F401

        post_migrate.connect(ensure_company_owner, sender=self)
