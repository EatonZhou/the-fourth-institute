const fs = require("node:fs");
const assert = require("node:assert/strict");
global.window = {};
require("../../../management_portal/static/portal_text.js");
const input = JSON.parse(fs.readFileSync(0, "utf8"));

const bootstrap = `
  const state = {lineage: initial, layoutSaveQueue: new Map(), localVersion: 0, residents: [],
    runtimeStatus: new Map(), residentRuntimeById: new Map(),
    sessionUserMessageCounts: new Map(), sessionContextPressures: new Map()};
  const programFeature = {beginMutation() {}, endMutation() {}};
  const toast = (...args) => hooks.toasts.push(args);
  const setSyncStatus = (...args) => hooks.statuses.push(args);
  const showProgramMutationStatus = message => hooks.statuses.push([message, "mutation"]);
  const renderProgramInspector = () => {};
  const selectNode = async () => {};
  const flushLineageSave = async () => {throw new Error("Unexpected graph save")};
  const renderLineageSurface = () => hooks.render?.(state);
  const societyService = {
    loadLineage: id => hooks.loadLineage(id),
    updateLayout: body => apiPost("/lineage/api/layout/positions", body),
    createProgramComponent: body => apiPost("/lineage/api/program/components/create", body),
  };
  // The edit queue, layout save and commit are the production module's.
  const {lineageEditQueue, activateLineageEdits, saveLineageLayout, flushLineageLayoutSave, commitLineage} =
    window.PortalHatgLineageState.createLineageState({
      state, programFeature, toast, setSyncStatus, renderProgramInspector, renderLineageSurface, societyService,
      nodeResidentId, clone: value => JSON.parse(JSON.stringify(value)), activeSeeds: () => [],
      reconcileGraphInteractionState: () => {}, bindResidentStateCollection: () => {},
      renderGraphRuntimePresentation: () => {}, renderControls: () => {}, updateLineageSummary: () => {},
      currentTemplateController: () => ({render() {}}),
    });
  // Object separation is the layout module's; dependencies it does not get here fail.
  const placement = {state, commitLineage, saveLineageLayout, toast};
  const {separateGraphObject} = window.PortalHatgLayout.createLayout(Object.fromEntries(
    window.PortalHatgLayout.dependencies.map(name => [name, Object.hasOwn(placement, name)
      ? placement[name] : () => { throw new Error("Unexpected use of " + name); }])));
  activateLineageEdits(initial.lineage_id);
  return {state, lineageEditQueue, activateLineageEdits, saveLineageLayout, flushLineageLayoutSave,
    applyProgramMutation, createProgramComponentFromDrop,
    renderLineageSurface};
`;

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, fail) => {resolve = accept; reject = fail;});
  return {promise, resolve, reject};
}

