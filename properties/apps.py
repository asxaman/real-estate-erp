from django.apps import AppConfig


class PropertiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "properties"
    verbose_name = "Properties"

    def ready(self):
        # Register this module's sample data and eraser with the Data tools.
        from . import seed  # noqa: F401
