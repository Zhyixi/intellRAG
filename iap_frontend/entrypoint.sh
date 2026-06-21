#!/bin/bash
set -e

APP_PORT="${FRONTEND_INTERNAL_PORT:-8501}"

echo "ENV=${ENV:-unset}"
echo "BACKEND_IP=${BACKEND_IP:-unset}"
echo "BACKEND_PORT=${BACKEND_PORT:-unset}"
echo "FRONTEND_PORT=${APP_PORT}"

if [ ! -d node_modules ]; then
    echo "node_modules missing; installing React dependencies..."
    npm ci
fi

if [ "$ENV" == "prod" ]; then
    echo "生產環境：building React/Vite app..."
    npm run build
    exec npm run preview -- --host 0.0.0.0 --port "${APP_PORT}"
elif [ "$ENV" == "dev" ]; then
    echo "開發環境：starting Vite dev server..."
    exec npm run dev -- --host 0.0.0.0 --port "${APP_PORT}"
else
    echo "未知環境: ${ENV}"
    exit 1
fi