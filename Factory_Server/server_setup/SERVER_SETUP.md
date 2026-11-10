# Factory server: setup from scratch

How to turn a fresh mini PC into the 24/7 factory server (Postgres + Django dashboard + FastAPI, reachable on the LAN and over HTTPS from anywhere).
First done on 2026-11-01 on an HP mini PC (8 GB RAM, 256 GB SSD). Scripts are in `server_setup/`; each one explains itself at the top.

## Result

| What | Where |
|---|---|
| Dashboard (LAN, scanners) | `http://factory-server.local:8080/` |
| Dashboard (anywhere, HTTPS) | `https://factory-server.<tailnet>.ts.net/` (Tailscale Funnel) |
| Scanner API | `POST /api/scan/`, header `X-API-Key` (see `Web_Application/docs/scanner-api.md`) |
| FastAPI (prototype) | `http://factory-server.local:8000/docs` |
| Postgres 18 | `localhost:6900` only; databases `factory_tracker` (FastAPI) and `dashboard` (Django), role `factory_app` |

```
ESP32 / browser -> Caddy :8080 -> /static, /media from disk
                               -> everything else -> gunicorn 127.0.0.1:8001 -> Django -> Postgres :6900
Internet -> Tailscale Funnel (HTTPS) -> Caddy 127.0.0.1:8081 (marks request as HTTPS) -> same gunicorn
```

## Rules
- Never expose Postgres; it listens on localhost only.
- You type every password yourself. Scripts never print them; `.env` files are mode 600 and never committed.
- No changes to `Web_Application` code are needed for this setup.

## 0. Install Ubuntu
- Ubuntu Server 26.04 LTS (Python 3.12+ needed for Django 6.1). Tick **OpenSSH server** in the installer. User `precap`.
- Then `sudo apt install ubuntu-desktop` if you want the GNOME desktop.
- If install media fails checksum checks, the netboot install (ISO loaded into RAM) is a reliable fallback.

## 1. Base system + Postgres + firewall
Check `HOSTNAME_NEW` and `LAN_CIDRS` at the top first (factory GL.iNet router = `192.168.8.0/24`).

```bash
sudo bash server_setup/setup-factory-server.sh
```
- Hostname `factory-server` (scanners find it as `factory-server.local` via Avahi), timezone Asia/Tokyo, full upgrade.
- Masks sleep/suspend/hibernate (24/7) and `systemd-networkd-wait-online` (it hung boot for 2 min waiting on the unused Wi-Fi).
- Installs Postgres on port 6900, asks you for the `factory_app` password, creates `factory_tracker` + `scans`.
- ufw: SSH, port 8000 from the LAN, 5353/udp (mDNS).

Then turn off GNOME's own auto-suspend (no sudo):
```bash
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
```

## 2. Wi-Fi via NetworkManager (desktop installs only)
The server installer leaves networking to systemd-networkd, so the GNOME Wi-Fi menu doesn't work properly.
```bash
sudo bash server_setup/netplan-networkmanager.sh
sudo systemctl disable --now systemd-networkd.service systemd-networkd.socket systemd-networkd-varlink.socket
```
Check `/etc/netplan/00-installer-config.yaml` for a hard-coded DNS server from the install network and remove it if it doesn't exist at the factory.

## 3. Nightly backups + VS Code
```bash
sudo bash backups/install-backups.sh
sudo bash server_setup/install-vscode.sh
```
- `backups/pg-nightly-backup.sh` dumps every database (found automatically) at 03:00 to `/var/backups/postgres/<db>-<date>.dump` and deletes dumps older than 14 days. It runs as the `postgres` user, so no password is stored.
- `pg-nightly-backup.timer` triggers `pg-nightly-backup.service`; `Persistent=true` catches up after a power-off. Check with `systemctl list-timers pg-nightly-backup.timer`, logs with `journalctl -u pg-nightly-backup`.
- Restore: `sudo bash backups/restore-backup.sh /var/backups/postgres/dashboard-<date>.dump` (stops the app, restores, restarts).
- Backups are on the same disk; copy them off the machine for real safety (S3 or a laptop over Tailscale).
- VS Code (snap): sign in to Settings Sync via the Accounts icon to get extensions and profiles.

Optional: pgAdmin (desktop app, no web server):
```bash
sudo bash server_setup/install-pgadmin.sh
```
On this mini PC it crashed on GPU init; fix the menu entry once (no sudo):
```bash
sed -E 's|^(Exec=[^ ]*pgadmin4)|\1 --disable-gpu|' /usr/share/applications/pgadmin4.desktop > ~/.local/share/applications/pgadmin4.desktop
```
Connect with host `localhost`, port `6900`, user `factory_app`.

## 4. Git, GitHub, clone
```bash
sudo apt install gh
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh repo clone FunnyKoalaBear/PreCapstone ~/PreCapstone
git config --global user.name "FunnyKoalaBear"
git config --global user.email "you@example.com"
```
If the terminal prompt misbehaves, run `gh auth login` with `< /dev/null`: it then just prints the device code and URL.

## 5. Python environments
```bash
cd ~/PreCapstone/Web_Application
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt gunicorn

python3 -m venv ~/venvs/factory_server
~/venvs/factory_server/bin/pip install -r ~/PreCapstone/Factory_Server/requirements.txt
```
The FastAPI venv lives outside the repo because the repo does not ignore `Factory_Server/.venv`.
Also run `echo "__pycache__/" >> ~/PreCapstone/.git/info/exclude` (local ignore, not committed).

