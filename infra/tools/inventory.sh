#!/usr/bin/env bash
# shellcheck disable=SC2016 # jq programs intentionally use their own $variables.
set -euo pipefail

AWS_CLI=${AWS_CLI:-aws}
JQ_CLI=${JQ_CLI:-jq}
AWS_PROFILE_NAME=${AWS_PROFILE_NAME:-battlevive-prod}
AWS_REGION_NAME=${AWS_REGION_NAME:-eu-north-1}
PROJECT_TAG=${PROJECT_TAG:-battlevive-bot}
ENVIRONMENT_TAG=${ENVIRONMENT_TAG:-production}

instance_json=$(
  "$AWS_CLI" ec2 describe-instances \
    --profile "$AWS_PROFILE_NAME" \
    --region "$AWS_REGION_NAME" \
    --filters \
      "Name=tag:Project,Values=$PROJECT_TAG" \
      "Name=tag:Environment,Values=$ENVIRONMENT_TAG" \
      "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --output json
)

instance_count=$(printf '%s' "$instance_json" | "$JQ_CLI" '[.Reservations[].Instances[]] | length')
if [[ $instance_count -ne 1 ]]; then
  echo "Expected exactly one tagged production instance; found $instance_count." >&2
  exit 1
fi

mapfile -t security_group_ids < <(printf '%s' "$instance_json" | "$JQ_CLI" -r '.Reservations[0].Instances[0].SecurityGroups[].GroupId')
mapfile -t volume_ids < <(printf '%s' "$instance_json" | "$JQ_CLI" -r '.Reservations[0].Instances[0].BlockDeviceMappings[].Ebs.VolumeId')

security_group_json='{"SecurityGroups":[]}'
if [[ ${#security_group_ids[@]} -gt 0 ]]; then
  security_group_json=$("$AWS_CLI" ec2 describe-security-groups --profile "$AWS_PROFILE_NAME" \
    --region "$AWS_REGION_NAME" --group-ids "${security_group_ids[@]}" --output json)
fi
volume_json='{"Volumes":[]}'
if [[ ${#volume_ids[@]} -gt 0 ]]; then
  volume_json=$("$AWS_CLI" ec2 describe-volumes --profile "$AWS_PROFILE_NAME" \
    --region "$AWS_REGION_NAME" --volume-ids "${volume_ids[@]}" --output json)
fi
ssm_json=$("$AWS_CLI" ssm describe-instance-information --profile "$AWS_PROFILE_NAME" \
  --region "$AWS_REGION_NAME" \
  --filters "Key=InstanceIds,Values=$(printf '%s' "$instance_json" | "$JQ_CLI" -r '.Reservations[0].Instances[0].InstanceId')" \
  --output json)

# Deliberately select an allowlist. Tags, user data, console output, command
# history, and Parameter Store values are never requested or rendered.
"$JQ_CLI" -n --argjson ec2 "$instance_json" --argjson groups "$security_group_json" \
  --argjson volumes "$volume_json" --argjson ssm "$ssm_json" '
  $ec2.Reservations[0].Instances[0] as $instance |
  {
    instance_id: $instance.InstanceId,
    instance_type: $instance.InstanceType,
    architecture: $instance.Architecture,
    ami_id: $instance.ImageId,
    availability_zone: $instance.Placement.AvailabilityZone,
    vpc_id: $instance.VpcId,
    subnet_id: $instance.SubnetId,
    public_ipv4: ($instance.PublicIpAddress // null),
    instance_profile_arn: ($instance.IamInstanceProfile.Arn // null),
    security_group_ids: [$instance.SecurityGroups[].GroupId],
    security_groups: [$groups.SecurityGroups[]? | {group_id: .GroupId, name: .GroupName, description: .Description, ingress: [.IpPermissions[]? | {protocol: .IpProtocol, from_port: .FromPort, to_port: .ToPort, ipv4_ranges: [.IpRanges[].CidrIp], ipv6_ranges: [.Ipv6Ranges[].CidrIpv6]}]}],
    root_device_name: $instance.RootDeviceName,
    block_devices: [$instance.BlockDeviceMappings[]? | . as $mapping | {
      device_name: .DeviceName,
      volume_id: .Ebs.VolumeId,
      delete_on_termination: .Ebs.DeleteOnTermination,
      size_gib: ([$volumes.Volumes[]? | select(.VolumeId == $mapping.Ebs.VolumeId) | .Size][0] // null),
      volume_type: ([$volumes.Volumes[]? | select(.VolumeId == $mapping.Ebs.VolumeId) | .VolumeType][0] // null),
      encrypted: ([$volumes.Volumes[]? | select(.VolumeId == $mapping.Ebs.VolumeId) | .Encrypted][0] // null)
    }],
    ssm: (($ssm.InstanceInformationList[0] // {}) | {ping_status: (.PingStatus // "NotRegistered"), agent_version: (.AgentVersion // null), platform: (.PlatformName // null)})
  }'
