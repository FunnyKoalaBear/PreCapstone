# Feature queue

Work through the entries below **in order, top to bottom**. For each entry:

1. Read the whole entry, then read the code it names before editing.
2. Build it, keeping code short (one-line docstrings, no long comment blocks).
3. Add the Japanese translation (UI is Japanese by default): new `msgid`s go in `locale/ja/LC_MESSAGES/django.po`, reuse existing msgids/translations where one exists, mark any wording you are unsure of with a `# CHECK:` comment like the existing entries, then run `compilemessages`.
4. Make sure the demo data lets the feature be tried (`seed_demo`), without touching unrelated demo data.
5. Run `python manage.py test orders` (and the full suite once at the end). If a test fails and the cause is not obvious, stop and report instead of working around it.
6. Commit with the entry's commit message (match the style of `git log`). **Remove the finished entry from this file in the same commit** so the queue does not grow. Do not push and do not start the server.
7. Move on to the next entry. When the queue is empty, print the "How to test" list of every entry you built.
