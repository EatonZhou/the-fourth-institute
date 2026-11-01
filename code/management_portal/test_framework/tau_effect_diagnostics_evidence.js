'use strict';

// One observation of the current native Inspector. The original action owns
// the deadline and records every sample; this does not fetch or save a script.
async function readEffectDiagnosticsReady(inspector, {componentId}) {
  if (typeof componentId !== 'string' || !componentId.trim()) throw new TypeError('Expected an exact Effect ID');
  const candidates = await inspector.evaluateAll((panels, expected) => panels.map(panel => {
    const visible = node => !!node?.isConnected && !node.closest('[hidden],[inert]')
      && node.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    const slots = [...panel.querySelectorAll('[data-effect-diagnostics]')];
    const slot = slots.length === 1 ? slots[0] : null;
    const state = slot?.getAttribute('data-effect-diagnostics-state') ?? null;
    const identity = panel.getAttribute('data-component-id');
    return {component_id:identity, state, slot_count:slots.length, visible:visible(panel)&&visible(slot),
      ready:identity===expected&&visible(panel)&&visible(slot)&&['ok','warning'].includes(state)};
  }), componentId);
  return {observed_at_ms:Date.now(), expected_component_id:componentId, candidates,
    ready:candidates.length===1&&candidates[0].ready};
}

module.exports = {readEffectDiagnosticsReady};
