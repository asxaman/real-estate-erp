from django.core.management.base import BaseCommand, CommandError

from core import datatools


class Command(BaseCommand):
    help = "Erase all business data (user accounts are kept unless --include-users)."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Confirm you really want this.")
        parser.add_argument("--include-users", action="store_true", help="Also delete all users.")

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("This deletes everything. Re-run with --yes to confirm.")
        deleted = datatools.erase_all_data(include_users=options["include_users"])
        self.stdout.write(self.style.SUCCESS(f"Erased {sum(deleted.values())} records."))
