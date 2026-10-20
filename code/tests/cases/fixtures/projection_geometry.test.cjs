const assert = require('node:assert/strict');
const { test } = require('node:test');
const g = require('../../../management_portal/static/projection_libraries/portal-graph/1.0.0/geometry.js');

test('zoom, pan, resize and client offsets preserve an unbounded world point', () => {
  const world = {width: 1280, height: 900};
  const point = {x: -30, y: 170};
  const offset = {left: 77, top: 123};
  for (const scale of [0.35, 1, 3.2]) {
    for (const viewport of [{width: 800, height: 600}, {width: 340, height: 210}]) {
      const transform = g.transformForCenter(point, scale, viewport, world, [0.35, 3.2]);
      const screen = g.worldToScreen(point, transform, world, offset);
      assert.ok(Math.abs(screen.x - (viewport.width / 2 + offset.left)) < 1e-9);
      assert.ok(Math.abs(screen.y - (viewport.height / 2 + offset.top)) < 1e-9);
      const back = g.screenToWorld(screen, transform, world, offset);
      const center = g.viewCenter(transform, viewport, world);
      for (const actual of [back, center]) {
        assert.ok(Math.abs(actual.x - point.x) < 1e-9);
        assert.ok(Math.abs(actual.y - point.y) < 1e-9);
      }
    }
  }
});

test('inspector occlusion retains its width and at least one pixel for canvas', () => {
  const viewport = {width: 340, height: 210};
  assert.deepEqual(g.viewportInsets({left: 280}, viewport), {left: 280, right: 0, top: 0, bottom: 0});
  const insets = g.viewportInsets({left: 300, right: 200, top: -4}, viewport);
  assert.equal(insets.left + insets.right, 339);
  assert.equal(insets.top, 0);
  assert.deepEqual(g.visibleWorldBounds({x: 10, y: 20, k: 2}, {width: 210, height: 220},
    {width: 100, height: 100}, {left: 10, right: 0, top: 20, bottom: 0}),
  {minX: 0, maxX: 100, minY: 0, maxY: 100});
});

test('curves retain endpoints, route orientation and parallel lane offsets', () => {
  const horizontal = g.bezierGeometry({x1: 0, y1: 0, x2: 100, y2: 0}, {width: 1280, height: 900});
  assert.equal(horizontal.d, 'M 0 0 C 10 0, 90 0, 100 0');
  assert.equal(g.routeLength([horizontal]), 100);
  assert.deepEqual(g.cubicPoint(horizontal, 0.5), {x: 50, y: 0});
  const vertical = g.bezierGeometry({x1: 0, y1: 100, x2: 0, y2: 0}, {width: 1280, height: 900}, {x: 3, y: 0});
  assert.equal(vertical.routeAxis, 'vertical');
  assert.deepEqual(g.cubicPoint(vertical, 0), {x: 0, y: 100});
  assert.deepEqual(g.cubicPoint(vertical, 1), {x: 0, y: 0});
  assert.deepEqual(g.cubicPoint(vertical, 0.5), {x: 3, y: 50});
  assert.ok(g.routeLength([vertical]) > 100);
  const points = g.sampledRoute([horizontal, {...horizontal, x1: 100, x2: 200, c1x: 110, c2x: 190}], 4);
  assert.equal(points.length, 9);
  assert.equal(points.filter(point => point.x === 100).length, 1);
  assert.equal(g.routePath([]), '');
});

test('marquee and obstacle hit tests include touching boundaries', () => {
  const center = {x: 50, y: 30}, half = {x: 10, y: 5};
  assert.ok(g.pointInBox({x: 40, y: 35}, center, half));
  assert.ok(!g.pointInBox({x: 39.9, y: 35}, center, half));
  assert.ok(g.boxIntersectsRect(center, half, {left: 60, right: 90, top: 35, bottom: 70}));
  assert.ok(!g.boxIntersectsRect(center, half, {left: 60.01, right: 90, top: 35, bottom: 70}));
});
