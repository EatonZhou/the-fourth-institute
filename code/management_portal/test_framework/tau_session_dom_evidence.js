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

module.exports = {readSessionDeleteDialog, readSessionGraphReady};
