# Changelog

## Module 0 - Foundation (current)
- Django project (`config/`) with SQLite and the Unfold admin theme, UAE time zone (Asia/Dubai)
- `core` app:
  - Users and Roles (groups) managed by superusers only, so the superuser can create other users
  - "Data tools" page in the admin sidebar (superuser only):
    - Seed sample data (4 roles + 4 demo staff users, safe to run repeatedly)
    - Erase all data (type ERASE to confirm; superusers are never deleted)
  - Same tools from the terminal: `python manage.py seed_data` and `python manage.py erase_data`
  - A registry (`core/data_registry.py`) so every future module adds its own sample data and eraser automatically

## Planned modules
1. Properties and units (buildings, floors, units, owners)
2. Tenants and leases
3. Rent invoicing and payments (cheques, bank transfers, cash)
4. Accounting (chart of accounts, journal entries, reports)
5. Maintenance requests
6. Owner portal and tenant portal
