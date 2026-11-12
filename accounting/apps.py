from django.apps import AppConfig
from django.db.models.signals import post_delete, post_migrate, post_save


def grant_permissions_after_migrate(sender, **kwargs):
    from . import seed

    seed.grant_role_permissions()
    try:
        from .posting import ensure_chart

        ensure_chart()
    except Exception:  # noqa: BLE001  (a fresh database may not have the tables yet)
        pass


class AccountingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounting"
    verbose_name = "Accounting"

    def ready(self):
        from . import seed  # noqa: F401  (registers sample data and eraser)

        post_migrate.connect(grant_permissions_after_migrate, sender=self)
        self.connect_sources()

    def connect_sources(self):
        """Every time a record that moves money is saved or removed, its ledger entry follows."""
        from django.apps import apps

        from . import posting

        kinds = {
            "leases.LeasePayment": "leasepayment", "payables.VendorBill": "vendorbill", "payables.Expense": "expense",
            "payables.PaymentVoucher": "paymentvoucher", "hr.PayrollRun": "payrollrun", "hr.FinalSettlement": "finalsettlement",
            "statements.OwnerStatement": "ownerstatement", "accounting.FundTransfer": "fundtransfer",
        }
        for label, kind in kinds.items():
            model = apps.get_model(label)

            def saved(sender, instance, raw=False, _kind=kind, **kwargs):
                if not raw:
                    posting.safe_sync(_kind, instance)

            def deleted(sender, instance, _kind=kind, **kwargs):
                posting.safe_remove(_kind, instance.pk)

            post_save.connect(saved, sender=model, weak=False, dispatch_uid=f"ledger-save-{kind}")
            post_delete.connect(deleted, sender=model, weak=False, dispatch_uid=f"ledger-del-{kind}")
