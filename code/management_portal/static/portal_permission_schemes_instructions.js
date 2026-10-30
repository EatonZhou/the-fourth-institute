(() => {
  "use strict";

  if (window.PortalPermissionSchemesInstructions) return;

  // The instruction map view: each instruction with its A elements and
  // their classification, the filters, the drop bar and an instruction's
  // element editor.

  const DEPENDENCIES = Object.freeze([
    "declaredText", "editable", "elementsOf", "esc", "freeNode", "instructionInfo", "instructionNames", "isFree",
    "nodeOptions", "staleMappings", "state", "t", "tx", "unmappedNames", "unmappedText",
  ]);

  function createInstructions(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesInstructions is missing: ${missing.join(", ")}`);
    const {
      declaredText, editable, elementsOf, esc, freeNode, instructionInfo, instructionNames, isFree, nodeOptions,
      staleMappings, state, t, tx, unmappedNames, unmappedText,
    } = dependencies;

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

    return {
      instructionsHtml,
    };
  }

  window.PortalPermissionSchemesInstructions = Object.freeze({ createInstructions, dependencies: DEPENDENCIES });
})();
