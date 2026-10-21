"""The posting engine: the only code that writes to the ledger.

Everything else (invoices, receipts, vouchers... in later steps) calls
`post_entry`. It checks the rules, writes the entry and its lines in one
database transaction, gives it the next number and seals it.
"""

from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .context import current_user
from .models import (
    ZERO,
    Account,
    ImmutableLedgerError,
    JournalEntry,
    JournalLine,
    check_lines,
    check_posting_date,
    money,
)


@dataclass
class LineSpec:
    """One line to post. `account` may be an Account or its code."""

    account: object
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    description: str = ""
    tenant: object = None
    owner: object = None
    vendor: object = None


def get_account(account):
    if isinstance(account, Account):
        return account
    found = Account.objects.filter(code=str(account)).first()
    if found is None:
        raise ValidationError(f"There is no account with code {account}.")
    return found


def post_entry(
    *,
    date,
    memo,
    lines,
    source=JournalEntry.Source.MANUAL,
    reference="",
    source_model="",
    source_id=None,
    source_tag="",
    user=None,
    reverses=None,
    allow_inactive_accounts=False,
):
    """Post a balanced journal entry. Returns the posted JournalEntry.

    `lines` is a list of LineSpec. Nothing is saved unless everything is valid."""
    user = user or current_user()
    if not isinstance(date, date_type):
        raise ValidationError("The posting date is missing.")
    if not memo or not str(memo).strip():
        raise ValidationError("Write a narration (what is this entry for).")

    resolved = []
    for spec in lines:
        account = get_account(spec.account)
        resolved.append((account, spec))
    check_lines(
        [
            (
                account,
                spec.debit,
                spec.credit,
                spec.tenant.pk if spec.tenant else None,
                spec.owner.pk if spec.owner else None,
                spec.vendor.pk if spec.vendor else None,
            )
            for account, spec in resolved
        ],
        allow_inactive=allow_inactive_accounts,
    )
    check_posting_date(date)

    with transaction.atomic():
        entry = JournalEntry.objects.create(
            date=date,
            memo=str(memo).strip()[:500],
            reference=reference,
            source=source,
            source_model=source_model,
            source_id=source_id,
            source_tag=source_tag,
            reverses=reverses,
            created_by=user if (user is not None and getattr(user, "pk", None)) else None,
        )
        for account, spec in resolved:
            JournalLine.objects.create(
                entry=entry,
                account=account,
                description=spec.description[:255],
                debit=money(spec.debit),
                credit=money(spec.credit),
                tenant=spec.tenant,
                owner=spec.owner,
                vendor=spec.vendor,
            )
        entry.post(user=user, allow_inactive_accounts=allow_inactive_accounts)
    return entry


def reverse_entry(entry, *, date=None, reason="", user=None):
    """Cancel a posted entry by posting its mirror image. The original stays
    exactly as it was. Returns the new reversing entry."""
    user = user or current_user()
    with transaction.atomic():
        entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
        if not entry.is_posted:
            raise ValidationError("Only a posted entry can be reversed.")
        if entry.source == JournalEntry.Source.REVERSAL:
            raise ValidationError("A reversal cannot itself be reversed. Post a new entry instead.")
        if entry.is_reversed:
            raise ValidationError(f"{entry.number} has already been reversed.")
        day = date or timezone.localdate()
        if day < entry.date:
            raise ValidationError("The reversal cannot be dated before the original entry.")
        specs = [
            LineSpec(
                account=line.account,
                debit=line.credit,
                credit=line.debit,
                description=line.description,
                tenant=line.tenant,
                owner=line.owner,
                vendor=line.vendor,
            )
            for line in entry.lines.select_related("account", "tenant", "owner", "vendor")
        ]
        text = f"Reversal of {entry.number}"
        if reason:
            text += f": {reason}"
        return post_entry(
            date=day,
            memo=text,
            lines=specs,
            source=JournalEntry.Source.REVERSAL,
            reference=entry.number or "",
            source_model="accounting.JournalEntry",
            source_id=entry.pk,
            user=user,
            reverses=entry,
            allow_inactive_accounts=True,
        )


__all__ = ["LineSpec", "post_entry", "reverse_entry", "get_account", "ImmutableLedgerError"]
