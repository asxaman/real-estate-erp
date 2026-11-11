"""Sample owner agreements, the eraser, and the permissions of the roles."""
from collections import defaultdict
from datetime import date

from django.contrib.auth.models import Group, Permission
from django.db.models import Q
from django.utils import timezone

from core.data_registry import register_eraser, register_seeder
from properties.models import Property

from .models import AgreementProperty, OwnerAgreement

MODELS = ("owneragreement", "agreementproperty")
ACTIONS = ("add", "change", "delete", "view")

# role -> permission codenames
ROLE_PERMISSIONS = {
    "Property Manager": [f"{a}_{m}" for m in MODELS for a in ACTIONS] + ["export_owneragreement"],
    "Accountant": [f"view_{m}" for m in MODELS] + ["export_owneragreement"],
    "Leasing Agent": [f"view_{m}" for m in MODELS],
}


def grant_role_permissions():
    """Give the roles that already exist their agreement permissions (never creates a role)."""
    for role, codenames in ROLE_PERMISSIONS.items():
        group = Group.objects.filter(name=role).first()
        if group is None:
            continue
        group.permissions.add(*Permission.objects.filter(content_type__app_label="agreements", codename__in=codenames))


def make_agreements_from_property_fees(note):
    """
    One agreement per owner and fee, built from the management fee that is stored on the properties.
    Properties that an agreement already covers are skipped. Returns how many agreements were made.
    """
    F = Property.FeeType
    covered = set(AgreementProperty.objects.values_list("property_id", flat=True))
    groups = defaultdict(list)
    for prop in Property.objects.filter(owner__is_company=False).exclude(management_fee_type="").select_related("owner"):
        if prop.pk in covered:
            continue
        if prop.management_fee_type == F.PERCENT and prop.management_fee_percent:
            groups[(prop.owner_id, "percent", prop.management_fee_percent)].append(prop)
        elif prop.management_fee_type == F.FIXED and prop.management_fee_fixed:
            groups[(prop.owner_id, "fixed", prop.management_fee_fixed)].append(prop)
    year = timezone.localdate().year
    payouts = [OwnerAgreement.Payout.MONTHLY, OwnerAgreement.Payout.QUARTERLY, OwnerAgreement.Payout.AS_COLLECTED]
    created = 0
    for index, ((owner_id, kind, value), props) in enumerate(sorted(groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]))):
        agreement = OwnerAgreement(
            owner_id=owner_id, status=OwnerAgreement.Status.ACTIVE, start_date=date(year, 1, 1), end_date=date(year, 12, 31),
            auto_renew=True, notice_days=90, payout_frequency=payouts[index % len(payouts)], notes=note,
        )
        if kind == "percent":
            agreement.fee_percent_collected = value
        else:
            agreement.fee_fixed_yearly = value * len(props)
        agreement.save()
        AgreementProperty.objects.bulk_create([AgreementProperty(agreement=agreement, property=p) for p in props])
        created += 1
    return created


def seed_agreements() -> str:
    """Sample data: agreements for the sample owners (from the fees the sample properties carry)."""
    grant_role_permissions()
    created = make_agreements_from_property_fees("Sample data")
    return f"{OwnerAgreement.objects.count()} owner agreements ({created} new)"


MARKER = "Old management fees turned into owner agreements"


def convert_old_fees() -> int:
    """
    Once, when this module arrives on a system that already has properties with a management fee: turn those fees
    into agreements, so nothing entered earlier is lost. A note in the activity log marks it as done, so it never
    runs again (not even after the agreements are deleted on purpose).
    """
    from core.audit import log_event, suppressed
    from core.models import AuditEntry

    if AuditEntry.objects.filter(action="tool", object_repr=MARKER).exists():
        return 0
    has_fee = Q(management_fee_percent__gt=0) | Q(management_fee_fixed__gt=0)
    if not Property.objects.filter(has_fee, owner__is_company=False).exclude(management_fee_type="").exists():
        return 0  # nothing to convert (a new system)
    with suppressed():
        made = make_agreements_from_property_fees("Created from the management fee that was on the property. Please check it.")
    log_event(MARKER, f"{made} agreement(s) created", user=None)
    return made


def erase_agreements() -> str:
    count = OwnerAgreement.objects.count()
    OwnerAgreement.objects.all().delete()
    return f"removed {count} owner agreements"


# Seed after the properties (order 10); erase before them (reverse order), so properties and owners are free to delete.
register_seeder("Owner agreements", seed_agreements, order=15)
register_eraser("Owner agreements", erase_agreements, order=15)
