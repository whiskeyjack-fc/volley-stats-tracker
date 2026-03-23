# VolleyStats — Second-Pass Bug-Fix Plan

## How to use this file
This plan covers bugs found in the **second analysis pass** — none overlap with the
existing `VolleyStats-Bug-Fix_Plan.md` sprints (except where noted).
Start a **new chat** for each sprint. Paste the sprint section as context.
The agent will read the codebase itself — you don't need to re-explain the project structure.

---

## Sprint A — Backend: Transaction Safety + Rowcount (`app.py` only)

**Git branch:**
```
git checkout main && git pull && git checkout -b fix/sprint-A-backend-transactions
```
Merge when done:
```
git add -A && git commit -m "Sprint A: transaction safety and rowcount checks (SA1–SA6)" && git checkout main && git merge fix/sprint-A-backend-transactions && git branch -d fix/sprint-A-backend-transactions
```

**Context to paste:** "Fix Sprint A backend bugs in this Flask/SQLite volleyball stats app (`app.py`). All fixes are about transaction safety and rowcount guards. Use `db.rollback()` in except branches. Check `cur.rowcount` after every UPDATE. Do not touch templates or JS."

| ID | File | Description |
|----|------|-------------|
| SA1 | app.py ~L1460 | `new_team()` — after the `club_teams` INSERT succeeds, the `club_team_players` INSERT loop runs outside any try/except. A mid-loop failure leaves the team row committed but with no players. Wrap the entire block (INSERT team + loop) in one try/except with `db.rollback()` on exception. |
| SA2 | app.py ~L1487 | `edit_team()` — the `IntegrityError` catch wraps only the `UPDATE` statement. The subsequent `DELETE club_team_players` and the INSERT loop are outside the try block. Extend the try/except to cover DELETE + loop, with `db.rollback()` on exception. |
| SA3 | app.py ~L1427 | `delete_game()` — three cascading DELETEs (`events`, `players`, `games`) have no error handling. A failure on the second or third DELETE leaves orphaned rows without a rollback. Wrap all three DELETEs + `commit()` in try/except with `db.rollback()`. |
| SA4 | app.py ~L1396 | `edit_game()` — the `UPDATE games` statement's result is not checked for `rowcount`. If the game was deleted between the auth check and the UPDATE, the code silently proceeds to rebuild players for a row that no longer exists. Capture the cursor, check `cur.rowcount == 0`, return 404. |
| SA5 | app.py ~L1501 | `edit_team()` — same pattern as SA4: `UPDATE club_teams` rowcount is never checked. Capture cursor, return 404 if `cur.rowcount == 0`. |
| SA6 | app.py ~L686 | `create_set()` — after INSERT, the SELECT to retrieve the new row is not null-guarded: `dict(new_set)` will raise `TypeError` if `fetchone()` returns `None`. Add `if not new_set: return jsonify({"error": "failed to retrieve set"}), 500`. |

**Note:** BL6 in the existing plan covers `edit_game()`'s *player INSERT loop* (~L1383) but its line reference (~L528) is wrong — L528 is `record_event()`, a single-event INSERT with no loop. SA4 covers the *UPDATE rowcount* in the same route — these are two different problems in the same function; both need fixing.

**Verify after:** Simulate mid-loop failures by temporarily raising inside the INSERT loops and confirm the DB is rolled back cleanly. Confirm editing a non-existent game returns 404, not a silent redirect.

**After implementing:** Update `CLAUDE.md` → **Lessons Learned**: "edit_team and new_team player INSERT loops must be wrapped in try/except+rollback; rowcount must be checked in every UPDATE route, not just JSON API endpoints."

---

## Sprint B — Security: CSRF + XSS (`templates/` + `app.py` wiring)

**Git branch:**
```
git checkout main && git pull && git checkout -b fix/sprint-B-security
```
Merge when done:
```
git add -A && git commit -m "Sprint B: CSRF protection and XSS guard in chart labels (SB1–SB2)" && git checkout main && git merge fix/sprint-B-security && git branch -d fix/sprint-B-security
```

