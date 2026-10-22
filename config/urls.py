from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from core.views import data_tools_view

urlpatterns = [
    # Superuser-only page with the "Seed sample data" and "Erase all data" buttons.
    # Must be listed BEFORE "admin/" so it is matched first.
    path("admin/data-tools/", admin.site.admin_view(data_tools_view), name="data_tools"),
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]
