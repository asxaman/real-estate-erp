"""
Small JSON endpoints used by the pop-ups and the auto-fill on the lease pages (the browser side is
core/static/core/leases.js). Each one answers {"ok": true, ...} or {"ok": false, "errors": [...]}.
"""
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils import timezone
from django.utils.formats import get_format

from core.admin_utils import format_amount
from core.formats import fmt_date
from core.toasts import titled
from properties.models import COMMERCIAL_UNIT_TYPES, Property, Unit

from . import pickers
from .models import CHARGE_FIELDS, Lease, LeaseDeduction, LeasePayment, add_months, money

# Standalone properties that are normally leased with VAT.
COMMERCIAL_PROPERTY_TYPES = (
    Property.PropertyType.SHOP, Property.PropertyType.WAREHOUSE, Property.PropertyType.YARD,
)


def fail(*texts, status=400):
    return JsonResponse({"ok": False, "errors": [str(t) for t in texts]}, status=status)


def refuse():
    return fail("You do not have permission to do this.", status=403)


def read_date(text, label="date"):
    """A date typed in the style chosen in System settings (2026-11-06 is always accepted too)."""
    try:
        value = forms.DateField().clean((text or "").strip())
    except ValidationError:
        raise ValidationError(f"Enter a valid {label}.") from None
    if value is None:
        raise ValidationError(f"Enter a valid {label}.")
    return value


def read_number(text):
    try:
        value = Decimal((text or "").replace(",", "").strip())
    except InvalidOperation:
        raise ValidationError("Enter a valid number.") from None
    if not value.is_finite():
        raise ValidationError("Enter a valid number.")
    return value


def _digits(text):
    return int(text) if (text or "").isdigit() else 0


