import hashlib
import json
from decimal import Decimal

import razorpay
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import View

from .billing import calculate_billing, get_current_period
from .models import Payment


def _client():
    key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
    secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    if not key_id or not secret:
        raise RuntimeError("Razorpay credentials are not configured")
    return razorpay.Client(auth=(key_id, secret))


def _paise(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))


def _idempotency_key(user_id, month, amount):
    return hashlib.sha256(
        f"{user_id}:{month.isoformat()}:{amount}".encode()
    ).hexdigest()


class CreateRazorpayOrderView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        billing = calculate_billing(request.user, get_current_period())

        if billing["currency"] != "INR":
            return JsonResponse(
                {"error": "Razorpay checkout requires BILLING_CURRENCY=INR."},
                status=400,
            )

        amount = billing["outstanding_total"]
        if amount <= 0:
            return JsonResponse(
                {"error": "There is no outstanding amount to pay."}, status=400
            )

        month = billing["period"].start.date().replace(day=1)
        key = _idempotency_key(request.user.pk, month, amount)

        try:
            with transaction.atomic():
                existing = Payment.objects.filter(idempotency_key=key).first()

                if existing and existing.status == Payment.Status.CREATED:
                    return JsonResponse(
                        {
                            "key_id": settings.RAZORPAY_KEY_ID,
                            "order_id": existing.razorpay_order_id,
                            "amount": _paise(existing.amount),
                            "currency": existing.currency,
                        }
                    )

                order = _client().order.create(
                    {
                        "amount": _paise(amount),
                        "currency": "INR",
                        "receipt": f"audoack-{request.user.pk}-{month:%Y%m}-{key[:12]}",
                        "notes": {
                            "user_id": str(request.user.pk),
                            "billing_month": month.isoformat(),
                        },
                    }
                )

                payment = Payment.objects.create(
                    user=request.user,
                    billing_month=month,
                    amount=amount,
                    currency="INR",
                    idempotency_key=key,
                    razorpay_order_id=order["id"],
                )

            return JsonResponse(
                {
                    "key_id": settings.RAZORPAY_KEY_ID,
                    "order_id": payment.razorpay_order_id,
                    "amount": _paise(payment.amount),
                    "currency": payment.currency,
                }
            )
        except RuntimeError as exc:
            return JsonResponse({"error": str(exc)}, status=503)
        except razorpay.errors.BadRequestError:
            return JsonResponse(
                {"error": "Razorpay rejected the order request."}, status=502
            )


class VerifyRazorpayPaymentView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body)
            order_id = payload["razorpay_order_id"]
            payment_id = payload["razorpay_payment_id"]
            signature = payload["razorpay_signature"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse(
                {"error": "Invalid payment verification payload."}, status=400
            )

        try:
            payment = Payment.objects.get(
                razorpay_order_id=order_id,
                user=request.user,
            )
        except Payment.DoesNotExist:
            return JsonResponse({"error": "Unknown Razorpay order."}, status=404)

        if payment.status == Payment.Status.PAID:
            return JsonResponse({"status": "already_verified"})

        try:
            _client().utility.verify_payment_signature(
                {
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": signature,
                }
            )
        except Exception:
            return JsonResponse({"error": "Invalid payment signature."}, status=400)

        try:
            details = _client().payment.fetch(payment_id)
        except Exception:
            return JsonResponse(
                {"error": "Unable to verify payment with Razorpay."}, status=502
            )

        if (
            details.get("order_id") != payment.razorpay_order_id
            or int(details.get("amount", -1)) != _paise(payment.amount)
            or details.get("currency") != payment.currency
        ):
            return JsonResponse(
                {"error": "Razorpay payment does not match the billing order."},
                status=400,
            )

        payment.razorpay_payment_id = payment_id
        payment.save(update_fields=["razorpay_payment_id"])

        return JsonResponse({"status": "verified"})


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
    if not secret:
        return JsonResponse({"error": "Webhook secret is not configured."}, status=503)

    signature = request.headers.get("X-Razorpay-Signature", "")
    if not signature:
        return JsonResponse({"error": "Missing webhook signature."}, status=400)

    try:
        _client().utility.verify_webhook_signature(
            request.body.decode("utf-8"),
            signature,
            secret,
        )
    except Exception:
        return JsonResponse({"error": "Invalid webhook signature."}, status=400)

    try:
        payload = json.loads(request.body)
        event = payload.get("event")
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

        # order.paid has an order entity instead of a payment entity in some
        # webhook payloads. Use the payment entity when available and fall
        # back to the order entity for the order id.
        order_entity = payload.get("payload", {}).get("order", {}).get("entity", {})
        entity = payment_entity or order_entity
        order_id = entity.get("order_id") or order_entity.get("id")

        if not order_id:
            return JsonResponse({"status": "ignored"})

        payment = Payment.objects.get(razorpay_order_id=order_id)

        if event in {"payment.captured", "order.paid"}:
            amount = entity.get("amount")
            currency = entity.get("currency")

            if amount is not None and int(amount) != _paise(payment.amount):
                return JsonResponse({"error": "Webhook amount mismatch."}, status=400)

            if currency and currency != payment.currency:
                return JsonResponse({"error": "Webhook currency mismatch."}, status=400)

            # Idempotent: repeated captured/paid webhooks leave the same
            # payment in the PAID state and never add another payment row.
            update_fields = []

            if payment.status != Payment.Status.PAID:
                payment.status = Payment.Status.PAID
                update_fields.append("status")

            payment_id = entity.get("id") or payment.razorpay_payment_id
            if payment_id and payment.razorpay_payment_id != payment_id:
                payment.razorpay_payment_id = payment_id
                update_fields.append("razorpay_payment_id")

            if payment.paid_at is None:
                payment.paid_at = timezone.now()
                update_fields.append("paid_at")

            if update_fields:
                payment.save(update_fields=update_fields)

        elif event == "payment.failed":
            Payment.objects.filter(
                razorpay_order_id=order_id,
                status=Payment.Status.CREATED,
            ).update(status=Payment.Status.FAILED)

    except Payment.DoesNotExist:
        # Do not retry forever for an order that does not belong to this app.
        return JsonResponse({"status": "ignored"})
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid webhook payload."}, status=400)

    return JsonResponse({"status": "ok"})
