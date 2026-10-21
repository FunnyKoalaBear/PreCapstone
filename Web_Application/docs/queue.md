# Feature queue
Order matters. [ ] todo, [x] done, [!] blocked. IDs match the feature tracker. Every item needs Japanese strings and tests.

## Queue

- [x] **F46 Roles and login**
  Groups Operator, Planner, Admin (created by a data migration; Admin group or superuser = Admin). `core/permissions.py` with `role_required(*roles)`; superusers always pass.
  Replace every `staff_member_required`. Anonymous -> `/login/` (own login/logout pages, JA/EN) with `next`. Logged in without the role -> translated 403 page. `is_staff` stays only for /admin.
  Operator: home, order list/detail, later the progress matrix. Planner: + new/edit order, Tags, event correction. Admin: + Simulate scan, /admin.
  Nav shows only links the user can use. Update existing tests; add one test per role and page group.

- [x] **F77 Drawing on the order detail page**
  Per item: "Open drawing" link (new tab); inline preview for png/jpg; "No drawing" otherwise.
  Serve the file through a view that checks the role and streams it (FileResponse). Do not rely on the DEBUG-only media URL: it does not work on the Pi.

- [x] **F73 Process and supplier/vendor codes**
  New `Vendor` model: `code` (5 characters, digits only, unique, stored as text), `name`, `name_ja`, `kind` INTERNAL / SUPPLIER / VENDOR, `lead_days` (nullable).
  Validation: INTERNAL codes 00000-00099; SUPPLIER and VENDOR codes 00100-00429.
  `RouteStep.vendor` nullable FK (who does or supplies the step). `Process.default_vendor` nullable FK, prefilled when the process is picked. Keep `Process.vendor_name` and `vendor_lead_days` as fallback; a data migration turns existing vendor names into Vendor rows.
  Order form: after choosing the process, a dropdown showing "code name". Show code and name together everywhere (detail page, timeline). Searchable `/codes/` list page (Planner and up). Register in admin.

- [x] **F79 Planned due date per route step**
  `RouteStep.due_date` (nullable) and `slip_number` (text, blank). Fields in the order form per step; shown on the order detail page.
  Vendor due-back uses the step due date when set, else started + lead days (vendor, then process). Tests for both paths.

- [x] **F80 Material on order items**
  `OrderItem.material` (text, 100, blank). In the form, detail page and order search.

- [x] **F78 Demo data with our own codes**
  Update the seed script and the static demo (`demo/js/data.js` and the pages that show steps). Use `docs/demo-data/codes.csv`, `processes.csv` and `workers_machines.csv` (invented, in the two blocks) so the seed, the static demo and the CSV import agree. Never use the company's real codes or names.

- [x] **F42 CSV order import**
  Upload page (Planner and up). Spec, columns and the expected result of every error row: `docs/demo-data/README.md`. Files: `orders_demo.csv`, `orders_demo_sjis.csv`, `orders_demo_errors.csv`.
  Read UTF-8 (with or without BOM), then fall back to Shift-JIS (cp932). Preview every row with ok/error, import all-or-nothing in one transaction, option "create new customers". Rows with the same customer and customer order number become one order. Reuse the order-creation service so job numbers and items work as usual.
  Tests: clean file imports 7 orders and 10 items; each error row gives its expected message; the Shift-JIS file gives the same result.
  The real company format will arrive later: keep the column mapping in one place so it is easy to change.

- [x] **F66 Progress matrix (replaces the home process board)**
  Rows = unfinished items, columns = step 1..N; each cell shows that item's own process name (vendor name for outsourced steps), coloured by state with a legend (CSS variables only). Finished steps collapse into one compact cell.
  Fixed-height box with its own scroll and a frozen header and first column. Default: unfinished orders by due date, first 25, "Show more". Filters: status, customer, due range, overdue only. Option "start at current step". Click a row to open the order.
  Home shows a short version (most urgent 10) linking to the full page. Remove the old process board only when this works.

- [x] **F43 Order to PDF (work instruction sheet)**
  Use ReportLab and pypdf (pure Python: works on Windows and the Pi) with an embedded Japanese font (Noto Sans JP, OFL licence, bundled in static/fonts).
  Page 1 per item: job number, customer, customer PO, material, due date, quantity, notes. Route table: sequence, process, code + name (F73), step due date (F79), quantity, blank boxes for date / worker / notes, actual qty, defect qty. Optional QR of the item code.
  Page 2: the drawing (pdf pages appended; png/jpg scaled to fit) so duplex printing puts it on the back. No drawing -> no page 2.
  Button "Print sheet" on the order detail page (Planner and up).

- [ ] **F64 Extra states: paused, resumed, partial, cancelled** (website side only)
  Add event types and status rules; planner buttons on the order detail page; no scanner changes. Derived status stays computed from events. Cancelled orders leave the overdue lists.

- [x] **F65 Worker, machine, quantity and defects (website side)**
  `Worker` and `Machine` models (name, name_ja, active); demo names in `docs/demo-data/workers_machines.csv`.
  New event type RESULT (append-only, does not change step state): process, worker, machine (both optional), quantity done, defect count. "Record result" form on the order detail page for Operator and up (extend the F46 permissions). Corrections work as for other events (F47).
  Show results in the item history; per-process totals and defect rate on a simple summary page. The scanner can send RESULT later; no scanner changes now.

- [x] **F68 Planned vs actual time per process**
  Planned = step due date (F79) minus the previous step finish; actual from events. Show per order and a simple per-process average page.

## Blocked
- [!] F48 Backups: waiting for the Pi decisions (yours to look into).
- [!] F44 Print server: printer details unknown (yours to look into).
