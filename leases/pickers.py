"""Search-as-you-type pickers for the lease form (tenant, unit, standalone property).

With thousands of units a drop-down that holds every choice would make the page heavy and slow to use. The
form only renders the option that is already chosen; the browser asks the search endpoint for the rest
(see core/static/core/leases.js)."""
import re

from django import forms
from django.db.models import Q, Value
from django.db.models.functions import Concat

from core.search import digits_expr, phone_digits

from properties.models import Property, Unit
from tenants.models import Tenant

LIMIT = 20


def tenant_label(tenant):
    return tenant.name


def unit_label(unit):
    return f"{unit.property.code}-{unit.unit_number} · {unit.property.name}"


def property_label(prop):
    return f"{prop.code} · {prop.name}"


class SearchSelect(forms.Select):
    """A select that renders only the chosen option, so a huge list never reaches the page."""

    def optgroups(self, name, value, attrs=None):
        iterator = self.choices
        queryset, field = getattr(iterator, "queryset", None), getattr(iterator, "field", None)
        if queryset is None or field is None:
            return super().optgroups(name, value, attrs)
        chosen = [str(v) for v in value if v not in (None, "")]
        rows = [("", "---------")]
        if chosen:
            for obj in queryset.filter(pk__in=chosen):
                rows.append((field.prepare_value(obj), field.label_from_instance(obj)))
        return [
            (None, [self.create_option(name, val, label, str(val) in chosen, index, attrs=attrs)], index)
            for index, (val, label) in enumerate(rows)
        ]


def _words(text):
    """The words typed, without separators such as the dot in a chosen label ("B010-301 · Jumeirah Hub")."""
    return [w for w in re.split(r"\s+", text) if re.search(r"[0-9A-Za-z\u0600-\u06FF]", w)][:8]


def _every_word(queryset, words, columns, digit_columns=()):
    """Keep rows where each typed word is found in at least one column (or, for numbers, in a phone's digits)."""
    if digit_columns:
        queryset = queryset.alias(**{f"_pd{i}": digits_expr(c) for i, c in enumerate(digit_columns)})
    for word in words:
        q = Q()
        for column in columns:
            q |= Q(**{f"{column}__icontains": word})
        digits = phone_digits(word)
        if digits:
            for i in range(len(digit_columns)):
                q |= Q(**{f"_pd{i}__contains": digits})
        queryset = queryset.filter(q)
    return queryset


def search(kind, text):
    """Up to LIMIT matches for the picker: [{id, label, sub}]. Same rules as the lease form's lists.

    Every typed word must be found somewhere, in any order, so "B010-301", "B010 301", "b010301",
    "301 jumeirah" and the full chosen label all find the unit."""
    text = (text or "").strip()[:80]
    words = _words(text)
    if kind == "tenant":
        found = Tenant.objects.filter(is_active=True, is_blacklisted=False)
        found = _every_word(found, words, ("name", "code", "email", "phone"), ("phone",))
        return [
            {"id": t.pk, "label": tenant_label(t), "sub": " \u00b7 ".join(x for x in (t.code, t.phone) if x)}
            for t in found.order_by("name")[:LIMIT]
        ]
    if kind == "unit":
        found = Unit.objects.select_related("property").filter(property__is_active=True).alias(
            _ref=Concat("property__code", Value("-"), "unit_number"),
            _ref2=Concat("property__code", "unit_number"),
        )
        found = _every_word(found, words, ("_ref", "_ref2", "unit_number", "property__name", "property__code"))
        return [
            {"id": u.pk, "label": unit_label(u), "sub": f"{u.get_status_display()}" + (f" \u00b7 asking AED {u.asking_rent:,.0f}" if u.asking_rent else "")}
            for u in found.order_by("property__code", "floor_sort", "unit_number")[:LIMIT]
        ]
    if kind == "property":
        found = Property.objects.exclude(property_type__in=Property.BUILDING_TYPES).filter(is_active=True)
        found = _every_word(found, words, ("name", "code"))
        return [
            {"id": p.pk, "label": property_label(p), "sub": p.get_property_type_display()}
            for p in found.order_by("code")[:LIMIT]
        ]
    return []
