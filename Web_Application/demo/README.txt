Factory Tracker - static demo (工程進捗管理)
============================================
A copy of the real pages with sample data. It needs no server, no database and no
internet: plain HTML, CSS and JavaScript. Do not edit these files by hand; they are
generated.

Refresh it (after any change to the site)
-----------------------------------------
    python manage.py export_demo

The command builds a scratch database that holds only the seed_demo data, opens every
page of the site as a planner, and writes this folder again. It stops with an error if a
link is broken or a secret-looking word (password, token, localhost ...) appears.
(Run `python manage.py seed_demo --reset` first if you also want your own dev database
to hold the same demo data; the export itself never touches it.)

Layout
------
  index.html      Sends the visitor to ja/index.html
  ja/  en/        The same pages in Japanese and English (the 日本語 / English switch links
                  to the same page in the other folder). ja/manual.html (使い方) exists
                  only in Japanese.
  static/         The site's own CSS and JavaScript, plus demo.css and demo.js

What works in the demo
----------------------
- Every menu link and tab; order, customer, box and priority pages.
- Search, filters and sorting, done in the browser (static/js/demo.js). The Analysis page
  and the monthly on-time table are recalculated in the browser from the data embedded in
  the page.
- Scroll panels, the frozen first column of the progress matrix, the menu button.
- Simulate scan: pick a tag and its next step is shown (one pre-rendered page per open tag); "Confirm" shows the demo notice.
- Forms (new order, import, add/edit/deactivate code, tags, customers, corrections) and
  links to files (PDF sheet, CSV export, drawings, edit pages) show "This is a demo,
  nothing is saved" in the page's language.

Upload to Neocities
-------------------
Upload the whole folder (index.html, ja, en, static) and open index.html. To try it
locally: `python -m http.server` inside this folder, then http://localhost:8000/.

Notes
-----
- The data is a snapshot of the day it was exported; run the command again to refresh the dates.
- All names, parts, codes and customers are invented (seed_demo, docs/demo-data).
