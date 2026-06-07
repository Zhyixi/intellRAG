#!/bin/bash

if [ "$ENV" == "prod" ]; then
    echo "IAP Platform — 生產環境"
    cd fast_api_service
    exec python main.py
elif [ "$ENV" == "dev" ]; then
    echo "IAP Platform — 開發環境（FastAPI :44000）"
    cd fast_api_service
    exec python main.py
else
    echo "未知環境: $ENV"
    exit 1
fi

echo "All processes are done. Exiting."