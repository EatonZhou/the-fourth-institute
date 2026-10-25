'use strict';
// Call in the original committed-delete callback. Two passive frames describe
// current visible UI and cache; they cannot replace a failed earlier callback.
async function readMailboxDeletedTarget(page,{residentId,entryKey}) {
 if(!/^resident_[0-9a-f]+$/.test(residentId)||entryKey!=='resident:'+residentId)throw new Error('Exact Resident and entry key required');
 return page.evaluate(async({residentId,entryKey})=>{
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
  const samples=[];for(let i=0;i<2;i++){await new Promise(requestAnimationFrame);samples.push(sample());}
  return {resident_id:residentId,entry_key:entryKey,samples,ready:samples.every(s=>s.store_ready&&s.store_loading===false&&s.store_syncing===false&&s.store_refreshing===false&&s.store_error===false&&s.target_count===0&&s.visible_node_count===0&&s.visible_row_count===0&&s.visible_dialogs.length===0),hidden_dom_is_retained_evidence:true};
 },{residentId,entryKey});
}
module.exports={readMailboxDeletedTarget};
