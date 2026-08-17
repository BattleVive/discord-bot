#!/usr/bin/env bash
# shellcheck disable=SC2016 # jq programs intentionally use their own $variables.
set -euo pipefail

AWS_CLI=${AWS_CLI:-aws}
JQ_CLI=${JQ_CLI:-jq}
AWS_PROFILE_NAME=${AWS_PROFILE_NAME:-battlevive-prod}
AWS_REGION_NAME=${AWS_REGION_NAME:-eu-north-1}
end_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
start_time=$(date -u -d '168 hours ago' +%Y-%m-%dT%H:%M:%SZ)

instance_id=$(
  "$AWS_CLI" ec2 describe-instances --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION_NAME" \
    --filters "Name=tag:Project,Values=battlevive-bot" "Name=tag:Environment,Values=production" \
    --query 'Reservations[].Instances[].InstanceId' --output text
)
read -r -a instances <<<"$instance_id"
if [[ ${#instances[@]} -ne 1 ]]; then
  echo "Resize gate requires exactly one tagged instance." >&2
  exit 1
fi

metric() {
  namespace=$1 metric_name=$2 statistic=$3 period=$4
  shift 4
  "$AWS_CLI" cloudwatch get-metric-statistics --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION_NAME" \
    --namespace "$namespace" --metric-name "$metric_name" --statistics "$statistic" \
    --period "$period" --start-time "$start_time" --end-time "$end_time" "$@" \
    --output json | "$JQ_CLI" -e --arg statistic "$statistic" \
      'if (.Datapoints | length) == 0 then error("missing metric data") else [.Datapoints[][$statistic]] | {count: length, value: (if $statistic == "Minimum" then min elif $statistic == "Sum" then add else max end)} end'
}

instance_dimension=(--dimensions "Name=InstanceId,Value=${instances[0]}")
memory=$(metric CWAgent mem_used Maximum 300 "${instance_dimension[@]}")
swap_max=$(metric CWAgent swap_used Maximum 300 "${instance_dimension[@]}")
swap_average=$(metric CWAgent swap_used Average 300 "${instance_dimension[@]}")
disk=$(metric CWAgent disk_used_percent Maximum 300 "${instance_dimension[@]}")
credits=$(metric AWS/EC2 CPUCreditBalance Minimum 300 "${instance_dimension[@]}")
surplus=$(metric AWS/EC2 CPUSurplusCreditsCharged Sum 3600 "${instance_dimension[@]}")
status=$(metric AWS/EC2 StatusCheckFailed Sum 300 "${instance_dimension[@]}")
health=$(metric Battlevive/Production BotHealthy Minimum 60)

report=$(
  "$JQ_CLI" -n \
    --arg start "$start_time" --arg end "$end_time" \
    --argjson memory "$memory" --argjson swap_max "$swap_max" \
    --argjson swap_average "$swap_average" --argjson disk "$disk" \
    --argjson credits "$credits" --argjson surplus "$surplus" \
    --argjson status "$status" --argjson health "$health" \
    '{window:{start:$start,end:$end},metrics:{memory_bytes_max:$memory,swap_bytes_max:$swap_max,swap_bytes_average:$swap_average,disk_percent_max:$disk,cpu_credit_min:$credits,cpu_surplus_sum:$surplus,status_failures_sum:$status,bot_health_min:$health}} |
     .safe = (.metrics.memory_bytes_max.value <= 314572800 and .metrics.swap_bytes_average.value == 0 and .metrics.swap_bytes_max.value <= 67108864 and .metrics.disk_percent_max.value < 70 and .metrics.cpu_credit_min.value >= 24 and .metrics.cpu_surplus_sum.value == 0 and .metrics.status_failures_sum.value == 0 and .metrics.bot_health_min.value >= 1)'
)
printf '%s\n' "$report"
[[ $(printf '%s' "$report" | "$JQ_CLI" -r .safe) == true ]]
