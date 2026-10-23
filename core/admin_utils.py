from django.utils.html import format_html, strip_tags


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


def format_amount(value):
    """95000 -> '95,000'; 1200.5 -> '1,200.50'; nothing -> '-'. For list pages."""
    if value is None or value == "":
        return "-"
    text = f"{value:,.2f}"
    return text[:-3] if text.endswith(".00") else text


def circle_avatar(url, initials, size=32):
    """Round picture, or a circle with initials when there is no picture."""
    if url:
        return format_html(
            '<img src="{}" alt="" style="width:{}px;height:{}px;border-radius:50%;object-fit:cover;">',
            url, size, size,
        )
    return format_html(
        '<span style="display:inline-flex;align-items:center;justify-content:center;'
        'width:{}px;height:{}px;border-radius:50%;background:#ede9fe;color:#6d28d9;'
        'font-weight:600;font-size:{}px;flex-shrink:0;">{}</span>',
        size, size, int(size * 0.4), initials,
    )


def avatar_html(user, size=32):
    """A user's round profile picture (or initials)."""
    profile = getattr(user, "profile", None)
    url = profile.photo.url if profile is not None and profile.photo else ""
    initials = ((user.first_name[:1] + user.last_name[:1]) or user.get_username()[:2]).upper()
    return circle_avatar(url, initials, size)


_BADGE_TONES = {
    "success": ("#dcfce7", "#166534"),
    "warning": ("#fef3c7", "#92400e"),
    "danger": ("#fee2e2", "#991b1b"),
    "info": ("#dbeafe", "#1e40af"),
    "neutral": ("#f3f4f6", "#374151"),
}


def badge(text, tone="neutral"):
    """A small coloured pill."""
    background, color = _BADGE_TONES.get(tone, _BADGE_TONES["neutral"])
    return format_html(
        '<span style="display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;'
        'font-weight:600;background:{};color:{};white-space:nowrap;">{}</span>',
        background, color, text,
    )
