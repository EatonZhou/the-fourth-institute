(() => {
  "use strict";

  if (window.PortalHatgResidentRuntime) return;

  // What each Resident of the shown Lineage is doing: the state subscription and
  // the card status it gives, re-reading the Lineage when it changes elsewhere,
  // checking the running state, and sending a prompt to or stopping a Resident.
  const DEPENDENCIES = Object.freeze([
    "apiGet", "applyLineageEventProjection", "applyNodeMessageCounts", "commitLineage",
    "currentHatgComponentOptions", "currentPanelActive", "effectiveNodeStatus", "nodeById",
    "nodeExecutionGate", "nodeIntakeState", "nodeResidentId", "nodeWorkPresentation",
    "renderControls", "renderGraph", "renderGraphRuntimePresentation", "renderLineageSurface",
    "runningLineageNodes", "selectNode", "setBusy", "setNodeRuntimeStatus", "societyService",
    "state", "toast", "updateLineageSummary",
  ]);

  function runtimeStatusFromResidentProjection(projection, node) {
    const residentState = window.PortalResidentProtocol.stateView(projection?.resident_state);
    if (residentState.status !== "idle") return residentState.status;
    return ["done", "blocked"].includes(node?.status) ? node.status : "idle";
  }

  function createResidentRuntime(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgResidentRuntime is missing: ${missing.join(", ")}`);
    const {
      apiGet, applyLineageEventProjection, applyNodeMessageCounts, commitLineage,
      currentHatgComponentOptions, currentPanelActive, effectiveNodeStatus, nodeById,
      nodeExecutionGate, nodeIntakeState, nodeResidentId, nodeWorkPresentation, renderControls,
      renderGraph, renderGraphRuntimePresentation, renderLineageSurface, runningLineageNodes,
      selectNode, setBusy, setNodeRuntimeStatus, societyService, state, toast,
      updateLineageSummary,
    } = dependencies;

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
        currentHatgComponentOptions().onRuntimeProjectionChange?.({
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
      if (!currentPanelActive() || !lineageId || !residentIds.length) return;
      state.residentStateCollection = window.PortalResidentProtocol.acquireStates(
        residentIds,
        applyResidentStateCollection,
      );
    }

    function bindLineageProjectionSubscription() {
      state.lineageProjectionSubscription?.();
      state.lineageProjectionSubscription = null;
      if (!currentPanelActive() || !state.lineage) return;
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

    return Object.freeze({
      reconcileLineageRuntime, bindResidentStateCollection, bindLineageProjectionSubscription,
      stopLineageNode, sendPromptToNode, stopAllRunningNodes,
    });
  }

  window.PortalHatgResidentRuntime = Object.freeze({
    createResidentRuntime, dependencies: DEPENDENCIES,
  });
})();
