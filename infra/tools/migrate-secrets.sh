#!/usr/bin/env bash
# shellcheck disable=SC2016 # jq programs intentionally use their own $variables.
set -euo pipefail

AWS_CLI=${AWS_CLI:-aws}
JQ_CLI=${JQ_CLI:-jq}
AWS_PROFILE_NAME=${AWS_PROFILE_NAME:-battlevive-prod}
AWS_REGION_NAME=${AWS_REGION_NAME:-eu-north-1}

parameter_names=(
  /battlevive/production/secrets/database-url
  /battlevive/production/secrets/discord-token
  /battlevive/production/secrets/supabase-api-key
  /battlevive/production/secrets/bootstrap-jwt
  /battlevive/production/secrets/bootstrap-refresh-token
  /battlevive/production/secrets/postgres-password
  /battlevive/production/tokens
)

echo "This helper sends values directly to SSM and never prints them."
echo "Do not paste values into shell arguments, Terraform variables, or chat."

umask 077
workdir=$(mktemp -d)
cleanup() {
  unset secret_value
  rm -rf -- "$workdir"
}
trap cleanup EXIT INT TERM

for parameter_name in "${parameter_names[@]}"; do
  prompt=${parameter_name##*/}
  if [[ $parameter_name == /battlevive/production/tokens ]]; then
    prompt="tokens JSON (access_token and refresh_token)"
  fi
  read -r -s -p "Enter $prompt: " secret_value
  echo
  if [[ -z $secret_value ]]; then
    echo "Refusing empty value for $parameter_name." >&2
    exit 1
  fi
  if [[ $parameter_name == /battlevive/production/tokens ]]; then
    if ! printf '%s' "$secret_value" | "$JQ_CLI" -e \
      'type == "object" and (.access_token | type == "string" and length > 0) and (.refresh_token | type == "string" and length > 0)' \
      >/dev/null; then
      echo "Token JSON must contain non-empty access_token and refresh_token strings." >&2
      exit 1
    fi
  fi

  request="$workdir/request.json"
  printf '%s' "$secret_value" | "$JQ_CLI" -Rn \
    --arg name "$parameter_name" \
    '{Name: $name, Type: "SecureString", Tier: "Standard", Overwrite: true, Value: input}' \
    >"$request"
  unset secret_value
  "$AWS_CLI" ssm put-parameter \
    --profile "$AWS_PROFILE_NAME" \
    --region "$AWS_REGION_NAME" \
    --cli-input-json "file://$request" >/dev/null
  : >"$request"
  echo "Stored $parameter_name."
done

echo "All runtime parameters were stored. Run render-secrets on the host before deployment."
