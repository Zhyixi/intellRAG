#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "This installer requires Linux x86_64." >&2
    exit 1
fi

sudo apt-get update
sudo apt-get install -y \
    "linux-headers-$(uname -r)" \
    nvidia-driver-580-server \
    nvidia-utils-580-server \
    docker.io \
    ca-certificates \
    curl \
    git \
    gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor --yes -o /etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl enable --now docker
sudo systemctl restart docker
sudo usermod -aG docker "${SUDO_USER:-$USER}"

echo "Host installation complete. Reboot before building Strata."
