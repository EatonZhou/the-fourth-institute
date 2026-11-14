// Graph semantics and interaction belong to the exploration surface, not its hosts.
import {t} from './frontier_model.js';
let d3Ready;
// Keep words together; CJK characters remain natural break opportunities.
export function wrapNodeTitle(label, measureWidth, width=210) {
  const tokens=String(label ?? '').match(/\s+|[\u2e80-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]|[^\s\u2e80-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+/gu)||[];
  const lines=[[]];
  for(const token of tokens){
    if(/^\s+$/.test(token)){if(lines.at(-1).length)lines.at(-1).push(' ');continue;}
    const pieces=measureWidth(token)>width?Array.from(token):[token];
    for(const piece of pieces){
      const i=lines.length-1;
      if(lines[i].length&&measureWidth(lines[i].join('')+piece)>width)lines.push([piece]);
      else lines[i].push(piece);
    }
  }
  const result=lines.slice(0,2).map(line=>line.join('').trim());
  if(lines.length>2){
    const last=lines[1].slice();
    while(last.length&&measureWidth(last.join('').trim()+'…')>width)last.pop();
    result[1]=last.join('').trim()+'…';
  }
  return result;
}
export function ensureD3() {
  if (window.d3?.zoom) return Promise.resolve(window.d3);
  return d3Ready ||= new Promise((resolve,reject)=>{
    const script=document.createElement('script');script.src='/static/vendor/d3/d3.v7.9.0.min.js';
    script.onload=()=>resolve(window.d3);script.onerror=()=>{d3Ready=null;script.remove();reject(new Error(t('画布引擎加载失败，请刷新。')));};
    document.head.append(script);
  });
}
export const itemKey = r => r._lineage+':'+r.id;
export function graphData(rows,{title,project,map,teams=[]}={}) {
  const nodes=[],edges=[];
  const add=(id,kind,label,x,y,extra={})=>{const node={id,kind,label,x,y,...extra};nodes.push(node);return node;};
  const link=(source,target,kind,label='')=>edges.push({id:source+'|'+target+'|'+kind,source,target,kind,label});
  add('scope','scope',title || t('Map'),0,0,{subtitle:project?t('项目目标'):t('团队探索')});
  const groups=new Map();
  rows.forEach(r=>{const key=r._lineage+'|'+r.line;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);});
  let cursor=0;
  const teamNodes=new Map();
  const directions=project?(map?.directions||[]):[];
  // A team may serve multiple directions. One team node connects to all of them;
  // a seed is never duplicated or represented as a direct direction output.
  for(const d of directions) {
    const index=directions.indexOf(d);
    add(d.direction_id,'direction',d.title,350,index*180,{subtitle:({focus:t('主攻'),explore:t('探索'),watch:t('观望'),candidate:t('候选'),concluded:t('已收束'),dropped:t('放弃')})[d.stance]||t('研究方向'),direction:d});
  }
  for(const d of directions)link(d.parent_id && directions.some(x=>x.direction_id===d.parent_id)?d.parent_id:'scope',d.direction_id,'structure');
  for(const r of map?.relations||[])if(directions.some(d=>d.direction_id===r.from)&&directions.some(d=>d.direction_id===r.to))link(r.from,r.to,r.kind,t('方向关系'));
  const teamIds=project?[...new Set([...rows.map(r=>r._lineage),...directions.flatMap(d=>d.teams)])]:[];
  for(const id of teamIds) {
    const gs=[...groups].filter(([,rs])=>rs[0]._lineage===id);
    const height=Math.max(180,gs.reduce((n,[,rs])=>n+Math.max(180,rs.length*140),0));
    const team=add('team:'+id,'team',teams.find(t=>t.lineage_id===id)?.title || rows.find(r=>r._lineage===id)?._team || id,700,cursor+height/2,{subtitle:gs.length+t(' 条工作线'),lineage:id});
    teamNodes.set(id,team);const assigned=directions.filter(d=>d.teams.includes(id));
    if(assigned.length)assigned.forEach(d=>link(d.direction_id,team.id,'team',t('负责团队')));
    else link('scope',team.id,'team',t('项目团队'));
    cursor+=height+60;
  }
  const otherTeams=teams.filter(t=>!teamIds.includes(t.lineage_id));
  if(project&&otherTeams.length){add('other-teams','teamCollection',otherTeams.length+t(' 个其他团队'),700,cursor,{subtitle:t('当前未展示节点 · 点击浏览'),teams:otherTeams});link('scope','other-teams','team',t('其他团队'));}
  cursor=0;
  for(const [key,rs] of groups) {
    const team=teamNodes.get(rs[0]._lineage);
    const y=cursor+rs.length*70;
    const group=add('line:'+key,'line',rs[0].line,project?1050:340,y,{subtitle:rs.length+t(' 个探索位置'),count:rs.length});
    link(team?.id||'scope',group.id,'structure');
    const depth=(r,seen=new Set())=>{if(!r.parent||seen.has(r.id))return 0;seen.add(r.id);const p=rs.find(x=>x.id===r.parent);return p?Math.min(4,1+depth(p,seen)):0;};
    rs.forEach((r,i)=>{
      add(itemKey(r),'seed',r.title,(project?1400:700)+depth(r)*340,cursor+i*140,{row:r,group:group.id,subtitle:r._status});
      link(group.id,itemKey(r),'membership');
    });
    cursor+=Math.max(180,rs.length*140)+70;
  }
  for(const r of rows)if(r.parent&&rows.some(p=>p._lineage===r._lineage&&p.id===r.parent))link(r._lineage+':'+r.parent,itemKey(r),r.relation,({builds_on:t('发展'),depends_on:t('依赖'),converges:t('汇合')})[r.relation]);
  const bottom=Math.max(cursor,...nodes.map(n=>n.y),0);
  nodes[0].y=bottom/2;
  if(!rows.length && !project) {add('new','new',t('添加第一个 Map 节点'),350,bottom/2,{subtitle:t('从一个问题或判断开始')});link('scope','new','membership');}
  return {nodes,edges};
}

