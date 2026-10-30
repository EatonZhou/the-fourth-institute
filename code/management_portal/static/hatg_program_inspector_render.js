(() => {
  "use strict";

  if (window.PortalHatgProgramInspectorRender) return;

  // Drawing the inspector for the open object (Seed, loop, Event Source,
  // Effect, Packet Operator or wire) with its listeners, keeping an open
  // draft through redraws, and syncing the Event Source fields.

  const DEPENDENCIES = Object.freeze([
    "$id", "AUTO_LAYOUT_VIEW", "applyProgramMutation", "bindProgramInvokeEditor", "bindProgramPrimitiveInspector",
    "bindProgramReferenceControls", "bindProgramTriggerPathObservation", "candidateForkOf", "definitionRevisions",
    "deleteProgramComponent", "deleteProgramWire", "displayNodeById", "displayProgramComponentById",
    "displayProgramWireById", "emitManualProgramEvent", "esc", "formatDate", "isPacketOperatorComponent",
    "loopRuntimeRowsHtml", "normalizeOptionalLimit", "openEditLineageModal", "openEffectRecipientsEditor",
    "packetOperatorImplementation", "programComponentSwitchHtml", "programDeliveryWireFieldsHtml",
    "programEffectRuntime", "programEffectRuntimeHtml", "programEventSourceRuntime", "programEventSourceStatus",
    "programExecutionExplanationHtml", "programFeature", "programPrimitiveDefaultType",
    "programPrimitiveInspectorHtml", "programResidentOptions", "programResourceAddressHtml",
    "programTriggerPathObservationHtml", "programWireTargetHint", "programWireTargetLabel",
    "programWireTargetOptions", "programWireTargetPlaceholder", "publishLocation", "refreshFunctionBlockMenu",
    "renderControls", "renderEffectDiagnostics", "renderGraph", "resetProgramEventSource",
    "saveProgramComponentFromInspector", "saveProgramWireFromInspector", "setSeedCandidateFork", "societyService",
    "state", "syncManualProgramEventInspectorBusy", "syncProgramComponentSwitch", "syncProgramLeaderGuidance",
    "toast", "toggleProgramComponent",
  ]);

  function syncProgramWireInspectorFields(panel) {
    window.PortalLineageElementFields.syncDeliveryFields(panel);
  }

  function createRender(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgProgramInspectorRender is missing: ${missing.join(", ")}`);
    const {
      $id, AUTO_LAYOUT_VIEW, applyProgramMutation, bindProgramInvokeEditor, bindProgramPrimitiveInspector,
      bindProgramReferenceControls, bindProgramTriggerPathObservation, candidateForkOf, definitionRevisions,
      deleteProgramComponent, deleteProgramWire, displayNodeById, displayProgramComponentById, displayProgramWireById,
      emitManualProgramEvent, esc, formatDate, isPacketOperatorComponent, loopRuntimeRowsHtml, normalizeOptionalLimit,
      openEditLineageModal, openEffectRecipientsEditor, packetOperatorImplementation, programComponentSwitchHtml,
      programDeliveryWireFieldsHtml, programEffectRuntime, programEffectRuntimeHtml, programEventSourceRuntime,
      programEventSourceStatus, programExecutionExplanationHtml, programFeature, programPrimitiveDefaultType,
      programPrimitiveInspectorHtml, programResidentOptions, programResourceAddressHtml,
      programTriggerPathObservationHtml, programWireTargetHint, programWireTargetLabel, programWireTargetOptions,
      programWireTargetPlaceholder, publishLocation, refreshFunctionBlockMenu, renderControls,
      renderEffectDiagnostics, renderGraph, resetProgramEventSource, saveProgramComponentFromInspector,
      saveProgramWireFromInspector, setSeedCandidateFork, societyService, state, syncManualProgramEventInspectorBusy,
      syncProgramComponentSwitch, syncProgramLeaderGuidance, toast, toggleProgramComponent,
    } = dependencies;

    const inspectorDisabledBeforeMutation = new WeakMap();

    function syncProgramEventInspectorFields(panel) {
      syncProgramLeaderGuidance(panel);
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
      if (!onClose) refreshFunctionBlockMenu();
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
          <section class="programInspectorNote" data-event-leader>
            <p data-event-leader-note></p>
            <button type="button" data-event-leader-settings${readonly ? " disabled" : ""}>设置 Leader</button>
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
        panel.querySelector("[data-event-leader-settings]")?.addEventListener("click", () => {
          openEditLineageModal();
          $id("lineageLeaderInput").focus();
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

    function seedCandidateForkFieldHtml(component, readonly) {
      const sourceId = candidateForkOf(component.id)?.source || "";
      const active = String(component.config?.status || "active") === "active";
      const residents = (state.lineage?.nodes || [])
        .map(node => `<option value="${esc(node.id)}"${node.id === sourceId ? " selected" : ""}>${esc(node.title || node.id)}</option>`)
        .join("");
      return `<label><span>候选 Fork</span><select data-seed-candidate-fork${readonly || !active ? " disabled" : ""}><option value=""${sourceId ? "" : " selected"}>未建立（不能采纳）</option>${residents}</select><small>候选从这个 Resident 的工作出发，采纳时 Fork 它；选择后立即生效。也可以把 Resident 的“+”拖到这个 Seed 上。</small></label>`;
    }

    return {
      renderProgramInspector,
    };
  }

  window.PortalHatgProgramInspectorRender = Object.freeze({ createRender, dependencies: DEPENDENCIES });
})();
