# VolleyStats — Copilot Coding Guidelines

> For architecture quick-ref, critical conventions, and lessons learned, see [CLAUDE.md](../CLAUDE.md) at the project root.

## Charts & Graphs
- **All graphs must support full-screen focus mode.** Use the shared `mkChart(id, cfg)` helper (which registers the config in `chartRegistry`) so any chart can be re-rendered in the full-screen modal. Do not call `new Chart(...)` directly in report pages.
- Every new chart card (`.chart-card` or `.dual-chart-card`) will automatically receive an expand button via the `querySelectorAll` block at the end of the script — no extra markup is required.
- For dual-panel cards (stacked counts + quality), the modal shows both panels separated by a `Quality` divider, matching the inline layout at larger scale.
- Use consistent color semantics: green = positive/kill, red = error/fault, gray = neutral.
- Chart colors must reference values from `CAT_COLORS` or the CSS variable palette (`--green`, `--red`, `--accent`, etc.) — no hardcoded color strings outside those definitions.
- New stat types must be added to both the tracker (`track.html`) and the report charts (`report.html`) consistently.
- All chart canvases must live inside a relatively-positioned wrapper (`.chart-body`, `.dual-top`, or `.dual-bottom`) so the expand button and other overlays position correctly.
- `charts-report.js` must be loaded before any template-level `<script>` block that uses chart infrastructure. No template may redefine `chartRegistry`, `mkChart`, `splitGroupPlugin`, `netTotalPlugin`, `cloneCfg`, `openChartModal`, `closeChartModal`, or `initChartModalListeners` inline — these are provided exclusively by `charts-report.js`.

## Filter Bars
- **Form-based filter bars** (pages with `<form method="get">`) must use the `.filter-bar` wrapper class, `.filter-search` on the text input, and the `filter_select` macro from `_macros.html` for each `<select>`. No inline styles on filter controls.
- **URL-driven chip filters** (report pages) use `.set-filter-bar` rows of `.set-chip` anchors. Use the `filter_chips` macro from `_macros.html` when adding new chip rows.
- Auto-submit on select change is provided globally by `base.html` — no per-page JS needed for `.filter-bar select.filter-select` elements.
- The `Filter` submit button must be kept as a no-JS fallback; it is invisible in normal use because auto-submit fires first.
- New form-based filter pages must `{% from "_macros.html" import filter_select %}` and use the macro — do not write raw `<select>` elements with inline styles.
- **Multi-value dropdown filters** (Excel-style include/exclude) use the `filter_multiselect(name, label, count)` macro from `_macros.html` plus `static/js/filter-multiselect.js` (self-initializing, load via a `<script>` tag on the page — not globally in base.html). Backend routes must read these fields with `request.args.getlist(name)` and build a parameterized `IN (...)` clause only when the list is non-empty; a fully-checked or fully-unchecked group must omit the query param entirely (handled client-side by disabling checkboxes before submit) so nullable columns aren't wrongly excluded.
- **List pages should remember the last-used filters across navigation** via a server-side cookie (not localStorage): on GET, if the request has no query string but a `<page>_filters` cookie exists, redirect to that cookie's query string; the reset link (`Alles tonen`) must pass `?reset=1` to explicitly clear the cookie and redirect to a clean URL; on a normal request with a non-empty query string, set `resp.set_cookie("<page>_filters", request.query_string.decode("utf-8"), max_age=60*60*24*180, samesite="Lax")` on the rendered response. See `kit_list()` in `app.py` for the reference implementation.

## Reports
- New reports live under `/reports/<name>` (page) and `/reports/<name>/export` (CSV), guarded by the access rule appropriate to their source data (e.g. `require_kit_access()` for kit-derived reports).
- Build a shared private helper (e.g. `_duplicate_number_rows()`) that both the page route and the export route call, so the on-screen table and CSV export can never drift out of sync.
- Report pages group results into per-group `.card` sections (team/number/etc. as the heading) each containing a `.table`, following the card-per-group pattern in `conflicts.html`.
- Nav: reports get their own `nav-group-label` ("Rapporten"), gated by the access rule matching their data source.

## Sporthal Conflict Detection (`/conflicts/sporthal`)
- Each match is modelled as timed segments — `warmup`/`game` (non-promo), or
  `warmup`/`reserve`/`warmup`/`game` (promo reeksen matching `^(OHP|ODP|OBP)`). This model is
  duplicated intentionally in two places and both must be kept in sync whenever the timing
  offsets change: `_match_segments()` in `app.py` (detection) and `buildRow()` in
  `conflicts.html` (timeline visualization).
- Whole-block overlap tests (grouping candidate conflict days) must use strict `<`/`>` —
  never `<=`/`>=`. Inclusive comparisons wrongly count boundary-touching (zero-duration)
  matches as overlapping.
