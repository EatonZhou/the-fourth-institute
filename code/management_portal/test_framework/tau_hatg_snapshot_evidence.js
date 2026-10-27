'use strict';

// One diagnostic read of the original, caller-bound HATG panel. Save the whole
// result before asserting captured. No navigation, mutations, recovery or polls;
// a snapshot is not a business receipt or a replacement for original readiness.
async function readHatgControlsSnapshot(panel, {lineageId, snapshotTimeoutMs = 1000} = {}) {
  if (!/^lineage_[a-z0-9]+$/.test(lineageId || '')) throw new TypeError('Exact lineageId is required');
  if (!Number.isFinite(snapshotTimeoutMs) || snapshotTimeoutMs <= 0 || snapshotTimeoutMs > 10000) {
    throw new TypeError('snapshotTimeoutMs must be finite and in (0, 10000]');
  }
  const result = {expected_lineage_id: lineageId, captured: false, candidates: [], before: null,
    snapshot: null, after: null, same_root: null, error: null};
  let handles = [], phase = 'candidates';
  // Serialized as a whole to the original ElementHandle, including its helpers.
  const inspect = root => {
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
    const describe = n => ({tag: n.tagName, id: n.id, role: n.getAttribute('role'), visible: visible(n),
      busy: n.getAttribute('aria-busy'), label: n.getAttribute('aria-label')});
    const identities = Array.from(root.querySelectorAll('#activeLineageIdentity [data-portal-resource-id]'))
      .map(n => ({id: n.getAttribute('data-portal-resource-id'), text: n.textContent, visible: visible(n)}));
    const nativeRoots = Array.from(root.querySelectorAll('main#agentLineageShell'));
    const panes = Array.from(root.querySelectorAll('#hatgPane'));
    return {
      document_url: location.origin + location.pathname, root: describe(root), connected: root.isConnected,
      native: root.matches('.portalHatgPanelComponent[role="region"]') && nativeRoots.length === 1 && panes.length === 1,
      native_roots: nativeRoots.map(describe), panes: panes.map(describe), identities,
      mains: Array.from(document.querySelectorAll('main')).map(describe),
      dialogs: Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]')).map(describe),
      controls: Array.from(root.querySelectorAll('button, input, select, textarea')).map(n => ({
        ...describe(n), disabled: n.matches(':disabled'), aria_disabled: n.getAttribute('aria-disabled'),
        title: n.getAttribute('title'), text: n.tagName === 'BUTTON' ? n.innerText : null,
      })),
    };
  };
  const valid = evidence => evidence.native && evidence.connected && evidence.root.visible &&
    evidence.root.busy !== 'true' && evidence.native_roots[0].visible && evidence.panes[0].visible &&
    evidence.identities.length === 1 && evidence.identities[0].visible &&
    evidence.identities[0].id === lineageId && evidence.identities[0].text === lineageId;
  try {
    handles = await panel.elementHandles();
    for (const handle of handles) result.candidates.push(await handle.evaluate(inspect));
    phase = 'identity';
    if (handles.length !== 1 || !valid(result.candidates[0])) {
      throw new Error('Expected one visible native HATG panel with the exact Lineage identity');
    }
    result.before = result.candidates[0];
    phase = 'aria_snapshot';
    result.snapshot = await panel.ariaSnapshot({timeout: snapshotTimeoutMs});
    phase = 'identity_after_snapshot';
    result.after = await handles[0].evaluate(inspect);
    result.same_root = await panel.evaluateAll((nodes, original) => nodes.length === 1 && nodes[0] === original, handles[0]);
    if (!result.same_root || !valid(result.after)) throw new Error('HATG root or identity changed during snapshot');
    if (typeof result.snapshot !== 'string' || !result.snapshot.trim()) throw new Error('HATG accessibility snapshot is empty');
    result.captured = true;
  } catch (error) {
    result.error = {phase, name: error.name, message: error.message, stack: error.stack || null};
  } finally {
    for (const handle of handles) await handle.dispose().catch(() => {});
  }
  return result;
}

module.exports = {readHatgControlsSnapshot};
