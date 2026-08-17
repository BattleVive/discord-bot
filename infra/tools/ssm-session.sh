#!/usr/bin/env bash
set -euo pipefail

AWS_CLI=${AWS_CLI:-aws}
AWS_PROFILE_NAME=${AWS_PROFILE_NAME:-battlevive-prod}
AWS_REGION_NAME=${AWS_REGION_NAME:-eu-north-1}
PROJECT_TAG=${PROJECT_TAG:-battlevive-bot}
ENVIRONMENT_TAG=${ENVIRONMENT_TAG:-production}

instance_ids=$(
  "$AWS_CLI" ec2 describe-instances \
    --profile "$AWS_PROFILE_NAME" \
    --region "$AWS_REGION_NAME" \
    --filters \
      "Name=tag:Project,Values=$PROJECT_TAG" \
      "Name=tag:Environment,Values=$ENVIRONMENT_TAG" \
      "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].InstanceId' \
    --output text
)

read -r -a instances <<<"$instance_ids"
if [[ ${#instances[@]} -ne 1 || -z ${instances[0]} ]]; then
  echo "Expected exactly one tagged production instance; found ${#instances[@]}." >&2
  exit 1
fi

exec "$AWS_CLI" ssm start-session \
  --target "${instances[0]}" \
  --document-name battlevive-production-shell \
  --region "$AWS_REGION_NAME" \
  --profile "$AWS_PROFILE_NAME"
