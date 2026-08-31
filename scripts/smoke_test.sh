#!/usr/bin/env sh
set -eu

BASE_URL="${BASE_URL:-http://127.0.0.1}"

echo "== HTTP =="
curl -fsS "$BASE_URL/healthz" || true
echo
echo "== HOME =="
curl -fsS -o /dev/null -w "HTTP %{http_code}\n" "$BASE_URL/"
echo "== PUBLIC DEMO START =="
curl -fsS -X POST "$BASE_URL/api/v1/demo/start/"
echo
