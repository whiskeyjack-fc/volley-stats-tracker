// filter-multiselect.js
// Excel-style multi-select checkbox dropdown for .filter-bar forms.
// Self-initializing on DOMContentLoaded — no manual init call needed.
// Pairs with the filter_multiselect() macro in templates/_macros.html.
//
// Behavior:
//   - Trigger button opens/closes a checkbox panel (.ms-panel).
//   - Opening snapshots the current checked state so "Annuleren" (Cancel)
//     or an outside click can revert unapplied changes.
//   - "Alles selecteren" master checkbox reflects/drives an indeterminate
//     tri-state based on the individual option checkboxes.
//   - ".ms-search" filters visible ".ms-option" rows by text (client-side).
//   - "Toepassen" (Apply): if every option in the group is checked, all
//     its checkboxes are disabled right before submit so the field's
//     query param is omitted entirely (clean "no filter" URL). Then the
//     enclosing form is submitted.

(function () {
  function closePanel(panel) {
    panel.hidden = true;
  }

  function openPanel(panel) {
    // snapshot current checked state for Cancel / outside-click revert
    const boxes = panel.querySelectorAll('.ms-options input[type=checkbox]');
    boxes.forEach((b) => { b.dataset.msSnapshot = b.checked ? '1' : ''; });
    panel.hidden = false;
    syncSelectAll(panel);
    const search = panel.querySelector('.ms-search');
    if (search) { search.value = ''; filterOptions(panel, ''); }
  }

  function revertSnapshot(panel) {
    const boxes = panel.querySelectorAll('.ms-options input[type=checkbox]');
    boxes.forEach((b) => { b.checked = b.dataset.msSnapshot === '1'; });
  }

  function syncSelectAll(panel) {
    const master = panel.querySelector('.ms-selectall-input');
    if (!master) return;
    const boxes = Array.from(panel.querySelectorAll('.ms-options input[type=checkbox]'));
    const checkedCount = boxes.filter((b) => b.checked).length;
    master.checked = boxes.length > 0 && checkedCount === boxes.length;
    master.indeterminate = checkedCount > 0 && checkedCount < boxes.length;
  }

  function filterOptions(panel, text) {
    const needle = text.trim().toLowerCase();
    panel.querySelectorAll('.ms-options .ms-option').forEach((label) => {
      const match = !needle || label.textContent.trim().toLowerCase().includes(needle);
      label.style.display = match ? '' : 'none';
    });
  }

  function applyGroup(panel) {
    // If a search term narrowed the list, options hidden by the search are
    // no longer an intentional choice — uncheck them so Apply only keeps
    // the values the user actually searched for/left checked. Without this,
    // a group that started "fully checked" (no filter) stays fully checked
    // after typing a search term (since hidden boxes are still checked),
    // so Apply would submit no filter at all instead of the narrowed set.
    const search = panel.querySelector('.ms-search');
    if (search && search.value.trim()) {
      panel.querySelectorAll('.ms-options .ms-option').forEach((label) => {
        if (label.style.display === 'none') {
          const box = label.querySelector('input[type=checkbox]');
          if (box) box.checked = false;
        }
      });
      syncSelectAll(panel);
    }
    closePanel(panel);
    const form = panel.closest('form');
    if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
  }

  function cleanupFullyCheckedGroups(form) {
    // Any group left fully checked means "no filter" for that field, so its
    // checkboxes must be disabled to omit the param entirely from the query
    // string. Runs on every submit path (Apply, Enter in a text input, or
    // the no-JS fallback Filter button) — not just the group whose Apply
    // button was clicked — otherwise an untouched-but-fully-checked group
    // (e.g. Lid/profile_id) would submit an explicit IN(...) list that
    // wrongly excludes rows where that column is NULL (e.g. unassigned items).
    form.querySelectorAll('.ms-panel').forEach((p) => {
      const boxes = p.querySelectorAll('.ms-options input[type=checkbox]');
      const checked = Array.from(boxes).filter((b) => b.checked);
      if (boxes.length > 0 && checked.length === boxes.length) {
        boxes.forEach((b) => { b.disabled = true; });
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.ms-select').forEach((wrapper) => {
      const trigger = wrapper.querySelector('.ms-trigger');
      const panel   = wrapper.querySelector('.ms-panel');
      const master  = wrapper.querySelector('.ms-selectall-input');
      const search  = wrapper.querySelector('.ms-search');
      const cancel  = wrapper.querySelector('.ms-cancel');
      const apply   = wrapper.querySelector('.ms-apply');
      if (!trigger || !panel) return;

      const form = wrapper.closest('form');
      if (form && !form.dataset.msCleanupBound) {
        form.dataset.msCleanupBound = '1';
        form.addEventListener('submit', function () { cleanupFullyCheckedGroups(form); });
      }

      trigger.addEventListener('click', function (e) {
        e.stopPropagation();
        const isOpen = !panel.hidden;
        document.querySelectorAll('.ms-panel').forEach((p) => { if (p !== panel) closePanel(p); });
        if (isOpen) closePanel(panel); else openPanel(panel);
      });

      panel.addEventListener('click', function (e) { e.stopPropagation(); });

      if (master) {
        master.addEventListener('change', function () {
          panel.querySelectorAll('.ms-options input[type=checkbox]').forEach((b) => {
            b.checked = master.checked;
          });
        });
      }

      panel.querySelectorAll('.ms-options input[type=checkbox]').forEach((b) => {
        b.addEventListener('change', function () { syncSelectAll(panel); });
      });

      if (search) {
        search.addEventListener('input', function () { filterOptions(panel, search.value); });
        search.addEventListener('keydown', function (e) {
          // Enter inside the search box would otherwise submit the whole
          // .filter-bar form directly (default browser behavior), bypassing
          // applyGroup()'s "uncheck options hidden by the search" step —
          // that made Enter look like it reset the filter instead of
          // applying the narrowed search. Route it through Apply instead.
          if (e.key === 'Enter') {
            e.preventDefault();
            applyGroup(panel);
          }
        });
      }

      if (cancel) {
        cancel.addEventListener('click', function () {
          revertSnapshot(panel);
          closePanel(panel);
        });
      }

      if (apply) {
        apply.addEventListener('click', function () { applyGroup(panel); });
      }
    });

    document.addEventListener('click', function () {
      document.querySelectorAll('.ms-panel').forEach((panel) => {
        if (!panel.hidden) {
          revertSnapshot(panel);
          closePanel(panel);
        }
      });
    });
  });
})();
