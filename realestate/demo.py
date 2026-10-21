"""Load and remove demo data, so you can play with the system safely.

Everything created here is listed in DemoRecord. Removing demo data deletes
only those records, never anything you entered yourself.
"""

import itertools
from datetime import timedelta
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from .models import (
    Cheque,
    ChequeEvent,
    DemoRecord,
    Expense,
    Lease,
    MaintenanceRequest,
    Owner,
    OwnerStatement,
    Property,
    StatementLine,
    Tenant,
    Unit,
    Vendor,
)
from .utils import add_months, make_iban

D = Decimal
BANKS = ["Emirates NBD", "Dubai Islamic Bank", "ADCB", "Mashreq Bank", "RAKBANK", "FAB"]


class DemoError(Exception):
    """Something the person should read, for example "already loaded"."""


def has_demo_data():
    return DemoRecord.objects.exists()


def demo_counts():
    """[(model name, how many)] for the demo page."""
    rows = []
    for ct in ContentType.objects.filter(
        pk__in=DemoRecord.objects.values_list("content_type", flat=True)
    ):
        model = ct.model_class()
        count = DemoRecord.objects.filter(content_type=ct).count()
        rows.append((model._meta.verbose_name_plural.capitalize(), count))
    return sorted(rows)


def _track(obj):
    DemoRecord.objects.get_or_create(
        content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk
    )
    return obj


def _make(model, **values):
    return _track(model.objects.create(**values))


def seed_demo(today=None):
    """Create a small, realistic set of records. Returns a short summary."""
    if has_demo_data():
        raise DemoError("Demo data is already loaded. Remove it first if you want a fresh copy.")
    today = today or timezone.localdate()
    with transaction.atomic():
        return _seed(today)


