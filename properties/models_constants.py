"""Plain constants and helpers for the properties app (no Django imports needed)."""

COMPANY_OWNER_NAME = "Company Owned"

# ---- Owner phone: optional "+", then digits, spaces, brackets or dashes (6 to 30 characters) ----
PHONE_REGEX = r"^\+?[0-9][0-9 ()\-]{5,28}$"
PHONE_ERROR = "Enter a valid phone number, for example +971 50 123 4567."


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
