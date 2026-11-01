'use strict';

// Read current native controls without clicks, retries or business assertions.
function hatgShowControlLocator(page) {
  return page.getByRole('button', {name: /^(Show|显示) HATG$/});
}
async function nativeHatgShowControl(page) {
  const graph = page.getByRole('region', {name: 'HATG', exact: true});
  if (await graph.count() === 1 && await graph.isVisible()) return null;
  const show = hatgShowControlLocator(page);
  if (await show.count() !== 1 || !await show.isVisible() || !await show.isEnabled())
    throw new Error('Current native HATG opening control is unavailable');
  return show;
}

async function currentNativeTab(page, name) {
  const tabs = page.getByRole('tab', {name, exact: true});
  const count = await tabs.count();
  if (count > 1) throw new Error('Ambiguous current native tab');
  return count === 1 ? tabs : null;
}
async function nativeEntryDestination(button) {
  const popup = await button.getAttribute('aria-haspopup');
  if (popup === 'menu' || popup === 'true') return 'menu';
  return popup === 'dialog' ? 'dialog' : null;
}
async function currentStatusTexts(scope) {
  return scope.getByRole('status').evaluateAll(nodes => nodes.map(node => node.textContent.trim()));
}
async function currentSelectOptions(select) {
  return select.locator('option').evaluateAll(nodes => nodes.map(node => ({
    label: node.label, value: node.value, disabled: node.disabled || !!node.closest('optgroup')?.disabled,
  })));
}
async function currentNativeDialog(page, name) {
  const dialog = page.getByRole('dialog', {name, exact: true})
    .or(page.getByRole('alertdialog', {name, exact: true}));
  const count = await dialog.count();
  if (count > 1) throw new Error('Ambiguous current native dialog');
  return count === 1 ? dialog : null;
}
async function currentProgrammingInspectors(page) {
  return page.locator('[data-inspector-kind]:visible').evaluateAll(nodes => nodes.map(node => ({
    id: node.id, kind: node.dataset.inspectorKind,
    component_id: node.dataset.componentId || null, wire_id: node.dataset.wireId || null,
    label: node.getAttribute('aria-label'),
  })));
}
async function currentMessageQuoteAction(page, {messageId}) {
  if (typeof messageId !== 'string' || !messageId) throw new Error('Original message identity is required');
  const menus = page.locator('[data-portal-message-menu]:visible');
  const identities = await menus.evaluateAll(nodes => nodes.map(node => node.dataset.portalMessageKey));
  const matches = identities.flatMap((key, index) => key === messageId ? [index] : []);
  if (matches.length > 1) throw new Error('Ambiguous current message menu');
  if (!matches.length) return null;
  const quote = menus.nth(matches[0]).locator('[data-portal-message-action="quote"]:visible');
  const count = await quote.count();
  if (count > 1) throw new Error('Ambiguous current quote action');
  return count === 1 && await quote.isEnabled() ? quote : null;
}

module.exports = {hatgShowControlLocator, nativeHatgShowControl, currentNativeTab, nativeEntryDestination, currentStatusTexts, currentSelectOptions, currentNativeDialog, currentProgrammingInspectors, currentMessageQuoteAction};
