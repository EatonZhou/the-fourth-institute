(() => {
  "use strict";

  if (window.PortalHatgProgramInspectorObservation) return;

  // What the open object did: the last run of an Effect, the rounds of a
  // loop, the Trigger Path of the last run with its steps, Executions and
  // unknown outcomes to resolve, and the saved Effect script's diagnostics.

  const DEPENDENCIES = Object.freeze([
    "displayNodeById", "esc", "executeHatgInstruction", "formatDate", "programComponentById", "programEffectRuntime",
    "societyService", "state",
  ]);

  function programEffectOutputValue(value) {
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value);
    } catch (_error) {
      return String(value ?? "");
    }
  }

  function createObservation(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgProgramInspectorObservation is missing: ${missing.join(", ")}`);
    const {
      displayNodeById, esc, executeHatgInstruction, formatDate, programComponentById, programEffectRuntime,
      societyService, state,
    } = dependencies;

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

    function loopRuntimeRowsHtml(runtime = {}) {
      const rows = [
        ["当前轮数", runtime.round ?? 0],
        ["状态", runtime.paused ? "已暂停" : runtime.state === "stopped" ? "已停止" : "进行中"],
        ["运行代次", runtime.generation ?? 0],
      ];
      return rows.map(([label, value]) => window.PortalI18n.html`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("");
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

    return {
      bindProgramTriggerPathObservation, loopRuntimeRowsHtml, programEffectRuntimeHtml,
      programTriggerPathObservationHtml, renderEffectDiagnostics,
    };
  }

  window.PortalHatgProgramInspectorObservation = Object.freeze({ createObservation, dependencies: DEPENDENCIES });
})();