class LeaseApiMixin:
    """Added to LeaseAdmin: the lease form's auto-fill and the Renew / Terminate / Cheques pop-ups."""

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("info/", wrap(self.info_view), name="leases_lease_info"),
            path("search/", wrap(self.search_view), name="leases_lease_search"),
            path("dates/", wrap(self.dates_view), name="leases_lease_dates"),
            path("<int:pk>/renew/", wrap(self.renew_view), name="leases_lease_renew"),
            path("<int:pk>/terminate/", wrap(self.terminate_view), name="leases_lease_terminate"),
            path("<int:pk>/cheques/", wrap(self.cheques_view), name="leases_lease_cheques"),
        ]
        return custom + super().get_urls()

    # ---- the search-as-you-type pickers ---------------------------------------------------
    def search_view(self, request):
        if not (self.has_add_permission(request) or self.has_change_permission(request)):
            return refuse()
        kind = request.GET.get("kind", "")
        if kind not in ("tenant", "unit", "property"):
            return fail("Unknown list.")
        return JsonResponse({"ok": True, "results": pickers.search(kind, request.GET.get("q", ""))})

    # ---- the "Today" and "+1 Year" buttons on the lease form --------------------------------
    def dates_view(self, request):
        """Today, and one year after `start` (the day before the anniversary), written the way dates are typed here."""
        if not (self.has_add_permission(request) or self.has_change_permission(request)):
            return refuse()
        shape = get_format("DATE_INPUT_FORMATS")[0]
        data = {"today": timezone.localdate().strftime(shape), "year_end": ""}
        text = (request.GET.get("start") or "").strip()
        if text:
            try:
                start = forms.DateField().clean(text)
            except ValidationError:
                start = None
            if start is not None:
                data["year_end"] = (add_months(start, 12) - timedelta(days=1)).strftime(shape)
        return JsonResponse({"ok": True, **data})

    # ---- auto-fill on the lease form ------------------------------------------------
    def info_view(self, request):
        if not (self.has_add_permission(request) or self.has_change_permission(request)):
            return refuse()
        unit = Unit.objects.select_related("property").filter(pk=_digits(request.GET.get("unit"))).first()
        prop = None if unit else Property.objects.filter(pk=_digits(request.GET.get("property"))).first()
        today = timezone.localdate()
        data = {"rent": "", "vat": False, "notes": [], "warning": "", "end": ""}
        if unit is None and prop is None:
            return JsonResponse(data)

        if unit is not None:
            label, rent = f"{unit} · {unit.property.name}", unit.asking_rent
            data["vat"] = unit.unit_type in COMMERCIAL_UNIT_TYPES
            others = Lease.objects.filter(unit=unit)
        else:
            label, rent = prop.name, prop.annual_rent
            data["vat"] = prop.property_type in COMMERCIAL_PROPERTY_TYPES
            others = Lease.objects.filter(property=prop, unit__isnull=True)
        if rent:
            data["rent"] = f"{rent:.2f}".rstrip("0").rstrip(".")
            data["notes"].append(f"Asking rent: AED {format_amount(rent)} a year")
        if data["vat"]:
            data["notes"].append("Commercial: VAT 5% was ticked")
        current = _digits(request.GET.get("lease"))
        if current:
            others = others.exclude(pk=current)

        start = None
        text = (request.GET.get("start") or "").strip()
        if text:
            try:
                start = forms.DateField().clean(text)
            except ValidationError:
                start = None
        if start is not None:
            end = add_months(start, 12) - timedelta(days=1)
            data["end"] = end.strftime(get_format("DATE_INPUT_FORMATS")[0])

        busy = [lease for lease in others if lease.effective_end >= today]
        if start is not None:
            end_day = add_months(start, 12) - timedelta(days=1)
            clash = [l for l in busy if l.start_date <= end_day and l.effective_end >= start]
        else:
            clash = []
        if clash:
            first = clash[0]
            data["warning"] = (
                f"{label} already has lease {first.code} ({fmt_date(first.start_date)} to "
                f"{fmt_date(first.effective_end)}), which overlaps these dates."
            )
        elif busy:
            first = busy[0]
            data["warning"] = (
                f"{label} has lease {first.code} until {fmt_date(first.effective_end)}. "
                "A new lease must start after that."
            )
        return JsonResponse(data)

    # ---- pop-ups ----------------------------------------------------------------------
    def _checked(self, request, permission):
        """(lease-less) common checks for the POST endpoints."""
        if request.method != "POST":
            return fail("This must be sent as a form.", status=405)
        if not permission(request):
            return refuse()
        return None

    def renew_view(self, request, pk):
        problem = self._checked(request, self.has_add_permission)
        if problem:
            return problem
        mode = request.POST.get("mode", "same")
        try:
            with transaction.atomic():
                # Lock the lease while renewing, so a double click cannot make two renewals.
                lease = get_object_or_404(Lease.objects.select_for_update(), pk=pk)
                new_rent = None
                if mode in ("percent", "amount"):
                    value = read_number(request.POST.get("value"))
                    if mode == "percent":
                        if not -50 <= value <= 200:
                            raise ValidationError("The change must be between -50% and 200%.")
                        new_rent = lease.annual_rent * (Decimal(100) + value) / 100
                    else:
                        new_rent = value
                new = lease.renew(new_rent)
        except ValidationError as exc:
            return fail(*exc.messages)
        messages.success(
            request,
            f"{new.code} was created as the renewal of {lease.code} ({fmt_date(new.start_date)} to "
            f"{fmt_date(new.end_date)}, AED {format_amount(new.annual_rent)} a year).",
            extra_tags=titled("Lease renewed"),
        )
        return JsonResponse({"ok": True, "redirect": reverse("admin:leases_lease_change", args=[new.pk])})

    def terminate_view(self, request, pk):
        problem = self._checked(request, self.has_change_permission)
        if problem:
            return problem
        lease = get_object_or_404(Lease, pk=pk)
        try:
            on = read_date(request.POST.get("date"), "termination date")
            cancelled = lease.terminate(
                on,
                (request.POST.get("reason") or "").strip(),
                cancel_unpaid=request.POST.get("cancel_unpaid") == "on",
                deposit_status=request.POST.get("deposit_status") or None,
            )
        except ValidationError as exc:
            return fail(*exc.messages)
        text = f"{lease.code} was terminated on {fmt_date(on)}."
        if cancelled:
            text += f" {cancelled} unpaid payment(s) after that date were cancelled."
        messages.success(request, text, extra_tags=titled("Lease terminated"))
        return JsonResponse({"ok": True, "redirect": ""})

    def cheques_view(self, request, pk):
        problem = self._checked(request, self.has_change_permission)
        if problem:
            return problem
        lease = get_object_or_404(Lease, pk=pk)
        try:
            count = lease.assign_cheques(request.POST.get("first"), request.POST.get("bank") or "")
        except ValidationError as exc:
            return fail(*exc.messages)
        if not count:
            return fail("Every payment of this lease is already paid, so there is nothing to number.")
        messages.success(request, f"{count} cheque number(s) were filled in for {lease.code}.", extra_tags=titled("Cheques numbered"))
        return JsonResponse({"ok": True, "redirect": ""})


