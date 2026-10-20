import { select, zoom, zoomIdentity, drag, dragEnable } from 'd3';
import { layoutGraph, layoutOverview, structureKey, structuralGraph, graphBounds, parentOutlines, edgeEndpoints, spatialNeighbor, retainPositions, layoutRuntime, layoutScope, runtimeProtocolGeometry, runtimeParent, wrapLabel } from './layout.js';
import './graph.css';

const TYPES = { module: '模块', protocol: '协议参与关系', design: 'Design', file: '文件', instruction: '指令', plan: 'Plan' };
const STATUS = { current: '当前', simulated: '模拟', proposed: '提议', missing: '证据缺失' };
let instance = 0;
let layoutRequest = 0;
const short = (value, max = 24) => {
  let length = 0, result = '';
  for (const character of String(value || '')) {
    length += character.codePointAt(0) > 255 ? 2 : 1;
    if (length > max) return `${result}…`;
    result += character;
  }
  return result;
};
const summary = node => [node.label || node.id, TYPES[node.type] || node.type, node.subtitle, STATUS[node.status] || node.status].filter(Boolean).join(' · ');

/** Render already-authorized graph data. The caller owns the injected worker. */
function createLegacyGraph(root, { worker, onSelect = () => {}, onEnter = () => {}, onZoom = () => {}, onEscape = () => {} } = {}) {
  const id = `arch-graph-${++instance}`;
  let disposed = false, hidden = false, dirty = false, fitPending = true, initialView = true;
  let model = { nodes: [], edges: [] }, positions = {}, structural = '', presentation = '', requestId = 0;
  let selected = null, highlighted = new Set(), focused = null, hovered = null;
  let width = 800, height = 520, camera = zoomIdentity, pendingResult = null, frame = null;
  let activeNodes = [], nodeById = new Map(), manualPositions = new Map();
  const positionHistory = new Map();
  let draggingView = null, zoomingView = null, overview = false, overviewProtocol = null, overviewHeight = 0;
  const el = document.createElement('section');
  el.className = 'arch-graph';
  el.setAttribute('aria-label', '架构关系图');
  const toolbar = document.createElement('div');
  toolbar.className = 'arch-graph-toolbar';
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', '图形视角');
  el.append(toolbar);
  const button = (label, action) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'portalButton arch-graph-control'; b.textContent = label;
    b.addEventListener('click', action); toolbar.append(b); return b;
  };
  button('缩小 −', () => zoomBy(1 / 1.25));
  button('放大 ＋', () => zoomBy(1.25));
  button('可读视图', () => readableView());
  button('适配 Home', () => fit());
  const scaleText = document.createElement('output');
  scaleText.className = 'arch-graph-scale'; scaleText.textContent = '100%'; toolbar.append(scaleText);
  const legend = document.createElement('span');
  legend.className = 'arch-graph-legend'; legend.textContent = '◇ 协议 · 实线参与 · 虚线关联 · 点线提议';
  const protocolControl = document.createElement('label'); protocolControl.className = 'arch-graph-protocol-control';
  protocolControl.append(document.createTextNode('协议参与关系 '));
  const protocolSelect = document.createElement('select'); protocolSelect.setAttribute('aria-label', '总览协议'); protocolControl.append(protocolSelect);
  const overviewSummary = document.createElement('span'); overviewSummary.className = 'arch-graph-overview-summary';
  protocolControl.append(overviewSummary); toolbar.append(protocolControl); protocolControl.hidden = true;
  protocolSelect.addEventListener('change', () => {
    const node = model.nodes.find(n => n.id === protocolSelect.value);
    if (node) { selected = node.id; onSelect(node); }
  });
  const stage = document.createElement('div'); stage.className = 'arch-graph-stage'; el.append(stage);
  const svg = select(stage).append('svg').attr('class', 'arch-graph-canvas').attr('role', 'application').attr('tabindex', 0)
    .attr('aria-label', '架构图，方向键选择空间邻居，Enter选中，Shift加Enter进入，Home适配，Escape返回')
    .attr('aria-describedby', `${id}-help`);
  const world = svg.append('g').attr('class', 'arch-graph-world');
  const outlineLayer = world.append('g').attr('class', 'arch-graph-outlines').attr('aria-hidden', 'true');
  const edgeLayer = world.append('g').attr('class', 'arch-graph-edges').attr('aria-hidden', 'true');
  const nodeLayer = world.append('g').attr('class', 'arch-graph-nodes');
  const tooltip = document.createElement('div'); tooltip.className = 'arch-graph-tooltip'; tooltip.id = `${id}-tooltip`; tooltip.setAttribute('role', 'tooltip'); tooltip.hidden = true; stage.append(tooltip);
  const status = document.createElement('div'); status.className = 'arch-graph-status'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); stage.append(status);
  const footer = document.createElement('div'); footer.className = 'arch-graph-footer'; el.append(footer);
  const mini = select(footer).append('svg').attr('class', 'arch-graph-minimap').attr('aria-label', '缩略图：点击定位，方向键平移视口')
    .attr('role', 'group').attr('tabindex', 0).attr('preserveAspectRatio', 'xMidYMid meet');
  const miniNodes = mini.append('g').attr('aria-hidden', 'true');
  const miniCamera = mini.append('rect').attr('class', 'arch-graph-minimap-camera').attr('aria-hidden', 'true');
  const help = document.createElement('p'); help.id = `${id}-help`; help.className = 'arch-graph-help';
  help.textContent = '总览可上下滚动并切换具名协议 · 拖动空白平移 · 滚轮缩放 · 拖动节点调整位置 · 方向键空间导航 · Enter选中 · Shift+Enter进入 · Esc返回'; help.prepend(legend, document.createElement('br')); footer.prepend(help);
  root.append(el);

  const zoomBehavior = zoom().scaleExtent([0.035, 4]).extent(() => [[0, 0], [width, height]])
    .filter(event => !hidden && (!overview || event.type !== 'wheel' || event.ctrlKey) && (!event.ctrlKey || event.type === 'wheel') && !event.button)
    .on('start.arch', event => { if (event.sourceEvent?.type === 'mousedown') zoomingView = event.sourceEvent.view; })
    .on('end.arch', () => { zoomingView = null; })
    .on('zoom.arch', event => {
      if (disposed || hidden) return;
      if (event.sourceEvent) initialView = false;
      camera = event.transform;
      world.attr('transform', camera.toString());
      scaleText.textContent = `${Math.round(camera.k * 100)}%`;
      readableLabels();
      updateMiniCamera();
      if (!tooltip.hidden) positionTooltip(hovered || focused);
      onZoom(camera.k);
    });
  svg.call(zoomBehavior).on('dblclick.zoom', null);
  const dragBehavior = drag().filter(event => !hidden && !event.button)
    .subject((event, node) => positions[node.id])
    .on('start.arch', event => { draggingView = event.sourceEvent?.view || null; event.sourceEvent?.stopPropagation(); hideTooltip(); })
    .on('drag.arch', (event, node) => {
      if (disposed || !positions[node.id] || hidden) return;
      positions[node.id] = { ...positions[node.id], x: event.x, y: event.y };
      manualPositions.set(node.id, positions[node.id]);
      scheduleGeometry();
    })
    .on('end.arch', () => { draggingView = null; if (!disposed && !hidden) { updateGeometry(); updateMini(); } });

  function releaseDrag() {
    if (draggingView) { select(draggingView).on('mousemove.drag mouseup.drag', null); dragEnable(draggingView); draggingView = null; }
    if (zoomingView) { select(zoomingView).on('mousemove.zoom mouseup.zoom', null); dragEnable(zoomingView); zoomingView = null; }
  }

  function scheduleGeometry() {
    if (frame != null || hidden || disposed) return;
    frame = requestAnimationFrame(() => { frame = null; updateGeometry(); });
  }
  function positionTooltip(nodeId) {
    const p = positions[nodeId]; if (!p) return;
    const x = camera.applyX(p.x), y = camera.applyY(p.y + p.height / 2);
    const visibleTop = overview ? stage.scrollTop : 0, visibleHeight = overview ? stage.clientHeight : height;
    tooltip.style.left = `${Math.max(8, Math.min(width - tooltip.offsetWidth - 8, x - tooltip.offsetWidth / 2))}px`;
    tooltip.style.top = `${Math.max(visibleTop + 8, Math.min(visibleTop + visibleHeight - tooltip.offsetHeight - 8, y + 12))}px`;
  }
  function showTooltip(node) {
    tooltip.textContent = summary(node); tooltip.hidden = false; positionTooltip(node.id);
  }
  function hideTooltip() { tooltip.hidden = true; tooltip.textContent = ''; }
  let labelScale = null;
  function readableLabels(force = false) {
    if (!force && labelScale === camera.k) return;
    labelScale = camera.k;
    const compact = camera.k < 0.85 && model.nodes.length <= 40;
    const crowded = camera.k < 0.25 && model.nodes.length > 40;
    nodeLayer.selectAll('.arch-graph-node-label').style('display', crowded ? 'none' : null);
    nodeLayer.selectAll('.arch-graph-node-type,.arch-graph-node-subtitle').style('display', compact ? 'none' : null);
    if (crowded) { nodeLayer.selectAll('.arch-graph-node-type,.arch-graph-node-subtitle').style('display', 'none'); return; }
    nodeLayer.selectAll('.arch-graph-node-label').each(function (n) {
      const label = select(this);
      if (!compact) {
        label.style('font-size', overview ? '15px' : null).attr('x', n.type === 'protocol' ? 0 : overview ? -positions[n.id].width / 2 + 16 : -80)
          .attr('y', n.type === 'protocol' ? 33 : overview ? -5 : 3).attr('text-anchor', n.type === 'protocol' ? 'middle' : 'start')
          .text(short(n.label || n.id, n.type === 'protocol' ? 20 : 23));
        return;
      }
      // Key labels keep a 13px screen size. Secondary metadata is available in
      // the inspector and accessible summary instead of shrinking into noise.
      const font = 13 / camera.k;
      const budget = Math.max(8, Math.floor(((positions[n.id]?.width || 148) * camera.k - 12) / 6.5));
      const lines = []; let line = '', length = 0;
      for (const token of String(n.label || n.id).match(/[A-Za-z0-9_]+-?|\s+|./gu) || []) {
        const size = [...token].reduce((sum, char) => sum + (char.codePointAt(0) > 255 ? 2 : 1), 0);
        if (length + size > budget && line.trim()) { lines.push(line.trim()); line = ''; length = 0; }
        line += token; length += size;
      }
      if (line) lines.push(line);
      const visible = lines.slice(0, 2); if (lines.length > 2) visible[1] = visible[1].slice(0, -1) + '…';
      label.text('').style('font-size', font + 'px').attr('x', 0).attr('y', n.type === 'protocol' ? 24 + font : (visible.length === 1 ? font * .35 : -font * .15)).attr('text-anchor', 'middle');
      for (let i = 0; i < visible.length; i++) label.append('tspan').attr('x', 0).attr('dy', i ? font * 1.05 : 0).text(visible[i]);
    });
  }
  function applyStyles() {
    nodeLayer.selectAll('g.arch-graph-node')
      .classed('arch-graph-selected', n => n.id === selected)
      .classed('arch-graph-highlighted', n => highlighted.has(n.id))
      .classed('arch-graph-muted', n => highlighted.size > 0 && !highlighted.has(n.id) && n.id !== selected)
      .attr('aria-pressed', n => String(n.id === selected))
      .attr('tabindex', n => n.id === (focused || selected || activeNodes[0]?.id) ? 0 : -1);
    edgeLayer.selectAll('path').classed('arch-graph-edge-active', e => e.source === hovered || e.target === hovered || e.source === selected || e.target === selected)
      .classed('arch-graph-edge-highlighted', e => highlighted.has(e.source) && highlighted.has(e.target));
  }
  function updateMiniCamera() {
    miniCamera.attr('x', camera.invertX(0)).attr('y', camera.invertY(overview ? stage.scrollTop : 0))
      .attr('width', width / camera.k).attr('height', (overview ? stage.clientHeight : height) / camera.k);
  }
  function updateMini() {
    const bounds = graphBounds(positions);
    mini.attr('viewBox', `${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`);
    miniNodes.selectAll('rect').data(activeNodes, n => n.id).join('rect')
      .attr('class', n => n.id === selected ? 'arch-graph-mini-selected' : 'arch-graph-mini-node')
      .attr('x', n => positions[n.id].x - positions[n.id].width / 2).attr('y', n => positions[n.id].y - positions[n.id].height / 2)
      .attr('width', n => positions[n.id].width).attr('height', n => positions[n.id].height).attr('rx', 8);
    updateMiniCamera();
  }
  function updateGeometry() {
    if (disposed || hidden) return;
    nodeLayer.selectAll('g.arch-graph-node').attr('transform', n => `translate(${positions[n.id].x},${positions[n.id].y})`);
    edgeLayer.selectAll('path').attr('d', edge => {
      const a = positions[edge.source], b = positions[edge.target];
      if (!a || !b) return '';
      if (overview && edge.type === 'participation') {
        const module = nodeById.get(edge.source)?.type === 'module' ? a : b, protocol = module === a ? b : a;
        const start = module.x + module.width / 2, junction = start + (protocol.x - 18 - start) * .4;
        return `M${start},${module.y}H${junction}V${protocol.y}H${protocol.x - 18}`;
      }
      const [start, end] = edgeEndpoints(a, b, nodeById.get(edge.source)?.type, nodeById.get(edge.target)?.type);
      return `M${start.x},${start.y}L${end.x},${end.y}`;
    });
    const outlines = parentOutlines(model.nodes, positions);
    const groups = outlineLayer.selectAll('g').data(outlines, n => n.id).join(enter => {
      const g = enter.append('g'); g.append('rect'); g.append('text'); return g;
    });
    groups.select('rect').attr('x', n => n.x).attr('y', n => n.y).attr('width', n => n.width).attr('height', n => n.height).attr('rx', 20);
    groups.select('text').attr('x', n => n.x + 12).attr('y', n => n.y + 17).text(n => `${nodeById.get(n.id)?.label || n.id} · 层级范围`);
  }
  function draw() {
    if (disposed || hidden) return;
    activeNodes = model.nodes.filter(node => positions[node.id]);
    nodeById = new Map(model.nodes.map(node => [node.id, node]));
    if (!nodeById.has(focused)) focused = null;
    edgeLayer.selectAll('path').data(model.edges.filter(e => positions[e.source] && positions[e.target]), e => e.id).join('path')
      .attr('class', e => `arch-graph-edge arch-graph-edge-${['membership', 'participation', 'association', 'proposed'].includes(e.type) ? e.type : 'association'}`)
      .selectAll('title').data(e => [e]).join('title').text(e => e.label || (e.type === 'participation' ? '协议参与关系，无调用顺序含义' : e.type));
    const nodes = nodeLayer.selectAll('g.arch-graph-node').data(activeNodes, n => n.id).join(enter => {
      const g = enter.append('g').attr('class', 'arch-graph-node').attr('role', 'button');
      g.append('rect').attr('class', 'arch-graph-hit');
      g.append('rect').attr('class', 'arch-graph-box');
      g.append('path').attr('class', 'arch-graph-diamond').attr('d', 'M0,-18L18,0L0,18L-18,0Z');
      g.append('text').attr('class', 'arch-graph-node-type');
      g.append('text').attr('class', 'arch-graph-node-label');
      g.append('text').attr('class', 'arch-graph-node-subtitle');
      g.append('title'); return g;
    });
    nodes.attr('data-node-id', n => n.id).attr('data-node-type', n => n.type)
      .classed('arch-graph-protocol', n => n.type === 'protocol')
      .attr('aria-label', summary)
      .on('click.arch', (event, node) => { event.stopPropagation(); initialView = false; selected = node.id; focused = node.id; applyStyles(); updateMini(); onSelect(node); })
      .on('dblclick.arch', (event, node) => { event.stopPropagation(); onEnter(node); })
      .on('mouseenter.arch', (_, node) => { hovered = node.id; showTooltip(node); applyStyles(); })
      .on('mousemove.arch', (_, node) => { if (hovered !== node.id || tooltip.hidden) { hovered = node.id; showTooltip(node); applyStyles(); } })
      .on('mouseleave.arch', () => { hovered = null; hideTooltip(); applyStyles(); })
      .on('focus.arch', (_, node) => { focused = node.id; showTooltip(node); applyStyles(); })
      .on('blur.arch', () => { hideTooltip(); })
      .call(dragBehavior);
    nodes.select('.arch-graph-hit').attr('x', n => -positions[n.id].width / 2).attr('y', n => -positions[n.id].height / 2)
      .attr('width', n => positions[n.id].width).attr('height', n => positions[n.id].height);
    nodes.select('.arch-graph-box').attr('x', n => -positions[n.id].width / 2).attr('y', n => -positions[n.id].height / 2)
      .attr('width', n => positions[n.id].width).attr('height', n => positions[n.id].height).attr('rx', 10);
    nodes.select('.arch-graph-node-type').attr('x', n => n.type === 'protocol' ? 0 : overview ? -positions[n.id].width / 2 + 16 : -80).attr('y', n => n.type === 'protocol' ? -24 : overview ? -24 : -17)
      .attr('text-anchor', n => n.type === 'protocol' ? 'middle' : 'start')
      .text(n => n.type === 'protocol' ? STATUS[n.status] || n.status || '' : [TYPES[n.type] || n.type, STATUS[n.status] || n.status].filter(Boolean).join(' · '));
    nodes.select('.arch-graph-node-label').attr('x', n => n.type === 'protocol' ? 0 : -80).attr('y', n => n.type === 'protocol' ? 33 : 3)
      .attr('text-anchor', n => n.type === 'protocol' ? 'middle' : 'start').text(n => short(n.label || n.id, n.type === 'protocol' ? 20 : 23));
    nodes.select('.arch-graph-node-subtitle').attr('x', -80).attr('y', 22).text(n => short(overview && n.type === 'module' ? n.data?.responsibility || n.subtitle : n.subtitle, 24));
    if (overview) {
      nodes.filter(n => n.type === 'module').select('.arch-graph-node-subtitle').each(function(n) {
        const label = select(this), x = -positions[n.id].width / 2 + 16, budget = Math.floor((positions[n.id].width - 32) / 6);
        const text = String(n.data?.responsibility || n.subtitle || ''), first = short(text, budget);
        label.text('').attr('x', x).attr('y', 15);
        label.append('tspan').attr('x', x).text(first.replace(/…$/, ''));
        if (first.endsWith('…')) label.append('tspan').attr('x', x).attr('dy', 15).text(short(text.slice(first.length - 1), budget));
      });
    }
    nodes.select('title').text(summary);
    applyStyles(); updateGeometry(); updateMini(); readableLabels(true);
    if (!tooltip.hidden) {
      const node = nodeById.get(hovered || focused);
      if (node) showTooltip(node); else hideTooltip();
    }
    status.textContent = activeNodes.length ? '' : '当前范围暂无可读节点';
    if (fitPending && activeNodes.length) { fitPending = false; readableView(true); }
  }

  function useResult(result) {
    if (disposed || result.requestId !== requestId) return;
    if (hidden) { pendingResult = result; return; }
    if (result.error) {
      status.textContent = `布局未完成：${result.error}`;
      svg.attr('aria-busy', 'false'); return;
    }
    // Merge stable positions from the UI too: manual drags never need to cross
    // the worker boundary and are retained when a snapshot adds nodes.
    positions = overview ? result.positions : retainPositions(result.positions || {}, Object.fromEntries([...positionHistory, ...Object.entries(positions), ...manualPositions]), model.edges);
    if (overview) { overviewHeight = result.height; height = overviewHeight; svg.style('height', height + 'px').attr('viewBox', `0 0 ${width} ${height}`); }
    for (const [key, value] of Object.entries(positions)) positionHistory.set(key, value);
    dirty = false;
    svg.attr('aria-busy', 'false'); draw();
  }
  function requestLayout() {
    if (disposed || hidden) { dirty = true; return; }
    const data = structuralGraph(model.nodes, model.edges);
    requestId = ++layoutRequest;
    status.textContent = '正在布局架构关系…'; svg.attr('aria-busy', 'true');
    if (overview) {
      width = stage.clientWidth || width;
      useResult({ requestId, ...layoutOverview(model.nodes, model.edges, width, overviewProtocol) });
      return;
    }
    if (worker) {
      try { worker.postMessage({ requestId, nodes: data.nodes, edges: data.edges }); }
      catch (error) { useResult({ requestId, error: error.message }); }
    } else {
      try { useResult({ requestId, ...layoutGraph(data.nodes, data.edges, { previous: positions }) }); }
      catch (error) { useResult({ requestId, error: error.message }); }
    }
  }
  const onWorkerMessage = event => useResult(event.data || {});
  const onWorkerError = () => { if (!disposed) { status.textContent = '布局 Worker 不可用，请重新打开架构区'; svg.attr('aria-busy', 'false'); } };
  worker?.addEventListener('message', onWorkerMessage);
  worker?.addEventListener('error', onWorkerError);

  function focusNode(nodeId) {
    if (!positions[nodeId]) return;
    initialView = false; focused = nodeId; applyStyles();
    const p = positions[nodeId], x = camera.applyX(p.x), y = camera.applyY(p.y);
    if (overview) stage.scrollTop = Math.max(0, y - stage.clientHeight / 2);
    else if (x < 80 || x > width - 80 || y < 50 || y > height - 50) setCamera({ x: width / 2 - p.x * camera.k, y: height / 2 - p.y * camera.k, k: camera.k });
    nodeLayer.selectAll('g.arch-graph-node').filter(n => n.id === nodeId).node()?.focus({ preventScroll: true });
  }
  svg.on('keydown.arch', event => {
    if (disposed || hidden) return;
    const currentId = [event.target.closest('[data-node-id]')?.getAttribute('data-node-id'), focused, selected, activeNodes[0]?.id].find(key => positions[key]);
    if (event.key.startsWith('Arrow')) {
      event.preventDefault(); focusNode(spatialNeighbor(currentId, event.key, positions) || currentId);
    } else if (event.key === 'Enter' && nodeById.has(currentId)) {
      event.preventDefault();
      if (event.shiftKey) onEnter(nodeById.get(currentId));
      else { selected = currentId; applyStyles(); updateMini(); onSelect(nodeById.get(currentId)); }
    } else if (event.key === 'Home') { event.preventDefault(); fit(); }
    else if (event.key === 'Escape') { event.preventDefault(); hideTooltip(); onEscape(); }
    else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomBy(1.25); }
    else if (event.key === '-') { event.preventDefault(); zoomBy(1 / 1.25); }
  });
  const onStageScroll = () => { updateMiniCamera(); hideTooltip(); };
  stage.addEventListener('scroll', onStageScroll, { passive: true });
  mini.on('click.arch', event => {
    const matrix = mini.node().getScreenCTM(); if (!matrix) return;
    const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    if (overview) stage.scrollTop = camera.applyY(p.y) - stage.clientHeight / 2;
    else setCamera({ k: camera.k, x: width / 2 - p.x * camera.k, y: height / 2 - p.y * camera.k });
  }).on('keydown.arch', event => {
    const directions = { ArrowLeft: [80, 0], ArrowRight: [-80, 0], ArrowUp: [0, 80], ArrowDown: [0, -80] };
    if (directions[event.key]) { event.preventDefault(); const [dx, dy] = directions[event.key]; setCamera({ k: camera.k, x: camera.x + dx, y: camera.y + dy }); }
    else if (event.key === 'Home') { event.preventDefault(); fit(); }
  });

  function resize() {
    if (disposed || hidden) return;
    const rect = stage.getBoundingClientRect();
    if (!rect.width || !rect.height) return; // Hidden ancestors do not define a new camera size.
    const oldWidth = width, oldHeight = height;
    width = stage.clientWidth || rect.width; height = overview ? overviewHeight || rect.height : rect.height;
    svg.attr('viewBox', `0 0 ${width} ${height}`);
    if (overview) { if (oldWidth !== width && model.nodes.length) requestLayout(); return; }
    if (activeNodes.length && initialView) readableView(true);
    else if (activeNodes.length) setCamera({ k: camera.k, x: camera.x + (width - oldWidth) / 2, y: camera.y + (height - oldHeight) / 2 });
  }
  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
  resizeObserver?.observe(stage); resize();

  function render({ nodes = [], edges = [], selected: nextSelected = null, highlighted: nextHighlighted = [], scopeKey = '', overview: nextOverview = false } = {}) {
    if (disposed) return;
    if (nextSelected) initialView = false;
    const uniqueNodes = [...new Map(nodes.map(n => [n.id, n])).values()];
    const previousOverview = overview; overview = nextOverview;
    const protocols = uniqueNodes.filter(n => n.type === 'protocol');
    if (protocols.some(n => n.id === nextSelected)) overviewProtocol = nextSelected;
    if (!protocols.some(n => n.id === overviewProtocol)) overviewProtocol = protocols[0]?.id;
    protocolControl.hidden = !overview; el.classList.toggle('arch-graph-overview', overview);
    protocolSelect.replaceChildren(...protocols.map(n => { const o = document.createElement('option'); o.value = n.id; o.textContent = n.label; return o; }));
    protocolSelect.value = overviewProtocol;
    overviewSummary.textContent = `${uniqueNodes.filter(n => n.type === 'module').length} 个顶层职责 · ${protocols.length} 项协议，逐项查看参与关系`;
    if (overview !== previousOverview) { svg.style('height', null); stage.scrollTop = 0; fitPending = overview; }
    const next = `${String(scopeKey)}:${overview ? overviewProtocol : ''}:${structureKey(uniqueNodes, edges)}`;
    const nextPresentation = JSON.stringify([uniqueNodes.map(n => [n.id, n.type, n.label, n.subtitle, n.status]), edges.map(e => [e.id, e.label])]);
    const presentationUnchanged = nextPresentation === presentation;
    presentation = nextPresentation;
    model = { nodes: uniqueNodes, edges };
    selected = typeof nextSelected === 'object' && nextSelected ? nextSelected.id : nextSelected;
    highlighted = new Set(nextHighlighted || []);
    if (next !== structural) {
      structural = next; dirty = true; hovered = null; hideTooltip();
      // Invalidate in-flight results even while hidden.
      requestId = ++layoutRequest; pendingResult = null;
      // Refresh surviving nodes immediately so a pending previous layout
      // never leaves clickable nodes bound to the previous current snapshot.
      if (!hidden) { draw(); requestLayout(); }
    } else if (!hidden && !dirty) {
      if (presentationUnchanged) {
        // Selection and impact highlights only touch classes. Refresh bound
        // data too, so callbacks receive the latest caller-owned metadata.
        activeNodes = model.nodes.filter(node => positions[node.id]);
        nodeById = new Map(model.nodes.map(node => [node.id, node]));
        nodeLayer.selectAll('g.arch-graph-node').data(activeNodes, n => n.id);
        edgeLayer.selectAll('path').data(model.edges.filter(e => positions[e.source] && positions[e.target]), e => e.id);
        applyStyles();
        miniNodes.selectAll('rect').attr('class', n => n.id === selected ? 'arch-graph-mini-selected' : 'arch-graph-mini-node');
      } else draw();
    }
  }
  function zoomBy(factor) {
    initialView = false;
    if (!disposed && !hidden && Number.isFinite(factor) && factor > 0) svg.call(zoomBehavior.scaleBy, factor);
  }
  function readableView(initial = false) {
    if (!initial) initialView = false;
    if (disposed || hidden || !activeNodes.length) return;
    if (overview) { setCamera({k:1,x:0,y:0}); if (!initial) stage.scrollTop = 0; return; }
    const anchor = [selected, focused, activeNodes.find(n => n.type === 'module')?.id, activeNodes[0].id].find(id => positions[id]);
    const p = positions[anchor], k = 1.2;
    // A readable portion of the actual relation graph, not a reduced model.
    // The minimap and keyboard navigation reach every offscreen object.
    setCamera({ k, x: Math.min(width * .3, 240) - p.x * k, y: Math.min(height * .2, 100) - p.y * k });
  }
  function fit() {
    initialView = false;
    if (disposed || hidden) return;
    const b = graphBounds(positions);
    const k = Math.max(0.035, Math.min(1.2, (width - 36) / b.width, ((overview ? stage.clientHeight : height) - 36) / b.height));
    setCamera({ k, x: width / 2 - (b.x + b.width / 2) * k, y: (overview ? stage.clientHeight : height) / 2 - (b.y + b.height / 2) * k });
  }
  function getCamera() { return { x: camera.x, y: camera.y, k: camera.k }; }
  function setCamera(next, explicit = false) {
    if (disposed || !next || ![next.x, next.y, next.k].every(Number.isFinite)) return;
    if (explicit) { initialView = false; fitPending = false; }
    const transform = zoomIdentity.translate(next.x, next.y).scale(Math.max(0.035, Math.min(4, next.k)));
    camera = transform;
    svg.call(zoomBehavior.transform, transform);
    if (hidden) { world.attr('transform', transform.toString()); updateMiniCamera(); }
  }
  function hide() {
    if (disposed || hidden) return;
    hidden = true; el.hidden = true; hideTooltip(); releaseDrag();
    if (frame != null) cancelAnimationFrame(frame); frame = null;
    svg.interrupt();
  }
  function show() {
    if (disposed) return;
    hidden = false; el.hidden = false; resize();
    if (pendingResult) { const result = pendingResult; pendingResult = null; useResult(result); }
    else if (dirty) { draw(); requestLayout(); }
    else draw();
  }
  function destroy() {
    if (disposed) return;
    disposed = true; requestId++; releaseDrag();
    if (frame != null) cancelAnimationFrame(frame);
    resizeObserver?.disconnect(); stage.removeEventListener('scroll', onStageScroll);
    worker?.removeEventListener('message', onWorkerMessage);
    worker?.removeEventListener('error', onWorkerError);
    svg.on('.zoom', null).on('.arch', null).interrupt();
    mini.on('.arch', null); nodeLayer.selectAll('*').on('.drag', null).on('.arch', null);
    el.remove(); model = { nodes: [], edges: [] }; positions = {}; manualPositions.clear(); positionHistory.clear();
  }
  return { render, zoomBy, fit, getCamera, setCamera: value => setCamera(value, true), hide, show, destroy, focusObject: focusNode };
}

