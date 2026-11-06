# Changelog

## Update 27 - Simpler lease form, Deductions, fines, refunds, bulk edit and remove, new module name
Run `python manage.py makemigrations`, then `migrate` and `test`.
- The lease form no longer asks for "Deposit is", "Deposit refunded on", "Amount refunded" or "Late fee per day". The deposit, commission, admin fee and Ejari fee stay; what actually happens is recorded as payment lines.
- "Deposit deductions" is now "Deductions": date, type (taken from the deposit / fine / other charge), reason, note and amount. Each one also becomes a line in Tenant Payments against its lease. Earlier deductions get their line when you run `migrate`.
- New payment types: Fine, Deduction and Deposit Refund (money paid back, which reduces the deposit still held). Money kept from the deposit is not counted as cash collected.
- "Rent payments" is now "Tenant Payments" (menu, page titles, export names).
- Tenant Payments: "Edit ticked" (dates, amount, method, bank, cheque numbers in order, notes) and "Remove ticked", as buttons above the list and in the Action drop-down. Received payments keep their date and amount and are never removed.
- The Terminate pop-up no longer asks about the deposit status.

## Update 26 (part B) - Deposit and fees as payment lines, a page for each lease's payments, database safety rules
Run `python manage.py check_lease_data`, then `makemigrations`, `migrate` and `test`.
- A lease now creates a payment line for each of: security deposit, office fee, agency fee and Ejari fee (new field on the lease), next to the rent instalments. They are received from the Rent Payments module like rent. Existing leases get their lines once, automatically, when you run `migrate`.
- New payment Type (Rent, Security deposit, Office fee, Agency fee, Ejari fee, Other) and new status "Cheque held": a cheque that is received but not cashed (blank or post-dated). It is not money in the bank until "Cash cheque" is clicked.
- VAT is added to rent only. Rent totals, rent roll and monthly collection count rent only; the deposit and fees are shown on their own.
- The Rent Payments list keeps the payments of one lease together; the lease number opens that lease's own payments page (all its lines, totals, Receive buttons).
- Editing the deposit or a fee on the lease updates its pending line; once received, change it on the payment line.
- Database rules: end date after start date, rent above zero, payment amount above zero, no duplicate payment number on a lease; indexes on the busy filters.
- New command `check_lease_data` looks for old data the new rules would refuse.

## Update 26 (part A) - Audit fixes, no database change
Just extract and run `python manage.py test`. No `makemigrations` / `migrate` needed.
- The sample-data and erase-everything tools now work only on your own PC (or when `DJANGO_ALLOW_DEV_TOOLS=1` is set on purpose). A live server refuses them whatever the System settings say.
- Login lock-out behind nginx / a load balancer (`DJANGO_BEHIND_PROXY=1`) now uses each visitor's own address, so one person's wrong passwords cannot lock everybody out.
- A lease can no longer be renewed twice by a double click, and a payment already recorded as paid is refused a second time (the first date and method stand).
- The dashboard "Needs attention" totals are worked out by the database instead of loading every payment: faster with thousands of payments.
- Downloads (Excel / CSV of owners, tenants, properties, units, leases, payments, rent roll) and sign-ins are now written to the audit log.
- Unused code removed (13 unused imports); new tests in leases/test_audit_fixes.py.

## Update 15 - Security and safety audit fixes
Run `python manage.py makemigrations`, then `migrate`, then `test`.
- Development mode switch in System settings (Development tab). Off = no sample data, no erase-all, demo accounts switched off. Erase all data also clears the audit log.
- Debug mode is now OFF unless the site is started with `manage.py` (your PC). A live server needs a real `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS`, and gets HTTPS redirect, secure cookies, HSTS, a 60-minute idle log-out and a log file (`logs/erp.log`).
- PostgreSQL is required: the silent SQLite fallback is gone.
- Uploaded files are served only to signed-in people with permission (core/media.py).
- Login lock-out after 5 wrong passwords (15 minutes); `unlock_logins` command.
- Large buildings (200+ units) can be saved: the form-field limit is raised.
- Export to Excel / CSV needs its own permission; Import "update existing" needs change permission.
- Password rule now also needs a small letter.
- Audit log masks personal numbers and hides dates of birth; older entries are cleaned by `migrate`. It now reads each row once per save.
- File content is checked against the file type; replaced or removed files are deleted from disk; import files are limited to 5 MB; CSV separator is read from the header line.
- Backups include the uploaded files; requirements are pinned.
- The database refuses duplicate owner names and tenant emails (capitals ignored).
- WhatsApp buttons only appear for numbers that are clearly UAE or international.
- Unit and Property share one status list; owner header label corrected; fewer database queries on owner/property pages and exports; tooltip code tidied; unused code removed.
- New tests for all of the above (core/test_hardening.py).

