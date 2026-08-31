from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.conf import settings
from django.db.models import QuerySet, Sum
from django.utils import timezone

from .models import AudioAnalysis, BatchUpload, Device, Payment

DEFAULT_AUDIO_ANALYSIS_RATE = Decimal("0.003")
DEFAULT_FAILED_ANALYSIS_RATE = Decimal("0.001")
DEFAULT_BATCH_RATE = Decimal("0.001")


def _rate(name: str, default: Decimal) -> Decimal:
    try:
        return Decimal(str(getattr(settings, name, default)))
    except Exception:
        return default


AUDIO_ANALYSIS_RATE = _rate("BILLING_AUDIO_ANALYSIS_RATE", DEFAULT_AUDIO_ANALYSIS_RATE)
FAILED_ANALYSIS_RATE = _rate(
    "BILLING_FAILED_ANALYSIS_RATE", DEFAULT_FAILED_ANALYSIS_RATE
)
BATCH_RATE = _rate("BILLING_BATCH_RATE", DEFAULT_BATCH_RATE)
BILLING_CURRENCY = getattr(settings, "BILLING_CURRENCY", "INR").upper()


@dataclass(frozen=True)
class BillingPeriod:
    start: datetime
    end: datetime
    label: str
    is_current: bool
    days_in_period: int
    elapsed_days: int


def get_billing_period(year: int, month: int) -> BillingPeriod:
    first_day = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(
        datetime.combine(first_day, datetime.min.time()), timezone=tz
    )
    end = timezone.make_aware(
        datetime.combine(last_day, datetime.max.time()), timezone=tz
    )
    now = timezone.localtime()
    is_current = now.year == year and now.month == month
    days = (last_day - first_day).days + 1
    elapsed = max(1, min(now.day, days)) if is_current else days
    return BillingPeriod(
        start, end, first_day.strftime("%B %Y"), is_current, days, elapsed
    )


def get_current_period() -> BillingPeriod:
    now = timezone.localtime()
    return get_billing_period(now.year, now.month)


def parse_period(value: Optional[str]) -> BillingPeriod:
    if value:
        try:
            year, month = value.split("-", 1)
            if 2000 <= int(year) <= 2100 and 1 <= int(month) <= 12:
                return get_billing_period(int(year), int(month))
        except (TypeError, ValueError):
            pass
    return get_current_period()


def _period_queryset(qs: QuerySet, period: BillingPeriod, field: str) -> QuerySet:
    return qs.filter(**{f"{field}__gte": period.start, f"{field}__lte": period.end})


def calculate_billing(user, period: BillingPeriod) -> dict:
    batches = _period_queryset(
        BatchUpload.objects.filter(user=user), period, "uploaded_at"
    )
    analyses = _period_queryset(
        AudioAnalysis.objects.filter(batch__user=user), period, "created_at"
    )
    successful = analyses.filter(status=AudioAnalysis.ProcessingStatus.SUCCESS)
    failed = analyses.filter(status=AudioAnalysis.ProcessingStatus.FAILED)

    batch_count = batches.count()
    successful_count = successful.count()
    failed_count = failed.count()
    device_count = Device.objects.filter(user=user).count()
    active_device_count = Device.objects.filter(
        user=user, last_seen__gte=period.start, last_seen__lte=period.end
    ).count()

    services = [
        {
            "key": "audio_analysis",
            "name": "Audio analysis",
            "description": "Successfully processed audio clips",
            "usage": successful_count,
            "unit": "clips",
            "rate": AUDIO_ANALYSIS_RATE,
            "cost": Decimal(successful_count) * AUDIO_ANALYSIS_RATE,
            "metered": True,
        },
        {
            "key": "failed_analysis",
            "name": "Failed processing",
            "description": "Processing attempts that failed after being submitted",
            "usage": failed_count,
            "unit": "clips",
            "rate": FAILED_ANALYSIS_RATE,
            "cost": Decimal(failed_count) * FAILED_ANALYSIS_RATE,
            "metered": True,
        },
        {
            "key": "batch_processing",
            "name": "Batch processing",
            "description": "Uploaded processing batches",
            "usage": batch_count,
            "unit": "batches",
            "rate": BATCH_RATE,
            "cost": Decimal(batch_count) * BATCH_RATE,
            "metered": True,
        },
        {
            "key": "devices",
            "name": "Device usage",
            "description": "Devices registered to your account",
            "usage": device_count,
            "unit": "devices",
            "rate": Decimal("0"),
            "cost": Decimal("0"),
            "metered": False,
            "note": "No device fee configured yet",
        },
    ]

    actual_total = sum((item["cost"] for item in services), Decimal("0")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    projected_total = (
        (
            actual_total * Decimal(period.days_in_period) / Decimal(period.elapsed_days)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if period.is_current
        else actual_total
    )

    month = period.start.date().replace(day=1)
    paid_total = Payment.objects.filter(
        user=user, billing_month=month, status=Payment.Status.PAID
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    outstanding_total = max(Decimal("0"), actual_total - paid_total).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return {
        "period": period,
        "services": services,
        "actual_total": actual_total,
        "projected_total": projected_total,
        "paid_total": paid_total,
        "outstanding_total": outstanding_total,
        "currency": BILLING_CURRENCY,
        "summary": {
            "batches": batch_count,
            "processed_clips": successful_count,
            "failed_clips": failed_count,
            "devices": device_count,
            "active_devices": active_device_count,
        },
    }
