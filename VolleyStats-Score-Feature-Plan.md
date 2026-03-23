# VolleyStats — Per-Set Score Display in Flow View

## How to use this file
This plan adds a live per-set scoreboard to the flow tracking view.
The score is reconstructed from the event log — no DB schema changes required.
Start a **new chat** for each sprint. Paste the sprint section as context.
The agent will read the codebase itself — you don't need to re-explain the project structure.

**Scoring model** (mirrors `STAT_POSITIVE` / `STAT_NEGATIVE` in `app.py`):

| Event | Who scores |
|-------|-----------|
| Our player: `serve.ace`, `attack.kill`, `block.kill` | Us |
| Our player: `serve.error`, `attack.error`, `receive.error`, `fault.fault` | Them |
| Opponent: `serve.error`, `attack.error`, `receive.error`, `fault.fault` | Us |
| Opponent: `serve.ace`, `attack.kill`, `block.kill` | Them |

---

## Sprint I — HTML + CSS: Score Bar Markup and Styles (`track.html`, `style.css`)

**Git branch:**
```
git checkout main && git pull && git checkout -b feat/sprint-I-score-bar-html-css
```
Merge when done:
```
git add -A && git commit -m "Sprint I: add flow score bar HTML and CSS (SI1–SI2)" && git checkout main && git merge feat/sprint-I-score-bar-html-css && git branch -d feat/sprint-I-score-bar-html-css
```

**Context to paste:** "Implement Sprint I of the per-set score display feature in the flow tracking view. SI1 adds the score bar HTML to `track.html`. SI2 adds the CSS styles to `style.css`. Do not touch `tracker.js` or `app.py` — the JS wiring is in Sprint J."

| ID | File | Description |
|----|------|-------------|
| SI1 | track.html between L110 and L111 | The `#lineup-panel` closing tag is at L110 and `#flow-trail` opens at L111. Insert a new `<div id="flow-score-bar" class="flow-score-bar hidden">` between them. Its inner markup: `<span class="score-team">Us</span><span id="score-home" class="score-val">0</span><span class="score-sep">–</span><span id="score-opp" class="score-val">0</span><span class="score-team">Them</span>`. The `hidden` class keeps it invisible until a set is active — Sprint J controls show/hide via JS. |
| SI2 | style.css after L1863 (end of file) | Append three new rule blocks. `.flow-score-bar`: `display: flex; align-items: center; justify-content: center; gap: 0.5rem; padding: 0.45rem 0; border-bottom: 1px solid var(--border); background: var(--surface);`. `.score-val`: `font-size: 1.6rem; font-weight: 700; color: var(--accent); min-width: 2rem; text-align: center;`. `.score-team`: `font-size: 0.72rem; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.04em;`. `.score-sep`: `font-size: 1.3rem; color: var(--text-muted);`. All colours reference CSS variables — no hardcoded hex/rgba. |

**Verify after:** Open the tracking page in flow mode. The score bar div exists in the DOM between the lineup panel and the rally trail. It is not visible (has `hidden` class). Inspect the four new CSS classes in DevTools — all colour values resolve to valid CSS variable references. No horizontal overflow at mobile width (≤ 600 px).

---

## Sprint J — JS: Score Computation and Live Update (`tracker.js` only)

**Git branch:**
```
git checkout main && git pull && git checkout -b feat/sprint-J-score-logic
```
Merge when done:
```
git add -A && git commit -m "Sprint J: compute and display per-set score in flow view (SJ1–SJ6)" && git checkout main && git merge feat/sprint-J-score-logic && git branch -d feat/sprint-J-score-logic
```

**Context to paste:** "Implement Sprint J of the per-set score display feature in `tracker.js` only. The `#flow-score-bar`, `#score-home`, and `#score-opp` elements already exist in the DOM (added in Sprint I). Do not touch templates or `app.py`. Scoring model: our players — `serve.ace`/`attack.kill`/`block.kill` = home point, `serve.error`/`attack.error`/`receive.error`/`fault.fault` = opp point; opponent (pid==='opponent') — errors/faults = home point, ace/kills = opp point."

