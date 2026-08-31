# AudoAck AI — GCP Deployment Summary

## Project & Instance

| Item | Value |
|---|---|
| GCP Project ID | `autoaceai` |
| VM name | `autoace-server` |
| Zone | `us-east1-b` |
| Machine type | `e2-standard-2` (2 vCPU, 8GB RAM) |
| OS | Ubuntu 22.04 LTS |
| Boot disk size | 30GB |
| External IP | **Static, reserved**: `34.148.248.202` (won't change on stop/start) |
| Internal IP | `10.142.0.2` |
| SSH | `gcloud compute ssh autoace-server --zone=us-east1-b` |

## Networking

- **Current public entry point:** TCP port `80` through Nginx reverse proxy
- Firewall rule `allow-80` — opens TCP port 80 from `0.0.0.0/0`, targets VM tag `http-server`
- Legacy firewall rule `allow-8000` — should be **disabled**, not deleted initially, to provide an easy rollback path
- VM has tags: `http-server`, `https-server`
- Nginx listens on port `80` and proxies requests to the Django/Gunicorn `web` service on internal Docker port `8000`
- App accessible at: `http://34.148.248.202`
- Django admin: `http://34.148.248.202/admin`
- Direct public access to `:8000` should no longer be required once Nginx is verified
- Static IP reservation: `autoace-static-ip` (region `us-east1`)
- **HTTPS is not configured yet:** the VM already has the `https-server` tag, but there is currently no `tcp:443` firewall rule; add TLS/443 when a domain and HTTPS certificate are configured.

## Software stack on the VM

- Docker Engine + Compose plugin installed via Docker's official APT repo (not Ubuntu's default `docker.io` — that lacks `docker-compose-plugin`)
- User `gourav_tailor_ai` is in the `docker` group (no `sudo` needed for docker commands)
- 4GB swap file at `/swapfile`, enabled in `/etc/fstab`

## Application architecture

- **`db` service**: `postgres:15-alpine`, container name `autoace_postgres`, internal port 5432
- **`web` service**: Django + gunicorn, image `ghcr.io/gourav-tailor/audoack_ai:latest` (built via GitHub Actions on push to `main`, pushed to GHCR), container name `autoace_django`, internal port 8000
- **`nginx` service**: `nginx:alpine`, public port `80`, reverse-proxies requests to the `web` service on Docker port 8000
- **Public traffic path:** Internet → GCP firewall TCP 80 → Nginx → Django/Gunicorn
- Gunicorn config: `--workers 1 --timeout 300` (single worker deliberately, due to memory-heavy ML imports; long timeout for CPU-bound audio inference)
- App does audio emotion/quality analysis using `librosa` + Hugging Face `transformers` (`superb/wav2vec2-base-superb-er` model)

## Persistent Docker volumes (critical — do not delete)

| Volume | Purpose |
|---|---|
| `postgres_data` | Postgres database files |
| `media_data` | Uploaded audio files |
| `static_data` | Django collected static files |
| `hf_cache` → mounted at `/root/.cache/huggingface` | Caches the ML model so it isn't re-downloaded from Hugging Face on every container restart |

## Files on the VM (`~` = `/home/gourav_tailor_ai`)

- `~/docker-compose.yml`
- `~/.env` (contains `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DB_*`, `HF_TOKEN`)
- `~/analyzer.py` (dev copy for reference — the real one lives in the app image at `/app/audio_analytics/analyzer.py`)
- `~/diagnose_acoustic.py` — diagnostic script for checking real librosa-computed audio metrics against ground truth (run via `docker cp` into the container, then `docker compose exec web python diagnose_acoustic.py ...`)

## GCP Firewall Migration: Port 8000 → Nginx Port 80

Run these commands from Cloud Shell or any machine with the Google Cloud CLI configured. You do not need to SSH into the VM because firewall rules are project-level.

```bash
# Make sure you're using the correct project
gcloud config set project autoaceai

# Check existing HTTP/8000 firewall rules first.
gcloud compute firewall-rules list --filter="targetTags:http-server OR name~http OR name~8000"
```

If an existing rule such as `default-allow-http` already allows `tcp:80` for the `http-server` tag, do **not** create a duplicate rule.

Otherwise create the Nginx port-80 rule:

