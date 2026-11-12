'use strict';

// Passive snapshots for the original action window. No clicks, requests,
// retries, historical repair, business-terminal or visual-pass inference.
async function readSessionLineageIdentity(panel, {lineageId}) {
  if (!/^lineage_[a-z0-9]+$/.test(lineageId || '')) throw new Error('Expected fixed Lineage identity');
  const count = await panel.count();
  if (count === 0) return {panel_present:false, identity_ready:false};
  if (count !== 1) throw new Error('Expected one active HATG panel');
  return panel.evaluate((root, lineageId) => {
    if (!root.matches('.portalHatgPanelComponent[role="region"], #lineage-hatg-panel[role="region"], section#hatgPane[data-lineage-pane="hatg"][aria-label]'))
      throw new Error('Expected the full native HATG region');
    const visible = n => !!n?.isConnected && !n.closest('[hidden],[inert],[aria-hidden="true"]')
      && n.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    // The resource header can expose the same identity through several native
    // controls. Distinct values, rather than DOM copies, define ambiguity.
    const ids = [...new Set([...root.querySelectorAll('#activeLineageIdentity [data-portal-resource-id]')].filter(visible)
      .map(n => n.getAttribute('data-portal-resource-id')))];
    const buttons = [...root.querySelectorAll('button[id="addNodeBtn"]')].filter(visible);
    const createEnabled = buttons.length === 1 && !buttons[0].disabled && buttons[0].getAttribute('aria-disabled') !== 'true';
    const mount = root.closest('.portalHatgPanelComponent') || root;
    const mountLoading = mount.getAttribute('aria-busy') === 'true' || mount.inert;
    const mountError = mount.getAttribute('data-hatg-error') || null;
    return {panel_present:true, panel_visible:visible(root), lineage_id:lineageId,
      displayed_lineage_ids:ids, create_enabled:createEnabled, mount_loading:mountLoading, mount_error:mountError,
      identity_ready:visible(root) && !mountLoading && !mountError && ids.length === 1 && ids[0] === lineageId && createEnabled};
  }, lineageId);
}

async function readSessionMessageCards(region, {residentId, messageId}) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '') || !/^msg_[a-z0-9]+$/.test(messageId || ''))
    throw new Error('Expected exact original Resident and accepted message identities');
  const count = await region.count();
  if (count === 0) return {region_present: false, ready: false};
  if (count !== 1) throw new Error('Expected one original Chat region');
  return region.evaluate((root, {residentId, messageId}) => {
    if (!root.matches('[role="region"], section.portalChatWorkspaceRoot'))
      throw new Error('Expected the full original Chat region');
    const visible = node => !!node?.isConnected && !node.closest('[hidden],[inert],[aria-hidden="true"]')
      && node.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
    const bound = [...new Set([...root.querySelectorAll('[data-workspace-resident-id]')]
      .filter(visible).map(n => n.getAttribute('data-workspace-resident-id')))];
    const cards = [...root.querySelectorAll('article.messageItem')].filter(n => visible(n)
      && (n.getAttribute('data-message-key') === messageId || n.getAttribute('data-message-instance') === messageId));
    const targetMatches = bound.length === 1 && bound[0] === residentId;
    return {region_present: true, region_visible: visible(root), resident_id: residentId,
      target_matches: targetMatches, message_id: messageId, visible_card_count: cards.length,
      // Count nodes, never add their two identity attributes together.
      cards: cards.map(n => ({key: n.getAttribute('data-message-key'), instance: n.getAttribute('data-message-instance')})),
      ready: visible(root) && targetMatches && cards.length === 1};
  }, {residentId, messageId});
}

