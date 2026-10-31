"""
Protection against password guessing on the login page.

Five wrong passwords for the same user name within 15 minutes lock that user name for the rest of
the 15 minutes (30 wrong attempts from the same computer lock that computer). A correct login clears
the count. Locked out? Wait 15 minutes, or run:  python manage.py unlock_logins
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied
from django.utils import timezone

MAX_FAILURES_PER_USER = 5
MAX_FAILURES_PER_COMPUTER = 30
WINDOW = timedelta(minutes=15)


def _ip(request):
    return (request.META.get("REMOTE_ADDR") or None) if request is not None else None


def _name(username):
    return (username or "").strip().lower()[:150]


def minutes_left(username, ip):
    """0 when the login is allowed, otherwise how many minutes remain before it is."""
    from .models import LoginAttempt

    since = timezone.now() - WINDOW
    recent = LoginAttempt.objects.filter(created__gte=since)
    locked_until = None
    name = _name(username)
    if name:
        times = list(recent.filter(username=name).order_by("-created").values_list("created", flat=True)[:MAX_FAILURES_PER_USER])
        if len(times) >= MAX_FAILURES_PER_USER:
            locked_until = times[0] + WINDOW
    if ip:
        times = list(recent.filter(ip=ip).order_by("-created").values_list("created", flat=True)[:MAX_FAILURES_PER_COMPUTER])
        if len(times) >= MAX_FAILURES_PER_COMPUTER:
            until = times[0] + WINDOW
            locked_until = max(locked_until, until) if locked_until else until
    if locked_until is None:
        return 0
    seconds = (locked_until - timezone.now()).total_seconds()
    return max(1, int(seconds // 60) + 1) if seconds > 0 else 0


class LockoutBackend(ModelBackend):
    """The normal login check, refused while the user name (or computer) is locked."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get("username")
        wait = minutes_left(username, _ip(request))
        if wait:
            if request is not None:
                try:
                    messages.error(
                        request, f"Too many wrong passwords. Try again in {wait} minute{'s' if wait != 1 else ''}."
                    )
                except Exception:  # no message storage on this request
                    pass
            raise PermissionDenied
        return super().authenticate(request, username=username, password=password, **kwargs)


def record_failure(sender, credentials, request=None, **kwargs):
    """Signal: a login failed. Count it (unless the user name is already locked)."""
    from .models import LoginAttempt

    username = _name((credentials or {}).get("username"))
    ip = _ip(request)
    if minutes_left(username, ip):
        return  # already locked: do not extend the lock
    LoginAttempt.objects.create(username=username, ip=ip)
    LoginAttempt.objects.filter(created__lt=timezone.now() - timedelta(days=1)).delete()


def clear_failures(sender, request, user, **kwargs):
    """Signal: a correct login. Forget the earlier wrong passwords of that user name."""
    from .models import LoginAttempt

    LoginAttempt.objects.filter(username=_name(user.get_username())).delete()
