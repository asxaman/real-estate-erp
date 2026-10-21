"""Remembers which user is making the current web request, so that automatic
postings (for example when a cheque is marked as cleared) are recorded under
the right person in the audit log."""

from contextvars import ContextVar

_current = ContextVar("accounting_current_user", default=None)


def current_user():
    user = _current.get()
    if user is not None and getattr(user, "pk", None):
        return user
    return None


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current.set(getattr(request, "user", None))
        try:
            return self.get_response(request)
        finally:
            _current.reset(token)
