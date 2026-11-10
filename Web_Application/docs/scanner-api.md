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
{"ok": true, "state": "recorded", "event_id": 12, "tag_uid": "04A1B2C3",
 "item": "2610-001-01", "status": "In progress", "event_type": "STARTED",
 "process_id": 3, "process_name": "Cutting"}
```

`event_type`, `process_id` and `process_name` tell what was just saved. `status` is the item status after the event. The reply does not say what comes next.

Box out at a vendor: there is no special reply. Scanning it again records `RETURNED` (state `recorded`, `event_type` `RETURNED`). The `status` before that scan was "At vendor".

### 200 `finished` - route already complete, nothing saved

```json
{"ok": true, "state": "finished", "tag_uid": "04A1B2C3",
 "item": "2610-001-01", "status": "Complete"}
```

### 200 `duplicate` - same tag scanned again too soon, nothing saved

Same keys as `finished`, with `"state": "duplicate"`. Happens in tag-only mode when this tag already has an event in the last `SCAN_REPEAT_SECONDS` seconds (default 5, 0 = off). The explicit form has no such guard.

### 404 - tag not assigned, nothing saved

```json
{"ok": false, "error": "unassigned_tag"}
```

The tag has no open assignment to an item. The device `last_seen` is still updated.

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
| 200 `recorded` | Saved | OK, with `process_name` + `event_type` | No |
| 200 `finished` | Route already done | "Already finished" | No |
| 200 `duplicate` | Scanned twice | Ignore or "Already scanned" | No |
| 404 `unassigned_tag` | Tag not linked to an item | "Tag not assigned" | No. Fix in the web app (Tags page) |
| 401 `bad_key` | Key wrong or device off | "Device key error" | No. Check key and active flag |
| 400 `invalid` | Firmware bug or bad data | "Request error" | No |
| 405 | Wrong method | "Request error" | No |
| 5xx, timeout, no network | Server or network down | "Server error" | Yes, after a pause. A retry of a tag-only scan inside 5 s is ignored as `duplicate`; later it may save a second step |

# Scan preview API (ask before recording)

Checked against `tracking/api.py` (`scan_preview`). Same `X-API-Key` header and `401 bad_key` as the scan API. **Saves nothing.**

The scanner shows the operator which order and step a scan would record, waits for YES, and only then posts `/api/scan/` (tag-only). NO sends nothing.

## `GET /api/scan-preview/?tag_uid=04A1B2C3`

The UID is trimmed and upper-cased like the scan API. Only GET; anything else gets `405`.

### 200 `next` - a tag-only scan would record this step

```json
{"ok": true, "state": "next", "tag_uid": "04A1B2C3", "item": "2611-001-01",
 "part": "Gear", "part_number": "", "drawing_number": "DW-1", "revision": "B", "material": "S45C",
 "quantity": 10, "customer": "Mithun", "order_number": "HD-B0031",
 "ordered": "2026-11-02", "due": "2026-12-01", "status": "In progress",
 "step": 2, "steps": 5, "event_type": "STARTED", "process_id": 3, "process_name": "Cutting"}
```

- `step` of `steps`: position of that step in the box's route (1-based). Machining and outsourced steps come up twice at the same `step` (`STARTED` then `FINISHED`, `SENT_OUT` then `RETURNED`).
- `event_type`, `process_id`, `process_name`: what the scan would save. Strings may be empty; `part` and `customer` may be Japanese.
- `ordered` is the date the order was registered (JST), `due` the order's due date.

### 200 `finished` - route complete, a scan would save nothing

Same item fields, `"state": "finished"`, `step` = `steps`, and no `event_type`, `process_id` or `process_name`.

### 404, 401, 400

`404 {"ok": false, "error": "unassigned_tag"}` (tag not linked), `401 bad_key`, `400 invalid` (`tag_uid` missing or empty). `last_seen` is updated on 200 and 404.

### Notes

- Another scanner may record between the preview and the scan. The scan reply is what counts: show its `state`.
- A YES within `SCAN_REPEAT_SECONDS` of the tag's last event gets `duplicate` (nothing saved).

# Tag setter API (link a tag to an item)

Checked against `tracking/api.py` (`assignable_items_list`, `assign`). Same `X-API-Key` header and same `401 bad_key` as the scan API. A setter is an ordinary scanner device (Tags -> Devices); any active key works on all three endpoints.

Flow: the setter lists the items that still need a tag, the operator picks one, taps a card, and the setter posts the UID with the item id. The server checks everything again (the list may be out of date).

## 1. `GET /api/assignable-items/?offset=0&limit=20`

Items that still have a free box slot, oldest job number first (the same list as the Tags page form). Paged so the ESP32 never parses a big reply: `limit` 1-50 (default 20), `offset` >= 0 (default 0). Anything else gives `400 invalid`.

```json
{"ok": true, "total": 80, "offset": 0,
 "items": [{"id": 914, "item": "2611-001-01", "part": "Mit", "customer": "Mithun",
            "due": "2026-12-01", "linked": 0, "boxes": 1, "quantity": 10,
            "order_number": "HD-B0031", "ordered": "2026-11-02", "steps": 5}]}
