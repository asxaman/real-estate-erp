"""Group the side menu by kind of work instead of one long alphabetical list."""

GROUPS = [
    ("Daily work", ["Cheque", "MaintenanceRequest", "Expense"]),
    ("Properties and people", ["Property", "Unit", "Owner", "Tenant"]),
    ("Contracts", ["Lease"]),
    ("Owner payouts", ["OwnerStatement"]),
    ("Setup", ["Vendor"]),
]


def install(site):
    original = site.get_app_list

    def get_app_list(request, app_label=None):
        apps = original(request, app_label)
        if app_label is not None:
            return apps  # a single app page: leave it alone
        ours = [app for app in apps if app["app_label"] == "realestate"]
        others = [app for app in apps if app["app_label"] != "realestate"]
        if not ours:
            return apps

        models = {model["object_name"]: model for model in ours[0]["models"]}
        groups = []
        for number, (name, object_names) in enumerate(GROUPS):
            items = [models.pop(n) for n in object_names if n in models]
            if items:
                groups.append(
                    {**ours[0], "name": name, "app_label": f"realestate_group_{number}", "models": items}
                )
        leftovers = list(models.values())
        if leftovers:  # a model we forgot to list is still shown, never lost
            groups.append(
                {**ours[0], "name": "Other", "app_label": "realestate_group_other", "models": leftovers}
            )
        return groups + others

    site.get_app_list = get_app_list
