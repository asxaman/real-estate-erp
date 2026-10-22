from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.generic import TemplateView
from unfold.views import UnfoldModelAdminViewMixin

from . import datatools, seeding

ERASE_PHRASE = "ERASE ALL DATA"


class DataToolsView(UnfoldModelAdminViewMixin, TemplateView):
    """Superuser-only page: seed sample data / erase all data."""

    title = "Data tools"
    permission_required = ()
    template_name = "core/data_tools.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Data tools"
        context["summary"] = datatools.data_summary()
        context["seeders"] = seeding.all_seeders()
        context["erase_phrase"] = ERASE_PHRASE
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")

        if action == "seed":
            try:
                for line in seeding.run_all():
                    messages.success(request, line)
            except Exception as exc:  # show the problem instead of a crash page
                messages.error(request, f"Seeding failed, nothing was saved: {exc}")

        elif action == "erase":
            phrase = request.POST.get("confirm_text", "").strip()
            password = request.POST.get("password", "")
            if phrase != ERASE_PHRASE:
                messages.error(request, f'You must type exactly "{ERASE_PHRASE}". Nothing was erased.')
            elif not request.user.check_password(password):
                messages.error(request, "Wrong password. Nothing was erased.")
            else:
                include_users = request.POST.get("include_users") == "on"
                try:
                    deleted = datatools.erase_all_data(
                        include_users=include_users, keep_user=request.user
                    )
                    total = sum(deleted.values())
                    extra = " Other user accounts were removed too." if include_users else ""
                    messages.success(request, f"All data erased ({total} records removed).{extra}")
                except Exception as exc:
                    messages.error(request, f"Erase failed, nothing was changed: {exc}")
        else:
            messages.error(request, "Unknown action.")

        return redirect("admin:core_data_tools")
