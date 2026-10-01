#!/usr/bin/env bash
# Runs as root on first boot of the VM. Installs Docker (+ NVIDIA container toolkit on GPU VMs).
set -euxo pipefail

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

if command -v nvidia-smi >/dev/null 2>&1 && ! dpkg -s nvidia-container-toolkit >/dev/null 2>&1; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update && apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi

# Let Docker pull from Artifact Registry using the VM's service account.
REGION_HOST="$(curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/zone | awk -F/ '{print $NF}' | sed 's/-[a-z]$//')-docker.pkg.dev"
gcloud auth configure-docker "$REGION_HOST" --quiet || true

mkdir -p /opt/video-agent
