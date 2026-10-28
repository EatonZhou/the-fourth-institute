(() => {
  "use strict";

  if (window.PortalHatgNodeStatus) return;

  // What a canvas card shows about the work behind it, and the controls on the card:
  // an Event or Effect's on/off switch and listening state, the manual trigger or
  // reset, a Resident's work phase, intake and Stop action, its message count and
  // how long its context has grown.
  const DEPENDENCIES = Object.freeze([
    "applyProgramMutation", "displayNodeById", "effectiveNodeStatus", "esc",
    "executeHatgInstruction", "familyRoles", "functionBlockInstanceById",
    "instanceManualSources", "isFunctionBlockInstance", "isProgramComponent",
    "manualProgramEventKey", "nodeById", "nodeResidentId", "normalizeOptionalLimit",
    "programEffectRuntime", "programEventSourceRuntime", "programExecutionExplanation",
    "renderGraphRuntimePresentation", "state",
  ]);

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

  function createNodeStatus(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgNodeStatus is missing: ${missing.join(", ")}`);
    const {
      applyProgramMutation, displayNodeById, effectiveNodeStatus, esc, executeHatgInstruction,
      familyRoles, functionBlockInstanceById, instanceManualSources, isFunctionBlockInstance,
      isProgramComponent, manualProgramEventKey, nodeById, nodeResidentId,
      normalizeOptionalLimit, programEffectRuntime, programEventSourceRuntime,
      programExecutionExplanation, renderGraphRuntimePresentation, state,
    } = dependencies;

    function programEventSourceStatus(componentId) {
      const runtime = programEventSourceRuntime(componentId);
      if (runtime.paused || runtime.state === "paused") return { state: "paused", label: window.PortalI18n.t("已关闭"), reason: "" };
      if (runtime.exhausted || runtime.state === "exhausted") return { state: "exhausted", label: window.PortalI18n.t("次数耗尽"), reason: "" };
      if (["blocked", "error"].includes(runtime.state)) {
        return { state: runtime.state, label: runtime.state === "blocked" ? "自动触发受阻" : "运行错误", reason: String(runtime.error || "请查看 Lineage 诊断。") };
      }
      return { state: runtime.state || "unknown", label: runtime.state === "listening" ? "监听中" : "等待运行状态", reason: "" };
    }

    function syncProgramLeaderGuidance(panel) {
      const note = panel?.querySelector("[data-event-leader-note]");
      if (!note) return;
      const leaderId = String(state.lineage?.leader_resident_id || "").trim();
      note.textContent = leaderId
        ? window.PortalI18n.text`自动运行使用 Leader（${leaderId}）的身份和权限。`
        : window.PortalI18n.t("尚未设置 Leader，请先在 Lineage 设置中设置 Leader，再启用自动运行。");
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
        syncProgramLeaderGuidance(panel);
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

    function nodeUserMessageCount(node) {
      const key = nodeResidentId(node);
      if (key && state.sessionUserMessageCounts.has(key)) return state.sessionUserMessageCounts.get(key);
      return null;
    }

    function nodeMessageCountText(node) {
      const count = nodeUserMessageCount(node);
      return count === null ? "— messages" : `${count} messages`;
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

    return Object.freeze({
      programEventSourceStatus, syncProgramLeaderGuidance, programComponentPaused,
      programComponentSwitchHtml, syncProgramComponentSwitch, toggleProgramComponent,
      programExecutionExplanationHtml, nodeExecutionGate, nodeIntakeState, nodeWorkPresentation,
      hatgNodeControl, hatgNodeHoverActions, nodeMessageCountText, nodeContextPressure,
      nodeContextHeatBadge, applyNodeMessageCounts,
    });
  }

  window.PortalHatgNodeStatus = Object.freeze({
    createNodeStatus, dependencies: DEPENDENCIES,
  });
})();
