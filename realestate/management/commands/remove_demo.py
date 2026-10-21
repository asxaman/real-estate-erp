from django.core.management.base import BaseCommand

from realestate.demo import remove_demo


class Command(BaseCommand):
    help = "Delete the demo data (and only the demo data)."

    def handle(self, *args, **options):
        result = remove_demo()
        self.stdout.write(self.style.SUCCESS(f"Removed {result['removed']} demo record(s)."))
        for line in result["kept"]:
            self.stdout.write(self.style.WARNING(f"Kept (real data uses it): {line}"))
