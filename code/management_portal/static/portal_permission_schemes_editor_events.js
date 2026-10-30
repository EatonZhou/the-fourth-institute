(() => {
  "use strict";

  if (window.PortalPermissionSchemesEditorEvents) return;

  // The rules editor's click, input and change listeners, and going to the
  // place an error of the server's check points at.

  const DEPENDENCIES = Object.freeze([
    "ENTRY_ID", "NODE_ID", "act", "addNode", "applyChange", "busy", "canHang", "changed", "classify",
    "confirmDiscard", "copyScheme", "elementMap", "entryId", "errorTarget", "fitRelation", "freeNode", "freshId",
    "indexTree", "moveNode", "open", "parse", "placeInstruction", "removeNode", "renameNode", "render", "root",
    "setNotice", "settlePlacement", "side", "state", "switchEntryKind", "t", "tryDecide", "tx", "typesOf",
  ]);

  function createEditorEvents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesEditorEvents is missing: ${missing.join(", ")}`);
    const {
      ENTRY_ID, NODE_ID, act, addNode, applyChange, busy, canHang, changed, classify, confirmDiscard, copyScheme,
      elementMap, entryId, errorTarget, fitRelation, freeNode, freshId, indexTree, moveNode, open, parse,
      placeInstruction, removeNode, renameNode, render, root, setNotice, settlePlacement, side, state,
      switchEntryKind, t, tryDecide, tx, typesOf,
    } = dependencies;

    // ---- events ---------------------------------------------------------------------
    root.addEventListener("click", event => {
      const target = event.target.closest?.("button");
      if (!target || state.destroyed) return;
      const data = target.dataset;
      const doc = state.draft;
      if (data.openScheme) {
        if (data.openScheme === state.current?.scheme_id) return;
        void (async () => { if (await confirmDiscard()) await busy(async () => { setNotice(""); await open(data.openScheme); }); })();
      } else if (data.copyScheme) {
        // The copy opens afterwards, so unsaved rules of the open scheme are given up first.
        const title = state.schemes.find(item => item.scheme_id === data.copyScheme)?.title;
        void (async () => { if (!state.busy && await confirmDiscard()) await copyScheme(data.copyScheme, title); })();
      } else if (data.view) { state.view = data.view; state.highlightEntry = -1; render(); }
      else if (data.tree) { state.tree = data.tree; state.selected = ""; render(); }
      else if (data.instructionShow) { state.show = data.instructionShow; render(); }
      else if (data.select !== undefined) { state.selected = data.select; render(); }
      else if (data.expand) {
        if (state.expanded.has(data.expand)) state.expanded.delete(data.expand); else state.expanded.add(data.expand);
        render();
      } else if (data.addNode) void addNode(state.tree, data.addNode);
      else if (data.addEach) {
        const index = indexTree(doc, state.tree);
        // Nested under an each node the new node is placed under its type; under a class node, without a placement.
        const under = index.isEach(data.addEach) ? typesOf(index.nodes.get(data.addEach))[0] : "";
        const kind = (state.vocabulary.object_types?.[state.tree] || []).find(item => !index.placing(item, under) && canHang(index, [item], data.addEach, state.vocabulary));
        if (!kind) { setNotice(t("没有可以放在这里的对象类型：一类对象在一棵树中每种放置关系只有一个规则节点，挂在规则节点下还需要能放在它的对象之下。"), "error"); render(); }
        else void addNode(state.tree, data.addEach, [kind]);
      } else if (data.removeNode) void removeNode(state.tree, data.removeNode);
      else if (data.defaultNode) { doc.trees[state.tree].default_parent = data.defaultNode; changed(); }
      else if (data.editInstruction) {
        if (state.editing.has(data.editInstruction)) state.editing.delete(data.editInstruction); else state.editing.add(data.editInstruction);
        render();
      } else if (data.slotFree) placeInstruction(data.slotFree, freeNode(doc));
      else if (data.slotClear) placeInstruction(data.slotClear, doc.trees.A.default_parent);
      else if (data.staleClear) { delete doc.instructions[data.staleClear]; changed(); }
      else if (data.entryAdd) {
        const id = freshId(new Set((doc.entries || []).map(entryId)), data.entryAdd === "deny" ? "deny" : "entry", ENTRY_ID);
        doc.entries = [...(doc.entries || []), {id, effect: data.entryAdd, p: doc.trees.P.root, a: doc.trees.A.root, r: doc.trees.R.root, title: ""}];
        state.highlightEntry = doc.entries.length - 1;
        changed();
      } else if (data.entryRemove) { doc.entries.splice(Number(data.entryRemove), 1); state.highlightEntry = -1; changed(); }
      else if (data.mergeTake) {
        // A conflict of the last automatic merge takes one side into the draft; new items are placed by that side's rules.
        const conflict = (state.source?.last_merge?.conflicts || []).find(item => item.key === data.mergeTake);
        if (!conflict) return;
        const now = data.mergeSide === "own" ? conflict.own : conflict.now;
        const order = data.mergeSide === "own" ? JSON.parse(state.base) : state.source.rules || {};
        applyChange(doc, {key: conflict.key, now: side(now)}, order);
        changed();
      } else if (data.gotoError) gotoError(data.gotoError);
      else if (data.checkRun !== undefined) void tryDecide();
      else if (data.action) void act(data.action);
    });

    root.addEventListener("input", event => {
      const input = event.target;
      const data = input.dataset || {};
      const doc = state.draft;
      if (data.docField) { doc[data.docField] = input.value; changed({rerender: false}); }
      else if (data.nodeTitle) {
        const node = doc.trees[state.tree].nodes.find(item => item.id === data.nodeTitle);
        if (node) { node.title = input.value; changed({rerender: false}); }
      } else if (data.entry) {
        const [index, field] = parse(data.entry);
        if (field === "title" || field === "id") { doc.entries[Number(index)][field] = input.value.trim(); changed({rerender: false}); }
      } else if (data.instructionFilter !== undefined) { state.filter = input.value; render(); }
      else if (data.check) {
        state.checkInput[data.check] = input.value.trim();
        state.checkResult = null;
        if (data.check === "instruction") render();
      } else if (data.checkArgument) state.checkInput.arguments[data.checkArgument] = input.value.trim();
    });

    root.addEventListener("change", event => {
      const input = event.target;
      const data = input.dataset || {};
      const doc = state.draft;
      if (data.mergePick !== undefined && state.merge) {
        state.merge.picks.set(data.mergePick, input.type === "checkbox" ? (input.checked ? "source" : "own") : input.value);
        render();
        return;
      }
      if (data.nodeId) {
        const value = input.value.trim();
        if (value === data.nodeId) return;
        if (!NODE_ID.test(value) || doc.trees[state.tree].nodes.some(node => node.id === value)) {
          setNotice(tx`节点 ID 无效或已存在：${value}。ID 以 @ 开头，只含小写字母、数字和下划线。`, "error");
          render();
          return;
        }
        renameNode(doc, state.tree, data.nodeId, value);
        state.selected = value;
        changed();
      } else if (data.nodeParent) { if (moveNode(state.tree, data.nodeParent, input.value)) changed(); else render(); }
      else if (data.nodeOtherwise || data.nodeUnder) {
        const field = data.nodeOtherwise ? "otherwise" : "under";
        const node = doc.trees[state.tree].nodes.find(item => item.id === (data.nodeOtherwise || data.nodeUnder));
        if (input.value) node[field] = input.value; else delete node[field];
        changed();
      } else if (data.nodeCategory) {
        const node = doc.trees.P.nodes.find(item => item.id === data.nodeCategory);
        if (input.value) node.user_category = input.value; else delete node.user_category;
        changed();
      }
      else if (data.eachType) {
        const node = doc.trees[state.tree].nodes.find(item => item.id === state.selected);
        const kinds = new Set(typesOf(node));
        if (input.checked) kinds.add(data.eachType); else kinds.delete(data.eachType);
        if (!kinds.size) { setNotice(t("规则节点至少放置一类对象；不需要时删除节点。"), "error"); render(); return; }
        const list = [...kinds].sort();
        node.each = list.length === 1 ? list[0] : list;
        settlePlacement(indexTree(doc, state.tree), node, state.vocabulary, false);
        changed();
      } else if (data.place) {
        // A row's related type changes in place, keeping the rows' order and paths.
        const [name, index, row, field] = parse(data.place);
        const slot = doc.instructions[name][Number(index)];
        if (field !== "type") return;
        slot.place = Object.fromEntries(Object.entries(slot.place).map(([type, paths], at) => [at === Number(row) ? input.value : type, paths]));
        changed();
      } else if (data.entry) {
        const [index, field] = parse(data.entry);
        if (field === "title" || field === "id") return;
        const entry = doc.entries[Number(index)];
        if (field === "kind") doc.entries[Number(index)] = switchEntryKind(entry, input.value === "relation");
        else entry[field] = input.value;
        // A relation rule's new R may not fit its relation; take the first one that does.
        if (field === "r") fitRelation(entry);
        changed();
      } else if (data.classify) {
        // An element takes an A class node; a classified fixed slot also takes the R class node it asks about.
        const [element, field] = parse(data.classify);
        if (field === "a") classify(element, input.value);
        else if (field === "r" && doc.instructions?.[element]) doc.instructions[element].r = input.value;
        else return;
        changed();
      }
    });

    function gotoError(path) {
      const target = errorTarget(path, state.draft);
      if (target.field) { root.querySelector(`[data-focus="${target.field}"]`)?.focus(); return; }
      state.view = target.view;
      state.highlightEntry = target.entry ?? -1;
      if (target.tree) { state.tree = target.tree; state.selected = target.node || ""; }
      // An error names an A element; its instruction's row opens (a stale key's instruction may be gone).
      if (target.instruction) {
        state.filter = target.instruction;
        state.show = "all";
        state.editing.add(elementMap().get(target.instruction)?.instruction || target.instruction.split(":")[0]);
      }
      render();
    }
  }

  window.PortalPermissionSchemesEditorEvents = Object.freeze({ createEditorEvents, dependencies: DEPENDENCIES });
})();
