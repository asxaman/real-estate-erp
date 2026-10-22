"""
Django settings for the Real Estate ERP project.

Settings can be overridden with environment variables (see README.md) so the
same code can later run on a real server without editing this file.
"""
import os
from pathlib import Path

from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

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
# Database (SQLite)
# --------------------------------------------------------------------------
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
