# Dashboard

Django + PostgreSQL web dashboard for an RFID order-tracking system.
It shows order status derived from an append-only log of scan events.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env   # then edit .env with real values
```

## Database

Requires a running PostgreSQL server. Create an empty database, then put its name, user and password in `.env` (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`).

```powershell
psql -U postgres -c "CREATE DATABASE dashboard;"
```

Then apply migrations:

```powershell
.venv\Scripts\python manage.py migrate
```

### Switching database (Pi or local)

Keep one file per profile next to `.env`: `.env.db.pi` and `.env.db.local`, each with only the `DB_*` lines (see `.env.db.example`). They are git-ignored. Then:

```powershell
scripts\db.ps1 pi       # use the Raspberry Pi database
scripts\db.ps1 local    # use the local database
scripts\db.ps1 status   # show the active profile
```

The script copies the chosen file to `.env.db`, which overrides the `DB_*` lines of `.env`. Restart `runserver` after switching.

## Run

```powershell
.venv\Scripts\python manage.py runserver
```

## Docs

- [Setup (local and Raspberry Pi)](docs/setup.md)
- [Scanner API for the ESP32 firmware](docs/scanner-api.md)
