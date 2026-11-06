"""The shared list of banks (System settings -> Banks) and the lists the pages use."""
from django.db.utils import DatabaseError

# UAE banks, to start the list with. Edit it in System settings -> Banks.
UAE_BANKS = [
    "Abu Dhabi Commercial Bank (ADCB)", "Abu Dhabi Islamic Bank (ADIB)", "Ajman Bank", "Al Maryah Community Bank",
    "Arab Bank", "Bank of Baroda", "Bank of Sharjah", "Banque Misr", "BNP Paribas", "Citibank",
    "Commercial Bank International (CBI)", "Commercial Bank of Dubai (CBD)", "Doha Bank", "Dubai Islamic Bank (DIB)",
    "Emirates Islamic", "Emirates NBD", "First Abu Dhabi Bank (FAB)", "Habib Bank AG Zurich", "Habib Bank Limited (HBL)",
    "HSBC", "Invest Bank", "Liv", "Mashreq Bank", "Mashreq Neo", "National Bank of Fujairah (NBF)",
    "National Bank of Umm Al Quwain (NBQ)", "Qatar National Bank (QNB)", "RAKBANK", "Sharjah Islamic Bank",
    "Standard Chartered", "State Bank of India", "United Arab Bank", "Wio Bank", "Zand Bank",
]


def names(include=""):
    """Active bank names, in order. `include` keeps an old value that is no longer in the list."""
    from .models import Bank

    try:
        found = list(Bank.objects.filter(is_active=True).values_list("name", flat=True))
    except DatabaseError:
        found = []
    include = (include or "").strip()
    if include and include.lower() not in {n.lower() for n in found}:
        found.append(include)
    return found


def seed_banks(sender=None, **kwargs):
    """After `migrate`: start the list with the UAE banks when it is empty. Best effort."""
    from .models import Bank

    try:
        if Bank.objects.exists():
            return
        Bank.objects.bulk_create([Bank(name=name) for name in UAE_BANKS])
    except DatabaseError:
        return
