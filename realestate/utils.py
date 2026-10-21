"""Small helper functions with no Django inside, so they are easy to test."""

import calendar
import re
from datetime import date
from decimal import ROUND_DOWN, Decimal


def add_months(d, months):
    """Return date `d` moved forward by `months`, keeping the day where possible.

    31 Jan + 1 month = 28/29 Feb (the last day of the shorter month).
    """
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def split_amount(total, parts):
    """Split `total` into `parts` equal amounts in fils (2 decimals).

    Any rounding difference goes on the last amount, so the parts always add
    up to exactly `total`.
    """
    total = Decimal(total)
    base = (total / parts).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    amounts = [base] * parts
    amounts[-1] = total - base * (parts - 1)
    return amounts


def normalize_emirates_id(value):
    """784199012345671 or '784 1990 1234567 1' -> '784-1990-1234567-1'."""
    text = str(value).strip()
    digits = re.sub(r"[\s\-]", "", text)
    if re.fullmatch(r"\d{15}", digits):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:14]}-{digits[14]}"
    return text


def normalize_iban(value):
    """'ae07 0331 2345 6789 0123 456' -> 'AE070331234567890123456'."""
    return re.sub(r"\s+", "", str(value)).upper()


def normalize_passport(value):
    """' ab 123456 ' -> 'AB123456'."""
    return re.sub(r"\s+", "", str(value)).upper()


def iban_is_valid(value):
    """Full IBAN check: shape, UAE length, and the mod-97 check digits."""
    iban = normalize_iban(value)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    if iban.startswith("AE") and not re.fullmatch(r"AE\d{21}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1
