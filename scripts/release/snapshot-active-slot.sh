#!/usr/bin/env bash
# Create the explicit final snapshot required before retiring an active slot.
set -euo pipefail

active=${1:?active slot required}
[[ $active == blue || $active == green ]] || { echo "active slot must be blue or green" >&2; exit 2; }
snapshot="battlevive-${active}-$(date -u +%Y%m%d%H%M%S)"
aws rds create-db-snapshot --db-instance-identifier "battlevive-${active}-postgres" --db-snapshot-identifier "$snapshot" >/dev/null
aws rds wait db-snapshot-available --db-snapshot-identifier "$snapshot"
echo "$snapshot"
