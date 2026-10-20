import { schemaMatches } from '../shared/format-tags.js';
/**
 * Pure consumer of the v1 architecture exploration format (TFI and legacy tags).
 * Validation checks the declared structure, not source authenticity, permissions,
 * JSON Schema case evaluation or domain invariants. Those belong to the loader
 * and the mechanism's own interpreter. Projection never invents an interaction.
 */
const KINDS = ['module', 'protocol', 'concept', 'external'];
const RELATIONS = ['calls', 'submits', 'claims', 'renews', 'observes', 'projects', 'reads', 'writes', 'emits', 'interrupts', 'hosts', 'adapts'];
const TYPES = ['string', 'boolean', 'integer', 'number', 'object', 'array'];
const ASPECTS = ['responsibility', 'composition', 'interactions', 'mechanism', 'sources'];
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' && value.trim().length > 0;
const list = value => Array.isArray(value) ? value : [];
const has = (value, key) => Object.hasOwn(value, key);
const LOCAL_EXTERNAL_ROOT = 'model-services';
const LOCAL_EXTERNAL_CONCEPTS = new Set(['model-services.chat-completion', 'model-services.typed-decisions', 'model-services.native-inference']);
const MAP_SOURCE = 'repo_5d287fed9c7e/file/architecture/modules.yaml';
const matchesType = (value, type) => type === 'integer' ? Number.isInteger(value)
  : type === 'number' ? typeof value === 'number' && Number.isFinite(value)
    : type === 'object' ? record(value) : type === 'array' ? Array.isArray(value) : typeof value === type;

// This exact exception is declared by the frozen exploration contract. The map
// reference supplies evidence, never a fabricated module or broadcast identity.
function localExternalIdentity(object, objects) {
  const owner = objects.get(LOCAL_EXTERNAL_ROOT);
  const identityMatches = value => value?.focus_owner_id === LOCAL_EXTERNAL_ROOT
    && value.focus_kind === null && value.local_only === true && value.resource_ref === MAP_SOURCE;
  if (!owner || owner.kind !== 'external' || owner.parent_id !== null || !identityMatches(owner.identity)
    || !identityMatches(object.identity)) return false;
  if (object.id === LOCAL_EXTERNAL_ROOT) return object.kind === 'external';
  if (object.kind !== 'concept' || !LOCAL_EXTERNAL_CONCEPTS.has(object.id)) return false;
  const visited = new Set([object.id]);
  let parent = objects.get(object.parent_id);
  while (parent?.kind === 'concept' && !visited.has(parent.id)) {
    visited.add(parent.id); parent = objects.get(parent.parent_id);
  }
  return parent === owner;
}

function inspector(errors) {
  const fail = (code, path, message) => errors.push({ code, path, message });
  const strings = (value, path) => {
    if (!Array.isArray(value) || value.some(item => !text(item))) fail('string_array', path, 'Expected an array of nonempty strings');
  };
  const index = (value, path, key = 'id') => {
    if (!Array.isArray(value)) fail('array', path, 'Expected an array');
    const result = new Map();
    list(value).forEach((item, i) => {
      if (!record(item) || !text(item[key])) fail('id', `${path}[${i}].${key}`, 'Expected a nonempty identifier');
      else if (result.has(item[key])) fail('duplicate_id', `${path}[${i}].${key}`, 'Identifier must be unique in its collection');
      else result.set(item[key], item);
    });
    return result;
  };
  const ref = (id, target, path, nullable = false) => {
    if (nullable && id === null) return;
    if (!text(id) || !target.has(id)) fail('dangling_reference', path, 'Reference must resolve in the supplied model');
  };
  const refs = (value, target, path) => {
    strings(value, path);
    const seen = new Set();
    list(value).forEach((id, i) => {
      ref(id, target, `${path}[${i}]`);
      if (seen.has(id)) fail('duplicate_reference', `${path}[${i}]`, 'Reference must occur only once');
      seen.add(id);
    });
  };
  const requiredText = (value, keys, path) => {
    for (const key of keys) if (!text(value[key])) fail('text', `${path}.${key}`, 'Expected nonempty text');
  };
  const descriptor = (value, path) => {
    if (!record(value) || !text(value.address) || !/^[a-f0-9]{64}$/.test(value.sha256 || '') || !Number.isSafeInteger(value.size) || value.size < 0)
      fail('descriptor', path, 'Expected address, SHA-256 and nonnegative byte size');
  };
  return { fail, strings, index, ref, refs, requiredText, descriptor };
}

