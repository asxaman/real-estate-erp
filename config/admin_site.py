"""The admin site with one clean address per module.

Django builds addresses as /admin/<app>/<model>/ (for example /admin/leases/leasepayment/), which makes a module look as if
it sits inside another one. Here every module gets its own plain address (/admin/tenant-payments/). The menu grouping on the
left is unchanged. The old addresses still work: they send you to the new ones.
"""
from django.http import HttpResponsePermanentRedirect
from django.urls import path, re_path
from django.urls.resolvers import RoutePattern

# old address (app/model)  ->  new address
ROUTES = {
    "properties/owner": "owners",
    "properties/property": "properties",
    "properties/unit": "units",
    "tenants/tenant": "tenants",
    "leases/lease": "leases",
    "leases/leasepayment": "tenant-payments",
    "auth/user": "users",
    "auth/group": "groups",
    "core/systemsettings": "system-settings",
    "core/auditentry": "audit-log",
    "core/bank": "banks",
}


def give_modules_clean_addresses(site):
    """Rewrites the address of every module on the admin site. (Unfold supplies the admin site itself, so it is adjusted here
    instead of being replaced.) Called once from config/urls.py."""
    original = site.get_urls

    def get_urls():
        urls = original()
        for item in urls:
            pattern = getattr(item, "pattern", None)
            old = str(pattern).rstrip("/") if pattern is not None else ""
            if old in ROUTES and hasattr(item, "url_patterns"):
                item.pattern = RoutePattern(ROUTES[old] + "/", name=getattr(pattern, "name", None), is_endpoint=False)
        # The reports live under /admin/reports/ (the Lease admin supplies the pages; the address names stay the same).
        from leases.models import Lease

        lease_admin = site._registry.get(Lease)
        reports = []
        if lease_admin is not None:
            reports = [
                path("reports/rent-roll/", site.admin_view(lease_admin.rent_roll_view), name="leases_lease_rentroll"),
                path("reports/monthly/", site.admin_view(lease_admin.monthly_view), name="leases_lease_monthly"),
            ]
        return reports + urls

    site.get_urls = get_urls


def _old_to_new(request, old, rest):
    query = request.META.get("QUERY_STRING", "")
    return HttpResponsePermanentRedirect(f"/admin/{ROUTES[old]}/{rest}" + (f"?{query}" if query else ""))


# Used in config/urls.py (before the admin itself): /admin/leases/leasepayment/5/change/ -> /admin/tenant-payments/5/change/
legacy_redirects = [
    # the two reports used to sit under /admin/leases/
    re_path(r"^admin/leases/(?P<report>rent-roll|monthly)/$",
            lambda request, report: HttpResponsePermanentRedirect(f"/admin/reports/{report}/"), name="legacy_report_address"),
    re_path(r"^admin/(?P<old>" + "|".join(ROUTES) + r")/(?P<rest>.*)$", _old_to_new, name="legacy_admin_address"),
]
