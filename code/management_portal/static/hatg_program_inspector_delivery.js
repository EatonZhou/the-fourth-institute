(() => {
  "use strict";

  if (window.PortalHatgProgramInspectorDelivery) return;

  // A Route wire's delivery: its invoke arguments per target kind, the
  // argument editor that follows a changed target, the message budget and
  // its reset, and the queue of deliveries waiting on the wire.

  const DEPENDENCIES = Object.freeze([
    "canonicalProgramEndpointId", "clone", "commitLineage", "displayProgramComponentById", "esc", "formatDate",
    "programWireTargetReferenceError", "renderGraph", "setBusy", "societyService", "state", "toast",
  ]);

  function createDelivery(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgProgramInspectorDelivery is missing: ${missing.join(", ")}`);
    const {
      canonicalProgramEndpointId, clone, commitLineage, displayProgramComponentById, esc, formatDate,
      programWireTargetReferenceError, renderGraph, setBusy, societyService, state, toast,
    } = dependencies;

    const PROGRAM_INVOKE_EDITOR_STATES = new WeakMap();

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
      <div class="programDeliveryQueueHead"><strong data-wire-sent-count>${runtime.sent_count || 0} 条消息已发送</strong><button type="button" class="portalButton is-compact" data-wire-delivery-reset${readonly ? " disabled" : ""}>重置发送计数</button></div>
      <span data-wire-remaining>${runtime.max_messages == null ? window.PortalI18n.t("剩余次数：无限次") : window.PortalI18n.text`剩余次数：${runtime.remaining_messages ?? runtime.max_messages}`}</span>
      <small data-wire-limit-state>${runtime.message_limit_reached ? window.PortalI18n.t("已达到发送上限，新的消息将不再发送。") : window.PortalI18n.t("计数持久保存；重置后继续接收新消息，不补发已丢弃的消息。")}</small>
    </section>` : "";
      const pendingCount = Math.max(0, Math.trunc(Number(runtime.pending_count) || 0));
      const backlogHtml = window.PortalI18n.html`<section class="programWireBacklogStatus" data-event-wire-backlog data-pending-event-count="${pendingCount}">
        <div class="programDeliveryQueueHead">
          <strong data-delivery-count>${pendingCount} 条投递正在这条边上等待</strong>
          <button type="button" class="portalButton is-compact" data-delivery-refresh>刷新队列</button>
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

    return {
      bindProgramInvokeEditor, programDeliveryWireFieldsHtml, programInvokeEditorState, programInvokeProfileKey,
      switchProgramInvokeEditorTarget,
    };
  }

  window.PortalHatgProgramInspectorDelivery = Object.freeze({ createDelivery, dependencies: DEPENDENCIES });
})();
