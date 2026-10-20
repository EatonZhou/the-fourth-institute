import { executionCapability, hasExecutionCapability } from './exploration-reading.js';
import { createExplorationNavigation } from './exploration-navigation.js';

const copy = value => value == null ? null : structuredClone(value);
const invalid = code => Object.assign(new Error(code), { code });

/** Owns the single reading stack and authorization boundary across domain views. */
export function createExplorationSession({ loader, host, resolveView, resolveEntry = (_bundle, route) => route,
  focusObject, focusView, openSource, onSourceError = () => {}, onChange = () => {} }) {
  let bundle = null, frame = null, view = null, generation = 0, selectionGeneration = 0, sourceGeneration = 0;
  let hidden = false, disposed = false, status = 'empty', error = null, refreshError = null, focusError = null;
  let validation = null;
  const object = id => bundle?.model.objects.find(value => value.id === id);
  const context = () => ({ batchKey: bundle.batchKey, mode: bundle.mode });
  function routeAllowed(ctx, route) {
    if (!bundle || ctx.batchKey !== bundle.batchKey || ctx.mode !== bundle.mode) return null;
    if (route.kind === 'composition' && route.objectId === null) return route;
    if (route.kind === 'capability') return route.objectId === null && route.mechanismId == null && route.viewId === executionCapability.id && hasExecutionCapability(bundle.model.objects) ? route : null;
    const target = object(route.objectId);
    if (!target) return null;
    if (route.kind === 'composition') return target.kind !== 'protocol' ? route : null;
    if (route.kind === 'protocol' && target.kind !== 'protocol') return null;
    if (route.mechanismId && (!target.mechanism_ids.includes(route.mechanismId)
      || !bundle.model.mechanisms.some(value => value.id === route.mechanismId))) return null;
    return route;
  }
  const known = id => !!object(id) || !!bundle?.model.relations.some(value => value.id === id);
  const navigation = createExplorationNavigation({
    resolveRoute: routeAllowed,
    resolveSelection: (_ctx, _route, id) => known(id),
    // The whole closed model is reauthorized before restoration. Domain views
    // also validate their own state shape and case/step identities on restore.
    sanitizeViewState: (_ctx, _route, state) => state,
  });
  const snapshot = () => copy({ status, error, refreshError, focusError, hidden, route: frame?.route, selectionId: frame?.selectionId,
    mode: bundle?.mode, batchKey: bundle?.batchKey, commit: bundle?.binding.commit, scene: host.capture().scene });
  const notify = () => onChange(snapshot());
  function leaving() {
    if (!frame) return null;
    const state = host.capture().viewState;
    return { ...frame, viewState: state ?? frame.viewState, camera: state?.camera ?? frame.camera };
  }
  function suspend() {
    const saved = leaving(); host.hide(); view = null;
    if (saved && bundle) { navigation.save(context(), saved); frame = saved; }
    return saved;
  }
  const current = token => !disposed && !hidden && token === generation;
  function failed(cause, token) {
    if (!current(token)) return;
    host.hide(); view = null; status = 'error'; error = cause?.reason || cause?.code || cause?.message || 'unavailable';
    // A revoked return frame must not survive a later navigation operation.
    if (cause?.reason === 'permission_denied' && bundle) { navigation.clearBatch(context()); frame = null; }
    notify();
  }
  async function display(next, token) {
    const prepared = await resolveView(bundle, next.route);
    if (!current(token)) return;
    if (!prepared || typeof prepared.factory !== 'function') throw invalid('exploration_domain_not_implemented');
    if (prepared.catalogBundle) await loader.revalidate(prepared.catalogBundle);
    if (!current(token)) return;
    frame = next; view = prepared;
    const result = await host.mount({ ...prepared, initialState: frame.viewState,
      ports: { select, navigate: navigateFromView, source: requestSource, return: back, change: () => {
        if (current(token)) { const updated = leaving(); if (updated) { frame = updated; navigation.save(context(), updated); } notify(); }
      } } });
    if (!current(token) || result.status !== 'mounted') return;
    status = 'ready'; error = null; notify();
  }
  async function loadCandidate(readCandidate, { route = { kind: 'composition', objectId: 'execution', mechanismId: null } } = {}) {
    if (disposed) throw invalid('exploration_session_destroyed');
    hidden = false; const token = ++generation; selectionGeneration++;
    const previousBundle = bundle, previousFrame = suspend();
    status = 'loading'; error = null; notify();
    try {
      const candidate = await readCandidate();
      if (!current(token)) return;
      if (candidate.status !== 'available') throw invalid('exploration_not_declared');
      bundle = candidate; frame = null; refreshError = null;
      const next = navigation.restore(context(), resolveEntry(bundle, route));
      await display(next, token);
    } catch (cause) {
      if (current(token) && previousBundle && previousFrame) {
        // Retention is a fresh authorization decision, never a cache fallback.
        bundle = previousBundle; frame = previousFrame;
        refreshError = cause?.reason || cause?.code || cause?.message || 'unavailable';
        try {
          await loader.revalidate(previousBundle);
          if (current(token)) await display(navigation.restore(context(), previousFrame.route), token);
        } catch (retentionError) { failed(retentionError, token); }
      } else failed(cause, token);
      throw cause;
    }
  }
  const load = (pin, { mode = 'current', ...options } = {}) => loadCandidate(() => loader.loadDelivery(pin, { mode }), options);
  const install = (candidate, options) => loadCandidate(async () => { await loader.revalidate(candidate); return candidate; }, options);
  function navigate(route) { return navigateTo(route, true); }
  function navigateFromView(requested) {
    if (!bundle || disposed || hidden) return;
    const route = resolveEntry(bundle, requested), previous = frame?.route;
    // Authored modes belong to one object workbench. Keep their reading state
    // separately without inserting mode changes into the graph return path.
    const sameWorkbench = previous && ['mechanism', 'protocol'].includes(previous.kind) &&
      route.kind === previous.kind && route.objectId === previous.objectId &&
      previous.mechanismId && route.mechanismId;
    return navigateTo(route, false, Boolean(sameWorkbench));
  }
  async function navigateTo(route, resolveDefault, switchMode = false) {
    if (!bundle || disposed || hidden) return;
    // Resolve an authored entry before it enters the reading stack. Restoring
    // an existing frame must reauthorize its exact route, never choose again.
    if (resolveDefault) route = resolveEntry(bundle, route);
    // Invalid requests cannot remove the readable current view.
    if (!routeAllowed(context(), route)) throw invalid('exploration_route_unavailable');
    const token = ++generation; selectionGeneration++; const previous = suspend(); status = 'loading'; error = null; notify();
    try {
      await loader.revalidate(bundle); if (!current(token)) return;
      await display(switchMode ? navigation.switchMode(context(), route, previous)
        : navigation.enter(context(), route, previous), token);
    } catch (cause) { failed(cause, token); throw cause; }
  }
  async function back() {
    if (!bundle || disposed || hidden) return;
    const token = ++generation; selectionGeneration++; const previous = suspend(); status = 'loading'; error = null; notify();
    try {
      await loader.revalidate(bundle); if (!current(token)) return;
      const next = navigation.back(context(), previous) || navigation.restore(context(), { kind: 'composition', objectId: null });
      await display(next, token);
    } catch (cause) { failed(cause, token); throw cause; }
  }
  async function select(value) {
    if (!bundle || status !== 'ready' || disposed || hidden) return;
    const id = value?.objectId || value?.relationId;
    if (value?.viewId === executionCapability.id && hasExecutionCapability(bundle.model.objects)) {
      const token = generation, selectedToken = ++selectionGeneration;
      frame.selectionId = null; notify();
      if (bundle.mode === 'current' && focusView) {
        const valid = () => current(token) && selectedToken === selectionGeneration;
        try {
          await loader.revalidate(bundle);
        } catch (cause) { failed(cause, token); throw cause; }
        if (!valid()) return;
        try {
          await focusView(bundle, executionCapability, valid);
          if (valid()) { focusError = null; notify(); }
        } catch (cause) {
          if (!valid()) return;
          // Focus is a separate source/publish operation. Keep the readable
          // graph only after a fresh core check; a real revocation still closes it.
          try { await loader.revalidate(bundle); }
          catch (unavailable) { if (valid()) failed(unavailable, token); throw unavailable; }
          if (valid()) { focusError = cause?.message || 'focus_unavailable'; notify(); }
        }
      }
      return;
    }
    // Instruction/category selection is local state, not a new global kind.
    if (!id) { notify(); return; }
    if (!known(id)) throw invalid('exploration_selection_unavailable');
    const token = generation, selectedToken = ++selectionGeneration;
    frame.selectionId = id; notify();
    const target = object(id);
    if (!target || bundle.mode !== 'current' || target.identity.local_only || !focusObject) return;
    try {
      await loader.revalidate(bundle);
      if (!current(token) || selectedToken !== selectionGeneration) return;
      await focusObject(bundle, target, () => current(token) && selectedToken === selectionGeneration);
    } catch (cause) { failed(cause, token); throw cause; }
  }
  async function source(value) {
    if (!bundle || status !== 'ready' || disposed || hidden) return;
    const token = generation, request = ++sourceGeneration, sourceBundle = bundle;
    const active = () => current(token) && request === sourceGeneration;
    const id = typeof value === 'string' ? value : value?.evidenceId;
    const evidence = bundle.model.evidence.find(item => item.id === id);
    try {
      const source = evidence ? await loader.source(bundle, id)
        : view?.catalogBundle ? await loader.catalogSource(bundle, view.catalogBundle, typeof value === 'string' ? value : value?.ref)
          : null;
      if (!source) throw invalid('exploration_source_unavailable');
      if (active()) await openSource?.(source);
    } catch (cause) {
      if (!active()) return;
      try { await loader.revalidate(sourceBundle); }
      catch (unavailable) {
        // A real denial of the shared model also invalidates a newer route in
        // that model; an obsolete source error alone must not touch its UI.
        if (sourceBundle === bundle) failed(unavailable, generation);
        throw unavailable;
      }
      if (!active()) return;
      // A bad or inaccessible source is not a new fact about the current graph.
      // Keep the reader's route and reject only this source request.
      onSourceError(cause);
      throw cause;
    }
  }
  // DOM event callers have no rejection channel. Error presentation is guarded
  // inside source(), before a newer navigation/source can take over.
  function requestSource(value) { return source(value).catch(() => {}); }
  async function show() {
    if (disposed || !bundle) return;
    // Visibility is a state transition, not a refresh command. The parent
    // mount and SDK's initial visible snapshot can both repeat an already
    // visible state; neither should start another full permission sweep or
    // compete with navigation. Watch/manual refresh still revalidate, and
    // hidden -> visible still authorizes before restoring the frame below.
    if (!hidden && (status === 'ready' || status === 'loading')) return;
    hidden = false; const target = frame?.route || { kind: 'composition', objectId: 'execution' };
    return navigateTo(target, false);
  }
  function revalidate() {
    if (!bundle || disposed || hidden) return Promise.resolve();
    // Observation verifies access to the existing frame. It never competes
    // with navigation, retires the renderer, or turns a ready view into loading.
    if (validation?.token === generation && validation.view === view) {
      validation.again = true; return validation.promise;
    }
    const pending = { token: generation, bundle, view, again: false };
    validation = pending;
    pending.promise = (async () => {
      let checkingCatalog = false;
      try {
        do {
          pending.again = false;
          checkingCatalog = false;
          await loader.revalidate(pending.bundle);
          checkingCatalog = true;
          if (pending.view?.catalogBundle) await loader.revalidate(pending.view.catalogBundle);
        } while (pending.again && current(pending.token) && view === pending.view);
      } catch (cause) {
        // Navigation does not make a denial of its shared model irrelevant.
        // An old catalog-only denial, however, cannot clear an unrelated view.
        if (!disposed && !hidden && pending.bundle === bundle && (!checkingCatalog || pending.view === view)) {
          failed(cause, generation); generation++; selectionGeneration++;
        }
        throw cause;
      } finally { if (validation === pending) validation = null; }
    })();
    return pending.promise;
  }
  async function redraw() {
    if (!bundle || disposed || hidden || status !== 'ready') return;
    const token = ++generation; selectionGeneration++;
    const saved = suspend(); status = 'loading'; notify();
    try { await loader.revalidate(bundle); if (current(token)) await display(saved, token); }
    catch (cause) { failed(cause, token); throw cause; }
  }
  function hide() { if (disposed) return; generation++; selectionGeneration++; suspend(); hidden = true; status = 'hidden'; notify(); }
  function destroy() { if (disposed) return; hide(); disposed = true; host.destroy(); navigation.clear(); bundle = null; frame = null; }
  return { load, install, navigate, back, select, source, requestSource, show, hide, destroy, snapshot,
    revalidate, redraw,
    getBundle: () => bundle,
    getRequiredRefs: () => [...new Set([...(bundle?.requiredRefs || []), ...(view?.catalogBundle?.requiredRefs || [])])],
  };
}
