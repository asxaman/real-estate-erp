"""A nicer file field: preview plus View / Edit / Delete buttons (behaviour is in erp.js)."""
from pathlib import PurePosixPath

from django import forms

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class FileActionsWidget(forms.ClearableFileInput):
    """
    Replaces Django's plain "Currently: file / Clear / Change" control.

    * preview=True shows a large picture (profile photos and logos).
    * round=True shows the preview as a circle (people's pictures).
    * compact=True is a small row of icon buttons (for tables such as the Documents tab).
    "Delete" only ticks the hidden clear box; the file is removed when the form is saved.
    """

    template_name = "core/widgets/file_actions.html"

    def __init__(self, attrs=None, preview=False, compact=False, accept="", round=False):
        attrs = dict(attrs or {})
        if accept:
            attrs["accept"] = accept
        super().__init__(attrs)
        self.preview = preview
        self.compact = compact
        self.round = round

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        widget = context["widget"]
        url = ""
        is_image = False
        if widget.get("is_initial"):
            url = widget["value"].url
            is_image = PurePosixPath(str(widget["value"].name)).suffix.lower() in IMAGE_EXTENSIONS
        widget["erp_url"] = url
        widget["erp_is_image"] = is_image
        widget["erp_preview"] = self.preview
        widget["erp_compact"] = self.compact
        widget["erp_round"] = self.round
        widget["erp_kind"] = "Image" if is_image else "Document"
        return context


def use_file_widget(form, field_name, **options):
    """Swap one file field of a form over to the FileActionsWidget."""
    field = form.fields.get(field_name)
    if field is not None:
        widget = FileActionsWidget(**options)
        widget.is_required = field.required
        field.widget = widget
