// â”€â”€ IndexedDB offline queue â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const DB = (() => {
  const DB_NAME  = "volleystats";
  const DB_VER   = 1;
  let _db = null;

  function open() {
    if (_db) return Promise.resolve(_db);
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VER);
      req.onupgradeneeded = e => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains("ops")) {
          const os = db.createObjectStore("ops", { keyPath: "localId", autoIncrement: true });
          os.createIndex("status", "status");
        }
        if (!db.objectStoreNames.contains("gameCache")) {
          db.createObjectStore("gameCache", { keyPath: "key" });
        }
      };
      req.onsuccess  = e => { _db = e.target.result; resolve(_db); };
      req.onerror    = ()  => reject(req.error);
    });
  }

  async function tx(storeName, mode, fn) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const t  = db.transaction(storeName, mode);
      const os = t.objectStore(storeName);
      const req = fn(os);
      req.onsuccess = () => resolve(req.result);
      req.onerror   = () => reject(req.error);
    });
  }

  async function enqueue(op) {
    return tx("ops", "readwrite", os => os.add({ ...op, status: "pending" }));
  }

  async function getPending() {
    const db = await open();
    return new Promise((resolve, reject) => {
      const t   = db.transaction("ops", "readonly");
      const idx = t.objectStore("ops").index("status");
      const req = idx.getAll("pending");
      req.onsuccess = () => resolve(req.result);
      req.onerror   = () => reject(req.error);
    });
  }

  async function markSynced(localId) {
    return tx("ops", "readwrite", os => os.delete(localId));
  }

  async function setCache(key, value) {
    return tx("gameCache", "readwrite", os => os.put({ key, value }));
  }

  async function getCache(key) {
    const r = await tx("gameCache", "readonly", os => os.get(key));
    return r ? r.value : null;
  }

  return { enqueue, getPending, markSynced, setCache, getCache };
})();


