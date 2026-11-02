#!/usr/bin/env bash
# Installs Tailscale from its official apt repo and starts the login.
# Run:  sudo bash install-tailscale.sh
set -euo pipefail

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0"; exit 1; fi
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")

echo "== Tailscale repo =="
curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/$CODENAME.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/$CODENAME.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list

echo "== Install =="
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y install tailscale
systemctl enable --now tailscaled

echo "== Log in (open the URL it prints) =="
# --operator lets your normal user run tailscale commands without sudo
tailscale up --operator="$SUDO_USER" --hostname=factory-server

echo
tailscale status | head -5
echo "Done."
