# Real Estate ERP

Property management ERP built with Django (admin interface) and SQLite.

Manages company-owned and third-party-owned properties: owners, properties, units.

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