function scopeErrors(relation, objects, path, check) {
  const { fail, refs, ref } = check;
  if (!Array.isArray(relation.scope_views)) fail('array', `${path}.scope_views`, 'Expected explicit scope views');
  const seen = new Set();
  list(relation.scope_views).forEach((view, i) => {
    const at = `${path}.scope_views[${i}]`;
    if (!record(view)) { fail('scope_view', at, 'Expected a scope view'); return; }
    // null denotes a consumer's overall system view; it never implies edges.
    ref(view.scope_id, objects, `${at}.scope_id`, true);
    if (objects.get(view.scope_id)?.kind === 'protocol') fail('scope_kind', at, 'A protocol is not a composition scope');
    if (seen.has(view.scope_id)) fail('duplicate_scope', at, 'A relation has one placement per scope');
    seen.add(view.scope_id);
    refs(view.internal_endpoint_ids, objects, `${at}.internal_endpoint_ids`);
    const endpoints = [...new Set([relation.from, relation.to])];
    const direct = endpoints.filter(id => objects.has(id) && objects.get(id).kind !== 'protocol' && objects.get(id).parent_id === view.scope_id);
    const declared = list(view.internal_endpoint_ids);
    if (direct.length !== declared.length || direct.some(id => !declared.includes(id)))
      fail('scope_endpoints', at, 'Internal endpoints must be exactly the real direct endpoints in this scope');
    const internal = direct.length === endpoints.length;
    if (!['internal', 'boundary'].includes(view.placement) || (view.placement === 'internal') !== internal)
      fail('scope_placement', at, 'Placement must agree with actual endpoint composition');
  });
}

function resultFlowErrors(relation, objects, evidence, path, check) {
  if (!record(relation.contract) || !has(relation.contract, 'result_flow')) return;
  const flow = relation.contract.result_flow, at = `${path}.contract.result_flow`;
  if (!record(flow)) { check.fail('result_flow', at, 'Expected a declared synchronous return channel'); return; }
  check.ref(flow.from, objects, `${at}.from`); check.ref(flow.to, objects, `${at}.to`);
  if (flow.from !== relation.to || flow.to !== relation.from)
    check.fail('result_flow_endpoints', at, 'The return channel must reverse the actual call endpoints');
  if (flow.delivery !== 'synchronous_return') check.fail('result_flow_delivery', `${at}.delivery`, 'Expected synchronous_return');
  check.strings(flow.payload, `${at}.payload`);
  check.requiredText(flow, ['consumption', 'condition'], at);
  check.refs(flow.evidence_ids, evidence, `${at}.evidence_ids`);
}

