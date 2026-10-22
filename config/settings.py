"""
Django settings for the Real Estate ERP project.

Settings can be overridden with environment variables (see README.md) so the
same code can later run on a real server without editing this file.
"""
import os
from pathlib import Path

from django.templatetags.static import static
from django.urls import reverse_lazy

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
# IMPORTANT: before going live, set a real DJANGO_SECRET_KEY environment
# variable and DJANGO_DEBUG=0. The default below is for local development only.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-insecure-key-change-me-before-production",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
# PostgreSQL is used when DB_NAME is set (in the .env file or the environment).
# If DB_NAME is not set, the project falls back to the old SQLite file so it still runs.
if os.environ.get("DB_NAME"):
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
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
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

# Uploaded files (profile pictures). Back up the "media" folder together with the database.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

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


UNFOLD = {
    "SITE_TITLE": "Real Estate ERP",
    "SITE_HEADER": "Real Estate ERP",
    "SITE_SUBHEADER": "Property management suite",
    "SITE_URL": "/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    # Our own small style/behaviour layer (info icons, compact forms).
    # Bump the ?v= number whenever these files change so browsers reload them.
    "STYLES": [lambda request: static("core/erp.css") + "?v=7"],
    "SCRIPTS": [lambda request: static("core/erp.js") + "?v=10"],
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Property management",
                "separator": True,
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
                "title": "Administration",
                "separator": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                        "permission": _is_superuser,
                    },
                    {
                        "title": "Roles (groups)",
                        "icon": "badge",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                        "permission": _is_superuser,
                    },
                    {
                        "title": "Audit log",
                        "icon": "history",
                        "link": reverse_lazy("admin:core_auditentry_changelist"),
                        "permission": _is_superuser,
                    },
                    {
                        "title": "Data tools",
                        "icon": "database",
                        "link": reverse_lazy("data_tools"),
                        "permission": _is_superuser,
                    },
                ],
            },
        ],
    },
}
