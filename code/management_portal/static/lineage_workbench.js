(() => {
      "use strict";

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
      let residentSelectionRequest = 0;
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

      function residentSelectionId(value) {
        const residentId = typeof value === "string" ? value : value?.resident_id;
        return residentId ? `resident:${residentId}` : "";
      }

      let chatTargetEpochSequence = 0;
      const chatTargetEpochs = new WeakMap();

      function chatTargetEpoch(selection) {
        return selection && typeof selection === "object"
          ? Number(chatTargetEpochs.get(selection) || 0)
          : 0;
      }

      function createChatTarget(targetKind, targetId, lineageId = "", epoch = 0, presentation = {}) {
        const kind = String(targetKind || "").trim();
        const id = String(targetId || "").trim();
        if (!id || kind !== "resident") return null;
        const chatTarget = {
          key: `${kind}:${id}`,
          kind,
          id,
          lineage_id: String(lineageId || "").trim(),
          title: String(presentation?.title || presentation?.name || "").trim(),
          backend: String(presentation?.backend || "").trim(),
        };
        chatTargetEpochs.set(
          chatTarget,
          Number(epoch) || ++chatTargetEpochSequence,
        );
        return Object.freeze(chatTarget);
      }

      function activeChatTargetId() {
        return String(state.chatTarget?.key || "");
      }

      function activeLineageId() {
        return String(state.chatTarget?.lineage_id || "");
      }

      function commitChatTarget(chatTarget) {
        if (!chatTarget) {
          state.chatTarget = null;
          personalWorkspaces?.controller.setResident();
          return null;
        }
        const canonical = createChatTarget(
          chatTarget.kind,
          chatTarget.id,
          chatTarget.lineage_id,
          chatTargetEpoch(chatTarget),
          chatTarget,
        );
        if (!canonical || canonical.key !== String(chatTarget.key || "")) {
          throw new Error("ChatTarget 合同无效");
        }
        if (residentIdForSelection(canonical) !== selectedResidentId()) {
          personalWorkspaces?.controller.setResident({ id: residentIdForSelection(canonical) });
        }
        state.chatTarget = canonical;
        state.chatNavigationError = "";
        return canonical;
      }

      function selectedResidentId() {
        const selectionId = activeChatTargetId();
        return selectionId.startsWith("resident:") ? selectionId.slice("resident:".length) : "";
      }

      function residentExactId(value) {
        return String(value?.resident_id || "").trim();
      }

      function residentRootForExactId(residentId) {
        const exactId = String(residentId || "").trim();
        if (!exactId) return null;
        return state.residents.find(item => (
          String(item?.resident_id || "") === exactId
        )) || null;
      }

      async function resolveResidentNavigationTarget(chatTarget) {
        if (String(chatTarget?.lineage_id || "").trim()) return chatTarget;
        try {
          const target = await residentDiscovery.resolveNavigationTarget(chatTarget?.id);
          return residentChatTarget(target?.resident_id, target?.lineage_id, chatTarget);
        } catch (error) {
          const status = Number(error?.status || 0);
          if (status === 404 || status === 403) {
            window.PortalConversationStore?.discardResident?.(chatTarget?.id);
            window.PortalUI.toast(
              status === 404
                ? window.PortalI18n.t("该 Resident 已删除或不存在。")
                : window.PortalI18n.t("没有查看这个 Resident 的权限。"),
              "warning",
            );
            return null;
          }
          throw error;
        }
      }

      function firstAvailableResidentChatTarget({
        excludedLineageIds = new Set(),
        excludedResidentIds = new Set(),
      } = {}) {
        for (const root of lineageSidebarRoots("", { respectFilters: false })) {
          const lineageId = registeredLineageIdForResident(root);
          if (excludedLineageIds.has(lineageId)) continue;
          const resident = lineageSidebarResidentsForItem(root).find(candidate => {
            const residentId = residentExactId(candidate);
            return residentId && !excludedResidentIds.has(residentId);
          });
          if (resident) {
            return residentChatTarget(residentExactId(resident), lineageId, resident);
          }
        }
        return null;
      }

      function currentResident() {
        const residentId = selectedResidentId();
        if (!residentId) return null;
        return residentRootForExactId(residentId)
          || (String(state.detail?.resident_id || state.detail?.resident?.resident_id || "") === residentId ? state.detail.resident : null)
          // Chat targets an exact Resident identity. Navigation is only a
          // display projection and must not decide whether that target exists.
          || {
            resident_id: residentId,
            lineage_id: activeLineageId(),
            title: String(state.chatTarget?.title || "").trim(),
            backend: String(state.chatTarget?.backend || "").trim(),
          };
      }

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

      function initialResidentIdFromUrl() {
        const params = new URLSearchParams(window.location.search);
        return String(params.get("resident") || "").trim();
      }

      function initialLineageIdFromUrl() {
        const params = new URLSearchParams(window.location.search);
        return String(params.get("lineage") || "").trim();
      }

      function initialOpenChatFromUrl() {
        return new URLSearchParams(window.location.search).get("open_chat") === "1";
      }

      function consumeOpenChatNavigationIntent() {
        const target = new URL(window.location.href);
        if (!target.searchParams.has("open_chat")) return;
        target.searchParams.delete("open_chat");
        history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
      }

      function initialProgramItemIdFromUrl() {
        return String(new URLSearchParams(window.location.search).get("program_item") || "").trim();
      }

      function initialChatroomIdFromUrl() {
        const value = String(new URLSearchParams(window.location.search).get("chatroom") || "").trim();
        return /^chatroom_[0-9a-f]{12}$/.test(value) ? value : "";
      }

      function syncHatgLocation(detail = {}) {
        if (!state.chatTarget && (
          state.chatNavigationError
          || (!state.loadAllComplete && (openingNavigation.residentId || openingNavigation.lineageId))
        )) return;
        const target = new URL(window.location.href);
        const lineageId = String(detail.lineageId || "").trim();
        const programItemId = String(detail.programItemId || "").trim();
        if (lineageId) target.searchParams.set("lineage", lineageId);
        else target.searchParams.delete("lineage");
        // A reload opens the graph of the URL's Resident: one of another
        // Lineage (the source of a moved subgraph) would take it back there.
        const residentId = String(target.searchParams.get("resident") || "").trim();
        const resident = residentId
          ? (state.residents || []).find(item => String(item?.resident_id || "").trim() === residentId)
          : null;
        const residentLineageId = resident ? registeredLineageIdForResident(resident) : "";
        if (lineageId && residentLineageId && residentLineageId !== lineageId) {
          target.searchParams.delete("resident");
          target.searchParams.delete("open_chat");
        }
        if (lineageId && programItemId) target.searchParams.set("program_item", programItemId);
        else target.searchParams.delete("program_item");
        history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
      }

      function syncChatTargetLocation(chatTarget = state.chatTarget) {
        if (!chatTarget) return;
        const target = new URL(window.location.href);
        if (chatTarget.kind !== "resident") return;
        target.searchParams.set("resident", chatTarget.id);
        if (chatTarget.lineage_id) target.searchParams.set("lineage", chatTarget.lineage_id);
        else target.searchParams.delete("lineage");
        history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
      }

      function clearDeletedLineageLocation(lineageId, residentIds) {
        const deletedId = String(lineageId || "").trim();
        const deletedResidents = residentIds instanceof Set
          ? residentIds
          : new Set(residentIds || []);
        const target = new URL(window.location.href);
        if (target.searchParams.get("lineage") === deletedId) {
          target.searchParams.delete("lineage");
          target.searchParams.delete("program_item");
        }
        if (deletedResidents.has(String(target.searchParams.get("resident") || "").trim())) {
          target.searchParams.delete("resident");
          target.searchParams.delete("open_chat");
        }
        history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
      }

      async function retireDeletedResidentSurfaces(residentIds) {
        const deleted = new Set(residentIds.map(id => String(id || "").trim()).filter(Boolean));
        if (!deleted.size) return;
        window.PortalResidentMenu?.retireResidents([...deleted]);
        for (const id of deleted) unavailableResidentBindings.add(id);
        if (deleted.has(selectedResidentId())) {
          const resident = currentResident();
          const target = currentChatTarget();
          if (target && !target.title) {
            commitChatTarget({ ...target, title: resident?.title || resident?.name || target.id });
          }
          beginChatWorkspaceNavigation();
          state.chatNavigationError = window.PortalI18n.t("该 Resident 已删除。");
          state.detail = null;
          state.folder = null;
          state.status = null;
          state.selectedFile = "";
          state.folderRequestSequence += 1;
          writeLineageUiState({ lastChatTarget: null });
          resetChatRenderState();
        }
        const panels = new Set([agentChatPanel, ...(lineageWorkbench?.getPanelsByType?.("chat") || [])]);
        for (const runtime of panels) {
          if (deleted.has(String(runtime?.target?.()?.resident_id || ""))) {
            runtime.rejectUnavailableTarget?.({ status: 404 });
          }
        }
        for (const document of currentPreviewTabsController()?.snapshot?.()?.documents || []) {
          if (deleted.has(String(document.residentId || document.restoreLocator?.residentId || ""))) {
            if (currentPreviewTabsController().isActive(document.key)) stopPreviewRefresh();
            await currentPreviewTabsController().close(document.key, { reason: "resident-deleted" });
          }
        }
        for (const id of deleted) window.PortalConversationStore?.discardResident?.(id);
        clearDeletedResidentLocation(deleted);
      }

      function clearDeletedResidentLocation(residentIds) {
        const deletedResidents = residentIds instanceof Set
          ? residentIds
          : new Set(residentIds || []);
        const target = new URL(window.location.href);
        const residentId = String(target.searchParams.get("resident") || "").trim();
        if (deletedResidents.has(residentId) && residentId !== selectedResidentId()) {
          target.searchParams.delete("resident");
          target.searchParams.delete("open_chat");
          target.searchParams.delete("program_item");
        }
        history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
      }

      function residentChatTarget(residentId, lineageId = "", presentation = {}) {
        const targetId = String(residentId || "").trim();
        const resident = lineageSidebarRootByResidentId(targetId);
        return createChatTarget(
          "resident",
          targetId,
          String(lineageId || registeredLineageIdForResident(resident)).trim(),
          0,
          presentation,
        );
      }

      function residentChatTargetForLineage(lineageId) {
        const targetLineageId = String(lineageId || "").trim();
        if (!targetLineageId) return null;
        const lineage = lineageSidebarRoots("", { respectFilters: false })
          .find(item => registeredLineageIdForResident(item) === targetLineageId);
        if (!lineage) return null;
        const residentId = lineageLeaderResidentId(lineage)
          || residentExactId(lineageSidebarResidentsForItem(lineage)[0]);
        return residentChatTarget(residentId, targetLineageId, lineage);
      }

      function openLineageConversationFromInbox(event) {
        const request = event?.detail;
        const entry = request?.entry;
        if (!request || String(entry?.surface || "") !== "lineage") return;
        let target;
        try {
          target = new URL(String(entry.url || ""), window.location.href);
        } catch {
          return;
        }
        const residentId = String(target.searchParams.get("resident") || "").trim();
        const lineageId = String(target.searchParams.get("lineage") || "").trim();
        const chatroomId = String(target.searchParams.get("chatroom") || "").trim();
        let navigate = null;
        const residentSelection = residentId
          ? residentChatTarget(residentId, lineageId)
          : null;
        if (residentSelection) {
          navigate = () => selectResident(residentSelection.id, {
            forceView: true,
            lineageId: residentSelection.lineage_id,
            revealInGraph: true,
          });
        } else if (/^chatroom_[0-9a-f]{12}$/.test(chatroomId)) {
          navigate = () => {
            setSideView("chatrooms");
            return selectChatroom(chatroomId);
          };
        }
        if (!navigate) return;
        request.handled = true;
        Promise.resolve(navigate()).catch(error => {
          window.PortalUI.toast(window.PortalI18n.text`打开 Agent 对话失败：${error.message || error}`, "error");
        });
      }

      const unavailableResidentBindings = new Set();

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

      function readLastSelection() {
        const payload = readLineageUiState().lastChatTarget;
        if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
        const selection = createChatTarget(
          payload.kind,
          payload.id,
          payload.lineage_id,
          0,
          payload,
        );
        return selection?.key === String(payload.key || "")
          && !unavailableResidentBindings.has(selection.id) ? selection : null;
      }

      function writeLastSelection(selection = state.chatTarget) {
        if (!selection) return;
        if (isResidentChatSelection(selection) && unavailableResidentBindings.has(selection.id)) return;
        writeLineageUiState({
          lastChatTarget: selection,
        });
      }

      function chatWorkspaceSnapshotKey(snapshot) {
        if (!snapshot || typeof snapshot !== "object") return "";
        return String(snapshot.key || "");
      }

      function currentChatTarget() {
        return state.chatTarget;
      }

      function chatTargetForPanel(panel, target = panel?.target?.() || {}) {
        const residentId = String(target.resident_id || "").trim();
        if (!residentId) return currentChatTarget();
        if (currentChatTarget()?.id === residentId) return currentChatTarget();
        return createChatTarget("resident", residentId, target.lineage_id, 0, target);
      }

      function residentIdForSelection(selection) {
        if (!selection || selection.kind !== "resident") return "";
        return String(selection.id || "").trim();
      }

      function runChatTargetEffect(selection, effect) {
        if (selection !== state.chatTarget) return undefined;
        return effect();
      }

      function beginChatWorkspaceNavigation() {
        chatSelectionContextController?.clear?.();
        return state.chatTarget;
      }

      function rememberChatWorkspaceBeforeSwitch(nextSnapshot) {
        const current = currentChatTarget();
        if (!current || !nextSnapshot) return false;
        if (chatWorkspaceSnapshotKey(current) === chatWorkspaceSnapshotKey(nextSnapshot)) return false;
        state.previousChatWorkspace = current;
        // The tab's interface record names the Resident being left, so the
        // shortcut finds it after a reload or from another page.
        window.PortalState.interfaceHistory?.keepCurrent();
        return true;
      }

      // The Resident of a Society page address this tab left, when the page's
      // own memory of it went with a reload or a visit to another page.
      function previousChatWorkspaceFromInterfaceHistory() {
        const url = String(window.PortalState.interfaceHistory?.previousUrl?.() || "");
        if (!url) return null;
        let target;
        try {
          target = new URL(url, window.location.origin);
        } catch {
          return null;
        }
        if (!/^\/lineage(?:\.html)?$/.test(target.pathname)) return null;
        return createChatTarget("resident", target.searchParams.get("resident"), target.searchParams.get("lineage") || "");
      }

      function isResidentChatSelection(snapshot) {
        const selectionId = String(snapshot?.key || "").trim();
        if (!selectionId) return false;
        return snapshot?.kind === "resident";
      }

      async function goPreviousChatWorkspace(previous = state.previousChatWorkspace) {
        if (!isResidentChatSelection(previous)) return false;
        const current = currentChatTarget();
        if (current && chatWorkspaceSnapshotKey(current) === chatWorkspaceSnapshotKey(previous)) return false;
        await selectResident(previous.id, {
          forceView: true,
          lineageId: previous.lineage_id,
        });
        return true;
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

      function resetChatRenderState() {
        state.providerSessionMetadataRequest = "";
        state.detailSignature = "";
        state.residentStatusSignature = "";
        state.residentDetailStatusSignature = "";
        state.residentRuntimeSignature = "";
        state.workspaceMessageItems = [];
        agentChatWorkspace?.clearForwardSelection?.();
      }

      async function handleResidentCardClick(residentId) {
        const id = String(residentId || "").trim();
        if (!id) return;
        state.agentMenuId = "";
        await selectResident(id, { forceView: true });
      }

      async function selectDerivedResident(residentId, derivedResidentId) {
        const id = String(residentId || "").trim();
        const targetResidentId = String(derivedResidentId || "").trim();
        if (!id || !targetResidentId) return;
        await selectResident(targetResidentId, { forceView: true });
      }

      function publishConversationSelection(chatTarget, reason = "resident-select") {
        if (!chatTarget || !lineageWorkbench?.setSelection) return;
        lineageWorkbench.setSelection("conversationTarget", Object.freeze({
          residentId: String(chatTarget.id || ""),
          lineageId: String(chatTarget.lineage_id || ""),
          key: String(chatTarget.key || ""),
        }), { source: "lineage", reason });
      }

      async function selectResident(residentId, options = {}) {
        closeChatroomView();
        let chatTarget = residentChatTarget(
          residentId,
          options.lineageId,
          {
            title: options.title,
            backend: options.backend,
          },
        );
        if (!chatTarget?.key) return false;
        // forceView is an explicit conversation-opening intent, not merely a
        // request to focus a panel that happens to remain visible. Restore the
        // canonical Chat panel before any asynchronous target resolution.
        if (options.forceView === true) {
          lineageWorkbench?.focusPanel?.(agentChatPanel?.instanceId?.() || "chat");
        }
        if (lineageWorkbench && (
          !agentChatPanel
          || lineageWorkbench.getPanel(agentChatPanel.instanceId()) !== agentChatPanel
        )) {
          return lineageWorkbench.openResource({
            type: "chat",
            mode: "reuse",
            resource: {
              kind: "chat",
              resident_id: chatTarget.id,
              lineage_id: chatTarget.lineage_id,
              title: chatTarget.title,
              backend: chatTarget.backend,
              reveal_in_graph: options.revealInGraph === true,
            },
          });
        }
        const selectionRequest = ++residentSelectionRequest;
        chatTarget = await resolveResidentNavigationTarget(chatTarget);
        if (!chatTarget || selectionRequest !== residentSelectionRequest) return false;
        const selectionId = String(chatTarget.key || "");
        const syncSelectedGraph = target => {
          if (options.revealInGraph !== true && lineageHatgTargetMatches(target)) return undefined;
          return Promise.resolve(syncLineageHatgPanel({
            lineageId: target.lineage_id,
            residentId: target.id,
            revealInGraph: options.revealInGraph === true,
          })).then(() => {
            // A later selection or an authoritative move may supersede this
            // graph navigation while it is loading.
            if (
              selectionRequest === residentSelectionRequest
              && activeChatTargetId() === selectionId
              && state.chatTarget?.lineage_id === target.lineage_id
            ) syncChatTargetLocation(state.chatTarget);
          });
        };
        const sameSelection = activeChatTargetId() === selectionId;
        const sameReadyPanel = sameSelection && agentChatPanel?.isReadyFor?.({
          resident_id: chatTarget.id,
          lineage_id: chatTarget.lineage_id,
        });
        if (sameReadyPanel) {
          publishConversationSelection(chatTarget, "resident-reselect");
          const routingChanged = String(state.chatTarget?.lineage_id || "") !== chatTarget.lineage_id;
          if (routingChanged) {
            commitChatTarget(chatTarget);
            writeLastSelection();
          }
          syncChatTargetLocation(chatTarget);
          if (options.revealInGraph === true || !lineageHatgTargetMatches(chatTarget)) {
            await syncSelectedGraph(chatTarget);
          }
          if (options.forceView === true) {
            lineageWorkbench?.focusPanel?.(agentChatPanel?.instanceId?.() || "chat");
          }
          return true;
        }
        const switched = rememberChatWorkspaceBeforeSwitch(chatTarget);
        commitChatTarget(chatTarget);
        syncChatTargetLocation(chatTarget);
        if (switched) window.PortalState.interfaceHistory?.visit();
        beginChatWorkspaceNavigation();
        state.agentMenuId = "";
        state.detail = null;
        state.folder = null;
        state.status = null;
        resetChatRenderState();
        state.selectedFile = "";
        writeLastSelection();
        const detailRequest = loadDetail(false);
        syncResidentNavigationSelection();
        syncSelectedResidentSurface();
        // The graph already has an explicit Resident and Lineage. Let it select
        // and reveal the node while Chat loads, instead of waiting for its detail.
        void syncSelectedGraph(chatTarget);
        await detailRequest;
        if (selectionRequest !== residentSelectionRequest || activeChatTargetId() !== selectionId) return false;
        if (!agentChatPanel?.isReadyFor?.({
          resident_id: chatTarget.id,
          lineage_id: chatTarget.lineage_id,
        })) return false;
        // The detail response is authoritative if this Resident moved since a
        // sidebar entry or deep link was created. Chat reads use the Resident ID;
        // correct only the route/graph once the server has returned its lineage.
        const resolvedLineageId = String(agentChatPanel.target?.()?.lineage_id || "").trim();
        if (resolvedLineageId && resolvedLineageId !== chatTarget.lineage_id) {
          chatTarget = residentChatTarget(chatTarget.id, resolvedLineageId, chatTarget);
          commitChatTarget(chatTarget);
          syncChatTargetLocation(chatTarget);
          writeLastSelection();
          void syncSelectedGraph(chatTarget);
        }
        publishConversationSelection(chatTarget);
        if (options.forceView === true) {
          lineageWorkbench?.focusPanel?.(agentChatPanel?.instanceId?.() || "chat");
        }
        loadFolder().catch(() => {});
        return true;
      }

      function renderEmptyChatWorkspace() {
        closeChatExtensionMenu();
        renderChatHeader();
        watchboardController.render({});
        renderPreviewActions();
        agentChatPanel?.showEmpty?.({
          emptyText: state.chatNavigationError || window.PortalI18n.t("选择一个 Resident 开始工作。"),
          userName: state.me || window.PortalI18n.t("用户"),
          assistantName: "Agent",
          placeholder: window.PortalI18n.t("选择 Resident 后开始对话"),
          meta: window.PortalI18n.t("当前没有可用的对话 Session"),
          historyScope: composerDraftKey(),
        });
        if (!activePreviewPayload() && !activeAgentPreviewDocument()) {
          setPreviewTitle(window.PortalI18n.t("预览"));
          $id("previewPath").textContent = window.PortalI18n.t("选择文件预览。");
          setPreviewOpenInput("");
          currentPreviewSelectionController()?.hide?.();
          $id("previewBox").className = "previewBox";
          $id("previewBox").textContent = window.PortalI18n.t("选择文件后显示内容。");
        }
      }

      function syncSelectedResidentSurface() {
        const resident = currentResident();
        if (!resident) {
          if (agentChatPanel?.target?.().resident_id) {
            void agentChatPanel.closeSurface();
          }
          renderEmptyChatWorkspace();
          return;
        }
        const residentId = selectedResidentId();
        const panelOwnsTarget = String(agentChatPanel?.target?.().resident_id || "") === residentId;
        const detail = panelOwnsTarget ? agentChatPanel?.currentDetail?.() : null;
        if (detail) {
          state.detail = detail;
          syncResidentStatusSignatures(detail);
          refreshLineageChatView();
        }
        renderChatHeader(resident);
        watchboardController.render(detail || {});
        renderPreviewActions();
      }

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

      function bindLineageChatWorkspace(panel) {
        if (!panel || agentChatPanel === panel) return;
        chatSelectionContextController?.destroy?.();
        chatSelectionContextController = null;
        agentChatPanel = panel;
        agentChatWorkspace = agentChatPanel.workspace;
        bindWatchboardSplitter();
        requestAnimationFrame(() => applyWatchboardLayout());
      }

      function activateLineageChatWorkspace(panel, resource = null, intent = {}) {
        if (!panel) return Promise.resolve(false);
        bindLineageChatWorkspace(panel);
        const target = resource && typeof resource === "object"
          ? resource
          : (panel.target?.() || {});
        const residentId = String(target.resident_id || "").trim();
        if (!residentId) return Promise.resolve(false);
        return selectResident(residentId, {
          forceView: true,
          lineageId: String(target.lineage_id || ""),
          title: String(target.title || ""),
          backend: String(target.backend || ""),
          revealInGraph: target.reveal_in_graph === true
            || intent.revealInGraph === true,
        });
      }

      function closeChatExtensionMenu() {
        agentChatWorkspace?.closeExtensionMenu?.();
      }

      function renderChatExtensionMenu(detail = state.detail || {}) {
        agentChatPanel?.renderExtensions?.();
      }

      async function updateCurrentResidentWatchboard(action, residentId) {
        if (residentId !== selectedResidentId()) return;
        if (action === "remove" && !await window.PortalUI.confirm({ title: window.PortalI18n.t("移除 Watchboard？"), message: window.PortalI18n.t("只会解除 Portal 挂载，不会删除脚本文件。"), confirmLabel: window.PortalI18n.t("移除") })) return;
        if (residentId !== selectedResidentId()) return;
        if (!residentId || state.watchboardMutationInFlight) return;
        state.watchboardMutationInFlight = true;
        renderChatExtensionMenu();
        async function refreshProjection() {
          if (residentId !== selectedResidentId()) return;
          // Refresh the Chat owner as well as the graph after a committed
          // update or a conflicting write. Older reads must not restore it.
          const panel = agentChatPanel;
          window.PortalResidentProjection.invalidate(residentId);
          const detail = await window.PortalResidentProjection.load(residentId, { force: true });
          if (residentId !== selectedResidentId() || panel !== agentChatPanel) return;
          panel.applyDetail(detail);
          writeLineageUiState({ watchboardHiddenResidents: [...state.watchboardHiddenResidents] });
          closeChatExtensionMenu();
          watchboardController.render(state.detail);
          await Promise.allSettled([loadFolder(), refreshResidentSidebarIfDue(true)]);
        }
        try {
          const result = await residentService.updateWatchboard(action, {
            resident_id: residentId,
          });
          // Newly installed cards must be visible when their rendered bounds
          // are measured. Revealing them afterwards leaves their default slots.
          state.watchboardHiddenResidents.delete(residentId);
          if (result.lineageRefreshError) {
            window.PortalUI.toast(window.PortalI18n.text`Watchboard 已更新，但图刷新失败；请刷新查看，勿重复安装：${result.lineageRefreshError.message || result.lineageRefreshError}`, "warning");
          }
          if (result.lineage) {
            try {
              await lineageWorkbench?.getPanel("hatg")?.applyLineageMutation(result.lineage, {
                separateObjectIds: result.created_component_ids,
              });
            } catch (error) {
              // The definition is committed. A failed placement must not
              // leave the extension menu offering the installation again.
              window.PortalUI.toast(window.PortalI18n.text`Watchboard 已更新，但画布布局未保存：${error.message || error}`, "warning");
            }
          }
          await refreshProjection();
        } catch (err) {
          if (err.watchboardConflict) {
            let refreshed = !err.watchboardConflict.refreshError;
            try {
              if (err.watchboardConflict.lineage) {
                await lineageWorkbench?.getPanel("hatg")?.applyLineageMutation(err.watchboardConflict.lineage);
              }
              await refreshProjection();
            } catch (_) { refreshed = false; }
            window.PortalUI.toast(refreshed
              ? window.PortalI18n.t("图已被其他操作修改，Watchboard 操作未提交。请检查最新状态后重新操作。")
              : window.PortalI18n.t("Watchboard 操作未提交，最新状态读取失败。请刷新后重新操作。"), "error");
            return;
          }
          window.PortalUI.toast(window.PortalI18n.text`更新 Watchboard 失败：${err.message || err}`, "error");
        } finally {
          state.watchboardMutationInFlight = false;
          renderChatExtensionMenu();
        }
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

      async function handleResidentMemberCardAction(action, residentId, derivedResidentId) {
        state.residentMemberMenuId = "";
        if (action === "backpack") {
          renderAgentList(); renderFavoriteList();
          residentMemberMenuButton(residentMemberMenuKey(residentId, derivedResidentId))?.focus();
          await window.PortalNav.openPrincipalBackpack({ principalId: derivedResidentId || residentId });
          return;
        }
        if (action === "open-new-tab" || action === "open-side") {
          try {
            const sourceResident = state.residents.find(item => String(item?.resident_id || "") === String(residentId || ""))
              || lineageSidebarRootByResidentId(residentId)
              || {};
            const derivedResident = derivedResidentForSidebar(residentId, derivedResidentId, sourceResident) || { resident_id: derivedResidentId };
            await openNavigationEditor({
              type: "resident",
              item: {
                ...derivedResident,
                lineage_id: String(derivedResident?.lineage_id || lineageIdForResident(sourceResident) || ""),
              },
            }, action === "open-side" ? "side" : "new-tab");
          } catch (err) {
            window.PortalUI.toast(window.PortalI18n.text`打开编辑器失败：${err.message || err}`, "error");
          }
          renderAgentList();
          renderFavoriteList();
        } else if (action === "delete") {
          await deleteDerivedResidentFromSidebar(residentId, derivedResidentId);
        } else if (action === "edit") {
          await editDerivedResidentFromSidebar(residentId, derivedResidentId);
        }
      }

      async function refreshResidentSidebarIfDue(force = false) {
        if (!force) return false;
        const payload = await residentSidebarRefreshCoordinator.refresh({ force: true });
        return !!payload;
      }

      async function deleteDerivedResidentFromSidebar(residentId, derivedResidentId) {
        if (!await window.PortalUI.confirmAction("delete", "这个 Resident", {
          consequence: "Resident 将标记为已删除，并移除 Session、图引用与自有工作区；对话与运行轨迹保留。",
        })) return;
        const derivedResident = state.residents.find(item => (
          String(item?.resident_id || "").trim() === String(derivedResidentId || "").trim()
        ));
        const parentResident = state.residents.find(item => (
          String(item?.resident_id || "").trim() === String(residentId || "").trim()
        ));
        const parentLineageId = registeredLineageIdForResident(parentResident)
          || activeLineageId();
        await deleteResidentFromWorkbench({
          residentId: derivedResidentId,
          lineageId: registeredLineageIdForResident(derivedResident) || parentLineageId,
          fallbackTarget: residentChatTarget(residentId, parentLineageId, parentResident),
        });
      }

      function compactMessageSignature(items) {
        return (Array.isArray(items) ? items : []).map(item => ({
          id: item?.id || "",
          at: item?.created_at || item?.at || "",
          source: item?.source || "",
          kind: item?.kind || "",
          author: item?.author || "",
          title: item?.title || "",
          len: item?.content_length || String(item?.content || item?.text || item?.message || "").length,
          text: String(item?.content || item?.text || item?.message || "").slice(-220),
          refs: item?.context_refs || {},
          command: item?.slash_command?.command || item?.slash_command || "",
          runtimeStatus: item?.runtime_status || "",
          runtimeDetails: item?.runtime_details || [],
          responseSources: item?.response_sources || {},
          tokenUsage: item?.token_usage || item?.turn_usage || {},
          tokenUsageAt: item?.token_usage_at || "",
        }));
      }

      function workspaceMessageEntries(workspace = agentChatWorkspace) {
        const items = workspace?.messages?.() || [];
        return items.map((item, idx) => ({
          item,
          idx,
          key: window.PortalChatWorkspace.messageIdentity(item),
        }));
      }

      function workspaceMessageByKey(key, workspace = agentChatWorkspace) {
        const target = String(key || "");
        return workspaceMessageEntries(workspace).find(entry => entry.key === target)?.item || null;
      }

      function lineageIdForResident(item) {
        return String(item?.lineage_id || item?.lineage?.lineage_id || item?.resident_id || "").trim();
      }

      function registeredLineageIdForResident(item) {
        return String(item?.lineage_id || item?.lineage?.lineage_id || "").trim();
      }

      function detailSignatureFor(detail, status = null) {
        const resident = currentResident();
        if (resident) {
          return JSON.stringify({
            type: "resident",
            id: detail?.resident_id || resident?.resident_id || "",
            residentState: detail?.resident_state || null,
            runtime: compactRuntimeSignature(detail?.runtime_settings || {}),
            defaultDerivedRuntime: compactRuntimeSignature(detail?.default_derived_runtime_settings || {}),
            watchboard: detail?.watchboard?.signature || "",
            messages: compactMessageSignature(detail?.messages || []),
            managedResidentCount: detail?.managed_resident_count || 0,
            managedResidentLimit: detail?.managed_resident_limit || 0,
            canCreateDerivedResident: !!detail?.can_create_derived_resident,
          });
        }
        return "";
      }

      function compactRuntimeSignature(settings) {
        return {
          model: settings?.model || "",
          effort: settings?.reasoning_effort || "",
          tier: settings?.service_tier || "",
          permissions: settings?.permissions || "",
          reviewer: settings?.approvals_reviewer || "",
          sandbox: settings?.sandbox || "",
          source: settings?.runtime_settings_source || "",
        };
      }

      function syncResidentStatusSignatures(payload) {
        state.residentStatusSignature = String(payload?.content_signature || "");
        state.residentDetailStatusSignature = String(payload?.detail_signature || "");
        state.residentRuntimeSignature = String(payload?.runtime_signature || "");
      }

      function composerDraftKey() {
        const resident = currentResident();
        if (resident) {
          const residentId = selectedResidentId();
          if (!residentId) return "";
          return window.PortalChatWorkspace.residentSessionScope({
            resident_id: residentId,
          });
        }
        return "";
      }

      const messageRole = item => window.PortalChatWorkspace.messageRole(item);

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

      async function refreshProviderSessionMetadata() {
        const residentId = residentIdForSelection(currentChatTarget());
        if (
          !residentId
          || state.detail?.provider_session?.id
          || state.providerSessionMetadataRequest === residentId
        ) return;
        state.providerSessionMetadataRequest = residentId;
        try {
          const metadata = await window.PortalResidentProjection.load(
            residentId,
            { force: true },
          );
          if (residentId !== residentIdForSelection(currentChatTarget()) || !state.detail) return;
          const providerSession = metadata?.provider_session;
          if (!providerSession?.id) return;
          state.detail.provider_session = providerSession;
          refreshLineageChatView();
          renderChatHeader(currentResident());
        } catch (error) {
          console.error(window.PortalI18n.t("Provider session metadata refresh failed"), error);
        } finally {
          if (state.providerSessionMetadataRequest === residentId) {
            state.providerSessionMetadataRequest = "";
          }
        }
      }

      function handleLineageChatPanelDetail(nextDetail, panel, notification = null) {
        const residentId = selectedResidentId();
        if (!residentId || panel.target()?.resident_id !== residentId) return;
        const projectedResidentId = String(
          nextDetail?.resident_id
          || nextDetail?.resident?.resident_id
          || ""
        ).trim();
        if (projectedResidentId && projectedResidentId !== residentId) return;
        const previousTarget = state.chatTarget;
        const lineageId = String(
          nextDetail?.resident?.lineage_id
          || nextDetail?.chat_workspace?.target?.lineage_id
          || ""
        ).trim();
        if (lineageId && lineageId !== previousTarget?.lineage_id) {
          const graphLineageId = String(lineageWorkbench?.getPanel("hatg")?.snapshot?.()?.lineageId || "");
          const nextTarget = commitChatTarget(residentChatTarget(residentId, lineageId, previousTarget));
          publishConversationSelection(nextTarget, "resident-membership-changed");
          // Menu reloads stay on the same Chat identity. Follow its new
          // membership when the canvas still shows the source, while leaving
          // a separately opened graph alone.
          if (!graphLineageId || graphLineageId === previousTarget?.lineage_id) {
            syncChatTargetLocation(nextTarget);
            void Promise.resolve(syncLineageHatgPanel({ lineageId, residentId }))
              .catch(error => window.PortalUI.toast(
                window.PortalI18n.text`Resident 归属已更新，但图刷新失败，请刷新查看：${error.message || error}`,
                "warning",
              ));
          }
        }
        state.status = null;
        state.detail = nextDetail;
        syncLineageChatWorkspaceProjection(nextDetail);
        syncResidentStatusSignatures(nextDetail);
        writeLastSelection();
        const nextSignature = detailSignatureFor(nextDetail, null);
        const changed = nextSignature !== state.detailSignature;
        const eventType = String(notification?.event?.event_type || "");
        renderChatHeader(nextDetail?.resident || currentResident());
        if (!eventType || !eventType.startsWith("conversation.message.")) {
          watchboardController.render(nextDetail);
        }
        if (changed) state.detailSignature = nextSignature;
        if (!state.detailSignature) state.detailSignature = nextSignature;
        const message = notification?.event?.payload?.message;
        if (eventType === "conversation.message.appended" && messageRole(message) === "agent") {
          void refreshProviderSessionMetadata();
        }
      }

      async function loadDetail(force = false) {
        const resident = currentResident();
        const residentId = selectedResidentId();
        if (!resident || !residentId || !agentChatPanel) return;
        if (unavailableResidentBindings.has(residentId)) return;
        if (state.lineageDeleteSaving === activeLineageId()) return;
        const selection = currentChatTarget();
        try {
          await agentChatPanel.setResident({
            resident_id: residentId,
            lineage_id: activeLineageId(),
            title: resident.title || resident.name || selection?.title || "",
            backend: resident.backend || selection?.backend || "",
          }, {
            load: true,
            force,
          });
        } catch (_err) {
          // The shared chat panel owns target-unavailable and load errors.
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

      function saveLayoutState() {
        lineageLayoutStore.write({
          watchboard: Math.round(state.layout.watchboard),
        });
      }

      function watchboardWidthLimits() {
        const body = chatElement("body");
        const width = body?.getBoundingClientRect().width || 900;
        return {
          min: 280,
          max: Math.max(320, Math.min(1280, Math.round(width * 0.60))),
        };
      }

      function applyWatchboardLayout({ persist = false } = {}) {
        const shell = $id("consoleShell");
        if (!shell) return;
        const limits = watchboardWidthLimits();
        state.layout.watchboard = window.PortalUI.clampNumber(state.layout.watchboard, limits.min, limits.max);
        shell.style.setProperty("--watchboard-dashboard-w", `${Math.round(state.layout.watchboard)}px`);
        if (persist) saveLayoutState();
      }

      function syncNavigationPanelControl() {
        const collapsed = lineageWorkbench?.isPanelCollapsed("navigation") === true;
        document.querySelectorAll("[data-side-collapse-toggle]").forEach(btn => {
          window.PortalWorkbench.layout.syncCollapseButton(btn, collapsed, {
            expand: "展开左侧栏",
            collapse: "折叠左侧栏",
          });
        });
      }

      function setNavigationPanelVisible(visible) {
        if (!lineageWorkbench) return;
        if (visible) {
          if (lineageWorkbench.isPanelVisible("navigation")) lineageWorkbench.expandPanel("navigation");
          else lineageWorkbench.showPanel("navigation");
        } else lineageWorkbench.collapsePanel("navigation");
        syncNavigationPanelControl();
      }

      function syncPreviewPanelControl() {
        const collapsed = lineageWorkbench?.isPanelCollapsed("preview") === true;
        const collapseBtn = $id("previewCollapseBtn");
        window.PortalWorkbench.layout.syncCollapseButton(collapseBtn, collapsed, {
          expand: "展开 Preview",
          collapse: "折叠 Preview",
        });
      }

      function setPreviewPanelVisible(visible) {
        if (!lineageWorkbench) return;
        if (visible) {
          if (lineageWorkbench.isPanelVisible("preview")) lineageWorkbench.expandPanel("preview");
          else lineageWorkbench.showPanel("preview");
        } else lineageWorkbench.collapsePanel("preview");
        syncPreviewPanelControl();
      }

      function ensurePreviewVisible() {
        if (!lineageWorkbench?.isPanelExpanded("preview")) setPreviewPanelVisible(true);
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

      // /api/me usually answers after the workbench is built; a permitted Terminal then joins the hidden rail.
      function applyTerminalAccess() {
        if (!lineageWorkbench || !terminalAllowed() || lineageWorkbench.descriptors.has("terminal")) return;
        if (!lineageEditorTypes.includes(terminalEditorType)) lineageEditorTypes.push(terminalEditorType);
        lineageWorkbench.setEditorTypes(lineageEditorTypes);
        lineageWorkbench.addPanel(terminalEditorDescriptor(), { hidden: true });
        terminalController = lineageWorkbench.getPanel("terminal") || null;
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
              if (!initialLineageReadyResolved) return;
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
              onCollapseRequest: () => lineageWorkbench?.collapsePanel(instanceId),
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

      function bindWatchboardSplitter() {
        const handle = chatElement("watchboardSplitter");
        if (!handle) return;
        if (handle.dataset.watchboardSplitterBound === "1") return;
        window.PortalWorkbench.layout.bindSplitter({
          handle,
          boundKey: "watchboardSplitterBound",
          disabled: () => window.matchMedia("(max-width: 1180px)").matches,
          direction: -1,
          min: () => watchboardWidthLimits().min,
          max: () => watchboardWidthLimits().max,
          readSize: () => state.layout.watchboard,
          applySize: width => {
            state.layout.watchboard = width;
            applyWatchboardLayout();
          },
          onDone: () => applyWatchboardLayout({ persist: true }),
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
      $id("residentRegisterForm").addEventListener("submit", submitResidentRegister);
      $id("residentRegisterClose").addEventListener("click", closeResidentRegisterModal);
      $id("residentRegisterCancel").addEventListener("click", closeResidentRegisterModal);
      $id("residentAttachClose").addEventListener("click", closeResidentAttachChoiceModal);
      $id("residentAttachOverlay").addEventListener("click", async event => {
        const target = event.target instanceof Element ? event.target : null;
        const forkCreateBtn = target?.closest("[data-fork-create-action]");
        if (forkCreateBtn) {
          await handleForkCreateAction(forkCreateBtn.dataset.forkCreateAction || "");
        }
      });
      $id("residentAttachNewButton").addEventListener("click", () => {
        const item = residentAttachChoiceItem();
        if (!item) return;
        closeResidentForkCreateModal({ keepAttachOpen: true });
        resetResidentAttachOptionState();
        state.residentAttachChoice = {
          open: false,
          lineageId: "",
          leaderResidentId: "",
          title: "",
        };
        renderResidentAttachChoiceModal();
        openAttachResidentModal(item);
      });
      $id("residentAttachForkDetails").addEventListener("toggle", event => {
        const details = event.currentTarget;
        if (!(details instanceof HTMLDetailsElement) || !details.open) return;
        restoreResidentRegisterForm();
        const item = residentAttachChoiceItem();
        const lineageId = lineageIdForResident(item);
        const leaderResidentId = String(
          state.residentAttachChoice?.leaderResidentId
          || lineageLeaderResidentId(item)
          || ""
        ).trim();
        if (!item || !lineageId || !leaderResidentId) {
          window.PortalUI.toast(
            window.PortalI18n.t("当前 Lineage 缺少可用于生成分支的 Leader Resident。"),
            "warning",
          );
          details.open = false;
          return;
        }
        const leader = state.residents.find(candidate => (
          String(candidate?.resident_id || "") === leaderResidentId
        )) || {
          resident_id: leaderResidentId,
          title: window.PortalI18n.t("Leader Resident"),
          lineage_id: lineageId,
        };
        openResidentForkCreateModal(leader, {
          intent: "attach",
          inline: true,
          lineageId,
        });
      });
      $id("residentAttachForkDetails").addEventListener("toggle", event => {
        const details = event.currentTarget;
        if (!(details instanceof HTMLDetailsElement) || details.open) return;
        if (state.residentForkCreateInlineHost === "attach") {
          closeResidentForkCreateModal({ keepAttachOpen: true });
        }
      });
      $id("residentEditForm").addEventListener("submit", submitResidentEditSurface);
      $id("residentEditClose").addEventListener("click", closeResidentEditModal);
      $id("residentEditCancel").addEventListener("click", closeResidentEditModal);
      $id("residentForkCreateOverlay").addEventListener("click", async event => {
        const target = event.target instanceof Element ? event.target : null;
        const actionBtn = target?.closest("[data-fork-create-action]");
        if (!actionBtn) return;
        await handleForkCreateAction(actionBtn.dataset.forkCreateAction || "");
      });
      $id("residentForkCreateClose").addEventListener("click", closeResidentForkCreateModal);
      $id("residentForkCreateCancel").addEventListener("click", closeResidentForkCreateModal);
      $id("residentForkAccountInput").addEventListener("change", event => {
        state.residentForkCreate.backendRouteId = String(
          event.target.value || ""
        ).trim();
        renderResidentForkAccount();
      });
      $id("residentForkProviderInput").addEventListener("change", event => {
        state.residentForkCreate.provider = event.target.value;
        state.residentForkCreate.backendRouteId = undefined;
        renderResidentForkAccount();
      });
      $id("crossLineageForkForm").addEventListener("submit", submitCrossLineageFork);
      $id("crossLineageForkClose").addEventListener("click", closeCrossLineageForkModal);
      $id("crossLineageForkCancel").addEventListener("click", closeCrossLineageForkModal);
      $id("crossLineageForkLineageSelect").addEventListener("change", event => {
        loadCrossLineageForkNodes(event.target.value || "");
      });
      $id("crossLineageForkResidentSelect").addEventListener("change", event => {
        state.crossLineageFork.residentId = String(event.target.value || "");
        renderCrossLineageForkModal();
      });
      $id("residentBackendSelect").addEventListener("change", syncResidentBackendRuntimeFields);
      $id("residentRouteSelect").addEventListener("change", () => {
        state.residentRouteChosen = true;
        syncResidentBackendRuntimeFields();
      });
      $id("residentRuntimeModelSelect").addEventListener("change", () => {
        syncResidentEffortSelect("residentRuntimeModelSelect", "residentRuntimeEffortSelect", $id("residentRuntimeEffortSelect")?.value || "");
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
          .catch(error => toast(error.message || String(error), "error"));
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
