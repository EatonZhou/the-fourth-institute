(() => {
  "use strict";

  if (window.PortalPermissionSchemesInstance) return;

  // The instance view: the scheme's table row by row with its filters and
  // pages, editing, adding, disabling and deleting rows, node suggestions,
  // and the view's click, input and change listeners.

  const DEPENDENCIES = Object.freeze([
    "TREES", "dirty", "entryText", "esc", "instanceNodeLabel", "isTemporary", "may", "mountState", "nodeTitles",
    "parse", "render", "root", "run", "state", "t", "tx",
  ]);

  function createInstance(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesInstance is missing: ${missing.join(", ")}`);
    const {
      TREES, dirty, entryText, esc, instanceNodeLabel, isTemporary, may, mountState, nodeTitles, parse, render, root,
      run, state, t, tx,
    } = dependencies;

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

    // Suggests instance nodes for a P, A or R input: class nodes by ID or title, objects by ID.
    function suggestNodes(tree, query) {
      clearTimeout(mountState.searchTimer);
      if (!query.trim() || !state.current) return;
      mountState.searchTimer = setTimeout(async () => {
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

    return {
      instanceHtml,
    };
  }

  window.PortalPermissionSchemesInstance = Object.freeze({ createInstance, dependencies: DEPENDENCIES });
})();
