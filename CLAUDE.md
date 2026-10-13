# PreCapstone: context for Claude

RFID process-tracking system for small factories in Japan: scanners on workbenches record which step a part is at.
The developer is learning: explain concepts briefly, show the "why".

## Layout
- `Electronics/Scanner`: ESP32-S3 firmware (ESP-IDF v6.1, C++). Components: button, buzzer, rfid (PN532 over I2C), display (ST7796S + XPT2046 touch), wifi (confirmed connecting to the GL.iNet Mango router at 192.168.8.x; no HTTP yet), configuration (all GPIO pins). Build log: `Electronics/Scanner/NOTES.md`.
- `Factory_Server`: FastAPI + psycopg2. `main.py` = HTTP routes, `db.py` = Postgres connection and queries.
- `Client_Connection`: C++ PC test client (`pc_connection.cpp`, CMake, cpp-httplib; cJSON and nlohmann/json are fetched but unused). Works end to end. Build in the terminal, not VS Code Run Code (that uses the old MinGW g++): `cmake --build build --config Release`, run `build\Release\scan_client.exe`.
- `Web_Application`: Django dashboard with its own `CLAUDE.md`. Follow those rules when working there (only edit inside that folder).
- `CAD_Files`, `commands.txt` (handy shell commands).

## Working rules
- Explain fixes and ask before editing the developer's existing code. Creating files they explicitly ask for is fine.
- Never commit or push unless asked. Never read, print or commit `.env`.
- When a Scanner feature is finished and confirmed working, add a short entry to `Electronics/Scanner/NOTES.md`. Keep docs light.
- Keep code comments to one short line.

## Environment (Windows 11, PowerShell)
- ESP-IDF activate: `. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1`, then `idf.py build`. Target esp32s3, 16MB flash, no PSRAM, COM5.
- The OneDrive project path hits the 260-char Windows limit on some builds. Verify by copying to a short path (`robocopy` to `C:\scnchk3`). Leftovers `C:\scnchk`, `C:\scnchk2`, `C:\scnchk3` exist; the sandbox blocks deleting direct children of `C:\`.
- `gh` CLI is not installed. PATH `g++` is MinGW 6.3 (too old); VS 2022 Community and msys64 are installed.
- PowerShell: use `curl.exe`, and escape inner quotes in JSON (`\"`). Tailscale is installed on the laptop.
- Run the server from inside `Factory_Server` with its venv: `uvicorn main:app --reload`.

## Postgres (local, Windows)
- Version 18, service `postgresql-x64-18`, **port 6900** (not 5432). Database `factory_tracker`, app role `factory_app` (not superuser; tables were created by `postgres`, so it was granted DML + sequence rights).
- `listen_addresses='*'` but `pg_hba.conf` only allows localhost and there is no firewall rule, so it is local-only. Logs: `data\log` (logging_collector on, `log_statement` default).
- Table `scans(id serial, uid text, scanned_at timestamp default now())`. A `cards(uid, order_name)` table was proposed, not decided.
- `Factory_Server/.env` keys: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD (use the exact same case everywhere, Linux is case-sensitive).

## Architecture decisions
- ESP32 never talks to Postgres. Flow: ESP32 -HTTP/JSON-> FastAPI -psycopg2-> Postgres.
- The RFID UID is only a key; order data lives in the DB, not on the card.
- No offline requirement. Scanners and the Pi share the factory WiFi; there is a LAN, a WAN and a local DB copy. ~10 scanners, each reporting about every 20 min, and 10 simultaneous requests must work.
- Use plain `def` endpoints with psycopg2 (never `async def`). Currently one connection per request; later `ThreadedConnectionPool` with maxconn about 20 (it raises, it does not queue, when exhausted).
- Pi runs FastAPI + Postgres; AWS RDS later. HTTPS is needed before traffic leaves the LAN.
- IDF 6.1 core has NO cJSON or mDNS. Add them as managed components in a component's `idf_component.yml`: `espressif/cjson` (^1.7.19) and `espressif/mdns`. `esp_http_client`, `esp-tls`, `esp_wifi`, `lwip`, `mbedtls` are still in core.
- ESP32 finds the Pi with mDNS (`<hostname>.local`, Avahi on the Pi, `espressif/mdns`): resolve once, cache, re-resolve on failure. DHCP reservation is the simpler alternative. Not built yet.
- Remote dev access to the Pi's Postgres: Tailscale. Never port-forward Postgres.
- Docker deferred (add if a second factory site appears; keep Avahi on the host). Pi auto-start via systemd plus a one-time provisioning script.
- Security plan: per-device API key in an `X-API-Key` header, checked against a table with an `active` flag, return 401 otherwise. Django's `ScannerDevice` already provides this (key stored in plain text, generated with `secrets.token_urlsafe(32)`), so prefer it over building a new `scanners` table. HTTPS before the internet. Keys in ESP32 NVS (flash encryption for production). HMAC signing or mTLS later.

## Known issues and open questions
- Scanner `main.cpp`: `rfid.init()` retries forever if the PN532 is missing and blocks startup. `RFID`, `BUTTON`, `BUZZER`, `DISPLAY` in `app_main` are not `static`, which is a latent dangling-pointer risk for tasks if `app_main` ever returns.
- `RFID::scan()` never returns (internal `while(true)`), so it must be reworked to hand each UID out before WiFi can use it; a card held on the reader re-reads constantly, so debounce repeats.
- Partition table is `SINGLE_APP` (1 MB app slot) and builds use `-Og`. Adding WiFi + HTTP may overflow it; check the "Smallest app partition ... free" line after the first build (options: larger single-app table, custom `partitions.csv`, `-Os`).
- Display: touch `mirror_x`/`mirror_y` untested. SPI is 40MHz and works. Scrolling redraws the whole screen (no framebuffer).
- `Factory_Server`: route is `POST /record_scan` (tested working from the C++ client). `ScanData` is unused; `db.open_connection` returns a second cursor; `add_query` takes the model object, not a uid.
- **Merge with the Django dashboard (decision pending with the friend who owns it):** Django already models `Tag(uid)`, `TagAssignment` (UID <-> box <-> order item, dated), `ScannerDevice` (name, location, **api_key**, active, last_seen), `Process`, and an append-only `Event` (device, tag_uid, process, event_type ARRIVED/STARTED/FINISHED/SENT_OUT/RETURNED/CORRECTION). Scans enter only via `tracking/services.record_event(device, tag_uid, process, event_type)`, currently called by the staff-only "simulate" page; there is no scan API yet. It uses psycopg 3 and its own DB (README says `dashboard`). So our `scans`/`cards` tables and a separate `scanners` table are redundant with Django's. Leading option: add a `POST` ingest view in Django (auth via `X-API-Key` against `ScannerDevice.api_key`, calls `record_event`) and point the ESP32 at it; FastAPI stays a learning/prototype step. Open: what tells the server the `process` and `event_type` of a scan (fixed per device? chosen on the scanner? derived next step as in the simulate page's `_next_step`). Don't edit `Web_Application` from the electronics side; agree the JSON contract with the friend.
