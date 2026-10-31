from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

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

# Profile pictures are served by Django itself while developing (DEBUG on).
# On a real server the web server (nginx etc.) serves the 'media' folder instead.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
