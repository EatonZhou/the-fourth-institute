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

    function functionBlockBoundaryBounds(block, seen = new Set()) {
      if (seen.has(block.id)) return null;
      seen.add(block.id);
      const boxes = block.member_ids.map(id => state.nodeMap.get(id)).filter(Boolean).map(nodeFootprint);
      for (const id of block.child_block_ids || []) {
        const child = (state.model.functionBlockBoundaries || []).find(candidate => candidate.id === id);
        const bounds = child && functionBlockBoundaryBounds(child, seen);
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
      const boundary = layers.nodes.node()?.querySelector(`[data-function-block-boundary="${CSS.escape(block.id)}"]`);
      // Long identities wrap. Reserve their actual unscaled header height so
      // nested blocks and member ports remain below the title at every zoom.
      if (boundary) boundary.style.width = `${right - left}%`;
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
      const blocks = state.model.functionBlockBoundaries || [];
      const block = blocks.find(item => item.id === id);
      const members = new Set(block ? block.member_ids : [id]);
      const node = state.nodeMap.get(id);
      const box = block ? functionBlockBoundaryBounds(block) : node && nodeFootprint(node);
      if (!box) return null;
      const contains = (parent, target, seen = new Set()) => {
        if (seen.has(parent.id)) return false;
        seen.add(parent.id);
        return (parent.child_block_ids || []).some(childId => childId === target
          || (blocks.find(item => item.id === childId) && contains(blocks.find(item => item.id === childId), target, seen)));
      };
      const obstacles = state.model.nodes.filter(item => !members.has(item.id)).map(nodeFootprint);
      for (const other of blocks) {
        if (other.id === id || other.member_ids.includes(id)
          || (block && (contains(other, id) || contains(block, other.id)))) continue;
        const bounds = functionBlockBoundaryBounds(other);
        if (bounds) obstacles.push(bounds);
      }
      const gapX = 24 * 100 / worldSize.width, gapY = 24 * 100 / worldSize.height;
      const rectangles = obstacles.map(other => ({
        left: other.left - box.right - gapX, right: other.right - box.left + gapX,
        top: other.top - box.bottom - gapY, bottom: other.bottom - box.top + gapY,
      }));
      let best = { x: 0, y: 0 }, score = Infinity;
      const columns = new Set([0, ...rectangles.flatMap(rect => [rect.left, rect.right])]);
      for (const x of columns) {
        const intervals = rectangles.filter(rect => rect.left < x && x < rect.right)
          .sort((a, b) => a.top - b.top);
        const merged = [];
        for (const interval of intervals) {
          const last = merged.at(-1);
          if (last && interval.top < last.bottom) last.bottom = Math.max(last.bottom, interval.bottom);
          else merged.push({ top: interval.top, bottom: interval.bottom });
        }
        const covering = merged.find(interval => interval.top < 0 && interval.bottom > 0);
        const y = covering ? (Math.abs(covering.top) < covering.bottom ? covering.top : covering.bottom) : 0;
        const distance = (x * worldSize.width) ** 2 + (y * worldSize.height) ** 2;
        if (distance < score) { best = { x, y }; score = distance; }
      }
      return score > 0.000001 ? best : null;
    }

    function renderFunctionBlockBoundaries() {
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
      boundaries.each(function positionBoundary(block) {
          const bounds = functionBlockBoundaryBounds(block);
          this.hidden = !bounds;
          if (!bounds) return;
          const { left, right, top, bottom } = bounds;
          Object.assign(this.style, { left: `${left}%`, top: `${top}%`, width: `${right - left}%`, height: `${bottom - top}%` });
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
