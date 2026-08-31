"""
Canonical audio-session API used by the public demo, long recorder and
external devices.

Auth:
- Web long recorder: Django session authentication.
- ESP32/Raspberry Pi: Authorization: Token <device_key>.
- Public demo: signed short-lived demo token returned by /api/v1/demo/start/.
"""

import os
import tempfile
import traceback
import uuid

from django.core import signing
from django.db import transaction
from django.utils import timezone
from pydub import AudioSegment
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .device_authentication import DeviceAuthentication
from .models import AudioAnalysis, BatchUpload, Device
from .tasks import process_audio_chunk_task

DEMO_SALT = "autoace-public-demo-v1"
DEMO_MAX_SECONDS = 5.0
MAX_CHUNK_BYTES = 5 * 1024 * 1024


def _chunk_dir(batch_id):
    return os.path.join(tempfile.gettempdir(), f"batch_stream_{batch_id}")


def _touch_device(request):
    device = request.auth if isinstance(request.auth, Device) else None
    if device:
        device.last_seen = timezone.now()
        device.save(update_fields=["last_seen"])
    return device


def _user_batch(request, batch_id, recording_only=False):
    filters = {"id": batch_id, "user": request.user}
    device = request.auth if isinstance(request.auth, Device) else None
    if device:
        filters["device"] = device
    if recording_only:
        filters["status"] = "recording"
    return BatchUpload.objects.filter(**filters).first()


def _demo_batch_from_token(request):
    token = request.headers.get("X-Demo-Token")
    if not token:
        return None, "Missing demo token."
    try:
        data = signing.loads(
            token,
            salt=DEMO_SALT,
            max_age=15 * 60,
        )
        batch_id = int(data["batch_id"])
    except (
        signing.BadSignature,
        signing.SignatureExpired,
        KeyError,
        TypeError,
        ValueError,
    ):
        return None, "Invalid or expired demo token."

    batch = BatchUpload.objects.filter(
        id=batch_id,
        user__isnull=True,
        device__isnull=True,
        status="recording",
    ).first()
    if not batch:
        return None, "Demo session not found or expired."
    return batch, None


def _convert_chunk(audio_file, batch_id, chunk_index):
    chunk_dir = _chunk_dir(batch_id)
    os.makedirs(chunk_dir, exist_ok=True)

    raw_path = os.path.join(
        chunk_dir,
        f"temp_{chunk_index}_{uuid.uuid4().hex}.webm",
    )
    wav_path = os.path.join(chunk_dir, f"chunk_{chunk_index:04d}.wav")

    try:
        with open(raw_path, "wb") as dst:
            for chunk in audio_file.chunks():
                dst.write(chunk)

        if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
            raise ValueError("Uploaded audio chunk is empty.")

        sound = AudioSegment.from_file(raw_path)
        sound = sound.set_frame_rate(16000).set_channels(1)
        duration = float(sound.duration_seconds)

        sound.export(wav_path, format="wav")
        return wav_path, duration
    finally:
        if os.path.exists(raw_path):
            os.remove(raw_path)


def _reserve_chunk(batch, filename):
    with transaction.atomic():
        locked_batch = (
            BatchUpload.objects.select_for_update()
            .filter(
                id=batch.id,
                status="recording",
            )
            .first()
        )
        if not locked_batch:
            return None, "Recording session is no longer active."

        existing = AudioAnalysis.objects.filter(
            batch=locked_batch,
            filename=filename,
        ).first()
        if existing:
            return existing, False

        analysis = AudioAnalysis.objects.create(
            batch=locked_batch,
            filename=filename,
            status=AudioAnalysis.ProcessingStatus.PENDING,
        )
        return analysis, True