def _seed(today):
    numbers = itertools.count(100201)
    in_a_year = lambda start: start + timedelta(days=364)  # noqa: E731

    # --- owners ------------------------------------------------------------
    owner1 = _make(
        Owner,
        owner_type="individual",
        name="Ahmed Al Mansoori (demo)",
        emirates_id="784-1978-4521897-3",
        emirates_id_expiry=add_months(today, 20),
        nationality="United Arab Emirates",
        email="ahmed.demo@example.com",
        phone="+971 50 123 4567",
        address="Villa 12, Al Barsha 2, Dubai",
        bank_name="Emirates NBD",
        account_holder_name="Ahmed Al Mansoori",
        iban=make_iban("026", "0001234567890123"),
        management_fee_percent=D("10"),
        kyc_status="verified",
        notes="Demo data",
    )
    owner2 = _make(
        Owner,
        owner_type="company",
        name="Gulf Horizon Properties LLC (demo)",
        trade_licence_number="CN-1234567",
        trade_licence_expiry=add_months(today, 7),
        trn="100234567890003",
        email="finance.demo@example.com",
        phone="+971 4 555 0100",
        address="Office 805, Business Bay, Dubai",
        bank_name="Mashreq Bank",
        account_holder_name="Gulf Horizon Properties LLC",
        iban=make_iban("033", "0009876543210123"),
        management_fee_percent=D("8"),
        kyc_status="verified",
        notes="Demo data",
    )

    # --- vendors -----------------------------------------------------------
    cool = _make(Vendor, name="Cool Breeze AC Services LLC (demo)", trade="ac",
                 contact_person="Imran", phone="+971 55 234 5678", notes="Demo data")
    plumber = _make(Vendor, name="Al Noor Plumbing (demo)", trade="plumbing",
                    contact_person="Yusuf", phone="+971 52 345 6789", notes="Demo data")
    cleaner = _make(Vendor, name="Sparkle Cleaning Co (demo)", trade="cleaning",
                    phone="+971 56 456 7890", notes="Demo data")

    # --- properties and units ---------------------------------------------
    marina = _make(
        Property, name="Marina Heights Tower (demo)", property_type="building",
        emirate="dubai", community="Dubai Marina", address="Marina Walk, Dubai Marina",
        makani_number="3012345678", title_deed_number="DM-2015-0451", year_built=2015,
        ownership_type="third_party", owner=owner1, notes="Demo data",
    )
    jvc = _make(
        Property, name="JVC Gardens (demo)", property_type="building",
        emirate="dubai", community="Jumeirah Village Circle", address="District 12, JVC",
        makani_number="3098765432", title_deed_number="JV-2019-0877", year_built=2019,
        ownership_type="third_party", owner=owner2, management_fee_percent=D("8"),
        notes="Demo data",
    )
    barsha = _make(
        Property, name="Al Barsha Residence (demo)", property_type="mixed_use",
        emirate="dubai", community="Al Barsha 1", address="Sheikh Zayed Road, Al Barsha",
        makani_number="3055512345", ownership_type="company", notes="Demo data",
    )

    def unit(prop, number, kind="apartment", beds=1, rent=60000, **extra):
        return _make(
            Unit, property=prop, unit_number=number, unit_type=kind, bedrooms=beds,
            bathrooms=max(1, beds), size_sqft=D(str(600 + beds * 250)), rent_amount=D(rent),
            floor=extra.pop("floor", None), furnishing="unfurnished", **extra,
        )

    m101 = unit(marina, "101", beds=1, rent=85000, floor=1)
    m102 = unit(marina, "102", beds=2, rent=110000, floor=1)
    m201 = unit(marina, "201", beds=1, rent=72000, floor=2)
    unit(marina, "202", beds=2, rent=115000, floor=2)
    unit(marina, "301", beds=3, rent=160000, floor=3)
    unit(marina, "302", beds=1, rent=78000, floor=3, under_maintenance=True)
    j101 = unit(jvc, "101", "studio", 0, 42000, floor=1)
    j210 = unit(jvc, "210", beds=1, rent=55000, floor=2)
    j305 = unit(jvc, "305", beds=2, rent=75000, floor=3)
    unit(jvc, "401", beds=2, rent=78000, floor=4)
    b12 = unit(barsha, "B-12", "shop", 0, 120000, floor=0)
    unit(barsha, "B-14", "shop", 0, 135000, floor=0)

    # --- tenants -----------------------------------------------------------
    def person(name, eid, nationality, phone):
        return _make(
            Tenant, tenant_type="individual", name=f"{name} (demo)", emirates_id=eid,
            emirates_id_expiry=add_months(today, 14), nationality=nationality,
            residence_visa_expiry=add_months(today, 14), phone=phone,
            email=f"{name.split()[0].lower()}.demo@example.com", kyc_status="verified",
            notes="Demo data",
        )

    t1 = person("Rashid Al Suwaidi", "784-1988-7654321-1", "United Arab Emirates", "+971 50 700 0001")
    t2 = person("Priya Nair", "784-1991-1122334-5", "India", "+971 55 700 0002")
    t3 = person("Omar Haddad", "784-1985-2233445-6", "Jordan", "+971 52 700 0003")
    t4 = person("Elena Petrova", "784-1993-3344556-7", "Russia", "+971 56 700 0004")
    t5 = person("Sanjay Mehta", "784-1980-4455667-8", "India", "+971 50 700 0005")
    t6 = _make(
        Tenant, tenant_type="company", name="Bright Star Trading LLC (demo)",
        trade_licence_number="CN-7654321", trade_licence_expiry=add_months(today, 9),
        trn="100765432100003", authorised_signatory="Khalid Rahman",
        phone="+971 4 700 0006", email="info.brightstar.demo@example.com",
        kyc_status="verified", notes="Demo data",
    )
    t7 = person("Fatima Al Zaabi", "784-1990-5566778-9", "United Arab Emirates", "+971 54 700 0007")
    person("John Carter", "784-1987-6677889-0", "United Kingdom", "+971 58 700 0008")  # no lease yet

    # --- leases and cheques ------------------------------------------------
    def lease(tenant, unit_, start, rent, payments, states, number, vat=0, usage="residential"):
        item = _make(
            Lease, tenant=tenant, unit=unit_, contract_number=f"TC-{number}",
            ejari_number=f"EJ-{number}-{unit_.unit_number}", usage=usage, start_date=start,
            end_date=in_a_year(start), annual_rent=D(rent), vat_percent=D(vat),
            security_deposit=D(rent) * D("0.05"), number_of_payments=payments,
            status="active", notes="Demo data",
        )
        item.generate_cheque_schedule()
        for index, cheque in enumerate(item.cheques.order_by("cheque_date", "id")):
            _track(cheque)
            state = states[index] if index < len(states) else "pending"
            _apply_state(cheque, state, next(numbers), today)
        return item

    lease(t1, m101, add_months(today, -4), 85000, 4, ["cleared", "cleared", "received"], "2026-001")
    lease(t2, m102, add_months(today, -2), 110000, 4, ["returned", "received"], "2026-002")
    lease(t3, m201, add_months(today, -1), 72000, 12, ["cleared", "received"], "2026-003")
    lease(t4, j305, add_months(today + timedelta(days=1), -3), 75000, 4, ["cleared", "received"], "2026-004")
    lease(t5, j210, add_months(today, -8), 55000, 2, ["cleared", "received"], "2026-005")
    lease(t6, b12, add_months(today, -5), 120000, 4, ["cleared", "cleared", "received"], "2026-006",
          vat=5, usage="commercial")
    lease(t7, j101, add_months(today, -11), 42000, 1, ["cleared"], "2026-007")

    # --- maintenance and expenses -----------------------------------------
    this_month = today.replace(day=1)
    last_month_end = this_month - timedelta(days=1)
    last_month = last_month_end.replace(day=1)

    job_ac = _make(
        MaintenanceRequest, property=marina, unit=m101, tenant=t1,
        title="AC not cooling", category="ac", priority="urgent", status="open",
        description="Tenant reports the living room AC blows warm air.",
        reported_on=today - timedelta(days=2), vendor=cool, estimated_cost=D("900"),
        notes="Demo data",
    )
    job_tap = _make(
        MaintenanceRequest, property=jvc, unit=j305, tenant=t4,
        title="Leaking kitchen tap", category="plumbing", priority="normal",
        status="in_progress", reported_on=today - timedelta(days=6), vendor=plumber,
        notes="Demo data",
    )
    job_paint = _make(
        MaintenanceRequest, property=marina, unit=None,
        title="Repaint lobby", category="painting", priority="low", status="completed",
        reported_on=today - timedelta(days=20), vendor=None, notes="Demo data",
    )

    def expense(prop, desc, amount, on, bearer, category, vendor=None, unit_=None, vat=0, job=None):
        return _make(
            Expense, property=prop, unit=unit_, description=desc, amount=D(amount),
            vat_amount=D(vat), expense_date=on, bearer=bearer, category=category,
            vendor=vendor, maintenance_request=job, notes="Demo data",
        )

    expense(marina, "AC compressor repair", "1250.00", last_month + timedelta(days=14),
            "owner", "ac", cool, m101, vat="59.52", job=job_ac)
    expense(marina, "Common area deep cleaning", "800.00", last_month + timedelta(days=19),
            "owner", "cleaning", cleaner)
    expense(jvc, "Kitchen tap replacement", "480.00", last_month + timedelta(days=9),
            "owner", "plumbing", plumber, j305, job=job_tap)
    expense(barsha, "Pest control (annual contract)", "350.00", last_month + timedelta(days=4),
            "company", "pest")
    expense(marina, "Wall repair after tenant damage", "300.00", last_month + timedelta(days=11),
            "tenant", "repair", None, m102)
    expense(marina, "Lobby repainting", "2100.00", min(today, this_month + timedelta(days=2)),
            "owner", "painting", None, None, job=job_paint)

    # --- one draft owner statement for last month --------------------------
    statement = _make(
        OwnerStatement, owner=owner1, period_start=last_month, period_end=last_month_end,
        notes="Demo data: review it, then try Approve and Mark as paid.",
    )
    statement.recalculate()

    return {"records": DemoRecord.objects.count()}


