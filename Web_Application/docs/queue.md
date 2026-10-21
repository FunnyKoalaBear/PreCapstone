# Feature queue
Order matters. [ ] todo, [!] blocked. Finished entries are deleted; git history and the feature tracker keep the record. IDs match the feature tracker. Every item needs Japanese strings and tests.

## Queue

- [ ] **F40a Responsive layout: fluid layout and viewport tag**
  One commit. Add the viewport meta tag; make widths fluid (no fixed pixel widths on containers); no horizontal page scroll down to 360 px wide. Breakpoints: 768 px and 480 px, defined once in `theme.css`. Do not change colours or content.

- [ ] **F40b Responsive layout: tables become cards on narrow screens**
  One commit, after F40a.
  Decisions made:
  - Below 768 px these tables turn into cards: order list, tag list, codes list, results summary, shipping page.
  - These stay tables with a horizontal scroll inside their box: the progress matrix (F66) and the event history. The matrix keeps its fixed-height box and frozen header.
  - Pure CSS (data attributes for the cell labels); no JavaScript framework.

- [ ] **F40c Responsive layout: collapsible menu**
  One commit, after F40a. Below 768 px the navigation collapses behind a menu button. It still shows only the links the user's role can use (F46). Plain JS, keyboard usable, the button has an accessible label.

- [ ] **F40 Status colours**
  Why: states must be readable at a glance, in one place.
  Decisions made:
  - One CSS variable per state in `theme.css`: not started, waiting, in progress, at vendor, complete, shipped (if F69 exists), paused, cancelled, overdue. Reuse them in the progress matrix, lists, badges and the legend. Remove any hard-coded colour in templates or other CSS.
  - Every colour is always paired with its text label (colour is never the only signal). Text on each colour meets 4.5:1 contrast. Tell states apart by lightness as well as hue; prefer blue and orange over red and green.
  - No layout changes in this commit.
  Tests: a test or grep that no template or stylesheet other than `theme.css` contains a hex colour.

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
