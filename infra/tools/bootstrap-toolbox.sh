#!/usr/bin/env bash
set -euo pipefail

container=${AWS_TOOLBOX_NAME:-aws}
image=${AWS_TOOLBOX_IMAGE:-registry.fedoraproject.org/fedora-toolbox:43}

if ! toolbox list --containers 2>/dev/null | awk '{print $2}' | grep -Fxq "$container"; then
  toolbox create --container "$container" --image "$image"
fi

toolbox run --container "$container" sudo dnf install -y \
  awscli2 curl dnf-plugins-core golang jq python3-pytest shellcheck unzip
toolbox run --container "$container" sudo dnf config-manager addrepo \
  --from-repofile=https://rpm.releases.hashicorp.com/fedora/hashicorp.repo
toolbox run --container "$container" sudo dnf install -y terraform
toolbox run --container "$container" sudo env GOBIN=/usr/local/bin \
  go install github.com/terraform-linters/tflint@v0.64.0

host_arch=$(uname -m)
case $host_arch in
  x86_64) trivy_arch=x86_64 ;;
  aarch64) trivy_arch=aarch64 ;;
  *) echo "Unsupported Trivy architecture: $host_arch" >&2; exit 1 ;;
esac
toolbox run --container "$container" sudo dnf config-manager addrepo \
  --id=trivy \
  --set="baseurl=https://aquasecurity.github.io/trivy-repo/rpm/releases/$trivy_arch/" \
  --set=gpgcheck=0
toolbox run --container "$container" sudo dnf install -y trivy

machine=$host_arch
case "$machine" in
  x86_64) plugin_arch=64bit ;;
  aarch64) plugin_arch=arm64 ;;
  *) echo "Unsupported Session Manager plugin architecture: $machine" >&2; exit 1 ;;
esac
plugin_rpm="https://s3.amazonaws.com/session-manager-downloads/plugin/latest/linux_$plugin_arch/session-manager-plugin.rpm"
toolbox run --container "$container" sudo dnf install -y "$plugin_rpm"

toolbox run --container "$container" aws --version
toolbox run --container "$container" terraform version
toolbox run --container "$container" session-manager-plugin --version
toolbox run --container "$container" jq --version
toolbox run --container "$container" shellcheck --version
toolbox run --container "$container" tflint --version
toolbox run --container "$container" trivy --version
