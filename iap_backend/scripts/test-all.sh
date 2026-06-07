#!/usr/bin/env bash
# 快测（mock API + unit）+ 集成冒烟（真实 Agent/DB）。发版或大改后建议执行。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 隐藏 DeprecationWarning 等噪音，避免终端满屏「像报错」的黄字；要看警告可单独跑 pytest。
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore::DeprecationWarning,ignore::FutureWarning,ignore::UserWarning}"

echo "========================================"
echo "  test-all: 共 50 个用例（47 快测 + 3 集成）"
echo "  未包含: performance 等 2 个 manual 用例"
echo "========================================"
echo ""

echo "==> [1/2] 快测: API mock + unit（约 7s）"
pytest -v --tb=short

echo ""
echo "==> [2/2] 集成: AE/PE v3 retrieve 真链路（约 30s）"
pytest tests/api/test_api_integration.py --override-ini='addopts=' -v --tb=short

echo ""
echo "========================================"
echo "  结果: 全部通过（50 passed）"
echo "  说明: 末尾 warnings 若单独跑 pytest 仍可能出现，不是 FAILED"
echo "========================================"
