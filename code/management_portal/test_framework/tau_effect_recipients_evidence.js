'use strict';

/** Read the native Menu once; persist this evidence inside the original action
 * before asserting ready. This does not replace its ISA receipt or GetLineage
 * readback, and deliberately does not request, scroll, click, or poll. */
async function readClosedEffectRecipients(menu, {blockId, effectIds} = {}) {
  if (typeof blockId !== 'string' || !blockId.trim()) throw new TypeError('blockId is required');
  if (!Array.isArray(effectIds) || !effectIds.length ||
      effectIds.some(id => typeof id !== 'string' || !/^effect_\S+$/.test(id)) ||
      new Set(effectIds).size !== effectIds.length) {
    throw new TypeError('effectIds must be a nonempty unique list of exact Effect IDs');
  }
  const expected = {blockId, effectIds};
  const menuCount = await menu.count();
  if (menuCount !== 1) return {ready: false, expected, menu_count: menuCount, rows: []};
  return menu.evaluate((root, expected) => {
    const win = root.ownerDocument.defaultView;
    const visible = node => {
      if (!node || !node.isConnected) return false;
      for (let n = node; n; n = n.parentElement) {
        const style = win.getComputedStyle(n);
        if (n.hidden || n.getAttribute('aria-hidden') === 'true' ||
            style.display === 'none' || style.visibility === 'hidden' ||
            style.visibility === 'collapse' || Number(style.opacity) === 0) return false;
      }
      const rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    const panels = Array.from(root.querySelectorAll('[data-function-block-menu-panel]'));
    const matches = panels.filter(n => n.getAttribute('data-function-block-menu-panel') === expected.blockId);
    const scope = {
      role: root.getAttribute('role'), visible: visible(root),
      panels: panels.map(n => ({id: n.getAttribute('data-function-block-menu-panel'), visible: visible(n)})),
      matching_panels: matches.length,
    };
    scope.ready = scope.role === 'dialog' && scope.visible && matches.length === 1 && visible(matches[0]);
    const i18n = win.PortalI18n;
    const offText = typeof i18n?.t === 'function' ? i18n.t('已关闭 · 失败时不提醒任何人') : null;
    const rows = expected.effectIds.map(id => {
      const candidates = matches.length === 1
        ? Array.from(matches[0].querySelectorAll('.functionBlockObjectRow[data-block-object]'))
          .filter(n => n.getAttribute('data-block-object') === id) : [];
      const observations = candidates.map(row => {
        const titles = Array.from(row.querySelectorAll('.functionBlockObjectMain > strong'));
        const title = titles.length === 1 ? titles[0].textContent : null;
        const expectedLabel = title !== null && typeof i18n?.text === 'function'
          ? i18n.text`修改 ${title} 的失败通知` : null;
        const buttons = Array.from(row.querySelectorAll('button.functionBlockRecipients'));
        const buttonObservations = buttons.map(button => {
          const chips = Array.from(button.querySelectorAll(':scope > .effectRecipientChips'));
          const chipObservations = chips.map(chip => {
            const raw = chip.getAttribute('data-recipient-chips');
            let recipients = null, parseError = null;
            if (raw !== null) {
              try { recipients = JSON.parse(raw); }
              catch (error) { parseError = {name: error.name, message: error.message}; }
            }
            const statuses = Array.from(chip.querySelectorAll('.effectRecipientsOff'));
            return {
              visible: visible(chip), raw_recipients: raw, recipients, parse_error: parseError,
              state: chip.getAttribute('data-recipients-state'),
              statuses: statuses.map(n => ({text: n.textContent, state: n.getAttribute('data-recipients-state'), visible: visible(n)})),
              ready: visible(chip) && raw !== null && Array.isArray(recipients) && recipients.length === 0 &&
                chip.getAttribute('data-recipients-state') === 'off' && statuses.length === 1 &&
                visible(statuses[0]) && statuses[0].getAttribute('data-recipients-state') === 'off' &&
                offText !== null && statuses[0].textContent === offText,
            };
          });
          return {
            id: button.getAttribute('data-block-recipients'), aria_label: button.getAttribute('aria-label'),
            visible: visible(button), enabled: !button.matches(':disabled') && button.getAttribute('aria-disabled') !== 'true',
            whole_text: button.innerText, decoration_text: Array.from(button.querySelectorAll('[aria-hidden="true"]')).map(n => n.textContent),
            chips: chipObservations,
            ready: button.getAttribute('data-block-recipients') === id && visible(button) &&
              !button.matches(':disabled') && button.getAttribute('aria-disabled') !== 'true' &&
              expectedLabel !== null && button.getAttribute('aria-label') === expectedLabel &&
              chipObservations.length === 1 && chipObservations[0].ready,
          };
        });
        return {
          id: row.getAttribute('data-block-object'), kind: row.getAttribute('data-object-kind'), visible: visible(row),
          title, title_count: titles.length, expected_label: expectedLabel, buttons: buttonObservations,
          ready: row.getAttribute('data-object-kind') === 'effect' && visible(row) &&
            buttonObservations.length === 1 && buttonObservations[0].ready,
        };
      });
      return {id, candidates: observations, ready: observations.length === 1 && observations[0].ready};
    });
    return {ready: scope.ready && rows.every(row => row.ready), expected, menu_count: 1, scope, expected_off_text: offText, rows};
  }, expected);
}

module.exports = {readClosedEffectRecipients};
