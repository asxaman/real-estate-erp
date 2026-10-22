"""
Data migration: convert the old property types to the new list.

Old type          -> New type
residential       -> residential_building
commercial        -> commercial_building
mixed_use         -> commercial_building
villa_compound    -> residential_building  (a compound with units is a building with units)
land              -> yard

The schema changes (removing "area", new choices, optional management fee) are
created by `python manage.py makemigrations` as migration 0003.
"""
from django.db import migrations

OLD_TO_NEW = {
    "residential": "residential_building",
    "commercial": "commercial_building",
    "mixed_use": "commercial_building",
    "villa_compound": "residential_building",
    "land": "yard",
}


def convert_types(apps, schema_editor):
    Property = apps.get_model("properties", "Property")
    for old, new in OLD_TO_NEW.items():
        Property.objects.filter(property_type=old).update(property_type=new)


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(convert_types, migrations.RunPython.noop),
    ]
