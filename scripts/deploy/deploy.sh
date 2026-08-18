#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "usage: deploy --environment production --version X.Y.Z --image-digest sha256:... --bundle-key KEY --bundle-checksum SHA256 --target-selector TAGS --operations-bucket BUCKET --aws-region REGION" >&2
  exit 2
}

environment="" version="" image_digest="" bundle_key="" bundle_checksum=""
target_selector="" operations_bucket="" aws_region=""
while (($#)); do
  case "$1" in
    --environment) environment="${2:-}"; shift 2 ;;
    --version) version="${2:-}"; shift 2 ;;
    --image-digest) image_digest="${2:-}"; shift 2 ;;
    --bundle-key) bundle_key="${2:-}"; shift 2 ;;
    --bundle-checksum) bundle_checksum="${2:-}"; shift 2 ;;
    --target-selector) target_selector="${2:-}"; shift 2 ;;
    --operations-bucket) operations_bucket="${2:-}"; shift 2 ;;
    --aws-region) aws_region="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done

[[ "$environment" == production ]] || usage
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || usage
[[ "$image_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "image must be an immutable sha256 digest" >&2
  exit 2
}
[[ "$bundle_checksum" =~ ^[0-9a-f]{64}$ ]] || usage
[[ "$bundle_key" =~ ^releases/[A-Za-z0-9._/-]+$ && "$bundle_key" != *".."* ]] || usage
[[ -n "$target_selector" ]] || usage
[[ "$operations_bucket" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || usage
[[ "$aws_region" =~ ^[a-z]{2}-[a-z]+-[0-9]+$ ]] || usage

battlevive_root="${BATTLEVIVE_ROOT:-/opt/battlevive}"
host_env="${BATTLEVIVE_HOST_ENV:-/run/battlevive/host.env}"
operations_lock="${OPERATIONS_LOCK_PATH:-/run/lock/battlevive-operations.lock}"
health_timeout="${HEALTH_TIMEOUT_SECONDS:-120}"
rollback_timeout="${ROLLBACK_TIMEOUT_SECONDS:-60}"
health_poll="${HEALTH_POLL_SECONDS:-5}"
image="voxix/battlevive-bot@${image_digest}"
state_parameter="/battlevive/production/deployment/state"
mkdir -p "$battlevive_root/releases" "$(dirname "$operations_lock")"
[[ -f "$host_env" ]] || { echo "canonical host environment is missing: $host_env" >&2; exit 1; }
if [[ "${ALLOW_NON_ROOT_FOR_TESTS:-0}" != 1 ]]; then
  read -r env_owner env_mode < <(stat -c '%U %a' "$host_env")
  [[ "$env_owner" == root && "$env_mode" == 600 ]] || {
    echo "$host_env must be root-owned with mode 0600" >&2
    exit 1
  }
fi
required_host_settings=(
  "AWS_REGION=$aws_region"
  "OPERATIONS_BUCKET=$operations_bucket"
  "BATTLEVIVE_BOT_DATA_PATH=/var/lib/battlevive/bot"
  "BATTLEVIVE_POSTGRES_DATA_PATH=/var/lib/battlevive/postgresql"
  "BATTLEVIVE_LOG_GROUP=/battlevive/production/application"
)
for host_setting in "${required_host_settings[@]}"; do
  grep -Fqx -- "$host_setting" "$host_env" || {
    echo "canonical host environment is missing required setting: ${host_setting%%=*}" >&2
    exit 1
  }
done

exec 9>"$battlevive_root/deploy.lock"
flock -n 9 || { echo "another deployment is active" >&2; exit 1; }

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/battlevive-deploy.XXXXXX")"
archive="$work_dir/bundle.tar.gz"
stage="$work_dir/stage"
digest_prefix="${image_digest#sha256:}"
release_dir="$battlevive_root/releases/$version-${digest_prefix:0:12}"
previous_dir="" previous_image="" previous_state=""
transition_started=false complete=false active_dir=""

compose() {
  local directory="$1"
  shift
  BATTLEVIVE_IMAGE="$image" docker compose --env-file "$host_env" \
    -f "$directory/compose.yaml" -f "$directory/compose.aws.yaml" "$@"
}

version_compare() {
  local left_major left_minor left_patch right_major right_minor right_patch
  IFS=. read -r left_major left_minor left_patch <<<"$1"
  IFS=. read -r right_major right_minor right_patch <<<"$2"
  local left right
  for left in "$left_major" "$left_minor" "$left_patch"; do [[ "$left" =~ ^(0|[1-9][0-9]*)$ ]] || return 2; done
  for right in "$right_major" "$right_minor" "$right_patch"; do [[ "$right" =~ ^(0|[1-9][0-9]*)$ ]] || return 2; done
  if ((10#$left_major != 10#$right_major)); then ((10#$left_major > 10#$right_major)) && echo 1 || echo -1
  elif ((10#$left_minor != 10#$right_minor)); then ((10#$left_minor > 10#$right_minor)) && echo 1 || echo -1
  elif ((10#$left_patch != 10#$right_patch)); then ((10#$left_patch > 10#$right_patch)) && echo 1 || echo -1
  else echo 0
  fi
}

read_state() {
  local error_file
  error_file="$(mktemp "$work_dir/state-error.XXXXXX")"
  if previous_state="$(aws ssm get-parameter --name "$state_parameter" \
    --region "$aws_region" --query Parameter.Value --output text 2>"$error_file")"; then
    if jq -e 'type == "object" and . == {
      version: "0.0.0",
      image_digest: "",
      bundle_key: "",
      bundle_checksum: ""
    }' <<<"$previous_state" >/dev/null; then
      previous_state=""
      return
    fi
    jq -e 'type == "object" and
      (.version | type == "string" and test("^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$")) and
      (.image_digest | type == "string" and test("^sha256:[0-9a-f]{64}$")) and
      (.bundle_key | type == "string" and length > 0) and
      (.bundle_checksum | type == "string" and test("^[a-f0-9]{64}$"))' \
      <<<"$previous_state" >/dev/null || {
        echo "production deployment state is malformed" >&2
        return 1
      }
  elif grep -q ParameterNotFound "$error_file"; then
    previous_state=""
  else
    echo "unable to read production deployment state" >&2
    return 1
  fi
}

wait_healthy() {
  local expected_image="$1" timeout="$2" deadline running_image health database_health
  deadline=$((SECONDS + timeout))
  while ((SECONDS <= deadline)); do
    running_image="$(docker inspect --format '{{.Config.Image}}' battlevive-bot 2>/dev/null || true)"
    health="$(docker inspect --format '{{.State.Health.Status}}' battlevive-bot 2>/dev/null || true)"
    database_health="$(docker inspect --format '{{.State.Health.Status}}' battlevive-postgres 2>/dev/null || true)"
    if [[ "$running_image" == "$expected_image" && "$health" == healthy && "$database_health" == healthy ]]; then
      return 0
    fi
    sleep "$health_poll"
  done
  return 1
}

emit_metric() {
  local metric="$1"
  if ! aws cloudwatch put-metric-data --namespace Battlevive/Production \
    --metric-name "$metric" --value 1 --unit Count \
    --region "$aws_region" >/dev/null; then
    echo "warning: deployment telemetry failed for $metric" >&2
  fi
}

rollback() {
  if [[ "$transition_started" != true ]]; then return 0; fi
  if [[ -z "$previous_dir" || -z "$previous_image" ]]; then
    [[ -z "$active_dir" ]] || compose "$active_dir" stop bot
    return 0
  fi
  docker pull "$previous_image"
  image="$previous_image"
  compose "$previous_dir" up -d db
  compose "$previous_dir" run --rm migration
  compose "$previous_dir" up -d bot
  wait_healthy "$previous_image" "$rollback_timeout" || return 1
  ln -sfn "$previous_dir" "$battlevive_root/current"
}

on_exit() {
  local status=$?
  trap - EXIT
  if [[ "$complete" != true ]]; then
    if ! rollback; then
      echo "CRITICAL: rollback failed health or digest verification" >&2
    fi
    emit_metric DeploymentFailure
  fi
  rm -rf -- "$work_dir"
  exit "$status"
}
trap on_exit EXIT

read_state
if [[ -n "$previous_state" ]]; then
  current_version="$(jq -r .version <<<"$previous_state")"
  comparison="$(version_compare "$version" "$current_version")" || {
    echo "production deployment version is malformed" >&2
    exit 1
  }
  if [[ "$comparison" == -1 ]]; then
    echo "refusing production downgrade from $current_version to $version" >&2
    exit 1
  fi
  if [[ "$comparison" == 0 && "$(jq -r .image_digest <<<"$previous_state")" != "$image_digest" ]]; then
    echo "refusing a different digest for deployed version $version" >&2
    exit 1
  fi
fi

if [[ -L "$battlevive_root/current" ]]; then
  previous_dir="$(readlink -f "$battlevive_root/current")"
  [[ -f "$previous_dir/.image" ]] && previous_image="$(<"$previous_dir/.image")"
fi

aws s3 cp "s3://$operations_bucket/$bundle_key" "$archive" --region "$aws_region"
printf '%s  %s\n' "$bundle_checksum" "$archive" | sha256sum -c - >/dev/null
mkdir "$stage"
if tar -tzf "$archive" | awk '$0 ~ /^\// || $0 ~ /(^|\/)\.\.($|\/)/ { exit 1 }'; then
  tar -xzf "$archive" --no-same-owner -C "$stage"
else
  echo "bundle contains an unsafe path" >&2
  exit 1
fi
[[ -f "$stage/SHA256SUMS" && -f "$stage/compose.yaml" && -f "$stage/compose.aws.yaml" ]] || {
  echo "bundle is incomplete" >&2; exit 1;
}
(cd "$stage" && sha256sum -c SHA256SUMS >/dev/null)
[[ -x "$stage/scripts/backup.sh" ]] || { echo "bundle backup helper is missing" >&2; exit 1; }

exec 8>"$operations_lock"
flock -w 60 8
env BATTLEVIVE_OPERATIONS_LOCK_HELD=1 \
  COMPOSE_FILE="$stage/compose.yaml" AWS_REGION_NAME="$aws_region" \
  OPERATIONS_BUCKET="$operations_bucket" HOST_ENV_FILE="$host_env" \
  "$stage/scripts/backup.sh" --type pre-deploy --verify
transition_started=true
if [[ -n "$previous_dir" ]]; then
  image="${previous_image:-$image}"
  compose "$previous_dir" stop bot
fi

image="voxix/battlevive-bot@${image_digest}"
active_dir="$stage"
docker pull "$image"
compose "$stage" up -d db
compose "$stage" run --rm migration
compose "$stage" up -d bot
wait_healthy "$image" "$health_timeout" || {
  echo "deployment failed digest or health verification" >&2
  exit 1
}

if [[ -e "$release_dir" && "$(readlink -f "$battlevive_root/current" 2>/dev/null || true)" == "$release_dir" ]]; then
  echo "refusing to replace the active release directory" >&2
  exit 1
fi
rm -rf -- "$release_dir"
mv "$stage" "$release_dir"
active_dir="$release_dir"
printf '%s\n' "$image" >"$release_dir/.image"
new_state="$(jq -cn --arg version "$version" --arg image_digest "$image_digest" \
  --arg bundle_key "$bundle_key" --arg bundle_checksum "$bundle_checksum" \
  '{version: $version, image_digest: $image_digest,
    bundle_key: $bundle_key, bundle_checksum: $bundle_checksum}')"
printf '%s\n' "$new_state" >"$release_dir/.deployment.json"
ln -sfn "$release_dir" "$battlevive_root/current"
aws ssm put-parameter --name "$state_parameter" --type String --overwrite \
  --value "$new_state" --region "$aws_region" >/dev/null

complete=true
emit_metric DeploymentSuccess
