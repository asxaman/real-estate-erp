"""Settings for the Real Estate ERP (development)."""

from pathlib import Path

from django.templatetags.static import static
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

# Development-only values. Before going live these must be replaced
# (secret key from the environment, DEBUG = False, real ALLOWED_HOSTS).
SECRET_KEY = "django-insecure-change-this-before-going-live"
DEBUG = True
ALLOWED_HOSTS = []

INSTALLED_APPS = [
    "unfold",  # the admin theme: this line must stay ABOVE django.contrib.admin
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "realestate",
    "accounting",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounting.context.CurrentUserMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],  # our look-and-feel changes live here
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dubai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# Uploaded documents (Emirates ID scans, contracts...) are saved in this folder.
# It is listed in .gitignore, so these files never go to GitHub.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Admin look and feel (Unfold)
# ---------------------------------------------------------------------------
# The left menu is written out below. To add a page to it, copy one "items" line.
# Icon names come from https://fonts.google.com/icons (Material Symbols).


def _link(name):
    return reverse_lazy(f"admin:{name}")


def _can(permission):
    """Show a menu item only to users who may open that page."""
    return lambda request: request.user.has_perm(f"realestate.{permission}")


def _can_acc(permission):
    """Same as _can, for the accounting module."""
    return lambda request: request.user.has_perm(f"accounting.{permission}")


UNFOLD = {
    "SITE_TITLE": "Real Estate ERP",
    "SITE_HEADER": "Real Estate ERP",
    "SITE_SUBHEADER": "UAE property management",
    "SITE_SYMBOL": "apartment",
    "BORDER_RADIUS": "8px",
    "STYLES": [lambda request: static("realestate/admin/transitions.css")],
    "COLORS": {
        "primary": {
            "50": "oklch(97% 0.014 254.604)",
            "100": "oklch(93.2% 0.032 255.585)",
            "200": "oklch(88.2% 0.059 254.128)",
            "300": "oklch(80.9% 0.105 251.813)",
            "400": "oklch(70.7% 0.165 254.624)",
            "500": "oklch(62.3% 0.214 259.815)",
            "600": "oklch(54.6% 0.245 262.881)",
            "700": "oklch(48.8% 0.243 264.376)",
            "800": "oklch(42.4% 0.199 265.638)",
            "900": "oklch(37.9% 0.146 265.522)",
            "950": "oklch(28.2% 0.091 267.935)",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Overview",
                "separator": False,
                "items": [
                    {"title": "Dashboard", "icon": "dashboard", "link": _link("index")},
                    {
                        "title": "Daily cheque desk",
                        "icon": "event_available",
                        "link": _link("realestate_cheque_desk"),
                        "permission": _can("view_cheque"),
                    },
                ],
            },
            {
                "title": "Daily work",
                "separator": True,
                "items": [
                    {"title": "Payments & cheques", "icon": "payments", "link": _link("realestate_cheque_changelist"), "permission": _can("view_cheque")},
                    {
                        "title": "Maintenance jobs",
                        "icon": "construction",
                        "link": _link("realestate_maintenancerequest_changelist"),
                        "permission": _can("view_maintenancerequest"),
                    },
                    {"title": "Expenses", "icon": "receipt_long", "link": _link("realestate_expense_changelist"), "permission": _can("view_expense")},
                ],
            },
            {
                "title": "Properties and people",
                "separator": True,
                "items": [
                    {"title": "Properties", "icon": "domain", "link": _link("realestate_property_changelist"), "permission": _can("view_property")},
                    {"title": "Units", "icon": "meeting_room", "link": _link("realestate_unit_changelist"), "permission": _can("view_unit")},
                    {"title": "Owners", "icon": "badge", "link": _link("realestate_owner_changelist"), "permission": _can("view_owner")},
                    {"title": "Tenants", "icon": "groups", "link": _link("realestate_tenant_changelist"), "permission": _can("view_tenant")},
                ],
            },
            {
                "title": "Contracts and payouts",
                "separator": True,
                "items": [
                    {"title": "Leases", "icon": "description", "link": _link("realestate_lease_changelist"), "permission": _can("view_lease")},
                    {
                        "title": "Owner statements",
                        "icon": "request_quote",
                        "link": _link("realestate_ownerstatement_changelist"),
                        "permission": _can("view_ownerstatement"),
                    },
                ],
            },
            {
                "title": "Accounting",
                "separator": True,
                "items": [
                    {"title": "Accounting setup", "icon": "rule_settings", "link": _link("accounting_setup"), "permission": _can_acc("view_account")},
                    {"title": "Daily postings", "icon": "published_with_changes", "link": _link("accounting_postings"), "permission": _can_acc("view_journalline")},
                    {"title": "Lease invoices", "icon": "request_page", "link": _link("accounting_leaseinvoice_changelist"), "permission": _can_acc("view_leaseinvoice")},
                    {"title": "Receipt vouchers", "icon": "receipt", "link": _link("accounting_receiptvoucher_changelist"), "permission": _can_acc("view_receiptvoucher")},
                    {"title": "Journal entries", "icon": "menu_book", "link": _link("accounting_journalentry_changelist"), "permission": _can_acc("view_journalentry")},
                    {"title": "Trial balance", "icon": "balance", "link": _link("accounting_account_trial_balance"), "permission": _can_acc("view_journalline")},
                    {"title": "General ledger", "icon": "book", "link": _link("accounting_account_general_ledger"), "permission": _can_acc("view_journalline")},
                ],
            },
            {
                "title": "Setup",
                "separator": True,
                "items": [
                    {"title": "Vendors", "icon": "local_shipping", "link": _link("realestate_vendor_changelist"), "permission": _can("view_vendor")},
                    {"title": "Company settings", "icon": "tune", "link": _link("realestate_companysettings_changelist"), "permission": _can("view_companysettings")},
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": _link("auth_user_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Groups",
                        "icon": "lock",
                        "link": _link("auth_group_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Erase all data",
                        "icon": "delete_forever",
                        "link": _link("realestate_demorecord_erase"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Demo data",
                        "icon": "science",
                        "link": _link("realestate_demorecord_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                ],
            },
        ],
    },
}
