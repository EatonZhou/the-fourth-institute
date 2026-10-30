(() => {
  "use strict";

  if (window.PortalHatgProgramInspectorSaving) return;

  // Saving and deleting the open object: a component's form with its Watch
  // subject, a wire's target, invoke arguments and delivery settings, and a
  // Seed's candidate Fork.

  const DEPENDENCIES = Object.freeze([
    "applyProgramMutation", "canonicalProgramEndpointId", "canonicalProgramResidentId", "clone", "commitLineage",
    "definitionRevisions", "displayProgramWireById", "executeHatgInstruction", "isPacketOperatorComponent",
    "nodeById", "programComponentById", "programEffectReferenceError", "programEventSourceReferenceError",
    "programEventWireReferenceError", "programInvokeEditorState", "programInvokeProfileKey",
    "programPrimitiveDefaultType", "programResidentReferenceError", "programWireTargetReferenceError",
    "renderLineageSurface", "seedById", "selectProgramComponent", "setSyncStatus", "showProgramReferenceWire",
    "societyService", "state", "switchProgramInvokeEditorTarget", "toast",
  ]);

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

  function createSaving(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgProgramInspectorSaving is missing: ${missing.join(", ")}`);
    const {
      applyProgramMutation, canonicalProgramEndpointId, canonicalProgramResidentId, clone, commitLineage,
      definitionRevisions, displayProgramWireById, executeHatgInstruction, isPacketOperatorComponent, nodeById,
      programComponentById, programEffectReferenceError, programEventSourceReferenceError,
      programEventWireReferenceError, programInvokeEditorState, programInvokeProfileKey, programPrimitiveDefaultType,
      programResidentReferenceError, programWireTargetReferenceError, renderLineageSurface, seedById,
      selectProgramComponent, setSyncStatus, showProgramReferenceWire, societyService, state,
      switchProgramInvokeEditorTarget, toast,
    } = dependencies;

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

    return {
      deleteProgramComponent, deleteProgramWire, saveProgramComponentFromInspector, saveProgramWireFromInspector,
      setSeedCandidateFork,
    };
  }

  window.PortalHatgProgramInspectorSaving = Object.freeze({ createSaving, dependencies: DEPENDENCIES });
})();
