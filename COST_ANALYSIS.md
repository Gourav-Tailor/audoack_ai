# Deliverable #7: Audio Analytics Cost Analysis

## Executive Summary

* **External Inference/API Cost:** **$0.0000 per audio minute**.
* **Estimated Compute Cost:** approximately **$0.00005 per processed audio minute** under the stated benchmark assumptions.
* **Trial Ceiling:** **$0.0030 per audio minute**.
* **Compliance:** The local inference architecture is comfortably below the trial ceiling under the stated assumptions.

This is a compute-cost estimate, not a cloud-provider billing statement. Actual cost depends on instance pricing, worker utilization, storage, network, concurrency, and idle capacity.

## 1. Architectural Cost Breakdown

| Pipeline Component | Technology | External API Cost | Estimated Compute / Min |
|---|---|---:|---:|
| **Audio preprocessing & noise analysis** | Librosa / SoundFile | $0.00 | ~$0.00001 |
| **Speech Emotion Recognition** | `superb/wav2vec2-base-superb-er` locally | $0.00 | ~$0.00004 |
| **Batch processing & persistence** | Django / Celery / PostgreSQL | $0.00 | Included in host |
| **Total estimated compute** | Local pipeline | **$0.00** | **~$0.00005/min** |

## 2. Compute Assumptions

The estimate assumes a standard cloud worker around **2 vCPU / 4 GB RAM**.

The latency benchmark is approximately **1.45 seconds of compute per 60 seconds of audio**, corresponding to an RTF of approximately **0.024x**.

A representative container cost of approximately **$0.0008 per wall-clock minute** implies substantial cost headroom when the worker is efficiently utilized. Real workloads also include queueing, idle time, storage, and operational overhead.

Therefore, **~$0.00005 per processed audio minute should be presented as an estimate**, not as a guaranteed invoice rate.

## 3. Comparison with Paid API Alternatives

| Approach | External Audio/API Dependency | Cost Position | $0.003/min Ceiling |
|---|---|---:|---|
| **AudoAck local pipeline** | None | **~$0.00005/min compute estimate** | **YES** |
| Cloud transcription + separate sentiment/LLM workflow | Yes | Provider/workload dependent | Must be measured |
| Hosted speech/AI APIs | Yes | Provider/model dependent | Must be measured |

The trial requires disclosure of any external paid API, including pricing, retention, and whether customer audio leaves AudoAck-controlled infrastructure. The current architecture avoids that dependency.

## 4. Privacy & Data Governance

Because inference runs locally inside AudoAck-controlled infrastructure:

* Production call audio does not need to be sent to an external inference API.
* Wav2Vec2 and acoustic processing execute inside the application infrastructure.
* This reduces third-party data-sharing and retention concerns.
* Any future external ASR, diarization, or LLM component must be evaluated against AudoAck data-handling requirements and explicitly disclosed.

The trial specifically requires production-call audio to remain confidential and not be uploaded to unapproved public services.
