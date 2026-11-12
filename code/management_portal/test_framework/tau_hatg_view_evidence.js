'use strict';

// Read or wait for the specified HATG view. The explicit fitAndWait
// operation clicks the native Fit control. Sample recording is optional.
async function readHatgViewSnapshot(panel, {lineageId}) {
  if (!/^lineage_[a-z0-9]+$/.test(lineageId || '')) throw new TypeError('Exact Lineage identity required');
  const candidates = await panel.evaluateAll(roots => roots.map(root => {
    const visible = n => !!n?.isConnected && !n.closest('[hidden],[inert],[aria-hidden="true"]')
      && n.checkVisibility({checkOpacity:true, checkVisibilityCSS:true});
    // The native edges SVG is aria-hidden because accessible labels describe
    // it. That accessibility choice does not hide its painted graph paths.
    const edgeVisible = n => n instanceof SVGGraphicsElement
      ? !!n.isConnected && !n.closest('[hidden],[inert]')
        && n.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})
      : visible(n);
    const box = n => {
      const b = n.getBoundingClientRect();
      return Object.fromEntries(['x','y','width','height'].map(k => [k,Math.round(b[k]*10)/10]));
    };
    const mount = root.closest('.portalHatgPanelComponent') || root;
    const canvas = root.querySelector('#hatgCanvas');
    const bounds = canvas && visible(canvas) ? box(canvas) : null;
    const nodes = [...root.querySelectorAll('[data-node-id]')].filter(visible).map(n => {
      const geometry = box(n);
      return {id:n.getAttribute('data-node-id'), geometry,
        within_canvas:!!bounds && geometry.x >= bounds.x-1 && geometry.y >= bounds.y-1
          && geometry.x+geometry.width <= bounds.x+bounds.width+1
          && geometry.y+geometry.height <= bounds.y+bounds.height+1};
    }).sort((a,b)=>a.id.localeCompare(b.id));
    const edgeGeometry = [...(canvas?.querySelectorAll('path.hatgEdge[data-edge-id],.edgeLabelBtn[data-edge-id]') || [])].filter(edgeVisible)
      .map(n=>({id:n.getAttribute('data-edge-id'),tag:n.tagName,geometry:box(n),path:n.getAttribute('d')}))
      .sort((a,b)=>`${a.id}:${a.tag}`.localeCompare(`${b.id}:${b.tag}`));
    const edges = [...new Set(edgeGeometry.map(n=>n.id))].sort();
    const errors = [...root.querySelectorAll('[role="alert"],#lineageLoadError,#graphSyncStatus[data-state="error"]')]
      .filter(visible).map(n=>({id:n.id, text:n.textContent.trim()}));
    // Overview LOD hides this label while its native state remains attached to
    // the visible node. Hidden text must not turn unknown/closed into open.
    const gates = [...root.querySelectorAll('[data-node-id] [data-execution-gate]')]
      .filter(n=>visible(n.closest('[data-node-id]')))
      .map(n=>({resident_id:n.closest('[data-node-id]').getAttribute('data-node-id'),
        state:n.getAttribute('data-execution-gate'), visible:visible(n), text:n.textContent.trim(), title:n.getAttribute('title')}));
    return {native:root.matches('.portalHatgPanelComponent[role="region"],#lineage-hatg-panel[role="region"],section#hatgPane[data-lineage-pane="hatg"][aria-label]'),
      visible:visible(root), lineage_ids:[...new Set([...root.querySelectorAll('#activeLineageIdentity [data-portal-resource-id]')]
        .filter(visible).map(n=>n.getAttribute('data-portal-resource-id')))],
      busy:mount.inert || mount.getAttribute('aria-busy') === 'true', bounds,
      zoom:canvas?.getAttribute('data-zoom') || null, nodes, edges, edge_geometry:edgeGeometry, errors, execution_gates:gates};
  }));
  const view = candidates.length === 1 ? candidates[0] : null;
  return {observed_at_ms:Date.now(), lineage_id:lineageId, candidates,
    identity_ready:!!view && view.native && view.visible && !view.busy && !!view.bounds
      && view.lineage_ids.length === 1 && view.lineage_ids[0] === lineageId};
}

