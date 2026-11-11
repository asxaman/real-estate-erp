from django.apps import AppConfig
from django.db.models.signals import post_migrate


def grant_permissions_after_migrate(sender, **kwargs):
    from . import seed

    seed.grant_role_permissions()


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hr"
    verbose_name = "HR & Payroll"

    def ready(self):
        from . import seed  # noqa: F401  (registers sample data and eraser)

        post_migrate.connect(grant_permissions_after_migrate, sender=self)
