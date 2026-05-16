// ── Player Profile Picker ─────────────────────────────────────────────────
// Shared utility for pages that let users pick players from player_profiles.
//
// Usage:
//   initPlayerPicker(allProfilesJson, options)
//
// Options:
//   showClear    {bool}   — show "Delete all players" button (default true)
//   clearMsg     {string} — confirm message for clear button
//   noDupCheck   {bool}   — skip the built-in submit duplicate guard (default false)
//   showRoles    {bool}   — show Roles column per row (default false)
//
// Exposes globals used by page-specific JS after init:
//   refreshSelects()        — rebuild open dropdowns filtering already-chosen profiles
//   _makePlayerRow(id)      — create a <tr> with a combo-box, optionally pre-selected

const TEAM_ROLES = [
  {v: 'player',          l: 'Speler'},
  {v: 'head_coach',      l: 'Hoofdcoach'},
  {v: 'assistant_coach', l: 'Assistent-coach'},
  {v: 'team_manager',    l: 'Teammanager'},
  {v: 'medical',         l: 'Medisch'},
  {v: 'marker',          l: 'Markeerder'},
  {v: 'video_analyst',   l: 'Video-analist'},
];

let _allProfiles = [];
let _showRoles   = false;

function _esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _profileLabel(pr) {
  return (pr.number ? '#' + pr.number + ' ' : '') + pr.first_name + ' ' + pr.last_name;
}

function _getSelectedIds(excludeCombo) {
  const ids = new Set();
  document.querySelectorAll('#players-body .player-combo').forEach(combo => {
    if (combo !== excludeCombo) {
      const val = combo.querySelector('input[name="player_profile_id"]').value;
      if (val) ids.add(parseInt(val));
    }
  });
  return ids;
}

function _filteredProfiles(combo, query) {
  const others = _getSelectedIds(combo);
  const q = (query || '').trim().toLowerCase();
  return _allProfiles.filter(pr => {
    if (others.has(pr.id)) return false;
    if (!q) return true;
    return _profileLabel(pr).toLowerCase().includes(q);
  });
}

function _renderDropdown(combo) {
  const input    = combo.querySelector('.player-search-input');
  const ul       = combo.querySelector('.player-dropdown');
  const profiles = _filteredProfiles(combo, input.value);
  ul.innerHTML = '';
  if (!profiles.length) {
    const li = document.createElement('li');
    li.className   = 'player-dd-empty';
    li.textContent = 'Geen resultaten';
    ul.appendChild(li);
  } else {
    profiles.forEach(pr => {
      const li = document.createElement('li');
      li.dataset.id    = pr.id;
      li.dataset.label = _profileLabel(pr);
      li.textContent   = _profileLabel(pr);
      ul.appendChild(li);
    });
  }
  ul.style.display = 'block';
}

function _closeAllDropdowns(except) {
  document.querySelectorAll('#players-body .player-dropdown').forEach(ul => {
    if (ul.closest('.player-combo') !== except) ul.style.display = 'none';
  });
}

function _pickProfile(combo, id, label) {
  combo.querySelector('input[name="player_profile_id"]').value = id;
  combo.querySelector('.player-search-input').value            = label;
  combo.querySelector('.player-dropdown').style.display        = 'none';
  refreshSelects();
}

function refreshSelects() {
  // Rebuild any currently-open dropdowns so they exclude newly-chosen profiles
  document.querySelectorAll('#players-body .player-dropdown').forEach(ul => {
    if (ul.style.display !== 'none') _renderDropdown(ul.closest('.player-combo'));
  });
}

function _rolesLabel(vals) {
  return vals.map(v => { const r = TEAM_ROLES.find(r => r.v === v); return r ? r.l : v; }).join(', ');
}

