#!/usr/bin/env bash
# Adds a Funnel-only Caddy entrance (127.0.0.1:8081, marked HTTPS) and lets Django accept the .ts.net name.
# Run:  sudo bash setup-funnel.sh
set -euo pipefail

APP_USER="precap"
WEB="/home/$APP_USER/PreCapstone/Web_Application"
DJANGO_ENV="$WEB/.env"

if [ "$EUID" -ne 0 ]; then echo "Run with sudo: sudo bash $0"; exit 1; fi

TS_NAME=$(tailscale status --json | python3 -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")
echo "Tailscale name: $TS_NAME"
ADMIN_STATIC=$(ls -d "$WEB"/.venv/lib/python3*/site-packages/django/contrib/admin/static | head -n1)

echo "== Caddy: LAN entrance :8080 + Funnel entrance 127.0.0.1:8081 =="
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-funnel
cat > /etc/caddy/Caddyfile <<EOF
# Shared: static/media from disk
(files) {
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
}

# LAN (scanners, local browsers): plain HTTP
:8080 {
	import files
	handle {
		reverse_proxy 127.0.0.1:8001
	}
}

# Funnel only (localhost): visitors arrived over HTTPS, so tell gunicorn/Django
http://:8081 {
	bind 127.0.0.1
	import files
	handle {
		reverse_proxy 127.0.0.1:8001 {
			header_up X-Forwarded-Proto https
		}
	}
}
EOF
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy

echo "== Django: allow $TS_NAME =="
if grep -q "^ALLOWED_HOSTS=.*$TS_NAME" "$DJANGO_ENV"; then
  echo "already allowed"
else
  sed -i "/^ALLOWED_HOSTS=/ s/\$/,$TS_NAME/" "$DJANGO_ENV"
  echo "added"
fi
systemctl restart dashboard

echo "== Funnel: https://$TS_NAME -> 127.0.0.1:8081 =="
tailscale funnel --bg http://127.0.0.1:8081
tailscale funnel status

echo
echo "Done. Public URL: https://$TS_NAME/"
