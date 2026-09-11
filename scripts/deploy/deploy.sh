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
validator=${BATTLEVIVE_MANIFEST_VALIDATOR:-}
if [[ -z $validator ]]; then
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  validator="$script_dir/../release/release_manifest.py"
fi
python "$validator" --validate <"$manifest"
for service in gateway-service upstream-service image-renderer; do
  digest=$(jq -er --arg service "$service" '.services[$service].digest' "$manifest")
  image="ghcr.io/BattleVive/${service}@${digest}"
  docker pull "$image"
done
export BATTLEVIVE_SLOT="$slot"
export BATTLEVIVE_GATEWAY_IMAGE="ghcr.io/BattleVive/gateway-service@$(jq -er '.services["gateway-service"].digest' "$manifest")"
export BATTLEVIVE_UPSTREAM_IMAGE="ghcr.io/BattleVive/upstream-service@$(jq -er '.services["upstream-service"].digest' "$manifest")"
export BATTLEVIVE_RENDERER_IMAGE="ghcr.io/BattleVive/image-renderer@$(jq -er '.services["image-renderer"].digest' "$manifest")"
if [[ -x /usr/local/libexec/battlevive/compose ]]; then
  compose=(/usr/local/libexec/battlevive/compose)
else
  compose=(docker compose)
fi
"${compose[@]}" -f "$compose_file" run --rm migration
"${compose[@]}" -f "$compose_file" up -d --remove-orphans upstream-data image-renderer gateway
"${compose[@]}" -f "$compose_file" ps --status running gateway | grep -q gateway