function _syncRolesBtn(panel) {
  const checked = [...panel.querySelectorAll('input[type="checkbox"]:checked')].map(cb => cb.value);
  const vals = checked.length ? checked : ['player'];
  const hidden = panel.closest('td').querySelector('input[name="player_roles"]');
  if (hidden) hidden.value = vals.join(',');
  const btn = panel.closest('td').querySelector('.roles-btn');
  if (btn) btn.textContent = _rolesLabel(vals) + ' \u25be';
}

function _buildRolesCell(selectedRoles) {
  selectedRoles = (selectedRoles && selectedRoles.length) ? selectedRoles : ['player'];
  const label = _rolesLabel(selectedRoles);
  const checks = TEAM_ROLES.map(r =>
    '<label class="roles-check-label"><input type="checkbox" value="' + _esc(r.v) + '"' +
    (selectedRoles.includes(r.v) ? ' checked' : '') +
    ' style="accent-color:var(--accent)"> ' + _esc(r.l) + '</label>'
  ).join('');
  return '<td class="roles-td">' +
    '<button type="button" class="roles-btn">' + _esc(label) + ' \u25be</button>' +
    '<div class="roles-panel" style="display:none">' + checks + '</div>' +
    '<input type="hidden" name="player_roles" value="' + _esc(selectedRoles.join(',')) + '"></td>';
}

function _makePlayerRow(selectedId, selectedRoles) {
  const row = document.createElement('tr');
  let label = '';
  if (selectedId) {
    const pr = _allProfiles.find(p => p.id === selectedId);
    if (pr) label = _profileLabel(pr);
  }
  const profileCell =
    '<td><div class="player-combo">' +
    '<input type="text" class="player-search-input" autocomplete="off" ' +
    'placeholder="\u2014 selecteer lid \u2014" value="' + _esc(label) + '">' +
    '<ul class="player-dropdown" style="display:none"></ul>' +
    '<input type="hidden" name="player_profile_id" value="' + _esc(selectedId || '') + '">' +
    '</div></td>';
  const rolesCell = _showRoles ? _buildRolesCell(selectedRoles) : '';
  row.innerHTML = profileCell + rolesCell +
    '<td><button type="button" class="btn btn-sm btn-danger remove-row">\u2715</button></td>';
  return row;
}

function _injectStyles() {
  if (document.getElementById('player-picker-styles')) return;
  const style = document.createElement('style');
  style.id = 'player-picker-styles';
  style.textContent = `
.player-combo { position: relative; }
.player-search-input {
  width: 100%;
  box-sizing: border-box;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--fg);
  padding: .35rem .5rem;
  font-size: 0.9rem;
}
.player-search-input:focus {
  outline: none;
  border-color: var(--accent);
}
.player-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: 300;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-top: none;
  border-radius: 0 0 var(--radius) var(--radius);
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 220px;
  overflow-y: auto;
}
.player-dropdown li {
  padding: .35rem .5rem;
  cursor: pointer;
  font-size: 0.9rem;
  color: var(--fg);
}
.player-dropdown li:hover,
.player-dropdown li.active {
  background: var(--accent);
  color: #fff;
}
.player-dd-empty {
  padding: .35rem .5rem;
  font-size: 0.85rem;
  color: var(--muted);
  cursor: default;
}
`;
  document.head.appendChild(style);
}

