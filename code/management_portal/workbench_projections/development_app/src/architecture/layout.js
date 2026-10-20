import dagre from '@dagrejs/dagre';
import { forceSimulation, forceLink, forceManyBody, forceCollide, forceCenter, randomLcg } from 'd3';

// Geometry is deliberately independent of resource contents and the SDK. Edge
// orientation below is a layout constraint; it never denotes execution order.
export const NODE_SIZE = Object.freeze({ width: 184, height: 64 });
const GAP = 28;
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;

export function structuralGraph(nodes = [], edges = []) {
  const unique = new Map();
  for (const node of nodes) {
    if (node && typeof node.id === 'string' && !unique.has(node.id)) {
      unique.set(node.id, { id: node.id, type: node.type || 'module', parent: node.parent || null });
    }
  }
  const normalizedNodes = [...unique.values()].sort((a, b) => compare(a.id, b.id));
  const normalizedEdges = edges.filter(edge => edge && unique.has(edge.source) && unique.has(edge.target) && edge.source !== edge.target)
    .map(edge => ({ id: String(edge.id), source: edge.source, target: edge.target, type: edge.type || 'association' }))
    .sort((a, b) => compare(a.id, b.id) || compare(a.source, b.source) || compare(a.target, b.target));
  return { nodes: normalizedNodes, edges: normalizedEdges };
}

export function structureKey(nodes, edges) {
  return JSON.stringify(structuralGraph(nodes, edges));
}

export function nodeSize(node) {
  return node.type === 'protocol' ? { width: 148, height: 76 } : { ...NODE_SIZE };
}

function overlaps(a, b, gap = GAP) {
  return Math.abs(a.x - b.x) < (a.width + b.width) / 2 + gap && Math.abs(a.y - b.y) < (a.height + b.height) / 2 + gap;
}

// Preserve surviving IDs exactly when entering a nearby snapshot or semantic
// level. New nodes seek a free location near their newly calculated position.
export function retainPositions(positions, previous = {}, edges = []) {
  const result = Object.create(null);
  const old = previous instanceof Map ? Object.fromEntries(previous) : previous;
  for (const id of Object.keys(positions).sort(compare)) {
    const p = old[id];
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) result[id] = { ...positions[id], x: p.x, y: p.y };
  }
  const common = Object.keys(result);
  const shift = common.length ? common.reduce((s, id) => ({ x: s.x + result[id].x - positions[id].x, y: s.y + result[id].y - positions[id].y }), { x: 0, y: 0 }) : { x: 0, y: 0 };
  shift.x /= common.length || 1;
  shift.y /= common.length || 1;
  for (const id of Object.keys(positions).sort(compare)) {
    if (result[id]) continue;
    let origin = { x: positions[id].x + shift.x, y: positions[id].y + shift.y };
    const neighbors = edges.filter(e => e.source === id || e.target === id).map(e => result[e.source === id ? e.target : e.source]).filter(Boolean);
    if (common.length && neighbors.length) origin = { x: neighbors.reduce((s, n) => s + n.x, 0) / neighbors.length + NODE_SIZE.width + GAP * 2, y: neighbors.reduce((s, n) => s + n.y, 0) / neighbors.length };
    const p = { ...positions[id], ...origin };
    const occupied = Object.values(result);
    if (occupied.some(other => overlaps(p, other))) {
      // Deterministic concentric placement is only for additions; the initial
      // diagram and its hierarchy come from dagre, not a card-grid layout.
      let placed = false;
      for (let ring = 1; ring <= occupied.length + 1 && !placed; ring++) {
        const count = ring * 8;
        for (let i = 0; i < count; i++) {
          const angle = i * Math.PI * 2 / count;
          const candidate = { ...p, x: origin.x + Math.cos(angle) * ring * (NODE_SIZE.width + GAP), y: origin.y + Math.sin(angle) * ring * (NODE_SIZE.height + GAP) };
          if (!occupied.some(other => overlaps(candidate, other))) { Object.assign(p, candidate); placed = true; break; }
        }
      }
    }
    result[id] = p;
  }
  return result;
}

