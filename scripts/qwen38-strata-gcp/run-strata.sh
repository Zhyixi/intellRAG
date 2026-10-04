#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${STRATA_CONTAINER:-strata-qwen38}"
IMAGE="${STRATA_IMAGE:-ghcr.io/zhyixi/qwen38-strata:cuda13-sm86-sm89}"
VOLUME="${STRATA_VOLUME:-strata-data}"
BIND_ADDR="${BIND_ADDR:-127.0.0.1}"
PORT="${PORT:-8080}"
CONTEXT="${CONTEXT:-32768}"
API_KEY="${API_KEY:-}"

if [[ "$BIND_ADDR" != "127.0.0.1" && -z "$API_KEY" ]]; then
    echo "Refusing a non-loopback binding without API_KEY." >&2
    exit 1
fi

if sudo docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    sudo docker start "$CONTAINER"
    exit 0
fi

sudo docker volume create "$VOLUME" >/dev/null
sudo docker pull "$IMAGE"

args=(
    sudo docker run -d
    --name "$CONTAINER"
    --restart unless-stopped
    --gpus all
    --ulimit memlock=-1
    -p "$BIND_ADDR:$PORT:8080"
    -v "$VOLUME:/data"
    -e MODEL=IQ2_XS
    -e FAMILY=qwen
    -e "CONTEXT=$CONTEXT"
    -e VISION=no
    -e KV=int8
)

if [[ -n "$API_KEY" ]]; then
    args+=(-e "API_KEY=$API_KEY")
fi

args+=("$IMAGE")
"${args[@]}"

echo "Container started. Follow first-time setup with:"
echo "  sudo docker logs -f $CONTAINER"
