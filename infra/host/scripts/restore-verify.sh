#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 && ${ALLOW_NON_ROOT_FOR_TESTS:-0} != 1 ]]; then
  echo "restore verification must run as root." >&2
  exit 1
fi

BATTLEVIVE_OPERATIONS_LOCK=${BATTLEVIVE_OPERATIONS_LOCK:-/run/lock/battlevive-operations.lock}
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
RESTORE_TMP_ROOT=${RESTORE_TMP_ROOT:-/var/lib/battlevive/restore-verification}
POSTGRES_USER=${POSTGRES_USER:-battlevive}
POSTGRES_DB=${POSTGRES_DB:-battlevive}
HOST_ENV_FILE=${HOST_ENV_FILE:-/run/battlevive/host.env}
METRIC_NAMESPACE=${METRIC_NAMESPACE:-Battlevive/Production}

if [[ -f $HOST_ENV_FILE ]]; then
  read -r host_env_owner host_env_mode < <(stat -c '%U %a' "$HOST_ENV_FILE")
  if [[ $host_env_owner != root || $host_env_mode != 600 ]]; then
    echo "$HOST_ENV_FILE must be root-owned with mode 0600." >&2
    exit 1
  fi
  # shellcheck disable=SC1090
  source "$HOST_ENV_FILE"
fi

metric() {
  "$AWS_CLI" cloudwatch put-metric-data --region "$AWS_REGION_NAME" \
    --namespace "$METRIC_NAMESPACE" --metric-name RestoreDrillSuccess \
    --value "$1" --unit Count >/dev/null 2>&1 || true
}

workdir=""
restore_db=""
on_exit() {
  rc=$?
  if [[ -n $restore_db ]]; then
    "$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
      dropdb --if-exists --force --username "$POSTGRES_USER" "$restore_db" >/dev/null 2>&1 || true
  fi
  [[ -z $workdir ]] || rm -rf -- "$workdir"
  if [[ $rc -ne 0 ]]; then metric 0; fi
  exit "$rc"
}
trap on_exit EXIT

if [[ -z ${OPERATIONS_BUCKET:-} ]]; then
  OPERATIONS_BUCKET=$("$AWS_CLI" ssm get-parameter --region "$AWS_REGION_NAME" \
    --name /battlevive/production/config/operations-bucket \
    --query Parameter.Value --output text)
fi

archive_key=$("$AWS_CLI" s3api list-objects-v2 --bucket "$OPERATIONS_BUCKET" \
  --prefix backups/weekly/ --region "$AWS_REGION_NAME" \
  --query "reverse(sort_by(Contents[?ends_with(Key, '.dump')], &LastModified))[0].Key" --output text)
if [[ -z $archive_key || $archive_key == None ]]; then
  echo "No weekly backup is available for verification." >&2
  exit 1
fi

install -d -m 0700 "$RESTORE_TMP_ROOT"
workdir=$(mktemp -d "$RESTORE_TMP_ROOT/.restore.XXXXXX")
archive=${archive_key##*/}
for suffix in "" .sha256 .manifest.json; do
  "$AWS_CLI" s3 cp "s3://$OPERATIONS_BUCKET/$archive_key$suffix" \
    "$workdir/$archive$suffix" --region "$AWS_REGION_NAME" --only-show-errors
done
(cd "$workdir" && sha256sum --check "$archive.sha256")

restore_db="battlevive_restore_$(date -u +%Y%m%d%H%M%S)"
"$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
  createdb --username "$POSTGRES_USER" "$restore_db"
"$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
  pg_restore --exit-on-error --no-owner --no-privileges \
  --username "$POSTGRES_USER" --dbname "$restore_db" <"$workdir/$archive"

actual=$(
  "$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
    psql --username "$POSTGRES_USER" --dbname "$restore_db" --tuples-only --no-align \
    --command "SELECT json_build_object('schema_migrations', (SELECT count(*) FROM schema_migrations), 'guild_config', (SELECT count(*) FROM guild_config));"
)
expected=$("$JQ_CLI" -c '.row_counts' "$workdir/$archive.manifest.json")
if [[ $(printf '%s' "$actual" | "$JQ_CLI" -cS .) != $(printf '%s' "$expected" | "$JQ_CLI" -cS .) ]]; then
  echo "Restored key-table counts do not match the backup manifest." >&2
  exit 1
fi

metric 1
printf '{"verified_at":"%s","archive_key":"%s"}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$archive_key" | \
  "$AWS_CLI" s3 cp - "s3://$OPERATIONS_BUCKET/backups/restore-drills/$(date -u +%Y%m%dT%H%M%SZ).json" \
    --region "$AWS_REGION_NAME" --only-show-errors
trap - EXIT
"$COMPOSE_CLI" -f "$COMPOSE_FILE" exec -T db \
  dropdb --if-exists --force --username "$POSTGRES_USER" "$restore_db" >/dev/null
restore_db=""
rm -rf -- "$workdir"
echo "Isolated restore verification succeeded." >&2
