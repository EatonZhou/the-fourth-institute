import { marked } from 'marked';
import { schemaMatches } from '../shared/format-tags.js';
import { matchesCurrentModuleMap } from '../shared/current-module-map.js';

export const REFS = Object.freeze({
  evolution: 'repo_711b6ab6d5c8/file/code/architecture/evolution.yaml',
  modules: 'repo_711b6ab6d5c8/file/code/architecture/modules.yaml',
});
const headingGroups = [
  ['问题', '场景', '用户任务与当前依据', '当前工作流与问题边界', '现有能力与缺口', '为什么值得单独改善', '当前流程与具体风险', '当前基础与用户需要', '当前行为及后果', '已有基础与需要补齐的解释', '现状及用户判断困难', '当前依据与影响', '当前起点与证据限制', '当前起点与需要改变的体验', '现有行为与问题依据', '当前入口已经解决了什么', '当前能力与典型场景', '当前基础与缺口', '当前能力与使用场景', '当前基础与价值', '当前已有能力', '当前记录方式与用户任务', '现有基础与新的审阅单位'],
  ['预期体验', '方案', '改善后的完整流程', '目标体验', '用户如何完成核对', '目标阅读体验', '人能看清的一次归档', '预演应如何呈现', '确认后的执行流程', '希望用户看到什么', '完整操作体验', '目标流程', '审阅者应获得的结果', '一次打开后的完整过程', '目标审阅流程', '从成果到反馈', '目标交互', '从需求到选择', '目标工作流', '从研究问题到可读结果', '目标比较过程', '从一次有效协作提炼流程'],
  ['成功检验', '边界与验收', '验收与尚待验证', '验收', '验收与继续投入条件', '验收与风险', '代价与验收', '验收与范围', '验收与投入边界', '验收与代价', '验收与取舍', '验收与投入条件'],
];

/** Exact, reviewed heading vocabulary; ambiguous or missing sections stay unknown. */
export function planSections(text) {
  const original = String(text).replace(/\r\n?/g, '\n');
  let start = 0;
  if (original.startsWith('---\n')) {
    const end = original.indexOf('\n---', 4);
    if (end >= 0) start = end + 4 + (original[end + 4] === '\n' ? 1 : 0);
  }
  const sections = [];
  let cursor = start, current = null;
  for (const token of marked.lexer(original.slice(start))) {
    const raw = token.raw || '';
    if (token.type === 'heading' && token.depth === 2) {
      if (current) current.end = cursor;
      current = { heading: token.text, start: cursor, bodyStart: cursor + raw.length, end: original.length };
      sections.push(current);
    }
    cursor += raw.length;
  }
  return headingGroups.map((names, index) => {
    const matches = sections.filter(section => names.includes(section.heading));
    const label = ['问题', '预期体验', '成功检验'][index];
    if (matches.length !== 1) return { label, available: false };
    const section = matches[0];
    const markdown = original.slice(section.bodyStart, section.end).trim();
    return { label, available: !!markdown, heading: section.heading, markdown,
      line: original.slice(0, section.start).split('\n').length,
      endLine: original.slice(0, section.end).trimEnd().split('\n').length };
  });
}

export function lineAnchor(ref, id, text) {
  // The parsed YAML owns identity. This only locates a unique literal scalar in
  // the exact same byte version; unusual/ambiguous syntax is not guessed.
  const lines = String(text).split('\n');
  const found = lines.flatMap((line, i) => {
    const match = line.match(/^\s*-\s+id:\s*(.*?)\s*$/);
    if (!match) return [];
    const scalar = match[1];
    return [id, JSON.stringify(id), `'${id.replaceAll("'", "''")}'`].includes(scalar) ? [i + 1] : [];
  });
  return found.length === 1 ? `${ref}#L${found[0]}` : null;
}

export function bodyVersion(metadata) {
  const value = metadata?.record?.version ?? metadata?.version;
  return value == null ? null : String(typeof value === 'object' ? value.number ?? '' : value) || null;
}

async function readYaml(resources, ref) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const [parsed, text] = await Promise.all([resources.read(ref, 'yaml'), resources.read(ref, 'text')]);
    if (!parsed.readable || !text.readable) return null;
    const a = parsed.metadata?.content_sha256, b = text.metadata?.content_sha256;
    if (a && a === b && parsed.data && typeof parsed.data === 'object') return { data: parsed.data, text: text.data, version: a, ref };
  }
  throw new Error('来源的正文与结构版本不同，请重新读取。');
}

