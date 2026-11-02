#!/usr/bin/env bash
# Dumps every non-template database, then deletes dumps older than 14 days.
set -euo pipefail
stamp=$(date +%F)
for db in $(psql -p 6900 -Atc "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres'"); do
  pg_dump -p 6900 -Fc -f "/var/backups/postgres/$db-$stamp.dump" "$db"
done
find "/var/backups/postgres" -name '*.dump' -mtime +14 -delete
