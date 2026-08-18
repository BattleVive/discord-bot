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

required_metric() {
  namespace=$1 metric_name=$2 statistic=$3 period=$4
  shift 4
  expected=$((168 * 3600 / period))
  "$AWS_CLI" cloudwatch get-metric-statistics --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION_NAME" \
    --namespace "$namespace" --metric-name "$metric_name" --statistics "$statistic" \
    --period "$period" --start-time "$start_time" --end-time "$end_time" "$@" \
    --output json | "$JQ_CLI" -e \
      --arg metric "$metric_name" --arg statistic "$statistic" \
      --arg start "$start_time" --arg end "$end_time" \
      --argjson expected "$expected" --argjson period "$period" '
        (.Datapoints | sort_by(.Timestamp | fromdateiso8601)) as $points |
        ($start | fromdateiso8601) as $start_epoch |
        ($end | fromdateiso8601) as $end_epoch |
        ($points | map(.Timestamp | fromdateiso8601)) as $times |
        ([range(1; $times | length) | $times[.] - $times[. - 1]] | max // 0) as $max_gap |
        if ($points | length) < ($expected * 0.98 | floor) or
           ($times | length) == 0 or
           ($times[0] - $start_epoch) > ($period * 2) or
           ($end_epoch - $times[-1]) > ($period * 2) or
           $max_gap > ($period * 2)
        then error($metric + " lacks 168-hour continuous coverage")
        else [$points[][$statistic]] |
          {count: length, max_gap_seconds: $max_gap,
           value: (if $statistic == "Minimum" then min elif $statistic == "Sum" then add elif $statistic == "Average" then (add / length) else max end)}
        end'
}

optional_sum_metric() {
  namespace=$1 metric_name=$2 period=$3
  shift 3
  "$AWS_CLI" cloudwatch get-metric-statistics --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION_NAME" \
    --namespace "$namespace" --metric-name "$metric_name" --statistics Sum \
    --period "$period" --start-time "$start_time" --end-time "$end_time" "$@" \
    --output json | "$JQ_CLI" '{count: (.Datapoints | length), value: ([.Datapoints[].Sum] | add // 0)}'
}

instance_dimension=(--dimensions "Name=InstanceId,Value=${instances[0]}")
memory=$(required_metric CWAgent mem_used Maximum 300 "${instance_dimension[@]}")
swap_max=$(required_metric CWAgent swap_used Maximum 300 "${instance_dimension[@]}")
swap_average=$(required_metric CWAgent swap_used Average 300 "${instance_dimension[@]}")
disk=$(required_metric CWAgent disk_used_percent Maximum 300 "${instance_dimension[@]}")
inode_used=$(required_metric CWAgent disk_inodes_used Maximum 300 "${instance_dimension[@]}")
inode_total=$(required_metric CWAgent disk_inodes_total Minimum 300 "${instance_dimension[@]}")
credits=$(required_metric AWS/EC2 CPUCreditBalance Minimum 300 "${instance_dimension[@]}")
surplus=$(optional_sum_metric AWS/EC2 CPUSurplusCreditsCharged 3600 "${instance_dimension[@]}")
status=$(required_metric AWS/EC2 StatusCheckFailed Sum 300 "${instance_dimension[@]}")
health=$(required_metric Battlevive/Production BotHealthy Minimum 60)
oom=$(required_metric Battlevive/Production OOMEvents Maximum 60)
telemetry=$(required_metric Battlevive/Production HostTelemetryHealthy Minimum 60)

report=$(
  "$JQ_CLI" -n \
    --arg start "$start_time" --arg end "$end_time" \
    --argjson memory "$memory" --argjson swap_max "$swap_max" \
    --argjson swap_average "$swap_average" --argjson disk "$disk" \
    --argjson inode_used "$inode_used" --argjson inode_total "$inode_total" \
    --argjson credits "$credits" --argjson surplus "$surplus" \
    --argjson status "$status" --argjson health "$health" \
    --argjson oom "$oom" --argjson telemetry "$telemetry" \
    '{window:{start:$start,end:$end},metrics:{memory_bytes_max:$memory,swap_bytes_max:$swap_max,swap_bytes_average:$swap_average,disk_percent_max:$disk,inodes_used_max:$inode_used,inodes_total_min:$inode_total,cpu_credit_min:$credits,cpu_surplus_sum:$surplus,status_failures_sum:$status,bot_health_min:$health,oom_events_max:$oom,host_telemetry_min:$telemetry}} |
     .inode_percent_max = (100 * .metrics.inodes_used_max.value / .metrics.inodes_total_min.value) |
     .safe = (.metrics.memory_bytes_max.value <= 314572800 and .metrics.swap_bytes_average.value == 0 and .metrics.swap_bytes_max.value <= 67108864 and .metrics.disk_percent_max.value < 70 and .inode_percent_max < 70 and .metrics.cpu_credit_min.value >= 24 and .metrics.cpu_surplus_sum.value == 0 and .metrics.status_failures_sum.value == 0 and .metrics.bot_health_min.value >= 1 and .metrics.oom_events_max.value == 0 and .metrics.host_telemetry_min.value >= 1)'
)
printf '%s\n' "$report"
[[ $(printf '%s' "$report" | "$JQ_CLI" -r .safe) == true ]]