export function createGraph(host,{onSelect,onNew,onDirection,onTeam,onLayout}={}) {
  const d3=window.d3;
  const measure=document.createElement('canvas').getContext('2d');measure.font='600 14px system-ui';
  function wrap(label) {
    return wrapNodeTitle(label,text=>measure.measureText(text).width);
  }
  const svg=d3.select(host).append('svg').attr('class','frontierCanvas').attr('aria-label',t('Map')).attr('tabindex',0);
  const defs=svg.append('defs');
  const marker='frontierArrow-'+crypto.randomUUID();
  defs.append('marker').attr('id',marker).attr('viewBox','0 0 10 10').attr('refX',9).attr('refY',5).attr('markerWidth',6).attr('markerHeight',6).attr('orient','auto').append('path').attr('d','M0 0L10 5L0 10Z').attr('fill','currentColor');
  const world=svg.append('g');const edgeLayer=world.append('g');const nodeLayer=world.append('g');
  const mini=d3.select(host).append('svg').attr('class','frontierMinimap').attr('aria-label',t('全图导航')).attr('viewBox','0 0 170 100');
  let data={nodes:[],edges:[]},positions={},collapsed=new Set(),selection='',focus=false,zoomState=d3.zoomIdentity,undo=[],signature='',alive=true;
  const zoom=d3.zoom().scaleExtent([.12,2.5]).on('zoom',event=>{
    zoomState=event.transform;world.attr('transform',zoomState);svg.attr('data-detail',zoomState.k<.45?'overview':'full');
    host.dispatchEvent(new CustomEvent('graphzoom',{detail:Math.round(zoomState.k*100)}));drawMini();
  });
  svg.call(zoom).on('dblclick.zoom',null).on('click',e=>{if(e.target===svg.node()){selection='';focus=false;paint();}});
  const box=()=>{const ns=data.nodes.filter(n=>!n.hidden);return {x:d3.min(ns,n=>n.x)-145||-145,y:d3.min(ns,n=>n.y)-55||-55,w:(d3.max(ns,n=>n.x)-d3.min(ns,n=>n.x)||0)+290,h:(d3.max(ns,n=>n.y)-d3.min(ns,n=>n.y)||0)+110};};
  function fit(ids) {
    if(!data.nodes.length)return;
    let b=box();if(ids?.length){const ns=data.nodes.filter(n=>ids.includes(n.id));if(ns.length)b={x:d3.min(ns,n=>n.x)-160,y:d3.min(ns,n=>n.y)-70,w:d3.max(ns,n=>n.x)-d3.min(ns,n=>n.x)+320,h:d3.max(ns,n=>n.y)-d3.min(ns,n=>n.y)+140};}
    const w=host.clientWidth||900,h=host.clientHeight||600,k=Math.min(1.1,Math.max(.12,Math.min((w-100)/b.w,(h-100)/b.h)));
    svg.call(zoom.transform,d3.zoomIdentity.translate(w/2-(b.x+b.w/2)*k,h/2-(b.y+b.h/2)*k).scale(k));
  }
  function drawMini() {
    const b=box(),k=Math.min(160/b.w,90/b.h),x=5-b.x*k,y=5-b.y*k;
    mini.selectAll('*').remove();
    const byId=new Map(data.nodes.filter(n=>!n.hidden).map(n=>[n.id,n]));
    mini.selectAll('line').data(data.edges.filter(e=>byId.has(e.source)&&byId.has(e.target))).join('line').attr('x1',e=>byId.get(e.source).x*k+x).attr('y1',e=>byId.get(e.source).y*k+y).attr('x2',e=>byId.get(e.target).x*k+x).attr('y2',e=>byId.get(e.target).y*k+y).attr('stroke','var(--fg-border)');
    mini.selectAll('circle').data(data.nodes.filter(n=>!n.hidden)).join('circle').attr('cx',n=>n.x*k+x).attr('cy',n=>n.y*k+y).attr('r',n=>n.kind==='seed'?2.5:3.5).attr('class',n=>n.kind);
    mini.append('rect').attr('x',(-zoomState.x/zoomState.k)*k+x).attr('y',(-zoomState.y/zoomState.k)*k+y).attr('width',host.clientWidth/zoomState.k*k).attr('height',host.clientHeight/zoomState.k*k).attr('class','miniWindow');
    mini.on('click',event=>{const [mx,my]=d3.pointer(event);svg.call(zoom.transform,d3.zoomIdentity.translate(host.clientWidth/2-(mx-x)/k*zoomState.k,host.clientHeight/2-(my-y)/k*zoomState.k).scale(zoomState.k));});
  }
  function location() {
    nodeLayer.selectAll('.graphNode').attr('transform',n=>`translate(${n.x},${n.y})`);
    edgeLayer.selectAll('path').attr('d',e=>{
      const a=data.nodes.find(n=>n.id===e.source),b=data.nodes.find(n=>n.id===e.target);if(!a||!b)return '';
      if(a.x===b.x)return `M${a.x+125},${a.y} C${a.x+240},${a.y} ${b.x+240},${b.y} ${b.x+125},${b.y}`;
      const sign=b.x>a.x?1:-1,mid=(a.x+b.x)/2;
      return `M${a.x+125*sign},${a.y} C${mid},${a.y} ${mid},${b.y} ${b.x-125*sign},${b.y}`;
    });
    edgeLayer.selectAll('text').attr('x',e=>(data.nodes.find(n=>n.id===e.source).x+data.nodes.find(n=>n.id===e.target).x)/2).attr('y',e=>(data.nodes.find(n=>n.id===e.source).y+data.nodes.find(n=>n.id===e.target).y)/2-8);
    drawMini();
  }
  function activate(n) {
    selection=n.id;paint();
    if(n.kind==='seed')onSelect(n.row);
    else if(n.kind==='new')onNew();
    else if(n.kind==='direction')onDirection?.(n.direction);
    else if(n.kind==='team'||n.kind==='teamCollection')onTeam?.(n);
    else if(n.kind==='line'){collapsed.has(n.id)?collapsed.delete(n.id):collapsed.add(n.id);paint();}
  }
  function paint() {
    const neighbors=new Set([selection]);data.edges.forEach(e=>{if(e.source===selection)neighbors.add(e.target);if(e.target===selection)neighbors.add(e.source);});
    data.nodes.forEach(n=>n.hidden=collapsed.has(n.group));
    const visible=new Set(data.nodes.filter(n=>!n.hidden).map(n=>n.id));
    const edges=data.edges.filter(e=>visible.has(e.source)&&visible.has(e.target));
    edgeLayer.selectAll('path').data(edges,e=>e.id).join('path').attr('class',e=>'graphEdge '+e.kind).attr('marker-end',e=>['builds_on','depends_on','converges'].includes(e.kind)?`url(#${marker})`:null).classed('is-dim',e=>focus&&(!neighbors.has(e.source)||!neighbors.has(e.target)));
    edgeLayer.selectAll('text').data(edges.filter(e=>e.label),e=>e.id).join('text').text(e=>e.label).attr('class','edgeLabel');
    const ns=nodeLayer.selectAll('.graphNode').data(data.nodes.filter(n=>!n.hidden),n=>n.id).join(enter=>{
      const g=enter.append('g').attr('class','graphNode').attr('tabindex',0).attr('role','button');
      g.append('rect').attr('x',-125).attr('y',-46).attr('width',250).attr('height',92).attr('rx',12);
      g.append('circle').attr('class','statusDot').attr('cx',-105).attr('cy',-26).attr('r',4);
      g.append('text').attr('class','nodeMeta').attr('x',-93).attr('y',-22);
      g.append('text').attr('class','nodeTitle').attr('x',-106).attr('y',2);
      g.append('text').attr('class','nodeSub').attr('x',-106).attr('y',31);
      g.append('title');return g;
    });
    ns.attr('data-kind',n=>n.kind).attr('data-id',n=>n.id).attr('aria-label',n=>n.label).attr('aria-pressed',n=>n.id===selection).attr('data-attention',n=>n.row?.attention||'').classed('is-selected',n=>n.id===selection).classed('is-dim',n=>n.dim||(focus&&!neighbors.has(n.id)))
      .on('click',(event,n)=>{event.stopPropagation();activate(n);}).on('keydown',(event,n)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();activate(n);}})
      .call(d3.drag().on('start',(event,n)=>{event.sourceEvent.stopPropagation();undo.push(structuredClone(positions));}).on('drag',(event,n)=>{n.x+=event.dx;n.y+=event.dy;location();}).on('end',(event,n)=>{positions[n.id]={x:n.x,y:n.y};onLayout?.(positions);}));
    ns.select('title').text(n=>n.label+(n.row?'\n'+n.row.next:''));
    ns.select('.nodeMeta').text(n=>n.row?({decision:t('需要决定'),optional:t('可以介入'),explore:t('留待探索')})[n.row.attention]:({scope:t('Map'),line:collapsed.has(n.id)?t('＋ 展开工作线'):t('− 收起工作线'),direction:t('研究方向'),team:t('负责团队'),teamCollection:t('项目团队'),new:t('新节点')})[n.kind]);
    ns.select('.nodeTitle').each(function(n){const el=d3.select(this);el.selectAll('*').remove();wrap(n.label).forEach((line,i)=>el.append('tspan').attr('x',-106).attr('dy',i?17:0).text(line));});
    ns.select('.nodeSub').text(n=>n.row?n.row._status:n.subtitle||'');
    location();
  }
  // A container resize or hide/show must not discard the reader's camera.
  let sized = false;
  const observer=new ResizeObserver(()=>{if(alive&&host.clientWidth&&host.clientHeight){if(!sized){sized=true;fit();}else drawMini();}});observer.observe(host);
  svg.on('keydown',event=>{if(event.target!==svg.node())return;if(event.key==='f')fit();if(event.key==='Escape'){selection='';focus=false;paint();}if(event.key==='+'||event.key==='=')svg.call(zoom.scaleBy,1.2);if(event.key==='-')svg.call(zoom.scaleBy,1/1.2);});
  return {
    update(next,saved) {const nextSignature=next.nodes.map(n=>n.id).join('|');if(saved)positions=saved;data=next;data.nodes.forEach(n=>Object.assign(n,positions[n.id]));paint();if(signature!==nextSignature){signature=nextSignature;fit();}},
    search(query){const q=query.trim().toLowerCase();data.nodes.forEach(n=>n.dim=!!q&&!n.label.toLowerCase().includes(q));paint();if(q)fit(data.nodes.filter(n=>!n.dim).map(n=>n.id));},
    fit, zoom(factor){svg.call(zoom.scaleBy,factor);},focus(){focus=!focus;paint();return focus;},
    undo(){if(!undo.length)return;positions=undo.pop();onLayout?.(positions);return positions;},
    reset(){undo.push(structuredClone(positions));positions={};collapsed.clear();onLayout?.(positions);return positions;},
    select(id){const node=data.nodes.find(n=>n.id===id);if(!node)return false;collapsed.delete(node.group);activate(node);fit([id]);return true;},
    destroy(){alive=false;observer.disconnect();svg.on('.zoom',null).remove();mini.remove();}
  };
}
