from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

from audio_analytics import api_v1, device_views

urlpatterns = [
    path("admin/", admin.site.urls),
    # Public landing page.
    path(
        "",
        TemplateView.as_view(template_name="audio_analytics/home.html"),
        name="home",
    ),
    # Browser authentication.
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="audio_analytics/login.html"),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(next_page="home"),
        name="logout",
    ),
    # App routes.
    path("", include("audio_analytics.urls")),
    path(
        "privacy/",
        TemplateView.as_view(template_name="audio_analytics/privacy_policy.html"),
        name="privacy_policy",
    ),
    # Keep the old landing-page URL working.
    path(
        "home/",
        TemplateView.as_view(template_name="audio_analytics/home.html"),
        name="legacy_home",
    ),
    # Device / ESP32 / mobile APIs.
    path(
        "api/v1/sessions/",
        api_v1.SessionInitView.as_view(),
        name="api_v1_session_init",
    ),
    path(
        "api/v1/sessions/<int:batch_id>/chunks/",
        api_v1.SessionChunkView.as_view(),
        name="api_v1_session_chunk",
    ),
    path(
        "api/v1/sessions/<int:batch_id>/finalize/",
        api_v1.SessionFinalizeView.as_view(),
        name="api_v1_session_finalize",
    ),
    path(
        "api/v1/sessions/<int:batch_id>/heartbeat/",
        api_v1.SessionHeartbeatView.as_view(),
        name="api_v1_session_heartbeat",
    ),
    path(
        "api/v1/latest-analysis/",
        api_v1.LatestDeviceAnalysisView.as_view(),
        name="api_v1_latest_analysis",
    ),
    path(
        "devices/",
        device_views.device_tokens_view,
        name="device_tokens",
    ),
    path(
        "devices/<int:device_id>/delete/",
        device_views.delete_device_view,
        name="delete_device",
    ),
]