## Update 14d - Smarter imports and bulk actions
No new migration. Run `python manage.py test` and `runserver`.
- Imports (owners, properties, units, tenants) have an "Update existing records" tick. Owners are found by name, properties by Code, units by Property code + Unit number, tenants by Email. Only the cells you fill in change; blank cells keep what is saved. New rows in the same file are still added. The result says "Imported N new and updated M".
- When an import has problems, a "Download error report (CSV)" button saves the list of row numbers and problems.
- Import files accept the newer columns: owners (TRN), tenants (Emirates ID, passport, date of birth, trade licence, authorised signatory, TRN), properties (area / community, parking, title deed, plot, Makani, utility premise no.), units (parking, view, utility account no.).
- Bulk actions: "Make selected ... active / inactive" for owners, properties and tenants. A record that breaks a rule (for example an owner who still has active properties) is skipped and explained; the others are changed.
- "Change owner of selected properties": pick the new owner (active owners only) and, when the owner is not the company, optionally a management fee. All the properties move or none do.
- A property cannot be switched back on while its owner is inactive.

## Update 14c - Dashboard and Expiring documents
Run `python manage.py migrate` (no new tables; it also gives the existing Property Manager, Accountant and Leasing Agent roles access to the new property / unit documents and photos).
- The home page is now a Dashboard: counts (active properties, units, vacant units, occupancy, owners, tenants, expired and expiring documents), the documents that need attention, units by status, vacant units, blacklisted tenants and (for administrators) recent activity. Each card only shows what the signed-in person is allowed to see, and most cards open the matching list.
- New "Expiring documents" page (sidebar, under Overview) gathering the documents of owners, tenants, properties and units in one list, soonest first. Filter by status, by who they belong to, and by how far ahead to look (30 / 60 / 90 / 180 days); search; download as CSV.

## Update 14b - Richer Properties and Units
Run `python manage.py makemigrations` then `migrate`.
- Property and Unit pages now have a summary header and tabs. Property: Overview, Details, Units (buildings), Documents, Activity. Unit: Overview, Photos, Documents, Activity.
- New optional property fields: area / community, parking spaces, title deed no., plot no., Makani no. (10 digits), utility premise no. (DEWA / ADDC / SEWA).
- New optional unit fields: parking spaces, view, utility account no.
- Documents for properties (title deed, insurance, Civil Defence certificate, occupancy certificate, floor plan, NOC, other) and units (title deed, floor plan, handover report, inventory, other). Insurance and Civil Defence need an expiry date; only one title deed each. A warning shows when a document is expired or about to expire.
- Unit photos (up to 30 per unit). The first photo shows in the unit's header.
- Activity tab on properties and units (from the audit log). Property and unit changes also appear on the owner's Activity tab. Older history is attached automatically by `migrate`.
- Properties can be made inactive with an optional reason (Delete still works).
- Properties and Units exports include the new fields. (Import of the new columns comes with the "update existing" import in update 14d.)

## Update 14a - Richer Owners and Tenants
Run `python manage.py makemigrations` then `migrate`.
- Tenants: new Identity tab (Emirates ID no., passport no., date of birth for people; trade licence no., authorised signatory for companies; TRN for both). All optional.
- Owners: TRN (Tax Registration Number, 15 digits) added. Optional.
- Nationality is now a country dropdown. Old free-text nationalities ("Egyptian", "UAE") are converted by `migrate`; any it cannot recognise must be chosen again when that tenant is edited. Imports accept country names and common nationalities.
- Tenants can be marked Blacklisted with a reason; they show a red badge in the list and a warning on their page. (The warning when creating a lease arrives with Leases.)
- Owners and tenants can be made inactive with an optional reason. Deleting still works as before.
- Tenants list: "Complete / Incomplete" documents badge and filter (Emirates ID + passport for people, trade licence for companies). New System setting "Require complete documents" for blocking leases; it takes effect once Leases exist.
- A green WhatsApp-style chat button beside phone numbers opens a chat with that number.
- Tenant export/import now include the new fields.

