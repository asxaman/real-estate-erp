from django.utils.html import format_html, strip_tags
from django.utils.safestring import mark_safe


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


class ExportPermissionMixin:
    """Exporting to Excel / CSV needs its own permission (for example "Can export tenants")."""

    def has_export_permission(self, request):
        opts = self.model._meta
        return request.user.has_perm(f"{opts.app_label}.export_{opts.model_name}")


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


# ---- WhatsApp button ------------------------------------------------------------------------
# A green round button with a chat bubble (our own drawing). It opens a WhatsApp chat with the number.
_WHATSAPP_ICON = mark_safe(
    '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">'
    '<circle cx="12" cy="12" r="12" fill="#22c55e"/>'
    '<rect x="5.2" y="5.6" width="13.6" height="9.8" rx="3" fill="#fff"/>'
    '<path d="M8 15.2l-.5 3.6 4.1-3.6z" fill="#fff"/>'
    '<circle cx="9.2" cy="10.5" r="1.05" fill="#16a34a"/>'
    '<circle cx="12" cy="10.5" r="1.05" fill="#16a34a"/>'
    '<circle cx="14.8" cy="10.5" r="1.05" fill="#16a34a"/>'
    "</svg>"
)


def whatsapp_number(phone):
    """
    The digits WhatsApp wants (country code first, no + or spaces).
    '050 123 4567' -> '971501234567'; '+44 7700 900123' -> '447700900123'; '' -> ''.
    A number that starts with 0 is only taken as a UAE number when it has the shape of one;
    otherwise there is no button (a wrong country code would message a stranger).
    """
    text = (phone or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("00"):
        return digits[2:]
    if text.startswith("+"):
        return digits
    if digits.startswith("971"):
        return digits
    if digits.startswith("0"):
        rest = digits[1:]
        if (len(rest) == 9 and rest[0] == "5") or (len(rest) == 8 and rest[0] in "234679"):
            return "971" + rest  # a UAE mobile or landline written the local way
        return ""  # country unknown
    if len(digits) == 9 and digits[0] == "5":
        return "971" + digits  # a UAE mobile written without the 0
    return digits


def whatsapp_link(phone):
    """A small green WhatsApp button for a phone number ('' when there is no number)."""
    number = whatsapp_number(phone)
    if not number:
        return ""
    return format_html(
        '<a class="erp-wa" href="https://wa.me/{}" target="_blank" rel="noopener" '
        'title="Chat on WhatsApp" aria-label="Chat on WhatsApp">{}</a>',
        number, _WHATSAPP_ICON,
    )
