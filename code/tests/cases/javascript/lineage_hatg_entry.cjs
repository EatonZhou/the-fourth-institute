const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {readHatgModule, createHatgModule} = require('./hatg_modules.cjs');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const source = fs.readFileSync(path.join(staticRoot, 'lineage_workbench.js'), 'utf8');
const hatgSource = fs.readFileSync(path.join(staticRoot, 'hatg_panel.js'), 'utf8');
function owner(text, name) {
  const match = new RegExp(`^( +)(?:async )?function ${name}\\(`, 'm').exec(text);
  assert(match, `Missing production function ${name}`);
  const end = text.indexOf(`\n${match[1]}}\n`, match.index);
  assert(end > match.index, `Missing end of ${name}`);
  return text.slice(match.index, end + match[1].length + 3);
}
const noop = () => {};
const lineageId = 'lineage_8794980b1bb7';
const componentId = 'effect_54f65c501b86';
const familyId = 'fbi_4215c7259082';

function entry(query, {collapsed = false, hidden = true, delayed = false} = {}) {
  const calls = [], targets = [], reveals = [];
  const layout = {hidden: new Set(hidden ? ['hatg'] : []), collapsed: new Set(collapsed ? ['hatg'] : []), active: 'chat'};
  const graphState = {lineage: {lineage_id: lineageId}, selectedProgramObjectId: ''};
  // Selecting the program object is the production canvas selection module's.
  const selectionContext = vm.createContext({});
  selectionContext.window = selectionContext;
  vm.runInContext(readHatgModule('hatg_canvas_selection.js'), selectionContext);
  const graph = createHatgModule(selectionContext.PortalHatgCanvasSelection, 'createCanvasSelection', {
    state: graphState,
    displayProgramComponentById: id => id === componentId ? {id} : null,
    displayProgramWireById: () => null,
    functionBlockInstanceById: id => id === familyId ? {id, family: {}} : null,
    programFeature: {clearSelection: () => false}, publishLocation: noop,
    renderGraphSelection: () => true, renderGraph: noop,
    renderControls: noop, renderProgramInspector: noop,
    revealSelectedGraphNode: async id => reveals.push(id),
  });
  let release;
  const ready = delayed ? new Promise(resolve => { release = resolve; }) : Promise.resolve();
  const panel = {
    setTarget: target => {
      targets.push({...target}); calls.push('target');
      return ready.then(async () => {
        if (target.programItemId) await graph.selectProgramObject(target.programItemId);
      });
    },
    snapshot: () => ({programItemId: graphState.selectedProgramObjectId}),
  };
  const workbench = {
    getPanel: id => id === 'hatg' ? panel : null,
    applyWorkspace: workspace => {
      calls.push('workspace');
      layout.workspace = JSON.parse(JSON.stringify(workspace));
      layout.hidden = new Set(workspace.hidden);
      layout.collapsed.clear();
    },
    focusPanel: id => {
      calls.push('focus'); layout.hidden.delete(id); layout.collapsed.delete(id); layout.active = id;
    },
  };
  const context = vm.createContext({
    URLSearchParams,
    window: {
      location: {search: query}, PortalProjections: {lineage: noop},
      PortalI18n: {t: value => value},
      PortalWorkbench: {model: {
        split: (orientation, children, weights) => ({orientation, children, weights}),
        stack: panels => ({panels}),
      }},
    },
    lineageWorkbench: workbench, lineageEditorTypes: [],
    state: {chatTarget: {lineage_id: 'lineage_old'}, lineageDeleteSaving: ''},
    selectedResidentId: () => 'resident_old',
  });
  const names = ['initialResidentIdFromUrl', 'initialLineageIdFromUrl', 'initialChatroomIdFromUrl',
    'initialProgramItemIdFromUrl', 'initialOpenChatFromUrl', 'syncLineageHatgPanel',
    'lineageHatgWorkspace', 'applyOpeningHatgNavigation'];
  vm.runInContext(names.map(name => owner(source, name)).join('\n'), context);
  const capture = /const openingNavigation = \{[\s\S]*?\n      \};\n      let pendingProgramItemId = openingNavigation.programItemId;\n      let openingHatgNavigationApplied = false;/.exec(source);
  assert(capture, 'Capture the production opening URL before restored panels publish locations');
  vm.runInContext(capture[0], context);
  return {context, calls, targets, reveals, layout, graphState, release,
    open: () => context.applyOpeningHatgNavigation(),
    pending: () => vm.runInContext('pendingProgramItemId', context)};
}

