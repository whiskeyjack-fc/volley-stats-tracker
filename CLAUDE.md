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
| Templates (20) | `templates/` — `base.html`, `track.html`, `report.html`, `season_report.html`, `player_report.html`, `roster_list.html`, `roster_detail.html`, `roster_form.html`, `roster_import.html`, `training_groups.html`, `training_group_detail.html`, `training_group_form.html`, … |

**DB tables:** `users` · `games` · `players` · `sets` · `events` · `seasons` · `club_teams` · `club_team_players` · `player_profiles` · `player_remarks` · `training_groups` · `training_group_players` · `club_team_trainers`

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
| `_team_cond()` | Returns `(sql_fragment, params)` scoping `club_teams` to trainer's assigned teams; no-op for coordinator/admin |
| `_resolve_profile_id(first, last)` | Normalises `first+" "+last` and returns matching `player_profiles.id` or `None` |

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
| **Use `_team_cond()` for every `club_teams` query** | Trainers see only their assigned teams via `club_team_trainers`; coordinators/admins get no filter — mirrors `_uid_cond()` but for team scope |
| **Player identity = `name.strip().lower()`** | Applied at both insert time and query time; no alias matching |
| **`sqlite3.Row` row factory on every connection** | Set in `get_db()`; forgetting it breaks downstream `dict()` calls silently |
| **Keep line endings as LF** | CRLF in JS/HTML/Python files breaks string-matching tools (replace, grep, patch); enforce with `.gitattributes`: `* text=auto eol=lf` |

---

## Lessons Learned

- **`mkChart(id, cfg)` is mandatory everywhere — never `new Chart()`** — `new Chart()` breaks fullscreen silently (expand button renders blank). The rule applies inside modal open handlers too; assign a distinct ID (e.g. `srcId + '-modal'`) to the modal canvas so it doesn't collide with the inline canvas already in `chartRegistry`.
- **Per-set drill-down is only valid in `main`/`reserve` set-type mode** — do not surface it in filters that aggregate across set types.
- **Always check `cur.rowcount` after every `UPDATE`/`DELETE`** — applies to HTML form routes as well as JSON API endpoints. Return 404 when the row wasn't found; 200/302 for a no-op silently misleads clients.
- **Validate foreign-key ownership at the API boundary** — SQLite FK enforcement is off by default; verify that a supplied `set_id` belongs to the given `game_id` with a SELECT before INSERT.
- **Guard post-INSERT SELECT results** — `db.execute("INSERT…"); row = db.execute("SELECT…").fetchone()` can return `None`; always check `if not row` before accessing fields.
- **`migrate_db` exception handling must distinguish UNIQUE/already-exists errors from real failures** — use `except Exception as exc` and log to `sys.stderr`; never swallow silently with bare `except: pass`.
- **CRLF line endings break string-matching tools** — `replace_string_in_file`, `grep_search`, and patch tools fail silently on CRLF files. If a CRLF file must be edited, use PowerShell `[System.IO.File]::ReadAllText` / `WriteAllText` with regex replace as a fallback.
- **Event delegation + guard flag for tracker grid** — use a single delegated listener on the container (`e.target.closest(".stat-cell")`) instead of per-cell bindings; guard `attachGridDelegation()` with `let _gridDelegated = false` so repeated calls are no-ops.
- **`baseFreq` + `freqMap` double-counting on reload** — `reloadStats()` rebuilds `baseFreq` from server totals which already include in-session taps. Fix: subtract `freqMap` after rebuilding (`baseFreq[pid][stat] = Math.max(0, serverTotal - freqMap[pid][stat])`).
- **Never inject player-supplied names/numbers via `innerHTML`** — use `.textContent` or `document.createElement`; template literals in `.innerHTML` are an XSS vector.
- **Chart label strings must use string concatenation, not template literals, when embedding user-supplied data** — a crafted player name like `` ${alert(1)} `` would execute as JS. Use `(p.number ? "#" + p.number + " " : "") + p.name`.
- **`LOOP_OUTCOMES["fault"]` is intentionally empty** — fault auto-records after player selection; the `else if (loopStat === "fault")` branch in `onPlayerChosen` prevents falling through to an empty `renderOutcomeStep([])`. Do not remove that branch.
- **Background sync: guard on queue length and treat `!r.ok` as a network failure** — skip `isOnline()`/`flushQueue()` when the queue is empty; `!r.ok` responses must increment the failure streak and trigger a toast, same as thrown exceptions.
- **Duplicate player names must be caught client-side** — `game_setup.html`'s submit handler normalises with `.trim().toLowerCase()` and blocks submit on duplicates, mirroring the server-side identity rule.
- **Flask-Limiter on `/login`** — configured with `storage_uri="memory://"` (resets on restart); apply `@limiter.limit(...)` only to routes that need it.
- **Always validate user-supplied filter params against the actual data set** — a `?team=` param that doesn't match any team silently returns empty results; fetch the valid list and reset the param to `""` if not found.
- **Wrap multi-row INSERT loops in explicit try/except + rollback** — a mid-loop failure (incl. in `edit_team`/`new_team`) leaves the parent-row INSERT committed with no children. Extend the `try` block to cover the entire sequence (parent INSERT + child loop + `db.commit()`); add `db.rollback()` in every `except` branch.
- **`aria-expanded` must stay in sync with toggle state** — initialise as an attribute and update it alongside the CSS class toggle.
- **`CAT_COLORS`, `RESULT_LABELS`, and shared axis defaults live in `charts-common.js`** — do not re-declare them inline in any template. `CAT_COLORS` also carries `quality` and `neutral` keys for dual-panel and block charts.
- **Flask-WTF CSRF must cover all POST forms; exempt only `/api/…` JSON routes** — enable `CSRFProtect(app)`, configure `WTF_CSRF_SECRET_KEY`, add `{{ csrf_token() }}` to every HTML form, and decorate API mutation routes with `@csrf.exempt`.
- **Inline `style="width:Npx"` on table columns breaks mobile** — use a CSS class with a responsive `@media (max-width: 600px)` override instead.
- **Undefined CSS variables silently fall back to `inherit`** — always declare every variable explicitly in `:root`.
- **Per-set score in the flow view uses `STAT_POSITIVE`/`STAT_NEGATIVE`** — `computeScoreFromStats()` drives the score bar; `_lastRallyDelta` lets `undoLastAutoSave()` reverse the score client-side without a round-trip.

---

## New Directions

> Use this section to capture ongoing ideas, active work, and design decisions. Keep it current.

### Ideas / Backlog
- Wire `/api/roster/search` typeahead to training-group add-player form (currently uses raw numeric ID input)
- Fix `edit_team()` to use `can_view_all()` guard instead of `_uid_cond()` — trainers who originally created a team can currently still access the edit page
- Re-render `name`/`description` values in `training_group_form.html` on validation error

### Active Work


### Decisions Made
- Player management feature (roster, remarks, training groups, club_team_trainers) shipped on `feature/player-scouting`; trainer assignment UI lives on `team_list.html` (not `team_form.html`) — more user-friendly in practice


