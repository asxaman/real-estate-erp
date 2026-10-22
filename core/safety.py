"""Rules that stop administrators from locking themselves (or everybody) out."""
from django.contrib.auth import get_user_model


def _is_last_active_superuser(target):
    User = get_user_model()
    return not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk).exists()


def superuser_change_problem(acting_user, target, is_superuser=None, is_active=None, is_staff=None):
    """
    Check a requested change to the account `target` (as saved in the database).
    Pass the NEW value of a flag, or None when it is not being changed.
    Returns (field_name, message) when the change must be refused, otherwise None.
    """
    if target.pk is None:
        return None
    is_self = acting_user is not None and acting_user.pk == target.pk

    if is_superuser is False and target.is_superuser:
        if is_self:
            return ("is_superuser", "You cannot remove your own superuser status. "
                    "Ask another superuser to do it.")
        if _is_last_active_superuser(target):
            return ("is_superuser", "This is the last active superuser; the system must "
                    "always have one.")

    if is_active is False and target.is_active:
        if is_self:
            return ("is_active", "You cannot deactivate your own account.")
        if target.is_superuser and _is_last_active_superuser(target):
            return ("is_active", "This is the last active superuser; the system must "
                    "always have one.")

    if is_staff is False and is_self and target.is_staff:
        return ("is_staff", "You cannot remove your own staff access.")

    return None


def user_delete_problem(acting_user, target):
    """Returns a message when `target` must not be deleted, otherwise None."""
    if acting_user is not None and acting_user.pk == target.pk:
        return "You cannot delete your own account."
    if target.is_superuser and target.is_active and _is_last_active_superuser(target):
        return "The last active superuser cannot be deleted."
    return None
