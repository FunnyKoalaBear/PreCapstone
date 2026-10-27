# AWS setup and related commands (27 Oct 2026)

Windows 11, PowerShell, PostgreSQL 18 on port 6900. Passwords and keys are placeholders: use your own and never commit them.

## 1. Let the ESP32 reach the server on the laptop

Allow port 8000 (PowerShell as Administrator, local network only):

```powershell
New-NetFirewallRule -DisplayName "FastAPI 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 `
  -Action Allow -Profile Private,Public -RemoteAddress LocalSubnet
```

Listen on the LAN, not only on localhost (inside `Factory_Server`, venv active):

```powershell
uvicorn main:app --reload --host 0.0.0.0
```

## 2. Build the firmware

ESP-IDF has no `rebuild` command (inside `Electronics\Scanner`):

```powershell
idf.py build
idf.py fullclean build
```

## 3. Restore Michael's database dump

Shortcuts for this terminal window (they disappear when it closes):

```powershell
$pg = "C:\Program Files\PostgreSQL\18\bin"
$dump = "C:\Users\sbmit\Downloads\factory.dump"
```

Check that the dump opens (changes nothing):

```powershell
& "$pg\pg_restore.exe" --list $dump | Select-Object -First 15
```

Create the app role and an empty database it owns (asks for the `postgres` password):

```powershell
& "$pg\psql.exe" -U postgres -p 6900 -c "CREATE USER factory WITH PASSWORD 'CHOOSE_A_PASSWORD';"
& "$pg\psql.exe" -U postgres -p 6900 -c "CREATE DATABASE factory OWNER factory;"
```

Restore into it (asks for the `factory` password):

```powershell
& "$pg\pg_restore.exe" -U factory -h localhost -p 6900 --no-owner --clean --if-exists -d factory $dump
```

Check the tables arrived:

```powershell
& "$pg\psql.exe" -U factory -h localhost -p 6900 -d factory -c "\dt"
```

## 4. Run Michael's Django dashboard

Inside `Web_Application`:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py runserver 0.0.0.0:8000
```

Optional own admin account: `.venv\Scripts\python manage.py createsuperuser`

Open `http://localhost:8000/admin/`. `0.0.0.0` is only the listening address, not a URL. The git-ignored `.env` next to `manage.py` holds `DB_PORT=6900`, `DB_NAME=factory`, `DB_USER=factory`.

## 5. Export the schema

```powershell
& "$pg\pg_dump.exe" -U factory -h localhost -p 6900 --schema-only --no-owner `
  -f "$env:USERPROFILE\Downloads\factory_schema.sql" factory
```

pgAdmin Query Tool (database `factory`):

```sql
SELECT id, username, is_staff, is_superuser, date_joined FROM auth_user;
```

## 6. AWS CLI and S3

Install, then open a NEW terminal so PATH refreshes:

```powershell
winget install Amazon.AWSCLI
aws --version
```

Save the IAM access key on this machine (four prompts; region `ap-northeast-1`, output `json`):

```powershell
aws configure
```

Check it works:

```powershell
aws sts get-caller-identity
aws s3 ls
```

Make your own bucket (name must be globally unique; new buckets block public access by default):

```powershell
aws s3 mb s3://YOUR-UNIQUE-BUCKET-NAME --region ap-northeast-1
```

Python tools for the S3 script (`AWS_S3/s3_pdf.py`):

```powershell
python -m pip install boto3 python-dotenv
```

## Never commit

`.env` files, `*.dump` files, the AWS access key, or the downloaded `.csv` containing its secret. The key lives in `C:\Users\sbmit\.aws\credentials`, outside the project.
