#!/usr/bin/env bash
# shellcheck disable=SC2016 # jq programs intentionally use their own $variables.
set -euo pipefail

mode=weekly
if [[ ${1:-} == weekly || ${1:-} == predeploy ]]; then
  mode=$1
  shift
fi
while [[ $# -gt 0 ]]; do
  case $1 in
    --type)
      [[ $# -ge 2 ]] || { echo "--type requires weekly or pre-deploy." >&2; exit 2; }
      mode=$2
      shift 2
      ;;
    --verify)
      # Validation is mandatory for every backup; retain this explicit deploy API flag.
      shift
      ;;
    *)
      echo "Usage: $0 [weekly|predeploy] [--type weekly|pre-deploy] [--verify]" >&2
      exit 2
      ;;
  esac
done
[[ $mode == pre-deploy ]] && mode=predeploy
if [[ $mode != weekly && $mode != predeploy ]]; then
  echo "Backup type must be weekly or pre-deploy." >&2
  exit 2
fi

if [[ $EUID -ne 0 && ${ALLOW_NON_ROOT_FOR_TESTS:-0} != 1 ]]; then
  echo "backup must run as root." >&2
  exit 1
fi

BATTLEVIVE_OPERATIONS_LOCK=${BATTLEVIVE_OPERATIONS_LOCK:-${BACKUP_TMP_ROOT:-/run/lock}/battlevive-operations.lock}
if [[ ${BATTLEVIVE_OPERATIONS_LOCK_HELD:-0} != 1 ]]; then
  exec 8>"$BATTLEVIVE_OPERATIONS_LOCK"
  flock --exclusive --nonblock 8 || {
    echo "Another Battlevive operation is active." >&2
    exit 1
  }
fi

AWS_CLI=${AWS_CLI:-aws}
COMPOSE_CLI=${BATTLEVIVE_COMPOSE_CLI:-/usr/local/libexec/battlevive/compose}
JQ_CLI=${JQ_CLI:-jq}
AWS_REGION_NAME=${AWS_REGION_NAME:-eu-north-1}
COMPOSE_FILE=${COMPOSE_FILE:-/opt/battlevive/current/compose.yaml}
BACKUP_TMP_ROOT=${BACKUP_TMP_ROOT:-/var/lib/battlevive/backups}
HOST_ENV_FILE=${HOST_ENV_FILE:-/run/battlevive/host.env}

if [[ -f $HOST_ENV_FILE ]]; then
  read -r host_env_owner host_env_mode < <(stat -c '%U %a' "$HOST_ENV_FILE")
  if [[ $host_env_owner != root || $host_env_mode != 600 ]]; then
    echo "$HOST_ENV_FILE must be root-owned with mode 0600." >&2
    exit 1
  fi
  # This root-controlled file contains only POSTGRES_USER and POSTGRES_DB.
  # shellcheck disable=SC1090
  source "$HOST_ENV_FILE"
fi

POSTGRES_USER=${POSTGRES_USER:-battlevive}
POSTGRES_DB=${POSTGRES_DB:-battlevive}
METRIC_NAMESPACE=${METRIC_NAMESPACE:-Battlevive/Production}

metric() {
  "$AWS_CLI" cloudwatch put-metric-data \
    --region "$AWS_REGION_NAME" \
    --namespace "$METRIC_NAMESPACE" \
    --metric-name BackupSuccess \
    --value "$1" \
    --unit Count >/dev/null 2>&1 || true
}

workdir=""
on_exit() {
  rc=$?
  if [[ $rc -ne 0 ]]; then
    metric 0
  fi
  if [[ -n $workdir ]]; then
    rm -rf -- "$workdir"
  fi
  exit "$rc"
}
trap on_exit EXIT

if [[ -z ${OPERATIONS_BUCKET:-} ]]; then
  OPERATIONS_BUCKET=$(
    "$AWS_CLI" ssm get-parameter \
      --region "$AWS_REGION_NAME" \
      --name /battlevive/production/config/operations-bucket \
      --query Parameter.Value \
      --output text
  )
fi

install -d -m 0700 "$BACKUP_TMP_ROOT"
workdir=$(mktemp -d "$BACKUP_TMP_ROOT/.backup.XXXXXX")
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="battlevive-${timestamp}.dump"

"$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
  pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --format=custom --compress=9 --no-owner --no-privileges >"$workdir/$archive"

"$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
  pg_restore --list <"$workdir/$archive" >/dev/null

ledger_present=$(
  "$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
    psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
    --command "SELECT to_regclass('public.schema_migrations') IS NOT NULL;"
)
case "$ledger_present" in
  t)
    schema_version=$(
      "$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
        psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
        --command 'SELECT COALESCE(MAX(version), 0) FROM schema_migrations;'
    )
    ledger_count=$(
      "$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
        psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
        --command 'SELECT count(*) FROM schema_migrations;'
    )
    ;;
  f)
    schema_version=0
    ledger_count=0
    ;;
  *)
    echo "unable to determine migration-ledger presence for backup" >&2
    exit 1
    ;;
esac
row_counts=$(
  "$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
    psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
    --command "SELECT json_build_object('schema_migrations', $ledger_count, 'guild_config', (SELECT count(*) FROM guild_config));"
)
deployment_state=$(
  "$AWS_CLI" ssm get-parameter \
    --region "$AWS_REGION_NAME" \
    --name /battlevive/production/deployment/state \
    --query Parameter.Value \
    --output text
)
application_version=$(printf '%s' "$deployment_state" | "$JQ_CLI" -er \
  '.version | select(test("^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$"))')

(
  cd "$workdir"
  sha256sum "$archive" >"$archive.sha256"
)

"$JQ_CLI" -n \
  --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg mode "$mode" \
  --arg application_version "$application_version" \
  --arg schema_version "$schema_version" \
  --arg archive "$archive" \
  --argjson row_counts "$row_counts" \
  '{created_at: $created_at, mode: $mode, application_version: $application_version, schema_version: ($schema_version | tonumber), archive: $archive, row_counts: $row_counts}' \
  >"$workdir/$archive.manifest.json"

prefix="backups/$mode/$timestamp"
for filename in "$archive" "$archive.sha256" "$archive.manifest.json"; do
  "$AWS_CLI" s3 cp "$workdir/$filename" \
    "s3://$OPERATIONS_BUCKET/$prefix/$filename" \
    --region "$AWS_REGION_NAME" \
    --only-show-errors
done

metric 1
trap - EXIT
rm -rf -- "$workdir"
echo "Verified $mode backup uploaded to s3://$OPERATIONS_BUCKET/$prefix/." >&2
