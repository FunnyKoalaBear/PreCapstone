# TODO

## Server (Factory_Server)
- [ ] Tidy `db.py`: return `conn, cur`, pass the uid not the model, drop leftover cursor
- [ ] Move to a connection pool (`ThreadedConnectionPool`, maxconn ~20)
- [ ] Decide how this server connects to the Django dashboard (Event log)
- [ ] Test 10 simultaneous requests

## Security
- [ ] `scanners` table: name, key hash, active flag, last_seen
- [ ] `require_scanner` dependency checking `X-API-Key`, return 401 on failure
- [ ] Record which scanner sent each scan
- [ ] HTTPS plan (AWS cert, or self-signed cert embedded in firmware)
- [ ] Store each scanner's key in NVS on the ESP32 (flash encryption for production)
- [ ] Later: HMAC request signing, rate limiting

## Scanner firmware
- [ ] WiFi connection component
- [ ] `esp_http_client` POST with cJSON (migrate from `Client_Connection`)
- [ ] mDNS resolve of the Pi, cache the IP, timeout and retry
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
- [ ] Pi: hostname, Avahi check, Postgres install, systemd unit for the API
- [ ] Provisioning script for a new Pi / new factory site
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