def _follow_lease_amount(payment):
    """A deposit / fee line and the amount typed on the lease are the same money: keep them equal."""
    if payment.kind == LeasePayment.Kind.RENT:
        payment.lease.sync_rent_total()  # the annual rent is the total of the rent payments
    field = dict(CHARGE_FIELDS).get(payment.kind)
    if field and getattr(payment.lease, field) != payment.amount:
        Lease.objects.filter(pk=payment.lease_id).update(**{field: payment.amount})
    source = LeaseDeduction.objects.filter(payment_id=payment.pk).first()  # a line made from the Deductions tab
    if source is not None and (source.amount != payment.amount or source.date != payment.due_date):
        LeaseDeduction.objects.filter(pk=source.pk).update(amount=payment.amount, date=payment.due_date)


def read_optional_date(text, label):
    return read_date(text, label) if (text or "").strip() else None


def payment_values(payment):
    """What the edit pop-up shows (all text, ready for the form fields)."""
    def day(value):
        return value.isoformat() if value else ""

    return {
        "due_date": day(payment.due_date), "amount": f"{payment.amount:.2f}", "method": payment.method,
        "cheque_number": payment.cheque_number, "bank_name": payment.bank_name, "cheque_date": day(payment.cheque_date),
        "deposited_on": day(payment.deposited_on), "status": payment.status, "paid_on": day(payment.paid_on),
        "notes": payment.notes, "kind": payment.kind,
    }


def apply_payment(payment, data):
    """Copies the pop-up's fields onto a payment, refusing anything odd. Raises ValidationError."""
    payment.due_date = read_date(data.get("due_date"), "due date")
    amount = read_number(data.get("amount"))
    if amount <= 0:
        raise ValidationError("The amount must be more than zero.")
    payment.amount = money(amount)
    method = data.get("method", "")
    if method not in LeasePayment.Method.values:
        raise ValidationError("Choose how the payment is made.")
    payment.method = method
    status = data.get("status", "")
    if status not in LeasePayment.Status.values:
        raise ValidationError("Choose the status of the payment.")
    payment.status = status
    payment.cheque_number = (data.get("cheque_number") or "").strip()[:30]
    payment.bank_name = (data.get("bank_name") or "").strip()[:60]
    payment.cheque_date = read_optional_date(data.get("cheque_date"), "date on the cheque")
    payment.deposited_on = read_optional_date(data.get("deposited_on"), "deposit date")
    payment.paid_on = read_optional_date(data.get("paid_on"), "date received")
    payment.notes = (data.get("notes") or "").strip()[:200]
    payment.full_clean(exclude=["lease", "part"])  # the model's own rules (dates in order, paid needs a date ...)


