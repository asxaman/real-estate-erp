# Real Estate ERP

Django + Django Unfold + SQLite. Built module by module.

## First-time setup (Windows, PowerShell in VS Code)

Run these in the VS Code terminal (Terminal menu, New Terminal), inside this folder:

    python -m venv venv
    .\venv\Scripts\python.exe -m pip install -r requirements.txt
    .\venv\Scripts\python.exe manage.py makemigrations core
    .\venv\Scripts\python.exe manage.py migrate
    .\venv\Scripts\python.exe manage.py createsuperuser
    .\venv\Scripts\python.exe manage.py runserver

Open http://127.0.0.1:8000/ and log in. Stop the server with Ctrl+C.

## Command line extras

    .\venv\Scripts\python.exe manage.py seed_data
    .\venv\Scripts\python.exe manage.py erase_data --yes

## Adding a module

Each module is its own Django app, registered in `INSTALLED_APPS` and in the Unfold sidebar
(`config/settings.py`). A module's sample data goes in `<app>/seeders.py` using `@register(...)`
from `core.seeding`; it is then picked up automatically by the Seed button.
