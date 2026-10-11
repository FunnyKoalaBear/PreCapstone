Factory Tracker - static demo (工程進捗管理)
============================================
A self-contained copy of the dashboard with sample data. It needs no server, no
database and no internet: plain HTML, CSS and JavaScript only (no CDN, fonts or
frameworks). Japanese is the default; the 日本語 / English switch is in the header.

Files (upload all of them, keeping the folder structure)
--------------------------------------------------------
  index.html        Home: counters (clickable) and in-house / at-vendor tables
  boxes.html        Boxes list behind the counters (?status=, ?stagnant=1, ?vendor_late=1)
  orders.html       Orders list: search, status filter, sort, overdue only
  order.html        Order detail: timeline and event history per box (?job=...)
  new-order.html    New order, including the Boxes field
  tags.html         Link / release / replace tags, search linked tags
  simulate.html     Simulate a scan (next step only, plus "other action")
  board.html        Process board: running and waiting boxes per process
  priorities.html   Priorities: risk tiers, slack and reasons
  css/theme.css     Colour variables (same as the app)
  css/base.css      Styles (same as the app, plus the demo banner)
  js/i18n.js        Japanese and English texts
  js/data.js        Sample data (same scenarios as the seed_demo command)
  js/status.js      Status, priority and overview rules ported from the Django app
  js/app.js         Pages and UI

Upload to Neocities
-------------------
1. Log in to Neocities and open the dashboard ("Edit Site").
2. Upload the nine .html files to the top level of the site.
3. Create the folders css and js (or drag the folders in) and upload the files above.
4. Open https://<yoursite>.neocities.org/index.html. Staff can also open
   index.html straight from a local copy of this folder; no server is needed.

How it behaves
--------------
- All dates are relative to today, so the sample data always looks current.
- Scans, new orders and tag changes are saved in each visitor's own browser
  (localStorage), never on a server. If the browser blocks storage, the demo still
  works but forgets changes when the page is reloaded.
- "Reset demo data" in the top banner clears the saved changes. The language choice
  is kept (it is saved separately). ?lang=en or ?lang=ja in the address also sets it.

How it differs from the real app
--------------------------------
- No login, no database, no admin site, no scanner API: everything runs in the browser.
- Status rules are a JavaScript port of tracking/status.py, priority.py and overview.py.
  If the Python rules change, update js/status.js too.
- Sample data only (companies, parts and processes are made up). No drawing files.
- A new order cannot change an item's box count afterwards, and there is no
  device or tag management beyond the tags page.
- The simulate page reloads in place instead of using GET reloads, and the
  language is switched without a page reload.
- Process names show the Japanese name (name_ja) in Japanese and the English name
  otherwise, like the app.
