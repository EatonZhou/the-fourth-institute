(() => {
  "use strict";

  if (window.PortalHatgProgramInspectorPrimitiveFields) return;

  // A primitive's definition-driven fields: the Resident sets and Watch
  // subjects the primitive inspector leaves to the HATG, and drawing and
  // binding the inspector with them.

  const DEPENDENCIES = Object.freeze([
    "bindProgramReferenceControls", "canonicalProgramResidentId", "displayNodeById", "displayProgramWireById",
    "displayedProgram", "esc", "programEffectOptions", "programEventSourceOptions",
    "programObservableEventWireOptions", "programResidentOptions", "programResidentSetPicker",
    "programWatchedResidentPicker",
  ]);

  function createPrimitiveFields(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgProgramInspectorPrimitiveFields is missing: ${missing.join(", ")}`);
    const {
      bindProgramReferenceControls, canonicalProgramResidentId, displayNodeById, displayProgramWireById,
      displayedProgram, esc, programEffectOptions, programEventSourceOptions, programObservableEventWireOptions,
      programResidentOptions, programResidentSetPicker, programWatchedResidentPicker,
    } = dependencies;

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

    return {
      bindProgramPrimitiveInspector, programPrimitiveInspectorHtml,
    };
  }

  window.PortalHatgProgramInspectorPrimitiveFields = Object.freeze({ createPrimitiveFields, dependencies: DEPENDENCIES });
})();
