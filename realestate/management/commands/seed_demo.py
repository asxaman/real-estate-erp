from django.core.management.base import BaseCommand, CommandError

from realestate.demo import DemoError, seed_demo


class Command(BaseCommand):
    help = "Load demo owners, properties, tenants, leases, cheques and expenses to play with."

    def handle(self, *args, **options):
        try:
            result = seed_demo()
        except DemoError as error:
            raise CommandError(str(error))
        self.stdout.write(self.style.SUCCESS(f"Demo data loaded ({result['records']} records)."))
