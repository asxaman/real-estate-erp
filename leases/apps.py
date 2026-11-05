from django.apps import AppConfig
from django.db.models.signals import post_migrate


def grant_permissions_after_migrate(sender, **kwargs):
    """After `migrate`: roles that already exist get their lease permissions."""
    from . import seed

    seed.grant_role_permissions()


class LeasesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "leases"
    verbose_name = "Leases"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from . import occupancy
        from . import seed  # noqa: F401  (registers sample data and eraser)

        occupancy.connect()
        post_migrate.connect(grant_permissions_after_migrate, sender=self, dispatch_uid="lease-permissions")

        def refresh_on_login(sender, user=None, **kwargs):
            """Leases start and end by the calendar, so the first sign-in of the day brings statuses up to date."""
            try:
                occupancy.refresh_all()
            except Exception:  # never stop someone from signing in
                pass

        user_logged_in.connect(refresh_on_login, dispatch_uid="lease-occupancy-login")
