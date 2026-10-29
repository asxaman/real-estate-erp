from . import formats


class SystemSettingsMiddleware:
    """Keeps the date/time format in sync with the System settings page (one small query)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        formats.ensure_current()
        return self.get_response(request)
