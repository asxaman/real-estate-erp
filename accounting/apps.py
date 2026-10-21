from django.apps import AppConfig
from django.db.models.signals import post_migrate, pre_migrate


class AccountingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounting"
    verbose_name = "Accounting"

    def ready(self):
        from .triggers import drop_triggers, install_triggers

        # After every `migrate`, make sure the database itself refuses to change
        # or delete posted journal entries (a second lock behind the Python code).
        pre_migrate.connect(drop_triggers, sender=self)
        post_migrate.connect(install_triggers, sender=self)

        # The accounting permissions only exist once this app has migrated, so make
        # sure the ready-made roles pick them up (Accountant, Manager).
        from realestate.roles import after_migrate

        post_migrate.connect(after_migrate, sender=self)

        # Payments post to the ledger by themselves whenever they are saved.
        from django.db.models.signals import post_save

        from .signals import cheque_saved

        post_save.connect(cheque_saved, sender="realestate.Cheque", dispatch_uid="accounting_cheque_saved")
