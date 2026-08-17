#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "install.sh must run as root." >&2
  exit 1
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install -d -m 0755 /usr/local/libexec/battlevive
install -d -m 0755 /run/lock
install -d -m 0700 /run/battlevive /var/lib/battlevive/backups /var/lib/battlevive/restore-verification
install -m 0755 "$source_dir/bin/render-secrets.sh" /usr/local/libexec/battlevive/render-secrets
install -m 0755 "$source_dir/scripts/backup.sh" /usr/local/libexec/battlevive/backup
install -m 0755 "$source_dir/scripts/restore-verify.sh" /usr/local/libexec/battlevive/restore-verify
install -m 0755 "$source_dir/scripts/publish-health.sh" /usr/local/libexec/battlevive/publish-health
install -m 0755 "$source_dir/scripts/publish-operations-freshness.sh" /usr/local/libexec/battlevive/publish-operations-freshness
install -m 0644 "$source_dir/systemd/"*.service "$source_dir/systemd/"*.timer /etc/systemd/system/

if [[ ! -e /run/battlevive/host.env ]]; then
  install -m 0600 -o root -g root /dev/null /run/battlevive/host.env
  printf 'POSTGRES_USER=battlevive\nPOSTGRES_DB=battlevive\n' >/run/battlevive/host.env
fi

systemctl daemon-reload
systemctl enable --now battlevive-health.timer battlevive-operations-freshness.timer battlevive-backup.timer battlevive-restore-verify.timer
systemctl enable battlevive-secrets.service
