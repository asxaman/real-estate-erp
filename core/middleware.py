from zoneinfo import ZoneInfo

from django.utils import timezone

from . import sysconf


class SystemSettingsMiddleware:
    """Applies the System settings (date/time format, time zone ...) to every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        sysconf.ensure_current()
        try:
            timezone.activate(ZoneInfo(sysconf.get("timezone")))
        except Exception:  # unknown zone name: fall back to the default time zone
            timezone.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
