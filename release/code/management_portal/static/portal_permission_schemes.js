(() => {
  "use strict";
  // The "权限方案" page of Portal Management (decision_b820faf42218 §5,
  // decision_600ae27b0538). A permission scheme is its rules and its instance.
  // The left column lists the built-in and custom schemes. The rules editor
  // shows one rules document (the permission system's rules compiler): the P,
  // A and R trees with their class nodes and each nodes (one per Portal object
  // of a type), the entry rules, and the instruction map, which hangs each
  // instruction requirement under an A class node. Nodes, object types and
  // instructions move by dragging, or with the detail panel. Built-in schemes
  // are read-only and can be copied; a custom scheme's rules are saved whole
  // with a versioned update, after its dry_run difference to the instance is
  // shown. Only the server's compiler (dry_run while editing, and the save
  // itself) decides whether a draft is valid. The instance view edits the
  // scheme's table row by row.
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const t = value => window.PortalI18n.t(value);
  const tx = (strings, ...values) => window.PortalI18n.text(strings, ...values);
  // options.expectedVersion travels in the call envelope.
  const run = (name, args = {}, key = "", options = {}) => window.PortalInstructionClient.execute(
    name, args, {...options, ...(key ? {idempotencyKey: `${key}:${window.PortalUIPrimitives.randomUUID()}`} : {})});
  const clone = value => JSON.parse(JSON.stringify(value));
  const TREES = ["P", "A", "R"];
  const TREE_TITLES = {P: "主体树 P", A: "动作树 A", R: "资源树 R"};
  const NODE_ID = /^@[a-z][a-z0-9_]*$/;
  const ENTRY_ID = /^[a-z][a-z0-9_]*$/;

  // ---- rules model (tfi.permission-scheme.v2, decision_b820faf42218 §5.8) ----
  const typesOf = node => (node?.each == null ? null : Array.isArray(node.each) ? node.each : [node.each]);
  // An entry is a node rule {p, a, r} or a relation rule {relation, a, r}.
  const isRelationRule = entry => entry?.relation !== undefined;
  // An entry without an ID is named by its position, as the compiler does.
  const entryId = (entry, position) => entry.id || `entry_${position}`;
  // The related types every one of ``kinds`` can be placed under (vocabulary.placements).
  function sharedUnders(vocabulary, kinds) {
    const lists = (kinds || []).map(kind => (vocabulary.placements?.[kind] || []).map(item => item.under));
    return lists.length ? lists.reduce((shared, list) => shared.filter(under => list.includes(under))) : [];
  }
  // "所属" or "所有者": the placement's relation title for an object of ``kinds`` under ``under``.
  const placementTitle = (vocabulary, kinds, under) => (vocabulary.placements?.[(kinds || [])[0]] || [])
    .find(item => item.under === under)?.title || "";
  // Whether a relation applies to every object type of an R each node.
  function relationApplies(vocabulary, relation, kinds) {
    const types = (vocabulary.relations || []).find(item => item.relation === relation)?.types;
    return types === "any" || (Array.isArray(types) && (kinds || []).length > 0 && kinds.every(kind => types.includes(kind)));
  }

  function indexTree(doc, name) {
    const tree = doc.trees[name];
    const nodes = new Map(tree.nodes.map(node => [node.id, node]));
    const children = new Map(tree.nodes.map(node => [node.id, []]));
    for (const node of tree.nodes) if (node.parent && children.has(node.parent)) children.get(node.parent).push(node.id);
    const path = id => {
      const out = [];
      for (let node = nodes.get(id); node && !out.includes(node.id); node = nodes.get(node.parent)) out.unshift(node.id);
      return out;
    };
    const isEach = id => typesOf(nodes.get(id)) !== null;
    // The single object type of an each node's parent: a nested each node is placed under it.
    const parentType = id => {
      const parent = typesOf(nodes.get(nodes.get(id)?.parent));
      return parent?.length === 1 ? parent[0] : "";
    };
    // Where an each node's objects go without their relation: under a class node, that node;
    // nested under an each node, its ``otherwise`` class node, else the tree's default position.
    const fallback = id => {
      const node = nodes.get(id);
      if (!node) return "";
      if (!isEach(node.parent)) return node.parent || "";
      return node.otherwise && nodes.has(node.otherwise) && !isEach(node.otherwise) ? node.otherwise : tree.default_parent || "";
    };
    // Each object type may have several each nodes in a tree, one per ``under``.
    const placing = (kind, under, except = "") => tree.nodes.find(node => node.id !== except
      && (typesOf(node) || []).includes(kind) && (node.under || "") === (under || ""))?.id || "";
    const placed = new Set(tree.nodes.flatMap(node => typesOf(node) || []));
    return {name, tree, nodes, children, path, isEach, parentType, fallback, placing, placed, within: (id, ancestor) => path(id).includes(ancestor)};
  }

  function nodeReferences(doc, name, id) {
    const refs = [];
    const key = name.toLowerCase();
    if (doc.trees[name].default_parent === id) refs.push(t("默认位置"));
    for (const node of doc.trees[name].nodes) if (node.otherwise === id) refs.push(tx`${node.title || node.id} 没有关系时的位置`);
    (doc.entries || []).forEach((entry, position) => { if (entry[key] === id) refs.push(tx`表项 ${entryId(entry, position)}`); });
    // An A element's classification names an A class node and, for a fixed slot, an R class node.
    if (name !== "P") for (const [element, classified] of Object.entries(doc.instructions || {})) if (classified?.[key] === id) refs.push(tx`A 元素 ${element}`);
    return refs;
  }

  function renameNode(doc, name, from, to) {
    const tree = doc.trees[name];
    const key = name.toLowerCase();
    for (const node of tree.nodes) {
      if (node.id === from) node.id = to;
      if (node.parent === from) node.parent = to;
      if (node.otherwise === from) node.otherwise = to;
    }
    if (tree.root === from) tree.root = to;
    if (tree.default_parent === from) tree.default_parent = to;
    for (const entry of doc.entries || []) if (entry[key] === from) entry[key] = to;
    if (name !== "P") for (const classified of Object.values(doc.instructions || {})) if (classified?.[key] === from) classified[key] = to;
  }

  function freshId(used, base, pattern) {
    const start = pattern.test(base) ? base : base.replace(/[^a-z0-9_@]+/g, "_");
    if (!used.has(start) && pattern.test(start)) return start;
    for (let index = 2; ; index++) if (!used.has(`${start}_${index}`)) return `${start}_${index}`;
  }

  function freshNodeId(doc, name, hint) {
    const slug = String(hint || "").toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^[^a-z]+|_+$/g, "");
    return freshId(new Set(doc.trees[name].nodes.map(node => node.id)), `@${slug || "node"}`, NODE_ID);
  }

  // Where an each node of ``kinds`` may hang: under a class node, or under an
  // each node of a single type every one of ``kinds`` can be placed under.
  function canHang(index, kinds, parent, vocabulary) {
    const parentTypes = typesOf(index.nodes.get(parent));
    if (parentTypes === null) return true;
    if (!kinds) return false;
    return parentTypes.length === 1 && sharedUnders(vocabulary, kinds).includes(parentTypes[0]);
  }

  // A nested each node is placed under its parent's type. Under a class node it has no
  // ``otherwise``; it keeps an ``under`` chosen there, and drops the one it had as a nested node.
  function settlePlacement(index, node, vocabulary, wasNested) {
    const kinds = typesOf(node);
    if (!kinds) return;
    if (index.isEach(node.parent)) {
      node.under = index.parentType(node.id);
      return;
    }
    delete node.otherwise;
    if (wasNested || (node.under && !sharedUnders(vocabulary, kinds).includes(node.under))) delete node.under;
  }


  // Where an error path points in the editor.
  function errorTarget(path, doc) {
    const node = /^trees\.([PAR])(?:\.nodes\[(\d+)\])?/.exec(path);
    if (node) {
      const id = node[2] === undefined ? "" : doc.trees[node[1]]?.nodes[Number(node[2])]?.id;
      return {view: "trees", tree: node[1], node: id || ""};
    }
    const entry = /^entries\[(\d+)\]/.exec(path);
    if (entry) return {view: "entries", entry: Number(entry[1])};
    // instructions.<element>[.a|.r]; an element of an argument path holds dots itself (CreateTemplate:from_selection.lineage_id).
    const instruction = /^instructions\.(.+?)(?:\.[ar])?$/.exec(path);
    if (instruction) return {view: "instructions", instruction: instruction[1]};
    if (/^(title|description)$/.test(path)) return {field: path};
    return {view: "entries"};
  }

  // What a declared decision slot judges (vocabulary.instructions[].elements): an argument judged through a saved
  // record (via, vocabulary.records) names the record and the field that holds the object; a type argument judged
  // where the new object will sit (place) names, for each related type, the argument paths that give it; a fixed
  // slot names no object.
  const placeText = place => Object.entries(place || {}).map(([related, paths]) => tx`${related} 取 ${[].concat(paths).join(" / ")}`).join(t("；"));
  function declaredText(slot, records = []) {
    if (!slot.argument) return t("固定槽：不指向对象，判定方案给的 R 节点");
    const optional = slot.optional ? ` · ${t("可选")}` : "";
    if (slot.place) return `${slot.argument}${tx`（按新资源落点：${placeText(slot.place)}）`}${optional}`;
    const record = slot.via ? records.find(item => item.via === slot.via) : null;
    const via = !slot.via ? "" : record ? tx`（按${t(record.record_title)}的 ${slot.via.split(".").slice(1).join(".")}）` : tx`（按 ${slot.via}）`;
    return `${slot.argument}${via}${slot.types?.length ? ` (${slot.types.join(" / ")})` : ""}${optional}`;
  }
  // A compiled slot (授权差 remapped items: {element, a, argument…} or {element, a, resource}) with the titles of its nodes.
  function slotSummary(slot, doc, records = []) {
    const titled = (tree, id) => doc?.trees?.[tree]?.nodes?.find(node => node.id === id)?.title || id;
    const aTitle = titled("A", slot.a);
    const element = slot.element ? `${slot.element}：` : "";
    if (slot.resource) return `${element}${titled("R", slot.resource)} → ${aTitle}`;
    return `${element}${declaredText(slot, records)} → ${aTitle}`;
  }

  // 授权差 (b820 §5.12): what principals gain and lose on the current objects, summed into groups of
  // (P node, A levels before → after, R node) with their principal and resource counts. It only reports:
  // no confirmation waits on it. ``doc`` gives node titles; IDs show where it has none.
  const AUTH_KINDS = {neutral: "中性", tightening: "收紧", loosening: "放宽", mixed: "混合"};
  function authDiffParts(report, doc) {
    if (!report || typeof report !== "object") return null;
    const titled = (tree, id) => doc?.trees?.[tree]?.nodes?.find(node => node.id === id)?.title || id;
    const levels = list => list?.length ? list.map(id => titled("A", id)).join(t("、")) : t("无");
    const side = (tree, box = {}) => {
      const kinds = box.kinds?.length ? `（${box.kinds.join(" / ")}）` : "";
      const names = (box.except || []).map(id => titled(tree, id)).join(t("、"));
      const except = !names ? "" : (box.except_total || 0) > box.except.length
        ? tx`（除 ${names} 等 ${box.except_total} 个）` : tx`（除 ${names}）`;
      return `${box.title || titled(tree, box.node)}${kinds}${except}`;
    };
    const kindLabel = kind => t(AUTH_KINDS[kind] || kind || "中性");
    const count = (part = {}) => tx`${part.triples ?? 0} 项（${part.principals ?? 0} 个主体、${part.resources ?? 0} 个资源）`;
    const groups = report.groups?.items || [];
    // A group of a moved instruction element (thinking §14): the element moved between A class nodes, and a fixed
    // slot possibly between R nodes; the principals gain or lose just that element.
    // A release can also turn a fixed slot into one judging the object its argument names (resource "").
    const place = id => id ? titled("R", id) : t("参数所指对象");
    const moved = item => {
      const gaining = Boolean(item.gained?.length);
      const resource = item.resource && item.resource.before !== item.resource.after
        ? tx`；R：${place(item.resource.before)} → ${place(item.resource.after)}` : "";
      const a = item.a?.before === item.a?.after ? tx`${item.element} 仍在 ${titled("A", item.a?.after)} 下`
        : tx`${item.element} 从 ${titled("A", item.a?.before)} 改到 ${titled("A", item.a?.after)}`;
      return `${a}${gaining ? t("（得到）") : t("（失去）")}${resource}`;
    };
    const remaps = report.remapped?.items || [];
    // One line per A element, aligned by name across the two sides: only what changed, nodes by title.
    // The declarations are the same for every scheme, so mostly a and a fixed slot's resource change.
    const elementLines = item => {
      const before = new Map((item.before || []).map(slot => [slot.element, slot]));
      const after = new Map((item.after || []).map(slot => [slot.element, slot]));
      const lines = [];
      for (const [element, now] of after) {
        const was = before.get(element);
        if (!was) { lines.push(tx`新增元素 ${slotSummary(now, doc)}`); continue; }
        const head = element === item.instruction ? "" : `${element}：`;
        const changes = [];
        if (declaredText(was) !== declaredText(now)) changes.push(tx`声明：${declaredText(was)} → ${declaredText(now)}`);
        if (was.a !== now.a) changes.push(tx`A：${titled("A", was.a)} → ${titled("A", now.a)}`);
        // An argument slot has no resource: its R is the object the argument names.
        if ((was.resource || "") !== (now.resource || "")) changes.push(tx`R：${place(was.resource)} → ${place(now.resource)}`);
        if (changes.length) lines.push(`${head}${changes.join(t("；"))}`);
      }
      for (const [element, was] of before) if (!after.has(element)) lines.push(tx`去掉的元素 ${slotSummary(was, doc)}`);
      return lines;
    };
    return {
      kind: report.kind || "neutral", kindLabel: kindLabel(report.kind),
      head: report.groups?.total || report.complete === false
        ? tx`授权差：${kindLabel(report.kind)}。获得 ${count(report.gained)}，失去 ${count(report.lost)}；按动作分类计。`
        : remaps.length ? t("实例中的授权不变；下列指令的归类改变。") : t("授权不变。"),
      incomplete: report.complete === false ? t("未算完：计算超过 2 秒的上限，以上计数是下限。") : "",
      groups: groups.map(item => ({kind: item.kind, kindLabel: kindLabel(item.kind), element: item.element || "",
        text: `${tx`${side("P", item.p)} 在 ${side("R", item.r)} 上：`}${item.element ? moved(item) : tx`${levels(item.before)} → ${levels(item.after)}`}${tx`，${item.principals ?? 0} 个主体 × ${item.resources ?? 0} 个资源`}`})),
      more: (report.groups?.total || 0) > groups.length ? tx`共 ${report.groups.total} 组，仅列出前 ${groups.length} 组。` : "",
      remapped: remaps.map(item => ({instruction: item.instruction, lines: elementLines(item)})),
      remappedMore: (report.remapped?.total || 0) > remaps.length ? tx`共 ${report.remapped.total} 条指令，仅列出前 ${remaps.length} 条。` : "",
    };
  }
  function authDiffHtml(report, doc) {
    const parts = authDiffParts(report, doc);
    if (!parts) return "";
    const groups = parts.groups.map(item => `<li class="permSchemeAuthGroup is-${esc(item.kind)}" data-auth-group="${esc(item.kind)}"><span class="permSchemeBadge is-${esc(item.kind)}">${esc(item.kindLabel)}</span> ${esc(item.text)}</li>`).join("");
    const remapped = parts.remapped.map(item => `<li data-auth-remap="${esc(item.instruction)}"><code>${esc(item.instruction)}</code>
      <ul>${item.lines.map(line => `<li>${esc(line)}</li>`).join("")}</ul></li>`).join("");
    return `<div class="permSchemeAuthDiff" data-auth-diff data-kind="${esc(parts.kind)}">
      <p><span class="permSchemeBadge is-${esc(parts.kind)}">${esc(parts.kindLabel)}</span> ${esc(parts.head)}</p>
      ${parts.incomplete ? `<p class="permSchemeWarning" data-auth-incomplete>${esc(parts.incomplete)}</p>` : ""}
      ${groups ? `<ul class="permSchemeAuthGroups" data-auth-groups>${groups}</ul>` : ""}
      ${parts.more ? `<p class="permSchemeMuted" data-auth-more>${esc(parts.more)}</p>` : ""}
      ${remapped ? `<details class="permSchemeDiffGroup" data-auth-remapped open><summary>${esc(tx`归类改变的指令：${report.remapped.total} 条`)}</summary><ul>${remapped}</ul>${parts.remappedMore ? `<p class="permSchemeMuted">${esc(parts.remappedMore)}</p>` : ""}</details>` : ""}
    </div>`;
  }

  // The scheme's free A node: an entry rule lets every principal do it on the whole R tree
  // (default scheme: @principals × @free × @portal; a root entry on the A root makes that root free).
  // Read from the entries, so the editor fixes no node ID.
  function freeNode(doc) {
    const a = doc.trees.A;
    const classes = new Set(a.nodes.filter(node => typesOf(node) === null).map(node => node.id));
    const found = (doc.entries || []).filter(entry => entry.effect === "permit"
      && entry.p === doc.trees.P.root && entry.r === doc.trees.R.root && classes.has(entry.a)).map(entry => entry.a);
    return found.find(id => id !== a.root) || found[0] || "";
  }
  // A free element is classified on the free node; a free fixed slot asks about the R root, which the free entry covers.
  const isFree = (classified, node) => Boolean(node) && classified?.a === node;
  const freeClassification = (doc, node, fixed) => fixed ? {a: node, r: doc.trees.R.root} : {a: node};

  // Who passes an unclassified fixed slot: it asks for its element under the A default position on the
  // R default position, so the permits covering both decide. Labels of their P sides. Relation rules expand
  // over R each nodes, which never cover the R default position (a class node). An unclassified argument
  // slot still judges the objects its argument names, so who passes it depends on where they sit.
  function unmappedCallers(doc, pLabel) {
    const a = indexTree(doc, "A");
    const r = indexTree(doc, "R");
    const permits = (doc.entries || []).filter(entry => entry.effect === "permit" && !isRelationRule(entry)
      && a.within(a.tree.default_parent, entry.a) && r.within(r.tree.default_parent, entry.r));
    if (permits.some(entry => entry.p === doc.trees.P.root)) return null;
    return [...new Set(permits.map(entry => pLabel(entry.p)))];
  }

  // The instance view's own state: filters, the page of entries shown, and the row being edited or added.
  function freshInstanceState() {
    return {
      // cursor reads the page shown; cursors are the pages before it, for 上一页; offset only numbers the rows.
      loaded: false, loading: false, busy: false, error: "", total: 0, offset: 0, limit: 50, entries: [],
      cursor: "", cursors: [], next: "",
      filters: {p: "", a: "", r: "", p_scope: "exact", a_scope: "exact", r_scope: "exact", effect: "", disabled: "", temporary: ""},
      selected: new Set(), editing: "", edit: null, adding: {effect: "permit", p: "", a: "", r: ""}, serial: 0,
    };
  }
  // Temporary credentials live in the table but are revoked, not edited.
  const isTemporary = entry => Boolean(entry.expires_at || entry.issued_by || entry.message_id);
  const SCHEME_WRITES = ["UpdatePermissionScheme", "CreatePermissionScheme", "DeletePermissionScheme", "ActivatePermissionScheme"];

  // ---- conflicts left by the automatic merge of the source's changes (§5.11) ------------
  // A copy of a built-in scheme takes the source's changes when Portal starts, unit by unit (the server's
  // permission_system/scheme/merge.py): a tree's settings, a tree node, an entry rule, an A element's
  // classification and each other rules field. A unit both sides changed keeps the copy's version and is listed as a conflict.
  // The scheme ID and the name belong to the copy and are never merged.
  const canonical = value => JSON.stringify(value, (key, item) => item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(name => [name, item[name]])) : item);
  const same = (left, right) => canonical(left) === canonical(right);
  const OWN_FIELDS = ["scheme_id", "title", "trees", "entries", "instructions"];

  function mergeUnits(doc) {
    const units = new Map();
    for (const [key, value] of Object.entries(doc || {})) if (!OWN_FIELDS.includes(key)) units.set(`field|${key}`, value);
    for (const [name, tree] of Object.entries(doc?.trees || {})) {
      const {nodes, ...settings} = tree || {};
      units.set(`tree|${name}`, settings);
      for (const node of nodes || []) units.set(`node|${name}|${node.id}`, node);
    }
    (doc?.entries || []).forEach((entry, position) => units.set(`entry|${entryId(entry, position)}`, entry));
    for (const [element, classified] of Object.entries(doc?.instructions || {})) units.set(`instruction|${element}`, classified);
    return units;
  }

  // A person merging the source's changes compares the same units: those the source changed since its recorded
  // rules (was → now) that this scheme does not already match. A conflict is a unit this scheme changed too, differently.
  function sourceChanges(recorded, current, mine) {
    const [was, now, own] = [mergeUnits(recorded), mergeUnits(current), mergeUnits(mine)];
    return [...new Set([...now.keys(), ...was.keys()])].flatMap(key => {
      const change = {key, was: was.get(key), now: now.get(key), own: own.get(key)};
      if (same(change.was, change.now) || same(change.own, change.now)) return [];
      return [{...change, conflict: !same(change.own, change.was)}];
    });
  }

  // Puts the source's version of an item into a list; a new item goes after the nearest earlier item of the source's list.
  function putInList(list, keyOf, key, value, order) {
    const at = list.findIndex((item, position) => keyOf(item, position) === key);
    if (value === undefined) { if (at >= 0) list.splice(at, 1); return; }
    if (at >= 0) { list[at] = value; return; }
    const position = order.findIndex((item, index) => keyOf(item, index) === key);
    for (let index = position - 1; index >= 0; index -= 1) {
      const before = list.findIndex((item, place) => keyOf(item, place) === keyOf(order[index], index));
      if (before >= 0) { list.splice(before + 1, 0, value); return; }
    }
    list.unshift(value);
  }

  // Applies one conflict's source side to a scheme document: the unit becomes the source's current version.
  function applyChange(doc, change, current) {
    const [kind, name, id] = change.key.split("|");
    const now = change.now === undefined ? undefined : clone(change.now);
    if (kind === "field") {
      if (now === undefined) delete doc[name]; else doc[name] = now;
    } else if (kind === "tree") {
      doc.trees ||= {};
      if (now === undefined) delete doc.trees[name];
      else doc.trees[name] = {...now, nodes: doc.trees[name]?.nodes || []};
    } else if (kind === "node") {
      doc.trees ||= {};
      doc.trees[name] ||= {nodes: []};
      doc.trees[name].nodes ||= [];
      putInList(doc.trees[name].nodes, node => node.id, id, now, current.trees?.[name]?.nodes || []);
    } else if (kind === "entry") {
      doc.entries ||= [];
      putInList(doc.entries, entryId, name, now, current.entries || []);
    } else if (kind === "instruction") {
      doc.instructions ||= {};
      if (now === undefined) delete doc.instructions[name]; else doc.instructions[name] = now;
    }
  }

  function mount({container, onSystemChange}) {
    let systemRevision = 0;
    const state = {
      loading: true, busy: false, destroyed: false, notice: "", noticeTone: "",
      system: null, schemes: [], vocabulary: {}, current: null, base: "", draft: null,
      view: "trees", tree: "R", selected: "", expanded: new Set(),
      filter: "", show: "all", editing: new Set(), highlightEntry: -1,
      serverErrors: [], compiling: false, unmapped: null,
      // The dry_run difference shown before changed rules are saved: {rules, diff, merge}; a merge adds
      // the rows it cannot write back (unrestored) and the rows left deleted (deleted_again).
      pendingSave: null,
      // pendingActivate: {scheme_id, preview, on}, the activation's dry_run waiting for confirmation.
      pendingActivate: null,
      // source: the copy's source record (GetPermissionScheme.source), with last_merge after a merge.
      // review: the last merge's conflicts and unrestored rows are shown.
      // merge: the source's changes a person is merging, {digest, rules, changes, picks}.
      // merged: a merge applied to the draft, saved with its source digest: {digest, rules}.
      // ownMerge: a built-in's last release write-back (GetPermissionScheme.last_merge).
      source: null, ownMerge: null, review: false, merge: null, merged: null,
      // stale: a save met a newer version; discarding loads the scheme again instead of the rules read before.
      stale: false,
      instance: freshInstanceState(),
      checking: false, checkInput: {principal: "", instruction: "", arguments: {}}, checkResult: null,
    };
    // Titles of instance nodes seen in searches and listings, by tree and ID.
    const nodeTitles = {P: new Map(), A: new Map(), R: new Map()};
    let drag = null;
    let compileTimer = 0;
    let compileSerial = 0;
    const root = document.createElement("div");
    root.className = "permSchemePage";
    // The page lays itself out by the width of its host (a dialog is narrower than the window).
    container.classList.add("permSchemeHost");
    container.replaceChildren(root);

    const dirty = () => Boolean(state.draft) && JSON.stringify(state.draft) !== state.base;
    // With the permission system off the schemes stay editable; the active one is the scheme used when it is turned on.
    const enabled = () => state.system?.enabled === true;
    // Each button asks for the one instruction it runs (GetPermissionSystem.allows).
    const may = name => state.system?.allows?.[name] === true;
    const activeLabel = () => typeof state.system?.enabled !== "boolean" ? t("已选择") : enabled() ? t("启用中") : t("开启时使用");
    const editable = () => may("UpdatePermissionScheme") && !state.busy && Boolean(state.current) && !state.current.builtin;
    const instructionNames = () => (state.vocabulary.instructions || []).map(item => item.name);
    const instructionInfo = name => (state.vocabulary.instructions || []).find(item => item.name === name);
    // The A elements instructions declare (vocabulary.instructions[].elements): element -> {instruction, slot}.
    let elementCache = {vocabulary: null, map: new Map()};
    const elementMap = () => {
      if (elementCache.vocabulary !== state.vocabulary) {
        const map = new Map();
        for (const info of state.vocabulary.instructions || []) for (const slot of info.elements || []) map.set(slot.element, {instruction: info.name, slot});
        elementCache = {vocabulary: state.vocabulary, map};
      }
      return elementCache.map;
    };
    const elementsOf = name => instructionInfo(name)?.elements || [];
    const isFixed = element => Boolean(elementMap().get(element)?.slot.fixed);
    // The server lists the A elements a scheme does not classify (GetPermissionScheme and each dry_run);
    // until it answers, the editor counts them from the vocabulary.
    const unmappedNames = () => state.unmapped || [...elementMap().keys()].filter(element => !state.draft.instructions?.[element]);
    const principalLabel = value => state.draft.trees.P.nodes.find(node => node.id === value)?.title || value;
    const relationTitle = relation => t((state.vocabulary.relations || []).find(item => item.relation === relation)?.title || relation);
    // Who passes an unclassified element: a fixed slot on the A and R default positions, an argument slot
    // on the A default position over the objects its argument names.
    const unmappedText = (fixed = true) => {
      const where = state.draft.trees.A.default_parent;
      if (!fixed) return tx`未归类：按 A 默认位置 ${where} 判定参数指向的对象，放行取决于对象在 R 树中的位置`;
      const callers = unmappedCallers(state.draft, principalLabel);
      if (callers === null) return t("未归类：所有主体都能调用");
      return callers.length ? tx`未归类：只有${callers.join("、")}能调用` : t("未归类：没有表项放行，任何人都不能调用");
    };
    const setNotice = (message, tone = "") => { state.notice = message; state.noticeTone = tone; };

    // ---- loading ------------------------------------------------------------------
    async function load(selectId = "") {
      const revision = systemRevision;
      state.loading = true;
      render();
      try {
        const [system, list] = await Promise.all([run("GetPermissionSystem"), run("ListPermissionSchemes")]);
        if (state.destroyed) return;
        state.system = revision === systemRevision ? system : {...system, enabled: state.system?.enabled};
        onSystemChange?.(state.system.enabled);
        state.schemes = list.schemes || [];
        if (!Array.isArray(list.schemes)) setNotice(t("服务端没有返回方案列表。"), "error");
        const target = selectId || state.current?.scheme_id || system.scheme?.scheme_id || state.schemes[0]?.scheme_id;
        state.loading = false;
        if (target) await open(target);
        else render();
      } catch (error) {
        if (state.destroyed) return;
        state.loading = false;
        if (error?.details?.permission_system === "open") state.system = {enabled: false};
        setNotice(tx`读取权限方案失败：${error.message}`, "error");
        render();
      }
    }

    // Refresh only runtime state so a tab revisit never discards a rules draft.
    async function refreshSystem() {
      const revision = systemRevision;
      try {
        const system = await run("GetPermissionSystem");
        if (state.destroyed || revision !== systemRevision) return;
        state.system = system;
        onSystemChange?.(system.enabled);
        render();
      } catch (error) {
        if (!state.destroyed) { setNotice(error.message, "error"); render(); }
      }
    }

    async function open(schemeId) {
      const result = await run("GetPermissionScheme", {scheme_id: schemeId, vocabulary: true});
      if (state.destroyed) return;
      // A compile scheduled for the previous draft must not run against this one.
      clearTimeout(compileTimer);
      compileSerial += 1;
      state.current = {scheme_id: result.scheme_id || schemeId, version: result.version ?? null, builtin: Boolean(result.builtin), active: Boolean(result.active)};
      state.vocabulary = result.vocabulary || state.vocabulary;
      state.draft = clone(result.rules);
      state.base = JSON.stringify(state.draft);
      state.selected = "";
      state.serverErrors = [];
      state.pendingSave = null;
      state.pendingActivate = null;
      state.source = result.source || null;
      state.ownMerge = result.builtin ? result.last_merge || null : null;
      state.review = false;
      state.merge = null;
      state.merged = null;
      state.unmapped = result.unmapped || null;
      state.stale = false;
      state.checkResult = null;
      state.instance = freshInstanceState();
      for (const titles of Object.values(nodeTitles)) titles.clear();
      // Saved rules need no check; only a draft with changes is compiled.
      scheduleCompile();
      // The instance view loads its page when it is drawn.
      render();
    }

    async function confirmDiscard() {
      if (!dirty()) return true;
      return window.PortalUI.confirm({title: t("放弃未保存的修改？"), message: t("当前方案规则的修改尚未保存。"), confirmLabel: t("放弃修改"), tone: "danger"});
    }

    async function busy(callback) {
      if (state.busy || state.destroyed) return;
      state.busy = true;
      render();
      try { await callback(); }
      catch (error) { if (!state.destroyed) setNotice(error.message || String(error), "error"); }
      finally {
        state.busy = false;
        if (!state.destroyed) render();
      }
    }

    // ---- editing --------------------------------------------------------------------
    function changed({rerender = true} = {}) {
      state.unmapped = null;
      state.pendingSave = null;
      scheduleCompile();
      if (rerender) render();
      else renderStatus();
    }

    // Two rules dry_runs: checkRules only compiles (errors, unmapped instructions, whether saving
    // regenerates the instance) and is quick; previewDiff also generates the instance from every
    // object to list the difference, which takes seconds, so it runs only when saving.
    const DIFF_LIMIT = 50;
    // A merge's dry_runs carry its source digest: the server refuses a digest the source has moved past and
    // reports what the merge writes back to the instance.
    const dryRun = (rules, extra) => run("UpdatePermissionScheme", {
      scheme_id: state.current.scheme_id, rules, dry_run: true, ...extra});
    const checkRules = (rules, extra = {}) => dryRun(rules, {diff: false, ...extra});
    const previewDiff = (rules, extra = {}) => dryRun(rules, {diff_limit: DIFF_LIMIT, ...extra});

    // The server's compiler is the only judge of a draft: its errors decide whether it can be saved.
    // The last check is kept with the rules it answered for, so saving them need not compile again.
    let lastCheck = null;
    function scheduleCompile() {
      clearTimeout(compileTimer);
      state.compiling = false;
      if (!may("UpdatePermissionScheme") || !state.current || state.current.builtin || !dirty()) {
        if (!dirty()) state.serverErrors = [];
        return;
      }
      const serial = ++compileSerial;
      state.compiling = true;
      compileTimer = setTimeout(async () => {
        if (state.destroyed || serial !== compileSerial) return;
        const rules = clone(state.draft);
        try {
          const result = await checkRules(rules);
          if (state.destroyed || serial !== compileSerial) return;
          state.serverErrors = result.valid === false ? (result.errors || []) : [];
          state.unmapped = result.unmapped || null;
          lastCheck = {key: JSON.stringify(rules), result};
        } catch (error) {
          if (state.destroyed || serial !== compileSerial) return;
          state.serverErrors = error.details?.errors || (error.code === "instruction.conflict" ? [] : [{path: "rules", message: error.message}]);
        }
        state.compiling = false;
        renderStatus();
      }, 600);
    }

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

    // ---- rendering ------------------------------------------------------------------
    function render() {
      if (state.destroyed) return;
      const active = root.contains(document.activeElement) ? document.activeElement : null;
      const focus = active?.dataset.focus || "";
      const caret = typeof active?.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
      const scrolls = [...root.querySelectorAll("[data-scroll]")].map(item => [item.dataset.scroll, item.scrollTop, item.scrollLeft]);
      root.innerHTML = listHtml() + editorHtml();
      for (const [key, top, left] of scrolls) {
        const item = root.querySelector(`[data-scroll="${key}"]`);
        if (item) { item.scrollTop = top; item.scrollLeft = left; }
      }
      const restored = focus ? root.querySelector(`[data-focus="${CSS.escape(focus)}"]`) : null;
      if (restored) {
        restored.focus();
        if (caret && typeof restored.setSelectionRange === "function") restored.setSelectionRange(...caret);
      }
    }

    // Redraws only what typing changes, so the focused input keeps its caret.
    function renderStatus() {
      if (state.destroyed) return;
      root.querySelector("[data-editor-actions]")?.replaceWith(fragment(actionsHtml()));
      root.querySelector("[data-validation]")?.replaceWith(fragment(validationHtml()));
      // The server's answer arrives after the entries were drawn; mark the ones it names.
      root.querySelectorAll("[data-entry-card]").forEach(card => card.classList.toggle("is-invalid", entryInvalid(Number(card.dataset.entryCard))));
    }

    const entryInvalid = position => state.serverErrors.some(item => item.path.startsWith(`entries[${position}]`));

    const fragment = html => document.createRange().createContextualFragment(html);

    function listHtml() {
      const system = state.system;
      const title = system?.scheme?.title || system?.scheme?.scheme_id || "";
      const status = !system ? "" : typeof system.enabled !== "boolean"
        ? `<p class="permSchemeMuted" data-system-status>${esc(t("权限系统状态未读取"))}</p>` : !enabled()
        ? `<p class="permSchemeMuted" data-system-status>${esc(t("权限系统已关闭：方案暂不参与判定，规则和实例可以照常查看和编辑，开启权限系统后生效。"))}${title ? ` ${esc(tx`开启时使用：${title}`)}` : ""}</p>`
        : `<p class="permSchemeMuted" data-system-status>${esc(tx`当前启用：${title}`)}</p>`;
      const items = state.schemes.map(item => {
        const selected = item.scheme_id === state.current?.scheme_id;
        const badges = [item.builtin ? `<span class="permSchemeBadge">${esc(t("内置"))}</span>` : "",
          item.active ? `<span class="permSchemeBadge is-active">${esc(activeLabel())}</span>` : "",
          item.outdated ? `<span class="permSchemeBadge is-outdated" data-outdated>${esc(t("来源有新修改"))}</span>` : ""].join("");
        const count = Number.isFinite(item.entry_count) ? ` · ${esc(tx`实例 ${item.entry_count} 条表项`)}` : "";
        // A built-in's rules change only through a copy, so its card offers one.
        const copy = item.builtin ? `<button type="button" class="portalButton is-compact permSchemeListCopy" data-copy-scheme="${esc(item.scheme_id)}"
          ${may("CreatePermissionScheme") && !state.busy ? "" : "disabled"}>${esc(t("复制"))}</button>` : "";
        return `<div class="permSchemeListCard"><button type="button" class="permSchemeListItem" data-open-scheme="${esc(item.scheme_id)}" aria-current="${selected}">
          <span class="permSchemeListTitle"><strong>${esc(item.title || item.scheme_id)}</strong>${badges}</span>
          <small>${esc(item.description || "")}${count}</small></button>${copy}</div>`;
      }).join("");
      return `<aside class="permSchemeList"><h2>${esc(t("权限方案"))}</h2>${status}
        <p class="permSchemeHelp">${esc(t("一套权限方案由规则和实例组成：规则规定 Portal 的主体、动作和资源怎样放进三棵权限树、新对象带来哪些表项；实例是按规则生成的树和权限表，判定只查实例，可以逐行编辑。内置方案的规则只读，要改规则先复制为新方案；内置方案本身可以直接启用，实例可以直接编辑。"))}</p>
        <div class="permSchemeListItems">${items}</div></aside>`;
    }

    function editorHtml() {
      const notice = state.notice ? `<p class="permSchemeNotice" data-tone="${esc(state.noticeTone)}" role="status">${esc(state.notice)}</p>` : "";
      if (state.loading) return `<section class="permSchemeEditor">${notice}<p role="status">${esc(t("正在读取权限方案…"))}</p></section>`;
      if (!state.draft) return `<section class="permSchemeEditor">${notice}</section>`;
      const doc = state.draft;
      const locked = editable() ? "" : "disabled";
      const tabs = [["trees", "三棵树"], ["entries", "表规则"], ["instructions", "指令映射"], ["instance", "实例表"], ["check", "试判定"]]
        .map(([view, label]) => `<button type="button" role="tab" class="portalButton is-compact${state.view === view ? " is-primary" : ""}" aria-selected="${state.view === view}" data-view="${view}">${esc(t(label))}</button>`).join("");
      const body = {trees: treesHtml, entries: entriesHtml, instructions: instructionsHtml, instance: instanceHtml, check: checkHtml}[state.view]();
      return `<section class="permSchemeEditor">
        ${notice}
        <header class="permSchemeHeader">
          <div class="permSchemeTitleFields">
            <label class="portalField"><span>${esc(t("方案名称"))}</span><input data-doc-field="title" data-focus="title" value="${esc(doc.title || "")}" ${locked} /></label>
            <p class="permSchemeMuted">${esc(t("方案 ID"))} <code>${esc(state.current.scheme_id || "")}</code></p>
            <label class="portalField"><span>${esc(t("说明"))}</span><textarea data-doc-field="description" data-focus="description" rows="2" ${locked}>${esc(doc.description || "")}</textarea></label>
          </div>
          ${actionsHtml()}
        </header>
        ${validationHtml()}
        ${pendingSaveHtml()}
        ${pendingActivateHtml()}
        ${mergeHtml()}
        ${lastMergeHtml()}
        <div class="permSchemeTabs" role="tablist">${tabs}</div>
        <div class="permSchemeBody">${body}</div>
      </section>`;
    }

    function actionsHtml() {
      const current = state.current;
      const badges = [
        current.builtin ? `<span class="permSchemeBadge">${esc(t("内置，规则只读"))}</span>` : "",
        current.active ? `<span class="permSchemeBadge is-active">${esc(activeLabel())}</span>` : "",
        dirty() ? `<span class="permSchemeBadge is-draft">${esc(t("规则有未保存的修改"))}</span>` : "",
        state.merged ? `<span class="permSchemeBadge is-draft" data-merged>${esc(t("已合并来源的修改，保存后来源记录更新"))}</span>` : "",
        SCHEME_WRITES.some(may) ? "" : `<span class="permSchemeMuted">${esc(t("你只能查看方案。"))}</span>`,
        // A built-in's rules follow the code and are read-only; the scheme is activated and its instance edited
        // as it is (b820 §5.11).
        current.builtin ? `<span class="permSchemeMuted" data-builtin-note>${esc(t("内置方案的规则跟随代码发布，只读；要改规则先复制。实例可以直接编辑。"))}</span>` : "",
        current.builtin ? releaseNoteHtml() : sourceNoteHtml(),
      ].join("");
      const allowed = name => may(name) && !state.busy;
      const clean = !state.serverErrors.length;
      const buttons = [];
      // A merge is saved even when the draft already matched the source: saving moves the source record.
      const changes = dirty() || Boolean(state.merged);
      if (!current.builtin) {
        buttons.push(`<button type="button" class="portalButton is-primary is-compact" data-action="save" ${allowed("UpdatePermissionScheme") && changes && clean && !state.pendingSave ? "" : "disabled"}>${esc(t("保存规则"))}</button>`);
        buttons.push(`<button type="button" class="portalButton is-compact" data-action="discard" ${!state.busy && changes ? "" : "disabled"}>${esc(t("放弃修改"))}</button>`);
        // Merging starts from the saved rules: the source's changes are compared with what this scheme saved.
        if (state.source?.outdated && state.source.current_digest) {
          const ready = allowed("UpdatePermissionScheme") && !changes && !state.merge && !state.pendingSave;
          buttons.push(`<button type="button" class="portalButton is-compact" data-action="merge" ${ready ? "" : "disabled"}${!changes ? "" : ` data-portal-tooltip="${esc(t("先保存或放弃当前修改"))}"`}>${esc(t("合并来源的修改"))}</button>`);
        }
      }
      buttons.push(`<button type="button" class="portalButton is-compact" data-action="copy" ${allowed("CreatePermissionScheme") && !dirty() ? "" : "disabled"}>${esc(t("复制为新方案"))}</button>`);
      if (!current.active) {
        const label = enabled() ? (dirty() ? t("保存后才能启用") : t("启用")) : (dirty() ? t("保存后才能设为开启时使用") : t("设为开启时使用"));
        buttons.push(`<button type="button" class="portalButton is-compact" data-action="activate" ${allowed("ActivatePermissionScheme") && !dirty() && !state.pendingActivate ? "" : "disabled"}>${esc(label)}</button>`);
      }
      if (!current.builtin && !current.active) buttons.push(`<button type="button" class="portalButton is-compact is-danger" data-action="delete" ${allowed("DeletePermissionScheme") ? "" : "disabled"}>${esc(t("删除方案"))}</button>`);
      return `<div class="permSchemeActions" data-editor-actions><div class="permSchemeBadges">${badges}</div><div class="permSchemeButtons">${buttons.join("")}</div></div>`;
    }

    const schemeTitle = schemeId => state.schemes.find(item => item.scheme_id === schemeId)?.title || schemeId;

    // Where a copy came from, whether the source changed since it was copied or last merged,
    // and what the last merge could not take: conflicts, rows not written back and rows left deleted.
    function sourceNoteHtml() {
      const source = state.source;
      if (!source?.scheme_id || state.current.builtin) return "";
      const status = !source.current_digest ? t("来源方案已删除")
        : source.outdated ? t("来源有新修改") : t("已包含来源的全部修改");
      const merge = source.last_merge;
      const counts = mergeCounts(merge);
      const label = automatic(merge) ? tx`上次自动合并有 ${counts.join(t("、"))}` : tx`上次合并有 ${counts.join(t("、"))}`;
      return `<span class="permSchemeMuted" data-source-note>${esc(tx`来源：${schemeTitle(source.scheme_id)}`)}</span>
        <span class="permSchemeBadge${source.outdated ? " is-outdated" : ""}" data-source-status>${esc(status)}</span>
        ${counts.length ? `<button type="button" class="portalButton is-compact" data-action="last-merge" data-last-merge-open aria-expanded="${state.review}">${esc(label)}</button>` : ""}`;
    }

    // What a merge or a release write-back left for people to look at.
    const mergeCounts = merge => [[merge?.conflicts?.length, count => tx`${count} 项冲突`], [merge?.unrestored?.length, count => tx`${count} 条表项未写回`],
      [merge?.deleted_again?.length, count => tx`${count} 行因你删过而没加回`],
      // A merge that changed who may do what opens the panel for its 授权差 too.
      [(merge?.auth_diff?.groups?.total || merge?.auth_diff?.remapped?.total) ? 1 : 0, () => tx`授权差：${authDiffParts(merge.auth_diff).kindLabel}`],
    ].filter(([count]) => count).map(([count, text]) => text(count));

    // A release that changed a built-in's rules regenerated its instance and wrote the instance's edits back
    // (GetPermissionScheme.last_merge, shaped like a copy's source.last_merge). One that changed only the
    // instruction classification left the instance alone (regenerated: false): only A elements moved.
    const followedOnly = merge => merge?.regenerated === false;
    function releaseNoteHtml() {
      const counts = mergeCounts(state.ownMerge);
      const label = followedOnly(state.ownMerge) ? tx`上次跟随发布有 ${counts.join(t("、"))}` : tx`上次随发布重新生成有 ${counts.join(t("、"))}`;
      return counts.length ? `<button type="button" class="portalButton is-compact" data-action="last-merge" data-last-merge-open aria-expanded="${state.review}">${esc(label)}</button>` : "";
    }
    // The last merge of a copy, or the last release write-back of a built-in.
    const lastMerge = () => state.current?.builtin ? state.ownMerge : state.source?.last_merge;

    // One unit of the rules, named for people.
    function changeLabel(change) {
      const [kind, name, id] = change.key.split("|");
      const value = change.now ?? change.own ?? change.was ?? {};
      const titled = (title, key) => title && title !== key ? `${title} (${key})` : key;
      if (kind === "field") return name === "description" ? t("说明") : tx`规则字段 ${name}`;
      if (kind === "tree") return tx`${t(TREE_TITLES[name] || name)}：根与默认位置`;
      if (kind === "node") return tx`${t(TREE_TITLES[name] || name)}节点 ${titled(value.title, id)}`;
      if (kind === "entry") return tx`表规则 ${titled(value.title, name)}`;
      return tx`A 元素 ${name} 的归类`;
    }

    // missing lists the trees whose node a row names is gone.
    const missingText = missing => {
      const trees = [].concat(missing || []).map(tree => t(TREE_TITLES[tree] || tree));
      return trees.length ? tx`${trees.join(t("、"))} 中的节点已不存在` : "";
    };
    // A row the instance's edits added or disabled, or a temporary credential, that a merge did not write back,
    // and a row it left deleted.
    function unrestoredHtml(row) {
      const temporary = isTemporary(row) ? [t("临时凭证"), row.issued_by ? tx`签发者 ${row.issued_by}` : "", row.expires_at ? tx`到期 ${row.expires_at}` : ""] : [];
      return `<li class="permSchemeMergeItem" data-unrestored="${esc(row.entry_id || "")}">
        <div class="permSchemeMergeHead"><code>${esc(row.entry_id || "")}</code>
          <span>${esc([row.effect, row.p, row.a, row.r].join(" · "))}</span>
          ${row.disabled ? `<span class="permSchemeBadge">${esc(t("已停用"))}</span>` : ""}
          <small class="permSchemeMuted">${esc([missingText(row.missing), ...temporary].filter(Boolean).join(" · "))}</small></div></li>`;
    }
    const deletedAgainHtml = row => `<li class="permSchemeMergeItem" data-deleted-again>
        <div class="permSchemeMergeHead"><span>${esc([row.effect, row.p, row.a, row.r].join(" · "))}</span></div></li>`;
    const showUnit = value => value === undefined ? t("（没有）") : JSON.stringify(value, null, 2);

    // The source's changes since the recorded rules, one item each; conflicts wait for a choice.
    function mergeHtml() {
      const merge = state.merge;
      if (!merge) return "";
      const sideHtml = (label, value) => `<div><small>${esc(t(label))}</small><pre>${esc(showUnit(value))}</pre></div>`;
      const items = merge.changes.map((change, index) => {
        const pick = merge.picks.get(change.key);
        const verb = change.was === undefined ? t("来源新增") : change.now === undefined ? t("来源删除") : t("来源修改");
        const label = esc(changeLabel(change));
        const key = esc(change.key);
        const head = change.conflict
          ? `<strong>${label}</strong><span class="permSchemeBadge">${esc(verb)}</span><span class="permSchemeBadge is-conflict">${esc(t("冲突"))}</span>
            <span class="permSchemeMuted">${esc(change.own === undefined ? t("本方案删除了这一项") : change.was === undefined ? t("本方案也新增了这一项，内容不同") : t("本方案也修改了这一项"))}</span>
            <span class="permSchemeMergeChoice" role="radiogroup" aria-label="${label}">
              <label><input type="radio" name="merge-pick-${index}" data-merge-pick="${key}" value="source" ${pick === "source" ? "checked" : ""} /> ${esc(t("采用来源的"))}</label>
              <label><input type="radio" name="merge-pick-${index}" data-merge-pick="${key}" value="own" ${pick === "own" ? "checked" : ""} /> ${esc(t("保留本方案的"))}</label></span>`
          : `<label class="permSchemeCheckbox"><input type="checkbox" data-merge-pick="${key}" ${pick === "source" ? "checked" : ""} /> ${label}</label><span class="permSchemeBadge">${esc(verb)}</span>`;
        return `<li class="permSchemeMergeItem${change.conflict ? " is-conflict" : ""}" data-merge-item="${key}">
          <div class="permSchemeMergeHead">${head}</div>
          <details><summary>${esc(t("查看内容"))}</summary><div class="permSchemeMergeSides">${sideHtml("来源原来", change.was)}${sideHtml("来源现在", change.now)}${change.conflict ? sideHtml("本方案", change.own) : ""}</div></details></li>`;
      }).join("");
      const conflicts = merge.changes.filter(change => change.conflict).length;
      const unresolved = merge.changes.some(change => change.conflict && !merge.picks.get(change.key));
      const summary = !merge.changes.length ? t("来源的修改本方案都已包含；保存后来源记录更新到来源现在的规则。")
        : conflicts ? tx`来源方案 ${schemeTitle(state.source.scheme_id)} 在上次复制或合并之后有 ${merge.changes.length} 项修改，其中 ${conflicts} 项与本方案的修改冲突。`
          : tx`来源方案 ${schemeTitle(state.source.scheme_id)} 在上次复制或合并之后有 ${merge.changes.length} 项修改。`;
      return `<section class="permSchemeMerge" data-merge aria-label="${esc(t("合并来源的修改"))}">
        <p><strong>${esc(summary)}</strong></p>
        ${merge.changes.length ? `<p class="permSchemeMuted">${esc(t("勾选要合并的修改；冲突项是本方案也改过的同一项，需要选择采用哪一边。保存前先检查规则并列出实例的变化：实例按合并后的规则重新生成，实例中的编辑和临时凭证写回：增加和停用的行写回，删掉的行仍删掉。"))}</p>
        <ul class="permSchemeMergeItems">${items}</ul>` : ""}
        <div class="permSchemeButtons">
          <button type="button" class="portalButton is-primary is-compact" data-action="merge-apply" ${state.busy || unresolved || !may("UpdatePermissionScheme") ? "disabled" : ""}>${esc(t("合并并保存"))}</button>
          <button type="button" class="portalButton is-compact" data-action="merge-cancel">${esc(t("取消"))}</button>
          ${unresolved ? `<span class="permSchemeMuted">${esc(t("还有冲突项没有选择。"))}</span>` : ""}
        </div></section>`;
    }

    // A side of a conflict as recorded by the server: null means the side has no such unit.
    const side = value => value ?? undefined;
    // Merges at startup are made by portal; a merge saved with source_digest names the user who saved it.
    const automatic = merge => !merge?.merged_by || merge.merged_by === "portal";

    // The last merge: each conflict kept this scheme's version and can take the source's instead, saved like any
    // edit; rows whose nodes are gone were not written back to the instance; generated rows people had deleted
    // stayed deleted although the new rules give them again.
    function lastMergeHtml() {
      const merge = lastMerge();
      if (!state.review || !merge) return "";
      const units = mergeUnits(state.draft);
      const saved = mergeUnits(JSON.parse(state.base));
      const sideHtml = (label, value) => `<div><small>${esc(t(label))}</small><pre>${esc(showUnit(value))}</pre></div>`;
      const writable = may("UpdatePermissionScheme") && !state.busy && !state.pendingSave;
      const conflicts = (merge.conflicts || []).map(conflict => {
        const [was, now, own] = [side(conflict.was), side(conflict.now), side(conflict.own)];
        const unit = units.get(conflict.key);
        const status = same(unit, now) ? t("已采用来源的") : same(unit, own) ? t("保留本方案的") : t("已另行修改");
        const unsaved = !same(unit, saved.get(conflict.key));
        const key = esc(conflict.key);
        return `<li class="permSchemeMergeItem is-conflict" data-merge-item="${key}">
          <div class="permSchemeMergeHead"><strong>${esc(changeLabel({key: conflict.key, was, now, own}))}</strong>
            <span class="permSchemeBadge" data-merge-status>${esc(status)}</span>
            ${unsaved ? `<span class="permSchemeBadge is-draft">${esc(t("未保存"))}</span>` : ""}
            <span class="permSchemeButtons">
              <button type="button" class="portalButton is-compact" data-merge-take="${key}" data-merge-side="now" ${writable && !same(unit, now) ? "" : "disabled"}>${esc(t("采用来源的"))}</button>
              <button type="button" class="portalButton is-compact" data-merge-take="${key}" data-merge-side="own" ${writable && !same(unit, own) ? "" : "disabled"}>${esc(t("保留本方案的"))}</button>
            </span></div>
          <details><summary>${esc(t("查看内容"))}</summary><div class="permSchemeMergeSides">${sideHtml("来源原来", was)}${sideHtml("来源现在", now)}${sideHtml("本方案", own)}</div></details></li>`;
      }).join("");
      const rows = (merge.unrestored || []).map(unrestoredHtml).join("");
      const deleted = (merge.deleted_again || []).map(deletedAgainHtml).join("");
      const when = merge.merged_at ? new Date(merge.merged_at * 1000).toLocaleString() : "";
      const source = schemeTitle(merge.source_scheme_id || state.source?.scheme_id);
      // A built-in's write-back has no basis when its instance was saved before the rules it came from were kept.
      const head = state.current.builtin
        ? (followedOnly(merge) ? tx`${when} 的发布改变了指令分类：实例的表项不变，移动的 A 元素带来下面的授权变化。`
          : merge.from_digest ? tx`${when} 的发布改变了内置规则：实例已按新规则重新生成，实例中的编辑写回。`
            : tx`${when} 的发布改变了内置规则：实例没有记录生成时的规则，已按新规则重新生成。`)
        : automatic(merge) ? tx`来源方案 ${source} 的修改已在 ${when} 自动合并到本方案。`
          : tx`${merge.merged_by} 在 ${when} 把来源方案 ${source} 的修改合并到本方案。`;
      const label = !state.current.builtin ? t("上次合并") : followedOnly(merge) ? t("上次跟随发布") : t("上次随发布重新生成");
      return `<section class="permSchemeMerge" data-last-merge aria-label="${esc(label)}">
        <p><strong>${esc(head)}</strong></p>
        ${authDiffHtml(merge.auth_diff, JSON.parse(state.base))}
        ${conflicts ? `<p class="permSchemeMuted">${esc(t("冲突项是本方案也改过的同一项，自动合并保留了本方案的版本。采用来源的版本后按普通保存检查规则、确认实例差异。"))}</p>
        <ul class="permSchemeMergeItems" data-merge-conflicts>${conflicts}</ul>` : ""}
        ${rows ? `<p class="permSchemeMuted">${esc(t("实例按新规则重新生成后，这些实例中的编辑或临时凭证引用的节点已不存在，没有写回。需要时在实例表中按新节点重新添加。"))}</p>
        <ul class="permSchemeMergeItems" data-merge-unrestored>${rows}</ul>` : ""}
        ${deleted ? `<p class="permSchemeMuted">${esc(t("因你删过而没加回的行：规则生成过、后来被人删掉的行，新规则仍然给出，合并时保持删除。需要时在实例表中重新添加。"))}</p>
        <ul class="permSchemeMergeItems" data-merge-deleted-again>${deleted}</ul>` : ""}
        <div class="permSchemeButtons">
          ${rows || deleted ? `<button type="button" class="portalButton is-compact" data-view="instance">${esc(t("打开实例表"))}</button>` : ""}
          <button type="button" class="portalButton is-compact" data-action="last-merge">${esc(t("收起"))}</button>
        </div></section>`;
    }

    // Classifications of A elements no instruction declares any more (an instruction removed, or one that gained a
    // second slot and named its elements after them): the server's compiler refuses them when saving.
    const staleMappings = () => !state.vocabulary.instructions ? []
      : Object.keys(state.draft?.instructions || {}).filter(element => !elementMap().has(element));

    // Errors come only from the server's compiler.
    function validationHtml() {
      const errors = state.serverErrors;
      if (!errors.length) {
        const editable = state.current && !state.current.builtin && may("UpdatePermissionScheme");
        const status = !editable ? "" : state.compiling ? t("服务端检查中…") : dirty() ? t("服务端检查通过。") : "";
        const stale = editable ? staleMappings().length : 0;
        const hint = stale ? `<span class="permSchemeWarning" data-stale-mappings>${esc(tx`${stale} 条归类指向指令声明中已没有的 A 元素，保存规则前要在「指令映射」的「已失效」中删除。`)}</span>` : "";
        return `<div class="permSchemeValidation" data-validation data-ok="true">${esc(status)}${status && hint ? " " : ""}${hint}</div>`;
      }
      const shown = errors.slice(0, 12).map(item => `<li><button type="button" class="permSchemeLink" data-goto-error="${esc(item.path)}"><code>${esc(item.path)}</code></button> ${esc(item.message)}</li>`).join("");
      const more = errors.length > 12 ? `<li>${esc(tx`还有 ${errors.length - 12} 项`)}</li>` : "";
      return `<div class="permSchemeValidation" data-validation data-ok="false" role="alert"><strong>${esc(tx`服务端检查发现 ${errors.length} 个问题，处理后才能保存`)}</strong><ul>${shown}${more}</ul></div>`;
    }

    // An instance entry as text: @ IDs are the rules' class nodes (titled from the saved rules), the rest object IDs.
    function instanceNodeLabel(tree, id) {
      const saved = state.base ? JSON.parse(state.base) : state.draft;
      const title = String(id).startsWith("@") ? saved?.trees?.[tree]?.nodes?.find(node => node.id === id)?.title : nodeTitles[tree].get(id);
      return title && title !== id ? `${title} (${id})` : String(id);
    }
    const entryText = entry => `${entry.effect} · P ${instanceNodeLabel("P", entry.p)} · A ${instanceNodeLabel("A", entry.a)} · R ${instanceNodeLabel("R", entry.r)}${entry.disabled ? ` · ${t("已停用")}` : ""}`;

    // Saving changed trees or entry rules regenerates the instance; the dry_run difference is confirmed first.
    function pendingSaveHtml() {
      const pending = state.pendingSave;
      if (!pending) return "";
      const {added, removed, lost_edits: lost} = pending.diff;
      const group = (title, part, line) => {
        if (!part?.total) return "";
        const items = (part.items || []).map(item => `<li>${esc(line(item))}</li>`).join("");
        const more = part.total > (part.items || []).length ? `<li>${esc(tx`还有 ${part.total - (part.items || []).length} 条`)}</li>` : "";
        return `<details class="permSchemeDiffGroup" data-diff="${esc(title)}"><summary>${esc(tx`${t(title)}：${part.total} 条`)}</summary><ul>${items}${more}</ul></details>`;
      };
      const kinds = {added: t("在实例中新增"), deleted: t("在实例中删除"), disabled: t("在实例中停用"), unrestored: t("写不回")};
      const lostLine = item => [`${kinds[item.kind] || item.kind}：${entryText(item.entry)}`, missingText(item.missing)].filter(Boolean).join(" · ");
      // A merge writes back what people did to the instance; what it cannot is listed row by row (unrestored
      // carries the entry IDs and credential details that lost_edits leaves out).
      const rows = (title, list, html, data) => list?.length
        ? `<details class="permSchemeDiffGroup" data-diff="${esc(title)}" ${data}><summary>${esc(tx`${t(title)}：${list.length} 条`)}</summary><ul class="permSchemeMergeItems">${list.map(html).join("")}</ul></details>` : "";
      const body = [
        group("新增的表项", added, entryText),
        group("删除的表项", removed, entryText),
        !pending.merge ? group("将丢失的实例修改", lost, lostLine)
          : pending.unrestored?.length ? rows("写不回的表项", pending.unrestored, unrestoredHtml, "data-pending-unrestored")
            : group("写不回的表项", lost, lostLine),
        pending.merge ? rows("因你删过而没加回的行", pending.deleted_again, deletedAgainHtml, "data-pending-deleted-again") : "",
      ].join("");
      const head = !pending.regenerates ? t("保存后实例的表项不变；指令映射的修改移动了下列 A 元素，授权随之改变。")
        : pending.merge
          ? t("合并保存：实例按合并后的规则重新生成，实例中的编辑和临时凭证写回：增加和停用的行带原 ID 写回，删掉的行仍删掉；节点已不存在的表项写不回。")
          : t("保存后按新规则重新生成这套方案的实例，此前对实例的编辑不保留。");
      return `<section class="permSchemePendingSave" data-pending-save role="alertdialog" aria-label="${esc(t("确认保存规则"))}">
        <p><strong>${esc(head)}</strong></p>
        ${authDiffHtml(pending.auth, pending.rules)}
        ${body || (pending.regenerates ? `<p class="permSchemeMuted">${esc(t("实例的表项不会变化。"))}</p>` : "")}
        <div class="permSchemeButtons">
          <button type="button" class="portalButton is-primary is-compact" data-action="confirm-save" ${state.busy ? "disabled" : ""}>${esc(t("确认保存"))}</button>
          <button type="button" class="portalButton is-compact" data-action="cancel-save">${esc(t("返回编辑"))}</button>
        </div></section>`;
    }

    // Activating: what bringing the saved instance up to date does, and what principals gain and lose against
    // the scheme in use (授权差). With the permission system off it only records the choice.
    function pendingActivateHtml() {
      const pending = state.pendingActivate;
      if (!pending || pending.scheme_id !== state.current?.scheme_id) return "";
      const preview = pending.preview || {};
      const title = state.draft.title || pending.scheme_id;
      const fill = [preview.added_nodes ? tx`加入 ${preview.added_nodes} 个节点` : "", preview.moved_nodes ? tx`移动 ${preview.moved_nodes} 个节点` : "",
        preview.removed_nodes ? tx`删除 ${preview.removed_nodes} 个节点` : "",
        preview.added_entries ? tx`加入 ${preview.added_entries} 条表项` : "", preview.removed_entries ? tx`删除 ${preview.removed_entries} 条表项` : ""].filter(Boolean).join("、");
      // regenerate: the scheme has no instance of its current rules, so one is generated and replaces what was saved.
      const effect = preview.regenerate
        ? [t("这套方案还没有按当前规则生成的实例：Portal 按规则和现有对象生成实例，原先保存的实例及其中的修改不保留。"),
          tx`生成 ${preview.added_nodes ?? 0} 个节点、${preview.added_entries ?? 0} 条表项${preview.removed_entries ? tx`，替换原有 ${preview.removed_entries} 条表项` : ""}。`]
        : [t("Portal 加载这套方案保存的实例并补齐：停用期间新增的对象按规则加入，删除的对象及引用它们的表项删除；实例中修改过的部分保持原样。"),
          fill ? tx`补齐：${fill}。` : t("实例不需要补齐。")];
      const message = pending.on ? [...effect, t("所有判定立即按这套方案进行。")]
        : [t("权限系统仍保持关闭，现在不影响任何判定；在“执行设置”中开启权限系统时，Portal 补齐这套方案的实例后按它判定。"), ...effect];
      const compared = pending.on ? t("对比启用中的方案：") : t("对比开启时原本使用的方案：");
      return `<section class="permSchemePendingSave" data-pending-activate role="alertdialog" aria-label="${esc(pending.on ? tx`启用 ${title}？` : tx`开启权限系统时使用 ${title}？`)}">
        <p><strong>${esc(pending.on ? tx`启用 ${title}？` : tx`开启权限系统时使用 ${title}？`)}</strong></p>
        <p>${esc(message.join(" "))}</p>
        ${preview.auth_diff ? `<p class="permSchemeMuted">${esc(compared)}</p>${authDiffHtml(preview.auth_diff, state.draft)}` : ""}
        <div class="permSchemeButtons">
          <button type="button" class="portalButton ${pending.on ? "is-danger" : "is-primary"} is-compact" data-action="confirm-activate" ${state.busy ? "disabled" : ""}>${esc(pending.on ? t("启用") : t("设为开启时使用"))}</button>
          <button type="button" class="portalButton is-compact" data-action="cancel-activate">${esc(t("取消"))}</button>
        </div></section>`;
    }

    // -- trees view
    function treesHtml() {
      const switcher = TREES.map(name => `<button type="button" data-tree="${name}" aria-pressed="${state.tree === name}">${esc(t(TREE_TITLES[name]))}</button>`).join("");
      const index = indexTree(state.draft, state.tree);
      const selected = index.nodes.has(state.selected);
      const help = {
        P: "实线框是分类节点；虚线框是规则节点，Portal 中这一类的每个对象都是它下面的一个元素。规则节点按放置关系把元素放在相关对象之下，例如每个 Lineage 在所属 Project 之下、每个 Resident 在所属 Lineage 之下；没有这个关系的对象放在指定的分类节点下。授权给一个元素，就授权给它下面的全部 Resident。",
        A: "每个 A 元素（指令的一个判定槽）归在一个分类节点下；归在免检节点下的元素所有主体都能通过。没有归类的元素列在默认位置下，按 A 默认位置判定；固定槽还落在 R 默认位置，由覆盖那里的表项决定谁能调用。把元素或整条指令拖到另一个节点上可以改变归类。",
        R: "实线框是分类节点；虚线框是规则节点，Portal 中这一类的每个资源都放在它那里，并按放置关系放在相关对象之下（例如 Lineage 在所属 Project 之下）；没有这个关系的资源放在指定的分类节点下，没有指定时放在默认位置。方案没有放置的对象类型都在默认位置下。",
      }[state.tree];
      // The tree takes the whole width until a node is selected; then its detail panel opens beside it.
      return `<div class="permSchemeTreeLayout${selected ? "" : " is-single"}">
        <div class="permSchemeTreePane">
          <div class="permSchemeTreeBar">
            <div class="permSchemeTreeSwitch portalSegmented" role="group" aria-label="${esc(t("权限树"))}">${switcher}</div>
            ${!selected && editable() ? `<button type="button" class="portalButton is-compact" data-add-node="${esc(index.tree.root)}">${esc(t("在根下新增分类节点"))}</button>` : ""}
          </div>
          <p class="permSchemeHelp">${esc(t(help))}${editable() ? ` ${esc(t("拖动节点改变父节点。"))}` : ""} ${esc(t("点击节点查看和修改它的属性。"))}</p>
          <div class="permSchemeTreeScroll" data-scroll="tree"><ul class="permSchemeTree" role="tree" aria-label="${esc(t(TREE_TITLES[state.tree]))}">${nodeHtml(index, index.tree.root)}</ul></div>
          ${unplacedHtml(index)}
        </div>
        ${selected ? `<aside class="permSchemeDetail">${detailHtml(index)}</aside>` : ""}
      </div>`;
    }

    function markers(index, id) {
      const doc = state.draft;
      const out = [];
      if (id === index.tree.default_parent) out.push(index.name === "A" ? tx`默认位置 · 固定槽${unmappedText()}` : t("默认位置"));
      if (index.name === "A" && id === freeNode(doc)) out.push(t("免检：所有主体都能调用"));
      const category = index.nodes.get(id)?.user_category;
      if (category) out.push(tx`User 分类：${t((state.vocabulary.user_categories || []).find(item => item.category === category)?.title || category)}`);
      const kinds = typesOf(index.nodes.get(id));
      if (kinds) {
        const under = index.nodes.get(id).under;
        out.push(under ? tx`每个 ${kinds.join(" / ")}，在${t(placementTitle(state.vocabulary, kinds, under))} ${under} 之下` : tx`每个 ${kinds.join(" / ")}`);
      }
      return out.map(text => `<span class="permSchemeMarker">${esc(text)}</span>`).join("");
    }

    function nodeHtml(index, id) {
      const node = index.nodes.get(id);
      const selected = state.selected === id;
      const draggable = editable() && id !== index.tree.root;
      const children = (index.children.get(id) || []).map(child => nodeHtml(index, child)).join("");
      const extras = index.name === "A" ? instructionGroupHtml(id) : "";
      const inner = children || extras ? `<ul role="group">${children}${extras}</ul>` : "";
      return `<li role="treeitem" aria-expanded="true" aria-selected="${selected}">
        <div class="permSchemeNode${index.isEach(id) ? " is-each" : ""}${selected ? " is-selected" : ""}" data-drop="node|${index.name}|${esc(id)}" ${draggable ? `draggable="true" data-drag="node|${index.name}|${esc(id)}"` : ""}>
          <button type="button" class="permSchemeNodeLabel" data-select="${esc(id)}"><strong>${esc(node.title || id)}</strong> <code>${esc(id)}</code></button>
          ${markers(index, id)}
        </div>${inner}</li>`;
    }

    function instructionGroupHtml(id) {
      const doc = state.draft;
      // The A elements classified under this node; the default node holds the unclassified ones.
      const known = elementMap();
      const names = id === doc.trees.A.default_parent
        ? [...known.keys()].filter(element => !doc.instructions?.[element])
        : Object.entries(doc.instructions || {}).filter(([, classified]) => classified?.a === id).map(([element]) => element);
      if (!names.length) return "";
      const open = state.expanded.has(id);
      const chips = open ? `<div class="permSchemeChips">${names.sort().map(name => `<span class="permSchemeChip${known.size && !known.has(name) ? " is-stale" : ""}" ${editable() ? `draggable="true" data-drag="instruction|${esc(name)}"` : ""}>${esc(name)}</span>`).join("")}</div>` : "";
      return `<li role="treeitem" aria-expanded="${open}"><div class="permSchemeNode is-group">
        <button type="button" class="permSchemeNodeLabel" data-expand="${esc(id)}" aria-expanded="${open}"><strong>${esc(tx`${names.length} 个 A 元素`)}</strong> <span class="permSchemeMuted">${esc(open ? t("收起") : t("展开"))}</span></button></div>${chips}</li>`;
    }

    function unplacedHtml(index) {
      const available = state.vocabulary.object_types?.[index.name] || [];
      const missing = available.filter(kind => !index.placed.has(kind));
      if (!missing.length || index.name === "A") return "";
      const chips = missing.map(kind => `<span class="permSchemeChip" ${editable() ? `draggable="true" data-drag="type|${index.name}|${esc(kind)}"` : ""}>${esc(kind)}</span>`).join("");
      return `<div class="permSchemeUnplaced"><p class="permSchemeHelp">${esc(tx`这些对象类型没有规则节点，它们的对象都放在默认位置 ${index.tree.default_parent} 下。${editable() ? t("拖到一个节点上，为它新建规则节点：") : ""}`)}</p><div class="permSchemeChips">${chips}</div></div>`;
    }

    // classOnly leaves each nodes out; eachOnly shows class nodes only as the outline, not selectable.
    function nodeOptions(name, selected, {classOnly = false, eachOnly = false} = {}) {
      const index = indexTree(state.draft, name);
      const options = [];
      const walk = (id, depth) => {
        const node = index.nodes.get(id);
        const each = index.isEach(id);
        const outline = eachOnly && !each ? "disabled" : "";
        if (!(classOnly && each)) options.push(`<option value="${esc(id)}" ${id === selected ? "selected" : ""} ${outline}>${"　".repeat(depth)}${esc(node.title || id)}${each ? " ⋯" : ""}</option>`);
        for (const child of index.children.get(id) || []) walk(child, depth + 1);
      };
      if (index.nodes.has(index.tree.root)) walk(index.tree.root, 0);
      if (typeof selected === "string" && selected && !index.nodes.has(selected)) options.push(`<option value="${esc(selected)}" selected>${esc(tx`${selected}（未声明）`)}</option>`);
      return options.join("");
    }

    function detailHtml(index) {
      const locked = editable() ? "" : "disabled";
      const node = index.nodes.get(state.selected);
      if (!node) return "";
      const isRoot = node.id === index.tree.root;
      const kinds = typesOf(node);
      const refs = nodeReferences(state.draft, index.name, node.id);
      const parents = isRoot ? "" : (() => {
        const options = [];
        const walk = (id, depth) => {
          if (id === node.id) return;
          const item = index.nodes.get(id);
          if (canHang(index, kinds, id, state.vocabulary)) options.push(`<option value="${esc(id)}" ${id === node.parent ? "selected" : ""}>${"　".repeat(depth)}${esc(item.title || id)}</option>`);
          for (const child of index.children.get(id) || []) walk(child, depth + 1);
        };
        walk(index.tree.root, 0);
        return `<label class="portalField"><span>${esc(t("父节点"))}</span><select data-node-parent="${esc(node.id)}" ${locked}>${options.join("")}</select></label>`;
      })();
      let typeField = "";
      if (kinds) {
        // Nested under an each node the objects are placed under its type; under a class node the
        // placement is optional. One each node per object type and ``under`` in a tree.
        const nested = index.isEach(node.parent);
        const under = node.under || "";
        const choices = (state.vocabulary.object_types?.[index.name] || []).map(kind => {
          const elsewhere = index.placing(kind, under, node.id);
          const unplaceable = under && !sharedUnders(state.vocabulary, [kind]).includes(under);
          const disabled = !editable() || ((elsewhere || unplaceable) && !kinds.includes(kind));
          return `<label class="permSchemeCheckbox" title="${esc(elsewhere ? tx`已由 ${elsewhere} 放置` : unplaceable ? tx`${kind} 不能放在 ${under} 之下` : "")}"><input type="checkbox" data-each-type="${esc(kind)}" ${kinds.includes(kind) ? "checked" : ""} ${disabled ? "disabled" : ""} /> ${esc(kind)}</label>`;
        }).join("");
        const relationTitleOf = value => t(placementTitle(state.vocabulary, kinds, value));
        // A placement another rule node already makes for one of these types is not offered.
        const taken = value => value !== under && kinds.some(kind => index.placing(kind, value, node.id));
        const underField = nested
          ? `<p class="permSchemeHelp">${esc(tx`放置关系：在${relationTitleOf(under)} ${under} 之下（由父节点决定）`)}</p>`
          : `<label class="portalField"><span>${esc(t("放置关系"))}</span><select data-node-under="${esc(node.id)}" ${locked}>
              <option value="" ${under ? "" : "selected"} ${taken("") ? "disabled" : ""}>${esc(t("无：直接放在父节点下"))}</option>
              ${sharedUnders(state.vocabulary, kinds).map(item => `<option value="${esc(item)}" ${item === under ? "selected" : ""} ${taken(item) ? "disabled" : ""}>${esc(tx`在${relationTitleOf(item)} ${item} 之下`)}</option>`).join("")}
            </select></label>`;
        const places = [];
        const collect = (id, depth) => {
          if (index.isEach(id)) return;
          places.push(`<option value="${esc(id)}" ${node.otherwise === id ? "selected" : ""}>${"　".repeat(depth)}${esc(index.nodes.get(id).title || id)}</option>`);
          for (const child of index.children.get(id) || []) collect(child, depth + 1);
        };
        collect(index.tree.root, 0);
        const otherwiseField = nested ? `<label class="portalField"><span>${esc(tx`没有${relationTitleOf(under)} ${under} 时放在`)}</span><select data-node-otherwise="${esc(node.id)}" ${locked}>
            <option value="" ${node.otherwise ? "" : "selected"}>${esc(tx`默认位置（${index.tree.default_parent || ""}）`)}</option>
            ${places.join("")}
          </select></label>` : "";
        const fallback = index.fallback(node.id);
        typeField = `<fieldset class="permSchemeFieldset is-types"><legend>${esc(t("这一规则节点放置的对象类型"))}</legend>${choices}</fieldset>
          ${underField}${otherwiseField}
          <p class="permSchemeHelp">${esc(under ? tx`对象放在${relationTitleOf(under)} ${under} 之下；没有时放在 ${fallback} 下。` : tx`对象直接放在 ${fallback} 下。`)}</p>`;
      }
      const canHoldClass = !kinds;
      // A P class node may stand for one User category: Users of that category are placed under it.
      const categories = state.vocabulary.user_categories || [];
      const categoryField = index.name === "P" && !kinds && categories.length ? (() => {
        const taken = new Map(index.tree.nodes.filter(item => item.user_category && item.id !== node.id).map(item => [item.user_category, item.title || item.id]));
        const options = categories.map(item => `<option value="${esc(item.category)}" ${node.user_category === item.category ? "selected" : ""} ${taken.has(item.category) ? "disabled" : ""}>${esc(t(item.title || item.category))}${taken.has(item.category) ? esc(tx`（已由 ${taken.get(item.category)} 代表）`) : ""}</option>`).join("");
        return `<label class="portalField"><span>${esc(t("代表的 User 分类"))}</span><select data-node-category="${esc(node.id)}" ${locked}><option value="" ${node.user_category ? "" : "selected"}>${esc(t("无"))}</option>${options}</select></label>`;
      })() : "";
      return `<div class="permSchemeDetailHead"><h3>${esc(node.title || node.id)}</h3><button type="button" class="portalButton is-compact is-quiet" data-select="" aria-label="${esc(t("关闭节点详情"))}">${esc(t("关闭"))}</button></div>
        <p class="permSchemeMuted">${esc(kinds ? t("规则节点") : t("分类节点"))}</p>
        <label class="portalField"><span>${esc(t("名称"))}</span><input data-node-title="${esc(node.id)}" data-focus="node-title" value="${esc(node.title || "")}" ${locked} /></label>
        <label class="portalField"><span>${esc(t("节点 ID"))}</span><input data-node-id="${esc(node.id)}" data-focus="node-id" value="${esc(node.id)}" ${locked} /></label>
        ${parents}${typeField}${categoryField}
        ${refs.length ? `<p class="permSchemeHelp">${esc(tx`被引用：${refs.join("、")}`)}</p>` : ""}
        ${editable() ? `<div class="permSchemeButtons">
          ${canHoldClass ? `<button type="button" class="portalButton is-compact" data-add-node="${esc(node.id)}">${esc(t("新增分类子节点"))}</button>` : ""}
          ${index.name !== "A" ? `<button type="button" class="portalButton is-compact" data-add-each="${esc(node.id)}">${esc(t("新增规则子节点"))}</button>` : ""}
          ${kinds || node.id === index.tree.default_parent ? "" : `<button type="button" class="portalButton is-compact" data-default-node="${esc(node.id)}">${esc(t("设为默认位置"))}</button>`}
          ${isRoot ? "" : `<button type="button" class="portalButton is-compact is-danger" data-remove-node="${esc(node.id)}">${esc(t("删除节点"))}</button>`}
        </div>` : ""}`;
    }

    // -- entries view
    function entriesHtml() {
      const doc = state.draft;
      const locked = editable() ? "" : "disabled";
      const relations = state.vocabulary.relations || [];
      const palette = editable() ? `<div class="permSchemePalette">${TREES.map(name => {
        const index = indexTree(doc, name);
        const chips = [];
        const walk = (id, depth) => {
          const node = index.nodes.get(id);
          chips.push(`<span class="permSchemeChip${index.isEach(id) ? " is-each" : ""}" style="margin-inline-start:${depth * 12}px" draggable="true" data-drag="node|${name}|${esc(id)}">${esc(node.title || id)}</span>`);
          for (const child of index.children.get(id) || []) walk(child, depth + 1);
        };
        if (index.nodes.has(index.tree.root)) walk(index.tree.root, 0);
        return `<div><h4>${esc(t(TREE_TITLES[name]))}</h4>${chips.join("")}</div>`;
      }).join("")}<p class="permSchemeHelp">${esc(t("把节点拖到表项的 P、A、R 格中。"))}</p></div>` : "";
      const aIndex = indexTree(doc, "A");
      const rIndex = indexTree(doc, "R");
      const titled = (index, id) => index.nodes.get(id)?.title || id;
      const typesText = types => types === "any" ? t("任何对象") : (types || []).join("、");
      // One card per entry rule: read-only rules read as a sentence, editable ones show the same fields as controls.
      const rows = (doc.entries || []).map((entry, position) => {
        const invalid = entryInvalid(position);
        const relation = isRelationRule(entry);
        const deny = entry.effect === "deny";
        const classes = ["permSchemeEntry", deny ? "is-deny" : "", invalid ? "is-invalid" : "", state.highlightEntry === position ? "is-highlighted" : ""].filter(Boolean).join(" ");
        const kindLabel = relation ? t("关系规则") : t("节点规则");
        const cell = (axis, body, label = axis) => `<div class="permSchemeEntryCell" ${axis === "relation" ? "" : `data-drop="cell|${position}|${axis}"`}><span class="permSchemeAxis">${esc(label)}</span>${body}</div>`;
        if (!editable()) {
          const first = relation ? cell("relation", `<span>${esc(relationTitle(entry.relation))}</span>`, t("关系")) : cell("P", `<span>${esc(principalLabel(entry.p))}</span>`);
          return `<article class="${classes}" data-entry-card="${position}">
            <div class="permSchemeEntryHead"><span class="permSchemeEffect${deny ? " is-deny" : ""}">${esc(entry.effect)}</span>
              <strong>${esc(entry.title || entryId(entry, position))}</strong><code class="permSchemeMuted">${esc(entryId(entry, position))}</code><span class="permSchemeMarker">${esc(kindLabel)}</span></div>
            <div class="permSchemeEntryPar">${first}${cell("A", `<span>${esc(titled(aIndex, entry.a))}</span>`)}${cell("R", `<span>${esc(titled(rIndex, entry.r))}</span>`)}</div>
          </article>`;
        }
        const kindSelect = `<select class="portalControl" data-entry="${position}|kind" aria-label="${esc(t("规则种类"))}">
          <option value="node" ${relation ? "" : "selected"}>${esc(t("节点规则"))}</option><option value="relation" ${relation ? "selected" : ""}>${esc(t("关系规则"))}</option></select>`;
        // A relation applies to the object types of the R each node; the others stay listed but cannot be chosen.
        const rKinds = typesOf(rIndex.nodes.get(entry.r));
        const relationOptions = relations.map(item => {
          const fits = relationApplies(state.vocabulary, item.relation, rKinds);
          return `<option value="${esc(item.relation)}" ${item.relation === entry.relation ? "selected" : ""} ${fits || item.relation === entry.relation ? "" : "disabled"}>${esc(t(item.title || item.relation))}</option>`;
        }).join("");
        const known = relations.some(item => item.relation === entry.relation);
        const first = relation
          ? cell("relation", `<select class="portalControl" data-entry="${position}|relation" aria-label="${esc(t("关系"))}">${known ? "" : `<option value="${esc(entry.relation)}" selected>${esc(tx`${entry.relation || "—"}（未知关系）`)}</option>`}${relationOptions}</select>`, t("关系"))
          : cell("P", `<select class="portalControl" data-entry="${position}|p" aria-label="P">${nodeOptions("P", entry.p, {classOnly: true})}</select>`);
        const aSelect = `<select class="portalControl" data-entry="${position}|a" aria-label="A">${nodeOptions("A", entry.a, {classOnly: true})}</select>`;
        const rSelect = `<select class="portalControl" data-entry="${position}|r" aria-label="R">${nodeOptions("R", entry.r, relation ? {eachOnly: true} : {classOnly: true})}</select>`;
        return `<article class="${classes}" data-entry-card="${position}">
          <div class="permSchemeEntryHead">
            <select class="portalControl permSchemeEffect${deny ? " is-deny" : ""}" data-entry="${position}|effect" aria-label="${esc(t("效果"))}"><option value="permit" ${deny ? "" : "selected"}>permit</option><option value="deny" ${deny ? "selected" : ""}>deny</option></select>
            <input class="portalControl permSchemeEntryTitle" data-entry="${position}|title" data-focus="entry-${position}-title" value="${esc(entry.title || "")}" placeholder="${esc(t("名称"))}" aria-label="${esc(t("名称"))}" />
            <input class="portalControl permSchemeEntryId" data-entry="${position}|id" data-focus="entry-${position}-id" value="${esc(entry.id || "")}" placeholder="${esc(entryId(entry, position))}" aria-label="ID" spellcheck="false" />
            ${kindSelect}
            <button type="button" class="portalButton is-compact is-quiet permSchemeEntryRemove" data-entry-remove="${position}">${esc(t("删除"))}</button>
          </div>
          <div class="permSchemeEntryPar">${first}${cell("A", aSelect)}${cell("R", rSelect)}</div>
        </article>`;
      }).join("");
      const legend = relations.map(item => `<li><code>${esc(item.relation)}</code> ${esc(t(item.title || ""))}${esc(tx`（适用于 ${typesText(item.types)}）`)}</li>`).join("");
      return `<div class="permSchemeEntriesView">
        ${palette}
        <section class="permSchemeSection"><h3>${esc(t("表规则"))}</h3>
          <p class="permSchemeHelp">${esc(t("表规则决定生成实例时写入哪些表项。一条表项允许（permit）或拒绝（deny）P、A、R 三个节点之下全部元素的组合，deny 优先。节点规则的 P、R 都是分类节点，写成实例里的一行。关系规则的 R 是规则节点（⋯），对其中每个对象按关系找到主体，各写一行，新对象出现时也按它写入；关系种类由 Portal 固定，方案只能选用。"))}</p>
          ${legend ? `<details><summary>${esc(t("关系"))}</summary><ul class="permSchemeLegend">${legend}</ul></details>` : ""}
          <div class="permSchemeEntries">${rows || `<p class="permSchemeMuted">${esc(t("没有表项。"))}</p>`}</div>
          ${editable() ? `<div class="permSchemeButtons"><button type="button" class="portalButton is-compact" data-entry-add="permit">${esc(t("添加 permit 表项"))}</button><button type="button" class="portalButton is-compact" data-entry-add="deny">${esc(t("添加 deny 表项"))}</button></div>` : ""}
        </section></div>`;
    }

    // -- instruction map view
    // An instruction's row lists its A elements (vocabulary.instructions[].elements) and the scheme's classification of
    // each; classifications of elements no instruction declares any more are listed with the instruction they named.
    const staleOf = name => staleMappings().filter(key => key === name || key.startsWith(`${name}:`));
    function instructionsHtml() {
      const doc = state.draft;
      const known = instructionNames();
      const stale = staleMappings();
      const filter = state.filter.trim().toLowerCase();
      const free = freeNode(doc);
      const unmapped = new Set(unmappedNames());
      // A stale key whose instruction is gone gets a row of its own.
      const orphans = stale.filter(key => !instructionInfo(key.split(":")[0]));
      const rows = [...known, ...orphans].filter(name => {
        const elements = elementsOf(name).map(slot => slot.element);
        const classified = elements.map(element => doc.instructions?.[element]);
        if (state.show === "guarded" && !classified.some(item => item && !isFree(item, free))) return false;
        if (state.show === "free" && !(classified.length && classified.every(item => isFree(item, free)))) return false;
        if (state.show === "unmapped" && !elements.some(element => unmapped.has(element))) return false;
        if (state.show === "stale" && !staleOf(name).length) return false;
        if (!filter) return true;
        const info = instructionInfo(name);
        return [name, info?.description, info?.category, ...elements].some(value => String(value || "").toLowerCase().includes(filter));
      });
      const target = (id, label) => `<span class="permSchemeDropTarget" data-drop="node|A|${esc(id)}">${esc(label)}</span>`;
      const dropBar = doc.trees.A.nodes.filter(node => ![doc.trees.A.default_parent, doc.trees.A.root, free].includes(node.id))
        .map(node => target(node.id, node.title || node.id)).join("")
        + (free ? target(free, t("免检")) : "")
        + (doc.trees.A.default_parent !== free ? target(doc.trees.A.default_parent, t("取消归类")) : "");
      const shows = [["all", "全部"], ["guarded", "受保护"], ["free", "免检"], ["unmapped", "未归类"], ["stale", "已失效"]]
        .map(([value, label]) => `<button type="button" data-instruction-show="${value}" aria-pressed="${state.show === value}">${esc(t(label))}</button>`).join("");
      const unmappedNote = unmapped.size
        ? `<p class="permSchemeHelp" data-unmapped>${esc(tx`这套方案有 ${unmapped.size} 个 A 元素没有归类，落在 A 默认位置 ${doc.trees.A.default_parent}；固定槽还落在 R 默认位置 ${doc.trees.R.default_parent}（${unmappedText()}）`)}${unmapped.size <= 30
          ? `：${[...unmapped].sort().map(name => `<code>${esc(name)}</code>`).join(" ")}` : esc(t("，用「未归类」筛选查看。"))}</p>` : "";
      const classifiedCount = Object.keys(doc.instructions || {}).length - stale.length;
      return `<div class="permSchemeActionsView">
        <div class="permSchemeFilters">
          <label class="portalSearchField permSchemeSearch"><svg class="portalSearchFieldIcon" viewBox="0 0 24 24" fill="none" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg>
            <input type="search" data-instruction-filter data-focus="instruction-filter" placeholder="${esc(t("搜索指令、说明或分类"))}" aria-label="${esc(t("搜索指令、说明或分类"))}" value="${esc(state.filter)}" /></label>
          <div class="portalSegmented" role="group" aria-label="${esc(t("显示"))}">${shows}</div>
          <span class="permSchemeMuted">${esc(tx`${classifiedCount} 个 A 元素已归类，${unmapped.size} 个未归类，${stale.length} 个已失效`)}</span>
        </div>
        <p class="permSchemeHelp">${esc(t("每条指令判定哪些对象由指令声明给出，每个判定槽是一个 A 元素：只有一个槽时元素名就是指令名，多个槽时是“指令:参数”或“指令:固定槽名”。方案只为每个元素选择 A 树分类节点；固定槽不指向对象，另选它判定的 R 树分类节点。"))}</p>
        ${unmappedNote}
        ${editable() ? `<div class="permSchemeDropBar"><span class="permSchemeMuted">${esc(t("把指令或 A 元素拖到 A 树节点上："))}</span>${dropBar}</div>` : ""}
        <div class="permSchemeInstructions">${rows.map(instructionRowHtml).join("") || `<p class="permSchemeMuted">${esc(t("没有匹配的指令。"))}</p>`}</div></div>`;
    }

    // One A element as a line: what it judges and the node it is classified under; a fixed slot shows its R node.
    function elementLine(slot, several) {
      const doc = state.draft;
      const classified = doc.instructions?.[slot.element];
      const titled = (tree, id) => doc.trees[tree].nodes.find(node => node.id === id)?.title || id;
      // Several slots: an argument slot names its argument anyway; a fixed slot is told apart by its name.
      const head = several && slot.fixed ? `${slot.fixed}：` : "";
      if (!classified) return `${head}${several && slot.argument ? `${declaredText(slot, state.vocabulary.records)}：` : ""}${unmappedText(Boolean(slot.fixed))}`;
      if (isFree(classified, freeNode(doc))) return `${head}${several && slot.argument ? `${slot.argument}：` : ""}${t("免检：所有主体都能调用")}`;
      const what = slot.fixed ? tx`R ${titled("R", classified.r || doc.trees.R.default_parent)}` : declaredText(slot, state.vocabulary.records);
      return `${head}${what} → ${titled("A", classified.a)}`;
    }

    // One row per instruction: name and category, a two-line description (full text on hover), its A elements.
    function instructionRowHtml(name) {
      const doc = state.draft;
      const info = instructionInfo(name);
      const slots = elementsOf(name);
      const stale = staleOf(name);
      const free = freeNode(doc);
      const classified = slots.map(slot => doc.instructions?.[slot.element]);
      const open = state.editing.has(name);
      const allFree = slots.length && classified.every(item => isFree(item, free));
      const titled = id => doc.trees.A.nodes.find(node => node.id === id)?.title || id;
      const staleLines = stale.map(key => `<div class="permSchemeWarning" data-stale-element="${esc(key)}">${esc(tx`${key} → ${titled(doc.instructions[key]?.a)}：指令声明中已没有这个 A 元素`)}
        ${editable() ? `<button type="button" class="portalButton is-compact is-quiet" data-stale-clear="${esc(key)}">${esc(t("删除归类"))}</button>` : ""}</div>`).join("");
      const summary = (allFree ? `<span class="permSchemeMuted">${esc(t("免检：所有主体都能调用"))}</span>`
        : slots.map(slot => `<div data-element-line="${esc(slot.element)}">${esc(elementLine(slot, slots.length > 1))}</div>`).join("")) + staleLines;
      const toggle = slots.length ? `<button type="button" class="portalButton is-compact is-quiet" data-edit-instruction="${esc(name)}" data-portal-tooltip="${esc(t("展开或收起这条指令的判定槽"))}" aria-expanded="${open}">${esc(open ? t("收起") : editable() ? t("编辑") : t("详情"))}</button>` : "";
      const description = info?.description || "";
      return `<article class="permSchemeInstruction${info ? "" : " is-stale"}${classified.some(item => item && !isFree(item, free)) ? " is-guarded" : ""}${open ? " is-open" : ""}">
        <div class="permSchemeInstructionMain">
          <div class="permSchemeInstructionName"><span class="permSchemeChip" ${editable() && info ? `draggable="true" data-drag="instruction|${esc(name)}"` : ""}>${esc(name)}</span>
            <small class="permSchemeMuted">${esc(info?.category || "")}</small>${info ? "" : `<strong class="permSchemeWarning">${esc(t("Portal 中已没有"))}</strong>`}</div>
          <p class="permSchemeInstructionDescription" title="${esc(description)}">${esc(description)}</p>
          <div class="permSchemeInstructionSlots">${summary}</div>
          <div class="permSchemeInstructionToggle">${toggle}</div>
        </div>
        ${open && slots.length ? `<div class="permSchemeInstructionEditor">${elementEditorHtml(name)}</div>` : ""}
      </article>`;
    }

    // One row per A element of an instruction: what it judges, as the instruction declares it (read-only), and its
    // classification: an A class node (the A default node leaves it unclassified) and, for a fixed slot, the R class
    // node it asks about, chosen once the element is classified.
    function elementEditorHtml(name) {
      const doc = state.draft;
      const locked = editable() ? "" : "disabled";
      const records = state.vocabulary.records || [];
      const parameters = new Map((instructionInfo(name)?.parameters || []).map(item => [item.name, item.description || ""]));
      const free = freeNode(doc);
      const slots = elementsOf(name);
      const rows = slots.map(slot => {
        const classified = doc.instructions?.[slot.element];
        const key = esc(slot.element);
        const about = slot.argument ? parameters.get(slot.argument.split(".")[0]) : "";
        const record = slot.via ? records.find(item => item.via === slot.via) : null;
        const notes = [about, record?.may_be_unowned ? t("这类记录可能不属于任何对象；不属于任何对象时这个槽不提出判定请求。") : ""].filter(Boolean);
        const aField = `<label class="portalField"><span>${esc(t("A 树分类节点"))}</span><select data-classify="${key}|a" data-focus="classify-${key}-a" ${locked}>${nodeOptions("A", classified?.a || doc.trees.A.default_parent, {classOnly: true})}</select></label>`;
        const rField = !slot.fixed ? "" : `<label class="portalField"><span>${esc(t("判定的 R 树分类节点"))}</span><select data-classify="${key}|r" data-focus="classify-${key}-r" ${locked || (classified ? "" : "disabled")}>${nodeOptions("R", classified?.r || doc.trees.R.default_parent, {classOnly: true})}</select></label>`;
        return `<div class="permSchemeSlot" data-element="${key}">
          <div class="permSchemeElement"><code>${esc(slot.element)}</code><span>${esc(declaredText(slot, records))}</span>
            ${notes.length ? `<small class="permSchemeMuted">${esc(notes.join(" "))}</small>` : ""}</div>
          ${aField}${rField}</div>`;
      }).join("");
      const classified = slots.map(slot => doc.instructions?.[slot.element]);
      const buttons = editable() ? `<div class="permSchemeButtons">
        ${free && !classified.every(item => isFree(item, free)) ? `<button type="button" class="portalButton is-compact" data-slot-free="${esc(name)}">${esc(slots.length > 1 ? t("全部设为免检") : t("设为免检"))}</button>` : ""}
        ${classified.some(Boolean) ? `<button type="button" class="portalButton is-compact" data-slot-clear="${esc(name)}">${esc(slots.length > 1 ? t("全部取消归类") : t("取消归类"))}</button>` : ""}</div>` : "";
      return `<div class="permSchemeSlots">${rows}${buttons}</div>`;
    }

    // -- instance view
    // The scheme's own table, row by row. Writing a row checks only that its nodes exist on the scheme's trees.
    const AXES = ["p", "a", "r"];
    const AXIS_TREE = {p: "P", a: "A", r: "R"};
    const nodeInput = (key, axis, value, label) => `<input class="portalControl" list="permInstNodes-${AXIS_TREE[axis]}" data-inst-field="${key}|${axis}" data-inst-tree="${AXIS_TREE[axis]}"
      data-focus="inst-${key}-${axis}" value="${esc(value || "")}" placeholder="${esc(label)}" aria-label="${esc(label)}" spellcheck="false" />`;
    const effectSelect = (key, value) => `<select class="portalControl" data-inst-field="${key}|effect" aria-label="${esc(t("效果"))}">
      <option value="permit" ${value === "deny" ? "" : "selected"}>permit</option><option value="deny" ${value === "deny" ? "selected" : ""}>deny</option></select>`;

    function instanceHtml() {
      const inst = state.instance;
      if (!inst.loaded && !inst.loading && !inst.error) queueMicrotask(() => { if (!inst.loaded && !inst.loading) void loadInstance(); });
      // A built-in's instance is edited like any other; only its rules follow the code.
      const builtin = Boolean(state.current?.builtin);
      const idle = Boolean(state.current) && !inst.busy;
      const canAdd = idle && may("CreatePermissionEntry");
      const canUpdate = idle && may("UpdatePermissionEntry");
      const canDelete = idle && may("DeletePermissionEntry");
      const f = inst.filters;
      const scopes = axis => `<select class="portalControl" data-inst-filter="${axis}_scope" aria-label="${esc(tx`${AXIS_TREE[axis]} 的范围`)}">${[["exact", "只看这个节点"], ["subtree", "含其下节点"], ["ancestors", "对它生效（含上级）"]]
        .map(([value, label]) => `<option value="${value}" ${f[`${axis}_scope`] === value ? "selected" : ""}>${esc(t(label))}</option>`).join("")}</select>`;
      const choice = (key, options) => `<select class="portalControl" data-inst-filter="${key}">${options.map(([value, label]) => `<option value="${value}" ${f[key] === value ? "selected" : ""}>${esc(t(label))}</option>`).join("")}</select>`;
      const filters = `<div class="permInstFilters" data-inst-filters>
        ${AXES.map(axis => `<div class="permInstFilter"><span class="permSchemeAxis">${AXIS_TREE[axis]}</span>
          <input class="portalControl" list="permInstNodes-${AXIS_TREE[axis]}" data-inst-filter="${axis}" data-inst-tree="${AXIS_TREE[axis]}" data-focus="inst-filter-${axis}"
            value="${esc(f[axis])}" placeholder="${esc(t("节点 ID，留空为全部"))}" spellcheck="false" />${scopes(axis)}</div>`).join("")}
        ${choice("effect", [["", "permit 与 deny"], ["permit", "只看 permit"], ["deny", "只看 deny"]])}
        ${choice("disabled", [["", "启用与停用"], ["false", "只看启用"], ["true", "只看停用"]])}
        ${choice("temporary", [["", "含临时凭证"], ["false", "不含临时凭证"], ["true", "只看临时凭证"]])}
      </div>`;
      const rows = inst.entries.map(entry => {
        const temporary = isTemporary(entry);
        // Each listed row says whether the caller may change or delete it, judged on its own r; both instructions are
        // free at entry, so GetPermissionSystem.allows says yes to everyone and only the row's answer counts.
        const canUpdateRow = canUpdate && entry.allows?.UpdatePermissionEntry === true;
        const canDeleteRow = canDelete && entry.allows?.DeletePermissionEntry === true;
        const checked = inst.selected.has(entry.entry_id) ? "checked" : "";
        const select = `<td><input type="checkbox" data-inst-select="${esc(entry.entry_id)}" aria-label="${esc(tx`选择表项 ${entry.entry_id}`)}" ${checked} ${canDeleteRow && !temporary ? "" : "disabled"} /></td>`;
        if (inst.editing === entry.entry_id && inst.edit) {
          return `<tr class="permInstRow is-editing" data-inst-row="${esc(entry.entry_id)}">${select}<td>${effectSelect("edit", inst.edit.effect)}</td>
            ${AXES.map(axis => `<td>${nodeInput("edit", axis, inst.edit[axis], AXIS_TREE[axis])}</td>`).join("")}
            <td>${entry.disabled ? esc(t("已停用")) : ""}</td>
            <td class="permInstButtons"><button type="button" class="portalButton is-primary is-compact" data-inst-action="save-edit">${esc(t("保存"))}</button>
              <button type="button" class="portalButton is-compact is-quiet" data-inst-action="cancel-edit">${esc(t("取消"))}</button></td></tr>`;
        }
        const status = temporary
          ? `<span class="permSchemeBadge">${esc(t("临时凭证"))}</span> <small class="permSchemeMuted">${esc([entry.issued_by ? tx`签发者 ${entry.issued_by}` : "", entry.expires_at ? tx`到期 ${entry.expires_at}` : ""].filter(Boolean).join(" · "))}</small>`
          : entry.disabled ? `<span class="permSchemeBadge is-draft">${esc(t("已停用"))}</span>` : "";
        const buttons = temporary ? "" : [
          canUpdateRow ? `<button type="button" class="portalButton is-compact is-quiet" data-inst-action="toggle" data-inst-entry="${esc(entry.entry_id)}">${esc(entry.disabled ? t("启用") : t("停用"))}</button>` : "",
          canUpdateRow ? `<button type="button" class="portalButton is-compact is-quiet" data-inst-action="edit" data-inst-entry="${esc(entry.entry_id)}">${esc(t("编辑"))}</button>` : "",
          canDeleteRow ? `<button type="button" class="portalButton is-compact is-quiet is-danger" data-inst-action="delete" data-inst-entry="${esc(entry.entry_id)}">${esc(t("删除"))}</button>` : "",
        ].join("");
        return `<tr class="permInstRow${entry.effect === "deny" ? " is-deny" : ""}${entry.disabled ? " is-disabled" : ""}" data-inst-row="${esc(entry.entry_id)}">${select}
          <td><span class="permSchemeEffect${entry.effect === "deny" ? " is-deny" : ""}">${esc(entry.effect)}</span></td>
          ${AXES.map(axis => `<td><code title="${esc(entry[axis])}">${esc(instanceNodeLabel(AXIS_TREE[axis], entry[axis]))}</code></td>`).join("")}
          <td>${status}</td><td class="permInstButtons">${buttons}</td></tr>`;
      }).join("");
      // A scheme that was never activated has no instance yet; entries can only be added once one is generated.
      const noInstance = !state.schemes.find(scheme => scheme.scheme_id === state.current?.scheme_id)?.entry_count;
      const adding = canAdd && !noInstance ?`<tr class="permInstRow is-adding" data-inst-adding><td></td><td>${effectSelect("add", inst.adding.effect)}</td>
        ${AXES.map(axis => `<td>${nodeInput("add", axis, inst.adding[axis], AXIS_TREE[axis])}</td>`).join("")}<td></td>
        <td class="permInstButtons"><button type="button" class="portalButton is-compact" data-inst-action="add" ${AXES.every(axis => inst.adding[axis]) ? "" : "disabled"}>${esc(t("添加表项"))}</button></td></tr>` : "";
      const first = inst.total ? inst.offset + 1 : 0;
      const last = Math.min(inst.offset + inst.entries.length, inst.total);
      const pager = `<div class="permInstPager">
        <span class="permSchemeMuted" data-inst-count>${esc(inst.loading ? t("正在读取实例表…") : tx`第 ${first}–${last} 条，共 ${inst.total} 条`)}</span>
        <button type="button" class="portalButton is-compact" data-inst-action="prev" ${inst.cursors.length && !inst.loading ? "" : "disabled"}>${esc(t("上一页"))}</button>
        <button type="button" class="portalButton is-compact" data-inst-action="next" ${inst.next && !inst.loading ? "" : "disabled"}>${esc(t("下一页"))}</button>
        ${canDelete ? `<button type="button" class="portalButton is-compact is-danger" data-inst-action="delete-selected" ${inst.selected.size ? "" : "disabled"}>${esc(tx`删除所选（${inst.selected.size}）`)}</button>` : ""}
      </div>`;
      const datalists = TREES.map(name => `<datalist id="permInstNodes-${name}" data-inst-nodes="${name}"></datalist>`).join("");
      return `<div class="permInstView" data-instance-view>
        <p class="permSchemeHelp">${esc(t("实例表是这套方案判定时实际查询的权限表。每一行允许（permit）或拒绝（deny）P、A、R 三个节点之下全部元素的组合，deny 优先；落在 deny 之内的 permit 照常保存，但不起作用。新对象出现时 Portal 按规则写入它的表项；对象删除时引用它的表项一起删除。这里的增删改只改这一行，不做其他检查。"))}</p>
        ${builtin ? `<p class="permSchemeHelp" data-builtin-instance>${esc(t("发布改变内置规则的树或表规则时，实例按新规则重新生成，这里的编辑写回；引用的节点已不存在的表项写不回，列在上次随发布重新生成的记录中。发布只改指令分类时，实例不动。"))}</p>`
          : `<p class="permSchemeHelp">${esc(t("保存修改过的树或表规则时，实例按新规则重新生成，这里的编辑不保留。"))}${dirty() ? ` <strong>${esc(t("规则有未保存的修改；这里显示的是当前保存的实例。"))}</strong>` : ""}</p>`}
        ${noInstance ? `<p class="permSchemeNotice" data-no-instance>${esc(t("这套方案还没有实例：启用它时按规则和 Portal 现有对象生成，之后才能在这里添加表项。"))}</p>` : ""}
        ${filters}
        ${inst.error ? `<p class="permSchemeNotice" data-tone="error" role="alert">${esc(inst.error)}</p>` : ""}
        <div class="permInstTableWrap" data-scroll="instance"><table class="permInstTable">
          <thead><tr><th></th><th>${esc(t("效果"))}</th><th>P</th><th>A</th><th>R</th><th>${esc(t("状态"))}</th><th></th></tr></thead>
          <tbody>${rows || `<tr><td colspan="7" class="permSchemeMuted">${esc(inst.loading ? t("正在读取实例表…") : t("没有符合条件的表项。"))}</td></tr>`}${adding}</tbody>
        </table></div>
        ${pager}${datalists}
      </div>`;
    }

    function renderInstance() {
      if (state.destroyed || state.view !== "instance") return;
      if (root.querySelector("[data-instance-view]")) render();
    }

    async function loadInstance() {
      const inst = state.instance;
      if (!state.current) return;
      const serial = ++inst.serial;
      inst.loading = true;
      renderInstance();
      const f = inst.filters;
      const args = {scheme_id: state.current.scheme_id, limit: inst.limit, ...(inst.cursor ? {cursor: inst.cursor} : {})};
      for (const axis of AXES) if (f[axis].trim()) { args[axis] = f[axis].trim(); args[`${axis}_scope`] = f[`${axis}_scope`]; }
      if (!args.p && !args.r) { args.r = "@portal"; args.r_scope = "subtree"; }
      if (f.effect) args.effect = f.effect;
      if (f.disabled) args.disabled = f.disabled === "true";
      if (f.temporary) args.temporary = f.temporary === "true";
      try {
        const result = await run("ListPermissionEntries", args);
        if (state.destroyed || serial !== inst.serial || inst !== state.instance) return;
        inst.entries = result.entries || [];
        inst.total = result.total ?? inst.entries.length;
        inst.next = String(result.next_cursor || "");
        inst.error = "";
        const shown = new Set(inst.entries.map(entry => entry.entry_id));
        inst.selected = new Set([...inst.selected].filter(id => shown.has(id)));
      } catch (error) {
        if (state.destroyed || serial !== inst.serial || inst !== state.instance) return;
        inst.error = tx`读取实例表失败：${error.message || String(error)}`;
      }
      inst.loading = false;
      inst.loaded = true;
      renderInstance();
    }

    // One instance write at a time; a version conflict reloads the page so the newer row shows.
    async function instanceWrite(callback) {
      const inst = state.instance;
      if (inst.busy || state.destroyed) return;
      inst.busy = true;
      renderInstance();
      try {
        await callback();
        inst.error = "";
      } catch (error) {
        // A version conflict reloads the row; other conflicts (same content, no instance yet) keep the server's message.
        inst.error = error.code === "instruction.version_conflict"
          ? tx`这条表项已被他人修改（当前版本 ${error.details.current_version}），已重新载入。`
          : error.message || String(error);
      } finally {
        inst.busy = false;
      }
      await loadInstance();
    }

    let searchTimer = 0;
    // Suggests instance nodes for a P, A or R input: class nodes by ID or title, objects by ID.
    function suggestNodes(tree, query) {
      clearTimeout(searchTimer);
      if (!query.trim() || !state.current) return;
      searchTimer = setTimeout(async () => {
        try {
          const result = await run("SearchPermissionNodes", {scheme_id: state.current.scheme_id, tree, query: query.trim(), limit: 20});
          const list = root.querySelector(`[data-inst-nodes="${tree}"]`);
          if (!list || state.destroyed) return;
          for (const node of result.nodes || []) if (node.title) nodeTitles[tree].set(node.id, node.title);
          list.innerHTML = (result.nodes || []).map(node => `<option value="${esc(node.id)}">${esc([node.title, node.object_type, (node.path || []).join(" / ")].filter(Boolean).join(" · "))}</option>`).join("");
        } catch { /* suggestions are optional */ }
      }, 250);
    }

    root.addEventListener("click", event => {
      const target = event.target.closest?.("[data-inst-action]");
      if (!target || state.destroyed || target.disabled) return;
      const inst = state.instance;
      const scheme = state.current?.scheme_id;
      const entry = inst.entries.find(item => item.entry_id === target.dataset.instEntry);
      const action = target.dataset.instAction;
      if (action === "next" && inst.next) {
        inst.cursors.push(inst.cursor);
        inst.cursor = inst.next;
        inst.offset += inst.entries.length;
        void loadInstance();
      } else if (action === "prev" && inst.cursors.length) {
        inst.cursor = inst.cursors.pop();
        inst.offset = Math.max(0, inst.offset - inst.limit);
        void loadInstance();
      } else if (action === "edit" && entry) {
        inst.editing = entry.entry_id;
        inst.edit = {effect: entry.effect, p: entry.p, a: entry.a, r: entry.r};
        render();
      } else if (action === "cancel-edit") {
        inst.editing = "";
        inst.edit = null;
        render();
      } else if (action === "save-edit") {
        const current = inst.entries.find(item => item.entry_id === inst.editing);
        if (!current || !inst.edit) return;
        const changes = Object.fromEntries(["effect", ...AXES].filter(key => inst.edit[key] && inst.edit[key] !== current[key]).map(key => [key, inst.edit[key].trim()]));
        inst.editing = "";
        inst.edit = null;
        if (!Object.keys(changes).length) { render(); return; }
        void instanceWrite(() => run("UpdatePermissionEntry", {scheme_id: scheme, entry_id: current.entry_id, ...changes}, "permission-entry-update", {expectedVersion: current.version}));
      } else if (action === "toggle" && entry) {
        void instanceWrite(() => run("UpdatePermissionEntry", {scheme_id: scheme, entry_id: entry.entry_id, disabled: !entry.disabled}, "permission-entry-toggle", {expectedVersion: entry.version}));
      } else if (action === "add") {
        const row = Object.fromEntries(AXES.map(axis => [axis, inst.adding[axis].trim()]));
        void instanceWrite(async () => {
          await run("CreatePermissionEntry", {scheme_id: scheme, effect: inst.adding.effect, ...row}, "permission-entry-create");
          inst.adding = {effect: inst.adding.effect, p: "", a: "", r: ""};
        });
      } else if (action === "delete" && entry) {
        void (async () => {
          const approved = await window.PortalUI.confirm({title: t("删除这条表项？"), message: entryText(entry), confirmLabel: t("删除"), tone: "danger"});
          if (approved && !state.destroyed) await instanceWrite(() => run("DeletePermissionEntry", {scheme_id: scheme, entry_id: entry.entry_id}, "permission-entry-delete"));
        })();
      } else if (action === "delete-selected") {
        const ids = [...inst.selected];
        if (!ids.length) return;
        void (async () => {
          const approved = await window.PortalUI.confirm({title: tx`删除所选的 ${ids.length} 条表项？`, message: t("删除后无法恢复。"), confirmLabel: t("删除"), tone: "danger"});
          if (!approved || state.destroyed) return;
          await instanceWrite(async () => {
            await run("DeletePermissionEntry", {scheme_id: scheme, entry_ids: ids}, "permission-entry-delete");
            inst.selected = new Set();
          });
        })();
      }
    });

    root.addEventListener("input", event => {
      const input = event.target;
      const data = input.dataset || {};
      const inst = state.instance;
      if (data.instField) {
        const [key, field] = parse(data.instField);
        const target = key === "edit" ? inst.edit : inst.adding;
        if (!target) return;
        target[field] = input.value;
        if (data.instTree) suggestNodes(data.instTree, input.value);
        if (key === "add") root.querySelector('[data-inst-action="add"]')?.toggleAttribute("disabled", !AXES.every(axis => inst.adding[axis].trim()));
      } else if (data.instFilter && data.instTree) {
        inst.filters[data.instFilter] = input.value;
        suggestNodes(data.instTree, input.value);
      }
    });

    root.addEventListener("change", event => {
      const input = event.target;
      const data = input.dataset || {};
      const inst = state.instance;
      if (data.instFilter) {
        inst.filters[data.instFilter] = input.value;
        inst.offset = 0;
        inst.cursor = "";
        inst.cursors = [];
        void loadInstance();
      } else if (data.instSelect) {
        if (input.checked) inst.selected.add(data.instSelect); else inst.selected.delete(data.instSelect);
        render();
      } else if (data.instField) {
        const [key, field] = parse(data.instField);
        const target = key === "edit" ? inst.edit : inst.adding;
        if (target) target[field] = input.value;
      }
    });

    // -- try-decide view
    function checkHtml() {
      const input = state.checkInput;
      const active = state.current?.active && !dirty();
      // The arguments a call is judged on are the instruction's declared argument slots.
      const slots = elementsOf(input.instruction).filter(slot => slot.argument);
      // A via argument takes record IDs: the server reads the record and judges the object it names.
      const records = state.vocabulary.records || [];
      const field = (name, label, placeholder, hint = "") => `<label class="portalField"><span>${esc(label)}</span>
        <input data-check-argument="${esc(name)}" data-focus="check-${esc(name)}" value="${esc(input.arguments[name] || "")}" placeholder="${esc(placeholder)}" />
        ${hint ? `<small class="permSchemeMuted" data-check-place>${esc(hint)}</small>` : ""}</label>`;
      const args = slots.map(slot => {
        // A place slot takes the new object's type name and the IDs of the objects it will belong to (dotted paths
        // name nested arguments, e.g. fields.project_id).
        if (slot.place) {
          const paths = Object.entries(slot.place).flatMap(([related, list]) => [].concat(list).map(path => [path, related]));
          return [field(slot.argument, tx`${slot.argument}（新资源的类型）`, t("类型名，例如 workspace"), t("按新资源将要放进 R 树时的父节点判定")),
            ...paths.map(([path, related]) => field(path, path, tx`${related} ID`))].join("");
        }
        const record = slot.via ? records.find(item => item.via === slot.via) : null;
        const hint = !slot.via ? "" : record ? tx`${t(record.record_title)} ID，按记录中的${t(record.title)}判定` : tx`记录 ID（${slot.via}）`;
        return `<label class="portalField"><span>${esc(slot.argument)}${slot.types?.length ? ` (${esc(slot.types.join(" / "))})` : ""}</span>
        <input data-check-argument="${esc(slot.argument)}" data-focus="check-${esc(slot.argument)}" value="${esc(input.arguments[slot.argument] || "")}" placeholder="${esc(record ? tx`${t(record.record_title)} ID` : slot.via ? t("记录 ID") : t("资源 ID"))}" />
        ${hint ? `<small class="permSchemeMuted" data-check-via>${esc(hint)}</small>` : ""}</label>`;
      }).join("");
      const options = instructionNames().map(name => `<option value="${esc(name)}"></option>`).join("");
      const result = state.checkResult;
      const reason = item => (item.allowed ? t("允许") : item.reason === "denied" ? t("命中 deny 表项") : t("没有覆盖它的 permit 表项"));
      const resultHtml = !result ? "" : result.error
        ? `<p class="permSchemeNotice" data-tone="error">${esc(result.error)}</p>`
        : `<div class="permSchemeCheckResult" data-allowed="${Boolean(result.allowed)}"><strong>${esc(result.allowed ? t("允许") : t("拒绝"))}</strong>
            <ul>${(result.requests || []).map(item => `<li><code>${esc(item.action || "")}</code> · <code>${esc(item.resource || "")}</code>：${esc(reason(item))}${item.permit ? ` · permit <code>${esc(item.permit.entry_id || "")}</code>` : ""}${item.deny ? ` · deny <code>${esc(item.deny.entry_id || "")}</code>` : ""}</li>`).join("")}</ul></div>`;
      if (!enabled()) {
        return `<div class="permSchemeCheckView"><p class="permSchemeHelp">${esc(t("权限系统已关闭，没有可判定的权限实例。在“执行设置”中开启权限系统后才能试判定。"))}</p></div>`;
      }
      if (!active) {
        return `<div class="permSchemeCheckView"><p class="permSchemeHelp">${esc(t("试判定在当前启用方案的实例上进行。启用这套方案（并保存全部修改）后才能在这里试判定。"))}</p></div>`;
      }
      return `<div class="permSchemeCheckView">
        <p class="permSchemeHelp">${esc(t("按当前启用方案的实例判定，只看执行者自身的权限。"))}</p>
        <div class="permSchemeInline">
          <label class="portalField"><span>${esc(t("执行者（留空为自己）"))}</span><input data-check="principal" data-focus="check-principal" value="${esc(input.principal)}" placeholder="resident_… / username" /></label>
          <label class="portalField"><span>${esc(t("指令"))}</span><input list="permSchemeInstructions" data-check="instruction" data-focus="check-instruction" value="${esc(input.instruction)}" /></label>
          <datalist id="permSchemeInstructions">${options}</datalist>
        </div>
        <div class="permSchemeInline">${args || (input.instruction ? `<span class="permSchemeMuted">${esc(elementsOf(input.instruction).length ? t("这条指令只有固定槽，没有要填的参数。") : t("Portal 中没有这条指令。"))}</span>` : "")}</div>
        <button type="button" class="portalButton is-primary is-compact" data-check-run data-portal-tooltip="${esc(t("按当前启用方案的实例判定这次调用"))}" ${state.checking || !input.instruction ? "disabled" : ""}>${esc(state.checking ? t("正在判定…") : t("试判定"))}</button>
        ${resultHtml}</div>`;
    }

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

    async function tryDecide() {
      const input = state.checkInput;
      // A dotted name is a nested argument: fields.project_id is {fields: {project_id}}.
      const args = {};
      for (const [name, value] of Object.entries(input.arguments)) {
        if (!value) continue;
        const parts = name.split(".");
        let target = args;
        for (const part of parts.slice(0, -1)) target = target[part] = target[part] && typeof target[part] === "object" ? target[part] : {};
        target[parts.at(-1)] = value;
      }
      state.checking = true;
      render();
      try {
        // principal_id is required; an empty field means the signed-in user.
        const principal = input.principal || (await window.PortalIdentity.load()).username;
        state.checkResult = await run("CheckPermission", {
          principal_id: principal, instruction: input.instruction, arguments: args,
        });
      } catch (error) {
        state.checkResult = {error: error.message || String(error)};
      } finally {
        state.checking = false;
        if (!state.destroyed) render();
      }
    }

    async function act(action) {
      const current = state.current;
      if (action === "discard") {
        if (!(await confirmDiscard())) return;
        setNotice("");
        if (state.stale) { await busy(() => open(current.scheme_id)); return; }
        state.draft = JSON.parse(state.base);
        state.serverErrors = [];
        state.merged = null;
        changed();
      } else if (action === "merge") {
        await busy(async () => {
          // The digest is read before the source's rules: if the source changes in between, saving with it is refused.
          const fresh = await run("GetPermissionScheme", {scheme_id: current.scheme_id});
          if (state.destroyed) return;
          if (fresh.version !== current.version) {
            await open(current.scheme_id);
            setNotice(t("方案已被他人修改，已载入最新版本；请重新合并。"), "error");
            return;
          }
          state.source = fresh.source || null;
          if (!state.source?.outdated) {
            setNotice(state.source?.current_digest ? t("来源方案没有新的修改。") : t("来源方案已删除，没有可合并的修改。"));
            return;
          }
          const source = await run("GetPermissionScheme", {scheme_id: state.source.scheme_id});
          if (state.destroyed) return;
          const changes = sourceChanges(state.source.rules, source.rules, state.draft);
          // Changes this scheme did not touch are taken unless unticked; a conflict waits for a person's choice.
          const picks = new Map(changes.map(change => [change.key, change.conflict ? null : "source"]));
          state.merge = {digest: state.source.current_digest, rules: source.rules, changes, picks};
          state.review = false;
          setNotice("");
        });
      } else if (action === "merge-cancel") {
        state.merge = null;
        render();
      } else if (action === "merge-apply") {
        const merge = state.merge;
        if (!merge || merge.changes.some(change => change.conflict && !merge.picks.get(change.key))) return;
        const draft = clone(state.draft);
        for (const change of merge.changes) if (merge.picks.get(change.key) === "source") applyChange(draft, change, merge.rules);
        state.draft = draft;
        state.merge = null;
        state.merged = {digest: merge.digest, rules: merge.rules};
        changed({rerender: false});
        // The merged rules are checked and the instance difference listed before anything is saved.
        await act("save");
      } else if (action === "last-merge") {
        state.review = !state.review;
        render();
      } else if (action === "copy") {
        await copyScheme(current.scheme_id, state.draft.title);
      } else if (action === "save") {
        await busy(async () => {
          const rules = clone(state.draft);
          const key = JSON.stringify(rules);
          const refuse = result => {
            state.serverErrors = result.errors || [];
            setNotice(t("规则没有通过检查，未保存。"), "error");
          };
          const merge = state.merged ? {source_digest: state.merged.digest} : null;
          try {
            const checked = !merge && lastCheck?.key === key ? lastCheck.result : await checkRules(rules, merge || {});
            if (state.destroyed) return;
            if (checked.valid === false) return refuse(checked);
            // Titles, descriptions and the instruction mapping keep the instance; a changed mapping moves A elements,
            // which the 授权差 reports against the current instance (groups with an element, and remapped).
            // The difference to the instance takes seconds.
            if (checked.regenerates) {
              setNotice(t("正在按 Portal 现有对象计算保存后的实例差异，需要几秒…"));
              render();
            }
            const preview = await previewDiff(rules, merge || {});
            if (state.destroyed) return;
            setNotice("");
            if (preview.valid === false) return refuse(preview);
            const diff = preview.diff || {};
            const auth = preview.auth_diff || null;
            const kept = merge ? {unrestored: preview.unrestored || [], deleted_again: preview.deleted_again || []} : {};
            const reported = auth && (auth.groups?.total || auth.remapped?.total || auth.complete === false);
            if (diff.added?.total || diff.removed?.total || diff.lost_edits?.total || kept.unrestored?.length || kept.deleted_again?.length || reported) {
              state.pendingSave = {rules, diff, auth, regenerates: preview.regenerates !== false, merge: Boolean(merge), ...kept};
            } else await saveRules(rules);
          } catch (error) {
            if (!(merge && error.code === "instruction.conflict")) throw error;
            await mergeRefused();
          }
        });
      } else if (action === "confirm-save") {
        const pending = state.pendingSave;
        if (pending) await busy(() => saveRules(pending.rules));
      } else if (action === "cancel-save") {
        state.pendingSave = null;
        render();
      } else if (action === "activate") {
        // The dry_run's effect and 授权差 are confirmed in the editor; the report never blocks the button.
        await busy(async () => {
          const preview = await run("ActivatePermissionScheme", {scheme_id: current.scheme_id, dry_run: true, diff_limit: DIFF_LIMIT});
          if (state.destroyed) return;
          state.pendingActivate = {scheme_id: current.scheme_id, preview, on: enabled()};
        });
      } else if (action === "confirm-activate") {
        const pending = state.pendingActivate;
        if (!pending) return;
        await busy(async () => {
          await run("ActivatePermissionScheme", {scheme_id: pending.scheme_id}, "permission-scheme-activate");
          state.pendingActivate = null;
          setNotice(pending.on ? t("方案已启用。") : t("开启权限系统时将使用这套方案。"));
          await load(pending.scheme_id);
        });
      } else if (action === "cancel-activate") {
        state.pendingActivate = null;
        render();
      } else if (action === "delete") {
        const approved = await window.PortalUI.confirm({title: tx`删除方案 ${state.draft.title || current.scheme_id}？`, message: t("方案的规则和实例一起删除，无法恢复。"), confirmLabel: t("删除方案"), tone: "danger"});
        if (!approved) return;
        await busy(async () => {
          await run("DeletePermissionScheme", {scheme_id: current.scheme_id}, "permission-scheme-delete", {expectedVersion: current.version});
          state.current = null;
          state.draft = null;
          state.base = "";
          setNotice(t("方案已删除。"));
          await load(state.system?.scheme?.scheme_id || "");
        });
      }
    }

    // A copy takes the rules and the instance as they are saved.
    async function copyScheme(sourceId, sourceTitle) {
      const title = await window.PortalUI.prompt({title: t("复制为新方案"), label: t("新方案名称"), value: tx`${sourceTitle || sourceId} 副本`, confirmLabel: t("下一步")});
      if (!title || state.destroyed) return;
      const taken = new Set(state.schemes.map(item => item.scheme_id));
      const stem = `${String(sourceId).slice(0, 52)}_copy`;
      let suggested = stem;
      for (let suffix = 2; taken.has(suggested); suffix += 1) suggested = `${stem}_${suffix}`;
      const schemeId = await window.PortalUI.prompt({title: t("复制为新方案"), label: t("新方案 ID（小写字母开头，只含小写字母、数字和下划线；保存后不能修改）"), value: suggested, confirmLabel: t("复制")});
      if (!schemeId || state.destroyed) return;
      await busy(async () => {
        const result = await run("CreatePermissionScheme", {scheme_id: String(schemeId).trim(), source_scheme_id: sourceId, title: String(title).trim()}, "permission-scheme-create");
        setNotice(tx`已复制为新方案，实例 ${result.nodes ?? "?"} 个节点、${result.entries ?? "?"} 条表项。`);
        await load(result.scheme_id || String(schemeId).trim());
      });
    }

    async function saveRules(rules) {
      const current = state.current;
      const merged = state.merged;
      try {
        // A merge carries the source digest it was made against: the server writes back the instance edits
        // as an automatic merge does and moves the source record in the same save.
        const result = await run("UpdatePermissionScheme", {scheme_id: current.scheme_id, rules,
          ...(merged ? {source_digest: merged.digest} : {})}, "permission-scheme-update", {expectedVersion: current.version});
        current.version = result.version ?? current.version;
        state.base = JSON.stringify(rules);
        state.pendingSave = null;
        state.serverErrors = [];
        state.merged = null;
        const regenerated = result.regenerated;
        const left = merged && (result.unrestored?.length || result.deleted_again?.length);
        setNotice([regenerated ? tx`规则已保存，实例已重新生成：${regenerated.nodes ?? "?"} 个节点，${regenerated.entries ?? "?"} 条表项。` : t("规则已保存，实例不变。"),
          merged ? t("来源记录已更新到来源现在的规则。") : "", left ? t("没有写回的表项列在下面的上次合并中。") : ""].filter(Boolean).join(" "));
        state.instance = freshInstanceState();
        if (merged) state.source = {...state.source, rules: merged.rules, digest: result.source_digest || merged.digest, outdated: false};
        // The source record now holds this merge as last_merge.
        const [list, fresh] = await Promise.all([run("ListPermissionSchemes"), merged ? run("GetPermissionScheme", {scheme_id: current.scheme_id}) : null]);
        if (state.destroyed) return;
        state.schemes = list.schemes || state.schemes;
        if (fresh?.source) state.source = fresh.source;
        if (left) state.review = true;
      } catch (error) {
        state.pendingSave = null;
        if (error.details?.errors) { state.serverErrors = error.details.errors; setNotice(t("规则没有通过检查，未保存。"), "error"); return; }
        if (merged && error.code === "instruction.conflict") { await mergeRefused(); return; }
        if (error.code !== "instruction.version_conflict") throw error;
        state.stale = true;
        // Portal merges a copy's source when it starts; one that finished during this merge moved the scheme to the
        // same source rules. The conflict cannot tell, so the scheme's last merge is read.
        const last = merged ? (await run("GetPermissionScheme", {scheme_id: current.scheme_id})).source?.last_merge : null;
        if (state.destroyed) return;
        if (last && automatic(last) && last.to_digest === merged.digest) {
          setNotice(t("合并没有保存：Portal 已自动合并了来源的同一批修改。放弃修改即可载入合并后的方案，需要时在“上次自动合并”中调整冲突项。"), "error");
          return;
        }
        setNotice(tx`方案已被他人修改（当前版本 ${error.details?.current_version ?? "?"}）。放弃修改即可载入最新版本。`, "error");
      }
    }

    // The source changed after it was read: the digest no longer matches, so this merge is not saved. The draft
    // stays for reference; the source record is read again so the merge can start over from the saved rules.
    async function mergeRefused() {
      state.merged = null;
      state.pendingSave = null;
      const fresh = await run("GetPermissionScheme", {scheme_id: state.current.scheme_id});
      if (state.destroyed) return;
      state.source = fresh.source || null;
      setNotice(t("合并没有保存：来源方案在读取之后又有修改。已重新读取来源，放弃修改后重新合并来源的修改。"), "error");
    }

    void load();
    return {
      destroy() {
        state.destroyed = true;
        clearTimeout(compileTimer);
        clearTimeout(searchTimer);
        root.remove();
        container.classList.remove("permSchemeHost");
      },
      refresh: async () => { if (dirty() || state.busy) return false; await load(); return true; },
      refreshSystem,
      setSystemEnabled(value) {
        if (state.destroyed || typeof value !== "boolean") return;
        systemRevision++;
        const changed = state.system?.enabled !== value;
        state.system = {...state.system, enabled: value, mode: value ? "par" : "open"};
        if (changed) { state.pendingActivate = null; state.checkResult = null; render(); }
      },
      isBusy: () => state.busy,
      hasUnsavedChanges: () => dirty() || Boolean(state.merged),
      getPrincipalId: () => null,
      setPrincipalId: () => true,
    };
  }

  window.PortalPermissionSchemes = {mount, indexTree, authDiffParts};
})();
