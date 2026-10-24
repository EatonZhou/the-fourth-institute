(() => {
  "use strict";

  if (window.PortalHatgRelations) return;

  // Relations between Residents on the HATG canvas: the relation inspector, and
  // handoffs, from picking source and target on the canvas through editing the
  // handed-over items to saving, retrying and removing the relation.
  const DEPENDENCIES = Object.freeze([
    "$id", "clone", "closeModal", "commitLineage", "compactText", "displayEdgeById",
    "displayNodeById", "edgeById", "esc", "fillWorkspaceSelect", "formatBytes",
    "loadWorkspaceGraphBindings", "nodeById", "nodeResidentId", "openModal", "programFeature",
    "publishLocation", "RELATION_LABEL", "relationFamily", "renderControls", "renderGraph",
    "renderGraphModeHint", "renderGraphSelection", "renderLineageSurface",
    "renderProgramInspector", "renderSeedInspector", "seedById", "seedTypeLabel",
    "selectedBoundWorkspace", "selectNode", "selectSeed", "setBusy", "setGraphViewMode",
    "setSeedCandidateFork", "societyService", "state", "toast", "uid",
  ]);

  const MAX_HANDOFF_ITEMS = 24;

  function handoffEditorFieldId(base, index) {
    return index === 0 ? base : `${base}_${index + 1}`;
  }

  function handoffEditorField(card, name) {
    return card?.querySelector(`[data-handoff-field="${name}"]`) || null;
  }

  function handoffStatusMeta(edge) {
    const executionStatus = String(edge?.execution?.status || "");
    if (edge?.status === "failed" || executionStatus === "failed") return { label: window.PortalI18n.t("失败"), tone: "failed" };
    if (edge?.status === "preparing") return { label: window.PortalI18n.t("准备中"), tone: "active" };
    if (edge?.status === "ready") return { label: window.PortalI18n.t("已就绪"), tone: "ready" };
    if (edge?.status === "draft") return { label: window.PortalI18n.t("草稿"), tone: "waiting" };
    return { label: edge?.status || window.PortalI18n.t("未记录"), tone: "waiting" };
  }

  function handoffMethodLabel(method) {
    return ({
      direct: window.PortalI18n.t("共享目录"),
      filesystem_copy: window.PortalI18n.t("目录复制"),
      git_worktree: window.PortalI18n.t("Git worktree"),
    })[method] || method || "direct";
  }

  function handoffWorkspaceLabel(path, fallback = "Workspace") {
    const normalized = String(path || "").replace(/\/+$/, "");
    return normalized.split("/").filter(Boolean).at(-1) || String(fallback || "Workspace");
  }

  function handoffPreparationSteps(edge) {
    return [
      {
        title: window.PortalI18n.t("交接契约已保存"),
        detail: "Handoff 已成为图中的 Workspace 关系",
        state: "done",
      },
      {
        title: window.PortalI18n.t("准备 Workspace"),
        detail: edge.status === "failed"
          ? (edge.error || "准备失败")
          : edge.status === "ready"
            ? "所有 item 已准备并留存证据"
            : edge.status === "preparing"
              ? "materializer 正在运行"
              : "尚未执行 · 等待用户启动准备",
        state: edge.status === "failed"
          ? "failed"
          : edge.status === "ready"
            ? "done"
            : edge.status === "preparing"
              ? "active"
              : "waiting",
      },
      {
        title: window.PortalI18n.t("写入目标 Binding"),
        detail: edge.status === "ready" ? "Workspace 与 Binding 已就绪" : "只在准备成功后写入",
        state: edge.status === "ready" ? "done" : "waiting",
      },
    ];
  }

  function createRelations(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgRelations is missing: ${missing.join(", ")}`);
    const {
      $id, clone, closeModal, commitLineage, compactText, displayEdgeById, displayNodeById,
      edgeById, esc, fillWorkspaceSelect, formatBytes, loadWorkspaceGraphBindings, nodeById,
      nodeResidentId, openModal, programFeature, publishLocation, RELATION_LABEL, relationFamily,
      renderControls, renderGraph, renderGraphModeHint, renderGraphSelection,
      renderLineageSurface, renderProgramInspector, renderSeedInspector, seedById, seedTypeLabel,
      selectedBoundWorkspace, selectNode, selectSeed, setBusy, setGraphViewMode,
      setSeedCandidateFork, societyService, state, toast, uid,
    } = dependencies;

    function activateHandoffMode() {
      if (!state.lineage || state.lineage.nodes.length < 2) return;
      state.handoffMode = !state.handoffMode;
      state.handoffSourceId = "";
      state.handoffTargetId = "";
      if (state.handoffMode) {
        state.selectedEdgeId = "";
        state.graphRelationFilter = "all";
      }
      renderGraphModeHint();
      renderLineageSurface();
    }

    function cancelHandoffMode() {
      state.handoffMode = false;
      state.handoffSourceId = "";
      state.handoffTargetId = "";
      renderGraphModeHint();
      renderGraph();
      renderControls();
    }

    async function handleHandoffNodeClick(nodeId) {
      if (!state.handoffSourceId) {
        state.handoffSourceId = nodeId;
        state.selectedNodeId = nodeId;
        renderGraphModeHint();
        renderGraph();
        return;
      }
      if (state.handoffSourceId === nodeId) return toast(window.PortalI18n.t("交接目标不能与来源节点相同。"), "warning");
      state.handoffTargetId = nodeId;
      const existing = state.lineage?.edges?.find(edge => (
        edge.type === "handoff"
        && edge.source === state.handoffSourceId
        && edge.target === state.handoffTargetId
      ));
      renderGraphModeHint();
      renderGraph();
      try {
        const bindings = await loadWorkspaceGraphBindings(state.lineage, { render: false, fit: false });
        if (!bindings.complete) throw new Error("部分 Agent 的 Workspace Binding 尚未读取完成");
        if (!state.handoffMode || state.handoffTargetId !== nodeId) return;
        openHandoffModal(existing?.id || "");
      } catch (error) {
        state.handoffTargetId = "";
        renderGraphModeHint();
        renderGraph();
        toast(window.PortalI18n.text`无法配置 Handoff：${error.message || error}`, "error", 6200);
      }
    }

    function handoffRouteHtml(source, target) {
      return window.PortalI18n.html`<div class="handoffRouteNode"><strong>${esc(source?.title || window.PortalI18n.t("来源"))}</strong><small>${esc(nodeResidentId(source))}</small></div><div class="handoffRouteArrow">→</div><div class="handoffRouteNode"><strong>${esc(target?.title || window.PortalI18n.t("目标"))}</strong><small>${esc(nodeResidentId(target))}</small></div>`;
    }

    function newHandoffEditorItem(method = "direct", itemId = "") {
      const resolvedMethod = ["direct", "filesystem_copy", "git_worktree"].includes(method)
        ? method
        : "direct";
      return {
        item_id: itemId || uid("workspace"),
        label: "",
        source: { workspace_id: "", path: "", scope: "." },
        transfer: {
          mode: resolvedMethod === "direct" ? "direct" : "copy",
          method: resolvedMethod,
          parameters: {
            branch: "",
            base_commit: "",
            include_worktree_changes: true,
            exclude: [],
          },
        },
        target: { workspace_id: "", path: "", scope: ".", label: "" },
        attempts: [],
        result: {},
      };
    }

    function handoffItemEditorHtml(item, index, locked, total) {
      const disabled = locked ? " disabled" : "";
      const removeHidden = locked || total <= 1 ? " hidden" : "";
      const itemId = String(item?.item_id || (index === 0 ? "workspace" : uid("workspace")));
      return window.PortalI18n.html`
      <section class="handoffItemCard" data-handoff-item data-handoff-item-id="${esc(itemId)}">
        <div class="handoffItemHead">
          <strong>交接项 ${index + 1}</strong>
          <button class="handoffItemRemoveBtn" type="button" data-remove-handoff-item="${index}"${removeHidden}${disabled}>移除</button>
        </div>
        <label>来源 Workspace<select id="${handoffEditorFieldId("handoffSourceWorkspaceInput", index)}" data-handoff-field="source-workspace" required${disabled}></select><small>只能选择来源节点已经绑定的 Workspace。</small></label>
        <label>交接内容范围<input id="${handoffEditorFieldId("handoffSourceScopeInput", index)}" data-handoff-field="source-scope" maxlength="800" spellcheck="false" placeholder="整个 Workspace 填 .；也可填写相对目录或文件"${disabled} /></label>
        <div class="handoffMethodGrid">
          <label>交接方式<select id="${handoffEditorFieldId("handoffMaterializerInput", index)}" data-handoff-field="materializer"${disabled}><option value="direct">共享同一目录</option><option value="filesystem_copy">复制为普通目录</option><option value="git_worktree">创建 Git worktree</option></select></label>
          <label>目标 Workspace 名称<input id="${handoffEditorFieldId("handoffTargetLabelInput", index)}" data-handoff-field="target-label" maxlength="160" placeholder="例如：探索版 2 · Portal"${disabled} /></label>
        </div>
        <div class="handoffCopyFields" id="${handoffEditorFieldId("handoffCopyFields", index)}" data-handoff-field="copy-fields" hidden>
          <label>目标绝对路径<input id="${handoffEditorFieldId("handoffTargetPathInput", index)}" data-handoff-field="target-path" maxlength="4096" spellcheck="false" placeholder="留空时由 Lineage 在安全目录中生成"${disabled} /></label>
          <div class="handoffGitFields" id="${handoffEditorFieldId("handoffGitFields", index)}" data-handoff-field="git-fields" hidden>
            <label>Git 分支<input id="${handoffEditorFieldId("handoffGitBranchInput", index)}" data-handoff-field="git-branch" maxlength="180" spellcheck="false" placeholder="例如：explore/2-lineage-ui"${disabled} /></label>
            <label>基线 commit（可选）<input id="${handoffEditorFieldId("handoffGitBaseInput", index)}" data-handoff-field="git-base" maxlength="160" spellcheck="false" placeholder="留空使用创建 Handoff 时的来源 HEAD"${disabled} /></label>
            <label class="toggleField"><input id="${handoffEditorFieldId("handoffIncludeChangesInput", index)}" data-handoff-field="include-changes" type="checkbox"${disabled} /><span><strong>带上来源当前未提交改动</strong><small>精确 commit 之外的 patch 与安全 untracked 文件也复制到新 worktree。</small></span></label>
          </div>
        </div>
      </section>`;
    }

    function renderHandoffItemEditors(items, source, { locked = false } = {}) {
      const normalizedItems = (Array.isArray(items) && items.length
        ? items.slice(0, MAX_HANDOFF_ITEMS)
        : [newHandoffEditorItem(state.handoffPreset || "direct", "workspace")]
      ).map((item, index) => ({
        ...clone(item),
        item_id: String(item?.item_id || (index === 0 ? "workspace" : uid("workspace"))),
      }));
      state.handoffEditorItems = normalizedItems;
      state.handoffPreparationLocked = locked;
      const editor = $id("handoffItemsEditor");
      editor.innerHTML = normalizedItems
        .map((item, index) => handoffItemEditorHtml(item, index, locked, normalizedItems.length))
        .join("");
      [...editor.querySelectorAll("[data-handoff-item]")].forEach((card, index) => {
        const item = normalizedItems[index] || {};
        const transfer = item.transfer && typeof item.transfer === "object" ? item.transfer : {};
        const parameters = transfer.parameters && typeof transfer.parameters === "object" ? transfer.parameters : {};
        const target = item.target && typeof item.target === "object" ? item.target : {};
        const sourceItem = item.source && typeof item.source === "object" ? item.source : {};
        fillWorkspaceSelect(
          handoffEditorField(card, "source-workspace"),
          source.id,
          sourceItem.workspace_id || "",
        );
        handoffEditorField(card, "source-scope").value = sourceItem.scope || ".";
        handoffEditorField(card, "materializer").value = (
          ["direct", "filesystem_copy", "git_worktree"].includes(transfer.method)
            ? transfer.method
            : "direct"
        );
        handoffEditorField(card, "target-label").value = target.label || "";
        handoffEditorField(card, "target-path").value = target.path || "";
        handoffEditorField(card, "git-branch").value = parameters.branch || "";
        handoffEditorField(card, "git-base").value = parameters.base_commit || "";
        handoffEditorField(card, "include-changes").checked = parameters.include_worktree_changes === true;
      });
      $id("handoffItemsCount").textContent = `${normalizedItems.length} / ${MAX_HANDOFF_ITEMS}`;
      const addButton = $id("handoffAddItemBtn");
      addButton.disabled = locked || normalizedItems.length >= MAX_HANDOFF_ITEMS;
      addButton.title = locked
        ? window.PortalI18n.t("Workspace 准备完成后不能再修改交接范围")
        : normalizedItems.length >= MAX_HANDOFF_ITEMS
          ? window.PortalI18n.text`每个 Handoff 最多 ${MAX_HANDOFF_ITEMS} 个交接项`
          : window.PortalI18n.t("添加另一项 Workspace、目录或文件");
      syncHandoffMaterializerFields();
    }

    function collectHandoffEditorItems(source, target, { validate = false } = {}) {
      const previousById = new Map(
        (state.handoffEditorItems || []).map(item => [String(item?.item_id || ""), item]),
      );
      return [...$id("handoffItemsEditor").querySelectorAll("[data-handoff-item]")].map((card, index) => {
        const itemNumber = index + 1;
        const itemId = String(card.dataset.handoffItemId || (index === 0 ? "workspace" : uid("workspace")));
        const previous = previousById.get(itemId) || newHandoffEditorItem("direct", itemId);
        const workspaceSelect = handoffEditorField(card, "source-workspace");
        const sourceWorkspace = selectedBoundWorkspace(workspaceSelect, source.id);
        const method = handoffEditorField(card, "materializer").value || "direct";
        const sourceScope = handoffEditorField(card, "source-scope").value.trim() || ".";
        const targetPath = handoffEditorField(card, "target-path").value.trim();
        const branch = handoffEditorField(card, "git-branch").value.trim();
        if (validate && !sourceWorkspace) {
          throw new Error(`交接项 ${itemNumber}：请选择来源节点真实绑定的 Workspace。`);
        }
        if (validate && method === "git_worktree" && sourceScope !== ".") {
          throw new Error(`交接项 ${itemNumber}：Git worktree 只能交接整个 Workspace，请将范围设为 .。`);
        }
        if (validate && method === "git_worktree" && !branch) {
          throw new Error(`交接项 ${itemNumber}：Git worktree 需要填写目标分支。`);
        }
        if (validate && method !== "direct" && targetPath && !targetPath.startsWith("/")) {
          throw new Error(`交接项 ${itemNumber}：目标 Workspace 必须是绝对路径。`);
        }
        const previousSource = previous.source && typeof previous.source === "object" ? previous.source : {};
        const previousTarget = previous.target && typeof previous.target === "object" ? previous.target : {};
        const previousTransfer = previous.transfer && typeof previous.transfer === "object" ? previous.transfer : {};
        const previousParameters = (
          previousTransfer.parameters && typeof previousTransfer.parameters === "object"
            ? previousTransfer.parameters
            : {}
        );
        const sourceRecord = sourceWorkspace || previousSource;
        return {
          item_id: itemId,
          label: sourceWorkspace?.label || previous.label || window.PortalI18n.text`Workspace ${itemNumber}`,
          source: {
            workspace_id: sourceWorkspace?.workspace_id || previousSource.workspace_id || "",
            path: sourceWorkspace?.path || previousSource.path || "",
            scope: sourceScope,
          },
          transfer: {
            mode: method === "direct" ? "direct" : "copy",
            method,
            parameters: {
              ...previousParameters,
              branch,
              base_commit: handoffEditorField(card, "git-base").value.trim(),
              include_worktree_changes: handoffEditorField(card, "include-changes").checked,
            },
          },
          target: {
            workspace_id: previousTarget.workspace_id || "",
            path: method === "direct" ? sourceRecord.path || "" : targetPath,
            scope: previousTarget.scope || ".",
            label: handoffEditorField(card, "target-label").value.trim() || `${target.title} · Workspace`,
          },
          attempts: Array.isArray(previous.attempts) ? clone(previous.attempts) : [],
          result: previous.result && typeof previous.result === "object" ? clone(previous.result) : {},
        };
      });
    }

    function addHandoffItem() {
      if (state.handoffPreparationLocked) return;
      const source = nodeById(state.handoffSourceId);
      if (!source) return;
      state.handoffEditorItems = collectHandoffEditorItems(source, nodeById(state.handoffTargetId) || {}, { validate: false });
      if (state.handoffEditorItems.length >= MAX_HANDOFF_ITEMS) {
        toast(window.PortalI18n.text`每个 Handoff 最多可以包含 ${MAX_HANDOFF_ITEMS} 个交接项。`, "warning");
        return;
      }
      state.handoffEditorItems.push(newHandoffEditorItem(state.handoffPreset || "direct"));
      renderHandoffItemEditors(state.handoffEditorItems, source);
      const lastCard = $id("handoffItemsEditor").querySelector("[data-handoff-item]:last-child");
      handoffEditorField(lastCard, "source-workspace")?.focus();
    }

    function removeHandoffItem(button) {
      if (state.handoffPreparationLocked) return;
      const source = nodeById(state.handoffSourceId);
      const target = nodeById(state.handoffTargetId);
      if (!source || !target) return;
      state.handoffEditorItems = collectHandoffEditorItems(source, target, { validate: false });
      if (state.handoffEditorItems.length <= 1) return;
      const index = Number(button?.dataset.removeHandoffItem);
      if (!Number.isInteger(index) || index < 0 || index >= state.handoffEditorItems.length) return;
      state.handoffEditorItems.splice(index, 1);
      renderHandoffItemEditors(state.handoffEditorItems, source);
    }

    function syncHandoffMaterializerFields(itemCard = null) {
      const cards = itemCard
        ? [itemCard]
        : [...$id("handoffItemsEditor").querySelectorAll("[data-handoff-item]")];
      for (const card of cards) {
        const method = handoffEditorField(card, "materializer")?.value || "direct";
        handoffEditorField(card, "copy-fields").hidden = method === "direct";
        handoffEditorField(card, "git-fields").hidden = method !== "git_worktree";
        handoffEditorField(card, "target-path").placeholder = method === "git_worktree"
          ? window.PortalI18n.t("例如：/path/to/worktree")
          : window.PortalI18n.t("留空时由 Lineage 在安全目录中生成");
      }
      const methods = new Set(
        [...$id("handoffItemsEditor").querySelectorAll('[data-handoff-field="materializer"]')]
          .map(select => select.value || "direct"),
      );
      $id("directHandoffWorkspaceText").textContent = methods.size > 1
        ? window.PortalI18n.t("各交接项会按自己的方式准备；全部成功并形成可验证的目标 Workspace Binding 后，Handoff 才会就绪。")
        : methods.has("direct")
          ? window.PortalI18n.t("目标节点新增指向同一目录的 Workspace Binding；适合串行继续或明确协调写入。")
          : methods.has("git_worktree")
            ? window.PortalI18n.t("系统从精确 commit 创建分支与 worktree；默认把 Dev 当前未提交改动也带入目标。")
            : window.PortalI18n.t("系统复制所选范围，排除敏感文件、依赖缓存和符号链接，并记录内容指纹。");
    }

    function syncHandoffSubmitLabel() {
      const button = $id("handoffSubmitBtn");
      if (!button || button.hidden) return;
      const edge = edgeById(state.editingEdgeId);
      button.textContent = $id("handoffSendNowInput").checked
        ? edge?.status === "failed" ? window.PortalI18n.t("保存并重试") : window.PortalI18n.t("保存并准备 Workspace")
        : window.PortalI18n.t("仅保存 Handoff");
    }

    async function openHandoffModal(edgeId = "") {
      const edge = edgeId ? edgeById(edgeId) : null;
      const source = nodeById(edge?.source || state.handoffSourceId);
      const target = nodeById(edge?.target || state.handoffTargetId);
      if (!source || !target) return;
      const inheritance = edge?.type === "inheritance";
      if (!inheritance) {
        try {
          const bindings = await loadWorkspaceGraphBindings(state.lineage, { render: false, fit: false });
          if (!bindings.complete) throw new Error("部分 Agent 的 Workspace Binding 尚未读取完成");
        } catch (error) {
          toast(window.PortalI18n.text`无法配置 Handoff：${error.message || error}`, "error", 6200);
          return;
        }
      }
      state.editingEdgeId = edge?.id || "";
      state.handoffSourceId = source.id;
      state.handoffTargetId = target.id;
      $id("handoffModalTitle").textContent = inheritance ? window.PortalI18n.t("轨迹继承详情") : edge ? window.PortalI18n.t("编辑 Workspace Handoff") : window.PortalI18n.t("建立 Workspace Handoff");
      $id("handoffRoute").innerHTML = handoffRouteHtml(source, target);
      $id("directHandoffWorkspaceInfo").hidden = inheritance;
      const preparationLocked = inheritance || edge?.status === "ready" || edge?.status === "preparing";
      const initialItems = edge?.items?.length
        ? edge.items
        : [newHandoffEditorItem(inheritance ? "direct" : state.handoffPreset || "direct", "workspace")];
      renderHandoffItemEditors(initialItems, source, { locked: preparationLocked });
      $id("handoffAddItemBtn").hidden = inheritance;
      $id("handoffExplanationField").hidden = inheritance;
      $id("handoffExplanationInput").disabled = inheritance;
      $id("handoffExplanationInput").value = edge?.explanation || "";
      $id("handoffSendNowInput").checked = !edge || ["draft", "failed"].includes(edge?.status);
      $id("handoffSendNowInput").closest("label").hidden = inheritance;
      $id("handoffSubmitBtn").hidden = inheritance;
      $id("removeEdgeBtn").hidden = !edge;
      syncHandoffSubmitLabel();
      openModal("handoffModal");
    }

    async function submitHandoff(event) {
      event.preventDefault();
      const source = nodeById(state.handoffSourceId);
      const target = nodeById(state.handoffTargetId);
      if (!source || !target || !state.lineage) return;
      // The button asks for its own instruction on the Lineage listing; an unlisted Lineage is left to the server.
      const listed = (state.lineages || []).find(item => item.lineage_id === state.lineage.lineage_id);
      if (listed?.allows?.CreateWorkspaceHandoff === false) {
        return toast(window.PortalI18n.t("你没有在这个 Lineage 中创建 Handoff 的权限。"), "warning", 6200);
      }
      const button = $id("handoffSubmitBtn");
      const executeNow = $id("handoffSendNowInput").checked;
      const existing = edgeById(state.editingEdgeId);
      const preparationEditable = !existing || ["draft", "failed"].includes(existing.status);
      let items;
      try {
        items = preparationEditable
          ? collectHandoffEditorItems(source, target, { validate: true })
          : clone(existing.items || []);
      } catch (error) {
        return toast(error.message || String(error), "warning", 5600);
      }
      if (!items.length) return toast(window.PortalI18n.t("请至少添加一个交接项。"), "warning");
      state.handoffEditorItems = clone(items);
      const methods = new Set(items.map(item => item.transfer?.method || "direct"));
      setBusy(button, true, "保存交接…");
      try {
        let response;
        if (existing) {
          response = await societyService.updateHandoff({
            lineage_id: state.lineage.lineage_id,
            handoff_id: existing.id,
            changes: {
              explanation: $id("handoffExplanationInput").value.trim(),
              ...(preparationEditable ? { items } : {}),
            },
          });
          if (executeNow) {
            response = await societyService.executeHandoff({ lineage_id: state.lineage.lineage_id, handoff_id: existing.id });
          }
        } else {
          response = await societyService.createHandoff({
            lineage_id: state.lineage.lineage_id,
            source_resident_id: source.id,
            target_resident_id: target.id,
            label: methods.size > 1
              ? window.PortalI18n.t("混合 Workspace Handoff")
              : methods.has("direct")
                ? window.PortalI18n.t("共享 Workspace Handoff")
                : window.PortalI18n.t("独立 Workspace Handoff"),
            explanation: $id("handoffExplanationInput").value.trim(),
            items,
          });
          if (executeNow && response?.handoff_id) {
            response = await societyService.executeHandoff({ lineage_id: state.lineage.lineage_id, handoff_id: response.handoff_id });
          }
        }
        commitLineage(response.lineage);
        await Promise.all([
          loadWorkspaceGraphBindings(state.lineage),
        ]);
        closeModal("handoffModal");
        cancelHandoffMode();
        // A preparation reports its status at the top level, and a failed
        // preparation is its recorded result; a plain update reports the Handoff.
        const status = executeNow ? response.status : response.handoff?.status;
        if (status === "ready") {
          toast(window.PortalI18n.t("Handoff 已就绪，目标 Workspace Binding 已验证。"), "success", 5000);
        } else if (status === "failed") {
          toast(window.PortalI18n.text`Handoff 已保存，Workspace 准备失败：${response.error || window.PortalI18n.t("未知错误")}`, "error", 7200);
        } else if (status === "preparing") {
          toast(window.PortalI18n.t("正在准备 Workspace"), "success", 5000);
        } else {
          toast(window.PortalI18n.t("Handoff 已保存为草稿。"), "success", 5000);
        }
      } catch (error) {
        try {
          commitLineage(await societyService.loadLineage(state.lineage.lineage_id));
        } catch {}
        toast(window.PortalI18n.text`Handoff 未完成：${error.message || error}`, "error", 7200);
      } finally {
        setBusy(button, false);
      }
    }

    async function removeEditingEdge() {
      const edge = edgeById(state.editingEdgeId);
      if (!edge || !state.lineage) return;
      if (!await window.PortalUI.confirm({
        title: window.PortalI18n.t("移除关系？"),
        message: window.PortalI18n.text`移除关系「${edge.label}」？这不会删除两端 Agent。`,
        confirmLabel: window.PortalI18n.t("移除"),
        tone: "danger",
      })) return;
      closeModal("handoffModal");
      cancelHandoffMode();
      try {
        const response = await societyService.deleteHandoff({ lineage_id: state.lineage.lineage_id, handoff_id: edge.id });
        commitLineage(response.lineage);
        toast(window.PortalI18n.t("交接已从 Lineage 中移除。"), "warning");
      } catch (error) {
        toast(window.PortalI18n.text`移除交接失败：${error.message || error}`, "error");
      }
    }

    function selectRelationEdge(edgeId) {
      const edge = displayEdgeById(edgeId) || edgeById(edgeId);
      if (!edge) return;
      state.selectedEdgeId = edge.id;
      state.selectedSeedId = "";
      state.selectedProgramObjectId = "";
      programFeature.clearSelection();
      publishLocation(state.lineage?.lineage_id || "");
      if (!renderGraphSelection()) renderGraph();
      renderProgramInspector();
      renderSeedInspector();
    }

    function candidateForkInspectorMarkup(edge, source) {
      const seed = seedById(edge.target);
      const seedTitle = seed?.display_title || seed?.title || edge.target;
      const readonly = state.programMutationInFlight;
      return window.PortalI18n.html`<div class="relationInspectorHead">
        <span class="relationInspectorEyebrow"><i class="relationFamilyDot context"></i>CONTEXT RELATION</span>
        <button type="button" data-close-relation-inspector aria-label="关闭关系检查器" data-portal-icon="close"></button>
      </div>
      <div class="relationInspectorScroll">
        <div class="relationInspectorHero">
          <div class="relationInspectorType"><span>${esc(RELATION_LABEL.candidate_fork)}</span><em>candidate_fork</em></div>
          <h3>${esc(edge.label || RELATION_LABEL.candidate_fork)}</h3>
          <div class="relationInspectorRoute">
            <button type="button" data-relation-node="${esc(edge.source)}"><small>来源 Resident</small><strong>${esc(source?.title || edge.source)}</strong><span>${esc(nodeResidentId(source) || edge.source)}</span></button>
            <i>→</i>
            <button type="button" data-relation-seed="${esc(edge.target)}"><small>Seed</small><strong>${esc(seedTitle)}</strong><span>${esc(seed ? seedTypeLabel(seed) : edge.target)}</span></button>
          </div>
        </div>
        <section class="relationInspectorSection">
          <header><span>RELATION CONTRACT</span><small>3 fields</small></header>
          <dl class="relationInspectorFacts">
            <dt>候选从哪里出发</dt><dd>${esc(source?.title || edge.source)} 的工作</dd>
            <dt>采纳时</dt><dd>Fork ${esc(source?.title || edge.source)}，这条关系变成到新 Resident 的 Fork 继承</dd>
            <dt>候选文件</dt><dd>${esc(seed?.artifact_path || "—")}</dd>
          </dl>
        </section>
        <p class="relationInspectorNote">一个 Seed 只有一个候选 Fork。改换来源：把另一个 Resident 的“+”拖到这个 Seed 上，或在 Seed 设置中选择。</p>
        <div class="relationInspectorActions" data-portal-inbox-avoid>
          <button class="portalButton is-danger is-quiet" type="button" data-delete-candidate-fork${readonly ? " disabled" : ""}>删除候选 Fork</button>
        </div>
      </div>`;
    }

    function relationInspectorRows(edge) {
      if (edge.type === "inheritance") {
        const target = nodeById(edge.target);
        return [
          ["Resident", target?.resident_id || edge.target || "—"],
          ["创建事实", edge.truth?.verified ? "已核验：目标 Resident 由 Fork 操作创建" : "尚未核验"],
          ["继承范围", edge.label || "完整轨迹"],
        ];
      }
      if (edge.type === "absorption") {
        return [
          ["真实动作", "向目标 Agent 发送一条包含来源 Resident ID 的普通 Prompt"],
          ["资源读取", "沿用全局统一资源引用与 ResourceService 链路"],
          ["来源统一 ID", edge.absorption?.source_resident_id || edge.source || "—"],
          ["目标统一 ID", edge.absorption?.target_resident_id || edge.target || "—"],
          ["Prompt 内容", "不内嵌来源对话、读取命令或 Portal 实现细节"],
          ["投递状态", edge.absorption?.status === "failed" ? `失败：${edge.absorption?.error || "目标运行时不可用"}` : "普通 Prompt 已进入目标节点队列"],
          ["执行次数", `${edge.absorption?.run_count || 1} 次`],
        ];
      }
      if (edge.type === "handoff") return [
        ["Handoff 状态", ({ draft: "草稿", preparing: "正在准备 Workspace", ready: "Workspace 与 Binding 已就绪", failed: `失败：${edge.error || "未知错误"}` })[edge.status] || edge.status || "未记录"],
        ["准备方式", ({ direct: "共享同一目录", filesystem_copy: "普通目录复制", git_worktree: "Git worktree" })[edge.items?.[0]?.transfer?.method] || "未记录"],
        ["Git 分支 / 基线", edge.items?.[0]?.transfer?.method === "git_worktree"
          ? `${edge.items?.[0]?.result?.git?.branch || edge.items?.[0]?.transfer?.parameters?.branch || "—"} · ${compactText(edge.items?.[0]?.result?.git?.base_commit || edge.items?.[0]?.transfer?.parameters?.base_commit || "HEAD", 16)}`
          : "不适用"],
        ["来源内容", `${edge.items?.[0]?.source?.path || "—"} · ${edge.items?.[0]?.source?.scope || "."}`],
        ["目标 Binding", edge.items?.[0]?.result?.target_path || edge.items?.[0]?.target?.path || "—"],
        ["准备 attempt", edge.items?.[0]?.attempts?.length ? `第 ${edge.items[0].attempts.length} 次 · ${edge.items[0].attempts.at(-1)?.status || "unknown"}` : "尚未执行"],
        ["内容指纹", edge.items?.[0]?.result?.content_hash || "共享目录不冻结内容"],
        ["规模", edge.items?.[0]?.result ? `${edge.items[0].result.file_count || 0} 个文件 · ${formatBytes(edge.items[0].result.byte_size) || "0 B"}` : "—"],
      ];
      return [];
    }

    function relationInspectorEditorMarkup(edge, source, target) {
      const disabled = "";
      const deleteAndSave = window.PortalI18n.html`<button class="portalButton is-danger is-quiet" type="button" data-delete-relation>删除关系</button>
         <button class="portalButton is-primary" type="submit" data-save-relation>保存</button>`;
      if (edge.type === "handoff") {
        return window.PortalI18n.html`<form class="relationInspectorForm" data-relation-inspector-form data-edge-type="handoff">
        <label><span>标签</span><input name="label" maxlength="120" value="${esc(edge.label || "")}"${disabled}></label>
        <label><span>交接解释（选填）</span><textarea name="explanation" maxlength="8000"${disabled}>${esc(edge.explanation || "")}</textarea></label>
        <p class="relationInspectorNote">已经执行的 Workspace materialization 与证据保持不可变；草稿或失败状态仍可用“准备 / 重试”继续。后续 Agent 动作由 HATG Program 的 Event 与 Effect 明确表达。</p>
        <div class="relationInspectorActions" data-portal-inbox-avoid>${deleteAndSave}</div>
      </form>`;
      }
      return "";
    }

    function handoffInspectorMarkup(edge, source, target) {
      const meta = handoffStatusMeta(edge);
      const items = Array.isArray(edge.items) ? edge.items : [];
      const attemptCount = items.reduce((total, item) => total + (item.attempts?.length || 0), 0);
      const steps = handoffPreparationSteps(edge);
      const primaryItem = items[0] || {};
      const sourcePath = primaryItem.source?.path || "";
      const targetPath = primaryItem.result?.target_path
        || primaryItem.target?.path
        || sourcePath;
      const sameWorkspace = Boolean(sourcePath && targetPath && sourcePath === targetPath);
      const recoverable = ["draft", "failed"].includes(String(edge.status || ""));
      const failure = edge.error || edge.execution?.error || "";
      return window.PortalI18n.html`<div class="relationInspectorHead">
        <span class="relationInspectorEyebrow">CANONICAL HANDOFF</span>
        <button type="button" data-close-relation-inspector aria-label="关闭关系检查器" data-portal-icon="close"></button>
      </div>
      <div class="relationInspectorScroll">
        <div class="handoffInspectorHero" data-tone="${esc(meta.tone)}">
          <span class="handoffInspectorState"><i></i>${esc(meta.label)}</span>
          <h3>${esc(edge.label || window.PortalI18n.t("Workspace Handoff"))}</h3>
          ${sourcePath || targetPath ? window.PortalI18n.html`<div class="handoffWorkspaceRoute" data-internal="${sameWorkspace ? "true" : "false"}">
            <span><small>FROM WORKSPACE</small><strong title="${esc(sourcePath)}">${esc(handoffWorkspaceLabel(sourcePath, primaryItem.source?.workspace_id))}</strong></span>
            <b>${window.PortalUIPrimitives.iconHtml(sameWorkspace ? "refresh" : "arrow-right")}</b>
            <span><small>TO WORKSPACE</small><strong title="${esc(targetPath)}">${esc(handoffWorkspaceLabel(targetPath, primaryItem.target?.workspace_id))}</strong></span>
          </div>` : ""}
          <div class="handoffAgentRoute">
            <button type="button" data-relation-node="${esc(edge.source)}">${esc(source?.title || edge.source)}</button>
            <span>Agent → Agent</span>
            <button type="button" data-relation-node="${esc(edge.target)}">${esc(target?.title || edge.target)}</button>
          </div>
        </div>
        ${failure ? window.PortalI18n.html`<div class="handoffInspectorFailure"><span>!</span><div><strong>Handoff 已停在可恢复状态</strong><small>${esc(failure)}</small></div></div>` : ""}
        ${edge.explanation ? window.PortalI18n.html`<section class="relationInspectorSection">
          <header><span>HANDOFF EXPLANATION</span><small>optional</small></header>
          <p class="handoffExplanationText">${esc(edge.explanation)}</p>
        </section>` : ""}
        <section class="relationInspectorSection">
          <header><span>PREPARATION</span><small>${attemptCount} attempts</small></header>
          <ol class="handoffPreparation">
            ${steps.map((step, index) => window.PortalI18n.html`<li data-state="${esc(step.state)}"><i>${step.state === "done" ? window.PortalUIPrimitives.iconHtml("check") : index + 1}</i><span><strong>${esc(step.title)}</strong><small>${esc(step.detail)}</small></span></li>`).join("")}
          </ol>
        </section>
        <section class="relationInspectorSection">
          <header><span>CARGO &amp; EVIDENCE</span><small>${items.length} items</small></header>
          <div class="handoffCargoList">
            ${items.map(item => {
              const method = item.transfer?.method || item.method || "direct";
              const itemSourcePath = item.source?.path || item.source_path || "";
              const itemTargetPath = item.result?.target_path || item.target?.path || item.target_path || itemSourcePath;
              const result = item.result || {};
              const attempts = Array.isArray(item.attempts) ? item.attempts : [];
              const relay = method === "direct" || (itemSourcePath && itemSourcePath === itemTargetPath);
              return window.PortalI18n.html`<article data-kind="${relay ? "relay" : "copy"}">
                <div><span>${window.PortalUIPrimitives.iconHtml(relay ? "refresh" : "arrow-up-right")}</span><strong>${esc(item.label || item.item_id || window.PortalI18n.t("Workspace item"))}</strong><em>${esc(handoffMethodLabel(method))}</em></div>
                <small title="${esc(itemTargetPath)}">${esc(item.source?.scope || item.source_scope || ".")} → ${esc(compactText(itemTargetPath || window.PortalI18n.t("准备时生成"), 54))}</small>
                <dl>
                  <div><dt>attempt</dt><dd>${attempts.length || 0}</dd></div>
                  <div><dt>files</dt><dd>${result.file_count ?? "—"}</dd></div>
                  <div><dt>size</dt><dd>${result.byte_size == null ? "—" : (formatBytes(result.byte_size) || "0 B")}</dd></div>
                </dl>
                ${result.content_hash ? window.PortalI18n.html`<code title="${esc(result.content_hash)}">${esc(compactText(result.content_hash, 40))}</code>` : ""}
              </article>`;
            }).join("") || window.PortalI18n.t('<div class="relationInspectorEmpty">Handoff 尚未声明 Workspace item</div>')}
          </div>
        </section>
        <p class="relationInspectorNote">Handoff 只负责 Workspace 准备与目标 Binding；后续 Agent 动作由 HATG Program 的 Event 与 Effect 表达。Fork 操作产生的上下文继承仍由单独的继承边表达。</p>
        <section class="relationInspectorSection">
          <header><span>EDIT RELATION</span><small>直接保存</small></header>
          ${relationInspectorEditorMarkup(edge, source, target)}
        </section>
        <div class="relationInspectorActions" data-portal-inbox-avoid>
          ${recoverable ? window.PortalI18n.t('<button class="portalButton is-primary" type="button" data-retry-handoff>准备 / 重试</button>') : ""}
          <button class="portalButton is-quiet" type="button" data-inspector-context>回到 Context 定位关系</button>
        </div>
      </div>`;
    }

    function genericRelationInspectorMarkup(edge, source, target) {
      const family = relationFamily(edge);
      const editor = relationInspectorEditorMarkup(edge, source, target);
      const rows = relationInspectorRows(edge);
      const note = edge.type === "absorption"
        ? "吸收关系记录一次真实会话投递。再次把同一来源拖到目标上，会更新这条关系并执行新一轮吸收。"
        : "";
      return window.PortalI18n.html`<div class="relationInspectorHead">
        <span class="relationInspectorEyebrow"><i class="relationFamilyDot ${esc(family)}"></i>${esc(family.toUpperCase())} RELATION</span>
        <button type="button" data-close-relation-inspector aria-label="关闭关系检查器" data-portal-icon="close"></button>
      </div>
      <div class="relationInspectorScroll">
        <div class="relationInspectorHero">
          <div class="relationInspectorType"><span>${esc(RELATION_LABEL[edge.type] || edge.type)}</span><em>${esc(edge.type)}</em></div>
          <h3>${esc(edge.label || RELATION_LABEL[edge.type] || window.PortalI18n.t("关系"))}</h3>
          <div class="relationInspectorRoute">
            <button type="button" data-relation-node="${esc(edge.source)}"><small>来源 Agent</small><strong>${esc(source?.title || edge.source)}</strong><span>${esc(nodeResidentId(source))}</span></button>
            <i>→</i>
            <button type="button" data-relation-node="${esc(edge.target)}"><small>目标 Agent</small><strong>${esc(target?.title || edge.target)}</strong><span>${esc(nodeResidentId(target))}</span></button>
          </div>
        </div>
        <section class="relationInspectorSection">
          <header><span>RELATION CONTRACT</span><small>${rows.length} fields</small></header>
          <dl class="relationInspectorFacts">${rows.map(([term, value]) => window.PortalI18n.html`<dt>${esc(term)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>
        </section>
        ${note ? window.PortalI18n.html`<p class="relationInspectorNote">${esc(note)}</p>` : ""}
        ${editor
          ? window.PortalI18n.html`<section class="relationInspectorSection"><header><span>EDIT RELATION</span><small>直接保存</small></header>${editor}</section>`
          : window.PortalI18n.html`<div class="relationInspectorActions" data-portal-inbox-avoid><span>${edge.type === "absorption"
              ? window.PortalI18n.t("吸收关系由真实投递结果更新，不能手工伪造。")
              : window.PortalI18n.t("Fork 操作产生的继承关系由实际 Resident 来源证明决定，不允许只改线。")}</span></div>`}
      </div>`;
    }

    // Only Workspace Handoffs are edited here; Fork inheritance and absorption
    // relations follow from the instructions that created them.
    async function saveRelationFromInspector(edgeId, form) {
      if (!state.lineage || !form) return;
      const edge = edgeById(edgeId);
      if (!edge || edge.type !== "handoff") return toast(window.PortalI18n.t("这条关系不允许直接改写。"), "warning");
      const data = new FormData(form);
      const button = form.querySelector("[data-save-relation]");
      setBusy(button, true, "保存中…");
      try {
        const label = String(data.get("label") || "").trim();
        const response = await societyService.updateHandoff({
          lineage_id: state.lineage.lineage_id,
          handoff_id: edge.id,
          changes: {
            explanation: String(data.get("explanation") || "").trim(),
            ...(label ? { label } : {}),
          },
        });
        state.selectedEdgeId = edge.id;
        commitLineage(response.lineage);
        toast(window.PortalI18n.t("交接关系已保存。"), "success");
      } catch (error) {
        toast(window.PortalI18n.text`保存关系失败：${error.message || error}`, "error", 6200);
      } finally {
        setBusy(button, false);
      }
    }

    async function deleteRelationFromInspector(edgeId, button = null) {
      if (!state.lineage) return;
      const edge = edgeById(edgeId);
      if (!edge || edge.type !== "handoff") return toast(window.PortalI18n.t("只有工作区交接可以在这里删除。"), "warning");
      if (!await window.PortalUI.confirm({
        title: window.PortalI18n.text`删除交接「${edge.label || RELATION_LABEL[edge.type] || edge.type}」？`,
        message: window.PortalI18n.t("这不会删除两端的 Resident；已经准备好的文件和工作区绑定保留。"),
        confirmLabel: window.PortalI18n.t("删除交接"),
        tone: "danger",
      })) return;
      setBusy(button, true, "删除中…");
      try {
        const response = await societyService.deleteHandoff({ lineage_id: state.lineage.lineage_id, handoff_id: edge.id });
        state.selectedEdgeId = "";
        commitLineage(response.lineage);
        toast(window.PortalI18n.t("交接已删除。"), "warning");
      } catch (error) {
        toast(window.PortalI18n.text`删除交接失败：${error.message || error}`, "error", 6200);
      } finally {
        setBusy(button, false);
      }
    }

    function closeRelationInspector(options = {}) {
      if (!state.selectedEdgeId) return false;
      state.selectedEdgeId = "";
      if (!renderGraphSelection()) renderGraph();
      if (options.restoreFocus !== false) {
        window.requestAnimationFrame(() => $id("hatgCanvas")?.focus({ preventScroll: true }));
      }
      return true;
    }

    function renderRelationInspector(edge = null) {
      const panel = $id("relationInspectorPanel");
      const viewport = $id("hatgViewport");
      if (!panel) return;
      const selectedEdge = edge || displayEdgeById(state.selectedEdgeId) || edgeById(state.selectedEdgeId);
      const visible = Boolean(selectedEdge);
      panel.hidden = !visible;
      panel.setAttribute("aria-hidden", visible ? "false" : "true");
      viewport?.classList.toggle("edgeInspectorOpen", visible);
      if (!selectedEdge) {
        if (state.selectedEdgeId) state.selectedEdgeId = "";
        panel.removeAttribute("data-edge-type");
        panel.removeAttribute("data-family");
        return;
      }
      const source = nodeById(selectedEdge.source) || displayNodeById(selectedEdge.source);
      const target = nodeById(selectedEdge.target) || displayNodeById(selectedEdge.target);
      const family = relationFamily(selectedEdge);
      const renderKey = JSON.stringify([
        state.lineage?.lineage_id || "",
        state.lineage?.revision || 0,
        state.graphViewMode,
        state.localVersion,
        selectedEdge,
      ]);
      panel.dataset.edgeType = selectedEdge.type || "";
      panel.dataset.family = family;
      if (panel._relationRenderKey === renderKey) return;
      const previousEdgeId = panel.dataset.edgeId || "";
      panel._relationRenderKey = renderKey;
      panel.dataset.edgeId = selectedEdge.id;
      panel.innerHTML = selectedEdge.type === "handoff"
        ? handoffInspectorMarkup(selectedEdge, source, target)
        : selectedEdge.type === "candidate_fork"
          ? candidateForkInspectorMarkup(selectedEdge, source)
          : genericRelationInspectorMarkup(selectedEdge, source, target);
      if (previousEdgeId !== selectedEdge.id) {
        const scroll = panel.querySelector(".relationInspectorScroll");
        if (scroll) scroll.scrollTop = 0;
      }
      panel.querySelector("[data-close-relation-inspector]")?.addEventListener("click", () => closeRelationInspector());
      const inspectorForm = panel.querySelector("[data-relation-inspector-form]");
      inspectorForm?.addEventListener("submit", event => {
        event.preventDefault();
        saveRelationFromInspector(selectedEdge.id, event.currentTarget).catch(() => {});
      });
      panel.querySelector("[data-delete-relation]")?.addEventListener("click", event => {
        deleteRelationFromInspector(selectedEdge.id, event.currentTarget).catch(() => {});
      });
      panel.querySelector("[data-inspector-context]")?.addEventListener("click", async () => {
        await setGraphViewMode("context");
        if (!edgeById(selectedEdge.id)) return;
        state.selectedEdgeId = selectedEdge.id;
        renderGraph();
      });
      panel.querySelector("[data-retry-handoff]")?.addEventListener("click", async event => {
        const button = event.currentTarget;
        setBusy(button, true, "准备中…");
        try {
          const response = await societyService.executeHandoff({
            lineage_id: state.lineage.lineage_id,
            handoff_id: selectedEdge.id,
          });
          commitLineage(response.lineage, { render: false });
          await loadWorkspaceGraphBindings(state.lineage);
          renderLineageSurface();
          if (response.status === "failed") {
            toast(window.PortalI18n.text`Handoff 重试失败：${response.error || window.PortalI18n.t("未知错误")}`, "error", 7000);
          } else if (response.status === "preparing") {
            toast(window.PortalI18n.t("正在准备 Workspace"), "success");
          } else {
            toast(window.PortalI18n.t("Handoff Workspace 已就绪。"), "success");
          }
        } catch (error) {
          try { commitLineage(await societyService.loadLineage(state.lineage.lineage_id), { render: false }); } catch {}
          renderLineageSurface();
          toast(window.PortalI18n.text`Handoff 重试失败：${error.message || error}`, "error", 7000);
        } finally {
          setBusy(button, false);
        }
      });
      panel.querySelectorAll("[data-relation-node]").forEach(button => button.addEventListener("click", () => selectNode(button.dataset.relationNode || "", { force: true })));
      panel.querySelector("[data-relation-seed]")?.addEventListener("click", event => {
        selectSeed(event.currentTarget.dataset.relationSeed || "").catch(() => {});
      });
      panel.querySelector("[data-delete-candidate-fork]")?.addEventListener("click", async event => {
        const seed = seedById(selectedEdge.target);
        if (!await window.PortalUI.confirm({
          title: window.PortalI18n.text`删除“${seed?.display_title || seed?.title || "Seed"}”的候选 Fork？`,
          message: window.PortalI18n.t("Seed 和它的候选文件都保留；重新建立候选 Fork 之前不能采纳这个 Seed。"),
          confirmLabel: window.PortalI18n.t("删除候选 Fork"),
          tone: "danger",
        })) return;
        setBusy(event.currentTarget, true, "删除中…");
        state.selectedEdgeId = "";
        await setSeedCandidateFork(selectedEdge.target, "");
      });
    }

    return Object.freeze({
      activateHandoffMode, cancelHandoffMode, handleHandoffNodeClick, addHandoffItem,
      removeHandoffItem, syncHandoffMaterializerFields, syncHandoffSubmitLabel, submitHandoff,
      removeEditingEdge, selectRelationEdge, closeRelationInspector, renderRelationInspector,
    });
  }

  window.PortalHatgRelations = Object.freeze({
    createRelations, dependencies: DEPENDENCIES,
  });
})();
