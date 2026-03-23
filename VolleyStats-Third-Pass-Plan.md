# VolleyStats — Third-Pass Bug-Fix Plan

## How to use this file
This plan covers bugs found in the **third analysis pass** — none overlap with
`VolleyStats-Bug-Fix_Plan.md` (Sprints 1–5) or `VolleyStats-New-Bugs-Plan.md` (Sprints A–E).
All bugs in those two plans are **confirmed fixed**.
Start a **new chat** for each sprint. Paste the sprint section as context.
The agent will read the codebase itself — you don't need to re-explain the project structure.

---

## Sprint F — Backend: delete_team Transaction Safety + Name Normalisation (`app.py` only)

**Git branch:**
```
git checkout main && git pull && git checkout -b fix/sprint-F-backend
```
Merge when done:
```
git add -A && git commit -m "Sprint F: delete_team rollback and player name normalisation (SF1–SF2)" && git checkout main && git merge fix/sprint-F-backend && git branch -d fix/sprint-F-backend
```

**Context to paste:** "Fix Sprint F backend bugs in this Flask/SQLite volleyball stats app (`app.py`). SF1 is a missing try/except+rollback in `delete_team()`. SF2 is a player name normalisation asymmetry — all four INSERT sites must use `name.strip().lower()` to match the query-time convention. Do not touch templates or JS."

| ID | File | Description |
|----|------|-------------|
| SF1 | app.py ~L1557 | `delete_team()` — two `DELETE` statements (`club_team_players`, then `club_teams`) run without any `try/except`/rollback. If the first DELETE raises an exception the transaction is left open with no rollback, potentially leaving orphaned `club_teams` rows. Wrap both DELETEs + `db.commit()` in `try/except Exception: db.rollback(); raise` — the same pattern used in `delete_game()`. |
| SF2 | app.py L483, L1419, L1490, L1530 | Player names are stored with `name.strip()` (case preserved) at all four INSERT sites: `new_game()` player loop, `edit_game()` player loop, `new_team()` player loop, and `edit_team()` player loop. Query-time grouping in the player report uses `name.strip().lower()`, and the roster membership check at L1121 also uses `.strip().lower()`. Per CLAUDE.md: "Player identity = `name.strip().lower()` — Applied at both insert time and query time." Change every `name.strip()` at those four INSERT sites to `name.strip().lower()` so casing mismatches between games never silently create duplicate player identities. |

**Verify after:** Confirm `delete_team` with a simulated DB error leaves no orphaned rows and returns a clean 500 rather than a partial commit. Add a player as "Alice Smith" in one game and "alice smith" in another — the player report must group them as one player.

**After implementing:** Update `CLAUDE.md` → **Lessons Learned**: "Player name normalisation at INSERT must use `name.strip().lower()`, not `name.strip()` — the asymmetry between insert-time and query-time casing silently creates duplicate player identities in reports."

---

## Sprint G — CSS & Templates: Undefined Variable + Hardcoded Colours + Accessibility

**Git branch:**
```
git checkout main && git pull && git checkout -b fix/sprint-G-css-templates
```
Merge when done:
```
git add -A && git commit -m "Sprint G: fix --fg variable, hardcoded colours, and modal accessibility (SG1–SG6)" && git checkout main && git merge fix/sprint-G-css-templates && git branch -d fix/sprint-G-css-templates
```

**Context to paste:** "Fix Sprint G CSS and template bugs in this Flask volleyball stats app. Changes are in `static/css/style.css`, `static/js/charts-common.js`, and the three report templates (`report.html`, `season_report.html`, `player_report.html`). Do not touch `app.py` or `tracker.js`. Colours must reference `CAT_COLORS` or CSS variables — no hardcoded hex/rgba outside those definitions."

