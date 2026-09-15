#!/usr/bin/env bash
# Deploy one immutable release manifest to one named blue/green slot.
set -euo pipefail

usage() { echo 'usage: deploy --slot blue|green --manifest FILE --compose-file FILE' >&2; exit 2; }
slot='' manifest='' compose_file=${BATTLEVIVE_COMPOSE_FILE:-docker-compose.aws.yml}
while (($#)); do
  case "$1" in
    --slot) slot=${2:-}; shift 2 ;;
    --manifest) manifest=${2:-}; shift 2 ;;
    --compose-file) compose_file=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[[ $slot == blue || $slot == green ]] && [[ -f $manifest ]] && [[ -f $compose_file ]] || usage
health_timeout=${HEALTH_TIMEOUT_SECONDS:-120}
[[ $health_timeout =~ ^[1-9][0-9]*$ ]] || { echo "HEALTH_TIMEOUT_SECONDS must be a positive integer" >&2; exit 2; }
validator=${BATTLEVIVE_MANIFEST_VALIDATOR:-}
if [[ -z $validator ]]; then
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  validator="$script_dir/../release/release_manifest.py"
fi
python3 "$validator" --validate <"$manifest"
install -d -m 0755 /run/lock
exec 9>"/run/lock/battlevive-${slot}-deploy.lock"
flock -n 9 || { echo "another deployment is active for slot $slot" >&2; exit 1; }
for service in gateway-service upstream-service image-renderer; do
  digest=$(jq -er --arg service "$service" '.services[$service].digest' "$manifest")
  image="ghcr.io/battlevive/${service}@${digest}"
  docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
done
export BATTLEVIVE_SLOT="$slot"
export BATTLEVIVE_GATEWAY_IMAGE="ghcr.io/battlevive/gateway-service@$(jq -er '.services["gateway-service"].digest' "$manifest")"
export BATTLEVIVE_UPSTREAM_IMAGE="ghcr.io/battlevive/upstream-service@$(jq -er '.services["upstream-service"].digest' "$manifest")"
export BATTLEVIVE_RENDERER_IMAGE="ghcr.io/battlevive/image-renderer@$(jq -er '.services["image-renderer"].digest' "$manifest")"
if [[ -x /usr/local/libexec/battlevive/compose ]]; then
  compose=(/usr/local/libexec/battlevive/compose)
else
  compose=(docker compose)
fi
"${compose[@]}" -f "$compose_file" run --rm migration
"${compose[@]}" -f "$compose_file" up -d --remove-orphans upstream-data image-renderer gateway
wait_for_healthy() {
  local service=$1 container health deadline
  container="$("${compose[@]}" -f "$compose_file" ps -q "$service")"
  [[ -n $container ]] || { echo "$service container was not created" >&2; exit 1; }
  deadline=$((SECONDS + health_timeout))
  while (( SECONDS <= deadline )); do
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container" 2>/dev/null || true)"
    [[ $health == healthy ]] && return 0
    [[ $health == unhealthy ]] && { echo "$service became unhealthy" >&2; exit 1; }
    sleep 2
  done
  echo "$service readiness timed out" >&2
  exit 1
}
wait_for_healthy upstream-data
wait_for_healthy gateway
