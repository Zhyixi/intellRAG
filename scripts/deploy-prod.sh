#!/usr/bin/env bash
# 正式環境一鍵部署：build 後 up -d
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="${SCRIPT_DIR}/compose-prod.sh"

echo "=== iap 正式環境部署 ==="

if [[ ! -f "${SCRIPT_DIR}/../prod.env" ]]; then
  echo "找不到 prod.env，請先設定正式環境變數。" >&2
  exit 1
fi

# --build：映像不存在或 Dockerfile / requirements.in 變更時自動建置並安裝依賴
"${COMPOSE}" up -d --build
"${COMPOSE}" ps

echo ""
echo "部署完成。常用指令："
echo "  ${COMPOSE} logs -f iap_backend"
echo "  ${COMPOSE} logs -f iap_frontend"
echo "  ${COMPOSE} down"
