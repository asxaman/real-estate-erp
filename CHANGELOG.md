# Changelog

## Update 67 - New module: HR & Payroll
**Migration needed.** Run `python manage.py makemigrations hr`, then `python manage.py migrate`, then `python manage.py test`, then Ctrl+F5 twice.
- Left menu, new group **HR & Payroll**: Employees, Leave requests, Daily attendance, Attendance records, Payroll, Salary slips. Numbers are automatic: EMP-0001, LV-0001, PAY-0001.
- **Employees**: personal details, job, department, joining date, weekly days off, salary (basic + housing + transport + other allowance), overtime rate, yearly leave days, and how the salary is paid (bank transfer / cheque / cash). Documents tab: passport, Emirates ID, residence visa, labour card, health insurance, contract, with expiry dates (they appear in Expiring Documents). An employee who has left is marked "Left the company" with the last working day; one with pay or leave on record is never deleted.
- **Leave**: annual, sick, compassionate, maternity / paternity, unpaid and other paid leave. Working days are counted (the employee's days off are skipped). Annual leave is checked against the yearly entitlement, overlapping requests are refused, and a request is approved or rejected (a reason is needed to reject). Approving needs its own permission.
- **Daily attendance**: one page for the day lists every employee; choose Present, Late, Half day, Absent, On leave or Public holiday, add overtime hours and a note, and press Save. A button marks everyone not yet marked as present. Days off are not counted unless marked. Approved leave is shown already. Attendance can be corrected until that month's payroll is approved.
- **Payroll**: choose the month; a salary slip is made for every employee who worked in it. Pay is worked out on a 30-day month: unpaid days (absent, half days, unpaid leave, days before joining or after leaving) are deducted; overtime is paid at the hourly rate (basic / 30 / 8 x the overtime rate). On the Salary Slips tab add extra additions or deductions (advances, fines ...). Slips that need a look (days not marked, leave without an approved request) are flagged.
- **Draft -> Approved -> Paid.** Approving locks the amounts (and the month's attendance and leave). Paid makes one **payment voucher** for every salary (cash, cheque with its number, or bank transfer). Tick "Recalculate" on a draft after changing attendance or leave.
- **Bank (WPS) file**: for an approved payroll, "Download bank (WPS) file" makes the SIF salary file for the employees paid by bank transfer (needs the employer ID and routing codes). Check the first file with your bank.
- **Salary slip**: print / save as PDF, one slip or all of the month's slips.
- Search, Ctrl+K search (employees), Excel / CSV export, activity log (IBAN and ID numbers are masked).
- Roles: a new **HR Manager** role (employees, leave, attendance, payroll drafts); the Accountant approves and pays payroll. Property Manager and Leasing Agent see nothing of HR (salaries are private).
- Sample data and Erase include HR (the sample HR user is demo.hr).
- Not in this update (planned): end-of-service gratuity, leave pay-out, bonuses.

## Update 66 - New module: Owner Statements
**Migration needed.** Run `python manage.py makemigrations statements`, then `python manage.py migrate`, then `python manage.py test`, then Ctrl+F5 twice.
- Left menu, under Payables: **Owner statements** (`/admin/owner-statements/`). Numbers are automatic: STM-0001 ...
- **Generate**: choose the owner and the period (it offers last month). The statement is built by itself from:
  - **Rent received**: rent payments marked as paid for the owner's properties (before VAT; deposits and fees are not the owner's money).
  - **Management fees** from the owner agreement: % of rent collected; the fixed yearly fee (for the days of the period); the % of the yearly contract rent (once, in the statement where the lease starts).
  - **Guaranteed rent**: for a guaranteed-rent agreement the owner is credited the guaranteed amount for the days of the period, and the rent actually collected is listed "for information" (it stays with the company).
  - **Costs charged to the owner**: vendor bills and expenses set to "Owner" (cancelled ones are left out).
- **Nothing is missed or counted twice**: a payment, bill or expense is on one statement only. A bill entered late for an earlier month goes on the next statement. Statements follow each other without gaps or overlaps; an owner's first statement can start on any date.
- **Adjustment** (+ or -, with a note) on a draft, for example an advance. **Brought forward**: when an owner owes the company, the debt is carried to the next statement.
- **Draft -> Approved -> Paid.** An approved statement is locked. Setting it to Paid (with how it was paid) makes a **payment voucher** automatically. A wrong statement is voided with a reason; its items are released for a new one. A paid statement cannot be changed.
- List actions: approve the ticked drafts, rebuild the ticked drafts (to pick up new items).
- **Print / Save as PDF**: an A4 owner statement with your company details, the lines by section, totals and the payout in words.
- Owner page: new read-only "Statements" tab. Search, Ctrl+K search, Excel / CSV export, activity log.
- Roles: Accountant can generate, approve and pay out; Property Manager can generate and view but not approve; Leasing Agent has none.
- Sample data and Erase include owner statements.

## Update 65 - New module: Expenses, Vendor Bills & Payment Vouchers
**Migration needed.** Run `python manage.py makemigrations payables`, then `python manage.py migrate`, then `python manage.py test`, then Ctrl+F5 twice.
- Left menu, new group **Payables**: **Vendor bills** (`/admin/vendor-bills/`), **Expenses** (`/admin/expenses/`), **Payment vouchers** (`/admin/payment-vouchers/`). Numbers are automatic: BILL-0001, EXP-0001, PV-0001.
- **Vendor bills**: an invoice received from a vendor (vendor, the vendor's own invoice number, bill and due dates, type, property / unit, amount, 5% VAT option, scan). It can be paid in one go or in parts. The list shows Paid, Part Paid, Not Paid and Overdue, with what is left to pay, and quick filters (To Pay, Overdue, Due In 7 Days, Part Paid, Paid, Cancelled). The same vendor invoice number cannot be entered twice.
- **Record a payment**: on the bill's page, "Record a payment" opens the voucher form with the vendor and the amount left already filled in. Or tick bills and choose "Pay the selected bills in full (today, bank transfer)". You cannot pay more than what is left.
- **From maintenance**: on the Maintenance Requests list, tick completed jobs and choose "Create vendor bills for the selected jobs". The bill takes the vendor, property, unit, final cost, who pays (owner / tenant / company) and is due 30 days after completion. A job gets one open bill.
- **Expenses**: costs paid directly with no vendor bill (DEWA, government fees, insurance, bank charges ...). Paid to, type, date, method, reference, amount, VAT, scan. Its payment voucher is made by itself and stays in step if the expense is edited.
- **Who pays**: every bill and expense is charged to the Company, the Owner (the property's owner) or a Tenant. Costs charged to owners are what the owner statements will use. A property owned by the company is charged to the company.
- **Payment vouchers**: the proof of money paid out. **Print / Save as PDF** gives a clean A4 page with the company details, who was paid, how, the amount in words and signature lines.
- Nothing is deleted. A wrong bill or expense is cancelled (with a reason); a wrong voucher is voided (with a reason), which puts the money back on the bill. An issued voucher cannot be changed.
- Vendor page: new read-only "Bills" tab. Search, Ctrl+K search, Excel / CSV export, activity log.
- Roles: Accountant has full access; Property Manager can enter bills and expenses and view vouchers; Leasing Agent has none.
- Sample data and Erase include bills, expenses and vouchers.

## Update 64 - New module: Tenant Receipts & Invoices
**Migration needed.** Run `python manage.py makemigrations billing`, then `python manage.py migrate`, then `python manage.py test`, then Ctrl+F5 twice.
- Left menu, under Tenancy: **Receipts** (`/admin/receipts/`) and **Invoices** (`/admin/invoices/`). Numbers are automatic: RCP-0001 ..., INV-0001 ...
- **Receipts are made by themselves** when a payment is marked as paid (one at a time, in bulk, or from the lease page). Not for refunds or amounts kept from the deposit. If the payment later bounces, its receipt is voided; if its amount is changed, the old receipt is voided and a new one issued.
- **Invoices**: on the Tenant Payments list, tick payments and choose "Create invoices for the selected payments". Already-invoiced, waived, refund and deduction lines are skipped. The list shows whether each invoice is Paid, Overdue or Not Paid Yet.
- "Issue receipts for the selected payments" (same list) makes the receipts of paid payments that have none (for example data loaded in bulk).
- **Print / Save as PDF**: every receipt and invoice opens as a clean A4 page with your company name, address, phone, email and TRN (System settings), the tenant, what it is for, VAT at 5% where it applies, the amount in words, and a VOID stamp when voided. An invoice with VAT is titled "Tax Invoice" when the company TRN is filled in.
- Documents are never deleted or edited: a wrong one is voided (with a reason) and a new one issued. They keep their wording even if the tenant is renamed or the lease is removed.
- Tenant page: new read-only tabs "Receipts" and "Invoices". Quick filters, search, Ctrl+K search, Excel / CSV export, activity log (voiding / notes).
- Roles: Property Manager and Accountant can view, issue, void and export; Leasing Agent can view and issue.
- Sample data and Erase include receipts and invoices.

## Update 63 - New module: Vendors & Maintenance
**Migration needed.** Run `python manage.py makemigrations core properties maintenance`, then `python manage.py migrate`, then `python manage.py test`, then Ctrl+F5 twice.
- New left-menu group **Maintenance** with **Maintenance Requests** (`/admin/maintenance-requests/`) and **Vendors** (`/admin/vendors/`).
- **Vendors** (VND-0001 ...): the outside companies that do the work. Trade, contact, phone, email, trade licence number, TRN, Documents tab (trade licence, insurance, VAT certificate, contract) with expiry dates, and a Jobs tab. Vendor documents also appear in Expiring Documents. A vendor with open jobs cannot be made inactive; a vendor who has done work cannot be deleted (make them inactive).
- **Maintenance Requests** (MNT-0001 ...): property and optional unit, the problem, type of work, priority, status (New, Awaiting approval, Approved, In progress, Completed, Rejected, Cancelled), vendor, planned and finished dates, estimate, final cost, vendor invoice scan.
- **Who pays**: Owner, Tenant or Company. The tenant is filled in by itself from the running lease. "Tenant pays" needs a tenant; a company-owned property is paid by the company.
- **Approval**: a job above the limit waits as "Awaiting approval" and cannot start until someone with the approval permission approves it (Approval tab, Decision). The limit is the owner's own limit (Owner page, Overview tab) or, when empty, the company-wide limit (System settings, Maintenance tab, AED 2,000 to begin with). If the cost later goes above what was approved, it needs approval again. A rejected job needs a reason and can be reopened.
- Completed jobs need the vendor and the final cost. Only jobs nobody acted on can be deleted; others are cancelled.
- New read-only "Maintenance" tab on the Property, Unit and Tenant pages. Quick filters (Open, Awaiting Approval, In Progress, Urgent, Overdue, Completed), search, Ctrl+K search, Excel / CSV export, activity log.
- Roles: Property Manager full access and can approve; Accountant can view and export; Leasing Agent can log and edit jobs and view vendors.
- Sample data and Erase include vendors and jobs.

## Update 62 - The management fee now lives only in the Owner Agreement
No migration needed. Run `python manage.py migrate` once (it turns fees already entered on properties into agreements), then `python manage.py test`, then Ctrl+F5 twice.
- The Management Fee boxes (type, percent, fixed) are gone from the Property page. The page shows the fee read-only from the agreement that covers the property today, with a link to open it (or "No running owner agreement - Create one").
- Fees that were already entered on properties are turned into Owner Agreements once, automatically (check them in Owner Agreements: they are marked "Created from the management fee that was on the property"). Nothing is lost and it never runs twice.
- Change Owner (bulk): the fee box is gone. A property that an agreement covers cannot be moved to another owner until that agreement is ended; the page says which one.
- Import: the three fee columns are no longer in the template and are ignored if a file still has them. Export: the "Management fee" column shows the fee of the running agreement (for example "AGR-0003: 5% of rent collected").
- A property no longer needs a fee to be saved.
## Update 61 - New module: Owner Agreements
**Migration needed.** Run `python manage.py makemigrations agreements`, then `python manage.py migrate`, then `python manage.py test`, then Ctrl+F5 twice.
- New module **Owner Agreements** (left menu, under Property Management; address `/admin/owner-agreements/`): the management contract between the company and an owner. Numbers are given automatically (AGR-0001 ...).
- One agreement belongs to one owner and covers one or more of his properties (tab "Properties Covered").
- Fees (tab "Fees"): % of rent collected, fixed fee per year, % of the annual contract rent (any mix of these), or a guaranteed rent to the owner (stands alone).
- Dates: start, end (optional), renews automatically, notice period, signed on. "Owner is paid": monthly, every 3 or 6 months, once a year, or as each rent is collected.
- Status: Draft, Active, Terminated (with date and reason). The list shows Running, Ending Soon, Not Started, Expired, Draft, Terminated with counts.
- Rules: a property must belong to the agreement's owner; a property cannot be under two agreements in the same period; an active agreement must cover at least one property; only a Draft can be deleted (a signed one is terminated instead); company-owned properties need no agreement.
- The Owner page and the Property page have a new read-only tab "Agreements". Search box (Ctrl+K) finds agreements. Excel / CSV export. Changes are recorded in the activity log.
- Roles: Property Manager has full access, Accountant can view and export, Leasing Agent can view.
- Sample data: "Seed sample data" creates agreements from the fees on the sample properties; "Erase all data" removes them.
- Not changed yet: the Management Fee boxes on the Property page are still there. They will be removed in the next step, once agreements are confirmed working.
## Update 60 - Tenant Payments: the "With the ticked rows" buttons are gone
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Removed the row "With the ticked rows: Mark as Paid, Mark Cheques as Deposited, Edit Ticked, Remove Ticked" above the Tenant Payments list.
- Nothing is lost: tick rows and use the Actions list that appears (mark as paid, mark cheques as deposited, mark as bounced, edit, remove, export). Each row also keeps its own Record and Edit buttons.
## Update 59 - Same layout on a laptop as on a large monitor
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- The side panel now stays docked beside the page, open by default, from 1024 pixels wide (it was 1280). A laptop whose screen Windows scales to 150% gets the same layout as a large monitor.
- Open or closed is remembered on these screens too. Below 1024 pixels (tablets, phones) the panel still floats over the page and starts closed.
## Update 58 - Drawer handle for the side panel
No migration needed. Ctrl+F5 twice.
- The button that opens and closes the left panel moved from the top bar to a small handle fixed at the middle of the left edge. It stays in reach however far the page is scrolled, on every page.
## Update 57c - Reports have their own addresses
No migration needed. Restart the server, run `python manage.py test`, then Ctrl+F5 twice.
- Rent Roll is now `/admin/reports/rent-roll/` and Monthly Collection `/admin/reports/monthly/` (they used to sit under `/admin/leases/`).
- Both pages say "Back to Reports" and go back to the Reports page (Rent Roll said "Back to Leases" and Monthly Collection went to Leases).

## Update 57 - One clean address per module
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Every module now has its own plain address instead of looking like it sits inside another one: Owners `/admin/owners/`, Properties `/admin/properties/`, Units `/admin/units/`, Tenants `/admin/tenants/`, Leases `/admin/leases/`, Tenant Payments `/admin/tenant-payments/`, Users `/admin/users/`, System Settings `/admin/system-settings/`. The grouping stays only in the left menu.
- Old addresses (for example `/admin/leases/leasepayment/`) still work and send you to the new one, so bookmarks do not break.
- Nothing in the database or the app folders changed.
- (57b) Fixed the first try, which made modules open a "Page not found": Unfold supplies its own admin site, so the addresses are now rewritten on that site.
## Update 56 - New "Reports" module
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- New menu section "Reports" with a Reports page that lists every report (Rent Roll, Monthly Collection). Click a card to open the report.
- Removed the "Reports: Rent roll, Monthly collection" links from the top of the Leases and Tenant Payments lists.
- Future reports only need one line in `core/reports.py` and appear on the page automatically (shown only to users allowed to see them).
## Update 55 - Expiring Documents now behaves like the other lists
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Search box has the "i" icon (what you can search by), live as-you-type search, and yellow highlight of the matches, like every other list.
- Smarter search: several words in any order across type, name, document and number; numbers match without dashes or spaces; a small typo still finds the record (with a "similar names are shown" note).
- The Show chips (Needs Attention, Expired, Expiring Soon, All) now show counts; column headings can be clicked to sort.
- Fixed in all lists: after a live search the bottom strip (count and page numbers) now refreshes too, and the page number resets.
## Update 54 - No "Showing N" line at the top of lists
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Removed the "Showing 0" / "Showing 56" line above every list (the count is already in the bottom strip). When a filter or search is in use, the line with the filter tags and "Clear all" still appears. Same for Expiring Documents; Rent Roll keeps only "Running leases on <date>".

## Update 53 - Empty lists: Rows per page also goes to the bottom
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Fixed: after erasing all data, the lists (Properties, Units, Tenants, Leases, Tenant Payments ...) showed "Rows per page" at the top. The move to the bottom row only ran when the list had a table. It now also runs for an empty list, using the "0 properties" line at the bottom.

## Update 52 - Error messages no longer push boxes out of line; Year Built range 1800 to 9999
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- In every table-style section of a form (Owner bank accounts, contacts and documents; tenant and property documents; and any other), an error such as "This field is required" or "This IBAN is not valid" is now shown under its box without changing the height of that box's cell. All boxes in the row stay on the same line, and the row makes room for the message.
- Year Built accepts 1800 to 9999 (was 1900 to current year + 5).

## Update 51 - Top of Expiring Documents and Rent Roll built like the other lists
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- The quick-filter buttons use the same purple pill look as the other lists (the old blue ones are gone).
- "Showing N" line like the other lists.
- The search box is Unfold's own search box (magnifier, "/" key hint), inside the same bordered bar that sits on top of the table; the download buttons sit at the right of that bar like the "Filters" button does elsewhere.
- The table uses Unfold's own heading and row styles (bold headings, striped rows, rounded border joined to the search bar).

## Update 50 - Bottom strip of Expiring Documents and Rent Roll: same sticky wrapper as Unfold's lists
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- The strip is now wrapped in exactly the same two containers Unfold uses for its own list footer, so it stays fixed at the bottom of the screen, has the same blur, top line and width, and no longer runs under the Back to Top button.
- Other lists are not touched.

## Update 49 - Bottom strip of Expiring Documents and Rent Roll now built like the other lists
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- The strip is now placed in the same footer area, with the same wrapper (64px row, page numbers "1 2 3 ... 20 21", then "- 1238 documents"), as the admin lists, so it stays in view and looks the same. Rows per page and Page N of M [Go] sit at the right.
- Previous / Next buttons were replaced by the page numbers, as on the other lists.

## Update 48 - Fix: stray text above the bottom strip of Expiring Documents and Rent Roll
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- A long note inside the shared bottom-strip template was printed on the page instead of being hidden. It is now a proper hidden comment.

## Update 47 - Bottom strip of Expiring Documents and Rent Roll matches the other lists
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- The strip is taller, runs edge to edge across the page, has the same softer top line and the same count text size as the strip on the other lists.
- Test fix: the Expiring Documents test expected data that test does not create.

## Update 46 - The bottom strip stays in view on every list; two test fixes
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Expiring Documents and the Rent Roll report now have the same bottom strip as the other lists: it stays fixed at the bottom of the screen while you scroll (count on the left; Previous, Next, Rows per page and "Page 1 of N [Go]" on the right). One shared piece of code (`core/templates/core/_pager_bar.html`) now builds it, so any new list page gets it by including that file.
- Rent Roll also gets the frozen column headings and the Back to Top button.
- Test fixes: one new test was in the wrong test group, and one checked for a word the script no longer contains.

## Update 45 - Owners list back, Rows per Page is one big button, Expiring Documents gets the list tools
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Fixed: the Owners list looked empty at the bottom. The previous update hid the "56 owners" line; nothing is hidden any more. "Rows per page" and "Page 1 of 1" now go into the same row that holds the count.
- "Rows per page" is now one button: click anywhere on it (the words, the border, the number or the arrow) to open the list.
- Expiring Documents now has what the other lists have: the column headings stay in view while you scroll, the Back to Top button, "Rows per page" (50, 100 or 200; 100 is the default now) and "Page 1 of 25 [Go]" at the bottom of the list, together with Previous and Next.

## Update 44 - Bottom row on every list: count, Rows per Page, Page 1 of 1
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Every list now has the "Page 1 of N [Go]" box, even when everything fits on one page. On one-page lists (Owners, Users and so on) the bottom row is: the count on the left ("56 owners"), then "Rows per page" and "Page 1 of 1" on the right.
- Fixed: the Rows per page box in the Users list (and other one-page lists) had no left padding, so its label touched the border. It now looks like the other filter boxes.

## Update 43 - Rows per page also on single-page lists (Owners and others)
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Lists that fit on one page (for example Owners) had no page numbers, so "Rows per page" stayed at the top. It now sits at the bottom right of every list, in line with the "55 owners" count (and the "Page 1 of N" box when there are several pages).

## Update 42 - Rows per page next to the page numbers
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- In every module, "Rows per page" moved from the top of the list down to the row under the table, just left of the "Page 1 of 21 [Go]" box. The filter bar at the top is tidier (and has room for more filters).

## Update 41 - Unit, Tenant and Property search on the lease form
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- Fixed: typing a full unit reference such as B010-301 in the Unit box said "No match". The search looked for the whole text inside the unit number or the building code separately. It now checks every word you type against the building code, building name, unit number and the full reference.
- Works in any style: B010-301, B010 301, b010301, "301 Jumeirah", or the whole chosen label. Tenants: words in any order ("Abbas Ahmed") and phone numbers typed with spaces, dashes or +971. Properties: name or code words.

## Update 40 - Tidier list bars
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- The "Go to page" box now sits in the page-number row under the table (next to "1 2 3 ... 122 123"), not above the list.
- The filter dropdowns (Leases, Tenant Payments and the Rows per page bar in every module) are now small, compact pills that take only the width of the chosen value, so they fit on fewer rows.
- The "Search finds: ..." line is gone. An "i" sits inside the search bar instead; hover or tap it to see what you can search by.

## Update 39 - Lists: headings that stay in view, a back-to-top button, go to page
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- On every list, the column headings now stay at the top of the screen while you scroll down through the rows (a copy of the heading row appears just under the page header). Sort links and the select-all box on the copy work.
- When you have scrolled down a list (or any long page), a "Back to Top" button appears at the bottom right. It is also your sign that you are not at the top. Click it to go back up smoothly.
- A "Go to page" box: "Page 3 of 122 [Go]" in the summary line above the list and again under the table. Type a page number and press Enter (or Go). It shows on lists with three or more pages and keeps your search, filters and rows per page.
- A page number that does not exist (typed by hand, or the list got shorter) now shows the nearest page instead of an error page.

## Update 38 - Change Paid By in the Cheque Grid
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- In the Cheque Grid, the Paid By cell is now editable: click it (or type a few letters, or press Space) and choose Cheque, Cash, Bank transfer, Card or Other. Arrow keys, Enter and paste from Excel work in it like the other cells, and Ctrl+D copies the cell above.
- New helper "Set Paid By for Unpaid": choose a way of paying and apply it to every payment that is still pending in one click.
- Switching a payment away from Cheque clears its cheque number, bank and date (cheque details only go with a cheque) and switching it back restores what the cheque had. Typing a cheque number on an unpaid cash line makes it a cheque and gives it the due date as cheque date.
- A payment that was already received can be corrected to another way of paying (for example cash to bank transfer). Held and bounced cheques stay cheques, and money kept from the deposit cannot be changed here. The save is still all-or-nothing, and every change goes into the audit log.

## Update 37 - Cheque grid starts each cheque date at its due date
No migration needed. Run `python manage.py test`, then Ctrl+F5 twice.
- In the Cheque Grid, every unpaid cheque line with no cheque date yet now starts with its due date in the Cheque Date cell (shown in grey and italic, as a starting value). Change it where a cheque is dated differently. It works on existing leases too.
- A date is saved together with the rest of its row when you type or change something in that row and press Save. Rows you do not touch are left as they are, so opening and closing the grid asks no "unsaved changes" question.
- Received payments, held and bounced cheques, and lines planned as cash are not filled in.

## Update 36 - Cheque grid on the lease's Payments tab
No migration needed. Run `python manage.py test`, then Ctrl+F5.
- New "Cheque Grid" button (lease header, and at the top of the Payments tab): an Excel-style table of the lease's payment lines (rent, deposit, fees ...) where you fill in the cheque number, bank and cheque date for many payments at once, then save once.
- Tab, Enter and the arrow keys move between cells; you can paste a column or a block copied from Excel; Ctrl+D copies the cell above; bank cells give a searchable list of the banks in System settings.
- Helpers above the grid: number the empty cheques from a first number, fill the empty banks, and set empty cheque dates to the due date.
- Edited cells are highlighted; a repeated cheque number is marked red; Cancel warns when there are unsaved changes. Payments received in cash are locked. Typing cheque details on a cash line that is not received turns it into a cheque.
- One all-or-nothing save: if any row has a problem (unknown bank, unreadable date, a cheque number used twice, a cheque deposited before its date) nothing is saved and every problem is listed. Two people saving at once are handled one after the other.
- The "Cheque numbers" feature in the lease header is unchanged.

## Update 35 - WhatsApp icon
No migration needed. The WhatsApp button now uses the official icon file when you put it in core/static/core (whatsapp.svg or whatsapp.png); until then it shows the plain green chat button. One place, so every list and form uses it. The icon is shown at 18px and the official whatsapp.svg is included.

## Update 34 - VAT on fees is your choice; one VAT basis on every lease screen
Run `python manage.py makemigrations`, `migrate` and `test` (two new tick boxes on the lease need a small migration).
- Fees tab: new boxes "VAT (5%) added to the agency fee" and "VAT (5%) added to the office fee". They are off by default; tick them only when VAT is really charged on that fee. The deposit never has VAT. The payment line, totals, dashboard money and exports all follow the boxes, and a renewal keeps them.
- On a lease with VAT, every figure on the lease page now includes VAT: the header (Annual rent incl. VAT, Paid, Still to pay, Overdue), the overdue banner and the list's progress bar, the same as the Money summary.
- Rent roll: the Monthly column now includes VAT (the Annual rent column is shown before VAT, and both are labelled), so it agrees with Received and Overdue.

## Update 33 - Fixes from the full logic review
No migration needed. Run `python manage.py test`.
- A deposit refund, or a deposit deduction line, can no longer be larger than the security deposit still left (adding or editing).
- Waived rent is left out of "still to receive" in the lease export and the Monthly collection report; the Payments tab cards leave it out of the total too and show exact amounts (fils) instead of rounding to whole AED.
- A lease terminated with a future date still counts as running until that date: it stays in the rent roll (showing the real end date), the dashboard "Active leases" and the Active filter, and its tenant and property cannot be made inactive.
- The dashboard "Overdue payments" panel now counts bounced cheques, the same as the red card.
- Two people adding the same kind of payment (or deduction) to one lease at the same moment no longer collide: the second waits its turn instead of getting an error page.
- "Deposit is" in the lease export is worked out from the real refund and deduction lines (Held, Partly settled, Refunded, Deducted (kept)).

## Update 32 - Lease dates, termination, renewal deposit, switching off, waived payments
Run `python manage.py makemigrations`, `migrate` and `test` (the new "Waived" payment status needs a small migration).
- Lease dates: while nothing has been received, you can change the start date and the pending payments move with it. Once a payment is received or a cheque is held, the dates are fixed (renew the lease for a new period).
- Terminating a lease now also updates its annual rent to the rent that is still scheduled.
- Renewing a lease no longer asks again for a security deposit that was already received and is still held; it stays with the original lease.
- A tenant or property with a running or upcoming lease cannot be made inactive.
- New payment status "Waived": an overdue payment can be written off (Waive button in the Record pop-up). It stops counting as late, builds no late fee and is left out of "still to receive".
- "Rebuild the payment schedule" only replaces the rent lines; fines and other lines are kept.
- Moving a lease from one unit or property to another now frees the old place (it becomes Vacant) instead of leaving it Occupied.
- The Properties list has a Status column for stand-alone properties (Occupied, Vacant ...).
- A unit moved to another building takes its leases with it (their property no longer goes stale).
- A terminated lease cannot be made active again; the end date cannot be moved before a payment that is still due; a property rented as a whole cannot be switched to a building.

## Update 31 - Vacant standalone properties on the dashboard, friendlier delete messages, locked lease total
No migration needed. Run `python manage.py test`.
- Dashboard: the Vacant card, the status bars and the vacant list now count vacant units and vacant standalone properties together.
- Deleting something that is protected (a lease with payments, a tenant with leases) now shows one clear sentence instead of a raw list of records, in the pop-up and on the Django page.
- Lease form: once a lease has rent payments, Annual rent and Payments per year are locked (shown read-only with a note). The total is the sum of the rent payments; edit or remove a payment and the lease total follows.

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


## Update 29
- New lease form: "Lease no." says it is created automatically.
- Leases tab on units, properties and tenants: "Open lease" button instead of "Change"; the tenant's name opens the tenant.
- Villas and other stand-alone properties now have a Photos tab (new table PropertyPhoto); units and villas can upload many photos at once (button or drag and drop).
- Lease form: "Today" button under Starts on, "+1 Year" under Ends on (the day before the anniversary).
- Banks: new list in System settings -> Banks (new table Bank, started with UAE banks). Cheque pop-ups and owner bank accounts choose from it (searchable).
- Record Payment on a lease: choose which payment; "Cheque Held" and "Cheque Bounced" are hidden unless the payment is a cheque.
- Needs `makemigrations` and `migrate` (two new tables).


## Update 30
- Dates now follow the style chosen in System settings everywhere: the pop-ups (Record Payment, Edit, Terminate, bulk edit ...) use a text box and calendar in that style instead of the browser's own date picker; printed lease and rent roll, the dashboard line, search results, messages, notes, Excel downloads (cell format) and CSV downloads use it too.
- Imports also accept dates in the chosen style.

- Update 30b: the pop-ups' server side now reads dates typed in the chosen style (before it accepted only 2026-11-06).
