'use strict';

const copy = value => JSON.parse(JSON.stringify(value));

// Collect material for a use report. Missing observations remain explicit;
// callers may add a later observation without replaying the business action.
async function openReviewEvidenceWindow(root, { actionId, residentId, sourceMessage, record = () => {} } = {}) {
  const source = copy(sourceMessage || {});
  const part = source.content_parts?.find(p => p.type === 'native_turn') || {};
  const identity = { action_id: actionId, resident_id: residentId, source_message_id: source.message_id,
    segment_id: part.segment_id, execution_id: part.execution_id, thread_id: part.thread_id, turn_id: part.turn_id };
  const handle = await root.count() === 1 ? await root.elementHandle() : null;
  let closed = false;
  const close = async () => { if (!closed) { closed = true; await handle?.dispose(); } };
  const gaps = [];
  const save = async row => {
    try { await record(copy(row)); }
    catch (_) { if (!gaps.includes('未记录：观察记录写入失败')) gaps.push('未记录：观察记录写入失败'); }
  };
  const sample = async (replyIds = []) => {
    if (closed || !handle) throw new Error('Observation unavailable');
    return handle.evaluate((root, { cardId, replyIds }) => {
      const visible = e => {
        if (!e?.isConnected || !e.getClientRects().length) return false;
        for (let p = e; p; p = p.parentElement) {
          const css = getComputedStyle(p);
          if (p.hidden || p.inert || css.display === 'none' || css.visibility !== 'visible' || Number(css.opacity) === 0) return false;
          if (p.tagName === 'DETAILS' && !p.open && !p.querySelector(':scope > summary')?.contains(e)) return false;
        }
        return true;
      };
      const cards = [...root.querySelectorAll('[data-native-turn]')].filter(e => e.dataset.nativeTurn === cardId);
      const buttons = cards.length === 1 ? [...cards[0].querySelectorAll('[data-turn-review]')] : [];
      const button = buttons.length === 1 ? buttons[0] : null;
      const rect = button?.getBoundingClientRect();
      const hit = rect && document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      return {
        root_connected: root.isConnected, root_visible: visible(root),
        card_count: cards.length, card_visible: cards.length === 1 && visible(cards[0]),
        button_count: buttons.length, button_visible: visible(button),
        button_enabled: !!button && !button.disabled,
        button_hit: !!hit && (hit === button || button?.contains(hit)),
        review_message_id: cards.length === 1 ? cards[0].dataset.reviewMessage || null : null,
        replies: replyIds.map(id => {
          const nodes = [...root.querySelectorAll('.messageItem[data-message-key]')].filter(e => e.dataset.messageKey === id);
          return { message_id: id, count: nodes.length, visible: nodes.length === 1 && visible(nodes[0]),
            text: nodes.length === 1 ? nodes[0].innerText : '' };
        }),
      };
    }, { cardId: source.message_id, replyIds });
  };

  const collect = async (stage, material = {}) => {
    const row = { stage, observed_at: new Date().toISOString(), identity, ...copy(material), evidence_gaps: [...gaps] };
    try { row.sample = await sample(material.terminal?.reply_message_ids || []); }
    catch (_) { row.evidence_gaps.push('未记录：页面观察'); }
    if (stage === 'finish') for (const key of ['receipt', 'input', 'terminal', 'replies']) {
      if (material[key] == null) row.evidence_gaps.push('未记录：' + key);
    }
    await save(row);
    row.evidence_gaps = [...new Set([...row.evidence_gaps, ...gaps])];
    return copy(row);
  };
  await collect('before_click');
  return { close, finish: (material = {}) => collect('finish', material) };
}

module.exports = { openReviewEvidenceWindow };