/** Return diagnostics; an in-progress model can be structurally valid. */
export function validateExplorationModel(model) {
  const errors = [], check = inspector(errors);
  const { fail, strings, index, ref, refs, requiredText, descriptor } = check;
  if (!record(model)) return { valid: false, errors: [{ code: 'model', path: '$', message: 'Expected a model object' }] };
  if (!schemaMatches(model.schema, 'tfi.portal-architecture-exploration.v1') || model.revision !== 1) fail('schema', '$', 'Unsupported exploration schema or revision');
  const objects = index(model.objects, 'objects'), relations = index(model.relations, 'relations');
  const mechanisms = index(model.mechanisms, 'mechanisms'), evidence = index(model.evidence, 'evidence');
  const coverage = index(model.coverage, 'coverage', 'object_id'), work = index(model.work_items, 'work_items');
  const protocols = new Map([...objects].filter(([, object]) => object.kind === 'protocol'));
  // Catalog contents are loaded and authorized by the parent. Only their fixed
  // descriptors and explicit object links belong to this model contract.
  if (has(model, 'catalogs') && !record(model.catalogs)) fail('catalogs', 'catalogs', 'Expected a catalog mapping');
  const catalogs = new Map(Object.entries(record(model.catalogs) ? model.catalogs : {}));
  for (const [id, catalog] of catalogs) {
    const at = `catalogs[${id}]`;
    if (!text(id) || !record(catalog)) { fail('catalog', at, 'Expected a named catalog declaration'); continue; }
    requiredText(catalog, ['status'], at);
    if (catalog.status === 'available' || (has(catalog, 'manifest') && catalog.manifest !== null)) descriptor(catalog.manifest, `${at}.manifest`);
  }
  for (const [id, object] of objects) {
    const at = `objects[${id}]`;
    if (!KINDS.includes(object.kind)) fail('object_kind', `${at}.kind`, 'Unknown object kind');
    requiredText(object, ['title', 'responsibility'], at);
    ref(object.parent_id, objects, `${at}.parent_id`, true);
    refs(object.child_ids, objects, `${at}.child_ids`);
    refs(object.host_ids, objects, `${at}.host_ids`);
    refs(object.relation_ids, relations, `${at}.relation_ids`);
    refs(object.protocol_ids, protocols, `${at}.protocol_ids`);
    refs(object.mechanism_ids, mechanisms, `${at}.mechanism_ids`);
    if (has(object, 'catalog_id')) ref(object.catalog_id, catalogs, `${at}.catalog_id`);
    refs(object.evidence_ids, evidence, `${at}.evidence_ids`);
    strings(object.state_owned, `${at}.state_owned`);
    if (object.kind === 'protocol' && (object.parent_id !== null || list(object.child_ids).length))
      fail('protocol_composition', at, 'Protocols do not have composition parents or children');
    if (object.parent_id !== null && objects.has(object.parent_id) && !list(objects.get(object.parent_id).child_ids).includes(id))
      fail('composition_reciprocal', `${at}.parent_id`, 'Parent must declare this direct child');
    for (const child of list(object.child_ids)) if (objects.has(child) && objects.get(child).parent_id !== id)
      fail('composition_reciprocal', `${at}.child_ids`, 'Child must declare this parent');
    const identity = object.identity;
    if (!record(identity)) fail('identity', `${at}.identity`, 'Expected the declared semantic identity');
    else {
      const externalLocal = localExternalIdentity(object, objects);
      if ((id === LOCAL_EXTERNAL_ROOT || LOCAL_EXTERNAL_CONCEPTS.has(id)) && !externalLocal)
        fail('local_external_identity', `${at}.identity`, 'The declared external root and its concepts must preserve their exact local-only identity and composition ancestry');
      requiredText(identity, ['resource_ref', 'focus_owner_id'], `${at}.identity`);
      if (!externalLocal) requiredText(identity, ['focus_kind'], `${at}.identity`);
      if (typeof identity.local_only !== 'boolean') fail('identity', `${at}.identity.local_only`, 'Expected a boolean');
      if (object.kind === 'concept' && !identity.local_only) fail('local_identity', `${at}.identity`, 'Concept selection is local to its existing focus owner');
      if (identity.local_only) {
        ref(identity.focus_owner_id, objects, `${at}.identity.focus_owner_id`);
        const owner = objects.get(identity.focus_owner_id);
        if (owner && !externalLocal && (!['module', 'protocol'].includes(owner.kind) || identity.focus_kind !== owner.identity?.focus_kind || identity.resource_ref !== owner.identity?.resource_ref))
          fail('local_identity', `${at}.identity`, 'Local selection must preserve an existing module or protocol identity');
      }
    }
    if (object.kind === 'protocol') {
      const contract = object.contract;
      const research = coverage.get(id);
      const pendingContract = model.completion?.status === 'in_progress' && research
        && ['queued', 'researching', 'reviewed'].includes(research.status) && research.interactions === 'pending'
        && list(research.work_item_ids).length > 0 && !has(object, 'contract');
      if (!record(contract)) {
        if (!pendingContract) fail('protocol_contract', `${at}.contract`, 'Expected a protocol contract or explicit pending research');
        continue;
      }
      if (!Array.isArray(contract.roles)) fail('array', `${at}.contract.roles`, 'Expected role guarantees');
      list(contract.roles).forEach((role, i) => {
        if (!record(role)) { fail('role', `${at}.contract.roles[${i}]`, 'Expected a role'); return; }
        ref(role.object_id, objects, `${at}.contract.roles[${i}].object_id`);
        requiredText(role, ['role'], `${at}.contract.roles[${i}]`);
        strings(role.guarantees, `${at}.contract.roles[${i}].guarantees`);
      });
      strings(contract.invariants, `${at}.contract.invariants`);
      const operations = index(contract.operations, `${at}.contract.operations`);
      for (const [operation, value] of operations) for (const field of ['request_fields', 'response_fields', 'failures'])
        strings(value[field], `${at}.contract.operations[${operation}].${field}`);
    }
  }
  // Iterative parent walks avoid imposing a fixed depth or exhausting the stack.
  const visited = new Set();
  for (const id of objects.keys()) {
    const path = new Set(); let current = id;
    while (objects.has(current) && !visited.has(current)) {
      if (path.has(current)) { fail('composition_cycle', `objects[${id}]`, 'Composition must be acyclic'); break; }
      path.add(current); current = objects.get(current).parent_id;
    }
    for (const item of path) visited.add(item);
  }
  for (const [id, relation] of relations) {
    const at = `relations[${id}]`;
    if (!RELATIONS.includes(relation.kind)) fail('relation_kind', `${at}.kind`, 'Unknown relation kind');
    ref(relation.from, objects, `${at}.from`); ref(relation.to, objects, `${at}.to`);
    ref(relation.protocol_id, protocols, `${at}.protocol_id`, true);
    refs(relation.evidence_ids, evidence, `${at}.evidence_ids`);
    requiredText(relation, ['label'], at);
    if (!record(relation.contract)) fail('relation_contract', `${at}.contract`, 'Expected an interaction contract');
    else {
      requiredText(relation.contract, ['operation', 'transport'], `${at}.contract`);
      const ordering = relation.contract.ordering;
      if (!text(ordering) && !(Array.isArray(ordering) && ordering.length && ordering.every(text)))
        fail('ordering', `${at}.contract.ordering`, 'Expected an explicit ordering statement or nonempty statements');
      for (const field of ['input', 'output', 'preconditions', 'state_effects', 'errors']) strings(relation.contract[field], `${at}.contract.${field}`);
    }
    resultFlowErrors(relation, objects, evidence, at, check);
    scopeErrors(relation, objects, at, check);
  }
  for (const [id, mechanism] of mechanisms) {
    const at = `mechanisms[${id}]`;
    ref(mechanism.owner_id, objects, `${at}.owner_id`);
    requiredText(mechanism, ['title', 'question', 'presentation', 'adapter'], at);
    // Domains describe their own state semantics. Preserve structured contracts
    // rather than coercing them into one interpreter's description format.
    if (!text(mechanism.state_model) && !(record(mechanism.state_model) && Object.keys(mechanism.state_model).length))
      fail('state_model', `${at}.state_model`, 'Expected a state description or nonempty domain declaration');
    if (mechanism.mode !== 'simulated') fail('mechanism_mode', `${at}.mode`, 'Mechanisms must be declared local simulations');
    if (!record(mechanism.input_schema) && typeof mechanism.input_schema !== 'boolean') fail('input_schema', `${at}.input_schema`, 'Expected a JSON Schema');
    refs(mechanism.evidence_ids, evidence, `${at}.evidence_ids`);
    strings(mechanism.entry_conditions, `${at}.entry_conditions`); strings(mechanism.invariants, `${at}.invariants`);
    if (!Array.isArray(mechanism.participants)) fail('array', `${at}.participants`, 'Expected participants');
    list(mechanism.participants).forEach((p, i) => {
      if (!record(p)) { fail('participant', `${at}.participants[${i}]`, 'Expected a participant'); return; }
      ref(p.object_id, objects, `${at}.participants[${i}].object_id`); requiredText(p, ['role'], `${at}.participants[${i}]`);
    });
    const fields = index(mechanism.state_fields, `${at}.state_fields`), steps = index(mechanism.steps, `${at}.steps`);
    for (const [fieldId, field] of fields) {
      const fieldAt = `${at}.state_fields[${fieldId}]`;
      requiredText(field, ['label', 'meaning'], fieldAt);
      // Local observations may have no authoritative business owner. A declared
      // authority must resolve; it is never inferred from scope or the writer.
      if (has(field, 'authority_id')) {
        ref(field.authority_id, objects, `${fieldAt}.authority_id`);
        requiredText(field, ['state_scope'], fieldAt);
      }
      if (has(field, 'state_scope') && !['authoritative', 'observation'].includes(field.state_scope))
        fail('state_scope', `${fieldAt}.state_scope`, 'Expected authoritative or observation');
      if (!TYPES.includes(field.type) || !has(field, 'initial') || !matchesType(field.initial, field.type)) fail('state_type', fieldAt, 'Initial state must match its declared type');
      if (has(field, 'enum') && (!Array.isArray(field.enum) || !field.enum.some(v => JSON.stringify(v) === JSON.stringify(field.initial)))) fail('state_enum', fieldAt, 'Initial state must occur in its enumeration');
    }
    const condition = (when, where) => {
      if (!record(when)) { fail('condition', where, 'Expected a condition object'); return; }
      const groups = ['all', 'any'].filter(key => has(when, key));
      if (groups.length) {
        if (groups.length !== 1 || Object.keys(when).length !== 1 || !Array.isArray(when[groups[0]]) || !when[groups[0]].length) fail('condition', where, 'Expected one nonempty all/any group');
        else when[groups[0]].forEach((child, i) => condition(child, `${where}.${groups[0]}[${i}]`));
        return;
      }
      ref(when.field, fields, `${where}.field`);
      const ops = ['equals', 'not_equals', 'in', 'gte'].filter(key => has(when, key));
      if (ops.length !== 1 || Object.keys(when).length !== 2 || (ops[0] === 'in' && !Array.isArray(when.in)) || (ops[0] === 'gte' && !Number.isFinite(when.gte))) fail('condition', where, 'Expected one supported comparison');
    };
    for (const [stepId, step] of steps) {
      const stepAt = `${at}.steps[${stepId}]`;
      requiredText(step, ['title', 'trigger', 'guard'], stepAt);
      if (!text(step.observable) && !(Array.isArray(step.observable) && step.observable.length && step.observable.every(text)))
        fail('observable', `${stepAt}.observable`, 'Expected an observation statement or nonempty statements');
      ref(step.from, objects, `${stepAt}.from`); ref(step.to, objects, `${stepAt}.to`);
      ref(step.relation_id, relations, `${stepAt}.relation_id`, true);
      refs(step.next, steps, `${stepAt}.next`); refs(step.evidence_ids, evidence, `${stepAt}.evidence_ids`);
      // The native-domain package uses null for no additional machine guard;
      // its explanatory guard text remains required and is preserved verbatim.
      if (has(step, 'when') && step.when !== null) condition(step.when, `${stepAt}.when`);
      if (!Array.isArray(step.state_changes)) fail('array', `${stepAt}.state_changes`, 'Expected explicit state changes');
      list(step.state_changes).forEach((change, i) => {
        const changeAt = `${stepAt}.state_changes[${i}]`;
        if (!record(change)) { fail('state_change', changeAt, 'Expected a state change'); return; }
        ref(change.field, fields, `${changeAt}.field`);
        if (has(change, 'writer_id')) ref(change.writer_id, objects, `${changeAt}.writer_id`);
        if (!['set', 'increment', 'append'].includes(change.op)) fail('state_operation', changeAt, 'Unknown state operation');
        if (!has(change, 'value')) fail('state_value', changeAt, 'Expected an explicit value');
        const field = fields.get(change.field), dynamic = record(change.value) && has(change.value, 'input');
        if (dynamic && (Object.keys(change.value).length !== 1 || !text(change.value.input))) fail('state_value', changeAt, 'Input reference must contain only the input field name');
        if (field && ((change.op === 'increment' && (!['integer', 'number'].includes(field.type) || (!dynamic && !matchesType(change.value, field.type)))) || (change.op === 'append' && field.type !== 'array') || (change.op === 'set' && !dynamic && !matchesType(change.value, field.type))))
          fail('state_type', changeAt, 'State change must agree with the declared field type');
      });
    }
    const cases = index(mechanism.cases, `${at}.cases`);
    for (const [caseId, value] of cases) {
      const caseAt = `${at}.cases[${caseId}]`;
      requiredText(value, ['title'], caseAt);
      if (!text(value.outcome) && !(record(value.outcome) && Object.keys(value.outcome).length))
        fail('outcome', `${caseAt}.outcome`, 'Expected a result description or nonempty domain result');
      if (!record(value.inputs)) fail('case_inputs', `${caseAt}.inputs`, 'Expected self-contained case inputs');
      strings(value.path, `${caseAt}.path`);
      list(value.path).forEach((step, i) => ref(step, steps, `${caseAt}.path[${i}]`));
      refs(value.evidence_ids, evidence, `${caseAt}.evidence_ids`);
    }
  }
  for (const [id, source] of evidence) {
    const at = `evidence[${id}]`;
    if (!['code', 'design'].includes(source.kind)) fail('evidence_kind', at, 'Unknown evidence kind');
    requiredText(source, ['claim', 'address', 'symbol', 'excerpt'], at);
    if (!Number.isSafeInteger(source.start_line) || source.start_line < 1 || !Number.isSafeInteger(source.end_line) || source.end_line < source.start_line) fail('evidence_lines', at, 'Expected a valid inclusive source line range');
  }
  for (const [id, entry] of coverage) {
    const at = `coverage[${id}]`;
    ref(id, objects, `${at}.object_id`); requiredText(entry, ['owner'], at);
    if (!['queued', 'researching', 'reviewed', 'verified'].includes(entry.status)) fail('coverage_status', at, 'Unknown research status');
    for (const aspect of ASPECTS) if (!['complete', 'pending'].includes(entry[aspect])) fail('coverage_aspect', `${at}.${aspect}`, 'Expected complete or pending');
    refs(entry.work_item_ids, work, `${at}.work_item_ids`);
    const object = objects.get(id);
    const completeMechanisms = list(object?.mechanism_ids).length > 0 && list(object?.mechanism_ids).every(mechanismId => {
      const mechanism = mechanisms.get(mechanismId);
      return mechanism && list(mechanism.evidence_ids).length > 0 && list(mechanism.steps).length > 0 && list(mechanism.cases).length > 0
        && [...list(mechanism.steps), ...list(mechanism.cases)].every(item => list(item.evidence_ids).length > 0);
    });
    if (entry.status === 'verified' && (ASPECTS.some(aspect => entry[aspect] !== 'complete') || !object || !completeMechanisms || !list(object.evidence_ids).length))
      fail('false_verified', at, 'Verified research needs all five aspects, mechanisms and source evidence');
  }
  if (!record(model.completion) || !['in_progress', 'complete'].includes(model.completion.status)) fail('completion', 'completion', 'Expected an explicit completion status');
  else {
    if (has(model.completion, 'pending_object_ids')) refs(model.completion.pending_object_ids, objects, 'completion.pending_object_ids');
    if (model.completion.status === 'complete' && (!objects.size || list(model.completion.pending_object_ids).length || [...objects].some(([id, object]) => object.kind !== 'external' && coverage.get(id)?.status !== 'verified') || [...coverage.values()].some(item => item.status !== 'verified')))
      fail('false_complete', 'completion', 'Incomplete object research cannot be declared complete');
  }
  return { valid: errors.length === 0, errors };
}

