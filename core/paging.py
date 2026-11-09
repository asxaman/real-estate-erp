"""Tools every list shares: rows per page (default 100), quick-filter chips with counts, a "what am I looking at" line.

Django treats every unknown address parameter as a database lookup (and redirects with ?e=1), so the
private parameters used here ('per' = rows per page, 'chip' = quick filter) are taken out of the request
while the list is built and put back into every link it makes.
"""
from django.contrib.admin.options import IncorrectLookupParameters
from django.db.models import Count

PER_PAGE_CHOICES = (50, 100, 200)
DEFAULT_PER_PAGE = 100
PRIVATE_PARAMS = ("per", "chip")
_PAGED = {}
JUMP_FROM_PAGES = 1  # every list shows "Page 1 of N" (even a single page)


def _page_number(text, pages):
    """The page the address asks for, kept inside 1..pages."""
    try:
        number = int(text)
    except (TypeError, ValueError):
        number = 1
    return min(max(number, 1), max(pages, 1))


def pager_numbers(page, link):
    """The page numbers for the bottom strip of a custom list page: 1 2 3 4 ... 20 21 (as on the admin lists).
    `link(page=n)` gives the address of a page. Returns [{"label", "url", "current"}]; url is "" for the dots and the current page."""
    numbers = []
    for item in page.paginator.get_elided_page_range(page.number):
        if item == page.paginator.ELLIPSIS:
            numbers.append({"label": item, "url": "", "current": False})
        else:
            numbers.append({"label": item, "url": "" if item == page.number else link(page=item), "current": item == page.number})
    return numbers


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
                self.page_shown = 1
                original = request.GET
                if any(name in original for name in PRIVATE_PARAMS):
                    clean = original.copy()
                    for name in PRIVATE_PARAMS:
                        clean.pop(name, None)
                    request.GET = clean
                try:
                    try:
                        super().__init__(request, *args, **kwargs)
                        self.page_shown = _page_number(request.GET.get("p"), self.paginator.num_pages)
                    except IncorrectLookupParameters:
                        # A page number that does not exist (typed by hand, or the list got shorter):
                        # show the nearest page instead of an error page.
                        wanted = request.GET.get("p")
                        if not wanted:
                            raise
                        first = request.GET.copy()
                        first["p"] = "1"
                        request.GET = first
                        super().__init__(request, *args, **kwargs)
                        self.page_shown = _page_number(wanted, self.paginator.num_pages)
                        fixed = first.copy()
                        fixed["p"] = str(self.page_shown)
                        request.GET = fixed
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
      * a line with the filters in use ("Status: Vacant x - Clear all", only when narrowed) and the search hint; the count is in the bottom strip.
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
            data["page_jump"] = self._page_jump(cl)
        return response

    @staticmethod
    def _page_jump(cl):
        """What the 'go to page' box needs: how many pages there are, the page shown, and a link with a hole for the number."""
        paginator = getattr(cl, "paginator", None)
        if paginator is None or getattr(cl, "show_all", False) or paginator.num_pages < JUMP_FROM_PAGES:
            return None
        return {
            "pages": paginator.num_pages, "page": getattr(cl, "page_shown", 1),
            "url": cl.get_query_string({"p": "__P__"}),
        }

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
