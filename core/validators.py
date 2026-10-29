"""The one password rule for this system."""
import re

from django.core.exceptions import ValidationError

RULE = "Minimum 8 characters, with at least 1 capital letter, 1 number and 1 special character."


class ComplexPasswordValidator:
    """At least 8 characters, 1 capital letter, 1 number and 1 special character."""

    def validate(self, password, user=None):
        problems = []
        if len(password) < 8:
            problems.append("at least 8 characters")
        if not re.search(r"[A-Z]", password):
            problems.append("1 capital letter")
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