test('explicit canvas entry replaces a hidden restored layout with the existing canvas preset', async () => {
  const page = entry(`?lineage=${lineageId}&view=hatg`);
  await page.open();
  assert.equal(page.layout.active, 'hatg');
  assert.equal(page.layout.hidden.has('hatg'), false);
  assert.deepEqual([...page.layout.hidden], ['navigation', 'preview']);
  assert.deepEqual(page.layout.workspace.layout.weights, [520, 900]);
  assert.deepEqual(page.layout.workspace.layout.children.map(stack => stack.panels), [['chat'], ['hatg']]);
  assert.deepEqual(page.calls, ['target', 'workspace', 'focus']);
});

test('explicit canvas entry expands a restored collapsed panel', async () => {
  const page = entry(`?lineage=${lineageId}&view=hatg`, {hidden: false, collapsed: true});
  await page.open();
  assert.equal(page.layout.collapsed.has('hatg'), false);
  assert.equal(page.layout.active, 'hatg');
});

for (const objectId of [componentId, familyId]) {
  test(`canvas URL selects and reveals ${objectId} after asynchronous graph loading`, async () => {
    const page = entry(`?lineage=${lineageId}&view=hatg&program_item=${objectId}`, {delayed: true});
    // A restored panel can publish its old location during construction.
    page.context.window.location.search = '?lineage=lineage_restored&program_item=effect_old';
    const opened = page.open();
    assert.equal(page.layout.active, 'hatg');
    assert.equal(page.pending(), objectId);
    assert.equal(page.targets[0].lineageId, lineageId);
    assert.equal(page.targets[0].programItemId, objectId);
    page.release();
    await opened;
    assert.equal(page.graphState.selectedProgramObjectId, objectId);
    assert.deepEqual(page.reveals, [objectId]);
    assert.equal(page.pending(), '');
  });
}

for (const query of [
  `?lineage=${lineageId}`,
  `?lineage=${lineageId}&resident=resident_6199a7a36480&open_chat=1`,
  `?lineage=${lineageId}&program_item=${familyId}`,
  `?lineage=${lineageId}&view=chat`,
  `?view=hatg&program_item=${familyId}`,
]) {
  test(`non-canvas entry preserves restored chat and hidden HATG: ${query}`, async () => {
    const page = entry(query);
    await page.open();
    assert.deepEqual(page.calls, query.includes('lineage=') ? ['target'] : []);
    if (query.includes('lineage=')) assert.equal(page.targets[0].lineageId, lineageId);
    assert.equal(page.layout.active, 'chat');
    assert.equal(page.layout.hidden.has('hatg'), true);
  });
}

