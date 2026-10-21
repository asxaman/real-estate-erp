# Real Estate ERP

Property management ERP built with Django (admin interface) and SQLite, for the UAE market.

Manages company-owned and third-party-owned properties:

- Owners and tenants with Emirates ID / passport / trade licence KYC, expiry tracking and document uploads
- Properties (title deed, Makani, DEWA) and units
- Tenancy contracts (Ejari / Tawtheeq, VAT, deposit, commission) with their cheques
- Cheque workflow: expected, in hand, deposited, cleared, bounced, replaced, plus a daily cheque desk
- Vendors, maintenance requests and expenses (who pays: owner, company or tenant), with receipts
- A home-page dashboard: cheques to deposit, rent due and collected, who owes, vacancy, expiring leases and papers, payouts
- Owner statements: cleared rent minus management fee (and its VAT) minus owner expenses = payout, with approval, payment record and a printable page

## Run it locally (Windows PowerShell)

```
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py makemigrations realestate
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Then open http://127.0.0.1:8000/admin/

## Run the automatic tests

```
python manage.py test
```

## Privacy

Uploaded documents are saved in the `media` folder, which is excluded from Git on purpose.
Never upload that folder to GitHub.
