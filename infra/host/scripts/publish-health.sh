#!/usr/bin/env bash
# shellcheck disable=SC2016 # jq programs intentionally use their own $variables.
set -euo pipefail

AWS_CLI=${AWS_CLI:-aws}
COMPOSE_CLI=${BATTLEVIVE_COMPOSE_CLI:-/usr/local/libexec/battlevive/compose}
JQ_CLI=${JQ_CLI:-jq}
AWS_REGION_NAME=${AWS_REGION_NAME:-eu-north-1}
SYSTEMCTL_CLI=${SYSTEMCTL_CLI:-systemctl}
SYSTEM_MESSAGES_FILE=${SYSTEM_MESSAGES_FILE:-/var/log/battlevive-system.log}
COMPOSE_FILE=${COMPOSE_FILE:-/opt/battlevive/current/compose.yaml}
now=$(date -u +%s)
healthy=0
host_telemetry=0

if payload=$("$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T bot \
  cat /tmp/battlevive-health.json 2>/dev/null); then
  if printf '%s' "$payload" | "$JQ_CLI" -e \
    --argjson now "$now" \
    '.status == "healthy" and ((.timestamp | fromdateiso8601) >= ($now - 90))' >/dev/null; then
    healthy=1
  fi
fi

if [[ -f $SYSTEM_MESSAGES_FILE ]] && \
  "$SYSTEMCTL_CLI" is-active --quiet rsyslog && \
  "$SYSTEMCTL_CLI" is-active --quiet amazon-cloudwatch-agent; then
  host_telemetry=1
fi

"$AWS_CLI" cloudwatch put-metric-data --region "$AWS_REGION_NAME" \
  --namespace Battlevive/Production --metric-name BotHealthy \
  --value "$healthy" --unit Count

# OOM log filters publish 1 on a match. This zero heartbeat makes absence
# distinguishable from a broken system-log pipeline during the resize gate.
"$AWS_CLI" cloudwatch put-metric-data --region "$AWS_REGION_NAME" \
  --namespace Battlevive/Production --metric-name OOMEvents \
  --value 0 --unit Count
"$AWS_CLI" cloudwatch put-metric-data --region "$AWS_REGION_NAME" \
  --namespace Battlevive/Production --metric-name HostTelemetryHealthy \
  --value "$host_telemetry" --unit Count

[[ $healthy -eq 1 && $host_telemetry -eq 1 ]]
