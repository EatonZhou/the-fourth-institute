'use strict';

// Read the selected native WALL chat, without clicking or requesting history.
// The caller still proves original block/body/loading and response identity.
async function readWallChatPanel(root, {kind, messageId = ''} = {}) {
  if (!['discussion', 'work'].includes(kind)) throw new Error('Explicit WALL chat kind required');
  if (kind === 'work' && !/^msg_[a-zA-Z0-9]+$/.test(messageId))
    throw new Error('Original job message ID required for Work');
  if (await root.count() !== 1) throw new Error('One actual WALL detail root required');
  return root.evaluate((node, {kind, messageId}) => {
    if (node.id !== 'wallDetail') throw new Error('Expected full WALL detail root');
    const visible = el => !!el && !el.closest('[hidden],[inert],[aria-hidden="true"]')
      && el.checkVisibility() && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
    const tab = node.querySelector(kind === 'work' ? '#wallWorkTab' : '#wallDiscussionTab');
    const panelId = tab?.getAttribute('aria-controls');
    const panel = panelId ? node.querySelector('#' + CSS.escape(panelId)) : null;
    const controlled = panel?.getAttribute('role') === 'tabpanel' && panel.getAttribute('aria-labelledby') === tab.id;
    const selected = controlled && tab.getAttribute('aria-selected') === 'true';
    const markers = panel ? [...panel.querySelectorAll('[data-workspace-resident-id]')].filter(visible) : [];
    const residents = [...new Set(markers.map(el => el.getAttribute('data-workspace-resident-id')).filter(Boolean))];
    if (residents.length > 1) throw new Error('Ambiguous native WALL resident identity');
    const rows = panel ? [...panel.querySelectorAll('article.messageItem[data-message-key]')]
      .filter(el => /^msg_[a-zA-Z0-9]+$/.test(el.getAttribute('data-message-key')))
      .map(el => ({key: el.getAttribute('data-message-key'), instance: el.getAttribute('data-message-instance'), visible: visible(el)})) : [];
    const composers = panel ? [...panel.querySelectorAll('[role="textbox"][contenteditable]')].filter(visible) : [];
    const composer = composers.length === 1 ? composers[0] : null;
    const matched = rows.filter(row => row.key === messageId);
    return {sampled_at_ms: Date.now(), visible: visible(node), selected_tab: selected ? kind : '',
      panel_id: panel?.id || '', panel_visible: !!selected && visible(node) && visible(panel),
      resident_id: residents[0] || '', message_keys: rows.map(row => row.key), message_rows: rows,
      message_id: matched.length === 1 && matched[0].visible ? matched[0].key : '',
      composer_editable: !!composer && composer.isContentEditable && composer.getAttribute('aria-disabled') !== 'true',
      explicit_empty: !!panel && rows.length === 0 && [...panel.querySelectorAll('.portalWorkspaceEmpty')].some(visible),
    };
  }, {kind, messageId});
}

// Construction is an observable state, not an exception to turn into success.
// Wait for ready inside the original action, then run readWatchboardPanel and
// the original receipt/file/graph checks. A true result is not business ready.
async function probeWatchboardStructure(root) {
  const count = await root.count();
  if (count > 1) throw new Error('Ambiguous Watchboard roots');
  if (count === 0) return {sampled_at_ms: Date.now(), root_count: 0, ready: false};
  return root.evaluate(node => {
    if (!node.matches('aside.watchboard[aria-label]')) throw new Error('Expected full Watchboard aside');
    const visible = el => !!el && !el.closest('[hidden],[inert],[aria-hidden="true"]')
      && el.checkVisibility() && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
    const heads = node.querySelectorAll(':scope > .watchboardHead');
    const bodies = node.querySelectorAll(':scope > .watchboardBody');
    const structure = heads.length === 1 && bodies.length === 1;
    return {sampled_at_ms: Date.now(), root_count: 1, root_id: node.id, head_count: heads.length, body_count: bodies.length,
      visible: visible(node), body_visible: structure && visible(bodies[0]),
      ready: structure && visible(node) && visible(heads[0]) && visible(bodies[0])};
  });
}

module.exports = {readWallChatPanel, probeWatchboardStructure};
