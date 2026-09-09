import os
import tempfile
from functools import lru_cache

from faster_whisper import WhisperModel


MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
CPU_THREADS = int(os.getenv("WHISPER_CPU_THREADS", "4"))
NUM_WORKERS = int(os.getenv("WHISPER_NUM_WORKERS", "1"))


@lru_cache(maxsize=1)
def get_whisper_model():
    return WhisperModel(
        MODEL_SIZE,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        cpu_threads=CPU_THREADS,
        num_workers=NUM_WORKERS,
    )


def transcribe_audio(audio_bytes: bytes, filename: str) -> dict:
    suffix = os.path.splitext(filename)[1].lower() or ".wav"
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name

        segments, info = get_whisper_model().transcribe(
            temp_path,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            vad_filter=True,
            condition_on_previous_text=False,
        )

        items = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                items.append({
                    "start": round(float(segment.start), 2),
                    "end": round(float(segment.end), 2),
                    "text": text,
                })

        return {
            "text": " ".join(item["text"] for item in items),
            "language": info.language or "",
            "language_probability": (
                round(float(info.language_probability), 4)
                if info.language_probability is not None
                else None
            ),
            "segments": items,
        }
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass
