# Scan API

`POST /api/scan/` - a scanner sends a tag; the server saves the event.

Header: `X-API-Key: <device key>` (copy it from the admin, Scanner devices). Body is JSON:

```json
{"tag_uid": "AB12"}
```

Optional: `"process": <id>` and `"event_type": "ARRIVED|STARTED|FINISHED|SENT_OUT|RETURNED"`. Send both or neither. Without them the server picks the box's next step.

## Replies

| Code | Body | Meaning |
|------|------|---------|
| 200 | `{"ok": true, "state": "recorded", "event_id", "tag_uid", "box", "item", "status", "event_type", "process_id", "process_name"}` | Event saved |
| 200 | `{"ok": true, "state": "finished", "tag_uid", "box", "item", "status"}` | Route done, nothing saved (auto mode) |
| 200 | `{"ok": true, "state": "duplicate", ...}` | Same tag scanned again within `SCAN_REPEAT_SECONDS` (default 5, 0 = off), nothing saved (auto mode) |
| 400 | `{"ok": false, "error": "invalid", "detail": "..."}` | Bad JSON, missing tag_uid, only one of process/event_type, unknown process or invalid event |
| 401 | `{"ok": false, "error": "bad_key"}` | Missing, wrong or inactive key |
| 404 | `{"ok": false, "error": "unassigned_tag"}` | Tag has no open assignment, nothing saved |
| 405 | - | Not a POST |

## Example (PowerShell)

Write the body to a file first; PowerShell mangles quotes inside `-d`.

```powershell
'{"tag_uid": "AB12"}' | Set-Content -Encoding ascii body.json
curl.exe -X POST http://127.0.0.1:8000/api/scan/ -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" --data-binary "@body.json"
```
