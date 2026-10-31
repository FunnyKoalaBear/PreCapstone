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
