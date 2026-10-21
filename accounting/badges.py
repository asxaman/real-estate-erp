"""The red number next to "Daily postings" in the menu."""


def postings_badge(request):
    """How many postings are waiting (nothing is shown when there are none)."""
    user = getattr(request, "user", None)
    if user is None or not user.has_perm("accounting.view_journalline"):
        return None
    from .rules_more import waiting_count

    count = waiting_count()
    return str(count) if count else None