test('opening intent is applied once after identity/layout restore, with the menu using the same preset', () => {
  const load = owner(source, 'loadAll');
  const restore = load.indexOf('applyLineageUiState(');
  const reveal = load.indexOf('applyOpeningHatgNavigation()');
  assert(load.indexOf('await window.PortalIdentity.load()') < restore);
  assert(restore >= 0 && reveal > restore);
  assert.doesNotMatch(load.slice(restore, reveal), /if \(loadSeq === 1\) \{/);
  assert.match(owner(source, 'initLineageWorkbench'), /presets: \[[\s\S]*lineageHatgWorkspace\(\)/);
});

test('unavailable Chat removes only its stale URL target and retains the explicit graph', async () => {
  const residentId = 'resident_6199a7a36480';
  const page = entry(`?lineage=${lineageId}&resident=${residentId}&open_chat=1`, {delayed: true});
  const opened = page.open();
  assert.equal(page.targets[0].lineageId, lineageId, 'graph binds before Chat settles');
  assert.equal(page.layout.active, 'chat', 'binding does not steal focus');
  const location = new URL(`https://portal.invalid/lineage?lineage=${lineageId}&resident=${residentId}&open_chat=1#canvas`);
  let saved = {lastChatTarget: {kind: 'resident', id: residentId}};
  const unavailableResidentBindings = new Set();
  const context = vm.createContext({
    URL, window: {location}, unavailableResidentBindings,
    history: {state: {}, replaceState: (_state, _title, url) => { location.href = new URL(url, location).href; }},
    readLineageUiState: () => saved,
    writeLineageUiState: patch => { saved = {...saved, ...patch}; },
  });
  vm.runInContext(owner(source, 'onLineagePanelBinding'), context);
  const workbench = {getPanel: () => ({target: () => ({resident_id: residentId})})};
  context.onLineagePanelBinding({event: 'binding-unavailable', sourcePanelId: 'chat'}, workbench);
  assert.equal(location.searchParams.get('lineage'), lineageId);
  assert.equal(location.searchParams.has('resident'), false);
  assert.equal(location.searchParams.has('open_chat'), false);
  assert.equal(location.hash, '#canvas');
  assert.equal(saved.lastChatTarget, null);
  assert.equal(unavailableResidentBindings.has(residentId), true);
  page.release();
  await opened;
  assert.equal(page.targets[0].lineageId, lineageId);

  // A late unavailable response must not clear a more recent navigation.
  location.searchParams.set('resident', 'resident_new');
  location.searchParams.set('open_chat', '1');
  context.onLineagePanelBinding({event: 'binding-unavailable', sourcePanelId: 'chat'}, workbench);
  assert.equal(location.searchParams.get('resident'), 'resident_new');
  assert.equal(location.searchParams.get('open_chat'), '1');
  context.onLineagePanelBinding({event: 'binding-ready', sourcePanelId: 'chat'}, workbench);
  assert.equal(unavailableResidentBindings.has(residentId), false);
});


test('first surviving load applies the opening graph once, later recovery preserves new navigation', async () => {
  const page = entry(`?lineage=${lineageId}&view=hatg`, {delayed: true});
  // Identity was superseded before the first Workbench existed. No opening
  // action ran; the surviving service-recovery load owns the first surface.
  page.context.state.loadAllSeq = 2;
  const opened = page.open();
  assert.equal(page.targets.length, 1);
  assert.equal(page.targets[0].lineageId, lineageId);
  await page.open();
  assert.equal(page.targets.length, 1, 'overlapping recovery does not submit the target twice');
  page.release();
  await opened;
  page.context.window.location.search = '?lineage=lineage_new&view=chat';
  page.layout.active = 'chat';
  await page.open();
  assert.equal(page.targets.length, 1, 'later recovery cannot restore the obsolete opening target');
  assert.equal(page.layout.active, 'chat');
});

test('failed primitive prerequisite reaches the retry boundary and can initialize on retry', async () => {
  const failure = new Error('primitive definitions unavailable');
  let fail = true, bound = 0;
  const statuses = [];
  const context = vm.createContext({
    window: {
      PortalLineageProgram: {loadPrimitiveDefinitions: async () => { if (fail) throw failure; }},
      PortalIdentity: {load: async () => ({username: 'test-actor'})},
      PortalI18n: {text: parts => parts.join('')},
    },
    mountTemplates: noop, setSyncStatus: value => statuses.push(value), toast: noop,
    $id: () => null, bindModals: () => { bound++; }, bindActions: noop,
    initHatgGraph: noop, renderLineageSurface: noop, hatgComponentRoot: {},
    loadLineagePageData: async () => {}, state: {}, lineageCatalogHydrated: false,
  });
  vm.runInContext(owner(hatgSource, 'init'), context);
  await assert.rejects(context.init(), error => error === failure);
  assert.equal(context.lineageCatalogHydrated, false);
  assert.equal(bound, 0, 'failed prerequisite does not partially bind the surface');
  fail = false;
  await context.init();
  assert.equal(context.lineageCatalogHydrated, true);
  assert.equal(bound, 1);
});
