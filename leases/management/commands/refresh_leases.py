from django.core.management.base import BaseCommand

from leases import occupancy


class Command(BaseCommand):
    help = "Bring unit / property Occupied and Vacant statuses up to date with their leases."

    def handle(self, *args, **options):
        changed = occupancy.refresh_all()
        self.stdout.write(self.style.SUCCESS(f"{changed} status(es) updated."))