export function graphBounds(positions) {
  const values = Object.values(positions);
  if (!values.length) return { x: 0, y: 0, width: 1, height: 1 };
  const x = Math.min(...values.map(p => p.x - p.width / 2)) - 24;
  const y = Math.min(...values.map(p => p.y - p.height / 2)) - 32;
  const right = Math.max(...values.map(p => p.x + p.width / 2)) + 24;
  const bottom = Math.max(...values.map(p => p.y + p.height / 2)) + 24;
  return { x, y, width: right - x, height: bottom - y };
}

export function parentOutlines(nodes, positions) {
  const byId = new Map(nodes.map(n => [n.id, n]));
  const groups = new Map();
  for (const node of nodes) {
    const seen = new Set([node.id]);
    let parent = node.parent;
    while (parent && byId.has(parent) && !seen.has(parent)) {
      seen.add(parent);
      if (!groups.has(parent)) groups.set(parent, new Set([parent]));
      groups.get(parent).add(node.id);
      parent = byId.get(parent).parent;
    }
  }
  return [...groups].map(([id, ids]) => {
    const bounds = graphBounds(Object.fromEntries([...ids].filter(key => positions[key]).map(key => [key, positions[key]])));
    let maxDepth = 1;
    for (const child of ids) {
      let current = child, depth = 0;
      const seen = new Set();
      while (current && current !== id && !seen.has(current)) { seen.add(current); depth++; current = byId.get(current)?.parent; }
      if (current === id) maxDepth = Math.max(maxDepth, depth);
    }
    const extra = (maxDepth - 1) * 26;
    return { id, x: bounds.x - extra, y: bounds.y - extra, width: bounds.width + extra * 2, height: bounds.height + extra * 2 };
  })
    .sort((a, b) => b.width * b.height - a.width * a.height || compare(a.id, b.id));
}

// Clip a connector to the rectangle or protocol diamond boundary, so lines do
// not pass through labels. Returned endpoints remain valid after manual drag.
export function edgeEndpoints(source, target, sourceType, targetType) {
  const clip = (a, b, type) => {
    const dx = b.x - a.x, dy = b.y - a.y;
    if (!dx && !dy) return { x: a.x, y: a.y };
    const hx = type === 'protocol' ? 18 : a.width / 2;
    const hy = type === 'protocol' ? 18 : a.height / 2;
    const t = type === 'protocol' ? 1 / (Math.abs(dx) / hx + Math.abs(dy) / hy) : Math.min(dx ? hx / Math.abs(dx) : Infinity, dy ? hy / Math.abs(dy) : Infinity);
    return { x: a.x + dx * t, y: a.y + dy * t };
  };
  return [clip(source, target, sourceType), clip(target, source, targetType)];
}

export function spatialNeighbor(id, direction, positions) {
  const current = positions[id];
  if (!current) return null;
  const [vx, vy] = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[direction] || [0, 0];
  let best = null, score = Infinity;
  for (const candidate of Object.keys(positions).sort(compare)) {
    if (candidate === id) continue;
    const dx = positions[candidate].x - current.x, dy = positions[candidate].y - current.y;
    const forward = dx * vx + dy * vy;
    if (forward <= 0) continue;
    const perpendicular = Math.abs(dx * vy - dy * vx);
    const value = Math.hypot(dx, dy) + perpendicular * 2;
    if (value < score) { score = value; best = candidate; }
  }
  return best;
}

