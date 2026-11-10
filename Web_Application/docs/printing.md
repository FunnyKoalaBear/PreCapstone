# Printing work sheets

## Flow

1. Something asks for a sheet and a `PrintJob` row is saved with status `PENDING`:
   - `POST /api/assign/` (setter links a tag): saved in the same transaction as the `TagAssignment`, so a refused tag never prints and a link never misses its sheet. Every box of an item gets its own copy of the same item sheet.
   - The **Print sheet** button on the order page.
2. The `print_worker` command (a systemd service on the factory server) takes the oldest `PENDING` job:
   builds the order sheet and a separate random-drawing PDF, submits each file
   as its own CUPS print request, and marks it `SENT` with both CUPS job ids.
3. If printing fails (printer off, paused, no CUPS) the job stays `PENDING` and is retried every 15 s; after 5 tries it becomes `FAILED`.
   Admin -> Orders -> Print jobs -> "Print the selected jobs again" puts it back in the queue.

Why a queue: the HTTP reply to the setter stays fast (it has a 5 s timeout), nothing is lost while the printer is off,
and the printer code runs on the factory server even if the website later moves to AWS.

## Settings (.env)

| Key | Meaning |
|---|---|
| `PRINTER_NAME` | CUPS queue, e.g. `Brother_HL_L2460DW_direct` (see `Factory_Server/server_setup/SERVER_SETUP.md`) |
| `PRINT_DIR` | Folder that keeps every printed PDF (default `Web_Application/print`) |
| `PRINT_DRY_RUN` | `True` on a laptop without the printer: jobs are marked `SENT` with job id `dry-run` |

The factory server must have `PRINTER_NAME` set in `Web_Application/.env` and the
`print-worker.service` systemd service running. The setter API only queues the
job; the worker creates and submits the PDF.

## Commands

```bash
.venv/bin/python manage.py print_worker            # run forever (the service does this)
.venv/bin/python manage.py print_worker --once     # work through the queue once, then exit
sudo systemctl status print-worker                 # service state
journalctl -u print-worker -f                      # live log
```

## Later

Upload each printed PDF to S3 after `lp` succeeds (a failed upload must not block printing), and keep the S3 key on the `PrintJob`.