function initPlayerPicker(profiles, opts) {
  if (!document.getElementById('players-body')) return;
  _allProfiles = profiles || [];
  opts = Object.assign({ showClear: true, clearMsg: 'Remove all members?', noDupCheck: false, showRoles: false }, opts || {});
  _showRoles = opts.showRoles;
  _injectStyles();

  const body = document.getElementById('players-body');

  // ── add row
  document.getElementById('add-player').addEventListener('click', () => {
    body.appendChild(_makePlayerRow(null));
  });

  // ── delegated click events on tbody
  body.addEventListener('click', e => {
    // remove row
    if (e.target.classList.contains('remove-row')) {
      if (body.querySelectorAll('tr').length > 1) {
        e.target.closest('tr').remove();
        refreshSelects();
      }
      return;
    }
    // roles button toggle
    const rolesBtn = e.target.closest('.roles-btn');
    if (rolesBtn) {
      const panel = rolesBtn.closest('td').querySelector('.roles-panel');
      const isOpen = panel.style.display !== 'none';
      body.querySelectorAll('.roles-panel').forEach(p => { p.style.display = 'none'; });
      if (!isOpen) panel.style.display = 'flex';
      e.stopPropagation();
      return;
    }
    // combo item selection
    const li = e.target.closest('.player-dropdown li[data-id]');
    if (li) {
      _pickProfile(li.closest('.player-combo'), parseInt(li.dataset.id), li.dataset.label);
      return;
    }
    // click on search input — open dropdown
    if (e.target.classList.contains('player-search-input')) {
      const combo = e.target.closest('.player-combo');
      _closeAllDropdowns(combo);
      _renderDropdown(combo);
    }
  });

  // ── typing in search input filters dropdown
  body.addEventListener('input', e => {
    if (!e.target.classList.contains('player-search-input')) return;
    const combo  = e.target.closest('.player-combo');
    const hidden = combo.querySelector('input[name="player_profile_id"]');
    hidden.value = '';   // clear selection when user edits text
    _renderDropdown(combo);
  });

  // ── keyboard navigation
  body.addEventListener('keydown', e => {
    if (!e.target.classList.contains('player-search-input')) return;
    const combo = e.target.closest('.player-combo');
    const ul    = combo.querySelector('.player-dropdown');
    const items = [...ul.querySelectorAll('li[data-id]')];
    const active = ul.querySelector('li.active');

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (ul.style.display === 'none') _renderDropdown(combo);
      const idx  = active ? items.indexOf(active) : -1;
      const next = items[(idx + 1) % items.length];
      if (next) {
        active && active.classList.remove('active');
        next.classList.add('active');
        next.scrollIntoView({ block: 'nearest' });
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const idx  = active ? items.indexOf(active) : items.length;
      const prev = items[(idx - 1 + items.length) % items.length];
      if (prev) {
        active && active.classList.remove('active');
        prev.classList.add('active');
        prev.scrollIntoView({ block: 'nearest' });
      }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (active) _pickProfile(combo, parseInt(active.dataset.id), active.dataset.label);
      else ul.style.display = 'none';
    } else if (e.key === 'Escape') {
      ul.style.display = 'none';
      e.target.blur();
    }
  });

  // ── close dropdowns and roles panels on outside click
  document.addEventListener('click', e => {
    if (!e.target.closest('#players-body .player-combo')) {
      _closeAllDropdowns(null);
    }
    if (!e.target.closest('#players-body .roles-panel') && !e.target.closest('#players-body .roles-btn')) {
      body.querySelectorAll('.roles-panel').forEach(p => { p.style.display = 'none'; });
    }
  });

  // ── roles checkbox changes
  body.addEventListener('change', e => {
    const rolesPanel = e.target.closest('.roles-panel');
    if (rolesPanel) _syncRolesBtn(rolesPanel);
  });

  // ── clear all players
  if (opts.showClear) {
    const clearBtn = document.getElementById('clear-players');
    if (clearBtn) clearBtn.addEventListener('click', () => {
      if (!confirm(opts.clearMsg)) return;
      body.innerHTML = '';
      body.appendChild(_makePlayerRow(null));
    });
  }

  // ── submit duplicate guard
  if (!opts.noDupCheck) {
    document.querySelector('form').addEventListener('submit', e => {
      const seen = new Set();
      for (const combo of body.querySelectorAll('.player-combo')) {
        const val = combo.querySelector('input[name="player_profile_id"]').value;
        if (!val) continue;
        if (seen.has(val)) {
          e.preventDefault();
          const label = combo.querySelector('.player-search-input').value;
          combo.querySelector('.player-search-input').focus();
          alert('Dubbel lid: "' + label.trim() + '". Elk lid kan slechts één keer worden toegevoegd.');
          return;
        }
        seen.add(val);
      }
    });
  }
}