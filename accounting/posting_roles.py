"""The accounts the automatic postings use, by job.

Each job points to one account. By default it is the account with the code from
the starter chart; you can point a job at a different account under
Accounting > Posting accounts. No code change is needed.
"""

# key: (default account code, plain-language label, expected control kind or "")
ROLE_DEFAULTS = {
    "receivable": ("1100", "Tenant receivables (Tenant AR)", "tenant"),
    "cash_on_hand": ("1030", "Cash account (cash payments)", ""),
    "merchant_clearing": ("1040", "Merchant clearing account (card payments until the bank settles)", ""),
    "pdc_ar": ("1130", "PDC receivable (cheques held for the tenant)", "tenant"),
    "pdc_bounced": ("1140", "PDC bounced cheques receivable", "tenant"),
    "bank_operating": ("1010", "Operating bank (the company's own money)", ""),
    "bank_trust": ("1020", "Owner trust bank (owners' money)", ""),
    "owner_payable": ("2100", "Owner payables", "owner"),
    "vendor_payable": ("2010", "Vendor payables", "vendor"),
    "vat_payable": ("2400", "VAT output payable", ""),
    "security_deposits": ("2200", "Tenant security deposits held", ""),
    "rent_income": ("4010", "Rent income - company owned properties", ""),
    "fee_income": ("4020", "Management fee income", ""),
    "maintenance_income": ("4030", "Maintenance markup income", ""),
    "commission_income": ("4040", "Agency commission income", ""),
    "registration_income": ("4050", "Registration / Ejari fee income", ""),
    "penalty_income": ("4060", "Early-termination penalty income", ""),
    "bounce_fee_income": ("4070", "Bounce penalty income", ""),
    "other_income": ("4090", "Other income", ""),
    "expense_company": ("5020", "Repairs and maintenance paid by the company", ""),
    "input_vat": ("1200", "VAT receivable (input VAT)", ""),
    "opening_equity": ("3020", "Opening balance equity", ""),
}

ROLE_CHOICES = [(key, label) for key, (_, label, _c) in ROLE_DEFAULTS.items()]
