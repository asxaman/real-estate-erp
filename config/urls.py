from django.contrib import admin
from django.urls import path, re_path
from django.views.generic import RedirectView

from realestate.views import protected_media

urlpatterns = [
    path("admin/", admin.site.urls),
    # Uploaded documents: only staff who may see that kind of record can open them.
    re_path(r"^media/(?P<path>.*)$", protected_media),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]
