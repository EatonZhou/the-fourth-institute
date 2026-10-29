const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const root = path.resolve(__dirname, '../../..');
const graphGeometry = require(path.join(root, 'management_portal/static/projection_libraries/portal-graph/1.0.0/geometry.js'));
const context = vm.createContext({});
context.window = context;
vm.runInContext(fs.readFileSync(path.join(root, 'management_portal/static/lineage_graph_viewport.js'), 'utf8'), context);

function fixture() {
  const canvas = { left: 100, top: 80, right: 1100, bottom: 780 };
  const box = { left: 942, top: 679, right: 1090, bottom: 771 };
  const state = { hidden: false, rendered: true, configured: { top: 58, bottom: 0 } };
  const viewport = context.PortalHatgGraphViewport.createViewport({
    d3: {}, graphGeometry,
    minimap: { hasAttribute: () => state.hidden, getClientRects: () => state.rendered ? [box] : [], getBoundingClientRect: () => box },
    number: (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback,
    options: { viewportInsets: () => state.configured },
    root: { clientWidth: 1000, clientHeight: 700, getBoundingClientRect: () => canvas },
    scaleExtent: [0.35, 3.2], state: {}, worldSize: { width: 1280, height: 900 },
  });
  return { canvas, box, state, insets: () => JSON.parse(JSON.stringify(viewport.currentViewportInsets())) };
}

test('fit area excludes the actual minimap footprint and preserves caller insets', () => {
  const f = fixture();
  const insets = f.insets();
  assert.equal(insets.top, 58);
  assert.ok(f.canvas.bottom - insets.bottom <= f.box.top - 10);
  assert.deepEqual(f.state.configured, { top: 58, bottom: 0 });
  f.state.configured = { top: 58, right: 210, bottom: 240 };
  assert.deepEqual(f.insets(), { left: 0, right: 210, top: 58, bottom: 240 });
});

test('hidden, CSS-hidden and disjoint minimaps consume no viewport', () => {
  const f = fixture();
  f.state.hidden = true;
  assert.equal(f.insets().bottom, 0);
  f.state.hidden = false; f.state.rendered = false;
  assert.equal(f.insets().bottom, 0);
  f.state.rendered = true; f.box.left = f.canvas.right;
  assert.equal(f.insets().bottom, 0);
});

test('responsive minimap height is measured again and overlapping insets leave a finite area', () => {
  const f = fixture();
  f.box.top -= 70;
  assert.ok(f.canvas.bottom - f.insets().bottom <= f.box.top - 10);
  f.state.configured.top = 650;
  const insets = f.insets();
  assert.ok(insets.top + insets.bottom <= 699.000001);
  assert.ok(Object.values(insets).every(Number.isFinite));
});