// â”€â”€ Main Tracker module â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const Tracker = (() => {
  let gameId;
  let totalEvents = 0;
  let currentSetId = null;   // null = no set selected
  let cachedSets    = [];      // latest fetched set list
  let players = [];          // [{id, name, number}] from server/cache
  let oppName  = "";
  const HOLD_MS = 500;

  // per-session frequency counters: {pid â†’ {stat â†’ count}}
  const freqMap  = {};
  const baseFreq = {};

  function bumpFreq(pid, stat) {
    if (!freqMap[pid]) freqMap[pid] = {};
    freqMap[pid][stat] = (freqMap[pid][stat] || 0) + 1;
  }

  function freqSortedPlayers(stat) {
    return [...players].sort((a, b) => {
      const fa = ((baseFreq[a.id] || {})[stat] || 0) + ((freqMap[a.id] || {})[stat] || 0);
      const fb = ((baseFreq[b.id] || {})[stat] || 0) + ((freqMap[b.id] || {})[stat] || 0);
      return fb - fa || (parseInt(a.number)||999) - (parseInt(b.number)||999);
    });
  }

  // â”€â”€ Sync helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  let syncInFlight = false;
  // localSetRef â†’ real server set_id
  const setIdMap = {};

  async function isOnline() {
    if (!navigator.onLine) return false;
    try {
      const r = await fetch("/api/health", { method: "HEAD", cache: "no-store" });
      return r.ok;
    } catch { return false; }
  }

  async function flushQueue() {
    if (syncInFlight) return;
    syncInFlight = true;
    try {
      const ops = await DB.getPending();
      for (const op of ops) {
        let url     = op.url;
        let payload = op.payload ? { ...op.payload } : null;

        // Substitute local set ref with real server id
        if (payload && payload._localSetRef) {
          const realId = setIdMap[payload._localSetRef];
          if (!realId) continue;   // set not yet created â€” skip for now
          payload.set_id = realId;
          delete payload._localSetRef;
        }
        if (op._localSetRef && !op.url.includes("/sets/")) {
          const realId = setIdMap[op._localSetRef];
          if (!realId) continue;
          url = url.replace("__SET__", realId);
        }

        try {
          const fetchOpts = {
            method: op.method,
            headers: payload ? { "Content-Type": "application/json" } : {},
            body:    payload ? JSON.stringify(payload) : undefined,
          };
          const r = await fetch(url, fetchOpts);
          if (r.ok) {
            // If this was a create-set op, capture the new server id
            if (op.type === "createSet") {
              const d = await r.json();
              if (op.localSetRef) setIdMap[op.localSetRef] = d.id;
            }
            await DB.markSynced(op.localId);
          }
        } catch { /* network error â€” try again next cycle */ }
      }
    } finally {
      syncInFlight = false;
      updateSyncPill();
    }
  }

  async function updateSyncPill() {
    const ops = await DB.getPending();
    const pill = document.getElementById("sync-pill");
    const btn  = document.getElementById("sync-now-btn");
    if (!pill) return;
    if (ops.length === 0) {
      pill.style.display = "none";
    } else {
      pill.style.display = "";
      pill.textContent   = `Unsynced: ${ops.length}`;
    }
    if (btn) btn.style.display = ops.length > 0 ? "" : "none";
  }

  async function enqueueOp(op) {
    await DB.enqueue(op);
    updateSyncPill();
    isOnline().then(on => { if (on) flushQueue(); });
  }

  // â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

  function flashCell(td, cls) {
    td.classList.add(cls);
    setTimeout(() => td.classList.remove(cls), 220);
  }

  // â”€â”€ API calls (queue-backed) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  async function apiRecord(pid, stat, result) {
    const payload = { stat, result };
    if (pid !== "opponent") payload.player_id = parseInt(pid);
    if (currentSetId !== null) payload.set_id = currentSetId;
    await enqueueOp({ type: "event", method: "POST", url: `/api/games/${gameId}/events`, payload });
    return true;
  }

  async function apiDecrement(pid, stat, result) {
    const payload = { stat, result };
    if (pid !== "opponent") payload.player_id = parseInt(pid);
    await enqueueOp({ type: "decrement", method: "POST", url: `/api/games/${gameId}/events/decrement`, payload });
    return true;
  }

  async function apiUndoLast() {
    await enqueueOp({ type: "undoLast", method: "DELETE", url: `/api/games/${gameId}/events`, payload: null });
    return true;
  }

  // â”€â”€ Sets API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  async function fetchSets() {
    try {
      const r = await fetch(`/api/games/${gameId}/sets`);
      if (r.ok) {
        const data = await r.json();
        await DB.setCache(`sets-${gameId}`, data);
        return data;
      }
    } catch {}
    return (await DB.getCache(`sets-${gameId}`)) || [];
  }

  async function apiCreateSet(set_number, set_type) {
    try {
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
    } catch {
      // Offline: create a local placeholder
      const localRef = `local-${Date.now()}`;
      await enqueueOp({
        type: "createSet", method: "POST",
        url: `/api/games/${gameId}/sets`,
        payload: { set_number, set_type },
        localSetRef: localRef
      });
      const fakeId = -(Date.now());
      setIdMap[localRef] = null;   // will be filled on sync
      return { id: fakeId, set_number, set_type, finished: 0, _localRef: localRef };
    }
  }

  async function apiFinishSet(setId) {
    try {
      const r = await fetch(`/api/games/${gameId}/sets/${setId}/finish`, { method: "POST" });
      return r.ok;
    } catch {
      await enqueueOp({ type: "finishSet", method: "POST", url: `/api/games/${gameId}/sets/${setId}/finish`, payload: null });
      return true;
    }
  }

  async function apiDeleteSet(setId) {
    try {
      const r = await fetch(`/api/games/${gameId}/sets/${setId}`, { method: "DELETE" });
      return r.ok;
    } catch {
      await enqueueOp({ type: "deleteSet", method: "DELETE", url: `/api/games/${gameId}/sets/${setId}`, payload: null });
      return true;
    }
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
    try {
      const r = await fetch(`/api/games/${gameId}/sets/${setId}/reopen`, { method: "POST" });
      return r.ok;
    } catch {
      await enqueueOp({ type: "reopenSet", method: "POST", url: `/api/games/${gameId}/sets/${setId}/reopen`, payload: null });
      return true;
    }
  }

  // â”€â”€ Set bar rendering â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    chip.title = `Set ${s.set_number} â€” ${s.set_type}${s.finished ? " (finished)" : ""}`;
    chip.textContent = setLabel(s);
    chip.addEventListener("click", async () => {
      const allSets = await fetchSets();
      activateSet(s.id, allSets);
    });

    const del = document.createElement("button");
    del.className = "set-chip-del";
    del.title = "Delete set";
    del.textContent = "\u00D7";
    del.addEventListener("click", (e) => { e.stopPropagation(); deleteSet(s.id); });

    wrap.appendChild(chip);
    wrap.appendChild(del);
    return wrap;
  }

  async function renderSetBar() {
    const sets = await fetchSets();
    cachedSets = sets;
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
    if (typeof RallyFlow !== "undefined") RallyFlow.resetLineup();
  }

  function renderSetBarFromSets(sets) {
    cachedSets = sets;
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

  // â”€â”€ New set panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

  // â”€â”€ Event handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
      showToast("-1", "remove");
    }
  }

  // â”€â”€ Long-press detection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
        // short click â†’ add
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

  // â”€â”€ Load stats from server â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  async function reloadStats() {
    const url = currentSetId
      ? `/api/games/${gameId}/stats?set_id=${currentSetId}`
      : `/api/games/${gameId}/stats`;
    let data = null;
    try {
      const r = await fetch(url);
      if (r.ok) {
        data = await r.json();
        await DB.setCache(`stats-${gameId}-${currentSetId}`, data);
      }
    } catch {}
    if (!data) data = await DB.getCache(`stats-${gameId}-${currentSetId}`) || {};
    let total = 0;
    // Rebuild baseFreq from scratch (avoids double-counting on set changes)
    Object.keys(baseFreq).forEach(k => delete baseFreq[k]);
    for (const [pid, pdata] of Object.entries(data)) {
      if (!pdata.stats) continue;
      for (const [stat, results] of Object.entries(pdata.stats)) {
        for (const [result, count] of Object.entries(results)) {
          if (result === "total") continue;
          setCount(pid, stat, result, count);
          total += count;
          if (pid !== "opponent" && count > 0) {
            if (!baseFreq[pid]) baseFreq[pid] = {};
            baseFreq[pid][stat] = (baseFreq[pid][stat] || 0) + count;
          }
        }
      }
    }
    totalEvents = total;
    updateEventCount();
  }

  // â”€â”€ Init â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  async function init(gid, playersData, opponentName) {
    gameId  = gid;
    players = playersData || [];
    oppName = opponentName || "Opponent";

    // Cache players for offline use
    await DB.setCache(`players-${gameId}`, players);

    // Expose bridge functions for RallyFlow
    window.__players           = players;
    window.__oppName           = oppName;
    window.__freqSortedPlayers = freqSortedPlayers;
    window.__bumpFreq          = bumpFreq;
    window.__apiRecord         = apiRecord;
    window.__getCount          = getCount;
    window.__setCount          = setCount;
    window.__incrTotal         = () => { totalEvents++; };
    window.__decrTotal         = () => { totalEvents = Math.max(0, totalEvents - 1); };
    window.__apiDecrement      = apiDecrement;
    window.__updateEventCount  = updateEventCount;
    window.__showToast         = showToast;
    window.__getCurrentSetId   = () => currentSetId;
    window.__isActiveSetFinished = () => {
      if (!currentSetId) return false;
      const s = cachedSets.find(x => x.id === currentSetId);
      return s ? !!s.finished : false;
    };

    document.querySelectorAll(".stat-cell").forEach(attachCellHandlers);

    document.getElementById("sync-now-btn")?.addEventListener("click", async () => {
      showToast("Syncing...", "ok");
      await flushQueue();
      await reloadStats();
      showToast("Synced", "ok");
    });

    // Mode toggle: Grid â†” Flow
    const modeToggle = document.getElementById("mode-toggle");
    if (modeToggle) {
      const saved = localStorage.getItem("tracker-mode") || "grid";
      setMode(saved);
      modeToggle.addEventListener("click", () => {
        const next = getCurrentMode() === "grid" ? "flow" : "grid";
        setMode(next);
        localStorage.setItem("tracker-mode", next);
      });
    }

    await renderSetBar();
    updateSyncPill();

    // Start background sync polling every 30 s
    setInterval(() => { isOnline().then(on => { if (on) flushQueue(); }); }, 30000);

    RallyFlow.init(gameId);
  }

  function getCurrentMode() {
    return document.getElementById("grid-section")?.classList.contains("hidden") ? "flow" : "grid";
  }

  function setMode(mode) {
    const gridSection = document.getElementById("grid-section");
    const flowSection = document.getElementById("flow-section");
    const btn         = document.getElementById("mode-toggle");
    const hint        = document.querySelector(".grid-hint");
    if (!gridSection || !flowSection) return;
    if (mode === "flow") {
      gridSection.classList.add("hidden");
      flowSection.classList.remove("hidden");
      if (btn) btn.textContent = "\u2630 Grid";
      if (hint) hint.classList.add("hidden");
    } else {
      gridSection.classList.remove("hidden");
      flowSection.classList.add("hidden");
      if (btn) btn.textContent = "\u26A1 Flow";
      if (hint) hint.classList.remove("hidden");
    }
  }

  return { init, showNewSetPanel, hideNewSetPanel, startNewSet, finishCurrentSet, deleteSet };

})();


