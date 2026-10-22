from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        # Register this module's seed (and, in later modules, erase) steps.
        from . import seed  # noqa: F401
