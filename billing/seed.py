"""Sample receipts and invoices, the eraser, and the permissions of the roles."""
from datetime import timedelta

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from core.data_registry import register_eraser, register_seeder
from leases.models import LeasePayment

from .models import NO_RECEIPT_KINDS, Invoice, Receipt, Status, issue_invoice, issue_receipt

MODELS = ("receipt", "invoice")
ACTIONS = ("add", "change", "delete", "view")

# role -> permission codenames
ROLE_PERMISSIONS = {
    "Property Manager": [f"{a}_{m}" for m in MODELS for a in ("add", "change", "view")] + ["export_receipt", "export_invoice"],
    "Accountant": [f"{a}_{m}" for m in MODELS for a in ("add", "change", "view")] + ["export_receipt", "export_invoice"],
    "Leasing Agent": [f"view_{m}" for m in MODELS] + ["add_receipt", "add_invoice"],
}


def grant_role_permissions():
    """Give the roles that already exist their receipt and invoice permissions (never creates a role)."""
    for role, codenames in ROLE_PERMISSIONS.items():
        group = Group.objects.filter(name=role).first()
        if group is None:
            continue
        group.permissions.add(*Permission.objects.filter(content_type__app_label="billing", codename__in=codenames))


def issue_missing_receipts(queryset=None, limit=None):
    """Receipts for paid payments that have none (payments saved in bulk do not get one by themselves). Returns how many."""
    payments = queryset if queryset is not None else LeasePayment.objects.all()
    payments = payments.filter(status=LeasePayment.Status.PAID).exclude(kind__in=NO_RECEIPT_KINDS).exclude(
        method=LeasePayment.Method.FROM_DEPOSIT).exclude(pk__in=Receipt.objects.filter(status=Status.ISSUED).values("payment_id"))
    payments = payments.select_related("lease__tenant", "lease__unit__property", "lease__property").order_by("paid_on", "id")
    made = 0
    for payment in payments[:limit] if limit else payments:
        if issue_receipt(payment) is not None:
            made += 1
    return made


def seed_billing() -> str:
    grant_role_permissions()
    made_receipts = issue_missing_receipts(limit=300)
    today = timezone.localdate()
    upcoming = LeasePayment.objects.filter(
        status=LeasePayment.Status.PENDING, due_date__lte=today + timedelta(days=45),
    ).exclude(kind__in=NO_RECEIPT_KINDS).select_related("lease__tenant", "lease__unit__property", "lease__property")[:50]
    made_invoices = sum(1 for payment in upcoming if issue_invoice(payment)[1])
    return (f"{Receipt.objects.count()} receipts ({made_receipts} new), {Invoice.objects.count()} invoices ({made_invoices} new)")


def erase_billing() -> str:
    receipts, invoices = Receipt.objects.count(), Invoice.objects.count()
    Receipt.objects.all().delete()
    Invoice.objects.all().delete()
    return f"removed {receipts} receipts and {invoices} invoices"


# Seed after the leases and their payments (order 30); erase before them (reverse order).
register_seeder("Receipts & invoices", seed_billing, order=50)
register_eraser("Receipts & invoices", erase_billing, order=50)
