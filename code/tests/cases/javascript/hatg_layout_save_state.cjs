const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { babelParse } = require('./babel_parse.cjs');
const { findHatgFunction, hatgSources, overrideHatgFunctions, startHatgRuntime } = require('./hatg_modules.cjs');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const plain = value => JSON.parse(JSON.stringify(value));

// The real runtime with its parts, unmounted. Only painting unrelated panels
// and graph DOM is replaced; layout, projection, save queuing, HTTP payload
// construction, rollback and button writes stay real.
function runtimeSources(mutation) {
  const sources = hatgSources();
  // Keep these orchestration calls from their actual AST, so removing the
  // production control/surface call fails here instead of being healed by a
  // fixture that invented the same call.
  function renderCall(owner, callee) {
    const { file, node } = findHatgFunction(sources, owner);
    const statements = node.body.body.filter(item =>
      item.type === 'ExpressionStatement' && item.expression.type === 'CallExpression'
      && item.expression.callee.type === 'Identifier' && item.expression.callee.name === callee);
    assert.equal(statements.length, 1, `${owner} must dispatch ${callee}`);
    return sources[file].slice(statements[0].start, statements[0].end);
  }
  const overrides = {
    renderGraph: '() => window.__paint(graphVisibleModel())',
    renderControls: `() => { ${renderCall('renderControls', 'renderAutoLayoutControl')} }`,
    renderLineageSurface: `() => {
      ${renderCall('renderLineageSurface', 'renderGraph')}
      ${renderCall('renderLineageSurface', 'renderControls')}
    }`,
  };
  // A negative control edits the script that owns the arrange action or the
  // Lineage commit.
  const auto = findHatgFunction(sources, 'autoLayoutGraph');
  const attempt = auto.node.body.body.find(node => node.type === 'TryStatement');
  assert(attempt, 'Production layout save must have rollback/finally');
  const commit = findHatgFunction(sources, 'commitLineage');
  if (mutation) {
    const assignmentTo = (node, field) => node.type === 'ExpressionStatement'
      && node.expression.type === 'AssignmentExpression'
      && node.expression.left.type === 'MemberExpression'
      && node.expression.left.object.name === 'state'
      && node.expression.left.property.name === field;
    const callTo = (node, callee) => node.type === 'ExpressionStatement'
      && node.expression.type === 'CallExpression'
      && node.expression.callee.name === callee;
    const candidates = {
      // The rollback commits the saved Lineage back; the commit redraws it.
      'drop-position-rollback': attempt.handler.body.body.filter(node => callTo(node, 'commitLineage')),
      'drop-density-rollback': attempt.handler.body.body.filter(node =>
        node.type === 'IfStatement' && node.test.type === 'Identifier' && node.test.name === 'previousDensity'),
      'drop-busy-reset': attempt.finalizer.body.filter(node => assignmentTo(node, 'autoLayoutInFlight')),
      'drop-rollback-paint': commit.node.body.body.filter(node => callTo(node, 'renderCommittedLineage')),
    };
    assert.equal(candidates[mutation]?.length, 1, `Mutation must address exactly one AST statement: ${mutation}`);
    const [changed] = candidates[mutation];
    const file = mutation === 'drop-rollback-paint' ? commit.file : auto.file;
    sources[file] = sources[file].slice(0, changed.start) + ';' + sources[file].slice(changed.end);
  }
  return overrideHatgFunctions(sources, overrides);
}

