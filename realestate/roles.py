"""Ready-made user roles. Each role is a Django Group with certain permissions.

The roles are created automatically after `migrate`. Once created they are
yours: change them in the admin (Users and Groups > Groups) and they will not
be overwritten. `python manage.py setup_roles --reset` puts them back.
"""

from django.contrib.auth.models import Group, Permission

VIEW = ("view",)
EDIT = ("view", "add", "change")
FULL = ("view", "add", "change", "delete")

DOCUMENTS = ["ownerdocument", "tenantdocument", "propertydocument", "leasedocument"]
EVERYTHING = [
    "property", "unit", "owner", "tenant", "lease", "cheque", "chequeevent",
    "expense", "maintenancerequest", "vendor", "ownerstatement", "statementline",
] + DOCUMENTS


def _view_all(**overrides):
    spec = {model: VIEW for model in EVERYTHING}
    spec.update(overrides)
    return spec


ROLES = {
    "Manager": {
        **{model: EDIT for model in EVERYTHING},
        **{model: FULL for model in DOCUMENTS},
        "maintenancerequest": FULL,
        "chequeevent": VIEW,
        "statementline": VIEW,
    },
    "Accountant": _view_all(
        cheque=EDIT, expense=EDIT, ownerstatement=FULL, vendor=VIEW,
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
}


def _codenames(spec):
    return {f"{action}_{model}" for model, actions in spec.items() for action in actions}


def ensure_roles(reset=False):
    """Create the role groups. Existing groups keep your changes unless reset=True."""
    permissions = {
        p.codename: p for p in Permission.objects.filter(content_type__app_label="realestate")
    }
    created = []
    for name, spec in ROLES.items():
        group, is_new = Group.objects.get_or_create(name=name)
        if is_new or reset:
            wanted = [permissions[c] for c in sorted(_codenames(spec)) if c in permissions]
            group.permissions.set(wanted)
            created.append(name)
    return created


def after_migrate(sender, **kwargs):
    ensure_roles()
