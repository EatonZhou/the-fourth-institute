(function (global) {
  "use strict";

  if (global.PortalWorkbenchEditorOpening) return;

  // Opening resources in editor Panels through the editor open service:
  // reusing an open editor or creating one for the resource.

  const DEPENDENCIES = Object.freeze([
    "addPanel", "canFocusFixedPanel", "clone", "descriptors", "findPanel", "isLayoutLocked", "lastOpenTargetByType",
    "options", "render", "shellState", "uid",
  ]);

  function createEditorOpening(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchEditorOpening is missing: ${missing.join(", ")}`);
    const {
      addPanel, canFocusFixedPanel, clone, descriptors, findPanel, isLayoutLocked, lastOpenTargetByType, options,
      render, shellState, uid,
    } = dependencies;

    let editorOpenService = null;

    async function reusePanel(panelId, intent) {
      const descriptor = descriptors.get(String(panelId));
      if (!descriptor) throw new Error(`PortalWorkbench editor instance not found: ${panelId}`);
      if (isLayoutLocked() && !canFocusFixedPanel(descriptor.id)) {
        throw new Error("布局已固定，请先解锁布局再显示此面板。");
      }
      const resource = intent.resource || {};
      let result;
      if (descriptor.openResource) result = await descriptor.openResource(resource, intent, descriptor.runtime);
      else if (typeof descriptor.runtime?.openResource === "function") result = await descriptor.runtime.openResource(resource, intent);
      else {
        throw new Error(`PortalWorkbench editor ${descriptor.id} cannot open ${intent.type}.`);
      }
      if (result && typeof result === "object" && (result.accepted === false || result.stale === true)) {
        return result;
      }
      descriptor.binding = clone(resource);
      const nextTitle = String(options.editorTitle?.(intent.type, resource, descriptor) || resource.title || resource.label || "").trim();
      if (nextTitle) descriptor.title = nextTitle;
      shellState.activeOpenTargetPanelId = descriptor.openSource ? shellState.activeOpenTargetPanelId : descriptor.id;
      const placement = findPanel(shellState.model, descriptor.id);
      if (placement?.stack) {
        placement.stack.activePanelId = descriptor.id;
        shellState.focusedStackId = placement.stack.id;
      }
      lastOpenTargetByType.set(descriptor.type, descriptor.id);
      render("reuse-editor");
      return result;
    }

    async function createPanelForIntent(intent, placement = {}) {
      if (isLayoutLocked()) throw new Error("布局已固定，请先解锁布局再打开新面板。");
      const singleton = [...descriptors.values()].find(descriptor => (
        descriptor.singleton === true
        && descriptor.type === String(intent.type || "")
      ));
      if (singleton) {
        await reusePanel(singleton.id, intent);
        return { id: singleton.id, instanceId: singleton.id, runtime: singleton.runtime };
      }
      const instanceId = String(intent.instanceId || `${intent.type}-${Date.now().toString(36)}-${uid("editor")}`);
      const raw = options.createEditorDescriptor?.({
        instanceId,
        type: intent.type,
        // Runtime opening payloads may carry loaders, document sessions, or
        // other callable adapters.  They belong to the new panel's runtime
        // construction path and must not pass through the persistence clone.
        // The descriptor binding below remains the serializable locator that
        // is safe to store and restore.
        resource: intent.resource,
        intent,
        workbench: shellState.controller,
      });
      if (!raw) throw new Error(`PortalWorkbench cannot create editor type: ${intent.type}`);
      const descriptor = addPanel({
        ...raw,
        id: instanceId,
        type: String(raw.type || intent.type),
        binding: clone(intent.resource),
        closable: true,
        restoreOnReload: true,
      }, placement);
      return { id: descriptor.id, instanceId: descriptor.id, runtime: descriptor.runtime };
    }

    function editorOpenState() {
      return {
        activeTargetInstanceId: shellState.activeOpenTargetPanelId || lastOpenTargetByType.values().next().value || "",
        activeTargetStackId: findPanel(shellState.model, shellState.activeOpenTargetPanelId)?.stack?.id || shellState.focusedStackId,
        focusedStackId: shellState.focusedStackId,
        instances: [...descriptors.values()].map(descriptor => ({
          id: descriptor.id,
          type: descriptor.type,
          stackId: findPanel(shellState.model, descriptor.id)?.stack?.id || "",
          reusable: descriptor.reusable !== false,
          singleton: descriptor.singleton === true,
          locked: descriptor.locked === true,
          dirty: Boolean(descriptor.runtime?.snapshot?.()?.dirty),
        })),
      };
    }

    function openResource(intent = {}) {
      if (!editorOpenService) return Promise.reject(new Error("PortalWorkbench EditorOpenService is not ready."));
      return editorOpenService.open(intent);
    }

    function startEditorOpening() {
      if (!global.PortalEditorOpenService?.create) {
        throw new Error("PortalWorkbench requires PortalEditorOpenService.");
      }
      editorOpenService = global.PortalEditorOpenService.create({
        state: editorOpenState,
        reuseInstance: reusePanel,
        createInstance: createPanelForIntent,
      });
    }

    return {
      openResource, startEditorOpening,
    };
  }

  global.PortalWorkbenchEditorOpening = Object.freeze({ createEditorOpening, dependencies: DEPENDENCIES });
})(window);
