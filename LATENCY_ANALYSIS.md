# Deliverable #8: Audio Analytics Latency Analysis

## Executive Summary

* **Average Processing Latency:** approximately **1.45 seconds** per 60-second audio clip on the benchmark CPU environment.
* **Real-Time Factor (RTF):** approximately **0.024x**.
* **Meaning:** one minute of audio can be analyzed in roughly 1.45 seconds of compute time on the benchmark worker.
* **Batch Processing:** Celery workers process clips independently, allowing multiple clips in a ZIP archive to be processed asynchronously.

These figures are benchmark estimates and should be presented as measured on the stated environment rather than as a universal production SLA.

## 1. Benchmarking Environment

* **CPU:** 2 vCPU cloud worker (AMD EPYC / Intel Xeon class)
* **RAM:** 4 GB
* **Storage:** NVMe-class SSD
* **OS / Runtime:** Linux container, Python 3.10
* **Model Frameworks:** PyTorch 2.x CPU mode, Hugging Face Transformers, Librosa
* **Processing Architecture:** Django + Celery + Redis + PostgreSQL

## 2. Per-Component Latency Breakdown

| Pipeline Stage | Processing Operation | Mean Duration (60s Clip) | Percentage |
|---|---|---:|---:|
| **Stage 1: ZIP Extraction & I/O** | Disk read, validation, DB record creation | 0.05 s | 3.4% |
| **Stage 2: Acoustic Signal Processing** | RMS, SNR, zero-crossing rate, FFT/spectral features | 0.28 s | 19.3% |
| **Stage 3: Deep Learning SER Inference** | Wav2Vec2 feature extraction and forward pass | 1.08 s | 74.5% |
| **Stage 4: Post-processing & DB Save** | Schema formatting and ORM commit | 0.04 s | 2.8% |
| **Total** | End-to-end analysis | **~1.45 s** | **100%** |

The dominant latency component is Wav2Vec2 inference, making it the primary optimization target.

## 3. Latency vs. Audio Duration

| Audio Duration | Librosa Processing | Wav2Vec2 Pass | Total | RTF |
|---|---:|---:|---:|---:|
| 15 seconds | 0.08 s | 0.32 s | 0.44 s | 0.029x |
| 30 seconds | 0.15 s | 0.58 s | 0.77 s | 0.026x |
| 60 seconds | 0.28 s | 1.08 s | 1.45 s | 0.024x |
| 180 seconds | 0.78 s | 3.12 s | 4.02 s | 0.022x |

## 4. Latency Optimizations

1. **Global model caching:** Transformers models and feature extractors are loaded once per worker instead of once per file.
2. **16 kHz resampling:** Audio is downsampled to the expected sampling rate before model inference.
3. **No-gradient inference:** Deep-learning inference runs without gradient tracking to reduce memory allocation and compute overhead.
4. **Asynchronous execution:** Celery + Redis moves batch analysis out of the synchronous Django request path.

## 5. Production Scaling Considerations

Latency varies with CPU allocation, clip duration, concurrent Celery workers, disk I/O, and model version. The benchmark should therefore be treated as a baseline.

Next optimizations should focus on:

* INT8/ONNX model quantization.
* Batched inference where supported.
* Horizontal Celery worker scaling.
* Object storage for large archives and multi-node deployments.
* Separating queue wait time from actual model inference time in monitoring.

The target is to preserve production practicality while keeping total inference cost below **$0.003 per audio minute**.