async function waitForHatgView(panel, {lineageId, nodeIds, edgeIds, record = () => {},
  requireWithinCanvas=false, requireExecutionOpen=false, executionResidentIds=nodeIds,
  timeoutMs=10000, stableMs=350, intervalMs=75}) {
  const validSet = a => Array.isArray(a) && a.every(x=>typeof x==='string' && x) && new Set(a).size===a.length;
  if (!validSet(nodeIds) || !validSet(edgeIds) || !validSet(executionResidentIds)
      || executionResidentIds.some(id=>!nodeIds.includes(id)) || (requireExecutionOpen && !executionResidentIds.length)
      || typeof record !== 'function'
      || !Number.isFinite(timeoutMs) || timeoutMs<=0 || timeoutMs>600000
      || !Number.isFinite(stableMs) || stableMs<100 || stableMs>=timeoutMs
      || !Number.isFinite(intervalMs) || intervalMs<1 || intervalMs>stableMs)
    throw new TypeError('Exact node/edge sets, persistence callback and bounded stable window required');
  const expectedNodes=JSON.stringify([...nodeIds].sort()), expectedEdges=JSON.stringify([...edgeIds].sort());
  const started=Date.now(), deadline=started+timeoutMs;
  let signature=null, stableSince=null, last=null;
  while (Date.now()<deadline) {
    last=await readHatgViewSnapshot(panel,{lineageId});
    const v=last.candidates.length===1 ? last.candidates[0] : null;
    const matches=last.identity_ready && !v.errors.length
      && JSON.stringify(v.nodes.map(n=>n.id).sort())===expectedNodes && JSON.stringify(v.edges)===expectedEdges
      && (!requireWithinCanvas || v.nodes.every(n=>n.within_canvas))
      && (!requireExecutionOpen || v.execution_gates.filter(g=>executionResidentIds.includes(g.resident_id))
        .every(g=>g.state==='open'));
    const next=matches ? JSON.stringify({nodes:v.nodes,bounds:v.bounds,edges:v.edge_geometry,zoom:v.zoom}) : null;
    if (!matches || next!==signature) stableSince=matches ? Date.now() : null;
    signature=next;
    last.stable_for_ms=stableSince===null ? 0 : Date.now()-stableSince;
    last.ready=!!matches && last.stable_for_ms>=stableMs;
    last.elapsed_ms=Date.now()-started;
    try { await record(last); }
    catch (_) { last.evidence_gaps = ['未记录：观察记录写入失败']; }
    if (last.ready && Date.now()<=deadline) return last;
    await new Promise(resolve=>setTimeout(resolve,Math.min(intervalMs,Math.max(0,deadline-Date.now()))));
  }
  const error=new Error('Original HATG view did not settle before its deadline');
  error.code='tau.hatg_view_timeout'; error.snapshot=last; throw error;
}

async function fitAndWaitForHatgView(panel, options) {
  const started=Date.now(), timeoutMs=options.timeoutMs ?? 10000, deadline=started+timeoutMs;
  const persist=phase=>sample=>options.record?.({...sample,phase,original_elapsed_ms:Date.now()-started});
  // Fit before the restored graph mounts can be overwritten by its camera.
  await waitForHatgView(panel,{...options,timeoutMs,requireWithinCanvas:false,record:persist('before-fit')});
  const fit=panel.locator('#graphFitBtn');
  if(await fit.count()!==1)throw new Error('Expected one native HATG Fit control');
  if(deadline-Date.now()<=(options.stableMs??350)) {
    throw Object.assign(new Error('Original HATG Fit budget exhausted'),{code:'tau.hatg_view_timeout'});
  }
  await fit.click({timeout:Math.max(1,deadline-Date.now())});
  const remaining=deadline-Date.now();
  if(remaining<=(options.stableMs??350)) {
    throw Object.assign(new Error('Original HATG Fit budget exhausted'),{code:'tau.hatg_view_timeout'});
  }
  const settled=await waitForHatgView(panel,{...options,timeoutMs:remaining,
    requireWithinCanvas:true,record:persist('after-fit')});
  return {...settled,original_elapsed_ms:Date.now()-started};
}

// Read the formal graph from the original creation window. A range Family
// creates a member Watch before the fixture can be isolated from that Family.
function residentFamilyManagedWireIds(lineage, {residentId}) {
  if (!residentId || !Array.isArray(lineage?.nodes) || !Array.isArray(lineage?.function_blocks)
      || !Array.isArray(lineage?.program?.wires)
      || !lineage.nodes.some(node=>node.id===residentId))
    throw new TypeError('Complete formal Lineage containing the exact Resident required');
  const wires = new Set(lineage.program.wires.map(wire=>wire.id));
  const result = [];
  for (const block of lineage.function_blocks) {
    if (!block.family?.members?.includes(residentId)) continue;
    if (!Array.isArray(block.elements)) throw new Error('Family element membership is missing');
    for (const element of block.elements) {
      if (element.kind!=='wire' || !element.key?.startsWith('watch_')
          || !element.key.endsWith(`:${residentId}`)) continue;
      if (!wires.has(element.id)) throw new Error('Managed Family wire missing from formal graph');
      result.push(element.id);
    }
  }
  return [...new Set(result)].sort();
}

module.exports={readHatgViewSnapshot,waitForHatgView,fitAndWaitForHatgView,residentFamilyManagedWireIds};
