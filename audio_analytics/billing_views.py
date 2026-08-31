from datetime import date

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from .billing import calculate_billing, parse_period


class BillingView(LoginRequiredMixin, TemplateView):
    template_name = "audio_analytics/billing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        billing = calculate_billing(
            self.request.user,
            parse_period(self.request.GET.get("month")),
        )
        period = billing["period"]

        previous_month = period.start.date().replace(day=1)
        previous_month = (
            previous_month.replace(year=previous_month.year - 1, month=12)
            if previous_month.month == 1
            else previous_month.replace(month=previous_month.month - 1)
        )

        next_month = period.start.date().replace(day=1)
        next_month = (
            next_month.replace(year=next_month.year + 1, month=1)
            if next_month.month == 12
            else next_month.replace(month=next_month.month + 1)
        )

        current = date.today()
        billing["usd_inr_rate"] = getattr(settings, "BILLING_USD_INR_RATE", "90")
        context.update(
            billing=billing,
            period=period,
            previous_month=previous_month.strftime("%Y-%m"),
            next_month=next_month.strftime("%Y-%m"),
            can_go_next=(
                next_month.year < current.year
                or (
                    next_month.year == current.year
                    and next_month.month <= current.month
                )
            ),
            razorpay_enabled=bool(getattr(settings, "RAZORPAY_KEY_ID", "")),
        )
        return context
