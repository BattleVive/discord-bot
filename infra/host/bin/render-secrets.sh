#!/usr/bin/env bash
set -euo pipefail

AWS_CLI=${AWS_CLI:-aws}
AWS_REGION_NAME=${AWS_REGION_NAME:-eu-north-1}
BATTLEVIVE_SECRET_DIR=${BATTLEVIVE_SECRET_DIR:-/run/battlevive}
PARAMETER_PREFIX=${PARAMETER_PREFIX:-/battlevive/production/secrets}
RUNTIME_UID=${RUNTIME_UID:-10001}
RUNTIME_GID=${RUNTIME_GID:-10001}

if [[ $EUID -ne 0 && ${ALLOW_NON_ROOT_FOR_TESTS:-0} != 1 ]]; then
  echo "render-secrets must run as root." >&2
  exit 1
fi

secret_names=(
  database-url
  discord-token
  supabase-api-key
  bootstrap-jwt
  bootstrap-refresh-token
  postgres-password
)

if [[ ${ALLOW_NON_ROOT_FOR_TESTS:-0} == 1 && -z ${RUNTIME_GID_TEST_OVERRIDE:-} ]]; then
  RUNTIME_UID=$EUID
fi

umask 027
install -d -m 0750 -o "$EUID" -g "$RUNTIME_GID" "$BATTLEVIVE_SECRET_DIR"
staging=$(mktemp -d "$BATTLEVIVE_SECRET_DIR/.render.XXXXXX")
cleanup() {
  rm -rf -- "$staging"
}
trap cleanup EXIT

for name in "${secret_names[@]}"; do
  "$AWS_CLI" ssm get-parameter \
    --region "$AWS_REGION_NAME" \
    --name "$PARAMETER_PREFIX/$name" \
    --with-decryption \
    --query Parameter.Value \
    --output text >"$staging/$name"
  # AWS CLI text output appends one record separator; secret files contain only
  # the parameter bytes expected by *_FILE consumers.
  size=$(stat -c %s "$staging/$name")
  if [[ $size -gt 0 ]]; then
    truncate -s $((size - 1)) "$staging/$name"
  fi
  if [[ ! -s $staging/$name ]]; then
    echo "Parameter $PARAMETER_PREFIX/$name is empty; previous secret set was preserved." >&2
    exit 1
  fi
  chown "$EUID:$RUNTIME_GID" "$staging/$name"
  chmod 0640 "$staging/$name"
done

chown "$EUID:$RUNTIME_GID" "$BATTLEVIVE_SECRET_DIR"
chmod 0750 "$BATTLEVIVE_SECRET_DIR"

for name in "${secret_names[@]}"; do
  mv -f -- "$staging/$name" "$BATTLEVIVE_SECRET_DIR/$name"
done

echo "Rendered ${#secret_names[@]} runtime secret files." >&2
