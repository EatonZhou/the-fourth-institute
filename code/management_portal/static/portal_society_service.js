(function () {
  "use strict";

  // Society page operations. Every change is one named HATG ISA instruction
  // (decision_898b8911a2ff); a mutation answers with the instruction result
  // plus the freshly read Lineage. Canvas callers can request defer_refresh
  // to receive the committed result and a separate refresh promise instead.
  if (window.PortalSocietyService) return;

  function createClient(options = {}) {
    const get = options.get || (path => window.PortalAPI.get(path, { cache: "no-store" }));
    const execute = options.execute || ((name, args, controls) => window.PortalInstructionClient.execute(name, args, controls));
    const revisions = new Map();
    const reads = new Map();
    const reconciliations = new Map();
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

    async function loadReferenceFamilies(lineage, references) {
      // A slow identity must not leave the other read slots idle. The parent
      // only gains badges and navigation metadata, never child Function Blocks.
      const families = new Map();
      const remaining = references.values();
      const worker = async () => {
        for (const node of remaining) {
          try {
            const { resident } = await execute("GetResident", { resident_id: node.id });
            if (resident?.resident_id !== node.id || resident.lineage_id !== node.home_lineage_id) continue;
            const family = resident.family?.role === "facade" && resident.family.family_id
              ? { family_id: resident.family.family_id, facade: node.id } : null;
            families.set(node.id, family);
          } catch { /* Missing or denied identity stays an unclassified reference. */ }
        }
      };
      await Promise.all(Array.from({length: Math.min(8, references.length)}, worker));
      return {
        ...lineage,
        nodes: lineage.nodes.map(node => families.has(node.id)
          ? { ...node, _family_reference: families.get(node.id) } : node),
        // The child can change without advancing the parent's authored revision.
        _reference_family_signature: JSON.stringify([...families].sort(([a], [b]) => a.localeCompare(b))),
      };
    }

    function lineageRead(lineageId) {
      const id = requiredLineageId(lineageId);
      if (reads.has(id)) return reads.get(id);
      const projection = Promise.resolve().then(() => execute("GetLineage", { lineage_id: id })).then(result => {
        const lineage = result?.lineage || {};
        if (lineage.revision !== undefined) revisions.set(id, lineage.revision);
        const candidates = (lineage.nodes || []).filter(node => node.hierarchy_reference
          && node.accessible !== false && node.home_lineage_id && node.home_lineage_id !== id);
        const references = candidates.length ? loadReferenceFamilies(lineage, candidates) : null;
        return { lineage, references };
      });
      const complete = projection.then(({ lineage, references }) => references || lineage);
      const read = { projection, complete };
      reads.set(id, read);
      const release = () => { if (reads.get(id) === read) reads.delete(id); };
      complete.then(release, release);
      return read;
    }

    const loadLineage = lineageId => lineageRead(lineageId).complete;
    const loadLineageProjection = lineageId => lineageRead(lineageId).projection;

    // Capture the precondition before dispatch. A conflict only refreshes reads;
    // it must never replay a config assembled from an older definition.
    async function mutate(lineageId, name, args, payload = {}, prefix = "lineage", { definition = true, reload = true } = {}) {
      const scopedInstructions = new Set([
        "ForkResident", "UpdateResident", "MoveLineageObjects",
        "CreateWorkspaceHandoff", "UpdateWorkspaceHandoff", "DeleteWorkspaceHandoff", "RunWorkspaceHandoff",
      ]);
      if (scopedInstructions.has(name)) args = { ...args, lineage_id: requiredLineageId(lineageId) };
      // Capture the version used to construct args before any await can
      // install a newer projection in the displayed graph.
      const expectedVersion = payload.expected_version ?? revisionOf(lineageId);
      // An earlier committed write may still be reconciling. Do not submit a
      // new definition before that read settles. Its captured precondition
      // stays unchanged so stale drafts still conflict.
      if (definition && reconciliations.has(lineageId)) {
        const pending = reconciliations.get(lineageId);
        try {
          if (pending) await pending.catch(() => loadLineage(lineageId));
          else await loadLineage(lineageId);
          if (reconciliations.get(lineageId) === null) reconciliations.delete(lineageId);
        } catch (refreshError) {
          throw Object.assign(new Error(window.PortalI18n.t("图刷新失败，本次修改尚未提交。请刷新后重试。")), {
            code: "lineage.refresh_required", refreshError,
            body: { error: { commit_state: "not_committed" } },
          });
        }
      }
      const instructionControls = controls(payload, prefix);
      // Creating a fresh identity appends under the server's Lineage lock.
      // It does not replace the graph the page previously read.
      if (definition && (name !== "CreateResident" || payload.expected_version !== undefined)) {
        instructionControls.expectedVersion = expectedVersion;
      }
      let result;
      const residentDeletes = name === "ApplyLineageChanges" && !args.dry_run
        ? (args.operations || []).filter(op => op.op === "delete" && op.kind === "resident").map(op => op.id)
        : [];
      try {
        const operation = () => execute(name, args, instructionControls);
        result = residentDeletes.length === 1 && args.operations.length === 1 && window.PortalResidentService?.withResidentDeletion
          ? await window.PortalResidentService.withResidentDeletion(residentDeletes[0], operation)
          : await operation();
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
      const deletedResidents = result?.deleted_resident_ids || (name === "ApplyLineageChanges"
        ? residentDeletes : name.startsWith("Delete") ? (result?.resident_ids || []) : []);
      const previousRead = reads.get(lineageId);
      const refresh = Promise.resolve().then(async () => {
        try {
          if (deletedResidents.length) await options.onResidentsDeleted?.(deletedResidents);
          // A read started before the commit cannot acknowledge this write.
          if (previousRead) await previousRead.complete.catch(() => {});
          const lineage = reload && lineageId ? await loadLineage(lineageId) : undefined;
          return lineage ? { ...(result || {}), lineage } : (result || {});
        } catch (error) {
          // The write already committed. Keep its receipt distinct from this
          // failed read so callers can close creation forms without replaying it.
          error.committedMutation = { instruction: name, result: result || {} };
          error.message = `${window.PortalI18n.t("修改已提交，但图刷新失败。请刷新查看，勿重复提交。")} ${error.message || error}`;
          throw error;
        }
      });
      if (definition && reload && lineageId) {
        reconciliations.set(lineageId, refresh);
        // Failed reconciliation remains a barrier: the next action first
        // retries the read, never the already committed instruction.
        refresh.then(() => {
          if (reconciliations.get(lineageId) === refresh) reconciliations.delete(lineageId);
        }, () => {
          if (reconciliations.get(lineageId) === refresh) {
            reconciliations.set(lineageId, null);
          }
        });
      }
      refresh.catch(() => {});
      return payload.defer_refresh ? { ...(result || {}), refresh } : refresh;
    }

    function editProgram(payload, operation, prefix, placement = {}, additionalOperations = []) {
      const lineageId = requiredLineageId(payload.lineage_id);
      return mutate(lineageId, "ApplyLineageChanges", {
        lineage_id: lineageId, operations: [operation, ...additionalOperations], ...placement,
      }, payload, prefix);
    }

    return Object.freeze({
      listLineages() {
        return get("/lineage/api/lineages");
      },
      loadLineage, loadLineageProjection,
      mutate,
      async createLineage(payload = {}) {
        const creation = controls(payload, "lineage-create");
        const result = await execute("CreateLineage", pick(payload, [
          "title", "description", "project_id", "leader_resident_id",
          "definition",
        ]), creation);
        if (!payload.familize) return result;
        try {
          const family = await execute("Familize", {
            lineage_id: result.lineage_id, scope: "lineage", ...pick({ goal: payload.description }, ["goal"]),
          }, { idempotencyKey: `${creation.idempotencyKey}:family` });
          return { ...result, family_id: family.family_id };
        } catch (error) {
          error.details = { ...error.details, lineage_id: result.lineage_id };
          error.message = `${window.PortalI18n.t("Lineage 已创建，但 Familize 未完成。")} ${error.message || error}`;
          throw error;
        }
      },
      updateMetadata(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        const changes = pick(payload, [
          "title", "description", "project_id", "leader_resident_id",
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
        return mutate(payload.lineage_id, "MoveLineageObjects", pick(payload, ["object_ids", "target_lineage_id", "allow_empty_source"]),
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
        // Quick actions still present fixed round counts in the view.
        const recent = /^recent-(\d+)$/.exec(payload.fork_mode);
        const fork = recent ? { ...payload, fork_mode: "recent", recent_rounds: Number(recent[1]) } : payload;
        return mutate(lineageId, "ForkResident", pick(fork, [
          "source_resident_id", "fork_mode", "recent_rounds", "context_source_resident_id", "message",
          "title", "provider", "account_id", "family_id", ...PLACEMENT,
        ]), payload, "resident-fork");
      },
      updateResident(payload = {}) {
        return mutate(String(payload.lineage_id || ""), "UpdateResident", pick(payload, ["resident_id", "title"]), payload, "resident-update");
      },
      deleteNode(payload = {}) {
        return editProgram(payload, { op: "delete", kind: "resident", id: payload.resident_id }, "resident-delete");
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
      async createProgramComponent(payload = {}) {
        const result = await editProgram(payload, {
          op: "create", kind: "component",
          value: pick(payload, ["kind", "title", "client_key", "config"]),
        }, "component-create", pick(payload, PLACEMENT));
        return { ...result, component_id: result.created_ids?.[0] };
      },
      async saveProgramComponent(payload = {}) {
        const id = payload.component_id;
        const changes = pick(payload.component || payload.changes || {}, ["title", "config"]);
        const result = await editProgram(payload, { op: "update", kind: "component", id, changes },
          "component-update", {}, payload.wire_operations);
        const withComponent = receipt => ({
          ...receipt,
          component: receipt.lineage?.program?.components?.find(item => item.id === id) || { id, ...changes },
          created_wire_ids: receipt.created_ids?.filter(value => value.startsWith("wire_")) || [],
          updated_wire_ids: receipt.updated_ids?.filter(value => value.startsWith("wire_")) || [],
          removed_wire_ids: receipt.deleted_ids?.filter(value => value.startsWith("wire_")) || [],
        });
        const response = withComponent(result);
        if (result.refresh) {
          response.refresh = result.refresh.then(withComponent);
          response.refresh.catch(() => {});
        }
        return response;
      },
      deleteProgramComponent(payload = {}) {
        return editProgram(payload, { op: "delete", kind: "component", id: payload.component_id }, "component-delete");
      },
      saveProgramWire(payload = {}) {
        const source = payload.wire || payload;
        const wire = pick(source, [
          "client_key", "source", "source_port", "target", "target_port", "label", "config",
        ]);
        if (payload.wire_id) {
          return editProgram(payload, { op: "update", kind: "wire", id: payload.wire_id, changes: wire }, "wire-update");
        }
        return editProgram(payload, { op: "create", kind: "wire", value: { ...wire, kind: source.kind } }, "wire-create");
      },
      deleteProgramWire(payload = {}) {
        return editProgram(payload, { op: "delete", kind: "wire", id: payload.wire_id }, "wire-delete");
      },
      listBoardEntries(payload = {}) {
        // cursor "0" reads from the oldest kept entry instead of from this user's read position.
        return execute("ListBoardEntries", { cursor: "0", limit: 200, ...pick(payload, ["lineage_id", "board_id", "cursor", "limit", "author_id", "kind"]) });
      },
      createBoardEntry(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "CreateBoardEntry", {
          lineage_id: lineageId, ...pick(payload, ["board_id", "text", "kind", "data", "refs", "supersedes", "expires_at"]),
        }, { idempotency_key: payload.idempotency_key }, "board-entry", { definition: false, reload: false });
      },
      deleteBoardEntry(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "DeleteBoardEntry", {
          lineage_id: lineageId, ...pick(payload, ["board_id", "entry_id"]),
        }, { idempotency_key: payload.idempotency_key }, "board-entry-retract", { definition: false, reload: false });
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
        return mutate(lineageId, "SetComponentPaused", { lineage_id: lineageId, component_id: payload.component_id, paused: true }, {}, "component-pause", { definition: false });
      },
      resumeComponent(payload = {}) {
        const lineageId = requiredLineageId(payload.lineage_id);
        return mutate(lineageId, "SetComponentPaused", { lineage_id: lineageId, component_id: payload.component_id, paused: false }, {}, "component-resume", { definition: false });
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
        return mutate(String(payload.lineage_id || ""), "RunWorkspaceHandoff",
          { handoff_id: payload.handoff_id }, payload, "handoff-prepare", { definition: false });
      },
      executeInstruction(name, args = {}, instructionControls = {}) {
        return execute(name, args, instructionControls);
      },
    });
  }

  window.PortalSocietyService = Object.freeze({ createClient });
})();
