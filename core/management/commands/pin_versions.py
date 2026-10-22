from importlib import metadata
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

SKIP = {"pip", "setuptools", "wheel"}


class Command(BaseCommand):
    help = "List the exact package versions installed now. With --write, save them to requirements.txt."

    def add_arguments(self, parser):
        parser.add_argument("--write", action="store_true", help="Overwrite requirements.txt with these versions.")

    def handle(self, *args, **options):
        found = {}
        for dist in metadata.distributions():
            name = dist.metadata["Name"]
            if name and name.lower() not in SKIP:
                found[name.lower()] = f"{name}=={dist.version}"
        lines = [found[key] for key in sorted(found)]
        text = "\n".join(lines) + "\n"
        if options["write"]:
            (Path(settings.BASE_DIR) / "requirements.txt").write_text(text, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"requirements.txt now pins {len(lines)} packages:"))
        self.stdout.write(text)