// Only direction-attached Plans are read; the gallery never waits for unrelated
// Plans, and never invents membership from the retired Plan taxonomy.
export async function loadFacts(resources) {
  const [evolution, map] = await Promise.all([readYaml(resources, REFS.evolution), readYaml(resources, REFS.modules)]);
  if (!evolution) return { validity: 'revoked', deniedRefs: [REFS.evolution] };
  const raw = evolution.data;
  if (!schemaMatches(raw.schema, 'tfi.portal-evolution.v2') || !Array.isArray(raw.directions)) throw new Error('需要正式的演化图 v2。');
  const mapping = raw.module_map;
  const supportedMap = !!map && schemaMatches(map.data.schema, 'tfi.portal-architecture.v1');
  const mapMatches = supportedMap && matchesCurrentModuleMap(mapping, {
    ref: REFS.modules, schema: map.data.schema, modelVersion: map.data.model_version ?? 1,
  });
  const directions = raw.directions.filter(d => d?.id && d.status === 'proposed');
  const ids = [...new Set(directions.flatMap(d => d.plans || []))];
  const plans = [], documents = {}, deniedRefs = map ? [] : [REFS.modules];
  await Promise.all(ids.map(async id => {
    const plan = raw.plans?.find(p => p.id === id);
    if (!plan) return;
    const body = await resources.read(id, 'text');
    if (!body.readable) { deniedRefs.push(id); return; }
    plans.push(plan);
    documents[id] = { text: body.data, version: bodyVersion(body.metadata), hash: body.metadata?.content_sha256 || null, sections: planSections(body.data) };
  }));
  plans.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
  const sources = {
    evolution: { ref: REFS.evolution, version: evolution.version },
    modules: { ref: REFS.modules, version: map?.version || null },
    plans: Object.fromEntries(plans.map(p => [p.id, { version: documents[p.id].version, hash: documents[p.id].hash }])),
  };
  return { validity: 'ready', directions, retired: raw.retired_directions || [], plans, documents, deniedRefs,
    modules: supportedMap ? map.data.modules || [] : [], mapMatches,
    texts: { evolution: evolution.text, modules: map?.text || '' }, sources,
    observedAt: new Date().toISOString() };
}

export function baselineKey(facts) { return facts?.sources ? JSON.stringify(facts.sources) : null; }

export function semanticObject(selection, facts) {
  if (!selection || !facts?.sources) return null;
  const { kind, id } = selection;
  let ref, source, status = 'current';
  if (kind === 'direction' && facts.directions.some(d => d.id === id)) {
    source = REFS.evolution; ref = lineAnchor(source, id, facts.texts.evolution); status = 'proposed';
  } else if (kind === 'module' && facts.mapMatches && facts.modules.some(m => m.id === id)) {
    source = REFS.modules; ref = lineAnchor(source, id, facts.texts.modules);
  } else if (kind === 'plan' && facts.plans.some(p => p.id === id)) { ref = id; source = id; status = 'proposed'; }
  return ref ? { ref, kind: kind === 'plan' ? kind : `${kind}:${id}`, status, source, observed_at: facts.observedAt } : null;
}

export function linkedDirections(object, facts) {
  if (!facts.mapMatches || object?.source !== REFS.modules || !object.kind?.startsWith('module:')) return [];
  if (!facts.modules.some(m => m.id === object.kind.slice(7))) return [];
  const range = [object.kind.slice(7)];
  for (let i = 0; i < range.length; i++) for (const m of facts.modules) if (m.parent === range[i] && !range.includes(m.id)) range.push(m.id);
  return facts.directions.filter(d => d.module_mapping !== 'needs_review' && d.modules?.some(id => range.includes(id))).map(d => d.id);
}

export function feedbackPayload(facts, direction, local, text) {
  const object = semanticObject({ kind: 'direction', id: direction.id }, facts);
  if (!object) throw new Error('方向来源不可定位。');
  const frame = direction.visual?.storyboard?.[local.step];
  const scene = { panel: 'evolution', status: 'simulated', direction: direction.id,
    sources: facts.sources, mapping: facts.mapMatches ? 'current' : 'outdated',
    storyboard: { id: frame?.id, step: local.step, count: direction.visual?.storyboard?.length || 0 },
    playback: { playing: false, position_ms: local.positionMs }, local: structuredClone(local) };
  return { object, value: { ref: object.ref, subject: direction.title, text,
    anchor: JSON.stringify({ scope: 'direction', local_id: direction.id, version: facts.sources.evolution.version }),
    scene: JSON.stringify(scene) } };
}
