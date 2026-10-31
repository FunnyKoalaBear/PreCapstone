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

### [ ] U2 Sidebar shell with always-available Home
Why: the header has 11 links that wrap onto two rows, shows no current page, and has no constant way home. A sidebar with icons scales as pages are added.
What: for logged-in users replace the top header with a left sidebar.
- Layout: `<aside id="sidebar">` plus `.app-main` (messages, main, footer). A slim top bar is used only for logged-out pages (brand, language switch, theme button) and at 768px and below (hamburger and brand).
- Brand block at the top of the sidebar: logo mark plus "Factory Tracker", a link to the home page with aria-label "Home" and a tooltip. It is present in every sidebar state and on the mobile bar. This is the always-available way home. The Home item stays in the list too.
- Icons: inline SVG sprite in templates/includes (reuse the style of the existing tab_icon.html): 24 viewBox, stroke 1.75, currentColor, aria-hidden. Include tag `{% include "includes/icon.html" with name="..." %}`. Icons: home, orders, priorities, analysis, shipping, dashboard, customers, tags, users, settings, simulate, pin, panel-toggle, chevron, key, logout, sun, moon, auto.
- Groups and items, with exactly today's can.* gating: Work (Home, Orders, Priorities); Review (Analysis, Shipping, Dashboard); Manage (Customers, Tags, Users, Settings); Developer (Simulate scan, admin only). Hide a group label when it has no visible items.
- Active item: context processor `nav` in core/context_processors.py returns the groups as {label, url, icon, active}. Active is decided by request.resolver_match.url_name with a map for child pages (order detail/edit/sheet -> Orders, customer pages -> Customers, tag history -> Tags, result pages -> Analysis, etc.; check every URL name). The active item has aria-current="page" and --color-nav-active-bg with primary text.
- Modes saved in localStorage 'sidebar' (pinned or collapsed) and applied to <html data-sidebar> by the inline head script, so there is no layout jump:
  - pinned: 220px, icons plus labels, pushes the content.
  - collapsed: 56px icon rail, labels visually hidden but still read by screen readers, each item has title and aria-label. Hovering or focusing the rail expands it temporarily as an overlay without moving the content (150ms, collapses after the pointer leaves for 200ms), like the Claude interface.
  - A button at the top of the sidebar toggles pinned and collapsed (aria-pressed, label "Pin sidebar" or "Collapse sidebar").
  - Default: pinned at 1280px and wider, collapsed below. Respect prefers-reduced-motion.
  - At 768px and below the sidebar is an off-canvas drawer opened by the hamburger, with a backdrop. It closes on link click, Escape and backdrop click, and focus returns to the hamburger. Replace the old nav.js and remove .site-nav and .nav-toggle CSS after grepping for other uses.
- Language switch: at the bottom of the sidebar, always visible, using the existing form. In the collapsed rail show one button labelled with the other language ("EN" or "日本語"). Logged-out pages show both in the slim bar.
- The current account form stays at the bottom of the sidebar, minimal, until U3.
- No-JS fallback: the sidebar renders pinned and every link works; the toggle button is hidden when JS is off (use the existing .js class).
Decisions made:
- "Fixed" means pinned. "Disappear" means collapsing to the rail, with a hover peek. A fully hidden mode is not built.
- Only existing pages appear in the nav. Tabs inside pages (Orders hub, Settings, Tags) are not added to the sidebar.
Tests:
- nav context per role: Operator sees Home, Orders, Priorities, Analysis only; Planner also sees Shipping, Dashboard, Customers, Tags, Users, Settings; Admin also sees Simulate scan; no empty group labels.
- Active flag for child pages (order detail marks Orders, tag history marks Tags, etc.); exactly one aria-current per page.
- The brand link points to home on every page, including login. The logged-out page has no sidebar but has the language form.
- The head script sets data-sidebar before the stylesheet link.
- Every main page returns 200 for each role.

### [ ] U3 Account chip
Why: name, Change password and Log out take too much space.
What:
- Bottom of the sidebar: a chip with an initials avatar, username, role label and a chevron. In the collapsed rail it shows the avatar only, with the username as tooltip. Initials come from a small helper (first letters of first and last name if set, else the first two letters of the username, uppercase).
- Click or Enter opens a popover above it (aria-haspopup="menu", aria-expanded) containing: avatar, username, full name if set and the role label as a pill; "Change password" (link to the existing password change page); Theme as a radiogroup Auto / Light / Dark (reflects FT.getTheme and calls FT.setTheme); a divider; "Log out" (POST form with CSRF). Escape closes and returns focus; clicking outside closes; arrow keys move between items; it closes if the rail collapses while open.
- Remove the temporary header theme button from U1 and delete the old account.html markup and CSS. Logged-out pages show a "Log in" link in the slim bar.
- Role label is translated and comes from role_level; superusers show Admin.
Decisions made: the language switch stays outside the chip, always visible.
Tests: role label per role; logout is a POST form with csrf; change-password link; theme control has three options and marks the current one; anonymous sees Log in; the initials helper.

