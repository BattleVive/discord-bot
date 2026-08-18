#!/usr/bin/env bash
set -euo pipefail

for command in terraform tflint trivy shellcheck jq; do
  command -v "$command" >/dev/null || {
    echo "Required validation tool is missing: $command" >&2
    exit 1
  }
done

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_root"

terraform fmt -check -recursive infra
terraform -chdir=infra/bootstrap init -backend=false -input=false
terraform -chdir=infra/bootstrap validate
terraform -chdir=infra/production init -backend=false -input=false
terraform -chdir=infra/production validate
tflint --chdir=infra/bootstrap --config=../.tflint.hcl
tflint --chdir=infra/production --config=../.tflint.hcl --recursive
trivy config --exit-code 1 --severity HIGH,CRITICAL --ignorefile infra/.trivyignore infra
shellcheck infra/tools/*.sh infra/host/bin/*.sh infra/host/scripts/*.sh infra/host/install.sh
jq empty infra/host/cloudwatch-agent.json
bash -n infra/tools/*.sh infra/host/bin/*.sh infra/host/scripts/*.sh infra/host/install.sh
