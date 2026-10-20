import { schemaMatches } from '../shared/format-tags.js';
import { contentDigest } from '../shared/content-digest.js';
import { REPOSITORY, MAP_REF, EVOLUTION_REF, MANIFEST_REF, validateMap, sourceAnchors, auditMatches, unique, evolutionBinding } from './model.js';
import { loadDiscoveredBatch } from './discovery.js';
import { createComponentRelationsLoader } from './component-relations.js';
import { createArchitectureReader } from './exploration-resources.js';

export async function sha256(text) {
  return contentDigest(text);
}

export async function limitedMap(items, fn, width = 8) {
  const out = new Array(items.length); let next = 0;
  await Promise.all(Array.from({ length: Math.min(width, items.length) }, async () => {
    while (next < items.length) { const i = next++; out[i] = await fn(items[i], i); }
  }));
  return out;
}

export function createEvidenceLoader({ resources, sdk, signal }) {
  const check = () => { if (signal?.aborted) throw new DOMException('已关闭', 'AbortError'); };
  async function access(ref) { check(); const value = await resources.resolve(ref); check(); return value; }
  async function text(ref, expectedHash) {
    check(); const result = await resources.read(ref, 'text'); check();
    if (!result.readable) throw new Error('来源不存在或不可读');
    if (expectedHash && await sha256(result.data) !== expectedHash) throw new Error('证据摘要不匹配，请重新读取事实清单');
    return result;
  }
  async function json(artifact) {
    const result = await text(artifact.address || artifact, artifact.sha256);
    if (artifact.size != null && new TextEncoder().encode(result.data).length !== artifact.size) throw new Error('证据大小不匹配，请重新读取事实清单');
    return { ...result, data: JSON.parse(result.data) };
  }
  async function yaml(ref, expectedHash) {
    // Keep line anchors and parsed data tied to the same read generation.
    const before = await text(ref, expectedHash);
    const parsed = await resources.read(ref, 'yaml'); check();
    if (!parsed.readable) throw new Error('来源已不可读');
    const after = await text(ref, expectedHash);
    if (after.data !== before.data || (parsed.metadata?.content_sha256 && parsed.metadata.content_sha256 !== await sha256(before.data))) throw new Error('来源在读取中变化，请刷新重试');
    return { ...parsed, text: before.data, hash: await sha256(before.data) };
  }
  async function manifest(pin) {
    const read = createArchitectureReader(resources, signal);
    const input = pin || await read.json(MANIFEST_REF);
    return loadDiscoveredBatch(input, {
      readJSON: read.json,
    }).catch(error => { error.code ||= 'architecture.invalid_batch'; throw error; });
  }

  async function normalize(mapResult, evolutionResult, context) {
    const map = validateMap(mapResult.data);
    const modelVersion = map.model_version ?? 1;
    const evolution = evolutionResult?.data;
    if (evolutionResult && !['tfi.portal-evolution.v1', 'tfi.portal-evolution.v2'].some(tag => schemaMatches(evolution?.schema, tag))) throw new Error('演化格式不受支持');
    const model = { modelVersion, model_version: modelVersion, mapSchema: map.schema,
      evolutionSchema: evolution?.schema || null, moduleMap: evolution?.module_map || null,
      mapHash: mapResult.hash, evolutionAvailable: !!evolutionResult };
    const binding = evolutionBinding(model);
    const versionedMapping = modelVersion === 2 || schemaMatches(evolution?.schema, 'tfi.portal-evolution.v2');
    const designs = unique([...map.modules.flatMap(m => m.designs || []), ...map.protocols.flatMap(p => [...(p.designs || []), ...(modelVersion === 2 ? p.related_designs || [] : [])])]);
    const designMeta = {};
    await limitedMap(designs, async ref => { const permission = await access(ref); if (permission.readable) designMeta[ref] = { ref, title: permission.title || ref }; });
    for (const [type, items] of [['module', map.modules], ['protocol', modelVersion === 2 ? map.protocols : []]]) {
      for (const item of items) for (const ref of item.designs || []) if (designMeta[ref]) designMeta[ref].owner = { type, id: item.id };
    }
    for (const p of map.protocols) for (const ref of (modelVersion === 2 ? p.related_designs : p.designs) || []) {
      if (designMeta[ref]) (designMeta[ref].relatedProtocols ||= []).push(p.id);
    }
    const plans = [];
    await limitedMap(evolution?.plans || [], async plan => {
      const permission = await access(plan.id);
      if (permission.readable) plans.push({ ...plan, title: permission.title || plan.title || plan.id,
        modules: !versionedMapping ? plan.modules || [] : binding.valid && plan.module_mapping !== 'needs_review' ? (plan.modules || []).filter(id => map.modules.some(m => m.id === id)) : [],
        declaredModules: plan.modules || [], mappingValid: binding.valid && plan.module_mapping !== 'needs_review', mappingReason: binding.reason });
    });
    plans.sort((a, b) => a.id.localeCompare(b.id));
    const modules = map.modules.map(m => ({ ...m, designs: (m.designs || []).filter(ref => designMeta[ref]), code_paths: m.code_paths || [], instruction_owners: m.instruction_owners || [] }));
    const protocols = map.protocols.map(p => ({ ...p, modules: (p.modules || []).filter(id => modules.some(m => m.id === id)), designs: (p.designs || []).filter(ref => designMeta[ref]),
      ...(modelVersion === 2 ? { related_designs: (p.related_designs || []).filter(ref => designMeta[ref]) } : {}) }));
    return { ...context, ...model, modules, protocols, plans, directions: evolution?.directions || [], retiredDirections: evolution?.retired_directions || [],
      evolutionMappingValid: binding.valid, evolutionMappingReason: binding.reason,
      evolutionAnchors: sourceAnchors(evolutionResult?.text || ''), designMeta, anchors: sourceAnchors(mapResult.text), mapText: mapResult.text,
      mapHash: mapResult.hash, evolutionHash: evolutionResult?.hash || null, instructions: [], fileIndex: null, indexRef: context.currentSource?.file_index?.address || '',
      audit: null, observedAt: new Date().toISOString(), metadata: mapResult.metadata, evolutionAvailable: !!evolutionResult };
  }
  async function current(evidence, { reuse, canReuse = () => true } = {}) {
    if (!(await access(REPOSITORY)).readable) throw new Error('原仓库不可读');
    // Reuse already authorized associations only inside the same evidence batch
    // and while both current source bytes still match. The caller must first
    // revalidate the snapshot's permissions (shared with show); no permission
    // results are cached here. Watch invalidates reuse through canReuse.
    if (reuse?.mode === 'current' && reuse.manifest === evidence && canReuse()) {
      const map = await text(MAP_REF);
      const evolution = (await access(EVOLUTION_REF)).readable ? await text(EVOLUTION_REF) : null;
      if (await sha256(map.data) === reuse.mapHash && (evolution ? await sha256(evolution.data) : null) === reuse.evolutionHash && canReuse()) {
        return { ...reuse, metadata: map.metadata, observedAt: new Date().toISOString() };
      }
    }
    const map = await yaml(MAP_REF);
    const canReadEvolution = (await access(EVOLUTION_REF)).readable;
    const evolution = canReadEvolution ? await yaml(EVOLUTION_REF) : null;
    const currentSource = evidence.currentSource?.files?.['architecture/modules.yaml']?.sha256 === map.hash ? evidence.currentSource : null;
    return normalize(map, evolution, { mode: 'current', mapRef: MAP_REF, evolutionRef: EVOLUTION_REF, currentSource, manifest: evidence });
  }
  async function fileIndex(data) {
    if (data.fileIndex) return data.fileIndex;
    const artifact = data.currentSource?.file_index;
    if (!artifact) return null;
    const result = (await json(artifact)).data;
    if (result.map_sha256 !== data.mapHash || result.commit !== data.currentSource.commit) throw new Error('文件索引与地图时点不一致');
    data.fileIndex = result;
    return result;
  }
  async function files(data, { moduleIds = [], query = '', offset = 0, limit = 80 } = {}) {
    const index = await fileIndex(data);
    if (!index) return { items: [], next: null, missing: true };
    const candidates = index.files.filter(f => (!moduleIds.length || f.module_matches.some(m => moduleIds.includes(m.module_id))) && (!query || f.path.toLocaleLowerCase().includes(query.toLocaleLowerCase())));
    const items = [];
    // Authorization happens before any names or counts leave the loader. The
    // cursor counts raw entries internally, never exposes a denied total.
    let cursor = offset;
    while (cursor < candidates.length && items.length < limit) {
      const batch = candidates.slice(cursor, cursor + Math.min(16, limit - items.length)); cursor += batch.length;
      const resolved = await limitedMap(batch, async f => {
        // File navigation always resolves current workspace access.
        const ref = `${REPOSITORY}/file/${f.path}`;
        return (await access(ref)).readable ? { ...f, module_matches: f.module_matches.filter(m => data.modules.some(x => x.id === m.module_id)) } : null;
      });
      items.push(...resolved.filter(Boolean));
    }
    return { items, next: cursor < candidates.length ? cursor : null, missing: false };
  }
  async function instructions(data) {
    if (data.mode !== 'current') return [];
    const reply = await sdk.hatg('ListInstructions', {}); check();
    data.instructions = (reply.instructions || reply.result?.instructions || []).map(x => ({ ...x, instruction: x.instruction || x.name }));
    return data.instructions;
  }
  async function audit(data) {
    const snapshot = data.currentSource;
    const candidate = (data.manifest.audits || []).find(a => a.id === snapshot?.audit_ids?.at(-1));
    if (!auditMatches(snapshot, candidate) || data.mapHash !== snapshot.files['architecture/modules.yaml']?.sha256 || (data.evolutionHash && data.evolutionHash !== snapshot.files['architecture/evolution.yaml']?.sha256)) return null;
    const report = (await json(candidate.report)).data;
    if (report.commit !== snapshot.commit) throw new Error('报告的目标提交不匹配');
    const permissions = new Map();
    const allowed = ref => { if (!permissions.has(ref)) permissions.set(ref, access(ref)); return permissions.get(ref); };
    const findings = (await limitedMap(report.findings || [], async finding => {
      if (finding.module && !data.modules.some(m => m.id === finding.module)) return null;
      if (finding.subject && !(await allowed(finding.subject)).readable) return null;
      if (finding.path && !(await allowed(`${REPOSITORY}/file/${finding.path}`)).readable) return null;
      return { ...finding, candidate_modules: (finding.candidate_modules || []).filter(id => data.modules.some(m => m.id === id)) };
    })).filter(Boolean);
    data.audit = { ...candidate, findings, audit_failed: report.audit_failed };
    return data.audit;
  }
  async function scenarios(data, protocolId) {
    const baseEntries = (data.manifest.scenario_catalog || []).filter(s => s.protocol_id === protocolId);
    // Existing scene pairs keep their original base-batch authority. The v2 supplement adds only new pairs.
    let entries = baseEntries;
    if (!baseEntries.length) {
      const supplemental = await scenarioCatalog(data, protocolId);
      if (supplemental.status === 'error') throw supplemental.error;
      if (supplemental.status !== 'available' && supplemental.reason !== 'not_declared' && !supplemental.reason?.startsWith('declared_missing:')) {
        const error = new Error('补充场景不可用：' + supplemental.reason);
        error.code = 'architecture.component_relations_unavailable'; error.reason = supplemental.reason; throw error;
      }
      entries = supplemental.entries;
    }
    return (await limitedMap(entries, async entry => {
      const scene = (await json(entry.artifact)).data;
      for (const source of scene.source_versions || []) if (!(await access(source.ref)).readable) return null;
      if (scene.status !== 'simulated' || scene.protocol_id !== protocolId || scene.scenario_id !== entry.scenario_id) throw new Error('演示身份不匹配');
      const ids = new Set(scene.participants?.map(x => x.id));
      const steps = [...(scene.steps || []), ...(scene.branches || []).flatMap(x => x.steps || [])];
      if (steps.some(s => !ids.has(s.from) || !ids.has(s.to) || !s.source_refs?.length)) throw new Error('演示缺少参与者或逐步依据');
      if (entry.origin === 'supplement_v2') {
        const moduleIds = new Set(data.modules.map(item => item.id));
        if (scene.participants.some(item => !item.module_id || !moduleIds.has(item.module_id))) return null;
        const sources = [...(scene.source_refs || []), ...steps.flatMap(step => step.source_refs),
          ...(scene.branches || []).flatMap(branch => branch.source_refs || [])];
        const refs = unique([REPOSITORY, data.mapRef,
          ...scene.participants.map(item => data.mapRef + (data.anchors?.modules?.[item.module_id] ? `#L${data.anchors.modules[item.module_id]}` : '')),
          ...(scene.source_versions || []).flatMap(source => [source.ref, source.body?.address]),
          ...sources.flatMap(source => typeof source === 'string' ? [source] : [source.ref, source.snapshot_address]).filter(Boolean)]);
        if (!(await limitedMap(refs, async ref => (await access(ref)).readable)).every(Boolean)) return null;
      }
      check();
      return scene;
    })).filter(Boolean);
  }
  async function body(ref) {
    const expectedHash = String(ref).match(/\/objects\/[^/]+-([a-f0-9]{64})\.[^/#]+(?:#L\d+(?:-L\d+)?)?$/)?.[1];
    const result = await text(ref, expectedHash);
    const level = result.data.match(/^metaness:\s*(\d+)\s*$/m)?.[1];
    return { ...result, version: result.metadata?.version || result.metadata?.content_sha256 || await sha256(result.data), metaness: level == null ? null : Number(level) };
  }
  const { componentRelations, scenarioCatalog } = createComponentRelationsLoader({ resources, access, sha256, limitedMap, check });
  return { manifest, current, files, fileIndex, instructions, audit, scenarios, body, text, json, access, componentRelations };
}
