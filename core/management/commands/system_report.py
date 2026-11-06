"""
One command that checks the whole system and writes a report you can send to your developer:

    python manage.py system_report

It reads your data but changes nothing. The report is saved as system-report.txt in the project folder.
"""
import io
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import Count, F, Q
from django.template import TemplateSyntaxError
from django.template.loader import get_template
from django.utils import timezone


class Report:
    def __init__(self):
        self.lines = []
        self.problems = 0

    def head(self, text):
        self.lines += ["", "=" * 70, text, "=" * 70]

    def ok(self, text):
        self.lines.append(f"  OK       {text}")

    def bad(self, text):
        self.problems += 1
        self.lines.append(f"  PROBLEM  {text}")

    def info(self, text):
        self.lines.append(f"  {text}")


def run_command(name, *args):
    out, err = io.StringIO(), io.StringIO()
    try:
        call_command(name, *args, stdout=out, stderr=err)
        return True, out.getvalue() + err.getvalue()
    except SystemExit as exc:
        return exc.code in (0, None), out.getvalue() + err.getvalue()
    except Exception as exc:  # the command itself complained
        return False, out.getvalue() + err.getvalue() + str(exc)


class Command(BaseCommand):
    help = "Checks code, templates, migrations and data, and writes system-report.txt."

    def handle(self, *args, **options):
        report = Report()
        report.info(f"System report, {datetime.now():%d %b %Y %H:%M}. Python {sys.version.split()[0]}.")
        sections = (
            self.django_checks, self.migrations, self.code_files, self.templates, self.static_files,
            self.leases_and_payments, self.occupancy, self.people_and_places, self.accounts,
        )
        for section in sections:
            try:
                section(report)
            except Exception:  # a broken check must not hide the others
                report.bad(f"{section.__name__} could not finish:\n" + traceback.format_exc(limit=3))
        report.head("SUMMARY")
        if report.problems:
            report.info(f"{report.problems} thing(s) need a look. Send this file (system-report.txt) to your developer.")
        else:
            report.info("Nothing found. Everything this report can check looks fine.")
        text = "\n".join(report.lines) + "\n"
        path = Path(settings.BASE_DIR) / "system-report.txt"
        path.write_text(text, encoding="utf-8")
        self.stdout.write(text)
        self.stdout.write(self.style.SUCCESS(f"\nSaved to {path}. Send me that file."))

    # ---- Django's own checks -----------------------------------------------------------------
    def django_checks(self, report):
        report.head("1. Django system checks")
        ok, text = run_command("check")
        (report.ok if ok else report.bad)("manage.py check" + ("" if ok else "\n" + text.strip()))
        ok, text = run_command("check", "--deploy")
        notes = [line for line in text.splitlines() if line.strip()]
        report.info("check --deploy (live server settings; warnings are normal on your own PC):")
        for line in notes[:20] or ["no warnings"]:
            report.info("    " + line.strip())

    def migrations(self, report):
        report.head("2. Database changes (migrations)")
        ok, text = run_command("makemigrations", "--check", "--dry-run")
        if ok:
            report.ok("no model change is missing a migration")
        else:
            report.bad("some model changes have no migration yet. Run: python manage.py makemigrations\n" + text.strip()[:600])
        ok, text = run_command("migrate", "--check")
        (report.ok if ok else report.bad)("all migrations are applied" if ok else "some migrations are not applied. Run: python manage.py migrate")

    # ---- code ---------------------------------------------------------------------------------------
    def code_files(self, report):
        report.head("3. Code files (typos and mistakes)")
        root = Path(settings.BASE_DIR)
        skip = {"venv", ".venv", "node_modules", "staticfiles", "media", "backups", "__pycache__", ".git"}
        files = [p for p in root.rglob("*.py") if not skip & set(p.relative_to(root).parts)]
        broken = []
        for path in files:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:
                broken.append(f"{path.relative_to(root)} line {exc.lineno}: {exc.msg}")
        (report.bad if broken else report.ok)(f"{len(files)} Python files read" + ("" if not broken else ": " + "; ".join(broken)))

        ruff = [sys.executable, "-m", "ruff"]
        probe = subprocess.run(ruff + ["--version"], capture_output=True, text=True, cwd=root)
        if probe.returncode != 0:
            report.info("ruff (the code checker) is not installed, so the deeper code check was skipped.")
            report.info("    To include it, run once:  pip install ruff   and then run this report again.")
        else:
            done = subprocess.run(
                ruff + ["check", "--isolated", "--select", "F,E9,B006,B015,B018,B023,B904", "--exclude", "venv,.venv,migrations", "."],
                capture_output=True, text=True, cwd=root,
            )
            if done.returncode == 0:
                report.ok("ruff: no unused or undefined names, no obvious logic slips")
            else:
                report.bad("ruff found the following:\n" + done.stdout.strip()[:4000])

        node = shutil.which("node")
        scripts = [p for p in root.glob("*/static/**/*.js")]
        if node:
            bad = []
            for path in scripts:
                result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
                if result.returncode:
                    bad.append(f"{path.relative_to(root)}: {result.stderr.strip().splitlines()[0] if result.stderr else 'error'}")
            (report.bad if bad else report.ok)(f"{len(scripts)} JavaScript files read" + ("" if not bad else ": " + "; ".join(bad)))
        else:
            report.info(f"{len(scripts)} JavaScript files found; Node.js is not installed, so they were not syntax-checked.")

    def templates(self, report):
        report.head("4. Page templates")
        root = Path(settings.BASE_DIR)
        problems, count = [], 0
        for path in root.glob("*/templates/**/*.html"):
            name = "/".join(path.parts[path.parts.index("templates") + 1:])
            count += 1
            try:
                get_template(name)
            except TemplateSyntaxError as exc:
                problems.append(f"{name}: {exc}")
            except Exception as exc:
                problems.append(f"{name}: {type(exc).__name__}: {exc}")
        (report.bad if problems else report.ok)(f"{count} templates compile" + ("" if not problems else ":\n    " + "\n    ".join(problems)))

    def static_files(self, report):
        report.head("5. Style and script files the pages ask for")
        from django.contrib.staticfiles import finders

        missing = []
        unfold = getattr(settings, "UNFOLD", {})
        for key in ("STYLES", "SCRIPTS"):
            for item in unfold.get(key, []):
                try:
                    url = item(None) if callable(item) else item
                except Exception:
                    continue
                path = str(url).split("?")[0].replace(settings.STATIC_URL, "", 1)
                if not finders.find(path):
                    missing.append(path)
        (report.bad if missing else report.ok)("every file in the page header exists" if not missing else "missing: " + ", ".join(missing))

    # ---- data ---------------------------------------------------------------------------------------
    def leases_and_payments(self, report):
        report.head("6. Leases and payments (does the data make sense?)")
        from leases.models import CHARGE_FIELDS, Lease, LeaseDeduction, LeasePayment

        today = timezone.localdate()
        report.info(f"{Lease.objects.count()} leases, {LeasePayment.objects.count()} payment lines.")

        def check(title, rows):
            rows = list(rows)
            if rows:
                report.bad(f"{title} ({len(rows)}): " + ", ".join(str(r) for r in rows[:12]) + (" ..." if len(rows) > 12 else ""))
            else:
                report.ok(title + ": none")

        check("Leases ending on or before they start", Lease.objects.filter(end_date__lte=F("start_date")).values_list("code", flat=True))
        check("Leases with no rent", Lease.objects.filter(annual_rent__lte=0).values_list("code", flat=True))
        check("Payment lines with no amount", LeasePayment.objects.filter(amount__lte=0).values_list("lease__code", "kind", "part"))
        check("Leases whose property is not the unit's property",
              Lease.objects.filter(unit__isnull=False).exclude(property=F("unit__property")).values_list("code", flat=True))

        # Rent lines must add up to the annual rent.
        off = []
        for lease in Lease.objects.annotate(n=Count("payments", filter=Q(payments__kind="rent"))).filter(n__gt=0).prefetch_related("payments").iterator(chunk_size=500):
            rent = sum(p.amount for p in lease.payments.all() if p.kind == "rent")
            if lease.status == Lease.Status.ACTIVE and rent != lease.annual_rent:
                off.append(f"{lease.code} (lines {rent}, rent {lease.annual_rent})")
        check("Active leases whose rent lines do not add up to the annual rent", off)
        check("Leases with no payment lines at all", Lease.objects.filter(payments__isnull=True).values_list("code", flat=True))

        # Deposit and fees: a line for each amount, and equal.
        mismatch = []
        for kind, field in CHARGE_FIELDS:
            lines = {p.lease_id: p for p in LeasePayment.objects.filter(kind=kind)}
            for lease in Lease.objects.all().iterator():
                amount = getattr(lease, field) or 0
                line = lines.get(lease.pk)
                if amount > 0 and line is None:
                    mismatch.append(f"{lease.code} has {field} {amount} but no {kind} line")
                elif line is not None and line.amount != amount:
                    mismatch.append(f"{lease.code} {kind} line {line.amount} differs from {field} {amount}")
        check("Deposit / fee amounts on the lease that do not match their payment line", mismatch)

        check("Paid payments with no paid-on date", LeasePayment.objects.filter(status="paid", paid_on__isnull=True).values_list("lease__code", "kind", "part"))
        check("Held cheques that are not cheques", LeasePayment.objects.filter(status="held").exclude(method="cheque").values_list("lease__code", "kind", "part"))
        check("Pending payments that have a paid-on date", LeasePayment.objects.filter(status="pending", paid_on__isnull=False).values_list("lease__code", "kind", "part"))
        check("Deductions with no payment line", LeaseDeduction.objects.filter(payment__isnull=True).values_list("lease__code", "reason"))
        check("Deposits kept (deductions) above the deposit",
              [f"{l.code}" for l in Lease.objects.all().prefetch_related("deductions") if l.deposit_deducted() > (l.security_deposit or 0)])
        check("Terminated leases without a termination date", Lease.objects.filter(status="terminated", terminated_on__isnull=True).values_list("code", flat=True))
        check("Lease codes used twice", Lease.objects.values("code").annotate(n=Count("pk")).filter(n__gt=1).values_list("code", flat=True))

        # Overlaps (same unit or property, active leases).
        overlaps = []
        by_place = {}
        for lease in Lease.objects.filter(status="active").only("code", "unit_id", "property_id", "start_date", "end_date"):
            by_place.setdefault(("u", lease.unit_id) if lease.unit_id else ("p", lease.property_id), []).append(lease)
        for place, items in by_place.items():
            items.sort(key=lambda l: l.start_date)
            for first, second in zip(items, items[1:], strict=False):
                if second.start_date <= first.end_date:
                    overlaps.append(f"{first.code} / {second.code}")
        check("Active leases that overlap on the same unit or property", overlaps)
        report.info(f"(checked on {today:%d %b %Y})")

    def occupancy(self, report):
        report.head("7. Unit and property status versus leases")
        from leases.models import Lease
        from properties.models import Property, Unit

        today = timezone.localdate()
        running = Q(status="active", start_date__lte=today, end_date__gte=today) | Q(status="terminated", start_date__lte=today, terminated_on__gte=today)
        busy_units = set(Lease.objects.filter(running, unit__isnull=False).values_list("unit_id", flat=True))
        busy_props = set(Lease.objects.filter(running, unit__isnull=True).values_list("property_id", flat=True))
        wrong = [u.code if hasattr(u, "code") else str(u) for u in Unit.objects.filter(status="vacant", pk__in=busy_units)[:12]]
        (report.bad if wrong else report.ok)("Vacant units with a running lease: " + (", ".join(wrong) if wrong else "none"))
        stale = [str(u) for u in Unit.objects.filter(status="occupied").exclude(pk__in=busy_units)[:12]]
        (report.bad if stale else report.ok)("Occupied units with no running lease: " + (", ".join(stale) if stale else "none"))
        wrong = [p.code for p in Property.objects.filter(status="vacant", pk__in=busy_props)[:12]]
        (report.bad if wrong else report.ok)("Vacant standalone properties with a running lease: " + (", ".join(wrong) if wrong else "none"))
        report.info("(if any of these appear, sign out and in again; the daily refresh fixes them, or run: python manage.py refresh_leases)")

    def people_and_places(self, report):
        report.head("8. Owners, properties, units and tenants")
        from properties.models import Owner, Property, Unit
        from tenants.models import Tenant

        report.info(f"{Owner.objects.count()} owners, {Property.objects.count()} properties, {Unit.objects.count()} units, {Tenant.objects.count()} tenants.")
        dup = Unit.objects.values("property", "unit_number").annotate(n=Count("pk")).filter(n__gt=1)
        (report.bad if dup else report.ok)(f"The same unit number twice in one building: {dup.count()}")
        no_owner = Property.objects.filter(owner__isnull=True).count()
        (report.bad if no_owner else report.ok)(f"Properties with no owner: {no_owner}")
        codes = Owner.objects.exclude(code__isnull=True).values("code").annotate(n=Count("pk")).filter(n__gt=1)
        (report.bad if codes else report.ok)(f"Owner codes used twice: {codes.count()}")
        blocked = Tenant.objects.filter(is_blacklisted=True, leases__status="active", leases__end_date__gte=timezone.localdate()).distinct().count()
        report.info(f"Blacklisted tenants who still have a running lease: {blocked} (informational)")
        no_phone = Tenant.objects.filter(Q(phone="") | Q(email="")).count()
        (report.bad if no_phone else report.ok)(f"Tenants missing a phone or email (both are required): {no_phone}")

    def accounts(self, report):
        report.head("9. Users and settings")
        from django.contrib.auth import get_user_model

        from core import sysconf

        User = get_user_model()
        supers = User.objects.filter(is_superuser=True, is_active=True).count()
        (report.ok if supers else report.bad)(f"Active superusers: {supers}")
        report.info(f"Active users: {User.objects.filter(is_active=True).count()}, inactive: {User.objects.filter(is_active=False).count()}.")
        report.info(f"Development mode is {'ON' if sysconf.development_mode() else 'off'} (switch it off before real data goes in).")
        report.info(f"DEBUG is {'ON (normal on your own PC)' if settings.DEBUG else 'off'}.")
        models = len([m for m in apps.get_models()])
        report.info(f"{models} database tables are known to the system.")
