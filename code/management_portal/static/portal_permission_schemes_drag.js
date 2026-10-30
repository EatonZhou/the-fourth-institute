(() => {
  "use strict";

  if (window.PortalPermissionSchemesDrag) return;

  // Dragging tree nodes, object types and instructions onto tree nodes and
  // entry cells, and the page's drag and drop listeners.

  const DEPENDENCIES = Object.freeze([
    "addNode", "canHang", "changed", "editable", "fitRelation", "indexTree", "isRelationRule", "moveNode",
    "placeInstruction", "root", "state", "typesOf",
  ]);

  function createDrag(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesDrag is missing: ${missing.join(", ")}`);
    const {
      addNode, canHang, changed, editable, fitRelation, indexTree, isRelationRule, moveNode, placeInstruction, root,
      state, typesOf,
    } = dependencies;

    let drag = null;

    // ---- drag and drop --------------------------------------------------------------
    // data-drag / data-drop: "node|<tree>|<id>", "type|<tree>|<kind>",
    // "instruction|<name>", "cell|<entry index>|<tree>".
    const parse = value => String(value || "").split("|");

    function canDrop(source, target) {
      if (!source || !editable()) return false;
      const [sourceKind, sourceTree, sourceId] = source;
      const [targetKind, targetA, targetB] = target;
      if (targetKind === "node") {
        if (sourceKind === "instruction") return targetA === "A";
        if (sourceTree !== targetA) return false;
        const index = indexTree(state.draft, targetA);
        if (sourceKind === "type") return canHang(index, [sourceId], targetB, state.vocabulary);
        if (sourceKind !== "node" || sourceId === index.tree.root || sourceId === targetB || index.within(targetB, sourceId)) return false;
        return canHang(index, typesOf(index.nodes.get(sourceId)), targetB, state.vocabulary);
      }
      // A, P and a node rule's R take class nodes; a relation rule's R takes an each node.
      if (targetKind === "cell" && sourceKind === "node") {
        if (sourceTree !== targetB) return false;
        const each = indexTree(state.draft, sourceTree).isEach(sourceId);
        const relation = isRelationRule(state.draft.entries[Number(targetA)]);
        return sourceTree === "R" && relation ? each : !each;
      }
      return false;
    }

    function applyDrop(source, target) {
      const [sourceKind, sourceTree, sourceId] = source;
      const [targetKind, targetA, targetB] = target;
      if (targetKind === "cell") {
        const entry = state.draft.entries[Number(targetA)];
        entry[targetB.toLowerCase()] = sourceId;
        if (targetB === "R") fitRelation(entry);
        changed();
      } else if (sourceKind === "instruction") placeInstruction(source[1], targetB);
      else if (sourceKind === "type") void addNode(targetA, targetB, [sourceId]);
      else if (moveNode(targetA, sourceId, targetB)) changed();
    }

    root.addEventListener("dragstart", event => {
      const handle = event.target.closest?.("[data-drag]");
      if (!handle) return;
      if (!editable()) { event.preventDefault(); return; }
      drag = parse(handle.dataset.drag);
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", handle.dataset.drag);
    });
    root.addEventListener("dragover", event => {
      const zone = event.target.closest?.("[data-drop]");
      if (!zone || !canDrop(drag, parse(zone.dataset.drop))) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      root.querySelectorAll(".is-dropTarget").forEach(item => { if (item !== zone) item.classList.remove("is-dropTarget"); });
      zone.classList.add("is-dropTarget");
    });
    root.addEventListener("dragleave", event => {
      const zone = event.target.closest?.("[data-drop]");
      if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove("is-dropTarget");
    });
    root.addEventListener("drop", event => {
      const zone = event.target.closest?.("[data-drop]");
      if (!zone) return;
      event.preventDefault();
      const source = drag;
      drag = null;
      const target = parse(zone.dataset.drop);
      if (canDrop(source, target)) applyDrop(source, target);
    });
    root.addEventListener("dragend", () => {
      drag = null;
      root.querySelectorAll(".is-dropTarget").forEach(item => item.classList.remove("is-dropTarget"));
    });

    return {
      parse,
    };
  }

  window.PortalPermissionSchemesDrag = Object.freeze({ createDrag, dependencies: DEPENDENCIES });
})();