async function readSessionGraphSnapshot(panel, {residentId, minWidth = 160, minHeight = 64}) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '') || !Number.isFinite(minWidth)
      || !Number.isFinite(minHeight) || minWidth <= 0 || minHeight <= 0)
    throw new Error('Expected original Resident and positive capture dimensions');
  const count = await panel.count();
  if (count === 0) return {panel_present: false, search_ready: false, search_empty_ready: false, capture_candidate: false};
  if (count !== 1) throw new Error('Expected one active HATG panel');
  return panel.evaluate((root, {residentId, minWidth, minHeight}) => {
    if (!root.matches('.portalHatgPanelComponent[role="region"], #lineage-hatg-panel[role="region"], section#hatgPane[data-lineage-pane="hatg"][aria-label]'))
      throw new Error('Expected the full native HATG region');
    const doc = root.ownerDocument;
    const visible = n => !!n?.isConnected && !n.closest('[hidden],[inert],[aria-hidden="true"]')
      && n.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
    const inputs = [...root.querySelectorAll('input[id="graphNodeSearchInput"]')].filter(visible);
    if (inputs.length > 1) throw new Error('Ambiguous current search input');
    const input = inputs[0];
    const controls = (input?.getAttribute('aria-controls') || '').trim().split(/\s+/).filter(Boolean);
    if (controls.length > 1) throw new Error('Ambiguous original search control binding');
    // The native popover is portalled to BODY. Resolve through the current
    // input in this document, never through root descendants or a guessed first match.
    const popovers = controls.length ? [...doc.querySelectorAll('[id]')].filter(n => n.id === controls[0]) : [];
    if (popovers.length > 1) throw new Error('Ambiguous original search popover identity');
    const popover = popovers[0];
    if (popover && !popover.matches('.graphNodeSearchResults')) throw new Error('Wrong search popover kind');
    const lists = popover ? [...popover.querySelectorAll(':scope > .graphNodeSearchResultList')] : [];
    if (lists.length > 1) throw new Error('Ambiguous original search result list');
    const list = lists[0];
    const results = list ? [...list.querySelectorAll('[data-graph-search-node]')].filter(visible) : [];
    const hits = results.filter(n => n.getAttribute('data-graph-search-node') === residentId);
    const statuses = list ? [...list.querySelectorAll('.graphSearchEmpty')].filter(visible) : [];
    const statusTexts = statuses.map(n => n.textContent.trim());
    const isEmptyResult = text => text === '没有匹配节点' || text === 'No matching nodes';
    const unresolvedStatuses = statusTexts.filter(text => !isEmptyResult(text));
    const busy = [root, input, popover, list].filter(Boolean).some(n =>
      n.matches('[aria-busy="true"]') || [...n.querySelectorAll('[aria-busy="true"]')].some(visible));
    const cards = [...root.querySelectorAll('[data-node-id]')]
      .filter(n => n.getAttribute('data-node-id') === residentId && visible(n));
    const card = cards.length === 1 ? cards[0] : null;
    const box = card?.getBoundingClientRect();
    const geometry = box ? {x:box.x, y:box.y, width:box.width, height:box.height} : null;
    const viewport = doc.defaultView;
    const withinViewport = !!box && box.left >= 0 && box.top >= 0
      && box.right <= viewport.innerWidth && box.bottom <= viewport.innerHeight;
    const graphVisible = visible(root);
    const searchReady = graphVisible && visible(input) && input.value.trim() === residentId
      && visible(popover) && visible(list) && hits.length === 1 && !statuses.length && !busy && cards.length === 1;
    const searchEmptyReady = graphVisible && visible(input) && input.value.trim() === residentId
      && visible(popover) && visible(list) && !results.length && !cards.length && !busy
      && statusTexts.length === 1 && isEmptyResult(statusTexts[0]);
    return {panel_present:true, panel_visible:graphVisible, panel_id:root.id, resident_id:residentId,
      search_input_id:input?.id || '', search_popover_id:popover?.id || '',
      portalled_to_body:popover?.parentElement === doc.body, exact_hit_count:hits.length,
      search_status_texts:statusTexts, unresolved_status_count:unresolvedStatuses.length,
      busy, visible_node_count:cards.length, search_ready:searchReady, search_empty_ready:searchEmptyReady,
      geometry, layout_position:card ? {left:card.style.left, top:card.style.top} : null,
      // This is the exact visible Resident card's work state. It says nothing
      // about running tools, provider activity or business input completion.
      work_state:card?.getAttribute('data-status') || null,
      within_viewport:withinViewport, minimum_capture_size:{width:minWidth,height:minHeight},
      // A size sanity check only. Caller still captures and actually views
      // the original image, checks clipping/overlap and keeps exact graph identity.
      capture_candidate:graphVisible && !!box && box.width >= minWidth && box.height >= minHeight && withinViewport};
  }, {residentId, minWidth, minHeight});
}

module.exports = {readSessionLineageIdentity, readSessionMessageCards, readSessionGraphSnapshot};