**Context to paste:** "Fix Sprint B security bugs in this Flask/Jinja2 volleyball stats app. SB1 requires adding Flask-WTF for CSRF — update `requirements.txt` and `README.md`. SB2 is a client-side XSS guard in `player_report.html`. Only touch `app.py` to wire in Flask-WTF; do not change any route logic."

| ID | File | Description |
|----|------|-------------|
| SB1 | All templates + app.py | No CSRF protection on any POST form. Add Flask-WTF (`flask-wtf`). Configure `WTF_CSRF_SECRET_KEY` in app config. Use `{{ csrf_token() }}` as a hidden input in every POST form: `base.html` (logout), `admin_users.html` (role change), `index.html` (delete game), `game_setup.html`, `edit_game.html`, `team_list.html` (delete team), `team_form.html`. Add `@csrf.exempt` (or `CSRFProtect` exempt) only on the JSON API routes (`/api/…`) that are called with `fetch()` and pass their own auth context. Update `requirements.txt` and `README.md` → Dependencies. |
| SB2 | player_report.html ~L715, ~L1051, ~L1097 | Player names and jersey numbers are embedded into Chart.js label strings via JS template literals (e.g., `` `#${p.number} ${p.name}` ``). A crafted name like `` ${alert(1)} `` would execute in the page context. Replace all three occurrences with plain string concatenation: `(p.number ? "#" + p.number + " " : "") + p.name`. This removes template-literal expression evaluation while keeping identical output for normal names. |

**Verify after:** Submit a form without a CSRF token — confirm HTTP 400. Add a player named `<img src=x onerror=alert(1)>`, open the player report, confirm no alert fires and the name renders as literal text in the chart legend.

**After implementing:** Update `CLAUDE.md` → **Lessons Learned**: "Flask-WTF CSRF must cover all POST forms; exempt only `/api/…` JSON routes. Chart label strings must use string concatenation, not template literals, when embedding user-supplied data."  
Update `README.md` → **Dependencies**: add `flask-wtf`.

---

## Sprint C — Report Templates: Fullscreen Chart Bug + Hardcoded Colors

**Git branch:**
```
git checkout main && git pull && git checkout -b fix/sprint-C-report-charts
```
Merge when done:
```
git add -A && git commit -m "Sprint C: fix fullscreen modal charts and hardcoded colors (SC1–SC2)" && git checkout main && git merge fix/sprint-C-report-charts && git branch -d fix/sprint-C-report-charts
```

**Context to paste:** "Fix Sprint C chart bugs across the three report templates (`report.html`, `season_report.html`, `player_report.html`). Rule: always use `mkChart(id, cfg)`, never `new Chart()`. Colors must come from `CAT_COLORS` (defined in `charts-common.js`) or CSS variables (`--green`, `--red`, `--accent`, `--text`, `--border`). Do not touch `app.py` or `tracker.js`."

| ID | File | Description |
|----|------|-------------|
| SC1 | report.html ~L313, season_report.html ~L309, player_report.html ~L427 | The fullscreen modal rendering code calls `new Chart(tc, cloneCfg(...))` directly for each modal canvas. These charts are not registered in `chartRegistry`, so the expand button opens the modal but renders a blank canvas. Replace every `new Chart(tc, ...)` inside the modal render block with `mkChart(modalCanvasId, cloneCfg(...))`. Give each modal canvas a distinct ID (e.g., append `-modal` suffix) so it does not collide with the inline canvas ID already registered in `chartRegistry`. |
| SC2 | report.html, season_report.html, player_report.html | 30+ hardcoded `rgba(…)` and hex strings are scattered through the chart config blocks in all three templates — e.g., `rgba(251,191,36,.9)` for ace, `#3ecf6f` for kills, `#e8514a` for errors, `rgba(136,145,178,0.3)` for grid lines, `#a8b4c8` for axis text. Replace with: `CAT_COLORS['ace']`, `CAT_COLORS['kill']`, etc. for stat-specific colors; `getComputedStyle(document.documentElement).getPropertyValue('--green')` / `'--red'` for net-value fill; `getComputedStyle(…).getPropertyValue('--border')` for grid/separator lines. |

