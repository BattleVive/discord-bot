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
)
token_parameter_name=/battlevive/production/tokens

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
  read -r -s -p "Enter $prompt: " secret_value
  echo
  if [[ -z $secret_value ]]; then
    echo "Refusing empty value for $parameter_name." >&2
    exit 1
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

existing_token_parameter=$("$AWS_CLI" ssm describe-parameters \
  --profile "$AWS_PROFILE_NAME" \
  --region "$AWS_REGION_NAME" \
  --parameter-filters "Key=Name,Option=Equals,Values=$token_parameter_name" \
  --query 'Parameters[0].Name' \
  --output text)
case $existing_token_parameter in
  "$token_parameter_name")
    echo "Retained existing runtime token state."
    ;;
  None)
    "$AWS_CLI" ssm put-parameter \
      --profile "$AWS_PROFILE_NAME" \
      --region "$AWS_REGION_NAME" \
      --name "$token_parameter_name" \
      --type SecureString \
      --value '{}' >/dev/null
    echo "Initialized empty runtime token state."
    ;;
  *)
    echo "Unable to determine runtime token parameter state." >&2
    exit 1
    ;;
esac

echo "All runtime parameters were stored. Run render-secrets on the host before deployment."
