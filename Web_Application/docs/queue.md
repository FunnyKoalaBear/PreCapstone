# Feature queue
Order matters. [ ] todo, [!] blocked. Finished entries are deleted; git history and the feature tracker keep the record. IDs match the feature tracker. Every item needs Japanese strings and tests.

## Queue

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
