# Changelog
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
2. Tenants and leases
3. Rent invoicing and payments (cheques, bank transfers, cash)
4. Accounting (chart of accounts, journal entries, reports)
5. Maintenance requests
6. Owner portal and tenant portal
