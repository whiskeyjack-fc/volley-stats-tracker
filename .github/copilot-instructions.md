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

## UI & Styling
- Preserve the dark theme; do not introduce light-mode colors or `#fff` backgrounds.
- Follow the existing typography scale: section headers uppercase via `.chart-title`, body text `0.9–1rem`, labels `0.72rem`.
- New cards and panels must use `--surface`, `--border`, and `--radius` CSS variables.
- All new pages and components must be responsive — test at mobile width (≤ 600 px) and verify the layout does not overflow horizontally.
- Interactive controls (buttons, chips) must have a visible hover state using the existing transition pattern (`opacity`, `color`, `background`).

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
