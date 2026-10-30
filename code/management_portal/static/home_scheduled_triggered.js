(() => {
  if (window.PortalHomeScheduledTriggered) return;

  // Home's scheduled and triggered work panel: each Lineage's timers and event
  // sources with their plans, controls and follow-up refreshes.
  const DEPENDENCIES = Object.freeze([
    "currentAgentStatusData", "currentHomeController", "displayDateTime", "esc", "homeApi", "listen",
    "renderAgentStatus",
  ]);

  function createScheduledTriggered(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHomeScheduledTriggered is missing: ${missing.join(", ")}`);
    const {
      currentAgentStatusData, currentHomeController, displayDateTime, esc, homeApi, listen,
      renderAgentStatus,
    } = dependencies;

    let scheduledTriggeredStatusData = null;
    const scheduledTriggeredExpanded = new Set();

    function scheduledTriggeredScheduleText(source) {
      const config = source?.config || {};
      if (source?.event_type === "file.changed") return `文件变化 · ${config.path || "观察工作区"}`;
      if (config.schedule_mode === "at") return `指定时间 · ${displayDateTime(config.scheduled_at) || config.scheduled_at || "待设置"}`;
      if (config.schedule_mode === "countdown") return `倒计时 · ${config.delay_seconds || 0} 秒`;
      const pattern = config.recurrence_kind === "interval"
        ? `每 ${config.interval_seconds || 0} 秒`
        : `${config.calendar_frequency || "daily"} ${config.calendar_time || ""} · ${config.timezone || "UTC"}`;
      return `${pattern}${Number(config.jitter_seconds) ? ` · 随机延迟最多 ${config.jitter_seconds} 秒` : ""}`;
    }

    // When a source next fires: its due time includes the random delay of a
    // recurring schedule; a paused source has none (its stored plan no longer applies).
    function scheduledTriggeredNextAt(source) {
      const runtime = source?.runtime || {};
      if (source?.paused || runtime.paused || runtime.exhausted || ["paused", "exhausted", "blocked", "error"].includes(runtime.state)) return "";
      return runtime.next_due_at || runtime.next_scheduled_at || "";
    }

    function scheduledTriggeredWaitingForPlan(source) {
      const runtime = source?.runtime || {};
      return source?.event_type === "time.reached" && runtime.state === "listening"
        && !source.paused && !runtime.paused && !runtime.exhausted && !scheduledTriggeredNextAt(source);
    }

    function scheduledTriggeredSourceState(source) {
      const runtime = source.runtime || {};
      if (source.paused || runtime.paused || runtime.state === "paused") return "已暂停";
      if (runtime.exhausted || runtime.state === "exhausted") return "次数耗尽";
      if (["blocked", "error"].includes(runtime.state)) {
        return `${runtime.state === "blocked" ? "自动触发受阻" : "运行错误"} · ${runtime.error || "请查看 Lineage 诊断"}`;
      }
      if (scheduledTriggeredWaitingForPlan(source)) return "等待调度生成计划";
      return "";
    }

    function scheduledTriggeredItemId(item) {
      return item.row_id || item.resident_id || `${item.lineage_id}/${item.effect_id}`;
    }

    function scheduledTriggeredScriptStatus(status) {
      return ({running: "运行中", succeeded: "成功", failed: "失败", timed_out: "超时", cancelled: "已取消", lost: "运行丢失"})[status] || status || "尚未运行";
    }

    // Each button asks for the one instruction it runs (row.allows).
    const scheduledTriggeredAllows = (item, instruction) => item?.allows?.[instruction] === true;

    function scheduledTriggeredSourceHtml(item, source) {
      const config = source.config || {};
      const runtime = source.runtime || {};
      const id = esc(source.component_id);
      const isTime = source.event_type === "time.reached";
      const editable = scheduledTriggeredAllows(item, "UpdateComponent") && isTime && ["at", "countdown", "recurring"].includes(config.schedule_mode);
      const editField = config.schedule_mode === "at"
        ? window.PortalI18n.html`<label>触发时间（ISO 8601）<input type="text" data-scheduled-triggered-schedule-field="scheduled_at" value="${esc(config.scheduled_at || "")}"></label>`
        : config.schedule_mode === "countdown"
          ? window.PortalI18n.html`<label>倒计时（秒）<input type="number" min="1" data-scheduled-triggered-schedule-field="delay_seconds" value="${esc(config.delay_seconds || 1)}"></label>`
          : config.schedule_mode === "recurring"
            ? config.recurrence_kind === "interval"
              ? window.PortalI18n.html`<label>间隔（秒）<input type="number" min="1" max="31536000" data-scheduled-triggered-schedule-field="interval_seconds" value="${esc(config.interval_seconds || 60)}"></label>`
              : window.PortalI18n.html`<label>日历时间<input type="time" step="1" data-scheduled-triggered-schedule-field="calendar_time" value="${esc(config.calendar_time || "09:00")}"></label>`
            : "";
      const jitterField = config.schedule_mode === "recurring" ? window.PortalI18n.html`<label>最大随机延迟（秒）<input type="number" min="0" data-scheduled-triggered-schedule-field="jitter_seconds" value="${esc(config.jitter_seconds || 0)}"></label>` : "";
      return window.PortalI18n.html`<div class="scheduled-triggered-schedule" data-scheduled-triggered-source="${id}">
    <div class="scheduled-triggered-unit">${esc(source.title || source.component_id)}</div>
    ${scheduledTriggeredSourceState(source) ? window.PortalI18n.html`<div class="scheduled-triggered-subtle" data-scheduled-triggered-source-state="${esc(runtime.state || "paused")}">${esc(scheduledTriggeredSourceState(source))}</div>` : ""}
    ${["blocked", "error"].includes(runtime.state) ? window.PortalI18n.html`<a class="scheduled-triggered-action" href="/lineage?lineage=${encodeURIComponent(item.lineage_id)}&program_item=${encodeURIComponent(source.component_id)}">打开事件与运行身份设置</a>` : ""}
    <div class="scheduled-triggered-subtle">${esc(scheduledTriggeredScheduleText(source))}</div>
    ${scheduledTriggeredNextAt(source) ? window.PortalI18n.html`<div class="scheduled-triggered-subtle">下一次 ${esc(displayDateTime(scheduledTriggeredNextAt(source)) || scheduledTriggeredNextAt(source))}</div>` : ""}
    <div class="scheduled-triggered-actions">
      ${editField}${jitterField}
      ${editable ? window.PortalI18n.html`<button type="button" class="scheduled-triggered-action" data-scheduled-triggered-action="update_schedule" data-scheduled-triggered-row-id="${esc(scheduledTriggeredItemId(item))}" data-scheduled-triggered-source-id="${id}">修改定时</button>` : ""}
      ${scheduledTriggeredAllows(item, "PauseComponent") ? window.PortalI18n.html`<button type="button" class="scheduled-triggered-action ${source.paused ? "start" : "stop"}" data-scheduled-triggered-action="${source.paused ? "resume_schedule" : "pause_schedule"}" data-scheduled-triggered-row-id="${esc(scheduledTriggeredItemId(item))}" data-scheduled-triggered-source-id="${id}">${source.paused ? window.PortalI18n.t("恢复调度") : window.PortalI18n.t("暂停调度")}</button>` : ""}
    </div>
  </div>`;
    }

    function setScheduledTriggeredTarget(agentId) {
      if (agentId) scheduledTriggeredExpanded.add(agentId);
    }

    function scheduledTriggeredLineageHtml(monitor) {
      if (!monitor?.loaded) {
        const failed = monitor?.status === "error";
        return window.PortalI18n.html`<div class="scheduled-triggered-workbench" data-scheduled-triggered-workbench><div class="${failed ? "agent-status-error" : "agent-status-muted"}">${failed ? window.PortalI18n.text`状态不可用：${esc(monitor?.error || window.PortalI18n.t("unknown error"))}` : window.PortalI18n.t("正在读取定时与自动触发…")}</div></div>`;
      }
      const items = Array.isArray(monitor.items) ? monitor.items : [];
      const errors = Array.isArray(monitor.lineage_errors) ? monitor.lineage_errors : [];
      const errorHtml = errors.map(item => window.PortalI18n.html`<div class="agent-status-error">${esc(item.title || item.lineage_id)} · ${esc(item.error || window.PortalI18n.t("Lineage 读取失败"))}</div>`).join("");
      const rowHtml = items.map((item, index) => {
        const residentId = esc(scheduledTriggeredItemId(item));
        const isEffect = item.kind === "effect";
        const runningScripts = isEffect ? [...new Map([item.last_run, ...(item.recent_runs || [])]
          .filter(run => run?.script_run_id && run.status === "running")
          .map(run => [run.script_run_id, run])).values()] : [];
        const lineageId = esc(item.lineage_id);
        const state = item.state || {};
        const health = ["ok", "warn", "paused", "error"].includes(item.health) ? item.health : "warn";
        const expanded = scheduledTriggeredExpanded.has(scheduledTriggeredItemId(item));
        const detailId = `scheduled-triggered-detail-${index}`;
        const sources = Array.isArray(item.sources) ? item.sources : [];
        const next = sources.map(scheduledTriggeredNextAt).filter(Boolean).sort()[0];
        const unavailable = sources.filter(source => ["blocked", "error"].includes(source.runtime?.state));
        const waitingForPlan = sources.some(scheduledTriggeredWaitingForPlan);
        const summary = unavailable.length ? unavailable.map(scheduledTriggeredSourceState).join("；")
          : !sources.length ? window.PortalI18n.t("无定时 Event")
            : sources.every(source => scheduledTriggeredSourceState(source)) ? sources.map(scheduledTriggeredSourceState).join("；")
              : next ? `${displayDateTime(next) || next}${waitingForPlan ? `；${window.PortalI18n.t("等待调度生成计划")}` : ""}`
                : waitingForPlan ? window.PortalI18n.t("等待调度生成计划")
                  : sources.map(scheduledTriggeredScheduleText).join("；") || window.PortalI18n.t("等待事件");
        return window.PortalI18n.html`<div class="scheduled-triggered-row is-${esc(health)}" data-scheduled-triggered-row="${residentId}">
      <div class="scheduled-triggered-main">
        <span class="scheduled-triggered-status-dot ${esc(health)}" role="img" aria-label="${esc(health)}"></span>
        <div class="scheduled-triggered-identity"><div class="scheduled-triggered-title">${esc(item.title || item.effect_id || item.resident_id)}</div><div class="scheduled-triggered-kind">${esc(item.effect_id || item.resident_id)}</div>${isEffect ? window.PortalI18n.html`<div class="scheduled-triggered-subtle">${esc((item.script_paths || []).join("；"))}</div>` : ""}</div>
        <div class="scheduled-triggered-primary"><div class="scheduled-triggered-summary-label">下次触发</div><div class="scheduled-triggered-summary-value">${esc(summary)}</div></div>
        <div class="scheduled-triggered-recent"><div class="scheduled-triggered-summary-label">当前工作</div><div class="scheduled-triggered-summary-value">${esc(isEffect ? scheduledTriggeredScriptStatus(item.last_run?.status) : state.work_state || window.PortalI18n.t("未知"))}${isEffect && item.last_run?.started_at ? ` · ${esc(displayDateTime(item.last_run.started_at) || item.last_run.started_at)}` : ""}${Number(state.queued_count) ? window.PortalI18n.text` · 等待 ${esc(state.queued_count)}` : ""}</div></div>
        <div class="scheduled-triggered-row-tools">
          ${health === "error" ? window.PortalI18n.html`<span class="scheduled-triggered-badge error">异常</span>` : ""}
          <div class="scheduled-triggered-actions">
            ${scheduledTriggeredAllows(item, "TriggerEvent") ? (item.manual_sources?.length
                  ? window.PortalI18n.html`<button type="button" class="scheduled-triggered-action run-once" data-scheduled-triggered-action="run_once" data-scheduled-triggered-row-id="${residentId}" data-scheduled-triggered-source-id="${esc(item.manual_sources[0].component_id)}">运行一次</button>`
                  : window.PortalI18n.html`<button type="button" class="scheduled-triggered-action run-once" disabled title="Lineage 中没有连到该对象的手动事件源">运行一次</button>`) : ""}
            ${scheduledTriggeredAllows(item, "CancelScriptRun") ? runningScripts.map(run => window.PortalI18n.html`<button type="button" class="scheduled-triggered-action stop" data-scheduled-triggered-action="cancel_script" data-scheduled-triggered-row-id="${residentId}" data-scheduled-triggered-script-run-id="${esc(run.script_run_id)}">取消当前运行</button>`).join("") : ""}
            ${scheduledTriggeredAllows(item, "CancelInput") && item.active_message_id ? window.PortalI18n.html`<button type="button" class="scheduled-triggered-action stop" data-scheduled-triggered-action="cancel_run" data-scheduled-triggered-row-id="${residentId}">取消当前运行</button>` : ""}
            <a class="scheduled-triggered-action" href="/lineage?lineage=${lineageId}${isEffect ? "" : `&resident=${residentId}`}">打开 Lineage</a>
          </div>
          <button class="scheduled-triggered-toggle" type="button" data-scheduled-triggered-toggle="${residentId}" data-scheduled-triggered-title="${esc(item.title || item.effect_id || item.resident_id)}" aria-expanded="${expanded ? "true" : "false"}" aria-controls="${detailId}" aria-label="${expanded ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}${esc(item.title || item.effect_id || item.resident_id)}详情"></button>
        </div>
      </div>
      <div class="scheduled-triggered-detail" id="${detailId}"${expanded ? "" : " hidden"}>
        <section class="scheduled-triggered-detail-section"><div class="scheduled-triggered-detail-title">Event 来源</div>${sources.length ? sources.map(source => scheduledTriggeredSourceHtml(item, source)).join("") : window.PortalI18n.html`<div class="scheduled-triggered-subtle">此 Resident 当前没有连接的时间或文件 Event；运行状态仍来自所属 Lineage。</div>`}</section>
        <section class="scheduled-triggered-detail-section"><div class="scheduled-triggered-detail-title">任务说明</div>${(item.tasks || []).length ? item.tasks.map(task => window.PortalI18n.html`<pre class="scheduled-triggered-task">${esc(task.text)}</pre>`).join("") : window.PortalI18n.html`<div class="scheduled-triggered-subtle">连线上没有写任务说明。</div>`}</section>
        <section class="scheduled-triggered-detail-section"><div class="scheduled-triggered-detail-title">运行与设置</div>${isEffect ? window.PortalI18n.html`<div class="scheduled-triggered-subtle">最近脚本文件 ${esc(item.last_run?.path || window.PortalI18n.t("尚无记录"))}</div>` : window.PortalI18n.html`<div class="scheduled-triggered-subtle">Provider ${esc(item.resident?.provider || window.PortalI18n.t("未知"))} · 账号 ${esc(item.resident?.account || window.PortalI18n.t("默认"))}${item.settings?.model ? window.PortalI18n.text` · 模型 ${esc(item.settings.model)}` : ""}${item.settings?.reasoning_effort ? window.PortalI18n.text` · 推理 ${esc(item.settings.reasoning_effort)}` : ""}</div>
          ${scheduledTriggeredAllows(item, "UpdateResidentSettings") ? window.PortalI18n.html`<div class="scheduled-triggered-actions" data-scheduled-triggered-settings><label>模型<input type="text" data-scheduled-triggered-setting="model" value="${esc(item.settings?.model || "")}"></label><label>推理强度<input type="text" data-scheduled-triggered-setting="reasoning_effort" value="${esc(item.settings?.reasoning_effort || "")}"></label><button type="button" class="scheduled-triggered-action" data-scheduled-triggered-action="update_settings" data-scheduled-triggered-row-id="${residentId}">修改模型</button></div>` : ""}`}
          <div class="scheduled-triggered-subtle">最近运行 ${esc(isEffect ? scheduledTriggeredScriptStatus(item.last_run?.status) : item.last_run?.status || window.PortalI18n.t("无记录"))}${item.last_run?.started_at ? window.PortalI18n.text` · 开始 ${esc(displayDateTime(item.last_run.started_at) || item.last_run.started_at)}` : ""}${item.last_run?.finished_at ? window.PortalI18n.text` · 结束 ${esc(displayDateTime(item.last_run.finished_at) || item.last_run.finished_at)}` : ""}${Number(item.consecutive_failures) ? window.PortalI18n.text` · 连续失败 ${esc(item.consecutive_failures)} 次` : ""}</div>
          ${(item.recent_runs || []).slice(1).map(run => window.PortalI18n.html`<div class="scheduled-triggered-subtle">${esc(run.script_run_id || run.trigger_path_id)} · ${esc(isEffect ? scheduledTriggeredScriptStatus(run.status) : run.status)}${run.started_at ? ` · ${esc(displayDateTime(run.started_at) || run.started_at)}` : ""}</div>`).join("")}
          ${(item.diagnostics || []).map(entry => window.PortalI18n.html`<div class="${String(entry.severity || entry.level || "").toLowerCase() === "error" ? "agent-status-error" : "scheduled-triggered-subtle"}">${esc(entry.message || entry.code || "")}</div>`).join("")}
          ${(item.warnings || []).map(warning => window.PortalI18n.html`<div class="agent-status-error">${esc(warning)}</div>`).join("")}</section>
      </div>
    </div>`;
      });
      // Rows are grouped by Lineage; the member count shows whether the Resident
      // is one member of a team Lineage or the only Resident of its own Lineage.
      const groups = new Map();
      items.forEach((item, index) => {
        const key = item.lineage_id || item.resident_id;
        if (!groups.has(key)) groups.set(key, { item, html: [] });
        groups.get(key).html.push(rowHtml[index]);
      });
      const rows = [...groups.values()].map(({ item, html }) => {
        const members = Number(item.lineage_resident_count) || 0;
        return window.PortalI18n.html`<div class="scheduled-triggered-lineage" data-scheduled-triggered-lineage="${esc(item.lineage_id)}">
      <div class="scheduled-triggered-lineage-title">${esc(item.lineage_title || item.lineage_id)}${members ? window.PortalI18n.html`<span class="scheduled-triggered-subtle"> · Lineage 共 ${esc(members)} 个 Resident</span>` : ""}</div>
      ${html.join("")}
    </div>`;
      }).join("");
      return window.PortalI18n.html`<div class="scheduled-triggered-workbench" data-scheduled-triggered-workbench>
    <div class="scheduled-triggered-workbench-summary">${groups.size} 个 Lineage · ${items.filter(item => item.kind !== "effect").length} 个 Resident · ${items.filter(item => item.kind === "effect").length} 个脚本 Effect · ${items.reduce((count, item) => count + (item.sources?.length || 0), 0)} 个事件源</div>
    ${errorHtml}${rows ? window.PortalI18n.html`<div class="scheduled-triggered-grid">${rows}</div>` : window.PortalI18n.html`<div class="agent-status-muted">当前没有接到 Resident 或脚本 Effect 的时间或文件 Event。</div>`}
  </div>`;
    }

    function renderScheduledTriggeredStatus(node) {
      const signature = JSON.stringify(scheduledTriggeredStatusData);
      if (node.dataset.scheduledTriggeredSignature === signature) return;
      const drafts = [...node.querySelectorAll("input")].filter(input =>
        !input.disabled && (input.value !== input.defaultValue || input === document.activeElement)
      ).map(input => ({
        row: input.closest("[data-scheduled-triggered-row]").dataset.scheduledTriggeredRow,
        source: input.closest("[data-scheduled-triggered-source]")?.dataset.scheduledTriggeredSource,
        field: input.dataset.scheduledTriggeredScheduleField,
        setting: input.dataset.scheduledTriggeredSetting,
        value: input.value, focused: input === document.activeElement,
        start: input.selectionStart, end: input.selectionEnd,
      }));
      node.innerHTML = scheduledTriggeredLineageHtml(scheduledTriggeredStatusData);
      node.dataset.scheduledTriggeredSignature = signature;
      for (const button of node.querySelectorAll("[data-scheduled-triggered-toggle]")) {
        button.addEventListener("click", () => {
          const agentId = button.getAttribute("data-scheduled-triggered-toggle") || "";
          const detailId = button.getAttribute("aria-controls") || "";
          const title = button.getAttribute("data-scheduled-triggered-title") || "Agent";
          const detail = detailId ? document.getElementById(detailId) : null;
          const expanded = button.getAttribute("aria-expanded") !== "true";
          button.setAttribute("aria-expanded", expanded ? "true" : "false");
          button.setAttribute("aria-label", window.PortalI18n.text`${expanded ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}${title}详情`);
          button.setAttribute("title", expanded ? window.PortalI18n.t("收起详情") : window.PortalI18n.t("展开详情"));
          if (detail) detail.hidden = !expanded;
          if (agentId) {
            if (expanded) scheduledTriggeredExpanded.add(agentId);
            else scheduledTriggeredExpanded.delete(agentId);
          }
        });
      }
      for (const button of node.querySelectorAll("[data-scheduled-triggered-action]")) {
        button.addEventListener("click", () => controlScheduledTriggered(button));
      }
      for (const draft of drafts) {
        const row = node.querySelector(`[data-scheduled-triggered-row="${CSS.escape(draft.row)}"]`);
        const container = draft.source ? row?.querySelector(`[data-scheduled-triggered-source="${CSS.escape(draft.source)}"]`) : row;
        const input = container?.querySelector(draft.field
          ? `[data-scheduled-triggered-schedule-field="${CSS.escape(draft.field)}"]`
          : `[data-scheduled-triggered-setting="${CSS.escape(draft.setting)}"]`);
        if (!input) continue;
        input.value = draft.value;
        if (draft.focused) {
          input.focus({preventScroll: true});
          if (draft.start !== null) input.setSelectionRange(draft.start, draft.end);
        }
      }
    }

    async function controlScheduledTriggered(button) {
      const rowId = button?.getAttribute?.("data-scheduled-triggered-row-id") || "";
      const action = button?.getAttribute?.("data-scheduled-triggered-action") || "";
      const supported = ["pause_schedule", "resume_schedule", "update_schedule", "run_once", "cancel_run", "cancel_script", "update_settings"];
      if (!rowId || !supported.includes(action)) return;
      const item = (currentAgentStatusData()?.scheduled_triggered?.items || []).find(entry => scheduledTriggeredItemId(entry) === rowId);
      if (!item) return;
      const sourceId = button.getAttribute("data-scheduled-triggered-source-id") || "";
      const source = (item.sources || []).find(entry => entry.component_id === sourceId);
      // Each page action is exactly one HATG ISA instruction (decision_481dc4e6292c “操作”).
      let instruction = "";
      let argumentsPayload = {};
      if (action === "run_once") {
        instruction = "TriggerEvent";
        argumentsPayload = {lineage_id: item.lineage_id, event_source_id: sourceId};
      } else if (action === "cancel_script") {
        const scriptRunId = button.getAttribute("data-scheduled-triggered-script-run-id");
        const run = [item.last_run, ...(item.recent_runs || [])].find(entry => entry?.script_run_id === scriptRunId && entry.status === "running");
        if (item.kind !== "effect" || !run) return;
        instruction = "CancelScriptRun";
        argumentsPayload = {lineage_id: item.lineage_id, script_run_id: scriptRunId};
      } else if (action === "cancel_run") {
        instruction = "CancelInput";
        argumentsPayload = {resident_id: item.resident_id, message_id: item.active_message_id};
      } else if (action === "pause_schedule" || action === "resume_schedule") {
        if (!source) return;
        instruction = "PauseComponent";
        argumentsPayload = {lineage_id: item.lineage_id, component_id: sourceId, paused: action === "pause_schedule"};
      } else if (action === "update_settings") {
        const changes = {};
        for (const input of button.closest("[data-scheduled-triggered-settings]")?.querySelectorAll("[data-scheduled-triggered-setting]") || []) {
          const value = input.value.trim();
          if (value) changes[input.getAttribute("data-scheduled-triggered-setting")] = value;
        }
        if (!Object.keys(changes).length) {
          window.PortalUI.toast(window.PortalI18n.t("请填写模型或推理强度"), "error");
          return;
        }
        instruction = "UpdateResidentSettings";
        argumentsPayload = {resident_id: item.resident_id, changes};
      } else if (action === "update_schedule" && source) {
        const config = {...source.config};
        const container = button.closest("[data-scheduled-triggered-source]");
        for (const input of container?.querySelectorAll("[data-scheduled-triggered-schedule-field]") || []) {
          const field = input.getAttribute("data-scheduled-triggered-schedule-field");
          if (field === "calendar_time" || field === "scheduled_at") {
            if (!input.value) {
              window.PortalUI.toast(window.PortalI18n.t("请填写日历时间"), "error");
              return;
            }
            config[field] = input.value;
          } else {
            const value = Number(input.value);
            if (!Number.isInteger(value) || value < (field === "interval_seconds" ? 1 : 0)) {
              window.PortalUI.toast(window.PortalI18n.t("请填写有效的时间范围"), "error");
              return;
            }
            config[field] = value;
          }
        }
        instruction = "UpdateComponent";
        argumentsPayload = {lineage_id: item.lineage_id, component_id: sourceId, changes: {config}};
      } else return;
      if (!scheduledTriggeredAllows(item, instruction)) return;
      const originalText = button.textContent;
      const row = button.closest("[data-scheduled-triggered-row]");
      const rowControls = [...(row?.querySelectorAll("button, select, input") || [])];
      const controlStates = rowControls.map(control => [control, control.disabled]);
      rowControls.forEach(control => { control.disabled = true; });
      button.textContent = window.PortalI18n.t("处理中");
      let committed = false;
      try {
        await window.PortalInstructionClient.execute(instruction, argumentsPayload, {
          idempotencyKey: `scheduled-triggered:${window.PortalUIPrimitives.randomUUID()}`,
        });
        committed = true;
        button.textContent = window.PortalI18n.t("已提交，正在刷新状态");
        await loadScheduledTriggeredStatus(currentAgentStatusData(), undefined, true, {afterMutation: true});
        if (!scheduledTriggeredStatusData?.loaded && scheduledTriggeredStatusData?.error) {
          throw new Error(scheduledTriggeredStatusData.error);
        }
        window.PortalUI.toast(window.PortalI18n.t(
          scheduledTriggeredStatusData?.refreshing || !scheduledTriggeredStatusData?.loaded
            ? "操作已提交，正在刷新状态" : "操作已完成",
        ), "success");
      } catch (err) {
        if (committed) {
          window.PortalUI.toast(window.PortalI18n.text`操作已提交，但状态刷新失败：${err?.message || String(err)}`, "warning");
        } else {
          const actionLabel = action === "run_once" ? window.PortalI18n.t("运行一次") : window.PortalI18n.t("定时控制");
          window.PortalUI.toast(window.PortalUI.errorMessage(actionLabel, err), "error");
        }
      } finally {
        controlStates.forEach(([control, disabled]) => { control.disabled = disabled; });
        button.textContent = originalText;
      }
    }

    // Follow active work quickly, then keep a low-frequency read while visible so
    // an idle or empty panel also discovers newly configured schedules.
    const SCHEDULED_TRIGGERED_FOLLOWUP_MS = 5_000;
    const SCHEDULED_TRIGGERED_IDLE_REFRESH_MS = 30_000;
    const SCHEDULED_TRIGGERED_FOLLOWUP_LIMIT = 120;
    let scheduledTriggeredFollowupTimer = 0;
    let scheduledTriggeredFollowupCount = 0;
    let scheduledTriggeredLoadInFlight = null;

    function scheduledTriggeredWorkInFlight(monitor) {
      return (Array.isArray(monitor?.items) ? monitor.items : []).some(item => item?.active_message_id
        || ["queued", "running", "stopping"].includes(String(item?.state?.work_state || ""))
        || String(item?.last_run?.status || "") === "running");
    }

    function scheduleScheduledTriggeredFollowup(monitor) {
      window.clearTimeout(scheduledTriggeredFollowupTimer);
      scheduledTriggeredFollowupTimer = 0;
      if (document.hidden || !currentHomeController().isComponentVisible("scheduled-triggered")) {
        scheduledTriggeredFollowupCount = 0;
        return;
      }
      if (monitor?.refreshing || (monitor?.status === "loading" && !monitor.loaded)) {
        scheduledTriggeredFollowupTimer = window.setTimeout(() => {
          scheduledTriggeredFollowupTimer = 0;
          if (document.hidden || !currentHomeController().isComponentVisible("scheduled-triggered")) return;
          void loadScheduledTriggeredStatus(currentAgentStatusData(), undefined, false, { quiet: true });
        }, SCHEDULED_TRIGGERED_FOLLOWUP_MS);
        return;
      }
      if (!monitor?.loaded) return;
      const active = scheduledTriggeredWorkInFlight(monitor);
      if (!active) scheduledTriggeredFollowupCount = 0;
      const fast = active && scheduledTriggeredFollowupCount < SCHEDULED_TRIGGERED_FOLLOWUP_LIMIT;
      if (fast) scheduledTriggeredFollowupCount += 1;
      scheduledTriggeredFollowupTimer = window.setTimeout(() => {
        scheduledTriggeredFollowupTimer = 0;
        if (document.hidden || !currentHomeController().isComponentVisible("scheduled-triggered")) return;
        void loadScheduledTriggeredStatus(currentAgentStatusData(), undefined, true, { quiet: true });
      }, fast ? SCHEDULED_TRIGGERED_FOLLOWUP_MS : SCHEDULED_TRIGGERED_IDLE_REFRESH_MS);
    }

    async function loadScheduledTriggeredStatus(data, signal, force = false, { quiet = false, afterMutation = false } = {}) {
      if (scheduledTriggeredLoadInFlight) {
        if (!force) return scheduledTriggeredLoadInFlight;
        await scheduledTriggeredLoadInFlight;
        if (signal?.aborted) return;
        return loadScheduledTriggeredStatus(undefined, signal, true, {quiet, afterMutation});
      }
      const previous = scheduledTriggeredStatusData;
      if (!quiet && !previous?.loaded) {
        scheduledTriggeredStatusData = {status: "loading", loaded: false, items: []};
        renderAgentStatus(currentAgentStatusData());
      }
      const request = (async () => {
        try {
          const query = afterMutation ? "?refresh=1&fresh=1" : force ? "?refresh=1" : "";
          const result = await homeApi.get(`/api/agent-status/scheduled-triggered${query}`, {signal});
          if (!signal?.aborted) scheduledTriggeredStatusData = {...result, status: result?.status || "ready", loaded: result?.loaded !== false};
          else scheduledTriggeredStatusData = previous;
        } catch (err) {
          scheduledTriggeredStatusData = signal?.aborted || (quiet && previous?.loaded) ? previous : {
            status: "error", loaded: false, items: [], error: err?.message || String(err),
          };
        }
        if (!signal?.aborted) {
          renderAgentStatus(currentAgentStatusData());
          scheduleScheduledTriggeredFollowup(scheduledTriggeredStatusData);
        }
      })();
      scheduledTriggeredLoadInFlight = request;
      try { await request; }
      finally { if (scheduledTriggeredLoadInFlight === request) scheduledTriggeredLoadInFlight = null; }
    }

    function connectScheduledTriggered({ root, context, signal }) {
      let ready = false;
      const suspend = () => {
        window.clearTimeout(scheduledTriggeredFollowupTimer);
        scheduledTriggeredFollowupTimer = 0;
        scheduledTriggeredFollowupCount = 0;
      };
      const resume = () => {
        suspend();
        if (ready && !signal.aborted && !document.hidden && currentHomeController().isComponentVisible("scheduled-triggered")) {
          void loadScheduledTriggeredStatus(undefined, signal, true, { quiet: true });
        }
      };
      listen(root, "portal:home-panel-lifecycle", event => {
        if (event.detail?.phase === "show") resume();
        else if (["hide", "disconnect", "destroy"].includes(event.detail?.phase)) suspend();
      }, signal);
      listen(document, "visibilitychange", () => document.hidden ? suspend() : resume(), signal);
      context.currentUserReady.catch(() => {}).then(() => {
        ready = true;
        if (!signal.aborted) loadScheduledTriggeredStatus(undefined, signal, true);
      });
      return suspend;
    }

    return Object.freeze({
      setScheduledTriggeredTarget, renderScheduledTriggeredStatus, loadScheduledTriggeredStatus,
      connectScheduledTriggered,
      currentScheduledTriggeredStatus: () => scheduledTriggeredStatusData,
    });
  }

  window.PortalHomeScheduledTriggered = Object.freeze({
    createScheduledTriggered, dependencies: DEPENDENCIES,
  });
})();
