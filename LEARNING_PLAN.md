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

## Function map: what you will write and how it connects

Read this before Phase 1, and come back to it whenever you are lost. Step numbers refer to the phases below.

### The big picture (firmware)

Three input tasks only *queue* commands. One task, `setter_task`, owns the item list and does all the slow work
(HTTP, waiting for a card, drawing). That is why a button press never freezes anything and there are no data races.

```
 INPUT TASKS (main.cpp)                       COMMAND QUEUE              THE ONE WORKER (setter_app.cpp)
 panel_task    -> PANEL_ACTIONS[n]()  --+
 button_task   -> setter_back()       --+-> setter_*() -> send(cmd) -> [ queue, 8 cmd_t ] -> setter_task
 keyboard_task -> read_line() + parse --+                                                     |
                                                                                              |
     +----------------------------------------------------------------------------------------+
     |
     +- load_all() ------> SERVER_CLIENT::fetch_items() -> request(GET)  -> Django assignable_items_list
     +- sort_items()
     +- show_list() -----> ui->show_list(setter_list_t)          (SETTER_GUI draws, TEXT_UI prints)
     +- link_item()
          +- ui->show_waiting_for_card()
          +- wait_for_card() -> RFID::poll_uid()        (also reads the queue: BACK cancels)
          +- SERVER_CLIENT::assign() -> request(POST)   -> Django assign -> assign_tag + queue_print
          +- ui->show_result()
```

### Data types (write these first, everything else passes them around)

| Type | File | Holds | Made by -> used by |
|---|---|---|---|
| `setter_item_t` | `server_client.hpp` | one item: id, code, part, customer, order_number, due, ordered, linked, boxes, quantity, steps | `fetch_items()` -> `items[]` in setter_app -> UI |
| `assign_state_t` + `assign_result_t` | `server_client.hpp` | ASSIGNED / REFUSED / BAD_KEY / INVALID / SERVER_ERROR, message, linked, boxes | `assign()` -> `link_item()` -> `ui->show_result()` |
| `cmd_type_t` + `cmd_t` | `setter_app.cpp` | one queued command (+ row number or item code) | `send()` -> queue -> `setter_task` |
| `card_t` | `setter_app.cpp` | READ / TIMEOUT / CANCELLED | `wait_for_card()` -> `link_item()` |
| `setter_list_t` (+ view / sort / modal enums) | `setter_ui.hpp` | what the list screen shows: items, count, selected, view, sort, modal, banner | `show_list()` -> `ui->show_list()` |
| `SETTER_UI` | `setter_ui.hpp` | abstract class: `show_message`, `show_list`, `show_waiting_for_card`, `show_result`, `show_error`, `tick` | implemented by `TEXT_UI` and `SETTER_GUI` |

### Firmware functions

**`components/server_client`** (steps 2-6)

| Function | Does | Called by | Calls |
|---|---|---|---|
| `int request(bool post, path, body, reply, reply_size)` | one HTTP request with the 3 headers; fills `reply` with the body; returns the status or -1 | `fetch_items`, `assign`, `send_scan` | `WIFI::connected`, `esp_http_client_open / write / fetch_headers / read_response / close / cleanup` |
| `bool fetch_items(offset, limit, items, max_items, &count, &total)` | GET one page, parse with cJSON into `setter_item_t[]` | `load_all` | `request`, `cJSON_Parse`, `cJSON_ArrayForEach`, helpers `json_int`, `json_str`, `cJSON_Delete` |
| `assign_result_t assign(uid, item_id)` | POST `{"tag_uid", "item"}`, turn the status into `assign_state_t`, copy `detail` on 409 | `link_item` | `request`, `cJSON_Parse`, `json_int`, `json_str` |
| `bool send_scan(uid)` | the scanner's POST, kept for later | nothing on the Setter | `request` |

**`components/rfid`** (step 7)

| Function | Does | Called by | Calls |
|---|---|---|---|
| `bool RFID::init()` | start the PN532 over I2C (already exists) | `app_main` | PN532 driver |
| `bool RFID::poll_uid(uid_out, &len)` | one ~100 ms look for a card | `wait_for_card` | `pn532_read_passive_target_id` |

**`components/keyboard`** (step 8)

