"""Checks for the workflow fixes: lease lengths, payments, ending a lease,
deposits, locking, statements, privacy and permissions."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.urls import reverse

from . import utils
from .dashboard import dashboard_context
from .models import (
    Cheque,
    CompanySettings,
    Expense,
    Lease,
    Owner,
    OwnerStatement,
    PropertyOwnership,
    SystemState,
    Unit,
)
from .roles import ROLES, ensure_roles
from .tests import BaseCase
from .views import _allowed

D = Decimal
S = Cheque.Status


def user_with_role(name, *roles):
    user = get_user_model().objects.create_user(
        name, password="a-long-test-password-1", is_staff=True
    )
    for role in roles:
        user.groups.add(Group.objects.get(name=role))
    return user


class LeaseLengthTests(BaseCase):
    def test_six_month_lease(self):
        lease = self.make_lease(
            end_date=utils.add_months(self.today, 6) - timedelta(days=1),
            number_of_payments=1,
        )
        self.assertEqual(lease.term_months, 6)
        self.assertEqual(lease.contract_total, D("20000.00"))
        self.assertEqual(lease.generate_cheque_schedule(), 1)
        cheque = lease.cheques.get()
        self.assertEqual(cheque.amount, D("20000.00"))
        self.assertEqual(cheque.cheque_date, lease.start_date)

    def test_eighteen_month_lease(self):
        lease = self.make_lease(
            end_date=utils.add_months(self.today, 18) - timedelta(days=1),
            number_of_payments=2,
        )
        self.assertEqual(lease.term_months, 18)
        self.assertEqual(lease.contract_total, D("60000.00"))
        self.assertEqual(lease.generate_cheque_schedule(), 3)
        cheques = list(lease.cheques.order_by("cheque_date"))
        self.assertEqual(sum(c.amount for c in cheques), D("60000.00"))
        self.assertTrue(all(lease.start_date <= c.cheque_date <= lease.end_date for c in cheques))

    def test_five_monthly_payments(self):
        lease = self.make_lease(
            end_date=utils.add_months(self.today, 5) - timedelta(days=1),
            number_of_payments=12,
        )
        self.assertEqual(lease.generate_cheque_schedule(), 5)
        self.assertEqual(sum(c.amount for c in lease.cheques.all()), lease.contract_total)

    def test_rebuild_after_the_rent_changes(self):
        lease = self.make_lease()
        lease.generate_cheque_schedule()
        first = lease.cheques.order_by("cheque_date").first()
        Cheque.objects.filter(pk=first.pk).update(
            status=S.CLEARED, cleared_date=self.today, cheque_number="1", bank_name="NBD"
        )
        lease.annual_rent = D("48000")
        lease.save()
        self.assertTrue(lease.schedule_gap() != 0)
        removed, created = lease.rebuild_schedule()
        self.assertEqual((removed, created), (3, 3))
        self.assertEqual(lease.schedule_gap(), D("0.00"))

    def test_renewal_has_the_same_length(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        lease = self.make_lease(
            end_date=utils.add_months(self.today, 6) - timedelta(days=1),
            number_of_payments=1,
        )
        admin_obj = site._registry[Lease]
        request = RequestFactory().post("/")
        request.user = get_user_model().objects.create_superuser("s", "s@e.com", "x" * 12)
        request._messages = type("M", (), {"add": lambda *a, **k: None})()
        admin_obj.create_renewal(request, Lease.objects.filter(pk=lease.pk))
        renewal = Lease.objects.get(renewal_of=lease)
        self.assertEqual(renewal.term_months, 6)
        self.assertEqual(renewal.start_date, lease.end_date + timedelta(days=1))


class EndingALeaseTests(BaseCase):
    def started_lease(self):
        return self.make_lease(
            start_date=self.today - timedelta(days=10),
            end_date=self.today - timedelta(days=10) + timedelta(days=364),
        )

    def test_early_termination_cancels_what_is_not_due(self):
        lease = self.make_lease()
        lease.generate_cheque_schedule()
        exit_day = lease.start_date + timedelta(days=100)
        summary = lease.terminate(
            exit_date=exit_day,
            notice_given_on=lease.start_date,
            notice_days=90,
            penalty=D("5000"),
        )
        lease.refresh_from_db()
        self.assertEqual(lease.status, Lease.Status.TERMINATED)
        self.assertEqual(lease.exit_date, exit_day)
        self.assertEqual(summary["cancelled"], 2)
        self.assertEqual(summary["prorated"], 1)
        self.assertTrue(summary["penalty_payment"])
        self.assertEqual(lease.cheques.filter(status=S.CANCELLED).count(), 2)
        straddling = lease.cheques.filter(status=S.PENDING, purpose="rent").order_by("cheque_date")[1]
        self.assertTrue(D("0") < straddling.amount < D("10000"))
        penalty = lease.cheques.get(purpose=Cheque.Purpose.PENALTY)
        self.assertEqual(penalty.amount, D("5000"))
        self.assertEqual(penalty.status, S.PENDING)

    def test_notice_period_is_enforced(self):
        lease = self.make_lease()
        with self.assertRaises(ValidationError):
            lease.terminate(
                exit_date=lease.start_date + timedelta(days=30),
                notice_given_on=lease.start_date,
                notice_days=90,
            )
        with self.assertRaises(ValidationError):
            lease.terminate(exit_date=lease.start_date + timedelta(days=200))  # no notice date
        lease.terminate(
            exit_date=lease.start_date + timedelta(days=30), by_mutual_agreement=True
        )
        self.assertEqual(lease.status, Lease.Status.TERMINATED)

    def test_exit_must_be_before_the_end(self):
        lease = self.make_lease()
        with self.assertRaises(ValidationError):
            lease.terminate(exit_date=lease.end_date, by_mutual_agreement=True)

    def test_uncashed_cheques_after_exit_go_back_to_the_tenant(self):
        lease = self.make_lease()
        late = self.make_cheque(lease, cheque_date=lease.start_date + timedelta(days=250))
        deposited = self.make_cheque(
            lease, number="2", cheque_date=lease.start_date + timedelta(days=260),
            status=S.DEPOSITED,
        )
        summary = lease.terminate(
            exit_date=lease.start_date + timedelta(days=60), by_mutual_agreement=True
        )
        late.refresh_from_db()
        deposited.refresh_from_db()
        self.assertEqual(late.status, S.CANCELLED)
        self.assertEqual(summary["returned_to_tenant"], 1)
        self.assertEqual(deposited.status, S.DEPOSITED)  # at the bank: flagged, not touched
        self.assertEqual(summary["needs_review"], 1)

    def test_unit_stays_occupied_until_the_exit_date(self):
        lease = self.started_lease()
        lease.terminate(exit_date=self.today + timedelta(days=30), by_mutual_agreement=True)
        self.assertEqual(self.unit.get_status(), Unit.Status.OCCUPIED)
        statuses = Unit.objects.with_status().values_list("status_calc", flat=True)
        self.assertEqual(list(statuses), ["occupied"])

    def test_unit_is_free_after_the_exit_date_and_can_be_relet(self):
        lease = self.started_lease()
        lease.terminate(exit_date=self.today - timedelta(days=1), by_mutual_agreement=True)
        self.assertEqual(self.unit.get_status(), Unit.Status.VACANT)
        new = Lease(
            tenant=self.tenant, unit=self.unit, start_date=self.today,
            end_date=self.today + timedelta(days=364), annual_rent=D("1000"),
        )
        new.full_clean()

    def test_expiry_flow_and_holdover(self):
        lease = self.make_lease(
            start_date=self.today - timedelta(days=400),
            end_date=self.today - timedelta(days=35),
        )
        late = Cheque.objects.create(
            lease=lease, amount=D("100"), cheque_date=lease.end_date + timedelta(days=10),
            status=S.PENDING,
        )
        unpaid = Cheque.objects.create(
            lease=lease, amount=D("100"), cheque_date=lease.end_date - timedelta(days=5),
            status=S.PENDING,
        )
        self.assertEqual(self.unit.get_status(), Unit.Status.HOLDOVER)
        self.assertEqual(Unit.objects.with_status().get().status_calc, "holdover")
        self.assertEqual(lease.mark_expired(), 1)  # one payment still unpaid
        late.refresh_from_db()
        unpaid.refresh_from_db()
        self.assertEqual(late.status, S.CANCELLED)
        self.assertEqual(unpaid.status, S.PENDING)
        self.assertEqual(lease.status, Lease.Status.EXPIRED)
        self.assertEqual(self.unit.get_status(), Unit.Status.VACANT)

    def test_a_running_lease_cannot_be_marked_expired(self):
        lease = self.make_lease()
        with self.assertRaises(ValidationError):
            lease.mark_expired()


class DepositTests(BaseCase):
    def ended_lease(self, penalty=0):
        lease = self.make_lease(
            start_date=self.today - timedelta(days=200),
            end_date=self.today + timedelta(days=165),
        )
        Cheque.objects.create(
            lease=lease, purpose=Cheque.Purpose.SECURITY_DEPOSIT, amount=D("3000"),
            cheque_date=lease.start_date, status=S.CLEARED, cleared_date=lease.start_date,
        )
        lease.terminate(
            exit_date=self.today - timedelta(days=2),
            by_mutual_agreement=True,
            penalty=D(penalty),
        )
        return lease

    def test_refund_needs_the_handover(self):
        lease = self.ended_lease()
        self.assertEqual(lease.deposit_received(), D("3000.00"))
        self.assertTrue(lease.deposit_refund_problems())
        with self.assertRaises(ValidationError):
            lease.refund_deposit()
        lease.handover_date = self.today
        lease.save()
        self.assertEqual(lease.refund_deposit(), D("3000.00"))
        self.assertEqual(lease.deposit_refunded_on, self.today)
        self.assertTrue(lease.deposit_refund_problems())  # not twice

    def test_deductions_reduce_the_refund(self):
        lease = self.ended_lease()
        lease.handover_date = self.today
        lease.deposit_deductions = D("500")
        lease.save()
        self.assertEqual(lease.deposit_refundable(), D("2500.00"))

    def test_the_penalty_is_not_taken_from_the_deposit(self):
        lease = self.ended_lease(penalty="2000")
        lease.handover_date = self.today
        lease.save()
        problems = " ".join(lease.deposit_refund_problems())
        self.assertIn("penalty", problems)
        self.assertEqual(lease.deposit_refundable(), D("3000.00"))  # untouched
        Cheque.objects.filter(purpose=Cheque.Purpose.PENALTY).update(
            status=S.CLEARED, cleared_date=self.today
        )
        self.assertEqual(lease.deposit_refund_problems(), [])

    def test_a_carried_over_deposit_is_not_counted_twice(self):
        old = self.make_lease(
            start_date=self.today - timedelta(days=365),
            end_date=self.today - timedelta(days=1),
        )
        Cheque.objects.create(
            lease=old, purpose=Cheque.Purpose.SECURITY_DEPOSIT, amount=D("3000"),
            cheque_date=old.start_date, status=S.CLEARED, cleared_date=old.start_date,
        )
        new = self.make_lease(
            start_date=self.today, renewal_of=old, deposit_carried_over=True,
            security_deposit=D("3000"),
        )
        self.assertEqual(new.deposit_received(), D("3000.00"))
        old.status = Lease.Status.EXPIRED
        old.handover_date = self.today
        old.save()
        self.assertIn("carried over", " ".join(old.deposit_refund_problems()))


class PaymentMethodTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.lease = self.make_lease()

    def payment(self, method, **extra):
        values = dict(
            lease=self.lease, method=method, amount=D("1000"),
            cheque_date=self.today, status=S.PENDING,
        )
        values.update(extra)
        return Cheque(**values)

    def test_cash_needs_no_number_or_bank(self):
        self.payment("cash").full_clean()
        self.payment("cash", status=S.CLEARED).full_clean()

    def test_transfer_needs_a_reference_when_paid(self):
        with self.assertRaises(ValidationError) as caught:
            self.payment("transfer", status=S.CLEARED).full_clean()
        self.assertIn("reference", caught.exception.message_dict)
        self.payment("transfer", status=S.CLEARED, reference="TRX-9").full_clean()

    def test_other_methods_cannot_be_in_hand_or_deposited(self):
        with self.assertRaises(ValidationError) as caught:
            self.payment("cash", status=S.RECEIVED).full_clean()
        self.assertIn("status", caught.exception.message_dict)

    def test_mark_a_transfer_as_paid(self):
        payment = self.payment("transfer")
        payment.save()
        with self.assertRaises(ValidationError):
            payment.mark_cleared()  # no reference
        payment.mark_cleared(reference="TRX-1")
        payment.refresh_from_db()
        self.assertEqual(payment.status, S.CLEARED)
        self.assertEqual(payment.reference, "TRX-1")
        self.assertEqual(payment.cleared_date, self.today)

    def test_cash_cannot_be_deposited_or_bounced(self):
        payment = self.payment("cash")
        payment.save()
        with self.assertRaises(ValidationError):
            payment.mark_deposited()
        with self.assertRaises(ValidationError):
            payment.mark_returned("other")

    def test_cash_rent_reaches_the_owner_statement(self):
        payment = self.payment("cash", purpose="rent", amount=D("10000"))
        payment.save()
        payment.mark_cleared()
        statement = OwnerStatement.objects.create(
            owner=self.owner, period_start=self.today, period_end=self.today
        )
        statement.recalculate()
        self.assertEqual(statement.totals()["rent"], D("10000.00"))
        self.assertIn("cash", statement.lines.filter(kind="rent").get().description)

    def test_penalty_reaches_the_statement_without_a_fee(self):
        payment = self.payment("cash", purpose=Cheque.Purpose.PENALTY, amount=D("2000"))
        payment.save()
        payment.mark_cleared()
        statement = OwnerStatement.objects.create(
            owner=self.owner, period_start=self.today, period_end=self.today
        )
        statement.recalculate()
        totals = statement.totals()
        self.assertEqual(totals["rent"], D("2000.00"))
        self.assertEqual(totals["fee"], D("0.00"))


class BounceTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.lease = self.make_lease()

    def test_bounce_fee_and_attempts(self):
        settings_ = CompanySettings.load()
        settings_.bounce_fee = D("500")
        settings_.save()
        cheque = self.make_cheque(self.lease, status=S.DEPOSITED)
        cheque.mark_returned("insufficient_funds")
        fee = self.lease.cheques.get(purpose=Cheque.Purpose.BOUNCE_FEE)
        self.assertEqual(fee.amount, D("500.00"))
        self.assertEqual(fee.status, S.PENDING)
        first_replacement = cheque.create_replacement()
        self.assertEqual(cheque.attempt_number, 1)
        self.assertEqual(first_replacement.attempt_number, 2)

    def test_no_fee_when_none_is_set(self):
        cheque = self.make_cheque(self.lease, status=S.DEPOSITED)
        cheque.mark_returned("signature")
        self.assertFalse(self.lease.cheques.filter(purpose=Cheque.Purpose.BOUNCE_FEE).exists())


class LockingTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.lease = self.make_lease()
        self.cheque = self.make_cheque(
            self.lease, status=S.CLEARED, cleared_date=self.today
        )
        self.statement = OwnerStatement.objects.create(
            owner=self.owner, period_start=self.today, period_end=self.today
        )
        self.statement.recalculate()

    def test_a_draft_statement_does_not_lock(self):
        self.cheque.refresh_from_db()
        self.assertFalse(self.cheque.is_locked)

    def test_an_approved_statement_locks_the_payment(self):
        self.statement.approve()
        self.cheque.refresh_from_db()
        self.assertTrue(self.cheque.is_locked)
        self.cheque.amount = D("1")
        with self.assertRaises(ValidationError):
            self.cheque.full_clean()
        with self.assertRaises(ValidationError):
            self.cheque.save()
        self.cheque.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.cheque.cancel()
        self.statement.reopen()
        self.cheque.refresh_from_db()
        self.assertFalse(self.cheque.is_locked)

    def test_received_payments_are_cancelled_not_deleted(self):
        with self.assertRaises(ValidationError):
            self.cheque.delete()
        # On a statement the database itself refuses (ProtectedError); otherwise our rule does.
        with self.assertRaises((ValidationError, ProtectedError)):
            with transaction.atomic():
                Cheque.objects.filter(pk=self.cheque.pk).delete()
        self.assertTrue(Cheque.objects.filter(pk=self.cheque.pk).exists())

    def test_a_never_received_payment_can_be_deleted(self):
        expected = Cheque.objects.create(
            lease=self.lease, amount=D("10"), cheque_date=self.today, status=S.PENDING
        )
        expected.delete()
        self.assertFalse(Cheque.objects.filter(pk=expected.pk).exists())

    def test_cancel_keeps_the_history(self):
        expected = Cheque.objects.create(
            lease=self.lease, amount=D("10"), cheque_date=self.today, status=S.PENDING
        )
        expected.cancel()
        self.assertEqual(expected.status, S.CANCELLED)
        self.assertEqual(expected.events.count(), 1)

    def test_rent_dates_are_checked_against_the_lease(self):
        late = Cheque(
            lease=self.lease, amount=D("10"), status=S.PENDING,
            cheque_date=self.lease.end_date + timedelta(days=30),
        )
        with self.assertRaises(ValidationError) as caught:
            late.full_clean()
        self.assertIn("cheque_date", caught.exception.message_dict)


class StatementTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.lease = self.make_lease()
        self.cheque = self.make_cheque(self.lease, status=S.CLEARED, cleared_date=self.today)
        self.expense = Expense.objects.create(
            property=self.property, description="Fix AC", amount=D("300"),
            expense_date=self.today, bearer=Expense.Bearer.OWNER,
        )
        self.statement = OwnerStatement.objects.create(
            owner=self.owner, period_start=self.today, period_end=self.today
        )
        self.statement.recalculate()

    def test_a_fresh_statement_is_up_to_date(self):
        self.assertFalse(self.statement.is_stale())

    def test_editing_an_expense_makes_it_stale(self):
        self.expense.amount = D("350")
        self.expense.save()
        self.assertTrue(self.statement.is_stale())
        with self.assertRaises(ValidationError):
            self.statement.approve()
        self.statement.recalculate()
        self.assertFalse(self.statement.is_stale())
        self.statement.approve()
        self.assertFalse(self.statement.is_stale())  # frozen statements are never stale

    def test_a_changed_cheque_makes_it_stale(self):
        Cheque.objects.filter(pk=self.cheque.pk).update(amount=D("9000"))
        self.assertTrue(self.statement.is_stale())

    def test_the_default_fee_vat_comes_from_company_settings(self):
        settings_ = CompanySettings.load()
        settings_.fee_vat_percent = D("7")
        settings_.save()
        other = Owner.objects.create(name="Other", passport_number="X1")
        fresh = OwnerStatement(owner=other, period_start=self.today, period_end=self.today)
        self.assertEqual(fresh.fee_vat_percent, D("7.00"))


class OwnershipAndAgreementTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.lease = self.make_lease()

    def cleared(self, number, days_ago):
        day = self.today - timedelta(days=days_ago)
        return self.make_cheque(
            self.lease, number=number, status=S.CLEARED, cleared_date=day, cheque_date=day
        )

    def test_rent_goes_to_the_owner_of_the_day(self):
        owner_two = Owner.objects.create(name="Owner Two", passport_number="P7654321")
        self.property.owner = owner_two
        self.property.save()
        row = PropertyOwnership.objects.get()
        self.assertEqual(row.owner, self.owner)
        row.to_date = self.today - timedelta(days=10)
        row.save()

        self.cleared("OLD", 20)
        self.cleared("NEW", 0)
        first = OwnerStatement.objects.create(
            owner=self.owner, period_start=self.today - timedelta(days=30),
            period_end=self.today - timedelta(days=11),
        )
        first.recalculate()
        self.assertEqual(first.totals()["rent"], D("10000.00"))
        second = OwnerStatement.objects.create(
            owner=owner_two, period_start=self.today - timedelta(days=30), period_end=self.today
        )
        second.recalculate()
        self.assertEqual(second.totals()["rent"], D("10000.00"))
        self.assertEqual(second.lines.filter(kind="rent").count(), 1)

    def test_no_fee_after_the_agreement_ended(self):
        self.property.management_end = self.today - timedelta(days=5)
        self.property.save()
        self.cleared("LATE", 0)
        statement = OwnerStatement.objects.create(
            owner=self.owner, period_start=self.today, period_end=self.today
        )
        statement.recalculate()
        totals = statement.totals()
        self.assertEqual(totals["rent"], D("10000.00"))  # the owner still gets the rent
        self.assertEqual(totals["fee"], D("0.00"))

    def test_fee_charged_while_the_agreement_runs(self):
        self.property.management_end = self.today + timedelta(days=100)
        self.property.save()
        self.cleared("OK", 0)
        statement = OwnerStatement.objects.create(
            owner=self.owner, period_start=self.today, period_end=self.today
        )
        statement.recalculate()
        self.assertEqual(statement.totals()["fee"], D("1000.00"))


class ExpenseRecoveryTests(BaseCase):
    def test_amount_still_owed(self):
        expense = Expense.objects.create(
            property=self.property, description="Broken window", amount=D("800"),
            bearer=Expense.Bearer.TENANT, tenant=self.tenant, recovered_amount=D("300"),
        )
        self.assertEqual(expense.still_to_recover, D("500.00"))
        context = dashboard_context(None)["dash"]
        self.assertEqual(context["recover_count"], 1)
        self.assertEqual(context["recover_total"], D("500.00"))
        other = Expense.objects.create(
            property=self.property, description="Owner job", amount=D("10"),
            bearer=Expense.Bearer.OWNER,
        )
        self.assertEqual(other.still_to_recover, D("0.00"))


class DashboardConsistencyTests(BaseCase):
    def test_unpaid_cash_is_counted_like_the_desk(self):
        lease = self.make_lease()
        Cheque.objects.create(
            lease=lease, method="cash", amount=D("100"),
            cheque_date=self.today - timedelta(days=1), status=S.PENDING,
        )
        tiles = {l: n for l, n, _ in dashboard_context(None)["dash"]["cheque_tiles"]}
        self.assertEqual(tiles["Not received"], 1)

    def test_inactive_properties_are_not_counted(self):
        self.property.is_active = False
        self.property.save()
        context = dashboard_context(None)["dash"]
        self.assertEqual(context["total_units"], 0)
        self.assertEqual(context["units"]["vacant"], 0)

    def test_holdover_units_are_shown(self):
        self.make_lease(
            start_date=self.today - timedelta(days=400),
            end_date=self.today - timedelta(days=35),
        )
        self.assertEqual(dashboard_context(None)["dash"]["units"]["holdover"], 1)


class OwnerPrivacyTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.owner.iban = ""
        self.owner.save()
        self.url = reverse("admin:realestate_owner_change", args=[self.owner.pk])

    def test_leasing_agent_cannot_see_ids_but_manager_can(self):
        self.client.force_login(user_with_role("lina", "Leasing agent"))
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Owner One")
        self.assertNotContains(page, "P1234567")
        self.client.force_login(user_with_role("mona", "Manager"))
        page = self.client.get(self.url)
        self.assertContains(page, "P1234567")

    def test_an_individual_agent_can_be_given_access(self):
        user = user_with_role("lina", "Leasing agent", "Owner details access")
        self.client.force_login(user)
        self.assertContains(self.client.get(self.url), "P1234567")

    def test_limited_user_can_save_an_owner_without_ids(self):
        user = user_with_role("lina", "Leasing agent")
        user.user_permissions.add(Permission.objects.get(codename="change_owner"))
        self.client.force_login(user)
        response = self.client.post(
            self.url,
            {
                "owner_type": "individual", "name": "Owner One Renamed", "email": "",
                "phone": "", "whatsapp": "", "address": "", "nationality": "",
                "management_fee_percent": "10", "notes": "", "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.name, "Owner One Renamed")
        self.assertEqual(self.owner.passport_number, "P1234567")  # untouched

    def test_search_does_not_leak_ids(self):
        self.client.force_login(user_with_role("lina", "Leasing agent"))
        page = self.client.get(reverse("admin:realestate_owner_changelist"), {"q": "P1234567"})
        self.assertNotContains(page, "Owner One")

    def test_uploaded_files_follow_the_same_rules(self):
        agent = user_with_role("lina", "Leasing agent")
        manager = user_with_role("mona", "Manager")
        owner_file = "documents/ownerdocument/2026/01/id.pdf"
        tenant_file = "documents/tenantdocument/2026/01/id.pdf"
        self.assertFalse(_allowed(agent, owner_file))
        self.assertTrue(_allowed(manager, owner_file))
        self.assertTrue(_allowed(agent, tenant_file))
        self.assertFalse(_allowed(agent, "documents/expense/2026/01/r.pdf"))
        self.assertFalse(_allowed(agent, "secret.txt"))
        self.assertFalse(_allowed(agent, "documents/../secret.txt"))
        self.client.force_login(agent)
        self.assertEqual(self.client.get("/media/" + owner_file).status_code, 403)
        self.assertEqual(self.client.get("/media/" + tenant_file).status_code, 404)  # allowed, no file


class RolesTests(BaseCase):
    def codes(self, role):
        return set(Group.objects.get(name=role).permissions.values_list("codename", flat=True))

    def test_owner_details_permission_is_not_for_everyone(self):
        self.assertIn("view_owner_private", self.codes("Manager"))
        self.assertIn("view_owner_private", self.codes("Accountant"))
        self.assertNotIn("view_owner_private", self.codes("Leasing agent"))
        self.assertNotIn("view_owner_private", self.codes("Viewer (read only)"))
        self.assertIn("Owner details access", ROLES)

    def test_new_permissions_reach_roles_that_already_exist(self):
        manager = Group.objects.get(name="Manager")
        manager.permissions.remove(Permission.objects.get(codename="view_companysettings"))
        manager.permissions.remove(Permission.objects.get(codename="view_owner_private"))
        SystemState.objects.filter(key="roles_version").update(value="1")
        ensure_roles()
        self.assertIn("view_owner_private", self.codes("Manager"))
        self.assertIn("view_companysettings", self.codes("Manager"))
        # ...and nothing else the admin had removed comes back
        leasing = Group.objects.get(name="Leasing agent")
        leasing.permissions.remove(Permission.objects.get(codename="view_unit"))
        SystemState.objects.filter(key="roles_version").update(value="1")
        ensure_roles()
        self.assertNotIn("view_unit", self.codes("Leasing agent"))


class AdminFlowTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser(
            "boss", "boss@example.com", "a-long-test-password-1"
        )
        self.client.force_login(self.admin)

    def test_lease_page_and_terminate_page(self):
        lease = self.make_lease()
        lease.generate_cheque_schedule()
        change = reverse("admin:realestate_lease_change", args=[lease.pk])
        page = self.client.get(change)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Terminate early")
        url = reverse("admin:realestate_lease_terminate", args=[lease.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url,
            {
                "notice_given_on": lease.start_date.isoformat(),
                "notice_days": "90",
                "exit_date": (lease.start_date + timedelta(days=100)).isoformat(),
                "penalty": "4000",
                "reason": "Relocating",
            },
        )
        self.assertRedirects(response, change)
        lease.refresh_from_db()
        self.assertEqual(lease.status, Lease.Status.TERMINATED)
        self.assertEqual(self.client.get(change).status_code, 200)
        self.assertEqual(self.client.get(url, follow=True).status_code, 200)  # redirects away

    def test_terminate_page_shows_notice_errors(self):
        lease = self.make_lease()
        url = reverse("admin:realestate_lease_terminate", args=[lease.pk])
        response = self.client.post(
            url,
            {
                "notice_given_on": lease.start_date.isoformat(),
                "notice_days": "90",
                "exit_date": (lease.start_date + timedelta(days=20)).isoformat(),
                "penalty": "0",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "notice period")
        lease.refresh_from_db()
        self.assertEqual(lease.status, Lease.Status.ACTIVE)

    def test_expire_and_refund_buttons(self):
        lease = self.make_lease(
            start_date=self.today - timedelta(days=400),
            end_date=self.today - timedelta(days=35),
        )
        change = reverse("admin:realestate_lease_change", args=[lease.pk])
        self.assertContains(self.client.get(change), "Mark as expired")
        response = self.client.post(reverse("admin:realestate_lease_do", args=[lease.pk, "expire"]))
        self.assertRedirects(response, change)
        lease.refresh_from_db()
        self.assertEqual(lease.status, Lease.Status.EXPIRED)
        self.assertEqual(self.client.get(change).status_code, 200)

    def test_expire_action_uses_the_real_logic(self):
        running = self.make_lease()
        url = reverse("admin:realestate_lease_changelist")
        self.client.post(
            url, {"action": "mark_expired", "_selected_action": [running.pk]}, follow=True
        )
        running.refresh_from_db()
        self.assertEqual(running.status, Lease.Status.ACTIVE)

    def test_payment_pages_and_one_click_paid(self):
        lease = self.make_lease()
        cash = Cheque.objects.create(
            lease=lease, method="cash", amount=D("500"),
            cheque_date=self.today, status=S.PENDING,
        )
        self.assertEqual(self.client.get(reverse("admin:realestate_cheque_add")).status_code, 200)
        desk = self.client.get(reverse("admin:realestate_cheque_desk"))
        self.assertContains(desk, "Paid")
        self.client.post(
            reverse("admin:realestate_cheque_workflow", args=[cash.pk]),
            {"action": "pay", "reference": ""},
        )
        cash.refresh_from_db()
        self.assertEqual(cash.status, S.CLEARED)
        self.assertEqual(
            self.client.get(reverse("admin:realestate_cheque_change", args=[cash.pk])).status_code,
            200,
        )

    def test_cleared_payments_cannot_be_deleted_in_admin(self):
        lease = self.make_lease()
        cheque = self.make_cheque(lease, status=S.CLEARED, cleared_date=self.today)
        url = reverse("admin:realestate_cheque_delete", args=[cheque.pk])
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_company_settings_page(self):
        response = self.client.get(reverse("admin:realestate_companysettings_changelist"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(response["Location"]).status_code, 200)

    def test_expense_and_statement_pages(self):
        expense = Expense.objects.create(
            property=self.property, description="Fix", amount=D("50"),
            bearer=Expense.Bearer.TENANT, tenant=self.tenant,
        )
        self.assertEqual(
            self.client.get(reverse("admin:realestate_expense_change", args=[expense.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("admin:realestate_expense_changelist"), {"recovery": "open"}
            ).status_code,
            200,
        )

    def test_property_page_shows_previous_owners(self):
        other = Owner.objects.create(name="Owner Two", passport_number="P7654321")
        self.property.owner = other
        self.property.save()
        page = self.client.get(reverse("admin:realestate_property_change", args=[self.property.pk]))
        self.assertContains(page, "Previous owners")
