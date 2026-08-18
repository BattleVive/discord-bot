#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 && $1 == *:* ]] || { echo "usage: inspect-tag.sh IMAGE:TAG" >&2; exit 2; }
reference=$1
error_file=$(mktemp)
trap 'rm -f -- "$error_file"' EXIT

if manifest=$(docker buildx imagetools inspect --raw "$reference" 2>"$error_file"); then
  revision=$(jq -r '.annotations["org.opencontainers.image.revision"] // empty' <<<"$manifest")
  if [[ ! $revision =~ ^[0-9a-f]{40}$ ]]; then
    echo "refusing existing immutable tag without a verifiable OCI revision: $reference" >&2
    exit 1
  fi
  printf '%s\n' "$revision"
elif grep -Eqi 'manifest unknown|name_unknown' "$error_file"; then
  exit 4
else
  cat "$error_file" >&2
  echo "refusing to overwrite an unverifiable immutable tag: $reference" >&2
  exit 1
fi
