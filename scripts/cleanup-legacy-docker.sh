#!/usr/bin/env bash
# 停止並移除 ipa / intelligent_ai 更名前的 Compose 專案、容器與自訂映像
# 用法:
#   ./scripts/cleanup-legacy-docker.sh           # 預覽
#   ./scripts/cleanup-legacy-docker.sh --apply   # 執行清理
#   ./scripts/cleanup-legacy-docker.sh --apply --remove-images
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

APPLY=0
REMOVE_IMAGES=0
for arg in "$@"; do
  case "${arg}" in
    --apply) APPLY=1 ;;
    --remove-images) REMOVE_IMAGES=1 ;;
    -h|--help)
      echo "用法: $(basename "$0") [--apply] [--remove-images]"
      exit 0
      ;;
  esac
done

legacy_projects=(ipa_dev ipa_prod intellrag_dev intellrag_prod)
legacy_image_repos=(
  ipa_backend ipa_elasticsearch ipa_frontend
  intelligent_ai_elasticsearch intelligent_ai_backend intelligent_ai_frontend
)

echo "=== 舊 Docker 資源清理（ipa / intelligent_ai）==="
echo "專案目錄: ${ROOT_DIR}"
echo

echo "舊 Compose 專案:"
for proj in "${legacy_projects[@]}"; do
  if docker compose ls -q 2>/dev/null | grep -qx "${proj}"; then
    echo "  - ${proj}"
  fi
done

mapfile -t containers < <(docker ps -a --format '{{.Names}}' | grep -E '^(ipa_|intelligent_ai_)' || true)
if ((${#containers[@]})); then
  echo
  echo "舊容器 (${#containers[@]}):"
  printf '  - %s\n' "${containers[@]}"
fi

mapfile -t images < <(
  docker images --format '{{.Repository}}:{{.Tag}}' | while read -r line; do
    repo="${line%%:*}"
    for r in "${legacy_image_repos[@]}"; do
      [[ "${repo}" == "${r}" ]] && echo "${line}" && break
    done
  done
)

if ((${#images[@]})); then
  echo
  echo "舊自訂映像 (${#images[@]}):"
  printf '  - %s\n' "${images[@]}"
fi

if [[ "${APPLY}" -ne 1 ]]; then
  echo
  echo "預覽模式。執行: $(basename "$0") --apply [--remove-images]"
  exit 0
fi

for proj in "${legacy_projects[@]}"; do
  if docker compose ls -q 2>/dev/null | grep -qx "${proj}"; then
    echo "docker compose -p ${proj} down ..."
    docker compose -p "${proj}" -f docker-compose.yml down --remove-orphans 2>/dev/null \
      || docker compose -p "${proj}" down --remove-orphans 2>/dev/null \
      || true
  fi
done

for name in "${containers[@]}"; do
  echo "docker rm -f ${name}"
  docker rm -f "${name}" 2>/dev/null || true
done

if [[ "${REMOVE_IMAGES}" -eq 1 ]] && ((${#images[@]})); then
  for img in "${images[@]}"; do
    echo "docker rmi ${img}"
    docker rmi -f "${img}" 2>/dev/null || true
  done
fi

echo
echo "完成。請重建並啟動 iap 堆疊:"
echo "  ./scripts/compose-dev.sh up -d --build"