## 6. Django database
Either restore a dump from another machine (made with `pg_dump -Fc`):
```bash
sudo bash server_setup/setup-dashboard-db.sh /path/to/factory.dump
```
It creates `dashboard` owned by `factory_app`, restores with `--no-owner` (the laptop role doesn't exist here), prints row counts and opens port 8080 to the LAN.
Or, for an empty start, create the database and run `migrate` + `createsuperuser` (see `Web_Application/docs/setup.md`).
Uploaded files (`Web_Application/media/`) are not in the dump; copy that folder separately.

## 7. `.env` files, migrate, services
```bash
bash server_setup/make-env-files.sh
cd ~/PreCapstone/Web_Application && .venv/bin/python manage.py migrate
sudo bash ~/PreCapstone/Factory_Server/server_setup/setup-services.sh
```
- `make-env-files.sh` asks for the `factory_app` password, checks it against Postgres, generates a Django `SECRET_KEY`, sets `DEBUG=False` and `ALLOWED_HOSTS`, and writes both `.env` files (600). Edit `ALLOWED_HOSTS` in the script if the server's IPs differ.
- `setup-services.sh` installs Caddy and creates `dashboard.service` (gunicorn) and `factory-api.service` (uvicorn). All restart on failure and start at boot. Caddy joins the `precap` group so it can read static files in the 750 home folder.

Why gunicorn + Caddy instead of `runserver`: runserver is single-threaded and only serves CSS with `DEBUG=True` (which shows source code on errors). gunicorn runs several workers; Caddy serves files and can add HTTPS.

## 8. Remote access: Tailscale + Funnel
```bash
sudo bash server_setup/install-tailscale.sh
sudo bash server_setup/setup-funnel.sh
```
- Log in at the printed URL. HTTPS and Funnel must be enabled for the tailnet (Tailscale admin console, one time).
- Tailscale alone = private access for developers (SSH, VS Code Remote-SSH) from anywhere. Free plan.
- Funnel = public HTTPS URL `https://factory-server.<tailnet>.ts.net/`. The script adds a localhost-only Caddy entrance (`http://:8081` bound to 127.0.0.1) that sends `X-Forwarded-Proto: https`, so gunicorn reports HTTPS to Django and CSRF checks on login pass. It also appends the `.ts.net` name to `ALLOWED_HOSTS`.
- Off / on: `tailscale funnel --https=443 off` / `tailscale funnel --bg http://127.0.0.1:8081`.
- Day-to-day commands and troubleshooting: `TAILSCALE.md`.

## 9. Check
```bash
systemctl is-active postgresql caddy dashboard print-worker factory-api tailscaled
curl -I http://factory-server.local:8080/            # 302 to /login/
curl -X POST http://factory-server.local:8080/api/scan/ -H "Content-Type: application/json" -d '{"tag_uid":"X"}'   # 401 bad_key
systemd-analyze                                       # boot should not wait 2 min
```
Reboot once and check again that everything comes back on its own.

## Everyday

### After every `git pull`
gunicorn and uvicorn load the Python code once at start, but Django reads templates from disk. Without a restart, new templates meet old code and pages fail with error 500 (e.g. `NoReverseMatch` for a new URL name).
```bash
cd ~/PreCapstone && git pull
cd Web_Application
.venv/bin/pip install -r requirements.txt                  # only if requirements.txt changed
.venv/bin/python manage.py migrate                         # applies new migrations, harmless if none
sudo systemctl restart dashboard factory-api
systemctl is-active dashboard factory-api                  # both should print "active"
```
Static files need no extra step: Caddy serves `Web_Application/static/` straight from disk.

### Services
`dashboard` is short for `dashboard.service`, the unit file `/etc/systemd/system/dashboard.service` written by `setup-services.sh`.
```bash
systemctl status dashboard              # running? since when? last log lines
systemctl cat dashboard                 # show the unit file
sudo systemctl restart dashboard        # stop + start (loads new code)
sudo journalctl -u dashboard -f         # live logs (also caddy, factory-api, pg-nightly-backup); without sudo it may show nothing
```

### Raw start commands (what systemd runs)
For debugging: run one piece by hand in its own terminal to see errors directly. Stop its service first, or the port is already taken. Order matters: Postgres before the apps.
```bash
sudo systemctl stop dashboard factory-api caddy

# 1. Postgres (usually left running)
sudo pg_ctlcluster 18 main start

# 2. Django dashboard (gunicorn) on 127.0.0.1:8001
cd ~/PreCapstone/Web_Application
.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3 --timeout 60

# 3. FastAPI on 0.0.0.0:8000 (add --reload while editing)
cd ~/PreCapstone/Factory_Server
~/venvs/factory_server/bin/uvicorn main:app --host 0.0.0.0 --port 8000

# 4. Caddy: :8080 (LAN) and 127.0.0.1:8081 (Funnel) -> gunicorn
sudo -u caddy caddy run --environ --config /etc/caddy/Caddyfile

# 5. Public HTTPS URL (Funnel setting persists, normally already on)
sudo tailscale funnel --bg http://127.0.0.1:8081
```
Ctrl+C stops a hand-run piece. When done, go back to the services: `sudo systemctl start caddy dashboard factory-api`.

## At the factory
- DHCP reservation for the server on the GL.iNet router (fixed IP for `ALLOWED_HOSTS` and firmware).
- Remove ufw rules for any temporary network (`sudo ufw status numbered`, `sudo ufw delete N`).
- Create a ScannerDevice per ESP32 in Django admin and put its API key in the firmware.
