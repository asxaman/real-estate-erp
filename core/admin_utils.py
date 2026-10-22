from django.utils.html import strip_tags


def move_help_text_to_tooltip(form):
    """
    Remove each field's visible help text and attach it to the input as
    data-erp-help. core/static/core/erp.js then shows it as a hover "i" icon
    next to the field's label.
    """
    for field in form.fields.values():
        if field.help_text:
            text = " ".join(strip_tags(str(field.help_text)).split())
            if text:
                field.widget.attrs["data-erp-help"] = text
            field.help_text = ""


class TooltipHelpMixin:
    """Add to a ModelAdmin (before ModelAdmin) to show help texts as hover tooltips."""

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)

        class TooltipForm(form_class):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                move_help_text_to_tooltip(self)

        TooltipForm.__name__ = form_class.__name__
        TooltipForm.__qualname__ = form_class.__qualname__
        return TooltipForm
