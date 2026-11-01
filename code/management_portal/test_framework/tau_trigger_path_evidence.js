'use strict';

// Observe the native association text from the original Trigger Path. No fetch,
// click, expansion or business-state inference is performed here.
async function readTriggerPathExecution(inspector, {triggerPathId, stepId, executionId, residentId, status}) {
  const expected = {triggerPathId, stepId, executionId, residentId, status};
  if (Object.values(expected).some(value => typeof value !== 'string' || !value.trim()))
    throw new TypeError('Exact original Trigger Path, step, execution, Resident and status are required');
  const candidates = await inspector.locator('[data-trigger-path-observation]').evaluateAll((sections, ids) => {
    const visible = node => !!node?.isConnected && !node.closest('[hidden],[inert]')
      && node.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    return sections.map(section => {
      const containers = [...section.querySelectorAll('[data-trigger-path-executions]')];
      const container = containers.length === 1 ? containers[0] : null;
      const details = container?.closest('details');
      const rows = container ? [...container.querySelectorAll('p')].map(row => ({text:row.textContent.trim(),visible:visible(row)})) : [];
      const link = `${ids.stepId} → Execution ${ids.executionId}`;
      const execution = `${ids.executionId} · ${ids.residentId} · ${ids.status}`;
      const identity = section.dataset.triggerPathId;
      const expanded = !!details?.open;
      return {trigger_path_id:identity, expanded, rows, ready:identity===ids.triggerPathId
        && visible(section) && visible(container) && expanded
        && rows.filter(row=>row.visible && row.text===link).length===1
        && rows.filter(row=>row.visible && row.text===execution).length===1};
    });
  }, expected);
  return {observed_at_ms:Date.now(), expected, candidates, ready:candidates.length===1 && candidates[0].ready};
}

module.exports = {readTriggerPathExecution};
