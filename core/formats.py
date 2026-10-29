"""
Date and time format chosen by the administrator (System settings page).

How it works
------------
Django looks up every date/time format through `django.utils.formats.get_format`, which asks a
list of "format modules" first. We add one extra module at the front of that list whose values
come from the database (SystemSettings). So dates shown on pages, in lists, in date pickers and
accepted in forms all follow the chosen format. Stored data is never changed: only how it is
shown and typed.

`core.sysconf.ensure_current()` runs on every request (core.middleware) so every server process
notices a change straight away.
"""
import types

from django.utils import dateformat, formats

# key: (label, Django display format, Python input format)
DATE_FORMATS = {
    "iso": ("YYYY-MM-DD", "Y-m-d", "%Y-%m-%d"),
    "dmy_dash": ("DD-MM-YYYY", "d-m-Y", "%d-%m-%Y"),
    "dmy_slash": ("DD/MM/YYYY", "d/m/Y", "%d/%m/%Y"),
    "mdy_slash": ("MM/DD/YYYY", "m/d/Y", "%m/%d/%Y"),
    "dmy_text": ("DD MMM YYYY", "d M Y", "%d %b %Y"),
}
TIME_FORMATS = {
    "24h": ("24-hour (17:30)", "H:i", "%H:%M"),
    "12h": ("12-hour (5:30 PM)", "h:i A", "%I:%M %p"),
}
DEFAULT_DATE = "iso"
DEFAULT_TIME = "24h"

_module = types.ModuleType("erp_dynamic_formats")  # holds the values Django will read first
_applied = None  # (date key, time key) currently active in this process
_installed = False


def date_choices():
    return [(key, f"{label}") for key, (label, _d, _p) in DATE_FORMATS.items()]


def time_choices():
    return [(key, label) for key, (label, _d, _p) in TIME_FORMATS.items()]


def build_values(date_key, time_key):
    """The Django format settings for a chosen date and time style."""
    date_key = date_key if date_key in DATE_FORMATS else DEFAULT_DATE
    time_key = time_key if time_key in TIME_FORMATS else DEFAULT_TIME
    _, d_show, d_in = DATE_FORMATS[date_key]
    _, t_show, t_in = TIME_FORMATS[time_key]
    iso = DATE_FORMATS["iso"][2]  # ISO is always accepted too (it is unambiguous)
    date_inputs = [d_in] + ([iso] if d_in != iso else [])
    time_inputs = [t_in, "%H:%M:%S", "%H:%M", "%I:%M:%S %p"]
    datetime_inputs = []
    for d in date_inputs:
        for t in time_inputs:
            datetime_inputs.append(f"{d} {t}")
    return {
        "DATE_FORMAT": d_show,
        "SHORT_DATE_FORMAT": d_show,
        "TIME_FORMAT": t_show,
        "DATETIME_FORMAT": f"{d_show} {t_show}",
        "SHORT_DATETIME_FORMAT": f"{d_show} {t_show}",
        "DATE_INPUT_FORMATS": date_inputs,
        "TIME_INPUT_FORMATS": time_inputs,
        "DATETIME_INPUT_FORMATS": datetime_inputs,
    }


def apply(date_key, time_key):
    """Make this date/time style active in the current process."""
    global _applied
    for name, value in build_values(date_key, time_key).items():
        setattr(_module, name, value)
    formats.reset_format_cache()
    _applied = (date_key, time_key)


def install():
    """Put our module in front of Django's own format modules (done once at start-up)."""
    global _installed
    if _installed:
        return
    original = formats.get_format_modules

    def get_format_modules(lang=None):
        return [_module] + list(original(lang))

    formats.get_format_modules = get_format_modules
    _installed = True


def preview(date_key, time_key, when):
    """Example text for the settings page (does not change the active style)."""
    values = build_values(date_key, time_key)
    return {
        "date": dateformat.format(when, values["DATE_FORMAT"]),
        "time": dateformat.format(when, values["TIME_FORMAT"]),
        "both": dateformat.format(when, values["DATETIME_FORMAT"]),
    }


# ---- helpers for our own code (messages, headers) ------------------------------------------------
def fmt_date(value):
    return formats.date_format(value, "DATE_FORMAT") if value else "-"


def fmt_datetime(value):
    if not value:
        return "-"
    from django.utils import timezone

    return formats.date_format(timezone.localtime(value) if timezone.is_aware(value) else value, "DATETIME_FORMAT")
