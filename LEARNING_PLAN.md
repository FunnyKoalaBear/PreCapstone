# Rebuild the Setter + printing yourself (after the presentation)

Goal: write everything from the prototype again by hand, except the graphic UI (`setter_gui`), so you understand every piece.
The working version stays on branch `prototype` as the answer key. Do one step at a time: read the goal, write it, test it, then compare.

## How to work

- Practice branch from the state *before* the prototype work (main with Micheal's completion date, the original Setter test code):
  ```bash
  git switch -c practice b4e87cf
  ```
- Keep the answer key next to you: `git show prototype:<path>` prints a file, `git diff practice prototype -- <path>` shows what is still different.
- Try first, look second. If stuck for more than ~20 minutes, read only the function you need from `prototype`, close it, write it yourself.
- Commit after every step that works (`git commit -m "..."`), so you can always go back.
- Firmware: build in a short path (OneDrive path is too long), the board is on **COM8**:
  ```powershell
  robocopy Electronics\Setter C:\setchk /MIR /XD build managed_components /XF sdkconfig
  ```
  ```powershell
  cd C:\setchk; idf.py build flash monitor -p COM8
  ```
- Django tests on Postgres need the role to create a test database, once, as the `postgres` superuser: `ALTER ROLE factory CREATEDB;`
  Then `.venv\Scripts\python manage.py test tracking orders`.

## Phase 0: understand the contract (1 hour)

- [ ] Read `Web_Application/docs/scanner-api.md`, section "Tag setter API". The ESP32 and Django only agree on this JSON.
- [ ] Call it by hand with `curl.exe` (key from Tags -> Devices): `GET /api/assignable-items/?limit=3`, then `POST /api/assign/` with a spare tag. Look at 200, 409, 400 and 401 replies.
- [ ] Draw the flow on paper: list -> pick -> card -> assign -> print. Mark which side (ESP32 / Django / print worker) does each arrow.

## Phase 1: Setter firmware (`Electronics/Setter`)

Each step: **Learn** = concept to look up, **Done when** = how you know it works, **Key** = file on `prototype` to compare.

1. [ ] **Stop the screen flicker** (warm-up). One line after WiFi starts: `esp_wifi_set_ps(WIFI_PS_NONE);`
   Learn: WiFi modem sleep wakes ~10x per second and pulls current in bursts; the backlight shares the 3.3 V rail.
   Done when: the screen no longer flickers. Key: `components/wifi/wifi.cpp` (not on prototype yet, this one is yours).
2. [ ] **Kconfig paths.** Add `FACTORY_SERVER_ITEMS_PATH` and `FACTORY_SERVER_ASSIGN_PATH`.
   Learn: `Kconfig.projbuild` -> menuconfig -> `CONFIG_...` macros in C. Done when: they show in menuconfig. Key: `components/server_client/Kconfig.projbuild`.
3. [ ] **cJSON as a managed component.** `components/server_client/idf_component.yml` with `espressif/cjson`.
   Learn: IDF component manager, `dependencies.lock`, why IDF 6 has no built-in cJSON. Done when: `#include "cJSON.h"` builds.
4. [ ] **One HTTP helper that returns the reply body.** `request(post, path, body, reply, size)`: open -> write -> fetch_headers -> read_response -> close.
   Headers: `X-API-Key`, `Content-Type: application/json`, `Cookie: django_language=en`.
   Learn: why `esp_http_client_perform()` can't hand you the body; heap vs stack for a 8 KB buffer.
   Done when: a GET prints the raw JSON in the monitor. Key: `server_client.cpp` `request()`.
5. [ ] **`fetch_items()` with cJSON.** Fill `setter_item_t` (code, part, customer, due, linked, boxes, ...).
   Learn: `cJSON_Parse`, `cJSON_GetObjectItem`, `cJSON_ArrayForEach`, and that every parse needs `cJSON_Delete` (memory leak otherwise); `strlcpy` into fixed buffers.
   Done when: the monitor prints the first 20 items. Key: `fetch_items()`.
6. [ ] **`assign(uid, item_id)`.** Build the body with `snprintf`, turn the HTTP status into an enum (200 assigned, 409 refused + `detail`, 401, 400, other).
   Learn: why only "server error" is worth a retry. Done when: a used tag gives REFUSED with the reason. Key: `assign()`.
7. [ ] **`RFID::poll_uid()`.** One ~100 ms read instead of the 5 s loop, UID as uppercase hex (`%02X`).
   Learn: short reads let the task check for "cancel" between tries. Key: `components/rfid/rfid.cpp`.
8. [ ] **Keyboard over the cable.** `uart_driver_install` + `uart_vfs_dev_use_driver`, `read_line()` with echo and backspace.
   Learn: why reading the console returns nothing until the UART driver is installed. Done when: typed lines echo back. Key: `components/keyboard`.
9. [ ] **The setter task and its command queue.** Commands enum + `send()` that never blocks; one task owns all state.
   Learn: FreeRTOS queues; why a button or UI callback must not do HTTP (freezes the screen); why one owner means no data races.
   Key: `components/setter_app/setter_app.cpp` (`send`, `setter_task`).
10. [ ] **Card wait + link flow.** `wait_for_card()` (timeout + cancel), then assign, beeps, result, `xQueueReset`.
    Done when: pick -> tap -> LINKED, and cancel works mid-wait. Key: `wait_for_card()`, `link_item()`.
11. [ ] **Load the whole list.** Page through the API 20 at a time, keep the selected item by id, sort (number / start / due / part).
    Learn: `std::sort` with a tie-break (why `stable_sort` would allocate 20 KB). Key: `load_all()`, `sort_items()`.
12. [ ] **The UI interface.** `SETTER_UI` abstract class (`virtual ... = 0`) and `setter_list_t`; `TEXT_UI` prints to the serial monitor.
    Learn: an interface lets the UI team swap screens without touching the logic. Key: `setter_ui.hpp`, `text_ui.cpp`.
13. [ ] **`main.cpp` wiring.** `static` objects (tasks keep pointers after `app_main` returns), task stack sizes, panel button table (array of function pointers), steel button = back, SNTP clock (`TZ=JST-9`).
    Key: `main/main.cpp`.
14. [ ] **Plug in the provided UI** (not yours to rewrite): copy it from the prototype and add the two display helpers it needs.
    ```bash
    git checkout prototype -- Electronics/Setter/components/setter_gui
    ```
    Then write `SETTER_DISPLAY::draw_pixels()` and `glyph()` yourself (key: `setter_display.cpp`). Done when: the full UI runs on your code.

## Phase 2: printing in Django (`Web_Application`)

Follow `Web_Application/CLAUDE.md` (Japanese strings, demo data, tests). Tell Micheal before you push to `main`.

15. [ ] **Read what exists:** `orders/pdf.py` (`item_pdf`) and `Factory_Server/print_service/print_pdf.py` (your CUPS script).
16. [ ] **Real `print_pdf(path)`** in `orders/printing.py`: `lpstat -p` check, `lp -d`, return the job id; `LC_ALL=C` so the English output can be parsed; `PRINT_DRY_RUN` for laptops.
    Learn: `subprocess.run`, mocking it in tests. Key: `orders/printing.py`, `PrintPdfTests` in `orders/tests.py`.
17. [ ] **`PrintJob` model + migration + admin.** Status (waiting / sent / failed), source (tag linked / button), attempts, printer job, PDF file, error.
    Learn: `TextChoices`, `on_delete` choices, `makemigrations`. Key: `orders/models.py`, `orders/admin.py`.
18. [ ] **Queue the print with the link.** In `api.assign`: `transaction.atomic()` around `assign_tag()` + `queue_print()`.
    Learn: the "outbox" pattern: no print for a refused tag, no link without a print, and the HTTP reply stays fast. Key: `tracking/api.py`.
19. [ ] **The worker.** `run_next_job()` with `select_for_update(skip_locked=True)`, retries (5 tries, 15 s apart), and the `print_worker` management command.
    Learn: why a long-running loop calls `close_old_connections()`. Key: `orders/printing.py`, `orders/management/commands/print_worker.py`.
20. [ ] **Print sheet button queues too**, plus the admin "Print the selected jobs again" action.
    Translations: add `.po` entries by hand and run `compilemessages` (MSYS2's `makemessages` breaks the `.\path` references in this file).
21. [ ] **Extra list fields** for the details screen: `quantity`, `order_number`, `ordered`, `steps` (one grouped `Count` query per page, not one per item). Key: `tracking/api.py`.
22. [ ] **Service on the mini PC.** `Factory_Server/server_setup/setup-print-worker.sh`; check with `systemctl status print-worker` and `journalctl -u print-worker -f`.

## Phase 3: finish

- [ ] Full run on hardware: pick, tap, sheet prints, the scanner tracks the box.
- [ ] `git diff practice prototype` is empty or only differs where you chose something better (note why in the commit message).
- [ ] Short `NOTES.md` entry in your own words.

## Next features to build yourself

- [ ] Upload each printed PDF to S3 after `lp` succeeds (reuse `Factory_Server/AWS_S3/s3_pdf.py`; a failed upload must not block printing).
- [ ] Reload the Setter list every minute or two (it can be stale until a button press).
- [ ] Each device's API key in NVS instead of menuconfig.
- [ ] Find the server with mDNS (`factory-server.local`) instead of a fixed IP.
