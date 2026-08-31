from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from audio_analytics.billing import calculate_billing, get_current_period
from audio_analytics.models import AudioAnalysis, BatchUpload, Device


class BillingCalculationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="billing-user",
            password="test-password",
        )
        self.other_user = User.objects.create_user(
            username="other-user",
            password="test-password",
        )
        self.period = get_current_period()

    def test_billing_is_scoped_to_authenticated_user(self):
        BatchUpload.objects.create(
            user=self.user,
            zip_file="batches/user.zip",
        )
        BatchUpload.objects.create(
            user=self.other_user,
            zip_file="batches/other.zip",
        )

        result = calculate_billing(self.user, self.period)

        assert result["summary"]["batches"] == 1
        assert result["actual_total"] == Decimal("0.001")

    def test_successful_and_failed_analysis_are_metered(self):
        batch = BatchUpload.objects.create(
            user=self.user,
            zip_file="batches/user.zip",
        )

        AudioAnalysis.objects.create(
            batch=batch,
            filename="ok.wav",
            status=AudioAnalysis.ProcessingStatus.SUCCESS,
        )
        AudioAnalysis.objects.create(
            batch=batch,
            filename="failed.wav",
            status=AudioAnalysis.ProcessingStatus.FAILED,
            error_details="test",
        )

        result = calculate_billing(self.user, self.period)

        assert result["summary"]["processed_clips"] == 1
        assert result["summary"]["failed_clips"] == 1
        assert result["actual_total"] == Decimal("0.005")

    def test_devices_are_usage_only_until_pricing_is_configured(self):
        Device.objects.create(user=self.user, name="ESP32")
        result = calculate_billing(self.user, self.period)

        assert result["summary"]["devices"] == 1
        assert result["actual_total"] == Decimal("0")
