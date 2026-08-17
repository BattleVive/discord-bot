#!/usr/bin/env bash
# shellcheck disable=SC2016 # jq programs intentionally use their own $variables.
set -euo pipefail

AWS_CLI=${AWS_CLI:-aws}
DOCKER_CLI=${DOCKER_CLI:-docker}
JQ_CLI=${JQ_CLI:-jq}
AWS_REGION_NAME=${AWS_REGION_NAME:-eu-north-1}
COMPOSE_FILE=${COMPOSE_FILE:-/opt/battlevive/current/docker-compose.yml}
now=$(date -u +%s)
healthy=0

if payload=$("$DOCKER_CLI" compose -f "$COMPOSE_FILE" exec -T bot \
  cat /tmp/battlevive-health.json 2>/dev/null); then
  if printf '%s' "$payload" | "$JQ_CLI" -e \
    --argjson now "$now" \
    '.status == "healthy" and ((.timestamp | fromdateiso8601) >= ($now - 90))' >/dev/null; then
    healthy=1
  fi
fi

"$AWS_CLI" cloudwatch put-metric-data --region "$AWS_REGION_NAME" \
  --namespace Battlevive/Production --metric-name BotHealthy \
  --value "$healthy" --unit Count

[[ $healthy -eq 1 ]]
