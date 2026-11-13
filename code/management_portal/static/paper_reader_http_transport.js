// management_portal/paper_reader_app/zotero_prototype/persistence.js
var canonical = (value) => JSON.stringify(normalize(value));
function normalize(value) {
  if (Array.isArray(value)) return value.map(normalize);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, normalize(value[key])]));
  return value;
}
var error = (code, message) => Object.assign(new Error(message), { code });
function createPersistence({ scope, transport, operationId = () => crypto.randomUUID(), status = () => {
} }) {
  const identity = structuredClone(scope);
  for (const field of ["actorKey", "paper_id", "attachment_id", "attachment_version"]) {
    if (typeof identity[field] !== "string" || !identity[field]) throw error("scope", `Missing ${field}`);
  }
  if (transport.actorKey !== identity.actorKey) throw error("scope", "Transport principal does not match session");
  const context = Object.freeze(identity);
  const document = { paper_id: identity.paper_id, attachment_id: identity.attachment_id, attachment_version: identity.attachment_version };
  let active = true, revision, unresolved = null, conflict = null, queue = Promise.resolve();
  const confirmed = /* @__PURE__ */ new Map();
  const alive = () => {
    if (!active) throw error("stale", "Document session ended; changes retained by the old session");
  };
  const serial = (action) => {
    const result = queue.then(() => {
      alive();
      return action();
    });
    queue = result.catch(() => {
    });
    return result;
  };
  function validate(response) {
    alive();
    if (canonical(response.document) !== canonical(document) || typeof response.revision !== "string") {
      throw error("protocol", "Response document/version or revision mismatch");
    }
  }
  async function settle() {
    const attempt = unresolved;
    try {
      const response = await transport.commit(JSON.parse(attempt.bytes), context);
      validate(response);
      if (response.acknowledged_operation_id !== attempt.id) throw error("protocol", "Wrong operation acknowledgement");
      revision = response.revision;
      const body = JSON.parse(attempt.bytes);
      for (const id of body.deletes) confirmed.delete(id);
      for (const value of body.upserts) confirmed.set(value.id, value);
      unresolved = null;
      return attempt.intent;
    } catch (cause) {
      if (active) {
        if (cause.status === 409) conflict = cause;
        status(conflict ? "Revision conflict: pending changes retained; resolve explicitly before continuing" : "Save unconfirmed: pending request retained for exact replay");
      }
      throw cause;
    }
  }
  function write(upserts, deletes) {
    const intent = canonical({ upserts, deletes });
    return serial(async () => {
      if (conflict) throw conflict;
      if (revision === void 0) throw error("unloaded", "Load document before saving");
      if (unresolved) {
        const previous = await settle();
        if (previous === intent) return;
      }
      alive();
      const id = operationId();
      const materialized = JSON.parse(intent);
      materialized.upserts = materialized.upserts.map((value) => {
        if (!value.onlyTextOrComment) {
          delete value.onlyTextOrComment;
          return value;
        }
        const baseline = confirmed.get(value.id);
        if (!baseline) throw error("baseline", "Partial annotation update requires a confirmed full record");
        const full = structuredClone(baseline);
        for (const key of ["text", "comment", "dateModified"]) {
          if (Object.hasOwn(value, key)) full[key] = value[key];
        }
        return full;
      });
      const bytes = canonical({ ...document, expected_revision: revision, operation_id: id, format: "zotero-reader-v1", ...materialized });
      unresolved = { id, bytes, intent };
      await settle();
    });
  }
  return {
    load: () => serial(async () => {
      if (revision !== void 0) throw error("loaded", "Use a new session to reload; pending changes must be handled explicitly");
      const response = await transport.read({ ...document }, context);
      validate(response);
      revision = response.revision;
      for (const value of response.annotations) confirmed.set(value.id, structuredClone(value));
      return structuredClone(response);
    }),
    save: (values) => values.length ? write(values, []) : serial(async () => {
      if (conflict) throw conflict;
    }),
    remove: (ids) => ids.length ? write([], ids) : serial(async () => {
      if (conflict) throw conflict;
    }),
    dispose() {
      active = false;
    },
    // Export is explicit; an old session never installs data into a new reader.
    snapshot: () => ({ scope: { ...identity }, revision, pending: unresolved ? JSON.parse(unresolved.bytes) : null, conflict: Boolean(conflict), active })
  };
}

// management_portal/paper_reader_app/zotero_prototype/http_transport.js
async function openHttpDocument({ paperId, principal, fetcher = fetch }) {
  async function call(path, body) {
    const response = await fetcher("/paper/api/reader/" + path, {
      method: body ? "POST" : "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "X-Reader-Principal": principal, ...body ? { "Content-Type": "application/json" } : {} },
      ...body ? { body: JSON.stringify(body) } : {}
    });
    if (!response.ok) throw Object.assign(new Error((await response.json()).error), { status: response.status });
    return response;
  }
  const opened = await (await call("open", { paper_id: paperId })).json();
  if (opened.principal !== principal) throw Error("principal mismatch");
  const document = opened.document;
  const query = new URLSearchParams(document);
  const persistence = createPersistence({ scope: { actorKey: principal, ...document }, transport: {
    actorKey: principal,
    read: async () => (await call("annotations?" + query)).json(),
    commit: async (body) => (await call("annotations", body)).json()
  } });
  return { persistence, options: {
    load: async () => ({ ...await persistence.load(), pdf: new Uint8Array(await (await call("pdf?" + query)).arrayBuffer()), author: principal }),
    save: persistence.save,
    remove: persistence.remove,
    // Position persistence is not advertised until a formal position endpoint exists.
    savePosition: async () => {
    }
  } };
}
export {
  openHttpDocument
};
