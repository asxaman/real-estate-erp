"""Text fields that tidy up what you type (spaces, capitals, dashes)."""

from django.db import models

from .utils import normalize_emirates_id, normalize_iban, normalize_passport
from .validators import validate_emirates_id, validate_iban, validate_passport


class EmiratesIDField(models.CharField):
    """Stored as 784-YYYY-NNNNNNN-N whatever way it was typed."""

    default_validators = [validate_emirates_id]
    description = "UAE Emirates ID number"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 18)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return normalize_emirates_id(value) if value else value


class IBANField(models.CharField):
    """Stored in capitals without spaces, and checked with the IBAN check digits."""

    default_validators = [validate_iban]
    description = "IBAN bank account number"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 34)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return normalize_iban(value) if value else value


class PassportField(models.CharField):
    """Stored in capitals without spaces."""

    default_validators = [validate_passport]
    description = "Passport number"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 20)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return normalize_passport(value) if value else value