function fixture(mutation) {
  const attributes = {}, classes = new Set(), label = { textContent: '' };
  const button = {
    disabled: false, dataset: {},
    setAttribute: (name, value) => { attributes[name] = value; },
    querySelector: selector => { assert.equal(selector, 'span'); return label; },
    classList: { toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); } },
  };
  const sync = { dataset: {}, textContent: '' };
  const canvas = { getBoundingClientRect: () => ({ width: 1160, height: 720 }) };
  const elements = new Map([['autoLayoutBtn', button], ['hatgCanvas', canvas], ['graphSyncStatus', sync]]);
  const document = {
    querySelector: selector => elements.get(selector.slice(1)) || null,
    getElementById: id => elements.get(id) || null,
  };
  const requests = [], toasts = [], paints = [], fits = [];
  const context = vm.createContext({ document, CSS: { escape: value => value },
    localStorage: { getItem: () => null }, window: {
      PortalI18n: {
        t: value => value, definition: value => value,
        text: (strings, ...values) => strings.reduce((result, part, i) => result + part + (values[i] ?? ''), ''),
      },
      PortalUI: { toast: (message, kind) => toasts.push({ message, kind }) },
      PortalAPI: {
        get: () => { throw new Error('Unexpected network read'); },
        post: () => { throw new Error('Unexpected page write'); },
      },
      PortalInstructionClient: {
        execute: (instruction, args) => new Promise((resolve, reject) => requests.push({ instruction, body: plain(args), resolve, reject })),
      },
      PortalUIPrimitives: { randomUUID: () => 'layout-key' },
      // The canvas search's server client; arranging never searches.
      PortalResidentDiscoveryService: { createClient: () => ({}) },
      PortalLineageWorkbenchController: {},
      requestAnimationFrame: callback => callback(),
      __paint: model => paints.push(plain(model.nodes.map(({ id, x, y }) => ({ id, x, y })))),
    },
  });
  // Browser globals share one object; geometry exports through globalThis.
  Object.assign(context, context.window);
  context.window = context;
  for (const file of [
    'portal_state.js', 'hatg_panel_features.js', 'hatg_panel_state.js',
    'projection_libraries/portal-graph/1.0.0/geometry.js', 'lineage_graph.js',
    'lineage_program.js', 'portal_society_service.js',
  ]) {
    const source = fs.readFileSync(path.join(staticRoot, file), 'utf8');
    const program = file === 'portal_state.js' ? babelParse(source, file).program.body[0] : null;
    vm.runInContext(program ? source.slice(program.start, program.end) : source, context, { filename: file });
  }
  const { get, state } = startHatgRuntime(context, runtimeSources(mutation));
  const controller = Object.fromEntries([
    'autoLayoutGraph', 'activateLineageEdits', 'renderAutoLayoutControl', 'renderGraph',
    'graphVisibleModel', 'autoLayoutScope',
  ].map(name => [name, get(name)]));
  controller.state = state;
  controller.state.lineage = {
    lineage_id: 'lineage_aaaaaaaaaaaa', revision: 7, layout_revision: 0,
    nodes: Array.from({ length: 9 }, (_, index) => ({
      id: `resident_${String(index + 1).repeat(12)}`, kind: 'resident',
      title: `工作节点 ${index + 1}`, status: 'idle', x: 15 + index * 7, y: 10 + index * 8,
    })),
    edges: [], function_blocks: [], program: { components: [], wires: [] },
  };
  controller.state.graphController = { fit: options => fits.push(plain(options)) };
  controller.activateLineageEdits(controller.state.lineage.lineage_id);
  controller.renderAutoLayoutControl();
  controller.renderGraph();
  return { ...controller, button, attributes, classes, label, sync, requests, toasts, paints, fits };
}

const positions = f => plain(f.state.lineage.nodes.map(({ id, x, y }) => ({ id, x, y })));
const payloadPositions = f => positions(f).map(({ id, x, y }) => ({ object_id: id, x, y }));
const nextTurn = () => new Promise(resolve => setImmediate(resolve));

function assertButton(f, text, density, busy = false) {
  assert.equal(f.label.textContent, text, 'next layout action');
  assert.equal(f.button.dataset.layoutDensity, density, 'button density');
  assert.equal(f.attributes['aria-pressed'], density === 'compact' ? 'true' : 'false', 'aria-pressed');
  assert.equal(f.attributes['aria-busy'], String(busy), 'aria-busy');
  assert.equal(f.button.disabled, busy, 'button disabled only while saving');
  assert.equal(f.classes.has('busy'), busy, 'busy class');
}

