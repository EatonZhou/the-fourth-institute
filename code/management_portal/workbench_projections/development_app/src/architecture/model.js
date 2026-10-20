import { schemaMatches } from '../shared/format-tags.js';
import { matchesCurrentModuleMap } from '../shared/current-module-map.js';
// Architecture relations are evidence associations, never inferred call order.
export const REPOSITORY = 'repo_711b6ab6d5c8';
export const MAP_REF = `${REPOSITORY}/file/code/architecture/modules.yaml`;
export const EVOLUTION_REF = `${REPOSITORY}/file/code/architecture/evolution.yaml`;
export const MANIFEST_REF = 'workspace_04ad72d24263/file/release-runtime-v1/workspace_13209cb6666b/architecture-evidence/manifest.json';
export const unique = values => [...new Set(values)];
export const key = (type, id) => `${type}:${id}`;

export function sourceAnchors(text) {
  const result = { modules: {}, protocols: {}, directions: {} };
  let section = '';
  String(text).split('\n').forEach((line, index) => {
    if (/^(modules|protocols|directions|plans):\s*$/.test(line)) section = line.split(':')[0];
    const match = line.match(/^  - id:\s*["']?([\w.-]+)["']?\s*(?:#.*)?$/);
    if (match && result[section]) result[section][match[1]] = index + 1;
  });
  return result;
}

export function validateMap(map) {
  if (!map || !schemaMatches(map.schema, 'tfi.portal-architecture.v1') || !Array.isArray(map.modules) || !Array.isArray(map.protocols)) throw new Error('模块地图格式不受支持');
  if (map.model_version != null && ![1, 2].includes(map.model_version)) throw new Error('模块建模版本不受支持');
  for (const items of [map.modules, map.protocols]) {
    if (items.some(x => !x || typeof x.id !== 'string') || unique(items.map(x => x.id)).length !== items.length) throw new Error('地图包含无效或重复身份');
  }
  const ids = new Set(map.modules.map(m => m.id));
  for (const m of map.modules) {
    if (m.parent && !ids.has(m.parent)) throw new Error('模块父级缺失');
    const chain = new Set([m.id]); let parent = m.parent;
    while (parent) { if (chain.has(parent)) throw new Error('模块父级形成循环'); chain.add(parent); parent = map.modules.find(x => x.id === parent)?.parent; }
  }
  if (map.model_version === 2) {
    const owned = new Set();
    for (const item of [...map.modules, ...map.protocols]) for (const ref of item.designs || []) {
      if (owned.has(ref)) throw new Error('Design 主归属不唯一');
      owned.add(ref);
    }
  }
  return map;
}

export const modelVersion = data => data.modelVersion ?? data.model_version ?? 1;

// The shared current-map contract checks source identity and model semantics.
export function evolutionBinding(data) {
  if (!data.evolutionAvailable) return { valid: false, reason: '演化来源不可读或缺失' };
  if (!schemaMatches(data.mapSchema, 'tfi.portal-architecture.v1')) return { valid: false, reason: '模块地图格式不受支持' };
  if (schemaMatches(data.evolutionSchema, 'tfi.portal-evolution.v1') && modelVersion(data) === 1) return { valid: true, reason: '' };
  if (!schemaMatches(data.evolutionSchema, 'tfi.portal-evolution.v2')) return { valid: false, reason: '演化格式不受支持' };
  const binding = data.moduleMap;
  if (!matchesCurrentModuleMap(binding, { ref: MAP_REF, schema: data.mapSchema, modelVersion: modelVersion(data) })) {
    return { valid: false, reason: '方向模块映射与当前地图版本不一致，待重新映射' };
  }
  return { valid: true, reason: '' };
}

export function ancestors(modules, id) {
  const out = []; const seen = new Set(); let item = modules.find(m => m.id === id);
  while (item && !seen.has(item.id)) { out.unshift(item.id); seen.add(item.id); item = modules.find(m => m.id === item.parent); }
  return out;
}

export function semanticObject(data, node) {
  if (data.mode !== 'current' || !node?.ref) return null;
  return { ref: node.ref, kind: ['module', 'protocol', 'instruction', 'direction'].includes(node.type) ? `${node.type}:${node.localId}` : node.type,
    status: ['plan', 'direction'].includes(node.type) ? 'proposed' : 'current', source: node.source || data.mapRef, observed_at: data.observedAt };
}

// Lookup is against viewer-authorized facts, independent of camera and expansion.
export function semanticNode(data, type, id) {
  const status = 'current';
  const source = data.mapRef;
  const anchor = (section, localId) => { const line = data.anchors?.[section]?.[localId]; return source + (Number.isInteger(line) && line > 0 ? `#L${line}` : ''); };
  if (type === 'module' || type === 'protocol') {
    const item = data[type === 'module' ? 'modules' : 'protocols']?.find(x => x.id === id);
    if (!item) return null;
    return { id: key(type, id), type, localId: id, label: item.title || id,
      subtitle: type === 'module' ? item.responsibility || '' : item.description || '',
      parent: type === 'module' && item.parent ? key('module', item.parent) : undefined,
      ref: anchor(type === 'module' ? 'modules' : 'protocols', id), source, status, data: item };
  }
  if (type === 'design') {
    const meta = data.designMeta?.[id];
    if (!meta) return null;
    return { id: key(type, id), type, localId: id, label: meta.title || id,
      subtitle: meta.metaness == null ? 'Design · 未分级' : `Design · metaness ${meta.metaness}`,
      ref: id, source, status, data: meta };
  }
  if (type === 'plan') {
    const plan = data.plans?.find(p => p.id === id);
    return plan ? { id: key(type, id), type, localId: id, label: plan.title || id, subtitle: '拟议关联',
      ref: id, source: data.evolutionRef, status: 'proposed', data: plan } : null;
  }
  if (type === 'instruction' && data.mode === 'current') {
    const instruction = data.instructions?.find(i => (i.instruction || i.name) === id);
    const owner = instruction && data.modules.find(m => m.instruction_owners?.includes(instruction.owner));
    return owner ? { id: key(type, id), type, localId: id, label: id, subtitle: `owner · ${instruction.owner}`,
      ref: anchor('modules', owner.id), source, status, data: instruction } : null;
  }
  return null;
}

// v2 directions own their range; Plans are an independent optional association.
export function directionSelection(data, object) {
  if (data.mode !== 'current' || !data.evolutionAvailable || object.source !== data.evolutionRef ||
      object.ref?.split('#')[0] !== data.evolutionRef || !object.kind?.startsWith('direction:')) return null;
  const id = object.kind.slice('direction:'.length);
  const direction = data.directions?.find(d => d.id === id);
  const line = data.evolutionAnchors?.directions[id];
  if (!direction || !line) return null;
  const direct = schemaMatches(data.evolutionSchema, 'tfi.portal-evolution.v2') || modelVersion(data) === 2;
  const binding = evolutionBinding(data);
  const plans = data.plans.filter(p => direct ? direction.plans?.includes(p.id) : p.directions?.includes(id));
  const verified = plans.filter(p => p.module_mapping !== 'needs_review');
  const declared = unique(direct ? direction.modules || [] : verified.flatMap(p => p.modules));
  const modules = binding.valid && direction.module_mapping !== 'needs_review' ? declared.filter(id => data.modules.some(m => m.id === id)) : [];
  return { id: key('direction', id), type: 'direction', localId: id, label: direction.title || id,
    subtitle: direction.description || '', status: 'proposed', ref: `${data.evolutionRef}#L${line}`, source: data.evolutionRef,
    data: { ...direction, modules, plans, module_roles: Object.fromEntries(Object.entries(direction.module_roles || {}).filter(([id]) => modules.includes(id))),
      declaredModules: declared, mappingValid: binding.valid, mappingReason: binding.reason, mappingIssue: binding.reason,
      pendingPlans: plans.filter(p => p.module_mapping === 'needs_review'),
      incomplete: !binding.valid || direction.module_mapping === 'needs_review' || declared.some(id => !modules.includes(id)) } };
}

export function auditMatches(snapshot, audit) {
  return !!snapshot && !!audit && audit.commit === snapshot.commit && Object.entries(audit.inputs?.files || {}).length > 0 &&
    Object.entries(audit.inputs.files).every(([path, hash]) => snapshot.files?.[path]?.sha256 === hash);
}

export function impactOf(data, node, expanded = false) {
  const modules = new Set(); const paths = []; const protocols = new Set();
  const runtime = modelVersion(data) === 2;
  const add = (id, relation, evidence) => { if (data.modules.some(m => m.id === id)) { modules.add(id); paths.push({ module: id, relation, evidence }); } };
  if (node.type === 'module') add(node.localId, '起点模块', data.mapRef);
  if (node.type === 'design') {
    for (const m of data.modules) if (m.designs?.includes(node.ref)) add(m.id, 'Design 归属', data.mapRef);
    for (const p of data.protocols) {
      if (runtime && p.designs?.includes(node.ref)) {
        protocols.add(p.id);
        for (const id of p.modules) add(id, 'Design 归属协议的参与端点', data.mapRef);
      }
      if ((runtime ? p.related_designs : p.designs)?.includes(node.ref)) {
        protocols.add(p.id);
        paths.push({ protocol: p.id, relation: '协议依据（非归属）', evidence: node.ref });
      }
    }
  }
  if (node.type === 'file') for (const match of node.data.module_matches || []) add(match.module_id, '代码路径匹配', data.indexRef);
  if (node.type === 'protocol') {
    if (data.protocols.some(p => p.id === node.localId)) protocols.add(node.localId);
    for (const id of node.data.modules) add(id, '协议参与', data.mapRef);
  }
  const mappingValid = !runtime || evolutionBinding(data).valid;
  if (node.type === 'plan' && mappingValid && (!runtime || node.data.module_mapping !== 'needs_review')) for (const id of node.data.modules) add(id, '拟议影响', data.evolutionRef);
  if (node.type === 'direction' && mappingValid) for (const id of node.data.modules) add(id, runtime ? '方向声明的已核实模块范围' : '方向经可读 Plan 的已核实模块关联', data.evolutionRef);
  const endpointCovers = (endpoint, id) => runtime ? ancestors(data.modules, id).includes(endpoint) : endpoint === id;
  const collect = () => {
    let changed = false;
    if (expanded && !runtime) for (const id of [...modules]) {
      const parent = data.modules.find(m => m.id === id)?.parent;
      if (parent && !modules.has(parent)) { add(parent, '父模块归属（关联扩展）', data.mapRef); changed = true; }
    }
    for (const p of data.protocols) if (p.modules.some(endpoint => [...modules].some(id => endpointCovers(endpoint, id))) || (!runtime && node.type === 'design' && p.designs?.includes(node.ref))) {
      protocols.add(p.id);
      if (expanded) for (const id of p.modules) if (!modules.has(id) && data.modules.some(m => m.id === id)) { add(id, '协议关联扩展（非调用依赖）', data.mapRef); changed = true; }
    }
    return changed;
  };
  if (expanded) { while (collect()) { /* finite authorized module set */ } } else collect();
  const plans = mappingValid ? data.plans.filter(p => (!runtime || p.module_mapping !== 'needs_review') && p.modules.some(endpoint => [...modules].some(id => endpointCovers(endpoint, id)))) : [];
  return { modules: [...modules], protocols: [...protocols], plans: plans.map(p => p.id), paths,
    unknown: modules.size === 0 && (!runtime || protocols.size === 0), highlighted: new Set([...modules].map(id => key('module', id)).concat([...protocols].map(id => key('protocol', id)), plans.map(p => key('plan', p.id)))) };
}

export function graphForView(data, view) {
  if (modelVersion(data) === 2) return graphForScope(data, view);
  const nodes = [], edges = [], visible = new Set();
  const status = '当前';
  const branchPath = ancestors(data.modules, view.branch);
  const branch = data.modules.find(m => m.id === view.branch);
  for (const m of data.modules) if (!m.parent || branchPath.includes(m.parent) || branchPath.includes(m.id)) visible.add(m.id);
  for (const id of view.scopeModules || []) for (const ancestor of ancestors(data.modules, id)) visible.add(ancestor);
  const represent = id => { let m = data.modules.find(x => x.id === id); while (m && !visible.has(m.id)) m = data.modules.find(x => x.id === m.parent); return m?.id; };
  for (const m of data.modules.filter(m => visible.has(m.id))) {
    nodes.push({ id: key('module', m.id), type: 'module', localId: m.id, label: m.title, subtitle: `${status} · ${m.designs.length} Design`, parent: m.parent ? key('module', m.parent) : undefined,
      ref: `${data.mapRef}#L${data.anchors.modules[m.id] || 1}`, source: data.mapRef, data: m });
    if (m.parent && visible.has(m.parent)) edges.push({ id: `contains:${m.id}`, source: key('module', m.parent), target: key('module', m.id), type: 'membership', label: '子模块' });
  }
  for (const p of data.protocols) {
    const participants = unique(p.modules.map(represent).filter(Boolean));
    if (!participants.length) continue;
    nodes.push({ id: key('protocol', p.id), type: 'protocol', localId: p.id, label: p.id, subtitle: p.description || '', ref: `${data.mapRef}#L${data.anchors.protocols[p.id] || 1}`, source: data.mapRef, data: p });
    for (const id of participants) edges.push({ id: `participates:${p.id}:${id}`, source: key('module', id), target: key('protocol', p.id), type: 'participation', label: '协议参与（无方向）' });
  }
  const addDetail = (node, owner, relation) => { if (!nodes.some(n => n.id === node.id)) nodes.push(node); edges.push({ id: `${relation}:${owner}:${node.id}`, source: key('module', owner), target: node.id, type: 'association', label: relation }); };
  const detailModules = branch ? data.modules.filter(m => m.id === branch.id || m.parent === branch.id) : [];
  if (view.level >= 1) for (const m of detailModules) {
    const designs = m.designs.map(ref => ({ ref, ...(data.designMeta[ref] || {}) })).sort((a, b) => (a.metaness ?? Infinity) - (b.metaness ?? Infinity) || a.ref.localeCompare(b.ref));
    for (const d of designs) addDetail({ id: key('design', d.ref), type: 'design', label: d.title || d.ref, subtitle: d.metaness == null ? 'Design · 未分级' : `Design · metaness ${d.metaness}`, ref: d.ref, source: data.mapRef, data: d }, m.id, 'Design归属');
  }
  if (view.level >= 2) {
    for (const f of view.files || []) {
      const fileNode = { id: key('file', f.path), type: 'file', label: f.path.split('/').pop(), subtitle: f.path, ref: data.mode === 'current' ? `${REPOSITORY}/file/code/${f.path}` : f.content_address, source: data.indexRef, data: f };
      if (!f.module_matches.length) nodes.push(fileNode);
      for (const match of f.module_matches) if (detailModules.some(m => m.id === match.module_id)) addDetail(fileNode, match.module_id, '代码匹配');
    }
    if (data.mode === 'current') for (const m of detailModules) for (const instruction of data.instructions.filter(x => m.instruction_owners.includes(x.owner))) addDetail({ id: key('instruction', instruction.instruction || instruction.name), type: 'instruction', localId: instruction.instruction || instruction.name, label: instruction.instruction || instruction.name, subtitle: `owner · ${instruction.owner}`, ref: `${data.mapRef}#L${data.anchors.modules[m.id] || 1}`, source: data.mapRef, data: instruction }, m.id, '指令owner');
  }
  if (view.showPlans) for (const p of data.plans) {
    const participants = unique(p.modules.map(represent).filter(Boolean));
    if (!participants.length) continue;
    nodes.push({ id: key('plan', p.id), type: 'plan', label: p.title, subtitle: '拟议关联', status: 'proposed', ref: p.id, source: data.evolutionRef, data: p });
    for (const id of participants) edges.push({ id: `plan:${p.id}:${id}`, source: key('module', id), target: key('plan', p.id), type: 'proposed', label: '拟议影响' });
  }
  for (const node of nodes) {
    node.status = node.status || 'current';
  }
  if (view.query) {
    const term = view.query.toLocaleLowerCase();
    const matches = new Set(nodes.filter(n => `${n.label} ${n.subtitle} ${n.localId || ''}`.toLocaleLowerCase().includes(term)).map(n => n.id));
    const seeds = new Set(matches);
    for (const e of edges) if (seeds.has(e.source) || seeds.has(e.target)) { matches.add(e.source); matches.add(e.target); }
    return { nodes: nodes.filter(n => matches.has(n.id)), edges: edges.filter(e => matches.has(e.source) && matches.has(e.target)) };
  }
  return { nodes, edges };
}

/** Direct children and explicit protocol participation, without inferred calls. */
// Project exact semantic module identities onto the closest visible boundary.
// This adds emphasis only: identities, ownership and the graph remain unchanged.
export function visibleModuleAssociations(data, graph, moduleIds = []) {
  const visible = new Set([...(graph.nodes || []), graph.boundary, ...(graph.context || [])]
    .filter(node => node?.type === 'module').map(node => node.localId));
  const known = new Set(data.modules.map(module => module.id));
  return [...new Set(moduleIds)].filter(id => known.has(id)).flatMap(sourceId => {
    const visibleId = ancestors(data.modules, sourceId).reverse().find(id => visible.has(id));
    return visibleId ? [{ sourceId, visibleId }] : [];
  });
}

export function graphForScope(data, { scopeRoot = null, parentContext = false } = {}) {
  if (!Array.isArray(data?.modules) || !Array.isArray(data?.protocols)) throw new Error('架构来源尚未可读');
  const root = scopeRoot == null ? null : data.modules.find(m => m.id === scopeRoot);
  if (scopeRoot != null && !root) throw new Error('该层级不存在或已不可读');
  const children = data.modules.filter(m => root ? m.parent === root.id : !m.parent);
  const local = new Set(children.map(m => m.id));
  if (root) local.add(root.id);
  const authorized = new Set(data.modules.map(m => m.id));
  const context = new Set();
  const protocolNode = protocol => {
    const node = semanticNode(data, 'protocol', protocol.id);
    return { ...node, data: { ...node.data, modules: [...new Set(protocol.modules || [])].filter(id => authorized.has(id)) } };
  };
  const relation = protocol => {
    const node = protocolNode(protocol), actualMembers = node.data.modules;
    // At system scale a component is represented by its containing runtime boundary.
    // Preserve its real identity; never reuse this roll-up inside a component scope.
    const members = root ? actualMembers : [...new Set(actualMembers.map(id => ancestors(data.modules, id)[0]).filter(id => local.has(id)))];
    if (!members.some(id => local.has(id))) return null;
    const outside = members.filter(id => !local.has(id));
    outside.forEach(id => context.add(id));
    return { ...node,
      participants: members.map(id => key('module', id)),
      actualParticipants: actualMembers.map(id => key('module', id)),
      boundary: !!root && members.includes(root.id),
      context: outside.map(id => key('module', id)),
      relation: 'participation', direction: 'unspecified' };
  };
  const protocols = data.protocols.map(relation).filter(Boolean);
  // Parent context is a separate reading aid, never a relation attached to children.
  const parentProtocols = parentContext && root?.parent
    ? data.protocols.filter(p => p.modules?.includes(root.parent))
      .map(protocolNode) : [];
  return { runtime: true, scopeRoot, boundary: root ? semanticNode(data, 'module', root.id) : null,
    nodes: children.map(m => semanticNode(data, 'module', m.id)), edges: [], protocols,
    context: [...context].map(id => ({ ...semanticNode(data, 'module', id),
      path: ancestors(data.modules, id).map(part => data.modules.find(m => m.id === part).title || part) })),
    parentProtocols, childrenStatus: children.length ? 'registered' : 'not-registered',
    relationsStatus: protocols.length ? 'registered' : 'not-registered' };
}

export class RevisionGuard {
  constructor() { this.generation = 0; this.revision = -1; }
  start() { return ++this.generation; }
  valid(token) { return token === this.generation; }
  receive(revision) { if (!Number.isFinite(revision) || revision <= this.revision) return false; this.revision = revision; return true; }
}
