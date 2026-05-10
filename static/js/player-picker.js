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
//   refreshSelects()        — rebuild all dropdowns filtering already-chosen profiles
//   _makePlayerRow(id)      — create a <tr> with a profile <select>, optionally pre-selected

const TEAM_ROLES = [
  {v: 'player',          l: 'Player'},
  {v: 'head_coach',      l: 'Head Coach'},
  {v: 'assistant_coach', l: 'Assistant Coach'},
  {v: 'team_manager',    l: 'Team Manager'},
  {v: 'medical',         l: 'Medical'},
  {v: 'scorer',          l: 'Scorer'},
  {v: 'marker',          l: 'Marker'},
  {v: 'video_analyst',   l: 'Video Analyst'},
];

let _allProfiles = [];
let _showRoles   = false;

function _esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _getSelectedIds(excludeSel) {
  const ids = new Set();
  document.querySelectorAll('#players-body select[name="player_profile_id"]').forEach(sel => {
    if (sel !== excludeSel && sel.value) ids.add(parseInt(sel.value));
  });
  return ids;
}

function refreshSelects() {
  document.querySelectorAll('#players-body select[name="player_profile_id"]').forEach(sel => {
    const myVal = sel.value ? parseInt(sel.value) : (sel.dataset.pending ? parseInt(sel.dataset.pending) : null);
    const others = _getSelectedIds(sel);
    let html = '<option value="">\u2014 select player \u2014</option>';
    for (const pr of _allProfiles) {
      if (!others.has(pr.id)) {
        const label = (pr.number ? '#' + _esc(pr.number) + ' ' : '') + _esc(pr.first_name) + ' ' + _esc(pr.last_name);
        html += '<option value="' + pr.id + '"' + (pr.id === myVal ? ' selected' : '') + '>' + label + '</option>';
      }
    }
    sel.innerHTML = html;
    // once options are rendered the value is live — clear the pending hint
    delete sel.dataset.pending;
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
  const profileCell = '<td><select name="player_profile_id" class="player-select">' +
    '<option value="">\u2014 select member \u2014</option>' +
    '</select></td>';
  const rolesCell = _showRoles ? _buildRolesCell(selectedRoles) : '';
  row.innerHTML = profileCell + rolesCell +
    '<td><button type="button" class="btn btn-sm btn-danger remove-row">\u2715</button></td>';
  if (selectedId) {
    row.querySelector('select[name="player_profile_id"]').dataset.pending = selectedId;
  }
  return row;
}

function initPlayerPicker(profiles, opts) {
  _allProfiles = profiles || [];
  opts = Object.assign({ showClear: true, clearMsg: 'Remove all members?', noDupCheck: false, showRoles: false }, opts || {});
  _showRoles = opts.showRoles;

  document.getElementById('add-player').addEventListener('click', () => {
    document.getElementById('players-body').appendChild(_makePlayerRow(null));
    refreshSelects();
  });

  document.getElementById('players-body').addEventListener('click', (e) => {
    const rolesBtn = e.target.closest('.roles-btn');
    if (rolesBtn) {
      const panel = rolesBtn.closest('td').querySelector('.roles-panel');
      const isOpen = panel.style.display !== 'none';
      document.querySelectorAll('#players-body .roles-panel').forEach(p => { p.style.display = 'none'; });
      if (!isOpen) panel.style.display = 'flex';
      e.stopPropagation();
      return;
    }
    if (e.target.classList.contains('remove-row')) {
      const rows = document.querySelectorAll('#players-body tr');
      if (rows.length > 1) {
        e.target.closest('tr').remove();
        refreshSelects();
      }
    }
  });

  document.addEventListener('click', () => {
    document.querySelectorAll('#players-body .roles-panel').forEach(p => { p.style.display = 'none'; });
  });

  document.getElementById('players-body').addEventListener('change', (e) => {
    if (e.target.name === 'player_profile_id') refreshSelects();
    const rolesPanel = e.target.closest('.roles-panel');
    if (rolesPanel) _syncRolesBtn(rolesPanel);
  });

  if (opts.showClear) {
    const clearBtn = document.getElementById('clear-players');
    if (clearBtn) clearBtn.addEventListener('click', () => {
      if (!confirm(opts.clearMsg)) return;
      const tbody = document.getElementById('players-body');
      tbody.innerHTML = '';
      tbody.appendChild(_makePlayerRow(null));
      refreshSelects();
    });
  }

  if (!opts.noDupCheck) {
    document.querySelector('form').addEventListener('submit', (e) => {
      const selects = document.querySelectorAll('#players-body select[name="player_profile_id"]');
      const seen = new Set();
      for (const sel of selects) {
        const val = sel.value;
        if (!val) continue;
        if (seen.has(val)) {
          e.preventDefault();
          sel.focus();
          alert('Duplicate member: "' + sel.options[sel.selectedIndex].text.trim() + '". Each member can only be added once.');
          return;
        }
        seen.add(val);
      }
    });
  }

  refreshSelects();
}
