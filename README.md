# Real Estate ERP

Web ERP for a real estate management company.
Built with Django + Django Unfold (admin theme) + PostgreSQL.

Project folder on your computer: `C:\Projects\real-estate-erp\`

---

## First-time setup (Windows, PowerShell, VS Code)

### Step 1 - Check Python
Open **VS Code**, then **File > Open Folder...** and choose `C:\Projects\real-estate-erp`.
Open the terminal inside VS Code: **Terminal > New Terminal** (it should say PowerShell).

Run:

    python --version

You should see `Python 3.11` or newer. If you get an error, install Python from
https://www.python.org/downloads/ and **tick "Add python.exe to PATH"** in the installer,
then close and reopen VS Code.

### Step 2 - Create the virtual environment (a private box for this project's packages)

    python -m venv venv

A new `venv` folder appears. You only do this once.

### Step 3 - Activate it

    .\venv\Scripts\Activate.ps1

The line in the terminal now starts with `(venv)`.

If PowerShell says "running scripts is disabled on this system", run this once, answer `Y`,
then repeat the activate command:

    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

Every time you open a new terminal you must activate the environment again (this step only).

### Step 4 - Install the packages

    pip install -r requirements.txt

### Step 5 - Set up the PostgreSQL database

The system stores its data in PostgreSQL. Do the five parts below once.

**5a. Install PostgreSQL 18**
1. Open https://www.postgresql.org/download/windows/ and click **Download the installer**.
2. On the EDB page pick version **18.x** for **Windows x86-64** and download it.
3. Run the installer and click **Next** through the screens, keeping the defaults. Keep **PostgreSQL Server**,
   **pgAdmin 4** and **Command Line Tools** ticked.
4. When it asks for a **password for the database superuser (postgres)**, type a password and **write it down**.
   Use only letters and numbers (no quotes, spaces or symbols) to avoid problems later.
5. Keep port **5432** and the default locale. At the end, untick Stack Builder and click **Finish**.

**5b. Create the project database and its user**
1. Press the Windows key, type **SQL Shell (psql)** and open it.
2. Press **Enter** four times to accept Server, Database, Port and Username (`localhost`, `postgres`, `5432`, `postgres`).
3. Type the superuser password from 5a and press Enter (nothing shows while you type).
4. Paste these three lines, one at a time, pressing Enter after each. Replace `YourPasswordHere` with a NEW
   password for the project (letters and numbers only) and write it down:

       CREATE USER erp_user WITH PASSWORD 'YourPasswordHere';
       ALTER USER erp_user CREATEDB;
       CREATE DATABASE real_estate_erp OWNER erp_user;

   Each line should answer `CREATE ROLE`, `ALTER ROLE` and `CREATE DATABASE`.
5. Type `\q` and press Enter to leave.

**5c. Tell the project how to connect**
1. In the VS Code terminal run:

       Copy-Item .env.example .env

2. Open the new `.env` file in VS Code and replace `put-your-database-password-here` with the project password from 5b.
   Save the file. (It must be named exactly `.env`, not `.env.txt`. Git never uploads it.)

**5d. Install the packages again and test the connection**

    pip install -r requirements.txt
    python manage.py db_info

You should see `Database type : postgresql` and `Connection OK`.

**5e. Create the tables**

(From update 6 on, `makemigrations` will also create a first migration for the new audit log in the `core` folder.)

    python manage.py makemigrations
    python manage.py migrate

`makemigrations` should say "No changes detected". `migrate` then builds all tables in PostgreSQL.

If something goes wrong:
* `connection refused` - PostgreSQL is not running. Press Windows + R, type `services.msc`, find
  **postgresql-x64-18**, right-click and choose **Start**.
* `password authentication failed` - the password in `.env` does not match the one from 5b.
* `database "real_estate_erp" does not exist` - repeat 5b.
* `No module named psycopg` - repeat the `pip install -r requirements.txt` command with `(venv)` active.
* If `DB_NAME` is missing from `.env` the project quietly falls back to a local SQLite file
  (only meant for quick tests, never for real data); `python manage.py db_info` tells you which one is in use.

### Step 6 - Create your superuser (the main administrator)

    python manage.py createsuperuser

Type a username, an email (can be left empty) and a password (it will not show while you type).

### Step 7 - Start the system

    python manage.py runserver

Open http://127.0.0.1:8000/ in your browser and log in.
Stop the server any time with **Ctrl + C** in the terminal.

---

## What you can do in Module 0

* **Users** (sidebar > Administration > Users): click **Add user** to create staff accounts.
  After saving the username and password you are taken to a page where you set the name, email
  and permissions. Tick **Staff status** so the person can log in to the admin.
* **Roles (groups)**: collections of permissions you can assign to users (e.g. Accountant).
* **Data tools** (sidebar > Administration > Data tools):
  * **Seed sample data** - creates 4 roles and 4 demo users (password `Demo@12345`).
  * **Erase all data** - type `ERASE` to confirm. Superusers are never deleted.
* Only superusers can see Users, Roles and Data tools.

## What you can do in Module 1 (Properties and units)

Sidebar > **Property management**:

* **Owners** - the people or companies who own the buildings you manage. The optional
  *Portal access* field links an owner to a login account; it will be used by the owner portal later.
* **Properties** - each has a short unique **code** (e.g. `RMT`), a type, an owner and an emirate
  (all required). Address, map link and notes are optional.
  * **Owner "Company Owned"** is a built-in choice (always first in the list, cannot be renamed
    or deleted). Pick it for properties the company owns itself: the management fee fields
    disappear. For any other owner you must choose a **fee type** (percentage of collected rent,
    or a fixed AED amount per year) and enter the fee.
  * **Year built** and **Total floors** are hidden for Warehouse, Yard, Shop, Apartment and Parking Spot.
  * **Standalone properties** show **Annual rent (AED)** and **Status** instead of units.
  * **Property code**: 3 to 6 letters or numbers, starting with a letter (e.g. `RMT`, `ZV01`); saved in capitals.
  * **Apartment** (standalone) also asks for **Type** (Studio, 1BHK ... 5BHK) and **Bathrooms** (1-10).
  * **Residential Building** and **Commercial Building** have units. Save the property first,
    then the units section appears at the bottom of the page where you can add them.
  * **Villa, Warehouse, Yard, Shop, Apartment, Parking Spot** are standalone: no units section,
    and they cannot be chosen when adding a unit.
* **Units** - apartments, offices and shops inside a building. Shown as `CODE-number` (e.g. `RMT-304`).
  * Required: Property, Unit number, Floor, Unit type. Optional: Status, Bathrooms (1-10), Area, Furnished, Asking rent.
  * **Floor** accepts only: G, LG, UG, R, RF, PH, M, MZ, A, S, SC, GF, L, LV, E, B1, B2, B3 ... (basements),
    P1, P2, P3 ... (podium levels) or a number 1, 2, 3 ... Hover the "i" next to the field to see the list.
  * **Unit type**: Studio, 1BHK, 2BHK, 3BHK, 4BHK, 5BHK for residential buildings; Office or Shop for commercial buildings.
  * **Status** is optional for now; later it will follow the lease (leased, not leased, under maintenance ...).
* **Rules added in update 6** (all enforced on the server):
  * No owner can be named "Company Owned" (reserved) and two owners cannot share a name (capitals and extra spaces are ignored).
    Owner phone numbers are checked (for example `+971 50 123 4567`).
  * Inactive owners are not offered for new properties, and inactive buildings take no new units
    (existing records keep working).
  * A building cannot be switched between Residential and Commercial while it has units of the other kind.
  * Unit numbers are saved trimmed and in capitals (`g01` = `G01`). A numbered floor cannot be higher than the
    building's **Total floors**, and Total floors cannot be lowered below a floor that already has units.
  * Lists show money with thousands separators (for example `95,000`).
* **Seed sample data** now also creates 4 owners (one is the company itself), 12 properties
  (3 buildings with 80 units in total, plus 9 standalone properties of different types), and gives the demo roles permissions: Property Manager can do everything,
  Accountant and Maintenance Supervisor can only view, Leasing Agent can view and edit units.
  Log in as `demo.manager` / `demo.leasing` etc. to see what each role can see.
* **Erase all data** now also deletes all owners, properties and units.

## Safety and records (update 6)

* **Audit log** (sidebar > Administration > Audit log, superusers only): every time an owner, property, unit or user
  is created, changed or deleted, a line is added: who, when, and the old and new value of each changed field
  (so a rent or management-fee change can always be traced). Nobody can edit or delete these lines. Passwords are never logged.
  Seeding and erasing sample data add one summary line each instead of one line per record.
* **Superuser protection**: you cannot delete, deactivate or remove the superuser/staff status of your own account,
  and the last active superuser can never be removed. Ask another superuser to change your account.

## Automated tests

The project has automated checks of the business rules above. Run them after every update (with `(venv)` active):

    python manage.py test

The last lines should read `Ran ... tests` and `OK`. The tests build a temporary database named `test_real_estate_erp`
(your real data is never touched) and delete it afterwards. If a test fails, send me the full text it prints.

## Backup and restore (PostgreSQL)

**Make a backup** (takes a few seconds; do it before every update and at least weekly):

    python manage.py backup_db

The file appears in the `backups` folder (for example `backups\real_estate_erp_20261022_184500.dump`).
Copy it to another place too (USB drive, cloud drive): a backup that lives only on this PC is lost with the PC.
If the command says `pg_dump was not found`, run it by hand instead (replace `18` if your version differs):

    & "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -h localhost -U erp_user -F c -f backups\manual.dump real_estate_erp

**Restore a backup into the existing database** (this REPLACES the current data; stop `runserver` first):

    & "C:\Program Files\PostgreSQL\18\bin\pg_restore.exe" -h localhost -U erp_user -d real_estate_erp --clean --if-exists --no-owner backups\YOUR-FILE.dump

It asks for the `erp_user` password (nothing shows while you type). Messages that say a table "does not exist, skipping" are normal.

**Check a backup safely without touching your data**: open SQL Shell (psql) as `postgres` and run
`CREATE DATABASE restore_test OWNER erp_user;`, then run the restore command above with `-d restore_test`.

## Fixing the package versions

`requirements.txt` accepts a range of versions. To freeze exactly the versions that work on your PC, run once
(and again after any deliberate upgrade):

    python manage.py pin_versions --write

then commit `requirements.txt`. A new computer then installs exactly the same versions.

Same tools from the terminal:

    python manage.py seed_data
    python manage.py erase_data
    python manage.py erase_data --include-users

---

## Git and GitHub

### One-time setup
1. Install Git from https://git-scm.com/download/win (default options are fine), then restart VS Code.
2. Tell Git who you are (once per computer):

       git config --global user.name "Your Name"
       git config --global user.email "you@example.com"

3. Create an **empty** repository on https://github.com/new (name it `real-estate-erp`,
   keep it **Private**, do **not** add a README or .gitignore).
4. In the VS Code terminal:

       git init
       git add .
       git commit -m "Module 0: foundation"
       git branch -M main
       git remote add origin https://github.com/YOUR-USERNAME/real-estate-erp.git
       git push -u origin main

   (A browser window may ask you to sign in to GitHub the first time.)

### After every new module ZIP
1. Extract the ZIP into `C:\Projects\real-estate-erp\` and choose **Replace the files** if asked.
2. In the terminal (with `(venv)` active):

       pip install -r requirements.txt
       python manage.py makemigrations
       python manage.py migrate

   `makemigrations` writes the "recipe" for the new database tables (a file inside the
   module's `migrations` folder). `migrate` then builds the tables. Commit the new
   migration files to Git too (step 4 does this) so the recipe is saved with the code.
   If `makemigrations` says "No changes detected" that is fine.

3. Test it with `python manage.py runserver`.
4. Save to GitHub:

       git add .
       git commit -m "Module 1: properties"
       git push

The `.gitignore` file keeps your database, passwords and the `venv` folder out of GitHub.

---

## Project layout

    config/      project settings, URLs
    core/        Module 0: users, roles, seed/erase tools, audit log, safety rules, backup command
    properties/  Module 1: owners, properties, units
    backups/     database backups made by `backup_db` (never uploaded to GitHub)
    .env         your private settings and database password (never uploaded to GitHub)
    manage.py    command-line entry point

## Before going live
Set the environment variables `DJANGO_SECRET_KEY` (long random text) and `DJANGO_DEBUG=0`,
and set `DJANGO_ALLOWED_HOSTS` to your domain. Use PostgreSQL (see Step 5) with automatic, regular backups.
