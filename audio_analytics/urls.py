from django.urls import path
from django.views.generic import TemplateView

from .api_v1 import (
    LatestDeviceAnalysisView,
    PublicDemoAnalysisView,
    PublicDemoChunkView,
    PublicDemoFinalizeView,
    PublicDemoStartView,
    SessionChunkView,
    SessionFinalizeView,
    SessionHeartbeatView,
    SessionInitView,
)
from .billing_payments import (
    CreateRazorpayOrderView,
    VerifyRazorpayPaymentView,
    razorpay_webhook,
)
from .billing_views import BillingView
from .mobile_api import (
    MobileDeviceLatestAnalysisView,
    MobileDevicesView,
    MobileLoginView,
    MobileLogoutView,
)
from .views import (
    BatchDetailView,
    BatchListView,
    BatchUploadView,
    DashboardView,
    ExportBatchResultsView,
    SignUpView,
)

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("billing/", BillingView.as_view(), name="billing"),
    path(
        "billing/pay/", CreateRazorpayOrderView.as_view(), name="billing_create_order"
    ),
    path(
        "billing/payment/verify/",
        VerifyRazorpayPaymentView.as_view(),
        name="billing_verify_payment",
    ),
    path("billing/webhook/razorpay/", razorpay_webhook, name="razorpay_webhook"),
    path("signup/", SignUpView.as_view(), name="signup"),
    path(
        "demo/",
        TemplateView.as_view(template_name="audio_analytics/public_demo.html"),
        name="public_demo",
    ),
    path("upload/", BatchUploadView.as_view(), name="batch_upload"),
    path("batches/", BatchListView.as_view(), name="batch_list"),
    path("batches/<int:pk>/", BatchDetailView.as_view(), name="batch_detail"),
    path(
        "batches/<int:pk>/export/",
        ExportBatchResultsView.as_view(),
        name="batch_export",
    ),
    path(
        "long-recorder/",
        TemplateView.as_view(template_name="audio_analytics/long_recorder.html"),
        name="long_recorder",
    ),
    # Canonical API v1 recording pipeline.
    path("api/v1/sessions/", SessionInitView.as_view(), name="api_v1_session_init"),
    path(
        "api/v1/sessions/<int:batch_id>/chunks/",
        SessionChunkView.as_view(),
        name="api_v1_session_chunk",
    ),
    path(
        "api/v1/sessions/<int:batch_id>/finalize/",
        SessionFinalizeView.as_view(),
        name="api_v1_session_finalize",
    ),
    path(
        "api/v1/sessions/<int:batch_id>/heartbeat/",
        SessionHeartbeatView.as_view(),
        name="api_v1_session_heartbeat",
    ),
    path(
        "api/v1/latest-analysis/",
        LatestDeviceAnalysisView.as_view(),
        name="api_v1_latest_analysis",
    ),
    # Anonymous public demo: one 5-second chunk, signed short-lived token.
    path(
        "api/v1/demo/start/",
        PublicDemoStartView.as_view(),
        name="api_v1_demo_start",
    ),
    path(
        "api/v1/demo/<int:batch_id>/chunk/",
        PublicDemoChunkView.as_view(),
        name="api_v1_demo_chunk",
    ),
    path(
        "api/v1/demo/<int:batch_id>/analysis/",
        PublicDemoAnalysisView.as_view(),
        name="api_v1_demo_analysis",
    ),
    path(
        "api/v1/demo/<int:batch_id>/finalize/",
        PublicDemoFinalizeView.as_view(),
        name="api_v1_demo_finalize",
    ),
    path("api/v1/mobile/login/", MobileLoginView.as_view(), name="mobile_login"),
    path("api/v1/mobile/logout/", MobileLogoutView.as_view(), name="mobile_logout"),
    path("api/v1/mobile/devices/", MobileDevicesView.as_view(), name="mobile_devices"),
    path(
        "api/v1/mobile/devices/<int:device_id>/latest-analysis/",
        MobileDeviceLatestAnalysisView.as_view(),
        name="mobile_device_latest_analysis",
    ),
]
