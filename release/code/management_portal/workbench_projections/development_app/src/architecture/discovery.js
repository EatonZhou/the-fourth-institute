import { schemaMatches } from '../shared/format-tags.js';
// Discovery locates documentation for the current architecture only.
export const DISCOVERY_SCHEMA = 'tfi.portal-architecture-discovery.v1';
export const descriptor = value => !!value && typeof value.address === 'string' && !!value.address.trim()
  && /^[a-f0-9]{64}$/.test(value.sha256) && Number.isSafeInteger(value.size) && value.size > 0;
export const sameArtifact = (a, b) => descriptor(a) && descriptor(b)
  && a.address === b.address && a.sha256 === b.sha256 && a.size === b.size;
export const batchIdentity = evidence => JSON.stringify([evidence?.pinned,
  evidence?.discoveryPin?.pinned || null, evidence?.discoveryPin?.component_relations || {status:'not_declared'},
  evidence?.discoveryPin?.exploration || {status:'not_declared'}, evidence?.discoveryPin?.catalog_supplements || null]);
export function batchRefs(evidence) {
  const pin=evidence?.discoveryPin, extra=pin?.component_relations;
  return [pin?.pinned?.address,evidence?.pinned?.address,evidence?.consumer_contract?.address,
    extra?.manifest?.address,extra?.verification?.address,pin?.verification?.address,pin?.artifact?.address,pin?.contract_documentation?.address,
    pin?.exploration?.manifest?.address,pin?.catalog_supplements?.hatg?.lesson_sources?.address].filter(Boolean);
}

export function fullBindingMatches(binding, evidence, consumer) {
  const snapshot=evidence.currentSource;
  return binding && sameArtifact(binding.base_manifest,evidence.pinned)
    && sameArtifact(binding.consumer_contract,evidence.consumer_contract)
    && binding.commit===evidence.current_commit && consumer?.commit===binding.commit
    && sameArtifact(binding.map,consumer.map) && sameArtifact(binding.evolution,consumer.upstream_evolution?.source)
    && sameArtifact(binding.map,snapshot?.files?.['architecture/modules.yaml'])
    && sameArtifact(binding.evolution,snapshot?.files?.['architecture/evolution.yaml'])
    && binding.evolution_sha256===binding.evolution.sha256;
}
export function supplementEntryMatches(entry, body) {
  const binding=body?.binding;
  if (!binding || !schemaMatches(entry.schema, 'tfi.architecture-component-relations-manifest.v1')
    || !schemaMatches(body.schema, 'tfi.architecture-component-relations.v1')
    || ![2,3].includes(entry.revision) || body.revision!==entry.revision || entry.immutable!==true
    || !sameArtifact(entry.base_manifest,binding.base_manifest)
    || !sameArtifact(entry.consumer_contract,binding.consumer_contract) || !sameArtifact(entry.map,binding.map)
    || entry.binding?.commit!==binding.commit || entry.binding?.evolution_sha256!==binding.evolution_sha256) return false;
  if (entry.revision===2) return entry.binding.map_sha256===binding.map.sha256;
  return sameArtifact(entry.verification,body.verification)
    && binding.compatibility === 'exact_base_manifest_consumer_commit_map_evolution'
    && entry.binding.compatibility === binding.compatibility
    && ['base_manifest','consumer_contract','map','evolution'].every(key=>sameArtifact(entry.binding[key],binding[key]));
}

export function verificationMatches(report, evidence, body = null) {
  if (!schemaMatches(report?.schema, 'tfi.architecture-component-verification.v1') || report.revision !== 1
    || !sameArtifact(report.base_manifest,evidence.pinned) || report.commit !== evidence.current_commit
    || !descriptor(report.template_manifest)) return false;
  if (!body) return report.status === 'missing' && typeof report.reason_code === 'string' && !!report.reason_code
    && Object.hasOwn(report,'subject') && typeof report.action === 'string' && !!report.action;
  if (report.status !== 'verified' || report.method !== 'identical_map_evolution_design_versions_and_whole_referenced_code_files'
    || report.map_sha256 !== body.binding.map.sha256 || report.evolution_sha256 !== body.binding.evolution_sha256
    || !descriptor(report.template_artifact) || !Array.isArray(report.limitations)
    || !Array.isArray(report.design_checks) || !Array.isArray(report.code_checks) || !Array.isArray(report.scenario_checks)) return false;
  return (body.design_versions || []).every(source => report.design_checks.some(check => check.design_id === source.design_id
      && check.version === source.version && check.sha256 === source.body?.sha256
      && check.audit_observation?.sha256 === source.body?.sha256 && check.audit_observation?.version === source.version))
    && (body.code_evidence || []).every(code => report.code_checks.some(check => ['id','commit','path','git_blob','sha256','size','line_start','line_end','excerpt_sha256'].every(key => code[key] === check[key])))
    && (body.scenario_catalog || []).every(scene => report.scenario_checks.some(check => check.protocol_id === scene.protocol_id
      && check.scenario_id === scene.scenario_id && sameArtifact(scene.artifact,check.artifact)));
}
export async function loadDiscoveredBatch(input, { readJSON }) {
  // The index locates current documentation. Do not retain archived maps or
  // traverse contracts and sources to admit a page.
  if (descriptor(input)) input = await readJSON(input);
  if (!input || typeof input !== 'object') throw new Error('架构说明入口不是有效对象');
  if (input.schema != null && !['tfi.portal-architecture-evidence.v1', DISCOVERY_SCHEMA].some(tag => schemaMatches(input.schema, tag))) throw new Error('架构说明入口格式不受支持');
  if (input.discovery_schema != null && !schemaMatches(input.discovery_schema, DISCOVERY_SCHEMA)) throw new Error('架构发现入口格式不受支持');
  const base = input.immutable_manifest || input.base_manifest;
  let value = input;
  if (!input.exploration && input.immutable_discovery) {
    const discovery = await readJSON(input.immutable_discovery);
    if (!schemaMatches(discovery.schema, DISCOVERY_SCHEMA)) throw new Error('架构发现格式不受支持');
    value = { ...input, ...discovery };
  }
  if (!value.exploration && base && !Array.isArray(value.snapshots)) {
    const evidence = await readJSON(base);
    if (!schemaMatches(evidence.schema, 'tfi.portal-architecture-evidence.v1')) throw new Error('架构证据格式不受支持');
    value = { ...evidence, ...value };
  }
  const { snapshots, ...current } = value;
  const currentSource = value.currentSource || snapshots?.find(item => item.commit === value.current_commit) || null;
  return { ...current, currentSource, pinned: base,
    discoveryPin: { ...current, base_manifest: base, pinned: input.immutable_discovery,
      component_relations: value.component_relations || { status: 'not_declared' } } };
}