| Function | Does | Called by | Calls |
|---|---|---|---|
| `bool KEYBOARD::init()` | install the UART driver, route `printf` through it | `app_main` | `uart_driver_install`, `uart_vfs_dev_use_driver` |
| `int KEYBOARD::read_line(line, size)` | wait for Enter, echo keys, handle backspace | `keyboard_task` | `uart_read_bytes`, `uart_write_bytes` |

**`components/setter_app/setter_app.cpp`** (steps 9-11): public functions first, then the private ones in the `namespace {}`

| Function | Does | Called by | Calls |
|---|---|---|---|
| `bool setter_app_start(ui, rfid, buzzer)` | save the pointers, create the queue and `setter_task` (10 KB stack) | `app_main` | `xQueueCreate`, `xTaskCreate` |
| `setter_up / down / prev_page / next_page / link_selected / back / toggle_details / toggle_drawing / next_view / next_sort / pick(row) / pick_code(code) / reload` | one line each: queue a command | input tasks | `send` |
| `send(type, row, code)` | build a `cmd_t`, `xQueueSend` with 0 wait (never blocks the caller) | the 13 functions above | `xQueueSend` |
| `setter_task(arg)` | forever: reload if needed, wait 1 s for a command (else `ui->tick()`), `switch` on the command | FreeRTOS | `load_all`, `show_list`, `link_item`, `find_code`, `sort_items`, `ui->...` |
| `bool load_all(server)` | page through the API 20 at a time into `items[100]`, keep the selection by id | `setter_task` | `fetch_items`, `sort_items` |
| `sort_items()` | `std::sort` by number / start / due / part (tie-break on code), keep the selection | `load_all`, `setter_task` (SORT) | `std::sort`, `strcmp` |
| `show_list()` | pack the state into a `setter_list_t`, hand it to the UI, clear the banner | `setter_task` | `ui->show_list` |
| `link_item(server, item)` | the whole link flow: ask for card, wait, assign, show result, beeps, banner, `xQueueReset` | `setter_task` (LINK, PICK, PICK_CODE) | `ui->show_waiting_for_card`, `wait_for_card`, `assign`, `ui->show_result`, `beep` |
| `card_t wait_for_card(uid_hex, size)` | loop until 15 s: BACK in the queue -> CANCELLED; card -> hex UID, READ | `link_item` | `xQueueReceive`, `RFID::poll_uid`, `snprintf("%02X")` |
| `int find_code(code)` | index of an item code in `items[]`, or -1 | `setter_task` (PICK_CODE) | `strcasecmp` |
| `beep(times, on_ms)` | buzzer pattern | `link_item` | `BUZZER::on / off` |

**`components/setter_app/text_ui.cpp`** (step 12): `TEXT_UI` implements every `SETTER_UI` method with `printf`. `SETTER_GUI` owns one and calls it first, so the serial monitor always shows the same thing as the screen.

**`main/main.cpp`** (step 13)

| Function | Does | Calls |
|---|---|---|
| `app_main()` | create every object `static`, init display -> pick `SETTER_GUI` or `TEXT_UI`, init keyboard / buzzer / button / panel, WiFi, `start_clock`, RFID, `setter_app_start`, start the input tasks | everything's `init()`, `setter_app_start`, `xTaskCreate` |
| `panel_task(panel)` | every 50 ms: `BUTTON_PANEL::pressed()`, run `PANEL_ACTIONS[n - 1]()` | `setter_up`, `setter_down`, ... (via the table) |
| `button_task(button)` | steel button pressed -> `setter_back()` | `BUTTON::clicked`, `setter_back` |
| `keyboard_task(keyboard)` | `read_line`, then: one letter -> its command; contains `-` -> `setter_pick_code`; number -> `setter_pick(n - 1)` | `read_line`, `strtol`, `setter_*` |
| `start_clock()` | `TZ=JST-9` + SNTP from `pool.ntp.org` | `setenv`, `tzset`, `esp_netif_sntp_init` |

**Provided UI, copy only** (step 14): `SETTER_GUI` (`setter_gui.cpp`) implements `SETTER_UI` and draws through `CANVAS::render()`. It needs only two display functions from you: `SETTER_DISPLAY::draw_pixels(x, y, w, h, pixels)` and `SETTER_DISPLAY::glyph(c)`.

