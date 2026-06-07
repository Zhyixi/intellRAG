# syntax=docker/dockerfile:1.4
# 首次 docker compose up 時若無映像會自動 build；請透過 scripts/compose-dev.sh 或 compose-prod.sh 帶入 proxy
# 建置時由 requirements.in 自動 pip-compile 並安裝（無需本機先產生 requirements.txt）

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
ARG NO_PROXY
ARG PIP_INDEX_URL=https://pypi.org/simple

FROM ubuntu:22.04

# Re-declare build args so they are available in RUN (args before FROM are not in stage scope)
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
ARG NO_PROXY
ARG PIP_INDEX_URL

ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    http_proxy=${http_proxy} \
    https_proxy=${https_proxy} \
    NO_PROXY=${NO_PROXY} \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Taipei \
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}

# --- 系統：Python 3.11 與建置／維運工具 ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    git \
    wget \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    libpq-dev \
    unixodbc \
    unixodbc-dev \
    pkg-config \
    libcairo2-dev \
    poppler-utils \
    redis-tools \
    mysql-client \
    openssh-client \
    rsync \
    sshpass \
    tzdata \
    locales \
    && rm -rf /var/lib/apt/lists/*

# --- 系統：LibreOffice（PPTX→PDF 等文件轉換） ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-core \
    libreoffice-writer \
    libreoffice-impress \
    libreoffice-draw \
    libreoffice-calc \
    libreoffice-base \
    libreoffice-java-common \
    fonts-dejavu \
    fonts-noto \
    && rm -rf /var/lib/apt/lists/*

RUN ln -fs /usr/share/zoneinfo/${TZ} /etc/localtime \
    && dpkg-reconfigure --frontend noninteractive tzdata \
    && locale-gen en_US.UTF-8 && update-locale LANG=en_US.UTF-8

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/pip pip /usr/bin/pip3 1

WORKDIR /app

COPY python_env/requirements.in ./python_env/requirements.in

RUN python -m pip install --no-cache-dir --upgrade "pip<25" \
    && python -m pip install --no-cache-dir pip-tools \
    && pip-compile /app/python_env/requirements.in \
        --output-file /app/python_env/requirements.txt \
        --resolver=backtracking \
        --index-url "${PIP_INDEX_URL}" \
        --extra-index-url https://pypi.org/simple \
    && python -m pip install --no-cache-dir --timeout=200 \
        -i "${PIP_INDEX_URL}" \
        --extra-index-url https://pypi.org/simple \
        -r /app/python_env/requirements.txt
