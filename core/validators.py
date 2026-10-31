"""Rules for passwords and uploaded files."""
import re

from django.core.exceptions import ValidationError

RULE = (
    "Minimum 8 characters, with at least 1 capital letter, 1 small letter, "
    "1 number and 1 special character."
)


class ComplexPasswordValidator:
    """At least 8 characters, 1 capital letter, 1 small letter, 1 number and 1 special character."""

    def validate(self, password, user=None):
        problems = []
        if len(password) < 8:
            problems.append("at least 8 characters")
        if not re.search(r"[A-Z]", password):
            problems.append("1 capital letter")
        if not re.search(r"[a-z]", password):
            problems.append("1 small letter")
        if not re.search(r"[0-9]", password):
            problems.append("1 number")
        if not re.search(r"[^A-Za-z0-9]", password):
            problems.append("1 special character (for example ! @ # $ %)")
        if problems:
            raise ValidationError(
                "The password needs " + ", ".join(problems) + ".", code="password_too_weak"
            )

    def get_help_text(self):
        return RULE


# ---- uploaded files: the content must really be what the file name says -----------------------
def _is_pdf(head):
    return head.startswith(b"%PDF-")


def _is_jpeg(head):
    return head.startswith(b"\xff\xd8\xff")


def _is_png(head):
    return head.startswith(b"\x89PNG\r\n\x1a\n")


def _is_webp(head):
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


_SIGNATURES = {
    "pdf": (_is_pdf, "a PDF"),
    "jpg": (_is_jpeg, "a JPG picture"),
    "jpeg": (_is_jpeg, "a JPG picture"),
    "png": (_is_png, "a PNG picture"),
    "webp": (_is_webp, "a WebP picture"),
}


def validate_file_content(file):
    """
    Refuse a file whose first bytes do not match its extension (for example a program renamed to
    .pdf). Only newly uploaded files are looked at; files already saved are left alone.
    """
    if getattr(file, "_committed", None):  # an already saved FieldFile
        return
    name = getattr(file, "name", "") or ""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    check = _SIGNATURES.get(extension)
    if check is None:
        return  # other extensions are refused by the extension validator
    try:
        file.seek(0)
        head = file.read(16)
        file.seek(0)
    except Exception:
        return
    if not check[0](head or b""):
        raise ValidationError(
            f"This file is not really {check[1]}. Upload the original file, not a renamed one."
        )
