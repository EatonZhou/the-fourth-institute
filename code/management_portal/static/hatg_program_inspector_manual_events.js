(() => {
  "use strict";

  if (window.PortalHatgProgramInspectorManualEvents) return;

  // Triggering a manual Event Source with a one-off Prompt, resetting an
  // exhausted Event Source's count, and the inspector buttons' busy state
  // while either is under way.

  const DEPENDENCIES = Object.freeze([
    "$id", "commitLineage", "manualProgramEventKey", "programComponentById", "programComponentPaused",
    "programEventSourceRuntime", "programFeature", "renderGraph", "renderLineageSurface", "setBusy", "societyService",
    "state", "toast",
  ]);

  function createManualEvents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgProgramInspectorManualEvents is missing: ${missing.join(", ")}`);
    const {
      $id, commitLineage, manualProgramEventKey, programComponentById, programComponentPaused,
      programEventSourceRuntime, programFeature, renderGraph, renderLineageSurface, setBusy, societyService, state,
      toast,
    } = dependencies;

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

    return {
      emitManualProgramEvent, resetProgramEventSource, syncManualProgramEventInspectorBusy,
    };
  }

  window.PortalHatgProgramInspectorManualEvents = Object.freeze({ createManualEvents, dependencies: DEPENDENCIES });
})();
