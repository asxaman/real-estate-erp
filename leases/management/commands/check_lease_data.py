"""Run this BEFORE `python manage.py migrate` when you add the database safety rules (Update 26 B)."""
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count, F

from leases.models import Lease, LeasePayment


class Command(BaseCommand):
    help = "Looks for existing lease data that the new database rules would refuse, and says what to fix."

    def handle(self, *args, **options):
        problems = []
        for code in Lease.objects.filter(end_date__lte=F("start_date")).values_list("code", flat=True):
            problems.append(f"Lease {code}: the end date is not after the start date.")
        for code in Lease.objects.filter(annual_rent__lte=0).values_list("code", flat=True):
            problems.append(f"Lease {code}: the annual rent is zero or less.")
        for lease_id, count in LeasePayment.objects.filter(amount__lte=0).values_list("lease", "pk"):
            problems.append(f"Payment {count} (lease id {lease_id}): the amount is zero or less.")
        # Before the update the database has no payment "type" column yet: then the number alone must be unique.
        with connection.cursor() as cursor:
            columns = {c.name for c in connection.introspection.get_table_description(cursor, LeasePayment._meta.db_table)}
        group = ("lease", "kind", "part") if "kind" in columns else ("lease", "part")
        double = LeasePayment.objects.values(*group).annotate(n=Count("pk")).filter(n__gt=1)
        for row in double:
            code = Lease.objects.filter(pk=row["lease"]).values_list("code", flat=True).first()
            problems.append(f"Lease {code}: payment number {row['part']} appears {row['n']} times.")
        if not problems:
            self.stdout.write(self.style.SUCCESS("All good: your lease data follows the new rules. You can run migrate."))
            return
        self.stdout.write(self.style.WARNING("Fix these in the system first, then run migrate:"))
        for line in problems[:100]:
            self.stdout.write("  - " + line)
        if len(problems) > 100:
            self.stdout.write(f"  ... and {len(problems) - 100} more.")
