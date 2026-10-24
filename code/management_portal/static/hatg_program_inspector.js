(() => {
  "use strict";

  if (window.PortalHatgProgramInspector) return;

  // The inspector for the open program object: Event sources, Effects, wires and
  // primitives, their reference pickers and invoke editors, runtime and Trigger
  // Path observations, and saving, deleting, triggering and resetting them.
  const DEPENDENCIES = Object.freeze([
    "$id", "applyProgramMutation", "AUTO_LAYOUT_VIEW", "candidateForkOf", "clone",
    "commitLineage", "definitionRevisions", "displayedProgram", "displayNodeById",
    "displayProgramComponentById", "displayProgramWireById", "esc", "executeHatgInstruction",
    "formatDate", "isOperatorExecutorNode", "isPacketOperatorComponent", "manualProgramEventKey",
    "nodeById", "normalizeOptionalLimit", "openEditLineageModal", "openEffectRecipientsEditor",
    "packetOperatorImplementation", "programComponentById", "programComponentPaused",
    "programComponentSwitchHtml", "programEffectRuntime", "programEventSourceRuntime",
    "programEventSourceStatus", "programExecutionExplanationHtml", "programFeature",
    "programResourceAddressHtml", "publishLocation", "refreshFunctionBlockMenu",
    "renderControls", "renderGraph", "renderLineageSurface", "seedById",
    "selectProgramComponent", "setBusy", "setSyncStatus", "societyService", "state",
    "syncProgramComponentSwitch", "syncProgramRunAsGuidance", "toast", "toggleProgramComponent",
  ]);

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

  function programPrimitiveDefaultType() {
    return window.PortalLineageProgram?.primitiveDefaultType?.() || "";
  }

  function programEffectOutputValue(value) {
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value);
    } catch (_error) {
      return String(value ?? "");
    }
  }

  function syncProgramWireInspectorFields(panel) {
    window.PortalLineageElementFields.syncDeliveryFields(panel);
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

  function createProgramInspector(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgProgramInspector is missing: ${missing.join(", ")}`);
    const {
      $id, applyProgramMutation, AUTO_LAYOUT_VIEW, candidateForkOf, clone, commitLineage,
      definitionRevisions, displayedProgram, displayNodeById, displayProgramComponentById,
      displayProgramWireById, esc, executeHatgInstruction, formatDate, isOperatorExecutorNode,
      isPacketOperatorComponent, manualProgramEventKey, nodeById, normalizeOptionalLimit,
      openEditLineageModal, openEffectRecipientsEditor, packetOperatorImplementation,
      programComponentById, programComponentPaused, programComponentSwitchHtml,
      programEffectRuntime, programEventSourceRuntime, programEventSourceStatus,
      programExecutionExplanationHtml, programFeature, programResourceAddressHtml,
      publishLocation, refreshFunctionBlockMenu, renderControls, renderGraph,
      renderLineageSurface, seedById, selectProgramComponent, setBusy, setSyncStatus,
      societyService, state, syncProgramComponentSwitch, syncProgramRunAsGuidance, toast,
      toggleProgramComponent,
    } = dependencies;

    const PROGRAM_INVOKE_EDITOR_STATES = new WeakMap();

    const inspectorDisabledBeforeMutation = new WeakMap();

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

    return Object.freeze({
      renderProgramInspector, effectScriptLabel, setSeedCandidateFork,
      saveProgramComponentFromInspector, emitManualProgramEvent, resetProgramEventSource,
    });
  }

  window.PortalHatgProgramInspector = Object.freeze({
    createProgramInspector, dependencies: DEPENDENCIES, effectScriptLabel,
  });
})();
