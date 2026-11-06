"""One smart search box for every list.

What it does, for each word you type (every word must be found somewhere in the record):
  * normal text search in the columns listed in `search_text`;
  * phone numbers and ID numbers are compared by their digits only, so 0501234567, +971 50 123 4567
    and 971-50-123-4567 all find the same record (`search_digits`);
  * records on the "many" side (a tenant's contacts and documents, an owner's contacts ...) are looked
    up with a sub-query instead of a join, so no duplicate rows and no slowdown (`search_related`);
  * codes like OWN-12, TEN-0012 or LSE12 find the right record (`search_codes`);
  * computed texts such as the unit reference RMT-101 (`search_aliases` + `search_alias_fields`);
  * when nothing is found, similar names are offered (a typo such as "Mansoury" still finds "Mansoori").
"""
import difflib
import re

from django.db.models import F, Q, Value
from django.db.models.functions import Replace

NOISE = (" ", "-", "+", "(", ")", "/", ".")
MAX_WORDS = 8
FUZZY_LIMIT = 20000  # most rows looked at for similar names


def digits_expr(field):
    """The column with spaces, dashes, brackets and dots removed (+971 50 123 4567 -> 971501234567)."""
    expr = F(field)
    for char in NOISE:
        expr = Replace(expr, Value(char), Value(""))
    return expr


def phone_digits(word):
    """The digits to look for when `word` looks like a number (0501234567 -> 501234567), else ''."""
    digits = re.sub(r"\D", "", word)
    if len(digits) < 3 or len(digits) < len(word) * 0.6:
        return ""
    return digits.lstrip("0")


def similar(wanted, word):
    if len(wanted) < 4 or len(word) < 3:
        return False
    if wanted in word or word.startswith(wanted):
        return True
    matcher = difflib.SequenceMatcher(None, wanted, word)
    return matcher.real_quick_ratio() >= 0.72 and matcher.quick_ratio() >= 0.72 and matcher.ratio() >= 0.72


class SmartSearchMixin:
    """Add to a ModelAdmin (before ModelAdmin). Keep `search_fields` too: Django needs it to show the search box."""

    show_full_result_count = False  # no second COUNT query over the whole table on every page

    search_text = ()          # columns searched as text (a single foreign key such as "owner__name" is fine)
    search_digits = ()        # phone / ID columns compared by digits only
    search_related = ()       # ((Model, "fk_name", [text columns], [digit columns]), ...)
    search_codes = ()         # (("code", "OWN", 4), ...): OWN12 / own-12 -> OWN-0012
    search_aliases = {}       # {"_ref": expression} extra computed columns
    search_alias_fields = ()  # names from search_aliases that are searched as text
    search_fuzzy = ()         # columns whose words are compared when nothing is found
    search_hint = ""          # shown in the search box and above the list

    # ---- the search ----------------------------------------------------------------
    def get_search_results(self, request, queryset, search_term):
        term = (search_term or "").strip()
        if not term:
            return queryset, False
        words = term.split()[:MAX_WORDS]
        aliases = {f"_sd{i}": digits_expr(field) for i, field in enumerate(self.search_digits)}
        aliases.update(self.search_aliases)
        base = queryset.alias(**aliases) if aliases else queryset
        found = base
        for word in words:
            found = found.filter(self._word_q(word))
        if len(term) >= 4 and self.search_fuzzy and not found.exists():
            similar_pks = self._similar(queryset, words)
            if similar_pks:
                request._erp_fuzzy = term
                return queryset.filter(pk__in=similar_pks), False
        return found, False

    def _word_q(self, word):
        q = Q()
        for field in self.search_text:
            q |= Q(**{f"{field}__icontains": word})
        for name in self.search_alias_fields:
            q |= Q(**{f"{name}__icontains": word})
        digits = phone_digits(word)
        if digits:
            for i in range(len(self.search_digits)):
                q |= Q(**{f"_sd{i}__contains": digits})
        for model, fk, text_fields, digit_fields in self.search_related:
            sub = model.objects.all()
            inner = Q()
            for field in text_fields:
                inner |= Q(**{f"{field}__icontains": word})
            if digits and digit_fields:
                sub = sub.alias(**{f"_rd{i}": digits_expr(field) for i, field in enumerate(digit_fields)})
                for i in range(len(digit_fields)):
                    inner |= Q(**{f"_rd{i}__contains": digits})
            if inner:
                q |= Q(pk__in=sub.filter(inner).values(fk))
        for field, prefix, width in self.search_codes:
            match = re.fullmatch(rf"(?i){prefix}[-\s]?0*(\d+)", word)
            if match:
                q |= Q(**{field: f"{prefix}-{int(match.group(1)):0{width}d}"})
        return q

    def _similar(self, queryset, words):
        """Primary keys of records with a word that looks like every typed word (typos allowed)."""
        wanted = [w.lower() for w in words if len(w) >= 3]
        if not wanted:
            return []
        hits = []
        for row in queryset.values_list("pk", *self.search_fuzzy)[:FUZZY_LIMIT]:
            texts = [str(value or "").lower() for value in row[1:]]
            present = {w for text in texts for w in re.split(r"[\s\-_/.,()]+", text) if w}
            if all(any(similar(w, p) or w in p for p in present) for w in wanted):
                hits.append(row[0])
        return hits
