"""
Small JSON endpoints used by the pop-ups and the auto-fill on the lease pages (the browser side is
core/static/core/leases.js). Each one answers {"ok": true, ...} or {"ok": false, "errors": [...]}.
"""
from datetime import date, timedelta
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
from .models import Lease, LeasePayment, add_months, money

# Standalone properties that are normally leased with VAT.
COMMERCIAL_PROPERTY_TYPES = (
    Property.PropertyType.SHOP, Property.PropertyType.WAREHOUSE, Property.PropertyType.YARD,
)


def fail(*texts, status=400):
    return JsonResponse({"ok": False, "errors": [str(t) for t in texts]}, status=status)


def refuse():
    return fail("You do not have permission to do this.", status=403)


def read_date(text, label="date"):
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        raise ValidationError(f"Enter a valid {label}.")


def read_number(text):
    try:
        value = Decimal((text or "").replace(",", "").strip())
    except InvalidOperation:
        raise ValidationError("Enter a valid number.")
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
        lease = get_object_or_404(Lease, pk=pk)
        mode = request.POST.get("mode", "same")
        try:
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
        "notes": payment.notes,
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
        payment = get_object_or_404(LeasePayment.objects.select_related("lease"), pk=pk)
        action = request.POST.get("action", "paid")
        try:
            if action == "bounced":
                payment.bounce()
                messages.warning(
                    request, f"Payment {payment.part} of {payment.lease.code} was marked as bounced.",
                    extra_tags=titled("Cheque bounced"),
                )
            else:
                on = read_date(request.POST.get("date"), "date received")
                payment.record(
                    on, request.POST.get("method", ""), request.POST.get("cheque_number", ""), request.POST.get("bank_name", ""),
                )
                messages.success(
                    request,
                    f"Payment {payment.part} of {payment.lease.code} (AED {format_amount(payment.total)}) was recorded as paid on {fmt_date(on)}.",
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
                "ok": True, "values": payment_values(payment), "code": payment.lease.code, "part": payment.part,
                "can_remove": payment.status != LeasePayment.Status.PAID and request.user.has_perm("leases.delete_leasepayment"),
            })
        if request.method != "POST":
            return fail("This must be sent as a form.", status=405)
        try:
            apply_payment(payment, request.POST)
            payment.save()
        except ValidationError as exc:
            return fail(*exc.messages)
        messages.success(request, f"Payment {payment.part} of {payment.lease.code} was updated.", extra_tags=titled("Payment updated"))
        return JsonResponse({"ok": True, "redirect": ""})

    def remove_view(self, request, pk):
        if request.method != "POST":
            return fail("This must be sent as a form.", status=405)
        if not request.user.has_perm("leases.delete_leasepayment"):
            return refuse()
        payment = get_object_or_404(LeasePayment.objects.select_related("lease"), pk=pk)
        if payment.status == LeasePayment.Status.PAID:
            return fail("A payment that was received cannot be removed.")
        label = f"Payment {payment.part} of {payment.lease.code}"
        payment.delete()
        messages.success(request, f"{label} was removed.", extra_tags=titled("Payment removed"))
        return JsonResponse({"ok": True, "redirect": ""})

    def add_view_json(self, request, lease_pk):
        if request.method != "POST":
            return fail("This must be sent as a form.", status=405)
        if not request.user.has_perm("leases.add_leasepayment"):
            return refuse()
        lease = get_object_or_404(Lease, pk=lease_pk)
        payment = LeasePayment(lease=lease, part=(lease.payments.aggregate(m=Max("part"))["m"] or 0) + 1)
        try:
            data = request.POST.copy()
            data.setdefault("status", LeasePayment.Status.PENDING)
            apply_payment(payment, data)
            payment.save()
        except ValidationError as exc:
            return fail(*exc.messages)
        messages.success(request, f"Payment {payment.part} was added to {lease.code}.", extra_tags=titled("Payment added"))
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
