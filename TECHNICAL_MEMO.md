# Technical Memo: Voice Tone & Background Noise Analytics System

## 1. Objective

The system analyzes production call audio for emotional tone and background noise while targeting:

* Strong performance on unseen evaluation audio.
* Compliance with the **$0.003 per audio-minute** cost ceiling.
* Production-suitable latency.
* Reproducible deployment.
* Confidential handling of production audio.
* A hosted batch-upload and result-review workflow.

The required output schema is implemented for emotional tone, emotional intensity, background noise, noise severity, audio quality, speaker overlap, long silence, and confidence.

## 2. Architecture & Approach Selection

We selected a hybrid local-processing architecture to balance model capability, cost, latency, and privacy.

### Speech Emotion Recognition

`superb/wav2vec2-base-superb-er` is used through Hugging Face Transformers for:

* `emotional_tone`
* `emotional_intensity`
* `confidence`

Target tone classes:

`neutral | satisfied | frustrated | upset | distressed`

### Acoustic / Noise Analysis

Librosa and signal-processing features are used for:

* Signal-to-noise characteristics.
* RMS energy.
* Spectral features.
* Zero-crossing rate.
* Background-noise presence/type/severity.
* Audio-quality indicators.
* Silence detection.
* Acoustic overlap heuristics.

The design avoids paid external inference APIs and keeps production audio within AudoAck-controlled infrastructure.

## 3. Validation Results

The current provided labeled validation batch contains only **three audio files**. Batch #23 produced:

* **Emotional Tone Macro F1:** **0.3333**
* **Noise Detection Accuracy:** **66.67%**
* **Emotional-tone predictions:** 2 of 3 clips matched the supplied labels.

The confusion matrix was:

| Actual | Predicted |
|---|---|
| Neutral | Upset |
| Satisfied | Satisfied |
| Upset | Upset |

### Interpretation

The validation/reporting pipeline is functioning, but three files are insufficient to make a strong generalization claim. Macro F1 is especially unstable when only a few examples are available and several target classes have no examples.

The hidden evaluation set is therefore the decisive accuracy test.

The submission should report the measured three-file result transparently rather than replacing it with unsupported larger validation metrics.

## 4. Production Workflow

1. Evaluator uploads a ZIP containing audio files and the manifest.
2. Django validates the batch and creates the batch record.
3. The archive is made available to the worker through shared temporary storage.
4. Celery + Redis performs analysis asynchronously.
5. Each audio clip receives an independent `AudioAnalysis` result.
6. Individual failures are recorded without necessarily failing the complete batch.
7. Ground-truth labels are evaluated when supplied.
8. Batch-level metrics and confusion matrices are stored and displayed.
9. Structured results can be reviewed/downloaded from the dashboard.

This separates HTTP upload handling from CPU-heavy inference and supports batch workloads.

## 5. Cost

The pipeline uses no paid external inference APIs.

* **External inference cost:** $0.0000/min.
* **Estimated compute cost:** approximately **$0.00005 per processed audio minute** under the benchmark assumptions.
* **Trial ceiling:** $0.003/min.

The local architecture provides substantial cost headroom and reduces third-party data-transfer and retention concerns.

Actual production cost should be measured from the selected cloud instance price and real worker utilization.

## 6. Latency

On the benchmark CPU environment:

* **60-second clip:** ~1.45 seconds.
* **RTF:** ~0.024x.
* Wav2Vec2 inference accounts for approximately 74.5% of measured processing time.

The system therefore operates substantially faster than real time on the benchmark worker.

Celery + Redis allows clips to be processed asynchronously, while horizontal worker scaling can increase batch throughput.

## 7. Failure Modes & Limitations

### Short clips
Very short utterances provide insufficient temporal context and can produce unstable emotion predictions.

### Background noise and cross-talk
Loud environmental sound or another speaker can affect acoustic energy and therefore emotion/noise classification.

### Music and harmonic noise
Hold music and television audio can affect spectral features and noise-severity estimates.

### Sarcasm and linguistic context
An acoustic-only model can miss semantic contradictions such as a positive phrase delivered sarcastically.

### Overlap detection
Current overlap detection uses acoustic heuristics rather than full speaker diarization.

### Confidence calibration
Current confidence values indicate model uncertainty but should not be described as calibrated probabilities until calibration is validated on a larger held-out dataset.

### Validation size
The supplied labeled sample is only three clips. More grouped validation data is required before making strong accuracy/generalization claims.

## 8. Recommended Improvements

### Highest Priority

1. **Expand validation data** and use grouped or leave-one-call-out validation to prevent speaker/call leakage.
2. **Compare a second materially different approach**, as recommended by the trial—for example, acoustic features plus a lightweight classifier versus the current Wav2Vec2 approach.
3. **Calibrate confidence scores** on a held-out validation set.
4. **Add speaker diarization** to separate customer speech from agent/cross-talk.
5. **Add semantic context** through ASR plus a lightweight text classifier for ambiguous or sarcastic tone.

### Performance / Cost

1. Quantize Wav2Vec2 using ONNX/INT8 and benchmark accuracy versus latency.
2. Batch inference where practical.
3. Continue model caching at worker startup.
4. Scale Celery workers horizontally for large evaluation batches.

### Production Data Architecture

For the current single-server deployment, shared temporary storage is sufficient for batch processing. For multi-node deployment, move uploaded archives/audio to durable object storage and pass object keys to Celery rather than container-local filesystem paths.

## 9. Conclusion

The implementation demonstrates the required hosted workflow and a low-cost, low-latency local inference architecture.

The strongest submission position is **not to overclaim current model accuracy**. The provided three-file validation result is preliminary. The key opportunity is to improve generalization through stronger validation, a second model/feature baseline, confidence calibration, speaker separation, and semantic context.

This approach directly addresses the trial's priorities: hidden-set performance, cost efficiency, technical rigor, production practicality, privacy, and a working hosted dashboard.
