(() => {
      "use strict";

      // What the opening URL asks for, read before any panel restores its own location.
      const {
        initialResidentIdFromUrl, initialLineageIdFromUrl, initialOpenChatFromUrl,
        consumeOpenChatNavigationIntent, initialProgramItemIdFromUrl, initialChatroomIdFromUrl,
      } = workbenchModule("PortalLineageWorkbenchLocation");

      // Preserve the user's opening intent before restored panels publish their
      // own locations. An empty panel is not a new navigation request.
      const openingNavigation = {
        residentId: initialResidentIdFromUrl(),
        lineageId: initialLineageIdFromUrl(),
        chatroomId: initialChatroomIdFromUrl(),
        workspaceId: String(new URLSearchParams(window.location.search).get("workspace") || "").trim(),
        programItemId: initialProgramItemIdFromUrl(),
        view: String(new URLSearchParams(window.location.search).get("view") || "").trim(),
        openChat: initialOpenChatFromUrl(),
        messageId: String(new URLSearchParams(window.location.search).get("message") || "").trim(),
      };
      let pendingProgramItemId = openingNavigation.programItemId;
      let openingHatgNavigationApplied = false;
      let pendingWorkspaceId = openingNavigation.workspaceId;
      const $id = (id) => document.getElementById(id);
      const esc = value => window.PortalUIPrimitives.escapeHtml(value);
      const consoleStateHtml = (message, options = {}) => window.PortalUIPrimitives.asyncStateHtml({
        state: options.state || "empty",
        title: options.title || "",
        message,
        density: options.density || "compact",
        icon: options.icon,
      });
      const renderMarkdown = (s, options = {}) => window.MD?.render ? window.MD.render(s || "", options) : esc(s || "");
      const PERSONAL_AMA_UI_NAMESPACE = "lineage-workbench.personal-ama";
      const PDFJS_WORKER_SRC = "/static/vendor/pdfjs/pdf.worker.min.js";
      if (window.pdfjsLib) window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_SRC;
      const stateFactory = window.PortalLineageWorkbenchState;
      if (!stateFactory) throw new Error("PortalLineageWorkbenchState is not loaded.");
      const controllerFactory = window.PortalLineageWorkbenchController;
      if (!controllerFactory) throw new Error("PortalLineageWorkbenchController is not loaded.");
      // The workbench takes its parts from module scripts that lineage.html
      // loads before this one.
      function workbenchModule(name) {
        const module = window[name];
        if (!module) throw new Error(`${name} is not loaded.`);
        return module;
      }
      const state = stateFactory.createState();

      const {
        chatroomOverlay, chatroomDisplayTitle, renderChatroomList, applyPersonalAmaAssociation, loadChatrooms,
        applyChatroomRecords, chatroomRecord, selectChatroom, closeChatroomView, openCreateChatroomPicker,
      } = workbenchModule("PortalLineageWorkbenchChatrooms").createChatrooms({
        $id, consoleStateHtml, currentAgentChatPanel: () => agentChatPanel,
        currentLineageWorkbench: () => lineageWorkbench, esc, renderChatHeader, setSideView, state,
        // Resident sidebar is created after this part.
        knownUsers: (...args) => knownUsers(...args),
      });

      let lineageWorkbench = null;
      let agentChatWorkspace = null;
      let agentChatPanel = null;
      const chatElement = name => agentChatWorkspace?.element?.(name) || null;
      let chatSelectionContextController = null;
      let agentFilterSummary = null;
      let agentFilterDropdown = null;
      const pendingResidentDeletions = new Set();
      const lineageApi = window.PortalAPI.createClient({ cache: "no-store" });
      let resolveInitialLineageReady;
      const initialLineageReady = new Promise(resolve => {
        resolveInitialLineageReady = resolve;
      });
      let initialLineageReadyResolved = false;
      const recoveryLoadAll = async () => {
        // The recovery surface represents service availability only. Once the
        // service is ready, normal page data (including projection warm-up)
        // owns the loading state.
        societyServiceRecovery?.hide?.();
        await loadAll({
          forceCatalogs: true,
          propagateServiceUnavailable: true,
        });
        if (!initialLineageReadyResolved) {
          initialLineageReadyResolved = true;
          resolveInitialLineageReady();
        }
      };
      const societyServiceRecovery = window.PortalComponentRecovery.create({
        endpoint: "/lineage/api/service-status",
        serviceName: "SocietyService",
        unavailableCodes: ["lineage.unavailable", "lineage.busy"],
        surfaceName: window.PortalI18n.t("Society Workbench 页面"),
        host: "#lineageWorkbenchRoot",
        id: "societyServiceRecovery",
      });
      const waitForInitialLineageReady = () => initialLineageReady;
      async function initializeLineageWorkbench() {
        try {
          await recoveryLoadAll();
        } catch (error) {
          if (!societyServiceRecovery.isUnavailable(error)) throw error;
          await societyServiceRecovery.recover({}, recoveryLoadAll);
        }
      }
      const observeLineageApiFailure = error => {
        if (!societyServiceRecovery.isUnavailable(error)) return;
        void societyServiceRecovery.recover({}, recoveryLoadAll);
      };
      const apiGet = async path => {
        try {
          return await lineageApi.get(path);
        } catch (error) {
          observeLineageApiFailure(error);
          throw error;
        }
      };
      const apiPost = async (path, body) => {
        try {
          return await lineageApi.post(path, body);
        } catch (error) {
          observeLineageApiFailure(error);
          throw error;
        }
      };
      const societyService = window.PortalSocietyService.createClient({ get: apiGet });
      const residentService = window.PortalResidentService.createClient({
        api: { get: apiGet, post: apiPost },
      });
      const residentDiscovery = window.PortalResidentDiscoveryService.createClient({
        post: apiPost,
        resolveNavigationTarget: residentId => residentService.navigationTarget(residentId),
      });

      const {
        residentSelectionId, activeChatTargetId, activeLineageId, commitChatTarget, selectedResidentId,
        residentExactId, residentRootForExactId, resolveResidentNavigationTarget,
        firstAvailableResidentChatTarget, currentResident, residentChatTarget,
        residentChatTargetForLineage, unavailableResidentBindings, readLastSelection,
        writeLastSelection, currentChatTarget, chatTargetForPanel, residentIdForSelection,
        runChatTargetEffect, beginChatWorkspaceNavigation, rememberChatWorkspaceBeforeSwitch,
        previousChatWorkspaceFromInterfaceHistory, isResidentChatSelection, goPreviousChatWorkspace,
        lineageIdForResident, registeredLineageIdForResident,
      } = workbenchModule("PortalLineageWorkbenchChatTargets").createChatTargets({
        currentChatSelectionContextController: () => chatSelectionContextController,
        currentPersonalWorkspaces: () => personalWorkspaces, readLineageUiState, residentDiscovery,
        state, writeLineageUiState,
        // Selection is created after this part.
        selectResident: (...args) => selectResident(...args),
        // Resident sidebar is created after this part.
        lineageLeaderResidentId: (...args) => lineageLeaderResidentId(...args),
        lineageSidebarResidentsForItem: (...args) => lineageSidebarResidentsForItem(...args),
        lineageSidebarRootByResidentId: (...args) => lineageSidebarRootByResidentId(...args),
        lineageSidebarRoots: (...args) => lineageSidebarRoots(...args),
      });

      const {
        retireDeletedResidentSurfaces, openLineageConversationFromInbox, resetChatRenderState,
        handleResidentCardClick, selectDerivedResident, publishConversationSelection, selectResident,
        syncSelectedResidentSurface, activateLineageChatWorkspace, handleResidentMemberCardAction,
        refreshResidentSidebarIfDue,
      } = workbenchModule("PortalLineageWorkbenchSelection").createSelection({
        $id, activeChatTargetId, activeLineageId, beginChatWorkspaceNavigation,
        bindLineageChatWorkspace, closeChatroomView, commitChatTarget,
        currentAgentChatPanel: () => agentChatPanel,
        currentAgentChatWorkspace: () => agentChatWorkspace, currentChatTarget,
        currentLineageWorkbench: () => lineageWorkbench, currentResident,
        currentResidentSidebarRefreshCoordinator: () => residentSidebarRefreshCoordinator,
        currentWatchboardController: () => watchboardController, lineageHatgTargetMatches,
        lineageIdForResident, registeredLineageIdForResident, rememberChatWorkspaceBeforeSwitch,
        renderChatHeader, residentChatTarget, resolveResidentNavigationTarget, selectChatroom,
        selectedResidentId, setSideView, state, syncLineageHatgPanel, unavailableResidentBindings,
        writeLastSelection, writeLineageUiState,
        // Location is created after this part.
        clearDeletedResidentLocation: (...args) => clearDeletedResidentLocation(...args),
        syncChatTargetLocation: (...args) => syncChatTargetLocation(...args),
        // Preview is created after this part.
        activeAgentPreviewDocument: (...args) => activeAgentPreviewDocument(...args),
        activePreviewPayload: (...args) => activePreviewPayload(...args),
        currentPreviewSelectionController: (...args) => currentPreviewSelectionController(...args),
        currentPreviewTabsController: (...args) => currentPreviewTabsController(...args),
        loadFolder: (...args) => loadFolder(...args),
        refreshLineageChatView: (...args) => refreshLineageChatView(...args),
        renderPreviewActions: (...args) => renderPreviewActions(...args),
        setPreviewOpenInput: (...args) => setPreviewOpenInput(...args),
        setPreviewTitle: (...args) => setPreviewTitle(...args),
        stopPreviewRefresh: (...args) => stopPreviewRefresh(...args),
        // Chat detail is created after this part.
        closeChatExtensionMenu: (...args) => closeChatExtensionMenu(...args),
        composerDraftKey: (...args) => composerDraftKey(...args),
        loadDetail: (...args) => loadDetail(...args),
        syncResidentStatusSignatures: (...args) => syncResidentStatusSignatures(...args),
        // Resident dialogs is created after this part.
        editDerivedResidentFromSidebar: (...args) => editDerivedResidentFromSidebar(...args),
        // Card menus is created after this part.
        deleteResidentFromWorkbench: (...args) => deleteResidentFromWorkbench(...args),
        openNavigationEditor: (...args) => openNavigationEditor(...args),
        residentMemberMenuButton: (...args) => residentMemberMenuButton(...args),
        residentMemberMenuKey: (...args) => residentMemberMenuKey(...args),
        // Resident sidebar is created after this part.
        derivedResidentForSidebar: (...args) => derivedResidentForSidebar(...args),
        lineageSidebarRootByResidentId: (...args) => lineageSidebarRootByResidentId(...args),
        renderAgentList: (...args) => renderAgentList(...args),
        renderFavoriteList: (...args) => renderFavoriteList(...args),
        syncResidentNavigationSelection: (...args) => syncResidentNavigationSelection(...args),
      });

      const {
        applyWatchboardLayout, syncNavigationPanelControl, setNavigationPanelVisible,
        syncPreviewPanelControl, setPreviewPanelVisible, ensurePreviewVisible, bindWatchboardSplitter,
      } = workbenchModule("PortalLineageWorkbenchLayout").createLayout({
        $id, chatElement, currentLineageLayoutStore: () => lineageLayoutStore,
        currentLineageWorkbench: () => lineageWorkbench, state,
      });

      const {
        whichResidentCardHtml, recommendationConversationView, refreshRecommendationChats,
        restoreLatestWhichResident, bindWhichResidentControls, restoreWhichResidentState,
      } = workbenchModule("PortalLineageWorkbenchWhichResident").createWhichResident({
        appendChatWorkspaceDraft, currentLineageWorkbench: () => lineageWorkbench, esc,
        registeredLineageIdForResident, residentDiscovery, selectResident, state, writeLineageUiState,
        writeLineageUiStateSoon,
        // Resident dialogs is created after this part.
        openResidentForkCreateModal: (...args) => openResidentForkCreateModal(...args),
        // Resident sidebar is created after this part.
        lineageSidebarRootByResidentId: (...args) => lineageSidebarRootByResidentId(...args),
        renderAgentList: (...args) => renderAgentList(...args),
        renderRegisteredResidentItem: (...args) => renderRegisteredResidentItem(...args),
        residentSidebarPendingText: (...args) => residentSidebarPendingText(...args),
      });

      const lineageModules = window.PortalLineageWorkbenchModules;
      if (!lineageModules?.createWatchboard) {
        throw new Error("Society 工作台模块加载失败");
      }
      let terminalController = null;
      const watchboardController = lineageModules.createWatchboard({
        state,
        getChatElement: chatElement,
        getElementById: $id,
        escapeHtml: esc,
        portalUi: window.PortalUI,
        applyLayout: applyWatchboardLayout,
        bindSplitter: bindWatchboardSplitter,
        currentResident,
        residentService,
        selectedResidentId,
        submitChatPrompt: runSlashCommand,
        writeUiState: writeLineageUiState,
        // Preview is created after this part.
        loadFolder: (...args) => loadFolder(...args),
      });

      function appendChatWorkspaceDraft(text) {
        const draft = String(text || "").trim();
        if (!draft || !agentChatWorkspace) return false;
        const existing = String(agentChatWorkspace.draftText?.() || "").trim();
        const alreadyIncluded = existing === draft || existing.endsWith(`\n\n${draft}`);
        const nextDraft = existing && !alreadyIncluded ? `${existing}\n\n${draft}` : existing || draft;
        agentChatWorkspace.setDraftText(nextDraft);
        window.requestAnimationFrame(() => agentChatWorkspace.focusComposer?.());
        return true;
      }

      const {
        syncHatgLocation, syncChatTargetLocation, clearDeletedLineageLocation,
        clearDeletedResidentLocation,
      } = workbenchModule("PortalLineageWorkbenchLocation").createLocation({
        openingNavigation, registeredLineageIdForResident, selectedResidentId, state,
      });

      const {
        activePreviewPayload, loadLocalLinkPreview, openWorkspaceResource, openResourceChipPreview,
        bindChatMarkdownLinks, bindWorkspacePreviewHost, syncLineageChatWorkspaceProjection,
        refreshLineageChatView, setPreviewTitle, loadFolder, activeAgentPreviewDocument, connectPreviewSurface,
        initPreviewController, previewRefreshCoordinator, stopPreviewRefresh, schedulePreviewRefresh,
        refreshPreviewIfChanged, previewChangeMatches, setPreviewOpenInput, togglePreviewWordWrap,
        openPreviewInNewTab, renderPreviewActions, setPreviewMode, runPreviewEditorCommand, savePreviewEdit,
        currentPreviewTabsController, currentPreviewSelectionController, currentAgentPreviewSource,
        currentAgentPreviewPanelComponent,
      } = workbenchModule("PortalLineageWorkbenchPreview").createPreview({
        $id, apiGet, apiPost, currentAgentChatPanel: () => agentChatPanel,
        currentAgentChatWorkspace: () => agentChatWorkspace, currentLineageWorkbench: () => lineageWorkbench,
        currentPersonalWorkspaces: () => personalWorkspaces, currentResident, ensurePreviewVisible,
        lineageIdForResident, residentService, selectedResidentId, setSideView, showPersonalWorkspaces, state,
        unavailableResidentBindings,
      });

      const {
        closeChatExtensionMenu, renderChatExtensionMenu, updateCurrentResidentWatchboard,
        workspaceMessageByKey, syncResidentStatusSignatures, composerDraftKey, messageRole,
        handleLineageChatPanelDetail, loadDetail,
      } = workbenchModule("PortalLineageWorkbenchChatDetail").createChatDetail({
        activeLineageId, commitChatTarget, currentAgentChatPanel: () => agentChatPanel,
        currentAgentChatWorkspace: () => agentChatWorkspace, currentChatTarget,
        currentLineageWorkbench: () => lineageWorkbench, currentResident,
        currentWatchboardController: () => watchboardController, loadFolder,
        publishConversationSelection, refreshLineageChatView, refreshResidentSidebarIfDue,
        renderChatHeader, residentChatTarget, residentIdForSelection, residentService,
        selectedResidentId, state, syncChatTargetLocation, syncLineageChatWorkspaceProjection,
        syncLineageHatgPanel, unavailableResidentBindings, writeLastSelection, writeLineageUiState,
      });

      function onLineagePanelBinding({ event, sourcePanelId }, workbench) {
        if (event !== "binding-unavailable" && event !== "binding-ready") return;
        const residentId = workbench?.getPanel(sourcePanelId)?.target?.()?.resident_id;
        if (!residentId) return;
        if (event === "binding-ready") {
          unavailableResidentBindings.delete(residentId);
          return;
        }
        unavailableResidentBindings.add(residentId);
        // A stale Resident URL must not keep requesting the unavailable Chat
        // on every reload. Its explicit Lineage remains a separate binding.
        const location = new URL(window.location.href);
        if (location.searchParams.get("resident") === residentId) {
          location.searchParams.delete("resident");
          location.searchParams.delete("open_chat");
          history.replaceState(history.state || {}, "", `${location.pathname}${location.search}${location.hash}`);
        }
        const saved = readLineageUiState().lastChatTarget;
        if (saved?.kind === "resident" && saved.id === residentId) {
          writeLineageUiState({ lastChatTarget: null });
        }
      }

      function bindPreviousChatWorkspaceNavigation() {
        const previousTarget = () => isResidentChatSelection(state.previousChatWorkspace)
            ? state.previousChatWorkspace
            : previousChatWorkspaceFromInterfaceHistory();
        const unregister = window.PortalState.interfaceHistory.registerNavigator({
          canGoBack: () => isResidentChatSelection(previousTarget()),
          goBack: () => goPreviousChatWorkspace(previousTarget()),
        });
        lineageStateController.listen("page", window, "pagehide", event => {
          if (!event.persisted) unregister();
        }, true);
      }

      const lineageStateController = controllerFactory.createController({
        state,
        user: () => state.me || "anonymous",
      });
      function readLineageUiState() {
        return lineageStateController.read();
      }

      function writeLineageUiState(patch) {
        lineageStateController.write(patch);
      }

      function writeLineageUiStateSoon(patch) {
        lineageStateController.writeSoon(patch);
      }

      const lineageLayoutStore = window.PortalWorkbench.layout.createStore({
        read: () => readLineageUiState().layout || {},
        write: layout => writeLineageUiStateSoon({ layout }),
      });
      function applyLineageUiState({ preserveSideView = false } = {}) {
        const saved = readLineageUiState();
        restoreWhichResidentState(saved.whichResident);
        const savedSideView = window.PortalNavigation.normalizeLineageView(saved.lastSideView || saved.sideView);
        if (!preserveSideView) state.sideView = savedSideView;
        if (typeof saved.watchboardCollapsed === "boolean") state.watchboardCollapsed = saved.watchboardCollapsed;
        if (Array.isArray(saved.watchboardHiddenResidents)) {
          state.watchboardHiddenResidents = new Set(saved.watchboardHiddenResidents.map(value => String(value || "").trim()).filter(Boolean));
        }
        if (saved.layout && typeof saved.layout === "object" && !Array.isArray(saved.layout)) {
          state.layout.watchboard = Number(saved.layout.watchboard || state.layout.watchboard);
        }
        currentAgentPreviewPanelComponent()?.applyLayout?.(
          saved.layout && typeof saved.layout === "object" && !Array.isArray(saved.layout)
            ? saved.layout
            : {},
        );
        if (typeof saved.query === "string") state.query = saved.query;
        if (typeof saved.searchOpen === "boolean") state.searchOpen = saved.searchOpen;
        if (typeof saved.projectFilter === "string") state.projectFilter = saved.projectFilter || "all";
        state.projectDisplayFilter = normalizedProjectDisplayFilter(saved.projectDisplayFilter || state.projectDisplayFilter);
        if (saved.favoriteGroups && typeof saved.favoriteGroups === "object" && !Array.isArray(saved.favoriteGroups)) {
          state.favoriteGroups = { ...state.favoriteGroups, ...saved.favoriteGroups };
        }
        if (saved.favoriteProjectGroups && typeof saved.favoriteProjectGroups === "object" && !Array.isArray(saved.favoriteProjectGroups)) {
          state.favoriteProjectGroups = saved.favoriteProjectGroups;
        }
        if (saved.residentGroups && typeof saved.residentGroups === "object" && !Array.isArray(saved.residentGroups)) {
          state.residentGroups = { ...state.residentGroups, ...saved.residentGroups };
        }
        if (saved.residentProjectGroups && typeof saved.residentProjectGroups === "object" && !Array.isArray(saved.residentProjectGroups)) {
          state.residentProjectGroups = saved.residentProjectGroups;
        }
      }

      function bindLineageChatWorkspace(panel) {
        if (!panel || agentChatPanel === panel) return;
        chatSelectionContextController?.destroy?.();
        chatSelectionContextController = null;
        agentChatPanel = panel;
        agentChatWorkspace = agentChatPanel.workspace;
        bindWatchboardSplitter();
        requestAnimationFrame(() => applyWatchboardLayout());
      }

      function renderChatHeader(resident = null) {
        agentChatPanel?.syncActions?.();
        renderChatExtensionMenu(state.detail || {});
        const selectedResident = resident || currentResident();
        const visibleRoom = chatroomRecord(state.activeChatroomId) || chatroomOverlay.view?.room;
        lineageWorkbench?.setPanelTitle?.(
          agentChatPanel?.instanceId?.() || "chat",
          state.activeChatroomId
            ? (visibleRoom?.chatroom_id === state.activeChatroomId ? chatroomDisplayTitle(visibleRoom) : "正在读取聊天室")
            : selectedResident?.title || selectedResident?.name || selectedResidentId() || "Chat Workspace",
        );
      }

      const {
        syncResidentEffortSelect, syncResidentBackendRuntimeFields, refreshOpenResidentRegistrationOptions,
        restoreResidentRegisterForm, resetResidentAttachOptionState, residentAttachChoiceItem,
        renderResidentAttachChoiceModal, openLineageAttachChoiceModal, closeResidentAttachChoiceModal,
        openAttachResidentModal, openResidentEditModal, closeResidentRegisterModal, submitResidentRegister,
        renderResidentForkAccount, openResidentForkCreateModal, closeResidentForkCreateModal,
        handleForkCreateAction, completeResidentFork, closeResidentEditModal, editDerivedResidentFromSidebar,
        submitResidentEditSurface, renderCrossLineageForkModal, loadCrossLineageForkNodes,
        closeCrossLineageForkModal, submitCrossLineageFork,
      } = workbenchModule("PortalLineageWorkbenchResidentDialogs").createResidentDialogs({
        $id, activeChatTargetId, activeLineageId, appendChatWorkspaceDraft, beginChatWorkspaceNavigation,
        chatTargetForPanel, commitChatTarget, currentAgentChatWorkspace: () => agentChatWorkspace,
        currentLineageWorkbench: () => lineageWorkbench, esc, lineageIdForResident, loadDetail, loadFolder,
        messageRole, refreshResidentSidebarIfDue, registeredLineageIdForResident, residentChatTarget,
        residentDiscovery, residentIdForSelection, residentSelectionId, residentService, selectResident,
        societyService, state, syncSelectedResidentSurface, workspaceMessageByKey, writeLastSelection,
        // Resident sidebar is created after this part.
        beginResidentSidebarMutation: (...args) => beginResidentSidebarMutation(...args),
        derivedResidentForSidebar: (...args) => derivedResidentForSidebar(...args),
        endResidentSidebarMutation: (...args) => endResidentSidebarMutation(...args),
        lineageLeaderResidentId: (...args) => lineageLeaderResidentId(...args),
        lineageSidebarRoots: (...args) => lineageSidebarRoots(...args),
        renderAgentList: (...args) => renderAgentList(...args),
        renderFavoriteList: (...args) => renderFavoriteList(...args),
        revealResidentSidebarTarget: (...args) => revealResidentSidebarTarget(...args),
        // Recent Residents is created after this part.
        rememberResidentShortcutSession: (...args) => rememberResidentShortcutSession(...args),
      });

      const {
        residentMemberMenuKey, bindAgentCardControls, residentMemberMenuButton, openNavigationEditor,
        showResidentEditorOpenMenu, renderAgentFloatingMenu, deleteResidentFromWorkbench,
        deleteLineageFromConsole, favoriteSelectionForObject, handleAgentCardAction,
        currentAgentFloatingMenuOverlay, currentLineageEditorOpenCommands,
      } = workbenchModule("PortalLineageWorkbenchCardMenus").createCardMenus({
        $id, activeChatTargetId, beginChatWorkspaceNavigation, clearDeletedLineageLocation,
        clearDeletedResidentLocation, commitChatTarget, currentAgentChatPanel: () => agentChatPanel,
        currentChatTarget, currentLineageWorkbench: () => lineageWorkbench, esc,
        firstAvailableResidentChatTarget, handleResidentCardClick, handleResidentMemberCardAction,
        lineageIdForResident, loadDetail, loadFolder, openLineageAttachChoiceModal, openResidentEditModal,
        pendingResidentDeletions, refreshResidentSidebarIfDue, registeredLineageIdForResident,
        resetChatRenderState, residentChatTarget, residentExactId, residentService,
        retireDeletedResidentSurfaces, selectDerivedResident, selectedResidentId, selectResident,
        societyService, state, syncChatTargetLocation, syncLineageHatgPanel, syncSelectedResidentSurface,
        unavailableResidentBindings, writeLastSelection, writeLineageUiState,
        // Resident sidebar is created after this part.
        derivedResidentForSidebar: (...args) => derivedResidentForSidebar(...args),
        isFavoriteAgent: (...args) => isFavoriteAgent(...args),
        isLineageLeaderResident: (...args) => isLineageLeaderResident(...args),
        isPersonalAmaResident: (...args) => isPersonalAmaResident(...args),
        lineageContainsPersonalAmaLeader: (...args) => lineageContainsPersonalAmaLeader(...args),
        lineageLeaderResidentId: (...args) => lineageLeaderResidentId(...args),
        lineageSidebarResidentsForItem: (...args) => lineageSidebarResidentsForItem(...args),
        menuTargetById: (...args) => menuTargetById(...args),
        renderAgentList: (...args) => renderAgentList(...args),
        renderFavoriteList: (...args) => renderFavoriteList(...args),
        // Recent Residents is created after this part.
        dismissRecentAgentSession: (...args) => dismissRecentAgentSession(...args),
        recentAgentSessionKey: (...args) => recentAgentSessionKey(...args),
      });

      const {
        residentSidebarRefreshCoordinator, fmtDateTime, updateProjectOptions, lineageLeaderResidentId,
        isLineageLeaderResident, lineageSidebarRoots, lineageSidebarRootByResidentId, projectFilterOptions,
        normalizedProjectDisplayFilter, syncAgentTools, setSearchOpen, setProjectFilter,
        setProjectDisplayFilter, residentSidebarPendingText, isPersonalAmaResident,
        lineageContainsPersonalAmaLeader, fetchResidentSidebarSnapshot, applyResidentSidebarShortcuts,
        waitForResidentSidebarProjection, beginResidentSidebarMutation, endResidentSidebarMutation,
        renderRegisteredResidentItem, derivedResidentForSidebar, lineageSidebarResidentsForItem, knownUsers,
        refreshPersonalAssistantShortcut, renderAgentList, syncResidentNavigationSelection, renderFavoriteList,
        menuTargetById, isFavoriteAgent, revealResidentSidebarTarget, focusPreferredSidebarTarget,
      } = workbenchModule("PortalLineageWorkbenchSidebar").createSidebar({
        $id, activeChatTargetId, activeLineageId, bindAgentCardControls, bindWhichResidentControls,
        consoleStateHtml, currentAgentChatPanel: () => agentChatPanel,
        currentAgentFilterDropdown: () => agentFilterDropdown,
        currentAgentFilterSummary: () => agentFilterSummary, esc, lineageIdForResident,
        refreshOpenResidentRegistrationOptions, refreshRecommendationChats, refreshResidentSidebarIfDue,
        renderAgentFloatingMenu, residentExactId, residentSelectionId, residentService, selectedResidentId,
        setSideView, state, whichResidentCardHtml, writeLineageUiState,
        // Recent Residents is created after this part.
        recentTimeMs: (...args) => recentTimeMs(...args),
        renderActivityRecentRail: (...args) => renderActivityRecentRail(...args),
      });

      const {
        recentAgentSessionKey, dismissRecentAgentSession, rememberResidentShortcutSession, recentTimeMs,
        initLineageActivityRail, renderActivityRecentRail, currentLineageActivityRail,
      } = workbenchModule("PortalLineageWorkbenchRecent").createRecent({
        fmtDateTime, focusPreferredSidebarTarget, registeredLineageIdForResident, residentExactId,
        residentRootForExactId, selectedResidentId, selectResident, setSideView, state,
      });

      const {
        terminalAllowed, terminalEditorType, lineageEditorTypes, terminalEditorDescriptor,
        currentLineageEditorResource, lineageEditorDescriptor, lineageHatgWorkspace,
      } = workbenchModule("PortalLineageWorkbenchEditors").createEditors({
        activateLineageChatWorkspace, activePreviewPayload, apiGet, apiPost, bindChatMarkdownLinks,
        bindLineageChatWorkspace, chatTargetForPanel, completeResidentFork, currentAgentPreviewSource,
        currentInitialLineageReadyResolved: () => initialLineageReadyResolved,
        currentLineageWorkbench: () => lineageWorkbench, currentResident, deleteLineageFromConsole,
        handleLineageChatPanelDetail, initialLineageIdFromUrl, initialProgramItemIdFromUrl,
        initialResidentIdFromUrl, lineageLayoutStore, lineageSidebarRoots, lineageStateController,
        openResourceChipPreview, openWorkspaceResource, recommendationConversationView,
        refreshLineageChatView, refreshPreviewIfChanged, registeredLineageIdForResident,
        rememberResidentShortcutSession, renderMarkdown, residentIdForSelection,
        retireDeletedResidentSurfaces, runChatTargetEffect, selectedResidentId,
        showResidentEditorOpenMenu, state, syncHatgLocation, updateCurrentResidentWatchboard,
        waitForInitialLineageReady,
      });

      async function runSlashCommand(command) {
        const content = String(command || "").trim();
        if (!content || !currentResident() || !agentChatPanel) return;
        try {
          await agentChatPanel.send({
            text: content,
            plainText: content,
            refs: [],
            attachments: [],
          }, {
            clear: false,
            focus: false,
            rememberHistory: false,
          });
        } catch (err) {
          window.PortalUI.toast(window.PortalI18n.text`指令执行失败：${err.message || err}`, "error");
        }
      }

      async function loadAll(options = {}) {
        const loadSeq = ++state.loadAllSeq;
        const initialChatTarget = state.chatTarget;
        state.loadAllComplete = false;
        const sideViewInteractionSeq = state.sideViewInteractionSeq;
        $id("agentList").innerHTML = consoleStateHtml("加载 Residents…", { state: "loading" });
        if (!state.residents.length) {
          $id("favoriteAgentList").innerHTML = consoleStateHtml("加载收藏…", { state: "loading" });
        }
        const catalogOptions = { force: options.forceCatalogs === true };
        // Initial consumers share one catalog request. Explicit refreshes
        // bypass the short-lived value while retaining in-flight coalescing.
        const residentRequest = fetchResidentSidebarSnapshot(catalogOptions).then(
          data => ({ data, error: null }),
          error => {
            observeLineageApiFailure(error);
            return { data: null, error };
          },
        );
        const shortcutRequest = apiGet("/api/agent-workbench/residents/shortcuts")
          .catch(() => null);
        void refreshPersonalAssistantShortcut();
        // These catalogs do not depend on /api/me. Start them after the visible
        // Resident projection so identity latency is not added to every other
        // request on the Chat Workspace critical path.
        const secondaryRequests = [
          window.PortalLineageCatalogs.projectCatalog(catalogOptions).catch(() => ({ projects: [] })),
          window.PortalIdentity.listUIAssociations(PERSONAL_AMA_UI_NAMESPACE)
            .catch(() => ({ items: [] })),
          apiGet("/api/agent-workbench/favorites").then(
            data => ({ data, error: null }),
            error => ({ data: null, error }),
          ),
          window.PortalLineageCatalogs.users(catalogOptions).catch(() => []),
          window.PortalLineageCatalogs.agentBackends(catalogOptions).catch(() => null),
        ];
        const targetResidentId = loadSeq === 1 ? openingNavigation.residentId : initialResidentIdFromUrl();
        const targetLineageId = loadSeq === 1 ? openingNavigation.lineageId : initialLineageIdFromUrl();
        const initialResidentNavigationRequest = targetResidentId
          ? resolveResidentNavigationTarget(
              residentChatTarget(targetResidentId, targetLineageId),
            ).then(target => ({ target }), error => ({ error }))
          : null;
        let openingConversation = null;
        try {
          const me = await window.PortalIdentity.load().catch(() => ({ username: state.me }));
          if (loadSeq !== state.loadAllSeq) return;
          state.me = me.username || "";
          ensureLineageWorkbench();
          applyLineageUiState({
            preserveSideView: state.sideViewInteractionSeq !== sideViewInteractionSeq,
          });
          // A recovery load can supersede load #1 before identity is ready.
          // Consume the opening intent at the first live Workbench, once.
          void Promise.resolve(applyOpeningHatgNavigation()).catch(error => {
            window.PortalUI.toast(window.PortalI18n.text`打开 HATG 失败：${error.message || error}`, "error");
          });
          const preserveHiddenOpeningChat = !targetResidentId && !openingNavigation.openChat
            && performance.getEntriesByType("navigation")[0]?.type === "reload"
            && lineageWorkbench.getState().hidden.includes("chat");
          if (loadSeq === 1 && targetResidentId && !preserveHiddenOpeningChat) {
            // The exact Resident is already known. Its authorized reads can
            // overlap navigation resolution; only selectResident mounts it
            // after that resolution succeeds. The shared stores coalesce the
            // later panel reads, without another cache or a visibility shortcut.
            // Keep this subscription until the panel takes ownership: releasing
            // the last subscriber deliberately discards the message snapshot.
            openingConversation = window.PortalResidentProtocol.acquire(
              targetResidentId, () => {}, { load: false },
            );
            void window.PortalChatWorkspace.loadResidentConversation({
              resident_id: targetResidentId,
            }).catch(() => {});
          }
          await restoreLatestWhichResident().catch(() => {});

          if (usernameLabel) usernameLabel.textContent = state.me || "";
          const lastSelection = readLastSelection();
          const initialNavigation = await initialResidentNavigationRequest;
          if (initialNavigation?.error) throw initialNavigation.error;
          if (loadSeq !== state.loadAllSeq) return;
          let targetResidentSelection = initialNavigation?.target || null;
          // With no explicit Resident URL, reload keeps the active Editor's
          // binding. An explicit URL takes precedence over saved selection.
          const restoredTarget = agentChatPanel?.target?.();
          if (loadSeq === 1 && !targetResidentId && !openingNavigation.openChat && !openingNavigation.messageId
            && performance.getEntriesByType("navigation")[0]?.type === "reload"
            && restoredTarget?.resident_id) {
            targetResidentSelection = residentChatTarget(
              restoredTarget.resident_id, restoredTarget.lineage_id, restoredTarget,
            );
          }
          const mayInitializeWorkspace = state.chatTarget === initialChatTarget
            && !state.chatTarget;
          const openExplicitResidentConversation = loadSeq === 1
            && Boolean(openingNavigation.residentId)
            && Boolean(targetResidentId);
          const restoreHiddenChat = !openExplicitResidentConversation
            && performance.getEntriesByType("navigation")[0]?.type === "reload"
            && lineageWorkbench.getState().hidden.includes("chat");
          let initialResidentWorkspaceRequest = null;
          if (openExplicitResidentConversation) {
            lineageWorkbench.focusPanel("chat");
            bindLineageChatWorkspace(lineageWorkbench.getPanel("chat"));
          }
          if (
            targetResidentSelection
            && !restoreHiddenChat
            && (mayInitializeWorkspace || openExplicitResidentConversation)
          ) {
            initialResidentWorkspaceRequest = selectResident(targetResidentSelection.id, {
              lineageId: targetResidentSelection.lineage_id,
              forceView: true,
            });
          }
          if (openExplicitResidentConversation) consumeOpenChatNavigationIntent();
          const residentResult = await residentRequest;
          if (residentResult.error) throw residentResult.error;
          const residentData = residentResult.data;
          if (loadSeq !== state.loadAllSeq) return;
          residentSidebarRefreshCoordinator.start();
          if (!await waitForResidentSidebarProjection(residentData, loadSeq)) return;
          renderAgentList();
          renderActivityRecentRail();
          void shortcutRequest.then(data => {
            if (!data || loadSeq !== state.loadAllSeq) return;
            applyResidentSidebarShortcuts(data);
            applyPersonalAmaAssociation(state.personalAmaAssociations || {});
          });

          if (!targetResidentSelection && !targetResidentId) {
            targetResidentSelection = residentChatTargetForLineage(targetLineageId);
          }
          if (targetResidentSelection && !targetResidentId) {
            targetResidentSelection = await resolveResidentNavigationTarget(targetResidentSelection);
          }
          if (mayInitializeWorkspace && targetResidentSelection) {
            if (!state.chatTarget) {
              commitChatTarget(targetResidentSelection);
            }
          }

          const [projectData, personalAmaData, favoriteResult, users, backendCatalog] = await Promise.all(secondaryRequests);
          if (loadSeq !== state.loadAllSeq) return;
          state.agentBackendCatalog = backendCatalog;
          window.PortalLineageCatalogs.populateBackends($id("residentBackendSelect"), backendCatalog);
          syncResidentBackendRuntimeFields();
          state.registryProjects = Array.isArray(projectData.projects) ? projectData.projects : [];
          updateProjectOptions();
          applyPersonalAmaAssociation(personalAmaData);
          state.personalAmaAssociations = personalAmaData;
          state.favoriteLoadError = favoriteResult?.error
            ? String(favoriteResult.error?.message || favoriteResult.error)
            : "";
          state.favoriteAgentIds = new Set(
            (Array.isArray(favoriteResult?.data?.favorites) ? favoriteResult.data.favorites : [])
              .map(item => String(item || ""))
              .filter(Boolean),
          );
          state.favoriteStatuses = favoriteResult?.data?.favorite_statuses || {};
          state.users = users || [];
          renderAgentList();
          renderActivityRecentRail();
          const hasExplicitTarget = Boolean(targetResidentId || targetLineageId || openingNavigation.workspaceId);
          if (mayInitializeWorkspace) {
            if (!hasExplicitTarget && !state.chatTarget && isResidentChatSelection(lastSelection)
              && !unavailableResidentBindings.has(lastSelection.id)) {
              commitChatTarget(lastSelection);
            }
            if (!hasExplicitTarget && !state.chatTarget) {
              commitChatTarget(firstAvailableResidentChatTarget());
            }
            const lineageOnlyTarget = !targetResidentId && targetLineageId
              ? lineageSidebarRoots("", { respectFilters: false }).find(item => registeredLineageIdForResident(item) === targetLineageId)
              : null;
            if (lineageOnlyTarget && !state.chatTarget) {
              state.chatNavigationError = "";
              void openNavigationEditor({ type: "lineage", item: lineageOnlyTarget }, "reuse")
                .catch(error => window.PortalUI.toast(window.PortalI18n.text`打开 HATG 失败：${error.message || error}`, "error"));
            } else if ((targetResidentId || targetLineageId) && !state.chatTarget) {
              state.chatNavigationError = targetResidentId
                ? "指定的 Resident 不存在或无权访问。"
                : "指定的 Lineage 不存在或无权访问。";
            }
          }
          if (state.chatTarget) writeLastSelection();
          syncLineageHatgPanel({
            revealInGraph: Boolean(
              targetResidentId && targetResidentSelection
              && activeChatTargetId() === targetResidentSelection.key
            ),
          });
          $id("searchInput").value = state.query || "";
          renderFavoriteList();
          renderChatroomList();
          if (state.chatTarget) {
            syncSelectedResidentSurface();
            if (
              initialResidentWorkspaceRequest
              && targetResidentSelection
              && activeChatTargetId() === targetResidentSelection.key
            ) {
              await initialResidentWorkspaceRequest;
            } else {
              // Restored panels and opening prefetch already own their initial
              // load. Only an explicit subsequent refresh needs a fresh snapshot.
              await Promise.allSettled([loadDetail(loadSeq > 1), loadFolder()]);
            }
          } else {
            syncSelectedResidentSurface();
          }
          if (loadSeq === 1 && openingNavigation.messageId
            && targetResidentId && state.chatTarget?.id === targetResidentId) {
            await agentChatPanel?.workspace?.revealMessage(openingNavigation.messageId);
          }
          const targetChatroomId = loadSeq === 1 ? openingNavigation.chatroomId : initialChatroomIdFromUrl();
          if (mayInitializeWorkspace && targetChatroomId) state.sideView = "chatrooms";
          setSideView(state.sideView || "agents");
          if (mayInitializeWorkspace && targetChatroomId) {
            selectChatroom(targetChatroomId).catch(error => {
              closeChatroomView();
              window.PortalUI.toast(window.PortalI18n.text`打开聊天室失败：${error.message || error}`, "error");
            });
          }

          if (
            mayInitializeWorkspace
            &&
            targetResidentSelection
            && activeChatTargetId() === targetResidentSelection.key
            && state.sideViewInteractionSeq === sideViewInteractionSeq
            && state.sideView !== "workspaces"
          ) {
            focusPreferredSidebarTarget({
              residentId: targetResidentSelection.id,
              preserveQuery: true,
            });
          }
          if (loadSeq === state.loadAllSeq && pendingWorkspaceId) {
            const workspaceId = pendingWorkspaceId;
            const isCurrent = () => loadSeq === state.loadAllSeq && pendingWorkspaceId === workspaceId;
            try {
              if (await openWorkspaceResource(workspaceId, { isCurrent })) {
                pendingWorkspaceId = "";
                history.replaceState(history.state || {}, "", `/lineage?workspace=${encodeURIComponent(workspaceId)}`);
              }
            } catch (error) {
              if (isCurrent()) window.PortalUI.toast(window.PortalI18n.text`打开 Workspace 失败：${error.message || error}`, "error");
            }
          }
          if (loadSeq !== state.loadAllSeq) return;
          state.loadAllComplete = true;
        } catch (err) {
          if (loadSeq !== state.loadAllSeq) return;
          if (societyServiceRecovery.isUnavailable(err)) {
            if (options.propagateServiceUnavailable) throw err;
            void societyServiceRecovery.recover({}, recoveryLoadAll);
            return;
          }
          const message = window.PortalI18n.text`加载失败：${err.message || err}`;
          state.loadAllComplete = true;
          residentSidebarRefreshCoordinator.start();
          $id("agentList").innerHTML = consoleStateHtml(message, { state: "error" });
          agentChatPanel?.showError?.(message, {
            userName: state.me || "用户",
            assistantName: "Agent",
            placeholder: window.PortalI18n.t("加载失败"),
            meta: "等待 Resident sidebar 恢复…",
          });
        } finally {
          openingConversation?.destroy();
        }
      }

      function syncLineageHatgPanel(target = {}) {
        const panel = lineageWorkbench.getPanel("hatg");
        // The graph can be explicitly opened while Chat stays on another
        // Lineage. Catalog/service refresh preserves that graph's location;
        // a new Chat selection supplies its own explicit target below.
        const lineageId = String(
          target.lineageId
          || initialLineageIdFromUrl()
          || state.chatTarget?.lineage_id
          || ""
        ).trim();
        const residentId = String(
          target.residentId
          || (state.chatTarget?.lineage_id === lineageId ? selectedResidentId() : "")
          || ""
        ).trim();
        if (lineageId && state.lineageDeleteSaving === lineageId) return undefined;
        window.PortalProjections?.lineage(lineageWorkbench, lineageEditorTypes, lineageId);
        if (!panel || (residentId && !lineageId)) return undefined;
        const panelTarget = {
          lineageId,
          residentId,
        };
        const programItemId = lineageId === openingNavigation.lineageId
          ? pendingProgramItemId
          : "";
        if (programItemId) {
          panelTarget.programItemId = programItemId;
        }
        if (target.revealInGraph === true) panelTarget.revealInGraph = true;
        const applied = panel.setTarget?.(panelTarget);
        if (!programItemId) return applied;
        return Promise.resolve(applied).then(result => {
          if (
            pendingProgramItemId === programItemId
            && String(panel.snapshot?.()?.programItemId || "") === programItemId
          ) {
            pendingProgramItemId = "";
          }
          return result;
        });
      }

      function lineageHatgTargetMatches(chatTarget) {
        if (!lineageWorkbench?.isPanelVisible("hatg")) return true;
        const panel = lineageWorkbench.getPanel("hatg");
        if (!panel) return false;
        const snapshot = panel.snapshot?.() || {};
        const lineageId = String(snapshot.lineageId || "").trim();
        const residentId = String(snapshot.residentId || "").trim();
        return lineageId === String(chatTarget?.lineage_id || "").trim()
          && residentId === String(chatTarget?.id || "").trim();
      }

      // /api/me usually answers after the workbench is built; a permitted Terminal then joins the hidden rail.
      function applyTerminalAccess() {
        if (!lineageWorkbench || !terminalAllowed() || lineageWorkbench.descriptors.has("terminal")) return;
        if (!lineageEditorTypes.includes(terminalEditorType)) lineageEditorTypes.push(terminalEditorType);
        lineageWorkbench.setEditorTypes(lineageEditorTypes);
        lineageWorkbench.addPanel(terminalEditorDescriptor(), { hidden: true });
        terminalController = lineageWorkbench.getPanel("terminal") || null;
      }

      function initLineageWorkbench() {
        const { split, stack } = window.PortalWorkbench.model;
        const openingBindings = {
          ...(openingNavigation.residentId ? { chat: {
            resident_id: openingNavigation.residentId, lineage_id: openingNavigation.lineageId,
          } } : {}),
          ...(openingNavigation.lineageId ? { hatg: {
            lineageId: openingNavigation.lineageId, residentId: openingNavigation.residentId,
            programItemId: openingNavigation.programItemId,
          } } : {}),
        };
        const consoleLayout = split("horizontal", [
          stack(["navigation"]),
          stack(["chat"]),
          stack(["preview"]),
        ], [310, 720, 410]);
        const focusLayout = split("horizontal", [stack(["navigation"]), stack(["chat"])], [310, 900]);
        const reviewLayout = split("horizontal", [stack(["chat"]), stack(["preview"])], [720, 520]);
        const lineageLayout = split("horizontal", [
          stack(["navigation"]),
          stack(["chat"]),
          stack(["hatg"]),
          stack(["preview"]),
        ], [290, 420, 760, 390]);
        lineageWorkbench = window.PortalWorkbench.create({
          id: "lineage",
          root: "#lineageWorkbenchRoot",
          label: window.PortalI18n.t("Society 工作台"),
          // Explicit navigation is applied before any saved binding can load.
          initialPanelStates: {
            ...(openingBindings.chat ? { chat: { target: openingBindings.chat } } : {}),
            ...(openingBindings.hatg ? { hatg: openingBindings.hatg } : {}),
          },
          insets: { top: 0, right: 0, bottom: 0, left: 0 },
          initialEditors: [
            lineageEditorDescriptor({
              instanceId: "navigation",
              type: "navigation",
              resource: { kind: "navigation", title: window.PortalI18n.t("Lineage 导航") },
            }),
            lineageEditorDescriptor({
              instanceId: "chat",
              type: "chat",
              resource: { kind: "chat", title: window.PortalI18n.t("Chat Workspace") },
            }),
            lineageEditorDescriptor({
              instanceId: "preview",
              type: "preview",
              resource: currentLineageEditorResource("preview"),
            }),
            terminalEditorDescriptor(),
            lineageEditorDescriptor({
              instanceId: "hatg",
              type: "hatg",
              resource: currentLineageEditorResource("hatg"),
            }),
          ].filter(Boolean),
          defaultLayout: lineageLayout,
          editorTypes: lineageEditorTypes,
          resolveEditorResource: currentLineageEditorResource,
          createEditorDescriptor: lineageEditorDescriptor,
          restorePanel: spec => lineageEditorDescriptor({
            instanceId: spec.id,
            type: spec.type,
            resource: openingBindings[spec.id] || spec.binding || { kind: spec.type },
          }),
          resourceForPanel: (_panelId, runtime, descriptor) => descriptor.binding || runtime?.target?.() || currentLineageEditorResource(descriptor.type),
          editorTitle: (type, resource) => resource.title || resource.label || resource.resident_id || resource.lineageId || ({ chat: "Chat Workspace", hatg: "HATG", preview: "预览", terminal: "终端" }[type]),
          presets: [
            { id: "lineage", name: window.PortalI18n.t("完整工作台"), layout: lineageLayout },
            { id: "focus", name: window.PortalI18n.t("对话"), layout: focusLayout, hidden: ["preview", "hatg"] },
            { id: "console", name: window.PortalI18n.t("对话与成果"), layout: consoleLayout, hidden: ["hatg"] },
            { id: "review", name: window.PortalI18n.t("成果查看"), layout: reviewLayout, hidden: ["navigation", "hatg"] },
            lineageHatgWorkspace(),
          ],
          defaultPreset: "console",
          onPanelEvent: onLineagePanelBinding,
          onChange: () => window.requestAnimationFrame(() => {
            syncNavigationPanelControl();
            syncPreviewPanelControl();
            terminalController?.fit?.();
          }),
        });
        terminalController = lineageWorkbench.getPanel("terminal") || null;
        window.PortalIdentity.load().then(applyTerminalAccess).catch(() => {});
      }

      function initPaneLayout() {
        initLineageWorkbench();
        syncNavigationPanelControl();
        syncPreviewPanelControl();
        applyWatchboardLayout();
      }

      function bindMountedWorkbenchEvents() {
        agentFilterDropdown = window.PortalFormControls.bindFilterDropdown({
          button: "filterToggleBtn",
          panel: "agentFilterPanel",
          placement: "bottom-end",
          onToggle: open => {
            state.filterOpen = open;
            syncAgentTools();
          },
        });
        agentFilterSummary = window.PortalFormControls.bindFilterSummary({
          target: "agentFilterSummary",
          items: () => {
            const items = [];
            if (state.projectDisplayFilter !== "non-empty") {
              items.push({
                label: window.PortalI18n.t("显示范围"),
                value: "全部",
              });
            }
            if (state.projectFilter !== "all") {
              const option = projectFilterOptions().find(item => item.value === state.projectFilter);
              items.push({ label: window.PortalI18n.t("项目"), value: option?.label || state.projectFilter });
            }
            return items;
          },
          onClear: () => {
            state.projectFilter = "all";
            state.projectDisplayFilter = "non-empty";
            agentFilterDropdown?.close();
            writeLineageUiState({
              projectFilter: state.projectFilter,
              projectDisplayFilter: state.projectDisplayFilter,
            });
            renderAgentList();
          },
        });
        $id("chatroomCreateBtn").addEventListener("click", () => {
          openCreateChatroomPicker().catch(error => window.PortalUI.toast(error.message || String(error), "error"));
        });
        $id("chatroomRefreshBtn").addEventListener("click", () => loadChatrooms());
        $id("searchInput").addEventListener("input", event => {
          state.query = event.target.value || "";
          // Search is user-owned navigation state and must survive an immediate refresh.
          // It is small enough to persist synchronously on each input event.
          writeLineageUiState({ query: state.query });
          renderAgentList();
        });
        $id("searchToggleBtn").addEventListener("click", () => setSearchOpen(!state.searchOpen));
        $id("agentFilterPanel").addEventListener("click", event => {
          const target = event.target instanceof Element ? event.target : null;
          const btn = target?.closest("[data-project-filter]");
          if (btn) {
            setProjectFilter(btn.dataset.projectFilter || "all");
            return;
          }
          const displayBtn = target?.closest("[data-project-display-filter]");
          if (displayBtn) setProjectDisplayFilter(displayBtn.dataset.projectDisplayFilter || "non-empty");
        });
        $id("refreshBtn").addEventListener("click", () => societyServiceRecovery.run(recoveryLoadAll));
        $id("favoriteRefreshBtn").addEventListener("click", () => societyServiceRecovery.run(recoveryLoadAll));
        document.querySelectorAll("[data-side-collapse-toggle]").forEach(btn => {
          btn.addEventListener("click", () => setNavigationPanelVisible(!lineageWorkbench?.isPanelExpanded("navigation")));
        });
        $id("previewCollapseBtn")?.addEventListener("click", () => setPreviewPanelVisible(!lineageWorkbench?.isPanelExpanded("preview")));
        $id("editPreviewBtn").addEventListener("click", () => setPreviewMode("code"));
        $id("visualPreviewBtn").addEventListener("click", () => setPreviewMode("visual"));
        $id("undoPreviewBtn").addEventListener("click", () => runPreviewEditorCommand("undo"));
        $id("redoPreviewBtn").addEventListener("click", () => runPreviewEditorCommand("redo"));
        $id("wrapPreviewBtn").addEventListener("click", togglePreviewWordWrap);
        $id("openPreviewExternalBtn").addEventListener("click", openPreviewInNewTab);
        $id("savePreviewBtn").addEventListener("click", savePreviewEdit);
        $id("pinAgentPreviewBtn").addEventListener("click", () => currentPreviewTabsController()?.togglePin?.());
        $id("agentList").addEventListener("scroll", () => {
          if (state.agentMenuId || state.residentMemberMenuId) renderAgentFloatingMenu();
        });
        $id("favoriteAgentList").addEventListener("scroll", () => {
          if (state.agentMenuId || state.residentMemberMenuId) renderAgentFloatingMenu();
        });
      }

      function ensureLineageWorkbench() {
        if (lineageWorkbench) return lineageWorkbench;
        initPaneLayout();
        connectPreviewSurface();
        initLineageActivityRail();
        initPreviewController();
        bindWorkspacePreviewHost();
        bindMountedWorkbenchEvents();
        return lineageWorkbench;
      }

      function applyOpeningHatgNavigation() {
        if (openingHatgNavigationApplied || !openingNavigation.lineageId) return;
        // Set the captured URL target before Resident loading: a restored panel
        // may belong to another Lineage, and the Resident may no longer exist.
        const applied = syncLineageHatgPanel({
          lineageId: openingNavigation.lineageId,
          residentId: openingNavigation.residentId,
        });
        openingHatgNavigationApplied = true;
        // Bind the explicit graph before any Resident reads can fail. Ordinary
        // navigation retains the restored layout, including a hidden canvas.
        if (openingNavigation.view !== "hatg") return applied;
        lineageWorkbench.applyWorkspace(lineageHatgWorkspace());
        lineageWorkbench.focusPanel("hatg");
        return applied;
      }

      function bindSplitters() {
        lineageStateController.listen("page", window, "resize", () => {
          applyWatchboardLayout();
          window.requestAnimationFrame(() => terminalController?.fit?.());
          renderActivityRecentRail();
        });
      }

      let personalWorkspaces = null;
      function showPersonalWorkspaces() {
        const root = $id("personalWorkspaces");
        if (!root) return;
        if (personalWorkspaces?.root !== root) {
          personalWorkspaces?.controller.destroy();
          personalWorkspaces = { root, controller: window.PortalPersonalWorkspaces.mount({
            root, onOpen: (path, options) => loadLocalLinkPreview(path, "", options), onRefresh: loadFolder,
          }) };
          const residentId = selectedResidentId();
          personalWorkspaces.controller.setResident({
            id: residentId,
            roots: state.folder?.resident_id === residentId ? state.folder.roots || [] : null,
            error: state.folder?.resident_id === residentId ? state.folder.error : "",
          });
        }
        void personalWorkspaces.controller.load();
        return personalWorkspaces.controller;
      }

      function setSideView(view, { userInitiated = false, openingResource = false } = {}) {
        const next = window.PortalNavigation.normalizeLineageView(view);
        if (userInitiated) {
          // A real navigation supersedes an opening URL; restored panels and
          // a repeated loadAll must not consume that URL before it is opened.
          if (!openingResource) pendingWorkspaceId = "";
          state.sideViewInteractionSeq += 1;
          if (!lineageWorkbench?.isPanelExpanded("navigation")) setNavigationPanelVisible(true);
        }
        const activePanel = document.querySelector(`[data-side-panel="${next}"]`);
        const viewChanged = state.sideView !== next || !activePanel?.classList.contains("active");
        state.sideView = next;
        currentLineageActivityRail()?.setActiveView?.(next);
        writeLineageUiState({ sideView: next, lastSideView: next });
        document.querySelectorAll("[data-side-view]").forEach(btn => {
          const active = btn.dataset.sideView === next;
          btn.classList.toggle("active", active);
          btn.setAttribute("aria-pressed", active ? "true" : "false");
        });
        document.querySelectorAll("[data-side-panel]").forEach(panel => {
          panel.classList.toggle("active", panel.dataset.sidePanel === next);
        });
        if (next === "workspaces") showPersonalWorkspaces();
        if (!viewChanged) {
          return;
        }
        if (next === "chatrooms") {
          renderChatroomList();
          loadChatrooms();
        }
        else if (next === "favorites") renderFavoriteList();
      }

      function persistCurrentSideView() {
        writeLineageUiState({ sideView: state.sideView || "agents", lastSideView: state.sideView || "agents" });
      }

      const bootstrappedIdentity = window.PortalIdentity.peek();
      state.me = String(bootstrappedIdentity.username || "");
      ensureLineageWorkbench();
      const usernameLabel = $id("portalUsername");
      if (usernameLabel) usernameLabel.textContent = state.me;
      $id("agentFloatingMenu").addEventListener("click", event => {
        event.stopPropagation();
        const target = event.target instanceof Element ? event.target : null;
        const forkBtn = target?.closest("[data-resident-member-action]");
        if (forkBtn) {
          handleResidentMemberCardAction(forkBtn.dataset.residentMemberAction || "", forkBtn.dataset.residentId || "", forkBtn.dataset.memberResidentId || "");
          return;
        }
        const btn = target?.closest("[data-agent-floating-action]");
        if (!btn) return;
        handleAgentCardAction(btn.dataset.agentFloatingAction || "", state.agentMenuId);
      });
      lineageStateController.listen("page", window, "pagehide", persistCurrentSideView);

      workbenchModule("PortalLineageWorkbenchDialogControls").createDialogControls({
        $id, closeCrossLineageForkModal, closeResidentAttachChoiceModal, closeResidentEditModal,
        closeResidentForkCreateModal, closeResidentRegisterModal, handleForkCreateAction,
        lineageIdForResident, lineageLeaderResidentId, loadCrossLineageForkNodes,
        openAttachResidentModal, openResidentForkCreateModal, renderCrossLineageForkModal,
        renderResidentAttachChoiceModal, renderResidentForkAccount, resetResidentAttachOptionState,
        residentAttachChoiceItem, restoreResidentRegisterForm, state, submitCrossLineageFork,
        submitResidentEditSurface, submitResidentRegister, syncResidentBackendRuntimeFields,
        syncResidentEffortSelect,
      });
      lineageStateController.listen("page", window, "resize", () => {
        if (state.agentMenuId || state.residentMemberMenuId) renderAgentFloatingMenu();
      });
      let chatroomUnreadSignature = "";
      let recentSidebarSignature = "";
      const unsubscribeConversations = window.PortalConversationStore.subscribe(snapshot => {
        const recentSignature = JSON.stringify(snapshot?.recent || []);
        if (recentSignature !== recentSidebarSignature) {
          recentSidebarSignature = recentSignature;
          renderActivityRecentRail();
        }
        const signature = JSON.stringify(snapshot.entries
          .filter(entry => entry.kind === "chatroom")
          .map(entry => [entry.key, entry.unread_count, entry.muted, entry.unread_mention_count, entry.attention_unread]));
        if (signature === chatroomUnreadSignature) return;
        chatroomUnreadSignature = signature;
        if (state.sideView === "chatrooms") renderChatroomList();
      });
      // Chatroom list changes (new messages, members) only wake this read; the
      // server list stays authoritative (decision_a6bebf4ac283).
      const chatroomRefreshCoordinator = window.PortalRefreshCoordinator.create({
        key: () => `lineage-chatrooms:${state.me || "anonymous"}`,
        interval: 60000,
        initialDelay: 60000,
        fetch: () => window.PortalConversationStore.readChatrooms(),
        apply: payload => {
          applyChatroomRecords(payload?.chatrooms);
          if (state.sideView === "chatrooms") renderChatroomList();
        },
        subscribe: window.PortalRefreshCoordinator.portalSubscription({
          domains: ["projection"],
          refreshOnAttach: false,
          debounceMs: 200,
          matches: detail => Boolean(detail?.snapshot_required || detail?.projection_changes?.chatroom),
        }),
      });
      chatroomRefreshCoordinator.start();
      lineageStateController.listen("page", window, "portal:agent-conversation-open", openLineageConversationFromInbox);
      lineageStateController.listen("page", window, "portal:resident-menu-changed", event => {
        if (event.detail?.type !== "delete") return;
        void retireDeletedResidentSurfaces([event.detail.residentId])
          .catch(error => window.PortalUI.toast(error.message || String(error), "error"));
      });
      lineageStateController.listen("page", window, "portal:user-navigation-changed", event => {
        state.favoriteLoadError = "";
        state.favoriteAgentIds = new Set((event.detail?.items || []).map(favoriteSelectionForObject).filter(Boolean));
        renderAgentList(); renderFavoriteList();
      });
      lineageStateController.listen("page", window, "portal:change", event => {
        if (previewChangeMatches(event?.detail || {})) schedulePreviewRefresh();
        if ((event.detail?.changed?.lineage || []).includes(state.personalAssistant?.lineage_id)) void refreshPersonalAssistantShortcut();
      });
      lineageStateController.listen("page", window, "portal:events-ready", event => {
        if (event?.detail?.snapshot_required) schedulePreviewRefresh();
      });
      lineageStateController.listen("page", document, "visibilitychange", () => {
        if (!document.hidden) window.requestAnimationFrame(() => terminalController?.fit?.());
      });
      lineageStateController.listen("page", window, "pagehide", event => {
        // beforeunload may be canceled. A cached page resumes these same instances;
        // only a committed departure without BFCache ends the page's lifetime.
        if (event.persisted) return;
        chatSelectionContextController?.destroy?.();
        chatSelectionContextController = null;
        watchboardController.destroy();
        chatroomRefreshCoordinator.destroy();
        unsubscribeConversations();
        currentLineageActivityRail()?.destroy?.();
        currentLineageEditorOpenCommands()?.destroy?.();
        currentAgentFloatingMenuOverlay()?.destroy?.();
        lineageWorkbench?.destroy?.();
        agentChatPanel = null;
        agentChatWorkspace = null;
        residentSidebarRefreshCoordinator.destroy();
        previewRefreshCoordinator.destroy();
        lineageStateController.destroy();
      });

      bindSplitters();
      bindWatchboardSplitter();
      bindPreviousChatWorkspaceNavigation();
      void initializeLineageWorkbench();
})();