export function layoutGraph(nodes = [], edges = [], { previous = {} } = {}) {
  const graphData = structuralGraph(nodes, edges);
  // Dense graphs can create thousands of dummy ranks in a layered layout.
  // Use a bounded, seeded force layout in the worker for this size instead.
  const overview = graphData.nodes.length <= 40 && !graphData.nodes.some(node => node.parent) && graphData.edges.some(edge => edge.type === 'participation');
  if (overview || graphData.nodes.length > 160 || graphData.edges.length > 400) {
    const forceNodes = graphData.nodes.map(node => ({ ...node, ...nodeSize(node) }));
    const forceEdges = graphData.edges.map(edge => ({ ...edge }));
    const ids = new Set(forceNodes.map(n => n.id));
    for (const node of forceNodes) {
      if (node.parent && ids.has(node.parent) && node.parent !== node.id) forceEdges.push({ source: node.parent, target: node.id });
    }
    const simulation = forceSimulation(forceNodes).stop().randomSource(randomLcg(0.415))
      .force('link', forceLink(forceEdges).id(node => node.id).distance(overview ? 185 : 230).strength(overview ? 0.65 : 0.4))
      .force('charge', forceManyBody().strength(overview ? -260 : -450))
      .force('collision', forceCollide(node => Math.hypot(node.width, node.height) / 2 + 14).iterations(2))
      .force('center', forceCenter(0, 0));
    simulation.tick(180);
    if (overview) {
      // An undirected overview uses the wide map surface, not two long ranks.
      for (const node of forceNodes) { node.x *= 1.22; node.y *= 0.62; }
      for (let pass = 0; pass < 60; pass++) {
        let moved = false;
        for (let i = 0; i < forceNodes.length; i++) for (let j = i + 1; j < forceNodes.length; j++) {
          const a = forceNodes[i], b = forceNodes[j], dx = b.x - a.x, dy = b.y - a.y;
          const ox = (a.width + b.width) / 2 + 14 - Math.abs(dx), oy = (a.height + b.height) / 2 + 18 - Math.abs(dy);
          if (ox > 0 && oy > 0) {
            moved = true;
            if (ox < oy * 1.5) { const delta = (ox + 1) * (dx < 0 ? -1 : 1) / 2; a.x -= delta; b.x += delta; }
            else { const delta = (oy + 1) * (dy < 0 ? -1 : 1) / 2; a.y -= delta; b.y += delta; }
          }
        }
        if (!moved) break;
      }
    }
    const calculated = Object.fromEntries(forceNodes.map(node => [node.id, { x: node.x, y: node.y, width: node.width, height: node.height }]));
    const positions = (previous instanceof Map ? previous.size : Object.keys(previous).length) ? retainPositions(calculated, previous, graphData.edges) : calculated;
    return { positions, bounds: graphBounds(positions), outlines: parentOutlines(graphData.nodes, positions) };
  }
  const graph = new dagre.graphlib.Graph({ multigraph: true, compound: true });
  graph.setGraph({ rankdir: 'LR', ranker: 'longest-path', nodesep: 34, ranksep: graphData.nodes.some(n => n.parent) ? 28 : 72, marginx: 32, marginy: 40 });
  graph.setDefaultEdgeLabel(() => ({}));
  // Use internal safe keys: graphlib's dictionaries otherwise treat IDs such
  // as __proto__ as object properties rather than ordinary resource IDs.
  const keys = new Map(graphData.nodes.map((node, i) => [node.id, `n${String(i).padStart(6, '0')}`]));
  const byId = new Map(graphData.nodes.map(node => [node.id, node]));
  const parents = new Map();
  for (const node of graphData.nodes) {
    const seen = new Set([node.id]);
    let current = node.parent, valid = true;
    while (current && byId.has(current)) {
      if (seen.has(current)) { valid = false; break; }
      seen.add(current); current = byId.get(current).parent;
    }
    if (valid && keys.has(node.parent)) parents.set(node.id, node.parent);
  }
  const containers = new Set(parents.values());
  for (const node of graphData.nodes) {
    graph.setNode(keys.get(node.id), nodeSize(node));
    if (containers.has(node.id)) graph.setNode(`c${keys.get(node.id)}`, {});
  }
  for (const node of graphData.nodes) {
    const parent = parents.get(node.id), key = keys.get(node.id);
    if (containers.has(node.id)) {
      graph.setParent(key, `c${key}`);
      if (parent) graph.setParent(`c${key}`, `c${keys.get(parent)}`);
    } else if (parent) graph.setParent(key, `c${keys.get(parent)}`);
  }
  const ancestor = (a, b) => {
    let parent = parents.get(b);
    while (parent) { if (parent === a) return true; parent = parents.get(parent); }
    return false;
  };
  // Membership defines hierarchy; undirected relations use canonical ID order
  // for reproducible placement only, never for a displayed arrow.
  graphData.edges.forEach((edge, index) => {
    const [a, b] = ancestor(edge.source, edge.target) || edge.type === 'membership' ? [edge.source, edge.target] : ancestor(edge.target, edge.source) ? [edge.target, edge.source] : [edge.source, edge.target].sort(compare);
    graph.setEdge(keys.get(a), keys.get(b), { weight: edge.type === 'membership' ? 3 : 1 }, `edge:${index}`);
  });
  graphData.nodes.forEach(node => {
    const parent = parents.get(node.id);
    if (parent) graph.setEdge(keys.get(parent), keys.get(node.id), { weight: 4 }, `parent:${keys.get(node.id)}`);
  });
  if (graphData.nodes.length) dagre.layout(graph);
  const calculated = Object.fromEntries(graphData.nodes.map(node => {
    const p = graph.node(keys.get(node.id));
    return [node.id, { x: p.x, y: p.y, width: p.width, height: p.height }];
  }));
  const positions = (previous instanceof Map ? previous.size : Object.keys(previous).length) ? retainPositions(calculated, previous, graphData.edges) : calculated;
  return { positions, bounds: graphBounds(positions), outlines: parentOutlines(graphData.nodes, positions) };
}


