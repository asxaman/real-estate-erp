# Real Estate ERP

Property management ERP built with Django (admin interface) and SQLite, for the UAE market.

Manages company-owned and third-party-owned properties:

- Owners and tenants with Emirates ID / passport / trade licence KYC, expiry tracking and document uploads
- Properties (title deed, Makani, DEWA) and units
- Tenancy contracts (Ejari / Tawtheeq, VAT, deposit, commission) with their cheques
- Cheque workflow: expected, in hand, deposited, cleared, bounced, replaced, plus a daily cheque desk
- Vendors, maintenance requests and expenses (who pays: owner, company or tenant), with receipts
- Demo data you can load and remove with one button (Setup > Demo data), and ready-made user roles (Manager, Accountant, Leasing agent, Maintenance coordinator, Viewer)
- Modern Unfold admin theme with a grouped left menu
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

## Accounting (general ledger)

A double-entry general ledger lives in the `accounting` app (menu: Accounting). Step 1 is the foundation:

- **Chart of accounts** (data, not code): add or rename accounts in the admin. `setup_accounting` creates a *starter* chart
  built for this business (deferred rent, post-dated cheques, tenant/owner/vendor control accounts, separate operating and
  owner-trust bank accounts). Have your UAE accountant review it before relying on it.
- **Journal entries** are balanced (debits = credits), numbered without gaps (JV-2026-000001) and **never edited or deleted**:
  you cancel a mistake with a reversing entry. This is enforced by the program and again by the database itself.
- **Fiscal periods** (months of a year that ends 31 December) can be locked; nothing can be posted into a locked period.
  A **cutover date** blocks postings before the ledger starts.
- **Sub-ledgers:** every line on a tenant, owner or vendor control account must name that party, so the sub-ledger always
  equals the control account. Menu: Accounting > Control accounts.
- **Reports** (calculated only from the ledger): Trial balance, General ledger (also a statement for one tenant/owner/vendor).
- **Permissions:** post manual journals, reverse entries and lock periods are separate permissions (Accountant role). Re-opening a
  locked period is for the superuser only. Every posting, reversal, lock and unlock is written to the audit log.

First-time setup is done inside the app: **Accounting > Accounting setup** has buttons for the starter chart,
the monthly periods of a year and the integrity check, and links to your TRN and cutover date.
(Developers can still use `python manage.py setup_accounting --years 2026` and `python manage.py check_ledger`.)

```
python manage.py makemigrations accounting
python manage.py migrate
```

**Step 2: rent and cheques post by themselves (accrual basis).** Set your cutover date, then use Accounting > Daily postings:

- **Lease invoice** (once per lease, printable, numbered INV-2026-000001, carries your TRN). Company-owned rent is held as
  *deferred revenue* and the VAT as *output VAT not yet due*. Third-party rent is held for the owner, never as our income.
- **Cheques post by themselves** when you change their status on the cheque desk: in hand (the tenant's balance drops and a
  numbered receipt voucher is issued), deposited, cleared (money reaches the bank; for owners' rent it is credited to the owner and
  our management fee and its VAT are taken), bounced (the tenant's balance is restored). Cash, card and transfer post when paid.
  Owners' rent goes to the owner trust bank, company rent to the operating bank.
- **Month end** (Daily postings > Month end): releases rent from deferred revenue to rent income by the exact days of the lease,
  and moves VAT to "VAT payable" for instalments whose date has come.
- If a posting cannot be made (for example the month is locked) the payment is still saved and the step waits on Daily postings.
- Not posted yet (later steps): deposits, commission, fees, penalties, expenses, owner payouts, VAT return, financial statements.

Next steps: vendor bills and payment vouchers, owner payouts and deposits, VAT return, income statement, balance sheet, ageing,
bank reconciliation and year-end closing.

## Run the automatic tests

```
python manage.py test
```

## Privacy

Uploaded documents are saved in the `media` folder, which is excluded from Git on purpose.
Never upload that folder to GitHub.

## Demo data and user roles

```
python manage.py seed_demo      # load sample data (or use Setup > Demo data in the admin)
python manage.py remove_demo    # remove only the demo data
python manage.py setup_roles    # create the ready-made user roles (also done by migrate)
```

## How leases, payments and deposits work

- **Any lease length.** Enter the yearly rent and the start and end dates. The rent is pro-rated
  (a 6-month lease costs half the yearly rent, 18 months costs one and a half). The payment
  schedule is built for the real length and every payment falls inside the lease.
- **Payments.** "Payments & cheques" holds every payment: cheque (the normal case), cash, card
  or bank transfer. Cheques go through In hand, Deposited, Cleared or Bounced. Cash, card and
  transfers are Expected and then Paid (on the cheque desk, click **Paid**). All of them count
  on the dashboard and on owner statements. A payment is never deleted: cancel it, so the
  history stays. A payment on an approved owner statement is locked.
- **Ending a lease.** On the lease page use **Mark as expired** (after the end date) or
  **Terminate early**. Early termination follows normal UAE practice: written notice (60 to 90
  days, set in Company settings), rent payable through the notice, an optional penalty (1 to 2
  months of rent) that is collected as its own payment and is never taken from the deposit, and
  uncashed cheques for the time after the exit are cancelled so they can be handed back.
  Always confirm the details of a contract with your lawyer.
- **Security deposit.** Record the deposit as a payment with purpose "Security deposit". After
  the lease ends, enter the handover date and any genuine deductions (damage or unpaid
  utilities), then use **Record deposit refund**. It is blocked until the property is handed
  back and any penalty is paid. A renewal that keeps the old deposit is marked "carried over",
  so it is never counted twice.
- **Owner statements.** A draft shows a warning, and cannot be approved, if anything it was
  built from changed (an edited expense, a new payment, a new fee). Rent goes to the owner who
  owned the property on the day it was received (see "Previous owners" on the property page),
  and the management fee stops when the management agreement ends.

## Who can see owners' bank and ID details

Seeing an owner's bank details, Emirates ID, passport, KYC status and scanned documents is a
separate permission: "Can see owners' bank, ID and KYC details". Managers and Accountants have
it. Leasing agents and Viewers do not (they still see the owner's name and contact details).
To let one Leasing agent see them, add that user to the **Owner details access** group as well.
Uploaded files are checked the same way when they are opened.
