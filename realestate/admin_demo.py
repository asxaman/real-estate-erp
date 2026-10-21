"""A page where the superuser loads and removes demo data."""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from .demo import DemoError, demo_counts, has_demo_data, remove_demo, seed_demo
from .models import DemoRecord


@admin.register(DemoRecord)
class DemoRecordAdmin(UnfoldModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        here = reverse("admin:realestate_demorecord_changelist")

        if request.method == "POST":
            action = request.POST.get("action")
            if action == "seed":
                try:
                    result = seed_demo()
                    messages.success(
                        request,
                        f"Demo data loaded ({result['records']} records). Have a look around!",
                    )
                except DemoError as error:
                    messages.warning(request, str(error))
            elif action == "remove":
                result = remove_demo()
                messages.success(request, f"Removed {result['removed']} demo record(s).")
                for line in result["kept"]:
                    messages.warning(request, f"Kept, because real data uses it: {line}")
            return HttpResponseRedirect(here)

        context = {
            **self.admin_site.each_context(request),
            "title": "Demo data",
            "loaded": has_demo_data(),
            "counts": demo_counts(),
        }
        return TemplateResponse(request, "admin/realestate/demo/page.html", context)
