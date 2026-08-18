#!/usr/bin/env bash
set -euo pipefail

mode=${1:-}
plan_file=${TERRAFORM_PLAN_FILE:-/tmp/battlevive-production.tfplan}
: "${TF_STATE_BUCKET:?TF_STATE_BUCKET is required}"

case $mode in
  plan|apply) ;;
  *) echo "Usage: $0 plan|apply" >&2; exit 2 ;;
esac

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_root"
aws sts get-caller-identity --query Account --output text >/dev/null
terraform -chdir=infra/production init -input=false -reconfigure \
  -backend-config="bucket=$TF_STATE_BUCKET"

if [[ $mode == plan ]]; then
  terraform -chdir=infra/production plan -input=false -lock-timeout=5m -out="$plan_file"
  terraform -chdir=infra/production show -no-color "$plan_file"
else
  [[ -s $plan_file ]] || { echo "Reviewed Terraform plan is missing: $plan_file" >&2; exit 1; }
  terraform -chdir=infra/production apply -input=false -lock-timeout=5m -auto-approve "$plan_file"
fi
