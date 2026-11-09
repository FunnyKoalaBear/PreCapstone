#!/usr/bin/env bash
# Print worker as a systemd service: prints the work sheets queued by Django (see Web_Application/docs/printing.md).
# Safe to run again. Run:  sudo bash setup-print-worker.sh
set -euo pipefail

APP_USER="precap"
WEB="/home/$APP_USER/PreCapstone/Web_Application"

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0"; exit 1; fi

echo "== print-worker.service =="
cat > /etc/systemd/system/print-worker.service <<EOF
[Unit]
Description=Work sheet print worker (Django print_worker)
After=network.target postgresql.service cups.service
Requires=postgresql.service

[Service]
User=$APP_USER
WorkingDirectory=$WEB
ExecStart=$WEB/.venv/bin/python manage.py print_worker
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now print-worker.service
sleep 2
systemctl --no-pager --lines=5 status print-worker | grep -E "●|Active:|print_worker"
