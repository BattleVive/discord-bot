#!/usr/bin/env bash
# Promotion is intentionally explicit: all AWS mutations use the inactive slot.
set -euo pipefail
active=${1:?active slot required}
candidate=${2:?candidate slot required}
manifest=${3:?manifest required}
[[ $active != "$candidate" ]] || { echo "active and candidate slots must differ" >&2; exit 2; }
python scripts/release/release_manifest.py --validate <"$manifest"
snapshot="battlevive-${active}-$(date -u +%Y%m%d%H%M%S)"
aws rds create-db-snapshot --db-instance-identifier "battlevive-${active}-postgres" --db-snapshot-identifier "$snapshot" >/dev/null
aws rds wait db-snapshot-available --db-snapshot-identifier "$snapshot"
echo "$snapshot"
