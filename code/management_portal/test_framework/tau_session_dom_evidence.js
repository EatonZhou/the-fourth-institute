'use strict';

// Read-only observations for the ORIGINAL session action. They neither click
// nor wait nor claim a committed delete. The caller records the source graph,
// target identity and request/document identity before its one native action.
async function readSessionDeleteDialog(dialog, {title}) {
  if (!title || await dialog.count() !== 1) throw new Error('Expected one exact session delete dialog and target title');
  return dialog.evaluate((root, expectedTitle) => {
    const doc = root.ownerDocument;
    const visible = node => !!node && node.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
    if (root.getAttribute('role') !== 'alertdialog') throw new Error('Expected the native HATG alertdialog');
    const name = (root.getAttribute('aria-label') || (root.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => doc.getElementById(id)?.textContent || '').join(' ')).trim();
    const paragraphs = [...root.querySelectorAll('p')].map(node => node.textContent.trim());
    const target = paragraphs.some(text => text.startsWith(`Delete "${expectedTitle}" from HATG?`)
      || text.startsWith(`从 HATG 中删除「${expectedTitle}」？`));
    const retained = paragraphs.some(text => text.includes('conversation records and run traces are preserved')
      || text.includes('对话记录与运行轨迹保留'));
    const confirm = [...root.querySelectorAll('button')].filter(button =>
      ['Delete node', '删除节点'].includes((button.getAttribute('aria-label') || button.textContent).trim()) && visible(button));
    const top = [...doc.querySelectorAll('[role="alertdialog"]')].filter(visible);
    const enabled = confirm.length === 1 && !confirm[0].disabled && confirm[0].getAttribute('aria-disabled') !== 'true';
    return {dialog_name: name, target_title_matches: target, retention_notice: retained,
      visible: visible(root), unique_visible_alertdialog: top.length === 1 && top[0] === root,
      confirm_count: confirm.length, confirm_enabled: enabled,
      ready: visible(root) && top.length === 1 && top[0] === root
        && ['Delete HATG node?', '删除 HATG 节点？'].includes(name) && target && retained && enabled};
  }, title);
}

async function readSessionGraphReady(panel, searchResults, {residentId}) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '') || await panel.count() !== 1 || await searchResults.count() !== 1)
    throw new Error('Expected one original graph, its exact search results and Resident identity');
  const results = await searchResults.elementHandle();
  try {
    return await panel.evaluate((root, {results, residentId}) => {
      const visible = node => !!node && node.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
      if (root.ownerDocument !== results.ownerDocument || !root.isConnected || !results.isConnected)
        throw new Error('Graph/search document changed');
      if (!results.matches('.graphNodeSearchResultList')) throw new Error('Expected original graph search results');
      const cards = [...root.querySelectorAll('[data-node-id]')].filter(node => node.getAttribute('data-node-id') === residentId && visible(node));
      const hits = [...results.querySelectorAll('[data-graph-search-node]')].filter(node => node.getAttribute('data-graph-search-node') === residentId && visible(node));
      // Local matches may appear before asynchronous search settles. An
      // apparently successful match must never hide its still-visible status.
      const statuses = [...results.querySelectorAll('.graphSearchEmpty')].filter(visible);
      const busy = root.matches('[aria-busy="true"]') || [...root.querySelectorAll('[aria-busy="true"]')].some(visible)
        || results.matches('[aria-busy="true"]') || [...results.querySelectorAll('[aria-busy="true"]')].some(visible);
      return {resident_id: residentId, panel_visible: visible(root), results_visible: visible(results),
        visible_node_count: cards.length, exact_search_hit_count: hits.length,
        unresolved_search_status_count: statuses.length, busy,
        ready: visible(root) && visible(results) && cards.length === 1 && hits.length === 1 && !statuses.length && !busy};
    }, {results, residentId});
  } finally { await results?.dispose(); }
}

// The native Menu exposes the full opaque Session value even when Chat has no
// Session header. The caller opens/closes the discovered menu in the original
// action, persists this snapshot, and compares successive IDs before mutation.
async function readResidentMenuSession(menu, {residentId}) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '')) throw new TypeError('Exact Resident identity is required');
  const candidates = await menu.evaluateAll((roots, residentId) => roots.map(root => {
    const visible = node => !!node?.isConnected && !node.closest('[hidden],[inert],[aria-hidden="true"]')
      && node.checkVisibility({checkOpacity:true, checkVisibilityCSS:true});
    const identities = [...root.querySelectorAll('#nodeResidentIdentityReadout')]
      .map(n => ({text:n.textContent.trim(), visible:visible(n)}));
    const values = [...root.querySelectorAll('#nodeMenuSessionId')]
      .map(n => ({text:n.textContent.trim(), visible:visible(n)}));
    const buttons = [...root.querySelectorAll('#nodeMenuCopySession')]
      .map(n => ({visible:visible(n), enabled:!n.disabled && n.getAttribute('aria-disabled') !== 'true'}));
    const ready = root.matches('[role="dialog"].nodeSettingsSurface') && visible(root)
      && identities.length === 1 && identities[0].visible
      && identities[0].text === residentId && values.length === 1 && values[0].visible && !!values[0].text
      && buttons.length === 1 && buttons[0].visible && buttons[0].enabled;
    return {dom_id:root.id, identities, values, buttons, ready};
  }), residentId);
  const ready = candidates.length === 1 && candidates[0].ready;
  return {resident_id:residentId, candidates, ready, session_id:ready ? candidates[0].values[0].text : null};
}

module.exports = {readSessionDeleteDialog, readSessionGraphReady, readResidentMenuSession};
