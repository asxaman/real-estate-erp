import os
import shutil
import subprocess
import zipfile
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
    help = "Save a backup copy of the database and the uploaded files into the 'backups' folder."

    def add_arguments(self, parser):
        parser.add_argument("--folder", help="Where to put the backup (default: the 'backups' folder).")
        parser.add_argument("--no-media", action="store_true", help="Back up the database only (skip the uploaded files).")

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        folder = Path(options["folder"]) if options["folder"] else Path(settings.BASE_DIR) / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

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
        if not options["no_media"]:
            media = Path(settings.MEDIA_ROOT)
            files = [f for f in media.rglob("*") if f.is_file()] if media.exists() else []
            if files:
                archive = folder / f"media_{stamp}.zip"
                with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
                    for f in files:
                        zipped.write(f, f.relative_to(media).as_posix())
                self.stdout.write(self.style.SUCCESS(
                    f"Uploaded files saved: {archive}  ({len(files)} file(s), {archive.stat().st_size / 1024:,.0f} KB)"
                ))
            else:
                self.stdout.write("No uploaded files to back up yet.")
        self.stdout.write("Copy these files somewhere safe (another disk, cloud drive) - a backup on the same PC is not enough. Keep the .dump and the media .zip of the same date together.")