### Call chains to trace on paper

1. **Boot:** `app_main` -> `display.init` -> `gui.init` -> `keyboard.init` ... `wifi.init` -> `start_clock` -> `rfid.init` -> `setter_app_start` -> `setter_task` -> `load_all` -> `fetch_items` x4 -> `request` -> `show_list` -> `SETTER_GUI::show_list` -> `TEXT_UI::show_list` + `redraw`.
2. **Button 2 (down):** `panel_task` -> `PANEL_ACTIONS[1]` = `setter_down` -> `send(DOWN)` -> queue -> `setter_task` `case DOWN` -> `selected++` -> `show_list`.
3. **Button 3 (link) + card:** `setter_link_selected` -> `send(LINK)` -> `setter_task` `case LINK` -> `link_item` -> `show_waiting_for_card` -> `wait_for_card` -> `poll_uid` (repeat) -> `assign` -> `request(POST)` -> **Django** -> `show_result` -> back in `setter_task`: `need_load = true` -> `load_all`.
4. **Button 4 during the card wait:** `setter_back` -> `send(BACK)` -> it is `wait_for_card` (not `setter_task`'s `switch`) that reads it from the queue -> returns `CANCELLED`.
5. **Typed `2511-004-01` + Enter:** `keyboard_task` -> `setter_pick_code` -> `send(PICK_CODE)` -> `find_code` (if not found: `load_all`, try again) -> `link_item`.

### Django functions (Phase 2)

| Function | File | Does | Called by | Calls |
|---|---|---|---|---|
| `print_pdf(path, printer=None)` | `orders/printing.py` | check the queue with `lpstat -p`, send with `lp -d`, return the CUPS job id; `'dry-run'` if `PRINT_DRY_RUN` | `print_item` | `_run` |
| `_run(cmd)` | `orders/printing.py` | `subprocess.run` with `LC_ALL=C`; "lp not found" -> `RuntimeError` | `print_pdf` | `subprocess.run` |
| `print_item(item, name=None)` | `orders/printing.py` | write the sheet to `PRINT_DIR/name` (temp file + `os.replace`), print it | `run_next_job` | `item_pdf` (existing, `pdf.py`), `print_pdf` |
| `queue_print(item, source, assignment=None)` | `orders/printing.py` | create a `PrintJob` (status PENDING) | `assign` (API), `item_print` (button) | `PrintJob.objects.create` |
| `run_next_job()` | `orders/printing.py` | lock the oldest PENDING job (`select_for_update(skip_locked=True)`), print it, mark SENT, or count the attempt (FAILED after 5) | `print_worker` | `print_item` |
| `Command.handle(once, interval, retry_wait)` | `orders/management/commands/print_worker.py` | loop: `close_old_connections`, `run_next_job`, sleep | systemd (`print-worker.service`) | `run_next_job` |
| `assign(request)` | `tracking/api.py` | key check, parse JSON, `transaction.atomic()`: `assign_tag` + `queue_print`; 409 on `ValidationError` | the Setter's POST | `_device`, `assign_tag` (existing, `services.py`), `queue_print`, `_touch`, `_error` |
| `assignable_items_list(request)` | `tracking/api.py` | one page of items + quantity, order_number, ordered, steps | the Setter's GET | `assignable_items` (existing), one grouped `RouteStep` count |
| `item_print(request, pk)` | `orders/views.py` | Print sheet button -> `queue_print(item, BUTTON)` + message | the order page form | `queue_print` |
| `PrintJobAdmin.retry` | `orders/admin.py` | selected jobs back to PENDING, attempts 0 | Django admin action | `queryset.update` |
| `PrintJob` | `orders/models.py` | the queue table (item, assignment, source, status, attempts, printer_job, pdf_name, error, created_at, sent_at) | all of the above | |

**Django call chain, end to end:** Setter POST -> `assign` -> `assign_tag` + `queue_print` (one transaction) -> 200 reply (fast). Separately, every 2 s: `print_worker` -> `run_next_job` -> `print_item` -> `item_pdf` -> `print_pdf` -> `lpstat` + `lp` -> printer -> job SENT.

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
