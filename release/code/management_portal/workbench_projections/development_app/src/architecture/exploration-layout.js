import dagre from '@dagrejs/dagre';
import { overviewLayers, readingConnections, relationEmphasized, connectionPresentation, objectReadingNames, readingResponsibilities, compositionLayers } from './exploration-reading.js';
// Layout owns geometry only. Layers are reading groups, not composition parents.
const STEP = 10;
const snap = n => Math.round(n / STEP) * STEP;
const list = value => Array.isArray(value) ? value : [];
export const intersects = (a,b,gap=0) => a.x < b.x+b.width+gap && a.x+a.width+gap > b.x && a.y < b.y+b.height+gap && a.y+a.height+gap > b.y;
const center = r => ({x:r.x+r.width/2,y:r.y+r.height/2});
const distance = (a,b) => Math.abs(a.x-b.x)+Math.abs(a.y-b.y);
export function wrapGraphText(text, width, font = 14) {
  const lines = []; let line = '', used = 0;
  // Conservative glyph widths keep the SVG and HTML labels in the same boxes.
  const glyph = ch => /[^\u0000-\u00ff]/u.test(ch) ? font : font * .62;
  const tokens = String(text || '').match(/[A-Za-z0-9][A-Za-z0-9._:-]*|[^\u0000-\u00ff]|[\s\S]/gu) || [];
  for (const token of tokens) {
    const measure = part => [...part].reduce((sum, ch) => sum + glyph(ch), 0);
    const words = measure(token) > width ? token.split(/(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/u) : [token];
    const parts = words.flatMap(word => measure(word) > width ? [...word] : [word]);
    for (const part of parts) {
      const size = [...part].reduce((sum, ch) => sum + glyph(ch), 0);
      if (part === '\n' || (used + size > width && line)) {
        lines.push(line); line = ''; used = 0;
        if (part === '\n') continue;
      }
      line += part; used += size;
    }
  }
  if (line || !lines.length) lines.push(line);
  return lines;
}

class Heap {
  items = [];
  push(value) {
    let i = this.items.push(value) - 1;
    while (i) { const p = (i - 1) >> 1; if (this.items[p].f <= value.f) break; this.items[i] = this.items[p]; i = p; }
    this.items[i] = value;
  }
  pop() {
    const first = this.items[0], last = this.items.pop();
    if (this.items.length) {
      let i = 0;
      while (i * 2 + 1 < this.items.length) {
        let j = i * 2 + 1;
        if (j + 1 < this.items.length && this.items[j + 1].f < this.items[j].f) j++;
        if (this.items[j].f >= last.f) break;
        this.items[i] = this.items[j]; i = j;
      }
      this.items[i] = last;
    }
    return first;
  }
}

function routeGrid(start, end, blocked, occupied, width, height) {
  const cols = Math.floor(width / STEP) + 1, rows = Math.floor(height / STEP) + 1;
  const sx = Math.round(start.x / STEP), sy = Math.round(start.y / STEP), ex = Math.round(end.x / STEP), ey = Math.round(end.y / STEP);
  const key = (x, y, d) => (y * cols + x) * 4 + d;
  const heap = new Heap(), costs = new Map(), back = new Map();
  const dirs = [[1, 0], [0, 1], [-1, 0], [0, -1]];
  for (let d = 0; d < 4; d++) { const k = key(sx, sy, d); costs.set(k, 0); heap.push({ x: sx, y: sy, d, k, g: 0, f: 0 }); }
  while (heap.items.length) {
    const cur = heap.pop(); if (costs.get(cur.k) !== cur.g) continue;
    if (cur.x === ex && cur.y === ey) {
      const points = [];
      for (let k = cur.k; k != null; k = back.get(k)) {
        const cell = Math.floor(k / 4); points.push({ x: (cell % cols) * STEP, y: Math.floor(cell / cols) * STEP });
      }
      return points.reverse();
    }
    for (let d = 0; d < 4; d++) {
      const x = cur.x + dirs[d][0], y = cur.y + dirs[d][1];
      if (x < 1 || y < 1 || x >= cols - 1 || y >= rows - 1 || blocked.has(`${x},${y}`)) continue;
      const g = cur.g + 1 + (d !== cur.d ? 1.7 : 0) + (occupied.has(`${x},${y}`) ? 7 : 0), k = key(x, y, d);
      if (g >= (costs.get(k) ?? Infinity)) continue;
      costs.set(k, g); back.set(k, cur.k); heap.push({ x, y, d, k, g, f: g + Math.abs(x - ex) + Math.abs(y - ey) });
    }
  }
  return null;
}

function ports(rect, target, offset=0) {
  const x = snap(rect.x + rect.width / 2 + offset), y = snap(rect.y + rect.height / 2 + offset);
  return [
    { point: { x, y: rect.y }, stub: { x, y: rect.y - 10 } },
    { point: { x, y: rect.y + rect.height }, stub: { x, y: rect.y + rect.height + 10 } },
    { point: { x: rect.x, y }, stub: { x: rect.x - 10, y } },
    { point: { x: rect.x + rect.width, y }, stub: { x: rect.x + rect.width + 10, y } },
  ].sort((a, b) => distance(a.stub, target) - distance(b.stub, target));
}

function compact(points) {
  const result = [];
  for (const p of points) {
    const a = result.at(-2), b = result.at(-1);
    if (b && p.x === b.x && p.y === b.y) continue;
    if (a && ((a.x === b.x && b.x === p.x) || (a.y === b.y && b.y === p.y))) result.pop();
    result.push(p);
  }
  return result;
}

function connect(a, b, blocked, occupied, width, height, offset=0) {
  for (const pa of ports(a, center(b),offset)) for (const pb of ports(b, center(a),offset)) {
    if (blocked.has(`${pa.stub.x / STEP},${pa.stub.y / STEP}`) || blocked.has(`${pb.stub.x / STEP},${pb.stub.y / STEP}`)) continue;
    const grid = routeGrid(pa.stub, pb.stub, blocked, occupied, width, height);
    if (!grid) continue;
    for (const p of grid) occupied.add(`${p.x / STEP},${p.y / STEP}`);
    return compact([pa.point, ...grid, pb.point]);
  }
  throw new Error('exploration_relation_route_unavailable');
}


function objectBox(object, role, names, overview, maxWidth=240) {
  const displayTitle = overview ? objectReadingNames[object.id] || object.title : object.title;
  const titleLength = [...displayTitle].reduce((n,c)=>n+(c.charCodeAt(0)>255?16:9),0);
  const width = snap(Math.max(object.kind === 'capability_view' ? 220 : 160, Math.min(maxWidth, titleLength + 34)));
  const title = wrapGraphText(displayTitle, width-24,16);
  const hosting = !overview && list(object.host_ids).length ? wrapGraphText(`承载：${object.host_ids.map(id=>objectReadingNames[id] || names.get(id)?.title || id).join('、')}`,width-24,12) : [];
  const description=overview?wrapGraphText(object.kind === 'capability_view' ? object.responsibility : readingResponsibilities[object.id] || '',width-24,14):[];
  const headerHeight = Math.ceil(((overview?40:56)+title.length*21+hosting.length*17+description.length*18)/10)*10;
  return { id:object.id,object,role,width,height:headerHeight,headerHeight,
    title,description,hosting,x:0,y:0 };
}
function overviewPlacement(boxes, connections, viewportWidth, layers = overviewLayers, gap = 35) {
  const ranks=layers.map((layer)=>({...layer,nodes:layer.ids.map(id=>boxes.find(b=>b.id===id)).filter(Boolean)}));
  const extra=boxes.filter(b=>!layers.some(l=>l.ids.includes(b.id)));
  if(extra.length) ranks.push({title:'其他组成',nodes:extra});
  const rows=ranks.filter(r=>r.nodes.length);
  // Parallel responsibility groups retain their declared reading order.
  // Pixel positions and row heights derive from measured content, not slots.
  const narrow = viewportWidth < 600;
  const width = narrow ? Math.max(320, viewportWidth - 40, ...boxes.map(b => b.width + 40))
    : Math.max(700,viewportWidth-40,...rows.map(r=>r.nodes.reduce((n,b)=>n+b.width,0)+(r.nodes.length-1)*gap+40));
  let y=50;
  const bands=[];
  for(const row of rows) {
    const startY=y, titleLines=wrapGraphText(row.title,width-60,12);
    const isBand=row.band || row.nodes.length>2 || row.title==='发布与工程维护';
    if (isBand) y+=Math.max(0,titleLines.length-1)*16;
    const groups=narrow ? row.nodes.map(node=>[node]) : [row.nodes];
    for (const group of groups) {
      const used=group.reduce((n,b)=>n+b.width,0)+(group.length-1)*gap;
      let x=snap((width-used)/2); const h=Math.max(...group.map(n=>n.height));
      for(const box of group){box.x=x;box.y=y;x+=box.width+gap;}
      y+=h+(narrow && row.memberBand ? 150 : 80);
    }
    if(isBand) {
      bands.push({x:20,y:startY-30,width:width-40,height:y-startY-35,title:row.title,titleLines});
    }
    if(!narrow && row.nodes.length>1)y+=35;
  }
  return {width,height:y-80,bands};
}
function scopePlacement(content, nodes, context, root, connections, viewportWidth) {
  const graph=new dagre.graphlib.Graph({multigraph:true});
  graph.setGraph({rankdir:'TB',nodesep:55,ranksep:140,acyclicer:'greedy'});graph.setDefaultEdgeLabel(()=>({}));
  nodes.forEach(n=>graph.setNode(n.id,{width:n.width,height:n.height}));
  const local=new Set(nodes.map(n=>n.id));
  for(const r of connections) if(local.has(r.from)&&local.has(r.to)) graph.setEdge(r.from,r.to,{weight:1,minlen:1},r.id);
  if(nodes.length) dagre.layout(graph);
  const hints=compositionLayers[content.root?.id] || [], hinted=new Map(hints.flatMap((row,i)=>row.map(id=>[id,i])));
  const hintOrder=new Map(hints.flat().map((id,i)=>[id,i]));
  const rankOf=n=>hinted.has(n.id)?hinted.get(n.id)*10000:100000+graph.node(n.id).y;
  const compactBoundary=context.length<=3, rows=[];
  const portOnly=compactBoundary?[]:context.filter(n=>{
    const incident=connections.filter(r=>r.from===n.id||r.to===n.id);
    return incident.length>0 && incident.every(r=>connectionPresentation(content,r)==='port');
  });
  const adjacent=context.filter(n=>!portOnly.includes(n));
  const available=compactBoundary?Math.max(600,viewportWidth-80):Math.max(300,viewportWidth-360);
  for(const n of [...nodes].sort((a,b)=>rankOf(a)-rankOf(b)||(hintOrder.get(a.id)??0)-(hintOrder.get(b.id)??0)||graph.node(a.id).x-graph.node(b.id).x)) {
    const rank=rankOf(n),last=rows.at(-1);
    if(last && last.rank===rank && last.nodes.reduce((s,n)=>s+n.width+55,0)+n.width<available)last.nodes.push(n);
    else rows.push({rank,nodes:[n]});
  }
  const leftWidth=!compactBoundary && context.length?Math.max(...context.map(n=>n.width)):0;
  const localX=leftWidth?leftWidth+140:40;
  const localWidth=Math.max(root?.width||0,300,...rows.map(r=>r.nodes.reduce((s,n)=>s+n.width+55,0)-55));
  const upstream=compactBoundary?context.filter(n=>connections.some(r=>r.from===n.id)):[];
  const downstream=compactBoundary?context.filter(n=>!upstream.includes(n)):[];
  const topPad=upstream.length?Math.max(...upstream.map(n=>n.height))+110:0;
  let y=40+(root?.height||0)+70+topPad;
  for(const row of rows){let x=localX+(localWidth-row.nodes.reduce((s,n)=>s+n.width+55,0)+55)/2;for(const n of row.nodes){n.x=snap(x);n.y=snap(y);x+=n.width+55;}y+=Math.max(...row.nodes.map(n=>n.height))+140;}
  if(root){root.x=snap(localX+(localWidth-root.width)/2);root.y=40+topPad;}
  const nodeById=new Map([...nodes,...(root?[root]:[])].map(n=>[n.id,n]));
  const anchor=box=>{
    const ys=connections.filter(r=>r.from===box.id||r.to===box.id).map(r=>nodeById.get(r.from===box.id?r.to:r.from)?.y).filter(Number.isFinite);
    return ys.length?ys.reduce((s,n)=>s+n,0)/ys.length:40;
  };
  let outsideY=55;
  for(const box of (compactBoundary?[]:[...adjacent]).sort((a,b)=>anchor(a)-anchor(b)||a.id.localeCompare(b.id))) {
    box.x=20;box.y=snap(Math.max(outsideY,anchor(box)-40));outsideY=box.y+box.height+65;
  }
  if(compactBoundary) {
   let x=40;for(const box of upstream){box.x=x;box.y=30;x+=box.width+70;}
   x=40;for(const box of downstream){box.x=x;box.y=y+20;x+=box.width+70;}
   outsideY=downstream.length?y+Math.max(...downstream.map(n=>n.height))+60:0;
  }
  const bands=[];
  if(portOnly.length){
    const width=localX+localWidth,top=Math.max(y-70,outsideY)+75;
    let groupY=top+35,x=20,rowHeight=0;
    // Port peers need no long physical route. Group the actual objects below
    // the local topology; keep path peers beside the objects they connect to.
    for(const box of portOnly){
      if(x>20 && x+box.width>width){groupY+=rowHeight+45;x=20;rowHeight=0;}
      box.x=x;box.y=groupY;x+=box.width+40;rowHeight=Math.max(rowHeight,box.height);
    }
    outsideY=groupY+rowHeight+30;
    bands.push({x:10,y:top-10,width:width+20,height:outsideY-top+20,title:'跨范围协作 · 沿节点中的目标继续'});
  }
  const rootFrame={x:localX-20,y:15+topPad,width:localWidth+40,height:Math.max(150,y-125-topPad)};
  return {width:localX+localWidth+40,height:Math.max(y-70,outsideY+20),rootFrame,bands,boundaryLabel:context.length&&!compactBoundary?{x:20,y:20}:null};
}
function caption(connection, width=160) {
  const label=connection.readingLabel || connection.label;
  const lines=wrapGraphText(label,width-16,14);
  return {width,height:Math.ceil(Math.max(30,lines.length*18+14)/10)*10,lines};
}
function segmentHits(a,b,r) {
 return a.x===b.x ? a.x>r.x && a.x<r.x+r.width && Math.max(a.y,b.y)>r.y && Math.min(a.y,b.y)<r.y+r.height
 : a.y>r.y && a.y<r.y+r.height && Math.max(a.x,b.x)>r.x && Math.min(a.x,b.x)<r.x+r.width;
}
function cutLabel(points,r) {
 const paths=[];let current=[points[0]];
 for(let i=1;i<points.length;i++) {
  const a=points[i-1],b=points[i];
  if(!segmentHits(a,b,r)){current.push(b);continue;}
  const vertical=a.x===b.x,ascending=vertical?b.y>a.y:b.x>a.x;
  const low=vertical?{x:a.x,y:r.y}:{x:r.x,y:a.y}, high=vertical?{x:a.x,y:r.y+r.height}:{x:r.x+r.width,y:a.y};
  const entry=ascending?low:high,exit=ascending?high:low;
  current.push(entry);if(current.length>1)paths.push(compact(current));current=[exit,b];
 }
 if(current.length>1)paths.push(compact(current));return paths;
}
function routedConnections(connections,boxes,width,height) {
 const byId=new Map(boxes.map(n=>[n.id,n])),blocked=new Set(),occupied=new Set(),placed=[],result=[];
 const block=r=>{for(let x=Math.ceil((r.x-5)/STEP);x<=Math.floor((r.x+r.width+5)/STEP);x++)for(let y=Math.ceil((r.y-5)/STEP);y<=Math.floor((r.y+r.height+5)/STEP);y++)blocked.add(`${x},${y}`);};
 boxes.forEach(block);
 // Nearest layers first keep the spatial skeleton simple; long links route
 // around that skeleton. Caption positions follow routes, never drive them.
 const order=[...connections].sort((a,b)=>distance(center(byId.get(a.from)),center(byId.get(a.to)))-distance(center(byId.get(b.from)),center(byId.get(b.to))));
 for(const e of order) {
  const a=byId.get(e.from),b=byId.get(e.to),reverse=connections.some(r=>r.from===e.to&&r.to===e.from);
  const offset=reverse?(e.from.localeCompare(e.to)>0?10:-10):0;
  const points=connect(a,b,blocked,occupied,width,height,offset),label=caption(e);
  const candidates=[];
  for(let i=1;i<points.length;i++){
   const p=points[i-1],q=points[i],length=distance(p,q);
   for(const fraction of [.5,.25,.75])for(const delta of [0,-35,35,-65,65]) {
    const cx=p.x+(q.x-p.x)*fraction,cy=p.y+(q.y-p.y)*fraction;
    const r={...label,x:snap(cx-label.width/2+(p.x===q.x?delta:0)),y:snap(cy-label.height/2+(p.y===q.y?delta:0))};
    if(r.x<15||r.y<15||r.x+r.width>width-15||r.y+r.height>height-15)continue;
    if([...boxes,...placed].some(v=>intersects(r,v,10)))continue;
    if(result.some(previous=>previous.rawPath.some((point,j)=>j && segmentHits(previous.rawPath[j-1],point,r))))continue;
    candidates.push({r,score:Math.abs(delta)*2+Math.abs(fraction-.5)*20+1/(length+1)});
   }
  }
  candidates.sort((x,y)=>x.score-y.score);
  let labelBox=candidates[0]?.r;
  if(!labelBox) {
   let best=Infinity;
   for(let y=20;y+label.height<height-10;y+=20)for(let x=20;x+label.width<width-10;x+=20){
    const r={...label,x,y};if([...boxes,...placed].some(v=>intersects(r,v,10)))continue;
    if(result.some(previous=>previous.rawPath.some((point,j)=>j && segmentHits(previous.rawPath[j-1],point,r))))continue;
    const score=Math.min(...points.map(point=>distance(center(r),point)));
    if(score<best){best=score;labelBox=r;}
   }
  }
  if(!labelBox)throw new Error('exploration_caption_space_unavailable');
  placed.push(labelBox);block(labelBox);
  result.push({...e,labelBox,rawPath:points,paths:cutLabel(points,labelBox)});
 }
 return result;
}
// The three execution collaborators need explicit directional corridors.
// Geometry is derived from their measured boxes; these are view routes only.
function capabilityCorridors(connections, byId, narrow) {
  return connections.map(edge => {
    const from=byId.get(edge.from),to=byId.get(edge.to);
    const claim=edge.to==='execution', returning=edge.from==='providers';
    const label=caption(edge,narrow && !claim ? 140 : 160);
    let points,labelBox;
    if (narrow) {
      const upper=from.y<to.y?from:to,lower=upper===from?to:from;
      const middle=(upper.y+upper.height+lower.y)/2;
      const x=center(from).x+(claim?0:returning?75:-75);
      const start={x,y:from.y<to.y?from.y+from.height:from.y};
      const end={x,y:from.y<to.y?to.y:to.y+to.height};
      points=[start,end];labelBox={...label,x:x-label.width/2,y:middle-label.height/2};
    } else {
      const above=claim || returning;
      const a={x:center(from).x+(claim?-30:0),y:above?from.y:from.y+from.height};
      const b={x:center(to).x+(returning?30:0),y:above?to.y:to.y+to.height};
      const members=['execution','worker','providers'].map(id=>byId.get(id));
      const y=above?Math.min(...members.map(n=>n.y))-65:Math.max(...members.map(n=>n.y+n.height))+60;
      points=[a,{x:a.x,y},{x:b.x,y},b];labelBox={...label,x:(a.x+b.x-label.width)/2,y:y-label.height/2};
    }
    return {...edge,labelBox,rawPath:points,paths:cutLabel(points,labelBox)};
  });
}
export function layoutExploration(content,{width:viewportWidth=945}={}) {
  content = { ...content, narrowReading: viewportWidth < 600 };
  const children=list(content.children),outside=list(content.boundaryEndpoints);
  const all=[...children,...outside,...(content.root?[content.root]:[])],names=new Map(all.map(o=>[o.id,o]));
  if(names.size!==all.length)throw new TypeError('Duplicate exploration endpoint');
  const portWidth=content.capabilityView && viewportWidth>=600 ? Math.min(220,Math.floor((viewportWidth-120)/3)) : 220;
  const nodes=children.map(o=>objectBox(o,'internal',names,content.overview,content.capabilityView ? portWidth : 240));
  const context=outside.map(o=>objectBox(o,'boundary',names,false,210));
  const root=content.root?objectBox(content.root,'root',names,false):null;
  const connections=readingConnections(content),boxes=[...nodes,...context,...(root?[root]:[])],byId=new Map(boxes.map(b=>[b.id,b]));
  if(!boxes.length)return {reading:true,width:360,height:200,nodes,context,root,edges:[],bands:[]};
  for(const b of boxes)if(connections.some(r=>r.from===b.id&&connectionPresentation(content,r)==='port') && b.width<portWidth){
    b.width=portWidth;b.title=wrapGraphText(objectReadingNames[b.id]||b.object.title,b.width-24,16);
    b.description=content.overview?wrapGraphText(b.object.kind === 'capability_view' ? b.object.responsibility : readingResponsibilities[b.id]||'',b.width-24,14):b.description;
    b.headerHeight=Math.ceil((40+b.title.length*21+b.description.length*18+b.hosting.length*17)/10)*10;b.height=b.headerHeight;
  }
  const ports=[],paths=[];
  for(const r of connections) {
    if(!byId.has(r.from)||!byId.has(r.to))throw new TypeError(`Missing authorized endpoint: ${r.id}`);
    const presentation=connectionPresentation(content,r);
    if(presentation==='path'){paths.push({...r,presentation});continue;}
    const source=byId.get(r.from),target=byId.get(r.to),width=source.width-16;
    const lines=wrapGraphText(`→ ${target.title.join('')}\n${r.readingLabel || r.label}`,width-16,14);
    const labelBox={width,height:Math.ceil((lines.length*18+14)/10)*10,lines,x:0,y:source.height};
    ports.push({...r,presentation,labelBox,paths:[]});source.height+=labelBox.height+6;
  }
  for(const b of boxes)if(b.height>b.headerHeight)b.height=Math.ceil((b.height+10)/10)*10;
  const geometry=content.overview?overviewPlacement(nodes,connections,viewportWidth,content.readingLayers,content.executionGrouped || content.capabilityView ? 20 : 35):scopePlacement(content,nodes,context,root,connections,viewportWidth);
  for(const e of ports){const s=byId.get(e.from);e.labelBox.x=s.x+8;e.labelBox.y+=s.y;}
  const width=snap(geometry.width+10),height=snap(geometry.height+40);
  const primary=content.capabilityView ? paths.filter(e=>['worker>execution','worker>providers','providers>worker'].includes(e.from+'>'+e.to)) : [];
  const corridors=capabilityCorridors(primary,byId,content.narrowReading);
  const edges=routedConnections(paths.filter(e=>!primary.includes(e)),[...boxes,...corridors.map(e=>e.labelBox)],width,height);
  edges.push(...corridors);
  edges.push(...ports);
  const connectionOrder=new Map(connections.map((e,i)=>[e.id,i]));
  edges.sort((a,b)=>connectionOrder.get(a.id)-connectionOrder.get(b.id));
  edges.forEach(e=>{e.emphasized=relationEmphasized(e,content.readingLens,content.readingFocus);});
  return {reading:true,overview:!!content.overview,...geometry,width,height,nodes,context,root,edges};
}
export function initialGraphCamera(layout,viewport) {
  const k=Math.max(.1,Math.min(1,(viewport.width-16)/layout.width,(viewport.height-24)/layout.height));
  return {x:Math.max(0,(viewport.width-layout.width*k)/2),y:12,k};
}

export function readingGraphCamera(layout,viewport) {
  const k=1;
  return {x:Math.max(0,(viewport.width-layout.width*k)/2),y:12,k};
}
