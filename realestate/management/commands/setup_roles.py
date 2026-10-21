from django.core.management.base import BaseCommand

from realestate.roles import ROLES, ensure_roles


class Command(BaseCommand):
    help = "Create the ready-made user roles. Use --reset to put them back to the original permissions."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Overwrite existing roles.")

    def handle(self, *args, **options):
        changed = ensure_roles(reset=options["reset"])
        if changed:
            self.stdout.write(self.style.SUCCESS("Roles set up: " + ", ".join(changed)))
        else:
            self.stdout.write("All roles already exist. Use --reset to restore the originals.")
        self.stdout.write("Available roles: " + ", ".join(ROLES))
