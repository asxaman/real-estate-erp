"""Shared "Import" button and page for the Owners, Properties and Units lists."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import path, reverse
from unfold.decorators import action

from .owner_tools import MAX_IMPORT_ROWS


class ImportMixin:
    """Subclasses set import_noun, import_headers, import_notes and implement the two hooks."""

    import_noun = "records"
    import_headers = []
    import_notes = []

    def import_parse(self, upload):  # -> (objects, errors)
        raise NotImplementedError

    def import_template(self):  # -> HttpResponse
        raise NotImplementedError

    def _import_name(self, suffix=""):
        opts = self.model._meta
        return f"admin:{opts.app_label}_{opts.model_name}_import{suffix}"

    def has_import_permission(self, request, object_id=None):
        return self.has_add_permission(request)

    @action(description="Import", url_path="import-from-file", permissions=["import"])
    def import_button(self, request):
        return redirect(self._import_name())

    def get_urls(self):
        opts = self.model._meta
        base = f"{opts.app_label}_{opts.model_name}_import"
        custom = [
            path("import/", self.admin_site.admin_view(self.import_view), name=base),
            path("import/template/", self.admin_site.admin_view(self.import_template_view), name=base + "_template"),
        ]
        return custom + super().get_urls()

    def import_template_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        return self.import_template()

    def import_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        changelist = reverse(f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist")
        context = {
            **self.admin_site.each_context(request),
            "title": f"Import {self.import_noun}",
            "noun": self.import_noun,
            "errors": [],
            "headers": self.import_headers,
            "notes": self.import_notes,
            "max_rows": MAX_IMPORT_ROWS,
            "template_url": reverse(self._import_name("_template")),
            "back_url": changelist,
        }
        if request.method == "POST":
            upload = request.FILES.get("file")
            if not upload:
                context["errors"] = ["Choose a file first."]
            else:
                objects, errors = self.import_parse(upload)
                if errors:
                    context["errors"] = errors
                elif not objects:
                    context["errors"] = [f"The file has no {self.import_noun} in it."]
                else:
                    with transaction.atomic():
                        for obj in objects:
                            obj.save()
                    messages.success(request, f"Imported {len(objects)} {self.import_noun}.")
                    return redirect(changelist)
        return render(request, "properties/import.html", context)
