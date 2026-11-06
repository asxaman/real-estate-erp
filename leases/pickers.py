"""Search-as-you-type pickers for the lease form (tenant, unit, standalone property).

With thousands of units a drop-down that holds every choice would make the page heavy and slow to use. The
form only renders the option that is already chosen; the browser asks the search endpoint for the rest
(see core/static/core/leases.js)."""
from django import forms
from django.db.models import Q

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


def search(kind, text):
    """Up to LIMIT matches for the picker: [{id, label, sub}]. Same rules as the lease form's lists."""
    text = (text or "").strip()[:60]
    if kind == "tenant":
        found = Tenant.objects.filter(is_active=True, is_blacklisted=False)
        if text:
            found = found.filter(Q(name__icontains=text) | Q(code__icontains=text) | Q(email__icontains=text) | Q(phone__icontains=text))
        return [
            {"id": t.pk, "label": tenant_label(t), "sub": " · ".join(x for x in (t.code, t.phone) if x)}
            for t in found.order_by("name")[:LIMIT]
        ]
    if kind == "unit":
        found = Unit.objects.select_related("property").filter(property__is_active=True)
        if text:
            found = found.filter(
                Q(unit_number__icontains=text) | Q(property__name__icontains=text) | Q(property__code__icontains=text)
            )
        return [
            {"id": u.pk, "label": unit_label(u), "sub": f"{u.get_status_display()}" + (f" · asking AED {u.asking_rent:,.0f}" if u.asking_rent else "")}
            for u in found.order_by("property__code", "floor_sort", "unit_number")[:LIMIT]
        ]
    if kind == "property":
        found = Property.objects.exclude(property_type__in=Property.BUILDING_TYPES).filter(is_active=True)
        if text:
            found = found.filter(Q(name__icontains=text) | Q(code__icontains=text))
        return [
            {"id": p.pk, "label": property_label(p), "sub": p.get_property_type_display()}
            for p in found.order_by("code")[:LIMIT]
        ]
    return []