## Update 13 - Cleanup of Owners, Properties, Units and Tenants
Run `python manage.py makemigrations` then `migrate` (the unit status, a few limits and some help texts changed).
- Tenants: the same email cannot be used twice, and the same name with the same phone number (written any way, +971 / 05...) is refused too. Importing a file twice no longer creates tenants twice.
- Units: a new unit is Vacant by default (it used to have no status, which threw off the Vacant and Occupancy numbers). Old units without a status are set to Vacant by `migrate`.
- Status: still chosen by hand for now; a note says that it will follow the lease automatically once Leases exist.
- Excel import: a phone number that lost its leading zero (0501234567 became 501234567) is refused with an explanation. The Phone column of the Owner and Tenant templates is set to Text so Excel keeps the zero.
- Owner header: "Listed rent" is now "Asking rent, all units" and shows what it really is. Counts (properties, units, occupancy, rent) leave out inactive properties.
- An owner with active properties cannot be made inactive (deactivate or move the properties first).
- Limits: year built 1900 to 5 years ahead, total floors 1 to 200, area more than 0. An Emirates ID must have 15 digits starting 784 (it is tidied to 784-YYYY-NNNNNNN-C).
- Documents: Emirates ID, passport, visa and trade licence need an expiry date. Only one Emirates ID, passport, visa, trade licence or VAT certificate per owner / tenant.
- Expiry warnings: one message for all of a record's documents instead of one per document.
- The first contact of an owner or tenant becomes Primary, and the first bank account becomes Default, automatically.
- Properties and Units can now be exported to Excel and CSV (select rows, choose the action).
- Exports can no longer carry spreadsheet formulas (text starting with = + - or @ is made safe; phone numbers are left alone).
- Owners list: counts are worked out with sub-queries instead of one huge join, so it stays fast with many units.
- Owner and tenant codes (OWN-0001, TEN-0001): if two people save at the same moment, the second one gets the next code instead of an error.

## Update 12g - Fix
- System settings page crashed with an error when there were no roles yet (Django 6 rejects an HTML snippet with no values). Fixed, and a test now covers a page with no roles.

## Update 12f - Simple passwords
- One password rule: at least 8 characters, 1 capital letter, 1 number, 1 special character
- Add user and Change password forms no longer show the Enabled / Disabled choice

## Update 12e - Administration tidy-up
- Roles, Audit log and Data tools moved into System settings (three new tabs); their sidebar entries are gone. The full pages are one button away.

## Update 12d - Fixes
- System settings: the Address box has the theme's normal border again (I had replaced it with a bare text box)
- Users: the profile picture now has the same preview with View / Edit / Delete buttons as owners and tenants (round preview)

## Update 12 - System settings page and new dropdowns
- System settings now has four tabs: Company, Date and time (format, clock, time zone), Documents and files (expiry warning days, upload size limits), Notifications (pop-up time)
- The company name is used in the sidebar and browser tab
- Every dropdown in the system is now a styled menu with a fade animation, keyboard support and a search box for long lists
- Run `python manage.py makemigrations` and `migrate` (new fields in the settings table; help texts changed)

## Update 11 - Required tenant fields and System settings
- Tenants: Email, Contact number and Nationality are now required (also in import)
- New System settings page (superusers): choose the date format and time format for the whole system
- Run `python manage.py makemigrations` and `migrate` (new migrations in `tenants` and `core`)

## Update 10f - File fields (photo, documents)
- Owner and tenant photo/logo and the Documents tabs now have a preview and clear View / Edit / Delete buttons (Delete is applied when you save, with an Undo); pictures open in an in-app viewer

## Update 10e - Pop-ups moved to the bottom right corner

## Update 10d - Pop-up links
- Messages that contain a link ("The unit SHJ3-302 was added") show the name as a clickable link instead of raw HTML

