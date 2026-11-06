from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render

from .audit import log_tool
from .data_registry import TOOLS_OFF_MESSAGE, run_erasers, run_seeders, tools_allowed

ERASE_CONFIRMATION_WORD = "ERASE"


def data_tools_view(request):
    """Superuser-only page: seed sample data / erase all data."""
    if not request.user.is_superuser:
        raise PermissionDenied

    allowed = tools_allowed()
    if request.method == "POST":
        action = request.POST.get("action")
        if not allowed:
            messages.error(request, TOOLS_OFF_MESSAGE)
            return redirect("data_tools")

        if action == "seed":
            try:
                lines = run_seeders()
                log_tool(request.user, "Seed sample data", lines)
                for line in lines:
                    messages.success(request, line)
            except Exception as exc:  # show the problem instead of a crash page
                messages.error(request, f"Seeding failed and was rolled back: {exc}")

        elif action == "seed_small":
            try:
                from .mini_seed import seed_small

                lines = seed_small()
                log_tool(request.user, "Seed small sample set", lines)
                for line in lines:
                    messages.success(request, line)
            except Exception as exc:
                messages.error(request, f"Seeding failed and was rolled back: {exc}")

        elif action == "erase":
            typed = request.POST.get("confirm_text", "").strip()
            if typed != ERASE_CONFIRMATION_WORD:
                messages.error(
                    request,
                    f"Nothing was erased. You must type {ERASE_CONFIRMATION_WORD} "
                    "(in capital letters) to confirm.",
                )
            else:
                try:
                    include_users = request.POST.get("include_users") == "on"
                    lines = run_erasers(include_users=include_users)
                    log_tool(
                        request.user,
                        "Erase all data" + (" (including users and roles)" if include_users else ""),
                        lines,
                    )
                    for line in lines:
                        messages.warning(request, line)
                except Exception as exc:
                    messages.error(request, f"Erase failed and was rolled back: {exc}")

        return redirect("data_tools")

    User = get_user_model()
    context = {
        **admin.site.each_context(request),
        "title": "Data tools",
        "user_count": User.objects.count(),
        "superuser_count": User.objects.filter(is_superuser=True).count(),
        "confirmation_word": ERASE_CONFIRMATION_WORD,
        "tools_allowed": allowed,
    }
    return render(request, "core/data_tools.html", context)


def formats_view(request):
    """How dates are typed and shown (System settings), for the date boxes in the pop-ups."""
    from .formats import current_date_shape

    shape, label = current_date_shape()
    return JsonResponse({"date": shape, "label": label})


def banks_view(request):
    """The bank names for the drop-downs in the pop-ups (read by core/static/core/leases.js)."""
    from . import banks

    return JsonResponse({"banks": banks.names()})
