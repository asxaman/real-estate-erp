"""Ready-made user roles. Each role is a Django Group with certain permissions.

The roles are created automatically after `migrate`. Once created they are
yours: change them in the admin (Users and Groups > Groups) and they will not
be overwritten. `python manage.py setup_roles --reset` puts them back.

When a new version of the system adds permissions, they are ADDED to your
existing roles once (nothing you removed or changed is touched otherwise).

Seeing owners' bank, Emirates ID and KYC details is its own permission,
"Can see owners' bank, ID and KYC details". Managers and Accountants have it.
Leasing agents and Viewers do not. To let one particular Leasing agent see them,
also put that user in the "Owner details access" group.
"""

from django.contrib.auth.models import Group, Permission

VIEW = ("view",)
EDIT = ("view", "add", "change")
FULL = ("view", "add", "change", "delete")
CHANGE_ONLY = ("view", "change")

DOCUMENTS = ["ownerdocument", "tenantdocument", "propertydocument", "leasedocument"]
EVERYTHING = [
    "property", "unit", "owner", "tenant", "lease", "cheque", "chequeevent",
    "expense", "maintenancerequest", "vendor", "ownerstatement", "statementline",
    "propertyownership",
] + DOCUMENTS

# Bump this when permissions are added, and list the new ones in INTRODUCED.
ROLES_VERSION = 5
INTRODUCED = {
    5: {"view_creditnote"},
    4: {
        "view_leaseinvoice", "add_leaseinvoice", "view_receiptvoucher",
        "view_accountmapping", "change_accountmapping",
    },
    3: {
        "view_account", "add_account", "change_account",
        "view_fiscalperiod", "add_fiscalperiod", "change_fiscalperiod", "lock_fiscalperiod",
        "view_journalentry", "post_journalentry", "reverse_journalentry",
        "view_journalline", "view_auditlog",
        "view_accountingsettings", "change_accountingsettings",
    },
    2: {
        "view_owner_private",
        "view_propertyownership", "add_propertyownership",
        "change_propertyownership", "delete_propertyownership",
        "view_companysettings", "change_companysettings",
    },
}


# The accounting module (app "accounting"). Posting, reversing and locking a period
# are separate permissions. Re-opening a locked period is for the superuser only.
ACCOUNTING_VIEW = {
    "account": VIEW, "fiscalperiod": VIEW, "journalentry": VIEW,
    "journalline": VIEW, "auditlog": VIEW, "accountingsettings": VIEW,
    "leaseinvoice": VIEW, "receiptvoucher": VIEW, "accountmapping": VIEW, "creditnote": VIEW,
}


def _view_all(**overrides):
    spec = {model: VIEW for model in EVERYTHING}
    spec["owner_private"] = ()
    spec["companysettings"] = ()
    spec.update(overrides)
    return spec


ROLES = {
    "Manager": {
        **{model: EDIT for model in EVERYTHING},
        **{model: FULL for model in DOCUMENTS},
        "maintenancerequest": FULL,
        "chequeevent": VIEW,
        "statementline": VIEW,
        "propertyownership": FULL,
        "owner_private": VIEW,
        "companysettings": CHANGE_ONLY,
        **ACCOUNTING_VIEW,
    },
    "Accountant": _view_all(
        cheque=EDIT, expense=EDIT, ownerstatement=FULL, vendor=VIEW,
        owner_private=VIEW, companysettings=VIEW,
        account=EDIT,
        fiscalperiod=("view", "add", "change", "lock"),
        journalentry=("view", "post", "reverse"),
        journalline=VIEW, auditlog=VIEW,
        accountingsettings=("view", "change"),
        leaseinvoice=("view", "add"), receiptvoucher=VIEW, accountmapping=("view", "change"),
        creditnote=VIEW,
    ),
    "Leasing agent": _view_all(
        tenant=EDIT, lease=EDIT, unit=EDIT, cheque=EDIT,
        tenantdocument=FULL, leasedocument=FULL,
        expense=(), ownerstatement=(), statementline=(), vendor=(),
    ),
    "Maintenance coordinator": {
        "maintenancerequest": EDIT,
        "expense": EDIT,
        "vendor": EDIT,
        "property": VIEW,
        "unit": VIEW,
        "tenant": VIEW,
    },
    "Viewer (read only)": _view_all(),
    # Add this group to a user (on top of their main role) to let them see
    # owners' bank, ID and KYC details.
    "Owner details access": {
        "owner": VIEW,
        "ownerdocument": VIEW,
        "owner_private": VIEW,
    },
}


def _codenames(spec):
    return {f"{action}_{model}" for model, actions in spec.items() for action in actions}


def ensure_roles(reset=False):
    """Create the role groups. Existing groups keep your changes unless reset=True.

    Returns the names of the groups that were created or reset.
    """
    from .models import SystemState

    permissions = {
        p.codename: p
        for p in Permission.objects.filter(content_type__app_label__in=["realestate", "accounting"])
    }
    state, _ = SystemState.objects.get_or_create(key="roles_version", defaults={"value": "1"})
    try:
        stored = int(state.value or 1)
    except ValueError:
        stored = 1

    changed = []
    for name, spec in ROLES.items():
        group, is_new = Group.objects.get_or_create(name=name)
        wanted_codenames = _codenames(spec)
        if is_new or reset:
            wanted = [permissions[c] for c in sorted(wanted_codenames) if c in permissions]
            group.permissions.set(wanted)
            changed.append(name)
            continue
        # An existing role: add only the permissions introduced since it last synced.
        new_codenames = set()
        for version in range(stored + 1, ROLES_VERSION + 1):
            new_codenames |= INTRODUCED.get(version, set())
        to_add = [permissions[c] for c in sorted(wanted_codenames & new_codenames) if c in permissions]
        if to_add:
            group.permissions.add(*to_add)

    # If some permissions of the new version do not exist yet (they are created a moment
    # later in the same `migrate`), keep the old version so the next call adds them.
    pending = {
        code
        for version in range(stored + 1, ROLES_VERSION + 1)
        for code in INTRODUCED.get(version, set())
        if code not in permissions
    }
    if stored != ROLES_VERSION and not pending:
        state.value = str(ROLES_VERSION)
        state.save()
    return changed


def after_migrate(sender, **kwargs):
    ensure_roles()
