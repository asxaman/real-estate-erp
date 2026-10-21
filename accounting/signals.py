"""Keeps the ledger in step with the real estate records.

Whenever a payment, lease, expense or owner statement is saved, the postings that
follow from it are made by themselves. If a posting cannot be made (for example the
month is locked) the record is still saved: the posting waits on "Daily postings",
where the reason is shown, and a warning count appears in the menu.
"""

import logging

from django.core.cache import cache
from django.core.exceptions import ValidationError

log = logging.getLogger(__name__)


def _forget_count():
    from .rules_more import WAITING_CACHE_KEY

    cache.delete(WAITING_CACHE_KEY)


def _run(label, func, *args):
    try:
        func(*args)
    except ValidationError:
        pass  # waits on the Daily postings page, with the reason shown there
    except Exception:
        # A bug or database problem in the accounts must never stop the real work
        # (receiving a cheque, ending a lease...). It is logged and shown as waiting.
        log.exception("Automatic posting failed: %s", label)
    finally:
        _forget_count()


def cheque_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from .rules import sync_cheque
    from .rules_more import sync_termination

    _run(f"payment {instance.pk}", sync_cheque, instance)
    lease = instance.lease
    if lease.status == "terminated":
        _run(f"early exit {lease.pk}", sync_termination, lease)


def lease_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from .rules_more import sync_deposit_refund, sync_termination

    if instance.status == "terminated":
        _run(f"early exit {instance.pk}", sync_termination, instance)
    if instance.deposit_refunded_on:
        _run(f"deposit {instance.pk}", sync_deposit_refund, instance)


def expense_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from .rules_more import sync_expense

    _run(f"expense {instance.pk}", sync_expense, instance)


def expense_deleting(sender, instance, **kwargs):
    """A deleted expense must leave the books too. If that cannot be done (for
    example the month is locked) the deletion is refused."""
    from .rules_more import unpost_expense

    unpost_expense(instance)
    _forget_count()


def statement_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from .rules_more import sync_statement

    _run(f"statement {instance.pk}", sync_statement, instance)
