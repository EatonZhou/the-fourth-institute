'use strict';

// Reveal the existing native tab strip through a real pointer move. No forced
// click, focus injection, style change, panel close or automatic layout unlock.
async function revealWorkbenchTabControls(tab, {record = () => {}, timeoutMs=3000} = {}) {
  if (typeof record!=='function' || !Number.isFinite(timeoutMs) || timeoutMs<=0 || timeoutMs>30000)
    throw new TypeError('Persistence callback and bounded timeout required');
  const deadline=Date.now()+timeoutMs;
  let moved=false, last=null;
  while (Date.now()<deadline) {
    const candidates=await tab.evaluateAll(tabs=>tabs.map(t=>{
      const stack=t.closest('.portalWorkbenchStack'), slot=t.closest('.portalWorkbenchTabSlot');
      if (!stack || !slot || !t.matches('[role="tab"][data-portal-workbench-tab]')) return {native:false};
      const rect=e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height};};
      const s=rect(stack), b=rect(t), point={x:s.x+s.width/2,y:s.y+2};
      const hit=document.elementFromPoint(point.x,point.y);
      const close=slot.querySelector('.portalWorkbenchTabClose'), c=close ? rect(close) : null;
      const closeHit=c ? document.elementFromPoint(c.x+c.width/2,c.y+c.height/2) : null;
      const locked=t.closest('.portalWorkbench')?.dataset.portalWorkbenchLayoutLocked==='true';
      return {native:true,tab_id:t.dataset.portalWorkbenchTab,stack:s,tab:b,locked,point,
        hover_reachable:s.width>0 && s.height>4 && !!hit && stack.contains(hit),
        close:close ? {label:close.getAttribute('aria-label'),geometry:c,enabled:!close.disabled,
          reachable:!!closeHit && (close===closeHit || close.contains(closeHit))} : null,
        revealed:b.y>=s.y && b.y+b.height<=s.y+s.height};
    }));
    last={observed_at_ms:Date.now(),candidates,ready:false};
    const v=candidates.length===1 ? candidates[0] : null;
    last.ready=!!v?.native && !v.locked && v.revealed && !!v.close?.enabled && v.close.reachable;
    try { await record(last); }
    catch (_) { last.evidence_gaps = ['未记录：观察记录写入失败']; }
    if (last.ready && Date.now()<=deadline) return last;
    if (candidates.length>1 || v?.locked) throw Object.assign(new Error('Ambiguous or locked native tab'),{snapshot:last});
    if (v?.native && v.hover_reachable && !moved) {
      await tab.page().mouse.move(v.point.x,v.point.y); moved=true;
    }
    await new Promise(resolve=>setTimeout(resolve,Math.min(40,Math.max(0,deadline-Date.now()))));
  }
  throw Object.assign(new Error('Native tab controls did not become reachable'),{code:'tau.tab_controls_timeout',snapshot:last});
}

module.exports={revealWorkbenchTabControls};