// Semantic overview: all top-level responsibilities in a readable vertical
// rank, with one explicitly selected protocol's participant relationship.
// Nonparticipants stay visible; choosing another protocol never changes facts.
export function layoutOverview(nodes, edges, width, protocolId) {
  const modules = nodes.filter(n => n.type === 'module');
  const protocol = nodes.find(n => n.type === 'protocol' && n.id === protocolId);
  const padding = 20, moduleWidth = Math.min(430, (width - 100) * .52);
  const protocolWidth = Math.min(224, width - moduleWidth - 80);
  const positions = Object.create(null), row = 92;
  modules.forEach((n, i) => { positions[n.id] = { x: padding + moduleWidth / 2, y: 50 + i * row, width: moduleWidth, height: 76 }; });
  if (protocol) {
    positions[protocol.id] = { x: width - padding - protocolWidth / 2, y: 50 + row * 1.5, width: protocolWidth, height: 96 };
  }
  return { positions, height: Math.max(360, modules.length * row + 12), algorithm: 'protocol-participation-ranks' };
}

// This is a reading order for the current Portal runtime map, not a call order.
// Unknown runtimes remain accessible in the supporting layer. Never synthesize
// a missing module, and never infer component ownership from an ID prefix.
export const RUNTIME_CORE = Object.freeze(['interface', 'web', 'resources', 'lineage', 'execution', 'worker', 'providers', 'storage', 'postgres', 'model-services']);
const runtimeSlots = {
  interface: [1, 40], web: [1, 140], resources: [0, 250], lineage: [1, 250], execution: [2, 250],
  worker: [1, 374], providers: [2, 374], storage: [0, 510], postgres: [1, 510], 'model-services': [2, 510],
};
const moduleKey = value => value && (value.startsWith('module:') ? value : `module:${value}`);
export const runtimeParent = node => moduleKey(node.data?.parent || node.parent);

