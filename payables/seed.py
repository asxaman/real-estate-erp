"""Sample bills, expenses and vouchers, the eraser, and the permissions of the roles."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from core.data_registry import register_eraser, register_seeder
from maintenance.models import MaintenanceRequest, Vendor
from properties.models import Property

from .models import Expense, PaymentVoucher, VendorBill, create_bill_for_job

MODELS = ("vendorbill", "expense", "paymentvoucher")

# role -> permission codenames
ROLE_PERMISSIONS = {
    "Accountant": [f"{a}_{m}" for m in MODELS for a in ("add", "change", "view")]
    + ["export_vendorbill", "export_expense", "export_paymentvoucher"],
    "Property Manager": [f"{a}_{m}" for m in ("vendorbill", "expense") for a in ("add", "change", "view")]
    + ["view_paymentvoucher", "export_vendorbill", "export_expense", "export_paymentvoucher"],
    "Leasing Agent": [],
}


def grant_role_permissions():
    """Give the roles that already exist their permissions (never creates a role)."""
    for role, codenames in ROLE_PERMISSIONS.items():
        group = Group.objects.filter(name=role).first()
        if group is None or not codenames:
            continue
        group.permissions.add(*Permission.objects.filter(content_type__app_label="payables", codename__in=codenames))


def seed_payables() -> str:
    grant_role_permissions()
    today = timezone.localdate()
    made = 0
    # Bills for the finished sample jobs
    for job in MaintenanceRequest.objects.filter(status="completed", vendor__isnull=False, final_cost__gt=0):
        _, created = create_bill_for_job(job)
        made += created
    # A bill from a vendor that is still unpaid and overdue, one part-paid
    vendor = Vendor.objects.filter(is_active=True).order_by("name").first()
    prop = Property.objects.filter(owner__is_company=False).order_by("code").first()
    if vendor is not None and prop is not None and not VendorBill.objects.filter(description="Quarterly pest control").exists():
        bill = VendorBill(vendor=vendor, bill_number="INV-2291", bill_date=today - timedelta(days=45), due_date=today - timedelta(days=15),
                          category="cleaning", description="Quarterly pest control", property=prop, charged_to="owner", amount=Decimal("1800"),
                          vat_applies=True)
        bill.full_clean(exclude=["file"])
        bill.save()
        part = PaymentVoucher(bill=bill, amount=Decimal("500"), method="transfer", voucher_date=today - timedelta(days=10))
        part.full_clean()
        part.save()
        made += 1
    # Expenses
    if not Expense.objects.exists():
        samples = [
            ("DEWA", "Electricity, common areas", "utilities", "transfer", "", Decimal("2350"), False, -20, "owner"),
            ("Dubai Municipality", "Trade licence renewal", "government", "cheque", "004512", Decimal("1500"), False, -12, "company"),
            ("Emirates Insurance", "Building insurance premium", "insurance", "transfer", "", Decimal("4200"), True, -5, "owner"),
        ]
        for payee, description, category, method, ref, amount, vat, offset, charged in samples:
            expense = Expense(
                payee=payee, description=description, category=category, method=method, reference=ref, amount=amount, vat_applies=vat,
                expense_date=today + timedelta(days=offset), charged_to=charged if prop is not None else "company",
                property=prop if charged == "owner" else None,
            )
            expense.full_clean(exclude=["file"])
            expense.save()
            made += 1
    return f"{VendorBill.objects.count()} vendor bills, {Expense.objects.count()} expenses, {PaymentVoucher.objects.count()} payment vouchers ({made} new)"


def erase_payables() -> str:
    counts = (PaymentVoucher.objects.count(), VendorBill.objects.count(), Expense.objects.count())
    PaymentVoucher.objects.all().delete()
    VendorBill.objects.all().delete()
    Expense.objects.all().delete()
    return "removed {} vouchers, {} bills and {} expenses".format(*counts)


# Seed after maintenance (order 40); erase before it (reverse order).
register_seeder("Expenses, bills & vouchers", seed_payables, order=60)
register_eraser("Expenses, bills & vouchers", erase_payables, order=60)
