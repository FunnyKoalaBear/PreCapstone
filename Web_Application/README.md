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

## Run

```powershell
.venv\Scripts\python manage.py runserver
```
