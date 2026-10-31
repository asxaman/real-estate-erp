"""
Values from the System settings page, available anywhere without extra database queries.

`ensure_current()` runs at the start of every request (core.middleware) and reads the single
settings row. Other code calls `get("name")` or `expiry_days()`.
"""
from . import formats

DEFAULTS = {
    "company_name": "Real Estate ERP",
    "company_phone": "",
    "company_email": "",
    "company_address": "",
    "company_trn": "",
    "date_format": formats.DEFAULT_DATE,
    "time_format": formats.DEFAULT_TIME,
    "timezone": "Asia/Dubai",
    "expiry_warning_days": 30,
    "max_photo_mb": 2,
    "max_document_mb": 10,
    "require_complete_documents": False,
    "toast_seconds": 5,
}

TIMEZONES = [
    ("Asia/Dubai", "UAE (UTC+4)"),
    ("Asia/Muscat", "Oman (UTC+4)"),
    ("Asia/Riyadh", "Saudi Arabia (UTC+3)"),
    ("Asia/Qatar", "Qatar (UTC+3)"),
    ("Asia/Kuwait", "Kuwait (UTC+3)"),
    ("Asia/Bahrain", "Bahrain (UTC+3)"),
    ("Asia/Karachi", "Pakistan (UTC+5)"),
    ("Asia/Kolkata", "India (UTC+5:30)"),
    ("Europe/London", "United Kingdom"),
    ("UTC", "UTC"),
]

_current = dict(DEFAULTS)


def get(name):
    return _current.get(name, DEFAULTS.get(name))


def expiry_days():
    return int(get("expiry_warning_days") or DEFAULTS["expiry_warning_days"])


def refresh(row=None):
    """Load the saved values (row = a dict, or None to use the defaults) and apply them."""
    values = dict(DEFAULTS)
    if row:
        values.update({k: v for k, v in row.items() if k in DEFAULTS and v not in (None, "")})
        # text fields may legitimately be empty
        for key in ("company_phone", "company_email", "company_address", "company_trn"):
            if key in row:
                values[key] = row[key] or ""
    _current.clear()
    _current.update(values)
    formats.apply(values["date_format"], values["time_format"])


def ensure_current():
    """Re-read the saved settings when they differ from what this process is using."""
    from .models import SystemSettings

    try:
        row = SystemSettings.objects.values(*DEFAULTS.keys()).first()
    except Exception:  # table missing (before migrate) or database not reachable
        return
    wanted = row or dict(DEFAULTS)
    if any(_current.get(k) != wanted.get(k) for k in DEFAULTS):
        refresh(row)
