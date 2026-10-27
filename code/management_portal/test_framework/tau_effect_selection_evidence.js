'use strict';

// One read in the original selection action. Persist the result before judging
// ready; a click returning does not mean the native single-click callback ran.
// This helper never clicks, waits, polls, changes selection or opens Inspector.
async function readHatgEffectSelection(panel, {lineageId, effectId} = {}) {
  if (!/^lineage_[a-z0-9]+$/.test(lineageId || '') || !/^effect_[a-z0-9]+$/.test(effectId || ''))
    throw new TypeError('Original lineageId and created effectId are required');
  const result = {expected_lineage_id:lineageId, expected_effect_id:effectId,
    ready:false, candidates:[], error:null};
  let handles = [], phase = 'candidates';
  try {
    handles = await panel.elementHandles();
    phase = 'sample';
    for (const handle of handles) result.candidates.push(await handle.evaluate((root, effectId) => {
      const visible = node => {
        if (!node?.isConnected) return false;
        for (let n=node; n; n=n.parentElement) {
          const style=getComputedStyle(n);
          if (n.hidden || n.inert || n.getAttribute('aria-hidden')==='true' || style.display==='none' ||
              ['hidden','collapse'].includes(style.visibility) || Number(style.opacity)===0) return false;
        }
        const r=node.getBoundingClientRect();return r.width>0 && r.height>0;
      };
      const describe = n => ({tag:n.tagName,id:n.id,role:n.getAttribute('role'),visible:visible(n),
        connected:n.isConnected,busy:n.getAttribute('aria-busy')});
      const shells=[...root.querySelectorAll('main#agentLineageShell')];
      const panes=[...root.querySelectorAll('#hatgPane')];
      const canvases=[...root.querySelectorAll('#hatgCanvas')];
      const identities=[...root.querySelectorAll('#activeLineageIdentity [data-portal-resource-id]')]
        .map(n=>({id:n.getAttribute('data-portal-resource-id'),text:n.textContent,visible:visible(n)}));
      const cards=[...root.querySelectorAll('.hatgNode[data-node-id]')]
        .filter(n=>n.getAttribute('data-node-id')===effectId)
        .map(n=>({...describe(n),node_id:n.getAttribute('data-node-id'),kind:n.getAttribute('data-node-kind'),
          pressed:n.getAttribute('aria-pressed'),disabled:n.matches(':disabled'),aria_disabled:n.getAttribute('aria-disabled'),
          canvas_id:n.closest('#hatgCanvas')?.id || null,
          in_native_canvas:canvases.length===1 && n.closest('#hatgCanvas')===canvases[0],
          text:n.innerText,label:n.getAttribute('aria-label')}));
      return {root:describe(root),native:root.matches('.portalHatgPanelComponent[role="region"]'),
        shells:shells.map(describe),panes:panes.map(describe),canvases:canvases.map(describe),identities,cards};
    },effectId));
    const c=result.candidates[0],card=c?.cards[0];
    const oneReady=nodes=>nodes.length===1 && nodes[0].visible && nodes[0].busy!=='true';
    result.ready=result.candidates.length===1 && c.native && c.root.visible && c.root.busy!=='true' &&
      oneReady(c.shells) && oneReady(c.panes) && oneReady(c.canvases) &&
      c.identities.length===1 && c.identities[0].visible && c.identities[0].id===lineageId &&
      c.identities[0].text===lineageId && c.cards.length===1 && card.tag==='BUTTON' &&
      card.kind==='effect' && card.in_native_canvas && card.visible && !card.disabled &&
      card.aria_disabled!=='true' && card.busy!=='true' && card.pressed==='true';
  } catch (error) {
    result.error={phase,name:error.name,message:error.message,stack:error.stack || null};
  } finally {
    for (const handle of handles) await handle.dispose().catch(()=>{});
  }
  return result;
}

module.exports={readHatgEffectSelection};