/** The v1 map keeps its original geometry and meaning. The runtime
 * renderer consumes only caller-authorized modules and participant sets. */
export function createGraph(root, options = {}) {
  let active, mode, pendingCamera, hidden = false, disposed = false;
  function clear() {
    active?.destroy();
    active = null; mode = null;
  }
  return {
    render(model = {}) {
      if (disposed) return;
      // An empty authorization result has no rendering mode. Remove every
      // retained object and listener, including a hidden v1 surface.
      const scoped = model.runtime && model.scopeRoot && model.boundary;
      if (!model.nodes?.length && !scoped) { clear(); return; }
      const nextMode = scoped ? 'scope' : model.runtime ? 'runtime' : 'legacy';
      if (mode !== nextMode) {
        clear();
        mode = nextMode;
        active = scoped ? createScopeGraph(root, options) : model.runtime ? createRuntimeGraph(root, options) : createLegacyGraph(root, options);
        if (hidden) active.hide();
      }
      active.render(model);
      // The caller may restore the destination camera before changing modes.
      // Carry only this explicit numeric request, never the previous model.
      if (pendingCamera) { active.setCamera(pendingCamera); pendingCamera = null; }
    },
    getCamera: () => active?.getCamera() || pendingCamera || { x: 0, y: 0, k: 1 },
    setCamera(value) {
      if (disposed || !value || ![value.x, value.y, value.k].every(Number.isFinite)) return;
      pendingCamera = { x: value.x, y: value.y, k: value.k };
      active?.setCamera(pendingCamera);
    },
    focusObject: (id, scroll = true) => active?.focusObject(id, scroll),
    fit: () => active?.fit(), zoomBy: value => active?.zoomBy(value),
    hide() { hidden = true; active?.hide(); },
    show() { if (!disposed) { hidden = false; active?.show(); } },
    destroy() { disposed = true; clear(); pendingCamera = null; },
  };
}

