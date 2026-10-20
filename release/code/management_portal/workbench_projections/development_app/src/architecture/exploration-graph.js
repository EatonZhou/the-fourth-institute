import { select, zoom, zoomIdentity } from 'd3';
import { layoutExploration, initialGraphCamera, readingGraphCamera } from './exploration-layout.js';
import './exploration-graph.css';
import { readingProfile, readingConnections, relationEmphasized, executionCapability } from './exploration-reading.js';

let instanceSequence = 0;
const NS = 'http://www.w3.org/2000/svg';
const items = value => Array.isArray(value) ? value : [];
const clone = value => structuredClone(value);

/** Render only the authorized projection supplied by the parent view host. */
export function createExplorationGraph(root, { subject, content, ports = {}, signal, initialState = null }) {
  const doc = root.ownerDocument, win = doc.defaultView, instanceId = ++instanceSequence;
  let disposed = false, hidden = false, layout = null, camera = { x: 0, y: 0, k: 1 };
  let cameraMode = 'reading', readingLens = initialState?.readingLens || 'all', readingFocus = initialState?.readingFocus || null;
  const profile = readingProfile(content);
  let selected = null, focused = null, pendingClick = null, pendingState = initialState, laidOutWidth = 0;
  const cleanups = [];
  const shell = doc.createElement('section'); shell.className = 'arch-exploration-graph';
  shell.classList.toggle('aeg-overview', !!content.overview); shell.classList.toggle('aeg-reading', !!profile);
  shell.setAttribute('aria-label', `${content.root?.title || '系统'}内部组成与关系`);
  const toolbar = doc.createElement('div'); toolbar.className = 'aeg-toolbar';
  const viewport = doc.createElement('div'); viewport.className = 'aeg-viewport';
  const svg = doc.createElementNS(NS, 'svg'); svg.setAttribute('role', 'group'); svg.setAttribute('aria-label', '可缩放关系图');
  const defs = doc.createElementNS(NS, 'defs');
  const marker = doc.createElementNS(NS, 'marker'); marker.id = `aeg-arrow-${instanceId}`;
  Object.entries({ viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' }).forEach(([k, v]) => marker.setAttribute(k, v));
  const arrow = doc.createElementNS(NS, 'path'); arrow.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z'); marker.append(arrow); defs.append(marker); svg.append(defs);
  const world = doc.createElementNS(NS, 'g'); world.classList.add('aeg-world'); svg.append(world);
  const inspector = doc.createElement('aside'); inspector.className = 'aeg-inspector'; inspector.hidden = true;
  const announcer = doc.createElement('span'); announcer.className = 'aeg-sr'; announcer.setAttribute('aria-live', 'polite');
  viewport.append(svg); shell.append(toolbar, viewport, inspector, announcer); root.append(shell);
  const objects = new Map([content.root, ...items(content.children), ...items(content.boundaryEndpoints), ...items(content.protocols)].filter(Boolean).map(o => [o.id, o]));
  const objectFor = value => objects.get(value?.viewId === executionCapability.id ? executionCapability.nodeId : value?.objectId);
  const valueFor = object => object.viewId ? { viewId: object.viewId, memberIds: object.viewMembers.map(o => o.id) } : { objectId: object.id };
  const relatedIds = new Set(items(content.relatedObjectIds).filter(id => objects.has(id)));
  const originalGroups = [...items(content.internalRelations), ...items(content.boundaryRelations)];
  const connections = readingConnections(content);
  const relations = new Map(originalGroups.map(r => [r.id,r]));
  connections.forEach(r=>relations.set(r.id,r));
  const connectionFor = id => connections.find(r=>r.memberIds.includes(id));
  const mechanisms = new Map([...items(content.mechanisms), ...items(content.mechanismEntries)].map(m => [m.id, m]));
  const evidence = new Map(items(content.evidence).map(e => [e.id, e]));
  const allowed = () => !disposed && !hidden && !signal?.aborted;
  const emit = (name, value) => { if (allowed()) ports[name]?.(value); };
  const listen = (el, type, fn, options) => { el.addEventListener(type, fn, options); cleanups.push(() => el.removeEventListener(type, fn, options)); };
  const element = (tag, attrs = {}) => { const el = doc.createElementNS(NS, tag); for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value); return el; };
  function button(label, run, target = toolbar) {
    const b = doc.createElement('button'); b.type = 'button'; b.textContent = label;
    b.addEventListener('click', run); target.append(b); return b;
  }
  function textLines(parent, lines, x, y, step, className) {
    const text = element('text', { x, y, class: className });
    lines.forEach((line, i) => { const span = element('tspan', { x, dy: i ? step : 0 }); span.textContent = line; text.append(span); }); parent.append(text);
  }
  const zoomBehavior = zoom().scaleExtent([profile ? .1 : .45, 3.5]).extent(() => [[0, viewport.scrollTop], [viewport.clientWidth, viewport.scrollTop + viewport.clientHeight]])
    .wheelDelta(event => -event.deltaY * (event.deltaMode === 1 ? .05 : event.deltaMode === 2 ? 1 : .002))
    .filter(event => allowed() && (event.type === 'wheel'
      ? event.ctrlKey || event.metaKey
      : !event.button && !event.ctrlKey && !event.target.closest?.('.aeg-object,.aeg-edge-label,.aeg-reading .aeg-edge')))
    .on('zoom', event => {
      if (event.sourceEvent) cameraMode = 'manual';
      camera = { x: event.transform.x, y: event.transform.y, k: event.transform.k };
      applyTransform(); emit('change', getViewState());
    });
  select(svg).call(zoomBehavior).on('dblclick.zoom', null);
  function applyTransform() {
    svg.style.width = `${Math.max(viewport.clientWidth, (layout?.width || 0) * camera.k + Math.max(0, camera.x) + 16)}px`;
    world.setAttribute('transform', `translate(${camera.x} ${camera.y}) scale(${camera.k})`);
    svg.style.height = `${Math.max(viewport.clientHeight, (layout?.height || 0) * camera.k + Math.max(0, camera.y) + 16)}px`;
  }
  function setCamera(next) {
    if (!next || !['x', 'y', 'k'].every(k => Number.isFinite(next[k]))) return;
    select(svg).call(zoomBehavior.transform, zoomIdentity.translate(next.x, next.y).scale(Math.min(3.5, Math.max(profile ? .1 : .45, next.k))));
  }
  function fit(resetScroll = true) {
    if (!layout) return;
    cameraMode = 'fit';
    if (resetScroll) { viewport.scrollTop = 0; viewport.scrollLeft = 0; }
    setCamera(initialGraphCamera(layout, { width: viewport.clientWidth, height: viewport.clientHeight }));
  }
  function scale(factor) {
    cameraMode = 'manual';
    select(svg).call(zoomBehavior.scaleBy, factor, [viewport.clientWidth / 2, viewport.scrollTop + viewport.clientHeight / 2]);
  }
  toolbar.setAttribute('aria-label', '关系图视图控件');
  const toolbarMenus = []; let updateThemeLabel = () => {};
  function toolbarMenu(label, name) {
    const details = doc.createElement('details'); details.className = 'aeg-menu';
    const summary = doc.createElement('summary'); summary.textContent = label; summary.setAttribute('aria-label', name);
    const body = doc.createElement('div'); body.className = 'aeg-menu-body';
    details.append(summary, body); toolbar.append(details); toolbarMenus.push(details);
    listen(details, 'keydown', event => { if (event.key === 'Escape') { details.open = false; summary.focus(); event.stopPropagation(); } });
    listen(details, 'click', event => { if (event.target.closest('button')) details.open = false; });
    return { details, summary, body };
  }
  listen(doc, 'click', event => { for (const menu of toolbarMenus) if (!menu.contains(event.target)) menu.open = false; });
  const zoomControls = doc.createElement('div'); zoomControls.className = 'aeg-zoom-controls'; toolbar.append(zoomControls);
  button('−', () => scale(1 / 1.2), zoomControls).setAttribute('aria-label', '缩小关系图');
  button('+', () => scale(1.2), zoomControls).setAttribute('aria-label', '放大关系图');
  const viewMenu = toolbarMenu('视图', '关系图视图');
  button('适配全图', () => fit(), viewMenu.body);
  button('阅读大小',()=>{cameraMode='reading';viewport.scrollTop=0;viewport.scrollLeft=0;setCamera(readingGraphCamera(layout,{width:viewport.clientWidth,height:viewport.clientHeight}));}, viewMenu.body);
  const hint = doc.createElement('p'); hint.className = 'aeg-hint'; hint.textContent = '拖动平移 · 滚动继续 · Ctrl+滚轮缩放 · Enter 选择 · Shift+Enter 进入'; viewMenu.body.append(hint);
  if (content.overview) {
    hint.textContent = '连线汇总模块间的真实交互 · 选线看协议与依据 · 双击模块深入';
    const menu = doc.createElement('select'); menu.className = 'aeg-protocol-select'; menu.setAttribute('aria-label', '打开协议');
    const placeholder = doc.createElement('option'); placeholder.value = ''; placeholder.textContent = '协议'; menu.append(placeholder);
    for (const protocol of content.protocols) { const option = doc.createElement('option'); option.value = protocol.id; option.textContent = protocol.title; menu.append(option); }
    menu.addEventListener('change', () => { if (menu.value) emitNavigate({kind:'protocol',objectId:menu.value,mechanismId:null}); menu.value = ''; }); toolbar.append(menu);
  }
  if (profile) {
    hint.textContent = profile.explanation;
    const themeMenu = toolbarMenu('主题', '关系主题');
    themeMenu.details.classList.add('aeg-theme-menu');
    const themeValue = doc.createElement('span'); themeValue.className = 'aeg-theme-value'; themeMenu.summary.append(themeValue);
    const nav = doc.createElement('nav'); nav.className='aeg-reading-nav'; nav.setAttribute('aria-label','关系主题'); themeMenu.body.append(nav);
    const themes = [['all','整体'],['main','请求与执行'],['data','资源与持久化'],['ops','通知与维护']];
    for (const [id,label] of themes) {
      const b=button(label,()=>{readingLens=id;readingFocus=null;selected=null;inspector.hidden=true;highlight();emit('change',getViewState());},nav);
      b.dataset.readingLens=id;
    }
    const relationsButton = button(`关系`,()=>{
      inspector.replaceChildren();inspector.hidden=false;button('关闭说明',closeInspector,inspector);
      for (const r of originalGroups) button(`${objects.get(r.from)?.title} → ${objects.get(r.to)?.title} · ${r.label}`,()=>{
        readingLens='relation';readingFocus=r.id;choose({relationId:r.id});
      },inspector);
      for (const local of items(content.localRelations)) button(`对象内部：${local.relation.label}`,()=>choose({objectId:local.objectId}),inspector);
    });
    relationsButton.className = 'aeg-relations-button'; relationsButton.setAttribute('aria-label', `全部关系（${originalGroups.length}）`);
    const count = doc.createElement('span'); count.className = 'aeg-relation-count'; count.textContent = originalGroups.length; relationsButton.append(count);
    updateThemeLabel = () => { themeValue.textContent = themes.find(([id]) => id === readingLens)?.[1] || '所选关系'; };
    updateThemeLabel();
  }
  const relatedLegend = doc.createElement('span'); relatedLegend.className = 'aeg-related-legend'; relatedLegend.id = `aeg-related-${instanceId}`;
  relatedLegend.textContent = '浅金底色：当前方向关联'; relatedLegend.hidden = true; viewMenu.body.append(relatedLegend);

  function cancelClick() { if (pendingClick !== null) win.clearTimeout(pendingClick); pendingClick = null; }
  function emitNavigate(route) { cancelClick(); emit('navigate', route); }
  function ownMechanisms(object) { return items(object.mechanism_ids).map(id => mechanisms.get(id)).filter(Boolean); }
  function entry(object) {
    if (!object || !allowed()) return;
    if (object.viewId) return emitNavigate({ kind: 'capability', viewId: object.viewId, objectId: null, mechanismId: null });
    if (object.id === content.root?.id) return choose({ objectId: object.id });
    if (object.kind === 'protocol') return emitNavigate({ kind: 'protocol', objectId: object.id, mechanismId: null });
    if (items(object.child_ids).length) return emitNavigate({ kind: 'composition', objectId: object.id, mechanismId: null });
    const choices = ownMechanisms(object);
    if (choices.length === 1) return emitNavigate({ kind: 'mechanism', objectId: object.id, mechanismId: choices[0].id });
    if (choices.length > 1) return emitNavigate({ kind: 'composition', objectId: object.id, mechanismId: null });
    choose({ objectId: object.id });
  }
  function highlight() {
    updateThemeLabel();
    toolbar.querySelectorAll('[data-reading-lens]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.readingLens===readingLens));
    const relation = selected?.relationId && relations.get(selected.relationId);
    const endpoints = new Set(relation ? [relation.from, relation.to] : []);
    let hasRelated = false;
    world.querySelectorAll('[data-object-id],[data-view-id]').forEach(el => {
      const related = relatedIds.has(el.dataset.objectId);
      el.classList.toggle('is-related', related);
      if (related) { el.dataset.related = 'true'; el.setAttribute('aria-describedby', relatedLegend.id); hasRelated = true; }
      else { delete el.dataset.related; el.removeAttribute('aria-describedby'); }
      el.classList.toggle('is-selected', (el.dataset.viewId ? el.dataset.viewId === selected?.viewId : el.dataset.objectId === selected?.objectId));
      el.classList.toggle('is-endpoint', endpoints.has(el.dataset.viewId ? executionCapability.nodeId : el.dataset.objectId));
    });
    relatedLegend.hidden = !hasRelated;
    world.querySelectorAll('[data-relation-id]').forEach(el => {
      const edge = connectionFor(el.dataset.relationId) || relations.get(el.dataset.relationId);
      const incident = objectFor(selected) && [edge?.from, edge?.to].includes(objectFor(selected).id);
      el.classList.toggle('is-selected', edge?.memberIds?.includes(selected?.relationId) || el.dataset.relationId === selected?.relationId);
      el.classList.toggle('is-incident', !!incident);
      el.classList.toggle('is-muted', !!selected && !incident && !edge?.memberIds?.includes(selected.relationId));
      el.classList.toggle('is-emphasized',!!edge && relationEmphasized(edge,readingLens,readingFocus));
    });
  }
  function sourceButtons(ids, target) {
    for (const id of items(ids)) {
      const record = evidence.get(id); if (!record) continue;
      button(`依据：${record.claim || record.symbol || id}`, () => emit('source', id), target).classList.add('aeg-source');
    }
  }
  function closeInspector({ restore = true } = {}) {
    cancelClick(); inspector.hidden = true;
    if (selected) focused = { ...selected };
    if (restore) restoreFocus();
    emit('change', getViewState());
  }
  function inspect() {
    inspector.replaceChildren();
    const object = objectFor(selected), relation = relations.get(selected?.relationId);
    inspector.hidden = !object && !relation;
    if (inspector.hidden) return;
    button('关闭说明', closeInspector, inspector);
    const h = doc.createElement('h3'); h.textContent = object?.title || relation.label; inspector.append(h);
    const p = doc.createElement('p'); p.textContent = object?.responsibility || `${objects.get(relation.from)?.title || relation.from} → ${objects.get(relation.to)?.title || relation.to}`; inspector.append(p);
    if (object?.viewId) {
      button('进入模型执行协作', () => entry(object), inspector);
      const note = doc.createElement('p'); note.textContent = '能力视图：三个成员保留各自身份、组成和部署。内部领取、会话及事件关系在深入后展开。'; inspector.append(note);
      for (const member of object.viewMembers) button(`探索：${member.title}`, () => emitNavigate({ kind: 'composition', objectId: member.id, mechanismId: null }), inspector);
      return;
    }
    if (object) {
      if (object.kind === 'protocol') button('打开协议工作台', () => entry(object), inspector);
      else if (items(object.child_ids).length && object.id !== content.root?.id) button('进入内部组成', () => entry(object), inspector);
      for (const mechanism of ownMechanisms(object)) {
        const b = button(mechanism.title, () => emitNavigate({ kind: 'mechanism', objectId: object.id, mechanismId: mechanism.id }), inspector); b.dataset.mechanismId = mechanism.id;
      }
      for (const id of items(object.protocol_ids)) {
        const protocol = objects.get(id); if (protocol) button(`协议：${protocol.title}`, () => entry(protocol), inspector);
      }
      if (profile) button('查看相邻协作',()=>{readingLens='focus';readingFocus=object.id;inspector.hidden=true;highlight();emit('change',getViewState());},inspector);
      for (const local of items(content.localRelations).filter(v=>v.objectId===object.id)) {
        originalDetails(local.relation, inspector);
      }
      sourceButtons(object.evidence_ids, inspector);
    } else {
      if (relation.summary) {
        for (const id of relation.protocol_ids || []) {const p=objects.get(id);if(p)button(`打开协议：${p.title}`,()=>entry(p),inspector);}
        const explain = doc.createElement('p'); explain.textContent = `模块间交互汇总 · ${relation.summary.relations.length} 条具体关系。箭头保留声明方向；各条交互不表示一个连续调用流程。`; inspector.append(explain);
        for (const original of relation.summary.relations) {
          originalDetails(original, inspector);
        }
        return;
      }
      const contract = relation.contract || {};
      const labels = { operation: '操作', transport: '传递方式', input: '输入', output: '结果', preconditions: '前提', state_effects: '状态变化', errors: '失败处理', ordering: '顺序约束' };
      for (const key of ['operation', 'transport', 'input', 'output', 'preconditions', 'state_effects', 'errors', 'ordering']) {
        if (contract[key] == null || contract[key] === '') continue;
        const line = doc.createElement('p'); line.textContent = `${labels[key]}：${typeof contract[key] === 'string' ? contract[key] : JSON.stringify(contract[key])}`; inspector.append(line);
      }
      const protocol = objects.get(relation.protocol_id);
      if (protocol) button(`打开协议：${protocol.title}`, () => entry(protocol), inspector);
      sourceButtons(relation.evidence_ids, inspector);
    }
  }
  function originalDetails(original, parent) {
    const item=doc.createElement('section');parent.append(item);
    const title=doc.createElement('h4');title.textContent=original.label;item.append(title);
    const endpoints=doc.createElement('p');endpoints.textContent=`${original.from} → ${original.to} · ${original.kind} · ${original.id}`;item.append(endpoints);
    const labels={operation:'操作',transport:'传递方式',input:'输入',output:'结果',preconditions:'前提',state_effects:'状态变化',errors:'失败处理',ordering:'顺序约束',result_flow:'结果消费'};
    for(const [key,value] of Object.entries(original.contract || {})) {
      const line=doc.createElement('p');line.textContent=`${labels[key] || key}：${typeof value==='string'?value:JSON.stringify(value)}`;item.append(line);
    }
    for(const id of new Set([original.from,original.to])) button(`定位：${id}`,()=>emitNavigate({kind:'composition',objectId:id,mechanismId:null}),item);
    const protocol=objects.get(original.protocol_id);
    if(protocol) button(`打开协议：${protocol.title}`,()=>entry(protocol),item);
    sourceButtons(original.evidence_ids,item);
  }
  function choose(value) {
    if (!allowed() || (value.viewId ? !objectFor(value)?.viewId : value.objectId ? !objects.has(value.objectId) : !relations.has(value.relationId))) return;
    cancelClick(); selected = { ...value }; highlight(); inspect(); emit('select', selected); emit('change', getViewState());
  }
  function keyFor(el) { if (el.dataset.viewId) return valueFor(objects.get(executionCapability.nodeId)); return el.dataset.objectId ? { objectId: el.dataset.objectId } : { relationId: el.dataset.relationId }; }
  function focusable(el, value) {
    el.setAttribute('tabindex', '0'); el.setAttribute('role', 'button');
    el.addEventListener('focus', () => { focused = { ...value }; });
    el.addEventListener('click', event => {
      event.stopPropagation(); if (!allowed() || event.detail > 1) return;
      cancelClick(); pendingClick = win.setTimeout(() => { pendingClick = null; choose(value); }, 220);
    });
    el.addEventListener('dblclick', event => {
      event.stopPropagation(); cancelClick();
      if (!allowed()) return;
      if (value.objectId || value.viewId) entry(objectFor(value));
      else { const r = relations.get(value.relationId); if (r?.protocol_id && objects.has(r.protocol_id)) entry(objects.get(r.protocol_id)); else choose(value); }
    });
  }
  function restoreFocus() {
    if (!focused) return;
    const el = [...world.querySelectorAll('.aeg-object,.aeg-edge-label,.aeg-reading .aeg-edge')].find(e => focused?.viewId ? e.dataset.viewId === focused.viewId : focused?.objectId ? e.dataset.objectId === focused.objectId : e.dataset.relationId === (connectionFor(focused?.relationId)?.id || focused?.relationId));
    el?.focus({ preventScroll: true });
  }
  function render() {
    if (disposed || hidden) return;
    const width = Math.max(360, viewport.clientWidth || root.clientWidth || 625);
    if (layout && width === laidOutWidth) return;
    // A remembered graph node is view state, not permission to take keyboard
    // focus from Chat (or another frame). Preserve only focus we actually own
    // before replacing the nodes; an inactive iframe retains its activeElement.
    const restoreKeyboardFocus = doc.hasFocus() && world.contains(doc.activeElement);
    laidOutWidth = width; layout = layoutExploration({ ...content, readingLens, readingFocus }, { width });
    toolbar.querySelectorAll('[data-reading-lens]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.readingLens===readingLens));
    world.replaceChildren(); svg.setAttribute('width', '100%');
    for(const band of layout.bands || []) {
      world.append(element('rect',{x:band.x,y:band.y,width:band.width,height:band.height,rx:12,class:'aeg-layer-band'}));
      textLines(world,band.titleLines || [band.title],band.x+10,band.y+17,16,'aeg-layer-heading');
    }
    if (layout.rootFrame) world.append(element('rect', { ...layout.rootFrame, rx: 18, class: 'aeg-scope-frame' }));
    if (layout.boundaryLabel) textLines(world, ['范围外协作者 · 保留原有组成归属'], layout.boundaryLabel.x, layout.boundaryLabel.y, 16, 'aeg-boundary-heading');
    for (const edge of layout.edges) {
      const group = element('g', { class: `aeg-edge aeg-edge-${edge.kind}`, 'data-relation-id': edge.id });
      edge.paths.forEach((points, i) => {
        const attrs = { d: points.map((p, j) => `${j ? 'L' : 'M'}${p.x},${p.y}`).join(' '), class: 'aeg-edge-path' };
        if (i === edge.paths.length - 1) attrs['marker-end'] = `url(#aeg-arrow-${instanceId})`;
        group.append(element('path', attrs));
      });
      if (profile) { group.setAttribute('aria-label', `${edge.label}：${edge.summary?.relations.length || 1}条交互`); focusable(group, {relationId:edge.id}); }
      world.append(group);
    }
    for (const box of [layout.root, ...layout.nodes, ...layout.context].filter(Boolean)) {
      const group = element('g', { class: `aeg-object aeg-object-${box.role}`, transform: `translate(${box.x},${box.y})`, 'data-object-id': box.id, 'aria-label': `${box.object.title}；${box.object.responsibility || ''}` });
      if (box.object.viewId) { group.removeAttribute('data-object-id'); group.dataset.viewId = box.object.viewId; group.classList.add('aeg-capability'); }
      group.append(element('rect', { width: box.width, height: box.height, rx: 10 }));
      let y = 24; textLines(group, box.title, 12, y, 21, 'aeg-object-title'); y += box.title.length * 21 + 3;
      textLines(group, box.description, 12, y, 18, 'aeg-object-description'); y += box.description.length * 18 + 3;
      if (box.hosting.length) { textLines(group, box.hosting, 12, y, 17, 'aeg-object-host'); y += box.hosting.length * 17; }
      const hasChildren = items(box.object.child_ids).length > 0, hasMechanisms = ownMechanisms(box.object).length > 0;
      const entryLabel = box.object.viewId ? '进入执行协作 ↗' : box.role === 'root' ? `当前范围 · 选择查看${hasMechanisms ? '机制' : '说明'}` : hasChildren ? '进入内部组成 ↗' : hasMechanisms ? '探索机制 ↗' : '';
      if (entryLabel) textLines(group, [entryLabel], 12, box.headerHeight - 12, 16, 'aeg-entry-hint');
      focusable(group, valueFor(box.object)); world.append(group);
    }
    for (const edge of layout.edges) {
      const label = edge.labelBox, group = element('g', { class: `aeg-edge-label${edge.presentation === 'port' ? ' aeg-continuation' : ''}`, 'data-relation-id': edge.id,
        'aria-label': `${objects.get(edge.from)?.title} → ${objects.get(edge.to)?.title}：${edge.label}`, transform: `translate(${label.x},${label.y})` });
      group.append(element('rect', { width: label.width, height: label.height, rx: 6 }));
      textLines(group, label.lines, 8, 19, 17, 'aeg-relation-title'); focusable(group, { relationId: edge.id }); world.append(group);
    }
    highlight();
    if (pendingState) { const state = pendingState; pendingState = null; restoreViewState(state); }
    else if (cameraMode === 'fit') fit(false);
    else if (cameraMode === 'reading') setCamera(readingGraphCamera(layout,{width:viewport.clientWidth,height:viewport.clientHeight}));
    else applyTransform();
    if (restoreKeyboardFocus) restoreFocus();
  }
  // Wait until the click has a target: notifying the parent on pointerdown can
  // move its controls before mouseup and swallow the intended outside action.
  listen(doc, 'click', event => {
    if (allowed() && !inspector.hidden && !inspector.contains(event.target)) closeInspector({ restore: false });
  }, true);
  // The graph lives in an iframe, so a click in another workbench panel leaves this window.
  listen(win, 'blur', () => {
    if (allowed() && !inspector.hidden) closeInspector({ restore: false });
  });
  listen(shell, 'keydown', event => {
    if (!allowed() || event.key !== 'Escape') return;
    cancelClick();
    if (inspector.hidden) return;
    event.preventDefault(); event.stopPropagation(); closeInspector();
  });
  listen(svg, 'keydown', event => {
    if (!allowed()) return;
    const target = event.target.closest?.('.aeg-object,.aeg-edge-label,.aeg-reading .aeg-edge');
    if (event.key === '+' || event.key === '=') { event.preventDefault(); scale(1.2); return; }
    if (event.key === '-') { event.preventDefault(); scale(1 / 1.2); return; }
    if (event.key === '0') { event.preventDefault(); fit(); return; }
    if (!target) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault(); const value = keyFor(target);
      if (event.shiftKey && (value.objectId || value.viewId)) entry(objectFor(value));
      else if (event.shiftKey && value.relationId && relations.get(value.relationId)?.protocol_id) entry(objects.get(relations.get(value.relationId).protocol_id));
      else choose(value); return;
    }
    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault(); const current = target.getBoundingClientRect(), candidates = [...world.querySelectorAll('.aeg-object,.aeg-edge-label,.aeg-reading .aeg-edge')].filter(el => el !== target);
      const dx = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0, dy = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
      const nearest = candidates.map(el => { const r = el.getBoundingClientRect(), x = r.x - current.x, y = r.y - current.y; return { el, along: x * dx + y * dy, score: Math.hypot(x, y) + Math.abs(x * dy - y * dx) }; }).filter(x => x.along > 1).sort((a, b) => a.score - b.score)[0];
      if (nearest) { nearest.el.focus({ preventScroll: true }); nearest.el.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    }
  });
  listen(viewport, 'scroll', () => emit('change', getViewState()), { passive: true });
  const observer = new win.ResizeObserver(() => { render(); if (cameraMode==='fit') fit(false); }); observer.observe(viewport);
  function getViewState() { return clone({ version: 1, rootId: content.root?.id ?? null, capabilityView: content.capabilityView ?? null, readingLens, readingFocus, camera, cameraMode, layoutWidth: laidOutWidth, scrollTop: viewport.scrollTop, scrollLeft: viewport.scrollLeft, selected, focused, inspectorOpen: !inspector.hidden }); }
  function restoreViewState(state) {
    if (!state || state.rootId !== (content.root?.id ?? null) || (state.capabilityView ?? null) !== (content.capabilityView ?? null)) return;
    if (!layout) { pendingState = clone(state); return; }
    const restoreKeyboardFocus = doc.hasFocus() && world.contains(doc.activeElement);
    selected = state.selected?.viewId && objectFor(state.selected)?.viewId ? valueFor(objectFor(state.selected)) : state.selected?.objectId && objects.has(state.selected.objectId) ? { objectId: state.selected.objectId }
      : state.selected?.relationId && relations.has(state.selected.relationId) ? { relationId: state.selected.relationId } : null;
    focused = state.focused?.viewId && objectFor(state.focused)?.viewId ? valueFor(objectFor(state.focused)) : state.focused?.objectId && objects.has(state.focused.objectId) ? { objectId: state.focused.objectId }
      : state.focused?.relationId && relations.has(state.focused.relationId) ? { relationId: state.focused.relationId } : null;
    // Legacy states are explicit cameras. Responsive fit is opt-in in saved state,
    // so returning to an old manual view never silently recenters it.
    if (state.readingLens && state.readingLens !== readingLens) { readingLens=state.readingLens; readingFocus=state.readingFocus; layout=null;render(); }
    cameraMode = ['fit','reading'].includes(state.cameraMode) ? state.cameraMode : 'manual';
    if (cameraMode === 'fit' && state.layoutWidth !== laidOutWidth) fit(false);
    else setCamera(state.camera);
    viewport.scrollTop = Math.max(0, Number(state.scrollTop) || 0); viewport.scrollLeft = Math.max(0, Number(state.scrollLeft) || 0);
    highlight(); inspect(); inspector.hidden = !state.inspectorOpen || !selected;
    if (restoreKeyboardFocus) restoreFocus();
  }
  function destroy() {
    if (disposed) return; disposed = true; cancelClick(); observer.disconnect();
    select(svg).on('.zoom', null); cleanups.splice(0).forEach(fn => fn()); shell.remove();
  }
  signal?.addEventListener('abort', destroy, { once: true });
  cleanups.push(() => signal?.removeEventListener('abort', destroy));
  if (signal?.aborted) destroy(); else render();
  return {
    show() { if (disposed) return; hidden = false; shell.hidden = false; render(); },
    hide() { hidden = true; cancelClick(); shell.hidden = true; }, destroy, getViewState, restoreViewState,
    getScene() { return clone({ kind: content.capabilityView ? 'capability' : 'composition', ...(content.capabilityView ? {viewId: content.capabilityView, memberIds: content.memberIds} : {}), objectId: content.root?.id ?? subject?.objectId ?? null, selected, viewState: getViewState() }); },
  };
}
