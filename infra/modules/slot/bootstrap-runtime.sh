#!/bin/bash
set -euo pipefail

# Amazon Linux 2023 supplies Docker, but not the Compose CLI plugin. Install
# the ARM64 plugin that matches this module's Graviton host and verify it
# before Docker loads it.
dnf install -y awscli docker jq
install -d -m 0755 /usr/local/lib/docker/cli-plugins
curl --fail --location --silent --show-error \
  https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-aarch64 \
  --output /usr/local/lib/docker/cli-plugins/docker-compose
echo '732e3a84c1a0f67256ce80bc2598a24546b10ca05f9faa97efceb1171ece2ef7  /usr/local/lib/docker/cli-plugins/docker-compose' | sha256sum --check
chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose
systemctl enable --now docker
usermod -aG docker ec2-user
