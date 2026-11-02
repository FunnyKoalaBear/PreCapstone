#!/usr/bin/env bash
# Installs VS Code (snap, updates itself).
# Run:  sudo bash install-vscode.sh
set -euo pipefail

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0"; exit 1; fi
snap install code --classic
echo "Done. Sign in via the Accounts icon -> Backup and Sync Settings to get your extensions."
