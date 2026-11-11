from django.apps import AppConfig
from django.db.models.signals import post_migrate


def grant_permissions_after_migrate(sender, **kwargs):
    """After `migrate`: roles that already exist get their maintenance permissions."""
    from . import seed

    seed.grant_role_permissions()


class MaintenanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "maintenance"
    verbose_name = "Maintenance"

    def ready(self):
        from . import seed  # noqa: F401  (registers sample data and eraser)

        post_migrate.connect(grant_permissions_after_migrate, sender=self)
