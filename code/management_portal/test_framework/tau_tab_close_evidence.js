'use strict';

// Passive prerequisite only. Caller reveals the real native strip, waits in
// the original action, clicks once, and separately proves that panel closed.
async function readWorkbenchTabClose(tab, {panelId} = {}) {
  if (!panelId || typeof panelId !== 'string') throw new TypeError('Exact original panel ID required');
  if (await tab.count() !== 1) throw new Error('One original workbench tab required');
  return tab.evaluate(async (node, panelId) => {
    if (node.getAttribute('role') !== 'tab' || node.dataset.portalWorkbenchTab !== panelId)
      throw new Error('Wrong native workbench tab identity');
    const slot=node.parentElement, tabs=slot?.parentElement, stack=tabs?.parentElement;
    if (!slot?.matches('.portalWorkbenchTabSlot') || !tabs?.matches('.portalWorkbenchTabs[role=tablist]')
      || !stack?.matches('.portalWorkbenchStack')) throw new Error('Wrong native workbench tab scope');
    const buttons=slot.querySelectorAll(':scope > button.portalWorkbenchTabClose');
    if (buttons.length!==1) throw new Error('One original native close control required');
    const button=buttons[0], doc=node.ownerDocument, view=doc.defaultView;
    function sample(){
      const rect=button.getBoundingClientRect();
      const visible=button.isConnected && !button.closest('[hidden],[inert],[aria-hidden="true"]')
        && button.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
      const enabled=!button.disabled && button.getAttribute('aria-disabled')!=='true';
      const hit=doc.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);
      const animating=tabs.getAnimations({subtree:true}).filter(a=>a.playState==='running'||a.pending).length;
      return {at_ms:view.performance.now(),visible,enabled,animating,selected:node.getAttribute('aria-selected')==='true',
        connected:node.isConnected && node.parentElement===slot && button.parentElement===slot,
        bounds:{x:rect.x,y:rect.y,width:rect.width,height:rect.height},
        in_viewport:rect.width>0&&rect.height>0&&rect.left>=0&&rect.top>=0&&rect.right<=view.innerWidth&&rect.bottom<=view.innerHeight,
        center_hittable:!!hit&&(hit===button||button.contains(hit))};
    }
    const samples=[sample()];
    await new Promise(resolve=>view.requestAnimationFrame(()=>view.requestAnimationFrame(resolve)));
    samples.push(sample());
    const stable=Object.keys(samples[0].bounds).every(k=>Math.abs(samples[0].bounds[k]-samples[1].bounds[k])<=0.25);
    return {panel_id:panelId,samples,stable,ready:stable&&samples.every(s=>s.visible&&s.enabled&&s.selected&&s.connected&&s.in_viewport&&s.center_hittable&&s.animating===0)};
  },panelId);
}

module.exports={readWorkbenchTabClose};
