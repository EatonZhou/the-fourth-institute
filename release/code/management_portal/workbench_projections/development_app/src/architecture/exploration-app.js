import { projectExecutionReading, executionCapability } from './exploration-reading.js';
import { createResources, requireReadable } from '../shared/resources.js';
import { workspaceSource } from './exploration-resources.js';
import { createExplorationLoader } from './exploration-data.js';
import { projectExplorationScope } from './exploration-model.js';
import { createExplorationViewHost, instructionWorkbenchFactory } from './exploration-view-host.js';
import { createExplorationSession } from './exploration-session.js';
import { createExplorationFocus, explorationSemanticObject, readExecutionCapabilityFocus } from './exploration-focus.js';
import { createExplorationGraph } from './exploration-graph.js';
import { createInstructionWorkbench } from './instruction-workbench.js';
import { mechanismFactory, mechanismFactories, mechanismIdFactories, objectEntries, objectModes, objectWorkbenchFactory } from './exploration-renderers.js';
import { resolveDomainEntry, resolveDomainModes } from './exploration-entry-routes.js';
import { MANIFEST_REF, EVOLUTION_REF } from './model.js';
import { projectDirectionHighlights } from './exploration-directions.js';
import { createExplorationSearch } from './exploration-search.js';
import './exploration-app.css';


