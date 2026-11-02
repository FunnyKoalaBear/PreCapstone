# TODO

## Server (Factory_Server)
- [ ] Tidy `db.py`: return `conn, cur`, pass the uid not the model, drop leftover cursor
- [ ] Move to a connection pool (`ThreadedConnectionPool`, maxconn ~20)
- [ ] MERGE DECISION with the friend: scan ingest endpoint in Django (`record_event`) vs FastAPI writing to Django's tables; agree the JSON contract (uid, process, event_type or derived)
- [ ] Decide what sets `process` and `event_type` for a real scan (fixed per scanner, chosen on the scanner, or derived next step)
- [ ] Test 10 simultaneous requests

## Security
- [ ] Reuse Django's `ScannerDevice` (api_key, active, last_seen) instead of a new `scanners` table (pending merge decision)
- [ ] Check `X-API-Key` on the ingest endpoint, return 401 on failure
- [ ] Store the ESP32's key in NVS (copy it from the Django admin page)
- [ ] HTTPS plan (AWS cert, or self-signed cert embedded in firmware)
- [ ] Store each scanner's key in NVS on the ESP32 (flash encryption for production)
- [ ] Later: HMAC request signing, rate limiting

## Scanner firmware
- [x] WiFi component written (`components/wifi`, compile-checked)
- [x] WiFi wired into `main` and confirmed connecting on the GL.iNet Mango (ESP32 got 192.168.8.224)
- [x] Make the laptop server reachable: `uvicorn --host 0.0.0.0` + Windows firewall rule for port 8000 (laptop reserved at 192.168.8.200)
- [ ] Add `espressif/cjson` (and later `espressif/mdns`) via `idf_component.yml`
- [x] `esp_http_client` POST works end to end (on startup and on a button press, placeholder text)
- [x] Move `send_scan` out of `button_task` (2 KB stack, blocks the buzzer, POSTs repeatedly while the button is held) into a queue + network task with an 8 KB stack
- [ ] Send real RFID UIDs instead of placeholder text
- [ ] Rework `RFID::scan()` to return each UID; hex-format it; debounce repeat reads
- [ ] Check app size vs the 1 MB partition after adding WiFi/HTTP
- [ ] mDNS resolve of the factory server, cache the IP, timeout and retry
- [ ] Make `rfid.init()` non-blocking (or run it inside `rfid_task`)
- [ ] Make `RFID`, `BUTTON`, `BUZZER`, `DISPLAY` in `app_main` `static`
- [ ] Rotary encoder, keyboard buttons, IR sensor
- [ ] Replace the placeholder scroll list with real content (scan history)
- [ ] Verify touch `mirror_x` / `mirror_y` on the hardware

## Data and workflow
- [ ] Decide `cards` / `orders` tables; "Set RFID card" assignment flow
- [ ] Associate order details to each RFID card
- [ ] Select orders and assign them to RFID cards
- [ ] Schema migrations (Alembic or versioned SQL)

## Infrastructure
- [ ] Factory server (Ubuntu mini PC): hostname, Avahi check, Postgres install, systemd unit for the API
- [ ] Provisioning script for a new server / new factory site
- [ ] Postgres remote access over Tailscale (`pg_hba.conf` + firewall rule, Tailscale range only)
- [ ] AWS RDS migration (`pg_dump` / `pg_restore`)
- [ ] Docker, only if a second site appears

## Optional cleanup
- [ ] Remove `C:\scnchk`, `C:\scnchk2`, `C:\scnchk3`

## Done
- [x] Postgres installed; `factory_tracker` database and `factory_app` role created
- [x] psycopg2 script reads and writes the `scans` table
- [x] FastAPI POST endpoint written and tested end to end from the C++ PC client
- [x] `Client_Connection` builds and sends `POST /record_scan` (plain-string JSON body)
- [x] TFT display and touch scrolling working at 40 MHz SPI
- [x] Button, buzzer, RFID scanning
- [x] `NOTES.md` build log
