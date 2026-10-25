'use strict';

// Read the actual scoped Watchboard; a button ancestor is not the whole panel.
// The state is observed, not assumed to be idle. Generated files and instruction
// receipts still need to be checked inside the caller's original action.
async function readWatchboardPanel(root) {
  if (await root.count() !== 1) throw new Error('One actual Watchboard root is required');
  return root.evaluate(node => {
    const visible = el => !!el && el.checkVisibility() && el.getBoundingClientRect().width > 0
      && el.getBoundingClientRect().height > 0;
    const valid = node.matches('aside.watchboard[aria-label]') && !!node.querySelector(':scope > .watchboardHead')
      && !!node.querySelector(':scope > .watchboardBody');
    if (!valid) throw new Error('Expected the full Watchboard aside, not command-actions or Chat input');
    const body = node.querySelector(':scope > .watchboardBody');
    const statusButton = body.querySelector('[data-watchboard-status-open]');
    const section = statusButton?.closest('.watchboardSection');
    const labels = section?.querySelectorAll(':scope > .watchboardSectionHead > span');
    const errors = [...body.querySelectorAll('.consoleError')].filter(visible);
    return {sampled_at_ms: Date.now(), root_id: node.id, visible: visible(node), body_visible: visible(body),
      title: node.querySelector(':scope > .watchboardHead .watchboardTitle')?.textContent?.trim() || '',
      // textContent preserves the actual value across CSS text-transform/locales.
      status_text: labels?.length === 2 ? labels[1].textContent.trim() : null,
      status_visible: labels?.length === 2 && visible(labels[1]), errors_visible: errors.length,
      commands: [...body.querySelectorAll('[data-watchboard-run-command]')].map(button => ({
        command: button.getAttribute('data-watchboard-run-command'), visible: visible(button), enabled: !button.disabled,
      })),
      configs: [...body.querySelectorAll('[data-watchboard-config-open]')].map(button => ({
        path: button.getAttribute('data-watchboard-config-open'), visible: visible(button), enabled: !button.disabled,
      })),
    };
  });
}

// Both locators come from the original visible business area. This reads only;
// scrolling must still be performed through a native wheel/keyboard action.
async function readBusinessScrollport(container, lastItem) {
  if (await container.count() !== 1 || await lastItem.count() !== 1)
    throw new Error('One actual business scrollport and last item are required');
  const item = await lastItem.elementHandle();
  try {
    return await container.evaluate((node, last) => {
      if (!last || node === last || !node.contains(last) || node.isContentEditable
          || node.matches('input,textarea,[role="textbox"]') || node.closest('[contenteditable="true"]'))
        throw new Error('The composer is not a business scrollport; use the visible messages or Watchboard container');
      const rect = el => {
        const r = el.getBoundingClientRect();
        return {x: r.x, y: r.y, top: r.top, left: r.left, right: r.right, bottom: r.bottom, width: r.width, height: r.height};
      };
      const r = rect(node), l = rect(last), style = getComputedStyle(node);
      const hit = document.elementFromPoint(l.x + l.width / 2, l.y + l.height / 2);
      const within = l.top >= r.top - 1 && l.bottom <= r.bottom + 1 && l.left >= r.left - 1 && l.right <= r.right + 1;
      const visible = node.checkVisibility() && last.checkVisibility() && r.width > 0 && r.height > 0 && l.width > 0 && l.height > 0;
      return {sampled_at_ms: Date.now(), container_id: node.id, rect: r,
        scrollTop: node.scrollTop, clientHeight: node.clientHeight, scrollHeight: node.scrollHeight,
        clientWidth: node.clientWidth, scrollWidth: node.scrollWidth, overflowY: style.overflowY,
        has_vertical_overflow: node.scrollHeight > node.clientHeight + 1,
        has_horizontal_overflow: node.scrollWidth > node.clientWidth + 1,
        last: {rect: l, visible, within_container: visible && within, center_hit: visible && (hit === last || last.contains(hit))}};
    }, item);
  } finally { await item?.dispose(); }
}

module.exports = {readWatchboardPanel, readBusinessScrollport};
