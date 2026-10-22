from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.module_loading import autodiscover_modules


def create_roles(sender, **kwargs):
    """Make sure the standard staff/portal roles (user groups) always exist."""
    from django.contrib.auth.models import Group

    from .roles import ROLES

    for name in ROLES:
        Group.objects.get_or_create(name=name)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        post_migrate.connect(create_roles, sender=self)
        # Every module may ship a seeders.py; importing it registers its sample-data seeder.
        autodiscover_modules("seeders")
