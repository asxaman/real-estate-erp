"""Checks for UAE-specific data entered into forms."""

import os
import re
from datetime import date

from django.core.exceptions import ValidationError

from .utils import iban_is_valid, normalize_passport

MAX_UPLOAD_MB = 10
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def validate_emirates_id(value):
    if not value:
        return
    if not re.fullmatch(r"784-\d{4}-\d{7}-\d", value):
        raise ValidationError(
            "Enter a valid Emirates ID: 15 digits starting with 784, "
            "for example 784-1990-1234567-1."
        )
    birth_year = int(value[4:8])
    if birth_year < 1900 or birth_year > date.today().year:
        raise ValidationError("The birth year inside this Emirates ID looks wrong.")


def validate_iban(value):
    if not value:
        return
    if not iban_is_valid(value):
        raise ValidationError(
            "This IBAN is not valid. A UAE IBAN has 23 characters: AE followed by "
            "21 digits (example: AE07 0331 2345 6789 0123 456)."
        )


def validate_passport(value):
    if not value:
        return
    if not re.fullmatch(r"[A-Z0-9]{5,20}", normalize_passport(value)):
        raise ValidationError(
            "A passport number has 5 to 20 letters and digits, with no symbols."
        )


def validate_trn(value):
    if not value:
        return
    if not re.fullmatch(r"\d{15}", value.replace(" ", "")):
        raise ValidationError("A UAE VAT Tax Registration Number (TRN) has 15 digits.")


def validate_phone(value):
    if not value:
        return
    cleaned = re.sub(r"[\s\-()]", "", value)
    if not re.fullmatch(r"\+?\d{7,15}", cleaned):
        raise ValidationError(
            "Enter a phone number using digits only, with an optional + at the "
            "start, for example +971 50 123 4567."
        )


def validate_makani(value):
    if not value:
        return
    if not re.fullmatch(r"\d{10}", re.sub(r"[\s\-]", "", value)):
        raise ValidationError("A Makani number has 10 digits, for example 31234 56789.")


def validate_document_file(file):
    """Only PDF, JPG and PNG files up to 10 MB."""
    # A file that is already saved has been checked before: skip it.
    if getattr(file, "_committed", False):
        return
    extension = os.path.splitext(file.name)[1].lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValidationError("Upload a PDF, JPG or PNG file.")
    if file.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"The file is too large. The limit is {MAX_UPLOAD_MB} MB.")
