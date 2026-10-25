'use strict';

// Original pre-action DOM evidence only. Never fits, scrolls, selects or retries.
async function readWatchboardNodePrerequisites(panel, {residentId} = {}) {
  if (!/^resident_[a-z0-9]+$/.test(residentId || '')) throw new TypeError('Exact Resident ID required');
  if (await panel.count() !== 1) throw new Error('One current HATG panel required');
  return panel.evaluate(async (root, residentId) => {
    if (root.id !== 'lineage-hatg-panel' || root.getAttribute('role') !== 'region')
      throw new Error('Full native HATG region required');
    const doc = root.ownerDocument, win = doc.defaultView;
    const visible = n => !!n?.isConnected && !n.closest('[hidden],[inert],[aria-hidden="true"]')
      && n.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    const box = n => { const r = n.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
    const sample = () => {
      const canvases = root.querySelectorAll('[id="hatgCanvas"]');
      const cards = [...root.querySelectorAll('[data-node-id]')].filter(n => n.dataset.nodeId === residentId);
      const canvas = canvases.length === 1 ? canvases[0] : null, node = cards.length === 1 ? cards[0] : null;
      const bound = !!node && !!canvas && canvas.contains(node);
      const geometry = node ? box(node) : null, canvasGeometry = canvas ? box(canvas) : null;
      const clip = {left:0,top:0,right:win.innerWidth,bottom:win.innerHeight};
      const intersect = (r, x, y) => {
        if (x) {clip.left=Math.max(clip.left,r.x);clip.right=Math.min(clip.right,r.x+r.width);}
        if (y) {clip.top=Math.max(clip.top,r.y);clip.bottom=Math.min(clip.bottom,r.y+r.height);}
      };
      if (canvasGeometry) intersect(canvasGeometry,true,true);
      for (let p=node?.parentElement; p; p=p.parentElement) {
        const s=win.getComputedStyle(p), r=p.getBoundingClientRect();
        const sx=p.offsetWidth ? r.width/p.offsetWidth : 1, sy=p.offsetHeight ? r.height/p.offsetHeight : 1;
        intersect({x:r.x+p.clientLeft*sx,y:r.y+p.clientTop*sy,width:p.clientWidth*sx,height:p.clientHeight*sy},
          /^(auto|scroll|hidden|clip)$/.test(s.overflowX), /^(auto|scroll|hidden|clip)$/.test(s.overflowY));
      }
      const within = !!geometry && geometry.width>0 && geometry.height>0
        && geometry.x>=clip.left && geometry.y>=clip.top
        && geometry.x+geometry.width<=clip.right && geometry.y+geometry.height<=clip.bottom;
      const hit = geometry ? doc.elementFromPoint(geometry.x+geometry.width/2,geometry.y+geometry.height/2) : null;
      const centerHit = !!node && !!hit && (node===hit || node.contains(hit));
      const ancestors=[]; for(let n=node; n; n=n.parentElement) ancestors.push(n);
      const animations = ancestors.flatMap(n=>n.getAnimations()).filter(a=>a.pending || a.playState==='running');
      return {time_origin_ms:win.performance.timeOrigin,at_ms:win.performance.now(),
        resident_id:residentId,panel_visible:visible(root),canvas_count:canvases.length,node_count:cards.length,
        bound_to_canvas:bound,node_visible:visible(node),geometry,canvas_geometry:canvasGeometry,clip,
        within_clip:within,center_hit:centerHit,active_animations:animations.length};
    };
    const first=sample(); await new Promise(r=>win.requestAnimationFrame(()=>win.requestAnimationFrame(r)));
    const second=sample();
    const stable = ['geometry','canvas_geometry','clip'].every(k=>first[k] && second[k]
      && Object.keys(first[k]).every(p=>Math.abs(first[k][p]-second[k][p])<0.25));
    const usable=s=>s.panel_visible&&s.node_visible&&s.bound_to_canvas&&s.within_clip&&s.center_hit
      &&s.node_count===1&&s.canvas_count===1&&s.active_animations===0;
    return {resident_id:residentId,first,second,stable,ready:stable&&usable(first)&&usable(second)};
  },residentId);
}

module.exports={readWatchboardNodePrerequisites};
