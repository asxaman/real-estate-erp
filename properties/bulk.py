"""Actions that work on several selected records at once (Make active / inactive, Change owner)."""
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render

from .import_tools import _describe


def _count(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


def _report(request, modeladmin, changed, skipped, verb):
    meta = modeladmin.model._meta
    one, many = str(meta.verbose_name), str(meta.verbose_name_plural)
    if changed:
        messages.success(request, f"{verb} {_count(changed, one, many)}.")
    if skipped:
        text = f"{_count(len(skipped), one, many)} could not be changed: " + "; ".join(skipped[:4])
        if len(skipped) > 4:
            text += f"; and {len(skipped) - 4} more."
        messages.warning(request, text)
    if not changed and not skipped:
        messages.info(request, "Nothing to change - they were already like that.")


def set_active(modeladmin, request, queryset, active):
    """Switch the selected records on or off. A record that breaks a rule is skipped and explained."""
    changed, skipped = 0, []
    for obj in queryset:
        if obj.is_active == active:
            continue
        if getattr(obj, "is_company", False):
            skipped.append(f"{obj} (built into the system)")
            continue
        obj.is_active = active
        try:
            obj.clean()
        except ValidationError as exc:
            skipped.append(f"{obj} ({_describe(exc)})")
            continue
        obj.save()
        changed += 1
    _report(request, modeladmin, changed, skipped, "Made active:" if active else "Made inactive:")


def make_actions(label_plural):
    """The two actions, ready to put on a ModelAdmin: make_active and make_inactive."""

    @admin.action(description=f"Make selected {label_plural} active", permissions=["change"])
    def make_active(modeladmin, request, queryset):
        set_active(modeladmin, request, queryset, True)

    @admin.action(description=f"Make selected {label_plural} inactive", permissions=["change"])
    def make_inactive(modeladmin, request, queryset):
        set_active(modeladmin, request, queryset, False)

    return make_active, make_inactive


def change_owner(modeladmin, request, queryset):
    """
    Move the selected properties to another owner. First shows a page to pick the owner
    (and the management fee when the owner is not the company), then checks every property
    and changes all of them or none.
    """
    from .models import Owner, Property

    owners = Owner.objects.filter(is_active=True).order_by("-is_company", "name")
    properties = list(queryset.select_related("owner"))
    post = request.POST
    errors, form = [], {k: post.get(k, "") for k in ("owner", "fee_type", "fee_percent", "fee_fixed")}

    if post.get("apply"):
        owner = owners.filter(pk=form["owner"]).first() if form["owner"].isdigit() else None
        if owner is None:
            errors.append("Choose an owner.")
        else:
            prepared = []
            for prop in properties:
                if prop.owner_id == owner.pk:
                    errors.append(f"{prop.code}: already belongs to {owner.name}.")
                    continue
                prop.owner = owner
                if not owner.is_company and form["fee_type"]:
                    prop.management_fee_type = form["fee_type"]
                    if form["fee_type"] == Property.FeeType.PERCENT:
                        prop.management_fee_percent = form["fee_percent"] or 0
                        prop.management_fee_fixed = None
                    else:
                        prop.management_fee_fixed = form["fee_fixed"] or None
                try:
                    prop.full_clean(exclude=["created_at"])
                except ValidationError as exc:
                    errors.append(f"{prop.code}: {_describe(exc)}")
                    continue
                prepared.append(prop)
            if not errors:
                with transaction.atomic():
                    for prop in prepared:
                        prop.save()
                messages.success(
                    request, f"Moved {_count(len(prepared), 'property', 'properties')} to {owner.name}."
                )
                return redirect(request.path + ("?" + request.GET.urlencode() if request.GET else ""))

    context = {
        **modeladmin.admin_site.each_context(request),
        "title": "Change owner",
        "properties": properties,
        "owners": owners,
        "form": form,
        "errors": errors,
        "fee_types": Property.FeeType.choices,
        "company_ids": [str(o.pk) for o in owners if o.is_company],
        "selected": [str(p.pk) for p in properties],
        "back_url": request.path,
    }
    return render(request, "properties/change_owner.html", context)

