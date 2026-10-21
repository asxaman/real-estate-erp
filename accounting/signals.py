from django.core.exceptions import ValidationError


def cheque_saved(sender, instance, raw=False, **kwargs):
    """Post the payment's steps to the ledger. If a step cannot be posted (for
    example the month is locked) the payment is still saved and the step waits
    on the Daily postings page."""
    if raw:
        return
    from .rules import sync_cheque

    try:
        sync_cheque(instance)
    except ValidationError:
        pass
