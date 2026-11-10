#!/usr/bin/env bash
# Django (gunicorn behind Caddy on :8080) and FastAPI (uvicorn on :8000) as systemd services.
# Run:  sudo bash setup-services.sh
set -euo pipefail

APP_USER="precap"
REPO="/home/$APP_USER/PreCapstone"
WEB="$REPO/Web_Application"
API="$REPO/Factory_Server"
API_VENV="/home/$APP_USER/venvs/factory_server"

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0"; exit 1; fi

echo "== Caddy =="
DEBIAN_FRONTEND=noninteractive apt-get -y install caddy
# home is 750, so Caddy joins the precap group to read static files
usermod -aG "$APP_USER" caddy
ADMIN_STATIC=$(ls -d "$WEB"/.venv/lib/python3*/site-packages/django/contrib/admin/static | head -n1)

cp -n /etc/caddy/Caddyfile /etc/caddy/Caddyfile.orig || true
cat > /etc/caddy/Caddyfile <<EOF
# Dashboard: static/media from disk, everything else to gunicorn
:8080 {
	encode gzip
	handle_path /static/* {
		@app file {
			root $WEB/static
		}
		handle @app {
			root * $WEB/static
			file_server
		}
		handle {
			root * $ADMIN_STATIC
			file_server
		}
	}
	handle_path /media/* {
		root * $WEB/media
		file_server
	}
	handle {
		reverse_proxy 127.0.0.1:8001
	}
}
EOF
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

echo "== dashboard.service (gunicorn) =="
cat > /etc/systemd/system/dashboard.service <<EOF
[Unit]
Description=Django dashboard (gunicorn)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=$APP_USER
WorkingDirectory=$WEB
ExecStart=$WEB/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3 --timeout 60
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "== print-worker.service (Django print queue) =="
cat > /etc/systemd/system/print-worker.service <<EOF
[Unit]
Description=Django queued PDF printer
After=network.target postgresql.service cups.service dashboard.service
Requires=postgresql.service

[Service]
User=$APP_USER
WorkingDirectory=$WEB
ExecStart=$WEB/.venv/bin/python manage.py print_worker
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "== factory-api.service (uvicorn) =="
cat > /etc/systemd/system/factory-api.service <<EOF
[Unit]
Description=FastAPI factory server (uvicorn)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=$APP_USER
WorkingDirectory=$API
ExecStart=$API_VENV/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now dashboard.service print-worker.service factory-api.service
systemctl enable caddy
systemctl restart caddy

sleep 3
systemctl --no-pager --lines=0 status caddy dashboard print-worker factory-api | grep -E "●|Active:"
echo
echo "Done."
