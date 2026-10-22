from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Show which database the project is using and test the connection."

    def handle(self, *args, **options):
        settings = connection.settings_dict
        self.stdout.write(f"Database type : {connection.vendor}")
        self.stdout.write(f"Database name : {settings.get('NAME')}")
        if connection.vendor == "postgresql":
            self.stdout.write(f"Server        : {settings.get('HOST')}:{settings.get('PORT')}")
            self.stdout.write(f"User          : {settings.get('USER')}")
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute("SELECT version()")
            else:
                cursor.execute("SELECT sqlite_version()")
            self.stdout.write(f"Version       : {cursor.fetchone()[0]}")
        self.stdout.write(self.style.SUCCESS("Connection OK"))
