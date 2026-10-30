(() => {
  "use strict";

  if (window.PortalHatgPanelTemplateShelf) return;

  // The Lineage template shelf: mounting LineageTemplates on the canvas
  // and resolving where on the canvas a template is applied.

  const DEPENDENCIES = Object.freeze([
    "$id", "applyProgramMutation", "executeHatgInstruction", "nodeById", "panelHost", "renderGraph",
    "state", "toast",
  ]);

  function createTemplateShelf(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgPanelTemplateShelf is missing: ${missing.join(", ")}`);
    const {
      $id, applyProgramMutation, executeHatgInstruction, nodeById, panelHost, renderGraph, state, toast,
    } = dependencies;

    // Templates use the same selection and coordinate system as canvas gestures.
    function templateCanvasTarget(event) {
      const position = state.graphController?.screenToPosition?.(event.clientX, event.clientY) || { x: 50, y: 50 };
      const hit = event.target?.closest?.("[data-node-id], [data-edge-id], [data-function-block-boundary]");
      const id = hit?.dataset.nodeId || hit?.dataset.edgeId || hit?.dataset.functionBlockBoundary || "";
      if (state.selectedCanvasObjectIds.has(id)) {
        const element_ids = [...state.selectedCanvasObjectIds];
        return { kind: "selection", element_ids, resident_ids: element_ids.filter(id => Boolean(nodeById(id))), ...position };
      }
      if (nodeById(id)) return { kind: "resident", resident_ids: [id], ...position };
      return { kind: "canvas", ...position };
    }

    // Templates own their shelf, editor, drag handling and application forms.
    function mountTemplates() {
      panelHost.templateController?.destroy();
      panelHost.templateController = window.LineageTemplates.mount({
        shelf: $id("templateShelf"),
        canvas: $id("hatgCanvas"),
        getContext: () => ({ lineage_id: state.lineage?.lineage_id || "", leader_resident_id: state.lineage?.leader_resident_id, residents: state.lineage?.nodes || [] }),
        resolveTarget: templateCanvasTarget,
        residentSettingsOptions: () => state.agentBackendCatalog,
        onChange: renderGraph,
        toast,
        // Previews change nothing, so they do not wait for or block graph edits.
        request: (instruction, args) => instruction === "ApplyTemplate" && !args?.dry_run
          ? applyProgramMutation(() => executeHatgInstruction(instruction, args))
          : window.PortalInstructionClient.execute(instruction, args),
      });
    }

    return {
      mountTemplates,
    };
  }

  window.PortalHatgPanelTemplateShelf = Object.freeze({ createTemplateShelf, dependencies: DEPENDENCIES });
})();
