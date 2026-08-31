# AudoAck AI

Production deployment of AudoAck AI with a high-concurrency Go gateway in front of the existing Django + Celery + Python ML pipeline.

## Runtime architecture

- **Nginx**: public edge, static files and routing.
- **Go gateway**: device-token audio/session hot path; direct PostgreSQL + Redis access; ffmpeg conversion; Celery enqueue.
- **Django**: web UI, authentication, billing, admin, mobile APIs, and canonical `api_v1` implementation.
- **Celery**: asynchronous ML processing.
- **PostgreSQL**: application state.
- **Redis**: Celery broker/backend.
- **MinIO**: durable audio/object storage.
- **Python ML**: Wav2Vec2 + librosa analysis.

The public demo and browser long recorder continue to use `audio_analytics/api_v1.py`. Device-token requests are accelerated by the Go gateway; requests without a device token are passed through to Django so browser session authentication remains compatible.

## Local test

```bash
cp .env.example .env
# Edit .env

docker compose build
docker compose up -d

docker compose ps
curl http://localhost/healthz
curl http://localhost/
```

First ML inference downloads the Hugging Face model into the persistent `hf_cache` volume.

## VM deployment

See `DEPLOY.md`.

## Security

Never commit `.env`. Rotate any credentials that have ever been exposed outside the deployment environment.