/** Full text wrapping, with no ellipsis and no knowledge-dependent aliases. */
export function wrapLabel(value, width, size = 14) {
  const lines = []; let line = '', used = 0;
  const tokens = String(value || '').match(/[A-Za-z0-9_-]+|[^A-Za-z0-9_-]/gu) || [];
  for (const token of tokens) {
    const advance = [...token].reduce((sum, char) => sum + (char.codePointAt(0) > 255 ? size : size * .57), 0);
    if (token === '\n' || (line && used + advance > width)) { lines.push(line.trimEnd()); line = ''; used = 0; }
    if (token === '\n' || (!line && token === ' ')) continue;
    if (advance > width) {
      // Very narrow surfaces may split a long identifier at camel-case words.
      const pieces = token.split(/(?=[A-Z][a-z])/);
      if (pieces.length > 1) {
        for (const piece of pieces) { const amount = piece.length * size * .57; if (line && used + amount > width) { lines.push(line); line = ''; used = 0; } line += piece; used += amount; }
      } else {
        for (const char of token) { if (line && used + size * .57 > width) { lines.push(line); line = ''; used = 0; } line += char; used += size * .57; }
      }
    } else { line += token; used += advance; }
  }
  if (line) lines.push(line);
  return lines.length ? lines : [''];
}

export function layoutRuntime(nodes = [], { width = 625, showSupporting = false, reveal = [] } = {}) {
  width = Math.max(320, width);
  const byId = new Map(nodes.filter(n => n.type === 'module').map(n => [n.id, n]));
  const top = [...byId.values()].filter(n => !runtimeParent(n));
  const supporting = top.filter(n => !RUNTIME_CORE.includes(n.localId || n.id.replace(/^module:/, '')));
  const revealSet = new Set(reveal);
  const visible = top.filter(n => showSupporting || RUNTIME_CORE.includes(n.localId || n.id.replace(/^module:/, '')) || revealSet.has(n.id));
  // At 625px this is native-size text on three service columns. Narrower
  // surfaces retain the layers and use more vertical room, never a desktop fit.
  const column = (width - 32) / 3, nodeWidth = Math.min(200, column - 18);
  const x = [16 + column / 2, width / 2, width - 16 - column / 2];
  const narrow = width < 500, stretch = narrow ? 1.24 : 1;
  const positions = Object.create(null), components = Object.create(null);
  let extra = 0;
  for (const node of visible) {
    const local = node.localId || node.id.replace(/^module:/, '');
    const slot = runtimeSlots[local] || [extra % 3, 690 + Math.floor(extra++ / 3) * 122];
    const lines = wrapLabel(node.label || node.id, nodeWidth - 20, 15);
    positions[node.id] = { x: x[slot[0]], y: slot[1] * stretch, width: nodeWidth, height: Math.max(78, lines.length * 18 + 24) };
  }
  // Components belong to their own scope canvas. The overview never embeds a
  // second independently scrolling graph inside a runtime node.
  const bounds = graphBounds(Object.fromEntries(Object.entries(positions).filter(([, p]) => !p.parent)));
  return { positions, components, supporting: supporting.map(n => n.id), visible: visible.map(n => n.id), width,
    height: Math.max(558 * stretch, bounds.y + bounds.height + 8), stretch, algorithm: 'runtime-layers' };
}

/** A local component canvas. The caller supplies authorized semantic objects;
 * geometry never invents components, expands parent protocols, or infers calls.
 * A boundary is required even for a leaf, where nodes may legitimately be empty. */
