/* Portal graph geometry, shared by Lineage and projection applications.
 * World coordinates use the Lineage convention: 100 units span worldSize.
 * No DOM, application state, D3, or network dependencies.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PortalGraphGeometry = factory();
})(globalThis, function () {
  "use strict";

  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));

  function viewportInsets(configured, viewport) {
    const insets = Object.fromEntries(["top", "right", "bottom", "left"].map(side => (
      [side, Math.max(0, number(configured?.[side], 0))]
    )));
    for (const [start, end, size] of [["left", "right", viewport.width], ["top", "bottom", viewport.height]]) {
      const total = insets[start] + insets[end];
      const factor = total > 0 ? Math.min(1, Math.max(0, size - 1) / total) : 1;
      insets[start] *= factor;
      insets[end] *= factor;
    }
    return insets;
  }

  function screenToWorld(point, transform, worldSize, rect = { left: 0, top: 0 }) {
    const scale = Math.max(0.01, number(transform?.k, 1));
    return {
      x: (number(point?.x, rect.left) - rect.left - number(transform?.x)) / (worldSize.width * scale) * 100,
      y: (number(point?.y, rect.top) - rect.top - number(transform?.y)) / (worldSize.height * scale) * 100,
    };
  }

  function worldToScreen(point, transform, worldSize, rect = { left: 0, top: 0 }) {
    const scale = Math.max(0.01, number(transform?.k, 1));
    return {
      x: number(point?.x) / 100 * worldSize.width * scale + number(transform?.x) + rect.left,
      y: number(point?.y) / 100 * worldSize.height * scale + number(transform?.y) + rect.top,
    };
  }

  function viewCenter(transform, viewport, worldSize) {
    return screenToWorld({ x: viewport.width / 2, y: viewport.height / 2 }, transform, worldSize);
  }

  function visibleWorldBounds(transform, viewport, worldSize, insets = { top: 0, right: 0, bottom: 0, left: 0 }) {
    const start = screenToWorld({ x: insets.left, y: insets.top }, transform, worldSize);
    const end = screenToWorld({ x: viewport.width - insets.right, y: viewport.height - insets.bottom }, transform, worldSize);
    return { minX: start.x, maxX: end.x, minY: start.y, maxY: end.y };
  }

  function transformForCenter(center, scale, viewport, worldSize, scaleExtent = [0.01, Infinity]) {
    const k = clamp(number(scale, 1), scaleExtent[0], scaleExtent[1]);
    return {
      x: viewport.width / 2 - number(center?.x, 50) / 100 * worldSize.width * k,
      y: viewport.height / 2 - number(center?.y, 50) / 100 * worldSize.height * k,
      k,
    };
  }

  function cubicPoint(segment, progress) {
    const remaining = 1 - progress;
    const first = remaining * remaining * remaining;
    const second = 3 * remaining * remaining * progress;
    const third = 3 * remaining * progress * progress;
    const fourth = progress * progress * progress;
    return {
      x: first * segment.x1 + second * segment.c1x + third * segment.c2x + fourth * segment.x2,
      y: first * segment.y1 + second * segment.c1y + third * segment.c2y + fourth * segment.y2,
    };
  }

  function sampledRoute(segments, stepsPerSegment = 18) {
    const points = [];
    segments.forEach((segment, segmentIndex) => {
      for (let step = segmentIndex ? 1 : 0; step <= stepsPerSegment; step += 1) {
        points.push(cubicPoint(segment, step / stepsPerSegment));
      }
    });
    return points;
  }

  function routeLength(segments) {
    const points = sampledRoute(segments, 12);
    let total = 0;
    for (let index = 1; index < points.length; index += 1) {
      total += Math.hypot(points[index].x - points[index - 1].x, points[index].y - points[index - 1].y);
    }
    return total;
  }

  function routePath(segments) {
    if (!segments.length) return "";
    return `M ${segments[0].x1} ${segments[0].y1} ${segments.map(segment => `C ${segment.c1x} ${segment.c1y}, ${segment.c2x} ${segment.c2y}, ${segment.x2} ${segment.y2}`).join(" ")}`;
  }

  function bezierGeometry(points, worldSize, offset = { x: 0, y: 0 }) {
    const deltaX = points.x2 - points.x1;
    const deltaY = points.y2 - points.y1;
    const vertical = Math.abs(deltaY) / 100 * worldSize.height > Math.abs(deltaX) / 100 * worldSize.width;
    const delta = vertical ? deltaY : deltaX;
    const bend = Math.max(3, Math.min(10, Math.abs(delta) * 0.24));
    const direction = delta >= 0 ? 1 : -1;
    const curve = {
      ...points,
      c1x: (vertical ? points.x1 : points.x1 + bend * direction) + offset.x / 0.75,
      c1y: (vertical ? points.y1 + bend * direction : points.y1) + offset.y / 0.75,
      c2x: (vertical ? points.x2 : points.x2 - bend * direction) + offset.x / 0.75,
      c2y: (vertical ? points.y2 - bend * direction : points.y2) + offset.y / 0.75,
      routeAxis: vertical ? "vertical" : "horizontal",
    };
    return { ...curve, d: routePath([curve]) };
  }

  function pointInBox(point, center, half) {
    return Math.abs(point.x - center.x) <= half.x && Math.abs(point.y - center.y) <= half.y;
  }

  function boxIntersectsRect(center, half, rect) {
    return center.x + half.x >= rect.left && center.x - half.x <= rect.right
      && center.y + half.y >= rect.top && center.y - half.y <= rect.bottom;
  }

  return Object.freeze({ viewportInsets, screenToWorld, worldToScreen, viewCenter, visibleWorldBounds,
    transformForCenter, cubicPoint, sampledRoute, routeLength, routePath, bezierGeometry, pointInBox, boxIntersectsRect });
});
