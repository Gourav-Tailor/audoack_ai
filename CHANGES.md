# AudoAck deployment changes

This package contains the deployment files used for the working GCP VM configuration.

Changes captured:
- Django Gunicorn explicitly binds to 0.0.0.0:8000.
- Django Compose startup uses a single shell command with `exec gunicorn`.
- Nginx `/healthz` routes to the Go gateway.
- Go gateway source and build configuration are included.
- Shared `/tmp` volume is retained for gateway/Celery audio processing.
- No `.env`, passwords, API keys, or other secrets are included.
- `gateway.exe` is intentionally excluded; it is not required for Linux Docker deployment.