/** A fixed development delivery is explicit; it never substitutes for discovery. */
export async function mountExploration(root, context, { delivery, candidate = null, initialRoute,
  mechanisms = {}, mechanismIds = {}, protocols = {}, entries = {} } = {}) {
  if (!delivery && !candidate) throw new Error('需要架构说明入口');
  const doc = root.ownerDocument, resources = context.resources || createResources(context.sdk, { signal: context.signal });
  const loader = createExplorationLoader({ resources, signal: context.signal });
  const element = (tag, text, cls) => { const node = doc.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
  const shell = element('section', null, 'arch-deep-app'); shell.setAttribute('aria-label', '架构与协议');
  const header = element('header', null, 'ade-header'), backButton = element('button', '←', 'ade-back'), homeButton = element('button', '总览');
  backButton.type = homeButton.type = 'button'; backButton.setAttribute('aria-label', '返回上次视图'); homeButton.setAttribute('aria-label', '回到架构总览 Home');
  const breadcrumb = element('nav'); breadcrumb.setAttribute('aria-label', '架构层次');
  const batch = element('details', null, 'ade-batch'), batchToggle = element('summary'), version = element('span');
  const batchActions = element('div', null, 'ade-batch-actions'), selectionActions = element('div', null, 'ade-selection-actions');
  batchToggle.setAttribute('aria-label', '架构说明与刷新'); batchToggle.append(version); batch.append(batchToggle, batchActions);
  const notice = element('p', '', 'ade-notice'); notice.setAttribute('role', 'status'); notice.hidden = true;
  const sourcePanel = element('aside', null, 'ade-source'); sourcePanel.hidden = true; sourcePanel.setAttribute('aria-label', '当前来源正文');
  const directionPanel = element('aside', null, 'ade-direction'); directionPanel.hidden = true; directionPanel.setAttribute('aria-label', '关联方向');
  const surface = element('div', null, 'ade-body'); header.append(backButton, homeButton, breadcrumb, selectionActions); shell.append(header, notice, directionPanel, surface, sourcePanel); root.replaceChildren(shell);
  shell.addEventListener('click', event => { if (!batch.contains(event.target)) batch.open = false; });
  batch.addEventListener('keydown', event => { if (event.key === 'Escape') { batch.open = false; batchToggle.focus(); event.stopPropagation(); } });
  const host = createExplorationViewHost(surface);
  const factories = { ...mechanismFactories, ...mechanisms }, idFactories = { ...mechanismIdFactories, ...mechanismIds };
  let session, disposed = false, hidden = false, unsubscribe = null, watchKey = '', watchGeneration = 0, headerKey = '', pendingFocus = null;
  let directionInput = null, direction = null, incomingGeneration = 0;
  let observing = null;
  const drafts = new Map(); let activeDraft = null, draftEpoch = 0, lastReading = '';
  const focus = createExplorationFocus({ publish: object => context.sdk.focus(object), accept: receiveFocus });
  let formalCandidate = candidate;
  function message(text) { notice.textContent = text || ''; notice.hidden = !text; }
  function handle(promise) { return Promise.resolve(promise).catch(error => { if (!disposed && !hidden) message(error.message || '视图不可用'); }); }
  function button(label, action, parent) { const node = element('button', label); node.type = 'button'; node.addEventListener('click', () => handle(action())); parent.append(node); return node; }
  const searchRoot = element('div', null, 'ade-search'); header.append(searchRoot, batch);
  const search = createExplorationSearch(searchRoot, {
    authorize: async () => { const bundle = session.getBundle(); await loader.revalidate(bundle); return bundle; },
    isCurrent: bundle => !hidden && !disposed && bundle === session.getBundle(),
    onSelect: async id => {
      const bundle = session.getBundle(); await session.select({ objectId: id });
      if (hidden || disposed || bundle !== session.getBundle() || session.snapshot().status !== 'ready') return;
      const object = bundle.model.objects.find(item => item.id === id); if (!object) return;
      activeDraft = null; draftEpoch++; context.sdk.cancelFeedback?.(); sourcePanel.replaceChildren(); sourcePanel.hidden = false;
      sourcePanel.setAttribute('aria-label', '所选架构对象');
      button('关闭说明', () => { sourcePanel.hidden = true; }, sourcePanel);
      sourcePanel.append(element('h2', object.title), element('p', object.responsibility));
      button(object.kind === 'protocol' ? '进入协议工作台' : '进入对象', () => session.navigate({kind: object.kind === 'protocol' ? 'protocol' : 'composition', objectId: id}), sourcePanel);
      for (const evidenceId of object.evidence_ids) {
        const evidence = bundle.model.evidence.find(item => item.id === evidenceId);
        if (evidence) button(evidence.claim, () => readSource(evidenceId), sourcePanel);
      }
    },
    onEnter: route => session.navigate(route), onSource: id => readSource(id),
    onError: error => handle(Promise.reject(error)),
  });
  function renderDirection() {
    directionPanel.replaceChildren(); directionPanel.hidden = !direction;
    if (!direction) return;
    directionPanel.dataset.directionId = direction.id;
    directionPanel.append(element('strong', '关联方向：' + direction.title), element('span', direction.mappingValid
      ? '本层标出关联模块及其所在边界。' : '本批地图与方向范围未确认，暂不标出关联模块。'));
    if (direction.description) directionPanel.append(element('p', direction.description));
    button('清除方向关联', async () => {
      incomingGeneration++; directionInput = direction = null; renderDirection();
      if (session.snapshot().route?.kind === 'composition') await session.redraw();
    }, directionPanel);
  }
  async function refreshDirection(bundle) {
    if (!directionInput || bundle.mode !== 'current') { direction = null; renderDirection(); return; }
    const input = directionInput;
    try {
      const value = await loader.direction(bundle, input);
      if (input === directionInput && !disposed && bundle === session.getBundle()) direction = value;
    } catch { if (input === directionInput) { directionInput = direction = null; } }
    renderDirection();
  }
  async function semanticObject(bundle, target, stillCurrent) {
    if (bundle.mode !== 'current' || target.identity.local_only) return null;
    const ref = workspaceSource(target.identity.resource_ref);
    let result;
    try { result = requireReadable(await resources.read(ref, 'text'), ref); }
    catch (error) { if (stillCurrent()) message(error.message); return null; }
    if (!stillCurrent()) return null;
    const object = explorationSemanticObject(bundle, target, result.data, new Date().toISOString());
    if (!object) message('本批地图未定位到该对象的行锚，所选对象仅在本地定位。');
    return object;
  }
  async function resolveView(bundle, route) {
    await refreshDirection(bundle);
    const target = bundle.model.objects.find(value => value.id === route.objectId) || null;
    const subject = target ? { objectId: target.id, mechanismId: route.mechanismId, ...target.identity } : null;
    if (route.kind === 'composition' || route.kind === 'capability') {
      const projection = projectExecutionReading(projectExplorationScope(bundle.model, { rootId: route.objectId }), route.kind === 'capability' ? route.viewId : null);
      return { factory: createExplorationGraph, subject,
        content: { ...projection, relatedObjectIds: projectDirectionHighlights(bundle.model, projection, direction), batchKey: bundle.batchKey } };
    }
    if ((target?.catalog_id === 'hatg' && route.kind === 'protocol')
      || (route.mechanismId === 'web.m.isa-discovery-routing' && target?.mechanism_ids.includes(route.mechanismId))) {
      const catalogBundle = await loader.catalog(bundle, 'hatg', {
        lessonSourceSupplement: context.exploration?.lessonSourceSupplement,
        catalogAttachment: bundle.mode === 'current' ? currentCatalogAttachment : null,
      });
      if (catalogBundle.status !== 'available') throw new Error('本批没有 HATG 目录；该协议仍待完成。');
      // A concept's local route stays intact. Its catalog view uses the existing
      // semantic owner; instruction selection still never publishes focus.
      const identityOwner = target.identity.local_only
        ? bundle.model.objects.find(value => value.id === target.identity.focus_owner_id) : target;
      const protocolSubject = await semanticObject(bundle, identityOwner || target, () => session.getBundle() === bundle) || {
        ref: bundle.binding.module_map.address, kind: target.identity.focus_kind,
        status: 'current', source: bundle.pinned.exploration.address,
        observed_at: catalogBundle.content.provenance.observed_at,
      };
      return { factory: instructionWorkbenchFactory(createInstructionWorkbench), subject: protocolSubject, content: catalogBundle.content, catalogBundle };
    }
    const mechanism = bundle.model.mechanisms.find(value => value.id === route.mechanismId);
    const factory = mechanism && (objectWorkbenchFactory(target, mechanism)
      || mechanismFactory(mechanism, { byId: idFactories, byAdapter: factories }));
    if (['mechanism', 'protocol'].includes(route.kind) && factory) return {
      factory, subject,
      content: { mechanism, entryObjectId: target.id,
        mechanisms: bundle.model.mechanisms.filter(value => target.mechanism_ids.includes(value.id)),
        modeMechanisms: resolveDomainModes(bundle.model, target, objectModes),
        objects: bundle.model.objects, relations: bundle.model.relations, evidence: bundle.model.evidence,
        work_items: bundle.model.work_items, batchKey: bundle.batchKey,
        modelDescriptor: bundle.pinned.model },
    };
    if (route.kind === 'protocol' && protocols[target.id]) return protocols[target.id](bundle, route);
    throw new Error('该对象的专属机制尚未接入，仍是本轮待完成工作。');
  }
  function openSource(source) {
    activeDraft = null; draftEpoch++; context.sdk.cancelFeedback?.();
    sourcePanel.replaceChildren(); sourcePanel.hidden = false; sourcePanel.setAttribute('aria-label', '当前来源正文');
    button('关闭来源', () => { sourcePanel.hidden = true; }, sourcePanel);
    sourcePanel.append(element('h2', source.claim || '当前来源'), element('p', source.ref));
    const pre = element('pre', source.excerpt || source.text); sourcePanel.append(pre);
  }
  function update(state) {
    if (disposed) return;
    surface.setAttribute('aria-busy', String(state.status === 'loading'));
    if (state.status === 'loading') { search.close(); sourcePanel.hidden = true; message('正在读取架构说明…'); }
    if (state.status === 'error') { search.close(); sourcePanel.replaceChildren(); sourcePanel.hidden = true; direction = null; renderDirection(); message('视图暂不可用：' + state.error); }
    if (state.status === 'ready') {
      message(state.focusError ? `能力范围仅在本区选中，宿主焦点未更新，请勿用“这里”指代本能力。${state.focusError}`
        : state.refreshError ? '更新暂不可用，保留已加载的说明与阅读位置。'
        : pendingFocus ? '收到当前对象。当前视图恢复显示后再定位。' : '');
      version.textContent = '架构说明';
      batchToggle.title = '说明可随代码演进而更新；来源链接打开当前工作区';
      const key = JSON.stringify([state.route, state.selectionId, state.batchKey]);
      if (key !== headerKey) { headerKey = key; renderHeader(state); }
      const reading = JSON.stringify([state.batchKey, state.route, state.selectionId]);
      if (lastReading && lastReading !== reading) { activeDraft = null; draftEpoch++; sourcePanel.hidden = true; context.sdk.cancelFeedback?.(); }
      lastReading = reading;
      if (activeDraft && !hidden) restoreDraft();
      subscribe().catch(error => { if (!disposed && !hidden) message(error.message); });
    }
  }
  function renderHeader(state) {
    breadcrumb.replaceChildren(); selectionActions.replaceChildren(); batchActions.replaceChildren(); const model = session.getBundle().model;
    const target = model.objects.find(value => value.id === state.route?.objectId), chain = [];
    let at = target;
    while (at) { chain.unshift(at); at = model.objects.find(value => value.id === at.parent_id); }
    for (const object of chain) {
      const link = button(object.title, () => session.navigate({ kind: object.kind === 'protocol' ? 'protocol' : 'composition', objectId: object.id }), breadcrumb);
      link.title = object.title;
      if (object === chain.at(-1)) link.setAttribute('aria-current', 'page');
    }
    if (state.route?.kind === 'capability') {
      const label = element('span', executionCapability.title + ' · 跨进程协作'); label.setAttribute('aria-current', 'page'); breadcrumb.append(label);
    }
    if (state.route?.kind === 'mechanism') breadcrumb.append(element('span', '机制演示'));
    const selected = model.objects.find(value => value.id === state.selectionId);
    if (selected && state.mode === 'current' && !selected.identity.local_only) button('反馈', () => feedback(selected), selectionActions).setAttribute('aria-label', '反馈所选对象');
    batchActions.append(element('small', '工作区架构说明'));
    if (context.architectureEntry) {
      button('刷新架构说明', () => { batch.open = false; return context.architectureEntry.refresh(); }, batchActions);
    }
  }
  function feedback(target) {
    const bundle = session.getBundle(), before = session.snapshot();
    const key = JSON.stringify([bundle.batchKey, target.id]);
    const draft = drafts.get(key) || { text: '', result: '', sending: false }; drafts.set(key, draft);
    activeDraft = { key, targetId: target.id, batchKey: bundle.batchKey }; draftEpoch++;
    sourcePanel.replaceChildren(); sourcePanel.hidden = false;
    button('收起草稿', () => { activeDraft = null; draftEpoch++; sourcePanel.hidden = true; context.sdk.cancelFeedback?.(); }, sourcePanel);
    const form = element('form'), input = element('textarea'), submit = element('button', '交宿主确认'), result = element('p');
    input.required = true; input.setAttribute('aria-label', '反馈内容'); submit.type = 'submit'; result.setAttribute('role', 'status');
    input.value = draft.text; result.textContent = draft.result; submit.disabled = draft.sending;
    input.addEventListener('input', () => { draft.text = input.value; });
    form.append(element('h2', target.title), input, submit, result); sourcePanel.append(form);
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (draft.sending || !draft.text.trim()) return; submit.disabled = draft.sending = true;
      const epoch = draftEpoch;
      const valid = () => !disposed && !hidden && epoch === draftEpoch && session.getBundle() === bundle && session.snapshot().selectionId === target.id;
      try {
        await loader.revalidate(bundle); const object = await semanticObject(bundle, target, valid);
        if (!object || !valid()) throw new Error('对象或时点已改变，草稿保留');
        await focus.send(object); if (!valid()) throw new Error('所选对象已改变');
        const receipt = await context.sdk.feedback({ ref: object.ref, subject: target.title, anchor: target.id,
          scene: { region: 'architecture', mode: bundle.mode, exploration: before.scene }, text: draft.text.trim() });
        draft.result = receipt?.cancelled ? '已取消，草稿保留。' : '回执已交原生对话；请在对话区查看后续回复。';
      } catch (error) { draft.result = (error.code === 'projection.cancelled' ? '已取消' : error.message) + '；草稿保留，不自动重发。'; }
      finally { draft.sending = false; submit.disabled = false; result.textContent = draft.result; }
    }); input.focus();
  }
  function restoreDraft() {
    if (!activeDraft || sourcePanel.hidden === false) return;
    const bundle = session.getBundle(), target = bundle.model.objects.find(value => value.id === activeDraft.targetId);
    if (activeDraft.batchKey === bundle.batchKey && target && !target.identity.local_only && bundle.mode === 'current') feedback(target);
  }
  function sourceError(error) { sourcePanel.hidden = true; message(`来源暂不可用：${error.message}`); }
  function readSource(id) { return session.requestSource(id); }
  session = createExplorationSession({ loader, host, resolveView, openSource, onSourceError: sourceError, onChange: update,
    resolveEntry: (bundle, route) => resolveDomainEntry(bundle.model, route, { ...objectEntries, ...entries }),
    focusView: async (bundle, view, valid) => {
      const object = await readExecutionCapabilityFocus(bundle, resources, valid);
      if (object && valid()) await focus.send(object);
    },
    focusObject: async (bundle, target, valid) => { const object = await semanticObject(bundle, target, valid); if (object && valid()) await focus.send(object); },
  });
  async function subscribe() {
    if (!context.watch || hidden || disposed) return;
    const refs = [...session.getRequiredRefs(), ...(context.architectureEntry ? [MANIFEST_REF] : []), ...(directionInput ? [EVOLUTION_REF] : [])], key = JSON.stringify(refs);
    if (key === watchKey) return;
    watchKey = key; const token = ++watchGeneration; unsubscribe?.(); unsubscribe = null;
    const stop = await context.watch(refs, () => {
      if (token !== watchGeneration || hidden || disposed) return;
      if (observing?.token === token) { observing.again = true; return observing.promise; }
      const pending = observing = { token, again: false };
      return pending.promise = handle((async () => {
        try {
          do {
            pending.again = false;
            await session.revalidate();
            if (token !== watchGeneration || hidden || disposed) return;
            const state = session.snapshot();
            if (state.status === 'ready' && state.mode === 'current' && state.route.kind === 'composition') {
              if (context.architectureEntry) await context.architectureEntry.refresh();
              if (directionInput) {
                const before = JSON.stringify(direction);
                await refreshDirection(session.getBundle());
                if (before !== JSON.stringify(direction)) await session.redraw();
              }
            }
          } while (pending.again && token === watchGeneration && !hidden && !disposed);
        } finally { if (observing === pending) observing = null; }
      })());
    });
    if (token !== watchGeneration || hidden || disposed) stop?.(); else unsubscribe = stop;
  }
  backButton.addEventListener('click', () => handle(session.back()));
  homeButton.addEventListener('click', () => handle(session.navigate({ kind: 'composition', objectId: null })));
  const keydown = event => { if (!hidden && event.altKey && event.key === 'ArrowLeft') { event.preventDefault(); handle(session.back()); } };
  doc.addEventListener('keydown', keydown);
  function receiveFocus(object) {
    if (disposed) return;
    const bundle = session.getBundle(); if (!bundle) return;
    if (object?.kind?.startsWith('direction:')) {
      const token = ++incomingGeneration;
      handle((async () => {
        const value = await loader.direction(bundle, object);
        if (!value || disposed || token !== incomingGeneration || bundle !== session.getBundle()) return;
        directionInput = value.semantic; direction = value;
        if (hidden) return;
        renderDirection();
        // Reauthorize and repaint the same frame, including its original
        // selection, camera and return stack. A running mechanism is untouched.
        if (session.snapshot().route?.kind === 'composition') await session.redraw();
        else await subscribe();
      })());
      return;
    }
    const target = bundle.model.objects.find(value => value.identity.focus_kind === object?.kind && !value.identity.local_only);
    if (!target || !['current', 'proposed'].includes(object.status)) return;
    const token = ++incomingGeneration;
    // Defer the full identity, not just an ID. On return, validate its anchor
    // against the current readable map before changing the reading frame.
    if (hidden) { pendingFocus = { ...object }; return; }
    const state = session.snapshot();
    const valid = () => !disposed && !hidden && token === incomingGeneration && bundle === session.getBundle() &&
      JSON.stringify(session.snapshot().route) === JSON.stringify(state.route) && session.snapshot().selectionId === state.selectionId;
    const task = (async () => {
      const expected = await semanticObject(bundle, target, valid);
      if (!valid() || !expected || object.ref !== expected.ref || object.source !== expected.source) return;
      pendingFocus = null; directionInput = direction = null; renderDirection();
      await session.navigate({ kind: target.kind === 'protocol' ? 'protocol' : 'composition', objectId: target.id });
    })();
    handle(task); return task;
  }
  context.onFocus?.(focus.receive);
  const api = {
    session,
    notify: message,
    async acceptCandidate(next) {
      const state = session.snapshot(), old = formalCandidate; formalCandidate = next;
      const route = state.route && (state.route.objectId === null || next.exploration.model.objects.some(value => value.id === state.route.objectId))
        ? state.route : { kind: 'composition', objectId: null };
      try { await session.install(next.exploration, { route }); }
      catch (error) { formalCandidate = old; throw error; }
    },
    hide() { hidden = true; search.close(); draftEpoch++; context.sdk.cancelFeedback?.(); watchGeneration++; unsubscribe?.(); unsubscribe = null; watchKey = ''; sourcePanel.hidden = true; session.hide(); },
    async show() { if (disposed) return; hidden = false; await session.show(); if (pendingFocus && session.getBundle()?.mode === 'current') { const object = pendingFocus; pendingFocus = null; await receiveFocus(object); } },
    destroy() { if (disposed) return; disposed = true; search.destroy(); focus.destroy(); drafts.clear(); activeDraft = null; watchGeneration++; unsubscribe?.(); session.destroy(); doc.removeEventListener('keydown', keydown); root.replaceChildren(); },
  };
  context.signal?.addEventListener('abort', api.destroy, { once: true }); context.onDispose?.(api.destroy);
  try { if (candidate) await session.install(candidate.exploration, { route: initialRoute || { kind: 'composition', objectId: null } });
    else await session.load(delivery, { ...(initialRoute ? { route: initialRoute } : {}) }); }
  catch (error) { message(error.message); }
  return api;
}
import { currentCatalogAttachment } from './instruction-catalog-binding.js';