| ID | File | Description |
|----|------|-------------|
| SJ1 | tracker.js ~L102 | After the existing `let _syncFailStreak = 0;` module-level variable, add `let _lastRallyDelta = { home: 0, opp: 0 };` — stores the score delta of the most recently auto-saved rally so that `undoLastAutoSave()` can reverse it exactly. |
| SJ2 | tracker.js — new helper, add near `reloadStats` | Add a pure helper function `computeScoreFromStats(stats)` that iterates the stats JSON object (keyed by player id strings + `"opponent"`). For each key that is **not** `"opponent"`, read `stats[key].stats` and sum: `home += (serve.ace\|0) + (attack.kill\|0) + (block.kill\|0)` and `opp += (serve.error\|0) + (attack.error\|0) + (receive.error\|0) + (fault.fault\|0)`. For the `"opponent"` key reverse the assignments: errors/faults add to `home`; ace/kills add to `opp`. Use optional chaining (`stats[k].stats?.serve?.ace ?? 0`) to guard missing stat categories. Return `{ home, opp }`. |
| SJ3 | tracker.js — new helper, add near `computeScoreFromStats` | Add `updateFlowScore(home, opp)` that: (1) sets `document.getElementById("score-home").textContent = home` and `document.getElementById("score-opp").textContent = opp`; (2) shows `#flow-score-bar` (removes `hidden` class) when `currentSetId` is truthy, hides it otherwise. Use `.textContent` — never `.innerHTML` — since these are numeric values but the pattern must stay safe. |
| SJ4 | tracker.js ~L586 (inside `reloadStats`) | `reloadStats()` ends with `updateEventCount();` at ~L586. After that line, add: `const sc = computeScoreFromStats(data); updateFlowScore(sc.home, sc.opp);` — `data` is the stats JSON already fetched in that function. This reconstructs the full set score from all stored events on every stat reload (page load, set switch, offline-cache hit). |
| SJ5 | tracker.js ~L1418 (inside `autoSaveRally`) | `autoSaveRally()` loops over `rallyBuf` to POST each action, then calls `resetFlow()` at ~L1419. After the loop ends and before `resetFlow()`, compute the rally's score delta: iterate `rallyBuf` applying the same scoring model used in `computeScoreFromStats` but per-action (each entry has `{ pid, stat, result }`). Store in `_lastRallyDelta`. Then read the current displayed values (`parseInt(document.getElementById("score-home").textContent) \|\| 0` and same for opp) and call `updateFlowScore(home + _lastRallyDelta.home, opp + _lastRallyDelta.opp)`. This gives an instant visual update without waiting for a stats reload. |
| SJ6 | tracker.js ~L1447 (inside `undoLastAutoSave`) | `undoLastAutoSave()` reverses the saved events and calls `goToConfirm()` at ~L1448. Before that call, reverse the stored delta: read current displayed scores, subtract `_lastRallyDelta.home` and `_lastRallyDelta.opp`, call `updateFlowScore(home - delta.home, opp - delta.opp)`. Then reset `_lastRallyDelta = { home: 0, opp: 0 }`. This keeps the displayed score consistent with the undo without a round-trip to the server. |

**Verify after:**
1. Open tracking page in flow mode with a set selected. Score bar is visible above the rally trail showing `0 – 0` (or the correct reconstructed score if events already exist).
2. Record a rally ending in a kill (attack.kill by a home player) → home score increments by 1 immediately.
3. Record a rally ending in a serve error (serve.error by a home player) → opp score increments by 1 immediately.
4. Use the Undo toast → both scores revert to their pre-rally values.
5. Switch to grid mode → score bar disappears (it is inside `#flow-section` which is hidden).
6. Switch the active set (select a different set chip) → `reloadStats()` fires and score recomputes correctly from that set's events.
7. Reload the page with existing events in the set → score is reconstructed correctly from the event log (set = 0–0 for an empty set; correct totals for a set with recorded events).
8. With no set selected (no active set chip), the score bar is hidden.

**After implementing:** Update `CLAUDE.md` → **Lessons Learned**: "Per-set score in the flow view is derived entirely from events using the same `STAT_POSITIVE`/`STAT_NEGATIVE` model as `app.py` — `computeScoreFromStats(stats)` iterates the stats JSON, `updateFlowScore(home, opp)` writes to DOM and shows/hides `#flow-score-bar` based on `currentSetId`, and `_lastRallyDelta` stores the rally delta so `undoLastAutoSave()` can reverse it without a server round-trip."
