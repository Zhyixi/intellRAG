#!/usr/bin/env python3
"""Launch web search MCP via Docker (preferred) or local Python fallback."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

CONTAINER_NAME = os.getenv("IAP_BACKEND_CONTAINER", "iap_backend_dev")
MCP_MODULE = "iap_mcp.web_search_server"


def _docker_container_running(name: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.stdout.strip() == name


def _run_docker_mcp() -> int:
    return subprocess.call(
        [
            "docker",
            "exec",
            "-i",
            "-w",
            "/app",
            CONTAINER_NAME,
            "python",
            "-m",
            MCP_MODULE,
        ],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _run_local_mcp() -> int:
    backend_root = Path(__file__).resolve().parents[1] / "iap_backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from iap_mcp.web_search_server import mcp

    mcp.run(transport="stdio")
    return 0


def main() -> int:
    if _docker_container_running(CONTAINER_NAME):
        return _run_docker_mcp()

    sys.stderr.write(
        f"Docker container '{CONTAINER_NAME}' not running; "
        "trying local Python (requires: pip install mcp duckduckgo-search).\n"
    )
    try:
        return _run_local_mcp()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        sys.stderr.write(
            "Start the backend container or install MCP deps locally:\n"
            "  docker compose up -d iap_backend\n"
            "  pip install mcp duckduckgo-search\n"
        )
        return code
    except Exception as exc:
        sys.stderr.write(f"MCP launch failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
