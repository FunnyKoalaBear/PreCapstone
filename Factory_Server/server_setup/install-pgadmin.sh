#!/usr/bin/env bash
# Installs pgAdmin 4 (desktop mode only, no web server) from pgAdmin's official apt repo.
# Run:  sudo bash install-pgadmin.sh
set -euo pipefail

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0"; exit 1; fi

KEYRING=/usr/share/keyrings/packages-pgadmin-org.gpg
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")

echo "== pgAdmin signing key and repo =="
curl -fsS https://www.pgadmin.org/static/packages_pgadmin_org.pub | gpg --dearmor --yes -o "$KEYRING"
echo "deb [signed-by=$KEYRING] https://ftp.postgresql.org/pub/pgadmin/pgadmin4/apt/$CODENAME pgadmin4 main" \
  > /etc/apt/sources.list.d/pgadmin4.list

echo "== Install =="
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y install pgadmin4-desktop

echo
echo "Done. Open pgAdmin 4 from the app menu."
echo "Add server: host localhost, port 6900, db factory_tracker, user factory_app"
