'use strict';

// Return all visible native Editors for this exact header identity. The caller
// must keep multiple Editors distinct; this never chooses one or opens a Chat.
function visibleResidentChatRoots(page, {residentId}) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '')) throw new Error('Expected exact Resident identity');
  return page.locator('.portalChatWorkspaceRoot:visible').filter({
    has: page.locator(`.chatHead [data-workspace-identity="resident"][data-workspace-resident-id="${residentId}"]`),
  });
}

// Read the current full native Chat root in one DOM snapshot. The original
// action owns bounded waits and must persist each snapshot before asserting it.
// Only native Portal deletion feedback is inspected; no message text is returned.
// No requests, clicks, timers or implicit retries.
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
    const sessions = heads.flatMap(head => [...head.querySelectorAll('[data-workspace-status] [data-workspace-identity="session"]')])
      .map(e => ({ session_id: e.getAttribute('data-workspace-session-id'), copy_value: e.getAttribute('data-workspace-copy-value'),
        text: e.textContent, visible: visible(e) }));
    const inputs = composers.flatMap(composer => [...composer.querySelectorAll('.composerInput[role="textbox"]')])
      .map(e => ({ id: e.id, label: e.getAttribute('aria-label'), visible: visible(e),
        editable: e.isContentEditable && e.getAttribute('aria-disabled') !== 'true' && e.getAttribute('aria-readonly') !== 'true' }));
    const sendButtons = composers.flatMap(composer => [...composer.querySelectorAll('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')]);
    const quotes = composers.flatMap(composer => [...composer.querySelectorAll('[data-composer-quote-host] > [data-composer-quote][role="group"]')])
      .map(node => ({message_id:node.getAttribute('data-composer-quote'),visible:visible(node),
        preview_visible:visible(node.querySelector('.composerQuoteText')),
        cancel_enabled:[...node.querySelectorAll('[data-composer-quote-cancel]')]
          .filter(button=>visible(button)&&!button.disabled).length===1}));
    const deletedMessageVisible = bodies.some(body => [...body.querySelectorAll('.messageItem.portal .messageContent')]
      .some(node => visible(node) && /该 Resident 已删除或不存在|This Resident has been deleted/i.test(node.textContent || '')));
    const loading = { node_count: loadingNodes.length, aria_busy: root.getAttribute('aria-busy'),
      class_active: root.classList.contains('is-workspace-loading'), visible: loadingNodes.some(visible) };
    loading.active = loading.aria_busy === 'true' || loading.class_active || loading.visible;
    const role = root.getAttribute('role');
    const checks = {
      root: visible(root) && root.classList.contains('portalChatWorkspaceRoot')
        && heads.length === 1 && bodies.length === 1 && composers.length === 1,
      identity: headers.length === 1 && headers[0].visible && headers[0].resident_id === residentId
        && headers[0].copy_value === residentId && headers[0].text === residentId,
      loading_ended: loading.node_count === 1 && !loading.active,
      composer: inputs.length === 1 && inputs[0].visible && inputs[0].editable,
    };
    const deletionFeedbackReady = checks.root && checks.identity && checks.loading_ended
      && inputs.length === 1 && inputs[0].visible && !inputs[0].editable
      && sendButtons.length === 1 && visible(sendButtons[0]) && sendButtons[0].disabled
      && deletedMessageVisible;
    return { dom_id: root.id, role, connected: root.isConnected, visible: visible(root), headers, sessions, inputs, quotes, loading, checks,
      deletion_feedback_ready: deletionFeedbackReady,
      ready: Object.values(checks).every(Boolean) };
  }), residentId);
  const ready = candidates.length === 1 && candidates[0].ready;
  const sessions = ready ? candidates[0].sessions : [];
  // Session IDs are opaque and may be abbreviated in visible text. Keep the
  // native full copy identity only when it belongs to this unique ready Chat.
  // A new Resident legitimately has no Session yet; that does not fail ready.
  const sessionId = sessions.length === 1 && sessions[0].visible && sessions[0].session_id
    && sessions[0].session_id === sessions[0].copy_value ? sessions[0].session_id : null;
  return { resident_id: residentId, region_count: candidates.length, candidates,
    ready, session_id: sessionId, session_identity_ready: sessionId !== null,
    deletion_feedback_ready: candidates.length === 1 && candidates[0].deletion_feedback_ready };
}

async function readResidentComposerQuote(region, {residentId, messageId}) {
  if (typeof messageId !== 'string' || !messageId.trim()) throw new TypeError('Exact original quoted message ID required');
  const chat = await readResidentChatReady(region,{residentId});
  const quotes = chat.ready ? chat.candidates[0].quotes : [];
  return {observed_at_ms:Date.now(),resident_id:residentId,message_id:messageId,chat,quotes,
    ready:chat.ready && quotes.length===1 && quotes[0].message_id===messageId
      && quotes[0].visible && quotes[0].preview_visible && quotes[0].cancel_enabled};
}

module.exports = { readResidentChatReady, visibleResidentChatRoots, readResidentComposerQuote };
