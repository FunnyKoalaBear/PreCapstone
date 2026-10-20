# Scanner API (contract for the ESP32 firmware)

Checked against `tracking/api.py`. Anything not in the code is marked "unknown".

## Endpoint

- `POST http://<server>:8000/api/scan/`
- Only POST. Any other method gets `405`.
- No login, no CSRF token. Plain HTTP (HTTPS: unknown, not set up).
- Header `X-API-Key: <key>` is required. Header `Content-Type: application/json`.

## The API key

- One key per scanner device. A key is made when the device is created.
- Django admin -> Tracking -> Scanner devices -> Add. Fill name (and location), save.
- Open the device again: the **API key** is shown read-only. Copy it from there.
- Untick **active** to block a device. Its key then gets `401`.

## Request body

JSON object.

| Field | Type | Required | Notes |
|---|---|---|---|
| `tag_uid` | string | yes | Tag UID. Server trims spaces and upper-cases it. Empty string is rejected. |
| `process` | integer | only with `event_type` | Process id in the database. |
| `event_type` | string | only with `process` | `ARRIVED`, `STARTED`, `FINISHED`, `SENT_OUT` or `RETURNED`. |

Send `process` and `event_type` together, or neither. Only one of them gives `400`.

**Recommended for the firmware: send only the tag.** The server finds the box's next step itself.

```json
{"tag_uid": "04A1B2C3"}
```

Explicit form (the server checks the process kind fits the event type: ARRIVED needs a Material process, STARTED/FINISHED Machining, SENT_OUT/RETURNED Outsourced):

```json
{"tag_uid": "04A1B2C3", "process": 3, "event_type": "STARTED"}
```

Other fields are ignored.

## Responses

Fields to use for logic: HTTP status, `ok`, `state`, `error`. Do not parse `status` or `detail`: they are display text and depend on language (see below).

### 200 `recorded` - an event was saved

```json
{"ok": true, "state": "recorded", "event_id": 12, "tag_uid": "04A1B2C3", "box": "B1",
 "item": "2610-001-01", "status": "In progress", "event_type": "STARTED",
 "process_id": 3, "process_name": "Cutting"}
```

`event_type`, `process_id` and `process_name` tell what was just saved. `status` is the item status after the event. The reply does not say what comes next.

Box out at a vendor: there is no special reply. Scanning it again records `RETURNED` (state `recorded`, `event_type` `RETURNED`). The `status` before that scan was "At vendor".

### 200 `finished` - route already complete, nothing saved

```json
{"ok": true, "state": "finished", "tag_uid": "04A1B2C3", "box": "B1",
 "item": "2610-001-01", "status": "Complete"}
```

### 200 `duplicate` - same tag scanned again too soon, nothing saved

Same keys as `finished`, with `"state": "duplicate"`. Happens in tag-only mode when this tag already has an event in the last `SCAN_REPEAT_SECONDS` seconds (default 5, 0 = off). The explicit form has no such guard.

### 404 - tag not assigned, nothing saved

```json
{"ok": false, "error": "unassigned_tag"}
```

The tag has no open assignment to a box and item. The device `last_seen` is still updated.

### 401 - bad key

```json
{"ok": false, "error": "bad_key"}
```

Key missing, wrong, or device inactive. Nothing is read or saved.

### 400 - bad request, nothing saved

```json
{"ok": false, "error": "invalid", "detail": "KeyError: 'tag_uid'"}
```

Causes: body not JSON, body not an object, `tag_uid` missing or empty, only one of `process`/`event_type`, unknown process id, unknown `event_type`, process kind does not fit the event type. `detail` is free text.

### 405

Not a POST. Empty body, header `Allow: POST`.

### Other (5xx, no connection)

Format unknown. Treat as a failure.

## What the server saves and updates

- One `Event` row: device, tag UID, process, event type, time. Events are never edited or deleted.
- `last_seen` of the device is set to the server time on `recorded`, `finished`, `duplicate` and `404`. It is not set on `400` or `401`.

## Time

- The firmware cannot send a time. Any time field in the body is ignored.
- The server uses its own clock when the request arrives. It stores UTC and shows JST.
- So the Pi clock must be right (see `setup.md`).

## Language of `status` and `detail`

Without a language cookie the server answers in Japanese (for example `status` = `作業中`). To get English, send the header `Cookie: django_language=en`. Logic must not depend on either.

## Examples

Replace `YOUR_KEY` and `PI_ADDRESS`.

PowerShell (write the body to a file, PowerShell mangles quotes in `-d`):

```powershell
'{"tag_uid": "04A1B2C3"}' | Set-Content -Encoding ascii body.json
curl.exe -i -X POST http://PI_ADDRESS:8000/api/scan/ -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" --data-binary "@body.json"
```

Linux:

```bash
curl -i -X POST http://PI_ADDRESS:8000/api/scan/ \
  -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"tag_uid": "04A1B2C3"}'
```

## What the ESP32 should do

| Reply | Meaning | Show | Retry? |
|---|---|---|---|
| 200 `recorded` | Saved | OK, with `process_name` + `event_type` and `box` | No |
| 200 `finished` | Route already done | "Already finished", `box` | No |
| 200 `duplicate` | Scanned twice | Ignore or "Already scanned" | No |
| 404 `unassigned_tag` | Tag not linked to a box | "Tag not assigned" | No. Fix in the web app (Tags page) |
| 401 `bad_key` | Key wrong or device off | "Device key error" | No. Check key and active flag |
| 400 `invalid` | Firmware bug or bad data | "Request error" | No |
| 405 | Wrong method | "Request error" | No |
| 5xx, timeout, no network | Server or network down | "Server error" | Yes, after a pause. A retry of a tag-only scan inside 5 s is ignored as `duplicate`; later it may save a second step |
