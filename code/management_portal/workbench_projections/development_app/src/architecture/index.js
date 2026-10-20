import { createEvidenceLoader, limitedMap } from './data.js';
import { createGraph } from './graph.js';
import { createProtocolPlayer } from './player.js';
import { createProtocolWorkbench, supportsProtocolWorkbench } from './protocol-workbench.js';
import { visibleModuleAssociations } from './model.js';
import { createScopeNavigation } from './scope.js';
import { createFloatingPanels } from './floating-panels.js';
import { batchIdentity, batchRefs } from './discovery.js';
import { projectComponentRelations } from './component-relations.js';
import { renderMarkdown } from '../shared/markdown.js';
import { graphForView, impactOf, semanticNode, semanticObject, directionSelection, ancestors, key, auditMatches,
  RevisionGuard, MAP_REF, EVOLUTION_REF, MANIFEST_REF, REPOSITORY } from './model.js';
import './ui.css';
import { mountExploration } from './exploration-app.js';
import { mountDiscoveredArchitecture } from './architecture-mount.js';

export { mountExploration };

export async function mount(root, context) {
  // An explicit research delivery remains available to isolated development.
  // The real lifecycle selects its complete pair through authorized discovery.
  if (context.exploration?.delivery) return mountExploration(root, context, context.exploration);
  return mountDiscoveredArchitecture(root, context, { mountLegacy, mountExploration });
}

