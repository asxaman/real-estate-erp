"""Plain constants and helpers for the properties app (no Django imports needed)."""

COMPANY_OWNER_NAME = "Company Owned"
COMPANY_OWNER_CODE = "OWN-0000"  # the built-in owner; real owners get OWN-0001, OWN-0002 ...

# Documents that expire within this many days show a warning.
EXPIRY_WARNING_DAYS = 30

# ---- Bank details ----
SWIFT_REGEX = r"^[A-Za-z]{4}[A-Za-z]{2}[A-Za-z0-9]{2}([A-Za-z0-9]{3})?$"
SWIFT_ERROR = "Enter a valid SWIFT/BIC code (8 or 11 letters and numbers), for example EBILAEAD."


def normalise_iban(value):
    return "".join((value or "").split()).upper()


def iban_is_valid(value):
    """Standard IBAN check: length, characters and the mod-97 checksum (any country)."""
    iban = normalise_iban(value)
    if not (15 <= len(iban) <= 34) or not iban[:2].isalpha() or not iban[2:4].isdigit():
        return False
    if not iban.isalnum():
        return False
    moved = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in moved)
    return int(digits) % 97 == 1


def mask_iban(value):
    iban = normalise_iban(value)
    return f"{iban[:2]}** **** {iban[-4:]}" if len(iban) > 8 else iban

# ---- Owner phone: optional "+", then digits, spaces, brackets or dashes (6 to 30 characters) ----
PHONE_REGEX = r"^\+?[0-9][0-9 ()\-]{5,28}$"
PHONE_ERROR = "Enter a valid phone number, for example +971 50 123 4567."


def phone_key(value):
    """A phone number reduced to its significant digits, so +971 50 123 4567 and 050 123 4567 match."""
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if digits.startswith("00971"):
        digits = digits[5:]
    elif digits.startswith("971") and len(digits) >= 11:
        digits = digits[3:]
    return digits.lstrip("0")


def lost_zero_problem(text):
    """
    Excel turns 0501234567 into 501234567 when a cell is formatted as a number.
    Returns a short explanation when a phone number looks like that, otherwise "".
    """
    text = (text or "").strip()
    if text.isdigit() and (
        (len(text) == 9 and text[0] == "5")  # a UAE mobile without its 0
        or (len(text) == 8 and text[0] in "234679")  # a UAE landline without its 0
    ):
        return (
            "looks like it lost its leading zero (Excel does this to numbers). "
            "Write it as +971 50 123 4567, or format the Phone column as Text."
        )
    return ""


# ---- Emirates ID: 784-YYYY-NNNNNNN-C (15 digits) ----
def emirates_id_digits(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())


def emirates_id_is_valid(value):
    text = (value or "").strip()
    digits = emirates_id_digits(text)
    # only digits and dashes/spaces are allowed, 15 digits, starting with 784 (the UAE code)
    return (
        len(digits) == 15
        and digits.startswith("784")
        and all(ch.isdigit() or ch in "- " for ch in text)
    )


def normalise_emirates_id(value):
    digits = emirates_id_digits(value)
    return f"{digits[:3]}-{digits[3:7]}-{digits[7:14]}-{digits[14]}"


def normalise_unit_number(value):
    """' g 01 ' -> 'G 01': trim, collapse repeated spaces, capital letters."""
    return " ".join((value or "").split()).upper()


# ---- Property code: 3 to 6 letters/numbers, starting with a letter (RMT, ZV01 ...) ----
CODE_REGEX = r"(?i)^[A-Z][A-Z0-9]{2,5}$"
CODE_ERROR = "Use 3 to 6 letters or numbers, starting with a letter (for example RMT or ZV01)."

# ---- Unit floor codes ----
FLOOR_CODES = ["G", "LG", "UG", "R", "RF", "PH", "M", "MZ", "A", "S", "SC", "GF", "L", "LV", "E"]
FLOOR_REGEX = r"(?i)^(?:" + "|".join(FLOOR_CODES) + r"|[BP][1-9]\d?|[1-9]\d{0,2})$"
FLOOR_ERROR = (
    "Not a valid floor. Use G, LG, UG, R, RF, PH, M, MZ, A, S, SC, GF, L, LV, E, "
    "B1-B99, P1-P99, or a floor number (1, 2, 3 ...)."
)
FLOOR_HELP = (
    "Allowed: G, LG, UG, R, RF, PH, M, MZ, A, S, SC, GF, L, LV, E, "
    "B1, B2, B3 ... (basements), P1, P2, P3 ... (podium levels), "
    "or a floor number: 1, 2, 3 ..."
)

# Position used only to sort units sensibly (basements first, then ground, floors, roof, penthouse).
# Change the numbers here if you want a different order; units are re-sorted after `migrate`.
_NAMED_FLOOR_ORDER = {
    "LG": -5,
    "G": 0,
    "GF": 0,
    "E": 0,
    "UG": 5,
    "M": 8,
    "MZ": 8,
    "L": 9,
    "LV": 9,
    "A": 9,
    "S": 9,
    "SC": 9,
    "R": 9000,
    "RF": 9000,
    "PH": 9500,
}


def floor_sort_key(code):
    """Turn a floor code such as 'B2', 'G' or '12' into a number used for sorting."""
    code = (str(code) if code is not None else "").strip().upper()
    if not code:
        return 0
    if code in _NAMED_FLOOR_ORDER:
        return _NAMED_FLOOR_ORDER[code]
    if code[0] == "B" and code[1:].isdigit():
        return -10 * int(code[1:])
    if code[0] == "P" and code[1:].isdigit():
        return min(int(code[1:]), 9)
    if code.isdigit():
        return 10 * int(code)
    return 0
