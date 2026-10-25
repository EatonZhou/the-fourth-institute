'use strict';

// Read only the exact native Menu. Call before and after the single native
// Pin click, inside its original action. This cannot recover a missed ready.
async function readMailboxPinDom(menu, {residentId, pinned} = {}) {
  if (!/^resident_[a-f0-9]{12}$/.test(residentId || '') || typeof pinned !== 'boolean') {
    throw new TypeError('Exact Resident identity and expected Pin state required');
  }
  if (await menu.count() !== 1) throw new Error('Pin requires one original Menu locator');
  return menu.evaluate((root, expected) => {
    const visible = el => !!el && el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})
      && !el.closest('[hidden],[aria-hidden="true"]');
    const topDialogs=[...root.ownerDocument.querySelectorAll('[role="dialog"],[role="alertdialog"]')].filter(visible);
    const button=root.querySelectorAll('#nodeMenuPin');
    const identity=root.querySelectorAll('#nodeResidentIdentityReadout');
    const exact=root.getAttribute('role')==='dialog' && topDialogs.length===1 && topDialogs[0]===root
      && identity.length===1 && identity[0].textContent.trim()===expected.residentId;
    const control=button.length===1 ? button[0] : null;
    const label=control?.getAttribute('aria-label') || '';
    const pressed=control?.getAttribute('aria-pressed') ?? null;
    const enabled=!!control && !control.disabled && control.getAttribute('aria-disabled')!=='true';
    return {resident_id:expected.residentId,exact_menu:exact,control_id:control?.id || null,
      observed_label:label,pressed,enabled,visible:visible(control),
      ready:exact && visible(control) && enabled && !!label && pressed===String(expected.pinned)};
  },{residentId,pinned});
}
module.exports={readMailboxPinDom};
