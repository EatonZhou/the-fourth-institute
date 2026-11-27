(() => {
  "use strict";

  if (window.PortalHatgGraphFunctionBlocks) return;

  // Function Block boundaries: the frame drawn around a block's members and
  // nested blocks, and moving one enlarged object clear of the others.

  const DEPENDENCIES = Object.freeze([
    "d3", "layers", "nodeHalfExtents", "nodePosition", "options", "portPosition", "portsForNode",
    "refreshNodeExtentCache", "state", "worldSize",
  ]);

  function createFunctionBlocks(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphFunctionBlocks is missing: ${missing.join(", ")}`);
    const {
      d3, layers, nodeHalfExtents, nodePosition, options, portPosition, portsForNode, refreshNodeExtentCache, state,
      worldSize,
    } = dependencies;

    let boundaryIndex;
    let elementsById = new Map();

    function indexedBoundaries() {
      const blocks = state.model.functionBlockBoundaries;
      if (!boundaryIndex || boundaryIndex.blocks !== blocks) {
        const byId = new Map(), byMember = new Map(), parents = new Map();
        const add = (index, memberId, blockId) => {
          if (!index.has(memberId)) index.set(memberId, []);
          index.get(memberId).push(blockId);
        };
        for (const block of blocks || []) {
          byId.set(block.id, block);
          for (const id of block.member_ids) add(byMember, id, block.id);
          for (const id of block.child_block_ids || []) add(parents, id, block.id);
        }
        boundaryIndex = { blocks, byId, byMember, parents };
      }
      return boundaryIndex;
    }

    function nodeFootprint(node) {
      const position = nodePosition(node), half = nodeHalfExtents(node);
      const boxes = [{ left: position.x - half.x, right: position.x + half.x,
        top: position.y - half.y, bottom: position.y + half.y },
        ...portsForNode(node).map(port => {
          const point = portPosition(port);
          return { left: point.x - 3000 / worldSize.width, right: point.x + 3000 / worldSize.width,
            top: point.y - 1200 / worldSize.height, bottom: point.y + 1200 / worldSize.height };
        })];
      return { left: Math.min(...boxes.map(box => box.left)), right: Math.max(...boxes.map(box => box.right)),
        top: Math.min(...boxes.map(box => box.top)), bottom: Math.max(...boxes.map(box => box.bottom)) };
    }

    function functionBlockBoundaryBounds(block, seen = new Set(), footprints = new Map()) {
      if (seen.has(block.id)) return null;
      seen.add(block.id);
      const boxes = [];
      for (const id of block.member_ids) {
        const node = state.nodeMap.get(id);
        if (!node) continue;
        if (!footprints.has(id)) footprints.set(id, nodeFootprint(node));
        boxes.push(footprints.get(id));
      }
      const { byId } = indexedBoundaries();
      for (const id of block.child_block_ids || []) {
        const child = byId.get(id);
        const bounds = child && functionBlockBoundaryBounds(child, seen, footprints);
        if (bounds) boxes.push(bounds);
      }
      // Wire-only and empty blocks still need an accessible header and collapse control.
      if (!boxes.length) {
        const x = Number.isFinite(block.x) ? block.x : 50;
        const y = Number.isFinite(block.y) ? block.y : 50;
        return { left: x - 12100 / worldSize.width, right: x + 12100 / worldSize.width,
          top: y - 5000 / worldSize.height, bottom: y + 5000 / worldSize.height };
      }
      const left = Math.min(...boxes.map(box => box.left)) - 1600 / worldSize.width;
      const right = Math.max(...boxes.map(box => box.right)) + 1600 / worldSize.width;
      const boundary = elementsById.get(block.id);
      // Long identities wrap. Reserve their actual unscaled header height so
      // nested blocks and member ports remain below the title at every zoom.
      const width = `${right - left}%`;
      if (boundary && boundary.style.width !== width) boundary.style.width = width;
      const headerHeight = Math.max(42, (boundary?.querySelector("header")?.offsetHeight || 0) + 12);
      return {
        left,
        right,
        top: Math.min(...boxes.map(box => box.top)) - headerHeight * 100 / worldSize.height,
        bottom: Math.max(...boxes.map(box => box.bottom)) + 1600 / worldSize.height,
      };
    }

    // A title edit or explicit expansion can enlarge an object after its
    // original placement. Move that object alone, preserving all other layout.
    function getObjectSeparation(id) {
      refreshNodeExtentCache();
      renderFunctionBlockBoundaries();
      const { blocks, byId, parents } = indexedBoundaries();
      const block = byId.get(id);
      const members = new Set(block ? block.member_ids : [id]);
      const node = state.nodeMap.get(id);
      const box = block ? functionBlockBoundaryBounds(block) : node && nodeFootprint(node);
      if (!box) return null;
      const related = new Set([id]);
      const collect = (next) => {
        const seen = new Set([id]);
        for (const current of seen) for (const relative of next(current)) seen.add(relative);
        for (const relative of seen) related.add(relative);
      };
      if (block) {
        collect(current => parents.get(current) || []);
        collect(current => byId.get(current)?.child_block_ids || []);
      }
      const obstacles = state.model.nodes.filter(item => !members.has(item.id)).map(nodeFootprint);
      for (const other of blocks || []) {
        if (related.has(other.id) || other.member_ids.includes(id)) continue;
        const bounds = functionBlockBoundaryBounds(other);
        if (bounds) obstacles.push(bounds);
      }
      const gapX = 24 * 100 / worldSize.width, gapY = 24 * 100 / worldSize.height;
      const rectangles = obstacles.map(other => ({
        left: other.left - box.right - gapX, right: other.right - box.left + gapX,
        top: other.top - box.bottom - gapY, bottom: other.bottom - box.top + gapY,
      }));
      let best = { x: 0, y: 0 }, score = Infinity, bestOrder = Infinity;
      const columns = [...new Set([0, ...rectangles.flatMap(rect => [rect.left, rect.right])])]
        .map((x, order) => ({ x, order })).sort((a, b) => a.x - b.x);
      const starts = [...rectangles].sort((a, b) => a.left - b.left);
      const ends = [...rectangles].sort((a, b) => a.right - b.right);
      const active = new Set();
      let start = 0, end = 0;
      for (const { x, order } of columns) {
        // Edge contact is free. Retain the original column order for equal scores.
        while (start < starts.length && starts[start].left < x) active.add(starts[start++]);
        while (end < ends.length && ends[end].right <= x) active.delete(ends[end++]);
        const horizontalDistance = (x * worldSize.width) ** 2;
        if (horizontalDistance > score || (horizontalDistance === score && order >= bestOrder)) continue;
        const intervals = [...active].sort((a, b) => a.top - b.top);
        const merged = [];
        for (const interval of intervals) {
          const last = merged.at(-1);
          if (last && interval.top < last.bottom) last.bottom = Math.max(last.bottom, interval.bottom);
          else merged.push({ top: interval.top, bottom: interval.bottom });
        }
        const covering = merged.find(interval => interval.top < 0 && interval.bottom > 0);
        const y = covering ? (Math.abs(covering.top) < covering.bottom ? covering.top : covering.bottom) : 0;
        const distance = horizontalDistance + (y * worldSize.height) ** 2;
        if (distance < score || (distance === score && order < bestOrder)) {
          best = { x, y };
          score = distance;
          bestOrder = order;
        }
      }
      return score > 0.000001 ? best : null;
    }

    function positionBoundary(element, block, footprints) {
      const bounds = functionBlockBoundaryBounds(block, new Set(), footprints);
      element.hidden = !bounds;
      if (!bounds) return;
      const { left, right, top, bottom } = bounds;
      Object.assign(element.style, { left: `${left}%`, top: `${top}%`, width: `${right - left}%`, height: `${bottom - top}%` });
    }

    function renderFunctionBlockBoundaries(movedNodeIds) {
      const footprints = new Map();
      if (movedNodeIds) {
        const { byId, byMember, parents } = indexedBoundaries();
        const affected = new Set();
        for (const nodeId of movedNodeIds) {
          for (const blockId of byMember.get(nodeId) || []) affected.add(blockId);
        }
        for (const id of affected) {
          for (const parentId of parents.get(id) || []) affected.add(parentId);
          const element = elementsById.get(id);
          if (element) positionBoundary(element, byId.get(id), footprints);
        }
        return;
      }
      const boundaries = layers.nodes.selectAll("section.functionBlockBoundary")
        .data(state.model.functionBlockBoundaries || [], block => block.id)
        .join(enter => {
          const boundary = enter.append("section").attr("class", "functionBlockBoundary");
          const header = boundary.append("header");
          header.append("button").attr("type", "button").attr("class", "functionBlockBoundaryTitle");
          header.append("button").attr("type", "button").attr("class", "functionBlockBoundaryCollapse");
          return boundary;
        })
        .attr("data-function-block-boundary", block => block.id);
      boundaries.select(".functionBlockBoundaryTitle").text(block => block.title || "Function Block");
      boundaries.select(".functionBlockBoundaryCollapse").text("−");
      elementsById = new Map();
      boundaries.each(function indexBoundary(block) { elementsById.set(block.id, this); });
      boundaries.each(function bindBoundary(block) {
          positionBoundary(this, block, footprints);
          const boundary = d3.select(this);
          boundary.select(".functionBlockBoundaryTitle").text(block.title || "Function Block")
            .on("click.hatg", event => { event.stopPropagation(); options.onFunctionBlockMenu?.(block.id); });
          boundary.select(".functionBlockBoundaryCollapse").text("−")
            .attr("aria-label", window.PortalI18n.t("折叠"))
            .on("click.hatg", event => { event.stopPropagation(); options.onFunctionBlockCollapse?.(block.id); });
        });
    }

    return {
      functionBlockBoundaryBounds, getObjectSeparation, renderFunctionBlockBoundaries,
    };
  }

  window.PortalHatgGraphFunctionBlocks = Object.freeze({ createFunctionBlocks, dependencies: DEPENDENCIES });
})();