| ID | File | Description |
|----|------|-------------|
| SG1 | style.css L318, L359 | `--fg` CSS variable is used in `.set-chip { color }` and `.new-set-panel select { color }` but is never declared in `:root`. It only works today because `color` is an inherited property and the body carries `var(--text)`. Fix: add `--fg: var(--text);` to the `:root` block so the variable is explicitly defined. |
| SG2 | style.css ~L365 | `.new-set-panel select { background: #1e2130; }` — hardcoded dark colour that breaks theme consistency. Replace with `var(--surface2)` (defined as `#22263a` in `:root`). |
| SG3 | report.html L247, season_report.html L247, player_report.html L340 | `ctx.fillStyle = "rgba(255,255,255,0.04)"` inside the split-group canvas plugin uses a hardcoded white. This is a leftover from SC2. Replace with `"rgba(255,255,255,0.04)"` → compute from the CSS variable: `getComputedStyle(document.documentElement).getPropertyValue('--surface2').trim()` and paint at low opacity, **or** define a `--split-fill` CSS variable (e.g. `rgba(255,255,255,0.04)`) in `:root` and reference it via `getComputedStyle`. The simplest correct fix is to add `--split-fill: rgba(255,255,255,0.04);` to `:root` in `style.css` and replace the three hardcoded strings with `getComputedStyle(document.documentElement).getPropertyValue('--split-fill').trim()`. |
| SG4 | report.html ~L446 | `backgroundColor: "rgba(45,185,170,.75)"` — hardcoded teal used for quality stat bars in dual chart panels. Add `quality: "rgba(45,185,170,.75)"` to the `CAT_COLORS` object in `static/js/charts-common.js`, then replace the hardcoded string with `CAT_COLORS['quality']`. |
| SG5 | report.html ~L535 | `backgroundColor: "rgba(136,145,178,.65)"` — hardcoded grey used for neutral error bars in the block chart. This matches the `--gray` CSS variable value. Replace with `getComputedStyle(document.documentElement).getPropertyValue('--gray').trim()` wrapped in a helper, **or** use `CAT_COLORS['neutral']` after adding `neutral: "rgba(136,145,178,.65)"` to `CAT_COLORS` in `charts-common.js`. |
| SG6 | report.html L207, season_report.html L206, player_report.html L298 | Each fullscreen modal `<div role="dialog" aria-modal="true">` is missing an `aria-labelledby` attribute. The modal already contains an element with `id="chart-modal-title"` that is updated with the chart name. Add `aria-labelledby="chart-modal-title"` to the modal wrapper `<div>` in all three templates so screen readers announce the chart name when the dialog opens. |

**Verify after:** Inspect `.set-chip` text colour in browser DevTools — it must resolve to `var(--text)`. Toggle dark mode and confirm all chart colours remain visible. Open the fullscreen modal with a screen reader or the DevTools Accessibility tree — the dialog role must show an accessible name equal to the chart title. Check that quality and neutral bars in dual/block charts still render their original colours.

**After implementing:** Update `CLAUDE.md` → **Lessons Learned**: "Undefined CSS variables referenced via `var()` silently fall back to `inherit` for inherited properties — always declare every variable explicitly in `:root` to avoid fragile implicit inheritance."  
Update `charts-common.js` entry in `CLAUDE.md` **Architecture Quick-Ref** to note the `quality` and `neutral` keys in `CAT_COLORS`.

---

## Sprint H — JS: flushQueue HTTP Error Handling (`tracker.js` only)

**Git branch:**
```
git checkout main && git pull && git checkout -b fix/sprint-H-tracker-sync
```
Merge when done:
```
git add -A && git commit -m "Sprint H: surface HTTP errors in flushQueue sync loop (SH1)" && git checkout main && git merge fix/sprint-H-tracker-sync && git branch -d fix/sprint-H-tracker-sync
```

**Context to paste:** "Fix Sprint H in `tracker.js` only. SH1 is a partial fix to the sync loop: HTTP error responses (`!r.ok`) are currently silently skipped — only thrown network exceptions increment `_syncFailStreak` and trigger a toast. Extend the same streak/toast logic to cover `!r.ok` responses. Do not touch templates or `app.py`."

| ID | File | Description |
|----|------|-------------|
| SH1 | tracker.js ~L376 | `flushQueue()` inner loop: the `try` block checks `if (r.ok)` to mark an op synced, but the `else` branch (HTTP 4xx/5xx response) does nothing — the op is silently skipped and `_syncFailStreak` is never incremented. Only a *thrown* exception (e.g. network offline) reaches the `catch` block that increments the streak and shows a toast after 2 failures. Fix: add an `else` branch after `if (r.ok) { ... }` that executes `_syncFailStreak++; if (_syncFailStreak >= 2) { showToast("Sync failed – check connection", "warn"); _syncFailStreak = 0; }` — mirroring the existing `catch` logic so server-side errors also surface to the user. |

**Verify after:** In browser DevTools Network tab, block the `/api/…` sync URL. Create two events and trigger a background sync — confirm the "Sync failed – check connection" toast appears after 2 failed attempts. Re-enable the network — confirm the next sync succeeds silently and clears remaining queued ops. Also simulate an HTTP 500 response (via a proxy or temporary server error) and confirm the toast fires after 2 such responses.

**After implementing:** Update `CLAUDE.md` → **Lessons Learned**: "`flushQueue` must treat `!r.ok` HTTP responses the same as thrown network exceptions — both should increment the failure streak and trigger a user-visible toast after the threshold is reached."
