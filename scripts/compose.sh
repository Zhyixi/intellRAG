#!/usr/bin/env bash
# 從指定 env 檔讀取 proxy，供 docker compose build/pull 使用（BuildKit 讀取 HTTP_PROXY/HTTPS_PROXY）
# 用法: compose.sh <dev|prod> [docker compose 子命令與參數...]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  echo "用法: $(basename "$0") <dev|prod> [docker compose 參數...]" >&2
  echo "範例: $(basename "$0") dev up -d" >&2
  echo "      $(basename "$0") prod build" >&2
  exit 1
}

[[ $# -ge 1 ]] || usage

ENV_NAME="$1"
shift

case "${ENV_NAME}" in
  dev)
    ENV_FILE="${ROOT_DIR}/dev.env"
    ;;
  prod)
    ENV_FILE="${ROOT_DIR}/prod.env"
    ;;
  *)
    echo "未知環境: ${ENV_NAME}（請使用 dev 或 prod）" >&2
    usage
    ;;
esac

if [[ ! -f "${ENV_FILE}" ]]; then
  EXAMPLE_FILE="${ROOT_DIR}/${ENV_NAME}.env.example"
  echo "找不到 ${ENV_FILE}" >&2
  if [[ -f "${EXAMPLE_FILE}" ]]; then
    echo "請複製範本: cp ${EXAMPLE_FILE} ${ENV_FILE}" >&2
  fi
  exit 1
fi

_load_env_var() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    return 0
  fi
  local val="${line#*=}"
  val="${val%\"}"
  val="${val#\"}"
  printf '%s' "${val}"
}

http_proxy="$(_load_env_var http_proxy)"
https_proxy="$(_load_env_var https_proxy)"
no_proxy="$(_load_env_var no_proxy)"

export http_proxy="${http_proxy}"
export https_proxy="${https_proxy:-${http_proxy}}"
export HTTP_PROXY="${http_proxy}"
export HTTPS_PROXY="${https_proxy:-${http_proxy}}"
export no_proxy="${no_proxy:-localhost,127.0.0.1,::1}"
export NO_PROXY="${no_proxy}"

ENV_BASENAME="$(basename "${ENV_FILE}")"
echo "環境: ${ENV_NAME} (${ENV_BASENAME})"
echo "使用 proxy: HTTP_PROXY=${HTTP_PROXY:-<未設定>}"
echo "執行: docker compose --env-file ${ENV_BASENAME} $*"
cd "${ROOT_DIR}"
exec docker compose --env-file "${ENV_FILE}" "$@"
