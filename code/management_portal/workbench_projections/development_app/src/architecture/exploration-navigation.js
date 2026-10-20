// Local reading history. The loader owns immutable pairing and authorization.
// Callbacks must resolve against the currently authorized snapshot on every call.
const KINDS = new Set(['composition', 'mechanism', 'protocol', 'capability']);
const copy = value => value == null ? null : structuredClone(value);
function error(code) { return Object.assign(new Error(code), { code }); }
function routeShape(value) {
  if (!value || !KINDS.has(value.kind) ||
      (value.objectId !== null && (typeof value.objectId !== 'string' || !value.objectId))) {
    throw error('invalid_exploration_route');
  }
  if (value.kind === 'capability') {
    if (value.objectId !== null || value.mechanismId != null || value.viewId !== 'model-execution') throw error('invalid_exploration_route');
    return { kind: value.kind, objectId: null, mechanismId: null, viewId: value.viewId };
  }
  if (value.viewId != null) throw error('invalid_exploration_route');
  const mechanismId = value.mechanismId ?? null;
  if ((value.kind !== 'composition' && value.objectId === null) ||
      (value.kind === 'mechanism' && !mechanismId) ||
      (mechanismId !== null && (typeof mechanismId !== 'string' || !mechanismId)) ||
      (value.kind === 'composition' && mechanismId !== null)) throw error('invalid_exploration_route');
  return { kind: value.kind, objectId: value.objectId, mechanismId };
}
const routeKey = route => JSON.stringify([route.kind, route.objectId, route.mechanismId, route.viewId ?? null]);
function contextKey(context) {
  if (!context || context.mode !== 'current' ||
      typeof context.batchKey !== 'string' || !context.batchKey) throw error('missing_exploration_batch');
  return JSON.stringify([context.mode, context.batchKey]);
}
function camera(value) {
  return value && [value.x, value.y, value.k].every(Number.isFinite) && value.k > 0
    ? { x: value.x, y: value.y, k: value.k } : null;
}

export function createExplorationNavigation({ resolveRoute, resolveSelection, sanitizeViewState } = {}) {
  if (![resolveRoute, resolveSelection, sanitizeViewState].every(fn => typeof fn === 'function')) {
    throw error('exploration_authorization_callbacks_required');
  }
  const batches = new Map();
  function bucket(context) {
    const key = contextKey(context);
    if (!batches.has(key)) { batches.clear(); batches.set(key, { frames: new Map(), back: [] }); }
    return batches.get(key);
  }
  function resolve(context, requested) {
    const route = routeShape(requested);
    const checked = resolveRoute(context, copy(route));
    if (!checked) throw error('exploration_route_unavailable');
    const normalized = routeShape(checked);
    // Reauthorization cannot silently redirect to a different semantic identity.
    if (routeKey(normalized) !== routeKey(route)) throw error('exploration_route_identity_changed');
    return normalized;
  }
  function clean(context, frame) {
    if (frame.batchKey != null && frame.batchKey !== context.batchKey) throw error('exploration_batch_mismatch');
    if (frame.mode != null && frame.mode !== context.mode) throw error('exploration_mode_mismatch');
    contextKey(context);
    const route = resolve(context, frame.route);
    const selection = id => id && resolveSelection(context, route, id) ? id : null;
    return { batchKey: context.batchKey, mode: context.mode, route,
      selectionId: selection(frame.selectionId), camera: camera(frame.camera),
      returnFocusId: selection(frame.returnFocusId),
      viewState: copy(sanitizeViewState(context, route, copy(frame.viewState))) };
  }
  function save(context, frame) {
    const saved = clean(context, frame);
    bucket(context).frames.set(routeKey(saved.route), saved);
    return copy(saved);
  }
  function restore(context, requested) {
    const route = resolve(context, requested);
    const views = bucket(context);
    const frame = clean(context, views.frames.get(routeKey(route)) || { route });
    views.frames.set(routeKey(route), frame);
    return copy(frame);
  }
  function enter(context, requested, leaving = null, rememberReturn = true) {
    // Validate destination before changing history or saving a departing frame.
    const route = resolve(context, requested);
    const old = leaving ? clean(context, leaving) : null;
    const views = bucket(context);
    const next = clean(context, views.frames.get(routeKey(route)) || { route });
    if (old) {
      views.frames.set(routeKey(old.route), old);
      if (rememberReturn && routeKey(old.route) !== routeKey(route)) views.back.push(copy(old));
    }
    views.frames.set(routeKey(route), next);
    return copy(next);
  }
  function switchMode(context, requested, leaving) {
    const next = routeShape(requested), previous = routeShape(leaving?.route);
    if (!['mechanism', 'protocol'].includes(previous.kind) || next.kind !== previous.kind ||
        next.objectId !== previous.objectId || !previous.mechanismId || !next.mechanismId) {
      throw error('exploration_mode_switch_invalid');
    }
    return enter(context, next, leaving, false);
  }
  function back(context, leaving = null) {
    const views = bucket(context);
    if (leaving) {
      // A revoked active subject is not saved, but must not trap the reader.
      try { save(context, leaving); } catch (e) {
        if (e.code !== 'exploration_route_unavailable') throw e;
      }
    }
    while (views.back.length) {
      const previous = views.back.pop();
      try {
        const frame = clean(context, previous);
        views.frames.set(routeKey(frame.route), frame);
        return copy(frame);
      } catch (e) {
        if (e.code !== 'exploration_route_unavailable') throw e;
        views.frames.delete(routeKey(previous.route));
      }
    }
    return null;
  }
  function revalidate(context) {
    const views = bucket(context);
    for (const [key, frame] of views.frames) {
      try { views.frames.set(key, clean(context, frame)); }
      catch (e) {
        if (e.code !== 'exploration_route_unavailable') throw e;
        views.frames.delete(key);
      }
    }
    views.back = views.back.flatMap(frame => {
      try { return [clean(context, frame)]; }
      catch (e) { if (e.code !== 'exploration_route_unavailable') throw e; return []; }
    });
  }
  return { save, restore, enter, switchMode, back, revalidate,
    clearBatch: context => batches.delete(contextKey(context)), clear: () => batches.clear() };
}

