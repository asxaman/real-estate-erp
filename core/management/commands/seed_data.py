from django.core.management.base import BaseCommand

from core.data_registry import run_seeders


class Command(BaseCommand):
    help = "Create sample data for testing (safe to run more than once)."

    def handle(self, *args, **options):
        for line in run_seeders():
            self.stdout.write(self.style.SUCCESS(line))