```

- `quantity`, `order_number` (customer order number, may be empty), `ordered` (date the order was registered, JST) and `steps` (route steps) are for the setter's details screen.

- `id` is what you send back in step 2. `item` (job number + "-NN") is what you show.
- `linked` of `boxes` are already tagged. Show "box `linked+1` of `boxes`" only when `boxes` > 1.
- Next page: `offset` + number of items received, while that is below `total`.

## 2. `POST /api/assign/`

```json
{"tag_uid": "04A1B2C3", "item": 914}
```

`item` must be a JSON integer. The UID is trimmed and upper-cased like the scan API.

Optional `"replace": true` (exactly `true`): if the tag is linked to another item, that link is ended first and the tag is linked to this item instead, in one transaction (the reply then has `"released": "<old item code>"`). The setter sends it only after the operator confirmed. If the new link is refused anyway (item full, tag inactive), the old link stays.

| Reply | Meaning | Show | Retry? |
|---|---|---|---|
| 200 `{"ok": true, "state": "assigned", "tag_uid": "04A1B2C3", "item": "2611-001-01", "linked": 1, "boxes": 1, "print": "queued"}` | Linked from now on; the item's sheet is queued for printing (see `docs/printing.md`) | OK; if `linked` < `boxes`, offer the next box | No |
| 409 `refused` | Tag already in use, tag inactive, or all boxes of the item already linked. `reason` says which: `tag_in_use` (+ `current_item`, the item code it is on), `tag_inactive` or `item_full`. `detail` is display text, do not parse it | `detail`, then reload the list. On `tag_in_use`: ask whether to move the tag, and if yes send again with `"replace": true` | Only with `replace` after confirming |
| 400 `invalid` | Bad JSON, missing or empty `tag_uid`, `item` not an integer or unknown | "Request error" | No |
| 401 `bad_key` | Key wrong or device off | "Device key error" | No |
| 5xx, timeout | Server or network down | "Server error" | Yes. A retry after a success gives `409` (tag in use): check `detail` or the Tags page |

`last_seen` is updated on 200 and 409 (and on step 1), not on 400 or 401. Fixing a wrong link (release, replace a lost tag) stays on the Tags page.

Linux example:

```bash
curl -s "http://PI_ADDRESS:8080/api/assignable-items/?limit=5" -H "X-API-Key: YOUR_KEY"
curl -i -X POST http://PI_ADDRESS:8080/api/assign/ -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" -d '{"tag_uid": "04A1B2C3", "item": 914}'
```

# Tag lookup (what the scanner shows before a scan)

`GET /api/tag/<uid>/` with the same `X-API-Key`. Read only: it saves no event (only `last_seen`). Same `401 bad_key` and `404 unassigned_tag` as the scan API.

```json
{"ok": true, "state": "open", "tag_uid": "04A1B2C3", "item": "2611-001-01", "part": "Gear",
 "part_number": "PN-7", "drawing": "DWG-1 / B", "material": "S45C", "quantity": 4,
 "customer": "C", "order_number": "PO-9", "ordered": "2026-11-02", "due": "2026-12-01",
 "step": 2, "steps": 5, "process": "Milling", "event_type": "STARTED", "status": "In progress", "box": ""}
```

- `process` + `event_type` are what `POST /api/scan/` with only the tag would record now. `step` is that step's position (1-based) of `steps`.
- `state` `finished`: the route is done, `process` and `event_type` are empty and `step` = `steps`.
- `status` is display text in the request language, `box` is "Box k of n" for items with several boxes, else empty.