// -- RallyFlow state machine ---------------------------------------------------

const RallyFlow = (() => {

  const SERVE_OUTCOMES = [
    { label: "Ace", result: "ace",     color: "green"  },
    { label: "S3",  result: "3-serve", color: "yellow", loop: true },
    { label: "S2",  result: "2-serve", color: "yellow", loop: true },
    { label: "S1",  result: "1-serve", color: "yellow", loop: true },
    { label: "Err", result: "error",   color: "red"    },
  ];

  const RECEIVE_OPP_OUTCOMES = [
    { label: "Ace", result: "ace",   color: "red"   },
    { label: "Err", result: "error", color: "green" },
  ];

  const RECEIVE_OUR_OUTCOMES = [
    { label: "R3",  result: "3-receive", color: "yellow", loop: true },
    { label: "R2",  result: "2-receive", color: "yellow", loop: true },
    { label: "R1",  result: "1-receive", color: "yellow", loop: true },
    { label: "OvP", result: "overpass",  color: "yellow", loop: true },
    { label: "Err", result: "error",     color: "red"    },
  ];

  const LOOP_ACTIONS = [
    { label: "Attack",   stat: "attack"   },
    { label: "Block",    stat: "block"    },
    { label: "Freeball", stat: "freeball" },
    { label: "Fault",    stat: "fault"    },
  ];

  const LOOP_OUTCOMES = {
    attack:   [
      { label: "Kill", result: "kill",  color: "green" },
      { label: "Err",  result: "error", color: "red"   },
    ],
    block:    [
      { label: "Kill", result: "kill",  color: "green" },
      { label: "Err",  result: "error", color: "red"   },
    ],
    freeball: [
      { label: "F3",   result: "3-freeball", color: "yellow", loop: true },
      { label: "F2",   result: "2-freeball", color: "yellow", loop: true },
      { label: "F1",   result: "1-freeball", color: "yellow", loop: true },
      { label: "Err",  result: "error",      color: "red"    },
    ],
    fault:    [],
  };

  let state              = "idle";
  let rallyBuf           = [];
  let loopStat           = null;
  let loopPid            = null;
  let loopOutcomePending = null;  // set by combined card; cleared when player chosen
  let editIdx            = null;
  let lsKey              = null;  // set by init() once gameId is known
  let lastSavedBuf       = [];    // snapshot of last auto-saved rally (for undo)
  let undoToastTimer     = null;
  const lineupBySet      = {};  // setId (string) → Set of player ID strings

  function saveLineups() {
    if (!lsKey) return;
    const plain = {};
    Object.keys(lineupBySet).forEach(sid => {
      plain[sid] = [...lineupBySet[sid]];
    });
    try { localStorage.setItem(lsKey, JSON.stringify(plain)); } catch (_) {}
  }

  function loadLineups() {
    if (!lsKey) return;
    try {
      const raw = localStorage.getItem(lsKey);
      if (!raw) return;
      const plain = JSON.parse(raw);
      Object.keys(plain).forEach(sid => {
        lineupBySet[sid] = new Set(plain[sid]);
      });
    } catch (_) {}
  }

  // ── Lineup helpers ────────────────────────────────────────────────────────

  function getActiveLineup() {
    const sid = window.__getCurrentSetId ? String(window.__getCurrentSetId()) : null;
    if (!sid) return new Set(); // no set active — return throwaway
    if (!lineupBySet[sid]) lineupBySet[sid] = new Set();
    return lineupBySet[sid];
  }

  function getLineupSorted(stat) {
    const lineup = getActiveLineup();
    const all = window.__freqSortedPlayers
      ? window.__freqSortedPlayers(stat)
      : (window.__players || []);
    if (lineup.size === 0) return all;
    return all.filter(p => lineup.has(String(p.id)));
  }

  function updateLineupButton() {
    const size = getActiveLineup().size;
    const cnt = section("lineup-count");
    if (cnt) cnt.textContent = `${size}/7`;
    const badge = section("lineup-count-badge");
    if (badge) badge.textContent = `${size} / 7`;
  }

  function renderLineupPanel() {
    const grid = section("lineup-player-grid");
    if (!grid) return;
    grid.innerHTML = "";
    const lineup = getActiveLineup();
    const all = window.__players || [];
    all.forEach(p => {
      const pid     = String(p.id);
      const inField = lineup.has(pid);
      const tile    = document.createElement("button");
      tile.className = "lineup-tile" + (inField ? " active" : "");
      tile.innerHTML  = (p.number ? `<span class="fp-num">#${p.number}</span>` : "")
                      + `<span class="fp-name">${p.name}</span>`;
      tile.addEventListener("click", () => {
        const lu = getActiveLineup();
        if (lu.has(pid)) {
          lu.delete(pid);
        } else if (lu.size < 7) {
          lu.add(pid);
        }
        tile.classList.toggle("active", lu.has(pid));
        updateLineupButton();
        const badge = section("lineup-count-badge");
        if (badge) badge.textContent = `${lu.size} / 7`;
        saveLineups();
      });
      grid.appendChild(tile);
    });
    updateLineupButton();
  }

  function openLineupPanel() {
    if (!window.__getCurrentSetId || !window.__getCurrentSetId()) {
      window.__showToast("Select a set first", "warn");
      return;
    }
    if (window.__isActiveSetFinished && window.__isActiveSetFinished()) {
      window.__showToast("This set is finished", "warn");
      return;
    }
    renderLineupPanel();
    section("lineup-panel")?.classList.remove("hidden");
  }

  function closeLineupPanel() {
    section("lineup-panel")?.classList.add("hidden");
    if (state === "type" || state === "idle") renderTypePicker();
  }

  function resetLineup() {
    // Called on set change — don't wipe lineup data; just reset the flow
    // and re-render so the new set's saved lineup (if any) is loaded.
    updateLineupButton();
    if (state !== "idle") resetFlow();
    else renderTypePicker();
  }

  function section(id) { return document.getElementById(id); }

  function showOnly(...ids) {
    ["flow-type-picker","flow-player-step","flow-actions-step","flow-outcome-step","flow-confirm"]
      .forEach(id => { const el = section(id); if (el) el.classList.add("hidden"); });
    ids.forEach(id => { const el = section(id); if (el) el.classList.remove("hidden"); });
    // Hide Back button at the start of a rally (nothing to go back to)
    const backBtn = section("flow-back-btn");
    if (backBtn) backBtn.classList.toggle("hidden", state === "type" || state === "idle");
    renderRallyTrail();
  }

  // Stat category accent colours (matches CSS class names)
  const STAT_COLOR = { serve:"blue", receive:"teal", attack:"orange", block:"purple", freeball:"cyan", fault:"rose" };

  function getPlayerLabel(pid) {
    if (pid === "opponent") return { name: window.__oppName || "Opponent", num: "" };
    const p = (window.__players || []).find(p => String(p.id) === String(pid));
    return p ? { name: p.name, num: p.number || "" } : { name: "?", num: "" };
  }

  function renderRallyTrail() {
    const el = section("flow-trail");
    if (!el) return;
    el.innerHTML = "";

    rallyBuf.forEach((action, idx) => {
      if (idx > 0) {
        const arrow = document.createElement("span");
        arrow.className = "trail-arrow";
        arrow.textContent = "\u2192";
        el.appendChild(arrow);
      }

      const cc    = getActionColor(action.stat, action.result, action.pid); // "fc-green"|"fc-red"|"fc-yellow"
      const color = cc.replace("fc-","") || "gray"; // "green"|"red"|"yellow"
      const { name, num } = getPlayerLabel(action.pid);
      const statColor = STAT_COLOR[action.stat] || "gray";

      const node = document.createElement("div");
      node.className = `trail-node trail-node-${color}`;
      node.innerHTML =
        `<div class="trail-player">${num ? `<span class="trail-num">#${num}</span>` : ""}<span class="trail-name">${name}</span></div>`+
        `<div class="trail-badges">`+
          `<span class="trail-stat-badge trail-stat-${statColor}">${action.stat}</span>`+
          `<span class="trail-result-badge trail-result-${color}">${action.result}</span>`+
        `</div>`;
      if (state === "confirm") {
        const capturedIdx = idx;
        node.classList.add("trail-node-confirm");
        if (editIdx === capturedIdx) node.classList.add("trail-node-editing");
        const delBtn = document.createElement("button");
        delBtn.className = "trail-del-btn";
        delBtn.textContent = "\u00D7";
        delBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          rallyBuf.splice(capturedIdx, 1);
          if (editIdx === capturedIdx) editIdx = null;
          else if (editIdx !== null && editIdx > capturedIdx) editIdx--;
          if (rallyBuf.length === 0) { resetFlow(); return; }
          renderRallyTrail(); renderConfirm();
        });
        node.appendChild(delBtn);
        node.addEventListener("click", () => {
          editIdx = (editIdx === capturedIdx) ? null : capturedIdx;
          renderRallyTrail(); renderConfirm();
        });
      }
      el.appendChild(node);
    });

    // Pending indicator while a step is in progress
    if (state !== "idle" && state !== "type" && state !== "confirm") {
      if (rallyBuf.length > 0) {
        const arrow = document.createElement("span");
        arrow.className = "trail-arrow trail-arrow-pending";
        arrow.textContent = "\u2192";
        el.appendChild(arrow);
      }
      const pending = document.createElement("div");
      pending.className = "trail-node trail-node-pending";
      pending.innerHTML = `<span class="trail-pending-label">${getStepShortLabel()}</span><span class="trail-pulse"></span>`;
      el.appendChild(pending);
    }

    // Scroll trail to end
    el.scrollLeft = el.scrollWidth;
  }

  function getStepShortLabel() {
    if (state==="serve-player"||state==="serve-outcome") return "Serve";
    if (state==="receive-player"||state==="receive-outcome") return "Receive";
    if (state==="loop-combined"||state==="loop-action"||state==="insert") return "...";
    if (state==="loop-player"||state==="insert-player") return loopStat||"-";
    if (state==="loop-outcome"||state==="insert-outcome") return loopStat||"-";
    return "...";
  }

  // Step header labels shown at top of each active card
  const STEP_HEADERS = {
    "type":           { n:1, label:"Start rally" },
    "serve-player":   { n:1, label:"Serve — who served?" },
    "serve-outcome":  { n:2, label:"Serve — result" },
    "receive-player": { n:1, label:"Receive — who received?" },
    "receive-outcome":{ n:2, label:"Receive — result" },
    "loop-combined":  { n:null, label:"What happened?" },
    "loop-action":    { n:null, label:"Next action" },
    "loop-player":    { n:null, label:"Who?" },
    "loop-outcome":   { n:null, label:"Result" },
    "confirm":        { n:null, label:"Review rally" },
  };

  function setActiveCardHeader(cardId, overrideLabel) {
    const h = STEP_HEADERS[state];
    const label = overrideLabel || (h ? h.label : "");
    const stepNum = h && h.n ? `<span class="step-pill step-${rallyBuf.length+1}">${rallyBuf.length+1}<\/span>` : "";
    const el = section(cardId)?.querySelector(".flow-step-label");
    if (el) el.innerHTML = `${stepNum}${label}`;
  }

  function renderTypePicker() {
    state = "type";
    const cont = section("flow-type-inner");
    if (!cont) return;
    cont.innerHTML = "";
    if (!window.__getCurrentSetId || !window.__getCurrentSetId()) {
      const msg = document.createElement("p");
      msg.className = "flow-lineup-prompt";
      msg.textContent = "Select a set from the set bar before starting a rally.";
      cont.appendChild(msg);
      showOnly("flow-type-picker");
      return;
    }
    if (window.__isActiveSetFinished && window.__isActiveSetFinished()) {
      const msg = document.createElement("p");
      msg.className = "flow-lineup-prompt";
      msg.textContent = "This set is finished. Select an active set to record stats.";
      cont.appendChild(msg);
      showOnly("flow-type-picker");
      return;
    }
    if (getActiveLineup().size === 0) {
      const msg = document.createElement("p");
      msg.className = "flow-lineup-prompt";
      msg.textContent = "Select the on-court players before starting a rally.";
      cont.appendChild(msg);
      const btn = document.createElement("button");
      btn.className = "flow-type-btn";
      btn.textContent = "\uD83D\uDC65 Set Lineup";
      btn.addEventListener("click", () => openLineupPanel());
      cont.appendChild(btn);
    } else {
      [["serve","\uD83C\uDFD0 Our Serve"],["receive","\uD83E\uDD1C Our Receive"]].forEach(([type, label]) => {
        const btn = document.createElement("button");
        btn.className = `flow-type-btn flow-type-${type}`;
        btn.textContent = label;
        btn.addEventListener("click", () => onTypeChosen(type));
        cont.appendChild(btn);
      });
    }
    showOnly("flow-type-picker");
  }

  function renderPlayerStep(stat, includeOpponent) {
    const cont = section("flow-player-inner");
    if (!cont) return;
    cont.innerHTML = "";
    setActiveCardHeader("flow-player-step", "Who? \u2014 " + stat);

    const lineupList = getLineupSorted(stat);
    const rallyCounts = {};
    rallyBuf.forEach(a => { if (a.stat === stat) rallyCounts[String(a.pid)] = (rallyCounts[String(a.pid)] || 0) + 1; });
    const sorted = [...lineupList].sort((a, b) => {
      const ca = rallyCounts[String(a.id)] || 0;
      const cb = rallyCounts[String(b.id)] || 0;
      // tiebreak preserves the freq-sorted order from getLineupSorted
      return cb - ca || lineupList.indexOf(a) - lineupList.indexOf(b);
    });

    // Quick-pick row: top 3 players shown larger (only when lineup > 3)
    const quickPick = sorted.length > 3 ? sorted.slice(0, 3) : [];
    const rest      = sorted.length > 3 ? sorted.slice(3)    : sorted;

    if (quickPick.length > 0) {
      const qpRow = document.createElement("div");
      qpRow.className = "flow-quickpick-row";
      quickPick.forEach(p => {
        const btn = document.createElement("button");
        btn.className = "flow-player-btn flow-player-qp";
        btn.innerHTML = (p.number ? `<span class="fp-num">#${p.number}</span>` : "") + `<span class="fp-name">${p.name}</span>`;
        btn.addEventListener("click", () => onPlayerChosen(String(p.id)));
        qpRow.appendChild(btn);
      });
      cont.appendChild(qpRow);
      if (rest.length > 0 || includeOpponent) {
        const div = document.createElement("div");
        div.className = "flow-player-divider";
        cont.appendChild(div);
      }
    }

    rest.forEach(p => {
      const btn = document.createElement("button");
      btn.className = "flow-player-btn";
      btn.innerHTML = (p.number ? `<span class="fp-num">#${p.number}</span>` : "") + `<span class="fp-name">${p.name}</span>`;
      btn.addEventListener("click", () => onPlayerChosen(String(p.id)));
      cont.appendChild(btn);
    });

    if (includeOpponent) {
      const btn = document.createElement("button");
      btn.className = "flow-player-btn flow-player-opp";
      btn.innerHTML = `<span class="fp-name">${window.__oppName || "Opponent"}</span>`;
      btn.addEventListener("click", () => onPlayerChosen("opponent"));
      cont.appendChild(btn);
    }
    showOnly("flow-player-step");
  }

  function renderActionPicker() {
    state = "loop-action";
    const cont = section("flow-actions-inner");
    if (!cont) return;
    cont.innerHTML = "";
    LOOP_ACTIONS.forEach(a => {
      const btn = document.createElement("button");
      btn.className = `flow-action-btn flow-action-${a.stat}`;
      btn.textContent = a.label;
      btn.addEventListener("click", () => onActionChosen(a.stat));
      cont.appendChild(btn);
    });
    setActiveCardHeader("flow-actions-step");
    showOnly("flow-actions-step");
  }

  function renderCombinedActionOutcome() {
    state = "loop-combined";
    const cont = section("flow-actions-inner");
    if (!cont) return;
    cont.innerHTML = "";
    LOOP_ACTIONS.forEach(a => {
      const row = document.createElement("div");
      row.className = "flow-combined-row";
      const lbl = document.createElement("span");
      lbl.className = `flow-combined-lbl flow-action-${a.stat}`;
      lbl.textContent = a.label;
      row.appendChild(lbl);
      const outcomes = LOOP_OUTCOMES[a.stat] || [];
      if (outcomes.length === 0) {
        const btn = document.createElement("button");
        btn.className = "flow-combined-btn flow-outcome-red";
        btn.textContent = "Fault";
        btn.addEventListener("click", () => {
          loopStat = a.stat;
          loopOutcomePending = { result: "fault", color: "red", loop: false };
          state = "loop-player";
          renderPlayerStep(loopStat, true);
        });
        row.appendChild(btn);
      } else {
        outcomes.forEach(o => {
          const btn = document.createElement("button");
          btn.className = `flow-combined-btn flow-outcome-${o.color}`;
          btn.textContent = o.label;
          btn.addEventListener("click", () => {
            loopStat = a.stat;
            loopOutcomePending = o;
            state = "loop-player";
            renderPlayerStep(loopStat, loopStat !== "serve");
          });
          row.appendChild(btn);
        });
      }
      cont.appendChild(row);
    });
    setActiveCardHeader("flow-actions-step");
    showOnly("flow-actions-step");
  }

  function renderOutcomeStep(outcomes) {
    const cont = section("flow-outcome-inner");
    if (!cont) return;
    cont.innerHTML = "";
    outcomes.forEach(o => {
      const btn = document.createElement("button");
      btn.className = `flow-outcome-btn flow-outcome-${o.color}`;
      btn.innerHTML = `<span class="fo-label">${o.label}<\/span><span class="fo-hint">${o.loop ? "continues" : o.color==="green" ? "our point" : "their point"}<\/span>`;
      btn.addEventListener("click", () => onOutcomeChosen(o));
      cont.appendChild(btn);
    });
    setActiveCardHeader("flow-outcome-step", "Result" + (loopStat ? " \u2014 " + loopStat : ""));
    showOnly("flow-outcome-step");
  }

  function onTypeChosen(type) {
    if (type === "serve") {
      state = "serve-player";
      renderPlayerStep("serve", false);
    } else {
      state = "receive-player";
      renderPlayerStep("receive", true);
    }
  }

  function onPlayerChosen(pid) {
    loopPid = pid;
    if (state === "serve-player") {
      state = "serve-outcome";
      renderOutcomeStep(SERVE_OUTCOMES);
    } else if (state === "receive-player") {
      state = "receive-outcome";
      renderOutcomeStep(pid === "opponent" ? RECEIVE_OPP_OUTCOMES : RECEIVE_OUR_OUTCOMES);
    } else if (state === "loop-player" || state === "insert-player") {
      if (loopOutcomePending !== null) {
        const outcome = loopOutcomePending;
        loopOutcomePending = null;
        commitAction(pid, loopStat, outcome.result);
        if (state === "insert-player") {
          finaliseInsert();
        } else if (outcome.loop) {
          loopStat = null; loopPid = null;
          renderCombinedActionOutcome();
        } else {
          autoSaveRally();
        }
      } else if (loopStat === "fault") {
        commitAction(pid, "fault", "fault");
        if (state === "insert-player") { finaliseInsert(); }
        else { autoSaveRally(); }
      } else {
        state = (state === "insert-player") ? "insert-outcome" : "loop-outcome";
        renderOutcomeStep(LOOP_OUTCOMES[loopStat] || []);
      }
    }
  }

  function onActionChosen(stat) {
    loopStat = stat;
    const isInsert = (state === "insert");
    state = isInsert ? "insert-player" : "loop-player";
    renderPlayerStep(stat, stat !== "serve");
  }

  function onOutcomeChosen(outcome) {
    const stat = (state === "serve-outcome")    ? "serve"
               : (state === "receive-outcome")  ? "receive"
               : loopStat;
    const isInsert = (state === "insert-outcome");

    if (isInsert) {
      const pos = window.__insertPos || 0;
      rallyBuf.splice(pos, 0, { pid: loopPid, stat, result: outcome.result });
      window.__insertPos = null;
      if (window.__bumpFreq && loopPid !== "opponent") window.__bumpFreq(loopPid, stat);
      goToConfirm();
      return;
    }

    commitAction(loopPid, stat, outcome.result);
    if (outcome.loop) {
      loopStat = null; loopPid = null;
      renderCombinedActionOutcome();
    } else {
      autoSaveRally();
    }
  }

  function commitAction(pid, stat, result) {
    rallyBuf.push({ pid, stat, result });
    if (pid !== "opponent" && window.__bumpFreq) window.__bumpFreq(pid, stat);
    renderRallyTrail();
  }

  function finaliseInsert() {
    goToConfirm();
  }

  function goToConfirm() {
    state = "confirm";
    editIdx = null;
    renderConfirm();
    showOnly("flow-confirm");
  }

  function getOutcomesFor(stat, pid) {
    if (stat === "serve")   return SERVE_OUTCOMES;
    if (stat === "receive") return pid === "opponent" ? RECEIVE_OPP_OUTCOMES : RECEIVE_OUR_OUTCOMES;
    return LOOP_OUTCOMES[stat] || [];
  }

  function getActionColor(stat, result, pid) {
    const outcomes = getOutcomesFor(stat, pid);
    const o = outcomes.find(x => x.result === result);
    return o ? "fc-" + o.color : "";
  }

  function renderConfirm() {
    const list = section("flow-confirm-list");
    if (!list) return;
    list.innerHTML = "";

    if (editIdx !== null && rallyBuf[editIdx]) {
      const capturedIdx = editIdx;
      const action = rallyBuf[capturedIdx];
      const { name, num } = getPlayerLabel(action.pid);

      const form = document.createElement("div");
      form.className = "fc-edit-inline";

      const heading = document.createElement("div");
      heading.className = "fc-edit-heading";
      heading.textContent = (num ? "#" + num + " " : "") + name + " \u00B7 " + action.stat;
      form.appendChild(heading);

      // Player picker row
      const pRow = document.createElement("div");
      pRow.className = "fc-edit-row";
      const pLabel = document.createElement("span");
      pLabel.className = "fc-edit-sub";
      pLabel.textContent = "Player:";
      pRow.appendChild(pLabel);
      const sorted = window.__freqSortedPlayers ? window.__freqSortedPlayers(action.stat) : (window.__players || []);
      sorted.forEach(p => {
        const pb = document.createElement("button");
        pb.className = "fc-edit-btn" + (String(p.id) === String(action.pid) ? " active" : "");
        pb.textContent = (p.number ? "#" + p.number + " " : "") + p.name;
        pb.addEventListener("click", () => { rallyBuf[capturedIdx].pid = String(p.id); editIdx = null; renderRallyTrail(); renderConfirm(); });
        pRow.appendChild(pb);
      });
      if (action.stat !== "serve") {
        const pb = document.createElement("button");
        pb.className = "fc-edit-btn" + (action.pid === "opponent" ? " active" : "");
        pb.textContent = window.__oppName || "Opponent";
        pb.addEventListener("click", () => { rallyBuf[capturedIdx].pid = "opponent"; editIdx = null; renderRallyTrail(); renderConfirm(); });
        pRow.appendChild(pb);
      }
      form.appendChild(pRow);

      // Outcome picker row
      const oRow = document.createElement("div");
      oRow.className = "fc-edit-row";
      const oLabel = document.createElement("span");
      oLabel.className = "fc-edit-sub";
      oLabel.textContent = "Result:";
      oRow.appendChild(oLabel);
      getOutcomesFor(action.stat, action.pid).forEach(o => {
        const ob = document.createElement("button");
        ob.className = "fc-edit-btn fc-edit-" + o.color + (o.result === action.result ? " active" : "");
        ob.textContent = o.label;
        ob.addEventListener("click", () => { rallyBuf[capturedIdx].result = o.result; editIdx = null; renderRallyTrail(); renderConfirm(); });
        oRow.appendChild(ob);
      });
      form.appendChild(oRow);

      const doneBtn = document.createElement("button");
      doneBtn.className = "fc-edit-btn fc-edit-done";
      doneBtn.textContent = "Done";
      doneBtn.addEventListener("click", () => { editIdx = null; renderRallyTrail(); renderConfirm(); });
      form.appendChild(doneBtn);
      list.appendChild(form);
    } else {
      const hint = document.createElement("p");
      hint.className = "fc-tap-hint";
      hint.textContent = "Tap any step above to edit or delete it.";
      list.appendChild(hint);

      const addBtn = document.createElement("button");
      addBtn.className = "fc-insert-btn";
      addBtn.textContent = "+ add action";
      addBtn.addEventListener("click", () => startInsert(rallyBuf.length));
      list.appendChild(addBtn);
    }
  }

  function startInsert(pos) {
    window.__insertPos = pos;
    state = "insert";
    const cont = section("flow-actions-inner");
    if (!cont) return;
    cont.innerHTML = "";
    const allActions = [
      { label: "Serve",    stat: "serve"    },
      { label: "Receive",  stat: "receive"  },
      ...LOOP_ACTIONS
    ];
    allActions.forEach(a => {
      const btn = document.createElement("button");
      btn.className = `flow-action-btn flow-action-${a.stat}`;
      btn.textContent = a.label;
      btn.addEventListener("click", () => onActionChosen(a.stat));
      cont.appendChild(btn);
    });
    showOnly("flow-actions-step");
  }

  async function saveRally() {
    if (rallyBuf.length === 0) { resetFlow(); return; }
    for (const action of rallyBuf) {
      if (window.__apiRecord) await window.__apiRecord(action.pid, action.stat, action.result);
      const cur = window.__getCount ? window.__getCount(action.pid, action.stat, action.result) : 0;
      if (window.__setCount) window.__setCount(action.pid, action.stat, action.result, cur + 1);
      if (window.__incrTotal) window.__incrTotal();
    }
    if (window.__updateEventCount) window.__updateEventCount();
    window.__showToast(`Rally saved (${rallyBuf.length} action${rallyBuf.length!==1?"s":""})`, "ok");
    resetFlow();
  }

  // Auto-save rally (happy path) — saves immediately then shows undo toast
  async function autoSaveRally() {
    if (rallyBuf.length === 0) { resetFlow(); return; }
    // Dismiss any previous undo window; can't undo once a new save starts
    section("flow-undo-toast")?.classList.add("hidden");
    clearTimeout(undoToastTimer);
    lastSavedBuf = [...rallyBuf];
    for (const action of lastSavedBuf) {
      if (window.__apiRecord) await window.__apiRecord(action.pid, action.stat, action.result);
      const cur = window.__getCount ? window.__getCount(action.pid, action.stat, action.result) : 0;
      if (window.__setCount) window.__setCount(action.pid, action.stat, action.result, cur + 1);
      if (window.__incrTotal) window.__incrTotal();
    }
    if (window.__updateEventCount) window.__updateEventCount();
    const count = lastSavedBuf.length;
    resetFlow();
    showUndoToast(count);
  }

  function showUndoToast(count) {
    const toast = section("flow-undo-toast");
    const msg   = section("flow-undo-msg");
    if (!toast || !msg) return;
    msg.textContent = `\u2713 Rally saved (${count} action${count !== 1 ? "s" : ""})`;
    toast.classList.remove("hidden");
    clearTimeout(undoToastTimer);
    undoToastTimer = setTimeout(() => {
      toast.classList.add("hidden");
      lastSavedBuf = [];
    }, 10000);
  }

  async function undoLastAutoSave() {
    if (lastSavedBuf.length === 0) return;
    section("flow-undo-toast")?.classList.add("hidden");
    clearTimeout(undoToastTimer);
    const toUndo = [...lastSavedBuf];
    lastSavedBuf = [];
    for (const action of toUndo) {
      if (window.__apiDecrement) await window.__apiDecrement(action.pid, action.stat, action.result);
      const cur = window.__getCount ? window.__getCount(action.pid, action.stat, action.result) : 0;
      if (window.__setCount) window.__setCount(action.pid, action.stat, action.result, Math.max(0, cur - 1));
      if (window.__decrTotal) window.__decrTotal();
    }
    if (window.__updateEventCount) window.__updateEventCount();
    rallyBuf = [...toUndo];
    goToConfirm();
  }

  function discardRally() {
    window.__showToast("Rally discarded", "warn");
    resetFlow();
  }

  function resetFlow() {
    rallyBuf = []; state = "idle"; loopStat = null; loopPid = null; loopOutcomePending = null; editIdx = null;
    const bc = section("flow-trail");
    if (bc) bc.innerHTML = "";
    renderTypePicker();
  }

  function goBack() {
    if (state === "confirm") {
      if (rallyBuf.length === 0) { resetFlow(); return; }
      rallyBuf.pop();
      if (rallyBuf.length === 0) { resetFlow(); return; }
      loopStat = null; loopPid = null;
      renderCombinedActionOutcome();
      return;
    }
    if (["serve-outcome","serve-player","receive-outcome","receive-player","type"].includes(state)) { resetFlow(); return; }
    if (state === "loop-outcome" || state === "insert-outcome") {
      state = state === "insert-outcome" ? "insert-player" : "loop-player";
      renderPlayerStep(loopStat, loopStat !== "serve"); return;
    }
    if (state === "loop-player" || state === "insert-player") {
      if (loopOutcomePending !== null) { loopOutcomePending = null; }
      if (state === "insert-player") { state = "insert"; renderActionPicker(); return; }
      renderCombinedActionOutcome(); return;
    }
    if (state === "loop-combined") {
      if (rallyBuf.length > 0) rallyBuf.pop();
      if (rallyBuf.length === 0) { resetFlow(); return; }
      renderCombinedActionOutcome(); return;
    }
    if (state === "loop-action") {
      if (rallyBuf.length > 0) rallyBuf.pop();
      if (rallyBuf.length === 0) { resetFlow(); return; }
      renderActionPicker(); return;
    }
    if (state === "insert") { goToConfirm(); return; }
    resetFlow();
  }

  function init(gameId) {
    lsKey = gameId ? `lineup-${gameId}` : null;
    loadLineups();
    section("flow-back-btn")?.addEventListener("click", goBack);
    section("flow-save-btn")?.addEventListener("click", saveRally);
    section("flow-discard-btn")?.addEventListener("click", discardRally);
    section("flow-undo-btn")?.addEventListener("click", undoLastAutoSave);
    section("lineup-btn")?.addEventListener("click", () => openLineupPanel());
    section("lineup-done-btn")?.addEventListener("click", () => closeLineupPanel());
    renderTypePicker();
  }

  return { init, resetFlow, resetLineup };
})();

