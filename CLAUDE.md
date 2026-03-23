# VolleyStats — AI Orientation Guide

## Project Overview

VolleyStats is a Flask 3.x / SQLite volleyball statistics tracker. A single monolithic `app.py` (~1600 lines) handles all routes, stat computation, and DB access. The UI is server-rendered Jinja2 with Chart.js 4 charts and a live tracking grid driven by `tracker.js`. Three roles exist: **trainer** (own data only), **coordinator**, and **admin** (all data). Production runs on PythonAnywhere; deployment is automated via `.github/prompts/deploy.prompt.md`.

---

## Architecture Quick-Ref

| Layer | Key files |
|-------|-----------|
| Backend routes + logic | `app.py` |
| Live tracking JS | `static/js/tracker.js` |
| Styles | `static/css/style.css` |
| Templates (13) | `templates/` — `base.html`, `track.html`, `report.html`, `season_report.html`, `player_report.html`, … |

**DB tables:** `users` · `games` · `players` · `sets` · `events` · `seasons` · `club_teams` · `club_team_players`

**Stat pipeline:**
```
events → build_player_stats() → agg_team_stats() → build_chart_data() → charts
```

**Key backend helpers:**

| Function | Purpose |
|----------|---------|
| `build_player_stats(events, players)` | Per-player stat summary from raw events |
| `agg_team_stats(events)` | Team-level aggregates |
| `build_chart_data(game_rows)` | Chart-ready arrays; X-axis = games |
| `build_comparison_data(...)` | Multi-player cross-game comparison |
| `_uid_cond()` | Returns `(sql_fragment, params)` for user-scoped queries |

For full chart, UI, and API rules see [.github/copilot-instructions.md](.github/copilot-instructions.md).

---

## Critical Conventions

| Rule | Reason |
|------|--------|
| **Always `mkChart(id, cfg)`** — never `new Chart()` | Registers in `chartRegistry`; required for fullscreen expand modal |
| **Use `_uid_cond()` for every trainer query** | Coordinators/admins have no user filter — hardcoding `user_id=?` breaks their view |
| **Parameterized queries only** — `(?, ?)` placeholders | No f-string / concatenated SQL anywhere (SQL injection) |
| **Chart canvas IDs must be `{slug}-{chartName}`** | Both the template and `initPlayerCharts(slug, data)` must match exactly |
| **DB migrations are non-destructive** | `ALTER TABLE … ADD COLUMN IF NOT EXISTS` only; never DROP columns |
| **Tracking JS stays in `tracker.js`** | Report/chart JS lives inside each template's own `<script>` block; pages don't share runtime JS |
| **Player identity = `name.strip().lower()`** | Applied at both insert time and query time; no alias matching |
| **`sqlite3.Row` row factory on every connection** | Set in `get_db()`; forgetting it breaks downstream `dict()` calls silently |
| **Keep line endings as LF** | CRLF in JS/HTML/Python files breaks string-matching tools (replace, grep, patch); enforce with `.gitattributes`: `* text=auto eol=lf` |

---

## Lessons Learned

- **`new Chart()` breaks fullscreen silently** — the expand button appears but re-renders a blank chart. Always `mkChart()`.
- **Missing slug prefix on a canvas ID** causes multi-player chart datasets to map to the wrong player — no JS error, just wrong data rendered.
- **Skipping `_uid_cond()`** produces no error; a trainer simply sees other users' games. Always validate scope isolation after adding new list queries.
- **`sqlite3.Row` row factory** must be set on every new `get_db()` call. A missing line causes `dict(row)` failures that only surface at JSON serialisation, not at query time.
- **Chart colors outside `CAT_COLORS` / CSS variables** break dark-mode consistency and are invisible against dark backgrounds — always pull from the defined palette.
- **Player name normalisation must be symmetric** — if `name.strip().lower()` is applied on insert but not on lookup (or vice versa), the same player appears as two separate entries in reports.
- **Per-set drill-down is only valid in `main`/`reserve` set-type mode** — do not surface it in filters that aggregate across set types.
- **Always check `cur.rowcount` after `UPDATE`/`DELETE` in API endpoints** — returning 200/302 for a no-op UPDATE silently misleads clients; return 404 when the row wasn't found.
- **Validate foreign-key ownership at the API boundary** — SQLite FK enforcement is off by default, so verify that a supplied `set_id` belongs to the given `game_id` with a SELECT before INSERT.
- **Guard post-INSERT SELECT results** — `db.execute("INSERT…"); row = db.execute("SELECT…").fetchone()` can return `None` in race conditions or edge cases; always check `if not row` before accessing fields.
- **`migrate_db` exception handling must distinguish UNIQUE/already-exists errors from real failures** — use `except Exception as exc` and log unexpected errors to `sys.stderr` rather than silently swallowing them with bare `except: pass`.
- **CRLF line endings break string-matching tools** — `replace_string_in_file`, `grep_search`, and patch tools all fail silently or produce no matches when the file uses CRLF (`\r\n`). Set `* text=auto eol=lf` in `.gitattributes` to enforce LF repo-wide; if a CRLF file must be edited, use PowerShell `[System.IO.File]::ReadAllText` / `WriteAllText` with regex replace as a fallback.
- **Event delegation prevents duplicate listener accumulation** — attaching handlers to individual cells with `forEach(attachCellHandlers)` risks double-binding if the setup code ever runs more than once. A single delegated listener on the container (`e.target.closest(".stat-cell")`) is idempotent and handles any dynamically added cells automatically.
- **`baseFreq` + `freqMap` double-counting on reload** — `reloadStats()` rebuilds `baseFreq` from server totals which already include the in-session taps stored in `freqMap`. Fix: after rebuilding `baseFreq`, subtract `freqMap` per key so `freqSortedPlayers` returns the server total without double-counting (`baseFreq[pid][stat] = Math.max(0, serverTotal - freqMap[pid][stat])`).
- **Never inject player-supplied names/numbers via `innerHTML`** — player names and jersey numbers come from user input and must be set via `.textContent` or `document.createElement` + `textContent`; using template literals in `.innerHTML` creates an XSS vector even for internal tools.

---

## New Directions

> Use this section to capture ongoing ideas, active work, and design decisions. Keep it current.

### Ideas / Backlog


### Active Work


### Decisions Made


