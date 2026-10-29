from django.apps import AppConfig


class TenantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenants"
    verbose_name = "Tenants"

    def ready(self):
        from . import seed  # noqa: F401  (registers sample data and eraser)
