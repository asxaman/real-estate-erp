from django.contrib.auth import get_user_model

from .models import CompanySettings

# Build plan, shown on the dashboard. Mark a module "done" when it is finished.
MODULES = [
    ("1", "Foundation: users, roles, company settings, data tools", "done"),
    ("2", "Properties, buildings and units", "next"),
    ("3", "Owners and tenants", "planned"),
    ("4", "Leases and contracts (Ejari, renewals)", "planned"),
    ("5", "Rent billing, receipts and cheques", "planned"),
    ("6", "Accounting (chart of accounts, journals, VAT)", "planned"),
    ("7", "Maintenance and work orders", "planned"),
    ("8", "Owner statements and reports", "planned"),
    ("9", "Tenant and owner portals", "planned"),
]


def dashboard_callback(request, context):
    User = get_user_model()
    company = CompanySettings.load()
    done = sum(1 for _n, _t, s in MODULES if s == "done")
    context.update(
        {
            "company": company,
            "kpis": [
                {"label": "Users", "value": User.objects.count()},
                {"label": "Active staff", "value": User.objects.filter(is_staff=True, is_active=True).count()},
                {"label": "Modules finished", "value": f"{done} / {len(MODULES)}"},
            ],
            "modules": [{"number": n, "title": t, "status": s} for n, t, s in MODULES],
        }
    )
    return context
