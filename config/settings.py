"""
Django settings for the Real Estate ERP project.

Settings can be overridden with environment variables (see README.md) so the
same code can later run on a real server without editing this file.
"""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.functional import lazy

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path):
    """Read simple KEY=VALUE lines from a .env file (real environment variables win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Local settings such as the database password live in the .env file (never uploaded to GitHub).
_load_env_file(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
def _flag(name, default):
    """Read a yes/no setting from the environment (1/0, true/false, yes/no)."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# "Development" = started from this folder with manage.py (runserver, test, migrate ...).
# A real web server (gunicorn, waitress, IIS ...) does not start through manage.py, so it is
# treated as LIVE and gets the strict settings below, unless DJANGO_DEBUG says otherwise.
_STARTED_WITH_MANAGE_PY = bool(sys.argv) and Path(sys.argv[0]).name == "manage.py"
DEBUG = _flag("DJANGO_DEBUG", _STARTED_WITH_MANAGE_PY)

_DEV_KEY = "dev-only-insecure-key-change-me-before-production"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is not set. A live server needs its own secret key "
            "(a long random text). Put it in the .env file or in the server's environment."
        )
    SECRET_KEY = _DEV_KEY
elif not DEBUG and (SECRET_KEY == _DEV_KEY or len(SECRET_KEY) < 32):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is too short or is the development key. Use a random text of 32+ characters."
    )

_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "").strip()
if not _hosts and not DEBUG:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS is not set. List the website address(es) of the live server, "
        "separated by commas (for example erp.mycompany.ae)."
    )
ALLOWED_HOSTS = [h.strip() for h in (_hosts or "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------
INSTALLED_APPS = [
    # Unfold MUST come before django.contrib.admin
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # ERP modules
    "core",  # Module 0: foundation (users, roles, data tools)
    "properties",  # Module 1: owners, properties, units
    "tenants",  # Module 2: tenants (leases next)
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.toasts.ToastMiddleware",  # pop-up notifications (after messages are read)
    "core.middleware.SystemSettingsMiddleware",  # date / time format from System settings
    "core.audit.AuditMiddleware",  # remembers who is logged in, for the audit log
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",  # required by the admin check
                "core.toasts.toast_messages",  # runs after the one above: pop-ups instead of message bars
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
# PostgreSQL only. The connection details come from the .env file or the environment.
if not os.environ.get("DB_NAME"):
    raise ImproperlyConfigured(
        "DB_NAME is not set. Add the database settings (DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, "
        "DB_PORT) to the .env file in the project folder."
    )
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
# One simple rule: 8+ characters with a capital letter, a small letter, a number and a special character.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "core.validators.ComplexPasswordValidator"},
]
# Too many wrong passwords in a row lock the account for a few minutes (see core/lockout.py).
AUTHENTICATION_BACKENDS = ["core.lockout.LockoutBackend"]
LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/admin/"

# --------------------------------------------------------------------------
# Internationalisation (UAE)
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dubai"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static files
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded files. They are never served straight from the folder: every download goes through
# core/media.py, which checks that the person is logged in and may see that kind of file.
# Back up the "media" folder together with the database (the backup_db command does both).
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
# Optional, for a live server behind nginx: the internal address nginx uses for the media folder
# (for example /protected-media/). Django then only checks permission and nginx sends the file.
MEDIA_ACCEL_REDIRECT = os.environ.get("DJANGO_MEDIA_ACCEL", "").strip()
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
# A building page posts every unit row at once (about 9 fields per unit), so allow plenty of fields.
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.environ.get("DJANGO_MAX_FORM_FIELDS", "20000"))

# --------------------------------------------------------------------------
# Live server (only when DEBUG is off)
# --------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "erp.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "plain",
        },
    },
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "WARNING"},
        "django.security": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
        "core": {"handlers": ["console", "file"], "level": "INFO"},
    },
}

if "test" in sys.argv:
    LOGGING["loggers"]["django"]["level"] = "ERROR"  # keeps the test output free of expected 403/404 noise

if not DEBUG:
    # Everything below needs the site to be served over HTTPS (set DJANGO_HTTPS=0 only for a
    # temporary test on a plain http address).
    if _flag("DJANGO_BEHIND_PROXY", False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # nginx / a load balancer
    if _flag("DJANGO_HTTPS", True):
        SECURE_SSL_REDIRECT = True
        SESSION_COOKIE_SECURE = True
        CSRF_COOKIE_SECURE = True
        SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # Logged-out after this many minutes without activity (the timer restarts on every click).
    SESSION_COOKIE_AGE = 60 * int(os.environ.get("DJANGO_SESSION_MINUTES", "60"))
    SESSION_SAVE_EVERY_REQUEST = True
    SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# --------------------------------------------------------------------------
# Django Unfold (admin theme)
# --------------------------------------------------------------------------
def _is_superuser(request):
    return request.user.is_superuser


def _can(permission):
    """Sidebar helper: show a menu item only to users who hold this permission."""

    def check(request):
        return request.user.has_perm(permission)

    return check


def _read_company_name():
    from core import sysconf  # imported late: settings are loaded before the apps

    return sysconf.get("company_name")


_company_name = lazy(_read_company_name, str)

UNFOLD = {
    # The company name comes from the System settings page.
    "SITE_TITLE": _company_name(),
    "SITE_HEADER": _company_name(),
    "SITE_SUBHEADER": "Property management suite",
    "SITE_URL": "/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    # Our own small style/behaviour layer (info icons, compact forms).
    # Bump the ?v= number whenever these files change so browsers reload them.
    "STYLES": [lambda request: static("core/erp.css") + "?v=17"],
    "SCRIPTS": [lambda request: static("core/erp.js") + "?v=20"],
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Overview",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": reverse_lazy("dashboard"),
                    },
                    {
                        "title": "Expiring documents",
                        "icon": "event_busy",
                        "link": reverse_lazy("expiring_documents"),
                    },
                ],
            },
            {
                "title": "Property management",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Owners",
                        "icon": "account_circle",
                        "link": reverse_lazy("admin:properties_owner_changelist"),
                        "permission": _can("properties.view_owner"),
                    },
                    {
                        "title": "Properties",
                        "icon": "apartment",
                        "link": reverse_lazy("admin:properties_property_changelist"),
                        "permission": _can("properties.view_property"),
                    },
                    {
                        "title": "Units",
                        "icon": "meeting_room",
                        "link": reverse_lazy("admin:properties_unit_changelist"),
                        "permission": _can("properties.view_unit"),
                    },
                ],
            },
            {
                "title": "Tenancy",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Tenants",
                        "icon": "groups",
                        "link": reverse_lazy("admin:tenants_tenant_changelist"),
                        "permission": _can("tenants.view_tenant"),
                    },
                ],
            },
            {
                "title": "Administration",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                        "permission": _is_superuser,
                    },
                    {
                        "title": "System settings",
                        "icon": "settings",
                        "link": reverse_lazy("admin:core_systemsettings_changelist"),
                        "permission": _is_superuser,
                    },
                ],
            },
        ],
    },
}

# One-time messages (such as a temporary password) are kept in the session, not in a cookie.
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"
