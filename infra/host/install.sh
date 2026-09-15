#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 && ${ALLOW_NON_ROOT_FOR_TESTS:-0} != 1 ]]; then
  echo "install.sh must run as root." >&2
  exit 1
fi

source_dir=${BATTLEVIVE_BUNDLE_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}
install_root=${INSTALL_ROOT:-}
AWS_CLI=${AWS_CLI:-aws}
AWS_REGION=${AWS_REGION:-eu-north-1}
BATTLEVIVE_SLOT=${BATTLEVIVE_SLOT:?BATTLEVIVE_SLOT must be blue or green}
[[ $BATTLEVIVE_SLOT == blue || $BATTLEVIVE_SLOT == green ]] || { echo "invalid BATTLEVIVE_SLOT" >&2; exit 2; }
RUNTIME_UID=${RUNTIME_UID:-10001}
RUNTIME_GID=${RUNTIME_GID:-10001}
OPERATIONS_BUCKET=${OPERATIONS_BUCKET:?OPERATIONS_BUCKET must be supplied by the slot deployment document}

prefix() { printf '%s%s' "$install_root" "$1"; }
install -d -m 0755 "$(prefix /usr/local/libexec/battlevive)" "$(prefix /run/lock)" "$(prefix /etc/systemd/system)" "$(prefix /etc/rsyslog.d)"
install -d -m 0750 -o "$EUID" -g "$RUNTIME_GID" "$(prefix /run/battlevive)"
install -d -m 0750 -o "$RUNTIME_UID" -g "$RUNTIME_GID" "$(prefix /var/lib/battlevive/bot)"
install -d -m 0755 "$(prefix /opt/battlevive/releases)"
install -m 0755 "$source_dir/bin/render-secrets.sh" "$(prefix /usr/local/libexec/battlevive/render-secrets)"
install -m 0755 "$source_dir/bin/compose" "$(prefix /usr/local/libexec/battlevive/compose)"
install -m 0755 "$source_dir/scripts/deploy.sh" "$(prefix /usr/local/libexec/battlevive/deploy)"
install -m 0755 "$source_dir/scripts/release/release_manifest.py" "$(prefix /usr/local/libexec/battlevive/release_manifest.py)"
install -m 0755 "$source_dir/scripts/publish-health.sh" "$(prefix /usr/local/libexec/battlevive/publish-health)"
install -m 0755 "$source_dir/scripts/publish-operations-freshness.sh" "$(prefix /usr/local/libexec/battlevive/publish-operations-freshness)"
install -m 0644 "$source_dir/systemd/"*.service "$source_dir/systemd/"*.timer "$(prefix /etc/systemd/system/)"
install -m 0644 "$source_dir/rsyslog/30-battlevive-messages.conf" "$(prefix /etc/rsyslog.d/30-battlevive-messages.conf)"
install -d -m 0755 "$(prefix /var/log)"
install -m 0640 -o "$EUID" -g "$EUID" /dev/null "$(prefix /var/log/battlevive-system.log)"

host_env=$(prefix /run/battlevive/host.env)
install -m 0600 -o "$EUID" -g "$EUID" /dev/null "$host_env"
cat >"$host_env" <<EOF
AWS_REGION=$AWS_REGION
BATTLEVIVE_SLOT=$BATTLEVIVE_SLOT
OPERATIONS_BUCKET=$OPERATIONS_BUCKET
BATTLEVIVE_DEPLOY_ROOT=/opt/battlevive
BATTLEVIVE_BOT_DATA_PATH=/var/lib/battlevive/bot
BATTLEVIVE_LOG_GROUP=/battlevive/production/application
BATTLEVIVE_MANIFEST_VALIDATOR=/usr/local/libexec/battlevive/release_manifest.py
EOF
chmod 0600 "$host_env"

if [[ -z $install_root ]]; then
  dnf install -y rsyslog
  systemctl enable --now rsyslog
  systemctl daemon-reload
  systemctl enable --now battlevive-health.timer
  systemctl enable --now battlevive-secrets.service
fi
