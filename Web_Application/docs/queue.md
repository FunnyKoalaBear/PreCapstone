# Feature queue
Order matters. [ ] todo, [!] blocked. Finished entries are deleted; git history and the feature tracker keep the record. IDs match the feature tracker. Every item needs Japanese strings and tests.

## Queue

- [ ] **F72 Previous runs of this part**
  Why: lets a planner promise a date from what really happened last time (the company visit found dates cannot be promised reliably).
  Decisions made:
  - A box on the order detail page, per item with a part number (F70), listing the last 5 earlier items with the same part number (case-insensitive), newest first. Excludes the current item and cancelled ones. No earlier runs: hide the box.
  - Each run shows: job number (link to that order), finish date (JST), and per process the actual duration (started to finished; for outsourced steps sent out to returned), with the vendor code and name (F73), and total days from first to last event.
  - Reuse the duration calculation from F68; do not write a second one.
  Tests: runs listed newest first; cancelled run skipped; durations match F68; box hidden when no runs.

- [ ] **F45 Admin dashboard**
  Why: a management view beyond Django admin.
  Decisions made:
  - Page `/dashboard/`, Admin only (F46); Planner and Operator get 403. Nav link shown to Admin only.
  - Plain numbers and lists, no charts: users per role; items per status; events recorded today (JST); scanner devices not seen in the last 24 hours (from the F14 heartbeat), listed by name; events voided by corrections in the last 7 days.
  - Each block links to the matching `/admin` page.
  Tests: counts match a small fixture; non-admin roles get 403.

## Blocked
- [!] F48 Backups: waiting for the Pi decisions (yours to look into).
- [!] F44 Print server: printer details unknown (yours to look into).
