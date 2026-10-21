# Dashboard (Web_Application)
Django + PostgreSQL web dashboard for an RFID order-tracking system (replaces Technoa/TECHS-BK).
Windows 11, Python 3.13, VS Code. The developer is learning: explain each step briefly.

## Rules
- Only create or edit files inside Web_Application/. Never touch other folders.
- Commit only when the developer or the /next-feature skill asks. Never push, amend, rebase, reset --hard or force anything.
- If a spec leaves a real decision open, stop and ask. Do not guess.
- Never read, print or commit .env. Secrets only in .env; .env.example has fake values.
- Code short: at most a one-line comment per block, no long comment blocks. Explanations go in docs/.
- Work on main. One feature group per commit.

## Architecture (not yet confirmed by the team)
- Append-only Event log. Order status is derived from events, never stored or overwritten.
- Tag-box-order links are dated records (TagAssignment), because boxes get reused.
- Store time in UTC, display JST. English UI first, Japanese via Django translation.

## Frontend rules
- Colors only through CSS variables in static/css/theme.css; no hard-coded colors elsewhere.
- Every user-visible string uses {% trans %} (templates) or gettext (Python).
- Templates in Web_Application/templates/, static files in Web_Application/static/.
- Any list or table that can grow long uses the scroll panel: `{% load panels %}` then `{% scroll_panel %}...{% endscroll_panel %}` (options `max_height`, `first_col`, `label`). No bare tables, no new overflow wrappers.

## Demo data
- Every new feature ships with demo data: extend `core/management/commands/seed_demo.py` (bulk history in `_demo_bulk.py`) so its pages are not empty, then open each page to check. Events go through `record_event`, never status fields; keep it deterministic, relative to today, and re-runnable (`seed_demo --reset`).

## Data model decisions (confirmed)
- Status is never stored; it is derived from Events.
- Job number (seiban) format YYMM-NNN, auto-generated in JST, unique, never edited. Item code = job number + "-NN".
- One box holds one OrderItem, but an item may use several boxes (OrderItem.box_count = expected number, each box has its own tag/TagAssignment). Route (RouteStep) belongs to the OrderItem.
- Status is derived per box (each box runs the route on its own) and rolled up to the item in tracking/status.py. A box is the pair (item, box): the events of every tag it had, each within its own assignment window, replay together.
- Replacing a lost tag ends the old assignment and starts a new one on the same item and box (services.replace_tag); history is kept and the old tag can be marked inactive.
- Event types: ARRIVED, STARTED, FINISHED, SENT_OUT, RETURNED, CORRECTION. Events are append-only.
- All times shown in JST (TIME_ZONE Asia/Tokyo, USE_TZ True).
- Verbose names use gettext_lazy.

- TagAssignment history is never deleted or rewritten; end an assignment (end_at) and create a new one.

## Status rules
- Status is computed in tracking/status.py from events; never stored.
- Events voided by a CORRECTION are ignored. Box events = events whose tag UID was assigned to that box at that time; item events = all its boxes' events.

- All events are created through tracking/services.record_event (simulate page now, scan API later). The scan API (POST /api/scan/) rejects unassigned tags; the simulate page still saves them.


## Static demo
- The demo (demo/) is generated, never hand-edited: run `python manage.py export_demo` after UI changes (it uses a scratch database with seed data only and fails on broken links). Row filters in the demo read `data-f-*` attributes on table rows; new filterable tables need them. The Japanese-only manual is docs/manual_ja.md; update it when a page or term changes.

## Translation rule
- Every new user-visible string needs its Japanese translation in locale/ja using docs/glossary.md; then run makemessages and compilemessages (commit .po and .mo). No untranslated or fuzzy entries; add new terms to the glossary. Mark unsure choices with a `# CHECK: reason` translator comment.
