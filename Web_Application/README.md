# Dashboard

Django + PostgreSQL web dashboard for an RFID order-tracking system.
It shows order status derived from an append-only log of scan events.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env   # then edit .env with real values
```

## Run

```powershell
.venv\Scripts\python manage.py runserver
```
