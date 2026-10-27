'use strict';

// Passive prerequisite only. Caller reveals the real native strip, waits in
// the original action, clicks once, and separately proves that panel closed.
// This browser deadline bounds frame sampling only, not the caller's tool RTT.
// The caller must save this result before asserting ready or taking any action.
async function readWorkbenchTabClose(tab, {panelId, frameTimeoutMs = 1000} = {}) {
  if (!panelId || typeof panelId !== 'string') throw new TypeError('Exact original panel ID required');
  if (!Number.isFinite(frameTimeoutMs) || frameTimeoutMs <= 0 || frameTimeoutMs > 10000)
    throw new TypeError('Frame sampling deadline must be in (0, 10000] ms');
  if (await tab.count() !== 1) throw new Error('One original workbench tab required');
  return tab.evaluate(async (node, {panelId, frameTimeoutMs}) => {
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
    const samples=[];
    let framesCompleted=0, phase='initial_sample';
    try {
      samples.push(sample());
      phase='animation_frames';
      await new Promise((resolve,reject)=>{
        let frame=null, settled=false;
        const finish=error=>{
          if(settled)return;
          settled=true;
          view.clearTimeout(timer);
          if(frame!==null)view.cancelAnimationFrame(frame);
          if(error)reject(error);else resolve();
        };
        const timer=view.setTimeout(()=>{
          const error=new Error('Native close-control frame sampling deadline exceeded');
          error.code='frame_timeout';finish(error);
        },frameTimeoutMs);
        function next(){
          try {
            frame=view.requestAnimationFrame(()=>{
              if(settled)return;
              frame=null;framesCompleted++;
              if(framesCompleted===2)finish();else next();
            });
          } catch(error){finish(error);}
        }
        next();
      });
      phase='final_sample';
      samples.push(sample());
    } catch(error){
      return {panel_id:panelId,samples,stable:false,ready:false,frames_completed:framesCompleted,
        frame_timeout_ms:frameTimeoutMs,error:{code:error?.code||'sampling_error',phase,
          name:error?.name||'Error',message:String(error?.message||error),stack:String(error?.stack||'')}};
    }
    const stable=Object.keys(samples[0].bounds).every(k=>Math.abs(samples[0].bounds[k]-samples[1].bounds[k])<=0.25);
    return {panel_id:panelId,samples,stable,frames_completed:framesCompleted,frame_timeout_ms:frameTimeoutMs,error:null,
      ready:stable&&samples.every(s=>s.visible&&s.enabled&&s.selected&&s.connected&&s.in_viewport&&s.center_hittable&&s.animating===0)};
  },{panelId,frameTimeoutMs});
}

module.exports={readWorkbenchTabClose};
