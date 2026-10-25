'use strict';

// Call on the exact visible native Chat textbox before the one original Send.
// Do not use workspace.payload(): it can reconcile drafts and attachments.
async function readWatchboardComposer(input, {text} = {}) {
  if (typeof text !== 'string' || !text.trim()) throw new TypeError('Expected original nonempty text');
  if (await input.count() !== 1) throw new Error('One original Chat textbox required');
  return input.evaluate((node, text) => {
    if (node.getAttribute('role') !== 'textbox' || !node.isContentEditable) throw new Error('Expected native editable textbox');
    const serialize = window.PortalChatWorkspace?.serializeComposerInput;
    if (typeof serialize !== 'function') throw new Error('Native composer serializer unavailable');
    const attachments = node.querySelectorAll('[data-portal-composer-attachment-token]').length;
    const visible = node.isConnected && !node.closest('[hidden],[inert],[aria-hidden="true"]')
      && node.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    const finalText = serialize(node, [], {mode:'final'});
    const draftText = serialize(node, [], {mode:'draft'});
    return {input_id:node.id || null, intended_text:text, final_text:finalText, draft_text:draftText,
      visible, attachment_count:attachments, ready:visible && attachments === 0 && finalText === text};
  }, text);
}

// article must be reselected by its captured data-message-key inside the exact
// Chat root, never through the button name that changes when it is clicked.
async function readWatchboardMessageFold(article, {messageKey} = {}) {
  if (typeof messageKey !== 'string' || !messageKey) throw new TypeError('Exact original message key required');
  if (await article.count() !== 1) throw new Error('One original message article required');
  return article.evaluate((node, key) => {
    if (node.tagName !== 'ARTICLE' || node.dataset.messageKey !== key) throw new Error('Wrong original message identity');
    const buttons = node.querySelectorAll('[data-message-fold-toggle]');
    const bodies = node.querySelectorAll('[data-message-fold]');
    const button = buttons.length === 1 ? buttons[0] : null;
    const body = bodies.length === 1 ? bodies[0] : null;
    const visible = el => !!el && el.isConnected && !el.closest('[hidden],[inert],[aria-hidden="true"]')
      && el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    const rect = node.getBoundingClientRect();
    const fold = body?.dataset.messageFold ?? null, expanded = button?.getAttribute('aria-expanded') ?? null;
    const identity = !!button && !!body && button.dataset.messageFoldToggle === key && button.previousElementSibling === body;
    const consistent = (fold === 'expanded' && expanded === 'true') || (fold === 'collapsed' && expanded === 'false');
    return {message_key:key, message_instance:node.dataset.messageInstance ?? null,
      fold, aria_expanded:expanded, label:button?.textContent ?? null,
      rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height},
      ready:identity && consistent && visible(node) && visible(body) && visible(button) && !button.disabled};
  }, messageKey);
}

// GetResident's owned binding is distinct from shared bindings. This does not
// infer file inheritance: callers still read and compare the exact original bytes.
function readWatchboardForkWorkspace(bindings, {sourceWorkspaceId} = {}) {
  if (!/^workspace_[a-f0-9]{12}$/.test(sourceWorkspaceId || '') || !Array.isArray(bindings)) {
    throw new TypeError('Original source Workspace identity and actual Fork bindings required');
  }
  const owned = bindings.filter(binding => binding?.is_owned === true);
  const id = owned.length === 1 ? owned[0].workspace_id : null;
  const valid = typeof id === 'string' && /^workspace_[a-f0-9]{12}$/.test(id);
  return {source_workspace_id:sourceWorkspaceId, owned_count:owned.length, workspace_id:id,
    independent:valid && id !== sourceWorkspaceId, ready:valid && id !== sourceWorkspaceId};
}

module.exports = {readWatchboardComposer, readWatchboardMessageFold, readWatchboardForkWorkspace};
