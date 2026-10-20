import { sourceAnchors, MAP_REF } from './model.js';
import { requireReadable } from '../shared/resources.js';
import { workspaceSource } from './exploration-resources.js';
import { executionCapability, hasExecutionCapability } from './exploration-reading.js';

const fields = ['ref', 'kind', 'status', 'source', 'observed_at'];
const key = object => JSON.stringify(fields.map(field => object?.[field]));

// Focus names today's workspace map; authored capability membership may lag.
export async function readExecutionCapabilityFocus(bundle, resources, stillCurrent = () => true) {
  if (bundle.mode !== 'current') return null;
  const unavailable = reason => Object.assign(new Error('能力地图焦点不可用：' + reason), { reason });
  if (!hasExecutionCapability(bundle.model.objects)) throw unavailable('invalid_capability_map');
  const members = executionCapability.memberIds.map(id => bundle.model.objects.find(object => object.id === id));
  const source = members[0].identity.resource_ref;
  if (!source || members.some(object => object.kind !== 'module' || object.identity.local_only
    || object.identity.resource_ref !== source || object.identity.focus_owner_id !== object.id
    || object.identity.focus_kind !== `module:${object.id}`)) throw unavailable('invalid_capability_members');
  requireReadable(await resources.read(MAP_REF, 'text'), MAP_REF);
  if (!stillCurrent()) return null;
  return { ref: MAP_REF, kind: executionCapability.focusKind, status: 'current', source: MAP_REF, observed_at: new Date().toISOString() };
}

// Use the live map's line when present; an outdated explanation still has its
// object identity and can be discussed without a source-version certificate.
export function explorationSemanticObject(bundle, target, mapText, observedAt) {
  if (bundle.mode !== 'current' || !target || target.identity.local_only ||
      !['module', 'protocol'].includes(target.kind)) return null;
  const owner = bundle.model.objects.find(object => object.id === target.identity.focus_owner_id);
  if (!owner || owner.identity.local_only || !['module', 'protocol'].includes(owner.kind)) return null;
  const kind = `${owner.kind}:${owner.id}`, source = workspaceSource(owner.identity.resource_ref);
  if (owner.identity.focus_kind !== kind || target.identity.focus_kind !== kind ||
      workspaceSource(target.identity.resource_ref) !== source) return null;
  const line = sourceAnchors(mapText)[owner.kind === 'module' ? 'modules' : 'protocols'][owner.id];
  return { ref: Number.isInteger(line) && line > 0 ? `${source}#L${line}` : source, kind, status: 'current', source, observed_at: observedAt };
}

// The host broadcasts before replying to focus(). Match its exact revision,
// not just an object ID: a later selection of the same object is independent.
export function createExplorationFocus({ publish, accept }) {
  const pending = new Set();
  let revision = -1, disposed = false;
  function receive(object, metadata = {}) {
    if (disposed || !object || !Number.isInteger(metadata.revision) || metadata.revision <= revision) return;
    revision = metadata.revision;
    const match = [...pending].find(item => item.key === key(object));
    if (match) { match.deliveries.push({ object, metadata }); return; }
    return accept(object, metadata);
  }
  async function send(object) {
    const item = { key: key(object), deliveries: [] }; pending.add(item);
    let ownRevision;
    try {
      const result = await publish(object); ownRevision = result?.revision;
      if (Number.isInteger(ownRevision)) revision = Math.max(revision, ownRevision);
      return result;
    }
    finally {
      pending.delete(item);
      if (!disposed) for (const delivery of item.deliveries) {
        if (delivery.metadata.revision !== ownRevision && delivery.metadata.revision === revision) accept(delivery.object, delivery.metadata);
      }
    }
  }
  return { receive, send, destroy() { disposed = true; pending.clear(); } };
}
