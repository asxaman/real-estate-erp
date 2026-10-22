# Real Estate ERP

Web ERP for a real estate management company.
Built with Django + Django Unfold (admin theme) + SQLite.

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

### Step 5 - Create the database

    python manage.py migrate

This creates the file `db.sqlite3`.

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
       python manage.py migrate

3. Test it with `python manage.py runserver`.
4. Save to GitHub:

       git add .
       git commit -m "Module 1: properties"
       git push

The `.gitignore` file keeps your database, passwords and the `venv` folder out of GitHub.

---

## Project layout

    config/      project settings, URLs
    core/        Module 0: users, roles, seed/erase tools
    manage.py    command-line entry point
    db.sqlite3   your database (created by migrate, never uploaded to GitHub)

## Before going live
Set the environment variables `DJANGO_SECRET_KEY` (long random text) and `DJANGO_DEBUG=0`,
and set `DJANGO_ALLOWED_HOSTS` to your domain. SQLite is fine for development and small
teams; we can plan a move to PostgreSQL later if the company grows.
