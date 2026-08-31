import json
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def generate_device_key():
    return secrets.token_hex(20)


class Device(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="devices"
    )
    name = models.CharField(max_length=100)
    key = models.CharField(
        max_length=64, unique=True, default=generate_device_key, editable=False
    )
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.user.username})"


class BatchUpload(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="batches", null=True, blank=True
    )
    zip_file = models.FileField(upload_to="batches/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    error_message = models.TextField(blank=True, null=True)
    metrics_json = models.TextField(blank=True, null=True)
    name = models.CharField(max_length=255, default="Live Recording")
    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="batches"
    )

    def get_metrics(self):
        if self.metrics_json:
            try:
                return json.loads(self.metrics_json)
            except json.JSONDecodeError:
                return None
        return None

    def __str__(self):
        return f"Batch {self.id} - {self.status} ({self.uploaded_at.strftime('%Y-%m-%d %H:%M')})"


class AudioAnalysis(models.Model):
    class EmotionalTone(models.TextChoices):
        NEUTRAL = "neutral", "Neutral"
        SATISFIED = "satisfied", "Satisfied"
        FRUSTRATED = "frustrated", "Frustrated"
        UPSET = "upset", "Upset"
        DISTRESSED = "distressed", "Distressed"

    class EmotionalIntensity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class BackgroundNoiseSeverity(models.TextChoices):
        NONE = "none", "None"
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class AudioQuality(models.TextChoices):
        CLEAR = "clear", "Clear"
        SLIGHTLY_IMPAIRED = "slightly_impaired", "Slightly Impaired"
        SEVERELY_IMPAIRED = "severely_impaired", "Severely Impaired"

    class ProcessingStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    batch = models.ForeignKey(
        BatchUpload,
        related_name="analyses",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    filename = models.CharField(max_length=255)
    audio_file = models.FileField(upload_to="audio_clips/", blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
    )
    error_details = models.TextField(blank=True, default="")
    emotional_tone = models.CharField(
        max_length=20, choices=EmotionalTone.choices, blank=True, null=True
    )
    emotional_intensity = models.CharField(
        max_length=10, choices=EmotionalIntensity.choices, blank=True, null=True
    )
    background_noise_present = models.BooleanField(default=False)
    background_noise_type = models.CharField(max_length=255, blank=True, default="")
    background_noise_severity = models.CharField(
        max_length=10,
        choices=BackgroundNoiseSeverity.choices,
        default=BackgroundNoiseSeverity.NONE,
    )
    audio_quality = models.CharField(
        max_length=20, choices=AudioQuality.choices, blank=True, null=True
    )
    speaker_overlap_present = models.BooleanField(default=False)
    long_silence_present = models.BooleanField(default=False)
    confidence = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def to_dict(self):
        return {
            "emotional_tone": self.emotional_tone,
            "emotional_intensity": self.emotional_intensity,
            "background_noise_present": self.background_noise_present,
            "background_noise_type": self.background_noise_type,
            "background_noise_severity": self.background_noise_severity,
            "audio_quality": self.audio_quality,
            "speaker_overlap_present": self.speaker_overlap_present,
            "long_silence_present": self.long_silence_present,
            "confidence": (
                round(self.confidence, 2) if self.confidence is not None else None
            ),
        }

    def __str__(self):
        return f"{self.filename} ({self.status})"


class MobileAuthToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mobile_auth_tokens",
    )
    token = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["token", "revoked"]),
            models.Index(fields=["user", "revoked"]),
        ]

    @classmethod
    def issue(cls, user):
        return cls.objects.create(user=user, token=secrets.token_urlsafe(48))

    def __str__(self):
        return f"Mobile token for {self.user.username}"


class Payment(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_payments",
    )
    billing_month = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    idempotency_key = models.CharField(max_length=64, unique=True, editable=False)
    razorpay_order_id = models.CharField(max_length=100, unique=True)
    razorpay_payment_id = models.CharField(
        max_length=100, unique=True, null=True, blank=True
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CREATED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "billing_month", "status"]),
        ]

    def __str__(self):
        return f"{self.user.username} {self.billing_month:%Y-%m} {self.amount} {self.status}"
