#!/usr/bin/env bash
# Installs the nightly Postgres backup (script + systemd timer at 03:00) and runs one test backup.
# Run from this folder:  sudo bash install-backups.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR=/var/backups/postgres

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0"; exit 1; fi

install -d -o postgres -g postgres -m 700 "$BACKUP_DIR"
install -m 755 "$HERE/pg-nightly-backup.sh" /usr/local/bin/pg-nightly-backup.sh
install -m 644 "$HERE/pg-nightly-backup.service" "$HERE/pg-nightly-backup.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now pg-nightly-backup.timer

echo "== Test run =="
systemctl start pg-nightly-backup.service
ls -lh "$BACKUP_DIR"
systemctl list-timers pg-nightly-backup.timer --no-pager | head -2
