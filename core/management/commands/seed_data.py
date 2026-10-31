from django.core.management.base import BaseCommand, CommandError

from core.data_registry import TOOLS_OFF_MESSAGE, run_seeders, tools_allowed


class Command(BaseCommand):
    help = "Create sample data for testing (safe to run more than once)."

    def handle(self, *args, **options):
        if not tools_allowed():
            raise CommandError(TOOLS_OFF_MESSAGE)
        for line in run_seeders():
            self.stdout.write(self.style.SUCCESS(line))
