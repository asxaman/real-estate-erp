from django.core.management.base import BaseCommand

from core import seeding


class Command(BaseCommand):
    help = "Fill the database with sample data for testing."

    def handle(self, *args, **options):
        for line in seeding.run_all():
            self.stdout.write(self.style.SUCCESS(line))
