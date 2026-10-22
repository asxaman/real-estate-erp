from django.contrib import admin
from django.urls import path

admin.site.site_header = "Real Estate ERP"
admin.site.site_title = "Real Estate ERP"
admin.site.index_title = "Dashboard"

urlpatterns = [
    path("", admin.site.urls),  # the admin IS the application: open http://127.0.0.1:8000/
]
