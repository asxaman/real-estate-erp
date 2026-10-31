from django.conf import settings
from django.contrib import admin
from django.urls import path, re_path
from django.views.generic import RedirectView

from core.media import media_view
from core.overview import dashboard_view, expiring_documents_view
from core.views import data_tools_view

urlpatterns = [
    # Superuser-only page with the "Seed sample data" and "Erase all data" buttons.
    # Must be listed BEFORE "admin/" so it is matched first.
    path("admin/data-tools/", admin.site.admin_view(data_tools_view), name="data_tools"),
    # The home page and the Expiring documents page (also before "admin/").
    path("admin/", admin.site.admin_view(dashboard_view), name="dashboard"),
    path("admin/expiring-documents/", admin.site.admin_view(expiring_documents_view), name="expiring_documents"),
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]

# Uploaded files (IDs, passports, photos) are handed out by Django only to people who are signed in
# and allowed to see them. Do NOT also serve the media folder straight from the web server.
urlpatterns += [
    re_path(r"^" + settings.MEDIA_URL.lstrip("/") + r"(?P<path>.+)$", media_view, name="media"),
]
