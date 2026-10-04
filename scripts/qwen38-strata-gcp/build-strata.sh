#!/usr/bin/env bash
set -euo pipefail

STRATA_ROOT="${STRATA_ROOT:-$HOME/Strata}"
STRATA_REVISION="${STRATA_REVISION:-99f3dbd0b21d1401b3769e0c0d963913607f380b}"
CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES:-86;89}"
IMAGE="${STRATA_IMAGE:-strata:qwen38}"

if [[ -d "$STRATA_ROOT/.git" ]]; then
    git -C "$STRATA_ROOT" fetch origin "$STRATA_REVISION"
else
    git clone https://github.com/Niko1221/Strata.git "$STRATA_ROOT"
fi

git -C "$STRATA_ROOT" checkout --detach "$STRATA_REVISION"

sudo docker build \
    -t "$IMAGE" \
    --build-arg "CUDA_ARCHITECTURES=$CUDA_ARCHITECTURES" \
    --build-arg BUILD_VISION=0 \
    "$STRATA_ROOT"

sudo docker image inspect "$IMAGE" --format 'Built {{.RepoTags}} at {{.Created}}'
