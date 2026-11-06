"""Upload several photos at once on a unit or property page (the 'Photos' tab)."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path

MAX_PHOTOS = 30


class PhotoUploadMixin:
    """Add to a ModelAdmin. Set photo_model and photo_fk (the name of the link to the page's record)."""

    photo_model = None
    photo_fk = ""

    def get_urls(self):
        extra = [
            path(
                "<int:pk>/photos/upload/",
                self.admin_site.admin_view(self.photos_upload_view),
                name=f"{self.opts.app_label}_{self.opts.model_name}_photos_upload",
            )
        ]
        return extra + super().get_urls()

    def photos_upload_view(self, request, pk):
        if request.method != "POST":
            return JsonResponse({"ok": False, "errors": ["This must be sent as a form."]}, status=405)
        obj = get_object_or_404(self.model, pk=pk)
        add_perm = f"{self.photo_model._meta.app_label}.add_{self.photo_model._meta.model_name}"
        if not (self.has_change_permission(request, obj) and request.user.has_perm(add_perm)):
            return JsonResponse({"ok": False, "errors": ["You do not have permission to add photos here."]}, status=403)
        files = request.FILES.getlist("photos")
        if not files:
            return JsonResponse({"ok": False, "errors": ["Choose at least one photo."]}, status=400)

        room = MAX_PHOTOS - self.photo_model.objects.filter(**{self.photo_fk: obj}).count()
        added, errors = 0, []
        for upload in files:
            if added >= room:
                errors.append(f"{upload.name}: not added, a page can have at most {MAX_PHOTOS} photos.")
                continue
            photo = self.photo_model(**{self.photo_fk: obj}, image=upload)
            try:
                photo.full_clean()
            except ValidationError as exc:
                text = " ".join(m for messages_ in exc.message_dict.values() for m in messages_) if hasattr(exc, "error_dict") else " ".join(exc.messages)
                errors.append(f"{upload.name}: {text}")
                continue
            photo.save()
            added += 1
        if added:
            messages.success(request, f"{added} photo{'s' if added != 1 else ''} added.")
        if errors and not added:
            return JsonResponse({"ok": False, "errors": errors}, status=400)
        if errors:
            messages.warning(request, " ".join(errors))
        return JsonResponse({"ok": True, "added": added, "errors": errors})