class PaymentApiMixin:
    """Added to LeasePaymentAdmin: the Record, Edit, Add and Remove payment pop-ups."""

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("<int:pk>/record/", wrap(self.record_view), name="leases_leasepayment_record"),
            path("<int:pk>/edit/", wrap(self.edit_view), name="leases_leasepayment_edit"),
            path("<int:pk>/remove/", wrap(self.remove_view), name="leases_leasepayment_remove"),
            path("for-lease/<int:lease_pk>/add/", wrap(self.add_view_json), name="leases_leasepayment_add_to"),
            path("bulk/", wrap(self.bulk_view), name="leases_leasepayment_bulk"),
        ]
        return custom + super().get_urls()

    def record_view(self, request, pk):
        if request.method != "POST":
            return fail("This must be sent as a form.", status=405)
        if not self.has_change_permission(request):
            return refuse()
        action = request.POST.get("action", "paid")
        try:
            with transaction.atomic():
                # Lock the payment, so two people (or a double click) cannot record it twice.
                payment = get_object_or_404(LeasePayment.objects.select_for_update().select_related("lease"), pk=pk)
                if action not in ("bounced", "waived") and payment.status == LeasePayment.Status.PAID:
                    raise ValidationError(
                        f"{payment.label} of {payment.lease.code} is already recorded as paid"
                        f"{' on ' + fmt_date(payment.paid_on) if payment.paid_on else ''}. Refresh the page."
                    )
                if action == "held" and payment.status == LeasePayment.Status.HELD:
                    raise ValidationError(f"The cheque for {payment.label.lower()} of {payment.lease.code} is already held. Refresh the page.")
                if action == "held":
                    on = read_date(request.POST.get("date"), "date received")
                    payment.hold(on, request.POST.get("cheque_number", ""), request.POST.get("bank_name", ""))
                    messages.success(
                        request,
                        f"{payment.label} of {payment.lease.code}: the cheque is held, not cashed (AED {format_amount(payment.total)}).",
                        extra_tags=titled("Cheque held"),
                    )
                elif action == "waived":
                    payment.waive()
                    messages.success(
                        request, f"{payment.label} of {payment.lease.code} was waived (written off).",
                        extra_tags=titled("Payment waived"),
                    )
                elif action == "bounced":
                    payment.bounce()
                    messages.warning(
                        request, f"{payment.label} of {payment.lease.code} was marked as bounced.",
                        extra_tags=titled("Cheque bounced"),
                    )
                else:
                    on = read_date(request.POST.get("date"), "date received")
                    payment.record(
                        on, request.POST.get("method", ""), request.POST.get("cheque_number", ""), request.POST.get("bank_name", ""),
                    )
                    messages.success(
                        request,
                        f"{payment.label} of {payment.lease.code} (AED {format_amount(payment.total)}) was received on {fmt_date(on)}.",
                        extra_tags=titled("Payment recorded"),
                    )
        except ValidationError as exc:
            return fail(*exc.messages)
        return JsonResponse({"ok": True, "redirect": ""})

    def edit_view(self, request, pk):
        if not self.has_change_permission(request):
            return refuse()
        payment = get_object_or_404(LeasePayment.objects.select_related("lease"), pk=pk)
        if request.method == "GET":
            return JsonResponse({
                "ok": True, "values": payment_values(payment), "code": payment.lease.code, "part": payment.part, "label": payment.label,
                "can_remove": payment.status not in (LeasePayment.Status.PAID, LeasePayment.Status.HELD)
                and request.user.has_perm("leases.delete_leasepayment"),
            })
        if request.method != "POST":
            return fail("This must be sent as a form.", status=405)
        try:
            apply_payment(payment, request.POST)
            payment.save()
            _follow_lease_amount(payment)
        except ValidationError as exc:
            return fail(*exc.messages)
        messages.success(request, f"{payment.label} of {payment.lease.code} was updated.", extra_tags=titled("Payment updated"))
        return JsonResponse({"ok": True, "redirect": ""})

    def remove_view(self, request, pk):
        if request.method != "POST":
            return fail("This must be sent as a form.", status=405)
        if not request.user.has_perm("leases.delete_leasepayment"):
            return refuse()
        payment = get_object_or_404(LeasePayment.objects.select_related("lease"), pk=pk)
        if payment.status in (LeasePayment.Status.PAID, LeasePayment.Status.HELD):
            return fail("A payment that was received (or whose cheque is held) cannot be removed.")
        label = f"{payment.label} of {payment.lease.code}"
        field = dict(CHARGE_FIELDS).get(payment.kind)
        was_rent, lease = payment.kind == LeasePayment.Kind.RENT, payment.lease
        payment.delete()
        if field:  # a removed deposit / fee line means none is asked for any more
            Lease.objects.filter(pk=payment.lease_id).update(**{field: 0})
        if was_rent:
            lease.sync_rent_total()
        messages.success(request, f"{label} was removed.", extra_tags=titled("Payment removed"))
        return JsonResponse({"ok": True, "redirect": ""})

    def add_view_json(self, request, lease_pk):
        if request.method != "POST":
            return fail("This must be sent as a form.", status=405)
        if not request.user.has_perm("leases.add_leasepayment"):
            return refuse()
        lease = get_object_or_404(Lease, pk=lease_pk)
        kind = request.POST.get("kind") or LeasePayment.Kind.RENT
        if kind not in LeasePayment.Kind.values:
            return fail("Choose the type of payment.")
        if kind == LeasePayment.Kind.DEDUCTION:
            return fail("Deductions are added on the lease's Deductions tab. Use a Fine or Other line here.")
        if kind in dict(CHARGE_FIELDS) and lease.payments.filter(kind=kind).exists():
            return fail(f"This lease already has a {LeasePayment.Kind(kind).label.lower()} line. Edit that line instead.")
        payment = LeasePayment(
            lease=lease, kind=kind, part=(lease.payments.filter(kind=kind).aggregate(m=Max("part"))["m"] or 0) + 1,
        )
        try:
            data = request.POST.copy()
            if kind == LeasePayment.Kind.REFUND:  # money paid back to the tenant: done when it is entered
                data.setdefault("status", LeasePayment.Status.PAID)
                data.setdefault("paid_on", data.get("due_date", ""))
            data.setdefault("status", LeasePayment.Status.PENDING)
            apply_payment(payment, data)
            payment.save()
            _follow_lease_amount(payment)
        except ValidationError as exc:
            return fail(*exc.messages)
        messages.success(request, f"{payment.label} was added to {lease.code}.", extra_tags=titled("Payment added"))
        return JsonResponse({"ok": True, "redirect": ""})

    def _bulk_delete(self, request, ids):
        """Remove the ticked payments that were not received. Received ones (and held cheques) stay."""
        if not request.user.has_perm("leases.delete_leasepayment"):
            return refuse()
        removed = kept = 0
        with transaction.atomic():
            for payment in LeasePayment.objects.select_for_update().filter(pk__in=ids).order_by("pk"):
                if payment.status in (LeasePayment.Status.PAID, LeasePayment.Status.HELD):
                    kept += 1
                    continue
                field = dict(CHARGE_FIELDS).get(payment.kind)
                lease_id = payment.lease_id
                was_rent = payment.kind == LeasePayment.Kind.RENT
                payment.delete()
                if field:  # a removed deposit / fee line means none is asked for any more
                    Lease.objects.filter(pk=lease_id).update(**{field: 0})
                if was_rent:
                    Lease.objects.get(pk=lease_id).sync_rent_total()
                removed += 1
        if not removed:
            return fail(f"Nothing was removed: the {kept} ticked payment(s) were already received or hold a cheque.")
        text = f"{removed} payment(s) removed."
        if kept:
            text += f" {kept} kept (received, or the cheque is held)."
        messages.success(request, text, extra_tags=titled("Payments removed"))
        return JsonResponse({"ok": True, "redirect": ""})

    def _bulk_edit(self, request, ids):
        """Change the same detail on all ticked payments. A field left empty is not touched."""
        if not self.has_change_permission(request):
            return refuse()
        post = request.POST
        try:
            due = read_optional_date(post.get("due_date"), "due date")
            cheque_date = read_optional_date(post.get("cheque_date"), "date on the cheque")
            deposited = read_optional_date(post.get("deposited_on"), "deposit date")
            amount = read_number(post.get("amount")) if (post.get("amount") or "").strip() else None
            if amount is not None and amount <= 0:
                raise ValidationError("The amount must be more than zero.")
        except ValidationError as exc:
            return fail(*exc.messages)
        method = post.get("method", "")
        if method and method not in LeasePayment.Method.values:
            return fail("Choose how the payments are made.")
        bank = (post.get("bank_name") or "").strip()[:60]
        note = (post.get("notes") or "").strip()[:200]
        first = (post.get("cheque_first") or "").strip()
        match = re.fullmatch(r"(\D*)(\d+)", first) if first else None
        if first and not match:
            return fail("The first cheque number must end in digits, for example 000123 or CQ-0042.")
        if not any([due, cheque_date, deposited, amount is not None, method, bank, note, first]):
            return fail("Fill in at least one detail to change.")

        changed = skipped = 0
        counter = int(match.group(2)) if match else 0
        try:
            with transaction.atomic():
                payments = list(
                    LeasePayment.objects.select_for_update().select_related("lease").filter(pk__in=ids).order_by("due_date", "part", "pk")
                )
                for payment in payments:
                    received = payment.status in (LeasePayment.Status.PAID, LeasePayment.Status.HELD)
                    touched = False
                    if (due or amount is not None) and received:
                        skipped += 1  # the date and amount of received money are not changed in bulk
                    else:
                        if due:
                            payment.due_date, touched = due, True
                        if amount is not None:
                            payment.amount, touched = money(amount), True
                    if method:
                        payment.method, touched = method, True
                    if bank:
                        payment.bank_name, touched = bank, True
                    if cheque_date:
                        payment.cheque_date, touched = cheque_date, True
                    if deposited:
                        payment.deposited_on, touched = deposited, True
                    if note:
                        payment.notes, touched = note, True
                    if match and payment.method == LeasePayment.Method.CHEQUE and payment.status != LeasePayment.Status.PAID:
                        payment.cheque_number = f"{match.group(1)}{counter:0{len(match.group(2))}d}"[:30]
                        counter += 1
                        touched = True
                    if touched:
                        payment.save()
                        _follow_lease_amount(payment)
                        changed += 1
        except ValidationError as exc:
            return fail(*exc.messages)
        if not changed:
            return fail("Nothing was changed: the ticked payments were already received.")
        text = f"{changed} payment(s) updated."
        if skipped:
            text += f" The date or amount of {skipped} received payment(s) was left alone."
        messages.success(request, text, extra_tags=titled("Payments updated"))
        return JsonResponse({"ok": True, "redirect": ""})

    def bulk_view(self, request):
        """Mark many ticked payments as paid, or their cheques as deposited, in one go.

        Safe when several people work at once: the rows are locked while they are changed, and a payment that
        someone else has already received is skipped instead of being recorded twice."""
        if request.method != "POST":
            return fail("This must be sent as a form.", status=405)
        if not self.has_change_permission(request):
            return refuse()
        ids = [int(x) for x in (request.POST.get("ids") or "").split(",") if x.strip().isdigit()]
        if not ids:
            return fail("Tick the payments in the list first.")
        if len(ids) > 500:
            return fail("Tick at most 500 payments at a time.")
        action = request.POST.get("action", "")
        if action == "delete":
            return self._bulk_delete(request, ids)
        if action == "edit":
            return self._bulk_edit(request, ids)
        if action not in ("paid", "deposited"):
            return fail("Choose what to do with the ticked payments.")
        method = request.POST.get("method", "")
        if method and method not in LeasePayment.Method.values:
            return fail("Choose how the payments were made.")
        try:
            on = read_date(request.POST.get("date"), "date")
        except ValidationError as exc:
            return fail(*exc.messages)

        done = skipped = 0
        with transaction.atomic():
            for payment in LeasePayment.objects.select_for_update().select_related("lease").filter(pk__in=ids).order_by("pk"):
                try:
                    if action == "paid":
                        if payment.status == LeasePayment.Status.PAID:
                            skipped += 1
                            continue
                        payment.record(on, method or payment.method, payment.cheque_number, payment.bank_name)
                    else:
                        if payment.method != LeasePayment.Method.CHEQUE or payment.deposited_on or payment.status == LeasePayment.Status.BOUNCED:
                            skipped += 1
                            continue
                        payment.deposited_on = on
                        payment.save()
                except ValidationError:
                    skipped += 1
                    continue
                done += 1
        what = "marked as paid" if action == "paid" else "marked as deposited"
        if not done:
            return fail(f"Nothing was changed: {skipped} of the ticked payment(s) did not need it" + (" (already received)." if action == "paid" else " (not an undeposited cheque, or the date is before the date on the cheque)."))
        text = f"{done} payment(s) {what} on {fmt_date(on)}."
        if skipped:
            text += f" {skipped} skipped."
        messages.success(request, text, extra_tags=titled("Payments updated"))
        return JsonResponse({"ok": True, "redirect": ""})
