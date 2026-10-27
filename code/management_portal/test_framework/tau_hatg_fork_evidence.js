'use strict';

// One passive original-window snapshot. No selection, zoom, request, click or
// retry. Caller saves all fields before judging readiness, and independently
// verifies stability within its original bounded action before clicking once.
async function readHatgForkPrerequisites(panel, {residentId} = {}) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '')) throw new TypeError('Exact source Resident ID required');
  if (await panel.count() !== 1) throw new Error('One original HATG region required');
  return panel.evaluate((root, residentId) => {
    if (root.id !== 'lineage-hatg-panel' || root.getAttribute('role') !== 'region')
      throw new Error('Full native HATG region required');
    const doc=root.ownerDocument, view=doc.defaultView;
    const visible=n=>!!n?.isConnected && !n.closest('[hidden],[inert],[aria-hidden="true"]')
      && n.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    const box=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
    const canvases=[...root.querySelectorAll('[id="hatgCanvas"]')];
    const cards=[...root.querySelectorAll('[data-node-id]')].filter(n=>n.dataset.nodeId===residentId);
    const controls=[...root.querySelectorAll('button[data-node-quick-fork]')]
      .filter(n=>n.dataset.sourceNodeId===residentId);
    const canvas=canvases.length===1?canvases[0]:null;
    const candidates=controls.map(button=>{
      const geometry=box(button),clip={left:0,top:0,right:view.innerWidth,bottom:view.innerHeight};
      const intersect=(r,x,y)=>{
        if(x){clip.left=Math.max(clip.left,r.x);clip.right=Math.min(clip.right,r.x+r.width);}
        if(y){clip.top=Math.max(clip.top,r.y);clip.bottom=Math.min(clip.bottom,r.y+r.height);}
      };
      if(canvas)intersect(box(canvas),true,true);
      let activeAnimations=0;
      for(let n=button;n;n=n.parentElement){
        activeAnimations+=n.getAnimations().filter(a=>a.pending||a.playState==='running').length;
        const s=view.getComputedStyle(n),r=box(n);
        const sx=n.offsetWidth?r.width/n.offsetWidth:1,sy=n.offsetHeight?r.height/n.offsetHeight:1;
        if(n!==button)intersect({x:r.x+n.clientLeft*sx,y:r.y+n.clientTop*sy,width:n.clientWidth*sx,height:n.clientHeight*sy},
          /^(auto|scroll|hidden|clip)$/.test(s.overflowX),/^(auto|scroll|hidden|clip)$/.test(s.overflowY));
      }
      const hit=doc.elementFromPoint(geometry.x+geometry.width/2,geometry.y+geometry.height/2);
      return {source_id:button.dataset.sourceNodeId,geometry,clip,visible:visible(button),
        enabled:!button.disabled&&button.getAttribute('aria-disabled')!=='true',
        busy:button.getAttribute('aria-busy'),bound_to_canvas:!!canvas&&canvas.contains(button),
        // A visible subpixel square can pass elementFromPoint but has no
        // usable pointer quad. Do not treat either predicate alone as ready.
        has_pointer_area:geometry.width>=1&&geometry.height>=1,
        within_clip:geometry.x>=clip.left&&geometry.y>=clip.top&&geometry.x+geometry.width<=clip.right&&geometry.y+geometry.height<=clip.bottom,
        center_hit:!!hit&&(hit===button||button.contains(hit)),active_animations:activeAnimations};
    });
    const checks={panel_visible:visible(root),canvas_unique:canvases.length===1,source_unique:cards.length===1,
      source_visible:cards.length===1&&visible(cards[0]),source_bound:cards.length===1&&!!canvas&&canvas.contains(cards[0]),
      control_unique:controls.length===1};
    return {resident_id:residentId,zoom:canvas?.getAttribute('data-zoom')??null,
      semantic_zoom:canvas?.getAttribute('data-semantic-zoom')??null,checks,candidates,
      ready:Object.values(checks).every(Boolean)&&candidates.every(c=>c.visible&&c.enabled&&c.busy==='false'
        &&c.bound_to_canvas&&c.has_pointer_area&&c.within_clip&&c.center_hit&&c.active_animations===0)};
  },residentId);
}

module.exports={readHatgForkPrerequisites};
