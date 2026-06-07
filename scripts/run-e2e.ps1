# 在 iap_frontend 容器内运行 Playwright E2E
param(
    [string]$EnvName = "dev"
)

$Container = "iap_frontend_$EnvName"
$E2EBase = if ($env:E2E_BASE_URL) { $env:E2E_BASE_URL } else { "http://127.0.0.1:8501" }
$E2EBackend = if ($env:E2E_BACKEND_URL) { $env:E2E_BACKEND_URL } else { "http://iap_backend:44000" }
$E2EPass = if ($env:E2E_PASSWORD) { $env:E2E_PASSWORD } else { "E2eTestPass123!!" }
$E2EEmail = if ($env:E2E_EMAIL) { $env:E2E_EMAIL } else { "" }

docker exec `
  -e "E2E_BASE_URL=$E2EBase" `
  -e "E2E_BACKEND_URL=$E2EBackend" `
  -e "E2E_EMAIL=$E2EEmail" `
  -e "E2E_PASSWORD=$E2EPass" `
  $Container `
  bash -lc "cd /app/tests/e2e && pytest"
