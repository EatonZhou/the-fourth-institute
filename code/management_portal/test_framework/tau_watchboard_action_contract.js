'use strict';

// Match only the original removal of the exact graph captured before the action.
// This is a predicate over observed arguments; it neither submits nor retries.
function matchesWatchboardRemoval(args, {lineageId, residentId, componentIds, wireIds} = {}) {
  if (!/^lineage_[a-zA-Z0-9]+$/.test(lineageId || '') || !/^resident_[a-zA-Z0-9]+$/.test(residentId || '')
      || !Array.isArray(componentIds) || !componentIds.length || !Array.isArray(wireIds)
      || [...componentIds, ...wireIds].some(id => typeof id !== 'string' || !id)
      || new Set(componentIds).size !== componentIds.length || new Set(wireIds).size !== wireIds.length) {
    throw new TypeError('Exact original graph identity and component/wire IDs required');
  }
  if (args?.lineage_id !== lineageId || args.near_resident_id !== residentId || !Array.isArray(args.operations)) return false;
  const wanted = new Set([...componentIds.map(id => 'component:' + id), ...wireIds.map(id => 'wire:' + id)]);
  if (args.operations.length !== wanted.size) return false;
  for (const op of args.operations) {
    if (op?.op !== 'delete' || !wanted.delete(op.kind + ':' + op.id)) return false;
  }
  return wanted.size === 0;
}

// Read the descriptor from the actual native frame. A DOM root ID is not a tab ID.
// The tab strip can legitimately be concealed; visibility/hit checks belong to
// readWorkbenchTabClose after the caller's normal focus/hover reveal.
async function readWorkbenchPanelBinding(panel, {domId} = {}) {
  if (typeof domId !== 'string' || !domId) throw new TypeError('Exact original DOM root ID required');
  if (await panel.count() !== 1) throw new Error('One original panel root required');
  return panel.evaluate((node, domId) => {
    if (node.id !== domId) throw new Error('Wrong original panel DOM identity');
    const frame = node.closest('.portalWorkbenchPanelFrame[data-portal-workbench-panel-frame]');
    const body = frame?.parentElement, stack = body?.parentElement;
    if (!frame || !body?.matches('.portalWorkbenchStackBody') || !stack?.matches('.portalWorkbenchStack[data-portal-workbench-stack]')) {
      throw new Error('Original panel is not bound to a native workbench frame');
    }
    const descriptorId = frame.dataset.portalWorkbenchPanelFrame;
    if (!descriptorId) throw new Error('Missing native descriptor ID');
    const selector = '[role="tab"][data-portal-workbench-tab="' + CSS.escape(descriptorId) + '"]';
    const tabs = stack.querySelectorAll(':scope > .portalWorkbenchTabs[role="tablist"] > .portalWorkbenchTabSlot > ' + selector);
    const frames = body.querySelectorAll(':scope > [data-portal-workbench-panel-frame="' + CSS.escape(descriptorId) + '"]');
    if (tabs.length !== 1 || frames.length !== 1 || frames[0] !== frame) throw new Error('Ambiguous native panel/tab binding');
    const visible = node.isConnected && !node.closest('[hidden],[inert],[aria-hidden="true"]')
      && node.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    const selected = tabs[0].getAttribute('aria-selected') === 'true';
    return {dom_id: domId, descriptor_id: descriptorId, stack_id: stack.dataset.portalWorkbenchStack,
      tab_selector: selector, visible, selected, ready: visible && selected};
  }, domId);
}

// Observe the original finite response before inspecting its requested graph.
// Return failure details for the caller to save before asserting readiness.
async function readWatchboardInstallationReceipt(response, observer, {lineageId, residentId} = {}) {
  if (!/^lineage_[a-zA-Z0-9]+$/.test(lineageId || '') || !/^resident_[a-zA-Z0-9]+$/.test(residentId || '')) {
    throw new TypeError('Exact original Lineage and Resident required');
  }
  const original = await observer.original(response);
  const request = response.request().postDataJSON();
  const args = request?.arguments || {}, body = original.body || {}, result = body.result || {};
  const issues = [];
  if (request?.instruction !== 'ApplyLineageChanges' || args.lineage_id !== lineageId || args.near_resident_id !== residentId) issues.push('original_target_mismatch');
  if (!original.request_id || original.body_status !== 'read' || original.http_status !== 200 || body.status !== 'committed' || body.replayed !== false || !body.instruction_id) issues.push('original_commit_missing');
  const operations = Array.isArray(args.operations) ? args.operations : [];
  const created = Array.isArray(result.created_ids) ? result.created_ids : [];
  const graph = operations.map(op => ({op: op?.op ?? null, kind: op?.kind ?? null, id: op?.value?.id ?? null,
    ...(op?.kind === 'component' ? {client_key: op.value?.client_key ?? null} : {}),
    ...(op?.kind === 'wire' ? {source: op.value?.source ?? null, target: op.value?.target ?? null} : {})}));
  if (!graph.length || graph.some(op => op.op !== 'create' || !['component','wire'].includes(op.kind) || typeof op.id !== 'string' || !op.id)
      || !graph.some(op => op.kind === 'component') || !graph.some(op => op.kind === 'wire')
      || new Set(graph.map(op => op.id)).size !== graph.length) issues.push('invalid_original_create_operations');
  const requestedIds = graph.map(op => op.id), wanted = new Set(requestedIds);
  const missing = requestedIds.filter(id => !created.includes(id)), unexpected = created.filter(id => !wanted.has(id));
  if (!Array.isArray(result.created_ids) || created.some(id => typeof id !== 'string' || !id)
      || new Set(created).size !== created.length || missing.length || unexpected.length) issues.push('created_ids_mismatch');
  if (!Array.isArray(result.updated_ids) || result.updated_ids.length || !Array.isArray(result.deleted_ids) || result.deleted_ids.length) issues.push('unexpected_original_changes');
  const components = new Set(graph.filter(op => op.kind === 'component').map(op => op.id));
  const prefix = 'watchboard.' + residentId + '.';
  if (graph.some(op => op.kind === 'component' && (typeof op.client_key !== 'string' || !op.client_key.startsWith(prefix)))
      || graph.some(op => op.kind === 'wire' && (!components.has(op.source) || !components.has(op.target)))) issues.push('graph_ownership_mismatch');
  return {request_id: original.request_id, http_status: original.http_status, body_status: original.body_status,
    instruction_id: body.instruction_id ?? null, status: body.status ?? null, replayed: body.replayed ?? null,
    lineage_id: args.lineage_id ?? null, near_resident_id: args.near_resident_id ?? null,
    operations: graph, created_ids: Array.isArray(result.created_ids) ? result.created_ids : null,
    updated_ids: Array.isArray(result.updated_ids) ? result.updated_ids : null,
    deleted_ids: Array.isArray(result.deleted_ids) ? result.deleted_ids : null,
    missing_ids: missing, unexpected_ids: unexpected, issues, ready: issues.length === 0};
}

module.exports = {matchesWatchboardRemoval, readWorkbenchPanelBinding, readWatchboardInstallationReceipt};
