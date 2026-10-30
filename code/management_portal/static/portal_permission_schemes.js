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

  // mount draws the permission scheme page into `container`. Its parts live in
  // module scripts that load with this one; schemesPart(name) returns what a
  // script registers, when the page is mounted, so this script alone still
  // gives indexTree and authDiffParts:
  //   portal_permission_schemes_lookups.js  what the page reads off its state:
  //     unsaved changes, allowed instructions, the vocabulary's A elements,
  //     labels and the notice
  //   portal_permission_schemes_loading.js  reading the system and the scheme
  //     list, opening a scheme, the busy guard
  //   portal_permission_schemes_compile.js  marking the draft changed and the
  //     server's dry_run checks of it
  //   portal_permission_schemes_edits.js  moving, adding and removing tree
  //     nodes, classifying A elements, entry kinds
  //   portal_permission_schemes_drag.js  drag and drop onto tree nodes and
  //     entry cells
  //   portal_permission_schemes_merge_notes.js  a copy's source, merging its
  //     changes and the last merge or release write-back
  //   portal_permission_schemes_review.js  the server's check, and the save and
  //     activation waiting for confirmation
  //   portal_permission_schemes_trees.js  the trees view and a node's detail
  //   portal_permission_schemes_entries.js  the entry rules view
  //   portal_permission_schemes_instructions.js  the instruction map view
  //   portal_permission_schemes_instance.js  the instance view and its writes
  //   portal_permission_schemes_check.js  the try-decide view
  //   portal_permission_schemes_render.js  the scheme list, the editor and its
  //     actions
  //   portal_permission_schemes_editor_events.js  the editor's click, input and
  //     change listeners
  //   portal_permission_schemes_actions.js  saving, discarding, merging,
  //     copying, activating and deleting
  // mount holds the state the parts share, creates the parts in that order
  // (the drag, instance and editor listeners are added in it) and returns the
  // page's controller; a part that calls a function of a part created after it
  // gets an arrow forwarding the call.
  function schemesPart(name) {
    const part = window[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
  }

  function mount({container, onSystemChange}) {
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
    // The values mount reassigns that several parts read: the permission
    // system's read revision, the compile timer and serial, the last check and
    // the node search timer.
    const mountState = {
      systemRevision: 0, compileTimer: 0, compileSerial: 0, lastCheck: null, searchTimer: 0,
    };
    const root = document.createElement("div");
    root.className = "permSchemePage";
    // The page lays itself out by the width of its host (a dialog is narrower than the window).
    container.classList.add("permSchemeHost");
    container.replaceChildren(root);

    const {
      activeLabel, dirty, editable, elementMap, elementsOf, enabled, instructionInfo, instructionNames, isFixed, may,
      principalLabel, relationTitle, setNotice, unmappedNames, unmappedText,
    } = schemesPart("PortalPermissionSchemesLookups").createLookups({ state, t, tx, unmappedCallers });
    const {
      busy, confirmDiscard, load, open, refreshSystem,
    } = schemesPart("PortalPermissionSchemesLoading").createLoading({
      clone, dirty, freshInstanceState, mountState, nodeTitles, onSystemChange, render: (...args) => render(...args),
      run, scheduleCompile: (...args) => scheduleCompile(...args), setNotice, state, t, tx,
    });
    const {
      DIFF_LIMIT, changed, checkRules, previewDiff, scheduleCompile,
    } = schemesPart("PortalPermissionSchemesCompile").createCompile({
      clone, dirty, may, mountState, render: (...args) => render(...args),
      renderStatus: (...args) => renderStatus(...args), run, state,
    });
    const {
      addNode, classify, fitRelation, moveNode, placeInstruction, removeNode, switchEntryKind,
    } = schemesPart("PortalPermissionSchemesEdits").createEdits({
      canHang, changed, elementMap, elementsOf, freeClassification, freeNode, freshNodeId, indexTree, instructionInfo,
      isFixed, isRelationRule, nodeReferences, relationApplies, render: (...args) => render(...args), setNotice,
      settlePlacement, state, t, tx, typesOf,
    });
    const { parse } = schemesPart("PortalPermissionSchemesDrag").createDrag({
      addNode, canHang, changed, editable, fitRelation, indexTree, isRelationRule, moveNode, placeInstruction, root,
      state, typesOf,
    });
    const {
      automatic, deletedAgainHtml, lastMergeHtml, mergeHtml, missingText, releaseNoteHtml, side, sourceNoteHtml,
      unrestoredHtml,
    } = schemesPart("PortalPermissionSchemesMergeNotes").createMergeNotes({
      TREE_TITLES, authDiffHtml, authDiffParts, esc, isTemporary, may, mergeUnits, same, state, t, tx,
    });
    const {
      entryText, instanceNodeLabel, pendingActivateHtml, pendingSaveHtml, staleMappings, validationHtml,
    } = schemesPart("PortalPermissionSchemesReview").createReview({
      authDiffHtml, deletedAgainHtml, dirty, elementMap, esc, may, missingText, nodeTitles, state, t, tx,
      unrestoredHtml,
    });
    const { nodeOptions, treesHtml } = schemesPart("PortalPermissionSchemesTrees").createTrees({
      TREES, TREE_TITLES, canHang, editable, elementMap, esc, freeNode, indexTree, nodeReferences, placementTitle,
      sharedUnders, state, t, tx, typesOf, unmappedText,
    });
    const { entriesHtml } = schemesPart("PortalPermissionSchemesEntries").createEntries({
      TREES, TREE_TITLES, editable, entryId, entryInvalid: (...args) => entryInvalid(...args), esc, indexTree,
      isRelationRule, nodeOptions, principalLabel, relationApplies, relationTitle, state, t, tx, typesOf,
    });
    const { instructionsHtml } = schemesPart("PortalPermissionSchemesInstructions").createInstructions({
      declaredText, editable, elementsOf, esc, freeNode, instructionInfo, instructionNames, isFree, nodeOptions,
      staleMappings, state, t, tx, unmappedNames, unmappedText,
    });
    const { instanceHtml } = schemesPart("PortalPermissionSchemesInstance").createInstance({
      TREES, dirty, entryText, esc, instanceNodeLabel, isTemporary, may, mountState, nodeTitles, parse,
      render: (...args) => render(...args), root, run, state, t, tx,
    });
    const { checkHtml, tryDecide } = schemesPart("PortalPermissionSchemesCheck").createCheck({
      dirty, elementsOf, enabled, esc, instructionNames, render: (...args) => render(...args), run, state, t, tx,
    });
    const { entryInvalid, render, renderStatus } = schemesPart("PortalPermissionSchemesRender").createRender({
      SCHEME_WRITES, activeLabel, checkHtml, dirty, editable, enabled, entriesHtml, esc, instanceHtml,
      instructionsHtml, lastMergeHtml, may, mergeHtml, pendingActivateHtml, pendingSaveHtml, releaseNoteHtml, root,
      sourceNoteHtml, state, t, treesHtml, tx, validationHtml,
    });
    schemesPart("PortalPermissionSchemesEditorEvents").createEditorEvents({
      ENTRY_ID, NODE_ID, act: (...args) => act(...args), addNode, applyChange, busy, canHang, changed, classify,
      confirmDiscard, copyScheme: (...args) => copyScheme(...args), elementMap, entryId, errorTarget, fitRelation,
      freeNode, freshId, indexTree, moveNode, open, parse, placeInstruction, removeNode, renameNode, render, root,
      setNotice, settlePlacement, side, state, switchEntryKind, t, tryDecide, tx, typesOf,
    });
    const { act, copyScheme } = schemesPart("PortalPermissionSchemesActions").createActions({
      DIFF_LIMIT, applyChange, automatic, busy, changed, checkRules, clone, confirmDiscard, enabled,
      freshInstanceState, load, mountState, open, previewDiff, render, run, setNotice, sourceChanges, state, t, tx,
    });

    void load();
    return {
      destroy() {
        state.destroyed = true;
        clearTimeout(mountState.compileTimer);
        clearTimeout(mountState.searchTimer);
        root.remove();
        container.classList.remove("permSchemeHost");
      },
      refresh: async () => { if (dirty() || state.busy) return false; await load(); return true; },
      refreshSystem,
      setSystemEnabled(value) {
        if (state.destroyed || typeof value !== "boolean") return;
        mountState.systemRevision++;
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
