#!/usr/bin/env bash
# Writes Web_Application/.env and Factory_Server/.env without ever printing secrets.
# Run as your normal user (no sudo):  bash make-env-files.sh
set -euo pipefail

REPO="$HOME/PreCapstone"
DJANGO_ENV="$REPO/Web_Application/.env"
FASTAPI_ENV="$REPO/Factory_Server/.env"

for f in "$DJANGO_ENV" "$FASTAPI_ENV"; do
  if [ -e "$f" ]; then echo "$f already exists, not overwriting. Delete it first if you want a new one."; exit 1; fi
done

DB_PASS=""
while [ -z "$DB_PASS" ]; do
  read -r -s -p "Postgres password for factory_app (the one you chose in the setup script): " DB_PASS; echo
done
# check it against Postgres before writing anything
if ! PGPASSWORD="$DB_PASS" psql -h localhost -p 6900 -U factory_app -d factory_tracker -c "select 1" >/dev/null 2>&1; then
  echo "Postgres rejected that password. Nothing written; run again."; exit 1
fi
SECRET_KEY=$("$REPO/Web_Application/.venv/bin/python" -c "import secrets; print(secrets.token_urlsafe(50))")

umask 077
cat > "$DJANGO_ENV" <<EOF
SECRET_KEY=$SECRET_KEY
DEBUG=False
ALLOWED_HOSTS=127.0.0.1,localhost,factory-server,factory-server.local,10.1.140.220,192.168.8.200
DB_NAME=dashboard
DB_USER=factory_app
DB_PASSWORD=$DB_PASS
DB_HOST=localhost
DB_PORT=6900
STAGNATION_DEFAULT_HOURS=24
SESSION_HOURS=12
EOF

cat > "$FASTAPI_ENV" <<EOF
DB_HOST=localhost
DB_PORT=6900
DB_NAME=factory_tracker
DB_USER=factory_app
DB_PASSWORD=$DB_PASS
EOF

echo "Wrote both .env files (mode 600, readable only by you)."
