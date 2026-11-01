#!/usr/bin/env bash
# One-time setup for the factory server mini PC (Ubuntu 26.04 Desktop).
# Run after installing Ubuntu:  sudo bash setup-factory-server.sh
set -euo pipefail

HOSTNAME_NEW="factory-server"   # reachable as factory-server.local via Avahi
TIMEZONE="Asia/Tokyo"
PG_PORT=6900                    # same as the laptop dev setup, so .env DB_PORT stays 6900
DB_NAME="factory_tracker"
DB_USER="factory_app"
LAN_CIDRS="192.168.8.0/24 10.1.140.0/24"  # factory GL.iNet router + university LAN (remove later)

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0"; exit 1; fi

echo "== Hostname and timezone =="
hostnamectl set-hostname "$HOSTNAME_NEW"
sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$HOSTNAME_NEW/" /etc/hosts
timedatectl set-timezone "$TIMEZONE"

echo "== Packages =="
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y full-upgrade
DEBIAN_FRONTEND=noninteractive apt-get -y install \
  openssh-server avahi-daemon postgresql python3-venv python3-pip git ufw unattended-upgrades curl

echo "== Never sleep (24/7 server) =="
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
# waits for every NIC incl. the unused Wi-Fi, so boot hung 2 min
systemctl mask systemd-networkd-wait-online.service

echo "== Services start on boot =="
# ssh is already socket-activated (ssh.socket), so it is not enabled here
systemctl enable --now avahi-daemon postgresql unattended-upgrades

echo "== Postgres on port $PG_PORT, local only =="
PG_CONF=$(ls /etc/postgresql/*/main/postgresql.conf | head -n1)
sed -i "s/^#\?port = .*/port = $PG_PORT/" "$PG_CONF"
systemctl restart postgresql

read -r -s -p "Choose a password for Postgres role $DB_USER: " DB_PASS; echo
# password goes in via env, not argv, so it never shows in ps
export DB_PASS
runuser -u postgres -- psql -p "$PG_PORT" -v ON_ERROR_STOP=1 \
  -v user="$DB_USER" -v db="$DB_NAME" <<'SQL'
\getenv pass DB_PASS
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'user', :'pass')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'user') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'user')
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SQL
sudo -u postgres psql -p "$PG_PORT" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c \
  "CREATE TABLE IF NOT EXISTS scans (id serial PRIMARY KEY, uid text, scanned_at timestamp DEFAULT now()); ALTER TABLE scans OWNER TO $DB_USER;"

echo "== Firewall: SSH, FastAPI from LAN, mDNS =="
ufw allow OpenSSH
for cidr in $LAN_CIDRS; do ufw allow from "$cidr" to any port 8000 proto tcp; done
ufw allow 5353/udp
ufw --force enable

echo
echo "Done. Reboot, then from the laptop:  ssh $SUDO_USER@$HOSTNAME_NEW.local"
echo "Postgres: localhost:$PG_PORT  db=$DB_NAME  user=$DB_USER"
