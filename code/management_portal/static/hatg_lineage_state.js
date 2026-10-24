(() => {
  "use strict";

  if (window.PortalHatgLineageState) return;

  // The HATG panel's copy of the open Lineage: how reads and edit results merge
  // into it, the edit queue that orders this page's writes, layout saves, and the
  // single commit through which every new Lineage reaches the page state.
  const DEPENDENCIES = Object.freeze([
    "activeSeeds", "bindResidentStateCollection", "clone", "currentTemplateController",
    "nodeResidentId", "programFeature", "reconcileGraphInteractionState", "renderControls",
    "renderGraphRuntimePresentation", "renderLineageSurface", "renderProgramInspector",
    "setSyncStatus", "societyService", "state", "toast", "updateLineageSummary",
  ]);

  function createLineageEditQueue() {
    const tails = new Map();
    let active = null;
    const assertCurrent = intent => {
      if (!intent || intent !== active || !intent.lineageId) {
        const error = new Error("Lineage 已切换或编辑器已关闭，本次编辑已取消");
        error.code = "lineage_edit_superseded";
        throw error;
      }
    };
    return {
      isActive(lineageId) {
        return Boolean(active && active.lineageId === lineageId);
      },
      activate(lineageId = "") {
        active = lineageId ? { lineageId, layoutError: null } : null;
        return active;
      },
      capture(lineageId) {
        assertCurrent(active);
        if (active.lineageId !== lineageId) {
          const error = new Error("Lineage 正在切换，本次编辑已取消");
          error.code = "lineage_edit_superseded";
          throw error;
        }
        return active;
      },
      isCurrent: intent => Boolean(intent && intent === active),
      assertCurrent,
      enqueue(intent, request, options = {}) {
        const previous = tails.get(intent.lineageId) || Promise.resolve();
        const run = previous.catch(() => null).then(async () => {
          assertCurrent(intent);
          if (!options.layout && intent.layoutError) throw intent.layoutError;
          try {
            const result = await request();
            assertCurrent(intent);
            if (options.layout) intent.layoutError = null;
            return result;
          } catch (error) {
            if (options.layout) intent.layoutError = error;
            throw error;
          }
        });
        tails.set(intent.lineageId, run);
        const settled = () => {
          if (tails.get(intent.lineageId) === run) tails.delete(intent.lineageId);
        };
        run.then(settled, settled);
        return run;
      },
    };
  }

  function hasNewLineageGraphProjection(current, incoming) {
    const previousRevision = Number(current.revision || 0);
    const nextRevision = Number(incoming.revision || 0);
    // A queried snapshot can complete a read projection at the same authored
    // revision. Its server fingerprint also participates in invalidation.
    return nextRevision > previousRevision || (
      nextRevision === previousRevision
      && Boolean(incoming.refresh_signature)
      && incoming.refresh_signature !== current.refresh_signature
    );
  }

  function lineagePayloadsEqual(left, right) {
    if (left === right) return true;
    try {
      return JSON.stringify(left) === JSON.stringify(right);
    } catch (_error) {
      return false;
    }
  }

  const HANDOFF_EXECUTION_RUNTIME_FIELDS = [
    "status",
    "message_id",
    "error",
    "requested_at",
    "delivered_at",
  ];

  function lineageItemsById(items = []) {
    return new Map(
      (items || [])
        .filter(item => item && String(item.id || ""))
        .map(item => [String(item.id), item]),
    );
  }

  function overlayLineageLayout(target, source) {
    const overlayPositions = (targetItems, sourceItems) => {
      const sourceById = lineageItemsById(sourceItems);
      (targetItems || []).forEach(item => {
        const incoming = sourceById.get(String(item?.id || ""));
        if (!incoming) return;
        if (Number.isFinite(Number(incoming.x))) item.x = Number(incoming.x);
        if (Number.isFinite(Number(incoming.y))) item.y = Number(incoming.y);
      });
    };
    overlayPositions(target.nodes, source.nodes);
    overlayPositions(target.function_blocks, source.function_blocks);
    overlayPositions(
      target.program?.components,
      source.program?.components,
    );
    target.layout_revision = Number(source.layout_revision || 0);
    target.layout_updated_at = String(source.layout_updated_at || "");
    return target;
  }

  function sameIdSet(left, right) {
    return left.size === right.size && [...left].every(value => right.has(value));
  }

  function countSetChanges(previous, next) {
    return {
      added: [...next].filter(value => !previous.has(value)).length,
      removed: [...previous].filter(value => !next.has(value)).length,
    };
  }

  function createLineageState(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgLineageState is missing: ${missing.join(", ")}`);
    const {
      activeSeeds, bindResidentStateCollection, clone, currentTemplateController, nodeResidentId,
      programFeature, reconcileGraphInteractionState, renderControls,
      renderGraphRuntimePresentation, renderLineageSurface, renderProgramInspector,
      setSyncStatus, societyService, state, toast, updateLineageSummary,
    } = dependencies;

    const lineageEditQueue = createLineageEditQueue();

    function activateLineageEdits(lineageId = "") {
      const intent = lineageEditQueue.activate(lineageId);
      state.layoutSaveQueue = new Map();
      state.layoutSaveInFlight = null;
      programFeature.endMutation();
      return intent;
    }

    async function flushLineageLayoutSave() {
      if (!state.lineage) return null;
      const intent = lineageEditQueue.capture(state.lineage.lineage_id);
      if (state.layoutSaveInFlight) return state.layoutSaveInFlight;
      if (!state.layoutSaveQueue.size) return null;
      const lineageId = intent.lineageId;
      const pending = state.layoutSaveQueue;
      const run = lineageEditQueue.enqueue(intent, async () => {
        let latest = null;
        while (lineageEditQueue.isCurrent(intent) && pending.size) {
          const positions = [...pending.values()];
          pending.clear();
          setSyncStatus("保存画布布局…", "saving");
          try {
            latest = await societyService.updateLayout( {
              lineage_id: lineageId,
              positions,
            });
          } catch (error) {
            for (const position of positions) {
              if (!pending.has(position.object_id)) pending.set(position.object_id, position);
            }
            throw error;
          }
          if (!lineageEditQueue.isCurrent(intent)) return latest;
          const responseRevision = Number(latest.layout_revision || 0);
          if (responseRevision >= Number(state.lineage.layout_revision || 0)) {
            state.lineage.layout_revision = responseRevision;
            state.lineage.layout_updated_at = String(latest.layout_updated_at || state.lineage.layout_updated_at || "");
          }
        }
        if (lineageEditQueue.isCurrent(intent)) {
          setSyncStatus(`布局已保存 · revision ${Number(state.lineage.revision || 0)}`, "saved");
        }
        return latest;
      }, { layout: true });
      const saving = run.catch(error => {
        if (lineageEditQueue.isCurrent(intent)) {
          setSyncStatus("布局保存失败", "error");
          toast(window.PortalI18n.text`画布布局保存失败：${error.message || error}`, "error", 5200);
        }
        throw error;
      }).finally(() => {
        if (lineageEditQueue.isCurrent(intent) && state.layoutSaveInFlight === saving) {
          state.layoutSaveInFlight = null;
        }
      });
      state.layoutSaveInFlight = saving;
      return saving;
    }

    function saveLineageLayout(items) {
      if (!state.lineage) return Promise.resolve(null);
      for (const item of items || []) {
        const objectId = String(item?.id || item?.object_id || "").trim();
        const x = Number(item?.x);
        const y = Number(item?.y);
        if (!objectId || !Number.isFinite(x) || !Number.isFinite(y)) continue;
        state.layoutSaveQueue.set(objectId, {
          object_id: objectId,
          x,
          y,
        });
      }
      return flushLineageLayoutSave();
    }

    function overlayHandoffRuntime(target, source) {
      ["status", "error", "ready_at"].forEach(field => {
        if (Object.hasOwn(source, field)) target[field] = clone(source[field]);
      });
      const targetExecution = target.execution && typeof target.execution === "object"
        ? target.execution
        : {};
      const sourceExecution = source.execution && typeof source.execution === "object"
        ? source.execution
        : {};
      HANDOFF_EXECUTION_RUNTIME_FIELDS.forEach(field => {
        if (Object.hasOwn(sourceExecution, field)) {
          targetExecution[field] = clone(sourceExecution[field]);
        }
      });
      target.execution = targetExecution;
      const sourceItems = new Map(
        (source.items || [])
          .filter(item => item && String(item.item_id || ""))
          .map(item => [String(item.item_id), item]),
      );
      (target.items || []).forEach(item => {
        const incoming = sourceItems.get(String(item?.item_id || ""));
        if (!incoming) return;
        item.attempts = clone(incoming.attempts || []);
        item.result = clone(incoming.result || {});
        if (incoming.result && Object.keys(incoming.result).length) {
          item.target = item.target && typeof item.target === "object"
            ? item.target
            : {};
          ["workspace_id", "path", "scope"].forEach(field => {
            if (Object.hasOwn(incoming.target || {}, field)) {
              item.target[field] = clone(incoming.target[field]);
            }
          });
        }
      });
    }

    function overlayLineageRuntime(target, source) {
      const sourceNodes = lineageItemsById(source.nodes);
      (target.nodes || []).forEach(node => {
        const incoming = sourceNodes.get(String(node?.id || ""));
        if (!incoming) return;
        node.status = incoming.status;
        node.last_execution = clone(incoming.last_execution || {});
      });

      const targetResidentIds = new Set(
        (target.nodes || []).map(node => String(node?.id || "")),
      );
      const sourceAbsorptions = (source.edges || []).filter(edge => (
        edge?.type === "absorption"
        && targetResidentIds.has(String(edge.source || ""))
        && targetResidentIds.has(String(edge.target || ""))
      ));
      target.edges = [
        ...(target.edges || []).filter(edge => edge?.type !== "absorption"),
        ...clone(sourceAbsorptions),
      ];
      const sourceEdges = lineageItemsById(source.edges);
      (target.edges || []).forEach(edge => {
        const incoming = sourceEdges.get(String(edge?.id || ""));
        if (!incoming) return;
        if (edge.type === "handoff") {
          overlayHandoffRuntime(edge, incoming);
        }
      });
      target.runtime_revision = Number(source.runtime_revision || 0);
      target.runtime_updated_at = String(source.runtime_updated_at || "");
      return target;
    }

    function mergeLineageStateDomains(current, incoming) {
      if (!incoming) return current;
      if (
        !current
        || String(current.lineage_id || "") !== String(incoming.lineage_id || "")
      ) return clone(incoming);

      const currentLayout = Number(current.layout_revision || 0);
      const incomingLayout = Number(incoming.layout_revision || 0);
      const currentRuntime = Number(current.runtime_revision || 0);
      const incomingRuntime = Number(incoming.runtime_revision || 0);
      const takesIncomingGraph = hasNewLineageGraphProjection(current, incoming);
      const next = clone(takesIncomingGraph ? incoming : current);

      if (incomingLayout > currentLayout) {
        overlayLineageLayout(next, incoming);
      } else if (currentLayout > incomingLayout && takesIncomingGraph) {
        overlayLineageLayout(next, current);
      }
      if (incomingRuntime > currentRuntime) {
        overlayLineageRuntime(next, incoming);
      } else if (currentRuntime > incomingRuntime && takesIncomingGraph) {
        overlayLineageRuntime(next, current);
      }
      // Executions belong to Agent history, not the Lineage relation-work
      // revision. A new failed/cancelled turn can leave every graph revision
      // unchanged; take its observation from the latest serialized read.
      const observedResidents = lineageItemsById(incoming.nodes);
      for (const node of next.nodes || []) {
        const observed = observedResidents.get(String(node?.id || ""));
        if (observed && Object.hasOwn(observed, "last_execution")) {
          node.last_execution = clone(observed.last_execution || {});
        }
      }
      // Invoke-wire backlog is a volatile delivery projection. It changes
      // without mutating the authored graph or the Resident runtime revision,
      // so the newest server response must always win independently.
      if (Object.hasOwn(incoming, "program_invoke_wire_runtime")) {
        next.program_invoke_wire_runtime = clone(
          incoming.program_invoke_wire_runtime || {},
        );
      }
      if (Object.hasOwn(incoming, "program_event_source_runtime")) {
        next.program_event_source_runtime = clone(
          incoming.program_event_source_runtime || {},
        );
      }
      if (Object.hasOwn(incoming, "program_effect_runtime")) {
        next.program_effect_runtime = clone(
          incoming.program_effect_runtime || {},
        );
      }
      if (Object.hasOwn(incoming, "program_execution_explanations")) {
        next.program_execution_explanations = clone(
          incoming.program_execution_explanations || {},
        );
      }
      // The refresh signature belongs to the graph it was read with; it arrives
      // with `incoming` only when that graph is taken above.
      return next;
    }

    function displayedResidentIds(lineage) {
      return new Set(
        (lineage?.nodes || [])
          .filter(node => node.accessible !== false)
          .map(nodeResidentId)
          .filter(Boolean),
      );
    }

    function notifyLineageMembershipChange(previous, next) {
      const nodes = countSetChanges(
        new Set((previous.nodes || []).map(nodeResidentId)),
        new Set((next.nodes || []).map(nodeResidentId)),
      );
      if (nodes.added || nodes.removed) {
        const changes = [nodes.added ? `新增 ${nodes.added} 个节点` : "", nodes.removed ? `移除 ${nodes.removed} 个节点` : ""].filter(Boolean).join("，");
        toast(window.PortalI18n.text`Lineage 已更新：${changes}。`, "success", 5200);
      }
      const seeds = countSetChanges(
        new Set(activeSeeds(previous).map(seed => seed.id)),
        new Set(activeSeeds(next).map(seed => seed.id)),
      );
      if (seeds.added || seeds.removed) {
        const changes = [seeds.added ? `新增 ${seeds.added} 个候选` : "", seeds.removed ? `收起 ${seeds.removed} 个候选` : ""].filter(Boolean).join("，");
        toast(window.PortalI18n.text`探索 fork 已同步候选集合：${changes}。`, "success", 5200);
      }
    }

    function forgetRemovedLineageMembers(next) {
      const nodeIds = new Set((next.nodes || []).map(node => String(node?.id || "")));
      const residentIds = new Set((next.nodes || []).map(nodeResidentId));
      for (const nodeId of [...state.runtimeStatus.keys()]) {
        if (!nodeIds.has(nodeId)) state.runtimeStatus.delete(nodeId);
      }
      for (const byResident of [
        state.residentRuntimeById,
        state.sessionUserMessageCounts,
        state.sessionContextPressures,
      ]) {
        for (const residentId of [...(byResident?.keys?.() || [])]) {
          if (!residentIds.has(residentId)) byResident.delete(residentId);
        }
      }
    }

    function renderCommittedLineage(change, scope) {
      if (!scope) return;
      if (scope === "surface" || change.structureChanged) {
        updateLineageSummary();
        renderLineageSurface();
        return;
      }
      updateLineageSummary();
      renderGraphRuntimePresentation();
      renderProgramInspector();
      currentTemplateController()?.render();
      renderControls();
    }

    // Every Lineage read or mutation result for the open Lineage enters the
    // panel here (opening another Lineage is selectLineage). The merge keeps
    // each state domain at its newest revision; this function then keeps what
    // the panel derives from the displayed Lineage in step with it and redraws
    // what changed:
    // - `source: "mutation"` (default) is the result of this page's own edit
    //   and always redraws the surface; `source: "background"` is a refresh
    //   read: it yields to an edit or layout save in flight, whose own result
    //   carries the newer state, applies only when something changed, redraws
    //   only the changed scope and reports added or removed members.
    // - `render`: "surface", "changes" (runtime scope unless the structure
    //   changed) or false (the caller keeps an open form or draws itself).
    // - `afterMerge(lineage)` runs with the merged Lineage in place, before the
    //   redraw, for selection the redraw must already see.
    // - `replace: true` installs `incoming` as is (rolling back a local edit).
    function commitLineage(incoming, options = {}) {
      const background = options.source === "background";
      const change = { changed: false, structureChanged: false, runtimeChanged: false, membershipChanged: false };
      const previous = state.lineage;
      if (!incoming || !previous || String(incoming.lineage_id || "") !== String(previous.lineage_id || "")) return change;
      if (background && (state.layoutSaveInFlight || state.structureMutationInFlight)) return change;
      const next = options.replace ? incoming : mergeLineageStateDomains(previous, incoming);
      if (background && lineagePayloadsEqual(previous, next)) return change;
      change.changed = true;
      change.structureChanged = Boolean(options.replace)
        || hasNewLineageGraphProjection(previous, incoming)
        || Number(next.layout_revision || 0) !== Number(previous.layout_revision || 0);
      change.runtimeChanged = Number(next.runtime_revision || 0) !== Number(previous.runtime_revision || 0);
      change.membershipChanged = !sameIdSet(displayedResidentIds(previous), displayedResidentIds(next));
      state.lineage = next;
      state.localVersion += 1;
      forgetRemovedLineageMembers(next);
      options.afterMerge?.(next);
      reconcileGraphInteractionState();
      if (change.membershipChanged) bindResidentStateCollection();
      if (background) {
        setSyncStatus(`Lineage 已加载 · r${Number(next.revision || 0)}`, "saved");
        if (change.structureChanged) notifyLineageMembershipChange(previous, next);
      }
      const scope = Object.hasOwn(options, "render") ? options.render : (background ? "changes" : "surface");
      renderCommittedLineage(change, scope);
      return change;
    }

    function applyLineageEventProjection(lineageId, lineage) {
      if (lineageId !== state.lineage?.lineage_id) return;
      commitLineage(lineage, { source: "background" });
    }

    return Object.freeze({
      createLineageEditQueue, lineageEditQueue, activateLineageEdits, flushLineageLayoutSave,
      saveLineageLayout, lineagePayloadsEqual, mergeLineageStateDomains, commitLineage,
      applyLineageEventProjection,
    });
  }

  window.PortalHatgLineageState = Object.freeze({
    createLineageState, dependencies: DEPENDENCIES, createLineageEditQueue, lineagePayloadsEqual,
  });
})();