export function layoutScope(model, { width = 625 } = {}) {
  width = Math.max(320, width);
  const root = model.boundary;
  if (!model.scopeRoot || !root || root.id !== moduleKey(model.scopeRoot)) throw new Error('Scoped graph requires its real root boundary');
  const nodes = [...new Map((model.nodes || []).filter(n => n.type === 'module' && runtimeParent(n) === root.id).map(n => [n.id, n])).values()];
  const direct = new Set(nodes.map(n => n.id));
  const availableContext = [...new Map((model.context || []).filter(n => n.type === 'module' && n.id !== root.id && !direct.has(n.id)).map(n => [n.id, n])).values()];
  const authorized = new Set([root.id, ...direct, ...availableContext.map(n => n.id)]);
  const relations = (model.componentRelations || []).filter(r => r.endpoints?.length >= 2 && r.endpoints.every(e => {
    const id = moduleKey(e.displayModuleId);
    return authorized.has(id) && (e.display !== 'direct_component' || direct.has(id)) && (e.display !== 'root_boundary_role' || id === root.id);
  }));
  // Only an actual local collaboration earns a visible context boundary.
  // The complete participant set stays on the protocol object opened by the
  // compact protocol selector; it is never inherited by a direct child.
  const relationContext = new Set(relations.flatMap(r => r.endpoints.map(e => moduleKey(e.displayModuleId))));
  const context = availableContext.filter(n => relationContext.has(n.id));
  const positions = Object.create(null), padding = 28, columns = Math.min(Math.max(1, nodes.length), width >= 820 ? 4 : width >= 480 ? 2 : 1), gap = 24;
  const nodeWidth = (width - padding * 2 - gap * (columns - 1)) / columns;
  const titleLines = wrapLabel(root.label, width - 68, 19);
  const dutyLines = wrapLabel(root.data?.responsibility || root.subtitle || '', width - 68, 14);
  const headerBottom = 30 + titleLines.length * 24 + dutyLines.length * 19;
  let cursor = headerBottom + 22;
  for (let i = 0; i < nodes.length; i += columns) {
    const row = nodes.slice(i, i + columns);
    const heights = row.map(n => 20 + wrapLabel(n.label, nodeWidth - 24, 16).length * 20);
    const rowHeight = Math.max(50, ...heights);
    row.forEach((n, j) => { positions[n.id] = { x: padding + nodeWidth / 2 + j * (nodeWidth + gap), y: cursor + rowHeight / 2, width: nodeWidth, height: rowHeight }; });
    cursor += rowHeight + 18;
  }
  if (!nodes.length) cursor += 72;
  const componentsBottom = cursor;
  const relationColumns = width >= 480 ? 2 : 1, relationWidth = (width - 104) / relationColumns - 16;
  const relationRows = [];
  if (relations.length) cursor += 8;
  for (let i = 0; i < relations.length; i += relationColumns) {
    const row = relations.slice(i, i + relationColumns);
    const lines = row.map(r => wrapLabel(r.title, relationWidth - 16, 14));
    const height = Math.max(...lines.map(value => value.length * 18 + 14));
    row.forEach((r, j) => relationRows.push({ id: `relation:${r.id}`, relation: r, lines: lines[j], meaning: relationMeaning(r),
      x: 52 + (relationWidth + 32) * j + relationWidth / 2, y: cursor + height / 2, height, labelWidth: relationWidth }));
    cursor += height + 18;
  }
  const boundary = { x: 16, y: 12, width: width - 32, height: Math.max(180, cursor - 18), titleLines, dutyLines, headerBottom };
  // A root boundary has a real semantic identity, but is not a child node.
  positions[root.id] = { x: width / 2, y: 40, width: width - 64, height: 40, boundary: true };
  cursor = boundary.y + boundary.height + 24;
  const protocols = (model.protocols || []).map(p => ({ ...p, participants: [...new Set(p.participants || [])].filter(id => authorized.has(id)) }))
    .filter(p => p.participants.length >= 2 && p.participants.some(id => id === root.id || direct.has(id)));
  const contextStart = cursor;
  const contextColumns = width >= 480 ? Math.min(3, Math.max(1, context.length)) : 1;
  const contextWidth = (width - padding * 2 - 16 * (contextColumns - 1)) / contextColumns;
  context.forEach((n, i) => {
    const lines = wrapLabel(n.label, contextWidth - 24, 14);
    const previousRow = Math.floor(i / contextColumns);
    const rowHeight = 32 + Math.max(...context.slice(previousRow * contextColumns, (previousRow + 1) * contextColumns).map(item =>
      wrapLabel(item.label, contextWidth - 24, 14).length * 18));
    if (i % contextColumns === 0 && i) cursor += 16;
    const y = cursor + rowHeight / 2;
    positions[n.id] = { x: padding + contextWidth / 2 + (i % contextColumns) * (contextWidth + 16), y, width: contextWidth, height: rowHeight, context: true, lines };
    if (i % contextColumns === contextColumns - 1 || i === context.length - 1) cursor += rowHeight;
  });
  const parentProtocols = model.parentProtocols || [];
  const endpoint = (id, rowX, rowY) => {
    const p = positions[id];
    if (id === root.id) return rowY < boundary.y + boundary.height
      ? { x: rowX > width / 2 ? width - 16 : 16, y: rowY, boundary: true }
      : { x: rowX > width / 2 ? width - 34 : 34, y: boundary.y + boundary.height, boundary: true };
    const below = rowY > p.y;
    return { x: p.x, y: p.y + (below ? p.height / 2 : -p.height / 2) };
  };
  let channel = 0;
  const makeRoute = (id, rowX, rowY, i, arrow = null) => {
    const port = endpoint(id, rowX, rowY), offset = 6 + (++channel % 6) * 3;
    // Each relation owns its route. Shared participants do not become a single
    // visual bus joining unrelated protocols or consistency contracts.
    const side = rowX < width / 2 ? 16 + offset : width - 16 - offset;
    const approach = port.y + (rowY > port.y ? 1 : -1) * (6 + channel % 3 * 3);
    const points = port.boundary && port.y === rowY ? [{ x: port.x, y: rowY }, { x: rowX, y: rowY }]
      : [{ x: port.x, y: port.y }, { x: port.x, y: approach }, { x: side, y: approach }, { x: side, y: rowY }, { x: rowX, y: rowY }];
    return { participant: id, points, arrow };
  };
  const routes = [
    ...relationRows.map(row => ({ ...row, label: { x: row.x, y: row.y },
      routes: row.relation.endpoints.map((e, i, all) => {
        const direction = row.relation.interaction?.direction, initiator = row.relation.interaction?.initiator_module_id;
        // Arrows express only the supplied local interaction contract.
        const arrow = direction === 'call' && initiator ? (e.moduleId === initiator ? 'end' : 'start')
          : ['request_response', 'call_with_callback'].includes(direction) ? 'both' : null;
        return { ...makeRoute(moduleKey(e.displayModuleId), row.x, row.y, i, arrow), endpoint: e };
      }) })),
  ];
  return { positions, visible: nodes.map(n => n.id), context: context.map(n => n.id), boundary,
    routes, protocols, parentProtocols, componentsBottom, contextStart,
    width, height: Math.max(250, cursor + 12), algorithm: 'direct-component-scope' };
}

