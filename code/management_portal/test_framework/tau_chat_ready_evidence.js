'use strict';

// Read the current full native Chat root in one DOM snapshot. The original
// action owns bounded waits and must persist each snapshot before asserting it.
// No text search through messages, requests, clicks, timers or implicit retries.
async function readResidentChatReady(region, { residentId }) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '')) throw new Error('Expected exact Resident identity');
  const candidates = await region.evaluateAll((roots, residentId) => roots.map(root => {
    const visible = e => !!e?.isConnected && !e.closest('[hidden],[inert],[aria-hidden="true"]')
      && e.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    const heads = [...root.querySelectorAll(':scope > .chatHead')];
    const bodies = [...root.querySelectorAll(':scope > .chatWorkspaceBody')];
    const composers = [...root.querySelectorAll(':scope > .composer')];
    const loadingNodes = [...root.querySelectorAll(':scope > [data-workspace-loading]')];
    const headers = heads.flatMap(head => [...head.querySelectorAll('[data-workspace-status] [data-workspace-identity="resident"]')])
      .map(e => ({ resident_id: e.getAttribute('data-workspace-resident-id'), copy_value: e.getAttribute('data-workspace-copy-value'),
        text: e.textContent, visible: visible(e) }));
    const inputs = composers.flatMap(composer => [...composer.querySelectorAll('.composerInput[role="textbox"]')])
      .map(e => ({ id: e.id, label: e.getAttribute('aria-label'), visible: visible(e),
        editable: e.isContentEditable && e.getAttribute('aria-disabled') !== 'true' && e.getAttribute('aria-readonly') !== 'true' }));
    const loading = { node_count: loadingNodes.length, aria_busy: root.getAttribute('aria-busy'),
      class_active: root.classList.contains('is-workspace-loading'), visible: loadingNodes.some(visible) };
    loading.active = loading.aria_busy === 'true' || loading.class_active || loading.visible;
    const role = root.getAttribute('role');
    const checks = {
      root: visible(root) && ['region', 'tabpanel'].includes(role) && heads.length === 1 && bodies.length === 1 && composers.length === 1,
      identity: headers.length === 1 && headers[0].visible && headers[0].resident_id === residentId
        && headers[0].copy_value === residentId && headers[0].text === residentId,
      loading_ended: loading.node_count === 1 && !loading.active,
      composer: inputs.length === 1 && inputs[0].visible && inputs[0].editable,
    };
    return { dom_id: root.id, role, connected: root.isConnected, visible: visible(root), headers, inputs, loading, checks,
      ready: Object.values(checks).every(Boolean) };
  }), residentId);
  return { resident_id: residentId, region_count: candidates.length, candidates,
    ready: candidates.length === 1 && candidates[0].ready };
}

module.exports = { readResidentChatReady };
