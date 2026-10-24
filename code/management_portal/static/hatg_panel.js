(() => {
  "use strict";

  function createHatgRuntime() {

  let hatgComponentRoot = document;
  let hatgComponentOptions = {};
  // The panel's first read of its Lineage catalog has finished (successfully or not).
  let lineageCatalogHydrated = false;
  // Dialogs this runtime moved to document.body still belong to it. Several
  // HATG runtimes can be mounted with the same element IDs, so they are searched
  // before the document: getElementById would return the first runtime's copy.
  const detachedRoots = new Set();
  const $id = id => {
    const selector = `#${CSS.escape(id)}`;
    const found = hatgComponentRoot.querySelector?.(selector);
    if (found) return found;
    for (const root of detachedRoots) {
      const match = root.matches?.(selector) ? root : root.querySelector?.(selector);
      if (match) return match;
    }
    return document.getElementById(id) || null;
  };
  const $query = selector => hatgComponentRoot.querySelector(selector);
  const $queryAll = selector => hatgComponentRoot.querySelectorAll(selector);
  const $eventRoot = () => hatgComponentRoot;
  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const apiGet = path => window.PortalAPI.get(path, { cache: "no-store" });
  const apiPost = (path, body) => window.PortalAPI.post(path, body);
  const societyService = window.PortalSocietyService.createClient({
    get: apiGet,
    getRevision: id => state.lineage?.lineage_id === id ? state.lineage.revision : undefined,
    onResidentsDeleted: retireDeletedResidents,
    onConflict: ({ lineageId, lineage, error }) => {
      if (state.lineage?.lineage_id !== lineageId) return;
      const roots = [hatgComponentRoot, ...detachedRoots];
      let retainedDrafts = 0;
      roots.forEach(root => root.querySelectorAll?.('[data-program-draft-dirty="true"]').forEach(form => {
        retainedDrafts += 1;
        form.dataset.programDraftConflict = "true";
        if (lineage) definitionRevisions.set(form, lineage.revision);
        else definitionRevisions.delete(form);
      }));
      if (lineage) {
        // Keep the user's fields through this read. Only an explicit save may
        // submit them again against the refreshed definition.
        programFeature.setWireDraft(null);
        commitLineage(lineage);
      }
      if (retainedDrafts) {
        error.message = window.PortalI18n.t(lineage
          ? "图已被其他操作修改，本次修改未保存。草稿已保留，请核对后再次点击保存。"
          : "本次修改未保存，草稿已保留；最新版本读取失败。再次点击保存时会先重读，再尝试保存。");
      }
      setSyncStatus(error.message, "error");
    },
  });
  function retireDeletedResidents(ids) {
    window.PortalResidentMenu?.retireResidents(ids);
    return hatgComponentOptions.onResidentsDeleted?.(ids);
  }
  const residentDiscovery = window.PortalResidentDiscoveryService.createClient({ post: apiPost });
  const clone = value => JSON.parse(JSON.stringify(value));
  const normalizeOptionalLimit = (value, maximum = Number.MAX_SAFE_INTEGER) => (
    window.PortalLineageProgram?.normalizeOptionalLimit?.(value, maximum)
    ?? null
  );
  const MAX_HANDOFF_ITEMS = 24;
  const HATG_VIEW_UI_NAMESPACE = "lineage.hatg-view";
  const hatgViewPreferences = new Map();
  const hatgViewSaveTimers = new Map();
  const hatgViewSaveRequests = new Map();
  const PROGRAM_INVOKE_EDITOR_STATES = new WeakMap();
  const definitionRevisions = new WeakMap();
  const inspectorDisabledBeforeMutation = new WeakMap();
  const AUTO_LAYOUT_METRICS = Object.freeze({
    agent: Object.freeze({ width: 154, height: 94, columnGap: 88, rowGap: 24, maxRows: 7 }),
  });
  const HATG_WORLD_SIZE = Object.freeze({ width: 1280, height: 900 });
  // Both arrange and Fit all must fit the actual usable canvas. A readability
  // floor can put outer cards behind the toolbar in a narrow or short dock;
  // users can focus or zoom a card after the complete layout is visible.
  const AUTO_LAYOUT_VIEW = Object.freeze({ duration: 280, maxScale: 1, padding: 64 });
  const FIT_ALL_VIEW = Object.freeze({ duration: 280, maxScale: 1 });
  const DEFAULT_TEMPLATE_TIMEOUT_MS = 360_000;


  const stateFactory = window.PortalHatgPanelState;
  if (!stateFactory) throw new Error("PortalHatgPanelState is not loaded.");
  const controllerFactory = window.PortalLineageWorkbenchController;
  if (!controllerFactory) throw new Error("PortalLineageWorkbenchController is not loaded.");
  const state = stateFactory.createState();
  const programFeature = state.features.program;
  let graphNodeSearchPopover = null;
  let graphControlObserver = null;
  let seedInspectorObserver = null;
  function disconnectGraphObservers() {
    graphControlObserver?.disconnect();
    graphControlObserver = null;
    seedInspectorObserver?.disconnect();
    seedInspectorObserver = null;
  }
  let disposeLibraryResize = null;
  let graphNodeSearchRenderFrame = 0;
  let graphSmartSearchTimer = 0;
  let graphSmartSearchRequest = 0;
  let graphSmartSearch = { query: "", loading: false, mode: "", results: [], error: "" };
  const graphSearchTextCache = new WeakMap();
  let graphProgramProjectionCache = null;
  let panelActive = true;
  let pageStateController = null;
  let templateController = null;

  function uid(prefix) {
    const token = globalThis.PortalUIPrimitives.randomUUID().replace(/-/g, "").slice(0, 12);
    return `${prefix}_${token.toLowerCase()}`;
  }

  function compactText(value, limit = 100) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit - 1).trim()}…` : text;
  }

  function formatDate(value) {
    if (!value) return "";
    return window.PortalUI.formatDateTime(value, { style: "compact" });
  }

  function formatBytes(value) {
    return window.PortalUI.formatBytes(value, {
      emptyFallback: "",
      zeroFallback: "",
      maximumUnit: "MB",
      precision: "compact",
    });
  }

  function toast(message, kind = "success", timeout = 3600, options = {}) {
    return window.PortalUI.toast(message, kind, { ...options, duration: timeout });
  }

  function setBusy(button, busy, text = "处理中…") {
    window.PortalUI.setButtonBusy(button, busy, text);
  }

  const lineageDialogControllers = new Map();
  function lineageDialogController(id) {
    if (lineageDialogControllers.has(id)) return lineageDialogControllers.get(id);
    const modal = $id(id);
    if (!modal) return null;
    const controller = window.PortalUI.createDialogController({
      modal,
      openClass: "portalDialogOpen",
      bodyClass: "agentLineageModalOpen",
      backdropClose: true,
      escapeClose: true,
      onOpen: () => { modal.hidden = false; },
      onClose: () => {
        modal.hidden = true;
        if (id === "lineageModal" && modal.dataset.portalDetached === "1" && hatgComponentRoot) {
          delete modal.dataset.portalDetached;
          hatgComponentRoot.append(modal);
          detachedRoots.delete(modal);
        }
        if (id === "branchModal") {
          branchModalRequestSeq += 1;
          branchSourceSnapshot = null;
          state.branchMessageContext = null;
        }
        if (id === "seedDismissModal") state.dismissingSeedId = "";
        if (id === "nodeAbsorptionModal") state.absorptionDecision = null;
        if (id === "handoffModal" && state.handoffMode) cancelHandoffMode();
      },
    });
    lineageDialogControllers.set(id, controller);
    return controller;
  }

  function openModal(id) {
    lineageDialogController(id)?.open();
  }

  function closeModal(id) {
    lineageDialogController(id)?.close();
  }

  function currentNode() {
    return state.lineage?.nodes?.find(node => node.id === state.selectedNodeId) || null;
  }

  function normalizeHatgViewPreference(value) {
    const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    const viewport = source.viewport && typeof source.viewport === "object" && !Array.isArray(source.viewport)
      ? source.viewport
      : null;
    const normalizedViewport = viewport
      && [viewport.centerX, viewport.centerY, viewport.k].every(item => Number.isFinite(Number(item)))
      ? {
          centerX: Number(viewport.centerX),
          centerY: Number(viewport.centerY),
          k: Number(viewport.k),
          updatedAt: Number(viewport.updatedAt) || Date.now(),
        }
      : null;
    return {
      viewport: normalizedViewport,
      workspace_positions: Object.fromEntries(Object.entries(source.workspace_positions || {})
        .filter(([id, position]) => /^(workspace::workspace_|resident_)[a-z0-9]+$/i.test(id)
          && position && Number.isFinite(position.x) && Number.isFinite(position.y))
        .map(([id, { x, y }]) => [id, { x, y }])),
      // Families are folded to their facade unless the viewer expanded them.
      expanded_family_facade_ids: [...new Set(
        (Array.isArray(source.expanded_family_facade_ids)
          ? source.expanded_family_facade_ids
          : [])
          .map(item => String(item || "").trim())
          .filter(item => /^resident_[a-z0-9]+$/i.test(item)),
      )].slice(-160),
    };
  }

  async function loadHatgViewPreference(lineageId) {
    const key = String(lineageId || "").trim();
    if (!key) return normalizeHatgViewPreference({});
    try {
      await flushHatgViewPreferenceSave(key);
      const payload = await window.PortalIdentity.listUIAssociations(HATG_VIEW_UI_NAMESPACE);
      const association = (payload?.items || []).find(item => item?.key === key);
      const preference = normalizeHatgViewPreference(association?.value);
      hatgViewPreferences.set(key, preference);
      return preference;
    } catch (_error) {
      return hatgViewPreferences.get(key) || normalizeHatgViewPreference({});
    }
  }

  function scheduleHatgViewPreferenceSave(lineageId, patch = {}) {
    const key = String(lineageId || "").trim();
    if (!key) return;
    const current = hatgViewPreferences.get(key) || normalizeHatgViewPreference({});
    const preference = normalizeHatgViewPreference({ ...current, ...patch });
    hatgViewPreferences.set(key, preference);
    window.clearTimeout(hatgViewSaveTimers.get(key));
    hatgViewSaveTimers.set(key, window.setTimeout(() => {
      flushHatgViewPreferenceSave(key).catch(() => {});
    }, 300));
  }

  function flushHatgViewPreferenceSave(lineageId) {
    const key = String(lineageId || "").trim();
    if (!hatgViewSaveTimers.has(key)) return hatgViewSaveRequests.get(key) || Promise.resolve();
    window.clearTimeout(hatgViewSaveTimers.get(key));
    hatgViewSaveTimers.delete(key);
    const preference = hatgViewPreferences.get(key);
    // Serialize writes, but let a committed structural edit wait for its
    // camera before closing its form or reading preferences again.
    const pending = (hatgViewSaveRequests.get(key) || Promise.resolve()).catch(() => {})
      .then(() => window.PortalIdentity.putUIAssociation(HATG_VIEW_UI_NAMESPACE, key, preference))
      .catch(error => {
        console.warn("HATG personal view save failed", error);
        toast(window.PortalI18n.t("视图位置保存失败，当前调整仍保留；请再次拖动或整理以重试。"), "error");
        throw error;
      }).finally(() => {
        if (hatgViewSaveRequests.get(key) === pending) hatgViewSaveRequests.delete(key);
      });
    hatgViewSaveRequests.set(key, pending);
    return pending;
  }

  function displayedProgram() {
    const authored = window.PortalLineageProgram?.authoredProgram?.(
      state.lineage?.program || {},
    ) || { components: [], wires: [] };
    return authored;
  }

  function currentProgram() {
    return window.PortalLineageProgram?.normalizeProgram?.(
      state.lineage?.program || {},
    ) || { components: [], wires: [] };
  }

  function displayProgramComponentById(componentId) {
    return displayedProgram().components.find(component => component.id === componentId) || null;
  }

  function functionBlockInstanceById(instanceId) {
    return (state.lineage?.function_blocks || []).find(
      instance => instance.id === instanceId,
    ) || null;
  }

  function programResourceAddress(namespace, itemId, lineage = state.lineage) {
    const lineageId = String(lineage?.lineage_id || "").trim();
    const localId = String(itemId || "").trim();
    if (
      !lineageId
      || !localId
      || !["component", "wire"].includes(namespace)
    ) return "";
    return `${lineageId}/program/${namespace}/${localId}`;
  }

  function inspectorResourceAddressHtml(address, label = "资源地址") {
    if (!address) return "";
    return window.PortalI18n.html`<div class="programInspectorResource" data-program-resource-address="${esc(address)}">
      <span>${esc(label)}</span>
      <div>
        <code title="${esc(address)}">${esc(address)}</code>
        <button type="button" data-program-copy-resource aria-label="复制资源地址" title="复制资源地址">复制</button>
      </div>
    </div>`;
  }

  function programResourceAddressHtml(namespace, itemId) {
    return inspectorResourceAddressHtml(
      programResourceAddress(namespace, itemId),
      "嵌套资源地址",
    );
  }

  function programComponentById(componentId) {
    return (state.lineage?.program?.components || []).find(component => (
      component?.id === componentId
    )) || null;
  }

  function displayProgramWireById(wireId) {
    return displayedProgram().wires.find(wire => wire.id === wireId) || null;
  }

  function isProgramComponent(node) {
    return Boolean(
      !isFunctionBlockInstance(node)
      && (
        node?._program_component
        || window.PortalLineageProgram?.componentKinds?.includes(
          String(node?.kind || ""),
        )
      )
    );
  }

  function isFunctionBlockInstance(node) {
    return Boolean(
      node?._function_block
      || String(node?.kind || "") === "function_block"
      || (node?.id && functionBlockInstanceById(node.id))
    );
  }

  // A Seed's source is its candidate fork, a relation of the Lineage
  // (decision_4adcc1ff2f2c); a Seed view carries it as source_resident_id.
  function candidateForkOf(seedId, lineage = state.lineage) {
    return (lineage?.edges || []).find(edge => edge?.type === "candidate_fork" && edge.target === seedId) || null;
  }

  function seedView(component, lineage = state.lineage) {
    return {
      ...component,
      ...(component.config || {}),
      source_resident_id: candidateForkOf(component.id, lineage)?.source || "",
    };
  }

  function activeSeeds(lineage = state.lineage) {
    return (lineage?.program?.components || [])
      .filter(component => component?.kind === "seed")
      .map(component => seedView(component, lineage))
      .filter(seed => String(seed.status || "active") === "active");
  }

  function displaySeedById(seedId) {
    return activeSeeds(state.lineage).find(seed => seed.id === seedId) || null;
  }

  function seedById(seedId) {
    const component = programComponentById(seedId);
    return component?.kind === "seed" ? seedView(component) : null;
  }

  function graphSelectedId() {
    const programObjectId = state.selectedProgramObjectId;
    if (programObjectId) {
      const wire = displayProgramWireById(programObjectId);
      if (wire) return wire.source;
      if (
        displayProgramComponentById(programObjectId)
        || functionBlockInstanceById(programObjectId)
        || displayNodeById(programObjectId)
      ) return programObjectId;
    }
    return state.selectedSeedId || state.selectedNodeId;
  }

  function graphSelectedEdgeId() {
    return displayProgramWireById(state.selectedProgramObjectId)?.id
      || displayProgramWireById(state.programReferenceFeedbackWireId)?.id
      || state.selectedEdgeId;
  }

  function displayGraphItemById(itemId) {
    return displayNodeById(itemId)
      || displaySeedById(itemId)
      || functionBlockInstanceById(itemId)
      || displayProgramComponentById(itemId);
  }

  function graphItemById(itemId) {
    // Layout edits must update the stored graph objects. Seed and Function Block
    // display views are copies and cannot own position changes.
    return nodeById(itemId)
      || (state.lineage?.function_blocks || []).find(instance => instance?.id === itemId)
      || programComponentById(itemId);
  }

  function reconcileGraphInteractionState() {
    if (state.selectedProgramObjectId) {
      const selectedId = state.selectedProgramObjectId;
      const valid = Boolean(
        displayProgramComponentById(selectedId)
        || functionBlockInstanceById(selectedId)
        || displayProgramWireById(selectedId)
      );
      if (!valid) state.selectedProgramObjectId = "";
    }
    const focusId = state.graphFocusFamilyFacadeId || graphSelectedId();
    const folded = state.graphFocusMode ? foldedFamilyObjects() : null;
    const focusRemoved = state.graphFocusFamilyFacadeId
      ? !familyForFacade(state.graphFocusFamilyFacadeId)
      : !displayGraphItemById(graphSelectedId());
    // A newly folded outer Family can hide a still-existing focused member.
    // Leave that neighborhood before intersecting it with the visible graph.
    if (state.graphFocusMode && (focusRemoved || folded.residents.has(focusId) || folded.instances.has(focusId))) {
      state.graphFocusMode = false;
      state.graphFocusFamilyFacadeId = "";
      state.familyViewReturn = null;
      // The old focused facade no longer defines a restorable camera. Save
      // this replacement view so reload does not return to its former bounds.
      window.requestAnimationFrame(() => state.graphController?.fit?.({ duration: 220, maxScale: 1.45, persist: true }));
    }
    if (
      (state.selectedNodeId || state.selectedSeedId)
      && !displayNodeById(state.selectedNodeId)
      && !displaySeedById(state.selectedSeedId)
    ) {
      // A selected node that left the graph is not replaced by the Leader: the
      // Chat keeps its Resident and reports it deleted or no longer readable.
      clearNodeSelection();
    }
    reconcileCanvasSelection();
    if (!state.programWireDraft) return;
    if (!state.lineage) {
      programFeature.setWireDraft(null);
      return;
    }
    const programApi = window.PortalLineageProgram;
    const projection = programApi?.projectGraph?.(
      programApi?.authoredProgram?.(currentProgram()) || currentProgram(),
      state.lineage.nodes || [],
    );
    const ports = (projection?.nodes || []).flatMap(node => (
      programApi?.portsForNode?.(node) || []
    ));
    const draftKey = programApi?.portKey?.(state.programWireDraft);
    if (
      !draftKey
      || !ports.some(port => programApi.portKey(port) === draftKey)
    ) {
      programFeature.setWireDraft(null);
    }
  }

  function displayNodeById(nodeId) {
    return state.lineage?.nodes?.find(node => node.id === nodeId) || null;
  }

  function displayEdgeById(edgeId) {
    return state.lineage?.edges?.find(edge => edge.id === edgeId) || null;
  }

  function nodeById(nodeId) {
    return state.lineage?.nodes?.find(node => node.id === nodeId) || null;
  }

  function edgeById(edgeId) {
    return state.lineage?.edges?.find(edge => edge.id === edgeId) || null;
  }

  function isPacketOperatorComponent(node) {
    return window.PortalLineageElementFields.isOperatorKind(node?.kind);
  }

  function isOperatorExecutorNode(node) {
    return Boolean(node?.operator_executor?.component_id);
  }

  function packetOperatorImplementation(config, kind = "") {
    return window.PortalLineageElementFields.operatorImplementation(config, kind);
  }

  function lineageEdgeReferencesResident(edge, residentId) {
    const normalizedId = String(residentId || "");
    if (!edge || !normalizedId) return false;
    return edge.source === normalizedId || edge.target === normalizedId;
  }

  function manualProgramEventKey(componentId, lineageId = state.lineage?.lineage_id) {
    return `${String(lineageId || "")}:${String(componentId || "")}`;
  }

  function programEventSourceRuntime(componentId) {
    return state.lineage?.program_event_source_runtime?.[componentId] || {};
  }

  function programEventSourceStatus(componentId) {
    const runtime = programEventSourceRuntime(componentId);
    if (runtime.paused || runtime.state === "paused") return { state: "paused", label: window.PortalI18n.t("已关闭"), reason: "" };
    if (runtime.exhausted || runtime.state === "exhausted") return { state: "exhausted", label: window.PortalI18n.t("次数耗尽"), reason: "" };
    if (["blocked", "error"].includes(runtime.state)) {
      return { state: runtime.state, label: runtime.state === "blocked" ? "自动触发受阻" : "运行错误", reason: String(runtime.error || "请查看 Lineage 诊断。") };
    }
    return { state: runtime.state || "unknown", label: runtime.state === "listening" ? "监听中" : "等待运行状态", reason: "" };
  }

  function syncProgramRunAsGuidance(panel) {
    const note = panel?.querySelector("[data-event-run-as-note]");
    if (!note) return;
    const eventType = panel.querySelector('[name="event_type"]')?.value;
    const section = panel.querySelector("[data-event-run-as]");
    section.hidden = eventType !== "time.reached";
    const principal = state.lineage?.run_as_principal;
    const explicit = principal?.kind === "user" && String(principal.id || "").trim();
    note.textContent = explicit
      ? window.PortalI18n.text`自动运行 User：${principal.id}。时间事件以此 User 的身份和权限执行。`
      : window.PortalI18n.t("未指定 User。时间事件可正常自动触发，Resident 使用自身身份和权限执行。");
  }

  function programEffectRuntime(componentId) {
    return state.lineage?.program_effect_runtime?.[componentId] || {};
  }

  function programComponentPaused(component) {
    return Boolean((component?.kind === "effect"
      ? programEffectRuntime(component.id)
      : programEventSourceRuntime(component?.id)).paused);
  }

  function programComponentSwitchHtml(component, readonly) {
    const paused = programComponentPaused(component);
    const kind = component.kind === "effect" ? "Effect" : "Event";
    return window.PortalI18n.html`<section class="programComponentSwitch" data-component-switch-state="${paused ? "closed" : "open"}">
      <div><strong>${esc(kind)} 开关</strong><small data-component-switch-note aria-live="polite">${esc(paused ? (kind === "Event" ? "已关闭，不会触发事件。" : "已关闭，不会执行动作。") : "已打开，运行状态已保存。")}</small></div>
      <button type="button" role="switch" data-program-component-switch aria-label="${esc(window.PortalI18n.text`${kind} 运行开关`)}" aria-checked="${!paused}"${readonly ? " disabled" : ""}><span class="programComponentSwitchTrack" aria-hidden="true"></span><span data-component-switch-label>${esc(window.PortalI18n.t(paused ? "已关闭" : "已打开"))}</span></button>
    </section>`;
  }

  function syncProgramComponentSwitch(panel, component) {
    const control = panel?.querySelector("[data-program-component-switch]");
    if (!control) return;
    const paused = programComponentPaused(component);
    const busy = state.programMutationInFlight;
    control.disabled = busy;
    control.setAttribute("aria-checked", String(!paused));
    control.setAttribute("aria-busy", String(busy));
    panel.querySelector("[data-component-switch-state]").dataset.componentSwitchState = paused ? "closed" : "open";
    panel.querySelector("[data-component-switch-label]").textContent = window.PortalI18n.t(busy ? "更新中…" : paused ? "已关闭" : "已打开");
    panel.querySelector("[data-component-switch-note]").textContent = window.PortalI18n.t(paused
      ? component.kind === "effect" ? "已关闭，不会执行动作。" : "已关闭，不会触发事件。"
      : "已打开，运行状态已保存。");
    if (component.kind === "event_source") {
      const runtime = programEventSourceRuntime(component.id);
      const emit = panel.querySelector("[data-program-emit]");
      if (emit) emit.disabled = busy || paused || Boolean(runtime.exhausted)
        || state.manualProgramEventsInFlight.has(manualProgramEventKey(component.id));
      const status = panel.querySelector("[data-event-listening-status]");
      const presentation = programEventSourceStatus(component.id);
      if (status) {
        status.textContent = [window.PortalI18n.t(presentation.label), presentation.reason].filter(Boolean).join(" · ");
        status.dataset.eventState = presentation.state;
      }
      syncProgramRunAsGuidance(panel);
    }
  }

  async function toggleProgramComponent(panel, component) {
    if (state.programMutationInFlight || !state.lineage) return;
    const paused = programComponentPaused(component);
    const kind = component.kind === "effect" ? "Effect" : "Event";
    const control = panel.querySelector("[data-program-component-switch]");
    if (control) control.disabled = true;
    try {
      return await applyProgramMutation(
        () => executeHatgInstruction("PauseComponent", { component_id: component.id, paused: !paused }, { definition: false }),
        window.PortalI18n.text`${kind} 已${paused ? "打开" : "关闭"}。`,
        { failure: window.PortalI18n.text`${kind} ${paused ? "打开" : "关闭"}失败` },
      );
    } finally {
      if (panel.isConnected && panel.dataset.componentId === component.id) syncProgramComponentSwitch(panel, component);
    }
  }

  function programExecutionExplanation(objectId) {
    const projection = state.lineage?.program_execution_explanations || {};
    return projection.nodes?.[objectId] || projection.wires?.[objectId] || null;
  }

  function programExecutionExplanationHtml(objectId) {
    const explanation = programExecutionExplanation(objectId);
    if (!explanation) return "";
    const rows = [
      ["原因", explanation.why],
      ["上一步", explanation.last_action],
      ["下一步", explanation.next_action],
      ["阻塞", explanation.blocked_reason],
    ].filter(([, value]) => String(value || "").trim());
    return window.PortalI18n.html`<section class="primitiveExecutionExplanation" data-execution-state="${esc(explanation.state || "idle")}" data-execution-tone="${esc(explanation.tone || "neutral")}">
      <header><span>当前行为</span><strong>${esc(explanation.summary || window.PortalI18n.t("等待运行状态"))}</strong></header>
      ${rows.length ? window.PortalI18n.html`<dl>${rows.map(([label, value]) => window.PortalI18n.html`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>` : ""}
    </section>`;
  }

  function programEventSourceNodeControl(node) {
    if (
      !state.lineage
      || !isProgramComponent(node)
      || node.kind !== "event_source"
    ) return null;
    const key = manualProgramEventKey(node.id);
    const programBusy = state.programMutationInFlight;
    const runtime = programEventSourceRuntime(node.id);
    const maximum = normalizeOptionalLimit(
      runtime.max_fires ?? node.config?.max_fires,
      100000,
    );
    if (runtime.exhausted && maximum !== 0) {
      return {
        kind: "event_source",
        action: "reset-event-source",
        busy: state.programEventSourceResetsInFlight.has(key),
        disabled: programBusy,
        label: window.PortalI18n.text`重置「${node.title || window.PortalI18n.t("事件")}」的触发次数`,
        title: programBusy
          ? window.PortalI18n.t("事件配置正在保存；保存完成后才能重置。")
          : window.PortalI18n.t("清零这个事件已使用的触发次数，并重新开始监听后续事件。"),
        iconName: "refresh",
      };
    }
    if (String(node.config?.event_type || "") !== "manual.triggered") return null;
    return {
      kind: "event_source",
      action: "emit-manual-event",
      busy: state.manualProgramEventsInFlight.has(key),
      disabled: programBusy || Boolean(runtime.paused),
      label: window.PortalI18n.text`触发一次「${node.title || window.PortalI18n.t("手动 Event")}」事件`,
      title: runtime.paused
        ? window.PortalI18n.t("Event 已关闭；打开后才能触发。")
        : programBusy
        ? window.PortalI18n.t("事件配置正在保存；保存完成后才能手动触发。")
        : window.PortalI18n.t("立即产生一次手动事件，并沿 Route 连线启动所有已连接的下游节点。"),
      iconName: "play",
    };
  }

  function nodeExecutionGate(node) {
    const resident = state.residentRuntimeById.get(nodeResidentId(node));
    return window.PortalResidentProtocol.executionGateView(resident?.resident_state || resident);
  }

  function nodeIntakeState(node) {
    const resident = state.residentRuntimeById.get(nodeResidentId(node));
    return window.PortalResidentProtocol.stateView(
      resident?.resident_state || resident,
    ).intake_state;
  }

  function nodeWorkPresentation(node, status = effectiveNodeStatus(node)) {
    const resident = state.residentRuntimeById.get(nodeResidentId(node));
    const work = window.PortalResidentProtocol.stateView(resident?.resident_state || resident);
    if (work.display_work_state === "queued" && work.work_phase === "waiting_quota") {
      return {
        phase: "waiting_quota",
        label: window.PortalI18n.t("等待额度"),
        title: window.PortalI18n.t("自动任务已暂停：账号额度已用尽、达到配置的保护阈值或尚未确认"),
      };
    }
    if (work.display_work_state === "stopping") {
      return { phase: "stopping", label: window.PortalI18n.t("正在停止…"), title: window.PortalI18n.t("正在停止当前任务") };
    }
    return { phase: "", label: status, title: "" };
  }

  // A placed instance with one manual Event (Compact's 压缩, 定时工作's
  // 运行一次) is triggered from its card; inner objects are not on the canvas.
  function functionBlockInstanceNodeControl(node) {
    const instance = functionBlockInstanceById(node.id);
    const sources = instanceManualSources(instance);
    if (!sources.length) return null;
    const programBusy = state.programMutationInFlight;
    const title = window.PortalI18n.t("手动触发");
    return {
      kind: "function_block",
      action: "trigger-function-block",
      presentation: "corner",
      busy: sources.some(source => state.manualProgramEventsInFlight.has(manualProgramEventKey(source.id))),
      disabled: programBusy,
      label: window.PortalI18n.text`触发「${node.title || window.PortalI18n.t("Function Block")}」的「${title}」`,
      title: programBusy
        ? window.PortalI18n.t("Lineage 正在保存；保存完成后才能触发。")
        : window.PortalI18n.text`立即触发实例内部的「${title}」事件。`,
      iconName: "play",
    };
  }

  function hatgNodeControl(node) {
    if (isFunctionBlockInstance(node)) return functionBlockInstanceNodeControl(node);
    return programEventSourceNodeControl(node);
  }

  function runningLineageNodes() {
    return (state.lineage?.nodes || []).filter(
      node => effectiveNodeStatus(node) === "running",
    );
  }

  function hatgNodeHoverActions(node) {
    if (isFunctionBlockInstance(node)) {
      const roles = familyRoles(node);
      return roles ? [["router", "Router", "branch"], ["steward", "Steward", "shield-check"]]
        .filter(([role]) => displayNodeById(roles[role]))
        .map(([role, label, iconName], index) => ({
          action: `open-${role}`,
          kind: "resident-chat",
          placement: "below",
          index,
          label,
          title: `${label} · ${displayNodeById(roles[role]).title || roles[role]}`,
          iconName,
        })) : [];
    }
    if (!state.lineage || !nodeById(node?.id)) return [];
    if (effectiveNodeStatus(node) !== "running") return [];
    return [{
      action: "stop",
      kind: "resident-stop",
      busy: state.nodeStopInFlight.has(node.id),
      label: window.PortalI18n.text`Stop ${node.title || window.PortalI18n.t("节点")}`,
      title: window.PortalI18n.text`停止「${node.title || window.PortalI18n.t("节点")}」当前正在运行的 Session；效果与对话栏中的 Stop 相同。`,
      iconName: "stop",
    }];
  }

  const RELATION_FAMILY = Object.freeze({
    inheritance: "context",
    absorption: "context",
    candidate_fork: "context",
    handoff: "workspace",
  });

  const RELATION_LABEL = Object.freeze({
    inheritance: window.PortalI18n.t("Fork 继承"),
    absorption: window.PortalI18n.t("节点吸收"),
    candidate_fork: window.PortalI18n.t("候选 Fork"),
    handoff: window.PortalI18n.t("Workspace Handoff"),
  });

  function relationFamily(edgeOrType) {
    const type = typeof edgeOrType === "string" ? edgeOrType : edgeOrType?.type;
    return RELATION_FAMILY[type] || "context";
  }

  function relationVisibleInView(edge, mode) {
    if (mode === "context") {
      return relationFamily(edge) === "context" || edge?.type === "handoff";
    }
    return relationFamily(edge) === mode;
  }

  function workspaceBindingForNode(nodeId) {
    return state.workspaceGraphBindings.get(nodeId) || null;
  }

  function boundWorkspacesForNode(nodeId) {
    return window.PortalHatgWorkspaceProjection.workspaces(workspaceBindingForNode(nodeId));
  }

  function preferredWorkspaceForNode(nodeId) {
    const workspaces = boundWorkspacesForNode(nodeId);
    return workspaces.find(item => item.is_default_cwd)
      || workspaces[0]
      || null;
  }

  function fillWorkspaceSelect(select, nodeId, selectedDirectoryId = "") {
    const workspaces = boundWorkspacesForNode(nodeId);
    select.innerHTML = workspaces.length
      ? workspaces.map(workspace => {
        const directoryId = String(workspace.workspace_id || "");
        const suffix = workspace.is_default_cwd ? " · default cwd" : "";
        return window.PortalI18n.html`<option value="${esc(directoryId)}">${esc(workspace.label || "Workspace")}${esc(suffix)} · ${esc(workspace.path)}</option>`;
      }).join("")
      : window.PortalI18n.markup('<option value="">没有可交接的 Workspace</option>');
    const preferred = selectedDirectoryId
      ? workspaces.find(item => item.workspace_id === selectedDirectoryId)
      : preferredWorkspaceForNode(nodeId);
    if (preferred) select.value = String(preferred.workspace_id || "");
    return workspaces;
  }

  function selectedBoundWorkspace(select, nodeId) {
    const directoryId = String(select?.value || "");
    return boundWorkspacesForNode(nodeId).find(item => (
      directoryId === String(item.workspace_id || "")
    )) || null;
  }

  function effectiveNodeStatus(node) {
    return state.runtimeStatus.get(node?.id) || node?.status || "idle";
  }

  function setNodeRuntimeStatus(nodeId, status) {
    if (!nodeId) return;
    state.runtimeStatus.set(nodeId, status);
  }

  function nodeResidentId(node) {
    return String(node?.id || "").trim();
  }

  function nodeIsDerived(node) {
    return Boolean(String(node?.source_resident_id || "").trim());
  }

  function nodeUserMessageCount(node) {
    const key = nodeResidentId(node);
    if (key && state.sessionUserMessageCounts.has(key)) return state.sessionUserMessageCounts.get(key);
    return null;
  }

  function nodeMessageCountText(node) {
    const count = nodeUserMessageCount(node);
    return count === null ? "— messages" : `${count} messages`;
  }

  function normalizeContextPressure(value) {
    const pressure = value && typeof value === "object" ? value : {};
    const contextTokens = Math.max(0, Math.floor(Number(pressure.context_tokens) || 0));
    const contextWindow = Math.max(0, Math.floor(Number(pressure.model_context_window) || 0));
    const rawPercent = pressure.used_percent;
    const numericPercent = rawPercent === null || rawPercent === undefined || rawPercent === ""
      ? null
      : Number(rawPercent);
    const usedPercent = Number.isFinite(numericPercent) ? Math.max(0, numericPercent) : null;
    const heatLevel = ["warm", "hot"].includes(String(pressure.heat_level || ""))
      ? String(pressure.heat_level)
      : "normal";
    return {
      context_tokens: contextTokens,
      model_context_window: contextWindow,
      used_percent: usedPercent,
      heat_level: heatLevel,
      updated_at: String(pressure.updated_at || ""),
    };
  }

  function nodeContextPressure(node) {
    const key = nodeResidentId(node);
    return key && state.sessionContextPressures.has(key)
      ? state.sessionContextPressures.get(key)
      : normalizeContextPressure(null);
  }


  function nodeContextHeatBadge(node) {
    const pressure = nodeContextPressure(node);
    if (!["warm", "hot"].includes(pressure.heat_level)) return "";
    const levelLabel = pressure.heat_level === "hot" ? window.PortalI18n.t("上下文很长") : window.PortalI18n.t("上下文偏长");
    const compactUsage = `${pressure.used_percent !== null
      ? `${Math.round(pressure.used_percent)}%`
      : window.PortalChatWorkspace.formatTokenCount(pressure.context_tokens)} context`;
    const usageDetail = pressure.used_percent !== null && pressure.model_context_window
      ? `${pressure.used_percent.toFixed(1)}%（${window.PortalChatWorkspace.formatTokenCount(pressure.context_tokens)} / ${window.PortalChatWorkspace.formatTokenCount(pressure.model_context_window)} tokens）`
      : `最近一轮输入约 ${window.PortalChatWorkspace.formatTokenCount(pressure.context_tokens)} tokens`;
    const title = window.PortalI18n.text`${levelLabel}：${usageDetail}。建议 Compact，或从较短上下文执行 Fork 以创建新 Resident。`;
    return window.PortalI18n.html`<span class="nodeContextHeat" data-context-heat-label="${esc(pressure.heat_level)}" title="${esc(title)}">${esc(compactUsage)}</span>`;
  }

  function applyNodeMessageCounts(payload, options = {}) {
    if (!payload || payload.lineage_id !== state.lineage?.lineage_id) return false;
    let presentationChanged = false;
    for (const session of payload.sessions || []) {
      const key = String(session?.resident_id || "").trim();
      if (!key) continue;
      const rawCount = Number(session.user_message_count);
      const count = session.available !== false && Number.isFinite(rawCount) && rawCount >= 0
        ? Math.floor(rawCount)
        : null;
      if (!state.sessionUserMessageCounts.has(key) || state.sessionUserMessageCounts.get(key) !== count) {
        state.sessionUserMessageCounts.set(key, count);
        presentationChanged = true;
      }
      const pressure = normalizeContextPressure(session.context_pressure);
      const previousPressure = state.sessionContextPressures.get(key);
      const pressurePresentationChanged = (
        !previousPressure
        || previousPressure.context_tokens !== pressure.context_tokens
        || previousPressure.model_context_window !== pressure.model_context_window
        || previousPressure.used_percent !== pressure.used_percent
        || previousPressure.heat_level !== pressure.heat_level
      );
      if (pressurePresentationChanged || previousPressure?.updated_at !== pressure.updated_at) {
        state.sessionContextPressures.set(key, pressure);
      }
      if (pressurePresentationChanged) presentationChanged = true;
    }
    if (presentationChanged && options.render !== false) renderGraphRuntimePresentation();
    return presentationChanged;
  }

  function publishLocation(lineageId, programItemId = "") {
    return hatgComponentOptions.onLocationChange?.({
      lineageId: String(lineageId || ""),
      programItemId: String(programItemId || ""),
    });
  }

  function setSyncStatus(text, status = "idle") {
    const el = $id("graphSyncStatus");
    if (!el) return;
    el.textContent = text;
    el.dataset.state = status;
  }

  function updateLineageSummary() {
    if (!state.lineage) return;
    const summary = state.lineages.find(item => item.lineage_id === state.lineage.lineage_id);
    const statuses = {};
    state.lineage.nodes.forEach(node => {
      const status = effectiveNodeStatus(node);
      statuses[status] = (statuses[status] || 0) + 1;
    });
    const next = {
      lineage_id: state.lineage.lineage_id,
      title: state.lineage.title,
      description: state.lineage.description,
      leader_resident_id: state.lineage.leader_resident_id,
      node_count: state.lineage.nodes.length,
      edge_count: state.lineage.edges.length,
      active_seed_count: activeSeeds(state.lineage).length,
      statuses,
      revision: state.lineage.revision,
      layout_revision: state.lineage.layout_revision,
      runtime_revision: state.lineage.runtime_revision,
      created_at: state.lineage.created_at,
      updated_at: state.lineage.updated_at,
      graph_updated_at: state.lineage.graph_updated_at,
      layout_updated_at: state.lineage.layout_updated_at,
      runtime_updated_at: state.lineage.runtime_updated_at,
    };
    if (summary) Object.assign(summary, next);
    else state.lineages.unshift(next);
  }

  function createLineageEditQueue() {
    const tails = new Map();
    let active = null;
    const assertCurrent = intent => {
      if (!intent || intent !== active || !intent.lineageId) {
        const error = new Error("Lineage 已切换或编辑器已关闭，本次编辑已取消");
        error.code = "lineage_edit_superseded";
        throw error;
      }
    };
    return {
      isActive(lineageId) {
        return Boolean(active && active.lineageId === lineageId);
      },
      activate(lineageId = "") {
        active = lineageId ? { lineageId, layoutError: null } : null;
        return active;
      },
      capture(lineageId) {
        assertCurrent(active);
        if (active.lineageId !== lineageId) {
          const error = new Error("Lineage 正在切换，本次编辑已取消");
          error.code = "lineage_edit_superseded";
          throw error;
        }
        return active;
      },
      isCurrent: intent => Boolean(intent && intent === active),
      assertCurrent,
      enqueue(intent, request, options = {}) {
        const previous = tails.get(intent.lineageId) || Promise.resolve();
        const run = previous.catch(() => null).then(async () => {
          assertCurrent(intent);
          if (!options.layout && intent.layoutError) throw intent.layoutError;
          try {
            const result = await request();
            assertCurrent(intent);
            if (options.layout) intent.layoutError = null;
            return result;
          } catch (error) {
            if (options.layout) intent.layoutError = error;
            throw error;
          }
        });
        tails.set(intent.lineageId, run);
        const settled = () => {
          if (tails.get(intent.lineageId) === run) tails.delete(intent.lineageId);
        };
        run.then(settled, settled);
        return run;
      },
    };
  }

  const lineageEditQueue = createLineageEditQueue();

  function activateLineageEdits(lineageId = "") {
    const intent = lineageEditQueue.activate(lineageId);
    state.layoutSaveQueue = new Map();
    state.layoutSaveInFlight = null;
    programFeature.endMutation();
    return intent;
  }

  async function flushLineageLayoutSave() {
    if (!state.lineage) return null;
    const intent = lineageEditQueue.capture(state.lineage.lineage_id);
    if (state.layoutSaveInFlight) return state.layoutSaveInFlight;
    if (!state.layoutSaveQueue.size) return null;
    const lineageId = intent.lineageId;
    const pending = state.layoutSaveQueue;
    const run = lineageEditQueue.enqueue(intent, async () => {
      let latest = null;
      while (lineageEditQueue.isCurrent(intent) && pending.size) {
        const positions = [...pending.values()];
        pending.clear();
        setSyncStatus("保存画布布局…", "saving");
        try {
          latest = await societyService.updateLayout( {
            lineage_id: lineageId,
            positions,
          });
        } catch (error) {
          for (const position of positions) {
            if (!pending.has(position.object_id)) pending.set(position.object_id, position);
          }
          throw error;
        }
        if (!lineageEditQueue.isCurrent(intent)) return latest;
        const responseRevision = Number(latest.layout_revision || 0);
        if (responseRevision >= Number(state.lineage.layout_revision || 0)) {
          state.lineage.layout_revision = responseRevision;
          state.lineage.layout_updated_at = String(latest.layout_updated_at || state.lineage.layout_updated_at || "");
        }
      }
      if (lineageEditQueue.isCurrent(intent)) {
        setSyncStatus(`布局已保存 · revision ${Number(state.lineage.revision || 0)}`, "saved");
      }
      return latest;
    }, { layout: true });
    const saving = run.catch(error => {
      if (lineageEditQueue.isCurrent(intent)) {
        setSyncStatus("布局保存失败", "error");
        toast(window.PortalI18n.text`画布布局保存失败：${error.message || error}`, "error", 5200);
      }
      throw error;
    }).finally(() => {
      if (lineageEditQueue.isCurrent(intent) && state.layoutSaveInFlight === saving) {
        state.layoutSaveInFlight = null;
      }
    });
    state.layoutSaveInFlight = saving;
    return saving;
  }

  function saveLineageLayout(items) {
    if (!state.lineage) return Promise.resolve(null);
    for (const item of items || []) {
      const objectId = String(item?.id || item?.object_id || "").trim();
      const x = Number(item?.x);
      const y = Number(item?.y);
      if (!objectId || !Number.isFinite(x) || !Number.isFinite(y)) continue;
      state.layoutSaveQueue.set(objectId, {
        object_id: objectId,
        x,
        y,
      });
    }
    return flushLineageLayoutSave();
  }

  function renderLineageHeader() {
    const lineage = state.lineage;
    const lineageTitle = lineage?.title || window.PortalI18n.t("选择 Lineage");
    const lineageId = String(lineage?.lineage_id || "").trim();
    const cleanTitle = lineageTitle.replace(/\s*\|\s*Lineage\s*$/i, "").trim() || lineageTitle;
    window.PortalResourceHeader?.mount?.($id("activeLineageIdentity"), {
      title: cleanTitle,
      type: lineage ? "lineage" : "",
      resource_id: lineageId,
      copy_label: "lineage_id",
    }, {
      headingTag: "h1",
      titleId: "activeLineageTitle",
      typeId: "activeLineageType",
      idId: "activeLineageSubtitle",
    });
  }

  function renderControls() {
    const lineage = state.lineage;
    const display = state.lineage;
    const node = currentNode();
    const editable = !!lineage;
    ["lineageSettingsBtn", "addNodeBtn", "autoLayoutBtn", "addTemplateBtn", "reconcileLineageRuntimeBtn", "emptyAddNodeBtn"]
      .forEach(id => { if ($id(id)) $id(id).disabled = !editable; });
    const leaderJump = $id("graphLeaderJumpBtn");
    if (leaderJump) {
      const leaderId = String(lineage?.leader_resident_id || "").trim();
      const leader = leaderId ? displayNodeById(leaderId) : null;
      const label = leader
        ? window.PortalI18n.text`跳转到 Leader：${leader.title || leaderId}`
        : window.PortalI18n.t("当前 Lineage 没有可用的 Leader");
      leaderJump.disabled = !leader;
      leaderJump.title = label;
      leaderJump.setAttribute("aria-label", label);
    }
    renderAutoLayoutControl();
    const familyFacadeSelected = !!familyForFacade(node?.id);
    if ($id("branchNodeBtn")) $id("branchNodeBtn").disabled = !editable || !node || familyFacadeSelected;
    const wrapButton = $id("wrapFunctionBlockBtn");
    const wrapCount = $id("wrapFunctionBlockCount");
    const selectedCanvasCount = state.selectedCanvasObjectIds.size;
    ["saveSelectionTemplateBtn", "saveSelectionRewriteBtn"].forEach(id => {
      const button = $id(id);
      if (button) button.disabled = !state.lineage
        || state.graphViewMode !== "context" || !selectedCanvasCount || state.programMutationInFlight;
    });
    if (wrapButton) {
      wrapButton.disabled = (
        !editable
        || state.graphViewMode !== "context"
        || !selectedCanvasCount
      );
      wrapButton.classList.toggle(
        "is-active",
        selectedCanvasCount > 0,
      );
    }
    const familizeButton = $id("familizeBtn");
    if (familizeButton) {
      familizeButton.disabled = (
        !editable
        || state.graphViewMode !== "context"
        || selectedFamilyCandidateIds().length < 1
      );
    }
    if (wrapCount) {
      wrapCount.textContent = String(selectedCanvasCount);
      wrapCount.dataset.count = String(selectedCanvasCount);
    }
    const moveSubgraphButton = $id("moveSubgraphBtn");
    if (moveSubgraphButton) moveSubgraphButton.disabled = !editable || !selectedCanvasCount
      || state.programMutationInFlight || state.canvasSelectionDeleteInFlight || Boolean(subgraphMoveContext?.busy);
    const batchDeleteButton = $id("batchDeleteSelectionBtn");
    const cancelSelectionButton = $id("cancelCanvasSelectionBtn");
    const selectionHasDeletableObject = selectedCanvasObjects().some(
      canvasObjectCanDelete,
    );
    if (batchDeleteButton) {
      batchDeleteButton.disabled = (
        !editable
        || state.canvasSelectionDeleteInFlight
        || state.programMutationInFlight
        || !selectionHasDeletableObject
      );
      batchDeleteButton.setAttribute(
        "aria-busy",
        state.canvasSelectionDeleteInFlight ? "true" : "false",
      );
      const tooltip = selectionHasDeletableObject
        ? window.PortalI18n.text`批量删除当前选中的 ${selectedCanvasCount} 个 HATG 元素。`
        : window.PortalI18n.t("当前选区只包含不可删除的同步主轨迹节点。");
      batchDeleteButton.dataset.portalTooltip = tooltip;
      batchDeleteButton.setAttribute("aria-label", tooltip.replace(/。$/, ""));
    }
    if (cancelSelectionButton) {
      cancelSelectionButton.disabled = selectedCanvasCount === 0;
    }
    const stopLineage = $id("stopLineageBtn");
    if (stopLineage) {
      const tooltip = window.PortalI18n.t("全局 Stop：对当前 Lineage 每个正在运行的节点执行一次与 Session 对话栏相同的 Stop。");
      const runningCount = runningLineageNodes().length;
      stopLineage.disabled = !editable
        || state.globalStopInFlight
        || runningCount === 0;
      stopLineage.classList.toggle("busy", state.globalStopInFlight);
      stopLineage.setAttribute(
        "aria-busy",
        state.globalStopInFlight ? "true" : "false",
      );
      stopLineage.querySelector("span").textContent = state.globalStopInFlight
        ? window.PortalI18n.t("Stopping…")
        : window.PortalI18n.t("全局 Stop");
      stopLineage.dataset.portalTooltip = tooltip;
      stopLineage.setAttribute("aria-label", tooltip.replace(/。$/, ""));
    }
    const handoff = $id("handoffModeBtn");
    if (handoff) handoff.disabled = !editable || !display || display.nodes.length < 2;
    handoff?.classList.toggle("is-active", state.handoffMode);
    handoff?.setAttribute("aria-pressed", state.handoffMode ? "true" : "false");
    if (handoff) {
      const tooltip = state.handoffMode
        ? window.PortalI18n.t("工作交接：正在选择交接节点；可通过提示栏退出交接模式。")
        : window.PortalI18n.t("工作交接：选择来源和目标，明确传递工件与任务契约。");
      handoff.dataset.portalTooltip = tooltip;
      handoff.setAttribute("aria-label", tooltip.replace(/。$/, ""));
    }
    ["context", "workspace"].forEach(mode => {
      const tab = $id(`graph${mode[0].toUpperCase()}${mode.slice(1)}ViewBtn`);
      const active = state.graphViewMode === mode;
      tab?.classList.toggle("active", active);
      tab?.setAttribute("aria-selected", active ? "true" : "false");
      if (tab) tab.disabled = !display;
    });
    const visibleByView = {
      addNodeBtn: "context",
      branchNodeBtn: "context",
      wrapFunctionBlockBtn: "context",
      saveSelectionTemplateBtn: "context",
      saveSelectionRewriteBtn: "context",
      familizeBtn: "context",
      batchDeleteSelectionBtn: "context",
      moveSubgraphBtn: "context",
      cancelCanvasSelectionBtn: "context",
      handoffModeBtn: "context",
      stopLineageBtn: "context",
    };
    Object.entries(visibleByView).forEach(([id, mode]) => {
      const button = $id(id);
      if (!button) return;
      const selectionAction = button.hasAttribute("data-canvas-selection-action");
      const selectionOnly = [
        "saveSelectionTemplateBtn",
        "saveSelectionRewriteBtn",
        "batchDeleteSelectionBtn",
        "moveSubgraphBtn",
        "cancelCanvasSelectionBtn",
      ].includes(id);
      button.hidden = (
        state.graphViewMode !== mode
        || (id === "branchNodeBtn" && familyFacadeSelected)
        || (selectedCanvasCount > 0 ? !selectionAction : selectionOnly)
      );
    });
    const graphActions = $id("graphActions");
    if (graphActions) {
      const viewLabel = ({ context: "Context", workspace: "Workspace" })[state.graphViewMode] || "HATG";
      graphActions.dataset.viewMode = state.graphViewMode;
      graphActions.dataset.selectionActive = selectedCanvasCount > 0 ? "true" : "false";
      graphActions.setAttribute(
        "aria-label",
        selectedCanvasCount > 0
          ? window.PortalI18n.text`${viewLabel} 多选操作，已选择 ${selectedCanvasCount} 个元素`
          : window.PortalI18n.text`${viewLabel} 操作`,
      );
    }
  }

  function graphNodeContent(node, status) {
    if (node.accessible === false) return window.PortalI18n.markup('<strong>无权访问的 Lineage</strong><span class="hatgRestrictedNode">需要访问凭证</span>');
    if (node.kind === "seed") {
      const typeLabel = ({ idea: "Idea", plan: "Plan", next_step: "Next", other: "Seed" })[node.seed_type] || "Seed";
      const iconName = ({ idea: "idea", plan: "plan", next_step: "arrow-right", other: "status-dot" })[node.seed_type] || "status-dot";
      return window.PortalI18n.html`<span class="seedNodeGlyph" data-node-drag-handle data-portal-icon="${iconName}" title="拖动种子节点"></span>
        <strong title="${esc(node.display_title || node.title)}">${esc(node.display_title || node.title || window.PortalI18n.t("候选"))}</strong>
        <span class="seedNodeKind">${esc(typeLabel)}</span>`;
    }
    if (isFunctionBlockInstance(node)) {
      const failures = functionBlockInstanceUnacknowledgedFailures(node);
      // A placed block is its pigeon alone: the Family's, its template's, or one from its ID.
      const pigeon = window.LineageTemplates.pigeonPosition(node.family
        ? { builtin_key: "family" }
        : { builtin_key: templateController?.find?.(node.template_id)?.builtin_key, id: node.template_id || node.id });
      const title = node.title || window.PortalI18n.t("Function Block");
      return window.PortalI18n.html`<span class="functionBlockInstanceNode" data-node-drag-handle title="${esc(node.family ? title : window.PortalI18n.text`${title} · ${functionBlockSourceTitle(node)} · ${(node.elements || []).length} 个元素`)}">
        <span class="functionBlockPigeon" aria-hidden="true" style="background-position:${pigeon}"></span>
        ${failures.length ? window.PortalI18n.html`<button type="button" class="functionBlockAlert" data-function-block-alert data-function-block-issues="${esc(node.id)}" aria-label="${esc(window.PortalI18n.text`${failures.length} 个未查看问题，查看具体来源`)}" title="${esc(failures.map(issue => `${issue.title || issue.id}：${issue.error}`).join("\n"))}">${failures.length}</button>` : ""}
      </span>`;
    }
    if (isProgramComponent(node)) {
      const event = node.kind === "event_source";
      const effect = node.kind === "effect";
      const config = node.config || {};
      const explanation = programExecutionExplanation(node.id);
      const eventStatus = event ? programEventSourceStatus(node.id) : null;
      let detail = event
        ? window.PortalLineageProgram?.manifest?.event_types?.[config.event_type]?.title
          || "选择系统事件"
        : effect
          ? effectScriptLabel(config.script) || "编写要运行的脚本"
          : node.kind === "loop"
            ? `最多 ${config.max_rounds || 5} 轮`
          : `${config.operator_implementation === "resident" ? "Resident 语义执行" : "确定性规则执行"}${node.kind === "selector" ? ` · ${(config.branches || []).length} 个分支` : ""}`;
      if (event) {
        const runtime = programEventSourceRuntime(node.id);
        const lastDelivery = runtime.last_delivery || {};
        const maximum = normalizeOptionalLimit(
          runtime.max_fires ?? config.max_fires,
          100000,
        );
        const occurrenceCount = Math.max(
          0,
          Number(runtime.occurrence_count || 0),
        );
        if (maximum !== null) {
          detail += ` · ${occurrenceCount} / ${maximum} 次`;
          if (runtime.exhausted) detail += " · 已耗尽";
        } else {
          detail += ` · 已触发 ${occurrenceCount} 次`;
        }
        if (Number(lastDelivery.dropped_count || 0) > 0) {
          detail += ` · 最近丢弃 ${Number(lastDelivery.dropped_count)} 条`;
        } else if (Number(lastDelivery.failed_count || 0) > 0) {
          detail += ` · 最近失败 ${Number(lastDelivery.failed_count)} 条`;
        } else if (Number(lastDelivery.queued_count || 0) > 0) {
          detail += ` · 最近暂存 ${Number(lastDelivery.queued_count)} 条`;
        }
      }
      return window.PortalI18n.html`<span class="programNodeTopline" data-node-drag-handle title="拖动原语">
          <span class="programNodeGlyph" aria-hidden="true" data-portal-icon="${event ? "event" : effect ? "effect" : node.kind}"></span>
          <span class="programNodeKind">${esc(event ? "EVENT" : effect ? "EFFECT" : node.kind.toUpperCase())}</span>
        </span>
        <strong title="${esc(node.title)}">${esc(node.title || (event ? "Event" : effect ? "Effect" : node.kind))}</strong>
        <span class="programNodeDetail">${esc(detail)}</span>
        ${(event || effect) && programComponentPaused(node)
          ? window.PortalI18n.html`<span class="programNodeExecutionState" data-execution-state="paused">已关闭</span>`
          : eventStatus && eventStatus.state !== "paused" ? window.PortalI18n.html`<span class="programNodeExecutionState" data-execution-state="${esc(eventStatus.state)}" title="${esc(eventStatus.reason)}">${esc(window.PortalI18n.t(eventStatus.label))}${eventStatus.reason ? ` · ${esc(eventStatus.reason)}` : ""}</span>`
          : explanation ? window.PortalI18n.html`<span class="programNodeExecutionState" data-execution-state="${esc(explanation.state || "idle")}" title="${esc(explanation.next_action || explanation.why || "")}">${esc(explanation.summary || "")}</span>` : ""}`;
    }
    const dragTitle = window.PortalI18n.t("拖动节点");
    const activity = "Resident";
    const residentId = nodeResidentId(node);
    const intakeState = nodeIntakeState(node);
    const work = nodeWorkPresentation(node, status);
    const workStatus = `<span class="nodeStatus" data-intake-state="${esc(intakeState)}" data-work-phase="${esc(work.phase)}" title="${esc(work.title)}">${esc(work.label)}</span>`;
    const gate = nodeExecutionGate(node);
    const intakeStatus = gate.text
      ? `<span class="nodeIntakeStatus" data-execution-gate="${esc(gate.state)}" title="${esc(gate.text)}">${esc(gate.label)}</span>`
      : "";
    const memberTag = familyMemberTag(node);
    const family = familyForFacade(node.id);
    if (family) {
      const expanded = familyExpanded(family.roles);
      const count = family.roles.members.length;
      return window.PortalI18n.html`<span class="nodeTopline familyFacadeTopline" data-node-drag-handle title="${esc(dragTitle)}"><span class="nodeIdentity" data-resident-id="${esc(residentId)}" title="${esc(residentId)}">${node._is_leader ? '<em class="nodeLeaderBadge">LEADER</em>' : ""}${esc(compactText(residentId, 18))}</span>${workStatus}${intakeStatus}</span>
      <span class="familyFacadeHeading"><small>FAMILY</small><strong class="nodeTitle" title="${esc(node.title)}">${esc(node.title)}</strong></span>
      <span class="nodeFooter"><span class="nodeTypePill familyFacadePill">门面</span><span class="familyFacadeMembers" title="${esc(expanded ? window.PortalI18n.t("成员已展开，点击 − 折叠为门面") : window.PortalI18n.t("点击 + 展开成员"))}">${count} 位成员</span><span class="nodeMessageCount" title="该 Family 已接受的外部消息数">${esc(nodeMessageCountText(node))}</span></span>${memberTag}`;
    }

    const heading = window.PortalI18n.html`<strong class="nodeTitle" title="${esc(node.title)}">${esc(node.title)}</strong>`;
    const typeLabel = nodeIsDerived(node) ? "derived" : "resident";
    return window.PortalI18n.html`<span class="nodeTopline" data-node-drag-handle title="${esc(dragTitle)}"><span class="nodeIdentity" data-resident-id="${esc(residentId)}" title="${esc(residentId)}">${node._is_leader ? '<em class="nodeLeaderBadge">LEADER</em>' : ""}${esc(compactText(residentId, 18))}</span>${workStatus}${intakeStatus}</span>
      ${heading}
      <span class="nodeFooter"><span class="nodeTypePill">${typeLabel}</span><span class="nodeMessageCount" title="该 Resident 已接受的用户消息数">${esc(nodeMessageCountText(node))}</span>${nodeContextHeatBadge(node)}<span class="nodeFooterState">${esc(activity)}</span></span>${memberTag}`;
  }

  // The members of an expanded Family name it in a small tag.
  function familyMemberTag(node) {
    const member = node?._family_member;
    if (member?.role !== "member") return "";
    return window.PortalI18n.html`<span class="familyMemberTag" data-family-role="member" title="${esc(window.PortalI18n.text`Family「${member.title}」的成员`)}">${esc(member.title)}</span>`;
  }

  function functionBlockInstanceFailures(instance) {
    const issues = [];
    for (const component of instanceInnerObjects(instance, "components")) {
      const runtime = component.kind === "effect" ? programEffectRuntime(component.id) : programEventSourceRuntime(component.id);
      const explanation = programExecutionExplanation(component.id);
      if (runtime.status !== "failed" && explanation?.tone !== "danger") continue;
      const error = String(runtime.error || explanation?.blocked_reason || explanation?.why || explanation?.summary || "执行失败").slice(0, 2000);
      issues.push({ ...component, error, at: runtime.at || "", fingerprint: JSON.stringify([component.id, runtime.step_id || runtime.trigger_path_id || runtime.at || "", error]) });
    }
    for (const resident of instanceInnerObjects(instance, "nodes")) {
      const execution = resident.last_execution || {};
      if (execution.status !== "failed") continue;
      const error = String(execution.error || execution.error_code || "执行失败").slice(0, 2000);
      const at = execution.finished_at || execution.started_at || execution.created_at || "";
      // A retry may keep the input and error text; it is a new failure only
      // when the execution identity changes. Layout/title updates are not failures.
      issues.push({ ...resident, kind: "resident", error, at, fingerprint: JSON.stringify([resident.id, execution.execution_id || execution.message_id || at]) });
    }
    return issues;
  }

  function functionBlockInstanceUnacknowledgedFailures(instance) {
    const acknowledged = pageStateController?.read()?.functionBlockFailureAcknowledgements || {};
    return functionBlockInstanceFailures(instance).filter(issue => acknowledged[`${state.lineage.lineage_id}:${issue.id}`] !== issue.fingerprint);
  }

  function acknowledgeFunctionBlockFailures(issues) {
    const acknowledged = { ...(pageStateController.read().functionBlockFailureAcknowledgements || {}) };
    for (const issue of issues) {
      const key = `${state.lineage.lineage_id}:${issue.id}`;
      delete acknowledged[key];
      acknowledged[key] = issue.fingerprint;
    }
    pageStateController.write({ functionBlockFailureAcknowledgements: Object.fromEntries(Object.entries(acknowledged).slice(-100)) });
    renderLineageSurface();
  }

  // Membership is explicit and may contain nested Function Blocks.
  function functionBlockElements(block, seen = new Set()) {
    if (!block || seen.has(block.id)) return [];
    seen.add(block.id);
    return (block.elements || []).flatMap(element => [
      element,
      ...(element.kind === "function_block"
        ? functionBlockElements(functionBlockInstanceById(element.id), seen) : []),
    ]);
  }

  function instanceInnerObjects(block, collection) {
    const kind = collection === "nodes" ? "resident" : collection === "components" ? "component" : "wire";
    const ids = new Set(functionBlockElements(block).filter(element => element.kind === kind).map(element => element.id));
    return (collection === "nodes" ? state.lineage?.nodes || [] : state.lineage?.program?.[collection] || []).filter(item => ids.has(item.id));
  }

  function instanceManualSources(block) {
    return instanceInnerObjects(block, "components").filter(component =>
      component.kind === "event_source" && component.config?.event_type === "manual.triggered");
  }

  function functionBlockInstanceTriggersHtml(block, readonly, covered = new Set()) {
    const rows = instanceManualSources(block).filter(component => !covered.has(component.id)).map(component => window.PortalI18n.html`<div><dt>${esc(component.title || component.id)}</dt><dd><button class="portalButton" type="button" data-function-block-trigger="${esc(component.id)}"${readonly ? " disabled" : ""}>触发</button></dd></div>`);
    return rows.length ? window.PortalI18n.html`<section class="functionBlockInstanceBindings functionBlockInstanceTriggers"><span>手动触发</span><dl>${rows.join("")}</dl></section>` : "";
  }

  function expandedFunctionBlockIds() {
    try {
      const value = JSON.parse(localStorage.getItem(functionBlockPreferenceKey()) || "[]");
      return new Set(Array.isArray(value) ? value : []);
    } catch (_) { return new Set(); }
  }

  function functionBlockPreferenceKey() {
    return `portal.function-blocks.expanded.${state.lineage?.lineage_id || ""}`;
  }

  function setFunctionBlockExpanded(id, expanded) {
    const lineageId = state.lineage?.lineage_id;
    const ids = expandedFunctionBlockIds();
    if (expanded) ids.add(id); else ids.delete(id);
    try { localStorage.setItem(functionBlockPreferenceKey(), JSON.stringify([...ids])); } catch (_) { /* Local preferences may be unavailable. */ }
    renderLineageSurface();
    window.requestAnimationFrame(() => {
      if (state.lineage?.lineage_id !== lineageId || state.graphViewMode !== "context"
          || expandedFunctionBlockIds().has(id) !== expanded) return;
      state.graphController?.refresh?.({ measureNodes: true });
      if (expanded) {
        const boundary = graphVisibleModel().functionBlockBoundaries.find(block => block.id === id);
        if (!boundary) return;
        separateGraphObject(id).catch(() => {});
        // Fit visible members together with their enclosing block boundaries.
        state.graphController?.fit?.({ nodeIds: boundary.member_ids, duration: 220, maxScale: 1.15 });
      } else {
        state.graphController?.ensureNodeVisible?.(id);
      }
    });
  }

  // Folded outer blocks take precedence over nested blocks and Family facades.
  function functionBlockProjection(nodes, edges) {
    const blocks = state.lineage?.function_blocks || [];
    const expanded = expandedFunctionBlockIds();
    const standIn = new Map();
    const parent = new Map();
    for (const block of blocks) for (const element of block.elements || []) parent.set(element.id, block.id);
    for (const block of blocks) {
      if (block.family || expanded.has(block.id)) continue;
      for (const element of functionBlockElements(block)) standIn.set(element.id, block.id);
    }
    const representative = id => {
      const seen = new Set();
      let current = id, visible = id;
      while (parent.has(current) && !seen.has(current)) {
        seen.add(current);
        current = parent.get(current);
        const owner = functionBlockInstanceById(current);
        if (owner && !owner.family && !expanded.has(current)) visible = current;
      }
      return visible;
    };
    for (const id of standIn.keys()) standIn.set(id, representative(id));
    const visibleNodes = nodes.filter(node => !standIn.has(node.id)
      && !(isFunctionBlockInstance(node) && !node.family && expanded.has(node.id)));
    const merged = new Map();
    const visibleEdges = [];
    for (const edge of edges) {
      const source = standIn.get(edge.source) || edge.source;
      const target = standIn.get(edge.target) || edge.target;
      if (source === target && (source !== edge.source || target !== edge.target)) continue;
      if (source === edge.source && target === edge.target) { visibleEdges.push(edge); continue; }
      const key = `${source}\u0000${target}`;
      if (merged.has(key)) { merged.get(key).merged_edge_ids.push(edge.id); continue; }
      const projected = { ...edge, source, target, source_port: "", target_port: "",
        through_function_block: true, visual_only: true, merged_edge_ids: [edge.id] };
      merged.set(key, projected);
      visibleEdges.push(projected);
    }
    const visibleIds = new Set(visibleNodes.map(node => node.id));
    const boundaries = blocks.filter(block => !block.family && expanded.has(block.id) && !standIn.has(block.id))
      .map(block => ({ id: block.id, title: block.title, x: block.x, y: block.y,
        child_block_ids: (block.elements || []).filter(element => element.kind === "function_block" && expanded.has(element.id)).map(element => element.id),
        member_ids: [...new Set(functionBlockElements(block)
        .map(element => standIn.get(element.id) || element.id).filter(id => visibleIds.has(id)))] }));
    return { nodes: visibleNodes, edges: visibleEdges, boundaries };
  }

  function graphObjectTitle(objectId) {
    const block = functionBlockInstanceById(objectId);
    if (block) return block.title || objectId;
    const node = displayNodeById(objectId) || nodeById(objectId);
    if (node) return node.title || objectId;
    const component = programComponentById(objectId);
    return component ? (component.title || component.kind || objectId) : objectId;
  }



  // A Family (decision_44cf587c1721) shows as its facade: folded, the canvas has
  // the facade alone; expanded, it shows the steward, the members and the
  // Router Resident. Folding is each viewer's preference; Families start folded.
  function familyRoles(instance) {
    const family = instance?.family;
    const facade = String(family?.facade || "");
    if (!facade) return null;
    const steward = String(family.steward || "");
    const router = String(family.router || "");
    const members = Array.isArray(family.members) ? family.members.map(String) : [];
    return { facade, steward, router, members };
  }

  function familyInstances() {
    return (state.lineage?.function_blocks || [])
      .map(instance => ({ instance, roles: familyRoles(instance) }))
      .filter(family => family.roles);
  }

  function familyForFacade(residentId) {
    const id = String(residentId || "");
    return id ? familyInstances().find(family => family.roles.facade === id) || null : null;
  }

  function familyExpanded(roles) {
    return Boolean(roles && state.expandedFamilyFacadeIds.has(roles.facade));
  }

  function setFamilyExpanded(facadeId, expanded, { render = true, returnTo = null } = {}) {
    const id = String(facadeId || "");
    if (!id) return;
    if (expanded) state.expandedFamilyFacadeIds.add(id); else state.expandedFamilyFacadeIds.delete(id);
    scheduleHatgViewPreferenceSave(state.lineage?.lineage_id, {
      expanded_family_facade_ids: [...state.expandedFamilyFacadeIds],
    });
    if (!render) return;
    renderLineageSurface();
    window.requestAnimationFrame(() => {
      state.graphController?.refresh?.({ measureNodes: true });
      // Leaving a Family view returns to the canvas as it was, or to the whole canvas.
      if (returnTo === "whole") {
        state.graphController?.fit?.({ duration: 260, maxScale: 1.45 });
        return;
      }
      if (returnTo) {
        state.graphController?.setTransform?.(returnTo, { duration: 260 });
        return;
      }
      // Expanding brings the whole Family into view, wherever its members stand.
      const family = expanded && !state.graphFocusMode ? familyForFacade(id) : null;
      if (family) {
        state.graphController?.fit?.({ nodeIds: [...familyObjectIds(family)], duration: 260, maxScale: 1.15 });
      }
    });
  }

  // A Family's Router and steward live inside its pigeon: they are
  // never cards, and their relations end at the pigeon. A folded Family also
  // hides its pigeon and members behind the facade; an inner Family folds with the outer
  // one whatever its own state, pigeon and all. `standIn` names the visible
  // card that represents each hidden object on the canvas.
  function foldedFamilyObjects() {
    const families = familyInstances();
    const byFacade = new Map(families.map(family => [family.roles.facade, family]));
    const residents = new Set();
    const instances = new Set();
    const standIn = new Map();
    for (const family of families) {
      for (const element of functionBlockElements(family.instance)) {
        if (element.kind !== "component") continue;
        // Ordinary workflows are visible when expanded; roster machinery stays
        // behind the Family card. Folding either ancestor hides the workflow.
        if (String(element.key || "").startsWith("workflow:") && familyExpanded(family.roles)) continue;
        residents.add(element.id);
        standIn.set(element.id, family.instance.id);
      }
      for (const id of [family.roles.steward, family.roles.router]) {
        if (!id) continue;
        residents.add(id);
        standIn.set(id, family.instance.id);
      }
    }
    const hide = (family, facade) => {
      instances.add(family.instance.id);
      standIn.set(family.instance.id, facade);
      for (const id of family.roles.members) {
        if (!id || residents.has(id)) continue;
        residents.add(id);
        standIn.set(id, facade);
        const inner = byFacade.get(id);
        if (inner && inner !== family && !instances.has(inner.instance.id)) {
          hide(inner, facade);
        }
      }
    };
    for (const family of families) {
      if (!familyExpanded(family.roles)) hide(family, family.roles.facade);
    }
    for (const [id, target] of standIn) {
      let visible = target;
      for (let hops = 0; standIn.has(visible) && hops <= families.length * 2; hops += 1) visible = standIn.get(visible);
      standIn.set(id, visible);
    }
    return { residents, instances, standIn };
  }

  // Relations of a hidden Resident end at the facade that stands in for it, so
  // folding a Family never leaves its members' forks hanging; relations inside
  // one folded Family disappear with it. Program wires already end at the facade.
  function edgesThroughFoldedFamilies(edges, standIn) {
    if (!standIn.size) return edges;
    const shown = new Set();
    const key = (edge, source, target) => `${edge.type}\u0000${source}\u0000${target}`;
    const result = [];
    for (const edge of edges) {
      const source = standIn.get(edge.source) || edge.source;
      const target = standIn.get(edge.target) || edge.target;
      if (source === edge.source && target === edge.target) {
        shown.add(key(edge, edge.source, edge.target));
        result.push(edge);
      }
    }
    for (const edge of edges) {
      const source = standIn.get(edge.source) || edge.source;
      const target = standIn.get(edge.target) || edge.target;
      if (source === edge.source && target === edge.target) continue;
      if (source === target || shown.has(key(edge, source, target))) continue;
      shown.add(key(edge, source, target));
      result.push({ ...edge, source, target, source_port: "", target_port: "", through_family_facade: true, visual_only: true });
    }
    return result;
  }

  // A selected Family pigeon draws thin lines to its facade and the members on
  // the canvas; unselected, it draws none.
  function selectedFamilyPigeonId() {
    const selectedId = graphSelectedId();
    return familyInstances().some(item => item.instance.id === selectedId) ? selectedId : "";
  }

  function familyPigeonLinks() {
    const family = familyInstances().find(item => item.instance.id === selectedFamilyPigeonId());
    if (!family) return [];
    return [family.roles.facade, ...family.roles.members].filter(Boolean).map(target => ({
      id: `${family.instance.id}:family:${target}`,
      type: "familyLink",
      family: "context",
      source: family.instance.id,
      target,
      visual_only: true,
    }));
  }

  // Each Family has its own tone, shared by the facade and the cards it shows.
  const FAMILY_TONE_COUNT = 6;

  function familyTone(roles) {
    let hash = 0;
    for (const character of String(roles?.facade || "")) hash = (Math.imul(hash, 31) + character.codePointAt(0)) >>> 0;
    return hash % FAMILY_TONE_COUNT;
  }

  // Everything an expanded Family shows on the canvas, with expanded inner Families.
  function familyObjectIds(family, seen = new Set()) {
    const ids = new Set();
    if (!family || seen.has(family.instance.id)) return ids;
    seen.add(family.instance.id);
    const { instance, roles } = family;
    for (const id of [roles.facade, ...roles.members, instance.id]) if (id) ids.add(id);
    for (const element of instance.elements || []) {
      if (element.kind === "component" && String(element.key || "").startsWith("workflow:")) ids.add(element.id);
    }
    for (const member of roles.members) {
      const inner = familyForFacade(member);
      if (inner && familyExpanded(inner.roles)) for (const id of familyObjectIds(inner, seen)) ids.add(id);
    }
    return ids;
  }

  // Cards shown by an expanded Family carry its tone; its Residents also name it.
  function familyMembership(hidden) {
    const membership = new Map();
    for (const family of familyInstances()) {
      const { instance, roles } = family;
      if (!familyExpanded(roles) || hidden.has(roles.facade)) continue;
      const mark = { tone: familyTone(roles), title: String(instance.title || "Family"), facade: roles.facade };
      for (const id of roles.members) if (id) membership.set(id, { ...mark, role: "member" });
      for (const element of instance.elements || []) {
        if (element.kind === "component" && String(element.key || "").startsWith("workflow:")) {
          membership.set(element.id, { ...mark, role: "inner" });
        }
      }
      membership.set(instance.id, { ...mark, role: "inner" });
    }
    return membership;
  }

  // 邻域 on a facade shows the Family alone, where its cards stand.
  function focusedFamily() {
    if (!state.graphFocusMode || state.graphViewMode !== "context") return null;
    const family = familyForFacade(state.graphFocusFamilyFacadeId);
    return family && familyExpanded(family.roles) ? family : null;
  }

  // Selecting an expanded Family's facade brings the Family forward; the rest of
  // the canvas fades but stays usable.
  function familySpotlight() {
    if (state.graphViewMode !== "context" || state.graphFocusMode) return null;
    const family = familyForFacade(graphSelectedId());
    return family && familyExpanded(family.roles) ? family : null;
  }

  // A Resident made where an expanded Family is shown joins that Family: on one
  // of its cards, or anywhere in its own view. A folded Family's facade makes one
  // outside.
  function familyForNewResident(nodeId) {
    const id = String(nodeId || "");
    const own = familyForFacade(id);
    if (own && familyExpanded(own.roles)) return own;
    const holder = familyInstances().find(family => family.roles.members.includes(id) || family.roles.steward === id);
    if (holder && familyExpanded(holder.roles)) return holder;
    return focusedFamily();
  }

  // A facade's "+" shows its Family alone, as 邻域 on the facade does; "−"
  // folds it back into the facade and returns to the canvas as it was before "+".
  function toggleFamilyFacade(node) {
    const family = familyForFacade(node?.id);
    if (!family) return false;
    toggleFamilyView(family);
    return true;
  }

  function toggleFamilyView(family) {
    if (!familyExpanded(family.roles)) return showFamilyAlone(family);
    let returnTo = null;
    if (focusedFamily()?.roles.facade === family.roles.facade) {
      returnTo = familyViewReturn(family) || "whole";
      state.graphFocusMode = false;
      state.graphFocusFamilyFacadeId = "";
    }
    state.familyViewReturn = null;
    setFamilyExpanded(family.roles.facade, false, { returnTo });
  }

  // The view the canvas had before this Family's view, while that view is still
  // the Lineage on screen.
  function familyViewReturn(family) {
    const saved = state.familyViewReturn;
    return saved
      && saved.facadeId === family.roles.facade
      && saved.lineageId === String(state.lineage?.lineage_id || "")
      ? saved.transform
      : null;
  }

  // A Family's local view uses a compact projection, without moving the saved
  // objects. “全图” restores their actual positions with the Family still open.
  function showFamilyAlone(family) {
    if (state.graphViewMode !== "context") return setFamilyExpanded(family.roles.facade, true);
    // A deep link may name a Family hidden by a folded ancestor.
    for (const ancestor of familyInstances()) {
      if (functionBlockElements(ancestor.instance).some(element => element.id === family.instance.id)) {
        setFamilyExpanded(ancestor.roles.facade, true, { render: false });
      }
    }
    const transform = focusedFamily() ? null : state.graphController?.getTransform?.();
    state.familyViewReturn = transform ? {
      facadeId: family.roles.facade,
      lineageId: String(state.lineage?.lineage_id || ""),
      transform,
    } : null;
    state.graphFocusMode = true;
    state.graphFocusFamilyFacadeId = family.roles.facade;
    setFamilyExpanded(family.roles.facade, true, { render: false });
    renderLineageSurface();
    window.requestAnimationFrame(() => {
      state.graphController?.refresh?.({ measureNodes: true });
      state.graphController?.fit?.({ duration: 220, maxScale: 1, padding: 40 });
    });
  }

  function revealUnfamilizedMember(lineageId, result) {
    const retained = new Set(result?.member_resident_ids || []);
    if (!retained.size) return;
    const controller = state.graphController;
    window.requestAnimationFrame(() => {
      if (state.lineage?.lineage_id !== lineageId || state.graphController !== controller
        || state.graphViewMode !== "context") return;
      const node = graphVisibleModel().nodes.find(item => retained.has(nodeResidentId(item)));
      if (!node) return;
      // The removed facade may have been the only card in view. Reveal a
      // surviving member (including a nested facade), without selecting it or
      // moving the stored graph. A card already in view keeps the camera.
      controller?.refresh?.({ measureNodes: true });
      controller?.ensureNodeVisible?.(node.id, { persist: true });
    });
  }

  async function removeFamilyInstance(instanceId, action = "unfamilize") {
    const instance = functionBlockInstanceById(instanceId);
    if (!instance) return;
    const lineageId = state.lineage.lineage_id;
    const response = await window.PortalNav.removeFamily({
      family: instance, lineageId, action,
      execute: (instruction, args) => applyProgramMutation(
        () => executeHatgInstruction(instruction, args),
        action === "delete" ? "Family 及其内部对象已删除。" : "Family 已解除。",
      ),
    });
    if (!response) return;
    const roles = familyRoles(instance);
    if (roles) setFamilyExpanded(roles.facade, false);
    state.selectedProgramObjectId = "";
    programFeature.clearSelection();
    if (action === "unfamilize") revealUnfamilizedMember(lineageId, response);
    // The removed facade's Chat reports it deleted; the Leader is not opened
    // in its place.
    return response;
  }

  async function openGraphWorkspace(node) {
    if (node?.kind !== "workspace" || !node.workspace_id) return;
    state.selectedWorkspaceId = node.id;
    state.selectedEdgeId = "";
    renderGraph();
    try {
      if (typeof hatgComponentOptions.onWorkspaceOpen !== "function") {
        throw new Error("Navigation 工作区入口尚未就绪");
      }
      await hatgComponentOptions.onWorkspaceOpen(node.workspace_id);
    } catch (error) {
      toast(window.PortalI18n.text`工作区打开失败：${error.message || error}`, "error");
    }
  }

  let workspaceMenuOpenSequence = 0;
  async function openGraphWorkspaceMenu(node) {
    if (node?.kind !== "workspace" || !node.workspace_id) return;
    const lineageId = state.lineage?.lineage_id;
    let bindingSequence = state.workspaceGraphLoadSeq;
    const openSequence = ++workspaceMenuOpenSequence;
    const isCurrent = () => state.lineage?.lineage_id === lineageId
      && state.workspaceGraphLoadSeq === bindingSequence;
    state.selectedWorkspaceId = node.id;
    state.selectedEdgeId = "";
    renderGraph();
    try {
      await Promise.all([
        window.PortalUIPrimitives.loadAsset("/static/portal_resident_menu.css"),
        window.PortalUIPrimitives.loadAsset("/static/portal_workspace_menu.css"),
        window.PortalUIPrimitives.loadAsset("/static/portal_detail_surface.js", {ready: () => window.PortalDetailSurface}),
        window.PortalUIPrimitives.loadAsset("/static/portal_workspace_menu.js", {ready: () => window.PortalWorkspaceMenu}),
      ]);
      if (!isCurrent() || openSequence !== workspaceMenuOpenSequence) return;
      await window.PortalWorkspaceMenu.open({
        workspaceId: node.workspace_id,
        members: (node.member_ids || []).map(id => ({id, title: displayNodeById(id)?.title || id})),
        onOpen: hatgComponentOptions.onWorkspaceOpen,
        onChanged: async () => {
          if (!isCurrent()) return;
          for (const [residentId, binding] of state.workspaceGraphBindings) {
            if (binding.roots?.some(root => root.workspace_id === node.workspace_id)) {
              state.workspaceGraphBindings.delete(residentId);
            }
          }
          bindingSequence = ++state.workspaceGraphLoadSeq;
          const result = await loadWorkspaceGraphBindings(state.lineage, {fit: false});
          if (!result.complete && !result.stale) throw new Error(window.PortalI18n.t("Workspace 绑定读取失败，请重新打开视图。"));
        },
      });
    } catch (error) {
      if (isCurrent()) toast(window.PortalI18n.text`Workspace 菜单打开失败：${error.message || error}`, "error");
    }
  }

  function decorateGraphEdge(edge) {
    if (edge._program_wire) {
      return edge;
    }
    if (edge.type === "handoff") {
      const statusLabel = ({
        draft: edge.draft_preview ? window.PortalI18n.t("待配置 Handoff") : window.PortalI18n.t("Handoff 草稿"),
        preparing: window.PortalI18n.t("正在准备 Workspace"),
        ready: edge.execution?.status === "queued" ? window.PortalI18n.t("Workspace 就绪 · Agent 已启动") : window.PortalI18n.t("Workspace 已就绪"),
        failed: window.PortalI18n.t("Handoff 失败"),
      })[edge.status] || edge.label || window.PortalI18n.t("Workspace Handoff");
      return {
        ...edge,
        family: "workspace",
        handoff_status: edge.status || "draft",
        display_label: statusLabel,
      };
    }
    if (edge.type === "absorption") {
      return {
        ...edge,
        family: "context",
        absorption_status: edge.absorption?.status || "queued",
        display_label: edge.absorption?.status === "failed" ? "吸收失败" : (edge.label || "节点吸收"),
      };
    }
    return { ...edge, family: relationFamily(edge) };
  }

  function workspaceGraphModel() {
    const projection = window.PortalHatgWorkspaceProjection.project(
      state.lineage?.nodes || [], (state.lineage?.edges || []).map(decorateGraphEdge), state.workspaceGraphBindings,
    );
    const positions = window.PortalHatgGraph.computeWorkspaceGraphLayout(projection.nodes, projection.edges);
    const savedPositions = hatgViewPreferences.get(state.lineage?.lineage_id)?.workspace_positions || {};
    const overviewNodes = projection.nodes.map(node => ({
      ...node, ...(savedPositions[node.id] || positions.get(node.id)), navigation_position: true,
    }));
    const overviewEdges = projection.edges;
    const selectedId = overviewNodes.some(node => node.id === state.selectedWorkspaceId)
      ? state.selectedWorkspaceId : "";
    const visibleIds = new Set(state.graphFocusMode && selectedId
      ? [selectedId] : overviewNodes.map(node => node.id));
    if (state.graphFocusMode && selectedId) overviewEdges.forEach(edge => {
      if (edge.source === selectedId) visibleIds.add(edge.target);
      if (edge.target === selectedId) visibleIds.add(edge.source);
    });
    return {
      overviewNodes, overviewEdges, selectedId,
      functionBlockBoundaries: [], familySpotlightFacadeId: "",
      nodes: overviewNodes.filter(node => visibleIds.has(node.id)),
      edges: overviewEdges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)
        && (state.graphRelationFilter === "all" || edge.type === state.graphRelationFilter)),
    };
  }

  function graphVisibleModel() {
    if (state.graphViewMode === "workspace") return workspaceGraphModel();
    const display = state.lineage;
    const mode = state.graphViewMode;
    const shownNodes = display?.nodes || [];
    const projectionKey = display && mode === "context"
      ? [
          display.lineage_id || "",
          Number(display.revision || 0),
          Number(display.layout_revision || 0),
          Number(state.localVersion || 0),
          "latest",
          [...state.expandedFamilyFacadeIds].sort().join(","),
          (display.function_blocks || []).length,
          (display.nodes || []).length,
          (display.program?.components || []).length,
          (display.program?.wires || []).length,
        ].join("\u0000")
      : "";
    const folded = mode === "context"
      ? foldedFamilyObjects()
      : { residents: new Set(), instances: new Set(), standIn: new Map() };
    const programProjection = (
      mode === "context"
      && display
      && window.PortalLineageProgram?.projectGraph
    )
      ? (
          // The projection copies the displayed nodes. Every commit installs
          // a new Lineage object, so a cache entry is reused only for the
          // object it was built from, whatever changed inside the commit.
          graphProgramProjectionCache?.lineage === display
          && graphProgramProjectionCache.key === projectionKey
            ? graphProgramProjectionCache.projection
            : (() => {
                const program = displayedProgram();
                const projection = window.PortalLineageProgram.projectGraph(
                  {
                    program,
                    edges: display.edges || [],
                    function_blocks: display.function_blocks || [],
                  },
                  shownNodes,
                );
                graphProgramProjectionCache = { lineage: display, key: projectionKey, projection };
                return projection;
              })()
        )
      : {
          nodes: shownNodes,
          components: [],
          edges: [],
        };
    const eventWireRuntime = display?.program_invoke_wire_runtime || {};
    const programEdges = (programProjection.edges || []).map(edge => {
      const explanation = programExecutionExplanation(edge.id);
      const runtime = ["route"].includes(edge.type)
        ? (eventWireRuntime[edge.id] || {})
        : {};
      const pendingEventCount = Math.max(
        0,
        Math.trunc(Number(runtime.pending_count) || 0),
      );
      const baseLabel = edge.label
        || window.PortalLineageProgram?.manifest?.wire_types?.[edge.type]?.title
        || edge.type;
      return {
        ...edge,
        kind: edge.type,
        family: "context",
        _program_wire: true,
        pending_event_count: pendingEventCount,
        event_unavailable_policy: String(
          runtime.unavailable_policy
          || edge.config?.unavailable_policy
          || "drop",
        ),
        event_queue_limit: Number(
          runtime.queue_limit || edge.config?.queue_limit || 10,
        ),
        event_next_expires_at: String(runtime.next_expires_at || ""),
        execution_summary: String(explanation?.summary || ""),
        execution_next_action: String(explanation?.next_action || ""),
        execution_state: String(explanation?.state || ""),
        display_label: pendingEventCount
          ? `${baseLabel} · ${pendingEventCount} 待处理`
          : baseLabel,
      };
    });
    // The Program projection holds every Seed; the canvas shows the active
    // ones once, each with its projected ports and its own display fields.
    const projectedSeeds = new Map(
      (programProjection.nodes || []).filter(node => node.kind === "seed").map(node => [node.id, node]),
    );
    const seeds = mode === "context"
      ? activeSeeds(display).map(seed => ({ ...(projectedSeeds.get(seed.id) || {}), ...seed, kind: "seed", status: "active" }))
      : [];
    const leaderNodeId = effectiveLeaderNode(display)?.id || "";
    const existingHandoff = (display?.edges || []).some(edge => (
      edge.type === "handoff"
      && edge.source === state.handoffSourceId
      && edge.target === state.handoffTargetId
    ));
    const draftHandoff = (
      state.handoffMode
      && state.handoffSourceId
      && state.handoffTargetId
      && !existingHandoff
    ) ? [{
      id: "handoff_draft_preview",
      source: state.handoffSourceId,
      target: state.handoffTargetId,
      type: "handoff",
      label: window.PortalI18n.t("待配置 Handoff"),
      status: "draft",
      draft_preview: true,
    }] : [];
    const persistedDecoratedEdges = [
      ...(display?.edges || []),
      ...programEdges,
      ...draftHandoff,
    ].map(decorateGraphEdge);
    const decoratedEdges = persistedDecoratedEdges;
    let overviewNodes;
    let overviewEdges;
    const selectedId = graphSelectedId();
    const membership = mode === "context" ? familyMembership(folded.residents) : new Map();
    overviewNodes = [...(programProjection.nodes || []).filter(node => node.kind !== "seed"), ...seeds].filter(node => !folded.residents.has(node.id) && !folded.instances.has(node.id)).map(node => {
      const family = familyForFacade(node.id);
      const member = membership.get(node.id);
      return {
        ...node,
        _is_leader: node.id === leaderNodeId,
        ...(family ? {
          _family_facade: true,
          _family_expanded: familyExpanded(family.roles),
          _family_tone: familyTone(family.roles),
        } : {}),
        ...(member ? { _family_member: member } : {}),
      };
    });
    overviewEdges = [
      ...edgesThroughFoldedFamilies(
        decoratedEdges.filter(edge => relationVisibleInView(edge, mode)),
        folded.standIn,
      ),
      ...familyPigeonLinks(),
    ];

    const blockProjection = mode === "context" ? functionBlockProjection(overviewNodes, overviewEdges) : null;
    if (blockProjection) { overviewNodes = blockProjection.nodes; overviewEdges = blockProjection.edges; }
    let visibleIds = new Set(overviewNodes.map(node => node.id));
    const familyInFocus = mode === "context" ? focusedFamily() : null;
    if (familyInFocus) {
      // Include the Family's workflow and nested visible members.
      visibleIds = familyObjectIds(familyInFocus);
    } else if (state.graphFocusMode && selectedId) {
        visibleIds = new Set([selectedId]);
        overviewEdges.forEach(edge => {
          if (edge.source === selectedId) visibleIds.add(edge.target);
          if (edge.target === selectedId) visibleIds.add(edge.source);
        });
    }
    let nodes = overviewNodes.filter(node => visibleIds.has(node.id));
    if (state.graphFocusMode && selectedId && mode !== "workspace" && !familyInFocus) {
      const neighbors = nodes.filter(node => node.id !== selectedId);
      const positions = new Map([[selectedId, { x: 50, y: 50 }]]);
      neighbors.forEach((node, index) => {
        const angle = Math.PI + (Math.PI * 2 * index / Math.max(1, neighbors.length));
        positions.set(node.id, {
          x: 50 + Math.cos(angle) * 32,
          y: 50 + Math.sin(angle) * 30,
        });
      });
      nodes = nodes.map(node => ({ ...node, ...positions.get(node.id), navigation_position: true }));
    }
    const visibleEndpointIds = visibleIds;
    let edges = overviewEdges.filter(edge => (
      visibleEndpointIds.has(edge.source)
      && visibleEndpointIds.has(edge.target)
      && (
        state.graphRelationFilter === "all"
        || (
          state.graphRelationFilter === "program"
            ? Boolean(edge._program_wire)
            : edge.type === state.graphRelationFilter
        )
      )
    ));
    if (familyInFocus) {
      const positions = window.PortalHatgGraph.computeBranchAwareLayout(nodes, edges, {
        ...readableAutoLayoutOptions(nodes, state.autoLayoutDensityByScope.get(autoLayoutScope())),
        maxRowsPerLane: 5,
        feedbackEdgeTypes: ["watch", "target"],
        leaderId: familyInFocus.roles.facade,
        rootIds: [familyInFocus.roles.facade],
      });
      nodes = nodes.map(node => ({ ...node, ...positions.get(node.id), navigation_position: true }));
    }
    const spotlight = mode === "context" ? familySpotlight() : null;
    if (spotlight) {
      const inFamily = familyObjectIds(spotlight);
      nodes = nodes.map(node => (inFamily.has(node.id) ? node : { ...node, _family_dimmed: true }));
      edges = edges.map(edge => (
        inFamily.has(edge.source) && inFamily.has(edge.target) ? edge : { ...edge, _family_dimmed: true }
      ));
    }
    return {
      nodes,
      edges,
      familySpotlightFacadeId: spotlight?.roles.facade || "",
      functionBlockBoundaries: blockProjection?.boundaries || [],
      overviewNodes,
      overviewEdges,
      selectedId,
    };
  }

  function renderGraphNavigator(model = graphVisibleModel()) {
    renderAutoLayoutControl();
    const hasLineage = !!state.lineage;
    const focus = $id("graphFocusModeBtn");
    const filter = $id("graphRelationFilter");
    const search = $id("graphNodeSearchInput");
    const selectedFacade = state.graphViewMode === "context" && Boolean(familyForFacade(graphSelectedId()));
    focus.disabled = !hasLineage || (!state.graphFocusMode && !model.selectedId);
    focus.classList.toggle("active", state.graphFocusMode);
    focus.setAttribute("aria-pressed", state.graphFocusMode ? "true" : "false");
    focus.textContent = state.graphFocusMode ? window.PortalI18n.t("全图") : window.PortalI18n.t("邻域");
    focus.title = state.graphFocusMode
      ? window.PortalI18n.t("退出邻域聚焦，恢复全部节点")
      : selectedFacade
        ? window.PortalI18n.t("只显示这个 Family：门面、steward、成员与 Router，各自保持原位置")
        : window.PortalI18n.t("只显示选中节点与一跳邻居");
    const filterOptions = state.graphViewMode === "workspace"
      ? [["all", "全部 Workspace 关系"], ["workspace_binding", "Resident 绑定"], ["handoff", "Workspace Handoff"]]
      : [["all", "全部 Context 关系"], ["program", "HATG Program"], ["inheritance", "Fork 继承"], ["handoff", "Workspace Handoff"], ["absorption", "节点吸收"]];
    const optionSignature = filterOptions.map(item => item[0]).join(",");
    if (filter.dataset.options !== optionSignature) {
      filter.innerHTML = filterOptions.map(([value, label]) => window.PortalI18n.html`<option value="${esc(value)}">${esc(window.PortalI18n.t(label))}</option>`).join("");
      filter.dataset.options = optionSignature;
    }
    filter.hidden = false;
    filter.disabled = !hasLineage;
    filter.value = state.graphRelationFilter;
    search.disabled = !hasLineage;
    $id("graphDeepRerankBtn").hidden = state.graphViewMode === "workspace";
    $id("collaborationGraphLegend").hidden = (
      state.graphViewMode !== "context"
      || state.graphRelationFilter === "program"
    );
    const programLegend = $id("programGraphLegend");
    if (programLegend) {
      programLegend.hidden = (
        state.graphViewMode !== "context"
        || state.graphRelationFilter !== "program"
      );
    }
    $id("workspaceGraphLegend").hidden = state.graphViewMode !== "workspace";
    $id("graphVisibilitySummary").textContent = `${model.nodes.length} / ${model.overviewNodes.length}`;
  }

  function renderProgramWireHint() {
    const hint = $id("programWireHint");
    const text = $id("programWireHintText");
    const eyebrow = $id("programWireHintEyebrow");
    const cancel = $id("cancelProgramWireBtn");
    if (!hint || !text) return;
    const draft = state.programWireDraft;
    const selected = displayGraphItemById(graphSelectedId());
    const selectedPorts = selected && state.graphViewMode === "context"
      ? window.PortalLineageProgram?.portsForNode?.(selected) || []
      : [];
    const ready = Boolean(
      !draft

      && state.selectedProgramObjectId
      && !displayProgramWireById(state.selectedProgramObjectId)
      && selectedPorts.some(port => port.direction === "out" && !port.anchor_only),
    );
    hint.hidden = !draft && !ready;
    if (!draft) {
      delete hint.dataset.wireType;
      hint.dataset.mode = ready ? "ready" : "";
      if (eyebrow) eyebrow.textContent = ready ? window.PortalI18n.t("端口已显示") : "";
      text.textContent = ready
        ? window.PortalI18n.t("点击这个节点的任意输出端口开始连接；可连接的目标随后会高亮")
        : "";
      if (cancel) cancel.hidden = true;
      return;
    }
    hint.dataset.mode = "connecting";
    hint.dataset.wireType = draft.type;
    if (eyebrow) eyebrow.textContent = window.PortalI18n.t("正在连接");
    if (cancel) cancel.hidden = false;
    const title = window.PortalLineageProgram?.manifest?.wire_types?.[draft.type]?.title
      || draft.type;
    text.textContent = draft.binding === "watch"
      ? window.PortalI18n.t("观察对象：请选择高亮的卡片或关系；Esc 或点击空白处取消")
      : window.PortalI18n.text`${title}：请选择高亮的卡片或入口；Esc 或点击空白处取消`;
  }

  function cancelProgramWireDraft(options = {}) {
    if (!state.programWireDraft) return false;
    programFeature.setWireDraft(null);
    renderGraph();
    renderProgramWireHint();
    if (options.notify) toast(window.PortalI18n.t("已取消原语连线。"), "warning");
    return true;
  }

  async function saveOpenProgramComponentDraftBeforeWire() {
    const panel = $id("programInspectorPanel");
    const form = panel?.querySelector(
      "[data-program-component-form], [data-program-operator-form]",
    );
    if (!form || form.dataset.programDraftDirty !== "true") return "unchanged";
    if (form.dataset.programDraftConflict === "true") {
      toast(window.PortalI18n.t("草稿尚未保存，请核对后点击保存，再继续连线。"), "warning");
      return "blocked";
    }
    const componentId = String(panel?.dataset.componentId || "");
    if (!componentId || !programComponentById(componentId)) return "unchanged";
    const previousRevision = Number(state.lineage?.revision || 0);
    await saveProgramComponentFromInspector(componentId, form);
    const saved = Number(state.lineage?.revision || 0) > previousRevision;
    if (!saved && form.isConnected) {
      form.dataset.programDraftDirty = "true";
    }
    return saved ? "saved" : "blocked";
  }

  function programMutationBusyNotice() {
    toast(window.PortalI18n.t("上一项程序图修改仍在保存；保存完成后再连接端口。"), "warning", 2400);
  }

  async function handleProgramPortClick(port) {
    if (!state.lineage) return;
    if (state.programMutationInFlight) {
      programMutationBusyNotice();
      return;
    }
    let normalized = window.PortalLineageProgram.normalizePort(port);
    if (!normalized?.node_id || (!normalized.port_id && normalized.type !== "object")) return;
    const draftSaveStatus = await saveOpenProgramComponentDraftBeforeWire();
    if (draftSaveStatus === "blocked") return;
    if (!state.lineage) return;
    if (state.programMutationInFlight) {
      programMutationBusyNotice();
      return;
    }
    if (draftSaveStatus === "saved") {
      const refreshedNode = displayGraphItemById(normalized.node_id);
      const refreshedPort = (
        window.PortalLineageProgram?.portsForNode?.(refreshedNode) || []
      ).find(candidate => (
        String(candidate?.port_id || "")
          === normalized.port_id
        && String(candidate?.direction || "") === normalized.direction
      ));
      if (!refreshedPort) {
        renderGraph();
        renderProgramWireHint();
        toast(window.PortalI18n.t("原语配置已保存，端口随配置发生变化；请从新端口重新连线。"), "warning", 4200);
        return;
      }
      normalized = window.PortalLineageProgram.normalizePort(refreshedPort);
    }
    if (!state.programWireDraft) {
      if (normalized.direction !== "out") {
        toast(window.PortalI18n.t("请先选择一个输出端口开始连线。"), "warning");
        return;
      }
      state.programWireDraft = normalized;
      renderGraph();
      renderProgramWireHint();
      return;
    }
    if (
      normalized.node_id === state.programWireDraft.node_id
      && normalized.port_id === state.programWireDraft.port_id
      && normalized.direction === state.programWireDraft.direction
    ) {
      cancelProgramWireDraft();
      return;
    }
    const validation = window.PortalLineageProgram?.validateConnection?.(
      state.programWireDraft,
      normalized,
      {
        existingWires: currentProgram().wires,
        nodes: [...(state.lineage?.nodes || []), ...currentProgram().components],
      },
    );
    if (!validation?.ok) {
      toast(
        validation?.reason === "effect_endpoint_occupied"
              ? window.PortalI18n.t("这个 Effect 端点已经绑定了对象。")
            : validation?.reason === "packet_operator_cycle"
              ? window.PortalI18n.t("Packet Operator 的 Packet 路由不能形成循环。")
          : window.PortalI18n.t("这个端口与当前连线类型不兼容；连线仍保持选择。"),
        "warning",
        3000,
      );
      return;
    }
    // Built when the queued edit runs: wires connected in quick succession each
    // carry the revision their predecessor produced, not the one seen at click time.
    const draft = state.programWireDraft;
    const request = () => window.PortalLineageProgram.buildWireSaveRequest(
      state.lineage,
      draft,
      normalized,
      {
        existingWires: currentProgram().wires,
        nodes: [...(state.lineage?.nodes || []), ...currentProgram().components],
      },
    );
    try {
      await applyProgramMutation(
        () => societyService.saveProgramWire(request()),
        `${validation.type} 连线已建立。`,
      );
      programFeature.setWireDraft(null);
      renderGraph();
      renderProgramWireHint();
    } catch {
      renderGraph();
      renderProgramWireHint();
    }
  }

  async function createProgramComponentFromDrop(kind, position) {
    if (
      !state.lineage

      || !window.PortalLineageProgram?.componentKinds?.includes(
        String(kind || ""),
      )
    ) return;
    const response = await applyProgramMutation(
      () => societyService.createProgramComponent(
        window.PortalLineageProgram.buildComponentCreateRequest(
          state.lineage,
          kind,
          position,
          { placement: state.graphController.getPlacementGeometry({
            kind,
            primitive_kind: kind,
            title: window.PortalI18n.text`新 ${window.PortalLineageProgram.manifest.component_kinds[kind].title}`,
            config: {},
          }) },
        ),
      ),
      `${window.PortalLineageProgram?.manifest?.component_kinds?.[kind]?.title || "原语"} 已放入 HATG。`,
    );
    if (!response?.component_id) return;
    await new Promise(resolve => window.requestAnimationFrame(resolve));
    if (state.lineage?.lineage_id === response.lineage.lineage_id) {
      state.graphController?.ensureNodeVisible?.(response.component_id);
    }
  }

  async function selectProgramComponent(component) {
    if (!component?.id) return;
    if (isPacketOperatorComponent(component)) {
      const config = component.config || {};
      const executorId = String(config.resident_executor_id || "");
      if (
        config.operator_implementation === "resident"
        && executorId
        && displayNodeById(executorId)
      ) {
        await selectNode(executorId, { force: true });
      } else {
        clearNodeSelection();
      }
    }
    await selectProgramObject(component.id);
  }

  function renderGraph() {
    const empty = $id("graphEmpty");
    const display = state.lineage;
    const model = graphVisibleModel();
    reconcileCanvasSelection(model);
    const nodes = model.nodes;
    const edges = model.edges;
    state.renderedFamilySpotlight = model.familySpotlightFacadeId || "";
    state.renderedFamilyPigeon = selectedFamilyPigeonId();
    const sourceAgentId = state.handoffSourceId;
    const projectedSourceId = sourceAgentId;
    empty.hidden = !!nodes.length || !display;
    if (!display) empty.hidden = true;
    const canvasSelectionActive = (
      state.graphViewMode === "context"
      && state.selectedCanvasObjectIds.size > 0
    );
    state.graphController?.render({
      nodes,
      edges,
      functionBlockBoundaries: model.functionBlockBoundaries,
      selectedNodeId: canvasSelectionActive ? "" : model.selectedId,
      selectedNodeIds: canvasSelectionActive
        ? [...state.selectedCanvasObjectIds]
        : [],
      selectableNodeIds: state.graphViewMode === "context"
        ? nodes.filter(canvasNodeCanBeSelected).map(node => node.id)
        : [],
      selectionEnabled: state.graphViewMode === "context",
      selectedEdgeId: graphSelectedEdgeId(),
      handoffSourceId: projectedSourceId,
      handoffAgentId: sourceAgentId,
      handoffMode: state.handoffMode,
      viewMode: state.graphViewMode,
      layoutInteractive: ["context", "workspace"].includes(state.graphViewMode),
      // Workspace coordinates belong to personal preferences, never Context layout.
      navigationFocus: state.graphFocusMode,
      quickForkNodeId: state.quickForkNodeId,
      overviewNodes: model.overviewNodes,
      overviewEdges: model.overviewEdges,
      wireDraft: state.programWireDraft,
    });
    renderGraphNavigator(model);
    renderRelationInspector();
    renderProgramWireHint();
    if (graphNodeSearchPopover?.isOpen()) renderGraphNodeSearchResults();
  }

  function renderGraphRuntimePresentation() {
    const model = graphVisibleModel();
    if (!state.graphController?.patchRuntimePresentation?.(model)) renderGraph();
  }

  function renderGraphSelection() {
    if (state.graphFocusMode) return false;
    // Selecting or leaving an expanded Family's facade fades the rest of the canvas.
    if ((familySpotlight()?.roles.facade || "") !== state.renderedFamilySpotlight) return false;
    // So does selecting or leaving a Family pigeon, which draws its lines.
    if (selectedFamilyPigeonId() !== state.renderedFamilyPigeon) return false;
    const model = graphVisibleModel();
    const canvasSelectionActive = (
      state.graphViewMode === "context"
      && state.selectedCanvasObjectIds.size > 0
    );
    const patched = state.graphController?.patchSelection?.({
      selectedNodeId: canvasSelectionActive ? "" : model.selectedId,
      selectedNodeIds: canvasSelectionActive
        ? [...state.selectedCanvasObjectIds]
        : [],
      selectedEdgeId: graphSelectedEdgeId(),
    });
    if (!patched) return false;
    renderGraphNavigator(model);
    renderRelationInspector();
    renderProgramWireHint();
    return true;
  }

  function syncWorkspaceNodePosition(node, position = node) {
    const key = state.lineage?.lineage_id;
    if (!key || !["workspace", "workspace_resident"].includes(node?.kind)
      || !Number.isFinite(position?.x) || !Number.isFinite(position?.y)) return false;
    const preference = hatgViewPreferences.get(key) || normalizeHatgViewPreference({});
    hatgViewPreferences.set(key, {
      ...preference,
      workspace_positions: {
        ...preference.workspace_positions, [node.id]: { x: position.x, y: position.y },
      },
    });
    return true;
  }

  function syncGraphNodePosition(node, position = node) {
    if (!node?.id) return false;
    const target = graphItemById(node.id);
    const x = Number(position?.x);
    const y = Number(position?.y);
    if (!target || !Number.isFinite(x) || !Number.isFinite(y)) return false;
    target.x = x;
    target.y = y;
    // The position changes inside the committed Lineage object, so the canvas
    // projection would keep its copied coordinates without a new local version.
    state.localVersion += 1;
    return true;
  }

  function separateGraphObject(id) {
    return separateGraphObjects([id]);
  }

  function separateGraphObjects(objectIds) {
    if (state.graphViewMode !== "context" || state.graphFocusMode) return Promise.resolve(null);
    const moved = new Map();
    for (const id of new Set(objectIds)) {
      const offset = state.graphController?.getObjectSeparation?.(id);
      if (!offset) continue;
      const block = functionBlockInstanceById(id);
      const ids = new Set([id, ...(block ? functionBlockElements(block).map(element => element.id) : [])]);
      for (const objectId of ids) {
        const item = graphItemById(objectId);
        if (!item || !Number.isFinite(item.x) || !Number.isFinite(item.y)) continue;
        item.x += offset.x;
        item.y += offset.y;
        moved.set(objectId, item);
      }
      // Measure the next object against this placement before saving the batch.
      // The offsets edit the committed Lineage in place, so the canvas needs a
      // new local version to project them.
      state.localVersion += 1;
      renderGraph();
    }
    return moved.size ? saveLineageLayout([...moved.values()]) : Promise.resolve(null);
  }

  function initHatgGraph() {
    const factory = window.PortalHatgGraph?.createGraph;
    if (!factory) throw new Error("HATG 图形引擎未加载");
    state.graphController?.destroy?.();
    state.graphController = factory({
      root: $id("hatgCanvas"),
      world: $id("hatgWorld"),
      edgeLayer: $id("hatgEdgeLayer"),
      effectLayer: $id("hatgEffectLayer"),
      labelLayer: $id("hatgEdgeLabels"),
      nodeLayer: $id("hatgNodes"),
      minimap: $id("hatgMinimap"),
      worldWidth: HATG_WORLD_SIZE.width,
      worldHeight: HATG_WORLD_SIZE.height,
      zoomValue: $id("graphZoomValue"),
      semanticZoomRoot: $id("graphSemanticZoom"),
      nodeDoubleClickDelay: 260,
      zoomInButton: $id("graphZoomInBtn"),
      zoomOutButton: $id("graphZoomOutBtn"),
      resetButton: $id("graphZoomResetBtn"),
      fitButton: $id("graphFitBtn"),
      fitOptions: FIT_ALL_VIEW,
      scaleExtent: [0.05, 3.2],
      readViewport: lineageId => hatgViewPreferences.get(String(lineageId || ""))?.viewport || null,
      onViewportChange: (lineageId, viewport) => {
        scheduleHatgViewPreferenceSave(lineageId, { viewport });
      },
      viewportInsets: () => {
        // The docked Inspector already shrinks hatgCanvas in CSS. Measure
        // actual occlusion of the graph root, not the enclosing viewport.
        const viewport = $id("hatgCanvas")?.getBoundingClientRect();
        const occludingBounds = element => {
          if (!viewport || !element || element.hidden || !element.getClientRects().length) return null;
          const bounds = element.getBoundingClientRect();
          return bounds.left < viewport.right && bounds.right > viewport.left
            && bounds.top < viewport.bottom && bounds.bottom > viewport.top ? bounds : null;
        };
        const inspectorBounds = occludingBounds($id("relationInspectorPanel"));
        const programInspectorBounds = occludingBounds($id("programInspectorPanel"));
        const navigationBounds = occludingBounds($id("graphNavigationBar"));
        const hintBounds = occludingBounds($id("programWireHint"));
        const inspectors = [inspectorBounds, programInspectorBounds].filter(Boolean);
        const spansWidth = bounds => bounds.left <= viewport.left + 1 && bounds.right >= viewport.right - 1;
        return {
          top: Math.max(0, ...[navigationBounds, hintBounds].filter(Boolean).map(bounds => bounds.bottom - viewport.top + 10)),
          right: Math.max(0, ...inspectors.filter(bounds => !spansWidth(bounds)).map(bounds => viewport.right - bounds.left + 10)),
          bottom: Math.max(0, ...inspectors.filter(spansWidth).map(bounds => viewport.bottom - bounds.top + 10)),
        };
      },
      statusForNode: effectiveNodeStatus,
      contextPressureForNode: nodeContextPressure,
      nodeContent: graphNodeContent,
      nodeTooltip: node => {
        if (!isFunctionBlockInstance(node)) return "";
        const title = node.title || window.PortalI18n.t("Function Block");
        return node.family ? title
          : window.PortalI18n.text`${title} · ${functionBlockSourceTitle(node)} · ${(node.elements || []).length} 个元素`;
      },
      nodeDisclosure: node => {
        if (isFunctionBlockInstance(node) && !node.family) return {
          kind: "function_block", expanded: false, label: window.PortalI18n.t("展开"), title: window.PortalI18n.t("展开"),
        };
        const family = familyForFacade(node.id);
        if (family) {
          const expanded = familyExpanded(family.roles);
          const count = family.roles.members.length;
          return {
            kind: "family",
            expanded,
            action: {label: "Unfamilize", iconName: "close", title: "Unfamilize"},
            label: expanded
              ? window.PortalI18n.text`折叠 Family「${node.title || node.id}」，只显示门面`
              : window.PortalI18n.text`只显示 Family「${node.title || node.id}」：门面、${count} 位成员与 Family 模块`,
            title: expanded ? window.PortalI18n.t("折叠为门面") : window.PortalI18n.text`只显示这个 Family（${count} 位成员）`,
          };
        }
        return null;
      },
      nodeControl: node => node.accessible === false ? null : hatgNodeControl(node),
      nodeHoverActions: node => node.accessible === false ? [] : hatgNodeHoverActions(node),
      nodeDisabled: node => node.accessible === false,
      nodeOpenMenu: node => isFunctionBlockInstance(node)
        ? {
          label: window.PortalI18n.text`打开 ${node.title || node.id} 菜单`,
          title: window.PortalI18n.t("配置、展开或解除 Function Block"),
        }
        : residentEditorOpenMenu(node),
      portsForNode: node => (
        node.accessible !== false && state.graphViewMode === "context"
          ? window.PortalLineageProgram?.portsForNode?.(node) || []
          : []
      ),
      onPortClick: port => handleProgramPortClick(port).catch(() => {}),
      connectionTargets: (source, item) => window.PortalLineageProgram.connectionTargets(source, item),
      onWireDraftCancel: () => {
        cancelProgramWireDraft();
      },
      isPortCompatible: (source, target) => Boolean(
        window.PortalLineageProgram?.validateConnection?.(
          source,
          target,
          { existingWires: currentProgram().wires, nodes: [...(state.lineage?.nodes || []), ...currentProgram().components] },
        )?.ok
      ),
      onPrimitiveDrop: (kind, position) => {
        createProgramComponentFromDrop(kind, position).catch(() => {});
      },
      onNodeControl: (node, action, event) => {
        if (action === "emit-manual-event") {
          emitManualProgramEvent(node.id, event?.currentTarget || null).catch(() => {});
          return;
        }
        if (action === "trigger-function-block") {
          openFunctionBlockMenu(node.id);
          return;
        }
        if (action === "reset-event-source") {
          resetProgramEventSource(node.id, event?.currentTarget || null).catch(() => {});
          return;
        }
        if (action === "stop") stopLineageNode(node).catch(() => {});
      },
      onNodeDisclosure: node => {
        if (isFunctionBlockInstance(node) && !node.family) { setFunctionBlockExpanded(node.id, true); return; }
        if (toggleFamilyFacade(node)) return;
      },
      onNodeDisclosureAction: node => {
        const family = familyForFacade(node.id);
        if (family) removeFamilyInstance(family.instance.id).catch(() => {});
      },
      onFunctionBlockCollapse: id => setFunctionBlockExpanded(id, false),
      onFunctionBlockMenu: id => openFunctionBlockMenu(id),
      onNodeHoverAction: (node, action) => {
        if (isFunctionBlockInstance(node) && ["open-router", "open-steward"].includes(action)) {
          const roles = familyRoles(node);
          const resident = displayNodeById(roles?.[action.slice(5)]);
          if (resident) notifyResidentSelection(resident);
          return;
        }
        if (action !== "stop") return;
        stopLineageNode(node).catch(() => {});
      },
      onNodeOpenMenu: (node, _menu, event, trigger) => {
        if (isFunctionBlockInstance(node)) openFunctionBlockMenu(node.id);
        else notifyResidentOpenMenu(node, event, trigger);
      },
      isEdgeFlowing: edge => ["target", "watch"].includes(edge.type)
        ? false
        : ["queued", "running"].includes(effectiveNodeStatus(displayNodeById(edge.source_resident_id || edge.source)))
          || ["queued", "running"].includes(effectiveNodeStatus(displayNodeById(edge.target_resident_id || edge.target))),
      onCandidateForkDrop: (source, seedId) => {
        setSeedCandidateFork(seedId, source.id).catch(() => {});
      },
      onWorkspaceClick: node => openGraphWorkspace(node),
      onWorkspaceDoubleClick: node => openGraphWorkspaceMenu(node),
      onEdgeClick: (edge, event) => {
        if (edge.visual_only || edge.type === "workspace_binding") return;
        if (edge._program_wire && (event?.shiftKey || event?.metaKey || event?.ctrlKey)) {
          toggleCanvasObjectSelection(edge.id);
          renderGraph();
          return;
        }
        clearCanvasSelection({ render: false });
        if (edge._program_wire) {
          selectProgramObject(edge.id);
          return;
        }
        selectRelationEdge(edge.interaction_edge_id || edge.id);
      },
      onNodeClick: (node, event) => {
        if (state.graphViewMode === "workspace") return;
        const modifierSelection = Boolean(
          state.graphViewMode === "context"

          && (event?.shiftKey || event?.metaKey || event?.ctrlKey),
        );
        if (modifierSelection && canvasNodeCanBeSelected(node)) {
          toggleCanvasObjectSelection(node.id);
          return;
        }
        const selectionWasActive = state.selectedCanvasObjectIds.size > 0;
        clearCanvasSelection({ render: false });
        state.canvasSelectionAnchorId = (
          state.graphViewMode === "context" && canvasNodeCanBeSelected(node)
        ) ? node.id : "";
        // A Seed opens its card (candidate, adopt, dismiss); its settings open
        // from the card or on double-click.
        if (node.kind === "seed") selectSeed(node.id);
        else if (isProgramComponent(node)) {
          selectProgramComponent(node);
        }
        else if (isFunctionBlockInstance(node)) {
          selectProgramObject(node.id);
          openFunctionBlockMenu(node.id);
        }
        else if (state.handoffMode) handleHandoffNodeClick(node.id);
        else selectNode(node.id, { force: selectionWasActive, openChat: true });
      },
      onBoxSelect: nodes => {
        addCanvasObjectsToSelection(nodes.map(node => node.id));
      },
      onSelectionCancel: () => {
        clearCanvasSelection();
      },
      onBackgroundClick: () => {
        if (state.graphViewMode === "workspace") {
          state.selectedWorkspaceId = "";
          if (state.selectedEdgeId) closeRelationInspector({ restoreFocus: false });
          renderGraph();
          return;
        }
        if (state.selectedCanvasObjectIds.size) {
          clearCanvasSelection();
        } else if (state.selectedProgramObjectId) {
          state.selectedProgramObjectId = "";
          if (!renderGraphSelection()) renderGraph();
          renderControls();
          renderProgramInspector();
        } else if (state.selectedEdgeId) closeRelationInspector({ restoreFocus: false });
        else dismissSeedSelection();
      },
      onNodeDoubleClick: node => {
        if (state.graphViewMode === "workspace") return;
        if (node.accessible === false) return;
        if (isFunctionBlockInstance(node)) {
          openFunctionBlockMenu(node.id);
          return;
        }
        if (isProgramComponent(node)) {
          selectProgramComponent(node);
          return;
        }
        if (node.kind === "seed") {
          openSeedPromoteModal(node.id, "implement");
          return;
        }
        openNodeSettingsModal(node);
      },
      nodeQuickForkModes: () => Array.from(
        $id("branchModeChoices").querySelectorAll('input[name="branchMode"]'),
        input => ({
          value: input.value,
          label: input.closest("label").querySelector("strong").textContent,
          description: input.closest("label").querySelector("small").textContent,
        }),
      ),
      onNodeQuickFork: (node, _event, mode) => {
        createQuickFork(node, mode).catch(() => {});
      },
      onNodeMove: (node, position) => {
        if (state.graphViewMode === "workspace") {
          syncWorkspaceNodePosition(node, position);
          return;
        }
        syncGraphNodePosition(node, position);
        if (node?.id === state.selectedSeedId) positionSeedInspector();
      },
      onNodeMoveEnd: node => {
        if (state.graphViewMode === "workspace") {
          if (syncWorkspaceNodePosition(node)) scheduleHatgViewPreferenceSave(state.lineage.lineage_id);
          return;
        }
        if (!syncGraphNodePosition(node)) return;
        saveLineageLayout([node]).catch(() => {});
      },
      onNodesMoveEnd: nodes => {
        if (state.graphViewMode === "workspace") return;
        const movedItems = nodes
          .map(node => graphItemById(node.id))
          .filter(Boolean);
        if (!movedItems.length) return;
        saveLineageLayout(movedItems).catch(() => {});
      },
      canNodeAbsorb: (source, target) => (
        state.graphViewMode === "context"
        && !state.absorptionInFlight
        && source?.kind !== "seed"
        && target?.kind !== "seed"
        && !isProgramComponent(source)
        && !isProgramComponent(target)
        && !isFunctionBlockInstance(source)
        && !isFunctionBlockInstance(target)
        && source?.id !== target?.id
      ),
      onNodeAbsorbDrop: (source, target) => {
        absorbNodeIntoTarget(source.id, target.id);
      },
    });
    $id("hatgWorld").dataset.hatgEngine = state.graphController.engine;
  }

  function localGraphSearchMatches(query) {
    const normalized = String(query || "").trim().toLocaleLowerCase();
    if (!normalized) return [];
    if (state.graphViewMode === "workspace") {
      return workspaceGraphModel().overviewNodes.filter(node => (
        [node.title, node.workspace_path, node.workspace_id, node.resident_id]
          .some(value => String(value || "").toLocaleLowerCase().includes(normalized))
      )).slice(0, 8);
    }
    const matches = [];
    const addMatches = (nodes, kind) => {
      for (const node of nodes) {
        const values = [
          node.title,
          node.display_title,
          node.summary,
          node.artifact_path,
          nodeResidentId(node),
        ];
        const signature = values.map(value => String(value || "")).join("\u0000");
        const cached = graphSearchTextCache.get(node);
        const text = cached?.signature === signature
          ? cached.text
          : signature.toLocaleLowerCase();
        if (cached?.signature !== signature) graphSearchTextCache.set(node, { signature, text });
        if (!text.includes(normalized)) continue;
        matches.push({ ...node, kind });
        if (matches.length >= 8) return true;
      }
      return false;
    };
    if (addMatches(state.lineage?.nodes || [], "agent")) return matches;
    addMatches(activeSeeds(), "seed");
    return matches;
  }

  function graphSearchIdentityMatch(query) {
    const normalized = String(query || "").trim().toLocaleLowerCase();
    if (!normalized.startsWith("resident_")) return null;
    const candidates = (state.lineage?.nodes || []).map(node => ({
      node,
      residentId: nodeResidentId(node).toLocaleLowerCase(),
    })).filter(candidate => candidate.residentId);
    const exact = candidates.find(candidate => candidate.residentId === normalized);
    if (exact) return { ...exact.node, kind: "agent" };
    const prefixMatches = candidates.filter(candidate => candidate.residentId.startsWith(normalized));
    return prefixMatches.length === 1
      ? { ...prefixMatches[0].node, kind: "agent" }
      : null;
  }

  function graphSearchMatches(query) {
    const local = localGraphSearchMatches(query);
    if (state.graphViewMode === "workspace") return local;
    const identity = graphSearchIdentityMatch(query);
    if (String(graphSmartSearch.query || "") !== String(query || "").trim()) {
      return identity ? [identity, ...local.filter(node => node.id !== identity.id)] : local;
    }
    const nodesByResidentId = new Map(
      (state.lineage?.nodes || []).map(node => [nodeResidentId(node), node]),
    );
    const semantic = (graphSmartSearch.results || []).map(result => {
      const residentId = String(result.resident_id || "");
      const node = nodesByResidentId.get(residentId);
      const sources = Array.isArray(result.retrieval_sources) ? result.retrieval_sources : [];
      const matchKind = sources.includes("vector") ? "向量匹配" : sources.includes("lexical") ? "文字匹配" : "相关匹配";
      const focus = result.reason || result.summary?.current_focus || "";
      return node ? { ...node, kind: "agent", smartReason: focus ? `${matchKind} · ${focus}` : matchKind } : null;
    }).filter(Boolean);
    const semanticReady = !graphSmartSearch.loading && semantic.length > 0;
    const ordered = [
      ...(identity ? [identity] : []),
      ...(semanticReady ? semantic : []),
      ...local,
    ];
    const seen = new Set();
    return ordered.filter(node => {
      if (seen.has(node.id)) return false;
      seen.add(node.id);
      return true;
    }).slice(0, 8);
  }

  function graphSearchResidentIds() {
    return [...new Set((state.lineage?.nodes || []).map(nodeResidentId).filter(Boolean))];
  }

  function setGraphDeepRerankBusy(busy) {
    const button = $id("graphDeepRerankBtn");
    if (!button) return;
    button.disabled = Boolean(busy);
    button.setAttribute("aria-busy", busy ? "true" : "false");
    button.textContent = busy ? window.PortalI18n.t("深度中…") : window.PortalI18n.t("深度");
  }

  async function runGraphSmartSearch(query, { deep = false } = {}) {
    if (state.graphViewMode === "workspace") {
      graphSmartSearch = { query: "", loading: false, mode: "", results: [], error: "" };
      renderGraphNodeSearchResults();
      return;
    }
    const clean = String(query || "").trim();
    if (clean.length < 2) {
      graphSmartSearch = { query: clean, loading: false, mode: "", results: [], error: "" };
      setGraphDeepRerankBusy(false);
      renderGraphNodeSearchResults();
      return;
    }
    const requestId = ++graphSmartSearchRequest;
    const mode = deep ? "deep" : "search";
    graphSmartSearch = { query: clean, loading: true, mode, results: [], error: "" };
    setGraphDeepRerankBusy(deep);
    renderGraphNodeSearchResults();
    let autoFocusNodeId = "";
    const residentIds = graphSearchResidentIds();
    if (!residentIds.length) {
      graphSmartSearch = { query: clean, loading: false, mode, results: [], error: "" };
      setGraphDeepRerankBusy(false);
      renderGraphNodeSearchResults();
      return;
    }
    try {
      const result = deep
        ? await residentDiscovery.recommend({
            query: clean,
            limit: 8,
            resident_ids: residentIds,
            allow_fallback: false,
          })
        : await residentDiscovery.search({
            query: clean,
            limit: 8,
            resident_ids: residentIds,
          });
      if (requestId !== graphSmartSearchRequest) return;
      if (!result) return;
      graphSmartSearch = {
        query: clean,
        loading: false,
        mode,
        results: deep ? result.recommendations || [] : result.results || [],
        error: "",
      };
      if (deep) autoFocusNodeId = graphSearchMatches(clean)[0]?.id || "";
      if (deep && result.fallback) toast(window.PortalI18n.t("深度判断不可用，已保留常规搜索排序。"), "warning");
    } catch (error) {
      if (requestId !== graphSmartSearchRequest) return;
      graphSmartSearch = { query: clean, loading: false, mode, results: [], error: error.message || String(error) };
    } finally {
      if (requestId === graphSmartSearchRequest) setGraphDeepRerankBusy(false);
    }
    renderGraphNodeSearchResults();
    if (autoFocusNodeId) await focusGraphNode(autoFocusNodeId);
  }

  function scheduleGraphSmartSearch(query) {
    window.clearTimeout(graphSmartSearchTimer);
    const clean = String(query || "").trim();
    ++graphSmartSearchRequest;
    if (clean.length < 2) {
      graphSmartSearch = { query: clean, loading: false, mode: "", results: [], error: "" };
      setGraphDeepRerankBusy(false);
      return;
    }
    graphSmartSearch = { query: clean, loading: true, mode: "search", results: [], error: "" };
    graphSmartSearchTimer = window.setTimeout(() => runGraphSmartSearch(clean), 320);
  }

  function runGraphDeepRerank() {
    window.clearTimeout(graphSmartSearchTimer);
    const query = $id("graphNodeSearchInput").value;
    if (String(query || "").trim().length < 2) {
      toast(window.PortalI18n.t("先输入至少两个字，再进行深度重排。"), "warning");
      return;
    }
    runGraphSmartSearch(query, { deep: true });
  }

  function renderGraphNodeSearchResults() {
    // The popover owns these elements even while its panel is mounted in body.
    // Querying by ID would cross runtime boundaries when several HATGs are open.
    const input = graphNodeSearchPopover?.trigger;
    const root = graphNodeSearchPopover?.element;
    if (!input || !root || !panelActive) return;
    const matches = graphSearchMatches(input.value);
    if (!input.value.trim()) {
      graphNodeSearchPopover?.close();
      root.innerHTML = "";
      return;
    }
    const loadingText = graphSmartSearch.mode === "deep"
      ? "正在深度重排当前 Lineage 节点…"
      : "正在搜索当前 Lineage 节点…";
    const content = matches.length
      ? `${matches.map(node => window.PortalI18n.html`<button type="button" data-graph-search-node="${esc(node.id)}"><strong>${esc(node.display_title || node.title)}</strong><small>${esc(node.kind === "workspace" ? `${node.workspace_id} · ${node.workspace_path}` : node.kind === "seed" ? window.PortalI18n.text`种子 · ${node.artifact_path}` : node.smartReason || nodeResidentId(node))}</small></button>`).join("")}${graphSmartSearch.loading ? window.PortalI18n.html`<div class="graphSearchEmpty">${loadingText}</div>` : ""}`
      : window.PortalI18n.html`<div class="graphSearchEmpty">${graphSmartSearch.loading ? loadingText : graphSmartSearch.error ? window.PortalI18n.t("智能搜索暂时不可用") : window.PortalI18n.t("没有匹配节点")}</div>`;
    root.innerHTML = window.PortalI18n.html`<div class="graphNodeSearchResultList">${content}</div>`;
    graphNodeSearchPopover.open();
  }

  function scheduleGraphNodeSearchResultsRender() {
    if (graphNodeSearchRenderFrame) return;
    graphNodeSearchRenderFrame = window.requestAnimationFrame(() => {
      graphNodeSearchRenderFrame = 0;
      renderGraphNodeSearchResults();
    });
  }

  function handleGraphNodeSearchInput(event) {
    scheduleGraphSmartSearch(event.currentTarget.value);
    scheduleGraphNodeSearchResultsRender();
  }

  function cancelGraphNodeSearch() {
    window.clearTimeout(graphSmartSearchTimer);
    if (graphNodeSearchRenderFrame) window.cancelAnimationFrame(graphNodeSearchRenderFrame);
    graphNodeSearchRenderFrame = 0;
    ++graphSmartSearchRequest;
    graphSmartSearch.loading = false;
    setGraphDeepRerankBusy(false);
  }

  function clearGraphNodeSearch() {
    cancelGraphNodeSearch();
    graphSmartSearch = { query: "", loading: false, mode: "", results: [], error: "" };
    const input = graphNodeSearchPopover?.trigger;
    const results = graphNodeSearchPopover?.element;
    if (input) input.value = "";
    if (results) results.innerHTML = "";
    graphNodeSearchPopover?.close();
  }

  async function revealSelectedGraphNode(nodeId, options = {}) {
    const navigationSequence = state.graphController?.getNavigationSequence?.();
    await new Promise(resolve => window.requestAnimationFrame(resolve));
    if (graphSelectedId() !== nodeId) return false;
    if (navigationSequence !== state.graphController?.getNavigationSequence?.()) return false;
    state.graphController?.focusNode?.(nodeId, {
      scale: Number(options.scale || 1.25),
      duration: Number(options.duration ?? 240),
      compositor: true,
    });
    return true;
  }

  async function focusGraphNode(nodeId) {
    if (state.graphViewMode === "workspace") {
      const node = workspaceGraphModel().overviewNodes.find(item => item.id === nodeId);
      if (!node) return;
      clearGraphNodeSearch();
      state.graphFocusMode = false;
      if (node.kind === "workspace") await openGraphWorkspace(node);
      else renderGraph();
      state.graphController?.focusNode?.(node.id, { scale: 1.25, duration: 240 });
      return;
    }
    const node = displayGraphItemById(nodeId);
    if (!node) return;
    clearGraphNodeSearch();
    if (node.kind === "seed" || displaySeedById(node.id)) {
      await selectSeed(node.id);
      renderGraph();
      await revealSelectedGraphNode(node.id);
    } else {
      await selectNode(node.id, { force: true, revealInGraph: true });
    }
  }

  async function focusLineageLeader() {
    const leaderId = String(state.lineage?.leader_resident_id || "").trim();
    if (!leaderId || !displayNodeById(leaderId)) {
      renderControls();
      toast(window.PortalI18n.t("当前 Lineage 没有可跳转的 Leader。"), "warning");
      return;
    }
    if (state.graphViewMode !== "context") await setGraphViewMode("context");
    await focusGraphNode(leaderId);
  }

  function toggleGraphFocusMode() {
    const selectedId = state.graphViewMode === "workspace" ? state.selectedWorkspaceId : graphSelectedId();
    if (!state.graphFocusMode && !selectedId) return toast(window.PortalI18n.t("先选择一个节点，再聚焦它的一跳邻域。"), "warning");
    // 邻域 on a facade shows its Family alone; a folded Family opens for it.
    const family = !state.graphFocusMode && state.graphViewMode === "context"
      ? familyForFacade(graphSelectedId())
      : null;
    if (family) return showFamilyAlone(family);
    state.graphFocusMode = !state.graphFocusMode;
    state.graphFocusFamilyFacadeId = "";
    renderGraph();
    window.requestAnimationFrame(() => {
      state.graphController?.fit?.({ duration: 220, maxScale: state.graphFocusMode ? 1.15 : 1.45 });
    });
  }

  function changeGraphRelationFilter() {
    state.graphRelationFilter = $id("graphRelationFilter").value || "all";
    renderGraph();
  }

  function setWorkspaceViewLoading(loading) {
    state.workspaceViewLoading = Boolean(loading);
    const status = $id("workspaceViewLoading");
    if (status) status.hidden = !state.workspaceViewLoading;
    const tab = $id("graphWorkspaceViewBtn");
    if (tab) {
      tab.classList.toggle("loading", state.workspaceViewLoading);
      tab.setAttribute("aria-busy", state.workspaceViewLoading ? "true" : "false");
    }
  }

  async function setGraphViewMode(mode) {
    const next = ["context", "workspace"].includes(mode) ? mode : "context";
    if (next !== "context" && state.programWireDraft) cancelProgramWireDraft();
    if (next === "workspace" && state.workspaceViewLoading) return;
    const requestSequence = ++state.graphViewRequestSeq;
    if (next !== "workspace") setWorkspaceViewLoading(false);
    if (state.graphViewMode === next) return;
    if (next === "workspace" && state.lineage) {
      const lineageId = state.lineage.lineage_id;
      const needsBindings = (state.lineage.nodes || [])
        .some(node => !state.workspaceGraphBindings.has(node.id));
      if (needsBindings) {
        setWorkspaceViewLoading(true);
        let bindingResult;
        try {
          bindingResult = await loadWorkspaceGraphBindings(state.lineage, { render: false, fit: false });
        } catch (error) {
          if (requestSequence !== state.graphViewRequestSeq) return;
          setWorkspaceViewLoading(false);
          toast(window.PortalI18n.text`Workspace 视图加载失败：${error.message || error}`, "error", 5600);
          return;
        }
        if (
          requestSequence !== state.graphViewRequestSeq
          || state.lineage?.lineage_id !== lineageId
        ) return;
        setWorkspaceViewLoading(false);
        if (!bindingResult?.complete) {
          const failedCount = Number(bindingResult?.failedCount || 0);
          toast(
            failedCount
              ? window.PortalI18n.text`Workspace 视图暂未切换：${failedCount} 个节点的 Binding 读取失败。`
              : window.PortalI18n.t("Workspace 视图暂未切换：节点 Binding 尚未完整到齐。"),
            "error",
            5600,
          );
          return;
        }
      }
    }
    if (requestSequence !== state.graphViewRequestSeq) return;
    const seedSource = next !== "context" && state.selectedSeedId
      ? displaySeedById(state.selectedSeedId)?.source_resident_id
      : "";
    clearCanvasSelection({ render: false });
    state.graphViewMode = next;
    state.graphFocusMode = false;
    clearGraphNodeSearch();
    state.graphRelationFilter = "all";
    if (next !== "context" && state.handoffMode) cancelHandoffMode();
    const selectedEdge = displayEdgeById(state.selectedEdgeId);
    const clearedCrossViewEdge = Boolean(selectedEdge && !relationVisibleInView(selectedEdge, next));
    if (clearedCrossViewEdge) state.selectedEdgeId = "";
    if (seedSource) {
      state.selectedSeedId = "";
      state.selectedNodeId = seedSource;
      selectNode(seedSource, { force: true }).catch(() => {});
    }
    renderLineageSurface();
    window.requestAnimationFrame(() => state.graphController?.fit?.({ duration: 220, maxScale: 1.35 }));
  }

  function bindComponentLibraryResize() {
    disposeLibraryResize?.();
    const handle = $id("componentLibrarySplitter");
    const rail = handle.parentElement;
    const primitives = $id("hatgPrimitiveLibrary");
    const savedRatio = pageStateController.read().hatgLibrarySplitRatio;
    let ratio = typeof savedRatio === "number" && Number.isFinite(savedRatio) && savedRatio > 0 && savedRatio < 1
      ? savedRatio : 0.5;
    const availableWidth = () => Math.max(0, rail.getBoundingClientRect().width - handle.getBoundingClientRect().width);
    const minimum = () => Math.min(116, availableWidth() / 2);
    const maximum = () => availableWidth() - minimum();
    const readSize = () => primitives.getBoundingClientRect().width;
    const valueText = value => {
      const percent = Math.round(100 * value / (availableWidth() || 1));
      return window.PortalI18n.text`原语 ${percent}%，功能 ${100 - percent}%`;
    };
    const syncAria = () => {
      handle.setAttribute("aria-valuemin", String(Math.round(minimum())));
      handle.setAttribute("aria-valuemax", String(Math.round(maximum())));
      handle.setAttribute("aria-valuenow", String(Math.round(readSize())));
      handle.setAttribute("aria-valuetext", valueText(readSize()));
    };
    const applySplitRatio = () => {
      rail.style.setProperty("--hatg-library-primitives", `${ratio}fr`);
      rail.style.setProperty("--hatg-library-functions", `${1 - ratio}fr`);
      syncAria();
    };
    const persist = () => pageStateController.write({ hatgLibrarySplitRatio: ratio });
    applySplitRatio();
    const unbind = window.PortalWorkbench.layout.bindSplitter({
      handle,
      axis: "x",
      bodyClass: "",
      disabled: () => availableWidth() <= 0,
      readSize,
      min: minimum,
      max: maximum,
      valueText,
      applySize: size => {
        ratio = size / availableWidth();
        applySplitRatio();
      },
      onDone: persist,
    });
    pageStateController.listen("panel", handle, "dblclick", event => {
      event.preventDefault();
      ratio = 0.5;
      applySplitRatio();
      persist();
    });
    // CSS keeps both groups reachable at narrow widths without overwriting the
    // user's preferred ratio; only the separator's ARIA values need refreshing.
    const observer = new ResizeObserver(syncAria);
    observer.observe(rail);
    disposeLibraryResize = () => {
      unbind();
      observer.disconnect();
      handle.classList.remove("active");
    };
  }

  function bindPrimitiveLibrary() {
    $queryAll("[data-hatg-library-kind]").forEach(card => {
      if (card.dataset.programBound === "true") return;
      card.dataset.programBound = "true";
      const kind = String(card.dataset.hatgLibraryKind || "");
      card.setAttribute("draggable", "true");
      card.addEventListener("dragstart", event => {
        if (!state.lineage || !event.dataTransfer) {
          event.preventDefault();
          return;
        }
        event.dataTransfer.effectAllowed = "copy";
        if (window.PortalLineageProgram?.componentKinds?.includes(kind)) {
          event.dataTransfer.setData(window.PortalCompatibility.identifier("primitive_transfer"), kind);
        }
        card.classList.add("dragging");
      });
      card.addEventListener("dragend", () => card.classList.remove("dragging"));
      card.addEventListener("click", () => {
        if (!state.lineage) return;
        if (window.PortalLineageProgram?.componentKinds?.includes(kind)) {
          createProgramComponentFromDrop(kind, nextNodePosition()).catch(() => {});
        }
      });
    });
    $id("cancelProgramWireBtn")?.addEventListener("click", () => {
      cancelProgramWireDraft();
    });
    $eventRoot().addEventListener("keydown", event => {
      if (event.key === "Escape" && state.programWireDraft) {
        event.preventDefault();
        cancelProgramWireDraft();
      }
    });
  }

  function renderLineageSurface() {
    reconcileGraphInteractionState();
    renderLineageHeader();
    renderGraph();
    renderProgramInspector();
    templateController?.render();
    renderControls();
    renderSeedInspector();
  }

  function runtimeStatusFromResidentProjection(projection, node) {
    const residentState = window.PortalResidentProtocol.stateView(projection?.resident_state);
    if (residentState.status !== "idle") return residentState.status;
    return ["done", "blocked"].includes(node?.status) ? node.status : "idle";
  }

  function applyResidentStateProjections(states, options = {}) {
    const previousWorkPhases = new Map((state.lineage?.nodes || []).map(node => [node.id, nodeWorkPresentation(node).phase]));
    const previousGates = new Map((state.lineage?.nodes || []).map(node => [node.id, nodeExecutionGate(node).text]));
    const previousIntakeStates = new Map(
      (state.lineage?.nodes || []).map(node => [node.id, nodeIntakeState(node)])
    );
    const previousStatuses = new Map(
      (state.lineage?.nodes || []).map(node => [node.id, effectiveNodeStatus(node)])
    );
    if (options.replace === true) state.residentRuntimeById = new Map();
    for (const [residentId, residentState] of Object.entries(states || {})) {
      const cleanResidentId = String(residentId || "").trim();
      if (!cleanResidentId) continue;
      const normalizedState = window.PortalResidentProtocol.stateView(residentState);
      state.residentRuntimeById.set(cleanResidentId, {
        resident_id: cleanResidentId,
        intake_state: normalizedState.intake_state,
        resident_state: normalizedState,
      });
    }
    if (!state.lineage?.nodes?.length) return false;
    let displayChanged = false;
    for (const node of state.lineage.nodes) {
      const projection = state.residentRuntimeById.get(nodeResidentId(node));
      if (projection) {
        setNodeRuntimeStatus(node.id, runtimeStatusFromResidentProjection(projection, node));
      } else if (options.replace === true) {
        state.runtimeStatus.delete(node.id);
      }
      const previousStatus = previousStatuses.get(node.id) || "idle";
      const nextStatus = effectiveNodeStatus(node);
      if (previousStatus !== nextStatus) {
        displayChanged = true;
      }
      if (previousWorkPhases.get(node.id) !== nodeWorkPresentation(node).phase
        || previousIntakeStates.get(node.id) !== nodeIntakeState(node)
        || previousGates.get(node.id) !== nodeExecutionGate(node).text) {
        displayChanged = true;
      }
    }
    if (!displayChanged) return false;
    if (options.render !== false) {
      updateLineageSummary();
      renderGraph();
      renderControls();
    }
    return true;
  }

  function hasNewLineageGraphProjection(current, incoming) {
    const previousRevision = Number(current.revision || 0);
    const nextRevision = Number(incoming.revision || 0);
    // A queried snapshot can complete a read projection at the same authored
    // revision. Its server fingerprint also participates in invalidation.
    return nextRevision > previousRevision || (
      nextRevision === previousRevision
      && Boolean(incoming.refresh_signature)
      && incoming.refresh_signature !== current.refresh_signature
    );
  }

  function lineagePayloadsEqual(left, right) {
    if (left === right) return true;
    try {
      return JSON.stringify(left) === JSON.stringify(right);
    } catch (_error) {
      return false;
    }
  }

  const HANDOFF_EXECUTION_RUNTIME_FIELDS = [
    "status",
    "message_id",
    "error",
    "requested_at",
    "delivered_at",
  ];

  function lineageItemsById(items = []) {
    return new Map(
      (items || [])
        .filter(item => item && String(item.id || ""))
        .map(item => [String(item.id), item]),
    );
  }

  function overlayLineageLayout(target, source) {
    const overlayPositions = (targetItems, sourceItems) => {
      const sourceById = lineageItemsById(sourceItems);
      (targetItems || []).forEach(item => {
        const incoming = sourceById.get(String(item?.id || ""));
        if (!incoming) return;
        if (Number.isFinite(Number(incoming.x))) item.x = Number(incoming.x);
        if (Number.isFinite(Number(incoming.y))) item.y = Number(incoming.y);
      });
    };
    overlayPositions(target.nodes, source.nodes);
    overlayPositions(target.function_blocks, source.function_blocks);
    overlayPositions(
      target.program?.components,
      source.program?.components,
    );
    target.layout_revision = Number(source.layout_revision || 0);
    target.layout_updated_at = String(source.layout_updated_at || "");
    return target;
  }

  function overlayHandoffRuntime(target, source) {
    ["status", "error", "ready_at"].forEach(field => {
      if (Object.hasOwn(source, field)) target[field] = clone(source[field]);
    });
    const targetExecution = target.execution && typeof target.execution === "object"
      ? target.execution
      : {};
    const sourceExecution = source.execution && typeof source.execution === "object"
      ? source.execution
      : {};
    HANDOFF_EXECUTION_RUNTIME_FIELDS.forEach(field => {
      if (Object.hasOwn(sourceExecution, field)) {
        targetExecution[field] = clone(sourceExecution[field]);
      }
    });
    target.execution = targetExecution;
    const sourceItems = new Map(
      (source.items || [])
        .filter(item => item && String(item.item_id || ""))
        .map(item => [String(item.item_id), item]),
    );
    (target.items || []).forEach(item => {
      const incoming = sourceItems.get(String(item?.item_id || ""));
      if (!incoming) return;
      item.attempts = clone(incoming.attempts || []);
      item.result = clone(incoming.result || {});
      if (incoming.result && Object.keys(incoming.result).length) {
        item.target = item.target && typeof item.target === "object"
          ? item.target
          : {};
        ["workspace_id", "path", "scope"].forEach(field => {
          if (Object.hasOwn(incoming.target || {}, field)) {
            item.target[field] = clone(incoming.target[field]);
          }
        });
      }
    });
  }

  function overlayLineageRuntime(target, source) {
    const sourceNodes = lineageItemsById(source.nodes);
    (target.nodes || []).forEach(node => {
      const incoming = sourceNodes.get(String(node?.id || ""));
      if (!incoming) return;
      node.status = incoming.status;
      node.last_execution = clone(incoming.last_execution || {});
    });

    const targetResidentIds = new Set(
      (target.nodes || []).map(node => String(node?.id || "")),
    );
    const sourceAbsorptions = (source.edges || []).filter(edge => (
      edge?.type === "absorption"
      && targetResidentIds.has(String(edge.source || ""))
      && targetResidentIds.has(String(edge.target || ""))
    ));
    target.edges = [
      ...(target.edges || []).filter(edge => edge?.type !== "absorption"),
      ...clone(sourceAbsorptions),
    ];
    const sourceEdges = lineageItemsById(source.edges);
    (target.edges || []).forEach(edge => {
      const incoming = sourceEdges.get(String(edge?.id || ""));
      if (!incoming) return;
      if (edge.type === "handoff") {
        overlayHandoffRuntime(edge, incoming);
      }
    });
    target.runtime_revision = Number(source.runtime_revision || 0);
    target.runtime_updated_at = String(source.runtime_updated_at || "");
    return target;
  }

  function mergeLineageStateDomains(current, incoming) {
    if (!incoming) return current;
    if (
      !current
      || String(current.lineage_id || "") !== String(incoming.lineage_id || "")
    ) return clone(incoming);

    const currentLayout = Number(current.layout_revision || 0);
    const incomingLayout = Number(incoming.layout_revision || 0);
    const currentRuntime = Number(current.runtime_revision || 0);
    const incomingRuntime = Number(incoming.runtime_revision || 0);
    const takesIncomingGraph = hasNewLineageGraphProjection(current, incoming);
    const next = clone(takesIncomingGraph ? incoming : current);

    if (incomingLayout > currentLayout) {
      overlayLineageLayout(next, incoming);
    } else if (currentLayout > incomingLayout && takesIncomingGraph) {
      overlayLineageLayout(next, current);
    }
    if (incomingRuntime > currentRuntime) {
      overlayLineageRuntime(next, incoming);
    } else if (currentRuntime > incomingRuntime && takesIncomingGraph) {
      overlayLineageRuntime(next, current);
    }
    // Executions belong to Agent history, not the Lineage relation-work
    // revision. A new failed/cancelled turn can leave every graph revision
    // unchanged; take its observation from the latest serialized read.
    const observedResidents = lineageItemsById(incoming.nodes);
    for (const node of next.nodes || []) {
      const observed = observedResidents.get(String(node?.id || ""));
      if (observed && Object.hasOwn(observed, "last_execution")) {
        node.last_execution = clone(observed.last_execution || {});
      }
    }
    // Invoke-wire backlog is a volatile delivery projection. It changes
    // without mutating the authored graph or the Resident runtime revision,
    // so the newest server response must always win independently.
    if (Object.hasOwn(incoming, "program_invoke_wire_runtime")) {
      next.program_invoke_wire_runtime = clone(
        incoming.program_invoke_wire_runtime || {},
      );
    }
    if (Object.hasOwn(incoming, "program_event_source_runtime")) {
      next.program_event_source_runtime = clone(
        incoming.program_event_source_runtime || {},
      );
    }
    if (Object.hasOwn(incoming, "program_effect_runtime")) {
      next.program_effect_runtime = clone(
        incoming.program_effect_runtime || {},
      );
    }
    if (Object.hasOwn(incoming, "program_execution_explanations")) {
      next.program_execution_explanations = clone(
        incoming.program_execution_explanations || {},
      );
    }
    // The refresh signature belongs to the graph it was read with; it arrives
    // with `incoming` only when that graph is taken above.
    return next;
  }

  function displayedResidentIds(lineage) {
    return new Set(
      (lineage?.nodes || [])
        .filter(node => node.accessible !== false)
        .map(nodeResidentId)
        .filter(Boolean),
    );
  }

  function sameIdSet(left, right) {
    return left.size === right.size && [...left].every(value => right.has(value));
  }

  function countSetChanges(previous, next) {
    return {
      added: [...next].filter(value => !previous.has(value)).length,
      removed: [...previous].filter(value => !next.has(value)).length,
    };
  }

  function notifyLineageMembershipChange(previous, next) {
    const nodes = countSetChanges(
      new Set((previous.nodes || []).map(nodeResidentId)),
      new Set((next.nodes || []).map(nodeResidentId)),
    );
    if (nodes.added || nodes.removed) {
      const changes = [nodes.added ? `新增 ${nodes.added} 个节点` : "", nodes.removed ? `移除 ${nodes.removed} 个节点` : ""].filter(Boolean).join("，");
      toast(window.PortalI18n.text`Lineage 已更新：${changes}。`, "success", 5200);
    }
    const seeds = countSetChanges(
      new Set(activeSeeds(previous).map(seed => seed.id)),
      new Set(activeSeeds(next).map(seed => seed.id)),
    );
    if (seeds.added || seeds.removed) {
      const changes = [seeds.added ? `新增 ${seeds.added} 个候选` : "", seeds.removed ? `收起 ${seeds.removed} 个候选` : ""].filter(Boolean).join("，");
      toast(window.PortalI18n.text`探索 fork 已同步候选集合：${changes}。`, "success", 5200);
    }
  }

  function forgetRemovedLineageMembers(next) {
    const nodeIds = new Set((next.nodes || []).map(node => String(node?.id || "")));
    const residentIds = new Set((next.nodes || []).map(nodeResidentId));
    for (const nodeId of [...state.runtimeStatus.keys()]) {
      if (!nodeIds.has(nodeId)) state.runtimeStatus.delete(nodeId);
    }
    for (const byResident of [
      state.residentRuntimeById,
      state.sessionUserMessageCounts,
      state.sessionContextPressures,
    ]) {
      for (const residentId of [...(byResident?.keys?.() || [])]) {
        if (!residentIds.has(residentId)) byResident.delete(residentId);
      }
    }
  }

  function renderCommittedLineage(change, scope) {
    if (!scope) return;
    if (scope === "surface" || change.structureChanged) {
      updateLineageSummary();
      renderLineageSurface();
      return;
    }
    updateLineageSummary();
    renderGraphRuntimePresentation();
    renderProgramInspector();
    templateController?.render();
    renderControls();
  }

  // Every Lineage read or mutation result for the open Lineage enters the
  // panel here (opening another Lineage is selectLineage). The merge keeps
  // each state domain at its newest revision; this function then keeps what
  // the panel derives from the displayed Lineage in step with it and redraws
  // what changed:
  // - `source: "mutation"` (default) is the result of this page's own edit
  //   and always redraws the surface; `source: "background"` is a refresh
  //   read: it yields to an edit or layout save in flight, whose own result
  //   carries the newer state, applies only when something changed, redraws
  //   only the changed scope and reports added or removed members.
  // - `render`: "surface", "changes" (runtime scope unless the structure
  //   changed) or false (the caller keeps an open form or draws itself).
  // - `afterMerge(lineage)` runs with the merged Lineage in place, before the
  //   redraw, for selection the redraw must already see.
  // - `replace: true` installs `incoming` as is (rolling back a local edit).
  function commitLineage(incoming, options = {}) {
    const background = options.source === "background";
    const change = { changed: false, structureChanged: false, runtimeChanged: false, membershipChanged: false };
    const previous = state.lineage;
    if (!incoming || !previous || String(incoming.lineage_id || "") !== String(previous.lineage_id || "")) return change;
    if (background && (state.layoutSaveInFlight || state.structureMutationInFlight)) return change;
    const next = options.replace ? incoming : mergeLineageStateDomains(previous, incoming);
    if (background && lineagePayloadsEqual(previous, next)) return change;
    change.changed = true;
    change.structureChanged = Boolean(options.replace)
      || hasNewLineageGraphProjection(previous, incoming)
      || Number(next.layout_revision || 0) !== Number(previous.layout_revision || 0);
    change.runtimeChanged = Number(next.runtime_revision || 0) !== Number(previous.runtime_revision || 0);
    change.membershipChanged = !sameIdSet(displayedResidentIds(previous), displayedResidentIds(next));
    state.lineage = next;
    state.localVersion += 1;
    forgetRemovedLineageMembers(next);
    options.afterMerge?.(next);
    reconcileGraphInteractionState();
    if (change.membershipChanged) bindResidentStateCollection();
    if (background) {
      setSyncStatus(`Lineage 已加载 · r${Number(next.revision || 0)}`, "saved");
      if (change.structureChanged) notifyLineageMembershipChange(previous, next);
    }
    const scope = Object.hasOwn(options, "render") ? options.render : (background ? "changes" : "surface");
    renderCommittedLineage(change, scope);
    return change;
  }

  async function reconcileLineageRuntime(options = {}) {
    const lineageId = state.lineage?.lineage_id || "";
    if (!lineageId) return null;
    if (state.runtimeReconcileInFlight) return state.runtimeReconcileInFlight;
    const button = options.button || null;
    if (button) setBusy(button, true, "核对中…");
    // Runtime state reconciles itself in SocietyService; the page re-reads it.
    state.runtimeReconcileInFlight = societyService.loadLineage(lineageId).then(lineage => {
      const response = { lineage };
      if (lineage && state.lineage?.lineage_id === lineageId) commitLineage(lineage);
      if (options.notify !== false) toast(window.PortalI18n.t("HATG Program 运行状态已核对。"), "success");
      return response;
    }).catch(error => {
      if (!options.quiet) toast(window.PortalI18n.text`运行状态核对失败：${error.message || error}`, "error");
      throw error;
    }).finally(() => {
      state.runtimeReconcileInFlight = null;
      if (button) setBusy(button, false);
    });
    return state.runtimeReconcileInFlight;
  }

  function selectedLineageChangeMatches(detail) {
    if (detail?.snapshot_required) return true;
    const lineageId = String(state.lineage?.lineage_id || "");
    if (!lineageId) return false;
    const changed = detail?.changed || {};
    const lineages = Array.isArray(changed.lineage) ? changed.lineage : [];
    if (lineages.includes(lineageId)) return true;
    // Retained historical identities can remain readable after deletion.
    // Their identity notification must revalidate the displayed membership,
    // even when no readable Lineage-domain notification accompanies it.
    const residentIds = new Set((state.lineage.nodes || []).map(nodeResidentId));
    if (Object.entries(detail?.resident_changes || {}).some(([residentId, scopes]) => (
      residentIds.has(residentId)
      && Array.isArray(scopes)
      && scopes.includes("identity")
    ))) return true;
    return (detail?.resident_events || []).some(event => (
      residentIds.has(String(event?.resident_id || ""))
      && event.live !== true
      && event.event_type === "resident.input.state.changed"
      && ["completed", "failed", "cancelled"].includes(event.payload?.input_state?.state)
    ));
  }

  function applyResidentStateCollection(update) {
    if (!state.lineage || update?.type === "error") return;
    const runtimeChanged = applyResidentStateProjections(update?.states || {}, {
      render: false,
      replace: true,
    });
    if (runtimeChanged) {
      updateLineageSummary();
      renderGraphRuntimePresentation();
      renderControls();
      hatgComponentOptions.onRuntimeProjectionChange?.({
        lineageId: state.lineage.lineage_id,
      });
    }
  }

  function selectedLineageMessageCountsChangeMatches(detail) {
    if (selectedLineageChangeMatches(detail)) return true;
    const residentIds = new Set((state.lineage?.nodes || []).map(nodeResidentId));
    if (Object.entries(detail?.resident_changes || {}).some(([residentId, scopes]) => (
      residentIds.has(residentId) && Array.isArray(scopes) && scopes.includes("projection")
    ))) return true;
    return (detail?.resident_events || []).some(event => (
      residentIds.has(String(event?.resident_id || ""))
      && event.live !== true
      && [
        "conversation.message.appended", "conversation.message.updated",
        "resident.input.state.changed", "resident.queue.changed",
      ].includes(String(event.event_type || ""))
    ));
  }

  function bindResidentStateCollection() {
    state.residentStateCollection?.destroy?.();
    state.residentStateCollection = null;
    const lineageId = String(state.lineage?.lineage_id || "");
    const residentIds = (state.lineage?.nodes || [])
      .filter(node => node.accessible !== false)
      .map(nodeResidentId)
      .filter(Boolean);
    if (!panelActive || !lineageId || !residentIds.length) return;
    state.residentStateCollection = window.PortalResidentProtocol.acquireStates(
      residentIds,
      applyResidentStateCollection,
    );
  }

  function applyLineageEventProjection(lineageId, lineage) {
    if (lineageId !== state.lineage?.lineage_id) return;
    commitLineage(lineage, { source: "background" });
  }

  function bindLineageProjectionSubscription() {
    state.lineageProjectionSubscription?.();
    state.lineageProjectionSubscription = null;
    if (!panelActive || !state.lineage) return;
    const coordinator = window.PortalRefreshCoordinator.create({
      key: () => String(state.lineage?.lineage_id || ""),
      interval: 30000,
      fetch: async ({ key }) => ({ lineageId: key, lineage: await societyService.loadLineage(key) }),
      apply: payload => applyLineageEventProjection(payload?.lineageId, payload?.lineage),
      subscribe: window.PortalRefreshCoordinator.portalSubscription({
        domains: ["lineage", "resident"],
        refreshOnAttach: false,
        matches: selectedLineageChangeMatches,
        debounceMs: 150,
      }),
      onError: () => {},
    });
    const countsCoordinator = window.PortalRefreshCoordinator.create({
      key: () => String(state.lineage?.lineage_id || ""),
      interval: 30000,
      fetch: ({ key }) => apiGet(`/lineage/api/node-message-counts?id=${encodeURIComponent(key)}`),
      apply: payload => applyNodeMessageCounts(payload),
      subscribe: window.PortalRefreshCoordinator.portalSubscription({
        domains: ["lineage", "resident"],
        refreshOnAttach: false,
        matches: selectedLineageMessageCountsChangeMatches,
        debounceMs: 150,
      }),
      onError: () => {},
    });
    state.lineageProjectionSubscription = () => {
      coordinator.destroy();
      countsCoordinator.destroy();
    };
    coordinator.start();
    countsCoordinator.start();
    countsCoordinator.invalidate({ delay: 0 });
  }

  function notifyResidentSelection(node, options = {}) {
    if (options.notifyParent === false || !node || node.accessible === false) return undefined;
    const lineageId = options.lineageId || node.home_lineage_id || state.lineage?.lineage_id || "";
    const residentId = nodeResidentId(node);
    return hatgComponentOptions.emit?.("open-resource", {
      type: "chat",
      resource: {
        kind: "chat",
        resident_id: residentId,
        lineage_id: lineageId,
        lineage_resident_id: node.id,
        title: node.title || residentId,
        backend: node.backend,
      },
    });
  }

  function residentEditorOpenMenu(node) {
    if (
      !node
      || node.accessible === false
      || node.kind === "seed"
      || isProgramComponent(node)
      || isFunctionBlockInstance(node)
      || !nodeResidentId(node)
    ) return null;
    return {
      label: window.PortalI18n.text`Open ${node.title || node.id}`,
      title: window.PortalI18n.t("打开这个节点的 Chat Workspace"),
    };
  }

  function notifyResidentOpenMenu(node, event, trigger) {
    if (!residentEditorOpenMenu(node)) return undefined;
    return hatgComponentOptions.onResidentOpenMenu?.({
      lineageId: node.home_lineage_id || state.lineage?.lineage_id || "",
      residentId: nodeResidentId(node),
      lineageResidentId: node.id,
      openDetails: () => openNodeSettingsModal(node),
      isFamilyFacade: Boolean(familyForFacade(nodeResidentId(node))),
      title: node.title || nodeResidentId(node),
      trigger: trigger || event?.currentTarget || null,
      event,
    });
  }

  async function selectNode(nodeId, options = {}) {
    const node = displayNodeById(nodeId);
    if (!node || node.accessible === false) return;
    if (node.id === state.selectedNodeId && !state.selectedSeedId && !state.selectedEdgeId && !state.selectedProgramObjectId && !options.force) {
      // The canvas can hold a card selected without its Chat open, such as the
      // leader when the Lineage loads; clicking that card still opens its Chat.
      if (options.openChat) await notifyResidentSelection(node, options);
      return;
    }
    const workspaceChanged = state.resultWorkspaceNodeId !== node.id;
    state.selectedSeedId = "";
    state.selectedProgramObjectId = "";
    state.selectedEdgeId = "";
    state.selectedNodeId = node.id;
    const residentSelection = Promise.resolve().then(() => notifyResidentSelection(node, options));
    publishLocation(state.lineage?.lineage_id || "");
    state.detail = null;
    state.detailSeq += 1;
    if (!renderGraphSelection()) renderGraph();
    renderControls();
    renderSeedInspector();
    if (workspaceChanged) {
      state.resultWorkspace = null;
      state.resultWorkspaceNodeId = node.id;
      state.selectedResultPath = "";
      renderLineageHeader();
    }
    await Promise.all([
      residentSelection,
      options.revealInGraph ? revealSelectedGraphNode(node.id, options) : undefined,
    ]);
  }

  function canonicalProgramResidentId(value) {
    return String(value || "").trim().toLowerCase();
  }

  function programResidentReferenceError(value, options = {}) {
    const residentId = canonicalProgramResidentId(value);
    if (!residentId) {
      return options.required === false ? "" : "请填写 Resident ID。";
    }
    const pattern = String(
      window.PortalLineageProgram?.manifest?.resident_reference?.pattern
      || "^resident_[0-9a-f]{12}$",
    );
    if (!(new RegExp(pattern)).test(residentId)) {
      return "Resident ID 必须使用 resident_ 加 12 位小写十六进制字符。";
    }
    return "";
  }

  function programResidentOptions(eventType = "", stateDomain = "") {
    return (state.lineage?.nodes || [])
      .map(node => (
        window.PortalI18n.html`<option value="${esc(node.id)}">${esc(node.title || node.id)}</option>`
      ))
      .join("");
  }

  function programEventSourceOptions(currentComponentId = "", selectedId = "") {
    return displayedProgram().components
      .filter(component => (
        component.kind === "event_source"
        && component.id !== currentComponentId
      ))
      .map(component => (
        window.PortalI18n.html`<option value="${esc(component.id)}"${component.id === selectedId ? " selected" : ""}>${esc(component.title || component.id)} · ${esc(component.id)}</option>`
      ))
      .join("");
  }

  function programEventWatchContract(eventType) {
    return (
      window.PortalLineageProgram?.manifest?.event_types?.[eventType]
        ?.watch_contract
      || {}
    );
  }

  function programEventWatchKinds(eventType) {
    return new Set(programEventWatchContract(eventType).allowed_kinds || []);
  }

  function programEffectOptions(_eventType = "", selectedId = "") {
    return displayedProgram().components
      .filter(component => component.kind === "effect")
      .map(component => (
        window.PortalI18n.html`<option value="${esc(component.id)}"${component.id === selectedId ? " selected" : ""}>${esc(component.title || component.id)} · ${esc(component.id)}</option>`
      ))
      .join("");
  }

  function programEffectReferenceError(value, eventType = "effect.failed") {
    const effectId = String(value || "").trim().toLowerCase();
    if (!effectId) return "请选择要观察的 Effect。";
    const component = displayProgramComponentById(effectId);
    if (component?.kind !== "effect") {
      return `Effect ${effectId} 不在当前 HATG。`;
    }
    return "";
  }

  function programObservableEventWireOptions(currentComponentId = "", selectedId = "") {
    return displayedProgram().wires
      .filter(wire => {
        if (!["route"].includes(wire.type) || wire.source === currentComponentId) return false;
        return wire.type === "route"
          || displayProgramComponentById(wire.source)?.kind === "event_source";
      })
      .map(wire => {
        const source = displayProgramComponentById(wire.source);
        const target = displayNodeById(wire.target) || displayProgramComponentById(wire.target);
        const label = `${source?.title || wire.source} → ${target?.title || wire.target}`;
        return window.PortalI18n.html`<option value="${esc(wire.id)}"${wire.id === selectedId ? " selected" : ""}>${esc(label)} · ${esc(wire.id)}</option>`;
      })
      .join("");
  }

  function programEventSourceReferenceError(value, currentComponentId = "") {
    const componentId = String(value || "").trim().toLowerCase();
    if (!componentId) return "请选择要观察的 Event Source。";
    if (componentId === currentComponentId) return "运行 Event Source 不能监听自身。";
    const component = displayProgramComponentById(componentId);
    return component?.kind === "event_source"
      ? ""
      : `Event Source ${componentId} 不在当前 HATG。`;
  }

  function programEventWireReferenceError(value, currentComponentId = "") {
    const wireId = String(value || "").trim().toLowerCase();
    if (!wireId) return "请选择要观察的 Route 连线。";
    const wire = displayProgramWireById(wireId);
    if (wire?.source !== currentComponentId) {
      if (wire?.type === "route") return "";
      if (
        wire?.type === "route"
        && displayProgramComponentById(wire.source)?.kind === "event_source"
      ) return "";
    }
    return `Invoke 连线 ${wireId} 不存在，或不是 Route 连线。`;
  }

  function programResidentSetPicker(selectedIds = [], readonly = false) {
    const selected = new Set(
      (Array.isArray(selectedIds) ? selectedIds : [])
        .map(canonicalProgramResidentId)
        .filter(Boolean),
    );
    return (state.lineage?.nodes || []).map(node => {
      const residentId = canonicalProgramResidentId(node.id);
      return window.PortalI18n.html`<label class="programResidentSetOption">
        <input type="checkbox" name="resident_ids" value="${esc(residentId)}"${selected.has(residentId) ? " checked" : ""}${readonly ? " disabled" : ""}>
        <span><strong>${esc(node.title || residentId)}</strong><small>${esc(residentId)}</small></span>
      </label>`;
    }).join("");
  }

  function programWatchedResidentPicker(watchedIds, readonly = false) {
    const watched = new Set(watchedIds);
    const nodes = (state.lineage?.nodes || []).filter(node => !isOperatorExecutorNode(node));
    const known = new Set(nodes.map(node => canonicalProgramResidentId(node.id)));
    const outside = watchedIds.filter(id => !known.has(id)).map(id => ({ id, title: id }));
    return [...nodes, ...outside].map(node => {
      const residentId = canonicalProgramResidentId(node.id);
      return window.PortalI18n.html`<label class="programResidentSetOption">
        <input type="checkbox" name="watch_resident_ids" value="${esc(residentId)}"${watched.has(residentId) ? " checked" : ""}${readonly ? " disabled" : ""}>
        <span><strong>${esc(node.title || residentId)}</strong><small>${esc(residentId)}</small></span>
      </label>`;
    }).join("");
  }

  function canonicalProgramEndpointId(value) {
    return String(value || "").trim().toLowerCase();
  }

  function programWireAllowedTargetKinds(wire) {
    const definition = (
      window.PortalLineageProgram?.manifest?.wire_types?.[wire?.type]
      || {}
    );
    return new Set(definition.target_kinds || []);
  }

  function programWireTargetKind(endpointId) {
    const component = displayProgramComponentById(endpointId);
    if (component?.kind) return component.kind;
    const resident = displayNodeById(endpointId);
    return resident
      ? "agent"
      : "";
  }

  function programWireTargetOptions(wire) {
    const allowed = programWireAllowedTargetKinds(wire);
    const residentOptions = (state.lineage?.nodes || [])
      .filter(node => (
        !isOperatorExecutorNode(node)
        &&
        String(node?.id || "") !== String(wire?.source || "")
        && allowed.has(
          "agent",
        )
      ))
      .map(node => (
        window.PortalI18n.html`<option value="${esc(node.id)}">${esc(node.title || node.id)}</option>`
      ))
      .join("");
    const componentOptions = displayedProgram().components
        .filter(component => (
          allowed.has(component.kind) && component.id !== wire.source
        ))
      .map(component => (
        window.PortalI18n.html`<option value="${esc(component.id)}">${esc(component.title || component.id)} · ${esc(window.PortalLineageProgram?.manifest?.component_kinds?.[component.kind]?.title || component.kind)}</option>`
      ))
      .join("");
    return `${residentOptions}${componentOptions}`;
  }

  function programWireTargetLabel(wire) {
    return window.PortalI18n.t("连接目标");
  }

  function programWireTargetPlaceholder(wire) {
    const allowed = programWireAllowedTargetKinds(wire);
    const residentAllowed = ["agent", "role"].some(kind => (
      allowed.has(kind)
    ));
    const componentAllowed = window.PortalLineageProgram?.componentKinds?.some(
      kind => allowed.has(kind),
    );
    if (residentAllowed && componentAllowed) {
      return "resident_… / component_…";
    }
    if (allowed.has("event_source")) return "event_source_…";
    return allowed.has("effect") ? "effect_…" : "resident_…";
  }

  function programWireTargetHint(wire) {
    const allowed = programWireAllowedTargetKinds(wire);
    const names = [
      ...(allowed.has("agent") ? ["Agent"] : []),
      ...(allowed.has("role") ? ["Role"] : []),
      ...(allowed.has("filter") ? ["Filter"] : []),
      ...(allowed.has("selector") ? ["Selector"] : []),
      ...(allowed.has("reducer") ? ["Reducer"] : []),
      ...(allowed.has("effect") ? ["Effect"] : []),
      ...(allowed.has("event_source") ? [window.PortalI18n.t("Event Source")] : []),
    ];
    return window.PortalI18n.text`可选择当前 HATG 的 ${names.join(" / ")}；修改 ID 与在画布重新连接会更新同一条类型化连线。`;
  }

  function programWireTargetReferenceError(wire, value) {
    const endpointId = canonicalProgramEndpointId(value);
    if (!endpointId) return "请填写目标端点 ID。";
    if (endpointId === String(wire?.source || "")) {
      return "连线不能把节点自身作为目标。";
    }
    const allowed = programWireAllowedTargetKinds(wire);
    if (endpointId.startsWith("resident_")) {
      if (!displayNodeById(endpointId)) {
        return `Resident ${endpointId} 不在当前 HATG。`;
      }
      const residentError = programResidentReferenceError(endpointId);
      if (residentError) return residentError;
      const targetKind = programWireTargetKind(endpointId);
      if (allowed.has(targetKind)) return "";
      return `${String(wire?.type || "").toUpperCase()} 线不能连接这个类型的 Resident。`;
    }
    const component = displayProgramComponentById(endpointId);
    if (component) {
      if (component?.kind && allowed.has(component.kind)) return "";
      if (component?.kind) {
        const componentLabel = component.kind === "effect"
          ? "Effect"
          : component.kind === "event_source"
            ? window.PortalI18n.t("Event Source")
            : component.kind;
        return `${String(wire?.type || "").toUpperCase()} 线不能连接 ${componentLabel}。`;
      }
      return `原语 ${endpointId} 不在当前 HATG；请先把它加入画布。`;
    }
    return `${String(wire?.type || "").toUpperCase()} 线的目标必须使用当前 HATG 中允许的规范资源 ID。`;
  }

  function showProgramReferenceWire(wireId, focusNodeId = "", options = {}) {
    const wire = displayProgramWireById(wireId);
    if (!wire) return false;
    window.clearTimeout(state.programReferenceFeedbackTimer);
    state.programReferenceFeedbackWireId = wire.id;
    renderGraph();
    if (options.focus) {
      state.graphController?.focusNode?.(
        focusNodeId || wire.target || wire.source,
        { scale: 1 },
      );
    }
    if (options.persist) return true;
    state.programReferenceFeedbackTimer = window.setTimeout(() => {
      if (state.programReferenceFeedbackWireId !== wire.id) return;
      state.programReferenceFeedbackWireId = "";
      renderGraph();
    }, 4200);
    return true;
  }

  function referenceChangeMessage(input) {
    const current = canonicalProgramEndpointId(input?.value);
    const original = canonicalProgramEndpointId(input?.dataset.referenceOriginal);
    const noun = input?.dataset.referenceNoun || window.PortalI18n.t("Reference 连线");
    if (!original && !current) return window.PortalI18n.text`尚未绑定；填写 ID 后会创建${noun}。`;
    if (!original && current) return window.PortalI18n.text`保存后会创建指向 ${current} 的${noun}。`;
    if (original && !current) return window.PortalI18n.text`保存后会删除指向 ${original} 的${noun}。`;
    if (original !== current) return window.PortalI18n.text`保存后会把${noun}从 ${original} 改绑到 ${current}。`;
    return window.PortalI18n.text`当前由已保存的${noun}绑定到 ${current}。`;
  }

  function bindProgramReferenceControls(panel) {
    panel?.querySelectorAll?.("[data-program-reference-input]").forEach(input => {
      const statusId = String(input.dataset.referenceStatusId || "");
      const status = statusId ? panel.querySelector(`#${CSS.escape(statusId)}`) : null;
      const sync = () => {
        if (status) status.textContent = referenceChangeMessage(input);
      };
      input.addEventListener("input", sync);
      input.addEventListener("change", sync);
      sync();
    });
    panel?.querySelectorAll?.("[data-program-reference-locate]").forEach(button => {
      button.addEventListener("click", () => {
        const wireId = String(button.dataset.programReferenceLocate || "");
        const focusNodeId = String(button.dataset.referenceFocusNodeId || "");
        if (!showProgramReferenceWire(wireId, focusNodeId, { focus: true })) {
          toast(window.PortalI18n.t("这条 Reference 连线已经不存在，请重新保存当前配置。"), "warning", 4200);
        }
      });
    });
  }

  function programInvokeWireRuntime(wire) {
    if (!["route"].includes(wire?.type)) return {};
    const runtime = state.lineage?.program_invoke_wire_runtime;
    return (
      runtime
      && typeof runtime === "object"
      && runtime[wire.id]
      && typeof runtime[wire.id] === "object"
    ) ? runtime[wire.id] : {};
  }

  function programPrimitiveDefaultType() {
    return window.PortalLineageProgram?.primitiveDefaultType?.() || "";
  }

  function programInvokeProfileKey(targetId) {
    return window.PortalLineageElementFields.invokeProfileKey(displayProgramComponentById(targetId)?.kind);
  }

  function programInvokeArguments(wire, targetId = wire?.target) {
    const profileKey = programInvokeProfileKey(targetId);
    const profiles = wire?.config?.invoke_profiles || {};
    const profile = profiles[profileKey] || {};
    return {
      profileKey,
      arguments: clone(profile.arguments || {}),
    };
  }

  function programInvokeArgumentFieldsHtml(
    wire,
    readonly,
    targetId = wire?.target,
    mappingOverride = null,
  ) {
    const current = programInvokeArguments(wire, targetId);
    const profileKey = current.profileKey;
    const mappings = mappingOverride || current.arguments;
    const target = displayProgramComponentById(targetId);
    const targetKind = target?.kind === "effect"
      ? "effect"
      : programInvokeProfileKey(targetId);
    return window.PortalLineageInvocationInspector.mappingEditorHtml({
      profileKey,
      mapping: mappings,
      wireKind: wire.type,
      targetKind,
      effectConfig: target?.kind === "effect" ? target.config || {} : null,
      editorId: `program-${wire.id}-${profileKey}`,
      readonly,
    });
  }

  function programDeliveryWireFieldsHtml(wire, readonly) {
    const eventWire = wire?.type === "route";
    const invokeWire = eventWire || wire?.type === "route";
    if (!invokeWire) return "";
    const runtime = programInvokeWireRuntime(wire);
    const messageDelivery = programInvokeProfileKey(wire.target) === "agent";
    const budgetHtml = messageDelivery ? window.PortalI18n.html`<section class="programWireBacklogStatus" data-wire-delivery-budget>
      <div class="programDeliveryQueueHead"><strong data-wire-sent-count>${runtime.sent_count || 0} 条消息已发送</strong><button type="button" data-wire-delivery-reset${readonly ? " disabled" : ""}>重置发送计数</button></div>
      <span data-wire-remaining>${runtime.max_messages == null ? window.PortalI18n.t("剩余次数：无限次") : window.PortalI18n.text`剩余次数：${runtime.remaining_messages ?? runtime.max_messages}`}</span>
      <small data-wire-limit-state>${runtime.message_limit_reached ? window.PortalI18n.t("已达到发送上限，新的消息将不再发送。") : window.PortalI18n.t("计数持久保存；重置后继续接收新消息，不补发已丢弃的消息。")}</small>
    </section>` : "";
    const pendingCount = Math.max(0, Math.trunc(Number(runtime.pending_count) || 0));
    const backlogHtml = window.PortalI18n.html`<section class="programWireBacklogStatus" data-event-wire-backlog data-pending-event-count="${pendingCount}">
        <div class="programDeliveryQueueHead">
          <strong data-delivery-count>${pendingCount} 条投递正在这条边上等待</strong>
          <button type="button" data-delivery-refresh>刷新队列</button>
        </div>
        <span data-delivery-timing>${runtime.oldest_queued_at ? window.PortalI18n.text`最早进入：${esc(formatDate(runtime.oldest_queued_at))}` : window.PortalI18n.t("接收方恢复可消费后，Portal 会按边策略投递。")}${runtime.next_expires_at ? window.PortalI18n.text` · 最近过期：${esc(formatDate(runtime.next_expires_at))}` : ""}</span>
        <small>每条 Prompt 单独保存，仅影响本次调用；删除后不会再次投递。</small>
        <div data-delivery-queue-list>${pendingCount ? window.PortalI18n.t("正在加载等待条目…") : window.PortalI18n.t("暂无等待条目")}</div>
        <small data-delivery-queue-status role="status"></small>
      </section>`;
    return window.PortalLineageElementFields.deliveryFieldsHtml(wire, readonly, {
      extraHtml: budgetHtml + backlogHtml,
      messageDelivery,
      mappingHtml: programInvokeArgumentFieldsHtml(wire, readonly),
    });
  }

  function programInvokeEditorState(form, wire) {
    let editorState = PROGRAM_INVOKE_EDITOR_STATES.get(form);
    if (editorState) return editorState;
    editorState = {
      profiles: clone(wire?.config?.invoke_profiles || {}),
      profileKey: programInvokeProfileKey(wire?.target),
      targetId: String(wire?.target || ""),
    };
    PROGRAM_INVOKE_EDITOR_STATES.set(form, editorState);
    return editorState;
  }

  function switchProgramInvokeEditorTarget(form, wire, targetId, readonly) {
    const slot = form?.querySelector("[data-delivery-mapping-slot]");
    if (!slot) return { errors: [] };
    const editorState = programInvokeEditorState(form, wire);
    const current = window.PortalLineageInvocationInspector.readMapping(slot);
    if (current.errors.length) return current;
    editorState.profiles[editorState.profileKey] = {
      arguments: current.mapping,
    };
    const nextProfileKey = programInvokeProfileKey(targetId);
    editorState.profileKey = nextProfileKey;
    editorState.targetId = targetId;
    const messageLimit = form.querySelector("[data-message-limit]");
    if (messageLimit) {
      messageLimit.hidden = nextProfileKey !== "agent";
      messageLimit.querySelector("input").disabled = readonly || nextProfileKey !== "agent";
    }
    const nextMapping = clone(
      editorState.profiles[nextProfileKey]?.arguments || {},
    );
    slot.innerHTML = programInvokeArgumentFieldsHtml(
      wire,
      readonly,
      targetId,
      nextMapping,
    );
    window.PortalLineageInvocationInspector.bindMappingEditor(slot);
    return { mapping: nextMapping, errors: [] };
  }

  function bindProgramInvokeEditor(panel, wire, readonly) {
    if (!["route"].includes(wire?.type)) return;
    const form = panel?.querySelector("[data-program-wire-form]");
    if (!form) return;
    programInvokeEditorState(form, wire);
    form.querySelector("[data-wire-delivery-reset]")?.addEventListener("click", async event => {
      if (readonly || state.programMutationInFlight) return;
      const button = event.currentTarget;
      const lineageId = state.lineage?.lineage_id;
      setBusy(button, true);
      try {
        const response = await societyService.resetWireDeliveryCount({ lineage_id: lineageId, wire_id: wire.id });
        if (state.lineage?.lineage_id !== lineageId) return;
        // Update only runtime labels so an unsaved limit edit stays in the form.
        commitLineage(response.lineage, { render: false });
        const runtime = programInvokeWireRuntime(wire);
        form.querySelector("[data-wire-sent-count]").textContent = window.PortalI18n.text`${runtime.sent_count || 0} 条消息已发送`;
        form.querySelector("[data-wire-remaining]").textContent = runtime.max_messages == null ? window.PortalI18n.t("剩余次数：无限次") : window.PortalI18n.text`剩余次数：${runtime.remaining_messages}`;
        form.querySelector("[data-wire-limit-state]").textContent = runtime.message_limit_reached ? window.PortalI18n.t("已达到发送上限，新的消息将不再发送。") : window.PortalI18n.t("发送计数已清零。");
        toast(window.PortalI18n.t("发送计数已清零。"), "success");
      } catch (error) {
        toast(error.message || String(error), "error");
      } finally {
        setBusy(button, false);
      }
    });
    bindPendingDeliveryQueue(panel, wire, readonly);
    window.PortalLineageInvocationInspector.bindMappingEditor(form);
    const targetInput = form.querySelector('[name="target_endpoint_id"]');
    const switchToEnteredTarget = (event, reportMappingErrors = false) => {
      const nextTargetId = canonicalProgramEndpointId(event.currentTarget.value);
      const editorState = programInvokeEditorState(form, wire);
      if (
        !nextTargetId
        || nextTargetId === editorState.targetId
        || programWireTargetReferenceError(wire, nextTargetId)
      ) return;
      const result = switchProgramInvokeEditorTarget(
        form,
        wire,
        nextTargetId,
        readonly,
      );
      if (!result.errors.length || !reportMappingErrors) return;
      event.currentTarget.value = editorState.targetId;
      toast(result.errors.join(" "), "warning", 5600);
    };
    // A valid datalist choice or pasted canonical ID switches the argument
    // contract immediately. Waiting for blur would rebuild the fields between
    // a Save button's pointer-down and click, moving the button out from under
    // the pointer and silently swallowing that save.
    targetInput?.addEventListener("input", event => {
      switchToEnteredTarget(event);
    });
    targetInput?.addEventListener("change", event => {
      switchToEnteredTarget(event, true);
    });
  }

  function bindPendingDeliveryQueue(panel, wire, readonly) {
    const section = panel.querySelector("[data-event-wire-backlog]");
    if (!section) return;
    const lineageId = state.lineage?.lineage_id;
    const list = section.querySelector("[data-delivery-queue-list]");
    const status = section.querySelector("[data-delivery-queue-status]");
    let busy = false;
    const isCurrent = () => section.isConnected && state.lineage?.lineage_id === lineageId;
    const render = (response, changedId = "") => {
      const drafts = new Map([...list.querySelectorAll("[data-pending-delivery]")]
        .filter(row => row.dataset.pendingDelivery !== changedId)
        .map(row => [row.dataset.pendingDelivery, {
          revision: Number(row.dataset.deliveryRevision),
          value: row.querySelector("textarea").value,
        }]));
      const runtime = response.runtime || {};
      const count = Math.max(0, Number(runtime.pending_count) || 0);
      section.dataset.pendingEventCount = String(count);
      section.querySelector("[data-delivery-count]").textContent = window.PortalI18n.text`${count} 个 投递 正在这条边上等待`;
      section.querySelector("[data-delivery-timing]").textContent = (
        runtime.oldest_queued_at ? window.PortalI18n.text`最早进入：${formatDate(runtime.oldest_queued_at)}`
          : window.PortalI18n.t("接收方恢复可消费后，Portal 会按边策略投递。")
      ) + (runtime.next_expires_at ? window.PortalI18n.text` · 最近过期：${formatDate(runtime.next_expires_at)}` : "");
      list.innerHTML = (response.deliveries || []).map((item, index) => {
        const disabled = readonly || item.status === "dispatching";
        const draft = drafts.get(item.delivery_id);
        const prompt = draft?.revision === item.revision ? draft.value : item.prompt_override || "";
        return window.PortalI18n.html`<details class="programPendingDelivery" data-pending-delivery="${esc(item.delivery_id)}" data-delivery-revision="${item.revision}" open>
          <summary>投递 ${index + 1} · ${item.status === "dispatching" ? window.PortalI18n.t("正在投递") : window.PortalI18n.t("等待中")}</summary>
          <small>${esc(item.event_type)} · ${esc(formatDate(item.queued_at))}${item.expires_at ? window.PortalI18n.text` · 到期 ${esc(formatDate(item.expires_at))}` : ""}</small>
          <label><span>本次 Prompt</span><textarea data-delivery-prompt rows="3" maxlength="16000" placeholder="留空沿用连线的 Prompt 配置"${disabled ? " disabled" : ""}>${esc(prompt)}</textarea></label>
          <div class="programDeliveryQueueActions">
            <button type="button" data-delivery-delete${disabled ? " disabled" : ""}>删除此 投递</button>
            <button type="button" data-delivery-save${disabled ? " disabled" : ""}>保存本次 Prompt</button>
          </div>
        </details>`;
      }).join("") || "暂无等待条目";
      state.lineage.program_invoke_wire_runtime ||= {};
      state.lineage.program_invoke_wire_runtime[wire.id] = runtime;
      renderGraph();
    };
    const request = async (action, row = null) => {
      if (busy || !isCurrent()) return;
      busy = true;
      const controls = [...section.querySelectorAll("button, textarea")];
      const disabledStates = controls.map(control => control.disabled);
      const deliveryId = row?.dataset.pendingDelivery || "";
      const body = { lineage_id: lineageId, wire_id: wire.id };
      if (row) {
        body.delivery_id = deliveryId;
        if (action === "update") body.prompt = row.querySelector("textarea").value;
      }
      controls.forEach(control => { control.disabled = true; });
      status.textContent = action === "list" ? window.PortalI18n.t("正在刷新队列…") : window.PortalI18n.t("正在保存…");
      try {
        const method = {
          list: "listPendingDeliveries",
          update: "updatePendingDelivery",
          delete: "deletePendingDelivery",
        }[action];
        const response = await societyService[method](body);
        if (!isCurrent()) return;
        render(response, deliveryId);
        status.textContent = action === "delete" ? window.PortalI18n.t("已删除，不会再投递。")
          : action === "update" ? window.PortalI18n.t("本次 Prompt 已保存。") : "";
      } catch (error) {
        if (isCurrent()) status.textContent = String(error.message || error);
      } finally {
        controls.forEach((control, index) => { control.disabled = disabledStates[index]; });
        busy = false;
      }
    };
    section.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button || button.disabled) return;
      if (button.hasAttribute("data-delivery-refresh")) {
        request("list");
      } else if (!readonly) {
        const row = button.closest("[data-pending-delivery]");
        if (row) request(button.hasAttribute("data-delivery-delete") ? "delete" : "update", row);
      }
    });
    if (Number(section.dataset.pendingEventCount) > 0) request("list");
  }

  function programPrimitiveExternalFieldHtml(
    component,
    field,
    context,
  ) {
    const config = context.config || component.config || {};
    const readonly = context.readonly;
    if (field.type === "resident-set") {
      const visibility = field.visible_when
        ? ` data-visible-path="${esc(field.visible_when.path)}" data-visible-equals="${esc(field.visible_when.equals)}"`
        : "";
      return window.PortalI18n.html`<fieldset class="programResidentSetPicker" data-event-resident-set data-primitive-field data-field-path="${esc(field.path)}" data-field-type="resident-set"${visibility}>
        <legend>${esc(field.label || window.PortalI18n.t("指定 Resident 集合"))}</legend>
        <div>${programResidentSetPicker(config.resident_ids, readonly)}</div>
        <small>从当前 HATG 中选择一个或多个 Resident；每个对象始终使用规范 resident_* ID。</small>
      </fieldset>`;
    }
    if (field.type !== "watch-reference") return "";
    // A terminal Resident Event watching the Lineage or all of Portal needs no Watch wire.
    const watchVisibility = field.visible_when
      ? ` data-visible-path="${esc(field.visible_when.path)}" data-visible-equals="${esc(field.visible_when.equals)}"`
      : "";
    const contract = context.definition?.watch_contract || {};
    const allowedKinds = Array.isArray(contract.allowed_kinds)
      ? contract.allowed_kinds
      : [];
    const watchWire = displayedProgram().wires.find(item => (
      item.type === "watch"
      && item.source === component.id
    ));
    const watchedResidentIds = displayedProgram().wires
      .filter(item => item.type === "watch" && item.source === component.id)
      .map(item => canonicalProgramResidentId(item.target))
      .filter(Boolean);
    if (allowedKinds.includes("resident") && contract.multiple && watchedResidentIds.length > 1) {
      // One Event Source watching several Residents: any one fires it, and its Event names that Resident.
      return window.PortalI18n.html`<fieldset class="programResidentSetPicker" data-watch-resident-set data-primitive-field data-field-path="watch.subject"${watchVisibility}>
        <legend>${esc(field.label || window.PortalI18n.t("监听的 Resident"))}（任一触发）</legend>
        <div>${programWatchedResidentPicker(watchedResidentIds, readonly)}</div>
        <small>监听多个 Resident：任一 Resident 发生这个事件都会触发，事件会注明是哪一个。也可以在画布上连接或删除 Watch 连线。</small>
      </fieldset>`;
    }
    if (allowedKinds.includes("resident")) {
      const subjectResidentId = canonicalProgramResidentId(
        watchWire?.target,
      );
      const watchIsLocatable = Boolean(
        watchWire && displayNodeById(watchWire.target),
      );
      return window.PortalI18n.html`<label data-primitive-field data-field-path="watch.subject"${watchVisibility}><span>${esc(field.label || window.PortalI18n.t("监听的 Resident"))}</span><span class="programReferenceControl"><input name="subject_resident_id" list="programEventResidentOptions" maxlength="80" autocomplete="off" spellcheck="false" placeholder="选择或粘贴任意 resident_* ID" value="${esc(subjectResidentId)}" data-program-reference-input data-reference-original="${esc(watchWire?.target || "")}" data-reference-status-id="programEventReferenceStatus" data-reference-noun="监听连线"${readonly ? " disabled" : ""}>${watchIsLocatable ? window.PortalI18n.html`<button type="button" data-program-reference-locate="${esc(watchWire.id)}" data-reference-focus-node-id="${esc(watchWire.target)}">定位</button>` : ""}</span><small id="programEventReferenceStatus" class="programReferenceStatus"></small><small>可直接观察其他 Lineage 的 Resident；当前 HATG 节点仅作为快捷候选。</small><datalist id="programEventResidentOptions">${programResidentOptions(context.typeId, config.state_domain || "intake")}</datalist></label>`;
    }
    if (allowedKinds.includes("effect") && contract.multiple) {
      const selected = new Set(displayedProgram().wires
        .filter(item => item.type === "watch" && item.source === component.id)
        .map(item => item.target));
      return window.PortalI18n.html`<fieldset data-watch-effect-set data-primitive-field data-field-path="watch.subject"><legend>${esc(field.label || window.PortalI18n.t("观察的 Effect"))}（可多选）</legend>${displayedProgram().components
        .filter(item => item.kind === "effect")
        .map(item => window.PortalI18n.html`<label><input type="checkbox" name="watch_effect_ids" value="${esc(item.id)}"${selected.has(item.id) ? " checked" : ""}${readonly ? " disabled" : ""}>${esc(item.title || item.id)} · ${esc(item.id)}</label>`).join("")}<small>任一选中 Effect 的脚本结束时触发。</small></fieldset>`;
    }
    if (allowedKinds.includes("effect")) {
      return window.PortalI18n.html`<label data-effect-event-subject data-primitive-field data-field-path="watch.subject"><span>${esc(field.label || window.PortalI18n.t("观察的 Effect"))}</span><select name="subject_effect_id"${readonly ? " disabled" : ""}><option value="">请选择 Effect</option>${programEffectOptions(context.typeId, watchWire?.target || "")}</select><small>监听这个 Effect 的运行事实；结果通过当前 Event Source 的唯一出口继续路由。</small></label>`;
    }
    if (allowedKinds.includes("event_source")) {
      const supportsWire = allowedKinds.includes("wire");
      const deliverySubjectKind = (
        supportsWire
        && displayProgramWireById(watchWire?.target)
      ) ? "wire" : "event_source";
      return window.PortalI18n.html`${supportsWire ? window.PortalI18n.html`<label data-delivery-subject-kind data-primitive-field data-field-path="watch.subject.kind"><span>监听范围</span><select name="delivery_subject_kind"${readonly ? " disabled" : ""}><option value="event_source"${deliverySubjectKind === "event_source" ? " selected" : ""}>整个 Event Source</option><option value="wire"${deliverySubjectKind === "wire" ? " selected" : ""}>一条 Route 连线</option></select><small>监听 Source 会汇总它的失败或过期；监听连线只接收该路径的事实。</small></label>` : ""}
        <label data-runtime-event-source-subject data-primitive-field data-field-path="watch.subject.event_source"${supportsWire && deliverySubjectKind === "wire" ? " hidden" : ""}><span>${esc(field.label || window.PortalI18n.t("观察的 Event Source"))}</span><select name="subject_event_source_id"${readonly ? " disabled" : ""}><option value="">请选择 Event Source</option>${programEventSourceOptions(component.id, watchWire?.target || "")}</select></label>
        ${supportsWire ? window.PortalI18n.html`<label data-runtime-event-wire-subject data-primitive-field data-field-path="watch.subject.wire"${deliverySubjectKind === "wire" ? "" : " hidden"}><span>观察的 Route 连线</span><select name="subject_wire_id"${readonly ? " disabled" : ""}><option value="">请选择 Route 连线</option>${programObservableEventWireOptions(component.id, watchWire?.target || "")}</select></label>` : ""}`;
    }
    return window.PortalI18n.html`<p class="primitiveInspectorExternalHint">${esc(field.label || window.PortalI18n.t("观察对象"))}通过画布中的 Watch 连线绑定。</p>`;
  }

  function programPrimitiveInspectorOptions(
    component,
    kind,
    typeId,
    config,
    readonly,
    callbacks = {},
  ) {
    return {
      kind,
      typeId,
      config,
      readonly,
      renderExternalField: (field, context) => (
        programPrimitiveExternalFieldHtml(component, field, context)
      ),
      ...callbacks,
    };
  }

  function programPrimitiveInspectorHtml(
    component,
    kind,
    typeId,
    config,
    readonly,
  ) {
    return window.PortalLineagePrimitiveInspector.inspectorHtml(
      programPrimitiveInspectorOptions(
        component,
        kind,
        typeId,
        config,
        readonly,
      ),
    );
  }

  function bindProgramPrimitiveInspector(
    panel,
    component,
    kind,
    readonly,
    callbacks = {},
  ) {
    const inspector = panel?.querySelector("[data-primitive-inspector]");
    const initialType = inspector?.dataset.primitiveType || "";
    window.PortalLineagePrimitiveInspector.bindInspector(
      inspector,
      programPrimitiveInspectorOptions(
        component,
        kind,
        initialType,
        component.config || {},
        readonly,
        {
          ...callbacks,
          afterRender: detail => {
            bindProgramReferenceControls(panel);
            callbacks.afterRender?.(detail);
          },
        },
      ),
    );
  }

  function programEffectOutputValue(value) {
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value);
    } catch (_error) {
      return String(value ?? "");
    }
  }

  function programEffectRuntimeHtml(componentId) {
    const runtime = programEffectRuntime(componentId);
    const status = String(runtime.status || "");
    if (!status) {
      return window.PortalI18n.html`<section class="programEffectRuntime" data-effect-runtime-status="never"><strong>最近一次执行</strong><small>尚未执行。连接 Route 到输入端口 后，这里会显示当前结果。</small></section>`;
    }
    const rows = [
      ["状态", status === "executed" ? "执行成功" : status === "cancelled" ? "已取消" : "执行失败"],
      ["时间", formatDate(runtime.at) || runtime.at],
      ...Object.entries(runtime.outputs || {}).map(([field, value]) => [
        field,
        programEffectOutputValue(value),
      ]),
      ...(runtime.error ? [["错误", runtime.error]] : []),
    ].filter(([, value]) => String(value ?? "").trim());
    return window.PortalI18n.html`<section class="programEffectRuntime" data-effect-runtime-status="${esc(status)}"><strong>最近一次执行</strong><dl class="programInspectorEndpoints">${rows.map(([field, value]) => window.PortalI18n.html`<div><dt>${esc(field)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl><small>这里显示最近一次执行；每次执行都记在 Trigger Path 中。</small></section>${programTriggerPathObservationHtml(runtime.trigger_path_id)}`;
  }


  function programTriggerPathObservationHtml(triggerPathId) {
    if (!triggerPathId) return "";
    return window.PortalI18n.html`<section class="programTriggerPathObservation" data-trigger-path-observation data-trigger-path-id="${esc(triggerPathId)}">
      <header><strong>Trigger Path</strong><button type="button" class="portalButton is-quiet" data-trigger-path-refresh>刷新</button></header>
      <p data-trigger-path-status>正在读取触发流程…</p><div data-trigger-path-body></div>
    </section>`;
  }

  function bindProgramTriggerPathObservation(panel, triggerPathId) {
    const section = panel?.querySelector("[data-trigger-path-observation]");
    const lineageId = String(state.lineage?.lineage_id || "");
    if (!section || !lineageId || !triggerPathId) return;
    const status = section.querySelector("[data-trigger-path-status]");
    const body = section.querySelector("[data-trigger-path-body]");
    const refresh = section.querySelector("[data-trigger-path-refresh]");
    const nodeTitle = id => programComponentById(id)?.title || displayNodeById(id)?.title || id;
    let sequence = 0, nextOffset = null, scriptView = null;
    const current = request => request === sequence && section.isConnected
      && state.lineage?.lineage_id === lineageId && section.dataset.triggerPathId === triggerPathId;
    const appendSteps = response => {
      for (const step of response.steps?.items || []) {
        const article = document.createElement("article");
        article.className = "programTriggerPathStep";
        article.dataset.triggerPathStep = step.step_id;
        article.dataset.stepStatus = step.status;
        const disposition = step.result?.disposition;
        article.innerHTML = window.PortalI18n.html`<strong>${esc(nodeTitle(step.node_id))}</strong>
          <p>${esc(step.kind)} · ${esc(step.status)} · ${esc(formatDate(step.created_at))}</p>
          <details><summary>标识与来源</summary><code>${esc(step.node_id)}</code>
          ${step.input_message_id ? window.PortalI18n.html`<p>输入：<code>${esc(step.input_message_id)}</code></p>` : ""}</details>
          ${disposition ? window.PortalI18n.html`<p>处置：${esc(disposition.actor)} · ${esc(disposition.status)} · ${esc(disposition.note || "")}</p>` : ""}
          ${step.resolution_required ? window.PortalI18n.html`<p>外部调用结果未知</p><textarea data-effect-resolution-note maxlength="4000" aria-label="处置说明"></textarea>
          <button type="button" class="portalButton" data-effect-resolve="succeeded">标记成功</button><button type="button" class="portalButton is-danger" data-effect-resolve="failed">标记失败</button>
          <p data-effect-resolution-error role="status"></p>` : ""}`;
        article.querySelectorAll("[data-effect-resolve]").forEach(control => control.addEventListener("click", async () => {
          const request = sequence;
          article.querySelectorAll("button").forEach(button => { button.disabled = true; });
          try {
            await window.PortalInstructionClient.execute("ResolveEffectActivation", {
              lineage_id: lineageId, activation_key: step.activation_key, status: control.dataset.effectResolve,
              note: article.querySelector("[data-effect-resolution-note]").value,
            });
            if (current(request)) await load();
          } catch (error) {
            if (current(request)) article.querySelector("[data-effect-resolution-error]").textContent = String(error.message || error);
          } finally {
            if (current(request)) article.querySelectorAll("button").forEach(button => { button.disabled = false; });
          }
        }));
        body.querySelector("[data-trigger-path-steps]").append(article);
      }
      for (const link of response.execution_links?.items || []) {
        const p = document.createElement("p");
        p.textContent = `${link.step_id} → Execution ${link.execution_id}`;
        body.querySelector("[data-trigger-path-executions]").append(p);
      }
      for (const execution of response.executions?.items || []) {
        const p = document.createElement("p");
        p.textContent = `${execution.execution_id} · ${execution.agent_id} · ${execution.status}`;
        body.querySelector("[data-trigger-path-executions]").append(p);
      }
      const offsets = ["steps", "execution_links", "executions"].map(key => response[key]?.next_offset).filter(value => value != null);
      nextOffset = offsets.length ? Math.min(...offsets) : null;
      body.querySelector("[data-trigger-path-more]").hidden = nextOffset === null;
    };
    const fetchPage = offset => societyService.getTriggerPath({lineage_id: lineageId, trigger_path_id: triggerPathId, offset, limit: 50});
    const load = async () => {
      const request = ++sequence;
      refresh.disabled = true;
      try {
        const response = await fetchPage(0);
        if (!current(request)) return;
        const path = response.path;
        status.textContent = `${path.status} · ${path.root_kind} · ${nodeTitle(path.root_node_id)}`;
        scriptView?.destroy();
        body.innerHTML = window.PortalI18n.html`<p>起因：${esc(path.cause_kind)}</p>
          <p>发生时间：${esc(formatDate(path.occurred_at))}</p><div data-trigger-path-steps></div>
          <details><summary>执行关联</summary><code>${esc(path.cause_id)}</code><div data-trigger-path-executions></div></details>
          <button type="button" class="portalButton is-quiet" data-trigger-path-more hidden>更多步骤与执行</button>
          <div data-trigger-path-script-calls></div>`;
        appendSteps(response);
        body.querySelector("[data-trigger-path-more]").addEventListener("click", async event => {
          const control = event.currentTarget;
          control.disabled = true;
          try {
            const page = await fetchPage(nextOffset);
            if (current(request)) appendSteps(page);
          } catch (error) {
            if (current(request)) status.textContent = String(error.message || error);
          } finally { if (current(request)) control.disabled = false; }
        });
        scriptView = window.PortalScriptRuns.mount(body.querySelector("[data-trigger-path-script-calls]"), {lineageId, triggerPathId, response});
      } catch (error) {
        if (current(request)) { status.textContent = String(error.message || error); body.replaceChildren(); }
      } finally { if (current(request)) refresh.disabled = false; }
    };
    refresh.addEventListener("click", load);
    load();
  }

  function syncProgramEventInspectorFields(panel) {
    syncProgramRunAsGuidance(panel);
    const eventType = panel?.querySelector('[name="event_type"]')?.value
      || programPrimitiveDefaultType("event");
    const deliverySubjectKind = panel?.querySelector('[name="delivery_subject_kind"]')?.value || "event_source";
    const datalist = panel?.querySelector("#programEventResidentOptions");
    if (datalist) {
      datalist.innerHTML = programResidentOptions(
        eventType,
        panel?.querySelector('[name="state_domain"]')?.value || "intake",
      );
    }
    const sourceSubjectField = panel?.querySelector(
      '[data-field-path="watch.subject.event_source"]',
    );
    if (sourceSubjectField) {
      sourceSubjectField.hidden = deliverySubjectKind === "wire";
    }
    const wireSubjectField = panel?.querySelector(
      '[data-field-path="watch.subject.wire"]',
    );
    if (wireSubjectField) {
      wireSubjectField.hidden = deliverySubjectKind !== "wire";
    }
    const emit = panel?.querySelector("[data-program-emit]");
    if (emit) emit.hidden = eventType !== "manual.triggered";
  }

  function syncManualProgramEventInspectorBusy(componentId) {
    const panel = $id("programInspectorPanel");
    if (panel?.dataset.componentId !== String(componentId || "")) return null;
    const button = panel.querySelector("[data-program-emit]");
    const reset = panel.querySelector("[data-program-event-reset]");
    const key = manualProgramEventKey(componentId);
    const runtime = programEventSourceRuntime(componentId);
    const busy = state.manualProgramEventsInFlight.has(key);
    if (button) {
      setBusy(button, busy, "触发中…");
      if (!busy) {
        button.disabled = (
          state.programMutationInFlight
          || Boolean(runtime.paused)
          || Boolean(runtime.exhausted)
        );
      }
    }
    const resetting = state.programEventSourceResetsInFlight.has(key);
    if (reset) {
      setBusy(reset, resetting, "刷新中…");
      if (!resetting) {
        reset.disabled = state.programMutationInFlight;
      }
    }
    return button;
  }

  function syncProgramWireInspectorFields(panel) {
    window.PortalLineageElementFields.syncDeliveryFields(panel);
  }

  // A Function Block's Menu: double-clicking its pigeon opens it. The
  // block's own tab shows what the inspector beside the canvas used to show; a
  // Family adds a tab for its Router and one for its steward, each the
  // Resident's usual Menu, and opens their Chats from the block's tab.
  let functionBlockMenuRefresh = null;

  function openFunctionBlockMenu(instanceId, active = "block") {
    const instance = functionBlockInstanceById(instanceId);
    if (!instance) return null;
    const roles = familyRoles(instance);
    const resident = (key, label, residentId) => {
      const node = residentId ? displayNodeById(residentId) : null;
      return node ? { key, label, residentId, node: { ...node, intake_state: nodeIntakeState(node) } } : null;
    };
    const tabs = [
      { key: "block", label: instance.title || window.PortalI18n.t("Function Block") },
      { key: "objects", label: window.PortalI18n.t("内部对象") },
      { key: "issues", label: window.PortalI18n.t("问题") },
      resident("router", "Router", roles?.router),
      resident("steward", "Steward", roles?.steward),
    ].filter(Boolean);
    const tab = tabs.find(item => item.key === active) || tabs[0];
    return window.PortalNav.openResidentMenu({
      ...residentMenuHandlers(),
      lineage: state.lineage,
      residentId: tab.residentId || "",
      node: tab.node || null,
      group: {
        tabs,
        active: tab.key,
        render: (key, root, { close, setLabel }) => renderFunctionBlockMenuPanel(instance.id, root, close, setLabel, key),
      },
    });
  }

  function functionBlockInsideResidentsHtml(instance) {
    const roles = familyRoles(instance);
    const rows = roles ? [["Router", roles.router], ["Steward", roles.steward]]
      .filter(([, residentId]) => residentId)
      .map(([label, residentId]) => {
        const title = displayNodeById(residentId)?.title || residentId;
        return window.PortalI18n.html`<div><dt>${esc(label)}</dt><dd><span title="${esc(residentId)}">${esc(title)}</span><button class="portalButton" type="button" data-function-block-chat="${esc(residentId)}">打开对话</button></dd></div>`;
      }).join("") : "";
    return rows ? window.PortalI18n.html`<section class="functionBlockInstanceBindings functionBlockInsideResidents"><span>模块内的 Resident</span><dl>${rows}</dl></section>` : "";
  }

  function functionBlockSourceTitle(block) {
    return block.template_id
      ? (templateController?.find?.(block.template_id)?.title || block.template_id)
      : window.PortalI18n.t("自定义选区");
  }

  // Effect failure recipients are saved on their own (decision_4adcc1ff2f2c “Effect 失败通知”):
  // the inspector, a folded module's objects and its overview open the same editor
  // for one Effect or several.
  function effectRecipients(component) {
    return window.PortalLineageElementFields.recipientList(component?.config?.failure_recipients || []);
  }

  // "unset" (an older Effect without a list, not notified) is never shown as "off".
  function effectRecipientsState(component) {
    return window.PortalLineageElementFields.recipientState(component?.config);
  }

  function openEffectRecipientsEditor(effectIds) {
    const effects = effectIds.map(id => displayProgramComponentById(id)).filter(item => item?.kind === "effect");
    if (!effects.length || state.programMutationInFlight) return null;
    const revision = state.lineage.revision;
    return window.PortalLineageElementFields.openRecipientEditor({
      effects: effects.map(item => ({ id: item.id, title: item.title || item.id, recipients: effectRecipients(item), state: effectRecipientsState(item) })),
      me: state.me,
      onSave: changes => saveEffectRecipients(changes, revision),
    });
  }

  async function saveEffectRecipients(changes, revision) {
    const fields = window.PortalLineageElementFields;
    const single = changes.length === 1 ? changes[0] : null;
    const message = single
      ? (single.recipients.length
        ? window.PortalI18n.text`已保存：${single.title} 失败时提醒 ${fields.recipientNames(single.recipients)}。`
        : window.PortalI18n.text`已关闭 ${single.title} 的失败通知。`)
      : window.PortalI18n.text`已更新 ${changes.length} 个 Effect 的失败通知。`;
    // The list the editor showed belongs to this revision; a newer definition is a conflict.
    const response = await applyProgramMutation(() => executeHatgInstruction("ApplyLineageChanges", {
      operations: changes.map(item => ({ op: "update", kind: "component", id: item.id, changes: { "config.failure_recipients": item.recipients } })),
    }, { expectedVersion: revision }), message, { failure: "失败通知没有保存" });
    if (!response) throw new Error(window.PortalI18n.t("失败通知没有保存，请刷新后重试。"));
    // An inspector kept open with an unsaved script draft is not redrawn: show its new
    // recipients and let the draft save on top of this change, which it does not touch.
    document.querySelectorAll("[data-effect-recipients]").forEach(section => {
      const change = changes.find(item => item.id === section.dataset.effectRecipients);
      if (!change) return;
      fields.setRecipientChips(section.querySelector("[data-recipient-chips]"), change.recipients);
      const form = section.closest("form");
      if (form && definitionRevisions.get(form) === revision) definitionRevisions.set(form, state.lineage.revision);
    });
    return response;
  }

  function effectRecipientsChipsHtml(recipients, recipientsState, limit) {
    return `<span class="effectRecipientChips" data-recipient-chips="${esc(JSON.stringify(recipients))}" data-recipients-state="${esc(recipientsState)}" data-recipient-limit="${limit}">${window.PortalLineageElementFields.recipientChipsHtml(recipients, { limit, state: recipientsState })}</span>`;
  }

  function effectRecipientsButtonHtml(effect, { limit = 3 } = {}) {
    return window.PortalI18n.html`<button type="button" class="functionBlockRecipients" data-block-recipients="${esc(effect.id)}" aria-label="${esc(window.PortalI18n.text`修改 ${effect.title || effect.id} 的失败通知`)}" title="修改失败通知收件人"${state.programMutationInFlight ? " disabled" : ""}>${effectRecipientsChipsHtml(effectRecipients(effect), effectRecipientsState(effect), limit)}<span class="functionBlockRecipientsEdit" aria-hidden="true">修改</span></button>`;
  }

  // The overview names who hears about failures, grouped by identical lists; Effects
  // without a list form their own group, apart from those explicitly closed.
  function functionBlockRecipientsSummaryHtml(effects, readonly) {
    if (!effects.length) return "";
    const groups = new Map();
    effects.forEach(effect => {
      const recipients = effectRecipients(effect);
      const recipientsState = effectRecipientsState(effect);
      const key = JSON.stringify([recipientsState, recipients]);
      const group = groups.get(key) || { recipients, recipientsState, items: [] };
      group.items.push(effect);
      groups.set(key, group);
    });
    const rows = [...groups.values()].map(({ recipients, recipientsState, items }) => window.PortalI18n.html`<li data-recipients-group="${esc(recipientsState)}">${effectRecipientsChipsHtml(recipients, recipientsState, 4)}<small title="${esc(items.map(item => item.title || item.id).join("、"))}">${esc(window.PortalI18n.text`${items.length} 个 Effect`)}</small></li>`).join("");
    return window.PortalI18n.html`<section class="functionBlockInstanceBindings functionBlockRecipientsSummary"><span>失败通知</span><ul>${rows}</ul><button class="portalButton" type="button" data-block-recipients-all${readonly ? " disabled" : ""}>${esc(effects.length === 1 ? window.PortalI18n.t("修改收件人…") : window.PortalI18n.text`修改 ${effects.length} 个 Effect 的收件人…`)}</button></section>`;
  }

  function functionBlockObjectStatusHtml(item) {
    if (item.kind === "event_source") {
      const paused = programEventSourceRuntime(item.id).paused;
      return window.PortalI18n.html`<span class="portalStatus" data-tone="${paused ? "warning" : "info"}">${esc(window.PortalI18n.t(paused ? "已暂停" : "监听中"))}</span>`;
    }
    if (item.kind !== "effect") return "";
    const status = programEffectRuntime(item.id).status || "";
    const [tone, label] = status === "executed" ? ["success", "执行成功"] : status === "failed" ? ["danger", "执行失败"] : status === "cancelled" ? ["neutral", "已取消"] : ["neutral", "尚未执行"];
    return window.PortalI18n.html`<span class="portalStatus" data-tone="${tone}">${esc(window.PortalI18n.t(label))}</span>`;
  }

  function functionBlockObjectRowHtml(item, selected, readonly) {
    const title = item.title || item.id;
    const kind = item.kind === "event_source" ? "Event" : item.kind === "effect" ? "Effect" : item.kind;
    const paused = item.kind === "event_source" && programEventSourceRuntime(item.id).paused;
    const action = item.kind === "effect" ? effectRecipientsButtonHtml(item)
      : item.kind === "event_source" ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-block-event-switch="${esc(item.id)}" title="${esc(window.PortalI18n.t(paused ? "恢复监听这个 Event" : "暂停这个 Event"))}"${readonly ? " disabled" : ""}>${esc(window.PortalI18n.t(paused ? "恢复" : "暂停"))}</button>`
      : "";
    return window.PortalI18n.html`<li class="functionBlockObjectRow" data-block-object="${esc(item.id)}" data-object-kind="${esc(item.kind)}"${selected ? ' data-selected="true"' : ""}>
        <input type="checkbox" data-block-object-select="${esc(item.id)}" aria-label="${esc(window.PortalI18n.text`选择 ${title}`)}"${selected ? " checked" : ""}${readonly ? " disabled" : ""}>
        <span class="functionBlockObjectMain"><strong>${esc(title)}</strong><small>${esc(kind)} · ${esc(item.id)}</small></span>
        <span class="functionBlockObjectState">${functionBlockObjectStatusHtml(item)}</span>
        <span class="functionBlockObjectAction">${action}</span>
        <button type="button" class="portalButton is-compact" data-block-object-edit="${esc(item.id)}">设置</button>
      </li>`;
  }

  function familyTaskExecutionHtml(component, readonly, shared, isSource = false) {
    if (!component) return "";
    const event = component.kind === "event_source";
    const effect = component.kind === "effect";
    const paused = event && programEventSourceRuntime(component.id).paused;
    return window.PortalI18n.html`<div class="familyTaskExecutionRow">
      <div class="familyTaskExecutionName"><small>${event ? "触发条件" : isSource ? "前置流程" : "执行动作"}</small><strong>${esc(component.title || component.id)}</strong>${functionBlockObjectStatusHtml(component)}</div>
      <div class="familyTaskExecutionActions">
        <button class="portalButton is-compact" type="button" data-block-object-edit="${esc(component.id)}">${event ? "触发设置" : "执行设置"}</button>
        ${event ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-block-event-switch="${esc(component.id)}"${readonly ? " disabled" : ""}>${paused ? "恢复监听" : "暂停监听"}</button>` : ""}
        ${event && component.config?.event_type === "manual.triggered" ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-function-block-trigger="${esc(component.id)}"${readonly || paused ? " disabled" : ""}>立即触发</button>` : ""}
      </div>
      ${effect ? window.PortalI18n.html`<div class="familyTaskFailureRecipients"><span>失败时通知</span>${effectRecipientsButtonHtml(component)}</div>` : ""}
      ${shared > 1 ? window.PortalI18n.html`<small class="familyTaskShared">${esc(window.PortalI18n.text`此设置由 ${shared} 个场景共用。`)}</small>` : ""}
    </div>`;
  }

  function familyWorkGuidesHtml(block, readonly, drafts = new Map(), expanded = new Set()) {
    if (!familyRoles(block)) return "";
    const wires = new Map((state.lineage?.program?.wires || []).map(item => [item.id, item]));
    const components = new Map((state.lineage?.program?.components || []).map(item => [item.id, item]));
    const groups = { router: [], steward: [] };
    const entries = [];
    for (const element of block.elements || []) {
      const route = wires.get(element.id);
      for (const [profile, config] of Object.entries(route?.config?.invoke_profiles || {})) {
        for (const [trigger, saved] of Object.entries(config.arguments?.family_tasks?.value || {})) {
          if (groups[saved.role]) entries.push({ route, profile, trigger, saved });
        }
      }
    }
    const uses = new Map();
    for (const { route } of entries) {
      for (const id of new Set([route.source, route.target])) uses.set(id, (uses.get(id) || 0) + 1);
    }
    for (const { route, profile, trigger, saved } of entries) {
          const key = `${route.id}:${trigger}`;
          const draft = drafts.get(key);
          const task = { ...saved, ...draft };
          const source = components.get(route.source);
          const target = components.get(route.target);
          groups[task.role].push(window.PortalI18n.html`<details class="familyTaskCard" data-family-scene-key="${esc(key)}"${draft || expanded.has(key) ? " open" : ""}><summary><span>${esc(task.title)}</span>${source?.title && source.title !== task.title ? `<small>${esc(source.title)}</small>` : ""}</summary>
            <div class="familyTaskExecution">${familyTaskExecutionHtml(source, readonly, uses.get(route.source), true)}${target?.id !== source?.id ? familyTaskExecutionHtml(target, readonly, uses.get(route.target)) : ""}</div>
            ${trigger === "progress" ? familyProgressSettingsHtml(block, readonly) : ""}
            <form data-family-task-form data-wire-id="${esc(route.id)}" data-profile="${esc(profile)}" data-trigger="${esc(trigger)}" data-revision="${esc(state.lineage.revision)}">
              <label>工作指南<textarea name="guide" rows="7" required${readonly ? " disabled" : ""}>${esc(task.guide)}</textarea></label>
              <label>每轮任务模板<textarea name="prompt" rows="4" required${readonly ? " disabled" : ""}>${esc(task.prompt)}</textarea></label>
              <small>可用变量：<code>\${facts}</code> 本轮事实、<code>\${guide_path}</code> 阅读路径、<code>\${guide_version}</code> 版本。资料按需读取。</small>
              <button class="portalButton is-primary" type="submit"${readonly ? " disabled" : ""}>保存此场景</button>
              <span data-family-task-feedback role="status"></span>
            </form></details>`);
    }
    return window.PortalI18n.html`<section class="familyWorkGuides"><header><strong>工作流程</strong><p>按场景管理触发、执行、通知和工作指南。首轮发送完整指南，后续只发送本轮任务。</p></header>${Object.entries(groups).map(([role, cards]) => window.PortalI18n.html`<section class="familyWorkRole"><h3>${role === "router" ? "Router · 路由" : "Steward · 组织维护"}</h3>${cards.join("") || "<p>暂无工作场景。</p>"}</section>`).join("")}${functionBlockInstanceTriggersHtml(block, readonly, new Set(uses.keys()))}<footer class="familyWorkflowFooter"><span>全部组件、连接关系与批量操作</span><button class="portalButton" type="button" data-block-open-objects>内部对象</button></footer></section>`;
  }

  function bindFamilyWorkGuideForms(root, draw, drafts = new Map(), expanded = new Set()) {
  root.querySelectorAll("[data-family-scene-key]").forEach(card => card.addEventListener("toggle", () => {
    if (!card.isConnected) return;
    if (card.open) expanded.add(card.dataset.familySceneKey); else expanded.delete(card.dataset.familySceneKey);
  }));
  root.querySelectorAll("[data-family-task-form]").forEach(form => {
      form.addEventListener("input", () => drafts.set(`${form.dataset.wireId}:${form.dataset.trigger}`, {
        guide: form.elements.guide.value, prompt: form.elements.prompt.value,
      }));
      form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const feedback = form.querySelector("[data-family-task-feedback]");
    const template = form.elements.prompt.value;
    const unknown = [...template.matchAll(/\$\{([^}]+)\}/g)].map(match => match[1]).filter(key => !["facts", "guide_path", "guide_version"].includes(key));
    if (unknown.length) { feedback.textContent = `未知变量：${unknown.join("、")}`; return; }
    const route = (state.lineage?.program?.wires || []).find(item => item.id === form.dataset.wireId);
    if (!route) return;
    const profiles = structuredClone(route.config.invoke_profiles);
    const task = profiles[form.dataset.profile].arguments.family_tasks.value[form.dataset.trigger];
    task.guide = form.elements.guide.value;
    task.prompt = template;
    try {
      const response = await applyProgramMutation(() => executeHatgInstruction("ApplyLineageChanges", {
        operations: [{ op: "update", kind: "wire", id: route.id, changes: { "config.invoke_profiles": profiles } }],
      }, { expectedVersion: Number(form.dataset.revision) }), "工作指南已保存，下次触发生效。");
      if (response) { drafts.delete(`${route.id}:${form.dataset.trigger}`); draw(); }
    } catch (error) { feedback.textContent = error.message || String(error); }
  });
    });
  }

  function familyProgressSettingsHtml(block, readonly) {
    const routeId = (block.elements || []).find(item => item.key === "request_progress" && item.kind === "wire")?.id;
    const route = (state.lineage?.program?.wires || []).find(item => item.id === routeId);
    if (!block.family || !route) return "";
    const interval = route.config?.invoke_profiles?.effect?.arguments?.every_turns?.value ?? 20;
    return window.PortalI18n.html`<form class="programInspectorForm" data-family-progress-settings data-wire-id="${esc(route.id)}" data-revision="${esc(state.lineage.revision)}">
      <label><span>进展简报：每几轮请 Steward 汇总一次</span><input name="every_turns" type="number" min="1" step="1" required value="${esc(interval)}"${readonly ? " disabled" : ""}></label>
      <small>按门面收到的新工作输入计数，重试和 info 不重复计数。Steward 撰写最多 500 字的简报并更新门面摘要，不向成员群发；有相关信息时由 Steward 单独通知。</small>
      <button class="portalButton" type="submit"${readonly ? " disabled" : ""}>保存简报设置</button>
    </form>`;
  }

  function functionBlockElementListHtml(block) {
    const rows = functionBlockElements(block).map(element => window.PortalI18n.html`<div><dt>${esc(element.kind)}</dt><dd title="${esc(element.id)}">${esc(graphObjectTitle(element.id))}<small>${esc(element.id)}</small></dd></div>`).join("");
    return window.PortalI18n.html`<section class="functionBlockInstanceBindings functionBlockElementList"><span>元素列表</span><dl>${rows}</dl></section>`;
  }

  function functionBlockOverviewWorkflowHtml(block, readonly, drafts, expanded) {
    if (familyRoles(block)) return familyWorkGuidesHtml(block, readonly, drafts, expanded);
    const effects = instanceInnerObjects(block, "components").filter(item => item.kind === "effect");
    return `${functionBlockRecipientsSummaryHtml(effects, readonly)}${functionBlockInstanceTriggersHtml(block, readonly)}${functionBlockElementListHtml(block)}`;
  }

  function functionBlockObjectsHtml(components, selected, readonly, block) {
    const groups = [
      ["effect", "Effect", components.filter(item => item.kind === "effect")],
      ["event_source", "Event", components.filter(item => item.kind === "event_source")],
      ["other", window.PortalI18n.t("其他组件"), components.filter(item => !["effect", "event_source"].includes(item.kind))],
    ].filter(([, , items]) => items.length);
    const effects = components.filter(item => item.kind === "effect" && selected.has(item.id));
    const events = components.filter(item => item.kind === "event_source" && selected.has(item.id));
    const count = components.filter(item => selected.has(item.id)).length;
    if (!components.length) return window.PortalI18n.html`<p class="functionBlockObjectsEmpty">这个模块里没有组件。</p>`;
    return window.PortalI18n.html`${familyProgressSettingsHtml(block, readonly)}<p class="functionBlockObjectsIntro">折叠时也能直接管理模块内的组件：点 Effect 的收件人修改失败通知，勾选多个对象可以一起处理。</p>
      <div class="functionBlockObjectToolbar" role="toolbar" aria-label="批量操作">
        <label class="functionBlockSelectAll"><input type="checkbox" data-block-select-all${readonly ? " disabled" : ""}><span>${esc(count ? window.PortalI18n.text`已选 ${count} 项` : window.PortalI18n.t("全选"))}</span></label>
        <span class="functionBlockBulkButtons">
          <button class="portalButton is-compact" type="button" data-block-bulk-recipients${readonly || !effects.length ? " disabled" : ""}>${esc(window.PortalI18n.text`设置失败通知（${effects.length}）`)}</button>
          <button class="portalButton is-compact" type="button" data-block-events-pause${readonly || !events.length ? " disabled" : ""}>${esc(window.PortalI18n.text`暂停事件（${events.length}）`)}</button>
          <button class="portalButton is-compact" type="button" data-block-events-resume${readonly || !events.length ? " disabled" : ""}>${esc(window.PortalI18n.text`恢复事件（${events.length}）`)}</button>
        </span>
      </div>
      ${groups.map(([key, label, items]) => window.PortalI18n.html`<section class="functionBlockObjectGroup" data-object-group="${key}"><h4><span>${esc(label)}<small>${items.length}</small></span>${key === "effect" ? window.PortalI18n.html`<span class="functionBlockObjectColumn">失败通知</span>` : ""}</h4><ul>${items.map(item => functionBlockObjectRowHtml(item, selected.has(item.id), readonly)).join("")}</ul></section>`).join("")}
      ${familyRoles(block) ? window.PortalI18n.html`<details class="functionBlockObjectInventory"><summary>查看完整元素列表</summary>${functionBlockElementListHtml(block)}</details>` : ""}`;
  }

  function renderFunctionBlockMenuPanel(blockId, root, close, setLabel, pane = "block") {
    let titleDraft;
    const taskDrafts = new Map();
    const expandedTasks = new Set();
    let editingId = "";
    const selected = new Set();
    const editor = document.createElement("section");
    editor.className = "functionBlockObjectEditor";
    const draw = () => {
      const block = functionBlockInstanceById(blockId);
      if (!block) { root.textContent = window.PortalI18n.t("这个 Function Block 已不在 Lineage 中。"); return; }
      setLabel(pane === "objects" ? window.PortalI18n.t("内部对象") : pane === "issues" ? window.PortalI18n.t("问题") : block.title || window.PortalI18n.t("Function Block"));
      const roles = familyRoles(block);
      const readonly = state.programMutationInFlight;
      const failures = functionBlockInstanceFailures(block);
      const components = instanceInnerObjects(block, "components");
      const effects = components.filter(item => item.kind === "effect" && selected.has(item.id));
      const events = components.filter(item => item.kind === "event_source" && selected.has(item.id));
      const pending = new Set(functionBlockInstanceUnacknowledgedFailures(block).map(issue => issue.id));
      const blockEffects = components.filter(item => item.kind === "effect");
      const issueRows = failures.map(issue => window.PortalI18n.html`<article class="functionBlockIssue" data-block-issue="${esc(issue.id)}"><header><strong>${esc(issue.title || issue.id)}</strong><span>${esc(window.PortalI18n.t(pending.has(issue.id) ? "未查看" : "已查看"))}</span></header><small>${esc(issue.kind)} · ${esc(issue.id)}${issue.at ? ` · ${esc(formatDate(issue.at))}` : ""}</small><p>${esc(issue.error)}</p>${issue.kind === "effect" ? window.PortalI18n.html`<div class="functionBlockIssueRecipients"><span>失败通知</span>${effectRecipientsButtonHtml(issue, { limit: 4 })}</div>` : ""}<div>${issue.kind === "resident" ? window.PortalI18n.html`<button class="portalButton" type="button" data-function-block-chat="${esc(issue.id)}">查看对话</button>` : window.PortalI18n.html`<button class="portalButton" type="button" data-block-object-edit="${esc(issue.id)}">查看设置与运行记录</button>`}</div></article>`).join("");
      root.innerHTML = window.PortalI18n.html`<div class="portalHatgOverlayComponent functionBlockMenuPanel" data-function-block-menu-panel="${esc(block.id)}"><header class="functionBlockMenuIdentity"><strong>${esc(block.title || window.PortalI18n.t("Function Block"))}</strong><code>${esc(block.id)}</code></header><div class="programInspectorForm functionBlockInstanceInspector">
        ${pane === "block" ? window.PortalI18n.html`<label>标题<input data-function-block-title value="${esc(titleDraft ?? block.title)}"${readonly ? " disabled" : ""}></label>
        <button class="portalButton is-primary" type="button" data-function-block-rename${readonly ? " disabled" : ""}>保存标题</button>
        ${failures.length ? window.PortalI18n.html`<p data-function-block-failure>内部出错：${esc(failures.map(component => component.title || component.id).join("、"))}</p>` : ""}
        <section class="functionBlockInstanceBindings"><span>来源模板</span><p>${esc(functionBlockSourceTitle(block))}</p>${block.template_id ? '<button class="portalButton" type="button" data-function-block-source>查看来源模板</button>' : ""}</section>
        ${functionBlockInsideResidentsHtml(block)}
        ${functionBlockOverviewWorkflowHtml(block, readonly, taskDrafts, expandedTasks)}
        <div class="programInspectorActions functionBlockInstanceActions">
          <button class="portalButton" type="button" data-function-block-save-template${readonly ? " disabled" : ""}>存为模板</button>
          ${roles ? window.PortalI18n.html`<button class="portalButton" type="button" data-family-fold>${familyExpanded(roles) ? window.PortalI18n.t("折叠为门面") : window.PortalI18n.t("展开成员")}</button><button class="portalButton" type="button" data-family-unfamilize${readonly ? " disabled" : ""}>Unfamilize</button>`
          : window.PortalI18n.html`<button class="portalButton" type="button" data-function-block-expand>${expandedFunctionBlockIds().has(block.id) ? window.PortalI18n.t("折叠") : window.PortalI18n.t("展开")}</button><button class="portalButton" type="button" data-function-block-unfunctionalize${readonly ? " disabled" : ""}>Unfunctionalize</button><button class="portalButton is-danger" type="button" data-function-block-delete${readonly ? " disabled" : ""}>删除</button>`}
        </div>` : pane === "objects" ? functionBlockObjectsHtml(components, selected, readonly, block) : window.PortalI18n.html`<p>打开本页会自动消除当前错误提醒，保留错误与运行记录；新的失败会再次提示。成功运行或修正配置后，对应问题会自动移出当前清单。</p>${functionBlockInsideResidentsHtml(block)}${issueRows || '<p data-block-no-issues>当前没有问题。</p>'}`}
        <div data-block-object-editor></div>
      </div></div>`;
      const action = (selector, callback, closing = true) => root.querySelector(selector)?.addEventListener("click", () => {
        if (closing) close();
        Promise.resolve().then(callback).catch(error => toast(error.message || String(error), "error"));
      });
      root.querySelector("[data-function-block-title]")?.addEventListener("input", event => { titleDraft = event.target.value; });
      action("[data-function-block-rename]", async () => {
        const title = root.querySelector("[data-function-block-title]").value.trim();
        const result = await applyProgramMutation(() => executeHatgInstruction(
          roles ? "UpdateFamily" : "UpdateFunctionBlock",
          roles ? { family_id: block.id, title } : { function_block_id: block.id, title },
        ), "Function Block 标题已保存。");
        if (result) { titleDraft = undefined; draw(); }
      }, false);
      action("[data-function-block-source]", () => templateController.openEditor(block.template_id));
      action("[data-block-open-objects]", () => openFunctionBlockMenu(block.id, "objects"));
      action("[data-function-block-save-template]", async () => {
        await templateController.saveSelection([block.id]);
        toast(window.PortalI18n.t("已存为模板。"), "success");
      });
      action("[data-function-block-expand]", () => setFunctionBlockExpanded(block.id, !expandedFunctionBlockIds().has(block.id)));
      action("[data-function-block-unfunctionalize]", () => unfunctionalize(block.id));
      action("[data-function-block-delete]", () => deleteFunctionBlock(block.id));
      action("[data-family-unfamilize]", () => removeFamilyInstance(block.id));
      action("[data-family-fold]", () => { const family = familyForFacade(roles.facade); if (family) toggleFamilyView(family); });
      root.querySelectorAll("[data-function-block-trigger]").forEach(button => button.addEventListener("click", () => {
        emitManualProgramEvent(button.dataset.functionBlockTrigger, button).catch(() => {});
      }));
      root.querySelectorAll("[data-function-block-chat]").forEach(button => button.addEventListener("click", () => {
        close(); selectNode(button.dataset.functionBlockChat, { force: true, openChat: true }).catch(() => {});
      }));
      root.querySelectorAll("[data-block-object-edit]").forEach(button => button.addEventListener("click", () => {
        editingId = button.dataset.blockObjectEdit;
        editor.innerHTML = "";
        draw();
        editor.scrollIntoView({ block: "start", behavior: "smooth" });
      }));
      bindFamilyWorkGuideForms(root, draw, taskDrafts, expandedTasks);
      root.querySelector("[data-family-progress-settings]")?.addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget;
        if (!form.reportValidity()) return;
        const interval = Number(form.elements.every_turns.value);
        if (!Number.isSafeInteger(interval) || interval < 1) return;
        const route = (state.lineage?.program?.wires || []).find(item => item.id === form.dataset.wireId);
        if (!route) return;
        const profiles = structuredClone(route.config.invoke_profiles);
        profiles.effect.arguments.every_turns = { value: interval };
        const response = await applyProgramMutation(() => executeHatgInstruction("ApplyLineageChanges", {
          operations: [{ op: "update", kind: "wire", id: form.dataset.wireId,
            changes: { "config.invoke_profiles": profiles } }],
        }, { expectedVersion: Number(form.dataset.revision) }), "简报设置已保存。");
        if (response) draw();
      });
      root.querySelectorAll("[data-block-object-select]").forEach(input => input.addEventListener("change", () => {
        if (input.checked) selected.add(input.dataset.blockObjectSelect); else selected.delete(input.dataset.blockObjectSelect);
        draw();
      }));
      const selectAll = root.querySelector("[data-block-select-all]");
      if (selectAll) {
        const count = components.filter(item => selected.has(item.id)).length;
        selectAll.checked = count > 0 && count === components.length;
        selectAll.indeterminate = count > 0 && count < components.length;
        selectAll.addEventListener("change", () => {
          if (selectAll.checked) components.forEach(item => selected.add(item.id)); else selected.clear();
          draw();
        });
      }
      root.querySelectorAll("[data-block-recipients]").forEach(button => button.addEventListener("click", () => openEffectRecipientsEditor([button.dataset.blockRecipients])));
      action("[data-block-bulk-recipients]", () => openEffectRecipientsEditor(effects.map(item => item.id)), false);
      action("[data-block-recipients-all]", () => openEffectRecipientsEditor(blockEffects.map(item => item.id)), false);
      const changeEvents = async (pause, items) => {
        const errors = [];
        for (const item of items) {
          try { await applyProgramMutation(() => executeHatgInstruction("PauseComponent", { component_id: item.id, paused: pause }, { definition: false }), null); }
          catch (error) { errors.push(`${item.title || item.id}：${error.message}`); }
        }
        const done = items.length - errors.length;
        toast(errors.length
          ? (pause ? window.PortalI18n.text`已暂停 ${done} 个事件；失败：${errors.join("；")}` : window.PortalI18n.text`已恢复 ${done} 个事件；失败：${errors.join("；")}`)
          : (pause ? window.PortalI18n.text`已暂停 ${done} 个事件。` : window.PortalI18n.text`已恢复 ${done} 个事件。`), errors.length ? "error" : "success");
      };
      action("[data-block-events-pause]", () => changeEvents(true, events), false);
      action("[data-block-events-resume]", () => changeEvents(false, events), false);
      root.querySelectorAll("[data-block-event-switch]").forEach(button => button.addEventListener("click", () => {
        const item = components.find(component => component.id === button.dataset.blockEventSwitch);
        if (!item) return;
        changeEvents(!programEventSourceRuntime(item.id).paused, [item])
          .catch(error => toast(error.message || String(error), "error"));
      }));
      window.PortalLineageElementFields.hydrateRecipientChips(root);
      if (editingId && components.some(item => item.id === editingId)) {
        root.querySelector("[data-block-object-editor]").append(editor);
        renderProgramInspector({ panel: editor, objectId: editingId, onClose: () => { editingId = ""; draw(); }, onSaved: () => { editor.querySelector("form")?.setAttribute("data-program-draft-dirty", "false"); draw(); } });
      }
    };
    draw();
    functionBlockMenuRefresh = draw;
    if (pane === "issues") {
      const unseen = functionBlockInstanceUnacknowledgedFailures(functionBlockInstanceById(blockId));
      if (unseen.length) acknowledgeFunctionBlockFailures(unseen);
    }
    return () => { if (functionBlockMenuRefresh === draw) functionBlockMenuRefresh = null; };
  }

  async function openFunctionalize() {
    if (!state.lineage) return;
    const elementIds = [...state.selectedCanvasObjectIds];
    if (!elementIds.length) return;
    const title = await window.PortalUI.prompt({
      title: "Functionalize", message: window.PortalI18n.t("为选区命名"),
      value: "Function Block", confirmLabel: "Functionalize",
    });
    if (title === null || title === undefined) return;
    // A refresh/collapse while the prompt was open must not silently change
    // the operation the user confirmed. Keep the service's ownership checks.
    const visibleIds = visibleCanvasObjectIds();
    if (elementIds.some(id => !visibleIds.has(id))) {
      throw new Error(window.PortalI18n.t("选区已变化，请重新选择要封装的元素。"));
    }
    await applyProgramMutation(() => executeHatgInstruction("Functionalize", {
      element_ids: [...new Set(elementIds)], title: title.trim() || "Function Block",
    }), "选区已封装为 Function Block。");
    clearCanvasSelection();
  }

  async function unfunctionalize(id) {
    const block = functionBlockInstanceById(id);
    if (!block || block.family) return;
    await applyProgramMutation(() => executeHatgInstruction("Unfunctionalize", { function_block_id: id }), "Function Block 已拆开。");
    state.selectedProgramObjectId = "";
    programFeature.clearSelection();
  }

  async function deleteFunctionBlock(id) {
    const block = functionBlockInstanceById(id);
    if (!block || block.family) return;
    const residents = functionBlockElements(block).filter(element => element.kind === "resident");
    const details = residents.map(element => `${graphObjectTitle(element.id)} (${element.id})`).join("\n");
    const confirmed = await window.PortalUI.confirm({
      title: window.PortalI18n.t("删除 Function Block？"),
      message: window.PortalI18n.text`将删除「${block.title}」及其全部元素。将删除的 Resident：\n${details || window.PortalI18n.t("无")}`,
      confirmLabel: window.PortalI18n.t("删除"), tone: "danger",
    });
    if (!confirmed) return;
    await applyProgramMutation(() => executeHatgInstruction("DeleteFunctionBlock", { function_block_id: id }), "Function Block 已删除。");
    state.selectedProgramObjectId = "";
    programFeature.clearSelection();
  }

  // Templates use the same selection and coordinate system as canvas gestures.
  function templateCanvasTarget(event) {
    const position = state.graphController?.screenToPosition?.(event.clientX, event.clientY) || { x: 50, y: 50 };
    const hit = event.target?.closest?.("[data-node-id], [data-edge-id], [data-function-block-boundary]");
    const id = hit?.dataset.nodeId || hit?.dataset.edgeId || hit?.dataset.functionBlockBoundary || "";
    if (state.selectedCanvasObjectIds.has(id)) {
      const element_ids = [...state.selectedCanvasObjectIds];
      return { kind: "selection", element_ids, resident_ids: element_ids.filter(id => Boolean(nodeById(id))), ...position };
    }
    if (nodeById(id)) return { kind: "resident", resident_ids: [id], ...position };
    return { kind: "canvas", ...position };
  }

  function renderProgramInspector({ panel = $id("programInspectorPanel"), objectId = state.selectedProgramObjectId, onClose = null, onSaved = null } = {}) {
    if (!panel) return;
    const component = objectId ? displayProgramComponentById(objectId) : null;
    const wire = objectId ? displayProgramWireById(objectId) : null;
    const readonly = state.programMutationInFlight;
    const openInspectorDraft = panel.querySelector(
      "[data-program-component-form], [data-program-operator-form]",
    );
    const draftObjectId = component?.id || "";
    if (
      draftObjectId
      && panel.dataset.componentId === draftObjectId
      && openInspectorDraft?.dataset.programDraftDirty === "true"
    ) {
      openInspectorDraft.querySelectorAll("input, textarea, select, button").forEach(control => {
        if (readonly) {
          if (!inspectorDisabledBeforeMutation.has(control)) {
            inspectorDisabledBeforeMutation.set(control, control.disabled);
          }
          control.disabled = true;
        } else if (inspectorDisabledBeforeMutation.has(control)) {
          control.disabled = inspectorDisabledBeforeMutation.get(control);
          inspectorDisabledBeforeMutation.delete(control);
        }
      });
      syncProgramComponentSwitch(panel, component);
      return;
    }
    // Function Blocks use their shared instance Menu.
    if (!onClose) functionBlockMenuRefresh?.();
    panel.hidden = !(component || wire);
    panel.setAttribute("aria-hidden", panel.hidden ? "true" : "false");
    panel.removeAttribute("data-inspector-kind");
    panel.removeAttribute("data-component-id");
    panel.removeAttribute("data-wire-id");
    if (panel.hidden) {
      panel.innerHTML = "";
      return;
    }

    if (component?.kind === "seed") {
      const config = component.config || {};
      panel.dataset.inspectorKind = "seed";
      panel.dataset.componentId = component.id;
      panel.innerHTML = window.PortalI18n.html`<div class="programInspectorHead">
          <span><em>SEED</em><strong>配置候选工作</strong></span>
          <button type="button" data-program-inspector-close aria-label="关闭 Seed 设置" data-portal-icon="close"></button>
        </div>
        <form class="programInspectorForm" data-program-component-form>
          ${programResourceAddressHtml("component", component.id)}
          <label><span>名称</span><input name="title" maxlength="120" value="${esc(component.title || "")}" required${readonly ? " disabled" : ""}></label>
          <label><span>候选类型</span><select name="seed_type"${readonly ? " disabled" : ""}><option value="idea"${config.seed_type === "idea" ? " selected" : ""}>想法</option><option value="plan"${config.seed_type === "plan" ? " selected" : ""}>方案</option><option value="next_step"${config.seed_type === "next_step" ? " selected" : ""}>下一步</option><option value="other"${!["idea", "plan", "next_step"].includes(config.seed_type) ? " selected" : ""}>其他</option></select></label>
          <label><span>候选摘要</span><textarea name="summary" maxlength="800" rows="5"${readonly ? " disabled" : ""}>${esc(config.summary || "")}</textarea></label>
          <label><span>候选文件</span><input name="artifact_path" maxlength="800" value="${esc(config.artifact_path || "")}" placeholder="相对 Lineage 目录的路径（可选）"${readonly ? " disabled" : ""}></label>
          ${seedCandidateForkFieldHtml(component, readonly)}
          <dl class="programInspectorLifecycle"><div><dt>状态</dt><dd>${esc(config.status || "active")}</dd></div><div><dt>身份</dt><dd>Program component（尚非 Resident）</dd></div></dl>
          <p class="programInspectorNote">Seed 是 HATG Program 中的候选节点。Packet 可以流入这里；只有执行 PromoteSeed 指令后才会创建 Resident。</p>
          <div class="programInspectorActions" data-portal-inbox-avoid>
            <button type="button" data-program-delete${readonly ? " disabled" : ""}>删除</button>
            <button type="submit" data-program-save${readonly ? " disabled" : ""}>保存 Seed</button>
          </div>
        </form>`;
      panel.querySelector("[data-program-component-form]")?.addEventListener("submit", event => {
        event.preventDefault();
        saveProgramComponentFromInspector(component.id, event.currentTarget, { onSaved }).catch(() => {});
      });
      panel.querySelector("[data-seed-candidate-fork]")?.addEventListener("change", event => {
        setSeedCandidateFork(component.id, event.currentTarget.value).catch(() => {});
      });
      panel.querySelector("[data-program-delete]")?.addEventListener("click", () => {
        deleteProgramComponent(component.id).catch(() => {});
      });
    } else if (component?.kind === "loop") {
      const config = component.config || {};
      const runtime = state.lineage?.program_loop_runtime?.[component.id] || {};
      panel.dataset.inspectorKind = "loop";
      panel.dataset.componentId = component.id;
      panel.innerHTML = window.PortalI18n.html`<div class="programInspectorHead">
          <span><em>LOOP</em><strong>循环</strong></span>
          <button type="button" data-program-inspector-close aria-label="关闭循环设置" data-portal-icon="close"></button>
        </div>
        <form class="programInspectorForm" data-program-component-form>
          ${programResourceAddressHtml("component", component.id)}
          <label><span>名称</span><input name="title" maxlength="120" value="${esc(component.title || "")}" required${readonly ? " disabled" : ""}></label>
          ${window.PortalLineageElementFields.loopFieldsHtml(config, readonly)}
          <div class="programInspectorActions" data-portal-inbox-avoid data-loop-runtime-actions>
            <button type="button" data-loop-refresh>读取运行状态</button>
            <button type="button" data-loop-pause${readonly ? " disabled" : ""}>暂停</button>
            <button type="button" data-loop-resume${readonly ? " disabled" : ""}>恢复</button>
            <button type="button" data-loop-reset${readonly ? " disabled" : ""}>重新计数</button>
          </div>
          <dl class="programInspectorLifecycle" data-loop-runtime>${loopRuntimeRowsHtml(runtime)}</dl>
          ${programExecutionExplanationHtml(component.id)}
          <div class="programInspectorActions" data-portal-inbox-avoid>
            <button type="button" data-program-delete${readonly ? " disabled" : ""}>删除</button>
            <button type="submit" data-program-save${readonly ? " disabled" : ""}>保存循环</button>
          </div>
        </form>`;
      const runtimeSlot = panel.querySelector("[data-loop-runtime]");
      const lineageId = state.lineage?.lineage_id || "";
      const refreshRuntime = async () => {
        const result = await societyService.getComponentRuntime({ lineage_id: lineageId, component_id: component.id });
        if (runtimeSlot && panel.contains(runtimeSlot)) runtimeSlot.innerHTML = loopRuntimeRowsHtml((result?.components || [])[0] || {});
      };
      const runtimeAction = (method, message) => async () => {
        try {
          await applyProgramMutation(() => societyService[method]({ lineage_id: lineageId, component_id: component.id }), message);
          await refreshRuntime();
        } catch {}
      };
      panel.querySelector("[data-loop-refresh]")?.addEventListener("click", () => refreshRuntime().catch(error => toast(error.message || String(error), "error")));
      panel.querySelector("[data-loop-pause]")?.addEventListener("click", runtimeAction("pauseComponent", "循环已暂停。"));
      panel.querySelector("[data-loop-resume]")?.addEventListener("click", runtimeAction("resumeComponent", "循环已恢复。"));
      panel.querySelector("[data-loop-reset]")?.addEventListener("click", runtimeAction("resetProgramEvent", "循环已重新计数。"));
      refreshRuntime().catch(() => {});
      panel.querySelector("[data-program-component-form]")?.addEventListener("submit", event => {
        event.preventDefault();
        saveProgramComponentFromInspector(component.id, event.currentTarget, { onSaved }).catch(() => {});
      });
      panel.querySelector("[data-program-delete]")?.addEventListener("click", () => {
        deleteProgramComponent(component.id).catch(() => {});
      });
    } else if (component?.kind === "event_source") {
      const hasCanonicalConfig = Boolean(
        component.config
        && typeof component.config === "object"
        && !Array.isArray(component.config),
      );
      const config = hasCanonicalConfig ? component.config : {};
      const eventType = config.event_type || programPrimitiveDefaultType("event");
      const sourceRuntime = programEventSourceRuntime(component.id);
      const maximum = normalizeOptionalLimit(
        sourceRuntime.max_fires ?? config.max_fires,
        100000,
      );
      const hasMaximum = maximum !== null;
      const occurrenceCount = Math.max(
        0,
        Number(sourceRuntime.occurrence_count || 0),
      );
      const lastDelivery = sourceRuntime.last_delivery || {};
      const deliveryParts = [
        Number(lastDelivery.delivered_count || 0) > 0
          ? `送达 ${Number(lastDelivery.delivered_count)} 条`
          : "",
        Number(lastDelivery.queued_count || 0) > 0
          ? `暂存 ${Number(lastDelivery.queued_count)} 条`
          : "",
        Number(lastDelivery.dropped_count || 0) > 0
          ? `丢弃 ${Number(lastDelivery.dropped_count)} 条`
          : "",
        Number(lastDelivery.failed_count || 0) > 0
          ? `失败 ${Number(lastDelivery.failed_count)} 条`
          : "",
      ].filter(Boolean);
      const deliveryRuntimeRow = Object.keys(lastDelivery).length
        ? window.PortalI18n.html`<div data-event-last-delivery><dt>最近投递</dt><dd title="${esc(lastDelivery.at ? formatDate(lastDelivery.at) : "")}">${esc(deliveryParts.join(" · ") || window.PortalI18n.t("没有匹配的下游"))}</dd></div>`
        : "";
      const lastTriggerPathId = String(lastDelivery.trigger_path_id || "");
      const lastEventId = String(lastDelivery.event_id || "");
      const pathIdentityRows = lastTriggerPathId || lastEventId
        ? `${lastTriggerPathId ? window.PortalI18n.html`<div data-event-last-trigger-path><dt>最近 Trigger Path</dt><dd><code>${esc(lastTriggerPathId)}</code></dd></div>` : ""}
          ${lastEventId ? window.PortalI18n.html`<div data-event-last-event><dt>Event</dt><dd><code>${esc(lastEventId)}</code></dd></div>` : ""}`
        : "";
      const predicateLabel = ({
        "resident.work_duration_exceeded": window.PortalI18n.t("本轮是否超时"),
        "resident.queue_pressure_exceeded": window.PortalI18n.t("当前是否越界"),
        "state.condition_met": window.PortalI18n.t("条件是否成立"),
      })[eventType];
      const predicateRuntimeRow = predicateLabel
        ? window.PortalI18n.html`<div><dt>${predicateLabel}</dt><dd>${sourceRuntime.baseline_established ? (sourceRuntime.condition_met ? window.PortalI18n.t("是") : window.PortalI18n.t("否")) : window.PortalI18n.t("等待首次采样")}</dd></div>`
        : "";
      const exhaustionNote = sourceRuntime.exhausted && config.auto_delete_on_exhaustion
        ? window.PortalI18n.html`<p class="programInspectorNote" data-event-auto-delete-pending>次数已耗尽，相关在途调用处理完后将自动删除此 Event。${maximum > 0 ? window.PortalI18n.t("刷新次数可以取消本轮待删除状态。") : ""}</p>`
        : "";
      panel.dataset.inspectorKind = "event_source";
      panel.dataset.componentId = component.id;
      panel.innerHTML = window.PortalI18n.html`<div class="programInspectorHead">
          <span><em>EVENT SOURCE</em><strong>配置触发事件</strong></span>
          <button type="button" data-program-inspector-close aria-label="关闭原语设置" data-portal-icon="close"></button>
        </div>
        <form class="programInspectorForm" data-program-component-form>
          ${programComponentSwitchHtml(component, readonly)}
          ${programResourceAddressHtml("component", component.id)}
          <label><span>名称</span><input name="title" maxlength="120" value="${esc(component.title || "")}" required${readonly ? " disabled" : ""}></label>
          ${programPrimitiveInspectorHtml(component, "event", eventType, config, readonly)}
          <section class="programInspectorNote" data-event-run-as>
            <p data-event-run-as-note></p>
            <button type="button" data-event-run-as-settings${readonly ? " disabled" : ""}>设置自动运行 User</button>
          </section>
          ${exhaustionNote}
          ${programExecutionExplanationHtml(component.id)}
          <dl class="programInspectorLifecycle"><div><dt>监听状态</dt><dd data-event-listening-status>${esc([window.PortalI18n.t(programEventSourceStatus(component.id).label), programEventSourceStatus(component.id).reason].filter(Boolean).join(" · "))}</dd></div><div><dt>已触发</dt><dd>${occurrenceCount}${hasMaximum ? ` / ${maximum}` : window.PortalI18n.t(" / 不限")}</dd></div><div><dt>剩余</dt><dd>${hasMaximum ? Math.max(0, maximum - occurrenceCount) : window.PortalI18n.t("不限")}</dd></div>${deliveryRuntimeRow}${pathIdentityRows}${eventType === "residents.all_idle" ? window.PortalI18n.html`<div><dt>当前未完成</dt><dd>${sourceRuntime.baseline_established ? Math.max(0, Number(sourceRuntime.running_count || 0)) : window.PortalI18n.t("等待首次采样")}</dd></div>` : ""}${eventType === "resident.context_pressure_exceeded" ? window.PortalI18n.html`<div><dt>当前压力</dt><dd>${sourceRuntime.baseline_established && sourceRuntime.used_percent !== null && sourceRuntime.used_percent !== undefined ? `${Number(sourceRuntime.used_percent).toFixed(1)}%` : window.PortalI18n.t("等待首次采样")}</dd></div>` : ""}${predicateRuntimeRow}</dl>
          ${programTriggerPathObservationHtml(lastTriggerPathId)}
          <p class="programInspectorNote">事件来源只决定何时触发；发送给下游的内容和排队方式由每条事件连线分别设置。</p>
          <div class="programInspectorActions" data-portal-inbox-avoid>
            <button type="button" data-program-delete${readonly ? " disabled" : ""}>删除</button>
            <button type="button" data-program-event-reset${sourceRuntime.exhausted && maximum !== 0 ? "" : " hidden"}${readonly ? " disabled" : ""}>刷新次数</button>
            <button type="button" data-program-emit${readonly || sourceRuntime.exhausted || sourceRuntime.paused ? " disabled" : ""}>立即触发</button>
            <button type="submit" data-program-save${readonly ? " disabled" : ""}>保存事件</button>
          </div>
        </form>`;
      const eventConfigChanged = () => {
        syncProgramEventInspectorFields(panel);
      };
      bindProgramPrimitiveInspector(
        panel,
        component,
        "event",
        readonly,
        {
          onChange: eventConfigChanged,
          onTypeChange: () => {
            syncProgramEventInspectorFields(panel);
          },
        },
      );
      syncProgramEventInspectorFields(panel);
      panel.querySelector("[data-event-run-as-settings]")?.addEventListener("click", () => {
        openEditLineageModal();
        $id("lineageRunAsInput").focus();
      });
      if (!hasCanonicalConfig) {
        const emit = panel.querySelector("[data-program-emit]");
        if (emit) emit.hidden = true;
      }
      panel.querySelector("[data-program-component-form]")?.addEventListener("submit", event => {
        event.preventDefault();
        saveProgramComponentFromInspector(component.id, event.currentTarget, { onSaved }).catch(() => {});
      });
      panel.querySelector("[data-program-delete]")?.addEventListener("click", () => {
        deleteProgramComponent(component.id).catch(() => {});
      });
      panel.querySelector("[data-program-emit]")?.addEventListener("click", event => {
        emitManualProgramEvent(component.id, event.currentTarget).catch(() => {});
      });
      panel.querySelector("[data-program-event-reset]")?.addEventListener("click", event => {
        resetProgramEventSource(component.id, event.currentTarget).catch(() => {});
      });
      bindProgramTriggerPathObservation(panel, lastTriggerPathId);
      syncManualProgramEventInspectorBusy(component.id);
    } else if (component?.kind === "effect") {
      const config = component.config || {};
      panel.dataset.inspectorKind = "effect";
      panel.dataset.componentId = component.id;
      panel.innerHTML = window.PortalI18n.html`<div class="programInspectorHead">
          <span><em>EFFECT</em><strong>运行指令脚本</strong></span>
          <button type="button" data-program-inspector-close aria-label="关闭原语设置" data-portal-icon="close"></button>
        </div>
        <div class="programEffectDiagnostics" data-effect-diagnostics aria-live="polite"></div>
        <form class="programInspectorForm" data-program-component-form>
          ${programComponentSwitchHtml(component, readonly)}
          ${programResourceAddressHtml("component", component.id)}
          <label><span>名称</span><input name="title" maxlength="120" value="${esc(component.title || "")}" required${readonly ? " disabled" : ""}></label>
          ${window.PortalLineageElementFields.effectFieldsHtml(config, readonly, { effectId: component.id })}
          ${programExecutionExplanationHtml(component.id)}
          ${programEffectRuntimeHtml(component.id)}
          <div class="programInspectorActions" data-portal-inbox-avoid>
            <button type="button" data-program-delete${readonly ? " disabled" : ""}>删除</button>
            <button type="submit" data-program-save${readonly ? " disabled" : ""}>保存动作</button>
          </div>
        </form>`;
      window.PortalLineageElementFields.bindEffectFields(panel, {
        notify: message => toast(message, "warning", 6200),
      });
      panel.querySelector("[data-effect-recipients-edit]")?.addEventListener("click", () => openEffectRecipientsEditor([component.id]));
      renderEffectDiagnostics(panel, component.id).catch(() => {});
      bindProgramTriggerPathObservation(panel, programEffectRuntime(component.id).trigger_path_id);
      panel.querySelector("[data-program-component-form]")?.addEventListener("submit", event => {
        event.preventDefault();
        saveProgramComponentFromInspector(component.id, event.currentTarget, { onSaved }).catch(() => {});
      });
      panel.querySelector("[data-program-delete]")?.addEventListener("click", () => {
        deleteProgramComponent(component.id).catch(() => {});
      });
    } else if (isPacketOperatorComponent(component)) {
      const config = component.config || {};
      const implementation = packetOperatorImplementation(config, component.kind);
      const executorId = String(config.resident_executor_id || "");
      const executor = executorId ? displayNodeById(executorId) : null;
      panel.dataset.inspectorKind = component.kind;
      panel.dataset.componentId = component.id;
      panel.innerHTML = window.PortalI18n.html`<div class="programInspectorHead">
          <span><em>${esc(component.kind.toUpperCase())} OPERATOR</em><strong>${esc(component.title || component.id)}</strong></span>
          <button type="button" data-program-inspector-close aria-label="关闭 Packet Operator 设置" data-portal-icon="close"></button>
        </div>
        <form class="programInspectorForm programOperatorComponentForm" data-program-operator-form>
          ${programResourceAddressHtml("component", component.id)}
          <label><span>名称</span><input name="title" maxlength="120" value="${esc(component.title || "")}" required${readonly ? " disabled" : ""}></label>
          <dl class="programInspectorLifecycle programOperatorIdentity"><div><dt>图身份</dt><dd><code>${esc(component.id)}</code></dd></div><div><dt>执行器</dt><dd>${implementation === "rules" ? window.PortalI18n.t("确定性规则（无 Chat）") : executor ? window.PortalI18n.text`${esc(executor.title || executorId)} · 可对话` : window.PortalI18n.t("保存后创建 Resident 执行器")}</dd></div></dl>
          ${window.PortalLineageElementFields.operatorFieldsHtml(component, readonly)}
          ${programExecutionExplanationHtml(component.id)}
          <div class="programInspectorActions" data-portal-inbox-avoid>
            <button type="button" data-program-delete${readonly ? " disabled" : ""}>删除 ${esc(component.kind)}</button>
            <button type="submit" data-program-save${readonly ? " disabled" : ""}>保存 ${esc(component.kind)}</button>
          </div>
        </form>`;
      const operatorForm = panel.querySelector("[data-program-operator-form]");
      window.PortalLineageElementFields.bindOperatorFields(operatorForm, readonly, {
        busy: () => state.programMutationInFlight,
      });
      operatorForm?.addEventListener("submit", event => {
        event.preventDefault();
        saveProgramComponentFromInspector(component.id, event.currentTarget, { onSaved }).catch(() => {});
      });
      panel.querySelector("[data-program-delete]")?.addEventListener("click", () => {
        deleteProgramComponent(component.id).catch(() => {});
      });
    } else if (wire) {
      panel.dataset.inspectorKind = "wire";
      panel.dataset.wireId = wire.id;
      const wireNote = wire.type === "route"
        ? "Route 传递消息、事件或两者组成的路由包。参数映射、排队策略和过期时间属于这条路线；筛选、分支和合并由相应原语处理。"
        : wire.type === "target"
          ? "Target 将 Effect 的具名端点绑定到对象，不触发执行。"
          : "Watch 绑定 Event Source 观察的对象。";
      panel.innerHTML = window.PortalI18n.html`<div class="programInspectorHead">
          <span><em>${esc(String(wire.type || "").toUpperCase())} WIRE</em><strong>配置连线</strong></span>
          <button type="button" data-program-inspector-close aria-label="关闭连线设置" data-portal-icon="close"></button>
        </div>
        <form class="programInspectorForm" data-program-wire-form>
          ${programResourceAddressHtml("wire", wire.id)}
          <label><span>标签</span><input name="label" maxlength="120" value="${esc(wire.label || "")}"${readonly ? " disabled" : ""}></label>
          <label><span>${programWireTargetLabel(wire)}</span><input name="target_endpoint_id" list="programWireTargetOptions" maxlength="110" autocomplete="off" spellcheck="false" placeholder="${programWireTargetPlaceholder(wire)}" value="${esc(wire.target)}"${readonly ? " disabled" : ""}><small>${esc(programWireTargetHint(wire))}</small></label>
          <datalist id="programWireTargetOptions">${programWireTargetOptions(wire)}</datalist>
          ${programDeliveryWireFieldsHtml(wire, readonly)}
          ${programExecutionExplanationHtml(wire.id)}
          <p class="programInspectorNote">${wireNote}</p>
          <div class="programInspectorActions" data-portal-inbox-avoid>
            <button type="button" data-program-delete${readonly ? " disabled" : ""}>删除连线</button>
            <button type="submit" data-program-save${readonly ? " disabled" : ""}>保存连线</button>
          </div>
        </form>`;
      syncProgramWireInspectorFields(panel);
      bindProgramInvokeEditor(panel, wire, readonly);
      panel.querySelector('[name="unavailable_policy"]')?.addEventListener("change", () => {
        syncProgramWireInspectorFields(panel);
      });
      panel.querySelector("[data-program-wire-form]")?.addEventListener("submit", event => {
        event.preventDefault();
        saveProgramWireFromInspector(wire.id, event.currentTarget).catch(() => {});
      });
      panel.querySelector("[data-program-delete]")?.addEventListener("click", () => {
        deleteProgramWire(wire.id).catch(() => {});
      });
    }
    panel.querySelector("[data-program-component-switch]")?.addEventListener("click", () => {
      toggleProgramComponent(panel, component).catch(() => {});
    });
    const inspectorDraftForm = panel.querySelector(
      "[data-program-component-form], [data-program-operator-form]",
    );
    if (inspectorDraftForm && !readonly) {
      inspectorDraftForm.dataset.programDraftDirty = "false";
      const markProgramDraftDirty = () => {
        if (!state.programMutationInFlight) {
          inspectorDraftForm.dataset.programDraftDirty = "true";
        }
      };
      inspectorDraftForm.addEventListener("input", markProgramDraftDirty);
      inspectorDraftForm.addEventListener("change", markProgramDraftDirty);
    }
    panel.querySelectorAll("form").forEach(form => {
      definitionRevisions.set(form, state.lineage.revision);
    });
    panel.querySelector("[data-program-copy-resource]")?.addEventListener("click", async event => {
      const address = String(
        event.currentTarget
          .closest("[data-program-resource-address]")
          ?.dataset.programResourceAddress
        || "",
      ).trim();
      try {
        const copied = await window.PortalUI?.copyText?.(address, "资源地址");
        if (copied) toast(window.PortalI18n.t("已复制资源地址。"), "success");
      } catch (error) {
        toast(window.PortalI18n.text`复制失败：${error.message || error}`, "error");
      }
    });
    bindProgramReferenceControls(panel);
    panel.querySelector("[data-program-inspector-close]")?.addEventListener("click", () => {
      if (onClose) { onClose(); return; }
      state.selectedProgramObjectId = "";
      programFeature.clearSelection();
      publishLocation(state.lineage?.lineage_id || "");
      renderGraph();
      renderControls();
      renderProgramInspector();
      window.requestAnimationFrame(() => state.graphController?.fit?.(AUTO_LAYOUT_VIEW));
    });
  }

  let programMutationStatusTimer = 0;
  function showProgramMutationStatus(message) {
    const status = $id("programMutationStatus");
    if (!status) return;
    window.clearTimeout(programMutationStatusTimer);
    status.textContent = message;
    status.hidden = false;
    programMutationStatusTimer = window.setTimeout(() => {
      status.hidden = true;
      programMutationStatusTimer = 0;
    }, 3600);
  }

  function applyProgramMutation(request, message, { failure = "程序图更新失败", draftForm = null } = {}) {
    if (!state.lineage) return Promise.resolve(null);
    const lineageId = state.lineage.lineage_id;
    const intent = lineageEditQueue.capture(lineageId);
    let placementSave = null;
    const run = lineageEditQueue.enqueue(intent, async () => {
        programFeature.beginMutation();
        renderProgramInspector();
        try {
          const response = await request();
          if (!response?.lineage || response.lineage.lineage_id !== lineageId) {
            throw new Error("Portal 没有返回更新后的程序图");
          }
          if (!lineageEditQueue.isCurrent(intent)) return null;
          if (draftForm?.isConnected) draftForm.dataset.programDraftDirty = "false";
          commitLineage(response.lineage);
          const resizedObjectId = response.component_id || response.component?.id
            || response.function_block_id || response.function_block?.id;
          if (resizedObjectId) {
            await new Promise(resolve => window.requestAnimationFrame(resolve));
            if (lineageEditQueue.isCurrent(intent)) {
              // Layout uses the same edit queue. Await it after this definition
              // edit completes, and never retry a committed mutation on failure.
              placementSave = separateGraphObject(resizedObjectId).catch(() => null);
            }
          }
          const createdNodeId = response.facade_resident_id || response.function_block_id;
          if (createdNodeId) {
            await new Promise(resolve => window.requestAnimationFrame(resolve));
            if (lineageEditQueue.isCurrent(intent)) {
              state.graphController?.ensureNodeVisible?.(createdNodeId, { persist: true });
              // The structure is already committed. A preference failure gets
              // its own notice, never an invitation to submit the creation again.
              await flushHatgViewPreferenceSave(lineageId).catch(() => {});
            }
          }
          if (message) showProgramMutationStatus(message);
          return response;
        } finally {
          if (lineageEditQueue.isCurrent(intent)) {
            programFeature.endMutation();
            renderLineageSurface();
          }
        }
      });
    return run.then(async response => {
      if (placementSave) await placementSave;
      return response;
    }).catch(error => {
      if (lineageEditQueue.isCurrent(intent)) toast(`${failure}：${error.message || error}`, "error", 6200);
      throw error;
    });
  }

  // Run one HATG ISA instruction on the open Lineage. Lineage-scoped calls get
  // lineage_id; a change answers with the result plus the re-read Lineage.
  async function executeHatgInstruction(
    instruction,
    argumentsValue = {},
    options = {},
  ) {
    if (!state.lineage && options.withLineage !== false) throw new Error("当前没有可操作的 Lineage");
    const lineageId = state.lineage?.lineage_id || "";
    const args = options.withLineage === false
      ? { ...argumentsValue }
      : { lineage_id: lineageId, ...argumentsValue };
    return societyService.mutate(lineageId, instruction, args, {
      idempotency_key: options.idempotencyKey,
      expected_version: options.expectedVersion,
    }, "hatg", { definition: options.definition !== false, reload: options.reload !== false });
  }

  function loopRuntimeRowsHtml(runtime = {}) {
    const rows = [
      ["当前轮数", runtime.round ?? 0],
      ["状态", runtime.paused ? "已暂停" : runtime.state === "stopped" ? "已停止" : "进行中"],
      ["运行代次", runtime.generation ?? 0],
    ];
    return rows.map(([label, value]) => window.PortalI18n.html`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("");
  }

  // Card and summary label of an Effect script: the instructions it calls.
  function effectScriptLabel(script) {
    const code = String(script || "")
      .replace(/"(?:\\.|[^"\\])*"/g, '""')
      .replace(/'[^']*'/g, "''")
      .replace(/(^|\s)#.*$/gm, "$1");
    const names = [...code.matchAll(/(?:^|[\s;(])([A-Z][A-Za-z0-9]{1,79})(?=[\s)]|$)/g)].map(match => match[1]);
    if (!names.length) return "";
    if (names.length === 1) return window.PortalI18n.text`调用 ${names[0]}`;
    const unique = [...new Set(names)];
    return window.PortalI18n.text`${names.length} 次调用：${unique.slice(0, 3).join("、")}${unique.length > 3 ? "…" : ""}`;
  }

  // The saved script's diagnostics, from the server's Lineage diagnostics (decision_4adcc1ff2f2c).
  async function renderEffectDiagnostics(panel, componentId) {
    const slot = panel?.querySelector("[data-effect-diagnostics]");
    if (!slot || !state.lineage) return;
    const lineageId = state.lineage.lineage_id;
    slot.dataset.effectDiagnosticsState = "loading";
    slot.textContent = window.PortalI18n.t("正在检查已保存的脚本…");
    try {
      const response = await executeHatgInstruction("GetLineageDiagnostics", {}, { reload: false, definition: false });
      if (!panel.contains(slot) || panel.dataset.componentId !== componentId || state.lineage?.lineage_id !== lineageId) return;
      const items = (Array.isArray(response?.diagnostics) ? response.diagnostics : [])
        .filter(item => item?.item_id === componentId);
      slot.dataset.effectDiagnosticsState = items.some(item => (item.level || item.severity || "error") === "error")
        ? "error"
        : items.length ? "warning" : "ok";
      slot.innerHTML = items.length
        ? window.PortalI18n.html`<strong>脚本诊断</strong><ul>${items.map(item => window.PortalI18n.html`<li data-level="${esc(item.level || item.severity || "error")}"><code>${esc(item.code)}</code> ${esc(item.message)}</li>`).join("")}</ul>`
        : window.PortalI18n.markup("<strong>脚本诊断</strong> 已保存的脚本未发现问题。");
    } catch (error) {
      if (!panel.contains(slot)) return;
      slot.dataset.effectDiagnosticsState = "unavailable";
      slot.textContent = window.PortalI18n.text`无法读取脚本诊断：${error.message || error}`;
    }
  }

  function seedCandidateForkFieldHtml(component, readonly) {
    const sourceId = candidateForkOf(component.id)?.source || "";
    const active = String(component.config?.status || "active") === "active";
    const residents = (state.lineage?.nodes || [])
      .map(node => `<option value="${esc(node.id)}"${node.id === sourceId ? " selected" : ""}>${esc(node.title || node.id)}</option>`)
      .join("");
    return `<label><span>候选 Fork</span><select data-seed-candidate-fork${readonly || !active ? " disabled" : ""}><option value=""${sourceId ? "" : " selected"}>未建立（不能采纳）</option>${residents}</select><small>候选从这个 Resident 的工作出发，采纳时 Fork 它；选择后立即生效。也可以把 Resident 的“+”拖到这个 Seed 上。</small></label>`;
  }

  // Set, replace or remove a Seed's candidate fork (CreateCandidateFork / DeleteCandidateFork).
  async function setSeedCandidateFork(seedId, sourceResidentId) {
    if (!state.lineage) return;
    const seed = seedById(seedId);
    if (!seed || String(seed.source_resident_id || "") === String(sourceResidentId || "")) return;
    const seedTitle = seed.display_title || seed.title || "Seed";
    const source = sourceResidentId ? nodeById(sourceResidentId) : null;
    setSyncStatus(sourceResidentId ? "正在建立候选 Fork…" : "正在删除候选 Fork…", "saving");
    try {
      const result = sourceResidentId
        ? await executeHatgInstruction("CreateCandidateFork", { seed_id: seedId, source_resident_id: sourceResidentId })
        : await executeHatgInstruction("DeleteCandidateFork", { seed_id: seedId });
      commitLineage(result.lineage);
      setSyncStatus(sourceResidentId ? "候选 Fork 已建立" : "候选 Fork 已删除", "saved");
      toast(sourceResidentId
        ? `“${seedTitle}”的候选 Fork 来自「${source?.title || sourceResidentId}」：采纳时 Fork 它。`
        : `已删除“${seedTitle}”的候选 Fork；重新建立之前不能采纳。`, "success", 5200);
    } catch (error) {
      setSyncStatus("候选 Fork 未保存", "error");
      renderLineageSurface();
      toast(`候选 Fork 未保存：${error.message || error}`, "error", 6500);
    }
  }

  async function saveProgramComponentFromInspector(componentId, form, { onSaved = null } = {}) {
    // A failed conflict refresh leaves no safe precondition. Retry the read,
    // retain the form, and never send an unversioned replacement definition.
    if (form.dataset.programDraftConflict === "true" && definitionRevisions.get(form) === undefined) {
      if (form.dataset.programDraftRefreshing === "true") return;
      const lineageId = state.lineage?.lineage_id;
      form.dataset.programDraftRefreshing = "true";
      try {
        const lineage = await societyService.loadLineage(lineageId);
        if (state.lineage?.lineage_id !== lineageId || !form.isConnected) return;
        // The save below redraws; this form must survive until then.
        commitLineage(lineage, { render: false });
        definitionRevisions.set(form, lineage.revision);
      } catch (error) {
        toast(error.message || String(error), "error");
        throw error;
      } finally {
        delete form.dataset.programDraftRefreshing;
      }
    }
    const selectSaved = onSaved || selectProgramComponent;
    const component = programComponentById(componentId);
    if (!component) return;
    const data = new FormData(form);
    if (component.kind === "seed") {
      const response = await applyProgramMutation(
        () => societyService.saveProgramComponent( {
          lineage_id: state.lineage.lineage_id,
          expected_version: definitionRevisions.get(form),
          component_id: component.id,
          component: {
            title: String(data.get("title") || "").trim(),
            config: {
              ...(component.config || {}),
              seed_type: String(data.get("seed_type") || "other"),
              summary: String(data.get("summary") || "").trim(),
              artifact_path: String(data.get("artifact_path") || "").trim(),
            },
          },
        }),
        "Seed 已保存。",
        { draftForm: form },
      );
      if (response?.component) await selectSaved(response.component);
      return;
    }
    if (component.kind === "effect") {
      const response = await applyProgramMutation(
        () => societyService.saveProgramComponent({
          lineage_id: state.lineage.lineage_id,
          expected_version: definitionRevisions.get(form),
          component_id: component.id,
          component: {
            title: String(data.get("title") || "").trim(),
            config: window.PortalLineageElementFields.effectConfigFromFields(form),
          },
        }),
        "Effect 已保存。",
        { draftForm: form },
      );
      if (response?.component) await selectSaved(response.component);
      return;
    }
    if (component.kind === "loop") {
      const response = await applyProgramMutation(
        () => societyService.saveProgramComponent({
          lineage_id: state.lineage.lineage_id,
          expected_version: definitionRevisions.get(form),
          component_id: component.id,
          component: {
            title: String(data.get("title") || "").trim(),
            config: window.PortalLineageElementFields.loopConfigFromFields(form),
          },
        }),
        "循环已保存。",
        { draftForm: form },
      );
      if (response?.component) await selectSaved(response.component);
      return;
    }
    if (isPacketOperatorComponent(component)) {
      const { config, error } = window.PortalLineageElementFields.operatorConfigFromFields(form, component);
      if (error) {
        toast(error, "warning", 6200);
        return;
      }
      const response = await applyProgramMutation(
        () => societyService.saveProgramComponent( {
          lineage_id: state.lineage.lineage_id,
          expected_version: definitionRevisions.get(form),
          component_id: component.id,
          component: {
            title: String(data.get("title") || "").trim(),
            config,
          },
        }),
        `${component.kind} 已保存；组件身份与连线保持不变。`,
        { draftForm: form },
      );
      if (response?.component) {
        await selectSaved(response.component);
      }
      return;
    }
    const parsedConfig = window.PortalLineagePrimitiveInspector.readConfig(form);
    if (parsedConfig.errors.length) {
      toast(parsedConfig.errors.join(" "), "warning", 6200);
      return;
    }
    const config = parsedConfig.config;
    const selectedEventType = String(
      config.event_type || programPrimitiveDefaultType("event"),
    );
    const enteredTitle = String(data.get("title") || "").trim();
    const componentDefaults = window.PortalLineageProgram?.manifest?.component_kinds?.[component.kind] || {};
    const selectedTypeTitle = component.kind === "event_source"
      ? window.PortalLineageProgram?.manifest?.event_types?.[selectedEventType]?.title
      : "";
    const savedTitle = selectedTypeTitle
      && (!enteredTitle || enteredTitle === componentDefaults.default_title)
      ? selectedTypeTitle
      : enteredTitle;
    const subjectResidentId = canonicalProgramResidentId(
      data.get("subject_resident_id"),
    );
    const subjectEventSourceId = String(
      data.get("subject_event_source_id") || "",
    ).trim().toLowerCase();
    const subjectWireId = String(
      data.get("subject_wire_id") || "",
    ).trim().toLowerCase();
    const subjectEffectId = String(
      data.get("subject_effect_id") || "",
    ).trim().toLowerCase();
    const deliverySubjectKind = String(
      data.get("delivery_subject_kind") || "event_source",
    );
    // Watching the Lineage or all of Portal: the server drops the Watch wires.
    const watchScoped = component.kind === "event_source"
      && Boolean(programEventWatchContract(selectedEventType).required_when)
      && !["", "watched"].includes(String(config.resident_scope || ""));
    const watchKinds = watchScoped ? new Set() : programEventWatchKinds(selectedEventType);
    const watchedResidentSet = form.querySelector("[data-watch-resident-set]")
      ? data.getAll("watch_resident_ids").map(canonicalProgramResidentId).filter(Boolean)
      : null;
    const watchedEffectSet = form.querySelector("[data-watch-effect-set]")
      ? data.getAll("watch_effect_ids").map(value => String(value).trim()).filter(Boolean)
      : null;
    const watchSubject = component.kind !== "event_source" || watchScoped
      ? null
      : watchKinds.has("resident") && watchedResidentSet
        ? { kind: "resident", ids: watchedResidentSet }
      : watchKinds.has("resident")
        ? { kind: "resident", id: subjectResidentId }
        : watchKinds.has("effect")
          ? (watchedEffectSet ? { kind: "effect", ids: watchedEffectSet } : { kind: "effect", id: subjectEffectId })
          : watchKinds.has("wire") && deliverySubjectKind === "wire"
            ? { kind: "wire", id: subjectWireId }
            : watchKinds.has("event_source")
              ? { kind: "event_source", id: subjectEventSourceId }
              : {};
    if (
      component.kind === "event_source"
      && watchKinds.has("resident")
      && !watchedResidentSet
    ) {
      const residentError = programResidentReferenceError(
        subjectResidentId,
        {
          eventType: selectedEventType,
          stateDomain: String(config.state_domain || "intake"),
        },
      );
      if (residentError) {
        toast(residentError, "warning", 5600);
        return;
      }
    }
    if (
      component.kind === "event_source"
      && watchKinds.has("event_source")
      && !watchKinds.has("wire")
    ) {
      const sourceError = programEventSourceReferenceError(
        subjectEventSourceId,
        component.id,
      );
      if (sourceError) {
        toast(sourceError, "warning", 5600);
        return;
      }
    }
    if (
      component.kind === "event_source"
      && watchKinds.has("event_source")
      && watchKinds.has("wire")
    ) {
      const subjectError = deliverySubjectKind === "wire"
        ? programEventWireReferenceError(subjectWireId, component.id)
        : programEventSourceReferenceError(subjectEventSourceId, component.id);
      if (subjectError) {
        toast(subjectError, "warning", 5600);
        return;
      }
    }
    if (
      component.kind === "event_source"
      && watchKinds.has("effect")
    ) {
      const effectIds = watchedEffectSet || [subjectEffectId];
      const effectError = !effectIds.length ? "请选择至少一个 Effect。"
        : effectIds.map(id => programEffectReferenceError(id, selectedEventType)).find(Boolean);
      if (effectError) {
        toast(effectError, "warning", 5600);
        return;
      }
    }
    const response = await applyProgramMutation(() => societyService.saveProgramComponent( {
      lineage_id: state.lineage.lineage_id,
      expected_version: definitionRevisions.get(form),
      component_id: component.id,
      component: {
        title: savedTitle,
        config,
        ...(watchSubject && Object.keys(watchSubject).length ? { watch_subject: watchSubject } : {}),
      },
    }), "", { draftForm: form });
    if (onSaved) {
      await onSaved(response?.component);
      toast(window.PortalI18n.t("Event 已保存。"), "success");
      return;
    }
    const createdWireIds = response?.created_wire_ids || [];
    const updatedWireIds = response?.updated_wire_ids || [];
    const removedCount = Number(response?.removed_wire_ids?.length || 0);
    const referenceChangedCount = createdWireIds.length + updatedWireIds.length;
    const feedbackWireId = createdWireIds[0] || updatedWireIds[0] || "";
    if (feedbackWireId) {
      const focusNodeId = watchSubject?.id || component.id;
      showProgramReferenceWire(feedbackWireId, focusNodeId);
    }
    if (!removedCount && !referenceChangedCount) {
      toast(window.PortalI18n.text`${component.kind === "event_source" ? "Event" : "Effect"} 已保存。`, "success");
      return;
    }
    const connectionName = component.kind === "event_source"
      ? "监听连线"
      : "目标连线";
    const details = [
      ...(createdWireIds.length ? [`创建 ${createdWireIds.length} 条${connectionName}`] : []),
      ...(updatedWireIds.length ? [`改绑 ${updatedWireIds.length} 条${connectionName}`] : []),
      ...(removedCount ? [`删除 ${removedCount} 条不再使用的连线`] : []),
    ];
    toast(window.PortalI18n.text`${component.kind === "event_source" ? "Event" : "Effect"} 已保存，并${details.join("、")}。`, "success", 5600);
  }

  async function deleteProgramComponent(componentId, options = {}) {
    if (!programComponentById(componentId)) return false;
    const response = await applyProgramMutation(() => societyService.deleteProgramComponent( {
      lineage_id: state.lineage.lineage_id,
      component_id: componentId,
    }), options.quiet ? "" : "原语及其连线已删除。");
    if (!response) return false;
    state.selectedProgramObjectId = "";
    return true;
  }

  async function batchDeleteCanvasSelection() {
    if (
      state.canvasSelectionDeleteInFlight
      || !state.selectedCanvasObjectIds.size
    ) return;
    const deletableTargets = selectedCanvasObjects().filter(canvasObjectCanDelete).map(object => ({
      id: object.id,
      title: object.title || object.id,
      kind: isProgramComponent(object) ? "component" : "resident",
    }));
    if (!deletableTargets.length) return;
    const leaderId = String(state.lineage?.leader_resident_id || "");
    const includesLeader = Boolean(leaderId) && deletableTargets.some(target => (
      target.kind === "resident" && target.id === leaderId
    ));
    const residentCount = deletableTargets.filter(target => target.kind === "resident").length;
    const componentCount = deletableTargets.length - residentCount;
    const detail = [
      residentCount ? `${residentCount} 个 Resident 节点` : "",
      componentCount ? `${componentCount} 个 Event / Effect` : "",
    ].filter(Boolean).join("、");
    const confirmed = await window.PortalUI.confirm({
      title: window.PortalI18n.text`批量删除 ${deletableTargets.length} 个 HATG 元素？`,
      message: [
        window.PortalI18n.text`将删除选区中的${detail}，相关连线也会一并移除。`,
        residentCount
          ? window.PortalI18n.t("选中的 Resident 将标记为已删除，并移除 Session 和图引用；关联 Workspace 仅在没有其他存活 Resident 引用时回收，仍被引用的 Workspace 保留。对话与运行轨迹保留。")
          : "",
        includesLeader
          ? window.PortalI18n.t("选区包含这个 Lineage 的 Leader；删除后系统从其余 Resident 中随机选出新的 Leader。")
          : "",
      ].filter(Boolean).join("\n\n"),
      confirmLabel: window.PortalI18n.t("批量删除"),
      tone: "danger",
    });
    if (!confirmed) return;

    const button = $id("batchDeleteSelectionBtn");
    let committed = false;
    if (!programFeature.beginSelectionDelete()) return;
    setBusy(button, true, "删除中…");
    renderControls();
    try {
      const lineageId = state.lineage.lineage_id;
      const residentIds = deletableTargets.filter(target => target.kind === "resident").map(target => target.id);
      const componentIds = deletableTargets.filter(target => target.kind === "component").map(target => target.id);
      const deletedNodes = residentIds.map(nodeById).filter(Boolean);
      const idempotencyKey = `portal:delete-objects:${globalThis.PortalUIPrimitives.randomUUID()}`;
      const result = await applyProgramMutation(() => societyService.deleteObjects( {
        lineage_id: lineageId,
        idempotency_key: idempotencyKey,
        ...(residentIds.length ? { resident_ids: residentIds } : {}),
        ...(componentIds.length ? { component_ids: componentIds } : {}),
      }), "");
      if (!result) return;
      committed = true;
      for (const node of deletedNodes) {
        state.runtimeStatus.delete(node.id);
        state.sessionUserMessageCounts.delete(nodeResidentId(node));
        state.sessionContextPressures.delete(nodeResidentId(node));
      }
      const deletedIds = new Set(result.deleted_resident_ids || []);
      state.residents = state.residents.filter(item => !deletedIds.has(String(item.resident_id || "")));
      if (!programComponentById(state.selectedProgramObjectId)) state.selectedProgramObjectId = "";
    } catch (error) {
      // A transport failure may follow a commit; retain selection until the
      // authoritative graph arrives instead of reporting partial success.
      console.warn("HATG batch deletion failed", error);
    } finally {
      programFeature.endSelectionDelete(
        (committed ? [] : deletableTargets)
          .map(target => target.id)
          .filter(targetId => Boolean(canvasObjectById(targetId))),
      );
      const selectedNodeRemoved = Boolean(state.selectedNodeId) && !nodeById(state.selectedNodeId);
      if (selectedNodeRemoved) clearNodeSelection();
      setBusy(button, false);
      renderLineageSurface();
      if (selectedNodeRemoved || !state.lineage?.nodes.length) {
        void Promise.allSettled([loadResults()]);
      }
    }
    if (committed) {
      toast(
        window.PortalI18n.text`已批量删除 ${deletableTargets.length} 个 HATG 元素。`,
        "success", 4200,
      );
    }
  }

  async function saveProgramWireFromInspector(wireId, form) {
    const wire = displayProgramWireById(wireId);
    if (!wire) return;
    const data = new FormData(form);
    const hasTargetEndpoint = data.has("target_endpoint_id");
    const targetEndpointId = hasTargetEndpoint
      ? canonicalProgramEndpointId(data.get("target_endpoint_id"))
      : "";
    if (hasTargetEndpoint) {
      const endpointError = programWireTargetReferenceError(
        wire,
        targetEndpointId,
      );
      if (endpointError) {
        toast(endpointError, "warning", 5600);
        return;
      }
    }
    const effectiveTargetId = targetEndpointId || wire.target;
    const invokeWire = ["route"].includes(wire.type);
    let invokeArgumentMapping = {};
    const invokeEditorState = invokeWire
      ? programInvokeEditorState(form, wire)
      : null;
    if (invokeWire) {
      if (invokeEditorState.targetId !== effectiveTargetId) {
        const switched = switchProgramInvokeEditorTarget(
          form,
          wire,
          effectiveTargetId,
          state.programMutationInFlight,
        );
        if (switched.errors.length) {
          toast(switched.errors.join(" "), "warning", 5600);
          return;
        }
      }
      const parsedMapping = window.PortalLineageInvocationInspector.readMapping(
        form,
      );
      if (parsedMapping.errors.length) {
        toast(parsedMapping.errors.join(" "), "warning", 5600);
        return;
      }
      invokeArgumentMapping = parsedMapping.mapping;
    }
    const invokeProfiles = clone(
      invokeEditorState?.profiles || wire.config?.invoke_profiles || {},
    );
    if (invokeWire) {
      const profileKey = programInvokeProfileKey(effectiveTargetId);
      invokeProfiles[profileKey] = { arguments: invokeArgumentMapping };
    }
    await applyProgramMutation(() => societyService.saveProgramWire( {
      lineage_id: state.lineage.lineage_id,
      expected_version: definitionRevisions.get(form),
      wire_id: wire.id,
      wire: {
        kind: wire.type,
        source: wire.source,
        source_port: wire.source_port,
        target: effectiveTargetId,
        target_port: wire.target_port,
        label: String(data.get("label") || "").trim(),
        ...(["route"].includes(wire.type)
          ? {
              config: {
                invoke_profiles: invokeProfiles,
                ...window.PortalLineageElementFields.deliveryConfigFromFields(form),
              },
            }
          : {}),
      },
    }), "连线已保存。");
  }

  async function deleteProgramWire(wireId) {
    await applyProgramMutation(() => societyService.deleteProgramWire( {
      lineage_id: state.lineage.lineage_id,
      wire_id: wireId,
    }), "连线已删除。");
    state.selectedProgramObjectId = "";
  }

  async function emitManualProgramEvent(componentId, button = null) {
    const component = programComponentById(componentId);
    if (
      !state.lineage
      || state.programMutationInFlight
      || component?.kind !== "event_source"
      || String(component.config?.event_type || "") !== "manual.triggered"
      || programComponentPaused(component)
    ) return;
    const lineageId = state.lineage.lineage_id;
    const inFlightKey = manualProgramEventKey(componentId, lineageId);
    if (!programFeature.beginManualEvent(inFlightKey)) return;
    setBusy(button, true, "等待输入…");
    syncManualProgramEventInspectorBusy(componentId);
    renderGraph();
    try {
      const runtimePrompt = await window.PortalUI.prompt({
        title: `触发「${component.title || "手动 Event"}」`,
        message: window.PortalI18n.t("填写只用于本次触发的输入。它会追加到下游 Effect 的固定 Prompt，不会修改共享 Program；可留空。"),
        label: window.PortalI18n.t("本次 Prompt"),
        value: "",
        placeholder: window.PortalI18n.t("输入这一次要交给下游处理的内容"),
        confirmLabel: "触发 Event",
        multiline: true,
      });
      if (runtimePrompt === null) return;
      if (!state.lineage || state.lineage.lineage_id !== lineageId) return;
      const currentComponent = programComponentById(componentId);
      if (
        currentComponent?.kind !== "event_source"
        || String(currentComponent.config?.event_type || "") !== "manual.triggered"
      ) {
        throw new Error("这个 Event 已不再支持手动触发");
      }
      if (programComponentPaused(currentComponent)) throw new Error("Event 已关闭；打开后才能触发。");
      const response = await societyService.triggerProgramEvent( {
        lineage_id: lineageId,
        event_source_id: componentId,
        prompt: String(runtimePrompt),
      });
      if (!state.lineage || state.lineage.lineage_id !== lineageId) return;
      if (!response?.lineage || response.lineage.lineage_id !== lineageId) {
        throw new Error("Portal 没有返回更新后的 Lineage 状态");
      }
      // Residents the Event created join the Resident state subscription here.
      commitLineage(response.lineage);
      const delivery = response.delivery || {};
      const matchedCount = Number(delivery.matched_count);
      const deliveryStatuses = (delivery.results || [])
        .filter(item => item && typeof item === "object")
        .map(item => String(item.status || ""));
      const retainedCount = deliveryStatuses.filter(
        status => ["queued", "replaced"].includes(status),
      ).length;
      const discardedCount = deliveryStatuses.filter(
        status => ["dropped", "queue_full"].includes(status),
      ).length;
      toast(
        retainedCount
          ? window.PortalI18n.text`Event 已触发；${retainedCount} 条边暂存了事件，接收方可消费后会继续投递。`
          : discardedCount
            ? window.PortalI18n.text`Event 已触发；${discardedCount} 条边按各自策略丢弃了事件。`
            : Number.isFinite(matchedCount) && matchedCount === 0
              ? window.PortalI18n.t("Event 已交给 Portal Lineage；当前没有可接收事件的 Resident 或 Effect 路由。")
              : window.PortalI18n.t("Event 已交给 Portal Lineage。"),
        "success",
      );
    } catch (error) {
      if (state.lineage?.lineage_id === lineageId) {
        toast(window.PortalI18n.text`Event 触发失败：${error.message || error}`, "error", 6200);
      }
    } finally {
      programFeature.endManualEvent(inFlightKey);
      setBusy(button, false);
      syncManualProgramEventInspectorBusy(componentId);
      if (state.lineage?.lineage_id === lineageId) renderGraph();
    }
  }

  async function resetProgramEventSource(componentId, button = null) {
    const component = programComponentById(componentId);
    if (!state.lineage || state.programMutationInFlight || component?.kind !== "event_source") return;
    const lineageId = state.lineage.lineage_id;
    const key = manualProgramEventKey(componentId, lineageId);
    if (!programFeature.beginEventSourceReset(key)) return;
    setBusy(button, true, "刷新中…");
    syncManualProgramEventInspectorBusy(componentId);
    renderGraph();
    try {
      const response = await societyService.resetProgramEvent({
        lineage_id: lineageId,
        component_id: componentId,
      });
      if (!state.lineage || state.lineage.lineage_id !== lineageId) return;
      if (!response?.lineage || response.lineage.lineage_id !== lineageId) {
        throw new Error("Portal 没有返回更新后的 Lineage 状态");
      }
      commitLineage(response.lineage);
      toast(window.PortalI18n.t("Event 触发次数已清零，重新进入监听状态。"), "success");
    } catch (error) {
      if (state.lineage?.lineage_id === lineageId) {
        toast(window.PortalI18n.text`Event 刷新失败：${error.message || error}`, "error", 6200);
      }
    } finally {
      programFeature.endEventSourceReset(key);
      setBusy(button, false);
      syncManualProgramEventInspectorBusy(componentId);
      if (state.lineage?.lineage_id === lineageId) renderLineageSurface();
    }
  }

  function canvasNodeCanBeSelected(node) {
    if (!node) return false;
    return Boolean(
      isFunctionBlockInstance(node)
      || isProgramComponent(node)
      || displayProgramComponentById(node.id)
      || displayNodeById(node.id),
    );
  }

  function canvasObjectById(objectId) {
    return functionBlockInstanceById(objectId) || programComponentById(objectId) || displayProgramWireById(objectId) || nodeById(objectId) || null;
  }

  function selectedCanvasObjects() {
    return [...state.selectedCanvasObjectIds]
      .map(canvasObjectById)
      .filter(Boolean);
  }

  function canvasObjectCanDelete(object) {
    // The Leader is deletable too: the service picks a random successor.
    return Boolean(object && !isFunctionBlockInstance(object) && !displayProgramWireById(object.id));
  }

  function renderCanvasSelection() {
    publishLocation(state.lineage?.lineage_id || "");
    if (!renderGraphSelection()) renderGraph();
    renderControls();
    renderProgramInspector();
  }

  function clearCanvasSelection(options = {}) {
    if (!programFeature.clearSelection()) return false;
    if (options.render !== false) renderCanvasSelection();
    return true;
  }

  function visibleCanvasObjectIds(model = graphVisibleModel()) {
    if (state.graphViewMode !== "context") return new Set();
    return new Set([
      ...model.nodes.map(node => node.id),
      ...model.functionBlockBoundaries.map(block => block.id),
      ...model.edges.filter(edge => !edge.visual_only).map(edge => edge.id),
    ].filter(id => Boolean(canvasObjectById(id))));
  }

  function reconcileCanvasSelection(model = graphVisibleModel()) {
    const visibleIds = visibleCanvasObjectIds(model);
    programFeature.reconcileSelection(id => visibleIds.has(id));
    if (!visibleIds.has(state.canvasSelectionAnchorId)) state.canvasSelectionAnchorId = "";
    return visibleIds;
  }

  function currentCanvasSelectionAnchorId(visibleIds = visibleCanvasObjectIds()) {
    // A wire is selected by its own identity, never its source node. Chat may
    // keep a hidden Resident open without making it a canvas selection anchor.
    return [state.selectedProgramObjectId, state.canvasSelectionAnchorId,
      state.selectedSeedId, state.selectedNodeId].find(id => visibleIds.has(id)) || "";
  }

  function applyCanvasSelection(selected) {
    const visibleIds = visibleCanvasObjectIds();
    programFeature.replaceSelection(
      [...selected].filter(objectId => visibleIds.has(objectId)),
    );
    state.selectedProgramObjectId = "";
    state.selectedSeedId = "";
    state.selectedEdgeId = "";
    renderCanvasSelection();
  }

  function addCanvasObjectsToSelection(objectIds, options = {}) {
    const visibleIds = reconcileCanvasSelection();
    const selected = new Set(state.selectedCanvasObjectIds);
    if (options.includeCurrent && !selected.size) {
      const anchorId = currentCanvasSelectionAnchorId(visibleIds);
      if (anchorId) selected.add(anchorId);
    }
    (objectIds || []).forEach(objectId => {
      if (visibleIds.has(objectId)) selected.add(objectId);
    });
    applyCanvasSelection(selected);
  }

  function toggleCanvasObjectSelection(objectId) {
    const visibleIds = reconcileCanvasSelection();
    if (!visibleIds.has(objectId)) return;
    const selected = new Set(state.selectedCanvasObjectIds);
    if (!selected.size) {
      const anchorId = currentCanvasSelectionAnchorId(visibleIds);
      if (anchorId) selected.add(anchorId);
      selected.add(objectId);
    } else if (selected.has(objectId)) {
      selected.delete(objectId);
    } else {
      selected.add(objectId);
    }
    applyCanvasSelection(selected);
  }

  async function selectProgramObject(objectId, { revealFamily = false } = {}) {
    const component = displayProgramComponentById(objectId);
    const wire = displayProgramWireById(objectId);
    const functionBlockInstance = functionBlockInstanceById(objectId);
    if (!component && !wire && !functionBlockInstance) return;
    state.selectedSeedId = "";
    state.selectedEdgeId = "";
    state.selectedProgramObjectId = objectId;
    clearCanvasSelection({ render: false });
    publishLocation(
      state.lineage?.lineage_id || "",
      component || wire || functionBlockInstance ? objectId : "",
    );
    if (!renderGraphSelection()) renderGraph();
    renderControls();
    renderProgramInspector();
    if (revealFamily && functionBlockInstance?.family) {
      showFamilyAlone({ instance: functionBlockInstance, roles: familyRoles(functionBlockInstance) });
      return;
    }
    if (component || functionBlockInstance) {
      await revealSelectedGraphNode(objectId, { scale: 1, duration: 0 });
    }
  }

  function seedTypeLabel(seed) {
    return ({ idea: "候选想法", plan: "候选方案", next_step: "下一步", other: "候选" })[seed?.seed_type] || "候选";
  }

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

  function clearNodeSelection() {
    state.selectedNodeId = "";
    state.selectedSeedId = "";
    state.detail = null;
  }

  function residentInputBody(node, content) {
    const userContent = String(content || "").trim();
    const body = {
      resident_id: nodeResidentId(node),
      message: {
        message_id: window.PortalChatWorkspace.newMessageIdentity(),
        role: "user",
        content_parts: window.PortalChatWorkspace.composerPayloadContentParts({ text: userContent }),
      },
    };
    return body;
  }

  async function stopResidentTurn(node, options = {}) {
    const target = nodeById(node?.id);
    if (!target || !state.lineage) throw new Error("目标节点已不在当前 Lineage 中");
    if (options.runningOnly && effectiveNodeStatus(target) !== "running") return false;
    const runtime = state.residentRuntimeById.get(nodeResidentId(target));
    const messageId = String(runtime?.resident_state?.active_message_id || "").trim();
    if (!messageId) throw new Error("目标节点没有可停止的活跃消息");
    await window.PortalResidentProtocol.cancelInput({
        resident_id: nodeResidentId(target),
        message_id: messageId,
    });
    return true;
  }

  async function refreshAfterResidentStop(node) {
    const refreshes = [];
    if (state.residentStateCollection) {
      refreshes.push(state.residentStateCollection.load({ force: true }));
    }
    if (refreshes.length) await Promise.allSettled(refreshes);
  }

  async function stopLineageNode(node, options = {}) {
    const target = nodeById(node?.id);
    if (!target || state.nodeStopInFlight.has(target.id)) return false;
    const runningOnly = options.runningOnly !== false;
    if (runningOnly && effectiveNodeStatus(target) !== "running") return false;
    const lineageId = state.lineage?.lineage_id || "";
    state.nodeStopInFlight.add(target.id);
    renderGraph();
    try {
      const stopped = await stopResidentTurn(target, { runningOnly });
      if (!stopped) return false;
      if (state.lineage?.lineage_id !== lineageId) return false;
      toast(window.PortalI18n.text`${target.title} 已 Stop 当前 Session。`, "warning");
      await refreshAfterResidentStop(target);
      return true;
    } catch (error) {
      if (state.lineage?.lineage_id === lineageId) {
        toast(window.PortalI18n.text`Stop ${target.title} 失败：${error.message || error}`, "error", 6200);
      }
      return false;
    } finally {
      state.nodeStopInFlight.delete(target.id);
      if (state.lineage?.lineage_id === lineageId) {
        renderGraph();
        renderControls();
      }
    }
  }

  async function sendPromptToNode(node, prompt, options = {}) {
    const content = String(prompt || "").trim();
    if (!node || !content) throw new Error("目标节点或 prompt 为空");
    await window.PortalResidentProtocol.sendInput(residentInputBody(node, content));
    if (options.select !== false && node.id !== state.selectedNodeId) await selectNode(node.id, { force: true });
    renderGraph();
  }

  async function loadLineages() {
    const payload = await societyService.listLineages();
    state.lineages = Array.isArray(payload?.lineages) ? payload.lineages : [];
  }

  function loadLineageAuxiliaryCatalogs(options = {}) {
    const catalogOptions = { force: options.forceCatalogs === true };
    return Promise.all([
      window.PortalLineageCatalogs.residents(catalogOptions).catch(() => ({ residents: [] })),
      loadLineageRunAsUsers(catalogOptions),
      window.PortalLineageCatalogs.projectCatalog(catalogOptions).catch(() => ({ projects: [] })),
      window.PortalLineageCatalogs.agentBackends(catalogOptions).catch(() => null),
    ]).then(([residents, _users, projects, backendCatalog]) => {
      const nextResidents = Array.isArray(residents?.residents) ? residents.residents : [];
      if (residents?.account_selection) {
        state.accountSelection = residents.account_selection;
      }
      state.residents = nextResidents;
      state.agentBackendCatalog = backendCatalog;
      window.PortalLineageCatalogs.populateBackends($id("nodeBackendSelect"), backendCatalog);
      renderNodeProviderFields();
      state.projects = Array.isArray(projects?.projects) ? projects.projects : [];
      if (!$id("lineageModal")?.hidden) {
        renderLineageProjectOptions($id("lineageProjectInput")?.value || "");
      }
      if (!$id("subgraphMoveModal")?.hidden) {
        renderLineageProjectOptions($id("subgraphMoveProject")?.value || "", $id("subgraphMoveProject"));
      }
      if (state.lineage) renderControls();
    });
  }

  async function loadLineagePageData(options = {}) {
    void loadLineageAuxiliaryCatalogs(options).catch(error => {
      console.warn("Lineage auxiliary catalogs failed to load", error);
    });
    await loadLineages();
  }

  function renderNoLineage(options = {}) {
    const loadError = $id("lineageLoadError");
    if (loadError) loadError.hidden = true;
    state.residentStateCollection?.destroy?.();
    state.residentStateCollection = null;
    state.lineageProjectionSubscription?.();
    state.lineageProjectionSubscription = null;
    state.graphViewRequestSeq += 1;
    state.workspaceGraphLoadSeq += 1;
    setWorkspaceViewLoading(false);
    state.lineage = null;
    state.expandedFamilyFacadeIds = new Set();
    activateLineageEdits();
    state.graphFocusMode = false;
    state.graphRelationFilter = "all";
    state.graphViewMode = "context";
    state.selectedEdgeId = "";
    state.selectedProgramObjectId = "";
    programFeature.clearForLineageChange();
    state.workspaceGraphBindings = new Map();
    state.selectedWorkspaceId = "";
    state.handoffMode = false;
    state.graphController?.setContext?.("");
    state.resultWorkspace = null;
    state.resultWorkspaceNodeId = "";
    state.resultWorkspaceSeq += 1;
    state.runtimeStatus = new Map();
    state.sessionUserMessageCounts = new Map();
    state.sessionContextPressures = new Map();
    state.selectedResultPath = "";
    state.globalStopInFlight = false;
    state.nodeStopInFlight = new Set();
    if (options.publishLocation !== false) publishLocation("");
    renderLineageSurface();
    clearNodeSelection();
    setSyncStatus("等待创建 Lineage", "idle");
  }

  async function selectLineage(lineageId, options = {}) {
    if (!lineageId) return;
    const loadError = $id("lineageLoadError");
    if (loadError) loadError.hidden = true;
    const sequence = ++state.detailSeq;
    const requestedProgramItemId = String(options.programItemId || "").trim();
    const requestedResidentId = String(options.residentId || "").trim();
    if (state.lineage?.lineage_id === lineageId) {
      resumeDisplayedLineage();
      const requestedSession = (state.lineage.nodes || []).find(node => (
        node.id === requestedResidentId || nodeResidentId(node) === requestedResidentId
      ));
      if (requestedSession) {
        await selectNode(requestedSession.id, {
          force: true,
          notifyParent: false,
          revealInGraph: options.revealInGraph === true,
        });
      }
      if (requestedProgramItemId) {
        await selectProgramObject(requestedProgramItemId, { revealFamily: true });
      }
      return;
    }
    state.graphViewRequestSeq += 1;
    state.workspaceGraphLoadSeq += 1;
    setWorkspaceViewLoading(false);
    if (sequence !== state.detailSeq) return;
    state.residentStateCollection?.destroy?.();
    state.residentStateCollection = null;
    state.lineageProjectionSubscription?.();
    state.lineageProjectionSubscription = null;
    activateLineageEdits(lineageId);
    setSyncStatus("加载 HATG…", "saving");
    try {
      const [lineage, personalView] = await Promise.all([
        societyService.loadLineage(lineageId).catch(error => {
          if (sequence === state.detailSeq && Number(error?.status) === 404) {
            hatgComponentOptions.emit?.("binding-unavailable");
          }
          throw error;
        }),
        loadHatgViewPreference(lineageId),
      ]);
      if (sequence !== state.detailSeq) return;
      state.lineage = lineage;
      hatgComponentOptions.emit?.("binding-ready");
      // The previous Lineage's selection names no node here. Left in place,
      // rendering would take it for a removed node and open the leader's
      // Chat instead of the Resident this load was asked for.
      state.selectedNodeId = "";
      templateController.load().catch(error => toast(error.message || String(error), "error"));
      programFeature.clearForLineageChange();
      state.expandedFamilyFacadeIds = new Set(
        personalView.expanded_family_facade_ids,
      );
      state.graphFocusMode = false;
      state.graphRelationFilter = "all";
      state.handoffMode = false;
      state.selectedEdgeId = "";
      state.selectedProgramObjectId = "";
      state.workspaceGraphBindings = new Map();
      state.selectedWorkspaceId = "";
      clearGraphNodeSearch();
      const restoredPersonalViewport = state.graphController?.setContext?.(
        lineage.lineage_id,
      ) === true;
      state.resultWorkspace = null;
      state.resultWorkspaceNodeId = "";
      state.resultWorkspaceSeq += 1;
      state.runtimeStatus = new Map();
      state.residentRuntimeById = new Map();
      state.sessionUserMessageCounts = new Map();
      state.sessionContextPressures = new Map();
      state.localVersion += 1;
      state.selectedResultPath = "";
      state.globalStopInFlight = false;
      state.nodeStopInFlight = new Set();
      state.selectedSeedId = "";
      const requestedProgramExists = Boolean(
        requestedProgramItemId
        && (
          displayProgramComponentById(requestedProgramItemId)
          || displayProgramWireById(requestedProgramItemId)
          || functionBlockInstanceById(requestedProgramItemId)
        )
      );
      publishLocation(lineageId);
      updateLineageSummary();
      renderLineageSurface();
      if (restoredPersonalViewport) {
        window.requestAnimationFrame(() => {
          state.graphController?.setContext?.(lineage.lineage_id);
        });
      } else if ((lineage.program?.components || []).length) {
        window.requestAnimationFrame(() => {
          state.graphController?.fit?.({
            duration: 0,
            maxScale: 1.12,
          });
        });
      }
      if (panelActive) {
        bindResidentStateCollection();
        bindLineageProjectionSubscription();
      }
      const currentRevision = Number(lineage.revision || 0);
      setSyncStatus(currentRevision ? `已加载 · revision ${currentRevision}` : "已加载", "saved");
      const requestedSession = (lineage.nodes || []).find(node => (
        node.id === requestedResidentId || nodeResidentId(node) === requestedResidentId
      ));
      const selected = requestedSession
        || lineage.nodes.find(node => node.id === lineage.leader_resident_id)
        || lineage.nodes[0];
      if (selected) {
        await selectNode(selected.id, {
          force: true,
          notifyParent: false,
          revealInGraph: Boolean(requestedSession && options.revealInGraph === true),
        });
      } else {
        clearNodeSelection();
      }
      if (requestedProgramExists) {
        await selectProgramObject(requestedProgramItemId, { revealFamily: true });
      }
    } catch (error) {
      if (sequence !== state.detailSeq) return;
      renderNoLineage({ publishLocation: false });
      setSyncStatus("加载失败", "error");
      if (loadError) {
        loadError.textContent = window.PortalI18n.text`Lineage ${lineageId} 加载失败：${error.message || error}`;
        loadError.hidden = false;
      }
      toast(window.PortalI18n.text`Lineage 加载失败：${error.message || error}`, "error");
    }
  }

  function resumeDisplayedLineage() {
    const lineageId = state.lineage?.lineage_id;
    if (!lineageId) return;
    if (!lineageEditQueue.isActive(lineageId)) {
      activateLineageEdits(lineageId);
      setSyncStatus("已加载", "saved");
    }
    if (panelActive) {
      if (!state.residentStateCollection) bindResidentStateCollection();
      if (!state.lineageProjectionSubscription) bindLineageProjectionSubscription();
    }
  }

  function renderLineageProjectOptions(selectedProjectId = "", select = $id("lineageProjectInput")) {
    if (!select) return;
    const selected = String(selectedProjectId || "").trim();
    const projects = [...(state.projects || [])].sort((left, right) => (
      String(left?.name || left?.id || "").localeCompare(
        String(right?.name || right?.id || ""),
        undefined,
        { sensitivity: "base" },
      )
    ));
    const options = projects.map(project => ({
      value: String(project?.resource_id || project?.id || "").trim(),
      label: String(project?.name || project?.resource_id || project?.id || "").trim(),
    })).filter(item => item.value);
    if (selected && !options.some(item => item.value === selected)) {
      options.unshift({ value: selected, label: selected });
    }
    select.innerHTML = [
      window.PortalI18n.html`<option value="" disabled ${selected ? "" : "selected"}>请选择项目</option>`,
      ...options.map(option => window.PortalI18n.html`<option value="${esc(option.value)}" ${option.value === selected ? "selected" : ""}>${esc(option.label)}</option>`),
    ].join("");
  }

  let subgraphMoveContext = null;

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

  function readLineageRunAsInput() {
    const value = $id("lineageRunAsInput").value;
    return value ? JSON.parse(value) : null;
  }

  async function loadLineageRunAsUsers(options = {}) {
    try {
      const users = await window.PortalLineageCatalogs.users(options);
      state.users = Array.isArray(users) ? users : [];
      state.usersCatalogError = "";
    } catch (error) {
      state.usersCatalogError = String(error.message || error);
    }
    if (!$id("lineageModal")?.hidden) renderLineageRunAsOptions(readLineageRunAsInput());
    return state.users || [];
  }

  function renderLineageRunAsOptions(principal = null) {
    const select = $id("lineageRunAsInput");
    const selected = principal == null ? "" : JSON.stringify(principal);
    const options = (state.users || []).filter(user => user.id).map(user => ({
      value: JSON.stringify({ kind: "user", id: user.id }),
      label: user.name && user.name !== user.id ? `${user.name} · ${user.id}` : user.id,
    }));
    if (selected && !options.some(option => option.value === selected)) {
      options.unshift({ value: selected, label: `${principal.kind || "?"} · ${principal.id || "?"}（当前配置，用户目录未列出）` });
    }
    select.innerHTML = [
      window.PortalI18n.html`<option value="">未指定（Resident 使用自身权限）</option>`,
      ...options.map(option => window.PortalI18n.html`<option value="${esc(option.value)}">${esc(option.label)}</option>`),
    ].join("");
    select.value = selected;
    $id("lineageRunAsCatalogStatus").textContent = state.usersCatalogError
      ? window.PortalI18n.text`用户目录读取失败：${state.usersCatalogError}。已保留当前选择；重新打开设置可重试。`
      : "";
  }

  function prepareLineageRunAsForm(principal) {
    renderLineageRunAsOptions(principal);
    $id("lineageFormError").hidden = true;
    $id("lineageFormError").textContent = "";
    // Reuse the shared catalog; keep the user's selection if the read finishes later.
    void loadLineageRunAsUsers({ force: Boolean(state.usersCatalogError) });
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
    prepareLineageRunAsForm(null);
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
    prepareLineageRunAsForm(state.lineage.run_as_principal ?? null);
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
      const runAsPrincipal = readLineageRunAsInput();
      if (mode === "create") {
        const lineage = await societyService.createLineage({
          title,
          description,
          project_id: projectId,
          familize: $id("lineageFamilizeInput").checked,
          definition: { run_as_principal: runAsPrincipal },
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
          run_as_principal: runAsPrincipal,
          expected_version: Number(event.currentTarget.dataset.revision),
        })).lineage || {};
        if (String(saved?.project_id || "") !== projectId) {
          throw new Error("后端未提交所选项目，请刷新后重试");
        }
        state.lineage.run_as_principal = clone(saved.run_as_principal ?? null);
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
      const deleted = await hatgComponentOptions.onDeleteLineage({
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

  function nodeAccountConfig() {
    const selection = state.accountSelection
      && typeof state.accountSelection === "object"
      ? state.accountSelection
      : {};
    const accountIds = [...new Set(
      (Array.isArray(selection.account_ids) ? selection.account_ids : [])
        .map(accountId => String(accountId || "").trim().toLowerCase())
        .filter(Boolean),
    )];
    const configuredDefault = String(selection.default_account_id || "").trim().toLowerCase();
    return {
      enabled: selection.enabled !== false && accountIds.length > 0,
      accountIds,
      defaultAccountId: accountIds.includes(configuredDefault)
        ? configuredDefault
        : (accountIds[0] || ""),
    };
  }

  function renderNodeProviderFields() {
    const definition = state.agentBackendCatalog?.backends?.find(row => row.backend === $id("nodeBackendSelect")?.value) || {};
    const fields = definition.runtime_settings || [];
    const routes = definition.routes || [];
    const route = $id("nodeRouteSelect");
    const model = $id("nodeModelSelect");
    const effort = $id("nodeEffortSelect");
    if (!route || !model || !effort) return;
    const previousRoute = state.nodeRouteChosen ? route.value : "";
    route.replaceChildren(...routes.map(item => new Option(item.label, item.backend_route_id)));
    // The route is the one account the new Resident uses. Until the user picks one, a
    // Provider that selects accounts for Residents starts on the account a new session uses by default.
    const defaultAccount = definition.account_selection?.enabled ? nodeAccountConfig().defaultAccountId : "";
    const preferred = routes.some(item => item.backend_route_id === previousRoute)
      ? previousRoute
      : routes.find(item => defaultAccount && String(item.account_id || "").toLowerCase() === defaultAccount)?.backend_route_id;
    if (preferred) route.value = preferred;
    const selected = routes.find(item => item.backend_route_id === route.value);
    const catalog = window.PortalLineageCatalogs.routeRuntimeCatalog(selected, renderNodeProviderFields);
    const previousModel = model.value;
    const models = catalog?.models || [];
    model.replaceChildren(...models.map(item => new Option(item, item)));
    model.value = models.includes(previousModel) ? previousModel : catalog?.default_model || "";
    const previousEffort = effort.value;
    const efforts = catalog?.efforts_by_model?.[model.value] || [];
    effort.replaceChildren(new Option("Provider default", ""), ...efforts.map(item => new Option(item, item)));
    effort.value = efforts.includes(previousEffort) ? previousEffort : "";
    for (const input of [route, model, effort]) {
      const enabled = input === route ? routes.some(row => row.backend_route_id) : fields.includes(input === model ? "model" : "reasoning_effort");
      input.closest("[data-node-provider-setting]").hidden = !enabled;
      input.disabled = !enabled;
      input.required = enabled && input !== effort;
    }
    $id("nodeServiceTierInput").closest("label").hidden = !fields.includes("service_tier");
  }

  function openNodeModal() {
    if (!state.lineage) return;
    $id("nodeFormError").hidden = true;
    $id("nodeFormError").textContent = "";
    $id("nodeTitleInput").value = "";
    $id("nodeServiceTierInput").value = "";
    // A new Resident starts on the first Provider of the server's catalog.
    $id("nodeBackendSelect").selectedIndex = 0;
    state.nodeRouteChosen = false;
    $id("nodeTitleInput").required = true;
    renderNodeProviderFields();
    openModal("nodeModal");
  }

  function nextNodePosition() {
    const actual = [
      ...(state.lineage?.nodes || []),
      ...(displayedProgram().components || []),
      ...(state.lineage?.function_blocks || []),
    ].map(item => ({ x: Number(item?.x), y: Number(item?.y) }))
      .filter(position => Number.isFinite(position.x) && Number.isFinite(position.y));
    const visibleBounds = state.graphController?.getVisibleWorldBounds?.();
    return programFeature.nextPlacement(actual, visibleBounds);
  }

  async function submitNodeForm(event) {
    event.preventDefault();
    if (!state.lineage) return;
    const button = $id("nodeSubmitBtn");
    $id("nodeFormError").hidden = true;
    $id("nodeFormError").textContent = "";
    setBusy(button, true, "创建中…");
    try {
      if (state.layoutSaveInFlight) await state.layoutSaveInFlight;
      await flushLineageLayoutSave();
      const nearResidentId = nodeById(state.selectedNodeId)?.id || state.lineage.leader_resident_id;
      const family = familyForNewResident(state.selectedNodeId);
      const title = $id("nodeTitleInput").value.trim();
      if (!title) throw new Error("新 Resident 需要名称");
      const provider = $id("nodeBackendSelect").value;
      const route = $id("nodeRouteSelect").value;
      const settings = {model: $id("nodeModelSelect").value};
      if (!$id("nodeEffortSelect").disabled) settings.reasoning_effort = $id("nodeEffortSelect").value;
      if (!$id("nodeServiceTierInput").closest("label").hidden && $id("nodeServiceTierInput").value === "fast") settings.service_tier = "priority";
      const response = await societyService.createNode({
        lineage_id: state.lineage.lineage_id,
        title,
        ...(provider ? { provider } : {}),
        ...(route ? { account_id: route } : {}),
        ...(settings && Object.keys(settings).length ? { settings } : {}),
        near_resident_id: nearResidentId,
        placement: residentPlacementGeometry({}, { title }),
        ...(family ? { family_id: family.instance.id } : {}),
      });
      // Selecting the new node below draws it.
      commitLineage(response.lineage, { render: false });
      const node = (state.lineage.nodes || []).find(item => item.id === response.resident_id) || { id: response.resident_id, title };
      const record = { resident_id: response.resident_id, lineage_id: state.lineage.lineage_id, title };
      state.residents.unshift(record);
      state.selectedNodeId = node.id;
      closeModal("nodeModal");
      await selectNode(node.id, { force: true, notifyParent: false });
      // The server may place the first Resident of a new Lineage outside the current view.
      await new Promise(resolve => window.requestAnimationFrame(resolve));
      if (state.lineage?.lineage_id === record.lineage_id) state.graphController?.ensureNodeVisible?.(node.id);
      toast(window.PortalI18n.text`${node.title} 已创建。`);
    } catch (error) {
      if (error.committedMutation?.instruction === "CreateResident") {
        closeModal("nodeModal");
        setSyncStatus("Resident 已创建，图刷新失败", "error");
        toast(window.PortalI18n.text`Resident 已创建，请刷新查看，勿重复创建：${error.message || error}`, "warning", 9000);
      } else {
        $id("nodeFormError").textContent = error.message || String(error);
        $id("nodeFormError").hidden = false;
        toast(error.message || String(error), "error", 5200);
      }
    } finally {
      setBusy(button, false);
    }
  }

  function openNodeSettingsModal(requestedNode = currentNode()) {
    const node = displayNodeById(requestedNode?.id) || requestedNode;
    if (!node) return;
    return window.PortalNav.openResidentMenu({
      residentId: nodeResidentId(node), node: {...node, intake_state: nodeIntakeState(node)}, lineage: state.lineage,
      ...residentMenuHandlers(),
    });
  }

  function residentMenuHandlers() {
    return {
      onDelete: node => removeSelectedNode({nodeId: node.id}),
      onChanged: async ({type, residentId, lineageId, targetLineageId, result}) => {
        if (state.lineage?.lineage_id !== lineageId) return;
        if (type === "rename") {
          commitLineage(result.lineage);
        } else if (type === "intake") {
          renderGraph();
        } else if (type === "delete-family" || type === "unfamilize" || type === "family-members") {
          window.PortalResidentProjection?.invalidate?.(residentId);
          commitLineage(await societyService.loadLineage(lineageId));
          if (type === "unfamilize") revealUnfamilizedMember(lineageId, result);
        } else if (type === "move") {
          // The moved Resident keeps its identity and conversation, so the
          // canvas follows it into the receiving Lineage and its Chat stays
          // open there instead of passing to the source Lineage's Leader.
          window.PortalResidentProjection?.invalidate?.(residentId);
          hatgComponentOptions.onLocalTargetChange?.({lineageId: targetLineageId, residentId});
          await loadLineagePageData({forceCatalogs: true});
          await selectLineage(targetLineageId, {residentId, revealInGraph: true});
          if (state.lineage?.lineage_id !== targetLineageId) return;
          const moved = state.lineage.nodes.find(node => node.id === residentId || nodeResidentId(node) === residentId);
          if (moved) {
            await selectNode(moved.id, {force: true, lineageId: targetLineageId});
          } else {
            clearNodeSelection();
            renderLineageSurface();
          }
        }
      },
    };
  }

  function sourceCanBeDeletedAfterAbsorption(node) {
    return !!node && node.id !== state.lineage?.leader_resident_id;
  }

  function openNodeAbsorptionModal(source, target) {
    state.absorptionDecision = {
      sourceNodeId: source.id,
      targetNodeId: target.id,
      delivered: false,
    };
    $id("nodeAbsorptionTitle").textContent = window.PortalI18n.text`${target.title} 正在吸收 ${source.title}`;
    const sourceParticipantId = source.id;
    const targetParticipantId = target.id;
    $id("nodeAbsorptionRoute").innerHTML = window.PortalI18n.html`<div class="nodeAbsorptionRouteCard"><span>来源节点 A</span><strong>${esc(source.title)}</strong><small>统一 ID · ${esc(sourceParticipantId)}</small></div><div class="nodeAbsorptionRouteArrow"><b>→</b><small>吸收</small></div><div class="nodeAbsorptionRouteCard target"><span>吸收节点 B</span><strong>${esc(target.title)}</strong><small>统一 ID · ${esc(targetParticipantId)}</small></div>`;
    const status = $id("nodeAbsorptionStatus");
    status.dataset.state = "preparing";
    status.querySelector("strong").textContent = window.PortalI18n.t("正在向目标节点发送普通 Prompt");
    status.querySelector("small").textContent = window.PortalI18n.t("消息只包含来源 Resident ID 与吸收要求；资源引用由标准链路处理。");
    $id("nodeAbsorptionMode").innerHTML = window.PortalI18n.markup("<strong>普通 Prompt</strong><span>目标节点只会收到一条包含来源 Resident 统一 ID 的普通消息；资源引用继续使用全局标准链路。</span>");
    const deleteButton = $id("nodeAbsorptionDeleteBtn");
    deleteButton.disabled = true;
    deleteButton.textContent = window.PortalI18n.text`删除 ${source.title}`;
    $id("nodeAbsorptionKeepBtn").textContent = window.PortalI18n.text`保留 ${source.title}`;
    const retentionHint = $id("nodeAbsorptionRetentionHint");
    if (sourceCanBeDeletedAfterAbsorption(source)) {
      retentionHint.textContent = window.PortalI18n.t("保留后可继续独立讨论；删除会将 Resident 标记为已删除，并移除 Session 和图引用；关联 Workspace 仅在没有其他存活 Resident 引用时回收，仍被引用的 Workspace 保留。对话与运行轨迹保留。");
    } else {
      retentionHint.textContent = window.PortalI18n.t("来源节点是当前 Leader，不能单独删除；请先变更 Leader。");
    }
    openModal("nodeAbsorptionModal");
  }

  function updateNodeAbsorptionModal(source, target, response = null, error = null) {
    const decision = state.absorptionDecision;
    if (!decision || decision.sourceNodeId !== source.id || decision.targetNodeId !== target.id) return;
    const status = $id("nodeAbsorptionStatus");
    const statusTitle = status.querySelector("strong");
    const statusDetail = status.querySelector("small");
    const deleteButton = $id("nodeAbsorptionDeleteBtn");
    const failed = !!error;
    decision.delivered = !failed;
    status.dataset.state = failed ? "failed" : "queued";
    if (failed) {
      statusTitle.textContent = window.PortalI18n.t("吸收任务未能启动");
      statusDetail.textContent = String(error?.message || error || window.PortalI18n.t("目标节点运行时不可用"));
      $id("nodeAbsorptionMode").innerHTML = window.PortalI18n.markup("<strong>来源节点已保留</strong><span>吸收没有成功投递，因此这里不会提供删除来源节点的操作。</span>");
      deleteButton.disabled = true;
      return;
    }
    statusTitle.textContent = window.PortalI18n.text`${target.title} 已开始执行吸收工作`;
    statusDetail.textContent = response?.message_id
      ? window.PortalI18n.text`目标消息：${response.message_id}`
      : window.PortalI18n.t("任务已进入目标节点的运行队列。");
    $id("nodeAbsorptionMode").innerHTML = window.PortalI18n.html`<strong>普通 Prompt 已投递</strong><span>来源统一 ID：${esc(response?.source_resident_id || source.id || "—")}。消息不包含专属读取命令、Portal 路径或来源对话正文。</span>`;
    deleteButton.disabled = !sourceCanBeDeletedAfterAbsorption(source);
  }

  async function absorbNodeIntoTarget(sourceNodeId, targetNodeId) {
    if (state.absorptionInFlight || !state.lineage) return;
    const source = nodeById(sourceNodeId);
    const target = nodeById(targetNodeId);
    if (!source || !target || source.id === target.id) return;
    openNodeAbsorptionModal(source, target);
    state.absorptionInFlight = true;
    renderGraph();
    try {
      const response = await societyService.absorbNode( {
        lineage_id: state.lineage.lineage_id,
        source_resident_id: source.id,
        target_resident_id: target.id,
      });
      if (!response?.lineage || response.lineage.lineage_id !== state.lineage.lineage_id) {
        throw new Error("节点吸收响应缺少 Lineage 状态");
      }
      commitLineage(response.lineage, { afterMerge: () => setNodeRuntimeStatus(target.id, "queued") });
      updateNodeAbsorptionModal(source, target, response);
      selectNode(target.id, { force: true }).catch(() => {});
      toast(window.PortalI18n.text`${target.title} 已收到包含 ${source.title} 统一 ID 的普通 Prompt。`, "success", 5200);
    } catch (error) {
      updateNodeAbsorptionModal(source, target, null, error);
      toast(window.PortalI18n.text`节点吸收失败：${error.message || error}`, "error", 7000);
    } finally {
      state.absorptionInFlight = false;
      renderGraph();
    }
  }

  function keepAbsorbedSourceNode() {
    const source = nodeById(state.absorptionDecision?.sourceNodeId);
    closeModal("nodeAbsorptionModal");
    state.absorptionDecision = null;
    if (source) toast(window.PortalI18n.text`已保留 ${source.title}，可以继续独立讨论。`, "success");
  }

  async function deleteAbsorbedSourceNode() {
    const decision = state.absorptionDecision;
    const source = nodeById(decision?.sourceNodeId);
    if (!decision?.delivered || !source || !sourceCanBeDeletedAfterAbsorption(source)) return;
    const button = $id("nodeAbsorptionDeleteBtn");
    setBusy(button, true, "删除中…");
    const deleted = await removeSelectedNode({ nodeId: source.id, skipConfirm: true });
    if (deleted) {
      closeModal("nodeAbsorptionModal");
      state.absorptionDecision = null;
    } else {
      setBusy(button, false);
    }
  }

  async function removeSelectedNode(options = {}) {
    const node = nodeById(options.nodeId) || currentNode();
    if (!node || !state.lineage) return false;
    const family = familyForFacade(node.id);
    if (family) return Boolean(await removeFamilyInstance(family.instance.id, "delete"));
    const incidentEdges = state.lineage.edges.filter(edge => lineageEdgeReferencesResident(edge, node.id));
    const incomingInheritance = incidentEdges
      .filter(edge => edge.type === "inheritance" && edge.target === node.id)
      .sort((left, right) => Number(left.truth?.ancestor_distance || 1) - Number(right.truth?.ancestor_distance || 1));
    const outgoingInheritance = incidentEdges.filter(edge => edge.type === "inheritance" && edge.source === node.id);
    const predecessor = incomingInheritance[0] || null;
    const reconnectCount = predecessor
      ? outgoingInheritance.filter(edge => edge.target !== predecessor.source && !state.lineage.edges.some(item => (
          item.type === "inheritance" && item.source === predecessor.source && item.target === edge.target
        ))).length
      : 0;
    const reconnectNotice = reconnectCount
      ? window.PortalI18n.text`\n\n${reconnectCount} 个后继 Resident 会自动连接到「${nodeById(predecessor.source)?.title || window.PortalI18n.t("前驱节点")}」。`
      : "";
    const leaderNotice = nodeResidentId(node) === String(state.lineage.leader_resident_id || "")
      ? window.PortalI18n.t("\n\n它是这个 Lineage 的 Leader；删除后系统从其余 Resident 中随机选出新的 Leader。")
      : "";
    const identityNotice = window.PortalI18n.t("这会将 Resident 标记为已删除，并移除 Session、图引用与自有工作区；对话记录与运行轨迹保留。") + leaderNotice;
    if (!options.skipConfirm && !await window.PortalUI.confirm({
      title: window.PortalI18n.t("删除 HATG 节点？"),
      message: window.PortalI18n.text`从 HATG 中删除「${node.title}」？\n\n${identityNotice}${reconnectNotice}`,
      confirmLabel: window.PortalI18n.t("删除节点"),
      tone: "danger",
    })) return false;

    try {
        const result = await societyService.deleteNode( {
          lineage_id: state.lineage.lineage_id,
          resident_id: node.id,
        });
        if (!result?.lineage || result.lineage.lineage_id !== state.lineage.lineage_id) {
          throw new Error("删除节点的响应缺少 Lineage 状态");
        }
        state.residents = state.residents.filter(item => String(item.resident_id || "") !== nodeResidentId(node));
        commitLineage(result.lineage, {
          // The deleted node's Chat reports the deletion; no other node is selected for it.
          afterMerge: () => { if (state.selectedNodeId === node.id) clearNodeSelection(); },
        });
        const currentRevision = Number(state.lineage.revision || 0);
        setSyncStatus(currentRevision ? `状态已保存 · revision ${currentRevision}` : "状态已保存", "saved");
        void Promise.allSettled([loadResults()]);
        const reconnected = Number(result.reconnected_edge_count || 0);
        if (!options.quiet) {
          toast(
            reconnected
              ? window.PortalI18n.text`节点与对应 Resident session 已删除，并自动重连 ${reconnected} 条继承边。`
              : window.PortalI18n.t("节点与对应 Resident session 已同步删除。"),
            "success",
            6000,
          );
        }
      return true;
    } catch (error) {
      if (error.committedMutation?.instruction === "DeleteResident") {
        toast(window.PortalI18n.text`Resident 已删除，图刷新失败，请刷新查看，勿重复删除：${error.message || error}`, "warning", 9000);
        return true;
      }
      toast(window.PortalI18n.text`删除失败，HATG 未改变：${error.message || error}`, "error", 7000);
      return false;
    }
  }

  const branchMessageRole = item => window.PortalChatWorkspace.messageRole(item);

  function messageSelector(item) {
    return {
      message_id: window.PortalChatWorkspace.messageIdentity(item),
    };
  }

  function updateBranchAccountHint() {
    const mode = state.branchMessageContext
      ? "message"
      : ($id("branchModeChoices").querySelector('input[name="branchMode"]:checked')?.value || "full");
    $id("branchSubmitBtn").textContent = mode === "new-session"
      ? window.PortalI18n.t("创建 new session")
      : (mode === "message" ? window.PortalI18n.t("从这里创建分支") : window.PortalI18n.t("创建继承分支"));
    const field = $id("branchAccountField");
    const select = $id("branchAccountInput");
    const hint = $id("branchAccountHint");
    if (!field || field.hidden || !select || !hint) return;
    const accountId = String(select.value || "").trim();
    const sourceAccountId = String(select.dataset.sourceAccount || "").trim();
    if (!select.options.length) {
      hint.textContent = window.PortalI18n.t("该 Provider 暂无可用账号。");
      return;
    }
    if (mode === "new-session") {
      hint.textContent = window.PortalI18n.text`将在 ${accountId} 账号创建空白 Session，不继承来源节点的对话。`;
      return;
    }
    if (sourceAccountId && accountId === sourceAccountId) {
      hint.textContent = window.PortalI18n.text`默认沿用来源 Session 的 ${sourceAccountId} 账号。`;
      return;
    }
    hint.textContent = window.PortalI18n.text`跨账号分支：将在 ${accountId} 账号建立独立 Session，并继承所选上下文。`;
  }

  function configureBranchProvider(provider) {
    if (!branchSourceSnapshot) return;
    const selection = window.PortalLineageCatalogs.renderForkProvider({
      ...branchSourceSnapshot,
      providerSelect: $id("branchProviderInput"), accountSelect: $id("branchAccountInput"), provider,
    });
    const compacted = $id("branchModeChoices").querySelector('[value="compacted"]');
    compacted.disabled = !!branchSourceSnapshot.message || !selection.definition?.seeded_compaction;
    compacted.closest("label").title = compacted.disabled
      ? window.PortalI18n.t("目标 Provider 不支持压缩继承。") : "";
    if (compacted.disabled && compacted.checked) {
      $id("branchModeChoices").querySelector('[value="full"]').checked = true;
    }
    $id("branchSubmitBtn").disabled = !selection.available;
    updateBranchAccountHint();
  }

  let branchModalRequestSeq = 0;
  let branchSourceSnapshot = null;

  async function openBranchModal(message = null) {
    const node = currentNode();
    if (!node || familyForFacade(node.id)) return;
    const requestSeq = ++branchModalRequestSeq;
    const lineageId = state.lineage?.lineage_id;
    const selectionSeq = state.detailSeq;
    let sourceDetail = state.detail;
    let catalog;
    try {
      [sourceDetail, catalog] = await Promise.all([
        sourceDetail || societyService.loadNodeMetadata({
          lineageId: state.lineage?.lineage_id,
          residentId: node.id,
        }), window.PortalLineageCatalogs.agentBackends(),
      ]);
    } catch (error) {
      if (requestSeq !== branchModalRequestSeq) return;
      toast(window.PortalI18n.text`读取 Provider 和账号失败：${error.message || error}`, "error", 5600);
      return;
    }
    if (requestSeq !== branchModalRequestSeq || selectionSeq !== state.detailSeq
      || currentNode()?.id !== node.id || state.lineage?.lineage_id !== lineageId
      || familyForFacade(node.id)) return;
    const anchoredMessage = message && branchMessageRole(message) === "human"
      ? messageSelector(message)
      : null;
    // A dialog owns the displayed source until it is explicitly reopened.
    // Selection and metadata refreshes must not retarget an accepted form.
    branchSourceSnapshot = Object.freeze({ residentId: node.id, lineageId, message: anchoredMessage,
      source: clone(node), detail: clone(sourceDetail), catalog });
    state.branchMessageContext = anchoredMessage ? {
      source_resident_id: node.id,
      message: anchoredMessage,
    } : null;
    $id("branchSourceSummary").innerHTML = window.PortalI18n.html`<strong>${esc(node.title)}</strong><br>${esc(nodeResidentId(node))}`;
    $id("branchModalTitle").textContent = anchoredMessage ? window.PortalI18n.t("从这条消息建立分支") : window.PortalI18n.t("从当前节点建立分支");
    const messageAnchor = $id("branchMessageAnchor");
    messageAnchor.hidden = !anchoredMessage;
    $id("branchMessageAnchorText").textContent = anchoredMessage
      ? `${formatDate(anchoredMessage.created_at) || window.PortalI18n.t("当前会话")} · ${compactText(anchoredMessage.content, 240)}`
      : "";
    $id("branchModeChoices").hidden = !!anchoredMessage;
    $id("branchTitleInput").value = anchoredMessage ? `${node.title} · 从此处继续` : `${node.title} · 分支`;
    $id("branchModeChoices").querySelectorAll('input[name="branchMode"]').forEach(input => {
      if (anchoredMessage) input.disabled = true;
      else input.disabled = false;
      input.checked = input.value === "full";
    });
    configureBranchProvider();
    $id("branchSubmitBtn").textContent = anchoredMessage ? window.PortalI18n.t("从这里创建分支") : window.PortalI18n.t("创建继承分支");
    openModal("branchModal");
  }

  function residentPlacementGeometry(source = {}, options = {}) {
    return state.graphController.getPlacementGeometry({
      title: options.title || window.PortalI18n.text`${source.title} · 分支`,
      status: "idle",
    });
  }

  async function createAdjacentResident(sourceNodeId, options = {}) {
    if (!state.lineage || !sourceNodeId) return null;
    if (options.mode !== "new-session" && familyForFacade(sourceNodeId)) return null;
    if (state.structureMutationInFlight) {
      toast(window.PortalI18n.t("另一项图结构操作正在进行，请稍候。"), "warning", 4200);
      return null;
    }
    const mode = String(options.mode || "full");
    const fresh = mode === "new-session";
    const actionLabel = fresh ? window.PortalI18n.t("创建会话") : window.PortalI18n.t("创建分支");
    const button = options.button || null;
    const quick = options.quick === true;
    if (button) setBusy(button, true, `${actionLabel}…`);
    state.structureMutationInFlight = true;
    if (quick) {
      state.quickForkNodeId = sourceNodeId;
      renderGraph();
    }
    setSyncStatus(fresh ? "创建空白会话并写入 HATG…" : "执行 Fork 操作并写入 HATG…", "saving");
    try {
      if (state.layoutSaveInFlight) await state.layoutSaveInFlight;
      await flushLineageLayoutSave();
      if (options.sourceLineageId && state.lineage?.lineage_id !== options.sourceLineageId) {
        throw new Error("来源 Lineage 已切换，请重新打开分支对话框");
      }
      const source = nodeById(sourceNodeId);
      if (!source) throw new Error("来源节点已不在当前 HATG 中");
      if (!fresh && familyForFacade(source.id)) return null;
      const payload = {
        lineage_id: state.lineage.lineage_id,
        ...(fresh
          ? { provider: options.provider || source.backend }
          : { source_resident_id: source.id, fork_mode: mode }),
        title: String(options.title || "").trim() || `${source.title || source.id} · ${fresh ? window.PortalI18n.t("new session") : window.PortalI18n.t("分支")}`,
        near_resident_id: source.id,
        placement: residentPlacementGeometry(source, options),
      };
      const family = familyForNewResident(source.id);
      if (options.provider) payload.provider = options.provider;
      if (family) payload.family_id = family.instance.id;
      if (options.backendRouteId) payload.account_id = String(options.backendRouteId);
      if (mode === "message") {
        payload.message = options.message;
      }
      const response = fresh
        ? await societyService.createNode(payload)
        : await societyService.branchNode(payload);
      if (!response?.lineage || response.lineage.lineage_id !== state.lineage.lineage_id) {
        throw new Error(`${actionLabel}的响应缺少 Lineage 状态`);
      }
      commitLineage(response.lineage);
      const createdResidentId = String(response.resident_id || "");
      const node = state.lineage.nodes.find(item => item.id === createdResidentId);
      if (!node) throw new Error(`${actionLabel}的响应缺少新节点`);
      if (options.closeModalOnSuccess) closeModal("branchModal");
      // A successful Fork opens the child through the same navigation as a
      // card click. Creating a blank canvas node only selects its card.
      selectNode(node.id, { force: true, notifyParent: !fresh }).catch(() => {});
      const currentRevision = Number(state.lineage.revision || 0);
      const completedLabel = fresh ? window.PortalI18n.t("空白会话已创建") : window.PortalI18n.t("分支已创建");
      setSyncStatus(currentRevision ? `${completedLabel} · revision ${currentRevision}` : completedLabel, "saved");
      if (!options.suppressSuccessToast) {
        toast(
          options.successMessage
            || (fresh ? window.PortalI18n.t("已创建空白会话。") : (mode === "message" ? window.PortalI18n.t("已从所选消息 Fork 出新的 Resident。") : window.PortalI18n.t("已 Fork 出新的 Resident。"))),
          "success",
          4600,
        );
      }
      return node;
    } catch (error) {
      if (["CreateResident", "ForkResident"].includes(error.committedMutation?.instruction)) {
        if (options.closeModalOnSuccess) closeModal("branchModal");
        setSyncStatus(`${actionLabel}已完成，图刷新失败`, "error");
        toast(window.PortalI18n.text`${actionLabel}已完成，请刷新查看，勿重复提交：${error.message || error}`, "warning", 9000);
      } else {
        setSyncStatus(`${actionLabel}失败`, "error");
        toast(window.PortalI18n.text`${actionLabel}失败：${error.message || error}`, "error", 6000);
      }
      return null;
    } finally {
      state.structureMutationInFlight = false;
      if (quick) {
        state.quickForkNodeId = "";
        renderGraph();
      }
      bindResidentStateCollection();
      if (button) setBusy(button, false);
    }
  }

  async function createQuickFork(node, mode) {
    if (node?.kind === "seed") return null;
    const source = nodeById(node?.id);
    if (!source) return null;
    return createAdjacentResident(source.id, {
      mode,
      quick: true,
      suppressSuccessToast: true,
    });
  }

  async function submitBranch(event) {
    event.preventDefault();
    const source = branchSourceSnapshot;
    if (!source) return;
    if (state.lineage?.lineage_id !== source.lineageId || !nodeById(source.residentId)) {
      toast(window.PortalI18n.t("来源节点已不在当前 HATG 中，请重新打开分支对话框"), "error", 6000);
      return;
    }
    const mode = source.message
      ? "message"
      : ($id("branchModeChoices").querySelector('input[name="branchMode"]:checked')?.value || "full");
    const accountField = $id("branchAccountField");
    const accountInput = $id("branchAccountInput");
    await createAdjacentResident(source.residentId, {
      sourceLineageId: source.lineageId,
      mode,
      title: $id("branchTitleInput").value.trim(),
      provider: $id("branchProviderInput").value,
      backendRouteId: !accountField.hidden && accountInput.value ? accountInput.value : "",
      message: source.message,
      button: $id("branchSubmitBtn"),
      closeModalOnSuccess: true,
    });
  }

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

  function cancelGraphInteractionMode() {
    if (state.handoffMode) cancelHandoffMode();
  }

  function renderGraphModeHint() {
    const hint = $id("graphModeHint");
    if (!hint) return;
    hint.hidden = !state.handoffMode;
    if (hint.hidden) return;
    const source = nodeById(state.handoffSourceId);
    $id("graphModeHintText").textContent = source
      ? window.PortalI18n.text`交接来源：${source.title}。请选择接收工件与任务契约的目标节点。`
      : window.PortalI18n.t("先选择交接来源节点，再选择目标节点。");
    $id("cancelGraphModeBtn").textContent = window.PortalI18n.t("退出交接模式");
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

  function handoffEditorFieldId(base, index) {
    return index === 0 ? base : `${base}_${index + 1}`;
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

  function handoffEditorField(card, name) {
    return card?.querySelector(`[data-handoff-field="${name}"]`) || null;
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
      if (response.handoff?.status === "ready") {
        toast(window.PortalI18n.t("Handoff 已就绪，目标 Workspace Binding 已验证。"), "success", 5000);
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

  async function stopAllRunningNodes() {
    if (!state.lineage || state.globalStopInFlight) return;
    const targets = runningLineageNodes();
    if (!targets.length) {
      toast(window.PortalI18n.t("当前 Lineage 没有正在运行的节点。"), "warning");
      return;
    }
    const lineageId = state.lineage.lineage_id;
    state.globalStopInFlight = true;
    targets.forEach(node => state.nodeStopInFlight.add(node.id));
    renderGraph();
    renderControls();
    const failures = [];
    let stoppedCount = 0;
    try {
      for (const snapshot of targets) {
        if (state.lineage?.lineage_id !== lineageId) break;
        const target = nodeById(snapshot.id);
        if (!target) continue;
        if (effectiveNodeStatus(target) !== "running") continue;
        try {
          if (await stopResidentTurn(target, { runningOnly: true })) stoppedCount += 1;
        } catch (error) {
          failures.push({
            title: target.title || target.id,
            error: String(error?.message || error),
          });
        }
      }
      if (state.lineage?.lineage_id !== lineageId) return;
      await refreshAfterResidentStop(null);
      if (!failures.length) {
        toast(window.PortalI18n.text`已 Stop 当前 Lineage 的 ${stoppedCount} 个运行中 Session。`, "warning", 5200);
      } else {
        const failedNames = failures.map(item => item.title).join("、");
        toast(
          window.PortalI18n.text`${stoppedCount} 个 Session 已 Stop；${failures.length} 个节点失败（${failedNames}）。`,
          failures.length === targets.length ? "error" : "warning",
          7200,
        );
      }
    } finally {
      state.globalStopInFlight = false;
      targets.forEach(node => state.nodeStopInFlight.delete(node.id));
      if (state.lineage?.lineage_id === lineageId) renderLineageSurface();
    }
  }

  function autoLayoutScope() {
    return `${state.lineage?.lineage_id || ""}:${focusedFamily()?.roles.facade || ""}`;
  }

  function renderAutoLayoutControl() {
    const button = $id("autoLayoutBtn");
    if (!button) return;
    const workspaceProjection = state.graphViewMode === "workspace";
    const density = workspaceProjection ? "" : state.autoLayoutDensityByScope.get(autoLayoutScope());
    button.disabled = !state.lineage || state.autoLayoutInFlight;
    button.classList.toggle("busy", state.autoLayoutInFlight);
    button.setAttribute("aria-busy", state.autoLayoutInFlight ? "true" : "false");
    button.dataset.layoutDensity = density || "";
    button.setAttribute("aria-pressed", density === "compact" ? "true" : "false");
    const tooltip = workspaceProjection
      ? window.PortalI18n.t("重新自动排列 Workspace 与 Resident 标记，清除当前用户在此视图的手动位置。")
      : window.PortalI18n.t("自动整理：按分支归组并适配画布。再次点击可在紧凑与舒展布局之间切换。");
    button.dataset.portalTooltip = tooltip;
    button.setAttribute("aria-label", tooltip.replace(/。$/, ""));
    const label = button.querySelector("span");
    if (label) label.textContent = state.autoLayoutInFlight
      ? window.PortalI18n.t("整理中…")
      : density === "comfortable" ? window.PortalI18n.t("紧凑整理")
        : density === "compact" ? window.PortalI18n.t("舒展整理") : window.PortalI18n.t("整理");
  }

  function readableAutoLayoutOptions(items = [], density = "comfortable") {
    const measured = state.graphController?.getNodeSizes?.() || {};
    // Titles and runtime labels wrap. The layout engine uses uniform cells,
    // so each cell must accommodate the largest card actually being arranged.
    const metrics = { ...AUTO_LAYOUT_METRICS.agent };
    items.forEach(item => {
      const size = measured[item.id];
      if (size?.width > 0) metrics.width = Math.max(metrics.width, size.width / 100 * HATG_WORLD_SIZE.width);
      if (size?.height > 0) metrics.height = Math.max(metrics.height, size.height / 100 * HATG_WORLD_SIZE.height);
    });
    const compact = density === "compact";
    const horizontalPitch = metrics.width + (compact ? 32 : metrics.columnGap);
    const verticalPitch = metrics.height + (compact ? 12 : metrics.rowGap);
    const viewport = $id("hatgCanvas")?.getBoundingClientRect();
    return {
      horizontalGap: Math.round(horizontalPitch / HATG_WORLD_SIZE.width * 10000) / 100,
      verticalGap: Math.round(verticalPitch / HATG_WORLD_SIZE.height * 10000) / 100,
      nodeWidth: Math.round(metrics.width / HATG_WORLD_SIZE.width * 10000) / 100,
      nodeHeight: Math.round(metrics.height / HATG_WORLD_SIZE.height * 10000) / 100,
      maxRowsPerLane: metrics.maxRows,
      ...(compact ? {
        componentGap: 3,
        componentColumnGap: 3,
        componentWrapSpan: 0,
        // Layout coordinates are percentages of a non-square world.
        componentAspectRatio: viewport?.width > 0 && viewport?.height > 0
          ? (viewport.width / viewport.height) * (HATG_WORLD_SIZE.height / HATG_WORLD_SIZE.width)
          : 1,
      } : {}),
    };
  }

  async function autoLayoutGraph() {
    if (!state.lineage) return;
    if (state.autoLayoutInFlight) return;
    const computeLayout = window.PortalHatgGraph?.computeBranchAwareLayout;
    if (typeof computeLayout !== "function") return toast(window.PortalI18n.t("图布局引擎尚未就绪。"), "error");

    if (state.graphViewMode === "workspace") {
      scheduleHatgViewPreferenceSave(state.lineage.lineage_id, { workspace_positions: {} });
      renderGraph();
      window.requestAnimationFrame(() => state.graphController?.fit?.({ duration: 220, maxScale: 1.15 }));
      return;
    }

    // Arrange the exact unified Context projection that is rendered: Resident
    // nodes, Event/Effect primitives, persistent Function Blocks, seeds, typed
    // wires, and Context relations. Positions are then written back to the
    // corresponding persisted objects instead of mutating projection copies.
    const layoutModel = graphVisibleModel();
    const family = focusedFamily();
    const scope = autoLayoutScope();
    const previousDensity = state.autoLayoutDensityByScope.get(scope);
    const density = previousDensity === "comfortable" ? "compact" : "comfortable";
    const lineageId = state.lineage.lineage_id;
    const leaderId = family?.roles.facade || state.lineage.leader_resident_id;
    const visibleNodes = family ? layoutModel.nodes : layoutModel.overviewNodes;
    const visibleEdges = family ? layoutModel.edges : layoutModel.overviewEdges;
    const items = visibleNodes
      .map(item => graphItemById(item.id))
      .filter(Boolean);
    if (!items.length) return;
    const itemIds = new Set(items.map(item => item.id));
    const layoutNodes = visibleNodes.filter(item => itemIds.has(item.id));
    const layoutEdges = visibleEdges.filter(edge => (
      !edge.draft_preview
      && itemIds.has(edge.source)
      && itemIds.has(edge.target)
    ));
    const positions = computeLayout(layoutNodes, layoutEdges, {
      ...readableAutoLayoutOptions(layoutNodes, density),
      // Object bindings establish membership, without ordering execution.
      feedbackEdgeTypes: ["watch", "target"],
      leaderId,
      rootIds: [leaderId],
    });
    if (!positions.size) return;

    const previousLineage = clone(state.lineage);
    const previousFocusMode = state.graphFocusMode;
    let changed = false;
    items.forEach(item => {
      const position = positions.get(item.id);
      if (!position) return;
      changed = changed
        || Math.abs(Number(item.x || 0) - position.x) > 0.01
        || Math.abs(Number(item.y || 0) - position.y) > 0.01;
      item.x = position.x;
      item.y = position.y;
    });
    // Layout-only edits precede the server's layout_revision. Rebuild the
    // projected node copies now so rendering and fit use these new positions.
    state.localVersion += 1;
    state.autoLayoutDensityByScope.set(scope, density);
    if (!family) state.graphFocusMode = false;
    if (!changed) {
      renderGraph();
      renderControls();
      window.requestAnimationFrame(() => state.graphController?.fit?.(AUTO_LAYOUT_VIEW));
      toast(window.PortalI18n.t("当前 HATG 已经排列整齐。"));
      return;
    }

    state.autoLayoutInFlight = true;
    renderControls();
    renderGraph();
    window.requestAnimationFrame(() => state.graphController?.fit?.(AUTO_LAYOUT_VIEW));
    try {
      await saveLineageLayout(items);
      if (state.lineage?.lineage_id !== lineageId) return;
      toast(density === "compact"
        ? window.PortalI18n.t("HATG 已紧凑整理，节点位置已保存。")
        : itemIds.has(leaderId)
        ? window.PortalI18n.t("HATG 已整理，Leader 已移到关联节点侧方，节点位置已保存。")
        : window.PortalI18n.t("HATG 已按分支关系整理，节点位置已保存。"));
    } catch (error) {
      if (previousDensity) state.autoLayoutDensityByScope.set(scope, previousDensity);
      else state.autoLayoutDensityByScope.delete(scope);
      if (state.lineage?.lineage_id !== lineageId) return;
      commitLineage(previousLineage, {
        replace: true,
        afterMerge: () => { state.graphFocusMode = previousFocusMode; },
      });
      window.requestAnimationFrame(() => state.graphController?.fit?.({ ...AUTO_LAYOUT_VIEW, duration: 180 }));
      toast(window.PortalI18n.text`自动整理失败：${error.message || error}`, "error");
    } finally {
      state.autoLayoutInFlight = false;
      renderControls();
    }
  }

  const SEED_ACTION_DEFAULTS = {
    implement: seed => `实现种子“${seed.display_title || seed.title}”。先核对可行性，再形成可运行或可交付的实现，并记录验证结果。`,
    analyze: seed => `继续分析种子“${seed.display_title || seed.title}”。补足证据、边界条件和风险，给出是否值得采纳的明确判断。`,
    validate: seed => `独立验证种子“${seed.display_title || seed.title}”。设计最小验证，记录证据，并给出通过、部分通过或不通过结论。`,
    custom: seed => `继续探索种子“${seed.display_title || seed.title}”。结合当前种子内容推进工作，并形成可验收的结果。`,
  };

  // Templates own their shelf, editor, drag handling and application forms.
  function mountTemplates() {
    templateController?.destroy();
    templateController = window.LineageTemplates.mount({
      shelf: $id("templateShelf"),
      canvas: $id("hatgCanvas"),
      getContext: () => ({ lineage_id: state.lineage?.lineage_id || "", leader_resident_id: state.lineage?.leader_resident_id, residents: state.lineage?.nodes || [] }),
      resolveTarget: templateCanvasTarget,
      residentSettingsOptions: () => state.agentBackendCatalog,
      onChange: renderGraph,
      toast,
      // Previews change nothing, so they do not wait for or block graph edits.
      request: (instruction, args) => instruction === "ApplyTemplate" && !args?.dry_run
        ? applyProgramMutation(() => executeHatgInstruction(instruction, args))
        : window.PortalInstructionClient.execute(instruction, args),
    });
  }

  function selectedFamilyCandidateIds() {
    const selected = selectedCanvasObjects();
    const objects = selected.length ? selected : [canvasObjectById(graphSelectedId())].filter(Boolean);
    return objects
      .filter(object => !isProgramComponent(object) && !isFunctionBlockInstance(object) && nodeById(object.id))
      .map(object => object.id);
  }

  async function openFamilize() {
    const residentIds = selectedFamilyCandidateIds();
    if (!residentIds.length) return;
    await templateController.applyFamily({ kind: "selection", resident_ids: residentIds, element_ids: residentIds });
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

  const DEFAULT_LEADER_CHARTER = "持续审视 Lineage 的职责边界、交接质量、验收回路和未决风险。只提出可追踪的结构建议，不自行修改 HATG、创建或停止节点、发送交接或绕过人工确认。";

  function effectiveLeaderNode(lineage = state.lineage) {
    const nodes = lineage?.nodes || [];
    return nodes.find(node => node.id === lineage?.leader_resident_id)
      || null;
  }

  function leaderReviewSnapshot() {
    return {
      lineage_revision: Number(state.lineage?.revision || 0),
      resident_ids: (state.lineage?.nodes || []).map(node => node.id).filter(Boolean).sort(),
      edge_ids: (state.lineage?.edges || []).map(edge => edge.id).filter(Boolean).sort(),
      node_count: (state.lineage?.nodes || []).length,
      edge_count: (state.lineage?.edges || []).length,
    };
  }

  function leaderReviewDelta(previousReview, snapshot) {
    const before = previousReview?.snapshot || {};
    const added = (next, prior) => next.filter(id => !new Set(prior || []).has(id));
    const removed = (prior, next) => (prior || []).filter(id => !new Set(next).has(id));
    if (!previousReview) return { baseline: true, since_revision: null, added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [] };
    return {
      baseline: false,
      since_revision: Number(before.lineage_revision || 0),
      added_nodes: added(snapshot.resident_ids, before.resident_ids),
      removed_nodes: removed(before.resident_ids, snapshot.resident_ids),
      added_edges: added(snapshot.edge_ids, before.edge_ids),
      removed_edges: removed(before.edge_ids, snapshot.edge_ids),
    };
  }

  function leaderDeltaText(delta) {
    if (delta.baseline) return "这是第一份可追踪的 Leader 审视快照。";
    const changes = [
      delta.added_nodes.length ? `新增 ${delta.added_nodes.length} 个节点` : "",
      delta.removed_nodes.length ? `移除 ${delta.removed_nodes.length} 个节点` : "",
      delta.added_edges.length ? `新增 ${delta.added_edges.length} 条关系` : "",
      delta.removed_edges.length ? `移除 ${delta.removed_edges.length} 条关系` : "",
    ].filter(Boolean);
    return changes.length ? `相对 r${delta.since_revision}：${changes.join("；")}。` : `相对 r${delta.since_revision} 没有节点或关系集合变化；仍以当前目标、状态和契约为准。`;
  }

  function leaderGraphSummary(snapshot, delta, profile) {
    const nodes = state.lineage.nodes.map(node => ({
      id: node.id, title: node.title, resident_id: nodeResidentId(node),
      status: effectiveNodeStatus(node),
    }));
    const edges = state.lineage.edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, type: edge.type, label: edge.label, note: edge.note }));
    return JSON.stringify({
      lineage: { id: state.lineage.lineage_id, title: state.lineage.title, revision: snapshot.lineage_revision },
      leader: { resident_id: state.lineage.leader_resident_id, charter: profile.charter, authority: "propose_only" },
      snapshot,
      changes_since_previous_leader_review: delta,
      nodes,
      edges,
    }, null, 2);
  }

  async function submitLeaderRequest(event) {
    event.preventDefault();
    const leader = nodeById($id("leaderNodeSelect").value);
    if (!leader) return;
    const request = $id("leaderRequestInput").value.trim();
    const charter = $id("leaderCharterInput").value.trim() || DEFAULT_LEADER_CHARTER;
    const snapshot = leaderReviewSnapshot();
    const delta = leaderReviewDelta(null, snapshot);
    const profile = { charter };
    const prompt = [
      "[Lineage 持久 Leader 审视]",
      `你是「${state.lineage.title}」当前指定的 Leader 节点。本次审视要求：${charter}`,
      "权限边界：你只能提出建议；不能自行修改 HATG、创建或停止节点、发送交接，或执行任何未获明确授权的操作。",
      `当前图快照为 r${snapshot.lineage_revision}，它覆盖过去对图结构的记忆。${leaderDeltaText(delta)}`,
      request || "请审视当前 HATG 的职责边界、交接质量、验收回路和未决风险。",
      "当前 HATG 的完整快照：",
      "```json",
      leaderGraphSummary(snapshot, delta, profile),
      "```",
      "请按“触发证据、建议操作、影响节点、需要人类确认的部分”逐条输出。若当前快照不足以判断，请明确说明需要读取的工件路径。",
    ].join("\n\n");
    const button = $id("leaderSubmitBtn");
    setBusy(button, true, "设置 Leader 并发送快照…");
    try {
      // UpdateLineage records the Leader; DeliverMessage sends it the snapshot.
      const response = await societyService.updateMetadata({
        lineage_id: state.lineage.lineage_id,
        leader_resident_id: leader.id,
      });
      commitLineage(response.lineage);
      await sendPromptToNode(leader, prompt);
      closeModal("leaderModal");
      toast(window.PortalI18n.t("Leader 已设置；最新快照已发送到左侧 Chat Workspace。"), "success", 6000);
    } catch (error) {
      toast(window.PortalI18n.text`Leader 请求失败：${error.message || error}`, "error", 6000);
    } finally {
      setBusy(button, false);
    }
  }

  async function requestUnifiedPreview(request = {}) {
    const handler = hatgComponentOptions.onPreviewRequest
      || window.PortalPreview?.request;
    if (typeof handler !== "function") {
      throw new Error("统一 Preview 路由尚未初始化。");
    }
    await handler(request);
    return true;
  }

  function resolveWorkspacePreviewPath(value) {
    const path = String(value || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    if (!path) return "";
    const workspace = state.resultWorkspace || {};
    const entries = Array.isArray(workspace.entries) ? workspace.entries : [];
    const exact = entries.find(entry => String(entry?.path || "") === path);
    if (exact) return String(exact.path || path);

    const roots = Array.isArray(workspace.roots) ? workspace.roots : [];
    const directoryIds = new Set(roots.map(root => String(root?.directory_id || "")).filter(Boolean));
    if (directoryIds.has(path.split("/", 1)[0])) return path;

    const relativeMatches = entries.filter(entry => (
      String(entry?.rel_path || "") === path
      && !["directory", "symlink"].includes(String(entry?.type || entry?.kind || ""))
    ));
    if (relativeMatches.length === 1) return String(relativeMatches[0].path || path);
    const lineageMatch = relativeMatches.find(entry => entry?.is_lineage_workspace === true);
    if (lineageMatch) return String(lineageMatch.path || path);
    const workspaceMatch = relativeMatches.find(entry => String(entry?.root_kind || "") === "workspace");
    if (workspaceMatch) return String(workspaceMatch.path || path);

    const selectedSeed = displaySeedById(state.selectedSeedId) || seedById(state.selectedSeedId);
    const lineageRoot = roots.find(root => root?.is_lineage_workspace === true)
      ;
    if (
      lineageRoot?.directory_id
      && String(selectedSeed?.artifact_path || "").replace(/^\/+|\/+$/g, "") === path
    ) {
      return `${lineageRoot.directory_id}/${path}`;
    }
    const defaultRoot = roots.find(root => root?.is_default_cwd === true)
      || roots.find(root => root?.workspace_usage === "primary");
    return defaultRoot?.directory_id ? `${defaultRoot.directory_id}/${path}` : path;
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
        const response = await societyService.executeHandoff( {
          lineage_id: state.lineage.lineage_id,
          handoff_id: selectedEdge.id,
        });
        commitLineage(response.lineage, { render: false });
        await loadWorkspaceGraphBindings(state.lineage);
        renderLineageSurface();
        toast(window.PortalI18n.t("Handoff Workspace 已就绪。"), "success");
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

  async function loadResultFile(path, options = {}) {
    if (!state.lineage || !state.resultWorkspaceNodeId || !path) return false;
    const lineageId = state.lineage.lineage_id;
    const residentId = state.resultWorkspaceNodeId;
    const previewPath = resolveWorkspacePreviewPath(path);
    state.selectedResultPath = previewPath;
    return requestUnifiedPreview({
      locatorKind: "lineage-workspace",
      lineageId,
      residentId,
      path: previewPath,
      title: previewPath.split("/").pop() || window.PortalI18n.t("Preview"),
      source: "hatg",
      reload: options.reload === true,
    });
  }

  async function loadWorkspaceGraphBindings(lineage = state.lineage, options = {}) {
    if (!lineage?.lineage_id) {
      return { complete: false, failedCount: 0, stale: true };
    }
    const render = options.render !== false;
    const fit = options.fit !== false;
    const sequence = state.workspaceGraphLoadSeq;
    const lineageId = lineage.lineage_id;
    const missingNodes = (lineage.nodes || []).filter(node => !state.workspaceGraphBindings.has(node.id));
    if (!missingNodes.length) {
      if (render && state.graphViewMode === "workspace") renderGraph();
      return { complete: true, failedCount: 0, stale: false };
    }
    const results = await Promise.allSettled(missingNodes.map(async node => ({
      nodeId: node.id,
      workspace: await window.PortalResidentService.workspaces(node.id),
    })));
    if (sequence !== state.workspaceGraphLoadSeq || state.lineage?.lineage_id !== lineageId) {
      return { complete: false, failedCount: 0, stale: true };
    }
    const bindings = new Map(state.workspaceGraphBindings);
    results.forEach(result => {
      if (result.status === "fulfilled") bindings.set(result.value.nodeId, result.value.workspace);
    });
    state.workspaceGraphBindings = bindings;
    const failedCount = results.filter(result => result.status === "rejected").length;
    const complete = failedCount === 0
      && (lineage.nodes || []).every(node => bindings.has(node.id));
    if (render && state.graphViewMode === "workspace") {
      renderGraph();
      if (fit) {
        window.requestAnimationFrame(() => state.graphController?.fit?.({ duration: 220, maxScale: 1.15 }));
      }
    }
    return { complete, failedCount, stale: false };
  }

  async function loadNodeWorkspaces(node = currentNode()) {
    if (!state.lineage || !node) return null;
    const sequence = ++state.resultWorkspaceSeq;
    const lineageId = state.lineage.lineage_id;
    const nodeId = node.id;
    const workspace = await window.PortalResidentService.workspaces(nodeId);
    if (state.lineage?.lineage_id === lineageId) state.workspaceGraphBindings.set(nodeId, workspace);
    if (sequence !== state.resultWorkspaceSeq || state.lineage?.lineage_id !== lineageId || state.selectedNodeId !== nodeId) return null;
    state.resultWorkspace = workspace;
    state.resultWorkspaceNodeId = nodeId;
    renderLineageHeader();
    return workspace;
  }

  async function loadResults() {
    const node = currentNode();
    if (!state.lineage || !node) return;
    try {
      const workspace = await loadNodeWorkspaces(node);
      if (workspace && state.selectedResultPath) {
        await loadResultFile(state.selectedResultPath, { reload: true });
      }
    } catch (error) {
      toast(window.PortalI18n.text`成果列表刷新失败：${error.message || error}`, "error");
    }
  }

  function bindModals() {
    $queryAll("[data-close-modal]").forEach(button => button.addEventListener("click", () => closeModal(button.dataset.closeModal || "")));
    pageStateController.listen("panel", $eventRoot(), "keydown", event => {
      if (event.key !== "Escape") return;
      if (state.handoffMode) cancelHandoffMode();
      else if (state.selectedEdgeId) closeRelationInspector();
    });
  }

  function bindActions() {
    bindComponentLibraryResize();
    bindPrimitiveLibrary();
    $id("refreshLineagesBtn")?.addEventListener("click", () => loadLineages().catch(error => toast(error.message || error, "error")));
    $id("hatgCollapseBtn")?.addEventListener("click", () => {
      hatgComponentOptions.onCollapseRequest?.();
    });
    // The Lineage Menu; project, navigation and deletion stay in the settings dialog it links to.
    $id("lineageSettingsBtn").addEventListener("click", () => {
      if (!state.lineage) return;
      void window.PortalNav.openScopeMenu({
        kind: "lineage", id: state.lineage.lineage_id, record: state.lineage,
        onOpenAdvanced: openEditLineageModal,
      });
    });
    pageStateController.listen("panel", window, "portal:scope-menu-changed", event => {
      const detail = event.detail || {};
      if (detail.kind !== "lineage" || detail.id !== state.lineage?.lineage_id) return;
      if (detail.type === "family") {
        commitLineage(detail.lineage);
        return;
      }
      if (detail.type !== "settings") return;
      Object.assign(state.lineage, clone(detail.changes));
      state.localVersion += 1;
      updateLineageSummary();
      renderLineageSurface();
    });
    $id("lineageForm").addEventListener("submit", submitLineageForm);
    $id("deleteLineageBtn").addEventListener("click", deleteCurrentLineage);
    $id("addNodeBtn").addEventListener("click", openNodeModal);
    $id("emptyAddNodeBtn").addEventListener("click", openNodeModal);
    // Registered before the re-render below, so an explicit route choice is kept.
    $id("nodeRouteSelect").addEventListener("change", () => { state.nodeRouteChosen = true; });
    for (const id of ["nodeBackendSelect", "nodeRouteSelect", "nodeModelSelect"]) {
      $id(id).addEventListener("change", renderNodeProviderFields);
    }
    $id("nodeForm").addEventListener("submit", submitNodeForm);
    $id("nodeAbsorptionKeepBtn").addEventListener("click", keepAbsorbedSourceNode);
    $id("nodeAbsorptionDeleteBtn").addEventListener("click", deleteAbsorbedSourceNode);
    $id("branchNodeBtn").addEventListener("click", () => openBranchModal());
    $id("branchForm").addEventListener("submit", submitBranch);
    $id("branchAccountInput").addEventListener("change", updateBranchAccountHint);
    $id("branchProviderInput").addEventListener("change", event => configureBranchProvider(event.target.value));
    $id("branchModeChoices").querySelectorAll('input[name="branchMode"]').forEach(input => {
      input.addEventListener("change", updateBranchAccountHint);
    });
    $id("handoffModeBtn").addEventListener("click", () => {
      state.handoffPreset = "direct";
      activateHandoffMode();
    });
    $id("cancelGraphModeBtn").addEventListener("click", cancelGraphInteractionMode);
    $id("handoffForm").addEventListener("submit", submitHandoff);
    $id("removeEdgeBtn").addEventListener("click", removeEditingEdge);
    $id("handoffSendNowInput").addEventListener("change", syncHandoffSubmitLabel);
    $id("handoffAddItemBtn").addEventListener("click", addHandoffItem);
    $id("handoffItemsEditor").addEventListener("click", event => {
      const removeButton = event.target.closest("[data-remove-handoff-item]");
      if (removeButton) removeHandoffItem(removeButton);
    });
    $id("handoffItemsEditor").addEventListener("change", event => {
      if (event.target.matches('[data-handoff-field="materializer"]')) {
        syncHandoffMaterializerFields(event.target.closest("[data-handoff-item]"));
      }
    });
    $id("stopLineageBtn").addEventListener("click", stopAllRunningNodes);
    $id("autoLayoutBtn").addEventListener("click", autoLayoutGraph);
    $id("graphLeaderJumpBtn").addEventListener("click", focusLineageLeader);
    $id("addTemplateBtn").addEventListener("click", () => templateController.newTemplate());
    [["saveSelectionTemplateBtn", false], ["saveSelectionRewriteBtn", true]].forEach(([id, rewrite]) => {
      $id(id).addEventListener("click", async event => {
        const elementIds = [...state.selectedCanvasObjectIds];
        if (!elementIds.length) return;
        event.currentTarget.disabled = true;
        try { await templateController.saveSelection(elementIds, { rewrite }); }
        catch (error) { toast(window.PortalI18n.message(error.message || String(error)), "error"); }
        finally { renderControls(); }
      });
    });
    $id("wrapFunctionBlockBtn").addEventListener("click", () => {
      openFunctionalize().catch(error => toast(error.message || String(error), "error"));
    });
    $id("familizeBtn").addEventListener("click", () => {
      openFamilize().catch(error => toast(error.message || String(error), "error"));
    });
    $id("moveSubgraphBtn").addEventListener("click", openSubgraphMove);
    $id("subgraphMoveTarget").addEventListener("change", () => {
      if (subgraphMoveContext) subgraphMoveContext.key = `portal:move-subgraph:${globalThis.PortalUIPrimitives.randomUUID()}`;
      previewSubgraphMove();
    });
    $id("subgraphMoveProject").addEventListener("change", () => {
      if (subgraphMoveContext) subgraphMoveContext.key = `portal:move-subgraph:${globalThis.PortalUIPrimitives.randomUUID()}`;
    });
    $id("subgraphMovePreview").addEventListener("click", () => {
      if (subgraphMoveContext) subgraphMoveContext.key = `portal:move-subgraph:${globalThis.PortalUIPrimitives.randomUUID()}`;
      loadSubgraphMoveTargets();
    });
    $id("subgraphMoveForm").addEventListener("submit", submitSubgraphMove);
    $id("batchDeleteSelectionBtn").addEventListener(
      "click",
      batchDeleteCanvasSelection,
    );
    $id("cancelCanvasSelectionBtn").addEventListener(
      "click",
      () => clearCanvasSelection(),
    );
    $id("seedPromoteForm").addEventListener("submit", submitSeedPromotion);
    $id("seedDismissBtn").addEventListener("click", () => openSeedDismissModal(state.selectedSeedId));
    $id("seedSettingsBtn")?.addEventListener("click", () => {
      selectProgramObject(state.selectedSeedId).catch(() => {});
    });
    $id("hatgCanvas").addEventListener("hatg:transform", positionSeedInspector);
    const observedCanvas = $id("hatgCanvas");
    seedInspectorObserver = new ResizeObserver(() => {
      if (observedCanvas.isConnected) positionSeedInspector();
    });
    seedInspectorObserver.observe(observedCanvas);
    // The navigation can wrap as docks resize or translated labels change.
    // Reserve its measured block size without forcing synchronous layout reads.
    const graphControlSizes = new Map([
      [$id("graphNavigationBar"), "--hatg-navigation-height"],
    ]);
    const observedViewport = $id("hatgViewport");
    graphControlObserver = new ResizeObserver(entries => {
      if (!observedViewport.isConnected) return;
      for (const entry of entries) {
        const height = entry.borderBoxSize[0]?.blockSize ?? entry.contentRect.height;
        observedViewport.style.setProperty(graphControlSizes.get(entry.target), `${height}px`);
      }
    });
    graphControlSizes.forEach((_, element) => graphControlObserver.observe(element));
    $id("seedDismissForm").addEventListener("submit", submitSeedDismissal);
    $queryAll("[data-seed-promote-action]").forEach(button => {
      button.addEventListener("click", () => openSeedPromoteModal(state.selectedSeedId, button.dataset.seedPromoteAction || "custom"));
    });
    $queryAll("[data-seed-action-choice]").forEach(button => {
      button.addEventListener("click", () => setSeedActionChoice(button.dataset.seedActionChoice || "custom"));
    });
    $id("leaderForm").addEventListener("submit", submitLeaderRequest);
    $id("graphFocusModeBtn").addEventListener("click", toggleGraphFocusMode);
    $id("graphContextViewBtn")?.addEventListener("click", () => setGraphViewMode("context"));
    $id("graphWorkspaceViewBtn")?.addEventListener("click", () => setGraphViewMode("workspace"));
    $id("graphRelationFilter").addEventListener("change", changeGraphRelationFilter);
    $id("reconcileLineageRuntimeBtn")?.addEventListener("click", event => reconcileLineageRuntime({ button: event.currentTarget, notify: true }).catch(() => {}));
    graphNodeSearchPopover = window.PortalOverlay.bindPopover({
      trigger: $id("graphNodeSearchInput"),
      anchor: $query(".graphNodeSearch"),
      panel: $id("graphNodeSearchResults"),
      portal: true,
      placement: "bottom-end",
      ariaHaspopup: "listbox",
      closeOnOutside: true,
      closeOnEscape: true,
      onClose: cancelGraphNodeSearch,
    });
    $id("graphNodeSearchInput").addEventListener("input", handleGraphNodeSearchInput);
    $id("graphNodeSearchResults").addEventListener("click", event => {
      const button = event.target.closest("[data-graph-search-node]");
      if (!button) return;
      focusGraphNode(button.dataset.graphSearchNode || "");
    });
    $id("graphDeepRerankBtn").addEventListener("click", runGraphDeepRerank);
    $id("graphNodeSearchInput").addEventListener("keydown", event => {
      if (event.key !== "Enter") return;
      if (graphSmartSearch.loading) {
        event.preventDefault();
        return;
      }
      const first = graphSearchMatches(event.currentTarget.value)[0];
      if (!first) return;
      event.preventDefault();
      focusGraphNode(first.id);
    });
  }

  async function init() {
    mountTemplates();
    try {
      await window.PortalLineageProgram?.loadPrimitiveDefinitions?.();
    } catch (error) {
      setSyncStatus("原语定义加载失败", "error");
      toast(window.PortalI18n.text`HATG 原语定义加载失败：${error.message || error}`, "error", 8000);
      // Let the mount boundary retain the target and show its retry action.
      // A failed prerequisite is not an initialized, empty Lineage panel.
      throw error;
    }
    const hatgPane = $id("hatgPane");
    if (hatgPane) hatgPane.hidden = false;
    bindModals();
    bindActions();
    initHatgGraph();
    renderLineageSurface();
    // Remote reads hydrate the surface; they do not own its interaction lifecycle.
    hatgComponentRoot.inert = false;
    try {
      const me = await window.PortalIdentity.load();
      state.me = String(me?.username || "");
      // HATG needs only SocietyService. Waiting for the host page's whole
      // first load (Resident sidebar, conversations, catalogs) kept a cold
      // HATG idle for about a second, and for many seconds under load. Read
      // now; the service-ready boundary is needed only when that read fails
      // because the service is still starting or recovering.
      try {
        await loadLineagePageData();
      } catch {
        await hatgComponentOptions.waitForServiceReady();
        await loadLineagePageData();
      }
    } catch (error) {
      setSyncStatus("初始化失败", "error");
      toast(window.PortalI18n.text`Lineage 初始化失败：${error.message || error}`, "error", 7000);
    } finally {
      lineageCatalogHydrated = true;
    }
    window.PortalButtonTooltips?.enhance?.(hatgComponentRoot);
  }

  function mountRuntime(options = {}) {
    const root = typeof options.root === "string" ? document.querySelector(options.root) : options.root;
    if (!root) throw new TypeError("PortalHatgPanel.mountHatgPanel requires root.");
    if (typeof options.waitForServiceReady !== "function") {
      throw new TypeError("PortalHatgPanel requires its Workbench service-ready boundary.");
    }
    if (typeof options.onLocationChange !== "function") {
      throw new TypeError("PortalHatgPanel requires its Workbench location owner.");
    }
    if (typeof options.onDeleteLineage !== "function") {
      throw new TypeError("PortalHatgPanel requires its Workbench Lineage deletion owner.");
    }
    if (!options.pageState || typeof options.pageState.read !== "function" || typeof options.pageState.write !== "function") {
      throw new TypeError("PortalHatgPanel requires its Workbench page-state owner.");
    }
    hatgComponentRoot = root;
    hatgComponentOptions = { ...options };
    pageStateController = controllerFactory.createController({
      state,
      pageState: options.pageState,
    });
    pageStateController.listen("panel", root, "click", event => {
      const badge = event.target.closest("[data-function-block-issues]");
      if (!badge) return;
      event.preventDefault();
      event.stopPropagation();
      openFunctionBlockMenu(badge.dataset.functionBlockIssues, "issues");
    }, true);
    root.classList.add("pane", "lineageHatgPanel", "portalHatgPanelComponent");
    root.setAttribute("aria-busy", "true");
    root.inert = true;
    let destroyed = false;
    let templateRequestAbort = null;
    let loadPromise = Promise.resolve(null);
    let target = {
      lineageId: String(options.lineageId || ""),
      residentId: String(options.residentId || ""),
      programItemId: String(options.programItemId || ""),
    };
    let appliedTarget = null;
    let targetRequestSeq = 0;
    const parsedTemplateTimeout = options.templateTimeoutMs === undefined
      ? DEFAULT_TEMPLATE_TIMEOUT_MS
      : Number(options.templateTimeoutMs);
    const templateTimeoutMs = Number.isFinite(parsedTemplateTimeout)
      ? parsedTemplateTimeout
      : DEFAULT_TEMPLATE_TIMEOUT_MS;

    function renderTemplateLoadState({ title, detail, retry = false }) {
      if (retry) {
        root.removeAttribute("aria-busy");
        root.inert = false;
      } else {
        root.setAttribute("aria-busy", "true");
        root.inert = true;
      }
      const shell = document.createElement("div");
      shell.className = "lineageHatgEmpty";
      shell.setAttribute("role", retry ? "alert" : "status");
      const heading = document.createElement("strong");
      heading.textContent = title;
      const message = document.createElement("small");
      message.textContent = detail;
      shell.append(heading, message);
      if (retry) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portalButton";
        button.dataset.hatgTemplateRetry = "1";
        button.textContent = window.PortalI18n.t("重试");
        button.addEventListener("click", () => { void startTemplateLoad(); }, { once: true });
        shell.append(button);
      }
      root.replaceChildren(shell);
    }

    async function fetchTemplate() {
      templateRequestAbort?.abort();
      const requestAbort = new AbortController();
      templateRequestAbort = requestAbort;
      let timedOut = false;
      const timeout = templateTimeoutMs > 0
        ? window.setTimeout(() => {
            timedOut = true;
            requestAbort.abort(new DOMException("HATG template timed out", "TimeoutError"));
          }, templateTimeoutMs)
        : 0;
      try {
        return await fetch(options.templateUrl || "/hatg-panel.html", {
          cache: "no-store",
          signal: requestAbort.signal,
        });
      } catch (error) {
        if (timedOut) {
          const seconds = Math.max(1, Math.ceil(templateTimeoutMs / 1000));
          throw new Error(`HATG 模板加载超过 ${seconds} 秒，请重试。`);
        }
        throw error;
      } finally {
        if (timeout) window.clearTimeout(timeout);
        if (templateRequestAbort === requestAbort) templateRequestAbort = null;
      }
    }

    function startTemplateLoad() {
      if (destroyed) return Promise.resolve(null);
      delete root.dataset.hatgError;
      renderTemplateLoadState({ title: window.PortalI18n.t("正在加载 HATG"), detail: "正在读取图编辑模板。" });
      const request = (async () => {
        const [response] = await Promise.all([
          fetchTemplate(),
          window.PortalUIPrimitives.loadAsset("/static/lineage_templates.css"),
        ]);
        if (!response.ok) throw new Error(`HATG template failed (${response.status})`);
        const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
        const fragment = document.createDocumentFragment();
        parsed.querySelectorAll("body > :not(header):not(script)").forEach(node => {
          fragment.append(document.importNode(node, true));
        });
        if (!fragment.childNodes.length) throw new Error("HATG 模板内容为空。");
        if (destroyed) return null;
        root.replaceChildren(fragment);
        window.PortalI18n.renderStatic(root);
        await init();
        if (destroyed) return null;
        root.inert = false;
        if (target.lineageId) await controller.setTarget(target);
        delete root.dataset.hatgError;
        root.removeAttribute("aria-busy");
        return controller;
      })();
      loadPromise = request.catch(error => {
        if (destroyed) return null;
        disconnectGraphObservers();
        root.dataset.hatgError = String(error.message || error);
        renderTemplateLoadState({
          title: window.PortalI18n.t("HATG 加载失败"),
          detail: String(error.message || error),
          retry: true,
        });
        return controller;
      });
      return loadPromise;
    }

    const controller = Object.freeze({
      root,
      get ready() { return loadPromise; },
      retry() { return startTemplateLoad(); },
      suspendLineage(lineageId) {
        const suspendedId = String(lineageId || "");
        if (
          state.lineage?.lineage_id !== suspendedId
          && target.lineageId !== suspendedId
        ) return null;
        const snapshot = controller.snapshot();
        targetRequestSeq += 1;
        state.detailSeq += 1;
        target = { lineageId: "", residentId: "", programItemId: "" };
        appliedTarget = { ...target };
        renderNoLineage({ publishLocation: false });
        return snapshot;
      },
      resumeLineage(snapshot = {}) {
        if (!String(snapshot.lineageId || "")) return Promise.resolve(target);
        return controller.setTarget(snapshot);
      },
      applyLineageDeletion(lineageId) {
        const deletedId = String(lineageId || "");
        state.lineages = state.lineages.filter(item => item.lineage_id !== deletedId);
        if (controller.suspendLineage(deletedId)) publishLocation("");
      },
      async applyLineageMutation(lineage, { separateObjectIds = [] } = {}) {
        await loadPromise;
        if (state.layoutSaveInFlight) await state.layoutSaveInFlight;
        const changed = commitLineage(lineage).changed;
        if (lineage.lineage_id === state.lineage?.lineage_id && separateObjectIds.length) {
          await separateGraphObjects(separateObjectIds);
        }
        return changed;
      },
      async setTarget(next = {}) {
        const requestSeq = ++targetRequestSeq;
        const revealInGraph = next.revealInGraph === true;
        const hasProgramItem = Object.prototype.hasOwnProperty.call(next, "programItemId");
        const requestedLineageId = String(next.lineageId || target.lineageId || "");
        const requestedResidentId = String(next.residentId || target.residentId || "");
        const pendingProgramItemId = (
          target.lineageId === requestedLineageId
          && target.programItemId
          && target.programItemId !== appliedTarget?.programItemId
        ) ? target.programItemId : "";
        const requestedTarget = {
          lineageId: requestedLineageId,
          residentId: requestedResidentId,
          programItemId: hasProgramItem
            ? String(next.programItemId || "")
            : String(state.lineage?.lineage_id === requestedLineageId
              ? state.selectedProgramObjectId || pendingProgramItemId
              : pendingProgramItemId),
        };
        target = requestedTarget;
        state.targetResidentId = requestedTarget.residentId;
        // Before the first catalog read the template load applies this target. After it,
        // an explicit Lineage is opened even when the discoverable list is empty.
        if (!lineageCatalogHydrated) return target;
        const selectedTargetNode = currentNode();
        const selectedTargetMatches = (
          String(state.lineage?.lineage_id || "") === requestedTarget.lineageId
          && (
            !requestedTarget.residentId
            || selectedTargetNode?.id === requestedTarget.residentId
            || nodeResidentId(selectedTargetNode) === requestedTarget.residentId
          )
        );
        if (selectedTargetMatches) {
          // Returning to the displayed graph is still a new navigation. A
          // pending load for another graph must not overwrite this selection.
          state.detailSeq += 1;
          resumeDisplayedLineage();
          let selectionHandledReveal = false;
          if (requestedTarget.programItemId) {
            await selectProgramObject(requestedTarget.programItemId, { revealFamily: true });
          } else if (hasProgramItem && state.selectedProgramObjectId && selectedTargetNode) {
            await selectNode(selectedTargetNode.id, {
              force: true,
              notifyParent: false,
              revealInGraph,
            });
            selectionHandledReveal = revealInGraph;
          }
          if (requestSeq !== targetRequestSeq) return target;
          appliedTarget = { ...requestedTarget };
          if (revealInGraph && selectedTargetNode && !selectionHandledReveal) {
            await revealSelectedGraphNode(selectedTargetNode.id);
          }
          return target;
        }
        if (requestedTarget.lineageId) await selectLineage(requestedTarget.lineageId, {
          residentId: requestedTarget.residentId,
          programItemId: requestedTarget.programItemId,
          revealInGraph,
        });
        if (requestSeq !== targetRequestSeq) return target;
        if (requestSeq === targetRequestSeq) appliedTarget = { ...requestedTarget };
        return target;
      },
      async editLineage(next = {}) {
        await loadPromise;
        await controller.setTarget(next);
        if (!state.lineage) throw new Error("Lineage 尚未加载");
        const modal = $id("lineageModal");
        if (modal && modal.parentElement !== document.body) {
          modal.dataset.portalDetached = "1";
          document.body.append(modal);
          detachedRoots.add(modal);
        }
        openEditLineageModal();
        return state.lineage.lineage_id;
      },
      async createLineage(next = {}) {
        await loadPromise;
        const modal = $id("lineageModal");
        if (modal && modal.parentElement !== document.body) {
          modal.dataset.portalDetached = "1";
          document.body.append(modal);
          detachedRoots.add(modal);
        }
        openCreateLineageModal(next.projectId || "");
      },
      setContext(context = {}) {
        const next = context.hatg || context;
        if (next.lineageId) void controller.setTarget(next);
      },
      snapshot: () => ({
        lineageId: state.lineage?.lineage_id || target.lineageId,
        residentId: state.selectedNodeId || target.residentId,
        programItemId: state.lineage ? state.selectedProgramObjectId : target.programItemId,
      }),
      restore(snapshot = {}) { target = { ...target, ...snapshot }; },
      show() {
        panelActive = true;
        root.hidden = false;
        if (state.lineage) {
          bindResidentStateCollection();
          bindLineageProjectionSubscription();
        }
      },
      hide() {
        panelActive = false;
        clearGraphNodeSearch();
        root.hidden = true;
        state.residentStateCollection?.destroy?.();
        state.residentStateCollection = null;
        state.lineageProjectionSubscription?.();
        state.lineageProjectionSubscription = null;
      },
      destroy() {
        destroyed = true;
        panelActive = false;
        disconnectGraphObservers();
        disposeLibraryResize?.();
        disposeLibraryResize = null;
        clearGraphNodeSearch();
        graphNodeSearchPopover?.destroy();
        graphNodeSearchPopover = null;
        activateLineageEdits();
        templateRequestAbort?.abort();
        templateRequestAbort = null;
        root.removeAttribute("aria-busy");
        delete root.dataset.hatgError;
        root.inert = false;
        state.residentStateCollection?.destroy?.();
        state.residentStateCollection = null;
        state.lineageProjectionSubscription?.();
        state.lineageProjectionSubscription = null;
        state.graphController?.destroy?.();
        pageStateController?.destroy?.();
        templateController?.destroy();
        root.replaceChildren();
      },
    });
    loadPromise = startTemplateLoad();
    return controller;
  }

  return Object.freeze({ mount: mountRuntime });
  }

  function mountHatgPanel(options = {}) {
    return createHatgRuntime().mount(options);
  }

  window.PortalHatgPanel = Object.freeze({ mountHatgPanel });
})();
