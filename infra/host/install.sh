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
RUNTIME_UID=${RUNTIME_UID:-10001}
RUNTIME_GID=${RUNTIME_GID:-10001}
POSTGRES_UID=${POSTGRES_UID:-999}
POSTGRES_GID=${POSTGRES_GID:-999}
if [[ -z ${OPERATIONS_BUCKET:-} ]]; then
  OPERATIONS_BUCKET=$("$AWS_CLI" ssm get-parameter --region "$AWS_REGION" \
    --name /battlevive/production/config/operations-bucket \
    --query Parameter.Value --output text)
fi

prefix() { printf '%s%s' "$install_root" "$1"; }
install -d -m 0755 "$(prefix /usr/local/libexec/battlevive)" "$(prefix /run/lock)" "$(prefix /etc/systemd/system)" "$(prefix /etc/rsyslog.d)"
install -d -m 0750 -o "$EUID" -g "$RUNTIME_GID" "$(prefix /run/battlevive)"
install -d -m 0700 "$(prefix /var/lib/battlevive/backups)" "$(prefix /var/lib/battlevive/restore-verification)"
install -d -m 0750 -o "$RUNTIME_UID" -g "$RUNTIME_GID" "$(prefix /var/lib/battlevive/bot)"
install -d -m 0700 -o "$POSTGRES_UID" -g "$POSTGRES_GID" "$(prefix /var/lib/battlevive/postgresql)"
install -d -m 0755 "$(prefix /opt/battlevive/releases)"
install -m 0755 "$source_dir/bin/render-secrets.sh" "$(prefix /usr/local/libexec/battlevive/render-secrets)"
install -m 0755 "$source_dir/scripts/deploy.sh" "$(prefix /usr/local/libexec/battlevive/deploy)"
install -m 0755 "$source_dir/scripts/backup.sh" "$(prefix /usr/local/libexec/battlevive/backup)"
install -m 0755 "$source_dir/scripts/restore-verify.sh" "$(prefix /usr/local/libexec/battlevive/restore-verify)"
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
OPERATIONS_BUCKET=$OPERATIONS_BUCKET
BATTLEVIVE_DEPLOY_ROOT=/opt/battlevive
BATTLEVIVE_BOT_DATA_PATH=/var/lib/battlevive/bot
BATTLEVIVE_POSTGRES_DATA_PATH=/var/lib/battlevive/postgresql
BATTLEVIVE_LOG_GROUP=/battlevive/production/application
POSTGRES_USER=battlevive
POSTGRES_DB=battlevive
EOF
chmod 0600 "$host_env"

if [[ -z $install_root ]]; then
  dnf install -y rsyslog
  systemctl enable --now rsyslog
  systemctl daemon-reload
  systemctl enable --now battlevive-health.timer battlevive-operations-freshness.timer battlevive-backup.timer battlevive-restore-verify.timer
  systemctl enable battlevive-secrets.service
fi
