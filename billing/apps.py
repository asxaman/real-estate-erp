import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate, post_save

log = logging.getLogger(__name__)


def payment_saved(sender, instance, **kwargs):
    """A payment became paid -> its receipt is made. A paid payment stopped being paid -> its receipt is voided."""
    from . import models

    try:
        if instance.status == instance.Status.PAID:
            models.issue_receipt(instance)
        else:
            models.void_receipt_if_not_paid(instance)
    except Exception:  # never stop a payment from being saved because of its receipt; "Issue receipts" can make it later
        log.exception("Could not update the receipt of payment %s", instance.pk)


def grant_permissions_after_migrate(sender, **kwargs):
    from . import seed

    seed.grant_role_permissions()


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"
    verbose_name = "Receipts & invoices"

    def ready(self):
        from leases.models import LeasePayment

        from . import seed  # noqa: F401  (registers sample data and eraser)

        post_save.connect(payment_saved, sender=LeasePayment, dispatch_uid="billing_payment_saved")
        post_migrate.connect(grant_permissions_after_migrate, sender=self)