def _apply_state(cheque, state, number, today):
    """Move a freshly created expected cheque into the wanted state."""
    S = Cheque.Status
    if state == "pending":
        return
    day = cheque.cheque_date
    # Keep clearing / return dates in the same month as the cheque, so a statement
    # for that month always finds them.
    month_end = (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    cheque.cheque_number = f"{number:06d}"
    cheque.bank_name = BANKS[number % len(BANKS)]
    cheque.received_date = min(day - timedelta(days=7), today)
    if state == "received":
        cheque.status = S.RECEIVED
    elif state == "deposited":
        cheque.status = S.DEPOSITED
        cheque.deposited_date = day
    elif state == "cleared":
        cheque.status = S.CLEARED
        cheque.deposited_date = day
        cheque.cleared_date = min(day + timedelta(days=2), month_end, today)
    elif state == "returned":
        cheque.status = S.RETURNED
        cheque.deposited_date = day
        cheque.returned_date = min(day + timedelta(days=3), month_end, today)
        cheque.return_reason = Cheque.ReturnReason.INSUFFICIENT_FUNDS
    cheque.save()
    ChequeEvent.objects.create(
        cheque=cheque, from_status="", to_status=cheque.status, note="Demo data"
    )


# Children first, so nothing is blocked by something that still points at it.
REMOVAL_ORDER = [
    OwnerStatement,
    Expense,
    MaintenanceRequest,
    Cheque,
    Lease,
    Tenant,
    Unit,
    Property,
    Owner,
    Vendor,
]


def remove_demo():
    """Delete everything that "Load demo data" created. Returns a summary."""
    removed = 0
    kept = []
    with transaction.atomic():
        for model in REMOVAL_ORDER:
            ct = ContentType.objects.get_for_model(model)
            ids = list(
                DemoRecord.objects.filter(content_type=ct).values_list("object_id", flat=True)
            )
            if not ids:
                continue
            if model is OwnerStatement:
                StatementLine.objects.filter(statement_id__in=ids).delete()
            for pk in ids:
                try:
                    with transaction.atomic():
                        model.objects.filter(pk=pk).delete()
                except ProtectedError:
                    pass
            still_there = set(model.objects.filter(pk__in=ids).values_list("pk", flat=True))
            removed += len(ids) - len(still_there)
            for obj in model.objects.filter(pk__in=still_there):
                kept.append(f"{model._meta.verbose_name.capitalize()}: {obj}")
            DemoRecord.objects.filter(content_type=ct).exclude(object_id__in=still_there).delete()
    return {"removed": removed, "kept": kept}