function relationMeaning(relation) {
  const interaction = relation.interaction || {};
  const kind = relation.kind === 'shared_transaction' ? '同一事务的一致性关系 · 无调用方向'
    : ({ request_response: '请求与响应', call_with_callback: '调用与回调', call: '局部调用', undirected_consistency: '一致性关系 · 无调用方向' }[interaction.direction] || '具名关系 · 方向未登记');
  return [kind, interaction.conditions?.length || interaction.condition ? '条件见关系详情' : null].filter(Boolean).join(' · ');
}

// Each overview fragment is a subset of one actual participant set. These are
// deliberately not pairwise calls. Selection replaces it with the complete set.
const overviewProtocols = {
  'http-interface': { members: ['interface', 'web'], hub: [1, 90], label: [1, 90], width: 280 },
  'signed-rpc': { members: ['web', 'resources', 'lineage', 'execution'], hub: [1, 196], label: [1, 196], width: 290 },
  'resident-execution': { members: ['lineage', 'execution', 'worker'], hub: [1, 312], label: [.65, 312], width: 260 },
  'provider-native': { members: ['worker', 'providers'], hub: [1.5, 374], label: [2, 312], width: 190 },
  'file-storage': { members: ['resources', 'storage'], hub: [0, 442], label: [0, 442], width: 170 },
  'database-schema': { members: ['lineage', 'postgres'], hub: [.5, 442], label: [1, 442], width: 190 },
  'model-api': { members: ['providers', 'model-services'], hub: [2, 442], label: [2, 442], width: 180 },
};

