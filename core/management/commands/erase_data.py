from django.core.management.base import BaseCommand, CommandError

from core.data_registry import TOOLS_OFF_MESSAGE, run_erasers, tools_allowed


class Command(BaseCommand):
    help = "Permanently erase all business data. Superuser accounts are always kept."

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-users",
            action="store_true",
            help="Also delete all non-superuser users and all roles (groups).",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation question.",
        )

    def handle(self, *args, **options):
        if not tools_allowed():
            raise CommandError(TOOLS_OFF_MESSAGE)
        if not options["yes"]:
            answer = input("This permanently deletes data. Type ERASE to continue: ")
            if answer.strip() != "ERASE":
                raise CommandError("Cancelled. Nothing was erased.")
        for line in run_erasers(include_users=options["include_users"]):
            self.stdout.write(self.style.WARNING(line))
