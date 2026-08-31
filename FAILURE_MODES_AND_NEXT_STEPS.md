# Deliverable #9: Failure Modes, Limitations, and Production Roadmap

## 1. Observed Failure Modes & Root Causes

Testing of the labeled evaluation batch and review of the current pipeline indicate four important areas where predictions can degrade:

### A. Short Audio Clips & Extreme Truncation (< 1.5 seconds)
* **Symptom:** Emotion predictions can become unstable or low-confidence on very short utterances such as "Yeah" or "Okay".
* **Root Cause:** The Wav2Vec2 emotion-recognition model benefits from sufficient temporal context. Very short clips provide limited speech information for reliable classification.
* **Mitigation:** Treat very short clips as low-confidence cases and consider routing them for review rather than forcing a high-confidence emotion label.

### B. High Acoustic Noise & Cross-Talk
* **Symptom:** Loud overlapping speech or strong background sound can be interpreted as increased emotional intensity or frustration.
* **Root Cause:** Acoustic features such as energy and pitch can rise because of another speaker or environmental sound. The current pipeline does not perform full speaker diarization before emotion classification.
* **Mitigation:** Add speaker diarization/customer-channel isolation and explicitly separate speech activity from background events.

### C. Background Music & Non-Speech Harmonics
* **Symptom:** Hold music, television audio, or other harmonic sounds can affect noise detection and severity estimation.
* **Root Cause:** Spectral features such as spectral centroid and energy are useful but are not sufficient to reliably distinguish every environmental sound from speech.
* **Mitigation:** Add dedicated acoustic-event/noise classification and validate against representative production noise categories.

### D. Subdued, Ambiguous, or Sarcastic Tone
* **Symptom:** Linguistically positive phrases can be acoustically neutral or sarcastic and may therefore be assigned to `neutral` or `satisfied`.
* **Root Cause:** The current emotion classifier is primarily acoustic. It does not use transcript semantics.
* **Mitigation:** Add an optional ASR + lightweight text sentiment/context layer and compare it against the acoustic-only baseline.

## 2. Current System Limitations

1. **Small labeled validation set:** The current provided validation example contains only three files. Batch #23 achieved an emotional-tone macro F1 of **0.3333** and noise-detection accuracy of **66.67%**. These numbers are preliminary and do not establish hidden-set generalization.
2. **Acoustic-only emotion model:** The current tone classifier uses Wav2Vec2-based speech emotion recognition and does not incorporate transcript semantics.
3. **Mono / mixed-channel processing:** The current pipeline does not perform customer/agent channel separation before tone analysis.
4. **Heuristic overlap and silence detection:** Speaker overlap and long-silence detection use acoustic/energy heuristics rather than neural diarization.
5. **Language coverage:** The selected Wav2Vec2 emotion-recognition model is primarily suited to English speech patterns; multilingual or code-switched production audio requires additional validation.
6. **Confidence calibration:** Current confidence should be treated as model certainty rather than a fully calibrated probability until calibration is validated on a sufficiently large held-out dataset.
7. **Limited evidence for per-class performance:** With only three labeled clips, class-level F1 estimates are unstable and several target classes are not represented.

## 3. Production Roadmap

### Short-Term Improvements (1–2 Weeks)

* **Improve validation:** Expand the labeled evaluation set and use grouped/leave-one-call-out validation to prevent speaker or call leakage.
* **Diarization / speaker isolation:** Evaluate `pyannote.audio` or a lighter speaker-separation approach to isolate customer speech and improve overlap detection.
* **Confidence calibration:** Calibrate confidence scores on a held-out validation set and define a low-confidence review threshold.
* **Semantic context:** Add an ASR + lightweight text classifier as a second signal for ambiguous or sarcastic utterances.
* **Noise classifier:** Replace or supplement spectral heuristics with a dedicated acoustic-event/noise model for music, television, road noise, chatter, and mechanical sounds.

### Performance & Cost Optimization

* **ONNX / INT8 quantization:** Evaluate quantized Wav2Vec2 inference to reduce CPU and memory requirements while measuring any impact on classification quality.
* **Batch inference:** Where practical, batch model inference to reduce per-clip overhead.
* **Model caching:** Keep the model loaded once per worker to avoid repeated initialization overhead.

### Production Scaling

* **Asynchronous processing:** The current production architecture already uses **Celery + Redis** for background batch processing.
* **Shared/object storage:** For multi-worker or multi-node deployments, move temporary ZIP/audio storage from a shared local volume to object storage such as S3-compatible storage.
* **Active-learning review:** Route low-confidence or high-risk predictions to a human-review queue and use reviewed examples to improve the model.

## 4. Submission Position

The implementation demonstrates the required end-to-end workflow: hosted upload, asynchronous batch processing, per-file predictions, validation metrics, failure isolation, and structured results.

The main remaining risk is **model generalization**, not infrastructure. The provided three-file validation result is too small to support a strong accuracy claim, so the submission should emphasize validation methodology, cost/latency advantages, known limitations, and a concrete path to improving hidden-set performance.