## Update 10b - Pop-up notifications
- Success, warning and error messages across the whole app now appear as pop-up notifications (top right, auto-dismiss with a progress bar, pause on hover, close button, light/dark) instead of coloured bars

## Update 10 - Tenants module (part 1)
- New `tenants` app: Tenant, contacts, documents with expiry warnings
- Tenant page with summary header and tabs (Overview, Contacts, Documents, Activity), list columns, filters, search (including Emirates ID / licence number)
- Excel/CSV export and import for tenants
- Sample tenants in the seed data; roles get tenant permissions
- Audit log covers tenants (with an Activity tab)
- New sidebar section: Tenancy > Tenants
- Run `python manage.py makemigrations` and `migrate` (new migrations in `tenants` and `core`)
## Update 9f
- The Import page is reached from an *Import* button on the Owners list (the sidebar item is gone)
- New: import Properties and Units from Excel or CSV (template with an Allowed values sheet, every row checked, all-or-nothing)

## Update 9e
- New property form: the Owner field starts as "Company Owned"

## Update 9c
- Sidebar section headers (Property management, Administration) can be collapsed and expanded
- The sidebar remembers which sections you opened or closed (default: open; the section with the current page stays open)


## Update 9 - Owners module upgrade
- Owner type (Individual / Company), automatic owner code, photo or logo
- Summary header and tabs: Overview, Contacts, Properties, Documents, Financials, Activity
- Contacts, documents (with expiry warnings) and bank accounts (IBAN checked and masked in the audit log)
- Better list columns, filters, and search by phone, email, Emirates ID or licence number
- Export to Excel / CSV and import from Excel / CSV (all-or-nothing)
- Owner / tenant portals are postponed until the ERP modules are complete (no portal items on the owner screens)
- Owner activity tab fed by the audit log
- New requirement: openpyxl. Run `pip install -r requirements.txt`, then `makemigrations` and `migrate`

