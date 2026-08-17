#!/usr/bin/env bash
set -euo pipefail

AWS_CLI=${AWS_CLI:-aws}
AWS_REGION_NAME=${AWS_REGION_NAME:-eu-north-1}
now=$(date -u +%s)

if [[ -z ${OPERATIONS_BUCKET:-} ]]; then
  OPERATIONS_BUCKET=$("$AWS_CLI" ssm get-parameter --region "$AWS_REGION_NAME" \
    --name /battlevive/production/config/operations-bucket \
    --query Parameter.Value --output text)
fi

freshness_metric() {
  prefix=$1 max_age_seconds=$2 metric_name=$3 suffix=$4
  modified=$("$AWS_CLI" s3api list-objects-v2 --bucket "$OPERATIONS_BUCKET" \
    --prefix "$prefix" --region "$AWS_REGION_NAME" \
    --query "reverse(sort_by(Contents[?ends_with(Key, '$suffix')], &LastModified))[0].LastModified" \
    --output text)
  fresh=0
  if [[ -n $modified && $modified != None ]]; then
    modified_epoch=$(date -u -d "$modified" +%s)
    if (( now - modified_epoch <= max_age_seconds )); then fresh=1; fi
  fi
  "$AWS_CLI" cloudwatch put-metric-data --region "$AWS_REGION_NAME" \
    --namespace Battlevive/Production --metric-name "$metric_name" \
    --value "$fresh" --unit Count
}

freshness_metric backups/weekly/ $((8 * 86400)) BackupFresh .dump
freshness_metric backups/restore-drills/ $((35 * 86400)) RestoreDrillFresh .json
