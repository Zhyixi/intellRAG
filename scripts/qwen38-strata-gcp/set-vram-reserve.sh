#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${STRATA_CONTAINER:-strata-qwen38}"
RESERVE_MIB="${RESERVE_MIB:-12288}"

[[ "$RESERVE_MIB" =~ ^[0-9]+$ ]] || {
    echo "RESERVE_MIB must be an integer." >&2
    exit 1
}

sudo docker exec -e RESERVE_MIB="$RESERVE_MIB" "$CONTAINER" \
    /opt/strata/.venv/bin/python -c '
import json
import os

reserve = os.environ["RESERVE_MIB"]
paths = [
    "/opt/strata/strata-iq2_xs.json",
    "/data/config/strata-iq2_xs.json",
]

for path in paths:
    if not os.path.exists(path):
        continue
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    old = config["args"]
    new = []
    skip = False
    for item in old:
        if skip:
            skip = False
            continue
        if item == "--vram-reserve-mib":
            skip = True
            continue
        new.append(item)
    new.extend(["--vram-reserve-mib", reserve])
    config["args"] = new
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    print(f"updated {path}: reserve={reserve} MiB")
'

sudo docker restart "$CONTAINER" >/dev/null
echo "Restarted $CONTAINER. Model loading usually takes about two minutes."
