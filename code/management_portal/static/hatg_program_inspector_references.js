(() => {
  "use strict";

  if (window.PortalHatgProgramInspectorReferences) return;

  // The objects an inspector field can refer to: the Resident, Event Source,
  // Effect and Route wire options and pickers, the errors of a reference that
  // is not in the current HATG, and the reference controls that describe the
  // change a save makes and locate the saved wire on the canvas.

  const DEPENDENCIES = Object.freeze([
    "canonicalProgramEndpointId", "canonicalProgramResidentId", "displayNodeById", "displayProgramComponentById",
    "displayProgramWireById", "displayedProgram", "esc", "isOperatorExecutorNode", "programResidentReferenceError",
    "programWireAllowedTargetKinds", "referenceChangeMessage", "renderGraph", "state", "toast",
  ]);

  function createReferences(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgProgramInspectorReferences is missing: ${missing.join(", ")}`);
    const {
      canonicalProgramEndpointId, canonicalProgramResidentId, displayNodeById, displayProgramComponentById,
      displayProgramWireById, displayedProgram, esc, isOperatorExecutorNode, programResidentReferenceError,
      programWireAllowedTargetKinds, referenceChangeMessage, renderGraph, state, toast,
    } = dependencies;

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

    return {
      bindProgramReferenceControls, programEffectOptions, programEffectReferenceError, programEventSourceOptions,
      programEventSourceReferenceError, programEventWireReferenceError, programObservableEventWireOptions,
      programResidentOptions, programResidentSetPicker, programWatchedResidentPicker, programWireTargetOptions,
      programWireTargetReferenceError, showProgramReferenceWire,
    };
  }

  window.PortalHatgProgramInspectorReferences = Object.freeze({ createReferences, dependencies: DEPENDENCIES });
})();
