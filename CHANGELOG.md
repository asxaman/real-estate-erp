# Changelog

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
