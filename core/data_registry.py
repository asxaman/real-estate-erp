"""
Central registry for the "Seed sample data" and "Erase all data" tools.

How later modules plug in
-------------------------
Every ERP module (properties, tenants, leases, accounting ...) has its own
seed.py file. In it the module calls:

    register_seeder("Properties", seed_properties, order=20)
    register_eraser("Properties", erase_properties, order=20)

* Seeders run from the LOWEST order to the HIGHEST (so parents exist before
  the records that depend on them).
* Erasers run from the HIGHEST order to the LOWEST (children are deleted before
  their parents).

The admin page and the management commands only talk to this file, so adding a
new module never requires changing the admin page.
"""
from dataclasses import dataclass
from typing import Callable

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from .audit import suppressed


@dataclass(frozen=True)
class Step:
    label: str
    func: Callable[[], str]
    order: int


# The "large test data" steps (hundreds of properties, thousands of units, tenants and leases) read this
# number: 1.0 = full size, 0.1 = a tenth of it, 0 = skip them. (Tests set it small.)
SAMPLE_SCALE = 1.0


def sample_count(full: int, minimum: int = 1) -> int:
    """How many records a large-test-data step creates: `full` at scale 1, never below `minimum`."""
    if SAMPLE_SCALE <= 0:
        return 0
    return max(minimum, int(round(full * SAMPLE_SCALE)))


_seeders: dict[str, Step] = {}
_erasers: dict[str, Step] = {}


def register_seeder(label: str, func: Callable[[], str], order: int = 100) -> None:
    """Register a function that creates sample data. It returns a short summary text."""
    _seeders[label] = Step(label, func, order)


def register_eraser(label: str, func: Callable[[], str], order: int = 100) -> None:
    """Register a function that deletes a module's data. It returns a short summary text."""
    _erasers[label] = Step(label, func, order)


def tools_allowed() -> bool:
    """The sample-data and erase tools work only while Development mode is on (System settings)."""
    from django.conf import settings

    from . import sysconf

    if not getattr(settings, "ALLOW_DEV_TOOLS", False):
        return False  # a live server never allows them
    sysconf.ensure_current()
    return sysconf.development_mode()


TOOLS_OFF_MESSAGE = (
    "Development mode is switched off in System settings, so the sample-data and erase tools are "
    "disabled. (Switch it on there only on a test copy, never on real data.)"
)


def run_seeders() -> list[str]:
    """Run every registered seeder inside one transaction (all or nothing)."""
    messages = []
    with suppressed(), transaction.atomic():  # not logged record by record
        for step in sorted(_seeders.values(), key=lambda s: s.order):
            messages.append(f"{step.label}: {step.func()}")
    return messages


def run_erasers(include_users: bool = False) -> list[str]:
    """
    Delete all business data inside one transaction (all or nothing).

    Superuser accounts are NEVER deleted, so you cannot lock yourself out.
    If include_users is True, every non-superuser account and every role
    (group) is deleted as well.
    """
    messages = []
    with suppressed(), transaction.atomic():  # not logged record by record
        for step in sorted(_erasers.values(), key=lambda s: s.order, reverse=True):
            messages.append(f"{step.label}: {step.func()}")
        # Development phase only: the history of changes goes too (it holds personal numbers).
        from .models import AuditEntry, LoginAttempt

        entries, _ = AuditEntry.objects.all().delete()
        LoginAttempt.objects.all().delete()
        messages.append(f"Audit log: removed {entries} entr{'y' if entries == 1 else 'ies'}")
        if include_users:
            User = get_user_model()
            deleted_users, _ = User.objects.filter(is_superuser=False).delete()
            deleted_groups, _ = Group.objects.all().delete()
            messages.append(
                f"Users & roles: removed {deleted_users} record(s) for non-superuser "
                f"accounts and {deleted_groups} record(s) for roles (superusers kept)"
            )
    if not messages:
        messages.append("Nothing to erase yet - no modules with data are installed.")
    return messages