- A candidate group (3+ matches with overlapping blocks at `BELVOC_SPORTHAL` on the same day)
  is only a red **conflict** if some pair has an actual overlapping actual-play segment
  (`game` or `reserve` on both sides). If all overlaps only touch a `warmup` segment, downgrade
  the whole group to a yellow **warning** instead — see `_group_has_actual_play_overlap()`.
- Conflicts and warnings render on the same `conflicts_sporthal` view (no separate nav tab):
  warnings are a second sub-list below the red conflicts, reusing the same per-day
  card/timeline/table markup with `.conflict-card-warning`/`.badge-yellow` styling.

## UI & Styling
- Preserve the dark theme; do not introduce light-mode colors or `#fff` backgrounds.
- Follow the existing typography scale: section headers uppercase via `.chart-title`, body text `0.9–1rem`, labels `0.72rem`.
- New cards and panels must use `--surface`, `--border`, and `--radius` CSS variables.
- All new pages and components must be responsive — test at mobile width (≤ 600 px) and verify the layout does not overflow horizontally.
- Interactive controls (buttons, chips) must have a visible hover state using the existing transition pattern (`opacity`, `color`, `background`).
- Nav sections use `.nav-group-label` (plain text divider) and `.nav-sep` (horizontal rule) CSS classes to separate groups. Do not use dropdowns for top-level navigation.
- Stat-accent CSS variables must follow the `--{stat}-accent` naming pattern (e.g. `--serve-accent`, `--attack-accent`). Badge colors must be declared as CSS variables in `style.css`, not hardcoded in templates.

## API & Backend
- All API endpoints (`/api/...`) must return JSON and use appropriate HTTP status codes.
- Do not add new Python dependencies without updating `requirements.txt`.
- Validate all user-supplied input at the API boundary; never construct SQL strings from unvalidated input (use parameterised queries / ORM).

## Player Report (`player_report.html`)
- Chart IDs are namespaced with the player's slug: `{slug}-pointsChart`, `{slug}-chart-serve-top`, etc. Always prefix canvas IDs with `slug + '-'` in both the template and `initPlayerCharts(slug, data)`.
- Player identity is normalised by `name.strip().lower()` across games; the display name is taken from the first record encountered. Typo/alias matching is out of scope.
- Per-player chart data (X-axis = games) is built by calling `build_chart_data(game_rows)` where `game_rows` is a chronological list of `{name, stats: agg_team_stats(...)}` dicts — one entry per game.
- Flat layout only — no accordion for player sections.
- The page carries its own `initPlayerCharts` function; it does not share code with `season_report.html` at runtime, but must stay in sync with its chart logic.

## Player Picker
- **All player-selection tables must use the `player_roster_section` macro** from `_macros.html` — do not write raw `<select name="player_profile_id">` tables inline.
- **Always load `player-picker.js`** (`static/js/player-picker.js`) and call `initPlayerPicker(all_profiles, opts)` after any page that uses `player_roster_section`. The macro provides the HTML; the JS wires the interactivity.
- `initPlayerPicker` options: `showClear` (bool, default `true`), `clearMsg` (string), `noDupCheck` (bool, default `false` — set `true` when the page's own submit listener handles dup-checking).
- `refreshSelects()` (exposed globally by `player-picker.js`) rebuilds every dropdown to show only profiles not already chosen in another row. Call it whenever rows change (the library does this automatically via its own listeners, but page-specific code like team-autofill must also call it after programmatically populating rows).
- Backend routes that accept `player_profile_id` form values must use a Python **seen-set loop** to deduplicate before inserting, and `INSERT OR IGNORE` as the final DB guard.
- `api/teams/<id>/players` returns `profile_id` on each player object; team-autofill JS uses `_makePlayerRow(p.profile_id)` to pre-select the correct profile.

## General
- Do not add external CDN scripts without noting the dependency in the README.
- Prefer editing existing files over creating new ones.
- Keep JS logic for the tracking page in `tracker.js`; keep chart/report JS inside `report.html`'s `<script>` block.
- Do not add comments or docstrings to code you did not change.

## Keeping This File Up to Date
- **Before adding any new guideline, ask the user for confirmation** — propose the wording and the section it belongs to, and only write it once approved.
- Whenever a new pattern, constraint, or convention is established during a coding session, add it to the relevant section of this file before finishing the task.
- If a guideline turns out to be wrong or was superseded by a later change, update or remove it immediately — do not leave stale rules.
- When a new chart type, stat category, or UI component is introduced, record its specific conventions here (color usage, axis rules, helper functions required, etc.).
- New external CDN dependencies must be listed in both this file (under the relevant section) and in `README.md` under **Dependencies**.

## Keeping README.md Up to Date
- When a new page or template is added, add it to the **Project Structure** file tree in `README.md`.
- When a new feature or stat category is introduced, add a bullet to the **Features** section of `README.md`.
- When a new Python package is required, add it to `requirements.txt` **and** update the install instructions in `README.md`.
- When a new CDN script is added to `base.html`, document it in `README.md` under a **Dependencies** section (create it if it doesn't exist yet).