async function scenario(mutation) {
  const f = fixture(mutation);
  const scope = f.autoLayoutScope();
  const initial = positions(f);
  assertButton(f, '整理', '');

  const first = f.autoLayoutGraph();
  assertButton(f, '整理中…', 'comfortable', true);
  await nextTurn();
  assert.equal(f.requests.length, 1, 'first save dispatched once');
  assert.equal(f.requests[0].instruction, 'UpdateLineageLayout');
  assert.deepEqual(f.requests[0].body, { lineage_id: 'lineage_aaaaaaaaaaaa', positions: payloadPositions(f) });
  assert.notDeepEqual(positions(f), initial, 'real layout moved the original fixture');
  await f.autoLayoutGraph();
  assert.equal(f.requests.length, 1, 'busy guard prevents duplicate saves');
  f.requests[0].resolve({ lineage_id: 'lineage_aaaaaaaaaaaa', layout_revision: 1 });
  await first;
  assert.equal(f.state.autoLayoutInFlight, false, 'busy reset after first save');
  assertButton(f, '紧凑整理', 'comfortable');
  assert.equal(f.state.lineage.layout_revision, 1);
  assert.equal(f.state.autoLayoutDensityByScope.get(scope), 'comfortable');
  const before = positions(f);
  assert.deepEqual(f.paints.at(-1), before, 'normal projection reaches paint boundary');

  const failed = f.autoLayoutGraph();
  await nextTurn();
  assert.equal(f.requests.length, 2, 'compact attempt dispatched once');
  const attemptedCompact = positions(f);
  assert.notDeepEqual(attemptedCompact, before, 'compact attempt changes actual positions');
  assert.deepEqual(f.paints.at(-1), attemptedCompact, 'optimistic projection reaches paint boundary');
  assertButton(f, '整理中…', 'compact', true);
  assert.deepEqual(f.requests[1].body, { lineage_id: 'lineage_aaaaaaaaaaaa', positions: payloadPositions(f) });
  f.requests[1].reject(new Error('layout unavailable'));
  await failed;
  assert.deepEqual(positions(f), before, 'rollback restores every original position');
  assert.equal(f.state.autoLayoutDensityByScope.get(scope), 'comfortable', 'rollback restores density');
  assert.equal(f.state.autoLayoutInFlight, false, 'busy reset after rejected save');
  assert.equal(f.state.layoutSaveInFlight, null, 'save queue releases rejected promise');
  assert.equal(f.state.lineage.layout_revision, 1, 'failed attempt does not advance revision');
  assert.deepEqual(f.paints.at(-1), before, 'rollback dispatches restored projection to paint');
  assertButton(f, '紧凑整理', 'comfortable');
  assert.equal(f.sync.dataset.state, 'error');
  assert.ok(f.toasts.some(({ message, kind }) => kind === 'error'
    && message === '自动整理失败：layout unavailable'), 'original automatic-layout error toast');

  const retry = f.autoLayoutGraph();
  await nextTurn();
  assert.equal(f.requests.length, 3, 'one first save, one rejection, one retry');
  assert.deepEqual(f.requests[2].body, f.requests[1].body, 'retry targets the same compact arrangement');
  f.requests[2].resolve({ lineage_id: 'lineage_aaaaaaaaaaaa', layout_revision: 2 });
  await retry;
  assert.equal(f.state.autoLayoutInFlight, false);
  assert.equal(f.state.layoutSaveInFlight, null);
  assert.equal(f.state.layoutSaveQueue.size, 0);
  assert.equal(f.state.lineage.layout_revision, 2);
  assert.equal(f.state.autoLayoutDensityByScope.get(scope), 'compact');
  assertButton(f, '舒展整理', 'compact');
  assert.deepEqual(positions(f), attemptedCompact, 'retry applies compact coordinates');
  assert.deepEqual(f.paints.at(-1), attemptedCompact, 'retry paints compact projection');
  assert.equal(f.sync.dataset.state, 'saved');
  assert.ok(f.toasts.some(({ message, kind }) => kind === 'success'
    && message === 'HATG 已紧凑整理，节点位置已保存。'));
}

test('failed compact save restores positions and the next action, then can retry', () => scenario());

// Controlled negative copies exist only inside the VM. Each must be rejected
// by a specific original assertion; an unrelated ReferenceError cannot pass.
for (const [mutation, message] of [
  ['drop-position-rollback', 'rollback restores every original position'],
  ['drop-density-rollback', 'rollback restores density'],
  ['drop-busy-reset', 'busy reset after first save'],
  ['drop-rollback-paint', 'rollback dispatches restored projection to paint'],
]) test(`negative control: ${mutation}`, async () => {
  await assert.rejects(scenario(mutation), error => error.code === 'ERR_ASSERTION'
    && error.message.includes(message));
});
