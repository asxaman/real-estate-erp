from django.apps import AppConfig
from django.db.models.signals import post_migrate


class RealestateConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "realestate"
    verbose_name = "Property Management"

    def ready(self):
        from .roles import after_migrate

        # After every `migrate`, make sure the ready-made user roles exist.
        post_migrate.connect(after_migrate, sender=self)
