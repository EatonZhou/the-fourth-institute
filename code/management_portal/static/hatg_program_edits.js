(() => {
  "use strict";

  if (window.PortalHatgProgramEdits) return;

  // Changing a Lineage's program from the canvas: running one HATG instruction or
  // another mutation through the edit queue and committing its result, drawing a
  // wire from port to port, and creating a primitive dropped from the component
  // library. The status line under the canvas reports each change.
  const DEPENDENCIES = Object.freeze([
    "$eventRoot", "$id", "$queryAll", "commitLineage", "currentProgram", "displayGraphItemById",
    "displayProgramWireById", "flushHatgViewPreferenceSave", "graphSelectedId",
    "lineageEditQueue", "nextNodePosition", "programComponentById", "programFeature",
    "renderGraph", "renderLineageSurface", "renderProgramInspector",
    "saveProgramComponentFromInspector", "separateGraphObject", "societyService", "state",
    "toast",
  ]);

  function createProgramEdits(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgProgramEdits is missing: ${missing.join(", ")}`);
    const {
      $eventRoot, $id, $queryAll, commitLineage, currentProgram, displayGraphItemById,
      displayProgramWireById, flushHatgViewPreferenceSave, graphSelectedId, lineageEditQueue,
      nextNodePosition, programComponentById, programFeature, renderGraph, renderLineageSurface,
      renderProgramInspector, saveProgramComponentFromInspector, separateGraphObject,
      societyService, state, toast,
    } = dependencies;

    function renderProgramWireHint() {
      const hint = $id("programWireHint");
      const text = $id("programWireHintText");
      const eyebrow = $id("programWireHintEyebrow");
      const cancel = $id("cancelProgramWireBtn");
      if (!hint || !text) return;
      const draft = state.programWireDraft;
      const selected = displayGraphItemById(graphSelectedId());
      const selectedPorts = selected && state.graphViewMode === "context"
        ? window.PortalLineageProgram?.portsForNode?.(selected) || []
        : [];
      const ready = Boolean(
        !draft

        && state.selectedProgramObjectId
        && !displayProgramWireById(state.selectedProgramObjectId)
        && selectedPorts.some(port => port.direction === "out" && !port.anchor_only),
      );
      hint.hidden = !draft && !ready;
      if (!draft) {
        delete hint.dataset.wireType;
        hint.dataset.mode = ready ? "ready" : "";
        if (eyebrow) eyebrow.textContent = ready ? window.PortalI18n.t("端口已显示") : "";
        text.textContent = ready
          ? window.PortalI18n.t("点击这个节点的任意输出端口开始连接；可连接的目标随后会高亮")
          : "";
        if (cancel) cancel.hidden = true;
        return;
      }
      hint.dataset.mode = "connecting";
      hint.dataset.wireType = draft.type;
      if (eyebrow) eyebrow.textContent = window.PortalI18n.t("正在连接");
      if (cancel) cancel.hidden = false;
      const title = window.PortalLineageProgram?.manifest?.wire_types?.[draft.type]?.title
        || draft.type;
      text.textContent = draft.binding === "watch"
        ? window.PortalI18n.t("观察对象：请选择高亮的卡片或关系；Esc 或点击空白处取消")
        : window.PortalI18n.text`${title}：请选择高亮的卡片或入口；Esc 或点击空白处取消`;
    }

    function cancelProgramWireDraft(options = {}) {
      if (!state.programWireDraft) return false;
      programFeature.setWireDraft(null);
      renderGraph();
      renderProgramWireHint();
      if (options.notify) toast(window.PortalI18n.t("已取消原语连线。"), "warning");
      return true;
    }

    async function saveOpenProgramComponentDraftBeforeWire() {
      const panel = $id("programInspectorPanel");
      const form = panel?.querySelector(
        "[data-program-component-form], [data-program-operator-form]",
      );
      if (!form || form.dataset.programDraftDirty !== "true") return "unchanged";
      if (form.dataset.programDraftConflict === "true") {
        toast(window.PortalI18n.t("草稿尚未保存，请核对后点击保存，再继续连线。"), "warning");
        return "blocked";
      }
      const componentId = String(panel?.dataset.componentId || "");
      if (!componentId || !programComponentById(componentId)) return "unchanged";
      const previousRevision = Number(state.lineage?.revision || 0);
      await saveProgramComponentFromInspector(componentId, form);
      const saved = Number(state.lineage?.revision || 0) > previousRevision;
      if (!saved && form.isConnected) {
        form.dataset.programDraftDirty = "true";
      }
      return saved ? "saved" : "blocked";
    }

    function programMutationBusyNotice() {
      toast(window.PortalI18n.t("上一项程序图修改仍在保存；保存完成后再连接端口。"), "warning", 2400);
    }

    async function handleProgramPortClick(port) {
      if (!state.lineage) return;
      if (state.programMutationInFlight) {
        programMutationBusyNotice();
        return;
      }
      let normalized = window.PortalLineageProgram.normalizePort(port);
      if (!normalized?.node_id || (!normalized.port_id && normalized.type !== "object")) return;
      const draftSaveStatus = await saveOpenProgramComponentDraftBeforeWire();
      if (draftSaveStatus === "blocked") return;
      if (!state.lineage) return;
      if (state.programMutationInFlight) {
        programMutationBusyNotice();
        return;
      }
      if (draftSaveStatus === "saved") {
        const refreshedNode = displayGraphItemById(normalized.node_id);
        const refreshedPort = (
          window.PortalLineageProgram?.portsForNode?.(refreshedNode) || []
        ).find(candidate => (
          String(candidate?.port_id || "")
            === normalized.port_id
          && String(candidate?.direction || "") === normalized.direction
        ));
        if (!refreshedPort) {
          renderGraph();
          renderProgramWireHint();
          toast(window.PortalI18n.t("原语配置已保存，端口随配置发生变化；请从新端口重新连线。"), "warning", 4200);
          return;
        }
        normalized = window.PortalLineageProgram.normalizePort(refreshedPort);
      }
      if (!state.programWireDraft) {
        if (normalized.direction !== "out") {
          toast(window.PortalI18n.t("请先选择一个输出端口开始连线。"), "warning");
          return;
        }
        state.programWireDraft = normalized;
        renderGraph();
        renderProgramWireHint();
        return;
      }
      if (
        normalized.node_id === state.programWireDraft.node_id
        && normalized.port_id === state.programWireDraft.port_id
        && normalized.direction === state.programWireDraft.direction
      ) {
        cancelProgramWireDraft();
        return;
      }
      const validation = window.PortalLineageProgram?.validateConnection?.(
        state.programWireDraft,
        normalized,
        {
          existingWires: currentProgram().wires,
          nodes: [...(state.lineage?.nodes || []), ...currentProgram().components],
        },
      );
      if (!validation?.ok) {
        toast(
          validation?.reason === "effect_endpoint_occupied"
                ? window.PortalI18n.t("这个 Effect 端点已经绑定了对象。")
              : validation?.reason === "packet_operator_cycle"
                ? window.PortalI18n.t("Packet Operator 的 Packet 路由不能形成循环。")
            : window.PortalI18n.t("这个端口与当前连线类型不兼容；连线仍保持选择。"),
          "warning",
          3000,
        );
        return;
      }
      // Built when the queued edit runs: wires connected in quick succession each
      // carry the revision their predecessor produced, not the one seen at click time.
      const draft = state.programWireDraft;
      const request = () => window.PortalLineageProgram.buildWireSaveRequest(
        state.lineage,
        draft,
        normalized,
        {
          existingWires: currentProgram().wires,
          nodes: [...(state.lineage?.nodes || []), ...currentProgram().components],
        },
      );
      try {
        await applyProgramMutation(
          () => societyService.saveProgramWire(request()),
          `${validation.type} 连线已建立。`,
        );
        programFeature.setWireDraft(null);
        renderGraph();
        renderProgramWireHint();
      } catch {
        renderGraph();
        renderProgramWireHint();
      }
    }

    async function createProgramComponentFromDrop(kind, position) {
      if (
        !state.lineage

        || !window.PortalLineageProgram?.componentKinds?.includes(
          String(kind || ""),
        )
      ) return;
      const response = await applyProgramMutation(
        () => societyService.createProgramComponent(
          window.PortalLineageProgram.buildComponentCreateRequest(
            state.lineage,
            kind,
            position,
            { placement: state.graphController.getPlacementGeometry({
              kind,
              primitive_kind: kind,
              title: window.PortalI18n.text`新 ${window.PortalLineageProgram.manifest.component_kinds[kind].title}`,
              config: {},
            }) },
          ),
        ),
        `${window.PortalLineageProgram?.manifest?.component_kinds?.[kind]?.title || "原语"} 已放入 HATG。`,
      );
      if (!response?.component_id) return;
      await new Promise(resolve => window.requestAnimationFrame(resolve));
      if (state.lineage?.lineage_id === response.lineage.lineage_id) {
        state.graphController?.ensureNodeVisible?.(response.component_id);
      }
    }

    function bindPrimitiveLibrary() {
      $queryAll("[data-hatg-library-kind]").forEach(card => {
        if (card.dataset.programBound === "true") return;
        card.dataset.programBound = "true";
        const kind = String(card.dataset.hatgLibraryKind || "");
        card.setAttribute("draggable", "true");
        card.addEventListener("dragstart", event => {
          if (!state.lineage || !event.dataTransfer) {
            event.preventDefault();
            return;
          }
          event.dataTransfer.effectAllowed = "copy";
          if (window.PortalLineageProgram?.componentKinds?.includes(kind)) {
            event.dataTransfer.setData(window.PortalCompatibility.identifier("primitive_transfer"), kind);
          }
          card.classList.add("dragging");
        });
        card.addEventListener("dragend", () => card.classList.remove("dragging"));
        card.addEventListener("click", () => {
          if (!state.lineage) return;
          if (window.PortalLineageProgram?.componentKinds?.includes(kind)) {
            createProgramComponentFromDrop(kind, nextNodePosition()).catch(() => {});
          }
        });
      });
      $id("cancelProgramWireBtn")?.addEventListener("click", () => {
        cancelProgramWireDraft();
      });
      $eventRoot().addEventListener("keydown", event => {
        if (event.key === "Escape" && state.programWireDraft) {
          event.preventDefault();
          cancelProgramWireDraft();
        }
      });
    }

    let programMutationStatusTimer = 0;

    function showProgramMutationStatus(message) {
      const status = $id("programMutationStatus");
      if (!status) return;
      window.clearTimeout(programMutationStatusTimer);
      status.textContent = message;
      status.hidden = false;
      programMutationStatusTimer = window.setTimeout(() => {
        status.hidden = true;
        programMutationStatusTimer = 0;
      }, 3600);
    }

    function applyProgramMutation(request, message, { failure = "程序图更新失败", draftForm = null } = {}) {
      if (!state.lineage) return Promise.resolve(null);
      const lineageId = state.lineage.lineage_id;
      const intent = lineageEditQueue.capture(lineageId);
      let placementSave = null;
      const run = lineageEditQueue.enqueue(intent, async () => {
          programFeature.beginMutation();
          renderProgramInspector();
          try {
            const response = await request();
            if (!response?.lineage || response.lineage.lineage_id !== lineageId) {
              throw new Error("Portal 没有返回更新后的程序图");
            }
            if (!lineageEditQueue.isCurrent(intent)) return null;
            if (draftForm?.isConnected) draftForm.dataset.programDraftDirty = "false";
            commitLineage(response.lineage);
            const resizedObjectId = response.component_id || response.component?.id
              || response.function_block_id || response.function_block?.id;
            if (resizedObjectId) {
              await new Promise(resolve => window.requestAnimationFrame(resolve));
              if (lineageEditQueue.isCurrent(intent)) {
                // Layout uses the same edit queue. Await it after this definition
                // edit completes, and never retry a committed mutation on failure.
                placementSave = separateGraphObject(resizedObjectId).catch(() => null);
              }
            }
            const createdNodeId = response.facade_resident_id || response.function_block_id;
            if (createdNodeId) {
              await new Promise(resolve => window.requestAnimationFrame(resolve));
              if (lineageEditQueue.isCurrent(intent)) {
                state.graphController?.ensureNodeVisible?.(createdNodeId, { persist: true });
                // The structure is already committed. A preference failure gets
                // its own notice, never an invitation to submit the creation again.
                await flushHatgViewPreferenceSave(lineageId).catch(() => {});
              }
            }
            if (message) showProgramMutationStatus(message);
            return response;
          } finally {
            if (lineageEditQueue.isCurrent(intent)) {
              programFeature.endMutation();
              renderLineageSurface();
            }
          }
        });
      return run.then(async response => {
        if (placementSave) await placementSave;
        return response;
      }).catch(error => {
        if (lineageEditQueue.isCurrent(intent)) toast(`${failure}：${error.message || error}`, "error", 6200);
        throw error;
      });
    }

    // Run one HATG ISA instruction on the open Lineage. Lineage-scoped calls get
    // lineage_id; a change answers with the result plus the re-read Lineage.
    async function executeHatgInstruction(
      instruction,
      argumentsValue = {},
      options = {},
    ) {
      if (!state.lineage && options.withLineage !== false) throw new Error("当前没有可操作的 Lineage");
      const lineageId = state.lineage?.lineage_id || "";
      const args = options.withLineage === false
        ? { ...argumentsValue }
        : { lineage_id: lineageId, ...argumentsValue };
      return societyService.mutate(lineageId, instruction, args, {
        idempotency_key: options.idempotencyKey,
        expected_version: options.expectedVersion,
      }, "hatg", { definition: options.definition !== false, reload: options.reload !== false });
    }

    return Object.freeze({
      applyProgramMutation, executeHatgInstruction, renderProgramWireHint,
      cancelProgramWireDraft, handleProgramPortClick, createProgramComponentFromDrop,
      bindPrimitiveLibrary,
    });
  }

  window.PortalHatgProgramEdits = Object.freeze({
    createProgramEdits, dependencies: DEPENDENCIES,
  });
})();
