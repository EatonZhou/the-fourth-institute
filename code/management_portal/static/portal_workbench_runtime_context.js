(function (global) {
  "use strict";

  if (global.PortalWorkbenchRuntimeContext) return;

  // The context Panels work in: the context store and command scope of the
  // workbench, the runtime context each Panel gets (its events, selections
  // and resource opening), running commands and calling a Panel runtime's
  // lifecycle methods with the Panel's own context scope.

  const DEPENDENCIES = Object.freeze([
    "id", "options", "persist", "root", "shellState",
  ]);

  function createRuntimeContext(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkbenchRuntimeContext is missing: ${missing.join(", ")}`);
    const {
      id, options, persist, root, shellState,
    } = dependencies;

    const unavailableBindings = new Set();

    if (!global.PortalWorkbenchContext?.create) {
      throw new Error("PortalWorkbench requires PortalWorkbenchContext.");
    }
    if (!global.PortalCommandRegistry?.createScope) {
      throw new Error("PortalWorkbench requires PortalCommandRegistry.");
    }
    const ownsContextStore = !options.contextStore;
    const contextStore = options.contextStore || global.PortalWorkbenchContext.create({
      workbenchId: id,
      initial: options.context,
    });
    const commandScope = global.PortalCommandRegistry.createScope({
      id: `workbench.${id}`,
      context: () => ({
        ...contextStore.snapshot().values,
        workbench: shellState.controller,
        workbenchId: id,
      }),
    });
    const runtimeContext = Object.freeze({
      workbenchId: id,
      contextStore,
      commandScope,
      get context() { return contextStore.snapshot().values; },
      getSelection(channel) { return contextStore.get(channel); },
      setContext(channel, value, meta = {}) { return contextStore.set(channel, value, meta); },
      select(channel, value, meta = {}) { return contextStore.select(channel, value, meta); },
      subscribe(channels, listener, subscribeOptions = {}) {
        return contextStore.subscribe(channels, listener, subscribeOptions);
      },
      emit(sourcePanelId, event, detail) {
        const payload = Object.freeze({
          workbenchId: id,
          sourcePanelId: String(sourcePanelId || ""),
          event: String(event || ""),
          detail,
        });
        root.dispatchEvent(new CustomEvent("portal:workbench-panel-event", { detail: payload }));
        options.onPanelEvent?.(payload, shellState.controller);
        if (payload.event === "binding-unavailable" || payload.event === "binding-ready") {
          if (payload.event === "binding-unavailable") unavailableBindings.add(payload.sourcePanelId);
          else unavailableBindings.delete(payload.sourcePanelId);
          if (shellState.controller) persist();
        }
        if (payload.event === "persistence-change" && shellState.controller) persist();
        const selectionChannel = ({
          "inspect": "inspectionTarget",
          "inspection-change": "inspectionTarget",
          "conversation-change": "conversationTarget",
          "document-change": "documentTarget",
          "graph-selection": "graphSelection",
          "command-context": "commandContext",
        })[payload.event];
        if (selectionChannel) {
          const value = detail?.value ?? detail?.target ?? detail?.selection ?? detail;
          contextStore.select(selectionChannel, value, {
            source: `panel:${payload.sourcePanelId}`,
            reason: payload.event,
          });
        } else if (payload.event === "view-select") {
          contextStore.set("navigationView", detail?.view ?? detail, {
            source: `panel:${payload.sourcePanelId}`,
            reason: payload.event,
          });
        }
        if (payload.event === "open-resource") {
          if (!shellState.controller?.openResource) {
            throw new Error(`PortalWorkbench ${id} cannot open a resource before initialization.`);
          }
          return Promise.resolve(shellState.controller.openResource(detail)).then(result => {
            if (result?.value?.accepted === false || result?.value?.stale === true) return result;
            contextStore.select("documentTarget", detail?.value ?? detail?.target ?? detail?.selection ?? detail, {
              source: `panel:${payload.sourcePanelId}`,
              reason: payload.event,
            });
            return result;
          });
        }
        return payload;
      },
      getPanel(panelId) { return shellState.controller?.getPanel?.(panelId) || null; },
      invoke(panelId, method, ...args) { return shellState.controller?.invokePanel?.(panelId, method, ...args); },
    });
    const executeCommand = (commandId, event, source = "workbench") => commandScope
      .execute(commandId, { event, source })
      .catch(error => console.error(`Portal Workbench command failed: ${commandId}`, error));

    function callPanelLifecycle(descriptor, method, ...args) {
      const handler = descriptor?.runtime?.[method];
      if (typeof handler !== "function") return undefined;
      const scopedArgs = args.map(argument => argument === runtimeContext ? descriptor.contextScope.context : argument);
      if (method === "setContext") {
        scopedArgs[0] = descriptor.contextScope.values(scopedArgs[0]);
        if (scopedArgs[2]?.changedChannels) scopedArgs[2] = descriptor.contextScope.project(scopedArgs[2]);
      }
      try { return handler.apply(descriptor.runtime, scopedArgs); }
      catch (error) {
        console.error(`PortalWorkbench ${id} panel ${descriptor.id} ${method} failed`, error);
        return undefined;
      }
    }

    return {
      callPanelLifecycle, commandScope, contextStore, executeCommand, ownsContextStore, runtimeContext,
      unavailableBindings,
    };
  }

  global.PortalWorkbenchRuntimeContext = Object.freeze({ createRuntimeContext, dependencies: DEPENDENCIES });
})(window);
