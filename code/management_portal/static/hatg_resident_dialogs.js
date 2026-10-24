(() => {
  "use strict";

  if (window.PortalHatgResidentDialogs) return;

  // Resident nodes' dialogs and menu actions on the HATG canvas: creating and
  // editing a Resident, absorbing one into another, deleting, forking and quick
  // forks, and placing a new Resident next to its source.
  const DEPENDENCIES = Object.freeze([
    "$id", "bindResidentStateCollection", "clearNodeSelection", "clone", "closeModal",
    "commitLineage", "compactText", "currentHatgComponentOptions", "currentNode",
    "displayedProgram", "displayNodeById", "esc", "familyForFacade", "familyForNewResident",
    "flushLineageLayoutSave", "formatDate", "lineageEdgeReferencesResident",
    "loadLineagePageData", "loadResults", "nodeById", "nodeIntakeState", "nodeResidentId",
    "openModal", "programFeature", "removeFamilyInstance", "renderGraph", "renderLineageSurface",
    "revealUnfamilizedMember", "selectLineage", "selectNode", "setBusy", "setNodeRuntimeStatus",
    "setSyncStatus", "societyService", "state", "toast",
  ]);

  const branchMessageRole = item => window.PortalChatWorkspace.messageRole(item);

  function messageSelector(item) {
    return {
      message_id: window.PortalChatWorkspace.messageIdentity(item),
    };
  }

  function createResidentDialogs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgResidentDialogs is missing: ${missing.join(", ")}`);
    const {
      $id, bindResidentStateCollection, clearNodeSelection, clone, closeModal, commitLineage,
      compactText, currentHatgComponentOptions, currentNode, displayedProgram, displayNodeById,
      esc, familyForFacade, familyForNewResident, flushLineageLayoutSave, formatDate,
      lineageEdgeReferencesResident, loadLineagePageData, loadResults, nodeById, nodeIntakeState,
      nodeResidentId, openModal, programFeature, removeFamilyInstance, renderGraph,
      renderLineageSurface, revealUnfamilizedMember, selectLineage, selectNode, setBusy,
      setNodeRuntimeStatus, setSyncStatus, societyService, state, toast,
    } = dependencies;

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
            currentHatgComponentOptions().onLocalTargetChange?.({lineageId: targetLineageId, residentId});
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

    // A closed Fork dialog drops its source and ignores replies still on the way.
    function resetBranchModalState() {
      branchModalRequestSeq += 1;
      branchSourceSnapshot = null;
      state.branchMessageContext = null;
    }

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
        // Server placement can be outside a narrow canvas. Reveal the new card
        // before completing this action; Fork also awaits its Chat navigation.
        // A blank session keeps the current Chat, as ordinary node creation does.
        try {
          await selectNode(node.id, {
            force: true, notifyParent: !fresh, revealInGraph: true, duration: 0,
          });
        } catch (error) {
          setSyncStatus(`${actionLabel}已完成，页面定位失败`, "error");
          toast(window.PortalI18n.text`${actionLabel}已完成，请刷新查看，勿重复提交：${error.message || error}`, "warning", 9000);
          return node;
        }
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

    return Object.freeze({
      renderNodeProviderFields, openNodeModal, nextNodePosition, submitNodeForm,
      openNodeSettingsModal, residentMenuHandlers, absorbNodeIntoTarget, keepAbsorbedSourceNode,
      deleteAbsorbedSourceNode, updateBranchAccountHint, configureBranchProvider,
      resetBranchModalState, openBranchModal, createQuickFork, submitBranch, removeSelectedNode,
    });
  }

  window.PortalHatgResidentDialogs = Object.freeze({
    createResidentDialogs, dependencies: DEPENDENCIES,
  });
})();
