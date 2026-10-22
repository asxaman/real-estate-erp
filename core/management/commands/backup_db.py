import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _find_pg_dump():
    found = shutil.which("pg_dump")
    if found:
        return found
    # Default install location on Windows, newest version first.
    candidates = sorted(Path("C:/Program Files/PostgreSQL").glob("*/bin/pg_dump.exe"), reverse=True)
    return str(candidates[0]) if candidates else None


class Command(BaseCommand):
    help = "Save a backup copy of the database into the 'backups' folder."

    def add_arguments(self, parser):
        parser.add_argument("--folder", help="Where to put the backup (default: the 'backups' folder).")

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        folder = Path(options["folder"]) if options["folder"] else Path(settings.BASE_DIR) / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if "sqlite" in db["ENGINE"]:
            target = folder / f"db_{stamp}.sqlite3"
            shutil.copy2(db["NAME"], target)
        else:
            pg_dump = _find_pg_dump()
            if not pg_dump:
                raise CommandError(
                    "pg_dump was not found. Install the PostgreSQL 'Command Line Tools' "
                    "or add PostgreSQL's bin folder to PATH."
                )
            target = folder / f"{db['NAME']}_{stamp}.dump"
            command = [
                pg_dump, "-h", str(db["HOST"]), "-p", str(db["PORT"]), "-U", db["USER"],
                "-F", "c", "-f", str(target), db["NAME"],
            ]
            env = {**os.environ, "PGPASSWORD": db.get("PASSWORD", "")}
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            if result.returncode != 0:
                target.unlink(missing_ok=True)
                raise CommandError(f"Backup failed: {result.stderr.strip()}")

        size_kb = target.stat().st_size / 1024
        self.stdout.write(self.style.SUCCESS(f"Backup saved: {target}  ({size_kb:,.0f} KB)"))
        self.stdout.write("Copy this file somewhere safe (another disk, cloud drive) - a backup on the same PC is not enough.")
