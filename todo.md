# TODO

## Scanner firmware (next up)
- [ ] Point `send_scan` at Django `POST /api/scan/` on the factory server (`tag_uid` + `X-API-Key` header, see `Web_Application/docs/scanner-api.md`)
- [ ] Add `espressif/cjson` (and later `espressif/mdns`) via `idf_component.yml`
- [ ] Store each scanner's key in NVS (copy it from the Django admin page; flash encryption for production)
- [ ] Send real RFID UIDs instead of placeholder text
- [ ] Rework `RFID::scan()` to return each UID; hex-format it; debounce repeat reads
- [ ] Check app size vs the 1 MB partition after adding WiFi/HTTP
- [ ] mDNS resolve of the factory server, cache the IP, timeout and retry
- [ ] Make `rfid.init()` non-blocking (or run it inside `rfid_task`)
- [ ] Make `RFID`, `BUTTON`, `BUZZER`, `DISPLAY` in `app_main` `static`
- [ ] Rotary encoder, keyboard buttons, IR sensor
- [ ] Replace the placeholder scroll list with real content (scan history)
- [ ] Verify touch `mirror_x` / `mirror_y` on the hardware
- [x] WiFi component written (`components/wifi`, compile-checked)
- [x] WiFi wired into `main` and confirmed connecting on the GL.iNet Mango (ESP32 got 192.168.8.224)
- [x] Make the laptop server reachable: `uvicorn --host 0.0.0.0` + Windows firewall rule for port 8000 (laptop reserved at 192.168.8.200)
- [x] `esp_http_client` POST works end to end (on startup and on a button press, placeholder text)
- [x] Move `send_scan` out of `button_task` (2 KB stack, blocks the buzzer, POSTs repeatedly while the button is held) into a queue + network task with an 8 KB stack

## Server
- [ ] Test 10 simultaneous requests (against Django `/api/scan/`)
- [ ] FastAPI only, may be moot now that scans go to Django: tidy `db.py` (return `conn, cur`, pass the uid not the model, drop leftover cursor), `ThreadedConnectionPool` (maxconn ~20)
- [ ] Auto-deploy from GitHub: separate live clone + systemd timer that pulls `main`, migrates, restarts `dashboard`
- [x] Merge decision: scans go to Django `POST /api/scan/` (`tracking/api.py`, calls `record_event`)
- [x] `process` / `event_type` are derived by the server (next step), not sent by the scanner

## Printing
- [ ] Set `PRINTER_NAME=Brother_HL_L2460DW_direct` in `Factory_Server/.env`
- [ ] Commit the `print_pdf.py` fix (status check reads only its own printer, uses the `printer` argument)
- [ ] Later: fetch the PDF from S3, then print
- [x] Direct IPP queue `Brother_HL_L2460DW_direct` on the factory server (the cups-browsed auto queue is unreliable)
- [x] `print_pdf.py` prints from the factory server

## Security
- [ ] HTTPS plan for the ESP32 (the website already has HTTPS via Tailscale Funnel)
- [ ] Later: HMAC request signing, rate limiting
- [x] Reuse Django's `ScannerDevice` (api_key, active, last_seen) instead of a new `scanners` table
- [x] Check `X-API-Key` on the ingest endpoint, return 401 on failure

## Data and workflow
- [ ] Confirm with the friend that Django's `Tag` / `TagAssignment` cover cards, orders and the "Set RFID card" assignment flow (then drop the items below)
- [ ] Associate order details to each RFID card
- [ ] Select orders and assign them to RFID cards
- [ ] Schema migrations (Alembic or versioned SQL)

## Infrastructure
- [ ] Postgres remote access over Tailscale (`pg_hba.conf` + firewall rule, Tailscale range only)
- [ ] AWS RDS migration (`pg_dump` / `pg_restore`)
- [ ] Docker, only if a second site appears
- [x] Factory server (Ubuntu mini PC): hostname, Avahi, Postgres on 6900, systemd services, Caddy, nightly backups
- [x] Setup guide and scripts for a new server (`Factory_Server/server_setup/`, guide in `SERVER_SETUP.md`)
- [x] Tailscale + Funnel (public HTTPS dashboard), SSH over Tailscale
- [x] `Factory_Server/.venv` (Python 3.12 via uv, `requirements.txt` installed)

## Optional cleanup
- [ ] Remove `C:\scnchk`, `C:\scnchk2`, `C:\scnchk3`
- [ ] Turn on hardware acceleration in the Claude desktop app on the factory server
- [ ] Remove the 10 stale offline devices from the Tailscale admin console

## Done
- [x] Postgres installed; `factory_tracker` database and `factory_app` role created
- [x] psycopg2 script reads and writes the `scans` table
- [x] FastAPI POST endpoint written and tested end to end from the C++ PC client
- [x] `Client_Connection` builds and sends `POST /record_scan` (plain-string JSON body)
- [x] TFT display and touch scrolling working at 40 MHz SPI
- [x] Button, buzzer, RFID scanning
- [x] `NOTES.md` build log
- [x] VS Code on the factory server switched from Snap to Microsoft's `.deb` (GPU acceleration works)
