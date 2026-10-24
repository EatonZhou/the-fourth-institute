(() => {
  "use strict";

  if (window.PortalHatgGraphView) return;

  // The HATG canvas on the page: the graph controller and what each card shows,
  // drawing the projection, the selection and the running state, the navigator,
  // revealing and focusing objects, the context and Workspace views, the relation
  // filter and the hint of the current interaction mode.
  const DEPENDENCIES = Object.freeze([
    "$id", "absorbNodeIntoTarget", "addCanvasObjectsToSelection", "cancelHandoffMode",
    "cancelProgramWireDraft", "canvasNodeCanBeSelected", "clearCanvasSelection",
    "clearGraphNodeSearch", "closeRelationInspector", "compactText",
    "createProgramComponentFromDrop", "createQuickFork", "currentHatgComponentOptions",
    "currentProgram", "currentTemplateController", "dismissSeedSelection", "displayEdgeById",
    "displayGraphItemById", "displayNodeById", "displaySeedById", "effectiveNodeStatus",
    "effectScriptLabel", "emitManualProgramEvent", "esc", "familyExpanded", "familyForFacade",
    "familyMemberTag", "familyRoles", "familySpotlight", "FIT_ALL_VIEW",
    "functionBlockInstanceUnacknowledgedFailures", "functionBlockSourceTitle", "graphItemById",
    "graphSelectedEdgeId", "graphSelectedId", "graphVisibleModel", "handleHandoffNodeClick",
    "handleProgramPortClick", "HATG_WORLD_SIZE", "hatgNodeControl", "hatgNodeHoverActions",
    "hatgViewPreferences", "isFunctionBlockInstance", "isProgramComponent",
    "loadWorkspaceGraphBindings", "nodeById", "nodeContextHeatBadge", "nodeContextPressure",
    "nodeExecutionGate", "nodeIntakeState", "nodeIsDerived", "nodeMessageCountText",
    "nodeResidentId", "nodeWorkPresentation", "normalizeOptionalLimit", "notifyResidentOpenMenu",
    "notifyResidentSelection", "openFunctionBlockMenu", "openNodeSettingsModal",
    "openSeedPromoteModal", "positionSeedInspector", "programComponentPaused",
    "programEventSourceRuntime", "programEventSourceStatus", "programExecutionExplanation",
    "reconcileCanvasSelection", "refreshGraphNodeSearchResults", "relationVisibleInView",
    "removeFamilyInstance", "renderAutoLayoutControl", "renderControls", "renderLineageSurface",
    "renderProgramInspector", "renderProgramWireHint", "renderRelationInspector",
    "resetProgramEventSource", "residentEditorOpenMenu", "saveLineageLayout",
    "scheduleHatgViewPreferenceSave", "selectedFamilyPigeonId", "selectNode",
    "selectProgramComponent", "selectProgramObject", "selectRelationEdge", "selectSeed",
    "setFunctionBlockExpanded", "setSeedCandidateFork", "showFamilyAlone", "state",
    "stopLineageNode", "syncGraphNodePosition", "syncWorkspaceNodePosition", "toast",
    "toggleCanvasObjectSelection", "toggleFamilyFacade", "workspaceGraphModel",
  ]);

  function createGraphView(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgGraphView is missing: ${missing.join(", ")}`);
    const {
      $id, absorbNodeIntoTarget, addCanvasObjectsToSelection, cancelHandoffMode,
      cancelProgramWireDraft, canvasNodeCanBeSelected, clearCanvasSelection,
      clearGraphNodeSearch, closeRelationInspector, compactText, createProgramComponentFromDrop,
      createQuickFork, currentHatgComponentOptions, currentProgram, currentTemplateController,
      dismissSeedSelection, displayEdgeById, displayGraphItemById, displayNodeById,
      displaySeedById, effectiveNodeStatus, effectScriptLabel, emitManualProgramEvent, esc,
      familyExpanded, familyForFacade, familyMemberTag, familyRoles, familySpotlight,
      FIT_ALL_VIEW, functionBlockInstanceUnacknowledgedFailures, functionBlockSourceTitle,
      graphItemById, graphSelectedEdgeId, graphSelectedId, graphVisibleModel,
      handleHandoffNodeClick, handleProgramPortClick, HATG_WORLD_SIZE, hatgNodeControl,
      hatgNodeHoverActions, hatgViewPreferences, isFunctionBlockInstance, isProgramComponent,
      loadWorkspaceGraphBindings, nodeById, nodeContextHeatBadge, nodeContextPressure,
      nodeExecutionGate, nodeIntakeState, nodeIsDerived, nodeMessageCountText, nodeResidentId,
      nodeWorkPresentation, normalizeOptionalLimit, notifyResidentOpenMenu,
      notifyResidentSelection, openFunctionBlockMenu, openNodeSettingsModal,
      openSeedPromoteModal, positionSeedInspector, programComponentPaused,
      programEventSourceRuntime, programEventSourceStatus, programExecutionExplanation,
      reconcileCanvasSelection, refreshGraphNodeSearchResults, relationVisibleInView,
      removeFamilyInstance, renderAutoLayoutControl, renderControls, renderLineageSurface,
      renderProgramInspector, renderProgramWireHint, renderRelationInspector,
      resetProgramEventSource, residentEditorOpenMenu, saveLineageLayout,
      scheduleHatgViewPreferenceSave, selectedFamilyPigeonId, selectNode, selectProgramComponent,
      selectProgramObject, selectRelationEdge, selectSeed, setFunctionBlockExpanded,
      setSeedCandidateFork, showFamilyAlone, state, stopLineageNode, syncGraphNodePosition,
      syncWorkspaceNodePosition, toast, toggleCanvasObjectSelection, toggleFamilyFacade,
      workspaceGraphModel,
    } = dependencies;

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
          : { builtin_key: currentTemplateController()?.find?.(node.template_id)?.builtin_key, id: node.template_id || node.id });
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

    async function openGraphWorkspace(node) {
      if (node?.kind !== "workspace" || !node.workspace_id) return;
      state.selectedWorkspaceId = node.id;
      state.selectedEdgeId = "";
      renderGraph();
      try {
        if (typeof currentHatgComponentOptions().onWorkspaceOpen !== "function") {
          throw new Error("Navigation 工作区入口尚未就绪");
        }
        await currentHatgComponentOptions().onWorkspaceOpen(node.workspace_id);
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
          onOpen: currentHatgComponentOptions().onWorkspaceOpen,
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
      refreshGraphNodeSearchResults();
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

    return Object.freeze({
      graphNodeContent, renderGraph, renderGraphRuntimePresentation, renderGraphSelection,
      initHatgGraph, revealSelectedGraphNode, focusGraphNode, focusLineageLeader,
      toggleGraphFocusMode, changeGraphRelationFilter, setWorkspaceViewLoading, setGraphViewMode,
      cancelGraphInteractionMode, renderGraphModeHint,
    });
  }

  window.PortalHatgGraphView = Object.freeze({
    createGraphView, dependencies: DEPENDENCIES,
  });
})();
