(() => {
  "use strict";

  if (window.PortalHatgLineageDialogs) return;

  // The open Lineage's own dialogs: creating and editing a Lineage with its
  // project and Leader, moving a subgraph into another Lineage, and
  // deleting the Lineage.
  const DEPENDENCIES = Object.freeze([
    "$id", "clearCanvasSelection", "clone", "closeModal", "currentHatgComponentOptions",
    "displayNodeById", "esc", "isFunctionBlockInstance", "loadLineagePageData", "loadLineages",
    "openModal", "openNodeModal", "renderControls", "renderLineageSurface", "selectLineage",
    "setBusy", "societyService", "state", "toast", "updateLineageSummary",
  ]);

  function createLineageDialogs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgLineageDialogs is missing: ${missing.join(", ")}`);
    const {
      $id, clearCanvasSelection, clone, closeModal, currentHatgComponentOptions, displayNodeById,
      esc, isFunctionBlockInstance, loadLineagePageData, loadLineages, openModal, openNodeModal,
      renderControls, renderLineageSurface, selectLineage, setBusy, societyService, state, toast,
      updateLineageSummary,
    } = dependencies;

    function renderLineageProjectOptions(selectedProjectId = "", select = $id("lineageProjectInput")) {
      if (!select) return;
      const selected = String(selectedProjectId || "").trim();
      const projects = [...(state.projects || [])].sort((left, right) => (
        String(left?.project_id || "").localeCompare(String(right?.project_id || ""), undefined, { sensitivity: "base" })
      ));
      const options = projects.map(project => {
        const id = String(project?.project_id || "").trim();
        return { value: id, label: id };
      }).filter(item => item.value);
      if (selected && !options.some(item => item.value === selected)) {
        options.unshift({ value: selected, label: selected });
      }
      select.innerHTML = [
        window.PortalI18n.html`<option value="" disabled ${selected ? "" : "selected"}>请选择项目</option>`,
        ...options.map(option => window.PortalI18n.html`<option value="${esc(option.value)}" ${option.value === selected ? "selected" : ""}>${esc(option.label)}</option>`),
      ].join("");
    }

    let subgraphMoveContext = null;

    function subgraphMoveBusy() {
      return Boolean(subgraphMoveContext?.busy);
    }

    // A changed target or project is a new move request with its own idempotency key.
    function renewSubgraphMoveIntent() {
      if (subgraphMoveContext) subgraphMoveContext.key = `portal:move-subgraph:${globalThis.PortalUIPrimitives.randomUUID()}`;
    }

    function previewSubgraphMove() {
      const context = subgraphMoveContext;
      if (!context || context.busy) return;
      const targetId = $id("subgraphMoveTarget").value;
      $id("subgraphMoveNameField").hidden = targetId !== "__new__";
      // CreateLineage requires a Project; a source without one (a personal AMA) lets the user pick.
      $id("subgraphMoveProjectField").hidden = targetId !== "__new__" || Boolean(context.projectId);
      $id("subgraphMoveSubmit").disabled = true;
      context.plan = null;
      $id("subgraphMoveStatus").textContent = "";
      if (!targetId || context.targetsState !== "ready") return;
      // MoveLineageObjects has no preview: the page summarises the selection and
      // the instruction detaches cross-boundary relations itself.
      const residentIds = context.objectIds.filter(id => Boolean(displayNodeById(id)) && !isFunctionBlockInstance(displayNodeById(id)));
      const plan = { object_ids: context.objectIds, resident_ids: residentIds, blockers: [] };
      context.plan = plan;
      context.targetId = targetId;
      $id("subgraphMoveStatus").textContent = window.PortalI18n.text`选中 ${context.objectIds.length} 个对象（${residentIds.length} 个 Resident）。选区内部的连线和节点关系随对象迁移，与外部相连的关系会自动断开。${targetId !== "__new__" ? "" : context.projectId ? window.PortalI18n.t("新 Lineage 继承源图的所属项目。") : window.PortalI18n.t("源图不属于任何项目，请为新 Lineage 选择所属项目。")}`;
      $id("subgraphMoveSubmit").disabled = false;
    }

    async function loadSubgraphMoveTargets() {
      const context = subgraphMoveContext;
      if (!context || context.busy || context.targetsState === "loading") return;
      const select = $id("subgraphMoveTarget");
      const selectedId = select.value;
      const status = $id("subgraphMoveTargetsStatus");
      context.targetsState = "loading";
      select.disabled = true;
      $id("subgraphMovePreview").disabled = true;
      status.textContent = window.PortalI18n.t("正在加载目标 Lineage…");
      previewSubgraphMove();
      try {
        const payload = await societyService.listLineages();
        if (context !== subgraphMoveContext) return;
        select.replaceChildren(
          new Option(window.PortalI18n.t("请选择"), ""),
          new Option(window.PortalI18n.t("新建 Lineage"), "__new__"),
          ...(payload.lineages || []).filter(item => item.lineage_id !== context.lineageId)
            .map(item => new Option(`${item.title} · ${item.lineage_id}`, item.lineage_id)),
        );
        const retained = [...select.options].some(option => option.value === selectedId);
        select.value = retained ? selectedId : "";
        context.targetsState = "ready";
        status.textContent = retained ? "" : window.PortalI18n.t("原目标已不可用，请重新选择。");
      } catch (error) {
        if (context !== subgraphMoveContext) return;
        context.targetsState = "error";
        status.textContent = window.PortalI18n.text`目标列表加载失败：${error.message || error}。请点击“刷新预览”重试。`;
      } finally {
        if (context === subgraphMoveContext) {
          select.disabled = false;
          $id("subgraphMovePreview").disabled = false;
          previewSubgraphMove();
        }
      }
    }

    async function openSubgraphMove() {
      if (!state.lineage || subgraphMoveContext?.busy) return;
      const objectIds = [...state.selectedCanvasObjectIds];
      if (!objectIds.length) return;
      subgraphMoveContext = {
        lineageId: state.lineage.lineage_id, objectIds, busy: false,
        projectId: String(state.lineage.project_id || ""),
        key: `portal:move-subgraph:${globalThis.PortalUIPrimitives.randomUUID()}`,
      };
      $id("subgraphMoveTarget").replaceChildren(new Option("新建 Lineage", "__new__"));
      $id("subgraphMoveName").value = `${state.lineage.title || "Lineage"} · 选区`;
      renderLineageProjectOptions("", $id("subgraphMoveProject"));
      openModal("subgraphMoveModal");
      await loadSubgraphMoveTargets();
    }

    async function submitSubgraphMove(event) {
      event.preventDefault();
      const context = subgraphMoveContext;
      if (!context?.plan || context.busy) return;
      if (state.lineage?.lineage_id !== context.lineageId) return;
      if (context.targetId === "__new__" && !$id("subgraphMoveName").value.trim()) {
        $id("subgraphMoveStatus").textContent = window.PortalI18n.t("请输入新 Lineage 的名称。");
        return;
      }
      const projectId = context.projectId || $id("subgraphMoveProject").value;
      if (context.targetId === "__new__" && !projectId) {
        $id("subgraphMoveStatus").textContent = window.PortalI18n.t("请为新 Lineage 选择所属项目。");
        return;
      }
      context.busy = true;
      const controls = [...$id("subgraphMoveForm").querySelectorAll("input, select, button")];
      controls.forEach(control => { control.disabled = true; });
      renderControls();
      let committed = false;
      try {
        if (context.targetId === "__new__") {
          const target = await societyService.createLineage({
            title: $id("subgraphMoveName").value.trim(), project_id: projectId,
            idempotency_key: `${context.key}:create`,
          });
          context.targetId = target.lineage_id;
          $id("subgraphMoveTarget").append(new Option($id("subgraphMoveName").value.trim(), target.lineage_id));
          $id("subgraphMoveTarget").value = target.lineage_id;
          $id("subgraphMoveNameField").hidden = true;
        }
        // Retain this exact key on transport errors for an idempotent retry.
        const result = await societyService.moveObjects({
          lineage_id: context.lineageId,
          object_ids: context.objectIds,
          target_lineage_id: context.targetId,
          idempotency_key: context.key,
        });
        committed = true;
        closeModal("subgraphMoveModal");
        clearCanvasSelection();
        const detached = Array.isArray(result.detached) ? result.detached.length : 0;
        // Report committed disconnections before refresh reads can stall or fail.
        // Keep that consequence visible through target navigation until closed.
        toast(detached ? window.PortalI18n.text`子图已迁移，自动断开 ${detached} 条跨边界关系。` : window.PortalI18n.t("子图已迁移。"), "success", detached ? 0 : 7000);
        await loadLineagePageData({ forceCatalogs: true });
        await selectLineage(result.target_lineage_id);
      } catch (error) {
        $id("subgraphMoveStatus").textContent = committed
          ? window.PortalI18n.text`迁移已提交，刷新页面后查看目标 Lineage：${error.message || error}`
          : window.PortalI18n.text`迁移未确认：${error.message || error}。可直接重试；图已修改时请刷新预览。已创建的目标 Lineage 会保留。`;
        if (committed) toast($id("subgraphMoveStatus").textContent, "error", 9000);
      } finally {
        context.busy = false;
        controls.forEach(control => { control.disabled = false; });
        $id("subgraphMoveSubmit").disabled = committed || !context.plan;
        renderControls();
      }
    }

    function clearLineageFormError() {
      $id("lineageFormError").hidden = true;
      $id("lineageFormError").textContent = "";
    }

    function openCreateLineageModal(projectId = "") {
      $id("lineageModalTitle").textContent = window.PortalI18n.t("新建 Lineage");
      $id("lineageNameInput").value = "";
      $id("lineageDescriptionInput").value = "";
      renderLineageProjectOptions(projectId);
      $id("lineageProjectHint").textContent = window.PortalI18n.t("Lineage 必须属于一个 Project。");
      $id("lineageLeaderField").hidden = true;
      $id("lineageFamilizeField").hidden = false;
      $id("lineageFamilizeInput").checked = true;
      $id("lineageDeleteZone").hidden = true;
      $id("lineageSubmitBtn").textContent = window.PortalI18n.t("创建 Lineage");
      $id("lineageForm").dataset.mode = "create";
      clearLineageFormError();
      openModal("lineageModal");
    }

    function openEditLineageModal() {
      if (!state.lineage) return;
      $id("lineageModalTitle").textContent = window.PortalI18n.t("Lineage 设置");
      $id("lineageNameInput").value = state.lineage.title || "";
      $id("lineageDescriptionInput").value = state.lineage.description || "";
      renderLineageProjectOptions(state.lineage.project_id || "");
      $id("lineageProjectHint").textContent = window.PortalI18n.t("Lineage 必须属于一个 Project。修改后会同步资源注册并迁移整个 Lineage Folder；有任务运行时不能迁移。");
      $id("lineageLeaderField").hidden = false;
      $id("lineageFamilizeField").hidden = true;
      // A Lineage with a Resident always has a Leader; only an empty one shows the unset choice.
      $id("lineageLeaderInput").innerHTML = ((state.lineage.nodes || []).length ? "" : window.PortalI18n.markup('<option value="">未指定</option>')) +
        (state.lineage.nodes || []).map(node => window.PortalI18n.html`<option value="${esc(node.id)}">${esc(node.title || node.id)}</option>`).join("");
      $id("lineageLeaderInput").value = state.lineage.leader_resident_id || "";
      $id("lineageDeleteZone").hidden = false;
      $id("lineageSubmitBtn").textContent = window.PortalI18n.t("保存设置");
      $id("lineageForm").dataset.mode = "edit";
      $id("lineageForm").dataset.lineageId = state.lineage.lineage_id;
      $id("lineageForm").dataset.revision = String(state.lineage.revision);
      clearLineageFormError();
      openModal("lineageModal");
    }

    async function submitLineageForm(event) {
      event.preventDefault();
      const title = $id("lineageNameInput").value.trim() || window.PortalI18n.t("未命名 Lineage");
      const description = $id("lineageDescriptionInput").value.trim();
      const projectId = $id("lineageProjectInput").value.trim();
      const button = $id("lineageSubmitBtn");
      const mode = event.currentTarget.dataset.mode || "create";
      if (!projectId) {
        $id("lineageProjectInput").focus();
        toast(window.PortalI18n.t("请选择 Lineage 所属项目。"), "error", 5000);
        return;
      }
      setBusy(button, true, mode === "create" ? "创建中…" : "保存中…");
      try {
        $id("lineageFormError").hidden = true;
        if (mode === "create") {
          const lineage = await societyService.createLineage({
            title,
            description,
            project_id: projectId,
            familize: $id("lineageFamilizeInput").checked,
          });
          state.lineages.unshift({
            lineage_id: lineage.lineage_id, title, description, project_id: projectId,
            node_count: 0, edge_count: 0, statuses: {},
          });
          closeModal("lineageModal");
          await selectLineage(lineage.lineage_id);
          toast(window.PortalI18n.t(lineage.family_id ? "Lineage 和 Family 已创建。现在添加第一个成员。" : "Lineage 已创建。现在添加第一个真实 Agent 节点。"), "success", 5000);
          openNodeModal("new");
        } else if (state.lineage) {
          if (event.currentTarget.dataset.lineageId !== state.lineage.lineage_id) {
            throw new Error("当前 Lineage 已改变，请重新打开设置。");
          }
          const saved = (await societyService.updateMetadata({
            lineage_id: state.lineage.lineage_id,
            title,
            description,
            project_id: projectId,
            leader_resident_id: $id("lineageLeaderInput").value,
            expected_version: Number(event.currentTarget.dataset.revision),
          })).lineage || {};
          if (String(saved?.project_id || "") !== projectId) {
            throw new Error("后端未提交所选项目，请刷新后重试");
          }
          [
            "title",
            "description",
            "project_id",
            "leader_resident_id",
            "program_event_source_runtime",
            "program_execution_explanations",
            "program_diagnostics",
            "revision",
            "updated_at",
            "graph_updated_at",
          ].forEach(field => {
            if (Object.hasOwn(saved, field)) {
              state.lineage[field] = clone(saved[field]);
            }
          });
          state.localVersion += 1;
          updateLineageSummary();
          renderLineageSurface();
          closeModal("lineageModal");
          toast(window.PortalI18n.t("Lineage 设置已保存。"));
        }
      } catch (error) {
        $id("lineageFormError").textContent = error.message || String(error);
        $id("lineageFormError").hidden = false;
        if (mode === "create" && error.details?.lineage_id) {
          // Creation committed: leave the dialog so retry cannot create a duplicate Lineage.
          closeModal("lineageModal");
          await loadLineages();
          await selectLineage(error.details.lineage_id);
          void window.PortalNav.openScopeMenu({kind: "lineage", id: error.details.lineage_id, record: state.lineage});
        }
        toast(error.message || String(error), "error");
      } finally {
        setBusy(button, false);
      }
    }

    async function deleteCurrentLineage() {
      if (!state.lineage) return;
      const button = $id("deleteLineageBtn");
      setBusy(button, true, "删除中…");
      try {
        const deleted = await currentHatgComponentOptions().onDeleteLineage({
          lineage_id: state.lineage.lineage_id,
          title: state.lineage.title,
        });
        if (deleted) closeModal("lineageModal");
      } catch (error) {
        toast(error.message || String(error), "error");
      } finally {
        setBusy(button, false);
      }
    }

    return Object.freeze({
      renderLineageProjectOptions, subgraphMoveBusy, renewSubgraphMoveIntent,
      previewSubgraphMove, loadSubgraphMoveTargets, openSubgraphMove, submitSubgraphMove,
      openCreateLineageModal, openEditLineageModal, submitLineageForm,
      deleteCurrentLineage,
    });
  }

  window.PortalHatgLineageDialogs = Object.freeze({
    createLineageDialogs, dependencies: DEPENDENCIES,
  });
})();