## Update 8d - Audit of the delete pop-up
- "Cannot be deleted" is now decided from the page's main content only
- Correct singular wording when one record is selected from a list ("property", not "properties")
- Record names in the list pop-up are read more reliably (users with a profile picture)
- Checked the whole app: no other browser pop-ups or separate confirmation pages remain (only the browser's own box on closing the tab or reloading, which cannot be replaced)

## Update 8c - Fix: delete from a list, clearer wording
- Fix: "Delete selected" in lists did not delete (the page address was read wrongly because the list form has a field called "action")
- Delete pop-up wording: names what is being deleted (for example: Delete property “Sharjah 3 (SHJ3)”?), says plainly whether related items go too, and no longer lists a stray "Yes, I'm sure" line

## Update 8b - Fix: delete from a list
- Fix: "Delete selected" in list pages showed a wrong "This cannot be deleted" pop-up. The delete pop-up no longer depends on the layout of Django's confirmation page: it builds the delete request itself, lists the ticked records by name, and shows "cannot be deleted" only when Django reports protected records or missing permission

## Update 8 - Delete confirmation pop-up (whole app)
- Deleting anything (the Delete button on a record, or "Delete selected" in a list) now asks in a pop-up that lists what will be deleted, instead of opening a separate page. If something cannot be deleted (other records depend on it) the pop-up explains why
- Applies automatically to every current and future module
- No database changes

## Module 0, update 7c - Fix
- Fix: the Users page crashed ("no profile picture") for a user without a picture

## Module 0, update 7b - Picture in the sidebar
- The profile picture now also shows in the account block at the bottom of the left sidebar (Unfold reads it from `avatar_url`)
- No database changes

## Module 0, update 7 - User profile picture
- Optional profile picture on every user (Personal info section); round photo or initials circle in the Users list
- Pictures: JPG/PNG/WebP, max 2 MB, stored in the new `media` folder; the file is deleted when the picture is removed or the user is deleted
- New requirement: Pillow (run `pip install -r requirements.txt`)
- Run `makemigrations` then `migrate` after extracting

## Module 1, update 6b - Unsaved-changes pop-up
- Leaving a form with unsaved changes (sidebar, breadcrumbs, links) now shows a pop-up in the app's own style instead of the browser's "Leave site?" box. Closing the tab or reloading still uses the browser's box (browsers do not allow anything else)
- No database changes

## Module 1, update 6 - Audit fixes (current)
- Owners: "Company Owned" is reserved; duplicate names refused (ignoring capitals/spaces); phone format checked; email saved in lower case
- Building type: cannot switch Residential <-> Commercial while units of the other kind exist
- Unit numbers saved trimmed and in capitals; duplicates such as `g01` / `G01` are caught
- Unit floor cannot exceed the building's Total floors; Total floors cannot drop below an occupied floor
- Inactive owners are not offered for new properties; inactive buildings take no new units; Units list property choices show active buildings only
- Safety: you cannot delete, deactivate or demote your own account, and the last active superuser is protected
- New Audit log (sidebar > Administration): who changed what, with old and new values, for owners, properties, units and users; read-only
- Automated tests (`python manage.py test`), `backup_db` and `pin_versions` commands, backup/restore guide in the README
- Cosmetic: README brought up to date; Units list filter shows buildings only; no long help text in the units table on the Property page; money shown as `95,000`; Properties list gets an Annual rent column
- Run `makemigrations` then `migrate` after extracting (new migration files in `core` and `properties`)

## Infrastructure - PostgreSQL
- The project now uses PostgreSQL. Connection details come from a local `.env` file (see `.env.example`); `.env` is never uploaded to GitHub
- If `DB_NAME` is not set, the project falls back to the old SQLite file
- New command `python manage.py db_info` shows which database is in use and tests the connection
- `requirements.txt` adds the `psycopg` driver
- README has a step-by-step PostgreSQL setup guide for Windows
- Start with an empty database: run `migrate`, `createsuperuser`, then seed sample data again

## Module 1, update 5 - Fixes and Units form
- Fix: the management fee section now hides when "Company Owned" is chosen (more robust show/hide logic;
  whole sections including their headings are hidden)
- Property code must be 3 to 6 letters/numbers starting with a letter
- Apartment properties: new "Type" (Studio, 1BHK-5BHK) and "Bathrooms" (1-10) fields
- Units form rebuilt:
  - Floor is required and accepts only G, LG, UG, R, RF, PH, M, MZ, A, S, SC, GF, L, LV, E, B1.., P1.., 1, 2, 3 ...
  - Units are sorted by floor properly (basements, G, 1, 2 ... 10, roof, penthouse)
  - Unit type: Studio, 1BHK-5BHK (residential buildings), Office or Shop (commercial buildings); required
  - "Bedrooms" removed; Bathrooms (1-10), Status, Area, Furnished and Asking rent are optional
- Run `makemigrations` then `migrate` after extracting

## Module 1, update 4 - Property form logic
- Built-in owner "Company Owned" (created automatically after `migrate`, cannot be renamed or deleted,
  never removed by "Erase all data"). Selecting it hides the management fee fields
- Management fee type: percentage of rent or fixed AED per year; required for every owner except Company Owned
- Standalone properties get "Annual rent (AED)" and "Status" (hidden for buildings)
- Year built and Total floors hidden for Warehouse, Yard, Shop, Apartment, Parking Spot (cleared on save)
- New optional "Map link" field
- The server also enforces these rules, not only the page
- Run `makemigrations` then `migrate` after extracting

## Module 1, update 3 - Property form UI/UX polish
- Fields arranged in a compact two/three-column layout in three sections (Basic information, Details, Notes)
- Shorter address and notes boxes, helpful placeholders, tighter field spacing
- Searchable Owner dropdown
- Property code is shown and stored in capitals
- After saving a new building you stay on the page so you can add its units immediately
- Warning if you try to leave the form with unsaved changes
- Help text under fields is replaced by a small "i" icon next to the field name; hover (or Tab to it) to read the tip. Applies to the Owner, Property and Unit forms (the server moves each help text onto its field, so it no longer depends on Unfold's page markup)
- Property type shows a small "Has units" / "Standalone" pill that updates as you choose
- New files: `core/static/core/erp.css` and `erp.js` (loaded through the Unfold settings)
- No database changes in this update (no new migration needed, `migrate` can be skipped)

## Module 1, update 2 - Property form changes (current)
- Property types are now: Residential Building, Commercial Building (have units) and
  Villa, Warehouse, Yard, Shop, Apartment, Parking Spot (standalone, no units)
- Units section is hidden for standalone properties and for the "add property" page
  (save first); units can only be created for buildings
- "Area / Community" field removed
- Management fee is optional (0 = company-owned, above 0 = managed for another owner)
- Data migration 0002 converts old property types automatically
- Sample data: 4 owners, 12 properties (3 buildings, 80 units, 9 standalone)
- Run `makemigrations` then `migrate` after extracting

## Module 1 - Properties and units
- New `properties` app: Owner, Property and Unit models with Unfold admin pages
- Property page shows its units inline; Property list shows unit and vacant counts
- Unit list with colour status badges and filters (status, type, property, furnished)
- Sidebar section "Property management" (each item appears only for users with permission)
- Sample data: 3 owners, 4 properties, 88 units; demo roles receive permissions
- Erase all data also removes owners, properties and units
- Run `python manage.py makemigrations` then `migrate` after extracting

## Module 0 - Foundation
- Django project (`config/`) with SQLite and the Unfold admin theme, UAE time zone (Asia/Dubai)
- `core` app:
  - Users and Roles (groups) managed by superusers only, so the superuser can create other users
  - "Data tools" page in the admin sidebar (superuser only):
    - Seed sample data (4 roles + 4 demo staff users, safe to run repeatedly)
    - Erase all data (type ERASE to confirm; superusers are never deleted)
  - Same tools from the terminal: `python manage.py seed_data` and `python manage.py erase_data`
  - A registry (`core/data_registry.py`) so every future module adds its own sample data and eraser automatically

## Planned modules
2. Owner agreements and documents
3. Rent invoicing and receipts
4. Accounting (chart of accounts, journal entries, reports)
5. Maintenance requests
6. Owner portal and tenant portal

### Update 15b
- Tenant email rule now ignores blank emails; three old tests adjusted to the new rules; test output quieter.

### Update 16
- Picture editor: choosing a photo / logo opens a window to move, zoom and crop it (square, shown round for people) before it appears in the form. Works on Add and Edit for Owners, Tenants and Users; the Add user form now has a profile picture field.

### Update 17 - Leases and rent payments
- New module "leases": Leases, payment schedule, rent payments list, lease documents, renew and terminate, VAT, deposit, commission and Ejari details.
- Unit and property status now follows the leases (when a lease is saved and at each day's first sign-in; `refresh_leases` command).
- Dashboard cards (Active leases, Overdue payments); Expiring documents shows leases that end soon and Ejari expiries.
- Tenants, units and properties with a lease cannot be deleted; a lease with received rent cannot be deleted.
- Audit trail and activity tabs cover leases (new Activity tab on the lease page).
- Sample data: 5 leases with payments; lease permissions for the demo roles.

### Update 18 - Leases: faster work
No migration needed. Run `python manage.py test`.
- Lease form fills itself in: rent from the unit's/property's asking rent, VAT ticked for commercial units, end date = start + 1 year - 1 day, and a warning under the unit when it already has a lease that overlaps.
- "Record payment" pop-up (date received, method, cheque number, bank) and a "Cheque bounced" button, from the lease page, the Leases list and the Rent payments list.
- Quick-filter chips with counts above the Leases list (All, Active, Ending soon, Overdue payments, Not started, Expired, Terminated).
- Renew and Terminate pop-ups on the lease page and in the list. Renew can keep the rent, change it by a percentage, or set a new amount. Terminate can cancel the unpaid payments after the termination date and set what happens to the deposit.
- Cheque numbering: enter the first cheque number and every unpaid payment is numbered in date order (zeros and letter prefixes are kept).

### Update 19 - Leases: more detail
Run `python manage.py makemigrations`, then `migrate`, then `test`.
- Payment progress bar in the Leases list and in the lease header (red when something is overdue, green when fully paid).
- New "Money" tab on the lease: rent, VAT, total, deposit, commission, fee, received, still to receive, late fees, and a note if the payments do not add up to the annual rent.
- Timeline strip under the lease header: start → every payment (green paid, grey pending, red overdue/bounced) → Ejari expiry → end, with today marked.
- "Leases" tab on the Tenant, Unit and Property pages; the Units list has "Current tenant" and "Lease ends" columns.
- Days overdue column in Rent payments, and an optional "Late fee per day" on the lease (shown, never charged automatically).
- Deposit tracking: refund date and amount, a "Deposit deductions" tab (date, reason, amount) and a deposit summary.
- Cheque date and "Deposited in bank on" on every payment (a cheque cannot be deposited before its date).
- "Notes" tab: dated notes with the name of the writer, newest first; saved notes cannot be edited.

### Update 19c
- Fixed: the lease progress bar styles overrode the colours of the "Units by status" bars on the dashboard (all bars turned blue). The lease bar now has its own class names.

### Update 20 - Leases: reports and look
Run `python manage.py makemigrations`, then `migrate`, then `test`.
- Rent payments list: four summary cards on top (Due this week, Overdue, Collected this month, Bounced cheques) with AED amounts; click one to filter. New "Received" filter (this month / last month).
- Due dates are coloured with the days left or late ("5 days overdue" red, "in 3 days" amber, paid green).
- Monthly collection page (Leases > Reports): expected, received and still-open money per month, with a yearly total; click a month to see its payments.
- Rent roll page: every running lease with rent, received, overdue and next payment, with Excel / CSV download.
- Export to Excel / CSV for selected leases and selected payments. New permissions "Can export leases" and "Can export rent payments"; Property Manager and Accountant roles get them.
- Printable pages from the lease header: Tenancy summary and Payment statement (browser Print / Save as PDF).
- Dashboard: "Needs attention" list (overdue payments, bounced cheques, payments due this week, leases ending without renewal, Ejari expiring) and "Next leases to end" with a Renew button.

### Update 21 - Leases and rent payments: compact look
No migration needed. Run `python manage.py test`, then Ctrl+F5.
- The payment schedule on the lease page is now a compact table: Due (coloured), Amount, Payment (`Cheque · 004512 · ENBD` with cheque and deposit dates in small text), Status (with the paid date or days late). Notes show as a small "i" icon.
- Payments are edited in a pop-up (Edit button on every row): due date, amount, method, cheque number, bank, cheque date, deposited-on, status, date received, note. Unpaid payments can be removed there. "Add payment" in the lease header adds one more payment.
- The Rent payments list has fewer, merged columns (Tenant + what is rented, Payment, Status) with Record and Edit buttons on each row.
- Filters also appear as a row of drop-downs above the table, with "Clear filters".

### Update 22 - Leases and rent payments: ready for thousands
No migration needed. Run `python manage.py test`, then Ctrl+F5.
- Lease form: Tenant, Unit and Property are now search-as-you-type boxes (type a name, phone, unit or building). The page no longer carries a list of every tenant and unit, so it stays fast with thousands. Blacklisted or inactive tenants and inactive buildings are not offered.
- Rows per page (50 / 100 / 200) on the Leases and Rent payments lists; the choice is kept while filtering. The default is now 50.
- New filters: Building, Owner and a month filter (Ends in / Due in) on both lists.
- Rent roll: search box, paging (50 / 100 / 200 rows) and totals for all pages; Excel / CSV download follows the search.
- Rent payments list: tick rows, then "Mark as paid" (choose the date, and optionally how it was paid) or "Mark cheques as deposited". Payments already received are skipped, so two people working at the same time cannot record the same payment twice (up to 500 at a time).
- Removed (before testing Update 22, at the owner's request): the "Compact view" switch from Update 21 and all its styling (smaller rows, fixed heading row and columns, tighter lease header). The merged payment cells, the Edit pop-up and the drop-down filter bar stay.

### Update 23 - No browser suggestions, same drop-downs everywhere
No migration needed. Press Ctrl+F5.
- Browser suggestions and autofill are switched off on every form (add and change pages, pop-ups, inline rows, search boxes). The sign-in page is left alone so your password manager still works.
- The filter drop-downs above the Leases and Rent payments lists (Status, Due, Building, Owner, Rows per page ...) now use the same styled drop-down as the rest of the system.
- Title Case everywhere: headings, field labels, column titles, tabs, menu items, filter titles and buttons now show every word with a capital first letter, except small joining words (a, an, the, and, or, of, in, on, to, by, for, with ...) which stay small unless first or last. Acronyms (TRN, VAT, AED) are kept. Table data, typed values, help text and messages are not changed. Done by one helper in erp.js (`window.erpTitleCase`), so no migration and no change to the saved data.
- Autofill fix (23b): name, email, phone and address boxes stay locked until clicked, so Edge / Chrome "Saved info" is not offered.

### Update 24 - Large test data and 100 rows per page everywhere
No migration needed. Run `python manage.py test`, then (with Development mode on) use System settings > Data tools > Seed sample data, or `python manage.py seed_data`.
- Seed sample data now also creates a large set for testing: about 52 more owners, 210 more properties (70 buildings and 140 standalone), 2,300+ units, 2,000 tenants (with contacts and documents that are expired, expiring soon and valid), 2,000 leases (running, ending soon, finished, terminated early and starting soon; some with overdue or bounced payments) and about 8,000 rent payments. Unit and property statuses (Occupied / Vacant / Reserved / Under maintenance) match the leases. It is made with bulk inserts and is safe to run twice (it checks first). "Erase all data" removes it again.
- "Rows per page" (50 / 100 / 200) now also appears on Owners, Properties, Units, Tenants, Users and the Audit log, and the default is 100 rows on every list, including Leases, Rent payments and the Rent roll. The choice is kept while filtering or paging.
- Code: `core/paging.py` (the shared rows-per-page helper), `core/sample_lists.py` (names for the generated data), `SAMPLE_SCALE` in `core/data_registry.py` (1.0 = full size).

### Update 25 - Smart search, quick filters and Ctrl+K (includes everything from Update 24)
No migration needed. Run `python manage.py test`, press Ctrl+F5 twice, then try the lists.
- One smart search box on Owners, Properties, Units, Tenants, Leases and Rent payments (`core/search.py`). Every word you type must be found; phone numbers and ID numbers are compared by their digits (0501234567, +971 50 123 4567 and 971-50-123-4567 find the same tenant; Emirates ID with or without dashes); codes work in any style (own12, TEN-0012, LSE-12); units are found by reference (RMT-101 or rmt-1) or by "RMT 101"; contacts and documents are searched too. Related records are searched with sub-queries, not joins, so there are no duplicate rows and it stays fast; the extra "count all rows" query is gone.
- Typos: when nothing matches, similar names are shown with a note ("Mansoury" finds "Mansoori").
- Search as you type: the list updates after you pause typing (two letters or more), without Enter or a page reload, keeping filters, rows per page and the quick filter. If the page cannot refresh itself, Enter still searches normally (`core/static/core/lists.js`).
- The search box tells you what it can find (its placeholder and a line above the list), and the matched words are highlighted in the rows.
- Quick-filter chips with counts above Owners (Active, Inactive, Expired Documents, Expiring, With Vacancies, No Properties), Properties (Buildings, Standalone, With Vacancies, Documents Expiring, Inactive), Units (Vacant, Occupied, Reserved, Under Maintenance, Lease Ending, Never Leased) and Tenants (Active, Inactive, Blacklisted, Expired Documents, Expiring, Has a Running Lease, No Lease Yet). They combine with search and filters; click the chip again, or "All", to remove it.
- A summary line above every list: "Showing 37 of 2,018" with a tag for each active filter, chip and search (click a tag to remove it) and "Clear all".
- Ctrl+K (the search box in the sidebar) now finds records: tenants, units, properties, owners, leases and rent payments, only those you may view (`core/global_search.py`).
- Not done yet (waiting for decisions): searchable and multi-select filter drop-downs and the new filters (suggestion 5).


## Update 27c
- New command `python manage.py system_report`: checks code, templates, migrations, static files and data logic; writes system-report.txt (read-only).
- Fixed an old test that used "delete" as an invalid bulk action.


## Update 27e
- Data tools: new button "Seed small sample set" (2 owners, 2 properties, 2 units, 2 tenants; no leases or payments).
- Fixes from system_report: `raise ... from None` in 4 places, loop-variable binding in tenants/seed.py.


## Update 28
- Lease Payments tab: summary cards (total, received, outstanding, overdue, held, next due), progress bar, filter chips, search, status colour bars, line count and total for what is shown; the repeated 'LSE-0001 payment 1' caption is hidden.
