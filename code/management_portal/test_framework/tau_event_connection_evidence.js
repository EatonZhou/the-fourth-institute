'use strict';

// One original-action observation after the original CreateWire receipt. The
// caller persists it before deciding. No request, click, wait, poll or retry.
async function readEventEffectConnection(panel,{lineageId,eventId,effectId,wireId}={}) {
  for(const [name,prefix,value] of [['lineageId','lineage',lineageId],['eventId','event_source',eventId],['effectId','effect',effectId],['wireId','wire',wireId]])
    if(!new RegExp('^'+prefix+'_[a-z0-9]+$').test(value || '')) throw new TypeError('Original '+name+' is required');
  const result={expected:{lineage_id:lineageId,event_id:eventId,effect_id:effectId,wire_id:wireId},ready:false,
    edge_ready:false,draft_finished:false,candidates:[],error:null};
  let handles=[],phase='candidates';
  try {
    handles=await panel.elementHandles();phase='sample';
    for(const handle of handles) result.candidates.push(await handle.evaluate((root,{eventId,effectId,wireId})=>{
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
      const inside=n=>canvases.length===1 && n.closest('#hatgCanvas')===canvases[0];
      const ports=[...root.querySelectorAll('button.hatgPort[data-port-node-id]')]
        .filter(n=>[eventId,effectId].includes(n.getAttribute('data-port-node-id')))
        .map(n=>({...describe(n),node_id:n.getAttribute('data-port-node-id'),kind:n.getAttribute('data-port-node-kind'),
          port:n.getAttribute('data-port-id'),type:n.getAttribute('data-port-type'),direction:n.getAttribute('data-port-direction'),
          pressed:n.getAttribute('aria-pressed'),draft_source:n.getAttribute('data-wire-draft-source'),in_canvas:inside(n)}));
      return {root:describe(root),native:root.matches('.portalHatgPanelComponent[role="region"]'),
        identities:[...root.querySelectorAll('#activeLineageIdentity [data-portal-resource-id]')]
          .map(n=>({id:n.getAttribute('data-portal-resource-id'),text:n.textContent,visible:visible(n)})),
        canvases:canvases.map(n=>({...describe(n),draft_type:n.getAttribute('data-wire-draft-type'),
          draft_source_id:n.getAttribute('data-wire-draft-source-id'),draft_source_port:n.getAttribute('data-wire-draft-source-port')})),
        ports,edges:[...root.querySelectorAll('[data-edge-id]')].filter(n=>n.getAttribute('data-edge-id')===wireId)
          .map(n=>({...describe(n),edge_id:n.getAttribute('data-edge-id'),native_button:n.matches('button.edgeLabelBtn'),
            source:n.getAttribute('data-source-id'),target:n.getAttribute('data-target-id'),
            program_wire:n.getAttribute('data-program-wire'),wire_type:n.getAttribute('data-wire-type'),
            source_port:n.getAttribute('data-source-port'),target_port:n.getAttribute('data-target-port'),in_canvas:inside(n)}))};
    },{eventId,effectId,wireId}));
    const c=result.candidates[0],canvas=c?.canvases[0];
    const present=n=>!!n && n.connected && n.busy!=='true' && !n.disabled && n.aria_disabled!=='true';
    const identity=result.candidates.length===1 && c.native && present(c.root) && c.root.visible &&
      c.identities.length===1 && c.identities[0].visible && c.identities[0].id===lineageId && c.identities[0].text===lineageId &&
      c.canvases.length===1 && present(canvas) && canvas.visible;
    const buttons=c?.edges.filter(e=>e.native_button) || [];
    const edge=buttons[0];
    result.edge_ready=identity && buttons.length===1 && present(edge) && edge.visible && edge.in_canvas &&
      edge.program_wire==='true' && edge.wire_type==='route' && edge.source===eventId && edge.target===effectId &&
      edge.source_port==='output' && edge.target_port==='invoke';
    const outputs=c?.ports.filter(p=>p.node_id===eventId && p.port==='output') || [];
    const inputs=c?.ports.filter(p=>p.node_id===effectId && p.port==='invoke') || [];
    const source=outputs[0],target=inputs[0];
    // Native ports become hidden when the draft ends. Presence and explicit
    // false are required, but visibility is not required for these ports.
    result.draft_finished=identity && canvas.draft_type===null && canvas.draft_source_id===null && canvas.draft_source_port===null &&
      outputs.length===1 && inputs.length===1 && present(source) && present(target) && source.in_canvas && target.in_canvas &&
      source.kind==='event_source' && source.direction==='out' && source.type==='route' && source.pressed==='false' && source.draft_source===null &&
      target.kind==='effect' && target.direction==='in' && target.type==='invoke' && target.pressed==='false' && target.draft_source===null;
    result.ready=result.edge_ready && result.draft_finished;
  } catch(error) {
    result.ready=false;result.edge_ready=false;result.draft_finished=false;
    result.error={phase,name:error.name,message:error.message,stack:error.stack || null};
  } finally {for(const handle of handles)await handle.dispose().catch(()=>{});}
  return result;
}

module.exports={readEventEffectConnection};
