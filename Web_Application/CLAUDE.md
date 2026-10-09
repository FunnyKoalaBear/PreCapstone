# Dashboard (Web_Application)
Django + PostgreSQL web dashboard for an RFID order-tracking system (replaces Technoa/TECHS-BK).
Windows 11, Python 3.13, VS Code. The developer is learning: explain each step briefly.

## Rules
- Only create or edit files inside Web_Application/. Never touch other folders.
- Never run git commit or git push. The developer commits.
- Never read, print or commit .env. Secrets only in .env; .env.example has fake values.
- Code short: at most a one-line comment per block, no long comment blocks. Explanations go in docs/.
- Work on main. One feature group per commit.

## Architecture (not yet confirmed by the team)
- Append-only Event log. Order status is derived from events, never stored or overwritten.
- Tag-box-order links are dated records (TagAssignment), because boxes get reused.
- Store time in UTC, display JST. English UI first, Japanese via Django translation.