class SessionInitView(APIView):
    authentication_classes = [SessionAuthentication, DeviceAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        device = _touch_device(request)
        title = str(request.data.get("title") or "Long Live Recording")[:255]

        batch = BatchUpload.objects.create(
            user=request.user,
            name=f"{title} (Live Stream)",
            status="recording",
            device=device,
        )
        return Response(
            {"status": "success", "batch_id": batch.id},
            status=status.HTTP_201_CREATED,
        )


class SessionChunkView(APIView):
    authentication_classes = [SessionAuthentication, DeviceAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, batch_id):
        _touch_device(request)
        batch = _user_batch(request, batch_id, recording_only=True)
        if not batch:
            return Response(
                {"error": "Session not found or already finalized."},
                status=status.HTTP_404_NOT_FOUND,
            )

        chunk_index = request.data.get("index")
        audio_file = request.FILES.get("chunk_data")
        if chunk_index is None or not audio_file:
            return Response(
                {"error": "Missing index or chunk_data."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            chunk_index = int(chunk_index)
            if chunk_index < 0:
                raise ValueError
        except (TypeError, ValueError):
            return Response(
                {"error": "index must be a non-negative integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if audio_file.size > MAX_CHUNK_BYTES:
            return Response(
                {"error": "Audio chunk exceeds 5 MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        filename = f"chunk_{chunk_index:04d}.wav"

        existing = AudioAnalysis.objects.filter(
            batch=batch,
            filename=filename,
        ).first()
        if existing:
            return Response(
                {
                    "status": (
                        "chunk_queued" if not existing.audio_file else "chunk_saved"
                    ),
                    "filename": filename,
                    "index": chunk_index,
                    "duplicate": True,
                    "processing": not bool(existing.audio_file),
                },
                status=(
                    status.HTTP_202_ACCEPTED
                    if not existing.audio_file
                    else status.HTTP_200_OK
                ),
            )

        wav_path = None
        try:
            wav_path, _duration = _convert_chunk(
                audio_file,
                batch.id,
                chunk_index,
            )

            existing, created = _reserve_chunk(batch, filename)
            error = None
            if error:
                if wav_path and os.path.exists(wav_path):
                    os.remove(wav_path)
                return Response({"error": error}, status=409)

            # A concurrent request may have reserved this index. Only the
            # request that created the pending row should enqueue the task.
            if not created:
                if wav_path and os.path.exists(wav_path):
                    os.remove(wav_path)
                return Response(
                    {
                        "status": (
                            "chunk_saved" if existing.audio_file else "chunk_queued"
                        ),
                        "filename": filename,
                        "index": chunk_index,
                        "duplicate": True,
                    },
                    status=200,
                )

            process_audio_chunk_task.delay(
                batch.id,
                wav_path,
                filename,
            )
            wav_path = None

            return Response(
                {
                    "status": "chunk_queued",
                    "filename": filename,
                    "index": chunk_index,
                    "processing": True,
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as exc:
            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)
            traceback.print_exc()
            return Response(
                {"error": f"Failed to process chunk: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SessionFinalizeView(APIView):
    authentication_classes = [SessionAuthentication, DeviceAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, batch_id):
        _touch_device(request)

        with transaction.atomic():
            filters = {
                "id": batch_id,
                "user": request.user,
                "status": "recording",
            }
            device = request.auth if isinstance(request.auth, Device) else None
            if device:
                filters["device"] = device

            batch = BatchUpload.objects.select_for_update().filter(**filters).first()
            if not batch:
                return Response(
                    {"error": "Session not found or already finalized."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            batch.status = BatchUpload.Status.COMPLETED
            batch.save(update_fields=["status"])

        return Response(
            {
                "status": "completed",
                "batch_id": batch.id,
                "redirect_url": f"/batches/{batch.id}/",
            }
        )


class SessionHeartbeatView(APIView):
    authentication_classes = [SessionAuthentication, DeviceAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, batch_id):
        device = _touch_device(request)
        batch = _user_batch(request, batch_id, recording_only=True)
        if not batch:
            return Response(
                {"error": "Session not found or already finalized."},
                status=404,
            )
        return Response(
            {
                "status": "alive",
                "batch_status": batch.status,
                "device_id": device.id if device else None,
            }
        )


class LatestDeviceAnalysisView(APIView):
    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _touch_device(request)
        device = request.auth

        batch = (
            BatchUpload.objects.filter(
                user=request.user,
                device=device,
            )
            .order_by("-id")
            .first()
        )
        if not batch:
            return Response(
                {
                    "status": "no_recording",
                    "batch_id": None,
                    "batch_status": None,
                    "analysis": None,
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
        if not analysis:
            return Response(
                {
                    "status": "processing",
                    "batch_id": batch.id,
                    "batch_status": batch.status,
                    "analysis": None,
                }
            )

        return Response(
            {
                "status": "success",
                "batch_id": batch.id,
                "batch_status": batch.status,
                "analysis": self._serialize_analysis(analysis),
            }
        )

    @staticmethod
    def _serialize_analysis(analysis):
        fields = [
            "emotional_tone",
            "emotional_intensity",
            "background_noise_present",
            "background_noise_type",
            "background_noise_severity",
            "audio_quality",
            "speaker_overlap_present",
            "long_silence_present",
            "confidence",
        ]
        data = {
            "id": analysis.id,
            "batch_id": analysis.batch_id,
            "filename": analysis.filename,
            "status": analysis.status,
            "created_at": analysis.created_at.isoformat(),
        }
        for field in fields:
            data[field] = getattr(analysis, field, None)
        return data


class PublicDemoStartView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        batch = BatchUpload.objects.create(
            user=None,
            device=None,
            name="Public Demo (5s)",
            status="recording",
        )
        token = signing.dumps(
            {"batch_id": batch.id},
            salt=DEMO_SALT,
        )
        return Response(
            {
                "status": "success",
                "batch_id": batch.id,
                "demo_token": token,
                "max_seconds": DEMO_MAX_SECONDS,
            },
            status=status.HTTP_201_CREATED,
        )


class PublicDemoChunkView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, batch_id):
        batch, error = _demo_batch_from_token(request)
        if error or not batch or batch.id != batch_id:
            return Response(
                {"error": error or "Invalid demo session."},
                status=status.HTTP_404_NOT_FOUND,
            )

        audio_file = request.FILES.get("chunk_data")
        if not audio_file:
            return Response({"error": "Missing chunk_data."}, status=400)

        if audio_file.size > MAX_CHUNK_BYTES:
            return Response({"error": "Demo audio exceeds 5 MB."}, status=400)

        # Public demo is intentionally one chunk only and <= 5 seconds.
        if AudioAnalysis.objects.filter(batch=batch).exists():
            return Response({"error": "Demo recording already submitted."}, status=409)

        wav_path = None
        try:
            wav_path, duration = _convert_chunk(audio_file, batch.id, 0)
            if duration > DEMO_MAX_SECONDS + 0.25:
                os.remove(wav_path)
                return Response(
                    {"error": "Public demo is limited to 5 seconds of audio."},
                    status=400,
                )

            analysis, created = _reserve_chunk(batch, "chunk_0000.wav")
            if not created:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
                return Response(
                    {"error": "Demo recording already submitted."},
                    status=409,
                )

            process_audio_chunk_task.delay(
                batch.id,
                wav_path,
                "chunk_0000.wav",
            )
            wav_path = None

            return Response(
                {
                    "status": "processing",
                    "batch_id": batch.id,
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as exc:
            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)
            return Response(
                {"error": f"Failed to process demo audio: {exc}"},
                status=500,
            )


class PublicDemoAnalysisView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, batch_id):
        batch, error = _demo_batch_from_token(request)
        if error or not batch or batch.id != batch_id:
            return Response(
                {"error": error or "Invalid demo session."},
                status=404,
            )

        analysis = AudioAnalysis.objects.filter(batch=batch).order_by("-id").first()
        if not analysis:
            return Response({"status": "processing", "analysis": None})

        if analysis.status == AudioAnalysis.ProcessingStatus.FAILED:
            return Response(
                {"status": "failed", "error": analysis.error_details},
                status=500,
            )

        if analysis.status != AudioAnalysis.ProcessingStatus.SUCCESS:
            return Response({"status": "processing", "analysis": None})

        return Response(
            {
                "status": "success",
                "analysis": LatestDeviceAnalysisView._serialize_analysis(analysis),
            }
        )


class PublicDemoFinalizeView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, batch_id):
        batch, error = _demo_batch_from_token(request)
        if error or not batch or batch.id != batch_id:
            return Response(
                {"error": error or "Invalid demo session."},
                status=404,
            )
        with transaction.atomic():
            batch = BatchUpload.objects.select_for_update().get(id=batch.id)
            if batch.status == "recording":
                batch.status = BatchUpload.Status.COMPLETED
                batch.save(update_fields=["status"])
        return Response({"status": "completed", "batch_id": batch.id})
