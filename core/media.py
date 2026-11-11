"""
Downloads of uploaded files (documents, photos) - only for people who are logged in and are allowed
to see that kind of file. The files are never handed out straight from the media folder.
"""
import mimetypes

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.utils._os import safe_join

# folder inside "media" -> permission(s); holding ANY one of them is enough
FOLDER_PERMISSIONS = {
    "owner_photos": ("properties.view_owner",),
    "owner_documents": ("properties.view_ownerdocument",),
    "tenant_photos": ("tenants.view_tenant",),
    "tenant_documents": ("tenants.view_tenantdocument",),
    "record_documents": ("properties.view_propertydocument", "properties.view_unitdocument"),
    "unit_photos": ("properties.view_unitphoto",),
    "property_photos": ("properties.view_propertyphoto",),
    "lease_documents": ("leases.view_leasedocument",),
    "agreement_documents": ("agreements.view_owneragreement",),
    "vendor_documents": ("maintenance.view_vendordocument",),
    "maintenance_files": ("maintenance.view_maintenancerequest",),
    "bill_files": ("payables.view_vendorbill",),
    "expense_files": ("payables.view_expense",),
    "employee_documents": ("hr.view_employeedocument",),
    "leave_files": ("hr.view_leaverequest",),
    "profile_photos": (),  # any signed-in staff member (they appear in the sidebar and user list)
}


def may_open(user, path):
    """Is `user` allowed to open the media file at `path` (relative to the media folder)?"""
    if not (user.is_authenticated and user.is_active and user.is_staff):
        return False
    if user.is_superuser:
        return True
    folder = path.split("/", 1)[0]
    if folder not in FOLDER_PERMISSIONS:
        return False  # an unknown folder: only superusers
    needed = FOLDER_PERMISSIONS[folder]
    return not needed or any(user.has_perm(p) for p in needed)


def media_view(request, path):
    if not may_open(request.user, path):
        # Same answer for "not allowed" and "does not exist", so file names cannot be guessed.
        raise Http404
    try:
        full = safe_join(settings.MEDIA_ROOT, path)
    except Exception:
        raise Http404 from None
    if settings.MEDIA_ACCEL_REDIRECT:
        response = HttpResponse()
        response["X-Accel-Redirect"] = settings.MEDIA_ACCEL_REDIRECT.rstrip("/") + "/" + path
        response["Content-Type"] = mimetypes.guess_type(path)[0] or "application/octet-stream"
    else:
        try:
            response = FileResponse(open(full, "rb"), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            raise Http404 from None
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Disposition"] = "inline"
    return response
