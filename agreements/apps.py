from django.apps import AppConfig
from django.db.models.signals import post_migrate


def grant_permissions_after_migrate(sender, **kwargs):
    """After `migrate`: roles that already exist get their owner-agreement permissions."""
    from . import seed

    seed.grant_role_permissions()


def convert_old_fees_after_migrate(sender, **kwargs):
    """After `migrate`: fees entered on properties before agreements existed become agreements (once)."""
    from . import seed

    try:
        seed.convert_old_fees()
    except Exception:  # never stop a migrate because of this; the agreements can be made by hand
        pass


class AgreementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agreements"
    verbose_name = "Owner agreements"

    def ready(self):
        from . import seed  # noqa: F401  (registers sample data and eraser)

        post_migrate.connect(grant_permissions_after_migrate, sender=self)
        post_migrate.connect(convert_old_fees_after_migrate, sender=self)
