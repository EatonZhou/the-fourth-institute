(() => {
  "use strict";

  if (window.PortalPermissionSchemesEntries) return;

  // The entry rules view: one card per entry rule, read-only or with its
  // controls, and the palette of nodes to drag into its cells.

  const DEPENDENCIES = Object.freeze([
    "TREES", "TREE_TITLES", "editable", "entryId", "entryInvalid", "esc", "indexTree", "isRelationRule",
    "nodeOptions", "principalLabel", "relationApplies", "relationTitle", "state", "t", "tx", "typesOf",
  ]);

  function createEntries(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesEntries is missing: ${missing.join(", ")}`);
    const {
      TREES, TREE_TITLES, editable, entryId, entryInvalid, esc, indexTree, isRelationRule, nodeOptions,
      principalLabel, relationApplies, relationTitle, state, t, tx, typesOf,
    } = dependencies;

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

    return {
      entriesHtml,
    };
  }

  window.PortalPermissionSchemesEntries = Object.freeze({ createEntries, dependencies: DEPENDENCIES });
})();
