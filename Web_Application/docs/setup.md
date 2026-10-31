# Setup

For Mithun: run the dashboard locally, then on a Raspberry Pi 5. Checked against `config/settings.py`, `.env.example` and `README.md`.

## Local setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`, create an empty PostgreSQL database, then:

```powershell
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py runserver
```

## Roles

Migrations create the groups Operator, Planner and Admin. In /admin, add each user to one group (superusers count as Admin). Tick "Staff status" only for people who need /admin.

## Raspberry Pi setup

Needs 64-bit Raspberry Pi OS with **Python 3.12 or newer** (Django 6.1 requirement). Check with `python3 --version`. Python 3.11 will not work. How to get 3.12+ on your OS image: unknown.

1. Update and install packages.

   ```bash
   sudo apt update
   sudo apt install postgresql python3-venv git
   ```

2. Create the database and user. Use your own password.

   ```bash
   sudo -u postgres psql -c "CREATE USER factory WITH PASSWORD 'YOUR_DB_PASSWORD';"
   sudo -u postgres psql -c "CREATE DATABASE factory OWNER factory;"
   ```

3. Get the code into a folder, then create the venv and install.

   ```bash
   cd Web_Application
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

   `psycopg[binary]` has ARM wheels, so no extra system libraries are expected. If pip builds from source, install `libpq-dev`.

4. Create `.env` next to `manage.py` (copy `.env.example`). Keys:

   ```
   SECRET_KEY=change-me
   DEBUG=True
   ALLOWED_HOSTS=127.0.0.1,localhost,PI_IP_OR_HOSTNAME
   DB_NAME=factory
   DB_USER=factory
   DB_PASSWORD=change-me
   DB_HOST=localhost
   DB_PORT=5432
   STAGNATION_DEFAULT_HOURS=24
   SCAN_REPEAT_SECONDS=5
   ```

   - `SECRET_KEY`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` are required. The app will not start without them.
   - `ALLOWED_HOSTS` is comma separated. It must contain the Pi's IP or hostname, otherwise Django rejects the ESP32 and browsers (`400 Bad Request`).
   - `DEBUG` defaults to False when missing. Static files (CSS) and uploaded drawings are served by Django only when `DEBUG=True`; no static-file setup for production exists yet.
   - `SCAN_REPEAT_SECONDS` is not in `.env.example`. Default 5, 0 turns the repeat guard off.
   - Never commit `.env`.

5. Optional DB profile. If `.env.db` exists next to `.env`, its `DB_*` lines override `.env`. On Windows `scripts\db.ps1 pi|local|status` copies `.env.db.pi` or `.env.db.local` to `.env.db`. On the Pi you do not need it.

6. Create the tables and the admin user.

   ```bash
   .venv/bin/python manage.py migrate
   .venv/bin/python manage.py createsuperuser
   ```

7. Create a scanner device. Open `http://PI_ADDRESS:8000/admin/`, log in, Tracking -> Scanner devices -> Add. Save, then open it again and copy the **API key**. One key per scanner. See `scanner-api.md`.

8. Run for trials (not for production):

   ```bash
   .venv/bin/python manage.py runserver 0.0.0.0:8000
   ```

   Open port 8000 if a firewall is on.

## Copy the database between laptop and Pi

Use the custom format (`-Fc`). Dump files are git-ignored (`*.dump`). Never commit them.

Laptop to Pi:

```powershell
pg_dump -U factory -h localhost -Fc -f factory.dump factory
scp factory.dump USER@PI_ADDRESS:~/
```

```bash
pg_restore -U factory -h localhost --no-owner --clean --if-exists -d factory ~/factory.dump
```

Pi to laptop:

```bash
pg_dump -U factory -h localhost -Fc -f factory.dump factory
```

```powershell
scp USER@PI_ADDRESS:~/factory.dump .
pg_restore -U factory -h localhost --no-owner --clean --if-exists -d factory factory.dump
```

Restore overwrites the target database. Stop the app first. Run `migrate` afterwards if the code versions differ.

Reaching the Pi database directly from the laptop (the `pi` profile) needs `listen_addresses` in `postgresql.conf` and a line in `pg_hba.conf`. Not set up here; unknown.

## Check the Pi clock

Event times come from the Pi clock when the scan arrives.

```bash
timedatectl
```

Look for `System clock synchronized: yes` and `NTP service: active`. If not: `sudo timedatectl set-ntp true`. The Pi needs network access to an NTP server (unknown if the factory network allows it). Django stores UTC and shows Asia/Tokyo; the Pi's own timezone does not matter.

## Choosing a production server and running at boot

**Mithun's decision. Not set up here.**

Open points:

- Which server runs Django (the project has `config/wsgi.py` and `config/asgi.py`; none is installed).
- Serving static files and uploaded drawings with `DEBUG=False` (no `STATIC_ROOT` is set).
- Starting at boot and restarting on failure.
- HTTPS (unknown if needed on the factory LAN).
- A fixed IP or hostname for the Pi, to put into `ALLOWED_HOSTS` and the firmware.

## Checklist

- [ ] `timedatectl` shows the clock synchronized.
- [ ] `migrate` ran without errors.
- [ ] `http://PI_ADDRESS:8000/` opens from the laptop and the admin login works.
- [ ] A scanner device exists and you have its API key.
- [ ] A tag and an item are linked (Tags page in the web app).
- [ ] This curl from the laptop returns `"ok": true`:

  ```powershell
  '{"tag_uid": "YOUR_TAG_UID"}' | Set-Content -Encoding ascii body.json
  curl.exe -i -X POST http://PI_ADDRESS:8000/api/scan/ -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" --data-binary "@body.json"
  ```

- [ ] The new event is in the Pi database: admin -> Tracking -> Events, or `psql -U factory -h localhost -d factory -c "select id, tag_uid, event_type, occurred_at from tracking_event order by id desc limit 3;"`
- [ ] The device's **last seen** in admin is updated.