async function nodeProbe() {
  const calls = [];
  const gate = deferred();
  const started = deferred();
  const hooks = {toasts: [], statuses: [], loadLineage: async () => {throw new Error("Unexpected refresh");}};
  const initial = {lineage_id: "lineage_111111111111", revision: 4, layout_revision: 1, nodes: [], program: {components: [], wires: []}};
  let fixture;
  let layoutCalls = 0;
  let holdLayout = true;
  const post = async (url, body) => {
    calls.push({url, body: JSON.parse(JSON.stringify(body))});
    if (url.endsWith("/positions")) {
      layoutCalls += 1;
      if (holdLayout && layoutCalls === 1) {started.resolve(); await gate.promise;}
      return {layout_revision: layoutCalls + 1};
    }
    return {lineage: {...fixture.state.lineage, revision: fixture.state.lineage.revision + 1}};
  };
  for (const name of ["hatg_lineage_state.js", "hatg_layout.js"]) {
    new Function("window", fs.readFileSync(input.root + "/management_portal/static/" + name, "utf8"))(window);
  }
  fixture = new Function("initial", "apiPost", "hooks", input.functions + bootstrap)(initial, post, hooks);
  const move = (x = 20) => fixture.saveLineageLayout([{id: "event_source_111111111111", x, y: 60}]);
  const create = () => fixture.applyProgramMutation(() => post("/create", {lineage_id: fixture.state.lineage.lineage_id, expected_revision: fixture.state.lineage.revision}), "");
  const absorb = promise => promise.then(value => ({value}), error => ({error}));
  const scenario = input.scenario;

  if (scenario === "layout_waits_for_program") {
    const mutation = fixture.applyProgramMutation(async () => {started.resolve(); await gate.promise; return {lineage: {...initial, revision: 5}};}, "");
    await started.promise;
    const layout = move();
    await Promise.resolve();
    assert.equal(calls.length, 0);
    holdLayout = false;
    gate.resolve();
    await Promise.all([mutation, layout]);
    assert.equal(calls.length, 1);
    assert.equal(fixture.state.lineage.revision, 5);
  } else {
    const layout = absorb(move());
    await started.promise;
    const mutation = absorb(create());
    const followUp = absorb(create());
    if (scenario === "failure_blocks_creation" || scenario === "newer_positions_survive_failure") {
      let extra;
      if (scenario === "newer_positions_survive_failure") extra = absorb(move(27));
      gate.reject(new Error("layout commit failed"));
      assert.match((await layout).error.message, /layout commit failed/);
      assert.match((await mutation).error.message, /layout commit failed/);
      assert.match((await followUp).error.message, /layout commit failed/);
      if (extra) assert.ok((await extra).error);
      assert.equal(calls.length, 1);
      const pending = [...fixture.state.layoutSaveQueue.values()];
      assert.equal(pending[0].x, extra ? 27 : 20);
      assert.ok((await absorb(create())).error);
      assert.equal(calls.length, 1);
      await fixture.flushLineageLayoutSave();
      await create();
      assert.equal(calls.at(-1).url, "/create");
      assert.equal(calls[1].body.positions[0].x, extra ? 27 : 20);
    } else if (scenario === "drain_positions") {
      const extra = move(29);
      gate.resolve();
      await Promise.all([layout, mutation, followUp, extra]);
      assert.deepEqual(calls.map(row => row.url), ["/lineage/api/layout/positions", "/lineage/api/layout/positions", "/create", "/create"]);
      assert.equal(calls[1].body.positions[0].x, 29);
      assert.equal(calls[3].body.expected_revision, 5);
    } else {
      fixture.activateLineageEdits(scenario === "destroy_fences_queued_intents" ? "" : "lineage_222222222222");
      if (scenario !== "destroy_fences_queued_intents") fixture.state.lineage = {...initial, lineage_id: "lineage_222222222222", revision: 12};
      let replacement;
      if (scenario === "same_id_new_visit_fences_old_intent") {
        fixture.activateLineageEdits(initial.lineage_id);
        fixture.state.lineage = {...initial, revision: 44};
        replacement = create();
      } else if (scenario === "switch_does_not_block_other_lineage") {
        await create();
        assert.equal(calls[1].body.lineage_id, "lineage_222222222222");
        assert.equal(fixture.state.lineage.revision, 13);
      }
      gate.resolve();
      assert.equal((await layout).error.code, "lineage_edit_superseded");
      assert.equal((await mutation).error.code, "lineage_edit_superseded");
      assert.equal((await followUp).error.code, "lineage_edit_superseded");
      if (replacement) {
        await replacement;
        assert.equal(calls.at(-1).body.expected_revision, 44);
      }
      assert.equal(calls.filter(row => row.url === "/create").length, scenario === "destroy_fences_queued_intents" ? 0 : 1);
      if (scenario === "destroy_fences_queued_intents") assert.throws(create, /已切换|已关闭/);
    }
  }
  console.log(JSON.stringify({scenario, passed: true, calls, statuses: hooks.statuses}));
}


