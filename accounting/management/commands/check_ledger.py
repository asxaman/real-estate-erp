from django.core.management.base import BaseCommand, CommandError

from accounting.reports import ledger_health


class Command(BaseCommand):
    help = "Check the integrity of the books: balanced entries, sub-ledgers vs control accounts, numbering."

    def handle(self, *args, **options):
        problems = ledger_health()
        if not problems:
            self.stdout.write(self.style.SUCCESS("The ledger is healthy: all checks passed."))
            return
        for problem in problems:
            self.stdout.write(self.style.ERROR("PROBLEM: " + problem))
        raise CommandError(f"{len(problems)} problem(s) found in the ledger.")
