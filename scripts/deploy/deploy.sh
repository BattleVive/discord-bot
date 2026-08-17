#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "usage: deploy.sh --version X.Y.Z --image-digest sha256:... --bundle-key KEY --bundle-checksum SHA256 --target-selector TAGS" >&2
  exit 2
}

version=""
image_digest=""
bundle_key=""
bundle_checksum=""
target_selector=""
while (($#)); do
  case "$1" in
    --version) version="${2:-}"; shift 2 ;;
    --image-digest) image_digest="${2:-}"; shift 2 ;;
    --bundle-key) bundle_key="${2:-}"; shift 2 ;;
    --bundle-checksum) bundle_checksum="${2:-}"; shift 2 ;;
    --target-selector) target_selector="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done

[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || usage
[[ "$image_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "image must be an immutable sha256 digest" >&2
  exit 2
}
[[ "$bundle_checksum" =~ ^[0-9a-f]{64}$ ]] || usage
[[ -n "$bundle_key" && "$bundle_key" != /* && "$bundle_key" != *".."* ]] || usage
[[ -n "$target_selector" ]] || usage
: "${OPERATIONS_BUCKET:?OPERATIONS_BUCKET is required}"
: "${AWS_REGION:?AWS_REGION is required}"

deploy_root="${BATTLEVIVE_DEPLOY_ROOT:-/var/lib/battlevive/deployments}"
health_timeout="${HEALTH_TIMEOUT_SECONDS:-240}"
health_poll="${HEALTH_POLL_SECONDS:-5}"
image="voxix/battlevive-bot@${image_digest}"
parameter_root="/battlevive/production/deployment"
mkdir -p "$deploy_root/releases"
exec 9>"$deploy_root/deploy.lock"
flock -n 9 || {
  echo "another deployment is active" >&2
  exit 1
}

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/battlevive-deploy.XXXXXX")"
archive="$work_dir/bundle.tar.gz"
stage="$work_dir/stage"
digest_prefix="${image_digest#sha256:}"
release_dir="$deploy_root/releases/$version-${digest_prefix:0:12}"
previous_dir=""
previous_image=""
transition_started=false
complete=false

if [[ -L "$deploy_root/current" ]]; then
  previous_dir="$(readlink -f "$deploy_root/current")"
  if [[ -f "$previous_dir/.image" ]]; then
    previous_image="$(<"$previous_dir/.image")"
  fi
fi

compose() {
  local directory="$1"
  shift
  BATTLEVIVE_IMAGE="$image" docker compose \
    -f "$directory/compose.yaml" -f "$directory/compose.aws.yaml" "$@"
}

emit_metric() {
  local metric="$1" value="$2"
  aws cloudwatch put-metric-data \
    --namespace Battlevive/Deployment \
    --metric-name "$metric" \
    --value "$value" \
    --unit Count \
    --dimensions "TargetSelector=$target_selector" \
    --region "$AWS_REGION" >/dev/null
}

rollback() {
  if [[ "$transition_started" != true || -z "$previous_dir" || -z "$previous_image" ]]; then
    return
  fi
  docker pull "$previous_image"
  image="$previous_image"
  compose "$previous_dir" up -d db
  compose "$previous_dir" run --rm migration
  compose "$previous_dir" up -d bot
  ln -sfn "$previous_dir" "$deploy_root/current"
}

on_exit() {
  local status=$?
  trap - EXIT
  if [[ "$complete" != true ]]; then
    rollback || true
    emit_metric DeploymentFailure 1 || true
  fi
  rm -rf -- "$work_dir"
  exit "$status"
}
trap on_exit EXIT

aws s3 cp "s3://$OPERATIONS_BUCKET/$bundle_key" "$archive" --region "$AWS_REGION"
printf '%s  %s\n' "$bundle_checksum" "$archive" | sha256sum -c - >/dev/null

mkdir "$stage"
if tar -tzf "$archive" | awk '$0 ~ /^\// || $0 ~ /(^|\/)\.\.($|\/)/ { exit 1 }'; then
  tar -xzf "$archive" --no-same-owner -C "$stage"
else
  echo "bundle contains an unsafe path" >&2
  exit 1
fi
[[ -f "$stage/SHA256SUMS" && -f "$stage/compose.yaml" && -f "$stage/compose.aws.yaml" ]] || {
  echo "bundle is incomplete" >&2
  exit 1
}
(cd "$stage" && sha256sum -c SHA256SUMS >/dev/null)
[[ -x "$stage/scripts/backup.sh" ]] || {
  echo "bundle backup helper is missing or not executable" >&2
  exit 1
}

"$stage/scripts/backup.sh" --type pre-deploy --verify
transition_started=true
if [[ -n "$previous_dir" ]]; then
  image="${previous_image:-$image}"
  compose "$previous_dir" stop bot
fi

image="voxix/battlevive-bot@${image_digest}"
docker pull "$image"
compose "$stage" up -d db
compose "$stage" run --rm migration
compose "$stage" up -d bot

deadline=$((SECONDS + health_timeout))
while ((SECONDS <= deadline)); do
  running_image="$(docker inspect --format '{{.Config.Image}}' battlevive-bot 2>/dev/null || true)"
  health="$(docker inspect --format '{{.State.Health.Status}}' battlevive-bot 2>/dev/null || true)"
  database_health="$(docker inspect --format '{{.State.Health.Status}}' battlevive-postgres 2>/dev/null || true)"
  if [[ "$running_image" == "$image" && "$health" == healthy && "$database_health" == healthy ]]; then
    break
  fi
  sleep "$health_poll"
done
[[ "$running_image" == "$image" && "$health" == healthy && "$database_health" == healthy ]] || {
  echo "deployment failed digest or health verification" >&2
  exit 1
}

rm -rf -- "$release_dir"
mv "$stage" "$release_dir"
printf '%s\n' "$image" >"$release_dir/.image"
jq -n \
  --arg version "$version" \
  --arg image_digest "$image_digest" \
  --arg bundle_key "$bundle_key" \
  --arg bundle_checksum "$bundle_checksum" \
  '{version: $version, image_digest: $image_digest,
    bundle_key: $bundle_key, bundle_checksum: $bundle_checksum}' \
  >"$release_dir/.deployment.json"
ln -sfn "$release_dir" "$deploy_root/current"

aws ssm put-parameter --name "$parameter_root/version" --type String --overwrite \
  --value "$version" --region "$AWS_REGION" >/dev/null
aws ssm put-parameter --name "$parameter_root/image-digest" --type String --overwrite \
  --value "$image_digest" --region "$AWS_REGION" >/dev/null
aws ssm put-parameter --name "$parameter_root/bundle-key" --type String --overwrite \
  --value "$bundle_key" --region "$AWS_REGION" >/dev/null
emit_metric DeploymentSuccess 1
complete=true
