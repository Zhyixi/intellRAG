#!/usr/bin/env bash
# 在 iap_frontend 容器内运行 Playwright E2E
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV="${ENV:-dev}"
CONTAINER="iap_frontend_${ENV}"

docker exec \
  -e E2E_BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:8501}" \
  -e E2E_BACKEND_URL="${E2E_BACKEND_URL:-http://iap_backend:44000}" \
  -e E2E_EMAIL="${E2E_EMAIL:-}" \
  -e E2E_PASSWORD="${E2E_PASSWORD:-E2eTestPass123!!}" \
  "${CONTAINER}" \
  bash -lc "cd /app/tests/e2e && pytest"
