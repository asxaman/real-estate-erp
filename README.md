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
  built for this business (post-dated cheques, tenant/owner/vendor control accounts, separate operating and
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

**Step 2: the accounting cycle posts by itself.** Set your cutover date, then use Accounting > Daily postings. The cycle:

1. **Lease invoice** (once per lease, for the whole contract, numbered INV-2026-000001): Dr Tenant receivable. Cr Owner payable
   (third-party unit) or Rent income (company unit). VAT, when the lease has it, goes to VAT output payable. On each lease you
   choose whether the VAT is added on top of the rent or already inside it.
2. **Every payment gets a receipt voucher (RV)**; the debit depends on how the tenant pays: cash > Cash account; bank transfer >
   Owner trust bank (third-party unit) or Operating bank (company unit); card > Merchant clearing (moves to the bank when you
   enter "card money reached the bank on"); post-dated cheque > PDC receivable. The credit is Tenant receivable
   (a security deposit credits Tenant security deposits instead).
3. **PDCs recorded:** Dr PDC receivable / Cr Tenant receivable. The tenant statement is clear.
4. **Cheque clears:** Dr Bank / Cr PDC receivable (depositing posts nothing).
5. **Cheque bounces** (even after clearing): Dr PDC bounced receivable / Cr PDC receivable. Account 1140 is the instant report
   of who owes for bad cheques.
6. **Bounced cheque replaced** (cash, transfer, card or another cheque): the replacement credits PDC bounced receivable.
   Fees and penalties are invoiced first (Dr Tenant receivable / Cr Income), then paid like any payment.
7. **Early exit:** cheques handed back are cancelled (Dr Tenant receivable / Cr PDC receivable), a credit note takes the unused
   rent off (Dr Owner payable or Rent income and VAT / Cr Tenant receivable), and the penalty is invoiced and paid.
8. **Maintenance** on a third-party unit: Dr Owner payable (cost + markup) / Cr Vendor payable (cost) / Cr Maintenance markup
   income. Enter "vendor paid on" to pay the vendor from the bank. The markup % is set on the property.
9. **Owner statement paid:** Dr Owner payable (fee + VAT on the fee) / Cr Management fee income and VAT; Dr Owner payable
   (payout) / Cr Owner trust bank.
10. **Move-out:** Dr Tenant security deposits / Cr Trust bank (the refund) and Cr Owner payable (damages for the owner).

There is no deferred rent and no month end any more. Third-party early-exit penalties go to the owner (they appear on the owner
statement); company-unit penalties are our income.
- If a posting cannot be made (for example the month is locked) the payment is still saved and the step waits on Daily postings.
- Also posted now: deposits, commission, fees and penalties, deposit refunds, expenses, owner statement payouts, early-exit credit notes, opening balances for leases that started before the cutover. Not yet: VAT return, financial statements.
- Locks: an invoiced lease and a posted cheque cannot have money fields changed. Optional two-person rule for manual journals, automatic month end.

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
