(() => {
  "use strict";

  if (window.PortalHatgSeeds) return;

  // Seeds on the HATG canvas: the Seed card beside its source, its preview in
  // the source Resident's Workspace, and promoting a Seed into a Resident or
  // dismissing it.
  const DEPENDENCIES = Object.freeze([
    "$id", "$queryAll", "closeModal", "commitLineage", "displayNodeById", "displaySeedById",
    "esc", "executeHatgInstruction", "loadResultFile", "loadResults", "nodeById", "openModal",
    "programComponentById", "programFeature", "renderControls", "renderGraph",
    "renderGraphSelection", "renderLineageHeader", "renderLineageSurface", "seedById",
    "selectNode", "setBusy", "setSyncStatus", "state", "toast",
  ]);

  function seedTypeLabel(seed) {
    return ({ idea: "候选想法", plan: "候选方案", next_step: "下一步", other: "候选" })[seed?.seed_type] || "候选";
  }

  const SEED_ACTION_DEFAULTS = {
    implement: seed => `实现种子“${seed.display_title || seed.title}”。先核对可行性，再形成可运行或可交付的实现，并记录验证结果。`,
    analyze: seed => `继续分析种子“${seed.display_title || seed.title}”。补足证据、边界条件和风险，给出是否值得采纳的明确判断。`,
    validate: seed => `独立验证种子“${seed.display_title || seed.title}”。设计最小验证，记录证据，并给出通过、部分通过或不通过结论。`,
    custom: seed => `继续探索种子“${seed.display_title || seed.title}”。结合当前种子内容推进工作，并形成可验收的结果。`,
  };

  function createSeeds(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgSeeds is missing: ${missing.join(", ")}`);
    const {
      $id, $queryAll, closeModal, commitLineage, displayNodeById, displaySeedById, esc,
      executeHatgInstruction, loadResultFile, loadResults, nodeById, openModal,
      programComponentById, programFeature, renderControls, renderGraph, renderGraphSelection,
      renderLineageHeader, renderLineageSurface, seedById, selectNode, setBusy, setSyncStatus,
      state, toast,
    } = dependencies;

    function renderSeedInspector() {
      const inspector = $id("seedInspector");
      if (!inspector) return;
      const seed = displaySeedById(state.selectedSeedId);
      const visible = !!seed && String(seed.status || "active") === "active" && state.graphViewMode === "context";
      inspector.hidden = !visible;
      if (!visible) return;
      $id("seedInspectorType").textContent = seedTypeLabel(seed);
      $id("seedInspectorTitle").textContent = seed.display_title || seed.title || "候选";
      $id("seedInspectorPath").textContent = seed.artifact_path || "";
      const source = displayNodeById(seed.source_resident_id) || nodeById(seed.source_resident_id);
      $id("seedInspectorProducer").textContent = source
        ? `候选 Fork：采纳时从 ${source.title} Fork`
        : "先建立候选 Fork 才能采纳：把一个 Resident 的“+”拖到这个 Seed 上，或在 Seed 设置中选择来源。";
      inspector.querySelectorAll("[data-seed-promote-action]").forEach(button => {
        button.disabled = !source;
        button.title = source ? `采纳并${button.textContent.trim()}` : "先建立候选 Fork 才能采纳";
      });
      const dismissButton = $id("seedDismissBtn");
      if (dismissButton) {
        dismissButton.disabled = false;
        dismissButton.title = window.PortalI18n.t("保留 Markdown 审计记录，并从当前 HATG 抛弃这个候选");
      }
      positionSeedInspector();
      // The canvas may draw the Seed after this card; place it again then.
      window.requestAnimationFrame(positionSeedInspector);
    }

    // The Seed card stands beside its Seed: to the right, or to the left when
    // there is no room, and follows it when the canvas pans, zooms or resizes.
    function positionSeedInspector() {
      const inspector = $id("seedInspector");
      if (!inspector || inspector.hidden || !state.selectedSeedId) return;
      const host = inspector.offsetParent;
      if (!host) return;
      const seedElement = $id("hatgNodes")?.querySelector(`.hatgNode[data-node-id="${CSS.escape(state.selectedSeedId)}"]`);
      if (!seedElement) return;
      const area = host.getBoundingClientRect();
      const seed = seedElement.getBoundingClientRect();
      const navigation = $id("graphNavigationBar")?.getBoundingClientRect();
      const width = inspector.offsetWidth;
      const height = inspector.offsetHeight;
      const gap = 12;
      const margin = 8;
      const right = seed.right - area.left + gap;
      const left = right + width <= area.width - margin ? right : seed.left - area.left - gap - width;
      const top = seed.top - area.top + (seed.height - height) / 2;
      const minTop = navigation?.height ? navigation.bottom - area.top + margin : margin;
      const clamp = (value, low, high) => Math.min(Math.max(low, value), Math.max(low, high));
      inspector.style.left = `${Math.round(clamp(left, margin, area.width - width - margin))}px`;
      inspector.style.top = `${Math.round(clamp(top, minTop, area.height - height - margin))}px`;
    }

    function dismissSeedSelection() {
      if (!state.selectedSeedId) return false;
      state.selectedSeedId = "";
      if (!renderGraphSelection()) renderGraph();
      renderControls();
      renderSeedInspector();
      return true;
    }

    // A Seed's Markdown is in the Lineage folder (decision_5b5653c3eaa5); any
    // Resident's workspace listing has that folder as its "lineage_folder" root.
    // The candidate fork source is the natural one to open it from.
    function seedHomeNode(seed) {
      return nodeById(seed?.source_resident_id) || state.lineage?.nodes?.[0] || null;
    }

    function seedPreviewPath(seed) {
      const path = String(seed?.artifact_path || "").replace(/^\/+/, "");
      const folder = (state.resultWorkspace?.roots || []).find(root => root?.root_kind === "lineage_folder");
      return folder?.directory_id && path ? `${folder.directory_id}/${path}` : path;
    }

    async function ensureSeedResultWorkspace(seed) {
      const source = seedHomeNode(seed);
      if (!state.lineage || !source) return false;
      const cachedSeedArtifact = (state.resultWorkspace?.entries || []).some(entry => (
        entry?.root_kind === "lineage_folder"
        && String(entry.rel_path || "") === String(seed?.artifact_path || "")
      ));
      if (
        state.resultWorkspace
        && state.resultWorkspaceNodeId === source.id
        && (
          state.resultWorkspace.listing_mode === "lazy"
          || cachedSeedArtifact
        )
      ) return true;
      const lineageId = state.lineage.lineage_id;
      const seedId = seed.id;
      const sequence = ++state.resultWorkspaceSeq;
      const workspace = await window.PortalResidentService.workspaces(source.id);
      if (
        sequence !== state.resultWorkspaceSeq
        || state.lineage?.lineage_id !== lineageId
        || state.selectedSeedId !== seedId
      ) return false;
      state.resultWorkspace = workspace;
      state.resultWorkspaceNodeId = source.id;
      state.workspaceGraphBindings.set(source.id, workspace);
      state.selectedResultPath = "";
      renderLineageHeader();
      return true;
    }

    async function selectSeed(seedId) {
      const seed = displaySeedById(seedId);
      if (!seed || String(seed.status || "active") !== "active") return;
      state.selectedSeedId = seed.id;
      state.selectedProgramObjectId = "";
      programFeature.clearSelection();
      state.selectedEdgeId = "";
      renderLineageSurface();
      try {
        if (await ensureSeedResultWorkspace(seed)) await loadResultFile(seedPreviewPath(seed));
      } catch (error) {
        if (state.selectedSeedId !== seed.id) return;
        toast(`候选内容加载失败：${error.message || error}`, "error", 5600);
      }
    }

    function setSeedActionChoice(actionType, options = {}) {
      const seed = seedById(state.promotingSeedId);
      if (!seed) return;
      const normalized = ["implement", "analyze", "validate", "custom"].includes(actionType) ? actionType : "custom";
      state.seedActionType = normalized;
      $id("seedActionTypeInput").value = normalized;
      $queryAll("[data-seed-action-choice]").forEach(button => {
        button.classList.toggle("active", button.dataset.seedActionChoice === normalized);
      });
      if (!options.keepPurpose) $id("seedActionPurposeInput").value = SEED_ACTION_DEFAULTS[normalized](seed);
    }

    function openSeedPromoteModal(seedId, actionType = "implement") {
      const seed = seedById(seedId);
      if (!seed || String(seed.status || "active") !== "active") return toast(window.PortalI18n.t("这个种子节点已经被采纳或收起。"), "warning");
      const source = nodeById(seed.source_resident_id);
      if (!source) return toast(window.PortalI18n.t("先建立候选 Fork 才能采纳：把一个 Resident 的“+”拖到这个 Seed 上，或在 Seed 设置中选择来源。"), "warning", 6500);
      state.promotingSeedId = seed.id;
      const seedTitle = seed.display_title || seed.title || "候选";
      $id("seedPromoteModalTitle").textContent = `采纳“${seedTitle}”`;
      $id("seedPromotionRoute").innerHTML = `<span><strong>${esc(source.title)}</strong><small>来源 Agent A</small></span><i>→</i><span><strong>${esc(seedTitle)}</strong><small>Fork 创建普通 Resident</small></span>`;
      $id("seedActionPromptInput").value = "";
      setSeedActionChoice(actionType);
      openModal("seedPromoteModal");
    }

    async function submitSeedPromotion(event) {
      event.preventDefault();
      if (!state.lineage) return;
      const seed = seedById(state.promotingSeedId);
      if (!seed) return;
      const requestedActionType = $id("seedActionTypeInput").value || "custom";
      const actionType = Object.hasOwn(SEED_ACTION_DEFAULTS, requestedActionType) ? requestedActionType : "custom";
      const purpose = $id("seedActionPurposeInput").value.trim() || SEED_ACTION_DEFAULTS[actionType](seed);
      const button = $id("seedPromoteSubmitBtn");
      setBusy(button, true, "执行 Fork…");
      setSyncStatus("正在采纳种子并执行 Fork…", "saving");
      try {
        const followUp = $id("seedActionPromptInput").value.trim();
        const task = followUp ? `${purpose}\n\n${followUp}` : purpose;
        const result = await executeHatgInstruction("PromoteSeed", {
          seed_id: seed.id,
          purpose: task,
        });
        if (!result?.lineage) throw new Error("采纳响应缺少 Lineage 状态");
        // The new Resident is a fork of the source; its first task names the candidate it adopts.
        let started = false;
        if (result.resident_id) {
          const candidate = result.candidate_path
            ? `候选 Markdown：${result.candidate_path}\n先读这份候选，再按上面的目的工作。`
            : "";
          try {
            await executeHatgInstruction("DeliverMessage", {
              resident_id: result.resident_id,
              message: { role: "user", content_parts: [{ type: "text", text: [task, candidate].filter(Boolean).join("\n\n") }] },
              business_ref: `seed/${seed.id}`,
            }, { withLineage: false, reload: false, definition: false });
            started = true;
          } catch (error) {
            toast(`新 Resident 已创建，但任务没有送达：${error.message || error}。请在它的对话中重新发送。`, "error", 9000);
          }
        }
        state.selectedSeedId = "";
        closeModal("seedPromoteModal");
        commitLineage(result.lineage);
        await loadResults();
        const child = (state.lineage.nodes || []).find(node => node.id === result.resident_id);
        if (child?.id) await selectNode(child.id, { force: true });
        setSyncStatus(started ? "Seed 已采纳，新 Resident 开始工作" : "Seed 已提升为待命 Resident", started ? "saved" : "error");
        if (started) toast(`已采纳“${seed.display_title || seed.title}”：Resident「${child?.title || seed.title}」开始按候选工作。`, "success", 6500);
      } catch (error) {
        setSyncStatus("种子采纳失败", "error");
        toast(`种子采纳失败：${error.message || error}`, "error", 7000);
      } finally {
        setBusy(button, false);
      }
    }

    function openSeedDismissModal(seedId) {
      const seed = seedById(seedId);
      if (!seed || String(seed.status || "active") !== "active") return toast(window.PortalI18n.t("这个种子节点已经被采纳或抛弃。"), "warning");
      state.dismissingSeedId = seed.id;
      const seedTitle = seed.display_title || seed.title || "候选";
      $id("seedDismissModalTitle").textContent = `抛弃“${seedTitle}”`;
      $id("seedDismissRoute").innerHTML = `<span><strong>${esc(seedTitle)}</strong><small>${esc(seedTypeLabel(seed))} · 当前 HATG</small></span><i>→</i><span><strong>已抛弃</strong><small>节点隐藏 · Markdown 保留</small></span>`;
      $id("seedDismissReasonInput").value = "";
      openModal("seedDismissModal");
    }

    async function submitSeedDismissal(event) {
      event.preventDefault();
      if (!state.lineage) return;
      const seed = seedById(state.dismissingSeedId);
      const reason = $id("seedDismissReasonInput").value.trim();
      if (!seed) return;
      const button = $id("seedDismissSubmitBtn");
      const seedTitle = seed.display_title || seed.title || "候选";
      const focusNodeId = seedHomeNode(seed)?.id || "";
      setBusy(button, true, "正在抛弃…");
      setSyncStatus("正在标记候选并更新 HATG…", "saving");
      try {
        // A dismissed Seed stays in the Lineage with status "dismissed".
        const component = programComponentById(seed.id) || {};
        const result = await executeHatgInstruction("UpdateComponent", {
          component_id: seed.id,
          changes: { config: {
            ...(component.config || {}),
            status: "dismissed",
            dismissed_at: new Date().toISOString(),
            dismissal_source: "owner",
            ...(reason ? { dismissal_reason: reason } : {}),
          } },
        });
        if (!result?.lineage) throw new Error("抛弃响应缺少 Lineage 状态");
        state.selectedSeedId = "";
        closeModal("seedDismissModal");
        commitLineage(result.lineage);
        if (focusNodeId && nodeById(focusNodeId)) {
          await selectNode(focusNodeId, { force: true });
          if (seed.artifact_path) await loadResultFile(seedPreviewPath(seed)).catch(() => {});
        }
        setSyncStatus("Seed 已抛弃", "saved");
        toast(`已抛弃 Seed“${seedTitle}”。`, "success", 6000);
      } catch (error) {
        setSyncStatus("候选抛弃失败", "error");
        toast(`候选抛弃失败：${error.message || error}`, "error", 7000);
      } finally {
        setBusy(button, false);
      }
    }

    return Object.freeze({
      seedTypeLabel, renderSeedInspector, positionSeedInspector, dismissSeedSelection,
      selectSeed, setSeedActionChoice, openSeedPromoteModal, submitSeedPromotion,
      openSeedDismissModal, submitSeedDismissal,
    });
  }

  window.PortalHatgSeeds = Object.freeze({
    createSeeds, dependencies: DEPENDENCIES, seedTypeLabel,
  });
})();
