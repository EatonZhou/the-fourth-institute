'use strict';

/** A passive, single-document snapshot of native Function Block Menu readiness.
 * The original action must save it before asserting ready. It neither opens a
 * menu nor substitutes for the creation/ownership receipt or earlier stages. */
async function readFunctionBlockMenuReady(page, {blockId, title, pane = 'block'} = {}) {
  if (typeof blockId !== 'string' || !blockId.trim() || typeof title !== 'string' || !title.trim()) {
    throw new TypeError('Exact blockId and title are required');
  }
  if (!['block', 'objects', 'issues'].includes(pane)) throw new TypeError('Unknown Function Block pane');
  return page.evaluate(expected => {
    const visible = node => {
      if (!node?.isConnected) return false;
      for (let n = node; n; n = n.parentElement) {
        const style = getComputedStyle(n);
        if (n.hidden || n.getAttribute('aria-hidden') === 'true' || style.display === 'none' ||
            ['hidden', 'collapse'].includes(style.visibility) || Number(style.opacity) === 0) return false;
      }
      const rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    const menuName = typeof window.PortalI18n?.t === 'function' ? window.PortalI18n.t('Menu') : null;
    const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]')).map(dialog => {
      const labelledBy = (dialog.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
      const labels = Array.from(dialog.querySelectorAll('[id]')).filter(n => labelledBy.includes(n.id));
      const name = dialog.getAttribute('aria-label') ?? labels.map(n => n.textContent).join(' ');
      const native = dialog.matches('[data-portal-detail-surface]') && !!dialog.closest('#nodeSettingsModal[data-portal-detail-overlay]');
      const groups = Array.from(dialog.querySelectorAll('#nodeMenuGroupPanel[role="tabpanel"]'));
      const tabs = Array.from(dialog.querySelectorAll('#nodeMenuGroupTabs [data-group-tab]')).map(tab => ({
        key: tab.getAttribute('data-group-tab'), selected: tab.getAttribute('aria-selected'),
        controls: tab.getAttribute('aria-controls'), visible: visible(tab),
        enabled: !tab.matches(':disabled') && tab.getAttribute('aria-disabled') !== 'true',
      }));
      const selected = tabs.filter(tab => tab.selected === 'true');
      const tabReady = selected.length === 1 && selected[0].key === expected.pane &&
        selected[0].controls === 'nodeMenuGroupPanel' && selected[0].visible && selected[0].enabled;
      const panels = Array.from(dialog.querySelectorAll('[data-function-block-menu-panel]')).map(panel => {
        const headers = Array.from(panel.querySelectorAll(':scope > .functionBlockMenuIdentity'));
        const codes = headers.flatMap(header => Array.from(header.querySelectorAll(':scope > code')));
        const titles = headers.flatMap(header => Array.from(header.querySelectorAll(':scope > strong')));
        const inGroup = groups.length === 1 && groups[0].contains(panel);
        return {
          id: panel.getAttribute('data-function-block-menu-panel'), visible: visible(panel),
          header_count: headers.length, codes: codes.map(n => ({text: n.textContent, visible: visible(n)})),
          titles: titles.map(n => ({text: n.textContent, visible: visible(n)})), in_group: inGroup,
          ready: panel.getAttribute('data-function-block-menu-panel') === expected.blockId && visible(panel) &&
            inGroup && headers.length === 1 && codes.length === 1 && titles.length === 1 &&
            codes[0].textContent === expected.blockId && titles[0].textContent === expected.title &&
            visible(codes[0]) && visible(titles[0]),
        };
      });
      const groupStates = groups.map(group => ({id: group.id, visible: visible(group), busy: group.getAttribute('aria-busy')}));
      return {
        id: dialog.id, role: dialog.getAttribute('role'), name, native, visible: visible(dialog), busy: dialog.getAttribute('aria-busy'),
        groups: groupStates, tabs, tab_ready: tabReady, panels,
        ready: native && visible(dialog) && menuName !== null && name === menuName &&
          dialog.getAttribute('aria-busy') !== 'true' && groups.length === 1 && groupStates[0].visible &&
          groupStates[0].busy !== 'true' && tabReady && panels.length === 1 && panels[0].ready,
      };
    });
    const visibleDialogs = dialogs.filter(dialog => dialog.visible);
    const matchingDialogs = dialogs.filter(dialog => dialog.ready);
    return {
      ready: visibleDialogs.length === 1 && matchingDialogs.length === 1,
      expected, expected_menu_name: menuName, visible_dialog_count: visibleDialogs.length,
      matching_dialog_count: matchingDialogs.length, dialogs,
    };
  }, {blockId, title, pane});
}

module.exports = {readFunctionBlockMenuReady};
