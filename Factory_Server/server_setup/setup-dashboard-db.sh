#!/usr/bin/env bash
# Creates the Django `dashboard` DB (owned by factory_app), restores the laptop dump, opens 8080 to the LAN.
# Run:  sudo bash setup-dashboard-db.sh /path/to/factory.dump
set -euo pipefail

PG_PORT=6900
DB_NAME="dashboard"
DB_USER="factory_app"
LAN_CIDRS="192.168.8.0/24 10.1.140.0/24"
DUMP="${1:?usage: sudo bash $0 /path/to/factory.dump}"

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0 $DUMP"; exit 1; fi

echo "== Create database $DB_NAME =="
runuser -u postgres -- psql -p "$PG_PORT" -v ON_ERROR_STOP=1 -v db="$DB_NAME" -v user="$DB_USER" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'user')
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SQL

echo "== Restore dump (objects become owned by $DB_USER) =="
# laptop role "precapstone" doesn't exist here, so drop owners/grants and create everything as factory_app
runuser -u postgres -- pg_restore -p "$PG_PORT" -d "$DB_NAME" --no-owner --no-acl --role="$DB_USER" --exit-on-error < "$DUMP"

echo "== Row counts =="
runuser -u postgres -- psql -p "$PG_PORT" -d "$DB_NAME" -Atc \
  "SELECT 'django_migrations', count(*) FROM django_migrations UNION ALL
   SELECT 'auth_user', count(*) FROM auth_user UNION ALL
   SELECT 'orders_order', count(*) FROM orders_order UNION ALL
   SELECT 'tracking_event', count(*) FROM tracking_event"

echo "== Firewall: Django 8080 from LAN =="
for cidr in $LAN_CIDRS; do ufw allow from "$cidr" to any port 8080 proto tcp; done

echo
echo "Done."