/**
 * Project a closed, already-authorized model. Full publication validation is a
 * separate operation: filtering may remove coverage and evidence records.
 * Throws INVALID_EXPLORATION_SCOPE on broken references needed by this view.
 * Returned records are copies; UI state cannot mutate the pinned input batch.
 */
export function projectExplorationScope(authorizedModel, { rootId = null } = {}) {
  const errors = [], check = inspector(errors);
  const { index, ref, fail } = check;
  const model = record(authorizedModel) ? authorizedModel : {};
  const objects = index(model.objects, 'objects'), relations = index(model.relations, 'relations');
  const mechanisms = index(model.mechanisms, 'mechanisms'), sources = index(model.evidence, 'evidence');
  ref(rootId, objects, 'rootId', true);
  const root = objects.get(rootId) || null;
  const children = root ? list(root.child_ids).map(id => { ref(id, objects, 'root.child_ids'); return objects.get(id); }).filter(Boolean)
    : [...objects.values()].filter(object => object.parent_id === null && object.kind !== 'protocol');
  if (root?.kind === 'protocol' && children.length) fail('protocol_composition', 'rootId', 'A protocol cannot contain composition children');
  for (const child of children) if (child.parent_id !== rootId || child.kind === 'protocol') fail('composition_reciprocal', 'children', 'Expected actual direct composition');
  const direct = new Set(children.map(child => child.id));
  const internalRelations = [], boundaryRelations = [], endpoints = new Map(), protocolSet = new Set(list(root?.protocol_ids));
  if (root?.kind === 'protocol') protocolSet.add(root.id);
  const evidenceSet = new Set(), mechanismEntries = [];
  const collectEvidence = value => {
    for (const id of list(value?.evidence_ids)) { ref(id, sources, 'evidence_ids'); evidenceSet.add(id); }
  };
  // The overall view collapses existing directed relations along real parent
  // links. It is a view projection, never a new relationship in the model.
  const top = id => {
    const seen = new Set();
    while (objects.get(id)?.parent_id != null) {
      if (seen.has(id)) { fail('composition_cycle', 'parent_id', 'Cannot collapse cyclic composition'); break; }
      seen.add(id); id = objects.get(id).parent_id;
    }
    return id;
  };
  const groups = new Map(), localRelations = [];
  const visibleAncestor = id => {
    let current = id; const seen = new Set();
    while (current != null && current !== rootId && !direct.has(current)) {
      if (!objects.has(current) || seen.has(current)) {
        fail('invalid_parent_chain', 'parent_id', 'Cannot project missing or cyclic ancestry'); return id;
      }
      seen.add(current); current = objects.get(current).parent_id;
    }
    return current === rootId || direct.has(current) ? current : id;
  };
  for (const relation of relations.values()) {
    const views = list(relation.scope_views).filter(view => view?.scope_id === rootId);
    if (rootId !== null && !views.length) continue;
    ref(relation.from, objects, 'relation.from'); ref(relation.to, objects, 'relation.to');
    resultFlowErrors(relation, objects, sources, `relations[${relation.id}]`, check);
    scopeErrors({ ...relation, scope_views: views }, objects, `relations[${relation.id}]`, check);
    if (rootId === null) {
      const from = top(relation.from), to = top(relation.to);
      // Self-contained implementation remains in its own composition view.
      if (!direct.has(from) || !direct.has(to)) { fail('invalid_overview_endpoint', 'relations', 'Expected a real non-protocol root'); continue; }
      if (from === to) continue;
      const key = JSON.stringify([from, to, relation.protocol_id]);
      if (!groups.has(key)) groups.set(key, { from, to, originals: [] });
      groups.get(key).originals.push(relation);
    } else {
      const from = visibleAncestor(relation.from), to = visibleAncestor(relation.to);
      if (![from,to].some(id=>id===rootId || direct.has(id))) fail('scope_without_internal_endpoint', 'relations', 'Neither endpoint belongs to this scope');
      if (from === to) localRelations.push({ objectId: from, relation });
      else {
        const key = JSON.stringify([from, to, relation.protocol_id, relation.kind]);
        if (!groups.has(key)) groups.set(key, { from, to, originals: [] });
        groups.get(key).originals.push(relation);
        for (const id of [from, to]) if (!direct.has(id) && id !== rootId && objects.has(id)) endpoints.set(id, objects.get(id));
      }
    }
    if (relation.protocol_id !== null) protocolSet.add(relation.protocol_id);
    collectEvidence(relation); collectEvidence(relation.contract?.result_flow);
  }
  if (rootId === null) {
    // Keep every declared protocol reachable, including protocols contained
    // wholly inside one root. Membership alone never creates an edge.
    for (const object of objects.values()) if (object.kind === 'protocol') protocolSet.add(object.id);
  }
    for (const { from, to, originals } of groups.values()) {
      const ids = [...new Set(originals.map(r => r.protocol_id).filter(Boolean))];
      const names = ids.map(id => objects.get(id)?.title || id);
      const target = (rootId === null || [from,to].every(id=>id===rootId || direct.has(id))) ? internalRelations : boundaryRelations;
      if (rootId !== null && originals.length === 1 && originals[0].from === from && originals[0].to === to) {
        target.push({ ...originals[0], scopeView: originals[0].scope_views.find(v => v.scope_id === rootId) }); continue;
      }
      target.push({
        id: originals[0].id, from, to, kind: 'summary',
        label: names.length ? names.join(' / ') : originals[0].label,
        protocol_id: ids.length === 1 ? ids[0] : null, protocol_ids: ids,
        evidence_ids: [...new Set(originals.flatMap(r => list(r.evidence_ids)))],
        summary: { kind: 'composition_aggregation', relations: originals },
      });
    }
  const protocols = [...protocolSet].map(id => {
    ref(id, objects, 'protocol_ids'); const protocol = objects.get(id);
    if (protocol && protocol.kind !== 'protocol') fail('protocol_kind', 'protocol_ids', 'Expected a protocol object');
    for (const role of list(protocol?.contract?.roles)) ref(role?.object_id, objects, 'protocol.contract.roles');
    return protocol;
  }).filter(Boolean);
  const mechanismIds = new Set([root, ...children, ...endpoints.values()].flatMap(object => list(object?.mechanism_ids)));
  const visibleMechanisms = [];
  for (const id of mechanismIds) {
    ref(id, mechanisms, 'mechanism_ids'); const mechanism = mechanisms.get(id);
    if (mechanism) {
      ref(mechanism.owner_id, objects, 'mechanism.owner_id');
      visibleMechanisms.push(mechanism);
      if (list(root?.mechanism_ids).includes(id)) mechanismEntries.push(mechanism);
      collectEvidence(mechanism);
      for (const participant of list(mechanism.participants)) ref(participant?.object_id, objects, 'mechanism.participants');
      for (const field of list(mechanism.state_fields)) if (has(field, 'authority_id')) ref(field.authority_id, objects, 'mechanism.state_field.authority_id');
      for (const step of list(mechanism.steps)) {
        ref(step.from, objects, 'mechanism.step.from'); ref(step.to, objects, 'mechanism.step.to');
        ref(step.relation_id, relations, 'mechanism.step.relation_id', true); collectEvidence(step);
        for (const change of list(step.state_changes)) if (has(change, 'writer_id')) ref(change.writer_id, objects, 'mechanism.step.state_change.writer_id');
      }
      for (const example of list(mechanism.cases)) collectEvidence(example);
    }
  }
  for (const value of [root, ...children, ...endpoints.values(), ...protocols]) collectEvidence(value);
  if (errors.length) {
    const error = new TypeError('Cannot project an inconsistent authorized exploration scope');
    error.code = 'INVALID_EXPLORATION_SCOPE'; error.errors = errors; throw error;
  }
  return structuredClone({ root, overview: rootId === null, children, internalRelations, boundaryRelations,
    boundaryEndpoints: [...endpoints.values()], localRelations, protocols, mechanismEntries, mechanisms: visibleMechanisms,
    evidence: [...evidenceSet].map(id => sources.get(id)) });
}
