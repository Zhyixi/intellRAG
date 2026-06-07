#!/bin/bash
set -e

echo "ENV=${ENV:-unset}"
echo "BACKEND_IP=${BACKEND_IP:-unset}"
echo "BACKEND_PORT=${BACKEND_PORT:-unset}"

STREAMLIT_ARGS=(
  run main.py
  --server.address=0.0.0.0
  --server.port=8501
  --browser.gatherUsageStats=false
)

if [ "$ENV" == "prod" ]; then
    echo "生產環境..."
    exec streamlit "${STREAMLIT_ARGS[@]}"
elif [ "$ENV" == "dev" ]; then
    echo "開發環境..."
    exec streamlit "${STREAMLIT_ARGS[@]}"
else
    echo "未知環境: ${ENV}"
    exit 1
fi