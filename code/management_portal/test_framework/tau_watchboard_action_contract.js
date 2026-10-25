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

module.exports = {matchesWatchboardRemoval, readWorkbenchPanelBinding};
