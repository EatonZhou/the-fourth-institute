'use strict';

// Read the current native Lineage catalog. Surface readiness and a
// network response are separate observations; either may supply report material.
async function readLineageCatalogSurface(panel, {panelId = 'navigation'} = {}) {
  if (typeof panelId !== 'string' || !panelId.trim()) throw new TypeError('Exact panelId required');
  const candidates = await panel.evaluateAll((roots, expectedId) => {
    const visible = node => {
      if (!node?.isConnected) return false;
      for (let n = node; n; n = n.parentElement) {
        const style = getComputedStyle(n);
        if (n.hidden || n.inert || n.getAttribute('aria-hidden') === 'true' || style.display === 'none' ||
            ['hidden', 'collapse'].includes(style.visibility) || Number(style.opacity) === 0) return false;
      }
      const rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    const state = n => ({id: n.id, visible: visible(n), busy: n.getAttribute('aria-busy')});
    return roots.map(root => {
      const views = Array.from(root.querySelectorAll(':scope > [data-side-panel]')).map(n => ({
        ...state(n), view: n.getAttribute('data-side-panel'), active: n.classList.contains('active'),
      }));
      const active = views.filter(n => n.active);
      const lists = Array.from(root.querySelectorAll(':scope > [data-side-panel="agents"] #agentList')).map(list => ({
        ...state(list), rendered: Array.from(list.querySelectorAll(':scope > .agentList')).map(state),
        // These are the catalog's native status rows, not arbitrary titles or
        // asynchronous Which Resident/Chat/Preview content elsewhere.
        states: Array.from(list.querySelectorAll(':scope > .portalAsyncState')).map(n => ({
          ...state(n), state: n.getAttribute('data-state'), role: n.getAttribute('role'), text: n.textContent,
        })),
      }));
      const controls = ['refreshBtn', 'filterToggleBtn'].map(id => ({id,
        candidates: Array.from(root.querySelectorAll(':scope > [data-side-panel="agents"] button[id="' + id + '"]'))
          .map(n => ({...state(n), label: n.getAttribute('aria-label'), disabled: n.matches(':disabled'),
            aria_disabled: n.getAttribute('aria-disabled')})),
      }));
      const search = Array.from(root.querySelectorAll(':scope > [data-side-panel="agents"] input#searchInput[type="search"]'))
        .map(n => ({...state(n), disabled: n.matches(':disabled'), read_only: n.readOnly,
          aria_disabled: n.getAttribute('aria-disabled')}));
      const native = root.matches('.portalNavigation[role="region"][data-portal-panel-package="navigation"]') &&
        root.getAttribute('data-portal-workbench-panel') === expectedId;
      const listReady = lists.length === 1 && lists[0].visible && lists[0].busy !== 'true' &&
        lists[0].rendered.length === 1 && lists[0].rendered[0].visible &&
        !lists[0].states.some(n => n.visible);
      const controlsReady = controls.every(c => c.candidates.length === 1 && c.candidates[0].visible &&
        !c.candidates[0].disabled && c.candidates[0].aria_disabled !== 'true');
      const searchReady = search.length === 1 && search[0].visible && !search[0].disabled &&
        !search[0].read_only && search[0].aria_disabled !== 'true';
      return {root: state(root), panel_id: root.getAttribute('data-portal-workbench-panel'),
        package: root.getAttribute('data-portal-panel-package'), role: root.getAttribute('role'), native,
        document_path: location.pathname, views, lists, controls, search,
        surface_ready: location.pathname === '/lineage' && native && visible(root) && root.getAttribute('aria-busy') !== 'true' &&
          active.length === 1 && active[0].view === 'agents' && active[0].visible && active[0].busy !== 'true' &&
          listReady && controlsReady && searchReady};
    });
  }, panelId);
  return {surface_ready: candidates.length === 1 && candidates[0].surface_ready, expected_panel_id: panelId,
    candidates, scope: 'navigation_surface_only; original catalog response and target controls still required'};
}

module.exports = {readLineageCatalogSurface};