```bash
gcloud compute firewall-rules create allow-80   --project=autoaceai   --direction=INGRESS   --action=ALLOW   --rules=tcp:80   --source-ranges=0.0.0.0/0   --target-tags=http-server   --description="Allow HTTP traffic on port 80 for nginx reverse proxy"
```

After Nginx is confirmed working, disable the old public port-8000 rule:

```bash
gcloud compute firewall-rules update allow-8000 --disabled
```

Keep it disabled during the initial rollout rather than deleting it. This provides a quick rollback path if the Nginx deployment has an issue.

After several days of successful operation, the old rule can be permanently removed:

```bash
gcloud compute firewall-rules delete allow-8000 --quiet
```

Verify the final HTTP rule:

```bash
gcloud compute firewall-rules list --filter="targetTags:http-server"
```

Expected architecture:

    Internet
       |
       | TCP 80
       v
    GCP Firewall
       |
       v
    Nginx :80
       |
       | Docker network
       v
    Django/Gunicorn :8000

Port `8000` is the internal application port. Port `80` is the public entry point.

## Common operational commands

```bash
# SSH in
gcloud compute ssh autoace-server --zone=us-east1-b

# Start/stop everything
docker compose up -d
docker compose down          # (does NOT delete named volumes)

# Check status / logs
docker compose ps
docker compose logs -f web

# After pushing a new image to GHCR
docker compose pull
docker compose up -d --force-recreate web

# Clean up disk space (run periodically — image layers accumulate)
docker system prune -a -f     # safe: does not touch named volumes or running containers

# Django management commands
docker compose exec web python manage.py showmigrations
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

## Reverse Proxy Verification

After deploying the Nginx configuration and changing the firewall:

```bash
# Confirm containers and published ports
docker compose ps

# Confirm Nginx logs
docker compose logs -f nginx

# Confirm Django/Gunicorn logs
docker compose logs -f web

# Test the public HTTP endpoint from the VM
curl -I http://127.0.0.1/

# Test the public endpoint from another machine
curl -I http://34.148.248.202/
```

Expected public behavior:

- `http://34.148.248.202/` → Nginx → Django
- `http://34.148.248.202/admin` → Nginx → Django admin
- `http://34.148.248.202:8000` → no longer intended as a public entry point after `allow-8000` is disabled

## Known operational gotchas (already solved once — don't re-debug from scratch)

1. **Disk fills up (~95%) after repeated image pulls/rebuilds** — old image layers aren't auto-cleaned. Fix: `docker system prune -a -f`. Consider a daily cron job doing this, or resize disk to 50GB if iterating a lot.
2. **External IP changes on VM stop/start** unless a static IP is reserved — already fixed via `autoace-static-ip`, should not recur.
3. **Model re-downloads from Hugging Face on every container restart** unless `hf_cache` volume is mounted — already fixed, don't remove that volume.
4. **Long audio files (2+ min) can crash the worker (OOM-looking SIGKILL)** due to quadratic attention cost in the transformer model — already fixed via chunking in `predict_emotion` (20-second windows, averaged probabilities).
5. **`ALLOWED_HOSTS`** in `.env` must include whatever IP/domain you're accessing the app from, or Django returns "Invalid HTTP_HOST header."
6. **Nginx is the public entry point:** keep Django/Gunicorn on internal port 8000 and expose port 80 through Nginx. Do not re-enable public 8000 unless needed for rollback/debugging.

## Current known limitations (for project writeup, not necessarily "bugs" to fix)

- Emotional tone classification has weak accuracy on real call audio — the pretrained model (`wav2vec2-base-superb-er`) was trained on acted/studio speech (IEMOCAP), a domain mismatch from real phone/call-center recordings. Threshold-based corrections were applied where justified by mechanism (anger-label mapping bug, missing amplitude normalization); further improvement would need model fine-tuning or more labeled data, not more threshold tuning.
- `speaker_overlap_present` is unreliable — real overlap detection needs speaker diarization, which isn't implemented; left conservative rather than faked.
- `long_silence_present` threshold (4.0s continuous near-zero energy) is a reasonable default, not calibrated against enough examples to be certain it matches the intended definition.
- Only 3 labeled ground-truth examples were available total (from the reviewer's `sample.zip`) — validation conclusions from this sample should be treated as indicative, not statistically reliable.
