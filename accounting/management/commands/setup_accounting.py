from datetime import date

from django.core.management.base import BaseCommand, CommandError

from accounting.chart import seed_chart
from accounting.models import AccountingSettings, FiscalPeriod


class Command(BaseCommand):
    help = (
        "Set up the accounting module: the starter chart of accounts (missing accounts only) "
        "and the monthly fiscal periods for one or more years."
    )

    def add_arguments(self, parser):
        parser.add_argument("--years", nargs="*", type=int, help="For example: --years 2026 2027")
        parser.add_argument("--no-chart", action="store_true", help="Do not create the starter chart.")
        parser.add_argument("--cutover", help="Cutover date, YYYY-MM-DD (only if none is set yet).")

    def handle(self, *args, **options):
        if not options["no_chart"]:
            created = seed_chart()
            self.stdout.write(f"Chart of accounts: {created} account(s) created.")
        years = options["years"] or [date.today().year]
        for year in years:
            try:
                made = FiscalPeriod.ensure_year(year)
            except Exception as exc:
                raise CommandError(str(exc))
            self.stdout.write(f"Fiscal periods {year}: {made} created.")
        if options["cutover"]:
            settings_ = AccountingSettings.load()
            if settings_.cutover_date:
                self.stdout.write(f"Cutover date already set ({settings_.cutover_date}); left unchanged.")
            else:
                try:
                    settings_.cutover_date = date.fromisoformat(options["cutover"])
                except ValueError:
                    raise CommandError("Use the format YYYY-MM-DD for the cutover date.")
                settings_.save()
                self.stdout.write(f"Cutover date set to {settings_.cutover_date}.")
        self.stdout.write(self.style.SUCCESS("Accounting setup done."))
