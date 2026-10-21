from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.urls import path, re_path
from django.views.generic import RedirectView
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    # Uploaded documents: only logged-in staff can open them.
    re_path(
        r"^media/(?P<path>.*)$",
        staff_member_required(serve),
        {"document_root": settings.MEDIA_ROOT},
    ),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]
