'use strict';

const { createHash } = require('node:crypto');
const copy = value => JSON.parse(JSON.stringify(value));
const text = value => String(value ?? '').replace(/\s+/g, ' ').trim();
const check = (condition, reason) => { if (!condition) throw new Error(`ReviewEvidence:${reason}`); };

// The caller owns the original action, observer document/request identities and
// monotonic measurement. This helper never sends input, polls, focuses or opens
// a message. Bind the visible, identity-verified Resident Chat before clicking.
async function openReviewEvidenceWindow(root, { actionId, residentId, sourceMessage, record }) {
  check(typeof record === 'function' && actionId && residentId, 'MissingOriginalContext');
  const source = copy(sourceMessage);
  const parts = source?.content_parts?.filter(p => p.type === 'native_turn') || [];
  check(source.message_id && source.author_id === residentId && parts.length === 1, 'SourceIdentity');
  const part = parts[0];
  check(part.segment_id && part.execution_id && part.thread_id && part.turn_id, 'NativeIdentity');
  check(typeof part.diff === 'string' && part.diff.trim() && !part.diff_truncated, 'CapturedDiff');
  check(await root.count() === 1, 'AmbiguousChat');
  const handle = await root.elementHandle();
  check(handle, 'MissingChat');
  let closed = false, finishing = false;
  const identity = {
    action_id: actionId, resident_id: residentId, source_message_id: source.message_id,
    segment_id: part.segment_id, execution_id: part.execution_id,
    thread_id: part.thread_id, turn_id: part.turn_id,
    diff_sha256: createHash('sha256').update(part.diff).digest('hex'),
  };
  const close = async () => { if (!closed) { closed = true; await handle.dispose(); } };
  const sample = async (replyIds = []) => {
    check(!closed, 'WindowClosed');
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
  try {
    const before = await sample();
    await record(copy({ stage: 'before_click', identity, sample: before }));
    check(before.root_visible && before.card_count === 1 && before.card_visible
      && before.button_count === 1 && before.button_visible && before.button_enabled && before.button_hit, 'OriginalButtonNotReady');
  } catch (error) { await close(); throw error; }

  return {
    close,
    async finish({ receipt, input, terminal, replies }) {
      check(!closed && !finishing, 'WindowClosed');
      finishing = true;
      // Save evidence before checking it. An accepted-only/failed window is
      // permanently closed; a later success cannot be appended to this record.
      try {
        const proof = copy({ receipt, input, terminal, replies });
        const ids = Array.isArray(proof.terminal?.reply_message_ids) ? proof.terminal.reply_message_ids : [];
        let after;
        try { after = await sample(ids); }
        catch (error) {
          await record(copy({ stage: 'finish', identity, ...proof,
            sample_error: { name: error.name, message: String(error.message).slice(0, 500) } }));
          throw error;
        }
        await record(copy({ stage: 'finish', identity, ...proof, sample: after }));
        const result = proof.receipt?.result;
        const message = proof.input;
        const reviewParts = message?.content_parts?.filter(p => p.type === 'turn_review') || [];
        check(proof.receipt?.instruction === 'DeliverMessage' && proof.receipt.instruction_id
          && proof.receipt.status === 'committed' && result?.accepted === true
          && result.resident_id === residentId && result.message_id, 'OriginalAcceptance');
        check(message?.role === 'user' && message.message_id === result.message_id
          && reviewParts.length === 1 && reviewParts[0].segment_id === part.segment_id, 'ReviewInputIdentity');
        check(proof.terminal?.message_id === message.message_id && proof.terminal.state === 'completed'
          && ids.length && new Set(ids).size === ids.length, 'OriginalTerminal');
        check(after.root_connected && after.root_visible && after.card_count === 1
          && after.review_message_id === message.message_id, 'OriginalChatIdentity');
        check(Array.isArray(proof.replies) && proof.replies.length === ids.length, 'ReplySet');
        for (const id of ids) {
          const matches = proof.replies.filter(m => m.message_id === id);
          check(matches.length === 1, 'ReplyIdentity');
          const reply = matches[0];
          check(reply.role === 'agent' && reply.author_id === residentId
            && reply.reply_to_message_id === message.message_id, 'ReplyIdentity');
          const body = (reply.content_parts || []).filter(p => p.type === 'text').map(p => text(p.text)).filter(Boolean);
          const row = after.replies.find(r => r.message_id === id);
          check(body.length && row?.count === 1 && row.visible && body.every(t => text(row.text).includes(t)), 'VisibleFinalMissing');
        }
        return copy({ identity, message_id: message.message_id, reply_message_ids: ids, ready: true });
      } finally { await close(); }
    },
  };
}

module.exports = { openReviewEvidenceWindow };