const segmentHits = (a, b, p) => {
  const left = p.x - p.width / 2 + .1, right = p.x + p.width / 2 - .1;
  const top = p.y - p.height / 2 + .1, bottom = p.y + p.height / 2 - .1;
  return a.x === b.x ? a.x > left && a.x < right && Math.max(a.y, b.y) > top && Math.min(a.y, b.y) < bottom
    : a.y > top && a.y < bottom && Math.max(a.x, b.x) > left && Math.min(a.x, b.x) < right;
};

function participationRoute(p, hub, obstacles, width) {
  const ports = [{ x: p.x, y: p.y - p.height / 2 }, { x: p.x, y: p.y + p.height / 2 },
    { x: p.x - p.width / 2, y: p.y }, { x: p.x + p.width / 2, y: p.y }];
  const railsX = [8, width - 8, hub.x, ...obstacles.flatMap(o => [o.x - o.width / 2 - 9, o.x + o.width / 2 + 9])];
  const railsY = [hub.y, ...obstacles.flatMap(o => [o.y - o.height / 2 - 12, o.y + o.height / 2 + 12])];
  let best = null, cost = Infinity;
  for (const port of ports) {
    const candidates = [
      ...railsX.map(x => [port, { x, y: port.y }, { x, y: hub.y }, hub]),
      ...railsY.map(y => [port, { x: port.x, y }, { x: hub.x, y }, hub]),
    ];
    for (const points of candidates) {
      let next = 0;
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1], b = points[i];
        next += Math.abs(b.x - a.x) + Math.abs(b.y - a.y);
        for (const o of obstacles) if (segmentHits(a, b, o)) next += 100000;
      }
      if (next < cost) { cost = next; best = points; }
    }
  }
  return best;
}

export function runtimeProtocolGeometry(protocols = [], layout, selected = null) {
  const { positions, width, stretch = 1 } = layout;
  const column = (width - 32) / 3, at = n => 16 + column / 2 + column * n;
  const obstacles = Object.values(positions).filter(p => !p.parent);
  return protocols.flatMap(protocol => {
    const key = protocol.localId || protocol.id.replace(/^protocol:/, ''), spec = overviewProtocols[key];
    const isSelected = protocol.id === selected;
    if (!isSelected && (!spec || selected)) return [];
    const all = [...new Set(protocol.participants || [])];
    const participants = all.filter(id => positions[id] && (!spec || isSelected || spec.members.includes(id.replace(/^module:/, ''))));
    if (participants.length < 2) return [];
    const hub = isSelected ? { x: at(.5), y: 196 * stretch } : { x: at(spec.hub[0]), y: spec.hub[1] * stretch };
    const label = isSelected ? { x: width / 2, y: 196 * stretch } : { x: at(spec.label[0]), y: spec.label[1] * stretch };
    const labelWidth = Math.min(isSelected ? width - 56 : spec.width, isSelected ? width - 56 : column - 12);
    // The wide HTTP/RPC label lives in a clear whole-row corridor.
    const available = !isSelected && ['http-interface', 'signed-rpc', 'resident-execution'].includes(key) ? Math.min(spec.width, width - 40) : labelWidth;
    const routes = participants.map(id => ({ participant: id, points: participationRoute(positions[id], hub, obstacles, width) }));
    return [{ id: protocol.id, protocol, participants, total: all.length, partial: participants.length < all.length,
      label, labelWidth: available, routes, hub, selected: isSelected }];
  });
}
