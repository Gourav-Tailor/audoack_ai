from datetime import timedelta

from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .mobile_authentication import MobileBearerAuthentication
from .models import AudioAnalysis, Device, MobileAuthToken, MobileRefreshToken

ACCESS_TOKEN_TTL = timedelta(minutes=30)
REFRESH_TOKEN_TTL = timedelta(days=30)


class MobileLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        password = str(request.data.get("password", ""))

        if not username or not password:
            return Response(
                {"success": False, "error": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request=request, username=username, password=password)
        if user is None:
            return Response(
                {"success": False, "error": "Invalid username or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"success": False, "error": "User account is inactive."},
                status=status.HTTP_403_FORBIDDEN,
            )

        access_token, refresh_token = _issue_token_pair(user)
        devices = Device.objects.filter(user=user).order_by("-created_at")

        return Response(
            {
                "success": True,
                "token": access_token.token,
                "refresh_token": refresh_token.token,
                "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
                "refresh_expires_in": int(REFRESH_TOKEN_TTL.total_seconds()),
                "devices": [
                    _serialize_device(device, include_key=False)
                    for device in devices
                ],
            }
        )


class MobileRefreshView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        refresh_token_value = str(request.data.get("refresh_token", "")).strip()
        if not refresh_token_value:
            return Response(
                {"success": False, "error": "Refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            refresh_token = (
                MobileRefreshToken.objects.select_for_update()
                .select_related("user", "access_token")
                .filter(
                    token=refresh_token_value,
                    revoked_at__isnull=True,
                )
                .first()
            )

            if refresh_token is None or refresh_token.is_expired():
                if refresh_token is not None:
                    refresh_token.revoked_at = timezone.now()
                    refresh_token.save(update_fields=["revoked_at"])
                return Response(
                    {"success": False, "error": "Refresh token expired or revoked."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            old_access = refresh_token.access_token
            now = timezone.now()
            old_access.revoked = True
            old_access.revoked_at = now
            old_access.save(update_fields=["revoked", "revoked_at"])

            refresh_token.revoked_at = now
            refresh_token.last_used_at = now
            refresh_token.save(update_fields=["revoked_at", "last_used_at"])

            access_token, new_refresh = _issue_token_pair(refresh_token.user)

        return Response(
            {
                "success": True,
                "token": access_token.token,
                "refresh_token": new_refresh.token,
                "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
                "refresh_expires_in": int(REFRESH_TOKEN_TTL.total_seconds()),
            }
        )


class MobileLogoutView(APIView):
    authentication_classes = [MobileBearerAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        MobileAuthToken.objects.filter(
            user=request.user,
            revoked=False,
        ).update(
            revoked=True,
            revoked_at=timezone.now(),
        )
        MobileRefreshToken.objects.filter(
            user=request.user,
            revoked_at__isnull=True,
        ).update(revoked_at=timezone.now())

        return Response({"success": True})


class MobileDevicesView(APIView):
    authentication_classes = [MobileBearerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _touch_access_token(request)
        devices = Device.objects.filter(user=request.user).order_by("-created_at")
        return Response(
            {
                "devices": [
                    _serialize_device(device, include_key=False)
                    for device in devices
                ]
            }
        )


class MobileDeviceLatestAnalysisView(APIView):
    authentication_classes = [MobileBearerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, device_id):
        _touch_access_token(request)
        device = Device.objects.filter(id=device_id, user=request.user).first()
        if device is None:
            return Response({"detail": "Device not found."}, status=404)

        batch = device.batches.order_by("-id").first()
        if batch is None:
            return Response(
                {
                    "status": "no_recording",
                    "device": _serialize_device(device, include_key=False),
                    "batch_id": None,
                    "batch_status": None,
                    "latestAnalysis": None,
                }
            )

        analysis = (
            AudioAnalysis.objects.filter(
                batch=batch,
                status=AudioAnalysis.ProcessingStatus.SUCCESS,
            )
            .order_by("-id")
            .first()
        )

        return Response(
            {
                "status": "success" if analysis else "processing",
                "device": _serialize_device(device, include_key=False),
                "batch_id": batch.id,
                "batch_status": batch.status,
                "latestAnalysis": _serialize_analysis(analysis) if analysis else None,
            }
        )


class MobileDeviceTranscriptsView(APIView):
    authentication_classes = [MobileBearerAuthentication]
    permission_classes = [IsAuthenticated]
    PAGE_SIZE = 100

    def get(self, request, device_id):
        _touch_access_token(request)
        device = Device.objects.filter(id=device_id, user=request.user).first()
        if device is None:
            return Response({"detail": "Device not found."}, status=404)

        try:
            after_id = int(request.query_params.get("after_id", "0"))
        except (TypeError, ValueError):
            return Response({"detail": "after_id must be a non-negative integer."}, status=400)
        if after_id < 0:
            return Response({"detail": "after_id must be a non-negative integer."}, status=400)

        batch = device.batches.order_by("-id").first()
        if batch is None:
            return Response(
                {
                    "status": "no_recording",
                    "device_id": device.id,
                    "batch_id": None,
                    "batch_status": None,
                    "items": [],
                    "latest_id": after_id,
                    "has_more": False,
                }
            )

        queryset = (
            AudioAnalysis.objects.filter(
                batch=batch,
                status=AudioAnalysis.ProcessingStatus.SUCCESS,
                id__gt=after_id,
            )
            .order_by("id")
        )
        rows = list(queryset[: self.PAGE_SIZE + 1])
        has_more = len(rows) > self.PAGE_SIZE
        rows = rows[: self.PAGE_SIZE]
        items = [_serialize_transcript(analysis) for analysis in rows]
        latest_id = rows[-1].id if rows else after_id

        return Response(
            {
                "status": "success",
                "device_id": device.id,
                "batch_id": batch.id,
                "batch_status": batch.status,
                "items": items,
                "latest_id": latest_id,
                "has_more": has_more,
            }
        )


def _issue_token_pair(user):
    now = timezone.now()
    access_token = MobileAuthToken.issue(user)
    access_token.last_used_at = now
    access_token.save(update_fields=["last_used_at"])
    refresh_token = MobileRefreshToken.issue(
        user=user,
        access_token=access_token,
        expires_at=now + REFRESH_TOKEN_TTL,
    )
    return access_token, refresh_token


def _touch_access_token(request):
    if isinstance(request.auth, MobileAuthToken):
        now = timezone.now()
        request.auth.last_used_at = now
        request.auth.save(update_fields=["last_used_at"])


def _serialize_device(device, include_key=False):
    data = {
        "id": device.id,
        "name": device.name,
        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
        "created_at": device.created_at.isoformat(),
    }
    if include_key:
        data["key"] = device.key
    return data


def _serialize_analysis(analysis):
    data = {
        "audio_playback_url": (
            analysis.audio_file.url
            if getattr(analysis, "audio_file", None) and analysis.audio_file
            else None
        ),
        "id": analysis.id,
        "batch_id": analysis.batch_id,
        "filename": analysis.filename,
        "status": analysis.status,
        "created_at": analysis.created_at.isoformat() if getattr(analysis, "created_at", None) else None,
    }
    for field in (
        "emotional_tone",
        "emotional_intensity",
        "background_noise_present",
        "background_noise_type",
        "background_noise_severity",
        "audio_quality",
        "speaker_overlap_present",
        "long_silence_present",
        "confidence",
    ):
        if hasattr(analysis, field):
            value = getattr(analysis, field)
            if field in {"background_noise_present", "speaker_overlap_present", "long_silence_present"} and value is not None:
                value = bool(value)
            elif field == "confidence" and value is not None:
                value = float(value)
            data[field] = value
    return data


def _serialize_transcript(analysis):
    return {
        "id": analysis.id,
        "batch_id": analysis.batch_id,
        "filename": analysis.filename,
        "transcript": analysis.transcript or "",
        "language": analysis.transcription_language or "",
        "confidence": (
            float(analysis.transcription_confidence)
            if analysis.transcription_confidence is not None
            else None
        ),
        "segments": analysis.transcript_segments or [],
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
    }
