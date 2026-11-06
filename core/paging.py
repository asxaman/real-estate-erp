"""Tools every list shares: rows per page (default 100), quick-filter chips with counts, a "what am I looking at" line.

Django treats every unknown address parameter as a database lookup (and redirects with ?e=1), so the
private parameters used here ('per' = rows per page, 'chip' = quick filter) are taken out of the request
while the list is built and put back into every link it makes.
"""
from django.db.models import Count, Q

PER_PAGE_CHOICES = (50, 100, 200)
DEFAULT_PER_PAGE = 100
PRIVATE_PARAMS = ("per", "chip")
_PAGED = {}


def paged(base):
    """A change list class that honours ?per=50|100|200 and ?chip=<quick filter>."""
    if base not in _PAGED:
        class Paged(base):
            def __init__(self, request, *args, **kwargs):
                self.per = None
                self.chip = ""
                value = request.GET.get("per", "")
                if value.isdigit() and int(value) in PER_PAGE_CHOICES:
                    self.per = int(value)
                self.chip = request.GET.get("chip", "")[:40]
                request._erp_params = {"per": self.per, "chip": self.chip}
                original = request.GET
                if any(name in original for name in PRIVATE_PARAMS):
                    clean = original.copy()
                    for name in PRIVATE_PARAMS:
                        clean.pop(name, None)
                    request.GET = clean
                try:
                    super().__init__(request, *args, **kwargs)
                finally:
                    request.GET = original

            def get_results(self, request):
                if self.per:
                    self.list_per_page = self.per
                super().get_results(request)

            def get_query_string(self, new_params=None, remove=None):
                new_params = dict(new_params or {})
                remove = remove or []
                if self.per and "per" not in new_params and "per" not in remove:
                    new_params["per"] = self.per
                if self.chip and "chip" not in new_params and "chip" not in remove:
                    new_params["chip"] = self.chip
                return super().get_query_string(new_params, remove)

        Paged.__name__ = Paged.__qualname__ = f"Paged{base.__name__}"
        _PAGED[base] = Paged
    return _PAGED[base]


class RowsPerPageMixin:
    """
    Add to a ModelAdmin (before ModelAdmin). Gives the list:
      * 100 rows per page and a "Rows per page" choice (50 / 100 / 200);
      * quick-filter chips with counts: define `quick_chips(self)` returning [(key, "Label", tone, Q), ...];
      * a summary line ("Showing 37 of 2,018 - Status: Vacant x - Clear all") and the search hint.
    """

    list_per_page = DEFAULT_PER_PAGE
    list_before_template = "core/list_bar.html"
    search_hint = ""

    def quick_chips(self):
        return []

    def get_changelist(self, request, **kwargs):
        return paged(super().get_changelist(request, **kwargs))

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        key = getattr(request, "_erp_params", {}).get("chip")
        if key:
            for chip_key, _label, _tone, condition in self.quick_chips():
                if chip_key == key:
                    return queryset.filter(condition)
        return queryset

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        data = getattr(response, "context_data", None)
        if data is not None and data.get("cl") is not None:
            cl = data["cl"]
            data["per_options"] = [(n, cl.get_query_string({"per": n}, ["p"]), n == cl.list_per_page) for n in PER_PAGE_CHOICES]
            data["quick_chips"] = self._chip_buttons(cl)
            data["list_summary"] = self._summary(request, cl)
            data["search_hint"] = self.search_hint
            term = getattr(request, "_erp_fuzzy", "")
            data["search_note"] = f"Nothing matches “{term}” exactly, so similar names are shown." if term else ""
        return response

    # ---- chips -----------------------------------------------------------------------
    def _chip_buttons(self, cl):
        chips = self.quick_chips()
        if not chips:
            return []
        counts = self.model._default_manager.aggregate(
            **{f"c_{key}": Count("pk", filter=condition, distinct=True) for key, _label, _tone, condition in chips}
        )
        current = getattr(cl, "chip", "")
        buttons = [{
            "label": "All", "count": None, "tone": "neutral", "active": not current,
            "url": cl.get_query_string(remove=["chip", "p"]),
        }]
        for key, label, tone, _condition in chips:
            active = key == current
            buttons.append({
                "label": label, "count": counts[f"c_{key}"], "tone": tone, "active": active,
                "url": cl.get_query_string(remove=["chip", "p"]) if active else cl.get_query_string({"chip": key}, ["p"]),
            })
        return buttons

    # ---- "what am I looking at" ---------------------------------------------------------
    def _summary(self, request, cl):
        tags = []
        for spec in cl.filter_specs:
            choices = list(spec.choices(cl))
            if len(choices) < 2:
                continue
            chosen = [c for c in choices[1:] if c["selected"]]
            if chosen:
                tags.append({"label": f"{spec.title}: {chosen[0]['display']}", "remove": choices[0]["query_string"]})
        current = getattr(cl, "chip", "")
        for key, label, _tone, _condition in self.quick_chips():
            if key == current:
                tags.append({"label": label, "remove": cl.get_query_string(remove=["chip", "p"])})
        if cl.query:
            tags.append({"label": f"Search “{cl.query}”", "remove": cl.get_query_string(remove=["q", "p"])})
        narrowed = bool(tags)
        total = self.model._default_manager.count() if narrowed else cl.result_count
        per = getattr(cl, "per", None)
        return {
            "shown": cl.result_count, "total": total, "narrowed": narrowed, "tags": tags,
            "clear_url": f"?per={per}" if per else "?",
        }