**Verify after:** Open a game report and click any chart's expand button — the fullscreen modal must render the correct chart, not a blank canvas. Toggle dark mode (or verify against the dark-theme background) and confirm all chart colours are visible.

**After implementing:** Update `CLAUDE.md` → **Lessons Learned**: "`new Chart()` inside modal render callbacks bypasses `chartRegistry` just as silently as it does for inline charts — the `mkChart()` rule applies everywhere, including inside modal open handlers."

---

## Sprint D — JS: Async Safety + Delegation Guard (`tracker.js` only)

**Git branch:**
```
git checkout main && git pull && git checkout -b fix/sprint-D-tracker-async
```
Merge when done:
```
git add -A && git commit -m "Sprint D: async safety and delegation guard in tracker.js (SD1–SD2)" && git checkout main && git merge fix/sprint-D-tracker-async && git branch -d fix/sprint-D-tracker-async
```

**Context to paste:** "Fix Sprint D bugs in `tracker.js` only. SD1 is a missing `await` that leaves the DOM stale after a set switch. SD2 is a missing idempotency guard on `attachGridDelegation()`. Do not touch templates or `app.py`."

| ID | File | Description |
|----|------|-------------|
| SD1 | tracker.js ~L498 | `activateSet()` calls `reloadStats()` without `await`. `reloadStats()` is async, so it completes after `activateSet()` returns — player-picker totals and stat display show stale data for the previous set until the next render cycle. Make `activateSet` `async` and add `await` before `reloadStats()`. Also add `await` before `activateSet(…)` at its call site in the set-chip click handler (~L485). |
| SD2 | tracker.js ~L425–468, ~L551 | `attachGridDelegation()` attaches 8+ event listeners to the grid container with no guard against being called more than once. If `init()` is ever re-run, all listeners accumulate and every tap fires multiple times (hold callbacks trigger twice, stats double-increment). Add a module-level flag `let _gridDelegated = false;` at the top of the IIFE. At the top of `attachGridDelegation()`, return immediately if `_gridDelegated` is already `true`, then set it to `true` after attaching. |

**Verify after:** Switch sets mid-session — player-picker totals must update immediately to reflect the new set's events, not the previous set's. Call `init()` twice manually in the browser console and confirm each stat cell still increments exactly once per tap (no doubling).

**After implementing:** Update `CLAUDE.md` → **Lessons Learned**: "`attachGridDelegation()` must be guarded with a boolean flag — event delegation prevents per-element accumulation but does not prevent the setup function itself from being called multiple times."

---

## Sprint E — Mobile: Fixed-Width Column Overflow (`game_setup.html`, `edit_game.html`, `team_form.html`, `style.css`)

**Git branch:**
```
git checkout main && git pull && git checkout -b fix/sprint-E-mobile
```
Merge when done:
```
git add -A && git commit -m "Sprint E: fix jersey-number column overflow on mobile (SE1)" && git checkout main && git merge fix/sprint-E-mobile && git branch -d fix/sprint-E-mobile
```

**Context to paste:** "Fix Sprint E mobile overflow. Replace `style=\"width:80px\"` on the jersey-number `<th>` in three templates with a CSS class defined in `style.css`, then add a responsive media query. Do not touch `app.py` or any JS."

| ID | File | Description |
|----|------|-------------|
| SE1 | game_setup.html ~L56, edit_game.html ~L32, team_form.html ~L25, style.css | The jersey-number `<th>` uses `style="width:80px"` which causes the player table to overflow horizontally at ≤ 600 px. In all three templates replace the inline style with `class="num-col"`. In `style.css` add `.num-col { width: 80px; }` at the table styles section, then add `@media (max-width: 600px) { .num-col { width: 56px; min-width: 40px; } }` so the column compresses gracefully on small screens. |

**Verify after:** Open game setup, edit game, and team form pages at 375 px viewport width — confirm the player table has no horizontal scrollbar and all columns remain visible.

**After implementing:** Update `CLAUDE.md` → **Lessons Learned**: "Inline `style=\"width:Npx\"` on table columns breaks mobile layout — always use a CSS class with a responsive media query instead."
