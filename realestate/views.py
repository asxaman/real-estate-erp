"""Views that are not part of the admin."""

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.views.static import serve

# Uploaded files live in media/documents/<kind>/<year>/<month>/<file>.
# Who may open them depends on the <kind>.
MEDIA_PERMISSIONS = {
    "ownerdocument": ("realestate.view_ownerdocument", "realestate.view_owner_private"),
    "tenantdocument": ("realestate.view_tenantdocument",),
    "propertydocument": ("realestate.view_propertydocument",),
    "leasedocument": ("realestate.view_leasedocument",),
    "expense": ("realestate.view_expense",),
}


def _allowed(user, path):
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if len(parts) < 3 or parts[0] != "documents" or ".." in parts:
        return user.is_superuser
    needed = MEDIA_PERMISSIONS.get(parts[1])
    if needed is None:
        return user.is_superuser
    return all(user.has_perm(p) for p in needed)


@staff_member_required
def protected_media(request, path):
    """Serve an uploaded document, but only to users allowed to see that kind of record."""
    if not _allowed(request.user, path):
        raise PermissionDenied
    return serve(request, path, document_root=settings.MEDIA_ROOT)
