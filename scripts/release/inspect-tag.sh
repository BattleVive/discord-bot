#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 && $1 == *:* ]] || { echo "usage: inspect-tag.sh IMAGE:TAG" >&2; exit 2; }
reference=$1
repository=${reference%:*}
tag=${reference##*:}
[[ -n $repository && -n $tag ]] || { echo "usage: inspect-tag.sh IMAGE:TAG" >&2; exit 2; }

manifest_file=$(mktemp)
trap 'rm -f -- "$manifest_file"' EXIT

if ! token="$(curl --fail --silent --show-error --get \
  --data-urlencode 'service=registry.docker.io' \
  --data-urlencode "scope=repository:${repository}:pull" \
  'https://auth.docker.io/token' | jq -er '.token // .access_token // empty')"; then
  echo "Unable to obtain a Docker Hub pull token for $repository" >&2
  exit 1
fi

if ! status="$(curl --silent --show-error --output "$manifest_file" --write-out '%{http_code}' \
  --header "Authorization: Bearer $token" \
  --header 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' \
  "https://registry-1.docker.io/v2/${repository}/manifests/${tag}")"; then
  echo "Docker Hub manifest lookup request failed for $reference" >&2
  exit 1
fi

case "$status" in
  200)
    revision=$(jq -r '.annotations["org.opencontainers.image.revision"] // empty' "$manifest_file")
    if [[ ! $revision =~ ^[0-9a-f]{40}$ ]]; then
      echo "refusing existing immutable tag without a verifiable OCI revision: $reference" >&2
      exit 1
    fi
    printf '%s\n' "$revision"
    ;;
  404)
    exit 4
    ;;
  *)
    echo "Docker Hub manifest lookup returned HTTP $status for $reference" >&2
    exit 1
    ;;
esac
