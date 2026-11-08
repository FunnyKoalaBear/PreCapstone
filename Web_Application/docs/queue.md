# Feature queue

Work through the entries below **in order, top to bottom**. For each entry:

1. Read the whole entry, then read the code it names before editing.
2. Build it, keeping code short (one-line docstrings, no long comment blocks).
3. Add the Japanese translation (UI is Japanese by default): new `msgid`s go in `locale/ja/LC_MESSAGES/django.po`, reuse existing msgids/translations where one exists, mark any wording you are unsure of with a `# CHECK:` comment like the existing entries, then run `compilemessages`.
4. Make sure the demo data lets the feature be tried (`seed_demo`), without touching unrelated demo data.
5. Run `python manage.py test orders` (and the full suite once at the end). If a test fails and the cause is not obvious, stop and report instead of working around it.
6. Commit with the entry's commit message (match the style of `git log`). **Remove the finished entry from this file in the same commit** so the queue does not grow. Do not push and do not start the server.
7. Move on to the next entry. When the queue is empty, print the "How to test" list of every entry you built.

---

## Setter print 2 (tracker task 3): per-item "Print sheet" button

**Why.** The order page has one "Print sheet" link that opens the whole order's PDF in the browser. Workers need to print the sheet of one item, on request, straight to the printer on the mini PC. Printing happens only when someone asks for it.

**What.**
- In `orders/views.py` add two views, both `@role_required(OPERATOR)` like the existing `order_sheet`:
  - `item_print(request, pk)`: POST only. Calls `print_item(item)`, adds a success message with the job id, or an error message with the printer's text, then redirects back to `order_detail` for the item's order, jumping to that item's card (`#item-<pk>`; give each card on `order_detail.html` that id).
  - `item_sheet(request, pk)`: GET, returns `item_pdf(item)` inline as `application/pdf`, filename `<item_code>.pdf`. This is the "open in browser" preview.
- `orders/urls.py`: `items/<int:pk>/print/` (`item_print`) and `items/<int:pk>/sheet/` (`item_sheet`).
- `templates/orders/order_detail.html`: in each item's card, next to the item heading, show a small POST form with a "Print sheet" button (with `{% csrf_token %}`) and an "Open sheet (PDF)" link to `item_sheet` (new tab like the existing link). Show both only when `can.operator`.
- The existing link in the page heading stays but is relabelled "Preview all sheets", so the word "Print sheet" now always means "send to the printer".

**Decisions made.**
- The print action is a **POST** with CSRF, never a GET link, so a browser prefetch or a reload can never print by accident.
- Errors never become a 500: catch `RuntimeError` and `FileNotFoundError` from `print_item`, show the message as an error with the item code (the printer text is English and is shown as is, only the surrounding sentence is translated), and log it with `logging.getLogger(__name__).exception`.
- Success wording must say "sent to the printer", not "printed", because the job is only queued.
- Printing is allowed for any item status including cancelled or finished ones (reprints are normal). No `OrderChange` row: this is not an edit of the order.
- Same role as the old button (Operator and above), because workers print their own sheets.
- Use the existing button classes (`button-secondary`) and keep the form inline so the card heading does not grow. Reuse the existing "Print sheet" msgid and translation.
- Not in this entry: copies, printer selection, printing from the setter's `/api/assign/`, the real `print_pdf`.
- Must keep working: `order_sheet` (whole order), the order edit page, the box/step display of the cards, and the existing tests that look for "Print sheet" on the order page.

**Tests** (in `orders/tests.py`; mock `orders.views.print_item`).
- Operator POSTs `items/<pk>/print/`: redirects to the order page, `print_item` called once with that item, a success message containing the job id is shown.
- GET on `items/<pk>/print/` returns 405 and prints nothing.
- Anonymous is redirected to login; a logged-in user without the Operator role gets 403.
- `print_item` raising `RuntimeError('Printer is paused')`: redirect (no 500) and the message is shown on the order page; same for `FileNotFoundError`.
- Unknown item pk returns 404.
- `item_sheet` returns 200, `application/pdf`, starts with `%PDF`; anonymous is redirected.
- The order page of a two-item order contains exactly two print forms (one per item) and still contains the "Preview all sheets" link; a viewer without the Operator role sees neither.

**Demo data.** Uses the demo order with and without a drawing from the previous entry; add nothing else.

**How to test.** Open an order with several items. Click "Open sheet (PDF)" on one item: only that item's sheet opens, drawing on the back. Click "Print sheet" with `PRINT_DRY_RUN=1` in `.env`: the page returns to the same item and says the sheet was sent to the printer (job `dry-run`), and `print/<item_code>.pdf` exists. Turn `PRINT_DRY_RUN` off and click again: a readable error appears instead of a crash. Switch the language to English and check the new texts.

**Commit message.** `Add per-item Print sheet button that sends the item's sheet to the printer`
