'use strict';

// One passive original-action read. The native collapsed card exposes its
// title as aria-label, not body text. This is card readiness, not a creation,
// membership, Menu or issue-state receipt. Persist before judging ready.
async function readFunctionBlockCardReady(panel,{lineageId,blockId,title}={}) {
  if(!/^lineage_[a-z0-9]+$/.test(lineageId || '') || !/^fbi_[a-z0-9]+$/.test(blockId || '') ||
      typeof title!=='string' || !title.trim()) throw new TypeError('Original lineageId, blockId and title are required');
  const result={expected:{lineage_id:lineageId,block_id:blockId,title},ready:false,candidates:[],error:null};
  let handles=[],phase='candidates';
  try {
    handles=await panel.elementHandles();phase='sample';
    for(const handle of handles)result.candidates.push(await handle.evaluate((root,blockId)=>{
      const visible=n=>{
        if(!n?.isConnected)return false;
        for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);
          if(p.hidden || p.inert || p.getAttribute('aria-hidden')==='true' || s.display==='none' ||
            ['hidden','collapse'].includes(s.visibility) || Number(s.opacity)===0)return false;}
        const r=n.getBoundingClientRect();return r.width>0 && r.height>0;
      };
      const describe=n=>({tag:n.tagName,id:n.id,visible:visible(n),connected:n.isConnected,busy:n.getAttribute('aria-busy'),
        disabled:n.matches(':disabled'),aria_disabled:n.getAttribute('aria-disabled')});
      const canvases=[...root.querySelectorAll('#hatgCanvas')];
      return {root:describe(root),native:root.matches('.portalHatgPanelComponent[role="region"]'),
        identities:[...root.querySelectorAll('#activeLineageIdentity [data-portal-resource-id]')]
          .map(n=>({id:n.getAttribute('data-portal-resource-id'),text:n.textContent,visible:visible(n)})),
        canvases:canvases.map(describe),
        cards:[...root.querySelectorAll('.hatgNode[data-node-id]')].filter(n=>n.getAttribute('data-node-id')===blockId)
          .map(n=>({...describe(n),node_id:n.getAttribute('data-node-id'),kind:n.getAttribute('data-node-kind'),
            family:n.getAttribute('data-function-block-family'),name:n.getAttribute('aria-label'),labelledby:n.getAttribute('aria-labelledby'),
            text:n.textContent,semantic_level:n.getAttribute('data-semantic-level'),pressed:n.getAttribute('aria-pressed'),
            body_count:n.querySelectorAll(':scope > .functionBlockInstanceNode').length,
            glyph_count:n.querySelectorAll(':scope > .functionBlockInstanceNode > .functionBlockPigeon[aria-hidden="true"]').length,
            in_canvas:canvases.length===1 && n.closest('#hatgCanvas')===canvases[0]}))};
    },blockId));
    const c=result.candidates[0],card=c?.cards[0],canvas=c?.canvases[0];
    const usable=n=>!!n && n.visible && n.connected && n.busy!=='true' && !n.disabled && n.aria_disabled!=='true';
    result.ready=result.candidates.length===1 && c.native && usable(c.root) &&
      c.identities.length===1 && c.identities[0].visible && c.identities[0].id===lineageId && c.identities[0].text===lineageId &&
      c.canvases.length===1 && usable(canvas) && c.cards.length===1 && usable(card) && card.tag==='BUTTON' &&
      card.kind==='function_block' && card.family===null && card.name===title && card.labelledby===null &&
      card.body_count===1 && card.glyph_count===1 && card.in_canvas;
  }catch(error){result.ready=false;result.error={phase,name:error.name,message:error.message,stack:error.stack || null};}
  finally{for(const handle of handles)await handle.dispose().catch(()=>{});}
  return result;
}

module.exports={readFunctionBlockCardReady};
