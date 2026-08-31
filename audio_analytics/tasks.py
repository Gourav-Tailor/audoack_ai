import csv
import io
import json
import os
import zipfile

from celery import shared_task
from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename

from .analyzer import analyze_audio_clip
from .evaluator import evaluate_predictions_against_ground_truth
from .models import AudioAnalysis, BatchUpload


def _archive_audio_to_minio(analysis, audio_data, filename, batch_id):
    """Persist an analyzed audio file through Django's MinIO storage backend."""
    safe_name = (
        get_valid_filename(os.path.basename(filename)) or f"audio_{analysis.pk}.wav"
    )
    storage_name = f"recordings/batches/{batch_id}/audio/{analysis.pk}_{safe_name}"
    analysis.audio_file.save(storage_name, ContentFile(audio_data), save=True)
    return analysis.audio_file.name


def _remove_temp_file(path):
    """Remove a temporary file and its empty parent directory when possible."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        return

    parent = os.path.dirname(path)
    if parent:
        try:
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except OSError:
            pass


@shared_task
def process_audio_chunk_task(batch_id, wav_path, filename):
    """
    Analyze one live audio chunk from the shared temporary filesystem.

    The local WAV is intentionally used for low-latency processing. After the
    analysis finishes, the same bytes are persisted to MinIO. Only after the
    MinIO save succeeds is the temporary local WAV deleted.
    """
    archive_succeeded = False
    try:
        batch = BatchUpload.objects.get(id=batch_id)

        existing = AudioAnalysis.objects.filter(
            batch=batch,
            filename=filename,
        ).first()

        if existing and existing.audio_file:
            return {
                "status": "already_processed",
                "batch_id": batch_id,
                "filename": filename,
                "audio_file": existing.audio_file.name,
            }

        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"Audio chunk not found: {wav_path}")

        with open(wav_path, "rb") as audio_file:
            audio_data = audio_file.read()

        result = analyze_audio_clip(audio_data, filename)
        if "error" in result:
            raise RuntimeError(result["error"])

        if existing:
            analysis = existing
            analysis.status = AudioAnalysis.ProcessingStatus.SUCCESS
            analysis.error_details = ""
            analysis.emotional_tone = result["emotional_tone"]
            analysis.emotional_intensity = result["emotional_intensity"]
            analysis.background_noise_present = result["background_noise_present"]
            analysis.background_noise_type = result["background_noise_type"]
            analysis.background_noise_severity = result["background_noise_severity"]
            analysis.audio_quality = result["audio_quality"]
            analysis.speaker_overlap_present = result["speaker_overlap_present"]
            analysis.long_silence_present = result["long_silence_present"]
            analysis.confidence = result["confidence"]
            analysis.save()
        else:
            analysis = AudioAnalysis.objects.create(
                batch=batch,
                filename=filename,
                status=AudioAnalysis.ProcessingStatus.SUCCESS,
                emotional_tone=result["emotional_tone"],
                emotional_intensity=result["emotional_intensity"],
                background_noise_present=result["background_noise_present"],
                background_noise_type=result["background_noise_type"],
                background_noise_severity=result["background_noise_severity"],
                audio_quality=result["audio_quality"],
                speaker_overlap_present=result["speaker_overlap_present"],
                long_silence_present=result["long_silence_present"],
                confidence=result["confidence"],
            )

        # Permanent archive happens BEFORE local cleanup.
        _archive_audio_to_minio(analysis, audio_data, filename, batch_id)
        archive_succeeded = True

        return {
            "status": "processed",
            "batch_id": batch_id,
            "filename": filename,
            "audio_file": analysis.audio_file.name,
        }

    except Exception as e:
        print(
            f"Chunk processing failed: batch={batch_id}, "
            f"filename={filename}, error={e}"
        )
        raise

    finally:
        # Never remove the only copy if MinIO archival failed.
        if archive_succeeded:
            _remove_temp_file(wav_path)


@shared_task
def process_batch_upload_task(batch_id, zip_file_path):
    """Process a temporary ZIP while retaining the uploaded audio in MinIO."""
    batch = None
    try:
        batch = BatchUpload.objects.get(id=batch_id)
        batch.status = BatchUpload.Status.PROCESSING
        batch.save()

        ground_truths = {}
        predictions = []

        with zipfile.ZipFile(zip_file_path, "r") as archive:
            file_list = archive.namelist()

            csv_filename = next(
                (
                    f
                    for f in file_list
                    if f.lower().endswith("labels.csv")
                    or f.lower().endswith("manifest.csv")
                ),
                None,
            )
            if csv_filename:
                with archive.open(csv_filename) as csv_file:
                    reader = csv.DictReader(
                        io.TextIOWrapper(csv_file, encoding="utf-8")
                    )
                    for row in reader:
                        filename = row.get("name", "").strip()
                        gt_json = row.get("result_json", "").strip()
                        if filename:
                            ground_truths[filename] = gt_json

            audio_members = [
                m
                for m in archive.infolist()
                if not m.is_dir()
                and not m.filename.startswith("__MACOSX")
                and "/." not in m.filename
                and os.path.basename(m.filename)
                .lower()
                .endswith((".wav", ".mp3", ".m4a", ".flac", ".ogg"))
            ]
            batch.total_files = len(audio_members)
            batch.processed_files = 0
            batch.failed_files = 0
            batch.save()

            for member in audio_members:
                filename = os.path.basename(member.filename)
                try:
                    audio_data = archive.read(member.filename)
                    result = analyze_audio_clip(audio_data, filename)

                    if "error" in result:
                        raise RuntimeError(result["error"])

                    analysis = AudioAnalysis.objects.create(
                        batch=batch,
                        filename=filename,
                        status=AudioAnalysis.ProcessingStatus.SUCCESS,
                        emotional_tone=result["emotional_tone"],
                        emotional_intensity=result["emotional_intensity"],
                        background_noise_present=result["background_noise_present"],
                        background_noise_type=result["background_noise_type"],
                        background_noise_severity=result["background_noise_severity"],
                        audio_quality=result["audio_quality"],
                        speaker_overlap_present=result["speaker_overlap_present"],
                        long_silence_present=result["long_silence_present"],
                        confidence=result["confidence"],
                    )

                    # Permanent audio archive. The original BatchUpload.zip_file
                    # is also stored in MinIO by the FileField backend.
                    _archive_audio_to_minio(
                        analysis,
                        audio_data,
                        filename,
                        batch.id,
                    )

                    result_with_name = dict(result)
                    result_with_name["filename"] = filename
                    predictions.append(result_with_name)
                    batch.processed_files += 1

                except Exception as clip_err:
                    batch.failed_files += 1
                    AudioAnalysis.objects.create(
                        batch=batch,
                        filename=filename,
                        status=AudioAnalysis.ProcessingStatus.FAILED,
                        error_details=str(clip_err),
                    )

                batch.save()

        if ground_truths and predictions:
            eval_results = evaluate_predictions_against_ground_truth(
                predictions,
                ground_truths,
            )
            if "error" not in eval_results:
                batch.metrics_json = json.dumps(eval_results)

        batch.status = BatchUpload.Status.COMPLETED
        batch.save()

    except Exception as e:
        if batch is not None:
            batch.status = BatchUpload.Status.FAILED
            batch.error_message = str(e)
            batch.save()
        raise
    finally:
        # This is only the processing copy. The original upload is retained
        # through BatchUpload.zip_file in MinIO.
        if os.path.exists(zip_file_path):
            try:
                os.remove(zip_file_path)
            except OSError:
                pass
