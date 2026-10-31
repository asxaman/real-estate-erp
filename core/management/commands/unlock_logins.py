from django.core.management.base import BaseCommand

from core.models import LoginAttempt


class Command(BaseCommand):
    help = "Remove every locked-out login (use it if you locked yourself out)."

    def handle(self, *args, **options):
        count, _ = LoginAttempt.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(f"Done. {count} failed login record(s) removed."))
