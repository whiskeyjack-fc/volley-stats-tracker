const Tracker = (() => {
  let gameId;
  let totalEvents = 0;
  let currentSetId = null;   // null = no set selected (track without sets)
  const HOLD_MS = 500;   // ms to trigger a long-press removal

  // ── Helpers ─────────────────────────────────────────────────────────────────

  function cellId(pid, stat, result) {
    return `cnt-${pid}-${stat}-${result}`;
  }

  function getCount(pid, stat, result) {
    const el = document.getElementById(cellId(pid, stat, result));
    return el ? (parseInt(el.textContent) || 0) : 0;
  }

  function setCount(pid, stat, result, val) {
    const el = document.getElementById(cellId(pid, stat, result));
    if (el) el.textContent = Math.max(0, val);
  }

  function showToast(msg, type = "ok") {
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.className = `toast ${type}`;
    clearTimeout(t._timer);
    t._timer = setTimeout(() => { t.className = "toast hidden"; }, 1200);
  }

  function updateEventCount() {
    const el = document.getElementById("event-count");
    if (el) el.textContent = `${totalEvents} event${totalEvents !== 1 ? "s" : ""} recorded`;
  }

  // ── Flash a cell ─────────────────────────────────────────────────────────────

  function flashCell(td, cls) {
    td.classList.add(cls);
    setTimeout(() => td.classList.remove(cls), 220);
  }

  // ── API calls ────────────────────────────────────────────────────────────────

  async function apiRecord(pid, stat, result) {
    const body = { stat, result };
    if (pid !== "opponent") body.player_id = parseInt(pid);
    if (currentSetId !== null) body.set_id = currentSetId;
    const r = await fetch(`/api/games/${gameId}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    return r.ok;
  }

  async function apiDecrement(pid, stat, result) {
    const body = { stat, result };
    if (pid !== "opponent") body.player_id = parseInt(pid);
    const r = await fetch(`/api/games/${gameId}/events/decrement`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!r.ok) return false;
    const data = await r.json();
    return data.removed;
  }

  async function apiUndoLast() {
    const r = await fetch(`/api/games/${gameId}/events`, { method: "DELETE" });
    return r.ok;
  }

  // ── Sets API ─────────────────────────────────────────────────────────────────

  async function fetchSets() {
    const r = await fetch(`/api/games/${gameId}/sets`);
    return r.ok ? await r.json() : [];
  }

  async function apiCreateSet(set_number, set_type) {
    const r = await fetch(`/api/games/${gameId}/sets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ set_number, set_type })
    });
    if (!r.ok) {
      const d = await r.json();
      return { error: d.error || "Failed" };
    }
    return await r.json();
  }

  async function apiFinishSet(setId) {
    const r = await fetch(`/api/games/${gameId}/sets/${setId}/finish`, { method: "POST" });
    return r.ok;
  }

  async function apiDeleteSet(setId) {
    const r = await fetch(`/api/games/${gameId}/sets/${setId}`, { method: "DELETE" });
    return r.ok;
  }

  async function deleteSet(setId) {
    if (!confirm("Delete this set and all its recorded events? This cannot be undone.")) return;
    const ok = await apiDeleteSet(setId);
    if (ok) {
      if (currentSetId === setId) currentSetId = null;
      showToast("Set deleted", "ok");
      await renderSetBar();
    } else {
      showToast("Delete failed", "warn");
    }
  }

  async function apiReopenSet(setId) {
    const r = await fetch(`/api/games/${gameId}/sets/${setId}/reopen`, { method: "POST" });
    return r.ok;
  }

  // ── Set bar rendering ─────────────────────────────────────────────────────────

  function setLabel(s) {
    const typeStr = s.set_type === "reserve" ? "R" : "";
    return `S${s.set_number}${typeStr}`;
  }

  function buildChipEl(s, sets) {
    const wrap = document.createElement("span");
    wrap.className = "set-chip-wrap";

    const chip = document.createElement("button");
    chip.className = "set-chip";
    if (s.finished) chip.classList.add("finished");
    if (s.id === currentSetId) chip.classList.add("active");
    chip.title = `Set ${s.set_number} — ${s.set_type}${s.finished ? " (finished)" : ""}`;
    chip.textContent = setLabel(s);
    chip.addEventListener("click", async () => {
      const allSets = await fetchSets();
      activateSet(s.id, allSets);
    });

    const del = document.createElement("button");
    del.className = "set-chip-del";
    del.title = "Delete set";
    del.textContent = "×";
    del.addEventListener("click", (e) => { e.stopPropagation(); deleteSet(s.id); });

    wrap.appendChild(chip);
    wrap.appendChild(del);
    return wrap;
  }

  async function renderSetBar() {
    const sets = await fetchSets();
    const tabsEl = document.getElementById("set-tabs");
    const finishBtn = document.getElementById("finish-set-btn");
    if (!tabsEl) return;

    tabsEl.innerHTML = "";
    for (const s of sets) {
      tabsEl.appendChild(buildChipEl(s, sets));
    }

    // Show finish button only when an active unfinished set is selected
    const activeSet = sets.find(s => s.id === currentSetId);
    if (finishBtn) {
      finishBtn.style.display = (activeSet && !activeSet.finished) ? "" : "none";
    }

    await reloadStats();
  }

  function activateSet(setId, sets) {
    currentSetId = (currentSetId === setId) ? null : setId;  // toggle off if clicking active
    renderSetBarFromSets(sets);
    reloadStats();
  }

  function renderSetBarFromSets(sets) {
    const tabsEl = document.getElementById("set-tabs");
    const finishBtn = document.getElementById("finish-set-btn");
    if (!tabsEl) return;

    tabsEl.innerHTML = "";
    for (const s of sets) {
      tabsEl.appendChild(buildChipEl(s, sets));
    }

    const activeSet = sets.find(s => s.id === currentSetId);
    if (finishBtn) {
      finishBtn.style.display = (activeSet && !activeSet.finished) ? "" : "none";
    }
  }

  // ── New set panel ─────────────────────────────────────────────────────────────

  function showNewSetPanel() {
    document.getElementById("new-set-panel")?.classList.remove("hidden");
  }

  function hideNewSetPanel() {
    document.getElementById("new-set-panel")?.classList.add("hidden");
  }

  async function startNewSet() {
    const number = parseInt(document.getElementById("ns-number").value);
    const type   = document.getElementById("ns-type").value;
    const result = await apiCreateSet(number, type);
    if (result.error) {
      showToast(result.error, "warn");
      return;
    }
    currentSetId = result.id;
    hideNewSetPanel();
    await renderSetBar();
    showToast(`Set ${number} (${type}) started`, "ok");
  }

  async function finishCurrentSet() {
    if (!currentSetId) return;
    await apiFinishSet(currentSetId);
    showToast("Set finished", "ok");
    await renderSetBar();
  }

  // ── Event handlers ────────────────────────────────────────────────────────────

  async function onAdd(td, pid, stat, result) {
    const ok = await apiRecord(pid, stat, result);
    if (ok) {
      setCount(pid, stat, result, getCount(pid, stat, result) + 1);
      totalEvents++;
      updateEventCount();
      flashCell(td, "cell-add");
    }
  }

  async function onRemove(td, pid, stat, result) {
    const current = getCount(pid, stat, result);
    if (current <= 0) {
      showToast("Nothing to remove", "warn");
      return;
    }
    const removed = await apiDecrement(pid, stat, result);
    if (removed) {
      setCount(pid, stat, result, current - 1);
      totalEvents = Math.max(0, totalEvents - 1);
      updateEventCount();
      flashCell(td, "cell-remove");
      showToast("−1", "remove");
    }
  }

  // ── Long-press detection ──────────────────────────────────────────────────────

  function attachCellHandlers(td) {
    const pid    = td.dataset.pid;
    const stat   = td.dataset.stat;
    const result = td.dataset.result;
    let holdTimer = null;
    let fired = false;

    function startHold(e) {
      fired = false;
      holdTimer = setTimeout(() => {
        fired = true;
        onRemove(td, pid, stat, result);
      }, HOLD_MS);
    }

    function cancelHold() {
      clearTimeout(holdTimer);
    }

    function endHold(e) {
      if (holdTimer) clearTimeout(holdTimer);
      if (!fired) {
        // short click → add
        onAdd(td, pid, stat, result);
      }
    }

    // Mouse
    td.addEventListener("mousedown",  startHold);
    td.addEventListener("mouseup",    endHold);
    td.addEventListener("mouseleave", cancelHold);

    // Touch
    td.addEventListener("touchstart", (e) => { e.preventDefault(); startHold(e); }, { passive: false });
    td.addEventListener("touchend",   (e) => { e.preventDefault(); endHold(e);   }, { passive: false });
    td.addEventListener("touchcancel",cancelHold);

    // Prevent context menu on long-press on mobile
    td.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  // ── Load stats from server ────────────────────────────────────────────────────

  async function reloadStats() {
    const url = currentSetId
      ? `/api/games/${gameId}/stats?set_id=${currentSetId}`
      : `/api/games/${gameId}/stats`;
    const r    = await fetch(url);
    const data = await r.json();
    let total = 0;
    for (const [pid, pdata] of Object.entries(data)) {
      if (!pdata.stats) continue;
      for (const [stat, results] of Object.entries(pdata.stats)) {
        for (const [result, count] of Object.entries(results)) {
          if (result === "total") continue;
          setCount(pid, stat, result, count);
          total += count;
        }
      }
    }
    totalEvents = total;
    updateEventCount();
  }

  // ── Init ──────────────────────────────────────────────────────────────────────

  async function init(gid) {
    gameId = gid;

    document.querySelectorAll(".stat-cell").forEach(attachCellHandlers);

    document.getElementById("undo-btn")?.addEventListener("click", async () => {
      const ok = await apiUndoLast();
      if (ok) {
        await reloadStats();
        showToast("Undone", "ok");
      }
    });

    await renderSetBar();
  }

  return { init, showNewSetPanel, hideNewSetPanel, startNewSet, finishCurrentSet, deleteSet };
})();

