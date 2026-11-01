#!/usr/bin/env bash
# Restores one nightly dump over its database (stops the app using it first).
# Run:  sudo bash restore-backup.sh /var/backups/postgres/dashboard-2026-11-02.dump
set -euo pipefail

PG_PORT=6900
DB_USER="factory_app"
DUMP="${1:?usage: sudo bash $0 /var/backups/postgres/<db>-<date>.dump}"
DB=$(basename "$DUMP" | sed -E 's/-[0-9]{4}-[0-9]{2}-[0-9]{2}\.dump$//')

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0 $DUMP"; exit 1; fi
case "$DB" in
  dashboard) SERVICE=dashboard ;;
  factory_tracker) SERVICE=factory-api ;;
  *) echo "Unknown database '$DB'"; exit 1 ;;
esac

read -r -p "Overwrite database '$DB' with $DUMP? Type yes: " ans
[ "$ans" = "yes" ] || { echo "Cancelled."; exit 1; }

systemctl stop "$SERVICE"
# restore runs as root and reads the file, so postgres does not need access to it
runuser -u postgres -- pg_restore -p "$PG_PORT" -d "$DB" --clean --if-exists --no-owner --role="$DB_USER" < "$DUMP"
systemctl start "$SERVICE"
echo "Restored $DB and restarted $SERVICE."
