// Experimental protocol only. No production URLs or credential lookup.
const canonical = value => JSON.stringify(normalize(value));
function normalize(value) {
  if (Array.isArray(value)) return value.map(normalize);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, normalize(value[key])]));
  return value;
}
const error = (code, message) => Object.assign(new Error(message), {code});
export function createPersistence({scope, transport, operationId = () => crypto.randomUUID(), status = () => {}}) {
  const identity = structuredClone(scope);
  for (const field of ['actorKey', 'paper_id', 'attachment_id', 'attachment_version']) {
    if (typeof identity[field] !== 'string' || !identity[field]) throw error('scope', `Missing ${field}`);
  }
  if (transport.actorKey !== identity.actorKey) throw error('scope', 'Transport principal does not match session');
  const context = Object.freeze(identity);
  const document = {paper_id: identity.paper_id, attachment_id: identity.attachment_id, attachment_version: identity.attachment_version};
  let active = true, revision, unresolved = null, conflict = null, queue = Promise.resolve();
  const confirmed = new Map();
  const alive = () => {if (!active) throw error('stale', 'Document session ended; changes retained by the old session');};
  const serial = action => {
    const result = queue.then(() => {alive();return action();});
    queue = result.catch(() => {});
    return result;
  };
  function validate(response) {
    alive();
    if (canonical(response.document) !== canonical(document) || typeof response.revision !== 'string') {
      throw error('protocol', 'Response document/version or revision mismatch');
    }
  }
  async function settle() {
    const attempt = unresolved;
    try {
      // Reparse immutable bytes on every attempt: transport mutation cannot alter replay.
      const response = await transport.commit(JSON.parse(attempt.bytes), context);
      validate(response);
      if (response.acknowledged_operation_id !== attempt.id) throw error('protocol', 'Wrong operation acknowledgement');
      revision = response.revision;
      const body = JSON.parse(attempt.bytes);
      for (const id of body.deletes) confirmed.delete(id);
      for (const value of body.upserts) confirmed.set(value.id, value);
      unresolved = null;
      return attempt.intent;
    } catch (cause) {
      if (active) {
        if (cause.status === 409) conflict = cause;
        status(conflict ? 'Revision conflict: pending changes retained; resolve explicitly before continuing' : 'Save unconfirmed: pending request retained for exact replay');
      }
      throw cause;
    }
  }
  function write(upserts, deletes) {
    // Snapshot when invoked, not when an earlier network request eventually completes.
    const intent = canonical({upserts, deletes});
    return serial(async () => {
      if (conflict) throw conflict;
      if (revision === undefined) throw error('unloaded', 'Load document before saving');
      if (unresolved) {
        const previous = await settle(); // Resolve old commit state before sending newer content.
        if (previous === intent) return;
      }
      alive();
      const id = operationId();
      const materialized = JSON.parse(intent);
      materialized.upserts = materialized.upserts.map(value => {
        if (!value.onlyTextOrComment) {delete value.onlyTextOrComment; return value;}
        const baseline = confirmed.get(value.id);
        if (!baseline) throw error('baseline', 'Partial annotation update requires a confirmed full record');
        const full = structuredClone(baseline);
        for (const key of ['text', 'comment', 'dateModified']) {
          if (Object.hasOwn(value, key)) full[key] = value[key];
        }
        return full;
      });
      const bytes = canonical({...document, expected_revision: revision, operation_id: id, format: 'zotero-reader-v1', ...materialized});
      unresolved = {id, bytes, intent};
      await settle();
    });
  }
  return {
    load: () => serial(async () => {
      if (revision !== undefined) throw error('loaded', 'Use a new session to reload; pending changes must be handled explicitly');
      const response = await transport.read({...document}, context);validate(response);
      revision = response.revision;
      for (const value of response.annotations) confirmed.set(value.id, structuredClone(value));
      return structuredClone(response);
    }),
    save: values => values.length ? write(values, []) : serial(async () => {if (conflict) throw conflict;}),
    remove: ids => ids.length ? write([], ids) : serial(async () => {if (conflict) throw conflict;}),
    dispose() {active = false;},
    // Export is explicit; an old session never installs data into a new reader.
    snapshot: () => ({scope: {...identity}, revision, pending: unresolved ? JSON.parse(unresolved.bytes) : null, conflict: Boolean(conflict), active}),
  };
}
