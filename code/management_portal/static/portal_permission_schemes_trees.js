(() => {
  "use strict";

  if (window.PortalPermissionSchemesTrees) return;

  // The trees view: the P, A and R trees with their nodes and A elements,
  // the object types no rule node places, node options for the selects,
  // and the selected node's detail panel.

  const DEPENDENCIES = Object.freeze([
    "TREES", "TREE_TITLES", "canHang", "editable", "elementMap", "esc", "freeNode", "indexTree", "nodeReferences",
    "placementTitle", "sharedUnders", "state", "t", "tx", "typesOf", "unmappedText",
  ]);

  function createTrees(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesTrees is missing: ${missing.join(", ")}`);
    const {
      TREES, TREE_TITLES, canHang, editable, elementMap, esc, freeNode, indexTree, nodeReferences, placementTitle,
      sharedUnders, state, t, tx, typesOf, unmappedText,
    } = dependencies;

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

    return {
      nodeOptions, treesHtml,
    };
  }

  window.PortalPermissionSchemesTrees = Object.freeze({ createTrees, dependencies: DEPENDENCIES });
})();
