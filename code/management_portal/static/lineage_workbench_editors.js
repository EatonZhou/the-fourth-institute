(() => {
  "use strict";

  if (window.PortalLineageWorkbenchEditors) return;

  // The workbench's editor panels (chat, HATG, Preview, terminal): what each
  // one opens and the options it is created with.
  const DEPENDENCIES = Object.freeze([
    "activateLineageChatWorkspace", "activePreviewPayload", "apiGet", "apiPost",
    "bindChatMarkdownLinks", "bindLineageChatWorkspace", "chatTargetForPanel", "completeResidentFork",
    "currentAgentPreviewSource", "currentInitialLineageReadyResolved", "currentLineageWorkbench",
    "currentResident", "deleteLineageFromConsole", "handleLineageChatPanelDetail",
    "initialLineageIdFromUrl", "initialProgramItemIdFromUrl", "initialResidentIdFromUrl",
    "lineageLayoutStore", "lineageSidebarRoots", "lineageStateController", "openResourceChipPreview",
    "openWorkspaceResource", "recommendationConversationView", "refreshLineageChatView",
    "refreshPreviewIfChanged", "registeredLineageIdForResident", "rememberResidentShortcutSession",
    "renderMarkdown", "residentIdForSelection", "retireDeletedResidentSurfaces", "runChatTargetEffect",
    "selectedResidentId", "showResidentEditorOpenMenu", "state", "syncHatgLocation",
    "updateCurrentResidentWatchboard", "waitForInitialLineageReady",
  ]);

  function createEditors(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchEditors is missing: ${missing.join(", ")}`);
    const {
      activateLineageChatWorkspace, activePreviewPayload, apiGet, apiPost, bindChatMarkdownLinks,
      bindLineageChatWorkspace, chatTargetForPanel, completeResidentFork, currentAgentPreviewSource,
      currentInitialLineageReadyResolved, currentLineageWorkbench, currentResident,
      deleteLineageFromConsole, handleLineageChatPanelDetail, initialLineageIdFromUrl,
      initialProgramItemIdFromUrl, initialResidentIdFromUrl, lineageLayoutStore, lineageSidebarRoots,
      lineageStateController, openResourceChipPreview, openWorkspaceResource,
      recommendationConversationView, refreshLineageChatView, refreshPreviewIfChanged,
      registeredLineageIdForResident, rememberResidentShortcutSession, renderMarkdown,
      residentIdForSelection, retireDeletedResidentSurfaces, runChatTargetEffect, selectedResidentId,
      showResidentEditorOpenMenu, state, syncHatgLocation, updateCurrentResidentWatchboard,
      waitForInitialLineageReady,
    } = dependencies;

    function lineageResidentWorkspaceOptions() {
        return {
          residentFeatures: { watchboard: true },
          onWatchboardAction: (action, residentId) => updateCurrentResidentWatchboard(action, residentId),
          conversationView: recommendationConversationView,
          draftOwner: () => state.me || "anonymous",
          onDetail: handleLineageChatPanelDetail,
          onAcknowledgement: (acknowledgement, context, panel) => {
            const sentTarget = context?.target || panel?.target?.() || {};
            const acknowledgedId = String(acknowledgement?.resident_id || sentTarget.resident_id || "").trim();
            const target = sentTarget.resident_id === acknowledgedId
              ? sentTarget
              : { resident_id: acknowledgedId };
            const requestTarget = chatTargetForPanel(panel, target);
            const residentId = residentIdForSelection(requestTarget);
            if (residentId) rememberResidentShortcutSession(residentId, target);
            runChatTargetEffect(requestTarget, refreshLineageChatView);
          },
          onError: err => window.PortalUI.toast(window.PortalUI.errorMessage("Resident 对话", err), "error"),
          onForkCreated: ({ lineageId, result }) => completeResidentFork(result, lineageId),
        };
    }

    // The Terminal exists on this page only for Users who may run OpenTerminal (/api/me access.allows).
    const terminalAllowed = () => window.PortalIdentity.peek()?.access?.allows?.OpenTerminal === true;
    const terminalEditorType = { type: "terminal", title: window.PortalI18n.t("终端"), icon: "terminal" };
    const lineageEditorTypes = [
      { type: "chat", title: window.PortalI18n.t("Chat Workspace"), icon: "chat" },
      { type: "hatg", title: "HATG", icon: "network" },
      { type: "preview", title: window.PortalI18n.t("预览"), icon: "window" },
      ...(terminalAllowed() ? [terminalEditorType] : []),
    ];

    function terminalEditorDescriptor() {
      return lineageEditorDescriptor({
        instanceId: "terminal",
        type: "terminal",
        resource: { ...currentLineageEditorResource("terminal"), defaultHidden: true },
      });
    }

    function currentLineageEditorResource(type) {
      const projection = window.PortalProjections?.resource(type);
      if (projection) return projection;
      const residentId = selectedResidentId() || initialResidentIdFromUrl();
      const lineageId = String(
        state.chatTarget?.lineage_id
        || initialLineageIdFromUrl()
        || ""
      ).trim();
      if (type === "chat") return { kind: "chat", resident_id: residentId, lineage_id: lineageId, title: currentResident()?.name || residentId || window.PortalI18n.t("Chat Workspace") };
      if (type === "hatg") return {
        kind: "hatg",
        lineageId,
        residentId,
        programItemId: initialProgramItemIdFromUrl(),
        title: "HATG",
      };
      if (type === "preview") {
        const document = activePreviewPayload();
        return { kind: "preview", document, title: document?.title || document?.path || window.PortalI18n.t("预览") };
      }
      if (type === "terminal") return { kind: "terminal", key: residentId || "portal-host", cwd: "", label: currentResident()?.name || window.PortalI18n.t("Portal 主机"), resident_id: residentId };
      return { kind: type };
    }

    function lineagePreviewPanelOptions(binding) {
      return {
        resource: binding,
        document: binding.document,
        reload: () => refreshPreviewIfChanged({ force: true }),
        label: window.PortalI18n.t("Lineage 资源管理器预览"),
        renderContext: {
          bindLinks: bindChatMarkdownLinks,
          onResourcePreview: openResourceChipPreview,
          rawUrl: current => currentAgentPreviewSource()?.rawUrl?.(current) || "",
          renderMarkdown: (content, markdownOptions) => renderMarkdown(content, markdownOptions),
        },
        panelIds: {
          title: "previewTitle",
          path: "previewPath",
          collapse: "previewCollapseBtn",
          browser: "agentResultBrowser",
        },
        surface: {
          explorer: false,
          ids: {
            previewPanel: "agentPreviewPanel",
            previewCollapse: "agentResultPreviewCollapseBtn",
            sectionHead: "agentPreviewSectionHead",
            tabsShell: "agentPreviewTabsShell",
            tabs: "agentPreviewTabs",
            pin: "pinAgentPreviewBtn",
            toolbar: "agentPreviewEditorToolbar",
            preview: "previewBox",
            selectionToolbar: "previewSelectionToolbar",
          },
          actionIds: {
            save: "savePreviewBtn",
            undo: "undoPreviewBtn",
            redo: "redoPreviewBtn",
            wrap: "wrapPreviewBtn",
            open: "openPreviewExternalBtn",
            download: "downloadPreviewBtn",
            refresh: "refreshPreviewBtn",
            code: "editPreviewBtn",
            visual: "visualPreviewBtn",
          },
          layoutStore: lineageLayoutStore,
        },
        onPreviewRequest: request => window.PortalPreview.request(request),
      };
    }

    function lineageEditorDescriptor({ instanceId, type, resource }) {
      const projection = window.PortalProjections?.descriptor({ instanceId, type, resource });
      if (projection) return projection;
      const binding = { ...(resource || {}), kind: type };
      if (type === "navigation") {
        return {
          id: instanceId,
          type,
          title: binding.title || window.PortalI18n.t("Lineage 导航"),
          openSource: true,
          options: { variant: "lineage", label: window.PortalI18n.t("Lineage 导航") },
        };
      }
      if (type === "chat") {
        return {
          id: instanceId,
          type,
          title: binding.title || binding.resident_id || window.PortalI18n.t("Chat Workspace"),
          layoutTitle: window.PortalI18n.t("Chat Workspace"),
          minWidth: 320,
          options: {
            target: binding,
            refsScope: "lineage",
            title: window.PortalI18n.t("Chat Workspace"),
            subtitle: window.PortalI18n.t("选择一个 Resident。"),
            onPreviewRequest: request => window.PortalPreview.request(request),
            ...lineageResidentWorkspaceOptions(),
          },
          onShow: ({ workbench, panel }) => {
            // Several Editors can become visible together during layout or
            // restoration. Visibility alone must not replace the active host
            // selection or retarget a sibling Chat after its async read.
            if (workbench.getActiveOpenTarget() !== panel?.id) return;
            const runtime = panel?.runtime;
            bindLineageChatWorkspace(runtime);
            // Initial page loading already owns its first target request.
            if (!currentInitialLineageReadyResolved()) return;
            // A hidden initial panel may not have received the page target yet.
            // Preserve an already-bound instance; otherwise use the host selection.
            const target = runtime?.target?.();
            void activateLineageChatWorkspace(runtime, target?.resident_id
              ? target
              : currentLineageEditorResource("chat"))
              .catch(error => window.PortalUI?.toast?.(error?.message || error, "error"));
          },
          openResource: (nextResource, intent, panel) => {
            return activateLineageChatWorkspace(panel, nextResource, intent);
          },
        };
      }
      if (type === "hatg") {
        return {
          id: instanceId,
          type,
          title: binding.title || "HATG",
          options: {
            lineageId: binding.lineageId,
            residentId: binding.residentId,
            programItemId: binding.programItemId,
            pageState: lineageStateController,
            waitForServiceReady: waitForInitialLineageReady,
            onLocationChange: syncHatgLocation,
            onResidentsDeleted: retireDeletedResidentSurfaces,
            onDeleteLineage: request => deleteLineageFromConsole(
              lineageSidebarRoots("", { respectFilters: false })
                .find(candidate => registeredLineageIdForResident(candidate) === request.lineage_id)
              || {
                lineage_id: request.lineage_id,
                title: request.title,
                lineage: {
                  lineage_id: request.lineage_id,
                  title: request.title,
                },
              },
            ),
            onResidentOpenMenu: showResidentEditorOpenMenu,
            onWorkspaceOpen: openWorkspaceResource,
            onPreviewRequest: request => window.PortalPreview.request(request),
            onCollapseRequest: () => currentLineageWorkbench()?.collapsePanel(instanceId),
          },
        };
      }
      if (type === "preview") {
        return {
          id: instanceId,
          type,
          title: binding.title || binding.document?.title || binding.document?.path || window.PortalI18n.t("预览"),
          singleton: true,
          options: lineagePreviewPanelOptions(binding),
        };
      }
      if (type === "terminal") {
        if (!terminalAllowed()) return null;
        return {
          id: instanceId,
          type,
          title: binding.label ? window.PortalI18n.text`终端 · ${binding.label}` : window.PortalI18n.t("终端"),
          defaultHidden: binding.defaultHidden === true,
          options: { target: binding, apiGet, apiPost },
        };
      }
      return null;
    }

    function lineageHatgWorkspace() {
      const { split, stack } = window.PortalWorkbench.model;
      return {
        id: "hatg",
        name: window.PortalI18n.t("HATG 协作"),
        layout: split("horizontal", [stack(["chat"]), stack(["hatg"])], [520, 900]),
        hidden: ["navigation", "preview"],
      };
    }

    return Object.freeze({
      terminalAllowed, terminalEditorType, lineageEditorTypes, terminalEditorDescriptor,
      currentLineageEditorResource, lineageEditorDescriptor, lineageHatgWorkspace,
    });
  }

  window.PortalLineageWorkbenchEditors = Object.freeze({ createEditors, dependencies: DEPENDENCIES });
})();