async function ownerProbe() {
  // Exercise the production edit queue and request builder against the
  // existing isolated owner HTTP/PG fixture. Geometry is a measured-UI port;
  // placement, layout commit and component creation stay in production.
  const { JSDOM } = require('jsdom');
  const dom = new JSDOM('<!doctype html><html></html>', {
    url: 'https://lineage.fixture.invalid', runScripts: 'outside-only',
  });
  global.window = dom.window;
  for (const name of ['portal_text.js', 'portal_state.js', 'lineage_program.js', 'hatg_lineage_state.js', 'hatg_layout.js']) {
    window.eval(fs.readFileSync(input.root + '/management_portal/static/' + name, 'utf8'));
  }
  window.PortalAPI = {get: async () => JSON.parse(fs.readFileSync(
    input.root + "/management_portal/static/lineage_primitive_definitions.json", "utf8"))};
  await window.PortalLineageProgram.loadPrimitiveDefinitions();
  window.requestAnimationFrame = callback => setImmediate(callback);
  const calls = [];
  const layoutStarted = deferred();
  const request = async (url, body) => {
    const response = await fetch(input.origin + url, body === undefined ? {} : {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) throw Object.assign(new Error(value.error), {status: response.status});
    return value;
  };
  const post = (url, body) => {
    calls.push({url, body: structuredClone(body)});
    const pending = request(url, body);
    if (url.endsWith("/positions")) layoutStarted.resolve();
    return pending;
  };
  const initial = await request("/state");
  const hooks = {toasts: [], statuses: [], loadLineage: () => request("/state")};
  const fixture = new Function("initial", "apiPost", "hooks", input.functions + bootstrap)(initial, post, hooks);
  const size = {width: 154 / 1280 * 100, height: 94 / 900 * 100};
  fixture.state.graphController = {
    getPlacementGeometry: () => ({
      node_sizes: Object.fromEntries(fixture.state.lineage.program.components.map(node => [node.id, {...size}])),
      child_size: {...size},
    }),
    ensureNodeVisible() {},
  };
  const movedNode = initial.program.components[0];
  const position = {id: movedNode.id, x: movedNode.x - 10, y: movedNode.y + 20};
  Object.assign(movedNode, position);
  await request("/hold", {});
  try {
    const layout = fixture.saveLineageLayout([position]);
    await layoutStarted.promise;
    const create = fixture.createProgramComponentFromDrop("event_source", position);
    const followUp = fixture.createProgramComponentFromDrop("event_source", position);
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(calls.map(row => row.url), ["/lineage/api/layout/positions"]);
    const before = await request("/observations");
    assert.equal(before.filter(row => row.path.endsWith("/create")).length, 0);
    await request("/release", {});
    await Promise.all([layout, create, followUp]);
    const stored = await request("/state");
    const moved = stored.program.components.find(row => row.id === position.id);
    const created = stored.program.components.filter(row => row.id !== position.id);
    assert.equal(stored.program.components.length, 3);
    assert.deepEqual([moved.x, moved.y], [position.x, position.y]);
    for (const node of created) assert.notDeepEqual([node.x, node.y], [moved.x, moved.y]);
    assert.notDeepEqual([created[0].x, created[0].y], [created[1].x, created[1].y]);
    const observations = await request("/observations");
    assert.deepEqual(observations.map(row => row.path), [
      "/lineage/api/layout/positions",
      "/lineage/api/program/components/create",
      "/lineage/api/program/components/create",
    ]);
    for (const row of observations.slice(1)) {
      assert.equal(row.layout_held, false);
      assert.deepEqual(row.before_positions.find(item => item[0] === position.id).slice(1), [position.x, position.y]);
    }
    console.log(JSON.stringify({
      passed: true, moved_position: [moved.x, moved.y],
      created_position: [created[0].x, created[0].y],
      writes_before_layout_release: false, observations,
      scope: "Node controller -> isolated HTTP owner/PG; measured geometry port; no browser",
    }));
  } finally {
    await request("/release", {});
    dom.window.close();
  }
}
const run = input.mode === "owner" ? ownerProbe : input.mode === "node" ? nodeProbe : null;
if (!run) throw new Error("Unknown probe mode: " + input.mode);
run().catch(error => {console.error(error.stack); process.exitCode = 1;});
