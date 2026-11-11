"""An amount in words for receipts and invoices: 1250.50 -> 'One thousand two hundred fifty dirhams and fifty fils only'."""
from decimal import ROUND_HALF_UP, Decimal

ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
        "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
SCALES = [(10**9, "billion"), (10**6, "million"), (1000, "thousand")]


def _below_thousand(n):
    parts = []
    if n >= 100:
        parts.append(ONES[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        parts.append(TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(ONES[n])
    return " ".join(parts)


def number_words(n):
    if n == 0:
        return "zero"
    parts = []
    for size, name in SCALES:
        if n >= size:
            parts.append(f"{_below_thousand(n // size)} {name}")
            n %= size
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(value):
    value = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    dirhams, fils = int(value), int((value - int(value)) * 100)
    text = f"{number_words(dirhams)} dirham{'' if dirhams == 1 else 's'}"
    if fils:
        text += f" and {number_words(fils)} fils"
    return text[0].upper() + text[1:] + " only"
