(function () {
  "use strict";

  // Society page operations. Every change is one named HATG ISA instruction
  // (decision_898b8911a2ff); a mutation answers with the instruction result
  // plus the freshly read Lineage so the page can redraw the current state.
  if (window.PortalSocietyService) return;

  function createClient(options = {}) {
    const get = options.get || (path => window.PortalAPI.get(path, { cache: "no-store" }));
    const execute = options.execute || ((name, args, controls) => window.PortalInstructionClient.execute(name, args, controls));
    const revisions = new Map();
    const revisionOf = lineageId => options.getRevision?.(lineageId) ?? revisions.get(lineageId);

    const requiredLineageId = value => {
      const lineageId = String(value || "").trim();
      if (!lineageId) throw new TypeError("lineage_id required");
      return lineageId;
    };
    const newKey = prefix => `${prefix}:${globalThis.PortalUIPrimitives.randomUUID()}`;
    const controls = (payload, prefix) => ({
      idempotencyKey: String(payload?.idempotency_key || "").trim() || newKey(prefix),
    });
    const pendingControls = (_payload, prefix) => ({ idempotencyKey: newKey(prefix) });
    const pick = (source, names) => Object.fromEntries(
      names.filter(name => source && Object.hasOwn(source, name) && source[name] !== undefined).map(name => [name, source[name]]),
    );
    const PLACEMENT = ["near_resident_id", "placement", "x", "y"];

    async function loadLineage(lineageId) {
      const result = await execute("GetLineage", { lineage_id: requiredLineageId(lineageId) });
      const lineage = result?.lineage || {};
      if (lineage.revision !== undefined) revisions.set(lineageId, lineage.revision);
      return lineage;
    }

    // Capture the precondition before dispatch. A conflict only refreshes reads;
    // it must never replay a config assembled from an older definition.
    async function mutate(lineageId, name, args, payload = {}, prefix = "lineage", { definition = true, reload = true } = {}) {
      const scopedInstructions = new Set([
        "ForkResident", "UpdateResident", "DeleteResident", "MoveLineageObjects",
        "CreateWorkspaceHandoff", "UpdateWorkspaceHandoff", "DeleteWorkspaceHandoff", "PrepareWorkspaceHandoff",
      ]);
      if (scopedInstructions.has(name)) args = { ...args, lineage_id: requiredLineageId(lineageId) };
      const instructionControls = controls(payload, prefix);
      // Creating a fresh identity appends under the server's Lineage lock.
      // It does not replace the graph the page previously read.
      if (definition && (name !== "CreateResident" || payload.expected_version !== undefined)) {
        instructionControls.expectedVersion = payload.expected_version ?? revisionOf(lineageId);
      }
      let result;
      try {
        result = await execute(name, args, instructionControls);
      } catch (error) {
        if (definition && ["instruction.conflict", "instruction.version_conflict"].includes(error.code)) {
          const reason = error.message;
          error.message = `${window.PortalI18n.t("图已被其他操作修改，本次修改未保存。请查看最新图后重新编辑。")} ${reason}`;
          const expected = instructionControls.expectedVersion;
          const ids = new Set([lineageId, ...(expected && typeof expected === "object" ? Object.keys(expected) : [])].filter(Boolean));
          for (const id of ids) {
            let lineage;
            try { lineage = await loadLineage(id); }
            catch (refreshError) {
              error.refreshError = refreshError;
              error.message = `${window.PortalI18n.t("图已发生冲突，本次修改未保存；重读失败，请刷新后重新编辑。")} ${reason}`;
            }
            await options.onConflict?.({ lineageId: id, lineage, error });
          }
        }
        throw error;
      }
      const deletedResidents = result?.deleted_resident_ids || (name === "DeleteResident" ? [args.resident_id]
        : name === "ApplyLineageChanges" ? (args.operations || []).filter(op => op.op === "delete" && op.kind === "resident").map(op => op.id)
        : name.startsWith("Delete") ? (result?.resident_ids || []) : []);
      let lineage;
      try {
        if (deletedResidents.length) await options.onResidentsDeleted?.(deletedResidents);
        lineage = reload && lineageId ? await loadLineage(lineageId) : undefined;
      } catch (error) {
        // The write already committed. Keep its receipt distinct from this
        // failed read so callers can close creation forms without replaying it.
        error.committedMutation = { instruction: name, result: result || {} };
        error.message = `${window.PortalI18n.t("修改已提交，但图刷新失败。请刷新查看，勿重复提交。")} ${error.message || error}`;
        throw error;
      }
      return lineage ? { ...(result || {}), lineage } : (result || {});
    }

    return Object.freeze({
      listLineages() {
        return get("/lineage/api/lineages");
      },
      loadLineage,
      mutate,
      createLineage(payload = {}) {
        return execute("CreateLineage", pick(payload, [
          "title", "description", "project_id", "leader_resident_id",
          "definition", "familize",
        ]), { idempotencyKey: String(payload.idempotency_key || "").trim() || newKey("lineage-create") });
      },
      updateMetadata(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        const changes = pick(payload, [
          "title", "description", "project_id", "leader_resident_id", "run_as_principal",
        ]);
        return mutate(lineageId, "UpdateLineage", { lineage_id: lineageId, changes }, payload, "lineage-update");
      },
      rename(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "UpdateLineage", { lineage_id: lineageId, changes: { title: payload.title } }, payload, "lineage-rename");
      },
      deleteLineage(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "DeleteLineage", { lineage_id: lineageId }, payload, "lineage-delete", { reload: false });
      },
      moveObjects(payload = {}) {
        const ids = [payload.lineage_id, payload.target_lineage_id].filter(Boolean);
        const expected = Object.fromEntries(ids.filter(id => revisionOf(id) !== undefined).map(id => [id, revisionOf(id)]));
        return mutate(payload.lineage_id, "MoveLineageObjects", pick(payload, ["object_ids", "target_lineage_id"]),
          { ...payload, expected_version: payload.expected_version ?? expected }, "lineage-move", { reload: false });
      },
      getMoveOperation(operationId) {
        return execute("GetMoveOperation", { operation_id: operationId });
      },
      // Queued deliveries on one wire.
      listPendingDeliveries(payload = {}) {
        return execute("ListPendingDeliveries", {
          lineage_id: requiredLineageId(payload.lineage_id), wire_id: payload.wire_id,
        });
      },
      updatePendingDelivery(payload = {}) {
        return execute("UpdatePendingDelivery", {
          lineage_id: requiredLineageId(payload.lineage_id), wire_id: payload.wire_id,
          delivery_id: payload.delivery_id, prompt: String(payload.prompt ?? ""),
        }, pendingControls(payload, "pending-update"));
      },
      deletePendingDelivery(payload = {}) {
        return execute("DeletePendingDelivery", {
          lineage_id: requiredLineageId(payload.lineage_id), wire_id: payload.wire_id,
          delivery_id: payload.delivery_id,
        }, pendingControls(payload, "pending-delete"));
      },
      loadNodeMetadata({ lineageId, residentId } = {}) {
        const query = new URLSearchParams({
          lineage_id: requiredLineageId(lineageId),
          resident_id: String(residentId || "").trim(),
        });
        if (!query.get("resident_id")) throw new TypeError("resident_id required");
        return get(`/lineage/api/node-metadata?${query}`);
      },
      // Coordinates are last-write-wins under their own layout_revision, so
      // a save neither carries nor advances the definition revision.
      updateLayout(payload = {}) {
        return execute("UpdateLineageLayout", {
          lineage_id: requiredLineageId(payload.lineage_id),
          positions: Array.isArray(payload.positions) ? payload.positions : [],
        }, controls(payload, "lineage-layout"));
      },
      createNode(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "CreateResident", {
          lineage_id: lineageId,
          ...pick(payload, ["title", "provider", "account_id", "settings", "workspace_paths", "family_id", ...PLACEMENT]),
        }, payload, "resident-create");
      },
      branchNode(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "ForkResident", pick(payload, [
          "source_resident_id", "fork_mode", "context_source_resident_id", "message",
          "title", "provider", "account_id", "family_id", ...PLACEMENT,
        ]), payload, "resident-fork");
      },
      updateResident(payload = {}) {
        return mutate(String(payload.lineage_id || ""), "UpdateResident", pick(payload, ["resident_id", "title"]), payload, "resident-update");
      },
      deleteNode(payload = {}) {
        return mutate(String(payload.lineage_id || ""), "DeleteResident", pick(payload, ["resident_id"]), payload, "resident-delete");
      },
      deleteObjects(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        const operations = [
          ...(payload.component_ids || []).map(id => ({ op: "delete", kind: "component", id })),
          ...(payload.wire_ids || []).map(id => ({ op: "delete", kind: "wire", id })),
          ...(payload.resident_ids || []).map(id => ({ op: "delete", kind: "resident", id })),
        ];
        return mutate(lineageId, "ApplyLineageChanges", { lineage_id: lineageId, operations }, payload, "lineage-delete-objects");
      },
      absorbNode(payload = {}) {
        return mutate(String(payload.lineage_id || ""), "AbsorbContext", pick(payload, ["source_resident_id", "target_resident_id"]), payload, "resident-absorb", { definition: false });
      },
      saveWorkspaceFile(payload = {}) {
        return execute("UpdateWorkspaceFile", pick(payload, ["address", "content"]), {
          idempotencyKey: String(payload.idempotency_key || "").trim() || newKey("workspace-file"),
        });
      },
      createProgramComponent(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "CreateComponent", {
          lineage_id: lineageId,
          ...pick(payload, ["kind", "title", "client_key", "config", "watch_subject", ...PLACEMENT]),
        }, payload, "component-create");
      },
      saveProgramComponent(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        const changes = pick(payload.component || payload.changes || {}, ["title", "config", "watch_subject"]);
        return mutate(lineageId, "UpdateComponent", { lineage_id: lineageId, component_id: payload.component_id, changes }, payload, "component-update");
      },
      deleteProgramComponent(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "DeleteComponent", { lineage_id: lineageId, component_id: payload.component_id }, payload, "component-delete");
      },
      saveProgramWire(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        const wire = pick(payload.wire || payload, [
          "client_key", "source", "source_port", "target", "target_port", "kind", "label", "config",
        ]);
        if (payload.wire_id) {
          return mutate(lineageId, "UpdateWire", { lineage_id: lineageId, wire_id: payload.wire_id, changes: wire }, payload, "wire-update");
        }
        return mutate(lineageId, "CreateWire", { lineage_id: lineageId, ...wire }, payload, "wire-create");
      },
      deleteProgramWire(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "DeleteWire", { lineage_id: lineageId, wire_id: payload.wire_id }, payload, "wire-delete");
      },
      listTriggerPaths(payload = {}) {
        return execute("ListTriggerPaths", pick(payload, ["lineage_id", "resident_id", "cursor", "limit"]));
      },
      getTriggerPath(payload = {}) {
        return execute("GetTriggerPath", pick(payload, ["lineage_id", "trigger_path_id", "offset", "limit"]));
      },
      triggerProgramEvent(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "TriggerEvent", {
          lineage_id: lineageId,
          ...pick(payload, ["event_source_id", "wire_id", "prompt", "content_parts"]),
        }, { idempotency_key: payload.idempotency_key }, "event-trigger", { definition: false });
      },
      getComponentRuntime(payload = {}) {
        return execute("GetComponentRuntime", pick(payload, ["lineage_id", "component_id"]));
      },
      pauseComponent(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "PauseComponent", { lineage_id: lineageId, component_id: payload.component_id, paused: true }, {}, "component-pause", { definition: false });
      },
      resumeComponent(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "PauseComponent", { lineage_id: lineageId, component_id: payload.component_id, paused: false }, {}, "component-resume", { definition: false });
      },
      resetProgramEvent(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "ResetComponentRuntime", {
          lineage_id: lineageId, component_id: payload.component_id || payload.event_source_id,
        }, {}, "component-reset", { definition: false });
      },
      resetWireDeliveryCount(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "ResetComponentRuntime", {
          lineage_id: lineageId, wire_id: payload.wire_id,
        }, {}, "wire-delivery-reset", { definition: false });
      },
      createHandoff(payload = {}) {
        const lineageId = String(payload.lineage_id || "");
        return mutate(lineageId, "CreateWorkspaceHandoff", pick(payload, [
          "source_resident_id", "target_resident_id", "items", "explanation", "label",
        ]), payload, "handoff-create");
      },
      updateHandoff(payload = {}) {
        return mutate(String(payload.lineage_id || ""), "UpdateWorkspaceHandoff", {
          handoff_id: payload.handoff_id,
          changes: pick(payload.changes || payload, ["items", "explanation", "label"]),
        }, payload, "handoff-update");
      },
      deleteHandoff(payload = {}) {
        return mutate(String(payload.lineage_id || ""), "DeleteWorkspaceHandoff", { handoff_id: payload.handoff_id }, payload, "handoff-delete");
      },
      executeHandoff(payload = {}) {
        return mutate(String(payload.lineage_id || ""), "PrepareWorkspaceHandoff",
          pick(payload, ["handoff_id", "source_resident_id", "target_resident_id"]), payload, "handoff-prepare", { definition: false });
      },
      executeInstruction(name, args = {}, instructionControls = {}) {
        return execute(name, args, instructionControls);
      },
    });
  }

  window.PortalSocietyService = Object.freeze({ createClient });
})();