function createRuntimeGraph(root, { onSelect = () => {}, onEnter = () => {}, onZoom = () => {}, onEscape = () => {}, onHome } = {}) {
  const el = document.createElement('section'); el.className = 'arch-runtime'; el.setAttribute('aria-label', 'Portal 运行架构图');
  const stage = document.createElement('div'); stage.className = 'arch-runtime-stage'; el.append(stage);
  const svg = select(stage).append('svg').attr('class', 'arch-runtime-canvas').attr('role', 'application').attr('tabindex', 0)
    .attr('aria-label', '运行单元与协议。上下左键移动，Enter查看，右键或Shift Enter进入，正负键缩放，Home总览，Alt Left返回。');
  const world = svg.append('g'), layer = world.append('g').attr('class', 'arch-runtime-layer'),
    connections = world.append('g').attr('class', 'arch-runtime-connections'), units = world.append('g');
  const toolbar = document.createElement('div'); toolbar.className = 'arch-runtime-tools'; toolbar.setAttribute('role', 'toolbar'); toolbar.setAttribute('aria-label', '架构视角'); el.append(toolbar);
  const makeButton = (label, text, fn) => {
    const b = document.createElement('button'); b.type = 'button'; b.title = label; b.setAttribute('aria-label', label); b.textContent = text;
    b.addEventListener('click', fn); toolbar.append(b); return b;
  };
  makeButton('缩小', '−', () => zoomBy(1 / 1.18)); makeButton('放大', '+', () => zoomBy(1.18));
  makeButton('回到架构总览 Home', '⌂', () => onHome ? onHome() : fit());
  const supports = makeButton('显示支撑运行单元', '支撑', () => { showSupporting = !showSupporting; redraw(); });
  const protocolChoice = document.createElement('select'); protocolChoice.setAttribute('aria-label', '查看协议全部参与者'); toolbar.append(protocolChoice);
  protocolChoice.addEventListener('change', () => {
    protocolId = protocolChoice.value || null; redraw();
    const p = objects.get(protocolId); if (p) onSelect(p);
  });
  const legend = document.createElement('div'); legend.className = 'arch-runtime-legend'; legend.textContent = '线表示登记的局部参与关系 · 选协议查看全部'; el.append(legend);
  const tooltip = document.createElement('div'); tooltip.className = 'arch-runtime-tooltip'; tooltip.hidden = true; tooltip.setAttribute('role', 'tooltip'); el.append(tooltip);
  root.append(el);
  let disposed = false, hidden = false, model = { nodes: [], protocols: [] }, objects = new Map(), geometry, routes = [];
  let width = 625, viewportHeight = 600, camera = zoomIdentity, focused = null, selected = null, protocolId = null, showSupporting = false;
  let zoomingView = null, geometryKey = '', highlighted = new Set(), dirty = false, clickTimer = null;
  const zoomBehavior = zoom().scaleExtent([.25, 5]).extent(() => [[0, 0], [width, viewportHeight]])
    .filter(event => !hidden && !disposed && !event.button && event.type !== 'dblclick')
    .on('start.runtime', event => { if (event.sourceEvent?.view) zoomingView = event.sourceEvent.view; })
    .on('zoom.runtime', event => {
      camera = event.transform; world.attr('transform', camera.toString()); tooltip.hidden = true; onZoom(camera.k);
    }).on('end.runtime', () => { zoomingView = null; });
  svg.call(zoomBehavior).on('dblclick.zoom', null);

  function hideTooltip() { tooltip.hidden = true; }
  function showTooltip(object, event) {
    if (!object) return;
    const relation = routes.find(r => r.id === object.id);
    tooltip.textContent = [object.label, object.subtitle || object.data?.description || object.data?.responsibility,
      object.type === 'protocol' ? `${relation?.participants.length || 0}/${object.participants?.length || 0} 个登记边界；无调用顺序含义。单击查看完整关系。${object.actualParticipants ? `实际端点：${(object.actualParticipantLabels || object.actualParticipants).join('、')}` : ''}` : STATUS[object.status] || ''].filter(Boolean).join(' · ');
    tooltip.hidden = false;
    const rect = el.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(width - Math.min(350, width - 16), (event?.clientX || rect.left + 8) - rect.left))}px`;
    tooltip.style.top = `${Math.max(42, Math.min(el.clientHeight - tooltip.offsetHeight - 8, (event?.clientY || rect.top + 80) - rect.top + 18))}px`;
  }
  function choose(object, event) {
    event?.stopPropagation(); if (!object) return;
    focused = object.id; selected = object.id;
    if (object.type === 'protocol') { protocolId = object.id; redraw(); }
    else styles();
    onSelect(object);
  }
  function cancelClick() { if (clickTimer != null) clearTimeout(clickTimer); clickTimer = null; }
  function pointerChoose(object, event) {
    event.stopPropagation(); cancelClick();
    if (event.detail > 1) return;
    const id = object.id;
    clickTimer = setTimeout(() => { clickTimer = null; if (!hidden && !disposed && objects.has(id)) choose(objects.get(id)); }, 200);
  }
  function styles() {
    units.selectAll('.arch-runtime-node').classed('selected', n => n.id === selected).classed('highlighted', n => highlighted.has(n.id))
      .classed('dimmed', n => highlighted.size > 0 && !highlighted.has(n.id) && n.id !== selected)
      .attr('aria-pressed', n => String(n.id === selected)).attr('tabindex', n => n.id === (focused || selected || geometry?.visible[0]) ? 0 : -1);
    connections.selectAll('.arch-runtime-protocol').classed('selected', d => d.id === selected)
      .classed('highlighted', d => highlighted.has(d.id)).attr('tabindex', d => d.id === focused ? 0 : -1);
  }
  function renderText(selection, lines, x, y, lineHeight = 18) {
    selection.text('').attr('x', x).attr('y', y);
    lines.forEach((line, index) => selection.append('tspan').attr('x', x).attr('dy', index ? lineHeight : 0).text(line));
  }
  function draw() {
    if (disposed || hidden || !geometry) return;
    const keepFocus = el.contains(document.activeElement) && document.activeElement !== protocolChoice && document.activeElement.closest('[data-node-id],[data-protocol-id]');
    const visible = geometry.visible.map(id => objects.get(id)).filter(Boolean);
    const rows = [
      { id: 'frontend', label: '浏览器', y: 40 }, { id: 'entry', label: 'Web 入口', y: 140 },
      { id: 'service', label: '领域服务', y: 250 }, { id: 'execution', label: '执行与适配', y: 374 },
      { id: 'data', label: '存储与外部', y: 510 },
    ];
    // Layer annotations sit in unused left space, never above a runtime label.
    layer.selectAll('text').data(rows.slice(0, 2), d => d.id).join('text').attr('x', 18).attr('y', d => d.y * geometry.stretch).text(d => d.label);
    const groups = units.selectAll('g.arch-runtime-node').data(visible, n => n.id).join(enter => {
      const g = enter.append('g').attr('class', 'arch-runtime-node').attr('role', 'button');
      g.append('rect').attr('class', 'arch-runtime-box'); g.append('text').attr('class', 'arch-runtime-title');
      g.append('text').attr('class', 'arch-runtime-status'); g.append('text').attr('class', 'arch-runtime-duty'); g.append('title');
      return g;
    });
    groups.attr('data-node-id', n => n.id).attr('data-node-type', 'module').attr('aria-label', n => summary(n))
      .attr('transform', n => `translate(${geometry.positions[n.id].x},${geometry.positions[n.id].y})`)
      .on('click.runtime', (event, n) => pointerChoose(n, event))
      .on('dblclick.runtime', (event, n) => { event.stopPropagation(); cancelClick(); onEnter(n); })
      .on('focus.runtime', (_, n) => { focused = n.id; styles(); })
      .on('mouseenter.runtime', (event, n) => showTooltip(n, event)).on('mouseleave.runtime', hideTooltip);
    groups.each(function(n) {
      const g = select(this), p = geometry.positions[n.id];
      g.select('rect').attr('x', -p.width / 2).attr('y', -p.height / 2).attr('width', p.width).attr('height', p.height).attr('rx', 8);
      const lines = wrapLabel(n.label || n.id, p.width - 20, 15);
      const duties = { resources: '资源解析、类型适配', lineage: '定义、输入队列、Family', execution: '持久调度、账号容量' };
      const excerpt = duties[n.localId];
      const duty = excerpt && n.data?.responsibility?.includes(excerpt) ? excerpt : '';
      renderText(g.select('.arch-runtime-title'), lines, 0, -(lines.length - 1) * 9 + (duty ? -12 : 1));
      renderText(g.select('.arch-runtime-duty'), duty ? wrapLabel(duty, p.width - 14, 14) : [], 0, 12, 16);
      g.select('.arch-runtime-status').attr('x', 0).attr('y', p.height / 2 - 10).text(n.data?.level === 'external' ? '外部' : '');
      g.select('title').text(summary(n));
    });
    const links = connections.selectAll('g.arch-runtime-protocol').data(routes, r => r.id).join(enter => {
      const g = enter.append('g').attr('class', 'arch-runtime-protocol').attr('role', 'button');
      g.append('g').attr('class', 'arch-runtime-wire'); g.append('rect').attr('class', 'arch-runtime-protocol-label-bg');
      g.append('text').attr('class', 'arch-runtime-protocol-label'); g.append('title'); return g;
    });
    links.attr('data-protocol-id', r => r.id).attr('aria-label', r => `${r.protocol.label}，${r.partial ? '局部' : '全部'} ${r.participants.length} 个参与者，无调用方向`)
      .on('click.runtime', (event, r) => choose(r.protocol, event))
      .on('mouseenter.runtime', (event, r) => showTooltip(r.protocol, event)).on('mouseleave.runtime', hideTooltip)
      .on('focus.runtime', (_, r) => { focused = r.id; styles(); });
    links.each(function(r) {
      const g = select(this), lines = wrapLabel(r.protocol.label || r.id, r.labelWidth - 8, 14);
      g.select('.arch-runtime-wire').selectAll('path').data(r.routes, route => route.participant).join('path')
        .attr('data-participant', route => route.participant).attr('d', route => route.points.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(''));
      const textWidth = Math.min(r.labelWidth, Math.max(...lines.map(line => [...line].reduce((sum, char) => sum + (char.codePointAt(0) > 255 ? 14 : 14 * .57), 0))) + 10);
      g.select('rect').attr('x', r.label.x - textWidth / 2).attr('y', r.label.y - lines.length * 8 - 7).attr('width', textWidth).attr('height', lines.length * 16 + 10).attr('rx', 4);
      renderText(g.select('text'), lines, r.label.x, r.label.y - (lines.length - 1) * 8 + 4, 16);
      g.select('title').text(`${r.protocol.label} · ${r.partial ? '总览局部参与关系' : '完整参与关系'}。${r.protocol.subtitle || ''}`);
    });
    styles();
    if (keepFocus && focused) focusElement(focused, false);
    dirty = false;
  }
  function redraw() {
    if (disposed) return; if (hidden) { dirty = true; return; }
    const currentProtocol = (model.protocols || []).find(p => p.id === protocolId);
    const reveal = [...(currentProtocol?.participants || [])];
    const target = objects.get(selected); if (target?.type === 'module') reveal.push(runtimeParent(target) || target.id);
    geometry = layoutRuntime(model.nodes, { width, showSupporting, reveal });
    routes = runtimeProtocolGeometry(model.protocols, geometry, currentProtocol?.id);
    svg.attr('viewBox', `0 0 ${width} ${Math.max(geometry.height, viewportHeight)}`).style('height', `${Math.max(geometry.height, viewportHeight)}px`);
    supports.hidden = !geometry.supporting.length; supports.setAttribute('aria-pressed', String(showSupporting)); supports.setAttribute('aria-label', showSupporting ? '收起支撑运行单元' : '显示支撑运行单元');
    protocolChoice.value = protocolId || '';
    legend.textContent = currentProtocol ? `完整参与关系 · ${currentProtocol.participants.length} 个登记对象 · 无调用顺序含义` : '总览局部参与关系 · 选协议查看全部';
    draw();
  }
  function render(next = {}) {
    if (disposed) return;
    cancelClick();
    dirty = true;
    const previousFocus = focused;
    model = { ...next, nodes: [...new Map((next.nodes || []).filter(n => n.type === 'module').map(n => [n.id, n])).values()], protocols: next.protocols || [] };
    objects = new Map([...model.nodes, ...model.protocols].map(n => [n.id, n]));
    selected = typeof next.selected === 'object' ? next.selected?.id : next.selected;
    highlighted = new Set(next.highlighted || []);
    focused = objects.has(previousFocus) ? previousFocus : null;
    if (objects.get(selected)?.type === 'protocol') protocolId = selected;
    if (!objects.has(protocolId)) protocolId = null;
    const current = protocolChoice.value;
    protocolChoice.replaceChildren();
    const overviewOption = document.createElement('option'); overviewOption.value = ''; overviewOption.textContent = '协议 · 总览'; protocolChoice.append(overviewOption);
    for (const p of model.protocols) { const option = document.createElement('option'); option.value = p.id; option.textContent = p.label; protocolChoice.append(option); }
    protocolChoice.value = protocolId || current || '';
    const key = JSON.stringify([width, model.nodes.map(n => [n.id, n.label, runtimeParent(n)]), model.protocols.map(p => [p.id, p.label, p.participants]), protocolId, selected]);
    // Metadata is rebound even if only a snapshot/version changed.
    if (key !== geometryKey) { geometryKey = key; redraw(); } else draw();
  }
  function focusElement(id, scroll = true) {
    const candidate = [...el.querySelectorAll('[data-node-id],[data-protocol-id]')].find(n => (n.dataset.nodeId || n.dataset.protocolId) === id);
    if (!candidate) return false;
    candidate.focus({ preventScroll: true });
    if (scroll) {
      const p = geometry.positions[id] || routes.find(r => r.id === id)?.label;
      const parent = p?.parent ? geometry.positions[p.parent] : p;
      if (parent) {
        const y = camera.applyY(parent.y);
        if (y < stage.scrollTop + 40 || y > stage.scrollTop + stage.clientHeight - 40) stage.scrollTop = Math.max(0, y - stage.clientHeight / 2);
        const x = camera.applyX(parent.x);
        if (x < 24 || x > width - 24) setCamera({ ...getCamera(), x: width / 2 - parent.x * camera.k });
      }
    }
    return true;
  }
  function focusObject(id, scroll = true) {
    if (!objects.has(id)) return false;
    focused = id;
    const object = objects.get(id);
    if (object.type === 'protocol' && !routes.some(r => r.id === id)) { protocolId = id; redraw(); }
    if (object.type === 'module' && !geometry.positions[id]) { showSupporting = true; redraw(); }
    styles(); return focusElement(id, scroll);
  }
  svg.on('keydown.runtime', event => {
    const target = event.target.closest('[data-node-id],[data-protocol-id]');
    const current = target?.dataset.nodeId || target?.dataset.protocolId || focused || selected || geometry?.visible[0];
    if (event.altKey && event.key === 'ArrowLeft') { event.preventDefault(); onEscape(); }
    else if (event.key === 'ArrowRight' && objects.get(current)?.type === 'module') { event.preventDefault(); onEnter(objects.get(current)); }
    else if (event.key.startsWith('Arrow')) {
      event.preventDefault(); const locations = { ...geometry.positions };
      for (const r of routes) locations[r.id] = r.label;
      focusObject(spatialNeighbor(current, event.key, locations) || current);
    } else if (event.key === 'Enter') {
      event.preventDefault(); const object = objects.get(current);
      if (event.shiftKey && object?.type === 'module') onEnter(object); else choose(object, event);
    } else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomBy(1.18); }
    else if (event.key === '-') { event.preventDefault(); zoomBy(1 / 1.18); }
    else if (event.key === 'Home') { event.preventDefault(); if (onHome) onHome(); else fit(); }
    else if (event.key === 'Escape') { event.preventDefault(); hideTooltip(); onEscape(); }
  });
  function zoomBy(factor) { if (!disposed && !hidden) svg.call(zoomBehavior.scaleBy, factor, [width / 2, stage.scrollTop + stage.clientHeight / 2]); }
  function getCamera() { return { x: camera.x, y: camera.y, k: camera.k }; }
  function setCamera(next) {
    if (disposed || !next || ![next.x, next.y, next.k].every(Number.isFinite)) return;
    camera = zoomIdentity.translate(next.x, next.y).scale(Math.max(.25, Math.min(5, next.k)));
    svg.call(zoomBehavior.transform, camera);
  }
  function fit() { protocolId = null; redraw(); setCamera({ x: 0, y: 0, k: 1 }); stage.scrollTop = 0; }
  function resize() {
    if (disposed || hidden || !stage.clientWidth) return;
    const next = stage.clientWidth; viewportHeight = stage.clientHeight;
    if (next !== width) { width = next; redraw(); } else if (!geometry) redraw();
  }
  const observer = new ResizeObserver(resize); observer.observe(stage); resize();
  stage.addEventListener('scroll', hideTooltip, { passive: true });
  function releasePan() {
    if (zoomingView) { select(zoomingView).on('mousemove.zoom mouseup.zoom', null); dragEnable(zoomingView); zoomingView = null; }
  }
  return {
    render, getCamera, setCamera, focusObject, zoomBy, fit,
    hide() { hidden = true; el.hidden = true; cancelClick(); hideTooltip(); releasePan(); },
    show() { if (!disposed) { hidden = false; el.hidden = false; resize(); if (dirty) redraw(); } },
    destroy() {
      disposed = true; cancelClick(); observer.disconnect(); releasePan(); stage.removeEventListener('scroll', hideTooltip);
      svg.on('.zoom', null).on('.runtime', null); world.selectAll('*').on('.runtime', null); el.remove();
      model = { nodes: [], protocols: [] }; objects.clear();
    },
  };
}

/** Scope owns only a view. Selection, navigation, authorization and history
 * remain with the adapter; no SDK or resource operation occurs here. */
function createScopeGraph(root, { onSelect = () => {}, onEnter = () => {}, onZoom = () => {}, onEscape = () => {}, onHome } = {}) {
  const el = document.createElement('section'); el.className = 'arch-runtime arch-scope';
  el.setAttribute('aria-label', '模块内部架构图');
  const stage = document.createElement('div'); stage.className = 'arch-runtime-stage'; el.append(stage);
  const svg = select(stage).append('svg').attr('class', 'arch-runtime-canvas').attr('role', 'application').attr('tabindex', 0)
    .attr('aria-label', '直属组件与边界关系。Enter查看，右键或Shift Enter进入，Alt Left返回，Home总览，正负键缩放。');
  const defs = svg.append('defs'), markerId = `arch-scope-arrow-${++instance}`;
  defs.append('marker').attr('id', markerId).attr('viewBox', '0 -4 8 8').attr('refX', 7).attr('refY', 0)
    .attr('markerWidth', 7).attr('markerHeight', 7).attr('orient', 'auto-start-reverse').append('path').attr('d', 'M0,-3L7,0L0,3').attr('fill', 'none').attr('stroke', 'currentColor');
  const world = svg.append('g'), boundaryLayer = world.append('g').attr('class', 'arch-scope-boundary'),
    wires = world.append('g'), units = world.append('g'), labels = world.append('g'), background = world.append('g');
  const toolbar = document.createElement('div'); toolbar.className = 'arch-runtime-tools arch-scope-tools'; toolbar.setAttribute('role', 'toolbar'); toolbar.setAttribute('aria-label', '架构视角'); el.append(toolbar);
  const protocolMenus = document.createElement('div'); protocolMenus.className = 'arch-scope-protocols'; toolbar.append(protocolMenus);
  const protocolMenu = (label, className) => {
    const menu = document.createElement('select'); menu.className = className; menu.setAttribute('aria-label', label);
    menu.addEventListener('change', () => { const object = objects.get(menu.value); if (object) choose(object); menu.value = ''; });
    protocolMenus.append(menu); return menu;
  };
  const rootProtocolMenu = protocolMenu('根边界协议，选择后打开完整关系', 'arch-scope-protocol-select');
  const parentProtocolMenu = protocolMenu('父层背景协议，不连接本层组件', 'arch-scope-parent-protocol');
  const button = (name, label, fn) => {
    const b = document.createElement('button'); b.type = 'button'; b.setAttribute('aria-label', name); b.title = name; b.textContent = label; b.addEventListener('click', fn); toolbar.append(b);
  };
  button('返回上层 Alt Left', '←', () => onEscape());
  button('回到架构总览 Home', '⌂', () => onHome ? onHome() : fit());
  button('缩小', '−', () => zoomBy(1 / 1.18)); button('放大', '+', () => zoomBy(1.18));
  button('适配当前层', '适配', () => fit());
  const tooltip = document.createElement('div'); tooltip.className = 'arch-runtime-tooltip'; tooltip.hidden = true; tooltip.setAttribute('role', 'tooltip'); el.append(tooltip);
  root.append(el);
  let model, geometry, objects = new Map(), selected = null, focused = null, highlighted = new Set();
  let disposed = false, hidden = false, dirty = false, clickTimer = null, width = 625, viewportHeight = 600, camera = zoomIdentity, panView = null;
  const zoomBehavior = zoom().scaleExtent([.25, 5]).extent(() => [[0, 0], [width, viewportHeight]])
    .filter(event => !hidden && !disposed && !event.button && event.type !== 'dblclick')
    .on('start.scope', event => { if (event.sourceEvent?.view) panView = event.sourceEvent.view; })
    .on('zoom.scope', event => { camera = event.transform; world.attr('transform', camera.toString()); tooltip.hidden = true; onZoom(camera.k); })
    .on('end.scope', () => { panView = null; });
  svg.call(zoomBehavior).on('dblclick.zoom', null);
  function textLines(selection, lines, x, y, spacing = 18) {
    selection.text('').attr('x', x).attr('y', y);
    lines.forEach((line, index) => selection.append('tspan').attr('x', x).attr('dy', index ? spacing : 0).text(line));
  }
  function hideTooltip() { tooltip.hidden = true; }
  function showTooltip(object, event) {
    if (!object) return;
    tooltip.textContent = object.type === 'relation' ? [object.label, geometry?.routes.find(r => r.id === object.id)?.meaning, ...object.data.endpoints.map(e => e.role), ...(object.data.interaction?.conditions || []), object.data.interaction?.result].filter(Boolean).join(' · ')
      : [object.label, object.subtitle || object.data?.responsibility, object.type === 'protocol' ? '共同参与；无调用顺序含义' : STATUS[object.status]].filter(Boolean).join(' · ');
    tooltip.hidden = false;
    const rect = el.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(width - Math.min(350, width - 16), (event?.clientX || rect.left) - rect.left))}px`;
    tooltip.style.top = `${Math.max(48, Math.min(el.clientHeight - tooltip.offsetHeight - 8, (event?.clientY || rect.top + 70) - rect.top + 14))}px`;
  }
  function styles() {
    const active = focused || selected || geometry?.visible[0] || model?.boundary?.id;
    world.selectAll('[data-object-id]').classed('selected', d => d.id === selected).classed('highlighted', d => highlighted.has(d.id))
      .attr('aria-pressed', d => String(d.id === selected)).attr('tabindex', d => d.id === active ? 0 : -1);
    wires.selectAll('.arch-scope-route').classed('selected', d => d.id === selected).classed('highlighted', d => highlighted.has(d.id));
  }
  function cancelClick() { if (clickTimer !== null) clearTimeout(clickTimer); clickTimer = null; }
  function choose(object, event) {
    event?.stopPropagation(); if (!object || hidden || disposed) return;
    focused = selected = object.id; styles(); onSelect(object);
  }
  function attach(selection, type = 'node') {
    return selection.attr('data-object-id', d => d.id).attr(type === 'node' ? 'data-node-id' : type === 'relation' ? 'data-relation-id' : 'data-protocol-id', d => d.id)
      .attr('role', 'button').attr('aria-label', d => [d.label, d.type === 'relation' ? '组件关系' : d.type === 'protocol' ? '协议共同参与关系' : d.data?.responsibility].filter(Boolean).join('，'))
      .on('click.scope', (event, d) => {
        event.stopPropagation(); cancelClick();
        if (d.type !== 'module') { choose(d, event); return; }
        if (event.detail > 1) return;
        const id = d.id;
        clickTimer = setTimeout(() => { clickTimer = null; if (!hidden && !disposed && objects.has(id)) choose(objects.get(id)); }, 200);
      }).on('dblclick.scope', (event, d) => { event.stopPropagation(); cancelClick(); if (d.type === 'module') onEnter(d); })
      .on('focus.scope', (_, d) => { focused = d.id; styles(); })
      .on('mouseenter.scope', (event, d) => showTooltip(d, event)).on('mouseleave.scope', hideTooltip);
  }
  function draw() {
    if (!model || !geometry || disposed || hidden) return;
    const keepFocus = el.contains(document.activeElement) && document.activeElement.hasAttribute('data-object-id');
    const b = geometry.boundary;
    boundaryLayer.selectAll('rect.arch-scope-frame').data([b]).join('rect').attr('class', 'arch-scope-frame')
      .attr('x', b.x).attr('y', b.y).attr('width', b.width).attr('height', b.height).attr('rx', 12);
    const heading = boundaryLayer.selectAll('g.arch-scope-root').data([model.boundary], n => n.id).join(enter => {
      const g = enter.append('g').attr('class', 'arch-scope-root'); g.append('text').attr('class', 'arch-scope-root-title'); g.append('text').attr('class', 'arch-scope-root-duty'); return g;
    });
    attach(heading);
    textLines(heading.select('.arch-scope-root-title'), b.titleLines, 32, 40, 24);
    textLines(heading.select('.arch-scope-root-duty'), b.dutyLines, 32, 44 + b.titleLines.length * 24, 19);
    const leaf = boundaryLayer.selectAll('text.arch-scope-empty').data(geometry.visible.length ? [] : ['尚未登记子模块']).join('text').attr('class', 'arch-scope-empty');
    leaf.attr('x', width / 2).attr('y', b.headerBottom + 54).text(d => d);
    const members = [...geometry.visible, ...geometry.context].map(id => objects.get(id)).filter(Boolean);
    const nodes = units.selectAll('g.arch-runtime-node').data(members, n => n.id).join(enter => {
      const g = enter.append('g').attr('class', 'arch-runtime-node'); g.append('rect').attr('class', 'arch-runtime-box');
      g.append('text').attr('class', 'arch-runtime-title'); g.append('text').attr('class', 'arch-runtime-duty'); g.append('text').attr('class', 'arch-scope-context-label'); g.append('title'); return g;
    });
    attach(nodes).attr('data-node-type', 'module').classed('arch-scope-context', n => !!geometry.positions[n.id].context)
      .attr('transform', n => `translate(${geometry.positions[n.id].x},${geometry.positions[n.id].y})`);
    nodes.each(function(n) {
      const g = select(this), p = geometry.positions[n.id], title = wrapLabel(n.label, p.width - 24, p.context ? 14 : 16);
      g.select('rect').attr('x', -p.width / 2).attr('y', -p.height / 2).attr('width', p.width).attr('height', p.height).attr('rx', 8);
      textLines(g.select('.arch-runtime-title'), title, 0, -(title.length - 1) * 10 + (p.context ? -4 : 5), 20);
      // Full responsibilities remain on hover and the selected object. In the
      // diagram the real component names and named collaborations carry scope.
      g.select('.arch-runtime-duty').text('');
      const shared = (model.componentRelations || []).some(r => r.endpoints?.some(e => e.display === 'shared_component_boundary' && `module:${e.displayModuleId}` === n.id));
      g.select('.arch-scope-context-label').attr('x', 0).attr('y', p.height / 2 - 8).text(p.context ? shared ? '共享组件边界' : '外部边界' : '');
      g.select('title').text(summary(n));
    });
    const routeGroups = wires.selectAll('g.arch-scope-route').data(geometry.routes, r => r.id).join('g').attr('class', 'arch-scope-route').attr('data-route-id', r => r.id);
    routeGroups.each(function(r) {
      select(this).selectAll('path').data(r.routes, (route, i) => `${route.participant}:${i}`).join('path')
        .attr('data-participant', route => route.participant).attr('data-boundary-role', route => route.endpoint?.display === 'root_boundary_role' ? route.endpoint.role : null)
        .attr('data-direction', r.relation?.interaction?.direction || 'unspecified')
        .attr('marker-start', route => ['start', 'both'].includes(route.arrow) ? `url(#${markerId})` : null)
        .attr('marker-end', route => ['end', 'both'].includes(route.arrow) ? `url(#${markerId})` : null)
        .attr('d', route => route.points.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(''));
    });
    const routeLabels = labels.selectAll('g.arch-scope-relation').data(geometry.routes, r => r.id).join(enter => {
      const g = enter.append('g').attr('class', 'arch-scope-relation'); g.append('rect'); g.append('text'); return g;
    });
    routeLabels.each(function(r) {
      const g = select(this), object = objects.get(r.id);
      // The DOM selection datum is always the semantic callback object; the
      // geometry remains separate and cannot replace its provenance.
      g.datum(object); attach(g, r.relation ? 'relation' : 'protocol');
      const lines = r.lines, h = lines.length * 18 + 16;
      g.attr('data-relation-kind', r.relation.kind).attr('data-direction', r.relation.interaction?.direction || 'unspecified');
      g.select('rect').attr('x', r.label.x - r.labelWidth / 2).attr('y', r.y - h / 2).attr('width', r.labelWidth).attr('height', h).attr('rx', 5);
      textLines(g.select('text'), lines, r.label.x, r.y - (lines.length - 1) * 9 + 4, 18);
    });
    const sections = geometry.context.length ? [{ id: 'context', text: '局部协作涉及的外部 / 共享边界', y: geometry.contextStart - 8 }] : [];
    background.selectAll('text.arch-scope-section').data(sections, d => d.id).join('text').attr('class', 'arch-scope-section').attr('x', 28).attr('y', d => d.y).text(d => d.text);
    const fillMenu = (menu, protocols, label) => {
      menu.replaceChildren(); menu.hidden = !protocols.length;
      const prompt = document.createElement('option'); prompt.value = ''; prompt.textContent = `${label} · ${protocols.length}`; menu.append(prompt);
      for (const p of protocols) { const option = document.createElement('option'); option.value = p.id; option.dataset.protocolId = p.id; option.textContent = p.label; menu.append(option); }
    };
    fillMenu(rootProtocolMenu, geometry.protocols, '根边界协议');
    fillMenu(parentProtocolMenu, geometry.parentProtocols, '父层背景');
    styles(); dirty = false;
    if (keepFocus && focused) focusObject(focused, false);
  }
  function redraw() {
    if (!model || disposed) return;
    if (hidden) { dirty = true; return; }
    geometry = layoutScope(model, { width });
    svg.attr('viewBox', `0 0 ${width} ${Math.max(viewportHeight, geometry.height)}`).style('height', `${Math.max(viewportHeight, geometry.height)}px`);
    draw();
  }
  function render(next) {
    if (disposed) return;
    cancelClick(); const oldRoot = model?.scopeRoot; model = next;
    el.dataset.scopeRoot = next.scopeRoot;
    objects = new Map([next.boundary, ...(next.nodes || []), ...(next.context || []), ...(next.protocols || []), ...(next.parentProtocols || []),
      ...(next.componentRelations || []).map(r => ({ id: `relation:${r.id}`, type: 'relation', label: r.title, data: r }))].filter(Boolean).map(n => [n.id, n]));
    selected = typeof next.selected === 'object' ? next.selected?.id : next.selected;
    if (!objects.has(focused)) focused = null;
    highlighted = new Set(next.highlighted || []);
    if (oldRoot && oldRoot !== next.scopeRoot) { focused = null; stage.scrollTop = 0; setCamera({ x: 0, y: 0, k: 1 }); }
    redraw();
  }
  function focusObject(id, scroll = true) {
    const protocolMenu = [rootProtocolMenu, parentProtocolMenu].find(menu => !menu.hidden && [...menu.options].some(option => option.value === id));
    if (protocolMenu) { focused = id; styles(); protocolMenu.focus({ preventScroll: true }); return true; }
    const target = [...el.querySelectorAll('[data-object-id]')].find(n => n.dataset.objectId === id);
    if (!target) return false;
    focused = id; styles(); target.focus({ preventScroll: true });
    if (scroll) {
      const p = geometry.positions[id] || geometry.routes.find(r => r.id === id)?.label;
      if (p) {
        const y = camera.applyY(p.y), x = camera.applyX(p.x);
        if (y < stage.scrollTop + 68 || y > stage.scrollTop + stage.clientHeight - 48) stage.scrollTop = Math.max(0, y - stage.clientHeight / 2);
        if (x < 28 || x > width - 28) { const value = getCamera(); setCamera({ ...value, x: width / 2 - p.x * value.k }); }
      }
    }
    return true;
  }
  svg.on('keydown.scope', event => {
    const current = event.target.closest('[data-object-id]')?.dataset.objectId || focused || geometry?.visible[0] || model?.boundary?.id;
    const object = objects.get(current);
    if (event.altKey && event.key === 'ArrowLeft') { event.preventDefault(); cancelClick(); onEscape(); }
    else if ((event.key === 'ArrowRight' || (event.key === 'Enter' && event.shiftKey)) && object?.type === 'module') { event.preventDefault(); cancelClick(); onEnter(object); }
    else if (event.key === 'Enter') { event.preventDefault(); cancelClick(); choose(object, event); }
    else if (event.key.startsWith('Arrow')) {
      event.preventDefault(); const positions = { ...geometry.positions };
      geometry.routes.forEach(r => { positions[r.id] = r.label; });
      focusObject(spatialNeighbor(current, event.key, positions) || current);
    } else if (['+', '='].includes(event.key)) { event.preventDefault(); zoomBy(1.18); }
    else if (event.key === '-') { event.preventDefault(); zoomBy(1 / 1.18); }
    else if (event.key === 'Home') { event.preventDefault(); if (onHome) onHome(); else fit(); }
    else if (event.key === 'Escape') { event.preventDefault(); hideTooltip(); onEscape(); }
  });
  // Include the native canvas scroll offset in the same three-number camera
  // contract. Restoring a layer reproduces its view without a second state API.
  function getCamera() { return { x: camera.x, y: camera.y - stage.scrollTop, k: camera.k }; }
  function setCamera(value) {
    if (disposed || !value || ![value.x, value.y, value.k].every(Number.isFinite)) return;
    stage.scrollTop = 0;
    svg.call(zoomBehavior.transform, zoomIdentity.translate(value.x, value.y).scale(Math.max(.25, Math.min(5, value.k))));
  }
  function zoomBy(factor) { if (!disposed && !hidden && factor > 0) svg.call(zoomBehavior.scaleBy, factor, [width / 2, stage.scrollTop + stage.clientHeight / 2]); }
  function fit() { setCamera({ x: 0, y: 0, k: 1 }); }
  function resize() {
    if (hidden || disposed || !stage.clientWidth) return;
    const changed = width !== stage.clientWidth; width = stage.clientWidth; viewportHeight = stage.clientHeight;
    if (changed || !geometry) redraw();
  }
  const observer = new ResizeObserver(resize); observer.observe(stage); resize();
  stage.addEventListener('scroll', hideTooltip, { passive: true });
  function releasePan() { if (panView) { select(panView).on('mousemove.zoom mouseup.zoom', null); dragEnable(panView); panView = null; } }
  return { render, getCamera, setCamera, focusObject, fit, zoomBy,
    hide() { hidden = true; el.hidden = true; cancelClick(); hideTooltip(); releasePan(); },
    show() { if (!disposed) { hidden = false; el.hidden = false; resize(); if (dirty) redraw(); } },
    destroy() { disposed = true; cancelClick(); observer.disconnect(); releasePan(); stage.removeEventListener('scroll', hideTooltip); svg.on('.scope', null).on('.zoom', null); world.selectAll('*').on('.scope', null); objects.clear(); el.remove(); },
  };
}
