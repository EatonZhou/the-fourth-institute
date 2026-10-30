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
    "syncProgramComponentSwitch", "syncProgramLeaderGuidance", "toast", "toggleProgramComponent",
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

  // createProgramInspector is the HATG panel's part for the open program
  // object. Its own parts live in module scripts that load before this one;
  // inspectorPart(name) returns what a script registers:
  //   hatg_program_inspector_references.js  reference options, pickers and
  //     errors, and the reference controls
  //   hatg_program_inspector_primitive_fields.js  the primitive inspector and
  //     the fields it leaves to the HATG
  //   hatg_program_inspector_delivery.js  a Route wire's invoke arguments,
  //     message budget and waiting deliveries
  //   hatg_program_inspector_observation.js  Effect and loop runtime, Trigger
  //     Paths and Effect script diagnostics
  //   hatg_program_inspector_manual_events.js  triggering and resetting Event
  //     Sources
  //   hatg_program_inspector_saving.js  saving and deleting components, wires
  //     and a Seed's candidate Fork
  //   hatg_program_inspector_render.js  drawing the inspector and its listeners
  // createProgramInspector creates the parts in that order and returns the
  // inspector's functions.
  function inspectorPart(name) {
    const part = window[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
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
      societyService, state, syncProgramComponentSwitch, syncProgramLeaderGuidance, toast,
      toggleProgramComponent,
    } = dependencies;

    const {
      bindProgramReferenceControls, programEffectOptions, programEffectReferenceError, programEventSourceOptions,
      programEventSourceReferenceError, programEventWireReferenceError, programObservableEventWireOptions,
      programResidentOptions, programResidentSetPicker, programWatchedResidentPicker, programWireTargetOptions,
      programWireTargetReferenceError, showProgramReferenceWire,
    } = inspectorPart("PortalHatgProgramInspectorReferences").createReferences({
      canonicalProgramEndpointId, canonicalProgramResidentId, displayNodeById, displayProgramComponentById,
      displayProgramWireById, displayedProgram, esc, isOperatorExecutorNode, programResidentReferenceError,
      programWireAllowedTargetKinds, referenceChangeMessage, renderGraph, state, toast,
    });

    const {
      bindProgramPrimitiveInspector, programPrimitiveInspectorHtml,
    } = inspectorPart("PortalHatgProgramInspectorPrimitiveFields").createPrimitiveFields({
      bindProgramReferenceControls, canonicalProgramResidentId, displayNodeById, displayProgramWireById,
      displayedProgram, esc, programEffectOptions, programEventSourceOptions, programObservableEventWireOptions,
      programResidentOptions, programResidentSetPicker, programWatchedResidentPicker,
    });

    const {
      bindProgramInvokeEditor, programDeliveryWireFieldsHtml, programInvokeEditorState, programInvokeProfileKey,
      switchProgramInvokeEditorTarget,
    } = inspectorPart("PortalHatgProgramInspectorDelivery").createDelivery({
      canonicalProgramEndpointId, clone, commitLineage, displayProgramComponentById, esc, formatDate,
      programWireTargetReferenceError, renderGraph, setBusy, societyService, state, toast,
    });

    const {
      bindProgramTriggerPathObservation, loopRuntimeRowsHtml, programEffectRuntimeHtml,
      programTriggerPathObservationHtml, renderEffectDiagnostics,
    } = inspectorPart("PortalHatgProgramInspectorObservation").createObservation({
      displayNodeById, esc, executeHatgInstruction, formatDate, programComponentById, programEffectRuntime,
      societyService, state,
    });

    const {
      emitManualProgramEvent, resetProgramEventSource, syncManualProgramEventInspectorBusy,
    } = inspectorPart("PortalHatgProgramInspectorManualEvents").createManualEvents({
      $id, commitLineage, manualProgramEventKey, programComponentById, programComponentPaused,
      programEventSourceRuntime, programFeature, renderGraph, renderLineageSurface, setBusy, societyService, state,
      toast,
    });

    const {
      deleteProgramComponent, deleteProgramWire, saveProgramComponentFromInspector, saveProgramWireFromInspector,
      setSeedCandidateFork,
    } = inspectorPart("PortalHatgProgramInspectorSaving").createSaving({
      applyProgramMutation, canonicalProgramEndpointId, canonicalProgramResidentId, clone, commitLineage,
      definitionRevisions, displayProgramWireById, executeHatgInstruction, isPacketOperatorComponent, nodeById,
      programComponentById, programEffectReferenceError, programEventSourceReferenceError,
      programEventWireReferenceError, programInvokeEditorState, programInvokeProfileKey, programPrimitiveDefaultType,
      programResidentReferenceError, programWireTargetReferenceError, renderLineageSurface, seedById,
      selectProgramComponent, setSyncStatus, showProgramReferenceWire, societyService, state,
      switchProgramInvokeEditorTarget, toast,
    });

    const { renderProgramInspector } = inspectorPart("PortalHatgProgramInspectorRender").createRender({
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
    });

    return Object.freeze({
      renderProgramInspector, effectScriptLabel, setSeedCandidateFork,
      saveProgramComponentFromInspector, emitManualProgramEvent, resetProgramEventSource,
    });
  }

  window.PortalHatgProgramInspector = Object.freeze({
    createProgramInspector, dependencies: DEPENDENCIES, effectScriptLabel,
  });
})();
