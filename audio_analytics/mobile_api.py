from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .mobile_authentication import MobileBearerAuthentication
from .models import AudioAnalysis, Device, MobileAuthToken


def _get_token(request):
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


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

        auth_token = MobileAuthToken.issue(user)

        devices = Device.objects.filter(user=user).order_by("-created_at")

        return Response(
            {
                "success": True,
                "token": auth_token.token,
                "devices": [
                    _serialize_device(device, include_key=False) for device in devices
                ],
            },
            status=status.HTTP_200_OK,
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

        return Response({"success": True})


class MobileDevicesView(APIView):
    authentication_classes = [MobileBearerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        devices = Device.objects.filter(user=request.user).order_by("-created_at")

        return Response(
            {
                "devices": [
                    _serialize_device(device, include_key=False) for device in devices
                ]
            }
        )


class MobileDeviceLatestAnalysisView(APIView):
    authentication_classes = [MobileBearerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, device_id):
        device = Device.objects.filter(
            id=device_id,
            user=request.user,
        ).first()

        if device is None:
            return Response(
                {"detail": "Device not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        batch = device.batches.order_by("-id").first()

        if batch is None:
            return Response(
                {
                    "status": "no_recording",
                    "device": _serialize_device(device, include_key=False),
                    "batch_id": None,
                    "batch_status": None,
                    "latestAnalysis": None,
                },
                status=status.HTTP_200_OK,
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
                "latestAnalysis": (_serialize_analysis(analysis) if analysis else None),
            },
            status=status.HTTP_200_OK,
        )


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
        "id": analysis.id,
        "batch_id": analysis.batch_id,
        "filename": analysis.filename,
        "status": analysis.status,
        "created_at": (
            analysis.created_at.isoformat()
            if getattr(analysis, "created_at", None)
            else None
        ),
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
            if (
                field
                in {
                    "background_noise_present",
                    "speaker_overlap_present",
                    "long_silence_present",
                }
                and value is not None
            ):
                value = bool(value)
            elif field == "confidence" and value is not None:
                value = float(value)
            data[field] = value

    return data
