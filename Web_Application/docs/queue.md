# Feature queue
Order matters. [ ] todo, [!] blocked. Finished entries are deleted; git history and the feature tracker keep the record. IDs match the feature tracker. Every item needs Japanese strings and tests.

## Queue

### Batch rules (apply to U1-U6)
- The site runs on a factory LAN that may have no internet: no CDN, no web fonts, no icon fonts, no external requests. Icons are inline SVG.
- The shop-floor screen is a budget USB monitor with mouse and keyboard (not touch). Design for 1366x768 as the smallest screen; no page may scroll sideways at that size. Clickable controls are at least 2rem high.
- Colors only through variables in static/css/theme.css (CLAUDE.md rule). No hex colors anywhere else, including inline styles and SVG.
- Printing and PDFs always stay light.
- Do not change behaviour, URLs, permissions or models unless the entry says so. This is a look-and-feel batch.
- Every new string gets Japanese per docs/glossary.md. Update docs/manual_ja.md where a page or control changes.
- Demo data: nothing new is needed; use the existing demo data to check every page.

## Blocked
- [!] F48 Backups: waiting for the Pi decisions (yours to look into).
- [!] F44 Print server: printer details unknown (yours to look into).
