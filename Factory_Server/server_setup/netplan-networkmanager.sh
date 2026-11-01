#!/usr/bin/env bash
# Hands all networking (wired + Wi-Fi) to NetworkManager, as on a normal Ubuntu Desktop.
# Run:  sudo bash netplan-networkmanager.sh
set -euo pipefail

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0"; exit 1; fi
NM_FILE=/etc/netplan/01-network-manager-all.yaml

echo "== Current installer config (passwords masked) =="
sed -E 's/((password|psk|key)[^:]*:).*/\1 ********/I' /etc/netplan/00-installer-config.yaml

echo "== Backup to /root/netplan-backup/ =="
# backup lives outside /etc/netplan, because netplan reads every .yaml there
install -d -m 700 /root/netplan-backup
cp -a /etc/netplan/*.yaml /root/netplan-backup/

echo "== Write $NM_FILE =="
cat > "$NM_FILE" <<'EOF'
# Let NetworkManager manage all devices on this system
network:
  version: 2
  renderer: NetworkManager
EOF
chmod 600 "$NM_FILE"

echo "== Apply =="
netplan generate
netplan apply
sleep 8
nmcli -f DEVICE,TYPE,STATE,CONNECTION dev

echo
echo "Done. Undo: sudo rm $NM_FILE && sudo netplan apply"