### [ ] U4 Feedback for actions: toasts, undo, confirm
Why: planners can now change a lot. They need clear feedback, a safety net for reversible toggles, and a confirm step for risky actions.
What:
1. Toasts replace the inline messages list. Bottom-right of the content area (full width at 480px and below), in an aria-live="polite" region. Success and info dismiss after 6s, warning after 10s, error stays until closed. Hover or focus pauses; a close button on each; at most 3 visible. Level shown by icon and color (success = --status-complete, warning = --status-waiting, error = --color-danger, info = primary), never color alone. No-JS fallback: the same markup renders as a plain list at the top of the page.
2. Undo on Active toggles only: workers, machines and defect causes (Settings), users (Users page), and processes and devices if those pages exist (grep). The toast shows "Undo". Add a helper in core/feedback.py so each view adds one line. Requirements: undo reuses the existing toggle endpoint and its permission checks; only the same user can undo; it expires after 60s and is ignored if the state has changed again; the undo is itself written to LogEntry; never offered for create, rename, delete, password reset or role change.
3. Confirm dialog: forms carrying `data-confirm="..."` open a themed native `<dialog>` (message names the item, Cancel and a confirm button, danger style when `data-confirm-danger`; focus starts on Cancel; Escape cancels). Without JS the form submits normally. Apply to every destructive or hard-to-reverse form: delete of unused records, reset password, deactivate user, regenerate key (if it exists), release tag, replace tag, cancel and ship actions. Grep templates for these forms and list what was changed in the commit message. Reversible toggles use Undo, not confirm.
Decisions made: wording names the item, for example `Delete worker "Sato"? This can't be undone.` Form validation errors are unchanged.
Tests: messages render with the right level classes and aria; undo reverses a worker deactivation and writes a LogEntry; undo by another user, after expiry, or after a state change is rejected; Operator still gets 403 on the toggle endpoints; data-confirm is present on each listed form (render each page with demo data); exactly one toast container in base.html.

### [ ] U5 Typography polish
Why: consistent type scale, aligned numbers, better Japanese text, and readable sizes on a budget monitor.
What:
- Tokens in theme.css: --text-sm 0.8125rem (smallest allowed), --text-base 1rem, --text-table 0.9375rem, --text-lg 1.05rem, --text-xl 1.2rem, --text-2xl 1.5rem; --leading-tight 1.3, --leading-base 1.5, --leading-ja 1.7; weights 400, 500, 600 only (brand may use 700).
- Apply: body base, h1 2xl, h2 xl, h3 lg (weight 600, tight leading), table cells --text-table with 1.4 leading, labels and helper text --text-sm in --color-muted. Replace every hard-coded font-size in base.css and inline styles with tokens; nothing below 0.8125rem.
- Tabular numbers: `.num` class (font-variant-numeric: tabular-nums, right aligned in tables) on quantities, counts, durations, KPI figures; tabular-nums without alignment change on job numbers, dates and times. Audit orders, order detail, analysis, shipping, priorities, boxes, results, matrix counts and settings counts.
- Japanese: `:lang(ja)` gets --leading-ja for paragraphs and notes, `line-break: strict`, and `font-feature-settings: "palt"` on headings and nav labels only.
- Do not add web fonts or change the font stack. Check whether static/fonts/NotoSansJP.ttf is referenced by CSS; if it is only for PDFs, leave it out of the CSS.
- Add a short "Type" rule to Web_Application/CLAUDE.md frontend rules: only --text-* tokens; numbers in tables use .num.
Tests: no font-size below 0.8125rem (13px) in static/css or templates; scale tokens exist; a rendered orders page contains `class="num"` cells; existing suite passes.

### [ ] U6 Component polish: buttons, tables, forms
Why: one consistent look and size for the building blocks on every page, in both themes. Toasts are done in U4.
What:
- Spacing tokens --space-1..6 (0.25, 0.5, 0.75, 1, 1.5, 2rem), --control-h 2.25rem, --control-h-sm 1.75rem, --radius 6px, shared by buttons AND inputs and selects so filter rows line up.
- Buttons: .button (primary), .button-secondary, .button-danger, .button-ghost, plus .button-sm. Links and buttons render identically (inline-flex, centered, no underline, gap for an icon). States: hover (--color-primary-hover), active, :focus-visible ring (2px --color-focus, 2px offset), disabled (muted but readable, cursor not-allowed). Keep the compact heading-inline rule for h1 and h2 buttons. Audit templates: one primary button per view; others become secondary; destructive actions use .button-danger; row actions use ghost small.
- Tables: one base style: muted semibold header on --color-surface-2, cell padding 0.5rem 0.75rem, 1px row separators, row hover --color-hover, sticky header inside .scroll-panel (check the panels template tag and the frozen first column z-index), numbers right-aligned via .num, status as one `.pill` component with a modifier per state (tinted background, state color text, icon and label; consolidate the existing badge classes), a quiet empty-row style. Keep the table-cards behaviour at 768px.
- Forms: shared include `includes/field.html` (label above input, help text below, error text with icon directly under the field, input border in danger color, aria-invalid and aria-describedby, required marker `*` with sr-only text). Convert the main forms without changing field names or POST behaviour: order form, customer edit, user form, settings add rows, tag assign, result form, import. Inputs, selects and textareas use the control height, border, radius, focus ring and muted placeholder; checkbox and radio use accent-color primary. `.form-actions` row: primary Save, then Cancel as a secondary link; sticky at the bottom on the long order form.
- Style guide page `/styleguide/` (Admin only, listed under Developer in the sidebar): all button variants, states and sizes; a table with pills and numbers; a form with an error; buttons that fire demo toasts; a confirm dialog sample; the type scale. English only, exempt from the translation rule (say so in the view's docstring). Its purpose is to check both themes quickly.
Decisions made: no behaviour, URL or permission changes; only markup and classes in templates.
Tests: /styleguide/ is 200 for Admin and 403 for Planner and Operator; smoke test that every main GET page returns 200 for Planner and Admin; an invalid form POST renders `.field-error` with aria-invalid; `python manage.py check` and the full suite pass.

## Blocked
- [!] F48 Backups: waiting for the Pi decisions (yours to look into).
- [!] F44 Print server: printer details unknown (yours to look into).
