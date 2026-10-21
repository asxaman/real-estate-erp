from django.apps import AppConfig
from django.db.models.signals import post_migrate, post_save, pre_delete, pre_migrate


def add_missing_accounts(sender, **kwargs):
    """After an update, give an existing chart the accounts the new postings need
    (deposits, commission, penalties...). Never changes or removes anything."""
    from django.db import DatabaseError

    from .chart import seed_chart
    from .models import Account

    from .models import AccountMapping
    from .posting_roles import ROLE_DEFAULTS

    try:
        if Account.objects.filter(code="1100").exists():
            seed_chart()
            AccountMapping.objects.exclude(key__in=list(ROLE_DEFAULTS)).delete()
    except DatabaseError:
        pass


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
        post_migrate.connect(add_missing_accounts, sender=self, dispatch_uid="accounting_add_missing_accounts")

        # The accounting permissions only exist once this app has migrated, so make
        # sure the ready-made roles pick them up (Accountant, Manager).
        from realestate.roles import after_migrate

        post_migrate.connect(after_migrate, sender=self)

        # Everything that happens to a payment, lease, expense or owner statement
        # posts to the ledger by itself.
        from . import signals

        post_save.connect(signals.cheque_saved, sender="realestate.Cheque", dispatch_uid="accounting_cheque_saved")
        post_save.connect(signals.lease_saved, sender="realestate.Lease", dispatch_uid="accounting_lease_saved")
        post_save.connect(signals.expense_saved, sender="realestate.Expense", dispatch_uid="accounting_expense_saved")
        pre_delete.connect(signals.expense_deleting, sender="realestate.Expense", dispatch_uid="accounting_expense_deleting")
        post_save.connect(signals.statement_saved, sender="realestate.OwnerStatement", dispatch_uid="accounting_statement_saved")

        # Let the real estate models ask the accounts questions (without importing them).
        from realestate import ledger_hooks

        from . import rules, rules_more

        ledger_hooks.register("cheque_posted", rules.cheque_is_posted)
        ledger_hooks.register("lease_invoiced", rules.lease_is_invoiced)
        ledger_hooks.register("waiting_count", rules_more.waiting_count)
