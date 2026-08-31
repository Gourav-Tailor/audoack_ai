# AudoAck AI VM deployment

## 1. Prepare

Copy this project to the VM. Create `.env` from `.env.example`:

```bash
cp .env.example .env
nano .env
```

Set a real `SECRET_KEY`, database password, MinIO password, Hugging Face token and Razorpay secrets.

If this project replaces the old `autoace_ai` deployment **on the same VM**, leave:

```text
POSTGRES_VOLUME=autoace_ai_postgres_data
MINIO_VOLUME=autoace_ai_minio_data
```

Those names preserve the old Compose project's persistent data.

Do not run `docker compose down -v`.

## 2. Pull/build

For GHCR images:

```bash
docker compose pull
docker compose up -d
```

For a local build/test:

```bash
docker compose up -d --build
```

## 3. Verify

```bash
docker compose ps
docker compose logs --tail=100 gateway
docker compose logs --tail=100 web
docker compose logs --tail=100 celery_worker

curl http://127.0.0.1/healthz
```

Expected health response:

```json
{"status":"ok"}
```

Then test:

- `/`
- `/login/`
- `/demo/`
- `/long-recorder/`
- `/api/v1/demo/start/`
- device-token session API
- mobile login
- batch upload
- billing if Razorpay is configured.

## 4. Existing data

The Compose file intentionally defaults to the old PostgreSQL and MinIO volume names:

- `autoace_ai_postgres_data`
- `autoace_ai_minio_data`

The MinIO bucket default remains `autoace-media` for compatibility with existing stored objects.

If your old VM uses different volume names, inspect them first:

```bash
docker volume ls
docker volume inspect <volume-name>
```

Then set `POSTGRES_VOLUME` and `MINIO_VOLUME` in `.env`.

## 5. Cutover from autoace_ai

From the directory containing the new project:

```bash
docker compose down
docker compose pull
docker compose up -d
```

`down` without `-v` does not remove persistent volumes.

Do not run:

```bash
docker compose down -v
```

during the cutover.

## 6. GHCR

The included GitHub Actions workflow publishes:

```text
ghcr.io/gourav-tailor/audoack_ai:latest
ghcr.io/gourav-tailor/audoack_ai-gateway:latest
```

If the GHCR packages are private, authenticate on the VM before `docker compose pull`:

```bash
echo "$CR_PAT" | docker login ghcr.io -u Gourav-Tailor --password-stdin
```

For public packages, no registry login is required.

## 7. Rollback

Keep the previous project/Compose file available.

To roll back, restore the old Compose/application files and run:

```bash
docker compose up -d
```

Do not delete the PostgreSQL or MinIO volumes.

## 8. Important production tuning

Start with:

```text
DJANGO_WORKERS=3
CELERY_CONCURRENCY=1
GATEWAY_DB_MAX_CONNS=20
```

Increase Celery concurrency only if the VM has enough CPU/RAM for the ML model. ML inference, not HTTP handling, is expected to dominate CPU/RAM usage.

The Go gateway is deliberately lightweight and keeps audio bytes out of Redis and PostgreSQL. Audio is converted into the shared `/tmp` volume and the Celery task receives only the batch ID, path and filename.

## 9. API compatibility

Device clients continue to use:

```text
Authorization: Token <device_key>
```

The gateway implements the high-volume device-token paths:

```text
POST /api/v1/sessions/
POST /api/v1/sessions/{batch_id}/chunks/
POST /api/v1/sessions/{batch_id}/finalize/
POST /api/v1/sessions/{batch_id}/heartbeat/
GET  /api/v1/latest-analysis/
```

Requests without a device token are proxied to Django's canonical `api_v1` views. This preserves browser session authentication and the public demo flow.
