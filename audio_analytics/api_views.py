# audio_analytics/api_views.py

import os
import tempfile
import traceback
import uuid
import zipfile

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from pydub import AudioSegment

from .analyzer import analyze_audio_clip
from .models import BatchUpload
from .tasks import process_batch_upload_task


@method_decorator(csrf_exempt, name="dispatch")
class LiveDemoAnalysisView(View):
    def post(self, request):
        audio_file = request.FILES.get("audio")
        if not audio_file:
            return JsonResponse({"error": "No audio data received."}, status=400)

        # Enforce maximum 10MB limit (~1-2 mins of audio)
        if audio_file.size > 10 * 1024 * 1024:
            return JsonResponse(
                {"error": "Audio file exceeds 1-minute max limit."}, status=400
            )

        try:
            filename = audio_file.name or "demo_recording.webm"
            audio_bytes = audio_file.read()

            # Analyze clip dynamically using your analyzer pipeline
            result = analyze_audio_clip(audio_bytes, filename)
            return JsonResponse({"success": True, "data": result})
        except Exception as e:
            return JsonResponse(
                {"error": f"Failed to analyze recording: {str(e)}"}, status=500
            )


class LongRecorderBatchView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse(
                {"error": "User authentication required. Please log in first."},
                status=401,
            )

        action = request.POST.get("action")

        try:
            # 1. Initialize Batch Session
            if action == "init":
                title = request.POST.get("title", "Long Live Recording")
                batch = BatchUpload.objects.create(
                    user=request.user, name=f"{title} (Live Stream)", status="recording"
                )
                return JsonResponse({"status": "success", "batch_id": batch.id})

            # 2. Upload and Convert Chunk
            # Keep the chunk local for low-latency conversion. The finalized
            # WAVs are persisted to MinIO by process_batch_upload_task.
            elif action == "upload_chunk":
                batch_id = request.POST.get("batch_id")
                chunk_index = request.POST.get("index")
                audio_file = request.FILES.get("chunk_data")

                try:
                    chunk_number = int(chunk_index)
                    if chunk_number < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    return JsonResponse(
                        {"error": "Invalid chunk index"},
                        status=400,
                    )

                if not batch_id or not audio_file:
                    return JsonResponse(
                        {"error": "Missing batch_id or audio content"},
                        status=400,
                    )

                chunk_dir = os.path.join(
                    tempfile.gettempdir(), f"batch_stream_{batch_id}"
                )
                os.makedirs(chunk_dir, exist_ok=True)

                # Unique raw filename per upload (not just per chunk_index) so two
                # concurrent requests -- e.g. a duplicate POST from a network/proxy
                # retry -- can never collide on the same temp path and delete each
                # other's file mid-conversion.
                raw_path = os.path.join(
                    chunk_dir, f"temp_{chunk_number}_{uuid.uuid4().hex}.webm"
                )
                wav_path = os.path.join(chunk_dir, f"chunk_{chunk_number:04d}.wav")

                # Save raw browser chunk
                with open(raw_path, "wb+") as dst:
                    for chunk in audio_file.chunks():
                        dst.write(chunk)

                if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
                    return JsonResponse(
                        {
                            "error": f"Uploaded chunk {chunk_index} was empty or failed to save."
                        },
                        status=400,
                    )

                # Convert WebM/OGG to standard 16kHz Mono WAV via Pydub
                try:
                    sound = AudioSegment.from_file(raw_path)
                    sound = sound.set_frame_rate(16000).set_channels(1)
                    sound.export(wav_path, format="wav")
                except Exception as conversion_error:
                    if os.path.exists(raw_path):
                        os.remove(raw_path)
                    return JsonResponse(
                        {
                            "error": (
                                f"Failed to convert chunk {chunk_number}: "
                                f"{conversion_error}"
                            )
                        },
                        status=500,
                    )
                finally:
                    if os.path.exists(raw_path):
                        os.remove(raw_path)

                return JsonResponse(
                    {
                        "status": "chunk_saved",
                        "filename": f"chunk_{chunk_number:04d}.wav",
                    }
                )

            # 3. Finalize Session & Trigger Async Task
            elif action == "finalize":
                batch_id = request.POST.get("batch_id")
                if not batch_id:
                    return JsonResponse({"error": "Missing batch_id"}, status=400)

                batch = BatchUpload.objects.filter(
                    id=batch_id, user=request.user
                ).first()
                if not batch:
                    return JsonResponse(
                        {"error": "Batch session not found"}, status=404
                    )

                chunk_dir = os.path.join(
                    tempfile.gettempdir(), f"batch_stream_{batch_id}"
                )
                if not os.path.exists(chunk_dir):
                    return JsonResponse(
                        {"error": "No recorded audio chunks found on server"},
                        status=400,
                    )

                try:
                    expected_chunks = int(request.POST.get("expected_chunks", "0"))
                except (TypeError, ValueError):
                    return JsonResponse(
                        {"error": "Invalid expected chunk count"},
                        status=400,
                    )

                wav_files = sorted(
                    f for f in os.listdir(chunk_dir) if f.lower().endswith(".wav")
                )

                if expected_chunks > 0:
                    expected_files = {
                        f"chunk_{index:04d}.wav" for index in range(expected_chunks)
                    }
                    missing_files = sorted(expected_files - set(wav_files))

                    if missing_files:
                        return JsonResponse(
                            {
                                "error": "Not all audio chunks reached the server.",
                                "missing_chunks": missing_files,
                            },
                            status=409,
                        )

                zip_path = os.path.join(
                    tempfile.gettempdir(), f"batch_{batch_id}_audio.zip"
                )
                with zipfile.ZipFile(zip_path, "w") as zipf:
                    for root, _, files in os.walk(chunk_dir):
                        for file in sorted(files):
                            if file.endswith(".wav"):
                                file_path = os.path.join(root, file)
                                zipf.write(file_path, arcname=file)

                # Clean temp directory
                for f in os.listdir(chunk_dir):
                    os.remove(os.path.join(chunk_dir, f))
                os.rmdir(chunk_dir)

                batch.status = "pending"
                batch.save()

                process_batch_upload_task.delay(batch.id, zip_path)

                return JsonResponse(
                    {"status": "queued", "redirect_url": f"/batches/{batch.id}/"}
                )

            return JsonResponse({"error": "Invalid action requested"}, status=400)

        except Exception as e:
            # Print exact stacktrace in server logs for easy debugging
            traceback.print_exc()
            return JsonResponse(
                {"error": f"Internal Server Error: {str(e)}"}, status=500
            )
