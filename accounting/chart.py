"""A STARTER chart of accounts, built for this business (tenant receivable, post-dated
cheques, strictly separate owner money). It is data, not code: after your UAE
accountant reviews it, rename, add or deactivate accounts in the admin.

Each row: code, name, type, extra settings. Headings come first in each group.
"""

ASSET, LIAB, EQUITY, INCOME, EXPENSE = "asset", "liability", "equity", "income", "expense"

# (code, name, type, parent_code, options)
STARTER_CHART = [
    ("1000", "ASSETS", ASSET, None, {"is_header": True}),
    ("1010", "Operating Bank (company money)", ASSET, "1000", {"is_bank": True, "fund": "operating"}),
    ("1020", "Owner Trust Bank (owners' money)", ASSET, "1000", {"is_bank": True, "fund": "trust"}),
    ("1030", "Cash Account", ASSET, "1000", {}),
    ("1040", "Merchant Clearing Account (card payments)", ASSET, "1000", {}),
    ("1100", "Tenant Receivables (control)", ASSET, "1000", {"control_kind": "tenant"}),
    ("1130", "PDC Receivable (cheques held)", ASSET, "1000", {"control_kind": "tenant"}),
    ("1140", "PDC Bounced Cheques Receivable", ASSET, "1000", {"control_kind": "tenant"}),
    ("1150", "Fees Due from Trust Account", ASSET, "1000", {}),
    ("1200", "VAT Receivable (input VAT)", ASSET, "1000", {}),
    ("2000", "LIABILITIES", LIAB, None, {"is_header": True}),
    ("2010", "Vendor Payables (control)", LIAB, "2000", {"control_kind": "vendor"}),
    ("2100", "Owner Payables (control)", LIAB, "2000", {"control_kind": "owner"}),
    ("2200", "Tenant Security Deposits", LIAB, "2000", {}),
    ("2400", "VAT Output Payable", LIAB, "2000", {}),
    ("3000", "EQUITY", EQUITY, None, {"is_header": True}),
    ("3010", "Retained Earnings", EQUITY, "3000", {}),
    ("3020", "Opening Balance Equity", EQUITY, "3000", {}),
    ("4000", "INCOME", INCOME, None, {"is_header": True}),
    ("4010", "Rent Income - Company Owned Properties", INCOME, "4000", {}),
    ("4020", "Management Fee Income", INCOME, "4000", {}),
    ("4030", "Maintenance Markup Income", INCOME, "4000", {}),
    ("4040", "Agency Commission Income", INCOME, "4000", {}),
    ("4050", "Registration and Ejari Fee Income", INCOME, "4000", {}),
    ("4060", "Early-Termination Penalty Income", INCOME, "4000", {}),
    ("4070", "Bounced-Cheque Fee Income", INCOME, "4000", {}),
    ("4090", "Other Income", INCOME, "4000", {}),
    ("5000", "EXPENSES", EXPENSE, None, {"is_header": True}),
    ("5010", "Operating Expenses", EXPENSE, "5000", {}),
    ("5020", "Repairs and Maintenance (company)", EXPENSE, "5000", {}),
    ("5030", "Bank Charges", EXPENSE, "5000", {}),
    ("5100", "Owner Recharges / Recoveries", EXPENSE, "5000", {}),
]


def seed_chart():
    """Create the starter accounts that do not exist yet. Never changes or
    removes an account you already have. Returns how many were created."""
    from .models import Account, ensure_mappings

    created = 0
    by_code = {}
    for code, name, account_type, parent_code, options in STARTER_CHART:
        account = Account.objects.filter(code=code).first()
        if account is None:
            account = Account(
                code=code,
                name=name,
                account_type=account_type,
                parent=by_code.get(parent_code) if parent_code else None,
                **options,
            )
            account.full_clean()
            account.save()
            created += 1
        by_code[code] = account
    ensure_mappings()
    return created
