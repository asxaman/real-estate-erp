from django.apps import AppConfig
from django.db.models.signals import post_migrate


def grant_permissions_after_migrate(sender, **kwargs):
    """After `migrate`: roles that already exist get their lease permissions."""
    from . import seed

    seed.grant_role_permissions()


def add_charge_lines_after_migrate(sender, **kwargs):
    """
    After `migrate`: leases made before deposits and fees became payment lines get their lines, once.
    Only runs while no deposit / fee line exists anywhere yet (the first migrate after this update).
    """
    from core.audit import suppressed
    from django.db.models import Q

    from .models import Lease, LeaseDeduction, LeasePayment

    try:
        with suppressed():  # earlier deductions get their line in the Payments module (once each)
            for deduction in LeaseDeduction.objects.filter(payment__isnull=True):
                deduction.sync_payment()
        if LeasePayment.objects.exclude(kind__in=(LeasePayment.Kind.RENT, LeasePayment.Kind.DEDUCTION)).exists():
            return
        with suppressed():
            leases = Lease.objects.filter(
                Q(security_deposit__gt=0) | Q(commission__gt=0) | Q(admin_fee__gt=0) | Q(ejari_fee__gt=0)
            )
            for lease in leases.iterator():
                lease.sync_charges()
    except Exception:  # best effort: never stop `migrate` (for example a partial migrate)
        return


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
        post_migrate.connect(add_charge_lines_after_migrate, sender=self, dispatch_uid="lease-charge-lines")

        def refresh_on_login(sender, user=None, **kwargs):
            """Leases start and end by the calendar, so the first sign-in of the day brings statuses up to date."""
            try:
                occupancy.refresh_all()
            except Exception:  # never stop someone from signing in
                pass

        user_logged_in.connect(refresh_on_login, dispatch_uid="lease-occupancy-login")
