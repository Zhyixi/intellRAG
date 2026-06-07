#!/usr/bin/env bash
# 正式環境 Docker Compose（讀 prod.env、帶公司 proxy）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/compose.sh" prod "$@"
