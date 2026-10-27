'use strict';
// Call in the original committed-delete callback. Two passive frames describe
// current visible UI and cache; they cannot replace a failed earlier callback.
// Save the complete result before asserting ready. This browser-frame budget
// is not a measurement of the original caller's RTT or business action.
async function readMailboxDeletedTarget(page,{residentId,entryKey,frameTimeoutMs=1000}={}) {
 if(!/^resident_[0-9a-f]+$/.test(residentId)||entryKey!=='resident:'+residentId)throw new Error('Exact Resident and entry key required');
 if(!Number.isFinite(frameTimeoutMs)||frameTimeoutMs<=0||frameTimeoutMs>10000)throw new TypeError('Frame sampling deadline must be in (0, 10000] ms');
 return page.evaluate(async({residentId,entryKey,frameTimeoutMs})=>{
  const sample=()=>{
   const s=window.PortalConversationStore?.snapshot?.();
   const visible=n=>n.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})&&!n.closest('[hidden],[inert],[aria-hidden="true"]');
   const nodes=[...document.querySelectorAll('[data-node-id]')].filter(n=>n.dataset.nodeId===residentId);
   const rows=[...document.querySelectorAll('[data-agent-conversation-key]')].filter(n=>n.dataset.agentConversationKey===entryKey);
   const dialogs=[...document.querySelectorAll('[role="alertdialog"],[role="dialog"]')].filter(visible);
   const entries=Array.isArray(s?.entries)?s.entries:null;
   return {epoch_ms:Date.now(),browser_ms:performance.now(),time_origin_ms:performance.timeOrigin,
    store_ready:s?.ready===true,store_loading:s?.loading??null,store_syncing:s?.syncing??null,store_refreshing:s?.refreshing??null,store_error:s?.error==null?null:Boolean(s.error),
    target_count:entries?entries.filter(x=>x.resident_id===residentId||x.key===entryKey).length:null,
    node_count:nodes.length,visible_node_count:nodes.filter(visible).length,hidden_node_count:nodes.filter(n=>!visible(n)).length,
    row_count:rows.length,visible_row_count:rows.filter(visible).length,
    visible_dialogs:dialogs.map(n=>({role:n.getAttribute('role'),label:n.getAttribute('aria-label'),labelledby:n.getAttribute('aria-labelledby')}))};
  };
  const samples=[];let framesCompleted=0,phase='animation_frames',samplingError=null;
  try{
   await new Promise((resolve,reject)=>{
    let frame=null,settled=false;
    const finish=error=>{
     if(settled)return;
     settled=true;window.clearTimeout(timer);
     if(frame!==null)window.cancelAnimationFrame(frame);
     if(error)reject(error);else resolve();
    };
    const timer=window.setTimeout(()=>{
     const error=new Error('Mailbox deleted-target frame sampling deadline exceeded');
     error.code='frame_timeout';finish(error);
    },frameTimeoutMs);
    function next(){
     try{
      frame=window.requestAnimationFrame(()=>{
       if(settled)return;
       frame=null;framesCompleted++;
       try{
        phase='sample';samples.push(sample());
        if(framesCompleted===2)finish();
        else{phase='animation_frames';next();}
       }catch(error){finish(error);}
      });
     }catch(error){finish(error);}
    }
    next();
   });
  }catch(error){
   samplingError={code:error?.code||'sampling_error',phase,name:error?.name||'Error',message:String(error?.message||error),stack:String(error?.stack||'')};
  }
  return {resident_id:residentId,entry_key:entryKey,samples,frames_completed:framesCompleted,frame_timeout_ms:frameTimeoutMs,error:samplingError,
   ready:!samplingError&&samples.length===2&&samples.every(s=>s.store_ready&&s.store_loading===false&&s.store_syncing===false&&s.store_refreshing===false&&s.store_error===false&&s.target_count===0&&s.visible_node_count===0&&s.visible_row_count===0&&s.visible_dialogs.length===0),hidden_dom_is_retained_evidence:true};
 },{residentId,entryKey,frameTimeoutMs});
}
module.exports={readMailboxDeletedTarget};
