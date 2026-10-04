#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
MODEL="${MODEL:-qwen3.8-flash-next-iq2_xs}"
OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

echo "Health:"
curl --fail --silent --show-error "$BASE_URL/health"
echo

echo "GPU before request:"
nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free \
    --format=csv,noheader

curl --fail --silent --show-error --max-time 300 \
    -o "$OUT" \
    -w 'HTTP=%{http_code} total_seconds=%{time_total}\n' \
    "$BASE_URL/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Please answer in Traditional Chinese: give three one-sentence reasons why quantization reduces LLM deployment resource requirements.\"}],\"max_tokens\":128,\"reasoning_effort\":\"none\"}"

python3 - "$OUT" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    response = json.load(handle)

choice = response["choices"][0]
print("Content:", choice["message"]["content"])
print("Finish:", choice["finish_reason"])
print("Usage:", response.get("usage"))
print("Timings:", response.get("timings"))
PY

echo "GPU after request:"
nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free \
    --format=csv,noheader