async function mountLegacy(root, context, initial = {}) {
  const { sdk, signal } = context, loader = createEvidenceLoader(context), guard = new RevisionGuard();
  let evidence, data, diagram, graph, player, selected, pendingFocus, unsubscribe, worker;
  let hidden = false, disposed = false, detailRevision = 0, fileRevision = 0, simulation = false, retainedScene = null;
  let updateAvailable = false, sourceRevision = 0, fileCursor = null, expanded = false, analyzing = false, auditOn = false;
  let visibleSourceRefs = [], transition = null, permissionRevision = 0, focusRead = null, permissionRead = null, locating = null, locatingView = null;
  let view = { scopeRoot: null, parentContext: false, branch: null, level: 0, expanded: [], files: [], query: '', showPlans: false };
  let refreshInFlight = null, refreshAgain = false, returnFocus = null;
  const navigation = createScopeNavigation({ resolveIdentity: (target, id, scopeRoot) => fromIdentity(id, target, scopeRoot) }), floating = createFloatingPanels(root);
  let relationState = null, mapBeforeProtocol = null;
  const runtime = target => target?.modelVersion === 2 || target?.model_version === 2;
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const button = (text, action, parent, cls = '') => {
    const n = el('button', text, 'portalButton ' + cls); n.type = 'button';
    n.addEventListener('click', () => Promise.resolve().then(action).catch(fail)); parent?.append(n); return n;
  };
  const p = (parent, text, cls) => { const n = el('p', text, cls); parent.append(n); return n; };
  const badge = () => '当前';
  const shell = el('section', null, 'arch-app'); shell.setAttribute('aria-label', '架构与协议');
  const header = el('header', null, 'arch-header'), stateLabel = el('span', '读取架构…', 'arch-badge');
  stateLabel.dataset.testid = 'architecture-state'; header.append(el('h1', '架构与协议'), stateLabel);
  const notice = el('div', null, 'arch-notice'); notice.setAttribute('role', 'status'); notice.hidden = true;
  const controls = el('div', null, 'arch-controls');
  const searchPanel = el('section', null, 'arch-search-panel'); searchPanel.hidden = true;
  searchPanel.setAttribute('aria-label', '搜索架构');
  const search = el('input'); search.type = 'search'; search.placeholder = '模块、协议、Design 或文件'; search.setAttribute('aria-label', '搜索架构对象');
  const searchResults = el('div', null, 'arch-search-results'); searchResults.setAttribute('aria-live', 'polite');
  searchPanel.append(search); button('查找', runSearch, searchPanel);
  button('关闭搜索', () => floating.close('search', true), searchPanel); searchPanel.append(searchResults);
  const searchToggle = button('搜索 ⌘K', () => { floating.toggle('search'); if (!searchPanel.hidden) search.focus(); }, controls);
  const layerMenu = el('details', null, 'arch-layer-menu'); layerMenu.append(el('summary', '图层与导航'));
  floating.register('search', { trigger: searchToggle, panel: searchPanel, isOpen: () => !searchPanel.hidden, setOpen: open => { searchPanel.hidden = !open; searchToggle.setAttribute('aria-expanded', String(open)); } });
  floating.register('layers', { trigger: layerMenu.firstElementChild, panel: layerMenu, isOpen: () => layerMenu.open, setOpen: open => { layerMenu.open = open; } });
  const layerOptions = el('div', null, 'arch-layer-options'); layerMenu.append(layerOptions); controls.append(layerMenu);
  search.addEventListener('keydown', e => { if (e.key === 'Enter') runSearch().catch(fail); });
  const level = el('select'); level.setAttribute('aria-label', '语义层次');
  ['运行单元', '组件', '文件与指令详情'].forEach((text, i) => { const o = el('option', text); o.value = i; level.append(o); });
  level.addEventListener('change', () => setLevel(Number(level.value)).catch(fail)); layerOptions.append(level);
  const planCheck = el('input'); planCheck.type = 'checkbox';
  const planLabel = el('label'); planLabel.append(planCheck, document.createTextNode('拟议 Plan'));
  planCheck.addEventListener('change', () => { view.showPlans = planCheck.checked; render(); }); layerOptions.append(planLabel);
  const auditCheck = el('input'); auditCheck.type = 'checkbox';
  const auditLabel = el('label'); auditLabel.append(auditCheck, document.createTextNode('核对偏离'));
  auditCheck.addEventListener('change', () => toggleAudit(auditCheck.checked).catch(fail)); layerOptions.append(auditLabel);
  button('刷新可读事实', () => reload(true), layerOptions);
  button('返回总览', () => enter(null), layerOptions);
  const parentCheck = el('input'); parentCheck.type = 'checkbox';
  const parentLabel = el('label'); parentLabel.append(parentCheck, document.createTextNode('显示父层协议背景'));
  parentCheck.addEventListener('change', () => { view.parentContext = parentCheck.checked; render(); }); layerOptions.append(parentLabel);
  const scopeStatus = el('p', null, 'arch-scope-status'); scopeStatus.hidden = true;
  const breadcrumb = el('nav', null, 'arch-breadcrumb'); breadcrumb.setAttribute('aria-label', '模块层次');
  const body = el('div', null, 'arch-body'), mapRoot = el('div', null, 'arch-map');
  const inspector = el('aside', null, 'arch-inspector'); inspector.setAttribute('aria-label', '对象检查栏');
  const detail = el('div', null, 'arch-detail');
  const locatingDetail = el('p', '正在定位当前对象，原对象详情暂时隐藏。', 'arch-locating-detail'); locatingDetail.hidden = true;
  button('返回地图', closeDetail, inspector, 'arch-return-map'); inspector.append(locatingDetail, detail); body.append(mapRoot, inspector);
  const auxiliary = el('details', null, 'arch-auxiliary'); auxiliary.append(el('summary', '树形导航与关系文字'));
  const tree = el('div', null, 'arch-tree'); auxiliary.append(tree);
  layerOptions.append(auxiliary);
  // Layer navigation is independent of the selection inspector.
  header.append(controls); shell.append(header, breadcrumb, scopeStatus, notice, searchPanel, body); root.replaceChildren(shell);
  const keyboard = event => {
    if (event.defaultPrevented || hidden || disposed) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); floating.open('search'); search.focus(); }
    if (event.key === 'Escape' && !searchPanel.hidden) { event.preventDefault(); floating.close('search', true); }
    else if (event.key === 'Escape' && shell.classList.contains('arch-detail-open')) { event.preventDefault(); closeDetail(); }
  };
  root.ownerDocument.addEventListener('keydown', keyboard);

  function fail(error) {
    if (disposed || signal?.aborted || error?.name === 'AbortError') return;
    const message = data && error?.code === 'architecture.invalid_batch' ? '候选批次不可用；仍显示先前批次（已陈旧）：' + error.message : error?.message || String(error);
    notify(message, () => evidence ? reload(true) : initialize());
  }
  function notify(message, retry) {
    notice.replaceChildren(el('span', message)); notice.hidden = false;
    if (retry) button('重试 / 显式刷新', retry, notice);
  }
  function clearNotice() { notice.replaceChildren(); notice.hidden = true; }
  function beginLocating(object, token) {
    if (locating === null) {
      const source = inspector.querySelector('.arch-source');
      locatingView = { detailHidden: detail.hidden, source, sourceHidden: source?.hidden };
    }
    locating = token; detailRevision++; fileRevision++;
    detail.hidden = true; locatingDetail.hidden = false;
    if (locatingView.source) locatingView.source.hidden = true;
    for (const part of [body, controls, searchPanel, breadcrumb, auxiliary]) part.inert = true;
    body.setAttribute('aria-busy', 'true');
    notify('正在定位' + (object.kind?.startsWith('module:') ? '当前模块' : '当前对象') + '… 读取完成前，原选择暂不可操作。');
  }
  function endLocating(token = locating) {
    if (locating !== token) return;
    locating = null;
    if (locatingView) {
      detail.hidden = locatingView.detailHidden;
      if (locatingView.source?.isConnected) locatingView.source.hidden = locatingView.sourceHidden;
      locatingView = null;
    }
    locatingDetail.hidden = true;
    for (const part of [body, controls, searchPanel, breadcrumb, auxiliary]) part.inert = false;
    body.removeAttribute('aria-busy');
  }
  function showUpdateNotice() {
    if (updateAvailable && data && simulation && locating === null && !pendingFocus && !disposed && notice.hidden) notify('架构说明已更新；当前模拟保持不变。', () => reload(true));
  }
  // Local navigation owns the view until it commits. Incoming focus is kept
  // separately, so a late receive cannot cancel an explicit transition.
  async function navigate(work) {
    const token = guard.start(); transition = token;
    try { await work(token); }
    catch (error) { if (guard.valid(token)) fail(error); }
    finally {
      if (transition === token) {
        transition = null;
        if (!disposed) await applyPendingFocus();
      }
    }
  }
  async function applyPendingFocus() {
    if (!pendingFocus || transition !== null || !data || !evidence || disposed) return;
    if (simulation) { pendingNotice(); return; }
    const object = pendingFocus, current = selected && semanticObject(data, selected);
    if (current?.ref === object.ref && current?.kind === object.kind) {
      guard.start(); endLocating(); pendingFocus = null; clearNotice(); return;
    }
    if (focusRead?.object === object && guard.valid(focusRead.token)) return;
    await switchCurrent(object, false);
  }
  function pendingNotice() {
    if (!pendingFocus || !simulation) return;
    notify('收到当前对象；仍在' + (simulation ? '本地演示' : badge()) + '。原选择与视窗保留。');
    button('查看当前模块 / 范围', () => switchCurrent(pendingFocus, true), notice);
    button('继续本地演示', () => { pendingFocus = null; clearNotice(); }, notice);
  }
  function openDetail() {
    if (!shell.classList.contains('arch-detail-open')) returnFocus = root.ownerDocument.activeElement;
    shell.classList.add('arch-detail-open'); layerMenu.open = false;
  }
  function closeDetail() {
    if (simulation) return returnMap();
    shell.classList.remove('arch-detail-open');
    if (returnFocus?.isConnected && !inspector.contains(returnFocus)) returnFocus.focus();
    else graph?.focusObject?.(selected?.id);
  }
  function stopPlayer() { player?.destroy(); player = null; simulation = false; visibleSourceRefs = []; shell.classList.remove('arch-playback-wide'); }
  function frame() {
    return { scopeRoot: view.scopeRoot, selectedId: selected?.id || null,
      camera: graph?.getCamera(), filters: { parentContext: view.parentContext, showPlans: view.showPlans, query: view.query },
      returnFocusId: root.ownerDocument.activeElement?.closest?.('[data-node-id]')?.dataset.nodeId || selected?.id || null };
  }
  function saveScope() { if (data && runtime(data)) navigation.save(data, frame()); }
  function fromIdentity(id, target = data, scopeRoot = view.scopeRoot) {
    if (!id) return null;
    if (id.startsWith('relation:')) {
      const bundle = relationState?.target === target ? relationState.result : null;
      if (!bundle) return null;
      const relation = projectComponentRelations(target, bundle, scopeRoot).componentRelations.find(r => 'relation:' + r.id === id);
      return relation ? { id, type: 'relation', label: relation.title, data: relation } : null;
    }
    if (id.startsWith('direction:')) return directionSelection(target, { kind: id, ref: target.evolutionRef, source: target.evolutionRef });
    const split = id.indexOf(':'); return split > 0 ? nodeFor(id.slice(0, split), id.slice(split + 1), target) : null;
  }
  function applyScope(saved) {
    view = { ...view, ...saved.filters, scopeRoot: saved.scopeRoot, branch: saved.scopeRoot, level: saved.scopeRoot ? 1 : 0, files: [], expanded: [] };
    selected = fromIdentity(saved.selectedId); fileCursor = null;
    graph.setCamera(saved.camera || { x: 0, y: 0, k: 1 }); render();
    if (saved.returnFocusId) graph.focusObject?.(saved.returnFocusId, false);
  }
  function existingRoot(target, previous = data, id = view.scopeRoot) {
    return ancestors(previous?.modules || [], id).reverse().find(id => target.modules.some(m => m.id === id)) || null;
  }
  async function prepareRelations(target) {
    const result = runtime(target) ? await loader.componentRelations(target) : { status: 'unavailable', reason: 'legacy_map', relations: [] };
    const candidate = !data || batchIdentity(target.manifest) !== batchIdentity(data.manifest);
    if (candidate && target.mode === 'current' && target.manifest.discoveryPin?.component_relations?.status === 'available' && result.status !== 'available') {
      throw Object.assign(new Error('候选批次的组件关系未通过核验：' + result.reason), { code: 'architecture.invalid_batch' });
    }
    return result;
  }
  function setRelations(target, result) { relationState = { target, result }; }
  function relationView() {
    return relationState?.target === data ? projectComponentRelations(data, relationState.result, view.scopeRoot)
      : { status: 'unavailable', reason: 'not_checked', componentRelations: [], contextModuleIds: [] };
  }
  function returnMap() {
    const saved = mapBeforeProtocol; mapBeforeProtocol = null; stopPlayer(); detailRevision++;
    inspector.querySelector('.arch-source')?.remove(); detail.hidden = false;
    shell.classList.remove('arch-detail-open');
    if (saved && runtime(data)) applyScope(saved); else render();
    if (selected) graph.focusObject?.(selected.id, false);
  }
  async function locate(item, publish = true) {
    if (!item) return;
    if (runtime(data)) {
      const owner = item.type === 'module' ? item.data : item.type === 'design' ? data.modules.find(m => m.designs.includes(item.ref)) : null;
      if (owner) await enter(owner.parent || null);
    }
    await choose(item, publish); graph.focusObject?.(item.id);
  }
  function render() {
    if (!data || disposed) return;
    const scopeModules = data.mode === 'current' && selected?.type === 'direction' ? selected.data.modules : [];
    diagram = graphForView(data, { ...view, scopeModules });
    if (diagram.runtime) {
      const projected = relationView(); diagram.componentRelations = projected.componentRelations;
      for (const id of projected.contextModuleIds) {
        const item = nodeFor('module', id);
        if (item && !diagram.nodes.some(n => n.id === item.id) && diagram.boundary?.id !== item.id && !diagram.context.some(n => n.id === item.id)) diagram.context.push(item);
      }
      diagram.relationsStatus = projected.status;
      scopeStatus.hidden = !view.scopeRoot || (projected.status === 'available' && projected.componentRelations.length > 0);
      scopeStatus.textContent = projected.status === 'error' ? '组件关系读取或校验失败；基础模块仍可浏览。' : projected.reason === 'binding_mismatch' ? '本批未匹配组件关系补充；不使用其他批次关系。' : '本层尚无可读的已核实组件关系；不表示组件之间没有交互。';
      if (projected.status === 'error') button('重试组件关系', () => revalidatePermissions(true), scopeStatus);
    }
    if (!diagram.runtime) scopeStatus.hidden = true;
    shell.dataset.scopeRoot = view.scopeRoot || '';
    const highlighted = analyzing && selected ? impactOf(data, selected, expanded).highlighted : new Set();
    for (const id of scopeModules) highlighted.add(key('module', id));
    if (view.showPlans) for (const plan of data.plans.filter(p => p.module_mapping !== 'needs_review')) for (const id of plan.modules) highlighted.add(key('module', id));
    if (auditOn && data.audit) for (const f of data.audit.findings) if (f.module) highlighted.add(key('module', f.module));
    if (diagram.runtime) {
      const moduleIds = [...highlighted].filter(id => id.startsWith('module:')).map(id => id.slice(7));
      for (const { visibleId } of visibleModuleAssociations(data, diagram, moduleIds)) highlighted.add(key('module', visibleId));
    }
    graph.render({ ...diagram, expanded: view.expanded, overview: runtime(data) ? !view.scopeRoot : !view.branch && view.level === 0,
      selected: selected?.id, highlighted, scopeKey: runtime(data) ? view.scopeRoot || 'overview' : view.branch || 'overview' });
    stateLabel.textContent = badge();
    stateLabel.hidden = data.mode === 'current';
    stateLabel.title = '当前读取时间 ' + data.observedAt;
    level.value = view.level; planCheck.checked = view.showPlans; planCheck.disabled = !data.evolutionAvailable;
    planLabel.title = data.evolutionAvailable ? '同一时点 evolution.yaml 的 plans[].modules' : '此时点缺少 evolution.yaml，图层禁用';
    const snapshot = data.currentSource;
    const audit = (data.manifest.audits || []).find(a => a.id === snapshot?.audit_ids?.at(-1));
    auditCheck.disabled = !auditMatches(snapshot, audit); auditCheck.checked = auditOn;
    auditLabel.title = auditCheck.disabled ? '尚无此版本核对报告，或当前提交未核实；不按零条计数' : '目标 commit 与全部输入 hash 匹配';
    breadcrumb.hidden = false; parentCheck.checked = view.parentContext; parentCheck.disabled = !view.scopeRoot;
    breadcrumb.replaceChildren(); button('地图总览', () => enter(null), breadcrumb);
    for (const id of ancestors(data.modules, runtime(data) ? view.scopeRoot : view.branch)) button(data.modules.find(m => m.id === id).title, () => enter(id), breadcrumb);
    tree.replaceChildren();
    const ul = el('ul');
    for (const item of [...diagram.nodes, ...(diagram.protocols || [])]) { const li = el('li'); button(item.label, () => choose(item), li); li.append(document.createTextNode(' · ' + (item.type === 'module' ? item.data.responsibility : item.subtitle))); ul.append(li); }
    tree.append(ul); const relations = el('ul');
    for (const edge of diagram.edges) relations.append(el('li', (diagram.nodes.find(n => n.id === edge.source)?.label || '') + ' — ' + edge.label + ' — ' + (diagram.nodes.find(n => n.id === edge.target)?.label || '')));
    tree.append(relations);

  }
  function nodeFor(type, id, target = data) {
    return semanticNode(target, type, id);
  }
  async function choose(item, publish = true) {
    if (!item || disposed || locating !== null) return;
    if (selected?.id !== item.id) void sdk.cancelFeedback?.().catch(() => {});
    stopPlayer(); detailRevision++; selected = item; analyzing = false; retainedScene = null;
    inspector.querySelector('.arch-source')?.remove(); detail.hidden = false;
    render(); openDetail();
    if (item.type === 'protocol' && !item.removed) detail.replaceChildren(el('h2', item.label), el('p', '正在读取此协议的固定讲解依据…'));
    else showDetails(item);
    if (publish && !item.removed) {
      const object = semanticObject(data, item);
      if (object && (await loader.access(object.ref)).readable && selected === item && !simulation && locating === null) await sdk.focus(object);
    }
    if (selected === item && item.type === 'protocol' && !item.removed) await startSimulation(item);
  }
  async function enter(id) {
    if (!data || locating !== null) return;
    floating.closeAll();
    if (runtime(data)) {
      const saved = navigation.enter(data, id, frame());
      guard.start(); detailRevision++; fileRevision++; mapBeforeProtocol = null;
      stopPlayer(); analyzing = false; retainedScene = null;
      inspector.querySelector('.arch-source')?.remove(); detail.hidden = false;
      shell.classList.remove('arch-detail-open'); search.value = saved.filters.query || '';
      applyScope(saved); return;
    }
    stopPlayer(); view.branch = id; view.query = ''; search.value = ''; view.files = []; fileCursor = null;
    view.level = id ? Math.max(1, view.level) : 0; render();
    if (id) { await choose(nodeFor('module', id), false); await designLevels(); if (view.level >= 2) await loadFiles(); }
  }
  async function setLevel(next) {
    if (!data) return;
    if (runtime(data) && next < 2) { await enter(next ? selected?.type === 'module' ? selected.localId : view.scopeRoot : null); return; }
    if (next && !view.branch) {
      if (selected?.type === 'module') view.branch = selected.localId;
      else { notify('先选择一个模块分支，再展开 Design、文件与指令。'); level.value = view.level; return; }
    }
    view.level = next;
    if (next && view.branch && !view.expanded.includes(view.branch)) view.expanded.push(view.branch);
    if (!next) view.expanded = [];
    render(); if (next >= 1 && data.modelVersion !== 2) await designLevels(); if (next >= 2) await loadFiles();
  }
  async function designLevels() {
    if (data?.mode !== 'current' || !view.branch) return;
    const target = data, branch = view.branch;
    const refs = [...new Set(data.modules.filter(m => m.id === branch || m.parent === branch).flatMap(m => m.designs))];
    await limitedMap(refs.filter(ref => !target.designMeta[ref]?.bodyChecked), async ref => {
      try { const body = await loader.body(ref); Object.assign(target.designMeta[ref], { metaness: body.metaness, bodyVersion: body.version, bodyChecked: true }); }
      catch { if (target.designMeta[ref]) target.designMeta[ref].bodyChecked = true; }
    });
    if (target === data && view.branch === branch && !disposed) render();
  }
  async function loadFiles(append = false, query = '') {
    const token = ++fileRevision, target = data, branch = view.branch;
    const moduleIds = query ? [] : data.modules.filter(m => m.id === branch || m.parent === branch).map(m => m.id);
    notify('正在按当前权限展开文件与指令…');
    const [files] = await Promise.all([loader.files(target, { moduleIds, query, offset: append ? fileCursor || 0 : 0 }),
      target.mode === 'current' && !target.instructions.length ? loader.instructions(target) : null]);
    if (disposed || target !== data || token !== fileRevision) return;
    view.files = append ? [...view.files, ...files.items] : files.items; fileCursor = files.next;
    if (query && files.items.length) view.branch = files.items[0].module_matches[0]?.module_id || view.branch;
    render(); notice.hidden = true;
    if (files.missing) notify('当前地图没有匹配的固定文件索引；文件展开缺少依据。');
    if (selected?.type === 'module') showDetails(selected);
  }
  async function runSearch() {
    if (!data) return;
    const term = search.value.trim().toLocaleLowerCase(), target = data, token = ++fileRevision;
    searchResults.replaceChildren(); if (!term) return;
    const all = [...data.modules.map(m => nodeFor('module', m.id)), ...data.protocols.map(p => nodeFor('protocol', p.id)),
      ...Object.keys(data.designMeta).map(ref => nodeFor('design', ref))].filter(Boolean);
    const matches = all.filter(n => [n.label, n.subtitle, n.localId, n.ref, n.data?.description, ...(n.data?.aliases || [])].join(' ').toLocaleLowerCase().includes(term));
    const addResult = item => button(({module:'模块',protocol:'协议',design:'Design',file:'文件'}[item.type] || item.type) + ' · ' + item.label, async () => {
      floating.close('search'); view.query = ''; await locate(item);
    }, searchResults, 'arch-search-result');
    matches.forEach(addResult);
    if (!matches.length) {
      searchResults.append(el('p', '正在查找已登记文件…'));
      const result = await loader.files(target, { query: term, limit: 40 });
      if (target !== data || token !== fileRevision || disposed) return;
      searchResults.replaceChildren();
      for (const f of result.items) addResult({ id: key('file', f.path), type:'file', label:f.path, subtitle:f.path,
        ref:REPOSITORY + '/file/' + f.path, source:data.indexRef, data:f });
      if (!result.items.length) p(searchResults, result.missing ? '没有匹配对象；本版本文件索引尚未提供。' : '没有匹配的可读对象。');
    }
  }
  function sourceLink(parent, ref, label = '查看来源') { if (ref) button(label, () => showSource(ref), parent, 'arch-reference-button'); }
  function showDetails(item) {
    if (simulation || selected !== item) return;
    detail.replaceChildren(el('h2', item.label), el('p', badge(), 'arch-badge'));
    p(detail, item.type === 'module' ? item.data.responsibility : item.type === 'protocol' ? item.data.description : item.subtitle);
    const actions = el('div', null, 'arch-actions'); detail.append(actions);
    if (item.type === 'relation') {
      const relation = item.data;
      p(detail, '局部关系 · ' + relation.kind + ' · 不是协议参与集合或全局执行轨迹');
      for (const endpoint of relation.endpoints) {
        const actual = nodeFor('module', endpoint.moduleId);
        if (actual) button(actual.label + ' · ' + endpoint.role, () => locate(actual), detail);
      }
      p(detail, JSON.stringify(relation.interaction, null, 2), 'arch-provenance');
      for (const basis of relation.sourceBasis || []) sourceLink(detail, basis.body?.address && basis.body.address + (Number.isInteger(basis.line_start) ? '#L' + basis.line_start + (basis.line_end ? '-L' + basis.line_end : '') : ''), '固定依据 · ' + (basis.section || basis.ref || ''));
      const protocolIds = [...new Set([relation.protocolId, ...(relation.relatedProtocolIds || [])].filter(Boolean))];
      for (const id of protocolIds) { const protocol = nodeFor('protocol', id); if (protocol) button('查看关联协议 · ' + protocol.label, () => choose(protocol), detail); }
      return;
    }
    if (item.type === 'direction') {
      detail.querySelector('.arch-badge').textContent = '拟议方向 · 当前依据';
      p(detail, item.data.modules.length ? '方向范围 · 已核实模块 ' + item.data.modules.length : '方向的模块范围尚不能与当前地图核对。', 'arch-direction-range');
      const members = el('ul', null, 'arch-direction-modules');
      for (const id of item.data.modules) {
        const li = el('li'); li.dataset.moduleId = id;
        button(data.modules.find(m => m.id === id).title + ' · ' + id, () => locate(nodeFor('module', id)), li); members.append(li);
      }
      detail.append(members);
      p(detail, '范围来自方向自身的模块映射；关联 Plan 另列，不决定方向范围。');
      if (item.data.mappingIssue || item.data.incomplete) p(detail, item.data.mappingIssue || '方向的地图版本或部分模块尚未核实，未作当前高亮。');
      for (const plan of item.data.plans || []) sourceLink(detail, plan.id, '关联 Plan · ' + plan.title + (plan.module_mapping === 'needs_review' ? '（映射待核对）' : ''));
    }
    if (item.type === 'module') {
      button('进入此模块', () => enter(item.localId), actions);
      if (runtime(data) && item.data.parent !== view.scopeRoot && (item.data.parent || view.scopeRoot)) button('定位到所属层', () => locate(item, false), actions);
      button('展开文件与指令', () => { view.branch = item.localId; return setLevel(2); }, actions);
      if (item.data.runtime || item.data.deployment) p(detail, [item.data.runtime, item.data.deployment].filter(Boolean).join(' · '), 'arch-runtime-note');
      if (item.data.deployed_in?.length) p(detail, '运行于：' + item.data.deployed_in.map(id => data.modules.find(m => m.id === id)?.title || id).join('、'));
      const protocols = data.protocols.filter(p => p.modules.includes(item.localId));
      if (protocols.length) { const links = el('div', null, 'arch-protocol-links'); detail.append(el('h3', '参与的协议'), links); for (const protocol of protocols) button(protocol.title || protocol.id, () => choose(nodeFor('protocol', protocol.id)), links); }
      const designs = el('ul');
      for (const ref of item.data.designs) { const li = el('li'); button(data.designMeta[ref]?.title || ref, () => { view.branch = item.localId; view.level = 1; return choose(nodeFor('design', ref)); }, li, 'arch-reference-button'); designs.append(li); }
      if (item.data.designs.length) detail.append(el('h3', '设计依据'), designs);
      p(detail, '代码匹配规则：' + (item.data.code_paths.join('、') || '本模块未登记；不继承父模块路径'));
      p(detail, '指令 owner：' + (item.data.instruction_owners.join('、') || '未登记'));
      if (view.level >= 2) {
        const files = el('ul', null, 'arch-file-list');
        for (const f of view.files) { const li = el('li'); button(f.path, () => choose({id:key('file',f.path),type:'file',label:f.path,subtitle:f.path,ref:data.mode === 'current' ? REPOSITORY+'/file/'+f.path : f.content_address,source:data.indexRef,data:f}), li); files.append(li); }
        detail.append(el('h3', '文件'), files);
        for (const instruction of data.instructions.filter(x => item.data.instruction_owners.includes(x.owner))) button(instruction.instruction, () => choose(nodeFor('instruction', instruction.instruction)), detail);
        if (fileCursor != null) button('继续展开可读文件', () => loadFiles(true), detail);
      }
    }
    if (item.type === 'protocol') {
      p(detail, '参与关系无方向，不表示调用全序。'); const members = el('ul');
      for (const id of item.data.modules) { const li = el('li'); button(data.modules.find(m => m.id === id)?.title || id, () => locate(nodeFor('module', id)), li); members.append(li); }
      detail.append(members); p(detail, item.data.example || '暂无具体请求例子');
      for (const ref of item.data.designs) sourceLink(detail, ref, '依据 · ' + (data.designMeta[ref]?.title || ref));
      for (const ref of item.data.related_designs || []) sourceLink(detail, ref, '参考设计 · ' + (data.designMeta[ref]?.title || ref));
      const launch = button('具体请求 · 条件演示', () => startSimulation(item), actions);
      launch.title = '读取 owner 核对的独立 simulated 场景';
    }
    if (item.type === 'file') {
      p(detail, '文件索引时点 ' + data.currentSource?.commit + ' · blob ' + item.data.blob_sha, 'arch-provenance');
      p(detail, '匹配模块：' + (item.data.module_matches.map(m => m.module_id + ' [' + m.patterns.join(', ') + ']').join('；') || '未映射，影响未知'));
    }
    if (item.type === 'instruction') p(detail, item.data.description || '暂无指令说明');
    if (['design', 'plan'].includes(item.type)) {
      sourceLink(detail, item.ref, '阅读正文');
    }
    sourceLink(detail, item.ref, '来源位置');
    button('分析影响', () => showImpact(item), actions);
    if (data.mode === 'current' && item.ref && !item.removed) button(item.type === 'protocol' ? '讨论协议解释' : '反馈此对象', () => feedback(item), actions);
    if (retainedScene && item.type === 'protocol') p(detail, '已返回实际协议。草稿可附带演示条件与步骤；演示仍为 simulated。');
  }
  function showImpact(item) {
    analyzing = true; render();
    detail.replaceChildren(el('h2', '关联影响 · ' + item.label), el('p', badge(), 'arch-badge'));
    const result = impactOf(data, item, expanded);
    button(expanded ? '切换直接关联' : '展开关联可达', () => { expanded = !expanded; showImpact(item); }, detail).setAttribute('aria-pressed', String(expanded));
    p(detail, expanded ? '关联可达不是调用依赖，也不证明必然受影响。' : '直接关联来自地图归属、协议依据与文件匹配。');
    p(detail, result.unknown ? '缺少映射，影响未知；不能解读为没有影响。' : '可读关联模块 ' + result.modules.length + ' · 协议 ' + result.protocols.length + ' · 拟议 Plan ' + result.plans.length);
    const list = el('ul');
    for (const path of result.paths) { const li = el('li', (path.module || path.protocol || '关联') + ' · ' + path.relation + ' '); sourceLink(li, path.evidence, '关系依据'); list.append(li); }
    for (const id of result.protocols) { const li = el('li'); button('协议参与 · ' + id, () => choose(nodeFor('protocol', id)), li); list.append(li); }
    for (const id of result.plans) { const li = el('li'); button('拟议影响 · ' + (data.plans.find(m => m.id === id)?.title || id), () => { view.showPlans = true; return choose(nodeFor('plan', id)); }, li); list.append(li); }
    detail.append(list);
    if (data.mode === 'current') button('在未来演化查看当前关联', async () => {
      const object = semanticObject(data, item);
      if (object && (await loader.access(object.ref)).readable) { await sdk.focus(object); await sdk.openPanel('evolution'); }
    }, detail);
    button('清除分析', () => { analyzing = false; render(); showDetails(item); }, detail); openDetail();
  }
  async function showSource(ref, source) {
    const token = ++detailRevision, target = data, oldScene = simulation;
    if (/^https?:/.test(ref)) { notify('外部引用不能在本区作为已鉴权正文读取。'); return; }
    player?.hide(); openDetail();
    const result = await loader.body(ref);
    if (disposed || token !== detailRevision || target !== data) return;
    visibleSourceRefs.push(ref);
    const pane = el('section', null, 'arch-source');
    pane.append(el('h2', '来源正文'), el('p', ref + '\n独立正文版本 ' + result.version, 'arch-provenance'));
    if (source) p(pane, '依据段落 ' + (source.section || '') + ' · 行 ' + source.line_start + '–' + source.line_end + ' · SHA256 ' + source.body_sha256);
    button(oldScene ? '返回演示' : '返回对象', () => { pane.remove(); detail.hidden = false; if (oldScene) player?.show(); else if (selected) showDetails(selected); }, pane);
    const content = el('div', null, 'arch-markdown'); renderMarkdown(content, result.data, { onLink: link => showSource(link).catch(fail) }); pane.append(content);
    inspector.querySelector('.arch-source')?.remove(); inspector.append(pane); detail.hidden = true;
    if (selected?.type === 'design' && data.mode === 'current') {
      Object.assign(data.designMeta[selected.ref], { metaness: result.metaness, bodyVersion: result.version }); render();
    }
  }
  async function startSimulation(item) {
    const token = ++detailRevision, target = data;
    let scenes;
    try { scenes = await loader.scenarios(target, item.localId); }
    catch (error) {
      if (disposed || token !== detailRevision || data !== target || selected !== item) return;
      showDetails(item); p(detail, '场景依据无法读取或校验：' + error.message + '。保留实际协议，不构造演示。');
      return;
    }
    if (disposed || token !== detailRevision || data !== target || selected !== item) return;
    if (!scenes.length) { showDetails(item); p(detail, '当前没有可读的逐步场景；不据参与关系构造异常或并发。'); return; }
    mapBeforeProtocol = frame(); simulation = true; analyzing = false; floating.closeAll();
    shell.classList.add('arch-playback-wide'); detail.replaceChildren();
    button('返回地图', returnMap, detail, 'arch-workbench-return');
    const choices = el('select'); choices.setAttribute('aria-label', '协议演示场景');
    const ordered = [...scenes].sort((a, b) => Number(supportsProtocolWorkbench(b)) - Number(supportsProtocolWorkbench(a)));
    for (const scene of ordered) { const o = el('option', scene.title || scene.scenario_id); o.value = scene.scenario_id; choices.append(o); }
    choices.hidden = scenes.length < 2; detail.append(choices); const playerRoot = el('div', null, 'arch-protocol-workbench'); detail.append(playerRoot);
    const start = scene => {
      visibleSourceRefs = [...new Set([
        ...(scene.source_versions || []).flatMap(s => [s.ref, s.body?.address]),
        ...((target.manifest.scenario_catalog || []).filter(entry => entry.protocol_id === scene.protocol_id && entry.scenario_id === scene.scenario_id).map(entry => entry.artifact?.address)),
        ...(!(target.manifest.scenario_catalog || []).some(entry => entry.protocol_id === scene.protocol_id) ? [...batchRefs(target.manifest), relationState?.result?.pinned?.artifact?.address] : []),
        ...(scene.steps || []).flatMap(step => (step.source_refs || []).flatMap(source => [source.ref, source.snapshot_address])),
        ...(scene.branches || []).flatMap(branch => [branch, ...(branch.steps || [])].flatMap(step => (step.source_refs || []).flatMap(source => [source.ref, source.snapshot_address]))),
      ].filter(Boolean))];
      player?.destroy();
      const create = supportsProtocolWorkbench(scene) ? createProtocolWorkbench : createProtocolPlayer;
      player = create(playerRoot, { scenario: scene,
        onSource: ref => showSource(ref).catch(fail),
        onReturn: sceneState => returnToProtocol(item.localId, sceneState).catch(fail),
        onChange: sceneState => { retainedScene = sceneState; } });
    };
    start(ordered[0]); await subscribe();
    if (disposed || token !== detailRevision || data !== target || selected !== item || !simulation) return;
    choices.addEventListener('change', () => { start(ordered.find(s => s.scenario_id === choices.value)); subscribe().catch(fail); }); openDetail();
  }
  async function returnToProtocol(id, scene) {
    pendingFocus = null;
    return navigate(async token => {
      const nextEvidence = await loader.manifest();
      if (await context.architectureEntry?.acceptEvidence(nextEvidence)) return;
      const refreshed = await loader.current(nextEvidence);
      const relations = await prepareRelations(refreshed);
      if (!guard.valid(token) || disposed) return;
      if (!refreshed.protocols.some(m => m.id === id)) { notify('当前协议不存在或不可读，保留本地演示。'); return; }
      const scopeRoot = existingRoot(refreshed); saveScope(); stopPlayer(); evidence = nextEvidence; data = refreshed; setRelations(refreshed, relations); view.scopeRoot = scopeRoot; mapBeforeProtocol = null; view.files = []; auditOn = false;
      selected = nodeFor('protocol', id); retainedScene = scene; detail.hidden = false;
      inspector.querySelector('.arch-source')?.remove(); render(); showDetails(selected); openDetail(); clearNotice(); await subscribe();
    });
  }
  function feedback(item) {
    if (simulation || data.mode !== 'current' || locating !== null) return;
    const original = data, scene = retainedScene, form = el('form', null, 'arch-feedback');
    form.append(el('h3', item.type === 'protocol' ? '讨论协议解释' : '反馈此对象'));
    const input = el('textarea'); input.required = true; input.setAttribute('aria-label', '反馈内容'); form.append(input);
    const submit = el('button', '交宿主确认', 'portalButton'); submit.type = 'submit'; form.append(submit);
    const result = el('p'); result.setAttribute('role', 'status'); form.append(result);
    button('收起草稿', () => { form.hidden = true; button('恢复反馈草稿', () => { form.hidden = false; input.focus(); }, detail); }, form);
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (submit.disabled || !input.value.trim()) return; submit.disabled = true;
      try {
        if (data !== original || simulation || data.mode !== 'current' || locating !== null) throw new Error('对象时点已变化，请返回实际对象后重开草稿');
        if (!(await loader.access(item.ref)).readable) throw new Error('对象已不可读');
        if (data !== original || selected !== item || locating !== null) throw new Error('正在定位其他对象，请等待定位完成');
        const object = semanticObject(data, item); await sdk.focus(object);
        if (data !== original || selected !== item || simulation || locating !== null) throw new Error('对象版本已变化，请重新打开实际对象');
        const receipt = await sdk.feedback({ ref: object.ref, subject: item.label, anchor: item.localId || item.data?.path || '',
          scene: { region: 'architecture', mode: 'current', map_sha256: data.mapHash, ...(scene ? { simulation: scene } : {}) }, text: input.value.trim() });
        result.textContent = receipt?.cancelled ? '已取消，草稿保留。' : '回执已交原生对话。回执不等于正式答复，请在对话区查看后续。';
      } catch (error) { result.textContent = error.code === 'projection.cancelled' ? '已取消，草稿保留。' : error.message + '；草稿保留，结果不明时请先核实，不自动重发。'; }
      finally { submit.disabled = false; }
    });
    detail.append(form); input.focus();
  }
  async function toggleAudit(enabled) {
    auditOn = false; if (!enabled) { render(); return; }
    const target = data, token = ++detailRevision, report = await loader.audit(data);
    if (disposed || target !== data || token !== detailRevision) return;
    if (!report) { notify('尚无此版本核对报告，目标提交和全部输入 hash 必须匹配。'); render(); return; }
    auditOn = true; render(); detail.replaceChildren(el('h2', '核对偏离'), el('p', badge(), 'arch-badge'));
    p(detail, '可读偏离 ' + report.findings.length + '；脚本执行成功不代表架构一致性通过。');
    p(detail, '报告 ' + report.id + '\n目标 ' + report.commit + '\n报告 SHA256 ' + report.report.sha256 + '\n输入 ' + JSON.stringify(report.inputs?.files), 'arch-provenance');
    sourceLink(detail, report.report.address, '固定报告与版本依据'); const list = el('ul');
    for (const finding of report.findings) {
      const li = el('li', finding.code + ' · ' + (finding.path || finding.subject || finding.module || ''));
      if (finding.module) button('定位 ' + finding.module, () => locate(nodeFor('module', finding.module)), li);
      list.append(li);
    }
    detail.append(list); openDetail();
  }
  async function reload(refreshManifest = false) {
    return navigate(async token => {
      detailRevision++; fileRevision++; notify('正在重新核实可读事实…');
      const nextEvidence = refreshManifest ? await loader.manifest() : evidence;
      if (await context.architectureEntry?.acceptEvidence(nextEvidence)) return;
      const next = await loader.current(nextEvidence);
      const relations = await prepareRelations(next);
      if (!guard.valid(token) || disposed) return;
      saveScope(); stopPlayer(); mapBeforeProtocol = null; selected = null; analyzing = false; auditOn = false;
      const root = existingRoot(next);
      evidence = nextEvidence; data = next; setRelations(next, relations); view.files = []; fileCursor = null;
      if (runtime(next)) view.scopeRoot = root;
      searchResults.replaceChildren(); searchPanel.hidden = true;
      void sdk.cancelFeedback?.().catch(() => {});
      if (!data.modules.some(m => m.id === view.branch)) view.branch = null;
      if (!view.branch) view.level = 0; view.showPlans = view.showPlans && data.evolutionAvailable;
      inspector.querySelector('.arch-source')?.remove(); detail.hidden = false;
      detail.replaceChildren(); closeDetail();
      if (runtime(data)) applyScope(navigation.restore(data, view.scopeRoot)); else render();
      updateAvailable = false; clearNotice();
      if (view.level >= 2) await loadFiles(); await subscribe();
    });
  }
  async function switchCurrent(object, publish) {
    if (!object) return;
    if (publish) {
      if (pendingFocus === object) pendingFocus = null;
      return navigate(token => readCurrentSelection(object, true, token));
    }
    const token = guard.start(); focusRead = { object, token };
    try { await readCurrentSelection(object, false, token); }
    catch (error) { if (guard.valid(token)) fail(error); }
    finally {
      if (focusRead?.token === token) focusRead = null;
      if (guard.valid(token) && pendingFocus === object) pendingFocus = null;
    }
  }
  async function readCurrentSelection(object, publish, token) {
    const type = object.kind?.split(':')[0], id = object.kind?.split(':').slice(1).join(':');
    // A progress change is a separate semantic identity, not a module ID.
    // Its following explicit module navigation must not queue behind a full map read.
    if (!['module', 'direction', 'design', 'file', 'protocol', 'plan', 'instruction'].includes(type)) {
      endLocating();
      if (simulation) { notify('此对象没有架构映射，保留原视图。请从来源区定位关联模块。'); return; }
      // An unrelated focus has no authority to replace the local architecture
      // selection. Keep its detail, breadcrumb and camera together until an
      // explicit, readable architecture object arrives.
      notify('收到的对象没有架构映射；保留当前架构选择与视图。请从来源区使用“在架构中查看”定位关联模块。');
      return;
    }
    beginLocating(object, token);
    try {
      const original = data;
      const sourceVersion = sourceRevision;
      const reusable = !publish && original?.mode === 'current' ? original : null;
      // The graph exposes module Design counts and Plan relations beyond the
      // selected node. Revalidate that entire authorized snapshot before reuse,
      // sharing this in-flight check with show(), never rendering before it ends.
      if (reusable) {
        await revalidatePermissions(true, { allowHidden: true });
        if (!guard.valid(token) || disposed) return;
      }
      const nextEvidence = simulation ? await loader.manifest() : evidence;
      if (await context.architectureEntry?.acceptEvidence(nextEvidence)) return;
      const next = await loader.current(nextEvidence, { reuse: reusable, canReuse: () => sourceVersion === sourceRevision });
      const relations = await prepareRelations(next);
      if (!guard.valid(token) || disposed) return;
      const branch = type === 'module' ? id : null;
      if (branch && !next.modules.some(m => m.id === branch)) { notify('当前同 ID 模块不存在或不可读；保留原视图。'); return; }
      const readable = !object.ref || (await loader.access(object.ref)).readable;
      if (!guard.valid(token) || disposed) return;
      if (!readable) { notify('当前对象不可读，保留原视图。'); return; }
      // Resolve before changing the historical state; failure must leave its camera intact.
      const old = data;
      let candidate = type === 'direction' ? directionSelection(next, object) : nodeFor(type, id || object.ref, next);
      let incomingFile;
      if (type === 'file' && object.ref?.startsWith(REPOSITORY + '/file/')) {
        const path = object.ref.slice((REPOSITORY + '/file/').length).replace(/#L\d+(?:-L\d+)?$/, '');
        const matches = await loader.files(next, { query: path });
        if (!guard.valid(token) || disposed) return;
        incomingFile = matches.items.find(f => f.path === path) || { path, module_matches: [], blob_sha: null };
        candidate = { id: key('file', path), type: 'file', label: path.split('/').pop(), subtitle: path, ref: object.ref, source: next.indexRef || MAP_REF, data: incomingFile };
      }
      if (!candidate && type === 'design' && next.designMeta[object.ref]) candidate = {
        id: key('design', object.ref), type: 'design', label: next.designMeta[object.ref].title, ref: object.ref, source: MAP_REF, data: next.designMeta[object.ref] };
      saveScope(); stopPlayer(); mapBeforeProtocol = null; evidence = nextEvidence; data = next; setRelations(next, relations); view.files = []; view.query = ''; search.value = ''; auditOn = false;
      if (branch) { view.branch = branch; view.level = 1; }
      else if (type === 'direction') { view.branch = null; view.level = 0; }
      else if (type === 'design') { view.branch = next.modules.find(m => m.designs.includes(object.ref))?.id; view.level = 1; }
      else if (incomingFile) { view.branch = incomingFile.module_matches[0]?.module_id || null; view.level = 2; view.files = [incomingFile]; }
      if (runtime(next)) {
        const owner = type === 'module' ? candidate?.data : type === 'design' ? next.modules.find(m => m.designs.includes(object.ref)) : null;
        const scopeRoot = type === 'direction' ? null : owner ? owner.parent || null : existingRoot(next, old);
        const saved = navigation.restore(next, scopeRoot); view.scopeRoot = scopeRoot;
        if (saved.camera) graph.setCamera(saved.camera);
      }
      selected = candidate; detail.hidden = false; inspector.querySelector('.arch-source')?.remove();
      if (pendingFocus === object) pendingFocus = null;
      endLocating(token); clearNotice(); render();
      if (selected) await choose(selected, publish);
      else { detail.replaceChildren(el('p', '当前对象未映射到架构；关联未知。')); openDetail(); }
      await subscribe();
      showUpdateNotice();
    } finally { endLocating(token); if (refreshAgain) queueMicrotask(refreshCurrentQuietly); }
  }
  async function receiveFocus(object, metadata = {}) {
    if (!object || disposed || !['current', 'proposed'].includes(object.status) || !guard.receive(metadata.revision)) return;
    pendingFocus = object;
    await applyPendingFocus();
  }
  function revalidatePermissions(force = false, { allowHidden = false } = {}) {
    if (!data || (!allowHidden && hidden) || disposed) return;
    if (!force && permissionRead?.target === data) return permissionRead.promise;
    const target = data, token = ++permissionRevision;
    const promise = checkPermissions(target, token).finally(() => { if (permissionRead?.promise === promise) permissionRead = null; });
    permissionRead = { target, promise }; return promise;
  }
  async function checkPermissions(target, token) {
    // Visibility checks must not invalidate an accepted semantic selection.
    // Their result applies only to the same data instance and newest check.
    const refs = [REPOSITORY, target.mapRef, ...(target.evolutionRef ? [target.evolutionRef] : []),
      ...(target.fileIndex && target.indexRef ? [target.indexRef] : []), ...visibleSourceRefs,
      ...Object.keys(target.designMeta), ...target.plans.map(m => m.id),
      ...view.files.map(f => REPOSITORY + '/file/' + f.path),
      ...(selected?.ref ? [selected.ref] : [])];
    const relationsPromise = prepareRelations(target);
    const checks = await limitedMap([...new Set(refs)], async ref => {
      if (token !== permissionRevision || disposed || data !== target) return null;
      return [ref, (await loader.access(ref)).readable];
    });
    if (token !== permissionRevision || disposed || data !== target) return;
    const relations = await relationsPromise;
    if (token !== permissionRevision || disposed || data !== target) return;
    setRelations(target, relations);
    const denied = new Set(checks.filter(Boolean).filter(([, allowed]) => !allowed).map(([ref]) => ref));
    if (!denied.size) {
      if (selected?.type === 'relation') {
        const fresh = relationView().componentRelations.find(r => 'relation:' + r.id === selected.id);
        if (!fresh) { selected = null; detail.replaceChildren(); closeDetail(); }
        else selected = { ...selected, data: fresh };
      }
      render(); return;
    }
    // Revocation does invalidate reads started under the previous permission
    // observation; show never lets such a late result restore removed objects.
    guard.start(); endLocating(); pendingFocus = null; clearNotice();
    void sdk.cancelFeedback?.().catch(() => {});
    stopPlayer(); detailRevision++; inspector.querySelector('.arch-source')?.remove(); detail.hidden = false;
    searchResults.replaceChildren(); searchPanel.hidden = true;
    selected = null; analyzing = false; auditOn = false;
    if (denied.has(REPOSITORY) || denied.has(target.mapRef)) {
      data = null; relationState = null; navigation.clear(); mapBeforeProtocol = null; graph.render({ nodes: [], edges: [] }); tree.replaceChildren(); detail.replaceChildren();
      breadcrumb.replaceChildren(); view.files = [];
      stateLabel.hidden = false; stateLabel.textContent = '来源不可读'; notify('当前权限不再允许读取此图。', initialize); return;
    }
    for (const ref of denied) delete data.designMeta[ref];
    data.modules = data.modules.map(m => ({ ...m, designs: m.designs.filter(ref => !denied.has(ref)), related_designs: (m.related_designs || []).filter(ref => !denied.has(ref)) }));
    data.protocols = data.protocols.map(m => ({ ...m, designs: m.designs.filter(ref => !denied.has(ref)), related_designs: (m.related_designs || []).filter(ref => !denied.has(ref)) }));
    data.plans = data.plans.filter(m => !denied.has(m.id));
    view.files = view.files.filter(f => !denied.has(REPOSITORY + '/file/' + f.path));
    if (denied.has(target.evolutionRef)) { data.plans = []; data.directions = []; data.evolutionAvailable = false; view.showPlans = false; }
    if (denied.has(target.indexRef)) { data.fileIndex = null; view.files = []; }
    detail.replaceChildren(el('p', '权限已变化；不可读对象及其关联已移除。'));
    if (runtime(data)) { view.scopeRoot = existingRoot(data); navigation.restore(data, view.scopeRoot); }
    render();
  }
  async function refreshCurrentQuietly() {
    if (refreshInFlight || hidden || disposed || !data || data.mode !== 'current' || simulation || locating !== null || transition !== null) return;
    const target = data, selection = selected, token = guard.generation, version = sourceRevision;
    refreshAgain = false;
    refreshInFlight = (async () => {
      const nextEvidence = await loader.manifest();
      if (disposed || hidden || data !== target || !guard.valid(token) || simulation || locating !== null || transition !== null || version !== sourceRevision) return;
      if (await context.architectureEntry?.acceptEvidence(nextEvidence)) return;
      const next = await loader.current(nextEvidence);
      const relations = await prepareRelations(next);
      if (disposed || hidden || data !== target || !guard.valid(token) || simulation || locating !== null || transition !== null || version !== sourceRevision) { refreshAgain = true; return; }
      const changed = next.mapHash !== target.mapHash || next.evolutionHash !== target.evolutionHash || batchIdentity(nextEvidence) !== batchIdentity(evidence);
      if (!changed) { updateAvailable = false; return; }
      if (changed) {
        await sdk.cancelFeedback?.();
        if (disposed || hidden || data !== target || !guard.valid(token) || simulation || locating !== null || transition !== null || version !== sourceRevision) { refreshAgain = true; return; }
        detailRevision++; fileRevision++;
      }
      searchResults.replaceChildren(); searchPanel.hidden = true;
      const scopeRoot = existingRoot(next); saveScope();
      evidence = nextEvidence; data = next; setRelations(next, relations); view.scopeRoot = scopeRoot; view.files = []; auditOn = false;
      view.expanded = view.expanded.filter(id => next.modules.some(m => m.id === id));
      if (!next.modules.some(m => m.id === view.branch)) view.branch = null;
      if (selection?.type === 'direction') selected = directionSelection(next, semanticObject(target, selection));
      else selected = selection ? nodeFor(selection.type, selection.localId || selection.ref, next) : null;
      updateAvailable = false; render(); await subscribe();
      if (selected && changed) { inspector.querySelector('.arch-source')?.remove(); detail.hidden = false; showDetails(selected); }
      else if (!selected) { detail.replaceChildren(); closeDetail(); }
    })().catch(error => { if (data === target && guard.valid(token)) { updateAvailable = true; fail(new Error('新事实批次未通过核验，保留先前批次（已陈旧）：' + error.message)); } }).finally(() => {
      refreshInFlight = null;
      if (refreshAgain && !disposed && !hidden && data?.mode === 'current' && !simulation && locating === null && transition === null) queueMicrotask(refreshCurrentQuietly);
    });
    return refreshInFlight;
  }
  async function subscribe() {
    unsubscribe?.(); unsubscribe = null; if (hidden || disposed || !context.watch) return;
    const supplement = relationState?.target === data ? relationState.result : null;
    const dependencyRefs = (supplement?.relations || []).flatMap(relation => [
      ...(relation.source_basis || []).flatMap(source => [source.design_id, source.body?.address]),
      ...(relation.codeEvidence || []).map(code => code.content_address || REPOSITORY + '/commit/' + code.commit + '/file/' + code.path),
    ]);
    const watched = [...new Set([REPOSITORY, MAP_REF, EVOLUTION_REF, MANIFEST_REF, ...batchRefs(data?.manifest),
      ...visibleSourceRefs, supplement?.pinned?.artifact?.address, ...dependencyRefs].filter(Boolean))];
    unsubscribe = await context.watch(watched, async () => {
      if (hidden || disposed) return; updateAvailable = true; sourceRevision++;
      void sdk.cancelFeedback?.().catch(() => {});
      refreshAgain = true;
      try { await revalidatePermissions(true); await refreshCurrentQuietly(); showUpdateNotice(); } catch (error) { fail(error); }
    });
  }
  async function initialize() {
    evidence = initial.evidence || await loader.manifest(); await reload();
  }
  try {
    worker = await sdk.createWorker('architecture-layout.worker.js');
    if (signal?.aborted) { worker.terminate(); root.ownerDocument.removeEventListener('keydown', keyboard); return { destroy() {} }; }
    context.onDispose?.(() => worker.terminate());
  }
  catch { /* Deterministic synchronous fallback for small views. */ }
  graph = createGraph(mapRoot, { worker, onSelect: item => choose(item).catch(fail),
    onEnter: item => (item.type === 'module' ? enter(item.localId) : choose(item)).catch(fail),
    onHome: () => enter(null).catch(fail),
    onEscape: () => { if (simulation) returnMap(); else enter(data?.modules.find(m => m.id === (runtime(data) ? view.scopeRoot : view.branch))?.parent || null).catch(fail); },
    onZoom: scale => {
      if (!data || !view.branch || simulation || locating !== null) return;
      if (data.modelVersion === 2 || data.model_version === 2) {
        return; // Zoom never silently changes the semantic root.
      }
      const next = view.level === 2 ? (scale < 0.9 ? 1 : 2) : view.level === 1 ? (scale > 1.7 ? 2 : scale < 0.4 ? 0 : 1) : (scale > 0.65 ? 1 : 0);
      if (next !== view.level) setLevel(next).catch(fail);
    } });
  context.onFocus?.((object, metadata) => receiveFocus(object, metadata).catch(fail));
  try { await initialize(); } catch (error) { fail(error); }
  return {
    notify: text => notify(text),
    getReadingState: () => ({ ready: !!data, mode: data?.mode, simulation }),
    hide() { hidden = true; floating.hide(); player?.hide(); graph.hide(); unsubscribe?.(); unsubscribe = null; },
    async show() { if (disposed) return; hidden = false; floating.show(); graph.show(); await revalidatePermissions(); if (disposed || hidden) return; await applyPendingFocus(); player?.show(); await subscribe(); await refreshCurrentQuietly(); showUpdateNotice(); },
    destroy() { if (disposed) return; disposed = true; guard.start(); detailRevision++; fileRevision++; unsubscribe?.(); root.ownerDocument.removeEventListener('keydown', keyboard); stopPlayer(); floating.destroy(); navigation.clear(); graph.destroy(); worker?.terminate(); root.replaceChildren(); },
  };
}
