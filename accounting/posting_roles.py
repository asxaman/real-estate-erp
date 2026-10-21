"""The accounts the automatic postings use, by job.

Each job points to one account. By default it is the account with the code from
the starter chart; you can point a job at a different account under
Accounting > Posting accounts. No code change is needed.
"""

# key: (default account code, plain-language label, expected control kind or "")
ROLE_DEFAULTS = {
    "receivable": ("1100", "Tenant receivables", "tenant"),
    "pdc_in_hand": ("1110", "Post-dated cheques in hand", ""),
    "pdc_collection": ("1120", "Cheques under collection (deposited)", ""),
    "bank_operating": ("1010", "Operating bank (the company's own money)", ""),
    "bank_trust": ("1020", "Owner trust bank (owners' money)", ""),
    "deferred_rent": ("2300", "Deferred rent revenue", ""),
    "owner_rent_pending": ("2110", "Owner rent - not yet collected", ""),
    "owner_payable": ("2100", "Owner payables", "owner"),
    "output_vat_pending": ("2410", "Output VAT - not yet due", ""),
    "vat_payable": ("2400", "VAT payable", ""),
    "rent_income": ("4010", "Rent income - company owned properties", ""),
    "fee_income": ("4020", "Management fee income", ""),
}

ROLE_CHOICES = [(key, label) for key, (_, label, _c) in ROLE_DEFAULTS.items()]
