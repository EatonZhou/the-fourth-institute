(() => {
  "use strict";

  if (window.PortalPermissionSchemesEdits) return;

  // Editing the rules: moving, adding and removing tree nodes, classifying
  // A elements under A class nodes, switching an entry between a node rule
  // and a relation rule.

  const DEPENDENCIES = Object.freeze([
    "canHang", "changed", "elementMap", "elementsOf", "freeClassification", "freeNode", "freshNodeId", "indexTree",
    "instructionInfo", "isFixed", "isRelationRule", "nodeReferences", "relationApplies", "render", "setNotice",
    "settlePlacement", "state", "t", "tx", "typesOf",
  ]);

  function createEdits(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesEdits is missing: ${missing.join(", ")}`);
    const {
      canHang, changed, elementMap, elementsOf, freeClassification, freeNode, freshNodeId, indexTree, instructionInfo,
      isFixed, isRelationRule, nodeReferences, relationApplies, render, setNotice, settlePlacement, state, t, tx,
      typesOf,
    } = dependencies;

    function moveNode(name, id, parent) {
      const index = indexTree(state.draft, name);
      if (id === index.tree.root || id === parent || index.within(parent, id)) return false;
      if (!canHang(index, typesOf(index.nodes.get(id)), parent, state.vocabulary)) return false;
      const node = index.nodes.get(id);
      const wasNested = index.isEach(node.parent);
      node.parent = parent;
      settlePlacement(index, node, state.vocabulary, wasNested);
      return true;
    }

    async function addNode(name, parent, kinds = null) {
      const index = indexTree(state.draft, name);
      let node;
      if (kinds) {
        node = {id: freshNodeId(state.draft, name, kinds[0]), parent, title: tx`每个 ${kinds.join(" / ")}`, each: kinds.length === 1 ? kinds[0] : kinds};
        if (index.isEach(parent)) node.under = typesOf(index.nodes.get(parent))[0];
      } else {
        if (index.isEach(parent)) return;
        const title = await window.PortalUI.prompt({title: t("新增分类节点"), label: t("节点名称"), value: "", confirmLabel: t("新增")});
        if (!title || state.destroyed) return;
        node = {id: freshNodeId(state.draft, name, title), parent, title: String(title).trim()};
      }
      state.draft.trees[name].nodes.push(node);
      state.selected = node.id;
      changed();
    }

    async function removeNode(name, id) {
      const index = indexTree(state.draft, name);
      if (id === index.tree.root) return;
      const refs = nodeReferences(state.draft, name, id);
      if (refs.length) {
        setNotice(tx`${id} 仍被引用：${refs.join("、")}。先改掉这些引用再删除。`, "error");
        render();
        return;
      }
      const node = index.nodes.get(id);
      const children = index.children.get(id) || [];
      if (children.some(child => !canHang(index, typesOf(index.nodes.get(child)), node.parent, state.vocabulary))) {
        setNotice(t("它的子节点不能挂到它的父节点下，先移走子节点。"), "error");
        render();
        return;
      }
      const approved = await window.PortalUI.confirm({
        title: tx`删除节点 ${node.title || id}？`,
        message: children.length ? t("它的子节点会移到它的父节点下。") : t("保存前可以放弃修改。"),
        confirmLabel: t("删除"), tone: "danger",
      });
      if (!approved || state.destroyed) return;
      for (const child of children) {
        const item = index.nodes.get(child);
        item.parent = node.parent;
        settlePlacement(index, item, state.vocabulary, Boolean(typesOf(node)));
      }
      state.draft.trees[name].nodes = state.draft.trees[name].nodes.filter(item => item.id !== id);
      state.selected = "";
      changed();
    }

    // Puts an instruction under an A class node: every requirement of the instruction moves
    // to that node. ``name`` is an A element, or an instruction whose elements all move.
    function placeInstruction(name, node) {
      const elements = elementMap().has(name) || !instructionInfo(name) ? [name] : elementsOf(name).map(slot => slot.element);
      for (const element of elements) classify(element, node);
      changed();
    }

    // Classifies an A element under an A class node; a fixed slot keeps the R node it asks about. The free node
    // writes the free classification; the default node removes the classification.
    function classify(element, node) {
      const doc = state.draft;
      doc.instructions = doc.instructions || {};
      const fixed = isFixed(element);
      if (!node || node === doc.trees.A.default_parent) delete doc.instructions[element];
      else if (node === freeNode(doc)) doc.instructions[element] = freeClassification(doc, node, fixed);
      else doc.instructions[element] = {a: node, ...(fixed && doc.instructions[element]?.r ? {r: doc.instructions[element].r} : {})};
    }

    // A node rule names P and R class nodes; a relation rule names a relation and an R each node.
    // Switching keeps A and picks the first fitting node and relation.
    function switchEntryKind(entry, relation) {
      const doc = state.draft;
      const r = indexTree(doc, "R");
      const {id, effect, a, title} = entry;
      if (!relation) {
        const target = r.isEach(entry.r) ? doc.trees.R.root : entry.r;
        return {id, effect, p: doc.trees.P.root, a, r: target, title};
      }
      const target = r.isEach(entry.r) ? entry.r : r.tree.nodes.find(node => r.isEach(node.id))?.id || entry.r;
      const kinds = typesOf(r.nodes.get(target));
      const choice = (state.vocabulary.relations || []).find(item => relationApplies(state.vocabulary, item.relation, kinds))?.relation || "";
      return {id, effect, relation: choice, a, r: target, title};
    }

    // A relation rule's new R may not fit its relation; take the first relation that does.
    function fitRelation(entry) {
      if (!isRelationRule(entry)) return;
      const fits = relation => relationApplies(state.vocabulary, relation, typesOf(indexTree(state.draft, "R").nodes.get(entry.r)));
      if (!fits(entry.relation)) entry.relation = (state.vocabulary.relations || []).find(item => fits(item.relation))?.relation || entry.relation;
    }

    return {
      addNode, classify, fitRelation, moveNode, placeInstruction, removeNode, switchEntryKind,
    };
  }

  window.PortalPermissionSchemesEdits = Object.freeze({ createEdits, dependencies: DEPENDENCIES });
})();
