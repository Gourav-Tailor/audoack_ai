from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from audio_analytics.billing import calculate_billing, get_current_period
from audio_analytics.models import BatchUpload, Payment


class BillingPaymentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="payer", password="password")
        self.client.login(username="payer", password="password")

    def test_billing_starts_with_zero_paid(self):
        billing = calculate_billing(self.user, get_current_period())
        self.assertEqual(billing["paid_total"], Decimal("0"))

    @patch("audio_analytics.billing_payments._client")
    def test_create_order_is_user_scoped(self, client_factory):
        client_factory.return_value.order.create.return_value = {"id": "order_test_123"}
        BatchUpload.objects.create(user=self.user, zip_file="batches/test.zip")
        response = self.client.post(reverse("billing_create_order"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Payment.objects.filter(
                user=self.user,
                razorpay_order_id="order_test_123",
            ).exists()
        )

    def test_webhook_requires_secret(self):
        response = self.client.post(
            reverse("razorpay_webhook"),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)
