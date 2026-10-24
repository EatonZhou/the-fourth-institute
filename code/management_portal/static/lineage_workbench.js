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
      const agentDialogControllers = new Map();
      let agentFloatingMenuOverlay = null;
      const agentDialogCloseHandlers = {
        residentRegisterOverlay: () => closeResidentRegisterModal(),
        residentAttachOverlay: () => closeResidentAttachChoiceModal(),
        residentForkCreateOverlay: () => closeResidentForkCreateModal(),
        crossLineageForkOverlay: () => closeCrossLineageForkModal(),
        residentEditOverlay: () => closeResidentEditModal(),
      };
      function agentDialogController(ref) {
        const element = typeof ref === "string" ? $id(ref) : ref;
        const id = String(element?.id || "").trim();
        if (!element || !id) return null;
        if (!agentDialogControllers.has(id)) {
          agentDialogControllers.set(id, window.PortalUI.createDialogController({
            modal: element,
            bodyClass: null,
            backdropClose: true,
            escapeClose: true,
            requestClose: event => agentDialogCloseHandlers[id]?.(event),
          }));
        }
        return agentDialogControllers.get(id);
      }
      function setAgentDialogOpen(ref, open) {
        const controller = agentDialogController(ref);
        if (!controller) return false;
        return open ? controller.open() : controller.close();
      }
      const esc = value => window.PortalUIPrimitives.escapeHtml(value);
      const consoleStateHtml = (message, options = {}) => window.PortalUIPrimitives.asyncStateHtml({
        state: options.state || "empty",
        title: options.title || "",
        message,
        density: options.density || "compact",
        icon: options.icon,
      });
      const renderMarkdown = (s, options = {}) => window.MD?.render ? window.MD.render(s || "", options) : esc(s || "");
      // Personal Chatroom settings (pins); membership itself lives on the server.
      const CHATROOM_UI_NAMESPACE = "lineage-workbench.chatrooms";
      const PERSONAL_AMA_UI_NAMESPACE = "lineage-workbench.personal-ama";
      let personalAssistantRead = 0;
      const PDFJS_WORKER_SRC = "/static/vendor/pdfjs/pdf.worker.min.js";
      if (window.pdfjsLib) window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_SRC;
      let whichResidentCorrectionSearchTimer = 0;
      const stateFactory = window.PortalLineageWorkbenchState;
      if (!stateFactory) throw new Error("PortalLineageWorkbenchState is not loaded.");
      const controllerFactory = window.PortalLineageWorkbenchController;
      if (!controllerFactory) throw new Error("PortalLineageWorkbenchController is not loaded.");
      const state = stateFactory.createState();
      let lineageWorkbench = null;
      let lineageEditorOpenCommands = null;
      let lineageActivityRail = null;
      let previewController = null;
      let previewAuxiliaryController = null;
      let previewTabsController = null;
      let previewEditorTools = null;
      let previewCodeEditor = null;
      let previewCodeEditorGeneration = 0;
      let agentPreviewSource = null;
      let previewSelectionController = null;
      const emptyPreviewSnapshot = Object.freeze({
        status: "empty",
        payload: null,
        requestId: 0,
        editing: false,
        draft: "",
        dirty: false,
        saving: false,
      });
      function activePreviewSnapshot() {
        return previewController?.snapshot?.() || emptyPreviewSnapshot;
      }
      function activePreviewPayload() {
        return activePreviewSnapshot().payload;
      }
      let agentChatWorkspace = null;
      let agentChatPanel = null;
      let residentSelectionRequest = 0;
      const chatElement = name => agentChatWorkspace?.element?.(name) || null;
      let whichResidentInputWatch = null;
      let whichResidentRetryTimer = 0;
      let chatSelectionContextController = null;
      let agentFilterSummary = null;
      let agentFilterDropdown = null;
      let chatroomMenuPopover = null;
      let chatroomMenuTarget = null;
      const chatroomOverlay = { host: null, view: null };
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
        loadFolder,
        residentService,
        selectedResidentId,
        submitChatPrompt: runSlashCommand,
        writeUiState: writeLineageUiState,
      });
      const residentSidebarRefreshCoordinator = window.PortalRefreshCoordinator.create({
        key: () => String(state.me || "").toLowerCase(),
        interval: 120000,
        initialDelay: () => 1000 + Math.floor(Math.random() * 1000),
        hiddenInterval: 300000,
        pauseWhenHidden: true,
        repairInterval: false,
        shouldRefresh: () => !!state.me,
        fetch: fetchResidentSidebarSnapshot,
        apply: applyResidentSidebarSnapshot,
        subscribe: subscribeResidentSidebar,
        onError: () => {},
      });
      function fmtDateTime(value) {
        return window.PortalUI.formatDateTime(value, { style: "compact" });
      }

      function normalizeProjectOptions(projects) {
        return [...new Map((projects || []).flatMap(project => {
          const id = String(project?.resource_id || "").trim();
          return id ? [[id, { ...project, id, name: String(project.name || id) }]] : [];
        })).values()];
      }

      function updateProjectOptions({ render = false } = {}) {
        const projects = normalizeProjectOptions(state.registryProjects);
        const signature = JSON.stringify(projects);
        if (signature === state.projectOptionsSignature) return false;
        state.projectOptionsSignature = signature;
        state.projects = projects;
        refreshOpenResidentRegistrationOptions();
        if (render) {
          renderAgentList();
          renderFavoriteList();
          renderActivityRecentRail();
        }
        return true;
      }

      function residentSearchText(item) {
        return [
          item?.title,
          item?.label,
          item?.name,
          item?.resident_id,
          item?.agent_id,
          item?.project_id,
          item?.project,
          item?.kind,
          item?.backend,
          item?.workspace_path,
          item?.created_by,
        ].join(" ").toLowerCase();
      }

      function lineageLeaderResidentId(item) {
        return String(
          item?.lineage_leader_resident_id
          || item?.lineage?.leader_resident_id
          || ""
        ).trim();
      }

      function isLineageLeaderResident(item) {
        const residentId = String(item?.resident_id || "").trim();
        return item?.is_lineage_leader === true
          || (!!residentId && residentId === lineageLeaderResidentId(item));
      }

      function residentTimeValue(item) {
        return recentTimeMs(item?.last_interaction_at, item?.updated_at, item?.created_at);
      }

      function sortLineageResidents(items) {
        return [...(items || [])].sort((left, right) => {
          const leftLeader = isLineageLeaderResident(left) ? 1 : 0;
          const rightLeader = isLineageLeaderResident(right) ? 1 : 0;
          if (leftLeader !== rightLeader) return rightLeader - leftLeader;
          return residentTimeValue(right) - residentTimeValue(left);
        });
      }

      function lineageSidebarRoots(_kind = "", { respectFilters = true } = {}) {
        const groups = new Map();
        for (const item of state.residents || []) {
          const lineageId = lineageIdForResident(item) || String(item.resident_id || "");
          if (!lineageId) continue;
          if (!groups.has(lineageId)) groups.set(lineageId, []);
          groups.get(lineageId).push(item);
        }
        const roots = [];
        const lineageCards = Array.isArray(state.lineages) ? state.lineages : [];
        for (const lineage of lineageCards) {
          const lineageId = String(lineage?.lineage_id || "").trim();
          if (!lineageId) continue;
          const allMembers = sortLineageResidents(groups.get(lineageId) || []);
          const projectId = String(lineage?.project_id || "").trim();
          if (
            respectFilters
            && state.projectFilter !== "all"
            && state.projectFilter !== projectId
          ) continue;
          const query = state.query.trim().toLowerCase();
          const visibleMembers = respectFilters
            ? allMembers.filter(item => !query || residentSearchText(item).includes(query))
            : allMembers;
          const lineageMatches = !query
            || [lineage?.title, lineage?.description, lineageId]
              .some(value => String(value || "").toLowerCase().includes(query));
          if (respectFilters && !visibleMembers.length && !lineageMatches) continue;
          const leaderId = String(lineage?.leader_resident_id || "").trim();
          const carrier = allMembers.find(item => String(item?.resident_id || "") === leaderId)
            || allMembers[0]
            || {};
          const root = {
            ...carrier,
            resident_id: String(carrier?.resident_id || leaderId || `lineage:${lineageId}`),
            lineage_id: lineageId,
            kind: "lineage",
            project_id: projectId,
            created_at: String(lineage?.created_at || carrier?.created_at || ""),
            updated_at: String(lineage?.updated_at || carrier?.updated_at || ""),
            lineage_leader_resident_id: leaderId,
            created_by: String(lineage?.created_by || ""),
            lineage_node_ids: Array.isArray(lineage?.lineage_node_ids) ? lineage.lineage_node_ids : [],
            lineage: {
              lineage_id: lineageId,
              title: String(lineage?.title || lineageId),
              project_id: String(lineage?.project_id || ""),
              leader_resident_id: leaderId,
              created_by: String(lineage?.created_by || ""),
              lineage_node_ids: Array.isArray(lineage?.lineage_node_ids) ? lineage.lineage_node_ids : [],
            },
            _lineageSidebarResidents: allMembers,
            _lineageSidebarCarriers: allMembers,
            _lineageSidebarResidentCount: Number(lineage?.resident_count || allMembers.length),
            _lineageSidebarMatchedResidentIds: new Set(visibleMembers.map(item => String(item?.resident_id || ""))),
          };
          roots.push(root);
        }
        return roots.sort((left, right) => {
          const leftMembers = left._lineageSidebarResidents || [];
          const rightMembers = right._lineageSidebarResidents || [];
          const leftTime = leftMembers.length
            ? Math.max(...leftMembers.map(residentTimeValue))
            : residentTimeValue(left);
          const rightTime = rightMembers.length
            ? Math.max(...rightMembers.map(residentTimeValue))
            : residentTimeValue(right);
          return rightTime - leftTime;
        });
      }

      function lineageSidebarRootByResidentId(residentId) {
        const id = String(residentId || "").trim();
        if (!id) return null;
        const roots = lineageSidebarRoots("", { respectFilters: false });
        // Membership determines navigation; another graph may also reference
        // this Resident. Sidebar sort order must not make that graph the owner.
        return roots.find(item => lineageSidebarResidentsForItem(item).some(member => (
          String(member?.resident_id || "").trim() === id
        )))
          || state.residents.find(item => String(item?.resident_id || "").trim() === id)
          || roots.find(item => (
            (Array.isArray(item?.lineage_node_ids) && item.lineage_node_ids.some(nodeId => (
              String(nodeId || "").trim() === id
            )))
            || (Array.isArray(item?.lineage?.lineage_node_ids) && item.lineage.lineage_node_ids.some(nodeId => (
              String(nodeId || "").trim() === id
            )))
          ))
          || null;
      }

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

      function projectFilterOptions() {
        const seen = new Map();
        for (const lineage of state.lineages || []) {
          const value = String(lineage?.project_id || "").trim();
          if (!value || seen.has(value)) continue;
          const project = projectOptionById(value);
          seen.set(value, project?.name || value);
        }
        for (const item of state.residents) {
          if (lineageIdForResident(item)) continue;
          const value = item.project_id || item.project || "";
          if (!value || seen.has(value)) continue;
          seen.set(value, item.project || value);
        }
        return [...seen.entries()]
          .map(([value, label]) => ({ value, label }))
          .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
      }

      function normalizedProjectDisplayFilter(filter) {
        return ["all", "non-empty"].includes(filter) ? filter : "non-empty";
      }

      function renderProjectFilterOptions() {
        const options = projectFilterOptions();
        if (state.projectFilter !== "all" && !options.some(item => item.value === state.projectFilter)) {
          state.projectFilter = "all";
        }
        state.projectDisplayFilter = normalizedProjectDisplayFilter(state.projectDisplayFilter);
        const projectOptions = [
          { value: "all", label: window.PortalI18n.t("全部项目") },
          ...options,
        ].map(item => window.PortalI18n.html`
          <button class="filterOption" type="button" data-project-filter="${esc(item.value)}" title="${esc(item.label)}">${esc(item.label)}</button>
        `).join("");
        const displayOptions = [
          { value: "non-empty", label: window.PortalI18n.t("有内容") },
          { value: "all", label: window.PortalI18n.t("全部") },
        ].map(item => window.PortalI18n.html`
          <button class="filterOption" type="button" data-project-display-filter="${esc(item.value)}" title="${esc(item.label)}">${esc(item.label)}</button>
        `).join("");
        $id("agentFilterPanel").innerHTML = window.PortalI18n.html`
          <div class="filterSection">
            <div class="filterLabel">显示范围</div>
            ${displayOptions}
          </div>
          <div class="filterSection">
            <div class="filterLabel">项目</div>
            ${projectOptions}
          </div>`;
      }

      function syncAgentTools() {
        const searchWrap = $id("agentSearchWrap");
        const searchBtn = $id("searchToggleBtn");
        const filterBtn = $id("filterToggleBtn");
        const filterPanel = $id("agentFilterPanel");
        const queryActive = !!state.query.trim();
        searchWrap.classList.toggle("open", state.searchOpen);
        searchBtn.classList.toggle("is-active", state.searchOpen || queryActive);
        searchBtn.setAttribute("aria-expanded", state.searchOpen ? "true" : "false");
        filterBtn.classList.toggle("is-active", state.filterOpen || state.projectFilter !== "all" || state.projectDisplayFilter !== "non-empty");
        filterBtn.setAttribute("aria-expanded", state.filterOpen ? "true" : "false");
        filterPanel.classList.toggle("open", state.filterOpen);
        filterPanel.setAttribute("aria-hidden", state.filterOpen ? "false" : "true");
        document.querySelectorAll("[data-project-filter]").forEach(btn => {
          btn.classList.toggle("active", btn.dataset.projectFilter === state.projectFilter);
        });
        document.querySelectorAll("[data-project-display-filter]").forEach(btn => {
          btn.classList.toggle("active", btn.dataset.projectDisplayFilter === state.projectDisplayFilter);
        });
        agentFilterSummary?.sync?.();
      }

      function setSearchOpen(open) {
        state.searchOpen = !!open;
        writeLineageUiState({ searchOpen: state.searchOpen });
        syncAgentTools();
        if (state.searchOpen) {
          window.setTimeout(() => $id("searchInput").focus(), 0);
        }
      }

      function setProjectFilter(filter) {
        state.projectFilter = filter || "all";
        agentFilterDropdown?.close();
        writeLineageUiState({ projectFilter: state.projectFilter });
        renderAgentList();
      }

      function setProjectDisplayFilter(filter) {
        state.projectDisplayFilter = normalizedProjectDisplayFilter(filter);
        agentFilterDropdown?.close();
        writeLineageUiState({ projectDisplayFilter: state.projectDisplayFilter });
        renderAgentList();
      }

      function projectMetaForAgent(item) {
        const projectId = String(item?.project_id || item?.project || "").trim();
        if (!projectId) return null;
        const project = projectOptionById(projectId);
        const label = String(project?.name || item?.project || project?.id || projectId).trim();
        return {
          id: String(project?.id || projectId).trim(),
          label,
        };
      }

      function residentGroups() {
        const projectGroups = window.PortalPrincipalNavigation.projectGroups(
          state.principalHierarchy,
          lineageSidebarRoots("", { respectFilters: false }),
          {
            query: state.query,
            projectFilter: state.projectFilter, nonEmptyOnly: state.projectDisplayFilter === "non-empty",
          },
        );
        return [{ id: "lineage", title: window.PortalI18n.t("My Projects"), items: [], itemType: "resident", projectGroups,
          empty: residentSidebarPendingText("Project") || (state.query ? "没有匹配的 Project。" : "暂无可访问的 Project。") }];
      }

      // Until the first ready sidebar snapshot arrives nothing is known about
      // Projects or Which Resident: say it is being read (or failed), never
      // that there is none or the service is down.
      function residentSidebarPendingText(subject) {
        if (state.residentSidebarReady) return "";
        return state.residentSidebarError ? `${subject} 读取失败：${state.residentSidebarError}` : `正在读取 ${subject}…`;
      }

      function isPersonalAmaResident(item) {
        return !!state.personalAmaResidentId
          && String(item?.resident_id || "").trim() === state.personalAmaResidentId;
      }

      function lineageContainsPersonalAmaLeader(item) {
        return lineageSidebarResidentsForItem(item).some(member => isPersonalAmaResident(member));
      }

      function renderResidentGroup(group) {
        const queryActive = !!state.query.trim();
        const open = queryActive || state.residentGroups[group.id] !== false;
        const hasProjectGroups = Array.isArray(group.projectGroups) && group.projectGroups.length;
        const bodyHtml = hasProjectGroups
          ? renderProjectSubgroups(group)
          : group.items.length
            ? group.items.map(renderRegisteredResidentItem).join("")
            : window.PortalI18n.html`<div class="residentEmpty">${esc(group.empty)}</div>`;
        return window.PortalI18n.html`<section class="residentSection ${open ? "open" : "collapsed"}" data-resident-group="${esc(group.id)}">
          <div class="residentGroupHead">
            <button class="residentToggle" type="button" data-resident-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" aria-label="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}" data-portal-icon="${open ? "chevron-up" : "chevron-down"}" data-portal-icon-class="residentChevron"></button>
            <button class="residentGroupName" type="button" data-resident-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}">${esc(group.title)}</button>
            ${group.id === "lineage" ? window.PortalI18n.html`<button class="residentAddBtn" type="button" data-project-create aria-label="创建 Project" title="创建 Project" data-portal-icon="plus"></button>` : ""}
          </div>
          ${open ? window.PortalI18n.html`<div class="residentGroupBody">
            ${bodyHtml}
          </div>` : ""}
        </section>`;
      }

      function projectFavoriteSelectionId(project) { return `project:${project.project_id}`; }

      function favoriteStar(selectionId) {
        const favorite = isFavoriteAgent(selectionId);
        return window.PortalI18n.html`<button type="button" class="agentFavoriteBtn ${favorite ? "active" : ""}" data-agent-favorite="${esc(selectionId)}" aria-pressed="${favorite}" aria-label="${favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("添加到收藏")}" title="${favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("添加到收藏")}" data-portal-icon="${favorite ? "star-filled" : "star"}"></button>`;
      }

      function projectCardHtml(project) {
        const open = !!state.query.trim() || state.residentProjectGroups[project.id] === true;
        const accessible = project.accessible !== false;
        const denied = !accessible && !project.can_initialize;
        const canOpen = accessible && !!project.resident_id;
        const menuId = `project:${project.project_id}`;
        return window.PortalI18n.html`<section class="residentSubgroup principalProject" data-resident-project-group="${esc(project.id)}">
          <div class="agentItem principalProjectCard ${denied ? "locked" : ""} ${project.resident_id && state.chatTarget?.id === project.resident_id ? "active" : ""}">
            <button type="button" class="agentSelect noStatusDot" data-resident-project-toggle="${esc(project.id)}" aria-expanded="${open}" ${project.unavailable ? "disabled" : ""}>
              <span class="principalProjectChevron" data-portal-icon="${open ? "chevron-down" : "chevron-right"}"></span>
              <span class="agentMain"><span class="agentTitle">${esc(project.title)}</span><span class="agentMeta"><span class="principalKind">Project</span><span class="principalMetaDetail" title="${project.unavailable ? window.PortalI18n.t("无访问权限") : window.PortalI18n.text`共 ${project.items.length} 个 Lineage`}" ${project.unavailable ? "" : window.PortalI18n.text`aria-label="共 ${project.items.length} 个 Lineage"`}>${project.unavailable ? window.PortalI18n.t("无访问权限") : window.PortalI18n.text`${project.items.length} 组`}</span></span></span>
            </button>
            ${!denied ? "" : window.PortalI18n.t('<span class="principalAccessLock" data-portal-icon="lock" aria-label="无访问权限" title="当前无权打开 Project 对话"></span>')}
            ${favoriteStar(project.favoriteSelectionId || projectFavoriteSelectionId(project))}
            <button type="button" class="principalCreateBtn" data-project-lineage-create="${esc(project.project_id)}" ${project.unavailable || project.project_readable === false ? "disabled" : ""} aria-label="在 ${esc(project.title)} 中添加 Lineage" title="添加 Lineage" data-portal-icon="plus"></button>
            <button type="button" class="principalOpenBtn" data-principal-open="${esc(project.resident_id || "")}" ${canOpen ? "" : "disabled"} aria-label="打开 ${esc(project.title)} 对话和 HATG" title="打开 Project 对话和 HATG" data-portal-icon="arrow-right"></button>
            <button type="button" class="agentMenuBtn" data-agent-menu="${esc(menuId)}" ${project.unavailable || project.project_readable === false ? "disabled" : ""} aria-label="${esc(project.title)} 菜单" data-portal-icon="more-horizontal"></button>
          </div>
          ${open && !project.unavailable ? window.PortalI18n.html`<div class="residentSubgroupBody">${project.items.map(item => renderRegisteredResidentItem(item, { asLineage: true })).join("") || window.PortalI18n.t('<div class="residentEmpty">暂无可访问的 Lineage。</div>')}</div>` : ""}
        </section>`;
      }

      function renderProjectSubgroups(group) {
        return window.PortalI18n.html`<div class="residentSubgroups">${(group.projectGroups || []).map(projectCardHtml).join("")}</div>`;
      }

      function residentSidebarSignature(residents) {
        return JSON.stringify([
          (Array.isArray(state.lineages) ? state.lineages : []).map(item => [
            item?.lineage_id || "",
            item?.title || "",
            item?.description || "",
            item?.project_id || "",
            item?.leader_resident_id || "",
            item?.allows || {},
            String(item?.created_by || ""),
            Array.isArray(item?.lineage_node_ids) ? item.lineage_node_ids : [],
            item?.resident_count || 0,
          ]),
          (Array.isArray(residents) ? residents : []).map(item => [
          item?.resident_id || "",
          item?.title || "",
          item?.lineage_id || "",
          String(item?.created_by || ""),
          item?.last_token_usage_at || "",
          item?.last_user_actor || "",
          item?.unread_signature || null,
          item?.conversation_signature || "",
        ]),
        ]);
      }

      // The navigation lists every Lineage and Resident the user can access;
      // favorites only add the "我的收藏" view.
      function applyResidentSidebarResidents(residents, lineages = []) {
        const uniqueResidents = new Map();
        for (const item of Array.isArray(residents) ? residents : []) {
          const residentId = String(item?.resident_id || "");
          if (!residentId) continue;
          uniqueResidents.set(residentId, { ...uniqueResidents.get(residentId), ...item });
        }
        const nextResidents = [...uniqueResidents.values()];
        const nextLineages = Array.isArray(lineages) ? lineages : [];
        const nextSignature = JSON.stringify([residentSidebarSignature(nextResidents), nextLineages]);
        const changed = nextSignature !== state.residentSidebarSignature;
        state.residentSidebarSignature = nextSignature;
        if (!changed) return false;
        state.lineages = nextLineages;
        state.residents = nextResidents;
        renderAgentList();
        renderFavoriteList();
        renderActivityRecentRail();
        return true;
      }

      async function fetchResidentSidebarSnapshot(options = {}) {
        return window.PortalLineageCatalogs.residents({
          values: {},
          force: options.force === true,
          load: values => residentService.list(values),
        });
      }

      function applyResidentSidebarSnapshotNow(payload) {
        const data = payload && typeof payload === "object" ? payload : {};
        state.residentSidebarReady = data.ready !== false;
        state.residentSidebarError = String(data.error || "");
        if (data.account_selection
          && JSON.stringify(data.account_selection) !== JSON.stringify(state.accountSelection)) {
          state.accountSelection = data.account_selection;
          refreshOpenResidentRegistrationOptions();
        }
        if (!state.residentSidebarReady) return false;

        const hierarchyChanged = JSON.stringify(state.principalHierarchy) !== JSON.stringify(data.principal_hierarchy);
        state.principalHierarchy = data.principal_hierarchy || { projects: [], user_residents: [] };
        if (Object.prototype.hasOwnProperty.call(data, "recommendation_resident")) {
          state.recommendationResident = data.recommendation_resident || null;
          state.recommendationStatus = data.recommendation_status || "unavailable";
        }
        state.sidebarNavigationResidents = data.residents || [];
        const changed = applyResidentSidebarResidents(
          [...state.sidebarNavigationResidents, ...state.sidebarShortcutResidents],
          data.lineages || [],
        );
        if (hierarchyChanged && !changed) renderAgentList();
        return changed || hierarchyChanged;
      }

      function applyResidentSidebarShortcuts(data) {
        state.sidebarShortcutResidents = Array.isArray(data?.residents) ? data.residents : [];
        if (data?.account_selection
          && JSON.stringify(data.account_selection) !== JSON.stringify(state.accountSelection)) {
          state.accountSelection = data.account_selection;
          refreshOpenResidentRegistrationOptions();
        }
        const recommendationChanged = JSON.stringify(state.recommendationResident) !== JSON.stringify(data?.recommendation_resident || null)
          || state.recommendationStatus !== (data?.recommendation_status || "unavailable");
        state.recommendationResident = data?.recommendation_resident || null;
        state.recommendationStatus = data?.recommendation_status || "unavailable";
        applyResidentSidebarResidents(
          [...state.sidebarNavigationResidents, ...state.sidebarShortcutResidents],
          state.lineages,
        );
        if (recommendationChanged) {
          renderAgentList();
          agentChatPanel?.refreshView?.();
        }
      }

      function applyResidentSidebarSnapshot(payload) {
        if (state.residentSidebarMutationDepth > 0) {
          state.residentSidebarDeferredSnapshot = payload;
          return false;
        }
        return applyResidentSidebarSnapshotNow(payload);
      }

      async function waitForResidentSidebarProjection(initialPayload, loadSeq) {
        let payload = initialPayload;
        while (loadSeq === state.loadAllSeq) {
          applyResidentSidebarSnapshot(payload);
          if (state.residentSidebarReady) return true;
          const retryAfterMs = Math.max(
            250,
            Math.min(2000, Number(payload?.retry_after_ms) || 500),
          );
          await new Promise(resolve => window.setTimeout(resolve, retryAfterMs));
          if (loadSeq !== state.loadAllSeq) return false;
          payload = await fetchResidentSidebarSnapshot({ force: true });
        }
        return false;
      }

      function beginResidentSidebarMutation() {
        state.residentSidebarMutationDepth += 1;
      }

      function endResidentSidebarMutation(options = {}) {
        state.residentSidebarMutationDepth = Math.max(0, state.residentSidebarMutationDepth - 1);
        if (state.residentSidebarMutationDepth > 0) return false;
        const pending = state.residentSidebarDeferredSnapshot;
        state.residentSidebarDeferredSnapshot = null;
        if (!pending) return false;
        const preservedResidents = Array.isArray(options.preserveResidents)
          ? options.preserveResidents.filter(item => item?.resident_id)
          : [];
        if (!preservedResidents.length) return applyResidentSidebarSnapshotNow(pending);
        const data = pending && typeof pending === "object" ? pending : {};
        const pendingResidents = Array.isArray(data.residents) ? data.residents : [];
        const pendingIds = new Set(pendingResidents.map(item => String(item?.resident_id || "")));
        return applyResidentSidebarSnapshotNow({
          ...data,
          residents: [
            ...preservedResidents.filter(item => !pendingIds.has(String(item.resident_id || ""))),
            ...pendingResidents,
          ],
        });
      }

      function subscribeResidentSidebar(callbacks) {
        const subscription = window.PortalRefreshCoordinator.portalSubscription({
          domains: ["projection"],
          matches: detail => {
            if (detail?.snapshot_required) return true;
            return Object.prototype.hasOwnProperty.call(
              detail?.projection_changes?.["resident-sidebar"] || {},
              "snapshot",
            );
          },
        });
        return subscription(callbacks);
      }





      function renderRegisteredResidentItem(item, options = {}) {
        const selected = residentSelectionId(item);
        const residentId = String(item.resident_id || "");
        const isPersonalAma = options.asResident === true || (options.asLineage !== true && isPersonalAmaResident(item));
        const favoriteSelectionId = String(options.favoriteSelectionId || lineageFavoriteSelectionId(item) || selected);
        const favorite = isFavoriteLineageSidebarItem(item);
        const lineageId = lineageIdForResident(item) || residentId;
        const menuId = isPersonalAma ? selected : `lineage:${lineageId}`;
        const listedCount = new Set(
          lineageSidebarResidentsForItem(item).map(value => String(value?.resident_id || "").trim()).filter(Boolean),
        ).size;
        const memberCount = Math.max(listedCount, Number(item._lineageSidebarResidentCount || 0));
        const memberLabel = window.PortalI18n.text`${memberCount} 个 Resident`;
        const selectedResident = selectedResidentId();
        const active = isPersonalAma
          ? selected && selected === activeChatTargetId()
          : (item?.lineage_node_ids || item?.lineage?.lineage_node_ids || []).includes(selectedResident);
        const showMenu = true;
        const menuOpen = showMenu && menuId === state.agentMenuId;
        const titleIcons = "";
        const title = esc(
          isPersonalAma
            ? (item.title || item.resident_id || window.PortalI18n.t("Ask Me Anything"))
            : (item.lineage?.title || item.lineage_title || item.title || lineageId)
        );
        const titleHtml = titleIcons
          ? window.PortalI18n.html`<span class="agentTitle withIcon"><span class="agentTitleText">${title}</span>${titleIcons}</span>`
          : window.PortalI18n.html`<span class="agentTitle">${title}</span>`;
        return window.PortalI18n.html`<div class="residentCardWrap" data-resident-card="${esc(residentId)}" data-lineage-card="${esc(lineageId)}">
        <div class="agentItem ${isPersonalAma ? "" : "principalLineageCard"} ${active ? "active" : ""} ${menuOpen ? "menuOpen" : ""}" data-resident-id="${esc(residentId)}">
          <button type="button" class="agentSelect noStatusDot" ${isPersonalAma ? `data-resident-select="${esc(residentId)}"` : `data-principal-open="${esc(lineageLeaderResidentId(item))}" data-lineage-open="${esc(lineageId)}"`} title="${esc(isPersonalAma ? (item.workspace_path || "") : lineageId)}">
            <span class="agentMain">
              ${titleHtml}
              <span class="agentMeta">${isPersonalAma ? `<span class="principalKind">Resident</span>` : window.PortalI18n.html`<span class="principalKind">Lineage</span><span class="principalMetaDetail" title="${esc(memberLabel)}" aria-label="${esc(memberLabel)}">${memberCount}</span>`}</span>
            </span>
          </button>
          ${isPersonalAma ? "" : window.PortalI18n.html`<button type="button" class="agentFavoriteBtn ${favorite ? "active" : ""}" data-agent-favorite="${esc(favoriteSelectionId)}" aria-pressed="${favorite ? "true" : "false"}" aria-label="${favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("添加到收藏")}" title="${favorite ? window.PortalI18n.t("取消收藏") : window.PortalI18n.t("添加到收藏")}" data-portal-icon="${favorite ? "star-filled" : "star"}"></button>`}
          ${showMenu ? window.PortalI18n.html`<button type="button" class="agentMenuBtn ${menuOpen ? "active" : ""}" data-agent-menu="${esc(menuId)}" aria-label="${isPersonalAma ? "Resident" : "Lineage"} 菜单" title="更多" data-portal-icon="more-horizontal"></button>` : ""}
        </div>
        </div>`;
      }






      function derivedResidentForSidebar(residentId, derivedResidentId) {
        return state.residents.find(item => residentExactId(item) === String(derivedResidentId || "")) || null;
      }

      const RECENT_AGENT_SESSION_LIMIT = 32;
      const RECENT_AGENT_SESSION_VISIBLE_LIMIT = 8;

      function recentAgentSessionKey(entry) {
        const residentId = String(entry?.resident_id || "").trim();
        return residentId ? `resident:${residentId}` : "";
      }

      function normalizeRecentAgentSession(raw) {
        if (!raw || typeof raw !== "object") return null;
        if (String(raw.type || "resident") !== "resident") return null;
        const t = Number(raw.t || 0) || Date.now();
        const residentId = String(raw.resident_id || "").trim();
        if (!residentId) return null;
        return {
          type: "resident", resident_id: residentId, t,
          title: String(raw.title || "").trim(),
          lineage_id: String(raw.lineage_id || "").trim(),
        };
      }

      function rememberRecentAgentSession(entry) {
        const normalized = normalizeRecentAgentSession({ ...entry, t: Date.now() });
        if (!normalized) return;
        const key = recentAgentSessionKey(normalized);
        window.PortalConversationStore?.remember?.({ ...normalized, key });
      }

      function dismissRecentAgentSession(key) {
        const cleanKey = String(key || "").trim();
        if (!cleanKey) return false;
        const label = recentAgentSessionInfos().find(item => item.key === cleanKey)?.label || window.PortalI18n.t("快捷入口");
        const removed = window.PortalConversationStore?.dismissRecent?.(cleanKey) === true;
        if (removed) {
          window.PortalUI?.toast?.(`已从快捷栏移除“${label}”。`, "success", {
            key: "lineage-recent-dismissed",
            duration: 2600,
          });
        }
        return removed;
      }

      function clearRecentAgentSessions() {
        const removed = window.PortalConversationStore?.clearRecent?.() || [];
        if (!removed.length) return false;
        window.PortalUI?.toast?.(`已清空 ${removed.length} 个最近快捷入口。`, "success", {
          key: "lineage-recent-cleared",
          duration: 6000,
          retryLabel: "撤销",
          retry: () => {
            if (!window.PortalConversationStore?.restoreRecent?.(removed)) return;
            window.PortalUI?.toast?.("已恢复最近快捷入口。", "success", {
              key: "lineage-recent-restored",
              duration: 2600,
            });
          },
        });
        return true;
      }

      function rememberResidentShortcutSession(residentId, target = {}) {
        rememberRecentAgentSession({ ...target, type: "resident", resident_id: residentId });
      }

      function recentTimeMs(...values) {
        for (const value of values) {
          if (Number.isFinite(Number(value)) && Number(value) > 0) return Number(value);
          const parsed = Date.parse(String(value || ""));
          if (Number.isFinite(parsed) && parsed > 0) return parsed;
        }
        return 0;
      }

      function recentTitleGlyph(label) {
        const text = String(label || "").trim();
        const characters = Array.from(text);
        const firstMeaningful = characters.find(character => (
          /[A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/u.test(character)
        ));
        if (/^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]$/u.test(firstMeaningful || "")) {
          return firstMeaningful;
        }
        const englishWords = text.match(/[A-Za-z]+/g) || [];
        if (englishWords.length >= 2) {
          return `${englishWords[0][0]}${englishWords[1][0]}`.toUpperCase();
        }
        if (englishWords.length === 1) {
          return englishWords[0].slice(0, 2);
        }
        return characters.slice(0, 2).join("").toUpperCase() || "?";
      }

      function recentAccentHue(key) {
        let hash = 0;
        for (const character of String(key || "")) {
          hash = ((hash * 31) + character.codePointAt(0)) >>> 0;
        }
        return hash % 360;
      }

      function recentIdentity(item) {
        return String(item?.residentId || "").trim();
      }

      function recentInfoForEntry(entry) {
        const residentId = String(entry?.resident_id || "").trim();
        if (!residentId) return null;
        // Navigation is a display projection, not the authority for a recent
        // conversation's existence. Keep acknowledged targets reachable even
        // when hidden from Navigation; opening still checks current access.
        const resident = residentRootForExactId(residentId)
          || (residentExactId(state.detail?.resident) === residentId ? state.detail.resident : null)
          || { resident_id: residentId, title: entry.title, lineage_id: entry.lineage_id };
        const label = String(resident.title || resident.resident_id || "Resident").trim();
        const active = selectedResidentId() === residentId;
        return {
          key: recentAgentSessionKey(entry),
          type: "resident",
          residentId,
          lineageId: registeredLineageIdForResident(resident) || String(entry.lineage_id || "").trim(),
          derived: false,
          label,
          subtitle: "Resident",
          glyph: recentTitleGlyph(label || "R"),
          active,
          t: Number(entry.t || 0) || recentTimeMs(resident?.updated_at, resident?.created_at),
        };
      }

      function recentAgentSessionInfos() {
        const byKey = new Map();
        for (const entry of window.PortalConversationStore?.recent?.() || []) {
          const key = recentAgentSessionKey(entry);
          if (key) byKey.set(key, entry);
        }
        return [...byKey.values()]
          .map(recentInfoForEntry)
          .filter(Boolean)
          .sort((a, b) => Number(b.t || 0) - Number(a.t || 0))
          .slice(0, RECENT_AGENT_SESSION_LIMIT);
      }

      function initLineageActivityRail() {
        lineageActivityRail?.destroy?.();
        lineageActivityRail = window.PortalWorkbench.createActivityRail({
          id: "lineage",
          root: "#lineageActivityRail",
          label: window.PortalI18n.t("Society 工作台视图"),
          itemsId: "activityRecentRail",
          itemsLabel: window.PortalI18n.t("最近发出命令的 Society 工作台会话"),
          activeView: state.sideView || "agents",
          views: [
            { id: "agents", icon: "users", label: "Agent" },
            { id: "chatrooms", icon: "chat-room", label: "Chatroom" },
            { id: "favorites", icon: "star", label: window.PortalI18n.t("我的收藏") },
            { id: "workspaces", icon: "folder", label: "Workspace", title: window.PortalI18n.t("查看当前对话的工作区、整理个人文件夹，并在 Preview 中浏览文件") },
          ],
          itemLimit: RECENT_AGENT_SESSION_VISIBLE_LIMIT,
          itemFallbackView: "agents",
          onViewSelect: view => setSideView(view, { userInitiated: true }),
          onItemDismiss: key => dismissRecentAgentSession(key),
          itemsAction: {
            icon: "trash",
            label: window.PortalI18n.t("清空最近 Resident 快捷入口"),
          },
          onItemsAction: () => clearRecentAgentSessions(),
          onItemSelect: async key => {
            const item = recentAgentSessionInfos().find(candidate => candidate.key === key);
            if (!item) return;
            if (item.residentId) {
              await selectResident(item.residentId, { lineageId: item.lineageId, forceView: true });
              focusPreferredSidebarTarget({ residentId: item.residentId });
            }
          },
        });
        renderActivityRecentRail();
        return lineageActivityRail;
      }

      function renderActivityRecentRail() {
        if (!lineageActivityRail) return;
        const items = recentAgentSessionInfos().slice(0, RECENT_AGENT_SESSION_VISIBLE_LIMIT);
        const glyphCounts = new Map();
        for (const item of items) glyphCounts.set(item.glyph, Number(glyphCounts.get(item.glyph) || 0) + 1);
        lineageActivityRail.setItems(items.map(item => ({
          ...(() => {
            const identity = recentIdentity(item);
            const suffix = identity.slice(-4).toUpperCase();
            const timeLabel = item.t ? fmtDateTime(new Date(Number(item.t || 0)).toISOString()) : "";
            const duplicateGlyph = Number(glyphCounts.get(item.glyph) || 0) > 1;
            return {
              key: item.key,
              kind: "resident",
              mark: duplicateGlyph ? suffix.slice(-2) : item.derived ? "D" : "",
              label: item.label,
              glyph: item.glyph,
              active: item.active,
              accentHue: recentAccentHue(identity),
              ariaLabel: [item.label, suffix ? window.PortalI18n.text`Resident 尾号 ${suffix}` : ""].filter(Boolean).join("，"),
              ariaDescription: timeLabel ? `最近使用 ${timeLabel}` : "",
              title: [item.label, item.subtitle, suffix ? window.PortalI18n.text`ID 尾号 ${suffix}` : "", timeLabel ? window.PortalI18n.text`最近使用 ${timeLabel}` : ""].filter(Boolean).join("\n"),
            };
          })(),
        })));
      }

      function lineageSidebarResidentsForItem(item) {
        if (Array.isArray(item?._lineageSidebarResidents)) return item._lineageSidebarResidents;
        return [];
      }


      function residentMemberMenuKey(residentId, derivedResidentId) {
        return `${residentId || ""}:${derivedResidentId || ""}`;
      }

      const RESIDENT_CARD_MENU_ACTIONS = [
        { id: "backpack", label: window.PortalI18n.t("Menu"), permission: "canRead", title: window.PortalI18n.t("查看此 Resident 的 Menu") },
        { id: "edit", label: window.PortalI18n.t("编辑"), permission: "canEdit", title: window.PortalI18n.t("编辑此 Resident") },
        { id: "delete", label: window.PortalI18n.t("删除"), permission: "canDelete", title: window.PortalI18n.t("删除此 Resident"), danger: true },
      ];

      function editorOpenMenuActionsHtml({ attributes = () => "" } = {}) {
        return window.PortalEditorOpenCommands.actionsHtml({
          className: "agentCardAction",
          attributes: command => attributes(command.id),
        });
      }

      function residentCardMenuActionsHtml({
        canEdit = false,
        canDelete = false,
        actionName = action => action,
        attributes = () => "",
      } = {}) {
        const permissions = { canRead: true, canEdit, canDelete };
        return RESIDENT_CARD_MENU_ACTIONS
          .filter(action => permissions[action.permission])
          .map(action => window.PortalI18n.html`<button class="agentCardAction ${action.danger ? "danger" : ""}" type="button" ${attributes(action.id)} data-resident-menu-action="${esc(actionName(action.id))}" title="${esc(action.title)}">${esc(action.label)}</button>`)
          .join("");
      }

      function renderResidentMemberMenu(residentId, derivedResident) {
        const derivedResidentId = residentExactId(derivedResident);
        const openActions = editorOpenMenuActionsHtml({
          attributes: action => `data-resident-member-action="${esc(action)}" data-resident-id="${esc(residentId)}" data-member-resident-id="${esc(derivedResidentId)}"`,
        });
        const manageActions = residentCardMenuActionsHtml({
          canEdit: lineageAllows(derivedResident?.lineage_id).UpdateResident === true,
          canDelete: residentAllows(derivedResident).DeleteResident === true,
          actionName: action => action,
          attributes: action => `data-resident-member-action="${esc(action)}" data-resident-id="${esc(residentId)}" data-member-resident-id="${esc(derivedResidentId)}"`,
        });
        return `${openActions}${manageActions}`;
      }

      function toggleResidentGroup(groupId) {
        if (!Object.prototype.hasOwnProperty.call(state.residentGroups, groupId)) return;
        state.residentGroups[groupId] = state.residentGroups[groupId] === false;
        writeLineageUiState({ residentGroups: state.residentGroups });
        state.agentMenuId = "";
        renderAgentList();
      }

      function toggleResidentProjectGroup(groupId) {
        if (!groupId) return;
        state.residentProjectGroups[groupId] = state.residentProjectGroups[groupId] !== true;
        writeLineageUiState({ residentProjectGroups: state.residentProjectGroups });
        state.agentMenuId = "";
        renderAgentList();
      }

      function runtimePermissionPreset(settings) {
        const data = settings && typeof settings === "object" ? settings : {};
        const preset = String(data.permission_preset || "").trim();
        if (preset) return preset;
        const profile = String(data.permissions || "").trim();
        const reviewer = String(data.approvals_reviewer || "").trim().toLowerCase();
        const policy = String(data.approval_policy || "").trim().toLowerCase();
        const sandbox = String(data.sandbox || "").trim();
        if (profile === ":danger-full-access" || sandbox === "danger-full-access") return "full-access";
        if (profile === ":workspace" && reviewer === "auto_review" && (!policy || policy === "on-request")) return "auto-review";
        if (profile === ":workspace" && (!reviewer || reviewer === "user") && (!policy || policy === "on-request")) return "default";
        return "";
      }

      function setSelectValue(id, value) {
        const el = $id(id);
        if (!el) return;
        const text = String(value || "");
        el.value = [...el.options].some(option => option.value === text) ? text : "";
      }

      function replaceSelectOptions(select, options, requestedValue = "") {
        if (!select) return;
        select.replaceChildren(...options.map(item => new Option(item.label, item.value)));
        const requested = String(requestedValue || "");
        select.value = options.some(item => item.value === requested) ? requested : "";
      }

      function residentRuntimeDefinition() {
        return state.agentBackendCatalog?.backends?.find(row => row.backend === $id("residentBackendSelect")?.value) || {};
      }

      function residentRuntimeCatalog() {
        const definition = residentRuntimeDefinition();
        const routes = definition.routes || [];
        const selected = $id("residentRouteSelect")?.value || "";
        const route = routes.find(row => row.backend_route_id === selected) || routes[0] || {};
        return { ...definition, ...window.PortalLineageCatalogs.routeRuntimeCatalog(route, refreshOpenResidentRegistrationOptions) };
      }

      function residentEffortsForModel(model) {
        return [...(residentRuntimeCatalog()?.efforts_by_model?.[String(model || "").trim()] || [])];
      }

      function syncResidentEffortSelect(modelSelectId, effortSelectId, requestedValue = "") {
        const model = String($id(modelSelectId)?.value || "").trim();
        const select = $id(effortSelectId);
        if (!select) return;
        const efforts = residentEffortsForModel(model);
        const requested = String(requestedValue || "").trim();
        const options = [
          { value: "", label: "default" },
          ...efforts.map(effort => ({ value: effort, label: effort })),
        ];
        replaceSelectOptions(select, options, efforts.includes(requested) ? requested : "");
      }

      function resetResidentRuntimeSettings(settings = {}) {
        syncResidentBackendRuntimeFields();
        const catalog = residentRuntimeCatalog();
        const model = settings.model || catalog.default_model || "";
        if (catalog.models?.includes(model)) $id("residentRuntimeModelSelect").value = model;
        syncResidentEffortSelect("residentRuntimeModelSelect", "residentRuntimeEffortSelect", settings.reasoning_effort || "");
        setSelectValue("residentRuntimeTierSelect", settings.service_tier === "priority" ? "priority" : "");
        setSelectValue("residentRuntimePermissionSelect", runtimePermissionPreset(settings));
      }

      function selectedResidentRuntimeSettings() {
        const settings = {};
        const model = String($id("residentRuntimeModelSelect")?.value || "").trim();
        const effort = String($id("residentRuntimeEffortSelect")?.value || "").trim();
        const tier = String($id("residentRuntimeTierSelect")?.value || "").trim();
        const permissionPreset = String($id("residentRuntimePermissionSelect")?.value || "").trim();
        if (model) settings.model = model;
        if (effort && residentRuntimeDefinition().runtime_settings?.includes("reasoning_effort")) settings.reasoning_effort = effort;
        if (tier && residentRuntimeDefinition().runtime_settings?.includes("service_tier")) settings.service_tier = tier;
        if (permissionPreset && residentRuntimeDefinition().runtime_settings?.includes("permissions")) settings.permission_preset = permissionPreset;
        return settings;
      }

      function projectOptionById(projectId) {
        const wanted = String(projectId || "").trim();
        if (!wanted) return null;
        return (state.projects || []).find(project => project.id === wanted) || null;
      }

      function syncResidentBackendRuntimeFields() {
        const definition = residentRuntimeDefinition();
        const fields = definition.runtime_settings || [];
        const routes = definition.routes || [];
        const routeSelect = $id("residentRouteSelect");
        const previousRoute = state.residentRouteChosen ? routeSelect?.value || "" : "";
        if (routeSelect) {
          routeSelect.replaceChildren(...routes.map(route => new Option(route.label, route.backend_route_id)));
          // The route is the one account the Resident uses. Until the user picks one, a
          // Codex Resident starts on the account a new session uses by default.
          const defaultAccount = residentAccountConfig().enabled ? residentAccountConfig().defaultAccountId : "";
          const preferred = routes.some(route => route.backend_route_id === previousRoute)
            ? previousRoute
            : routes.find(route => defaultAccount && String(route.account_id || "").toLowerCase() === defaultAccount)?.backend_route_id;
          if (preferred) routeSelect.value = preferred;
          routeSelect.required = routes.some(row => row.backend_route_id);
          routeSelect.disabled = !routeSelect.required;
          $id("residentRouteField").hidden = !routeSelect.required;
        }
        const catalog = residentRuntimeCatalog();
        const modelSelect = $id("residentRuntimeModelSelect");
        const previous = modelSelect?.value || "";
        if (modelSelect) {
          modelSelect.replaceChildren(...(catalog.models || []).map(value => new Option(value, value)));
          modelSelect.value = catalog.models?.includes(previous) ? previous : catalog.default_model || "";
        }
        for (const [id, field] of [["residentRuntimeEffortSelect", "reasoning_effort"], ["residentRuntimeTierSelect", "service_tier"], ["residentRuntimePermissionSelect", "permissions"]]) {
          const row = $id(id)?.closest(".residentFormRow");
          if (row) { row.hidden = !fields.includes(field); row.style.display = row.hidden ? "none" : ""; }
        }
        syncResidentEffortSelect("residentRuntimeModelSelect", "residentRuntimeEffortSelect", $id("residentRuntimeEffortSelect")?.value || "");
      }

      function setResidentWorkspaceAdvancedOpen(open) {
        const details = $id("residentWorkspaceAdvancedDetails");
        if (details) details.open = !!open;
      }

      function refreshOpenResidentRegistrationOptions() {
        // Catalogs may arrive after the dialog opens (including after reconnect).
        // Refresh only derived choices, never reset user-authored form fields.
        if (!$id("residentRegisterForm")?.getClientRects().length) return;
        syncResidentBackendRuntimeFields();
      }

      function knownUsers() {
        const seen = new Set();
        const users = [];
        for (const user of state.users || []) {
          const id = String(user.id || "").trim();
          if (!id || seen.has(id)) continue;
          seen.add(id);
          users.push({ ...user, id });
        }
        if (state.me && !seen.has(state.me)) {
          users.unshift({ id: state.me, name: state.me, type: "human" });
        }
        return users;
      }

      function canDeleteResident(item) {
        if (isPersonalAmaResident(item)) return false;
        return residentAllows(item).DeleteResident === true;
      }

      // Each menu button asks for the one instruction it runs: Lineage actions and editing a
      // Resident from the Lineage listing, deleting a Resident from its session projection.
      function lineageAllows(lineageId) {
        return (state.lineages || []).find(item => item?.lineage_id === lineageId)?.allows || {};
      }
      const residentAllowsCache = new Map();
      function residentAllows(item) {
        const residentId = residentExactId(item);
        if (!residentId) return {};
        const cached = residentAllowsCache.get(residentId);
        if (cached) return cached.allows || {};
        const entry = { allows: null };
        residentAllowsCache.set(residentId, entry);
        window.PortalResidentService.projection({ residentId, lineageId: String(item?.lineage_id || "") })
          .then(projection => { entry.allows = projection?.allows || {}; })
          .catch(() => { residentAllowsCache.delete(residentId); })
          .finally(() => { if (state.agentMenuId || state.residentMemberMenuId) renderAgentFloatingMenu(); });
        return {};
      }

      function residentRegisterDefaultModal() {
        return $id("residentRegisterOverlay")?.querySelector(".portalDetailSurface") || null;
      }

      function restoreResidentRegisterForm() {
        const form = $id("residentRegisterForm");
        const modal = residentRegisterDefaultModal();
        if (form && modal && form.parentElement !== modal) {
          modal.appendChild(form);
        }
      }

      function resetResidentAttachOptionState() {
        const forkDetails = $id("residentAttachForkDetails");
        if (forkDetails) forkDetails.open = false;
      }

      function resetResidentRegisterModalState() {
        state.residentAttachSource = null;
      }

      function residentAttachChoiceItem() {
        const lineageId = String(
          state.residentAttachChoice?.lineageId || ""
        ).trim();
        return lineageSidebarRoots("", { respectFilters: false }).find(
          item => lineageIdForResident(item) === lineageId
        ) || null;
      }

      function renderResidentAttachChoiceModal() {
        const modal = state.residentAttachChoice || {};
        const overlay = $id("residentAttachOverlay");
        if (!overlay) return;
        setAgentDialogOpen(overlay, !!modal.open);
        overlay.setAttribute("aria-hidden", modal.open ? "false" : "true");
        $id("residentAttachSubtitle").textContent = modal.title ? window.PortalI18n.text`Target lineage: ${modal.title}` : "";
      }

      function openLineageAttachChoiceModal(item) {
        const lineageId = lineageIdForResident(item);
        if (!lineageId) return;
        restoreResidentRegisterForm();
        restoreResidentForkCreateBody();
        resetResidentForkCreateState();
        renderResidentForkCreateModal();
        resetResidentAttachOptionState();
        state.residentAttachChoice = {
          open: true,
          lineageId,
          leaderResidentId: lineageLeaderResidentId(item),
          title: item?.lineage?.title || item.lineage_title || item.title || lineageId,
        };
        state.agentMenuId = "";
        renderResidentAttachChoiceModal();
      }

      function closeResidentAttachChoiceModal() {
        if (state.residentRegisterSaving || forkCreateState().busy) return;
        restoreResidentRegisterForm();
        closeResidentForkCreateModal({ keepAttachOpen: true });
        resetResidentAttachOptionState();
        resetResidentRegisterModalState();
        state.residentAttachChoice = {
          open: false,
          lineageId: "",
          leaderResidentId: "",
          title: "",
        };
        renderResidentAttachChoiceModal();
      }

      function openAttachResidentModal(item) {
        const lineageId = lineageIdForResident(item);
        if (!lineageId) return;
        restoreResidentRegisterForm();
        const sourceTitle = item?.lineage?.title || item.lineage_title || item.title || item.resident_id || "Lineage";
        state.residentAttachSource = {
          lineageId,
          leaderResidentId: lineageLeaderResidentId(item),
          title: sourceTitle,
        };
        $id("residentRegisterTitle").textContent = window.PortalI18n.text`Attach to Lineage · ${sourceTitle}`;
        $id("residentRegisterSubmit").textContent = window.PortalI18n.t("附加");
        $id("residentTitleInput").value = `${sourceTitle} Resident`;
        // A new Resident starts on the first Provider of the server's catalog.
        $id("residentBackendSelect").selectedIndex = 0;
        state.residentRouteChosen = false;
        $id("residentAdditionalWorkspacesInput").value = "";
        resetResidentRuntimeSettings();
        setResidentWorkspaceAdvancedOpen(false);
        syncResidentBackendRuntimeFields();
        setAgentDialogOpen("residentRegisterOverlay", true);
        $id("residentRegisterOverlay").setAttribute("aria-hidden", "false");
        window.setTimeout(() => $id("residentTitleInput").focus(), 0);
      }

      function openResidentEditModal(item) {
        if (!item?.resident_id) return;
        openResidentEditSurface({
          residentId: String(item.resident_id || ""),
          title: String(item.title || item.resident_id || "Resident"),
        });
      }

      function closeResidentRegisterModal() {
        if (state.residentRegisterSaving) return;
        restoreResidentRegisterForm();
        setAgentDialogOpen("residentRegisterOverlay", false);
        $id("residentRegisterOverlay").setAttribute("aria-hidden", "true");
        resetResidentRegisterModalState();
      }

      async function submitResidentRegister(event) {
        event.preventDefault();
        await submitResidentAttach();
      }

      async function submitResidentAttach() {
        const source = state.residentAttachSource || {};
        const sourceLineageId = String(source.lineageId || "").trim();
        if (!sourceLineageId || state.residentRegisterSaving) return;
        const backend = $id("residentBackendSelect").value;
        const payload = {
          lineage_id: sourceLineageId,
          title: $id("residentTitleInput").value.trim(),
          workspace_paths: $id("residentAdditionalWorkspacesInput").value.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
          backend,
          backend_route_id: selectedResidentRoute(),
          mode: "new_session",
          runtime_settings: selectedResidentRuntimeSettings(),
        };
        state.residentRegisterSaving = true;
        $id("residentRegisterSubmit").disabled = true;
        let attachedRecord = null;
        beginResidentSidebarMutation();
        try {
          const result = await residentService.attach(payload);
          const record = result.resident || result;
          if (!record?.resident_id) throw new Error("Attach 返回缺少 resident_id");
          attachedRecord = record;
          state.residents = [record, ...state.residents.filter(item => item.resident_id !== record.resident_id)];
          commitChatTarget(residentChatTarget(
            record.resident_id,
            registeredLineageIdForResident(record),
          ));
          beginChatWorkspaceNavigation();
          writeLastSelection();
          rememberResidentShortcutSession(record.resident_id);
          state.residentRegisterSaving = false;
          closeResidentRegisterModal();
          revealResidentSidebarTarget(record);
          syncSelectedResidentSurface();
          await Promise.allSettled([
            refreshResidentSidebarIfDue(true),
            loadDetail(true),
            loadFolder(),
          ]);
        } catch (err) {
          window.PortalUI.toast(window.PortalI18n.text`Attach 失败：${err.message || err}`, "error");
        } finally {
          endResidentSidebarMutation({
            preserveResidents: attachedRecord ? [attachedRecord] : [],
          });
          state.residentRegisterSaving = false;
          $id("residentRegisterSubmit").disabled = false;
        }
      }

      function whichResidentCorrectionSearchHtml() {
        const finder = state.whichResident;
        if (finder.correctionSearchLoading) {
          return window.PortalI18n.markup('<div class="whichResidentCorrectionSearchState">正在搜索可访问的完整 Resident 目录…</div>');
        }
        if (finder.correctionSearchError) {
          return window.PortalI18n.html`<div class="whichResidentCorrectionSearchState error">${esc(finder.correctionSearchError)}</div>`;
        }
        const results = Array.isArray(finder.correctionSearchResults)
          ? finder.correctionSearchResults : [];
        if (!results.length) {
          return window.PortalI18n.markup('<div class="whichResidentCorrectionSearchState">没有匹配的可访问 Resident。</div>');
        }
        return window.PortalI18n.html`<div class="whichResidentCorrectionResults" role="listbox">${results.map(item => {
          const residentId = String(item?.resident_id || "");
          const selected = residentId === finder.correctionResidentId;
          return window.PortalI18n.html`<button type="button" role="option" aria-selected="${selected ? "true" : "false"}" class="${selected ? "selected" : ""}" data-routing-eval-resident-id="${esc(residentId)}">
            <strong>${esc(item?.title || residentId)}</strong>
            <small>${esc(item?.lineage_title || item?.lineage_id || "")}</small>
            <span>${esc(residentId)}</span>
          </button>`;
        }).join("")}</div>`;
      }

      function whichResidentPanelHtml() {
        const finder = state.whichResident;
        const recommendations = Array.isArray(finder.result?.recommendations)
          ? finder.result.recommendations : [];
        const fork = finder.result?.fork_advice || {};
        const forkSourceId = String(fork.source_resident_id || "").trim();
        const forkSource = recommendations.find(item => item.resident_id === forkSourceId) || {};
        const forkSourceTitle = String(fork.source_title || forkSource.title || forkSourceId).trim();
        // The editable draft is not the query owned by an accepted request.
        const submittedQuery = String(finder.result?.query || finder.submittedQuery || "");
        const requestHtml = finder.requestId || finder.messageId || submittedQuery
          ? window.PortalI18n.html`<div class="whichResidentRequest" aria-label="推荐请求">
              ${finder.requestId ? window.PortalI18n.html`<div>请求 ID：<code>${esc(finder.requestId)}</code></div>` : ""}
              ${finder.messageId ? window.PortalI18n.html`<div>消息 ID：<code>${esc(finder.messageId)}</code></div>` : ""}
              <div>状态：${esc(finder.inputStatus || window.PortalI18n.t("提交中"))}</div>
              <div>已提交查询：${esc(submittedQuery || window.PortalI18n.t("历史查询未保存"))}</div>
            </div>`
          : "";
        const fallbackHtml = finder.result?.fallback
          ? window.PortalI18n.html`<div class="whichResidentStatus" role="status"><strong>目录检索回退</strong>：${esc(finder.result.summary || window.PortalI18n.t("推荐能力当前不可接单；以下候选未经过模型分析。"))}</div>`
          : "";
        const resultHtml = finder.busy
          ? window.PortalI18n.html`<div class="whichResidentStatus">${finder.error ? esc(finder.error) : window.PortalI18n.t("正在分析候选 Resident…")}</div>`
          : finder.error
            ? window.PortalI18n.html`<div class="whichResidentStatus error">${esc(finder.error)}</div>`
            : recommendations.length || (fork.recommended && forkSourceId)
              ? window.PortalI18n.html`${fallbackHtml}${recommendations.length && !finder.result?.fallback ? window.PortalI18n.t('<div class="whichResidentStatus">置信度是模型对职责匹配的判断，不代表统计概率。</div>') : ""}<div class="whichResidentResults">${recommendations.map(item => {
                  const recommendedAction = ["open", "fork"].includes(item.recommended_action) ? item.recommended_action : "open";
                  const confidence = String(item.confidence || "").toLowerCase();
                  const confidenceLabel = finder.result?.fallback ? window.PortalI18n.t("未评估（目录检索）")
                    : confidence === "high" ? window.PortalI18n.t("高") : confidence === "medium" ? window.PortalI18n.t("中")
                    : confidence === "low" ? window.PortalI18n.t("低") : window.PortalI18n.t("未提供");
                  return window.PortalI18n.html`
                  <article class="whichResidentResult">
                    <div><strong>${esc(item.title || item.resident_id)}</strong><small>${esc(item.lineage_title || item.lineage_id || item.resident_id)}</small></div>
                    <small class="whichResidentConfidence">置信度：${esc(confidenceLabel)}</small>
                    <p>${esc(item.reason || item.summary?.current_focus || "")}</p>
                    <div class="whichResidentActions">
                      <button type="button" class="${recommendedAction === "open" ? "recommended" : ""}" data-which-resident-open-id="${esc(item.resident_id)}">打开${recommendedAction === "open" ? window.PortalI18n.t("（推荐）") : ""}</button>
                      <button type="button" class="${recommendedAction === "fork" ? "recommended" : ""}" data-fork-source-resident-id="${esc(item.resident_id)}">创建分支${recommendedAction === "fork" ? window.PortalI18n.t("（推荐）") : ""}</button>
                    </div>
                  </article>`;
                }).join("")}</div>
                ${fork.recommended && forkSourceId ? window.PortalI18n.html`<div class="whichResidentForkAdvice">
                  <strong>建议从 ${esc(forkSourceTitle)} 创建分支</strong>
                  <small>${esc(fork.source_lineage_title || fork.source_lineage_id || forkSource.lineage_title || forkSource.lineage_id || forkSourceId)}</small>
                  <span>${esc(fork.reason || "")}</span>
                  ${fork.suggested_title ? window.PortalI18n.html`<span>建议分支：${esc(fork.suggested_title)}</span>` : ""}
                  <div class="whichResidentAdviceActions">
                    <button type="button" class="recommended" data-fork-source-resident-id="${esc(forkSourceId)}">创建分支（推荐）</button>
                  </div>
                </div>` : ""}`
              : finder.result
                ? window.PortalI18n.markup('<div class="whichResidentStatus">当前可访问摘要中没有足够匹配的 Resident。</div>')
                : window.PortalI18n.markup('<div class="whichResidentStatus">描述你要处理的问题、组件或目标。</div>');
        return window.PortalI18n.html`<section class="whichResidentPanel residentQuestionCard" aria-label="推荐选择">
          ${finder.result?.summary && !finder.result?.fallback ? window.PortalI18n.html`<p>${esc(finder.result.summary)}</p>` : ""}
          ${resultHtml}
          ${requestHtml ? window.PortalI18n.html`<details class="whichResidentRequestDetails"><summary>请求详情</summary>${requestHtml}</details>` : ""}
          ${recommendations.length ? window.PortalI18n.html`<div class="whichResidentCorrectionControls">
            <button type="button" data-which-resident-correction-toggle>${finder.correctionOpen ? window.PortalI18n.t("取消纠正") : window.PortalI18n.t("纠正并加入评测集")}</button>
          </div>` : ""}
          ${finder.correctionOpen ? window.PortalI18n.html`<form data-which-resident-correction-form class="whichResidentCorrectionForm">
            <strong>提交路由 Ground Truth</strong>
            <label>正确的 Resident
              <input type="search" name="resident_search" maxlength="300" autocomplete="off" placeholder="搜索名称、Lineage 或 Resident ID" value="${esc(finder.correctionSearchQuery || "")}">
            </label>
            <input type="hidden" name="ground_truth_resident_id" value="${esc(finder.correctionResidentId || "")}">
            ${finder.correctionResidentId ? window.PortalI18n.html`<div class="whichResidentCorrectionSelected">已选择：<strong>${esc(finder.correctionResidentTitle || finder.correctionResidentId)}</strong><span>${esc(finder.correctionResidentId)}</span></div>` : ""}
            ${whichResidentCorrectionSearchHtml()}
            <select name="ground_truth_action">
              <option value="open" ${finder.correctionAction === "open" ? "selected" : ""}>打开现有 Resident</option>
              <option value="fork" ${finder.correctionAction === "fork" ? "selected" : ""}>创建分支并加入 HATG</option>
            </select>
            <textarea name="rationale" rows="2" maxlength="1200" placeholder="可选：为什么这是正确路由">${esc(finder.correctionRationale || "")}</textarea>
            <button type="submit" ${finder.correctionSaving ? "disabled" : ""}>${finder.correctionSaving ? window.PortalI18n.t("提交中…") : window.PortalI18n.t("提交并加入评测集")}</button>
          </form>` : ""}
          ${finder.evalMessage ? window.PortalI18n.html`<div class="whichResidentStatus">${esc(finder.evalMessage)}</div>` : ""}
        </section>`;
      }

      const recommendationChatPanels = new Map();

      function whichResidentCardHtml() {
        const resident = state.recommendationResident;
        if (!resident && !state.residentSidebarReady) {
          const pending = residentSidebarPendingText("推荐服务");
          return window.PortalI18n.html`<div class="agentItem principalUnavailableCard" aria-busy="${state.residentSidebarError ? "false" : "true"}"><div class="agentSelect noStatusDot"><span class="agentMain"><span class="agentTitle">Which Resident</span><span class="agentMeta" title="${esc(pending)}">${esc(pending)}</span></span></div></div>`;
        }
        if (!resident) {
          const denied = state.recommendationStatus === "denied";
          return window.PortalI18n.html`<div class="agentItem principalUnavailableCard ${denied ? "locked" : ""}"><div class="agentSelect noStatusDot"><span class="agentMain"><span class="agentTitle">Which Resident</span><span class="agentMeta" title="${denied ? window.PortalI18n.t("没有推荐 Resident 的访问权限") : window.PortalI18n.t("推荐服务暂不可用，请刷新重试")}">${denied ? window.PortalI18n.t("没有访问权限") : window.PortalI18n.t("推荐服务暂不可用")}</span></span></div>${denied ? window.PortalI18n.t('<span class="principalAccessLock" data-portal-icon="lock" aria-label="无访问权限"></span>') : ''}</div>`;
        }
        return renderRegisteredResidentItem({ ...resident, title: window.PortalI18n.t("Which Resident") }, { asResident: true });
      }

      function recommendationConversationView(target, panel) {
        if (!state.recommendationResident || target.resident_id !== state.recommendationResident.resident_id) return null;
        recommendationChatPanels.set(panel.instanceId(), panel);
        return {
          messageActions: false,
          // The Resident's reply is presented only once the recommendation it produced is shown.
          readReceipt: () => {
            const request = state.whichResident;
            if (request.busy) return false;
            return request.inputStatus === "completed" ? !!request.result
              : ["failed", "cancelled"].includes(request.inputStatus) && !!request.error;
          },
          project: contract => {
            const request = state.whichResident;
            const query = request.result?.query || request.submittedQuery || "";
            const requestId = request.messageId || request.requestId || "resident-discovery-welcome";
            return { ...contract, title: window.PortalI18n.t("Which Resident"), subtitle: window.PortalI18n.t("描述任务，选择协作者或创建分支。"),
              messages: [
                ...(query ? [{ id: requestId, message_id: requestId, role: "user", content: query }] : []),
                { id: `${requestId}:result`, role: "agent", name: window.PortalI18n.t("Which Resident"),
                  content: request.error || request.result?.summary || "描述你要处理的问题、组件或目标。",
                  recommendation_view: { html: whichResidentPanelHtml() } },
              ],
              composer: { ...contract.composer, placeholder: window.PortalI18n.t("描述你要处理的任务"), can_attach: false,
                refs_enabled: false, can_auto: false,
                can_send: contract.composer?.can_send !== false && !request.busy,
                disabled_reason: request.busy
                  ? "正在寻找合适的 Resident，请等待本次推荐完成。"
                  : contract.composer?.disabled_reason || "" },
            };
          },
          renderMessageBody: item => item.recommendation_view?.html,
          onMessagesRendered: root => bindWhichResidentControls(root),
          send: async (payload, options, activePanel) => {
            if (state.whichResident.busy || activePanel.workspace.state.composer.can_send === false) return null;
            const query = String(payload.text || payload.plainText || "").trim();
            if (query.length > 4000) throw new Error("请将任务描述控制在 4000 字以内");
            return activePanel.workspace.submit(async () => {
              const sent = await runWhichResident(query);
              if (!sent) throw new Error(state.whichResident.error || "推荐请求未能提交");
              return sent;
            }, {
              payload,
              historyText: String(payload.plainText || query).trim(),
              rememberHistory: options.rememberHistory,
              clear: options.clear,
              focus: options.focus,
              clearRefsOnSuccess: true,
            });
          },
        };
      }

      function refreshRecommendationChats() {
        for (const [id, panel] of recommendationChatPanels) {
          if (lineageWorkbench?.getPanel(id) !== panel) { recommendationChatPanels.delete(id); continue; }
          if (panel.target().resident_id !== state.recommendationResident?.resident_id) continue;
          const focused = panel.workspace.els.messages.contains(document.activeElement) ? document.activeElement : null;
          const name = focused?.name;
          const cursor = focused?.selectionStart;
          panel.refreshView();
          if (name) {
            const input = panel.workspace.els.messages.querySelector(`[name="${CSS.escape(name)}"]`);
            input?.focus();
            if (typeof cursor === "number") input?.setSelectionRange?.(cursor, cursor);
          }
        }
      }

      async function runWhichResident(query, { recoveryRetry = false } = {}) {
        const clean = String(query || "").trim();
        if (!clean || state.whichResident.busy) return;
        window.clearTimeout(whichResidentRetryTimer);
        whichResidentRetryTimer = 0;
        whichResidentInputWatch?.destroy?.();
        whichResidentInputWatch = null;
        const requestSeq = Number(state.whichResident.requestSeq || 0) + 1;
        state.whichResident = {
          ...state.whichResident,
          query: clean,
          submittedQuery: clean,
          busy: true,
          requestSeq,
          retryCount: recoveryRetry ? Number(state.whichResident.retryCount || 0) : 0,
          requestId: "",
          result: null,
          error: "",
          residentId: "",
          messageId: "",
          inputStatus: "",
          resultLoading: false,
        };
        persistWhichResidentState({ immediate: true });
        renderAgentList();
        try {
          const started = await residentDiscovery.recommend({
            query: clean,
            limit: 5,
          });
          if (started?.status === "failed") {
            state.whichResident.requestId = String(started.request_id || "");
            throw new Error(started.error || "Resident 推荐请求失败");
          }
          if (started?.result) {
            state.whichResident = {
              ...state.whichResident,
              busy: false,
              requestId: String(started.request_id || ""),
              result: started.result,
              inputStatus: "completed",
            };
          } else {
            bindWhichResidentInputWatch(started, requestSeq);
          }
        } catch (error) {
          if (requestSeq !== state.whichResident.requestSeq) return;
          state.whichResident = {
            ...state.whichResident,
            busy: false,
            result: null,
            error: error.message || String(error),
            inputStatus: "failed",
          };
        }
        persistWhichResidentState({ immediate: true });
        renderAgentList();
        return state.whichResident.inputStatus !== "failed";
      }

      function stopUnavailableWhichResident(error, requestSeq) {
        if (![401, 403, 404, 410].includes(Number(error?.status)) || requestSeq !== state.whichResident.requestSeq) return false;
        window.clearTimeout(whichResidentRetryTimer);
        whichResidentRetryTimer = 0;
        whichResidentInputWatch?.destroy?.();
        whichResidentInputWatch = null;
        state.whichResident = {
          ...state.whichResident,
          busy: false,
          resultLoading: false,
          inputStatus: "unavailable",
          error: `原查询不存在或当前无权读取，已停止自动重试；可修改查询后重新提交：${error.message || String(error)}`,
        };
        persistWhichResidentState({ immediate: true });
        renderAgentList();
        return true;
      }

      function scheduleWhichResidentRetry(requestSeq, retry) {
        if (whichResidentRetryTimer || requestSeq !== state.whichResident.requestSeq) return;
        whichResidentRetryTimer = window.setTimeout(() => {
          whichResidentRetryTimer = 0;
          if (requestSeq !== state.whichResident.requestSeq || state.whichResident.result) return;
          retry();
        }, 2000);
      }

      function retryWhichResidentObservation(requestSeq) {
        scheduleWhichResidentRetry(requestSeq, () => {
          bindWhichResidentInputWatch(state.whichResident, requestSeq);
        });
      }

      function retryWhichResidentResult(requestSeq) {
        scheduleWhichResidentRetry(requestSeq, () => {
          loadWhichResidentResult(requestSeq).catch(() => {});
        });
      }

      function retryCancelledWhichResident(requestSeq) {
        if (requestSeq !== state.whichResident.requestSeq || Number(state.whichResident.retryCount || 0) >= 1) return false;
        const query = String(state.whichResident.submittedQuery || state.whichResident.query || "").trim();
        if (!query) return false;
        window.clearTimeout(whichResidentRetryTimer);
        whichResidentInputWatch?.destroy?.();
        whichResidentInputWatch = null;
        const retrySeq = requestSeq + 1;
        state.whichResident = {
          ...state.whichResident,
          requestSeq: retrySeq,
          busy: true,
          resultLoading: false,
          inputStatus: "retrying",
          error: "推荐任务被停止，正在重新提交一次",
          retryCount: 1,
        };
        persistWhichResidentState({ immediate: true });
        renderAgentList();
        whichResidentRetryTimer = window.setTimeout(() => {
          whichResidentRetryTimer = 0;
          if (retrySeq !== state.whichResident.requestSeq || state.whichResident.inputStatus !== "retrying") return;
          state.whichResident.busy = false;
          runWhichResident(query, { recoveryRetry: true }).catch(() => {});
        }, 1000);
        return true;
      }

      async function loadWhichResidentResult(requestSeq) {
        const finder = state.whichResident;
        if (
          requestSeq !== finder.requestSeq
          || finder.resultLoading
          || !finder.residentId
          || !finder.messageId
        ) return;
        finder.resultLoading = true;
        try {
          const payload = await residentDiscovery.loadRecommendationResult({
            resident_id: finder.residentId,
            message_id: finder.messageId,
          });
          if (requestSeq !== state.whichResident.requestSeq) return;
          const resultStatus = String(payload?.status || "").trim().toLowerCase();
          if (resultStatus === "cancelled" && retryCancelledWhichResident(requestSeq)) return;
          if (["failed", "cancelled", "unavailable", "missing"].includes(resultStatus)) {
            state.whichResident = {
              ...state.whichResident,
              busy: false,
              resultLoading: false,
              result: null,
              inputStatus: resultStatus,
              error: String(payload?.error || payload?.input_state?.error?.message || `Resident 推荐已${resultStatus === "cancelled" ? "取消" : "失败"}`),
            };
            window.clearTimeout(whichResidentRetryTimer);
            whichResidentRetryTimer = 0;
            whichResidentInputWatch?.destroy?.();
            whichResidentInputWatch = null;
            return;
          }
          if (payload.status !== "completed" || !payload.result) {
            retryWhichResidentResult(requestSeq);
            return;
          }
          state.whichResident = {
            ...state.whichResident,
            busy: false,
            resultLoading: false,
            result: payload.result,
            requestId: String(payload.request_id || state.whichResident.requestId || ""),
            error: "",
            inputStatus: "completed",
          };
          window.clearTimeout(whichResidentRetryTimer);
          whichResidentRetryTimer = 0;
          whichResidentInputWatch?.destroy?.();
          whichResidentInputWatch = null;
        } catch (error) {
          if (requestSeq !== state.whichResident.requestSeq) return;
          if (stopUnavailableWhichResident(error, requestSeq)) return;
          state.whichResident = {
            ...state.whichResident,
            busy: true,
            resultLoading: false,
            result: null,
            error: `推荐结果读取暂不可用，正在重试：${error.message || String(error)}`,
          };
          retryWhichResidentResult(requestSeq);
        } finally {
          if (requestSeq === state.whichResident.requestSeq) {
            state.whichResident.resultLoading = false;
            persistWhichResidentState({ immediate: true });
            renderAgentList();
          }
        }
      }

      function bindWhichResidentInputWatch(handle, requestSeq) {
        const residentId = String(handle?.resident_id || state.whichResident.residentId || "").trim();
        const messageId = String(handle?.message_id || state.whichResident.messageId || "").trim();
        if (!residentId || !messageId) throw new Error("Resident 推荐未返回消息身份");
        window.clearTimeout(whichResidentRetryTimer);
        whichResidentRetryTimer = 0;
        whichResidentInputWatch?.destroy?.();
        state.whichResident.residentId = residentId;
        state.whichResident.messageId = messageId;
        state.whichResident.requestId = String(handle?.request_id || state.whichResident.requestId || "");
        state.whichResident.inputStatus = String(handle?.status || handle?.inputStatus || "queued");
        whichResidentInputWatch = window.PortalResidentProtocol.watchInputState(
          residentId,
          messageId,
          update => {
            if (requestSeq !== state.whichResident.requestSeq) return;
            if (update.type === "error") {
              if (state.whichResident.result) return;
              if (stopUnavailableWhichResident(update.error, requestSeq)) return;
              state.whichResident.error = `状态读取暂不可用，正在重试：${update.error?.message || String(update.error || "未知错误")}`;
              retryWhichResidentObservation(requestSeq);
              persistWhichResidentState({ immediate: true });
              renderAgentList();
              return;
            }
            const inputState = update.input_state;
            if (!inputState) return;
            const status = String(inputState.state || "");
            if (status === "cancelled" && retryCancelledWhichResident(requestSeq)) return;
            state.whichResident.inputStatus = status;
            state.whichResident.error = "";
            state.whichResident.busy = !["failed", "cancelled"].includes(status);
            if (["failed", "cancelled"].includes(status)) {
              window.clearTimeout(whichResidentRetryTimer);
              whichResidentRetryTimer = 0;
              state.whichResident.busy = false;
              state.whichResident.error = String(inputState.error?.message || `Resident 推荐已${status === "cancelled" ? "取消" : "失败"}`);
            } else if (status === "completed") {
              loadWhichResidentResult(requestSeq).catch(error => {
                state.whichResident.busy = false;
                state.whichResident.error = error.message || String(error);
                renderAgentList();
              });
            }
            persistWhichResidentState({ immediate: true });
            renderAgentList();
          },
          { historyLimit: 1 },
        );
        persistWhichResidentState({ immediate: true });
        if (state.whichResident.inputStatus === "queued") {
          loadWhichResidentResult(requestSeq).catch(() => {});
        }
      }

      async function restoreLatestWhichResident() {
        if (state.whichResident.inputStatus === "retrying"
          || (state.whichResident.inputStatus === "cancelled" && state.whichResident.submittedQuery)) return;
        // A submission that failed before the server recorded it is newer than anything
        // the server's latest record holds: keep its query and error.
        if (!state.whichResident.requestId && state.whichResident.submittedQuery
          && ["failed", "unavailable"].includes(String(state.whichResident.inputStatus || ""))) return;
        const observedRequestSeq = Number(state.whichResident.requestSeq || 0);
        const latest = await residentDiscovery.latestRecommendation();
        if (observedRequestSeq !== Number(state.whichResident.requestSeq || 0)) return;
        // The server records every accepted request, so a different request ID there is
        // newer than the result this page last showed (for example one sent from another tab).
        const latestRequestId = String(latest?.request_id || "");
        const showsLatest = Boolean(state.whichResident.result)
          && (!latestRequestId || latestRequestId === String(state.whichResident.requestId || ""));
        if (latest?.status === "failed") {
          window.clearTimeout(whichResidentRetryTimer);
          whichResidentRetryTimer = 0;
          whichResidentInputWatch?.destroy?.();
          whichResidentInputWatch = null;
          state.whichResident = {
            ...state.whichResident,
            query: String(latest.query || ""),
            submittedQuery: String(latest.query || ""),
            requestId: latestRequestId,
            requestSeq: observedRequestSeq + 1,
            residentId: "",
            messageId: "",
            busy: false,
            resultLoading: false,
            inputStatus: "failed",
            result: null,
            error: String(latest.error || "Resident 推荐请求失败"),
          };
          persistWhichResidentState({ immediate: true });
          renderAgentList();
          return;
        }
        if (latest?.status === "completed" && latest.result && !showsLatest) {
          state.whichResident = {
            ...state.whichResident,
            query: String(latest.query || latest.result.query || state.whichResident.query || "").slice(0, 4000),
            submittedQuery: String(latest.query || latest.result.query || "").slice(0, 4000),
            busy: false,
            requestId: String(latest.request_id || ""),
            inputStatus: "completed",
            resultLoading: false,
            result: latest.result,
            error: "",
          };
          persistWhichResidentState({ immediate: true });
          renderAgentList();
          return;
        }
        const residentId = String(latest?.resident_id || "").trim();
        const messageId = String(latest?.message_id || "").trim();
        if (!residentId || !messageId || showsLatest) return;
        if (
          residentId === String(state.whichResident.residentId || "")
          && messageId === String(state.whichResident.messageId || "")
        ) {
          if (!state.whichResident.submittedQuery && latest.query) {
            state.whichResident.submittedQuery = String(latest.query).slice(0, 4000);
            persistWhichResidentState({ immediate: true });
            renderAgentList();
          }
          return;
        }
        const requestSeq = observedRequestSeq + 1;
        state.whichResident = {
          ...state.whichResident,
          query: String(latest.query || state.whichResident.query || ""),
          submittedQuery: String(latest.query || ""),
          requestId: String(latest.request_id || ""),
          busy: true,
          requestSeq,
          residentId,
          messageId,
          inputStatus: String(latest.status || "queued"),
          resultLoading: false,
          result: null,
          error: "",
        };
        persistWhichResidentState({ immediate: true });
        renderAgentList();
        bindWhichResidentInputWatch(latest, requestSeq);
      }

      async function searchRoutingEvalResidents(query = "") {
        const requestSeq = Number(state.whichResident.correctionSearchRequestSeq || 0) + 1;
        state.whichResident = {
          ...state.whichResident,
          correctionSearchRequestSeq: requestSeq,
          correctionSearchLoading: true,
          correctionSearchError: "",
        };
        renderAgentList();
        try {
          const result = await residentDiscovery.searchRoutingEvalResidents({
            query: String(query || "").trim(),
            limit: 30,
          });
          if (requestSeq !== state.whichResident.correctionSearchRequestSeq) return;
          state.whichResident = {
            ...state.whichResident,
            correctionSearchLoading: false,
            correctionSearchResults: Array.isArray(result.results) ? result.results : [],
          };
        } catch (error) {
          if (requestSeq !== state.whichResident.correctionSearchRequestSeq) return;
          state.whichResident = {
            ...state.whichResident,
            correctionSearchLoading: false,
            correctionSearchError: error.message || String(error),
          };
        }
        renderAgentList();
        window.requestAnimationFrame(() => {
          const input = document.querySelector("[data-which-resident-correction-form] input[name='resident_search']");
          if (!input || !state.whichResident.correctionOpen) return;
          input.focus();
          const end = String(input.value || "").length;
          input.setSelectionRange?.(end, end);
        });
      }

      function scheduleRoutingEvalResidentSearch(query) {
        window.clearTimeout(whichResidentCorrectionSearchTimer);
        whichResidentCorrectionSearchTimer = window.setTimeout(() => {
          searchRoutingEvalResidents(query).catch(() => {});
        }, 220);
      }

      async function submitRoutingEvalCorrection(form) {
        if (state.whichResident.correctionSaving) return;
        const data = new FormData(form);
        const residentId = String(data.get("ground_truth_resident_id") || "").trim();
        const action = String(data.get("ground_truth_action") || "open").trim();
        if (!residentId) {
          window.PortalUI.toast(window.PortalI18n.t("请选择正确的 Resident。"), "warning");
          return;
        }
        state.whichResident = { ...state.whichResident, correctionSaving: true, evalMessage: "" };
        renderAgentList();
        try {
          const recommendations = Array.isArray(state.whichResident.result?.recommendations)
            ? state.whichResident.result.recommendations : [];
          const result = await residentDiscovery.submitRoutingEval({
              query: String(state.whichResident.result?.query || state.whichResident.submittedQuery || "").trim(),
              predicted: recommendations.map(item => ({
                resident_id: item.resident_id,
                title: item.title,
                recommended_action: item.recommended_action,
                reason: item.reason,
              })),
              ground_truth_resident_id: residentId,
              ground_truth_action: action,
              rationale: String(data.get("rationale") || "").trim(),
          });
          const caseId = String(result.submission?.dataset_case_id || "");
          const datasetAdded = result.dataset_added === true;
          state.whichResident = {
            ...state.whichResident,
            correctionSaving: false,
            correctionOpen: false,
            correctionResidentId: "",
            correctionResidentTitle: "",
            correctionSearchQuery: "",
            correctionSearchResults: [],
            correctionSearchError: "",
            correctionAction: "open",
            correctionRationale: "",
            evalMessage: datasetAdded
              ? `已加入评测集${caseId ? `：${caseId}` : ""}。`
              : `该 Ground Truth 已存在${caseId ? `：${caseId}` : ""}，已记录本次纠正。`,
          };
          window.PortalUI.toast(datasetAdded ? window.PortalI18n.t("已加入路由评测集。") : window.PortalI18n.t("评测集已有相同 Ground Truth。"), "success");
        } catch (error) {
          state.whichResident = {
            ...state.whichResident,
            correctionSaving: false,
            evalMessage: error.message || String(error),
          };
        }
        renderAgentList();
      }

      const boundRecommendationCards = new WeakSet();
      const recommendationDetailsState = new WeakMap();
      function bindWhichResidentControls(root) {
        const card = root.querySelector(".whichResidentPanel");
        if (!card || boundRecommendationCards.has(card)) return;
        boundRecommendationCards.add(card);
        const details = card.querySelector('.whichResidentRequestDetails');
        if (details) {
          const requestSeq = state.whichResident.requestSeq || 0;
          const saved = recommendationDetailsState.get(root);
          if (saved?.requestSeq === requestSeq) details.open = saved.open;
          recommendationDetailsState.set(root, { requestSeq, open: details.open });
          details.addEventListener('toggle', () => {
            if (details.isConnected) {
              recommendationDetailsState.set(root, { requestSeq, open: details.open });
            }
          });
        }
        root.querySelectorAll("[data-which-resident-open-id]").forEach(button => {
          button.addEventListener("click", () => openWhichResidentRecommendation(
            button.dataset.whichResidentOpenId || "",
          ).catch(error => window.PortalUI.toast(error.message || error, "error")));
        });
        root.querySelectorAll("[data-fork-source-resident-id]").forEach(button => {
          button.addEventListener("click", () => openWhichResidentFork(
            button.dataset.forkSourceResidentId || "",
          ).catch(error => window.PortalUI.toast(error.message || error, "error")));
        });
        root.querySelector("[data-which-resident-correction-toggle]")?.addEventListener("click", () => {
          const opening = !state.whichResident.correctionOpen;
          state.whichResident = {
            ...state.whichResident,
            correctionOpen: opening,
            correctionSearchQuery: opening ? "" : state.whichResident.correctionSearchQuery,
            correctionSearchResults: opening ? [] : state.whichResident.correctionSearchResults,
            correctionSearchError: "",
            evalMessage: "",
          };
          renderAgentList();
          if (opening) searchRoutingEvalResidents("").catch(() => {});
        });
        const correctionForm = root.querySelector("[data-which-resident-correction-form]");
        correctionForm?.addEventListener("submit", event => {
          event.preventDefault();
          submitRoutingEvalCorrection(event.currentTarget).catch(() => {});
        });
        correctionForm?.querySelector("input[name='resident_search']")?.addEventListener("input", event => {
          const query = String(event.currentTarget.value || "").slice(0, 300);
          state.whichResident.correctionSearchQuery = query;
          state.whichResident.correctionResidentId = "";
          state.whichResident.correctionResidentTitle = "";
          scheduleRoutingEvalResidentSearch(query);
        });
        correctionForm?.querySelectorAll("[data-routing-eval-resident-id]").forEach(button => {
          button.addEventListener("click", () => {
            const residentId = String(button.dataset.routingEvalResidentId || "").trim();
            const resident = state.whichResident.correctionSearchResults.find(item => (
              String(item?.resident_id || "") === residentId
            )) || {};
            state.whichResident.correctionResidentId = residentId;
            state.whichResident.correctionResidentTitle = String(resident.title || residentId);
            state.whichResident.correctionSearchQuery = String(resident.title || residentId);
            renderAgentList();
          });
        });
        correctionForm?.querySelector("select[name='ground_truth_action']")?.addEventListener("change", event => {
          state.whichResident.correctionAction = String(event.currentTarget.value || "open");
        });
        correctionForm?.querySelector("textarea[name='rationale']")?.addEventListener("input", event => {
          state.whichResident.correctionRationale = String(event.currentTarget.value || "").slice(0, 1200);
        });
      }

      async function openWhichResidentFork(residentId) {
        const id = String(residentId || "").trim();
        const forkAdvice = state.whichResident.result?.fork_advice || {};
        const recommendation = state.whichResident.result?.recommendations?.find(item => item.resident_id === id)
          || (forkAdvice.source_resident_id === id ? {
            resident_id: id,
            title: forkAdvice.source_title || id,
            lineage_id: forkAdvice.source_lineage_id || "",
            lineage_title: forkAdvice.source_lineage_title || "",
          } : {});
        const exactResident = state.residents.find(item => String(item?.resident_id || "").trim() === id) || null;
        const sourceRoot = lineageSidebarRootByResidentId(id);
        const source = exactResident || sourceRoot || { resident_id: id, title: recommendation.title || id };
        const lineageId = String(recommendation.lineage_id || "").trim()
          || registeredLineageIdForResident(source)
          || registeredLineageIdForResident(sourceRoot);
        if (!id) throw new Error("缺少要创建分支的 Resident");
        if (!lineageId) {
          throw new Error("这个 Resident 不属于可写入 HATG 的 Lineage，无法创建分支");
        }
        await openResidentForkCreateModal(
          { ...source, resident_id: id, title: recommendation.title || source.title || id },
          {
            draftText: state.whichResident.query,
            intent: "lineage",
            lineageId,
          },
        );
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

      async function openWhichResidentRecommendation(residentId) {
        const query = String(state.whichResident.query || "").trim();
        const opened = await selectResident(residentId, {
          forceView: true,
          revealInGraph: true,
        });
        if (opened) appendChatWorkspaceDraft(query);
        return opened;
      }

      async function refreshPersonalAssistantShortcut() {
        const request = ++personalAssistantRead;
        const target = await window.PortalNav.personalAssistantTarget().catch(() => null);
        if (request !== personalAssistantRead) return;
        state.personalAssistant = target;
        renderAgentList();
        renderFavoriteList();
      }

      function residentNavigationShortcutsHtml() {
        const assistant = state.personalAssistant;
        return window.PortalI18n.html`<div class="whichResidentWrap">${whichResidentCardHtml()}</div>${assistant ? `<div class="personalAssistantResident">${renderRegisteredResidentItem(assistant, { asResident: true })}</div>` : ""}`;
      }

      function renderAgentList() {
        renderProjectFilterOptions();
        syncAgentTools();
        const list = $id("agentList");
        const groups = residentGroups();
        list.innerHTML = window.PortalI18n.html`<div class="agentList">${residentNavigationShortcutsHtml()}${groups.map(renderResidentGroup).join("")}</div>`;
        renderActivityRecentRail();
        list.querySelectorAll("[data-resident-toggle]").forEach(btn => {
          btn.addEventListener("click", event => {
            event.stopPropagation();
            toggleResidentGroup(btn.dataset.residentToggle || "");
          });
        });
        list.querySelectorAll("[data-resident-project-toggle]").forEach(btn => {
          btn.addEventListener("click", event => {
            event.stopPropagation();
            toggleResidentProjectGroup(btn.dataset.residentProjectToggle || "");
          });
        });
        bindAgentCardControls(list);
        list.querySelector("[data-project-create]")?.addEventListener("click", async event => {
          event.stopPropagation();
          const result = await window.PortalProjectCreation.open();
          if (!result) return;
          state.query = "";
          state.projectDisplayFilter = "all";
          state.projectFilter = "all";
          state.residentGroups.lineage = true;
          $id("searchInput").value = "";
          writeLineageUiState({ query: "", projectFilter: "all", projectDisplayFilter: "all" });
          window.PortalUI.toast(window.PortalI18n.t("Project 已创建，可在项目卡片中添加 Lineage。"), "success");
          try {
            await refreshResidentSidebarIfDue(true);
          } catch (error) {
            window.PortalUI.toast(window.PortalI18n.text`Project 已创建，导航刷新失败：${error.message || error}`, "error");
          }
        });
        bindWhichResidentControls(list);
        refreshRecommendationChats();
        renderAgentFloatingMenu();
      }

      // A conversation selection changes only highlighting, not the contents
      // of Navigation. Keep the existing cards and their handlers mounted.
      function syncResidentNavigationSelection() {
        const residentId = selectedResidentId();
        const lineageId = activeLineageId();
        for (const rootId of ["agentList", "favoriteAgentList"]) {
          const root = $id(rootId);
          if (!root) continue;
          root.querySelectorAll(".residentCardWrap").forEach(card => {
            const item = card.querySelector(".agentItem");
            if (!item) return;
            const personal = Boolean(item.querySelector("[data-resident-select]"));
            item.classList.toggle("active", personal
              ? card.dataset.residentCard === residentId
              : Boolean(lineageId && card.dataset.lineageCard === lineageId));
          });
          root.querySelectorAll(".principalProjectCard").forEach(card => {
            card.classList.toggle("active",
              card.querySelector("[data-principal-open]")?.dataset.principalOpen === residentId);
          });
        }
        renderActivityRecentRail();
      }

      function sameChatroomActor(left, right) {
        const clean = value => String(value || "").replace(/^[@＠]+/, "").trim().toLowerCase();
        return Boolean(clean(left)) && clean(left) === clean(right);
      }

      function chatroomUserName(userId) {
        const id = String(userId || "").trim();
        if (!id) return "";
        const listed = state.chatroomUsers.find(item => sameChatroomActor(item.username, id));
        if (listed) return String(listed.display_name || listed.username || id);
        const user = knownUsers().find(item => sameChatroomActor(item.id, id));
        return String(user?.name || user?.display_name || id);
      }

      async function loadChatroomUsers({ force = false } = {}) {
        if (state.chatroomUsers.length && !force) return state.chatroomUsers;
        const result = await window.PortalInstructionClient.execute("ListUsers", {}).catch(() => null);
        if (Array.isArray(result?.users)) state.chatroomUsers = result.users;
        return state.chatroomUsers;
      }

      function chatroomAuthorName(principalId) {
        const id = String(principalId || "").trim();
        if (!/^resident_[0-9a-f]{12}$/.test(id)) return chatroomUserName(id);
        const record = (state.residents || []).find(item => String(item?.resident_id || "") === id);
        return String(record?.title || record?.name || id);
      }

      function chatroomDisplayTitle(chatroom) {
        return String(chatroom?.title || "").trim()
          || (chatroom?.members || []).map(chatroomUserName).join("、")
          || window.PortalI18n.t("聊天室");
      }

      function chatroomActivity(chatroom) {
        return Date.parse(chatroom?.last_message?.created_at || chatroom?.updated_at || "") || 0;
      }

      // The card's time is secondary: as short as it can be (today HH:mm, this year M/D, else Y/M/D); the tooltip has it in full.
      function chatroomShortTime(timestamp, now = new Date()) {
        const date = new Date(timestamp);
        const pad = number => String(number).padStart(2, "0");
        if (date.toDateString() === now.toDateString()) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
        const day = `${date.getMonth() + 1}/${date.getDate()}`;
        return date.getFullYear() === now.getFullYear() ? day : `${date.getFullYear()}/${day}`;
      }

      function chatroomEntries() {
        return state.chatroomRecords
          .map(chatroom => ({ chatroom, pinnedAt: String(state.chatroomPins.get(chatroom.chatroom_id) || "") }))
          .sort((left, right) => (Number(Boolean(right.pinnedAt)) - Number(Boolean(left.pinnedAt)))
            || (chatroomActivity(right.chatroom) - chatroomActivity(left.chatroom)));
      }

      function chatroomMenuHtml(chatroom) {
        const id = String(chatroom.chatroom_id || "");
        const pinnedAt = state.chatroomPins.get(id);
        const muted = window.PortalConversationStore.entry(`chatroom:${id}`)?.muted ?? chatroom.muted;
        const permissions = chatroom.permissions || {};
        const action = (name, label, extra = "") => window.PortalI18n.html`<button type="button" ${extra} data-chatroom-action="${name}" data-chatroom-id="${esc(id)}">${label}</button>`;
        return [
          action(pinnedAt ? "unpin" : "pin", pinnedAt ? "取消置顶" : "置顶"),
          action(muted ? "unmute" : "mute", muted ? "取消消息免打扰" : "消息免打扰"),
          permissions.can_rename ? action("rename", "改名") : "",
          permissions.can_add_members ? action("add-members", "添加成员") : "",
          permissions.can_leave ? action("leave", "退出聊天室", 'class="danger"') : "",
          permissions.can_delete ? action("delete", "删除聊天室", 'class="danger"') : "",
        ].join("");
      }

      function renderChatroomMenu() {
        if (!chatroomMenuPopover || !chatroomMenuTarget) return;
        const chatroom = chatroomRecord(chatroomMenuTarget.id);
        if (!chatroom) { chatroomMenuPopover.close(); return; }
        const panel = chatroomMenuPopover.element;
        const html = chatroomMenuHtml(chatroom);
        if (panel.renderedHtml !== html) {
          const focusedAction = panel.contains(document.activeElement) ? document.activeElement.dataset.chatroomAction : "";
          panel.innerHTML = html;
          panel.renderedHtml = html;
          if (focusedAction) panel.querySelector(`[data-chatroom-action="${focusedAction}"]`)?.focus();
        }
        chatroomMenuPopover.reposition();
      }

      function openChatroomMenu(trigger, id, event) {
        if (chatroomMenuTarget?.trigger === trigger && chatroomMenuPopover?.isOpen()) {
          chatroomMenuPopover.close(event);
          return;
        }
        chatroomMenuPopover?.close();
        chatroomMenuTarget = { trigger, id };
        if (!chatroomMenuPopover) {
          const panel = document.createElement("div");
          panel.className = "chatroomCardMenu";
          panel.setAttribute("role", "menu");
          panel.setAttribute("aria-label", window.PortalI18n.t("聊天室操作"));
          panel.hidden = true;
          document.body.append(panel);
          chatroomMenuPopover = window.PortalOverlay.bindDynamicPopover({
            panel, placement: "bottom-end", portal: true,
            onOpen: () => {
              chatroomMenuTarget.trigger.classList.add("active");
              chatroomMenuTarget.trigger.closest(".chatroomCard")?.classList.add("menuOpen");
            },
            onClose: () => {
              chatroomMenuTarget.trigger.classList.remove("active");
              chatroomMenuTarget.trigger.closest(".chatroomCard")?.classList.remove("menuOpen");
            },
          });
          panel.addEventListener("click", event => {
            const button = event.target.closest("[data-chatroom-action]");
            if (!button) return;
            chatroomMenuPopover.close(event);
            handleChatroomAction(button.dataset.chatroomAction, button.dataset.chatroomId)
              .catch(error => window.PortalUI.toast(window.PortalI18n.text`聊天室操作失败：${error.message || error}`, "error"));
          });
        }
        renderChatroomMenu();
        chatroomMenuPopover.open(trigger, event);
      }

      function renderChatroomCard({ chatroom, pinnedAt }) {
        const id = String(chatroom.chatroom_id || "");
        const active = state.activeChatroomId === id;
        const viewerState = window.PortalConversationStore.entry(`chatroom:${id}`) || {};
        const unread = Number(viewerState.unread_count || 0);
        const muted = Boolean(viewerState.muted ?? chatroom.muted);
        const mentions = Number(viewerState.unread_mention_count || 0);
        const attention = viewerState.attention_unread ?? Boolean(unread);
        const last = chatroom.last_message || {};
        const meta = last.preview
          ? `${last.author_id === "portal" ? "" : `${chatroomAuthorName(last.author_id)}：`}${last.preview}`
          : `${(chatroom.members || []).length} 位成员`;
        const activity = chatroomActivity(chatroom);
        const time = activity ? chatroomShortTime(activity) : "";
        const fullTime = activity
          ? (window.PortalUI?.formatDateTime?.(new Date(activity).toISOString(), { style: "calendar-minute" }) || "")
          : "";
        return window.PortalI18n.html`<div class="chatroomCardWrap" data-chatroom-card="${esc(id)}">
          <div class="agentItem chatroomCard ${active ? "active" : ""} ${pinnedAt ? "pinned" : ""} ${attention ? "unread" : ""}">
            <button type="button" class="agentSelect noStatusDot" data-chatroom-select="${esc(id)}">
              <span class="chatroomEntryIcon" data-portal-icon="users" aria-hidden="true"></span>
              <span class="agentMain">
                <span class="chatroomEntryHead">
                  <span class="agentTitle">${esc(chatroomDisplayTitle(chatroom))}</span>
                  ${unread ? window.PortalI18n.html`<span class="chatroomUnreadCount${muted ? " is-muted" : ""}" aria-label="${unread} 条未读" title="${unread} 条未读">${unread > 99 ? "99+" : unread}</span>` : ""}
                  ${mentions ? window.PortalI18n.html`<span class="chatroomMentionBadge" title="定位第一条未读 @ 消息">@我 ${mentions}</span>` : ""}
                  ${muted ? window.PortalI18n.t('<span class="chatroomMutedMark" data-portal-icon="bell-off" aria-label="消息免打扰" title="消息免打扰"></span>') : ""}
                  ${pinnedAt ? window.PortalI18n.t('<span class="chatroomPinnedMark" data-portal-icon="pin" role="img" aria-label="已置顶"></span>') : ""}
                </span>
                <span class="chatroomEntryLine">
                  <span class="agentMeta">${esc(meta)}</span>
                  ${time ? window.PortalI18n.html`<span class="chatroomEntryTime" title="${esc(fullTime)}">${esc(time)}</span>` : ""}
                </span>
              </span>
            </button>
            <button type="button" class="agentMenuBtn" data-chatroom-menu="${esc(id)}" aria-label="打开聊天室操作菜单" title="打开聊天室操作菜单" data-portal-icon="more-horizontal"></button>
          </div>
        </div>`;
      }

      function renderChatroomList() {
        const root = $id("chatroomList");
        if (!root) return;
        const entries = chatroomEntries();
        const ids = new Set(entries.map(entry => entry.chatroom.chatroom_id));
        let list = root.querySelector(".chatroomList");
        if (!entries.length) {
          if (chatroomMenuTarget?.trigger.closest("#chatroomList")) chatroomMenuPopover?.close();
          root.innerHTML = consoleStateHtml("还没有聊天室。点击加号新建。");
          return;
        }
        if (!list) {
          list = document.createElement("div");
          list.className = "agentList chatroomList";
          root.replaceChildren(list);
        }
        const cards = new Map(Array.from(list.children).map(card => [card.dataset.chatroomCard, card]));
        for (const [id, card] of cards) {
          if (ids.has(id)) continue;
          if (card.contains(chatroomMenuTarget?.trigger)) chatroomMenuPopover?.close();
          card.remove();
        }
        let before = list.firstElementChild;
        for (const entry of entries) {
          const id = entry.chatroom.chatroom_id;
          const html = renderChatroomCard(entry);
          let card = cards.get(id);
          if (!card || card.renderedHtml !== html) {
            const template = document.createElement("template");
            template.innerHTML = html;
            const next = template.content.firstElementChild;
            if (card) {
              // Keep the buttons and open popover anchor alive across refreshes.
              const item = card.querySelector(".chatroomCard");
              const menuOpen = item.classList.contains("menuOpen");
              item.className = next.querySelector(".chatroomCard").className;
              item.classList.toggle("menuOpen", menuOpen);
              card.querySelector("[data-chatroom-select]").innerHTML = next.querySelector("[data-chatroom-select]").innerHTML;
            } else {
              card = next;
              card.querySelector("[data-chatroom-select]").addEventListener("click", () => {
                selectChatroom(id).catch(error => window.PortalUI.toast(window.PortalI18n.text`打开聊天室失败：${error.message || error}`, "error"));
              });
              const trigger = card.querySelector("[data-chatroom-menu]");
              trigger.addEventListener("click", event => openChatroomMenu(trigger, id, event));
            }
            card.renderedHtml = html;
          }
          if (card !== before) list.insertBefore(card, before);
          before = card.nextElementSibling;
        }
        renderChatroomMenu();
      }

      function applyChatroomPins(payload) {
        state.chatroomPins = new Map((Array.isArray(payload?.items) ? payload.items : [])
          .map(item => [String(item?.value?.chatroom_id || item?.key || "").trim(), String(item?.value?.pinned_at || "")])
          .filter(([id, pinnedAt]) => /^chatroom_[0-9a-f]{12}$/.test(id) && pinnedAt));
      }

      function applyPersonalAmaAssociation(payload) {
        const association = (Array.isArray(payload?.items) ? payload.items : [])
          .find(item => String(item?.key || "") === "primary")
          || (Array.isArray(payload?.items) ? payload.items[0] : null);
        const residentId = String(association?.value?.resident_id || "").trim();
        state.personalAmaResidentId = state.residents.some(
          item => String(item?.resident_id || "").trim() === residentId,
        ) ? residentId : "";
      }

      async function loadChatrooms() {
        const requestSeq = ++state.chatroomRequestSeq;
        if ($id("chatroomList") && !state.chatroomRecords.length) {
          $id("chatroomList").innerHTML = consoleStateHtml("正在读取聊天室。", { state: "loading" });
        }
        try {
          const [pins, chatrooms] = await Promise.all([
            window.PortalIdentity.listUIAssociations(CHATROOM_UI_NAMESPACE),
            window.PortalConversationStore.readChatrooms(),
            loadChatroomUsers(),
          ]);
          if (requestSeq !== state.chatroomRequestSeq) return;
          applyChatroomPins(pins);
          applyChatroomRecords(chatrooms?.chatrooms);
          renderChatroomList();
        } catch (err) {
          if (requestSeq !== state.chatroomRequestSeq) return;
          $id("chatroomList").innerHTML = consoleStateHtml(`加载失败：${err.message || err}`, { state: "error" });
        }
      }

      function applyChatroomRecords(records) {
        const wasListed = Boolean(chatroomRecord(state.activeChatroomId));
        state.chatroomRecords = Array.isArray(records) ? records : [];
        const active = chatroomRecord(state.activeChatroomId);
        if (active) syncChatroomPanelTitle(active);
        else if (wasListed) {
          // The open Chatroom was deleted by its creator (or this user left it elsewhere).
          closeChatroomView();
          window.PortalUI?.toast?.("这个聊天室已被删除，或你已不再是成员。", "info");
        }
      }

      function chatroomRecord(chatroomId) {
        return state.chatroomRecords.find(item => String(item?.chatroom_id || "") === String(chatroomId || "")) || null;
      }

      // A Chatroom is shown over the Chat panel; the Resident conversation below it
      // keeps its own target and comes back when a Resident is selected again.
      function chatroomHost() {
        const panelRoot = agentChatPanel?.workspace?.root;
        const container = panelRoot?.parentElement;
        if (!container) return null;
        if (!chatroomOverlay.host) {
          chatroomOverlay.host = document.createElement("section");
          chatroomOverlay.host.className = "lineageChatroomHost";
          chatroomOverlay.host.setAttribute("aria-label", "Chatroom");
          chatroomOverlay.host.hidden = true;
        }
        if (chatroomOverlay.host.parentElement !== container) {
          container.classList.add("lineageChatroomContainer");
          container.append(chatroomOverlay.host);
        }
        if (!chatroomOverlay.view) {
          chatroomOverlay.view = window.PortalChatroomView.mount({
            root: chatroomOverlay.host,
            id: "lineage-chatroom",
            me: () => state.me || "",
            authorName: chatroomAuthorName,
            title: chatroomDisplayTitle,
            onMenu: (trigger, id, event) => openChatroomMenu(trigger, id, event),
            onRoomChange: room => {
              if (!room || room.chatroom_id !== state.activeChatroomId) return;
              const index = state.chatroomRecords.findIndex(item => item.chatroom_id === room.chatroom_id);
              if (index >= 0) state.chatroomRecords[index] = { ...state.chatroomRecords[index], ...room };
              syncChatroomPanelTitle(room);
              if (state.sideView === "chatrooms") renderChatroomList();
            },
            onError: error => window.PortalUI.toast(error.message || String(error), "error"),
          });
        }
        return chatroomOverlay.host;
      }

      function syncChatroomPanelTitle(chatroom) {
        if (!chatroom || chatroom.chatroom_id !== state.activeChatroomId) return;
        renderChatHeader();
      }

      function syncChatroomLocation() {
        const target = new URL(window.location.href);
        if (state.activeChatroomId) target.searchParams.set("chatroom", state.activeChatroomId);
        else target.searchParams.delete("chatroom");
        history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
      }

      async function selectChatroom(chatroomId) {
        const id = String(chatroomId || "").trim();
        if (!/^chatroom_[0-9a-f]{12}$/.test(id)) return false;
        lineageWorkbench?.focusPanel?.(agentChatPanel?.instanceId?.() || "chat");
        const host = chatroomHost();
        if (!host) throw new Error("Chat 面板尚未就绪");
        if (state.activeChatroomId !== id) chatroomMenuPopover?.close();
        state.activeChatroomId = id;
        host.hidden = false;
        // The Resident Chat below is not being read while a Chatroom covers it.
        host.parentElement?.classList.add("chatroomActive");
        syncChatroomLocation();
        if (state.sideView === "chatrooms") renderChatroomList();
        const room = await chatroomOverlay.view.setChatroom(id);
        if (state.activeChatroomId !== id) return false;
        if (room && !chatroomRecord(id)) {
          state.chatroomRecords = [room, ...state.chatroomRecords];
          if (state.sideView === "chatrooms") renderChatroomList();
        }
        syncChatroomPanelTitle(room || chatroomRecord(id));
        await chatroomOverlay.view.revealNextMention();
        return true;
      }

      function closeChatroomView() {
        if (!state.activeChatroomId) return;
        if (chatroomMenuTarget?.id === state.activeChatroomId) chatroomMenuPopover?.close();
        state.activeChatroomId = "";
        if (chatroomOverlay.host) {
          chatroomOverlay.host.hidden = true;
          chatroomOverlay.host.parentElement?.classList.remove("chatroomActive");
        }
        syncChatroomLocation();
        renderChatHeader();
        if (state.sideView === "chatrooms") renderChatroomList();
      }

      async function setChatroomPinned(chatroomId, pinned) {
        if (pinned) {
          await window.PortalIdentity.putUIAssociation(CHATROOM_UI_NAMESPACE, chatroomId, {
            chatroom_id: chatroomId, pinned_at: new Date().toISOString(),
          });
        } else {
          await window.PortalIdentity.removeUIAssociation(CHATROOM_UI_NAMESPACE, chatroomId);
        }
        await loadChatrooms();
      }

      function openChatroomDialog(markup) {
        const overlay = document.createElement("div");
        overlay.className = "portalDetailOverlay lineageDialogOverlay chatroomDialogOverlay";
        overlay.hidden = true;
        overlay.innerHTML = markup;
        document.body.append(overlay);
        let dialog = null;
        let closed = false;
        const close = () => {
          if (closed) return;
          closed = true;
          dialog?.close?.({ force: true });
          dialog?.destroy?.();
          overlay.remove();
        };
        dialog = window.PortalUI.createDialogController({
          modal: overlay,
          bodyClass: null,
          backdropClose: true,
          escapeClose: true,
          requestClose: close,
        });
        dialog.open();
        return { overlay, close };
      }

      // One user picker serves “新建聊天室” and “添加成员”.
      async function openChatroomUserPicker({ title, confirmLabel, exclude = [], withTitle = false, onConfirm }) {
        const candidates = (await loadChatroomUsers({ force: true }))
          .filter(user => !sameChatroomActor(user.username, state.me))
          .filter(user => !exclude.some(id => sameChatroomActor(id, user.username)))
          .map(user => ({
            id: String(user.username || ""),
            title: String(user.display_name || user.username || ""),
            subtitle: String(user.username || ""),
          }))
          .filter(item => item.id);
        const { overlay, close } = openChatroomDialog(window.PortalI18n.html`
          <div class="portalDetailSurface chatroomPicker" data-portal-detail-surface role="dialog" aria-modal="true" aria-label="${esc(title)}">
            <div class="portalDetailHeader">
              <h2>${esc(title)}</h2>
              <button class="portalDetailClose" data-portal-icon="close" type="button" data-chatroom-picker-close aria-label="关闭" title="关闭"></button>
            </div>
            <div class="portalDetailForm">
              ${withTitle ? window.PortalI18n.t('<label class="residentField"><span>聊天室名称（可选）</span><input class="residentInput" data-chatroom-picker-title maxlength="80" autocomplete="off" /></label>') : ""}
              <input class="residentInput" data-chatroom-picker-search type="search" placeholder="搜索用户" autocomplete="off" />
              <div class="chatroomPickerList" data-chatroom-picker-list role="listbox" aria-multiselectable="true"></div>
              <div class="chatroomPickerHint" data-chatroom-picker-hint></div>
            </div>
            <div class="portalDetailFooter">
              <button class="portalButton" type="button" data-chatroom-picker-close>取消</button>
              <button class="portalButton is-primary" type="button" data-chatroom-picker-confirm ${withTitle ? "" : "disabled"}>${esc(confirmLabel)}</button>
            </div>
          </div>`);
        const selected = new Set();
        const list = overlay.querySelector("[data-chatroom-picker-list]");
        const search = overlay.querySelector("[data-chatroom-picker-search]");
        const confirm = overlay.querySelector("[data-chatroom-picker-confirm]");
        const hint = overlay.querySelector("[data-chatroom-picker-hint]");
        const render = () => {
          const query = String(search.value || "").trim().toLowerCase();
          const visible = candidates.filter(item => !query
            || item.title.toLowerCase().includes(query)
            || item.subtitle.toLowerCase().includes(query)).slice(0, 200);
          list.innerHTML = visible.length ? visible.map(item => window.PortalI18n.html`
            <button type="button" class="chatroomPickerItem ${selected.has(item.id) ? "selected" : ""}" role="option"
              aria-selected="${selected.has(item.id) ? "true" : "false"}" data-chatroom-picker-item="${esc(item.id)}">
              <span class="chatroomEntryIcon" data-portal-icon="user" aria-hidden="true"></span>
              <span class="agentMain"><span class="agentTitle">${esc(item.title)}</span><span class="agentMeta">${esc(item.subtitle)}</span></span>
            </button>`).join("") : consoleStateHtml("没有匹配的用户。", { state: "empty" });
          confirm.disabled = !withTitle && !selected.size;
          hint.textContent = selected.size
            ? window.PortalI18n.text`已选择 ${selected.size} 位用户`
            : (withTitle ? window.PortalI18n.t("可以先只有自己，之后再添加成员；在消息中输入 @ 选择要召唤的 Resident。") : "");
        };
        list.addEventListener("click", event => {
          const button = event.target.closest("[data-chatroom-picker-item]");
          if (!button) return;
          const id = button.dataset.chatroomPickerItem || "";
          if (selected.has(id)) selected.delete(id);
          else selected.add(id);
          render();
        });
        search.addEventListener("input", render);
        overlay.querySelectorAll("[data-chatroom-picker-close]").forEach(button => button.addEventListener("click", close));
        confirm.addEventListener("click", async () => {
          confirm.disabled = true;
          try {
            const chosenTitle = String(overlay.querySelector("[data-chatroom-picker-title]")?.value || "").trim();
            await onConfirm([...selected], chosenTitle);
            close();
          } catch (error) {
            window.PortalUI.toast(error.message || String(error), "error");
            confirm.disabled = false;
          }
        });
        render();
        (overlay.querySelector("[data-chatroom-picker-title]") || search).focus();
      }

      function openCreateChatroomPicker() {
        return openChatroomUserPicker({
          title: window.PortalI18n.t("新建聊天室"),
          confirmLabel: "创建",
          withTitle: true,
          onConfirm: async (members, title) => {
            const result = await window.PortalInstructionClient.execute("CreateChatroom", {
              members,
              ...(title ? { title } : {}),
            }, { idempotencyKey: `chatroom-create:${globalThis.PortalUIPrimitives.randomUUID()}` });
            setSideView("chatrooms");
            await loadChatrooms();
            await selectChatroom(result?.chatroom?.chatroom_id);
          },
        });
      }

      async function afterChatroomChange(chatroomId) {
        await loadChatrooms();
        if (state.activeChatroomId === chatroomId) chatroomOverlay.view?.refresh?.invalidate?.({ delay: 0 });
      }

      async function handleChatroomAction(action, chatroomId) {
        const chatroom = chatroomRecord(chatroomId);
        if (!chatroom) return;
        if (action === "mute" || action === "unmute") {
          await window.PortalInstructionClient.execute("UpdateChatroom", { chatroom_id: chatroomId, muted: action === "mute" });
          await window.PortalConversationStore.readChatrooms(chatroomId);
          await afterChatroomChange(chatroomId);
          return;
        }
        if (action === "pin" || action === "unpin") {
          await setChatroomPinned(chatroomId, action === "pin");
        } else if (action === "rename") {
          const title = await window.PortalUI.prompt({
            title: window.PortalI18n.t("聊天室改名"),
            label: window.PortalI18n.t("聊天室名称"),
            value: chatroom.title || "",
            confirmLabel: window.PortalI18n.t("保存"),
          });
          if (title === null) return;
          await window.PortalInstructionClient.execute("UpdateChatroom", {
            chatroom_id: chatroomId, title: String(title).trim(),
          }, { idempotencyKey: `chatroom-rename:${globalThis.PortalUIPrimitives.randomUUID()}` });
          await afterChatroomChange(chatroomId);
        } else if (action === "add-members") {
          await openChatroomUserPicker({
            title: window.PortalI18n.t("添加成员"),
            confirmLabel: "添加",
            exclude: chatroom.members || [],
            onConfirm: async members => {
              await window.PortalInstructionClient.execute("UpdateChatroom", {
                chatroom_id: chatroomId, add_members: members,
              }, { idempotencyKey: `chatroom-add:${globalThis.PortalUIPrimitives.randomUUID()}` });
              await afterChatroomChange(chatroomId);
            },
          });
        } else if (action === "leave") {
          const ok = await window.PortalUI.confirm({
            title: window.PortalI18n.t("退出聊天室？"),
            message: window.PortalI18n.text`确定退出「${chatroomDisplayTitle(chatroom)}」吗？`,
            description: window.PortalI18n.t("退出后不再看到这个聊天室；你是最后一位成员时，聊天室会被删除。"),
            confirmLabel: window.PortalI18n.t("退出"),
            tone: "danger",
          });
          if (!ok) return;
          await window.PortalInstructionClient.execute("LeaveChatroom", { chatroom_id: chatroomId }, {
            idempotencyKey: `chatroom-leave:${globalThis.PortalUIPrimitives.randomUUID()}`,
          });
          await window.PortalIdentity.removeUIAssociation(CHATROOM_UI_NAMESPACE, chatroomId).catch(() => {});
          if (state.activeChatroomId === chatroomId) closeChatroomView();
          await loadChatrooms();
        } else if (action === "delete") {
          const ok = await window.PortalUI.confirm({
            title: window.PortalI18n.t("删除聊天室？"),
            message: window.PortalI18n.text`确定删除「${chatroomDisplayTitle(chatroom)}」吗？`,
            description: window.PortalI18n.t("删除后所有成员都不再看到这个聊天室和其中的消息，被 @ 过的 Resident 也不能再读取。此操作不能撤销。"),
            confirmLabel: window.PortalI18n.t("删除"),
            tone: "danger",
          });
          if (!ok) return;
          await window.PortalInstructionClient.execute("DeleteChatroom", { chatroom_id: chatroomId }, {
            idempotencyKey: `chatroom-delete:${globalThis.PortalUIPrimitives.randomUUID()}`,
          });
          await window.PortalIdentity.removeUIAssociation(CHATROOM_UI_NAMESPACE, chatroomId).catch(() => {});
          if (state.activeChatroomId === chatroomId) closeChatroomView();
          await loadChatrooms();
        }
      }

      function favoriteTargets() {
        const targets = [];
        const representedSelections = new Set();
        const projects = window.PortalPrincipalNavigation.projectGroups(state.principalHierarchy, state.residents || []);
        for (const project of projects) {
          const selectionId = projectFavoriteSelectionId(project);
          if (!isFavoriteAgent(selectionId)) continue;
          representedSelections.add(selectionId);
          targets.push({ selectionId, target: { type: "project", item: { ...project, favoriteSelectionId: selectionId } } });
        }
        for (const item of lineageSidebarRoots("", { respectFilters: false })) {
          const selectionId = lineageFavoriteSelectionId(item);
          if (!selectionId || representedSelections.has(selectionId) || !isFavoriteLineageSidebarItem(item)) continue;
          representedSelections.add(selectionId);
          lineageSidebarResidentsForItem(item)
            .map(member => residentSelectionId(member))
            .filter(Boolean)
            .forEach(memberSelectionId => representedSelections.add(memberSelectionId));
          targets.push({
            selectionId,
            target: { type: "resident", item },
          });
        }
        for (const selectionId of state.favoriteAgentIds) {
          if (representedSelections.has(selectionId)) continue;
          const status = state.favoriteStatuses[selectionId] || { status: "unavailable" };
          const resident = residentByMenuId(selectionId);
          if (resident) targets.push({ selectionId, target: { type: "resident", item: resident } });
          else if (selectionId.startsWith("project:") && status.status === "denied") targets.push({ selectionId, target: { type: "project", item: {
            id: selectionId, project_id: selectionId.slice(8), title: selectionId.slice(8), items: [],
            accessible: false, unavailable: true, favoriteSelectionId: selectionId,
          } } });
          else if (selectionId.startsWith("lineage:") && status.status === "available" && status.leader_resident_id) {
            targets.push({ selectionId, target: { type: "resident", item: {
              resident_id: status.leader_resident_id, lineage_id: selectionId.slice(8),
              lineage: { title: status.title, leader_resident_id: status.leader_resident_id },
            } } });
          } else if (/^(project|lineage):/.test(selectionId)) {
            targets.push({ selectionId, target: { type: "unavailable", item: status } });
          }
        }
        return targets;
      }

      function favoriteTargetHtml(row) {
        const target = row?.target;
        if (!target) return "";
        if (target.type === "project") return projectCardHtml(target.item);
        if (target.type === "unavailable") {
          const kind = row.selectionId.startsWith("project:") ? "Project" : "Lineage";
          const status = target.item.status;
          const denied = status === "denied";
          const reason = { deleted: "已删除", missing: "资源不存在", denied: "无访问权限", available: "目录暂未载入", unavailable: "暂时无法确认状态" }[status] || "暂时无法确认状态";
          return window.PortalI18n.html`<div class="agentItem principal${kind}Card principalUnavailableCard ${denied ? "locked" : ""}" data-favorite-status="${esc(status)}"><div class="agentSelect noStatusDot"><span class="agentMain"><span class="agentTitle">${esc(target.item.title || window.PortalI18n.text`${reason}的 ${kind}`)}</span><span class="agentMeta">${kind} · ${reason}</span></span></div>${denied ? window.PortalI18n.t('<span class="principalAccessLock" data-portal-icon="lock" aria-label="无访问权限"></span>') : ''}${favoriteStar(row.selectionId)}</div>`;
        }
        return renderRegisteredResidentItem(target.item, {
          asLineage: true,
          favoriteSelectionId: row.selectionId,
        });
      }

      function favoriteResidentGroups() {
        const rows = favoriteTargets();
        return [
          {
            id: "favorite_lineage",
            title: window.PortalI18n.t("Projects 与 Lineages"),
            rows,
            projectGroups: [],
            empty: "暂无收藏的 Project 或 Lineage。",
          },
        ];
      }

      function renderFavoriteProjectSubgroups(groups) {
        return window.PortalI18n.html`<div class="residentSubgroups">${(groups || []).map(group => {
          const open = state.favoriteProjectGroups[group.id] !== false;
          return window.PortalI18n.html`<section class="residentSubgroup" data-favorite-project-group="${esc(group.id)}">
            <div class="residentSubgroupHead">
              <button class="residentSubgroupToggle" type="button" data-favorite-project-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" aria-label="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}" data-portal-icon="${open ? "chevron-up" : "chevron-down"}" data-portal-icon-class="residentChevron"></button>
              <button class="residentSubgroupName" type="button" data-favorite-project-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}">${esc(group.title)}</button>
              <span class="residentSubgroupCount">${group.rows.length}</span>
            </div>
            ${open ? window.PortalI18n.html`<div class="residentSubgroupBody">${group.rows.map(favoriteTargetHtml).join("")}</div>` : ""}
          </section>`;
        }).join("")}</div>`;
      }

      function renderFavoriteResidentGroup(group) {
        const open = state.favoriteGroups[group.id] !== false;
        const bodyHtml = group.projectGroups?.length
          ? renderFavoriteProjectSubgroups(group.projectGroups)
          : group.rows.length
            ? group.rows.map(favoriteTargetHtml).join("")
            : window.PortalI18n.html`<div class="residentEmpty">${esc(group.empty)}</div>`;
        return window.PortalI18n.html`<section class="residentSection ${open ? "open" : "collapsed"}" data-favorite-group="${esc(group.id)}">
          <div class="residentGroupHead">
            <button class="residentToggle" type="button" data-favorite-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" aria-label="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}" data-portal-icon="${open ? "chevron-up" : "chevron-down"}" data-portal-icon-class="residentChevron"></button>
            <button class="residentGroupName" type="button" data-favorite-toggle="${esc(group.id)}" aria-expanded="${open ? "true" : "false"}" title="${open ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")} ${esc(group.title)}">${esc(group.title)}</button>
          </div>
          ${open ? window.PortalI18n.html`<div class="residentGroupBody">${bodyHtml}</div>` : ""}
        </section>`;
      }

      function toggleFavoriteGroup(groupId) {
        if (!Object.prototype.hasOwnProperty.call(state.favoriteGroups, groupId)) return;
        state.favoriteGroups[groupId] = state.favoriteGroups[groupId] === false;
        writeLineageUiState({ favoriteGroups: state.favoriteGroups });
        state.agentMenuId = "";
        renderFavoriteList();
      }

      function toggleFavoriteProjectGroup(groupId) {
        if (!groupId) return;
        state.favoriteProjectGroups[groupId] = state.favoriteProjectGroups[groupId] === false;
        writeLineageUiState({ favoriteProjectGroups: state.favoriteProjectGroups });
        state.agentMenuId = "";
        renderFavoriteList();
      }

      function renderFavoriteList() {
        const root = $id("favoriteAgentList");
        if (!root) return;
        const content = state.favoriteLoadError
          ? consoleStateHtml("收藏加载失败，请点击刷新重试。", { state: "error" })
          : favoriteResidentGroups().map(renderFavoriteResidentGroup).join("");
        root.innerHTML = window.PortalI18n.html`<div class="agentList favoriteAgentList">${residentNavigationShortcutsHtml()}${content}</div>`;
        renderActivityRecentRail();
        root.querySelectorAll("[data-favorite-toggle]").forEach(btn => {
          btn.addEventListener("click", event => {
            event.stopPropagation();
            toggleFavoriteGroup(btn.dataset.favoriteToggle || "");
          });
        });
        root.querySelectorAll("[data-favorite-project-toggle]").forEach(btn => {
          btn.addEventListener("click", event => {
            event.stopPropagation();
            toggleFavoriteProjectGroup(btn.dataset.favoriteProjectToggle || "");
          });
        });
        root.querySelectorAll("[data-resident-project-toggle]").forEach(button => {
          button.addEventListener("click", () => {
            toggleResidentProjectGroup(button.dataset.residentProjectToggle);
            renderFavoriteList();
          });
        });
        bindAgentCardControls(root);
        renderAgentFloatingMenu();
      }

      function residentByMenuId(menuId) {
        const residentId = String(menuId || "").startsWith("resident:") ? String(menuId || "").slice("resident:".length) : "";
        if (!residentId) return null;
        return state.residents.find(item => String(item.resident_id || "") === residentId)
          || (state.personalAssistant?.resident_id === residentId ? state.personalAssistant : null);
      }

      function lineageByMenuId(menuId) {
        const lineageId = String(menuId || "").startsWith("lineage:")
          ? String(menuId || "").slice("lineage:".length)
          : "";
        if (!lineageId) return null;
        return lineageSidebarRoots("", { respectFilters: false })
          .find(item => lineageIdForResident(item) === lineageId) || null;
      }

      function menuTargetById(menuId) {
        if (String(menuId || "").startsWith("project:")) {
          const projectId = String(menuId).slice("project:".length);
          const project = (state.principalHierarchy?.projects || []).find(item => item.project_id === projectId);
          return project ? { type: "project", item: project } : null;
        }
        const lineage = lineageByMenuId(menuId);
        if (lineage) return { type: "lineage", item: lineage };
        const resident = residentByMenuId(menuId);
        if (resident) return { type: "resident", item: resident };
        return null;
      }

      function isFavoriteAgent(selectionId) {
        return !!selectionId && state.favoriteAgentIds.has(selectionId);
      }

      function lineageFavoriteSelectionId(item) {
        const lineageId = lineageIdForResident(item);
        return lineageId ? `lineage:${lineageId}` : "";
      }

      function isFavoriteLineageSidebarItem(item) {
        const selectionId = lineageFavoriteSelectionId(item);
        if (isFavoriteAgent(selectionId)) return true;
        const lineageResidents = lineageSidebarResidentsForItem(item);
        const candidates = lineageResidents.length ? lineageResidents : (item ? [item] : []);
        return candidates.some(member => isFavoriteAgent(residentSelectionId(member)));
      }

      function favoriteLineageRootByResidentId(residentId) {
        const root = lineageSidebarRootByResidentId(residentId);
        return isFavoriteLineageSidebarItem(root) ? root : null;
      }

      function sidebarPanelForView(view) {
        return document.querySelector(`[data-side-panel="${CSS.escape(view || state.sideView || "agents")}"]`);
      }

      function sidebarElementForTarget(panel, { residentId = "" } = {}) {
        if (!panel) return null;
        const cleanResidentId = String(residentId || "").trim();
        if (cleanResidentId) {
          const forkButton = panel.querySelector(`[data-member-resident-id="${CSS.escape(cleanResidentId)}"]`);
          if (forkButton) return forkButton.closest(".residentMemberItem") || forkButton;
          return panel.querySelector(`[data-resident-card="${CSS.escape(cleanResidentId)}"]`)
            || panel.querySelector(`[data-resident-id="${CSS.escape(cleanResidentId)}"]`);
        }
        return null;
      }

      function scrollSidebarTargetIntoView(view, target) {
        window.requestAnimationFrame(() => {
          const panel = sidebarPanelForView(view);
          const el = sidebarElementForTarget(panel, target);
          if (el) el.scrollIntoView({ block: "center", inline: "nearest" });
        });
      }

      function revealResidentSidebarTarget(record) {
        const residentId = String(record?.resident_id || "").trim();
        if (!residentId) {
          renderAgentList();
          renderFavoriteList();
          return;
        }
        focusPreferredSidebarTarget({ residentId });
      }

      function ensureResidentSidebarTargetVisible(item, { preserveQuery = false } = {}) {
        if (!item) return;
        state.residentGroups.lineage = true;
        const meta = projectMetaForAgent(item);
        if (meta) state.residentProjectGroups[`lineage:${meta.id}`] = true;
        const patch = {
          residentGroups: state.residentGroups,
          residentProjectGroups: state.residentProjectGroups,
        };
        if (!preserveQuery && state.query.trim()) {
          state.query = "";
          const input = $id("searchInput");
          if (input) input.value = "";
          patch.query = state.query;
        }
        const itemProject = String(item.project_id || item.project || "");
        if (state.projectFilter !== "all" && state.projectFilter !== itemProject) {
          state.projectFilter = "all";
          patch.projectFilter = state.projectFilter;
        }
        writeLineageUiState(patch);
      }

      function ensureFavoriteSidebarTargetVisible(item) {
        if (!item) return;
        state.favoriteGroups.favorite_lineage = true;
        const meta = projectMetaForAgent(item);
        if (meta) state.favoriteProjectGroups[`favorite_lineage:${meta.id}`] = true;
        writeLineageUiState({
          favoriteGroups: state.favoriteGroups,
          favoriteProjectGroups: state.favoriteProjectGroups,
        });
      }

      function focusPreferredSidebarTarget(target = {}) {
        const residentId = String(target.residentId || "").trim();
        const selectionId = residentId ? residentSelectionId(residentId) : "";
        const menuTarget = selectionId ? menuTargetById(selectionId) : null;
        const lineageRoot = residentId ? lineageSidebarRootByResidentId(residentId) : null;
        const favoriteLineageRoot = residentId ? favoriteLineageRootByResidentId(residentId) : null;
        if (favoriteLineageRoot) {
          ensureFavoriteSidebarTargetVisible(favoriteLineageRoot);
          setSideView("favorites");
          renderFavoriteList();
          scrollSidebarTargetIntoView("favorites", { residentId });
          return;
        }
        if (menuTarget?.type === "resident") {
          ensureResidentSidebarTargetVisible(
            lineageRoot || menuTarget.item,
            { preserveQuery: Boolean(target.preserveQuery) },
          );
          setSideView("agents");
          renderAgentList();
          scrollSidebarTargetIntoView("agents", { residentId });
          return;
        }
        setSideView("agents");
        renderAgentList();
        scrollSidebarTargetIntoView("agents", { residentId });
      }

      function bindAgentCardControls(root) {
        root.querySelectorAll("[data-project-lineage-create]").forEach(button => button.addEventListener("click", async event => {
          event.stopPropagation();
          button.disabled = true;
          try {
            if (!lineageWorkbench?.isPanelVisible("hatg")) lineageWorkbench?.showPanel("hatg", { zone: "right" });
            const panel = lineageWorkbench?.getPanel("hatg");
            if (!panel?.createLineage) throw new Error("Lineage 创建面板尚未就绪，请刷新重试。");
            await panel.createLineage({ projectId: button.dataset.projectLineageCreate });
          } catch (error) {
            window.PortalUI.toast(window.PortalI18n.text`打开 Lineage 创建表单失败：${error.message || error}`, "error");
          } finally {
            button.disabled = false;
          }
        }));
        root.querySelectorAll("[data-principal-open]").forEach(button => button.addEventListener("click", async event => {
          event.stopPropagation();
          let id = button.dataset.principalOpen;
          const lineageId = button.dataset.lineageOpen || "";
          if (!id && lineageId) {
            try {
              if (!lineageWorkbench?.isPanelVisible("hatg")) lineageWorkbench?.showPanel("hatg", { zone: "right" });
              await lineageWorkbench.openResource({
                type: "hatg",
                mode: "reuse",
                resource: { kind: "hatg", lineageId },
              });
            } catch (error) {
              window.PortalUI.toast(window.PortalI18n.text`打开 Lineage 失败：${error.message || error}`, "error");
            }
            return;
          }
          if (id) {
            if (!lineageWorkbench?.isPanelVisible("hatg")) lineageWorkbench?.showPanel("hatg", { zone: "right" });
            if (!lineageWorkbench?.isPanelVisible("chat")) lineageWorkbench?.showPanel("chat");
            selectResident(id, { lineageId, forceView: true, revealInGraph: true }).catch(error => window.PortalUI.toast(error.message, "error"));
          }
        }));
        root.querySelectorAll("[data-resident-select]").forEach(btn => {
          let residentSelectTimer = 0;
          btn.addEventListener("click", event => {
            if (event.detail > 1) return;
            const residentId = btn.dataset.residentSelect || "";
            residentSelectTimer = window.setTimeout(() => handleResidentCardClick(residentId), 300);
          });
          btn.addEventListener("dblclick", event => {
            event.preventDefault();
            event.stopPropagation();
            window.clearTimeout(residentSelectTimer);
            const residentId = String(btn.dataset.residentSelect || "").trim();
            const recommendation = String(state.recommendationResident?.resident_id || "") === residentId
              ? state.recommendationResident
              : null;
            const target = menuTargetById(`resident:${residentId}`)
              || (recommendation ? { type: "resident", item: recommendation } : null);
            if (!target) return;
            void openNavigationEditor(target, "new-tab")
              .catch(error => window.PortalUI.toast(window.PortalI18n.text`打开编辑器失败：${error.message || error}`, "error"));
          });
        });
        root.querySelectorAll("[data-resident-member-select]").forEach(btn => {
          btn.addEventListener("click", event => {
            event.stopPropagation();
            selectDerivedResident(btn.dataset.residentMemberSelect || "", btn.dataset.memberResidentId || "");
          });
        });
        root.querySelectorAll("[data-resident-member-menu]").forEach(btn => {
          btn.addEventListener("click", event => {
            event.stopPropagation();
            state.agentMenuId = "";
            state.residentMemberMenuId = state.residentMemberMenuId === btn.dataset.residentMemberMenu ? "" : (btn.dataset.residentMemberMenu || "");
            renderAgentList();
            renderFavoriteList();
          });
        });
        root.querySelectorAll("[data-resident-member-action]").forEach(btn => {
          btn.addEventListener("click", event => {
            event.stopPropagation();
            handleResidentMemberCardAction(btn.dataset.residentMemberAction || "", btn.dataset.residentId || "", btn.dataset.memberResidentId || "");
          });
        });
        root.querySelectorAll("[data-agent-menu]").forEach(btn => {
          btn.addEventListener("click", event => {
            event.stopPropagation();
            state.residentMemberMenuId = "";
            state.agentMenuId = state.agentMenuId === btn.dataset.agentMenu ? "" : (btn.dataset.agentMenu || "");
            renderAgentList();
            renderFavoriteList();
          });
        });
        root.querySelectorAll("[data-agent-favorite]").forEach(btn => {
          btn.addEventListener("click", event => {
            event.preventDefault();
            event.stopPropagation();
            const selectionId = btn.dataset.agentFavorite || "";
            setAgentFavorite(selectionId, !isFavoriteAgent(selectionId));
          });
        });
      }

      function agentMenuButton(instanceId) {
        for (const btn of document.querySelectorAll(".sideView.active [data-agent-menu]")) {
          if (btn.dataset.agentMenu === instanceId) return btn;
        }
        for (const btn of document.querySelectorAll("[data-agent-menu]")) {
          if (btn.dataset.agentMenu === instanceId) return btn;
        }
        return null;
      }

      function residentMemberMenuButton(menuId) {
        for (const btn of document.querySelectorAll(".sideView.active [data-resident-member-menu]")) {
          if (btn.dataset.residentMemberMenu === menuId) return btn;
        }
        for (const btn of document.querySelectorAll("[data-resident-member-menu]")) {
          if (btn.dataset.residentMemberMenu === menuId) return btn;
        }
        return null;
      }

      function residentMemberMenuTargetById(menuId) {
        const text = String(menuId || "");
        const splitAt = text.indexOf(":");
        if (splitAt < 0) return null;
        const residentId = text.slice(0, splitAt);
        const derivedResidentId = text.slice(splitAt + 1);
        if (!residentId || !derivedResidentId) return null;
        const resident = state.residents.find(item => String(item?.resident_id || "") === residentId) || null;
        const derivedResident = derivedResidentForSidebar(residentId, derivedResidentId, resident);
        return derivedResident ? { residentId, derivedResident } : null;
      }

      function menuActionsHtml(target) {
        const creatorHtml = ["resident", "lineage"].includes(target?.type)
          ? window.PortalI18n.html`<div class="agentMeta" data-principal-created-by>${esc(window.PortalI18n.text`创建者：${String(target.item?.created_by || "-")}`)}</div>`
          : "";
        const openActions = editorOpenMenuActionsHtml({
          attributes: action => `data-agent-floating-action="${esc(action)}"`,
        });
        if (target?.type === "project") {
          return `${target.item.lineage_id ? openActions : ""}${window.PortalI18n.html`<button class="agentCardAction" type="button" data-agent-floating-action="menu-scope" title="打开 Project Menu">Menu</button>`}`;
        }
        if (target?.type === "lineage") {
          const allows = lineageAllows(target.item?.lineage_id);
          const manageActions = window.PortalI18n.html`<button class="agentCardAction" type="button" data-agent-floating-action="menu-scope" title="打开 Lineage Menu">Menu</button>
              ${allows.CreateResident === true ? window.PortalI18n.html`<button class="agentCardAction" type="button" data-agent-floating-action="attach-resident" title="向此 Lineage 添加正式 Resident">Attach</button>` : ""}
              ${lineageContainsPersonalAmaLeader(target.item) || allows.DeleteLineage !== true ? "" : window.PortalI18n.html`<button class="agentCardAction danger" type="button" data-agent-floating-action="delete-lineage" title="删除整条 Lineage">删除</button>`}`;
          return `${creatorHtml}${openActions}${manageActions}`;
        }
        if (target?.type === "resident") {
          const canDelete = canDeleteResident(target.item);
          return `${creatorHtml}${openActions}${residentCardMenuActionsHtml({
            canEdit: lineageAllows(target.item?.lineage_id).UpdateResident === true,
            canDelete,
            actionName: action => `${action}-resident`,
            attributes: action => `data-agent-floating-action="${esc(`${action}-resident`)}"`,
          })}`;
        }
        return "";
      }

      function navigationEditorIntent(target) {
        if (target?.type === "project") return navigationEditorIntent({ type: "lineage", item: target.item });
        const item = target?.item || {};
        if (target?.type === "lineage") {
          const lineageId = lineageIdForResident(item);
          const candidateResident = lineageLeaderResidentId(item) || String(item?.resident_id || "").trim();
          const residentId = /^resident_[0-9a-f]{12}$/.test(candidateResident) ? candidateResident : "";
          return {
            type: "hatg",
            resource: {
              kind: "hatg",
              lineageId,
              residentId,
              title: String(item?.lineage?.title || item?.lineage_title || item?.title || lineageId || "HATG"),
            },
          };
        }
        if (target?.type === "resident") {
          const residentId = residentExactId(item);
          return {
            type: "chat",
            resource: {
              kind: "chat",
              resident_id: residentId,
              lineage_id: String(item?.lineage_id || item?.lineage?.lineage_id || "").trim(),
              title: String(item?.title || item?.name || item?.label || residentId || window.PortalI18n.t("Chat Workspace")),
            },
          };
        }
        return null;
      }

      function openNavigationEditor(target, mode) {
        const intent = navigationEditorIntent(target);
        if (!intent || !lineageWorkbench) return Promise.resolve(null);
        return Promise.resolve(lineageWorkbench.openResource({
          ...intent,
          mode,
          ...(mode === "side" ? { side: "right" } : {}),
        }));
      }

      function editorOpenCommandMenu() {
        if (lineageEditorOpenCommands) return lineageEditorOpenCommands;
        lineageEditorOpenCommands = window.PortalEditorOpenCommands.create({
          owner: $id("lineageWorkbenchRoot"),
          label: window.PortalI18n.t("打开编辑器"),
          onCommand: (command, context) => context?.open?.(command),
          additionalCommands: context => [
            ...(context?.reuseLabel && typeof context?.reuse === "function" ? [{
              id: "open-reuse", label: context.reuseLabel,
              run: () => context.reuse(),
            }] : []),
            ...(context?.openFamilyStatus ? [{ id: "family-status", label: window.PortalI18n.t("Family 状态"), run: () => context.openFamilyStatus() }] : []),
            ...(context?.principalId ? [{
              id: "backpack", label: window.PortalI18n.t("Menu"),
              run: () => context.openDetails ? context.openDetails() : window.PortalNav.openPrincipalBackpack({ principalId: context.principalId }),
            }] : []),
          ],
        });
        return lineageEditorOpenCommands;
      }

      function showEditorOpenMenu({ event, trigger, open, principalId = "", openDetails, openFamilyStatus, reuseLabel = "", reuse }) {
        if (typeof open !== "function") return;
        editorOpenCommandMenu().open({
          event,
          trigger,
          context: { open, principalId, openDetails, openFamilyStatus, reuseLabel, reuse },
        });
      }

      function panelIdsInLayout(node, ids = new Set()) {
        if (!node) return ids;
        if (node.type === "stack") (node.children || []).forEach(child => child?.panelId && ids.add(String(child.panelId)));
        else (node.children || []).forEach(child => panelIdsInLayout(child, ids));
        return ids;
      }

      function showResidentEditorOpenMenu(detail = {}) {
        const residentId = String(detail.residentId || "").trim();
        const lineageId = String(detail.lineageId || "").trim();
        if (!residentId) return;
        const resource = {
          kind: "chat",
          resident_id: residentId,
          lineage_id: lineageId,
          title: String(detail.title || residentId),
        };
        showEditorOpenMenu({
          event: detail.event,
          trigger: detail.trigger,
          principalId: residentId,
          openDetails: detail.openDetails,
          openFamilyStatus: detail.isFamilyFacade ? () => window.PortalChatWorkspace.openFamilyStatus(residentId, { anchor: detail.trigger }) : null,
          reuseLabel: "打开对话",
          reuse: () => {
            // The Chat the user has open for this Resident wins over one they closed earlier.
            const shown = panelIdsInLayout(lineageWorkbench.getLayout?.());
            const chats = (lineageWorkbench.getPanelsByType("chat") || []).filter(panel => (
              String(panel?.target?.()?.resident_id || "") === residentId
            ));
            const existing = chats.find(panel => shown.has(String(panel?.instanceId?.() || ""))) || chats[0];
            if (existing?.instanceId?.()) {
              lineageWorkbench.focusPanel(existing.instanceId());
              return existing;
            }
            return lineageWorkbench.openResource({ type: "chat", resource, mode: "reuse" });
          },
          open: command => lineageWorkbench.openResource({
            type: "chat",
            resource,
            mode: command.mode,
            ...(command.mode === "side" ? { side: command.side || "right" } : {}),
          }),
        });
      }

      function renderAgentFloatingMenu() {
        const menu = $id("agentFloatingMenu");
        if (!agentFloatingMenuOverlay) {
          agentFloatingMenuOverlay = window.PortalOverlay.bindDynamicPopover({
            panel: menu,
            placement: "top-end",
            gap: 6,
            margin: 8,
            portal: true,
            closeOnOutside: true,
            closeOnEscape: true,
            resolveTriggerAfterClose: previousTrigger => (
              previousTrigger.dataset.agentMenu
                ? agentMenuButton(previousTrigger.dataset.agentMenu)
                : residentMemberMenuButton(previousTrigger.dataset.residentMemberMenu || "")
            ),
            onOpen: () => menu.setAttribute("aria-hidden", "false"),
            onClose: () => {
              menu.setAttribute("aria-hidden", "true");
              const hadOpenMenu = !!(state.agentMenuId || state.residentMemberMenuId);
              state.agentMenuId = "";
              state.residentMemberMenuId = "";
              if (!hadOpenMenu) return;
              renderAgentFloatingMenu();
              renderAgentList();
              renderFavoriteList();
            },
          });
        }
        let button = null;
        let html = "";
        if (state.residentMemberMenuId) {
          const target = residentMemberMenuTargetById(state.residentMemberMenuId);
          button = residentMemberMenuButton(state.residentMemberMenuId);
          if (target) html = renderResidentMemberMenu(target.residentId, target.derivedResident);
        } else if (state.agentMenuId) {
          const target = menuTargetById(state.agentMenuId);
          button = agentMenuButton(state.agentMenuId);
          if (target) html = menuActionsHtml(target);
        }
        const contentHtml = String(html || "").trim();
        if (!button || !contentHtml) {
          agentFloatingMenuOverlay.close();
          menu.setAttribute("aria-hidden", "true");
          menu.innerHTML = "";
          return;
        }
        menu.innerHTML = contentHtml;
        agentFloatingMenuOverlay.open(button);
      }

      async function deleteResidentAgent(item) {
        const residentId = String(item?.resident_id || "").trim();
        if (!residentId) return;
        const title = item.title || residentId;
        const workspace = item.workspace_path || "";
        const ok = await window.PortalUI.confirm({
          title: window.PortalI18n.t("删除 Resident Agent？"),
          message: window.PortalI18n.text`确定删除 Resident Agent「${title}」吗？`,
          description: window.PortalI18n.t("Resident 将标记为已删除并移除对应 HATG 节点、自有工作区与模型会话；对话与运行轨迹保留。\n")
            + window.PortalI18n.t("不会删除整条 Lineage，也不会删除 TODO task。")
            + (isLineageLeaderResident(item) ? window.PortalI18n.t("\n它是所在 Lineage 的 Leader；删除后系统从其余 Resident 中随机选出新的 Leader。") : ""),
          metadata: [{ label: window.PortalI18n.t("工作区"), value: workspace || "-" }],
          confirmLabel: window.PortalI18n.t("删除"),
          tone: "danger",
        });
        if (!ok) return;
        await deleteResidentFromWorkbench({
          residentId,
          lineageId: registeredLineageIdForResident(item),
        });
      }

      async function deleteResidentFromWorkbench({
        residentId,
        lineageId = "",
        fallbackTarget = null,
      } = {}) {
        const exactResidentId = String(residentId || "").trim();
        if (!exactResidentId || pendingResidentDeletions.has(exactResidentId)) return null;
        pendingResidentDeletions.add(exactResidentId);
        let deletion = null;
        try {
          state.agentMenuId = "";
          state.residentMemberMenuId = "";
          agentFloatingMenuOverlay?.close?.();
          const result = await residentService.remove({
            lineage_id: String(lineageId || "").trim(),
            resident_id: exactResidentId,
          });
          const deletedIds = new Set([exactResidentId]);
          const deletedResidentId = String(result?.resident_id || "").trim();
          if (deletedResidentId) deletedIds.add(deletedResidentId);
          deletion = { result, deletedIds };
          await retireDeletedResidentSurfaces([...deletedIds]);

          state.residents = state.residents.filter(item => (
            !deletedIds.has(String(item?.resident_id || "").trim())
          ));
          for (const id of deletedIds) {
            dismissRecentAgentSession(recentAgentSessionKey({ resident_id: id }));
            window.PortalConversationStore?.discardResident?.(id);
          }
          clearDeletedResidentLocation(deletedIds);

          if (deletedIds.has(selectedResidentId())) {
            // Only a caller-named Resident (a derived Resident's parent) takes
            // over the Chat. Otherwise the Chat says the Resident was deleted
            // instead of opening the Lineage's Leader or another Resident.
            const nextTarget = fallbackTarget && !deletedIds.has(String(fallbackTarget.id || ""))
              ? fallbackTarget
              : null;
            if (nextTarget) {
              commitChatTarget(nextTarget);
              beginChatWorkspaceNavigation();
              writeLastSelection(nextTarget);
              syncChatTargetLocation(nextTarget);
            } else {
              state.chatNavigationError = window.PortalI18n.t("该 Resident 已删除。");
              writeLineageUiState({ lastChatTarget: null });
            }
            state.detail = null;
            state.folder = null;
            state.status = null;
            state.selectedFile = "";
            resetChatRenderState();
          }
          renderAgentList();
          renderFavoriteList();
          syncSelectedResidentSurface();
          await refreshResidentSidebarIfDue(true);
          await syncLineageHatgPanel();
          if (activeChatTargetId() && !unavailableResidentBindings.has(selectedResidentId())) {
            await Promise.allSettled([loadDetail(true), loadFolder()]);
          }
          return deletion;
        } catch (err) {
          window.PortalUI.toast(
            window.PortalUI.errorMessage(window.PortalI18n.t(deletion ? "刷新删除结果" : "删除 Resident"), err),
            "error",
          );
          return deletion;
        } finally {
          pendingResidentDeletions.delete(exactResidentId);
        }
      }

      async function deleteLineageFromConsole(item) {
        const lineageId = lineageIdForResident(item);
        if (!lineageId) return false;
        if (lineageContainsPersonalAmaLeader(item)) {
          window.PortalUI.toast(window.PortalI18n.t("Personal AMA 的 Leader Resident 不能删除。"), "warning");
          return false;
        }
        if (state.lineageDeleteSaving === lineageId) return false;
        state.lineageDeleteSaving = lineageId;
        let hatgPanel = null;
        let suspendedHatgTarget = null;
        let suspendedChatTarget = null;
        try {
          const lineage = await societyService.loadLineage(lineageId);
          if (!Array.isArray(lineage?.nodes)) throw new Error("无法确认 Lineage 的完整成员，未执行删除。");
          const members = [...new Map(lineage.nodes
            .filter(node => String(node?.id || "").trim())
            .map(node => [String(node.id).trim(), node])).values()];
          const title = String(
            lineage.title
            || item?.lineage?.title
            || item?.lineage_title
            || item?.title
            || lineageId
          ).trim();
          const ok = await window.PortalUI.confirm({
            title: window.PortalI18n.t("删除 Lineage？"),
            message: window.PortalI18n.text`确定删除 Lineage「${title}」吗？`,
            description: window.PortalI18n.t("Portal 会删除整条 Lineage，将其 Resident 标记为已删除并移除模型会话与工作区；对话与运行轨迹保留。\n")
              + window.PortalI18n.t("如果任一成员仍在运行，后端会拒绝删除。不会删除 TODO task。"),
            metadata: [
              { label: "Lineage", value: lineageId },
              { label: window.PortalI18n.t("Resident 数量"), value: String(members.length) },
            ],
            confirmLabel: window.PortalI18n.t("删除"),
            tone: "danger",
          });
          if (!ok) return false;

          const selectedResident = selectedResidentId();
          const memberIds = new Set(
            members
              .map(member => String(member?.id || "").trim())
              .filter(Boolean)
          );
          const activeTarget = currentChatTarget();
          const panelTarget = agentChatPanel?.target?.() || {};
          const activeTargetBelongsToLineage = Boolean(
            activeTarget
            && (
              String(activeTarget.lineage_id || "") === lineageId
              || memberIds.has(String(activeTarget.id || ""))
            )
          );
          const panelTargetBelongsToLineage = Boolean(
            panelTarget.resident_id
            && (
              String(panelTarget.lineage_id || "") === lineageId
              || memberIds.has(String(panelTarget.resident_id || ""))
            )
          );
          if (activeTargetBelongsToLineage) {
            suspendedChatTarget = activeTarget;
            memberIds.add(String(activeTarget.id || ""));
            commitChatTarget(null);
            beginChatWorkspaceNavigation();
          } else if (panelTargetBelongsToLineage) {
            suspendedChatTarget = residentChatTarget(
              panelTarget.resident_id,
              panelTarget.lineage_id,
              panelTarget,
            );
          }
          if (panelTargetBelongsToLineage) {
            memberIds.add(String(panelTarget.resident_id || ""));
            await Promise.resolve(agentChatPanel?.closeSurface?.());
          }
          hatgPanel = lineageWorkbench.getPanel("hatg");
          suspendedHatgTarget = hatgPanel?.suspendLineage?.(lineageId) || null;
          const result = await societyService.deleteLineage({
            lineage_id: lineageId,
          });
          for (const residentId of result?.deleted_resident_ids || result?.resident_ids || []) {
            const id = String(residentId || "").trim();
            if (id) memberIds.add(id);
          }
          await retireDeletedResidentSurfaces([...memberIds]);

          state.agentMenuId = "";
          state.lineages = state.lineages.filter(item => item.lineage_id !== lineageId);
          state.residents = state.residents.filter(resident => (
            lineageIdForResident(resident) !== lineageId
            && !memberIds.has(String(resident?.resident_id || "").trim())
          ));
          window.PortalLineageCatalogs.invalidate("residents");
          await refreshResidentSidebarIfDue(true).catch(err => {
            window.PortalUI.toast(err.message || String(err), "error");
          });
          clearDeletedLineageLocation(lineageId, memberIds);
          if (suspendedChatTarget || memberIds.has(selectedResident) || state.chatTarget?.lineage_id === lineageId) {
            commitChatTarget(firstAvailableResidentChatTarget({
              excludedLineageIds: new Set([lineageId]),
              excludedResidentIds: memberIds,
            }));
            beginChatWorkspaceNavigation();
            writeLastSelection();
            if (state.chatTarget) syncChatTargetLocation(state.chatTarget);
            state.detail = null;
            state.folder = null;
            state.status = null;
            state.selectedFile = "";
          }
          renderAgentList();
          renderFavoriteList();
          syncSelectedResidentSurface();
          hatgPanel?.applyLineageDeletion?.(lineageId);
          await syncLineageHatgPanel();
          if (activeChatTargetId()) {
            await Promise.allSettled([loadDetail(true), loadFolder()]);
          }
          window.PortalUI.toast(window.PortalI18n.t("已删除。"), "warning", 6000);
          return true;
        } catch (err) {
          if (suspendedHatgTarget) {
            await Promise.resolve(
              hatgPanel?.resumeLineage?.(suspendedHatgTarget),
            ).catch(() => {});
          }
          if (suspendedChatTarget) {
            commitChatTarget(suspendedChatTarget);
            beginChatWorkspaceNavigation();
            writeLastSelection();
            syncChatTargetLocation(suspendedChatTarget);
            syncSelectedResidentSurface();
            await agentChatPanel?.setResident?.({
              resident_id: suspendedChatTarget.id,
              lineage_id: suspendedChatTarget.lineage_id,
              title: suspendedChatTarget.title,
              backend: suspendedChatTarget.backend,
            }, {
              load: true,
              force: true,
            }).catch(() => {});
          }
          window.PortalUI.toast(
            window.PortalUI.errorMessage(window.PortalI18n.t("删除 Lineage"), err),
            "error",
          );
          return false;
        } finally {
          if (state.lineageDeleteSaving === lineageId) {
            state.lineageDeleteSaving = "";
          }
        }
      }

      function favoriteSelectionForObject(item) {
        const value = String(item || "");
        const prefix = ["lineage", "resident"].find(name => value.startsWith(`${name}_`));
        return value ? `${prefix || "project"}:${value}` : "";
      }

      async function setAgentFavorite(selectionId, favorite) {
        const id = String(selectionId || "").trim();
        if (!id) return;
        try {
          const objectId = id.includes(":") ? id.slice(id.indexOf(":") + 1) : id;
          const result = await window.PortalInstructionClient.execute("UpdateUserNavigation", {
            user_id: await window.PortalIdentity.userId(),
            object_id: objectId,
            favorite: !!favorite,
          });
          state.favoriteLoadError = "";
          state.favoriteAgentIds = new Set((Array.isArray(result?.items) ? result.items : []).map(favoriteSelectionForObject).filter(Boolean));
          state.agentMenuId = "";
          renderAgentList();
          renderFavoriteList();
        } catch (err) {
          window.PortalUI.toast(window.PortalI18n.text`${favorite ? window.PortalI18n.t("添加收藏") : window.PortalI18n.t("取消收藏")}失败：${err.message || err}`, "error");
        }
      }

      async function handleAgentCardAction(action, instanceId) {
        const target = menuTargetById(instanceId);
        if (!target) {
          state.agentMenuId = "";
          renderAgentList();
          renderFavoriteList();
          return;
        }
        state.agentMenuId = "";
        const item = target.item;
        if (action === "backpack-resident" && target.type === "resident") {
          renderAgentList(); renderFavoriteList();
          agentMenuButton(instanceId)?.focus();
          await window.PortalNav.openPrincipalBackpack({ principalId: residentExactId(item) });
          return;
        }
        if (action === "open-new-tab" || action === "open-side") {
          try {
            await openNavigationEditor(target, action === "open-side" ? "side" : "new-tab");
          } catch (err) {
            window.PortalUI.toast(window.PortalI18n.text`打开编辑器失败：${err.message || err}`, "error");
          }
          renderAgentList();
          renderFavoriteList();
          return;
        }
        if (action === "menu-scope" && ["project", "lineage"].includes(target.type)) {
          renderAgentList();
          renderFavoriteList();
          agentMenuButton(instanceId)?.focus();
          const id = target.type === "project" ? item.project_id : lineageIdForResident(item);
          const lineage = target.type === "lineage"
            ? state.lineages.find(entry => entry.lineage_id === id)
            : null;
          const members = new Map(lineageSidebarResidentsForItem(item).map(entry => [entry.resident_id, entry]));
          const record = lineage ? {
            lineage_id: id,
            project_id: lineage.project_id,
            title: lineage.title,
            description: lineage.description,
            leader_resident_id: lineage.leader_resident_id,
            nodes: (lineage.lineage_node_ids || []).map(residentId => {
              const member = members.get(residentId) || {};
              return {id: residentId, title: member.title || residentId, backend: member.backend, status: member.status};
            }),
          } : null;
          await window.PortalNav.openScopeMenu({
            kind: target.type, id, ...(record ? {record} : {}),
            onChanged: detail => ["settings", "family"].includes(detail.type) ? refreshResidentSidebarIfDue(true) : undefined,
            ...(target.type === "lineage" ? {
              onOpenAdvanced: () => lineageWorkbench?.getPanel("hatg")?.editLineage?.({
                lineageId: id,
                residentId: lineageLeaderResidentId(item) || item?.resident_id || "",
              }),
            } : {}),
          });
          return;
        }
        if (action === "delete-lineage") {
          if (target.type === "lineage") await deleteLineageFromConsole(item);
          return;
        }
        if (action === "delete-resident") {
          if (target.type === "resident" && canDeleteResident(item)) await deleteResidentAgent(item);
          return;
        }
        if (action === "edit-resident") {
          if (target.type === "resident") openResidentEditModal(item);
          renderAgentList();
          renderFavoriteList();
          return;
        }
        if (action === "attach-resident") {
          if (target.type === "lineage") openLineageAttachChoiceModal(item);
          renderAgentList();
          renderFavoriteList();
          return;
        }
        renderAgentList();
        renderFavoriteList();
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
        for (const document of previewTabsController?.snapshot?.()?.documents || []) {
          if (deleted.has(String(document.residentId || document.restoreLocator?.residentId || ""))) {
            if (previewTabsController.isActive(document.key)) stopPreviewRefresh();
            await previewTabsController.close(document.key, { reason: "resident-deleted" });
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

      function compactWhichResidentResult(result) {
        if (!result || typeof result !== "object" || Array.isArray(result)) return null;
        const recommendations = (Array.isArray(result.recommendations) ? result.recommendations : [])
          .slice(0, 8)
          .map(item => ({
            resident_id: String(item?.resident_id || ""),
            title: String(item?.title || ""),
            lineage_id: String(item?.lineage_id || ""),
            lineage_title: String(item?.lineage_title || ""),
            reason: String(item?.reason || ""),
            confidence: String(item?.confidence || ""),
            recommended_action: ["open", "fork"].includes(item?.recommended_action) ? item.recommended_action : "open",
            summary: { current_focus: String(item?.summary?.current_focus || "") },
          }))
          .filter(item => item.resident_id);
        const fork = result.fork_advice && typeof result.fork_advice === "object"
          ? result.fork_advice : {};
        return {
          schema: String(result.schema || ""),
          query: String(result.query || "").slice(0, 4000),
          summary: String(result.summary || "").slice(0, 1000),
          fallback: result.fallback === true,
          fallback_reason: String(result.fallback_reason || ""),
          recommendation_source: String(result.recommendation_source || ""),
          recommendations,
          fork_advice: {
            recommended: fork.recommended === true,
            source_resident_id: String(fork.source_resident_id || ""),
            source_title: String(fork.source_title || ""),
            source_lineage_id: String(fork.source_lineage_id || ""),
            source_lineage_title: String(fork.source_lineage_title || ""),
            reason: String(fork.reason || "").slice(0, 800),
            suggested_title: String(fork.suggested_title || "").slice(0, 200),
          },
        };
      }

      function persistWhichResidentState({ immediate = false } = {}) {
        const finder = state.whichResident || {};
        const payload = {
          version: 8,
          query: String(finder.query || "").slice(0, 4000),
          submitted_query: String(finder.submittedQuery || "").slice(0, 4000),
          result: compactWhichResidentResult(finder.result),
          request_id: String(finder.requestId || ""),
          resident_id: String(finder.residentId || ""),
          message_id: String(finder.messageId || ""),
          input_status: String(finder.inputStatus || ""),
          retry_count: Number(finder.retryCount || 0),
          error: String(finder.error || "").slice(0, 2000),
          busy: finder.busy === true,
          saved_at: new Date().toISOString(),
        };
        const patch = { whichResident: payload };
        if (immediate) writeLineageUiState(patch);
        else writeLineageUiStateSoon(patch);
      }

      let whichResidentStateHydrated = false;
      function restoreWhichResidentState(saved) {
        if (whichResidentStateHydrated) return;
        whichResidentStateHydrated = true;
        const restored = saved && typeof saved === "object" && !Array.isArray(saved)
          ? saved
          : {};
        const restoredResult = compactWhichResidentResult(restored.result);
        const restoredStatus = String(restored.input_status || "");
        state.whichResident = {
          ...state.whichResident,
          query: String(restored.query || "").slice(0, 4000),
          submittedQuery: String(restored.submitted_query || restored.result?.query || "").slice(0, 4000),
          busy: !restoredResult && !["failed", "cancelled", "unavailable", "missing"].includes(restoredStatus)
            && Boolean(restored.resident_id && restored.message_id),
          result: restoredResult,
          requestId: String(restored.request_id || ""),
          error: String(restored.error || "").slice(0, 2000),
          residentId: String(restored.resident_id || ""),
          messageId: String(restored.message_id || ""),
          inputStatus: restoredStatus,
          retryCount: Number(restored.retry_count || 0),
          resultLoading: false,
        };
        if (restoredStatus === "cancelled" && state.whichResident.retryCount < 1 && state.whichResident.submittedQuery) {
          const requestSeq = Number(state.whichResident.requestSeq || 0) + 1;
          state.whichResident.requestSeq = requestSeq;
          window.setTimeout(() => {
            if (requestSeq === state.whichResident.requestSeq && state.whichResident.inputStatus === "cancelled") {
              retryCancelledWhichResident(requestSeq);
            }
          }, 0);
        }
        if (state.whichResident.busy) {
          const requestSeq = Number(state.whichResident.requestSeq || 0) + 1;
          state.whichResident.requestSeq = requestSeq;
          window.setTimeout(() => {
            if (requestSeq !== state.whichResident.requestSeq || state.whichResident.result) return;
            try {
              bindWhichResidentInputWatch(state.whichResident, requestSeq);
            } catch (error) {
              state.whichResident.busy = false;
              state.whichResident.error = error.message || String(error);
              renderAgentList();
            }
          }, 0);
        }
      }
      const lineageLayoutStore = window.PortalWorkbench.layout.createStore({
        read: () => readLineageUiState().layout || {},
        write: layout => writeLineageUiStateSoon({ layout }),
      });
      let agentPreviewPanelComponent = null;
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
        agentPreviewPanelComponent?.applyLayout?.(
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

      function forkCreateState() {
        return state.residentForkCreate || {};
      }

      function accountSelectionConfig(selection, requestedSourceAccountId = "") {
        const payload = selection && typeof selection === "object" ? selection : {};
        const accountIds = [...new Set(
          (Array.isArray(payload.account_ids) ? payload.account_ids : [])
            .map(value => String(value || "").trim().toLowerCase())
            .filter(Boolean),
        )];
        const sourceAccountId = String(
          requestedSourceAccountId || payload.source_account_id || ""
        ).trim().toLowerCase();
        const defaultAccountId = String(
          sourceAccountId || payload.default_account_id || accountIds[0] || ""
        ).trim().toLowerCase();
        return {
          enabled: payload.enabled === true && accountIds.length > 0,
          accountIds,
          sourceAccountId,
          defaultAccountId,
        };
      }

      function residentAccountConfig() {
        const selection = state.accountSelection
          && typeof state.accountSelection === "object"
          ? { ...state.accountSelection }
          : {};
        selection.enabled = selection.enabled === true
          && residentRuntimeDefinition().account_selection?.enabled === true;
        return accountSelectionConfig(selection);
      }

      function selectedResidentRoute() {
        return String($id("residentRouteSelect")?.value || "").trim();
      }

      function renderResidentForkAccount() {
        const modal = forkCreateState();
        const field = $id("residentForkAccountField");
        const select = $id("residentForkAccountInput");
        const hint = $id("residentForkAccountHint");
        if (!field || !select || !hint) return;
        const resident = state.residents.find(item => (
          String(item?.resident_id || "") === String(modal.residentId || "")
        )) || null;
        const providerSelect = $id("residentForkProviderInput");
        const selection = window.PortalLineageCatalogs.renderForkProvider({
          catalog: modal.catalog, source: resident, detail: modal.detail,
          providerSelect, accountSelect: select, provider: modal.provider, account: modal.backendRouteId,
        });
        const ready = selection.available && !modal.loading && !modal.busy;
        providerSelect.disabled = !!modal.loading || !!modal.busy;
        select.disabled = !ready;
        if (ready) {
          modal.provider = selection.provider;
          modal.backendRouteId = selection.account;
        }
        $id("residentForkCreateBody").querySelectorAll("[data-fork-create-action]").forEach(button => {
          button.disabled = !ready || (button.dataset.forkCreateAction === "compacted" && !selection.definition?.seeded_compaction);
        });
        hint.textContent = modal.loading ? window.PortalI18n.t("正在读取 Provider 和账号…")
          : ready ? window.PortalI18n.t("在所选 Provider 和账号建立独立会话，继承所选上下文。")
            : window.PortalI18n.t("该 Provider 暂无可用账号。");
      }

      function resetResidentForkCreateState() {
        state.residentForkCreate = {
          open: false,
          residentId: "",
          lineageId: "",
          title: "",
          intent: "fork",
          loading: false,
          detail: null,
          busy: false,
          backendRouteId: undefined,
          draftText: "",
        };
      }

      function residentForkCreateDefaultModal() {
        return $id("residentForkCreateOverlay")?.querySelector(".portalDetailSurface") || null;
      }

      function restoreResidentForkCreateBody() {
        const body = $id("residentForkCreateBody");
        const modal = residentForkCreateDefaultModal();
        if (body && modal && body.parentElement !== modal) {
          modal.appendChild(body);
        }
        state.residentForkCreateInlineHost = "";
      }

      function moveResidentForkCreateBodyToAttachPanel() {
        const body = $id("residentForkCreateBody");
        const panel = $id("residentAttachForkPanel");
        if (!body || !panel) return false;
        if (body.parentElement !== panel) {
          panel.appendChild(body);
        }
        state.residentForkCreateInlineHost = "attach";
        return true;
      }

      function renderResidentForkCreateModal() {
        const modal = forkCreateState();
        const overlay = $id("residentForkCreateOverlay");
        if (!overlay) return;
        const inlineAttach = state.residentForkCreateInlineHost === "attach";
        setAgentDialogOpen(overlay, !!modal.open && !inlineAttach);
        overlay.setAttribute("aria-hidden", modal.open && !inlineAttach ? "false" : "true");
        $id("residentForkCreateTitle").textContent = modal.intent === "attach"
          ? window.PortalI18n.t("创建并附加分支 Resident")
          : modal.intent === "lineage"
            ? window.PortalI18n.t("创建 Lineage 分支")
            : window.PortalI18n.t("创建分支 Resident");
        $id("residentForkCreateSubtitle").textContent = modal.title ? modal.title : "";
        renderResidentForkAccount();
      }

      async function openResidentForkCreateModal(item, options = {}) {
        const residentId = String(item?.resident_id || item?.instance_id || "").trim();
        if (!residentId) return;
        const inline = options.inline === true;
        if (inline) {
          restoreResidentRegisterForm();
          setAgentDialogOpen("residentRegisterOverlay", false);
          $id("residentRegisterOverlay").setAttribute("aria-hidden", "true");
        } else {
          restoreResidentForkCreateBody();
        }
        state.residentForkCreate = {
          open: true,
          residentId,
          lineageId: String(
            options.lineageId || registeredLineageIdForResident(item) || "",
          ).trim(),
          title: item?.title || residentId,
          intent: ["attach", "lineage"].includes(options.intent) ? options.intent : "fork",
          loading: true,
          detail: null,
          busy: false,
          backendRouteId: undefined,
          draftText: String(options.draftText || "").trim(),
        };
        if (inline) {
          moveResidentForkCreateBodyToAttachPanel();
        }
        renderResidentForkCreateModal();
        const modal = state.residentForkCreate;
        try {
          const [detail, catalog] = await Promise.all([window.PortalChatWorkspace.loadResidentConversation(
            { resident_id: residentId },
            { historyLimit: 0 },
          ), window.PortalLineageCatalogs.agentBackends()]);
          if (state.residentForkCreate !== modal) return;
          state.residentForkCreate.detail = detail;
          state.residentForkCreate.catalog = catalog;
        } catch (err) {
          if (state.residentForkCreate !== modal) return;
          modal.detail = null;
          window.PortalUI.toast(window.PortalI18n.text`来源 Resident 读取失败：${err.message || err}`, "error");
        } finally {
          if (state.residentForkCreate === modal) {
            modal.loading = false;
            renderResidentForkCreateModal();
          }
        }
      }

      function closeResidentForkCreateModal(options = {}) {
        if (forkCreateState().busy) return;
        const inlineAttach = state.residentForkCreateInlineHost === "attach";
        restoreResidentForkCreateBody();
        resetResidentForkCreateState();
        renderResidentForkCreateModal();
        if (inlineAttach && options.keepAttachOpen !== true) {
          resetResidentAttachOptionState();
          state.residentAttachChoice = {
            open: false,
            lineageId: "",
            leaderResidentId: "",
            title: "",
          };
          renderResidentAttachChoiceModal();
        }
      }

      async function handleForkCreateAction(action) {
        const residentId = String(forkCreateState().residentId || "").trim();
        if (!residentId) return;
        const lineageId = String(forkCreateState().lineageId || "").trim();
        const backendRouteId = String(
          forkCreateState().backendRouteId || ""
        ).trim();
        const provider = forkCreateState().provider;
        const draftText = String(forkCreateState().draftText || "").trim();
        const mode = action;
        closeResidentForkCreateModal();
        await createResidentForkFromMenu(residentId, {
          mode,
          backendRouteId,
          provider,
          lineageId,
          draftText,
        });
      }

      async function completeResidentFork(response, lineageId, options = {}) {
        const residentId = String(response?.resident_id || "").trim();
        const lineage = response?.lineage;
        if (!residentId || lineage?.lineage_id !== lineageId) {
          window.PortalUI.toast(window.PortalI18n.t("分支已创建，但返回结果不完整；请刷新 Lineage 后定位新 Resident。"), "warning");
          return false;
        }
        try {
          const hatgPanel = lineageWorkbench?.getPanel("hatg");
          if (hatgPanel) {
            try {
              await hatgPanel.applyLineageMutation(lineage, { separateObjectIds: [residentId] });
            } catch (error) {
              window.PortalUI.toast(window.PortalI18n.text`分支已创建，但画布布局未保存：${error.message || error}`, "warning");
            }
          }
          rememberResidentShortcutSession(residentId);
          const opened = await selectResident(residentId, {
            forceView: true,
            lineageId,
            revealInGraph: true,
          });
          if (opened && options.draftText) appendChatWorkspaceDraft(options.draftText);
          return opened;
        } catch (err) {
          window.PortalUI.toast(window.PortalI18n.text`分支已创建，但页面未能定位新 Resident：${err.message || err}`, "warning");
          return false;
        }
      }

      async function createResidentForkFromMenu(residentId, options = {}) {
        const id = String(residentId || "").trim();
        const lineageId = String(options.lineageId || "").trim();
        if (!id || !lineageId) {
          window.PortalUI.toast(window.PortalI18n.t("来源 Resident 不属于可写入 HATG 的 Lineage，无法创建分支。"), "error");
          return;
        }
        state.agentMenuId = "";
        state.residentMemberMenuId = "";
        beginResidentSidebarMutation();
        try {
          const response = await societyService.branchNode({
            lineage_id: lineageId,
            source_resident_id: id,
            fork_mode: options.mode || "full",
            ...(options.provider ? { provider: options.provider } : {}),
            ...(options.backendRouteId ? { account_id: options.backendRouteId } : {}),
          });
          await completeResidentFork(response, lineageId, { draftText: options.draftText });
        } catch (err) {
          window.PortalUI.toast(window.PortalI18n.text`创建分支 Resident 失败：${err.message || err}`, "error");
          renderAgentList();
          renderFavoriteList();
        } finally {
          endResidentSidebarMutation();
        }
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
          previewSelectionController?.hide?.();
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

      function emptyResidentEditor() {
        return {
          residentId: "",
          title: "",
          saving: false,
        };
      }

      function openResidentEditSurface(config = {}) {
        state.residentEditor = {
          ...emptyResidentEditor(),
          residentId: String(config.residentId || ""),
          title: String(config.title || "").trim(),
          saving: false,
        };
        $id("residentEditTitle").textContent = window.PortalI18n.text`编辑 · ${state.residentEditor.title || "Resident"}`;
        $id("residentEditTitleInput").value = state.residentEditor.title;
        setAgentDialogOpen("residentEditOverlay", true);
        $id("residentEditOverlay").setAttribute("aria-hidden", "false");
        window.setTimeout(() => $id("residentEditTitleInput").focus(), 0);
      }

      function openDerivedResidentEditModal(residentId, derivedResidentId) {
        const sourceResident = state.residents.find(item => String(item.resident_id || "") === String(residentId)) || {};
        const derivedResident = derivedResidentForSidebar(residentId, derivedResidentId, sourceResident) || {};
        openResidentEditSurface({
          residentId: derivedResidentId,
          title: String(derivedResident?.title || derivedResident?.label || "").trim() || derivedResidentId,
        });
      }

      function closeResidentEditModal() {
        if (state.residentEditor.saving) return;
        setAgentDialogOpen("residentEditOverlay", false);
        $id("residentEditOverlay").setAttribute("aria-hidden", "true");
        state.residentEditor = emptyResidentEditor();
      }

      function editDerivedResidentFromSidebar(residentId, derivedResidentId) {
        openDerivedResidentEditModal(residentId, derivedResidentId);
      }

      async function submitResidentEditSurface(event) {
        event.preventDefault();
        if (state.residentEditor.saving) return;
        const residentId = state.residentEditor.residentId;
        const title = $id("residentEditTitleInput").value.trim();
        if (!residentId) return;
        if (!title) {
          window.PortalUI.toast(window.PortalI18n.t("title 不能为空。"), "warning");
          return;
        }
        state.residentEditor.saving = true;
        $id("residentEditSubmit").disabled = true;
        let record;
        try {
          record = await residentService.update({
            resident_id: residentId,
            title,
          });
        } catch (err) {
          window.PortalUI.toast(window.PortalI18n.text`更新 Resident 失败：${err.message || err}`, "error");
          return;
        } finally {
          state.residentEditor.saving = false;
          $id("residentEditSubmit").disabled = false;
        }
        closeResidentEditModal();
        try {
          await refreshResidentSidebarIfDue(true);
          renderAgentList();
          renderFavoriteList();
          if (activeChatTargetId() === residentSelectionId(record)) {
            state.folder = null;
            state.selectedFile = "";
            syncSelectedResidentSurface();
            await Promise.allSettled([loadDetail(true), loadFolder()]);
          }
        } catch (err) {
          window.PortalUI.toast(window.PortalI18n.text`Resident 名称已保存，但界面刷新失败：${err.message || err}`, "warning");
        }
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

      function crossLineageForkTargets() {
        const currentLineageId = activeLineageId();
        return lineageSidebarRoots("", { respectFilters: false }).filter(item => (
          registeredLineageIdForResident(item)
          && registeredLineageIdForResident(item) !== currentLineageId
        ));
      }

      function resetCrossLineageForkState() {
        state.crossLineageFork = {
          open: false,
          messageKey: "",
          lineageId: "",
          residentId: "",
          nodes: [],
          loading: false,
          busy: false,
          error: "",
          sourceWorkspace: null,
        };
      }

      function renderCrossLineageForkModal() {
        const modal = state.crossLineageFork || {};
        const overlay = $id("crossLineageForkOverlay");
        if (!overlay) return;
        setAgentDialogOpen(overlay, !!modal.open);
        overlay.setAttribute("aria-hidden", modal.open ? "false" : "true");
        const lineageSelect = $id("crossLineageForkLineageSelect");
        const residentSelect = $id("crossLineageForkResidentSelect");
        const targets = crossLineageForkTargets();
        lineageSelect.innerHTML = targets.length
          ? targets.map(item => {
              const lineageId = registeredLineageIdForResident(item);
              const title = String(item?.lineage?.title || lineageId);
              return window.PortalI18n.html`<option value="${esc(lineageId)}">${esc(title)}</option>`;
            }).join("")
          : window.PortalI18n.markup('<option value="">没有其他可编辑的 Lineage</option>');
        lineageSelect.value = targets.some(item => (
          registeredLineageIdForResident(item) === modal.lineageId
        )) ? modal.lineageId : "";
        residentSelect.innerHTML = modal.loading
          ? window.PortalI18n.markup('<option value="">正在读取 HATG 节点…</option>')
          : (modal.nodes || []).map(node => {
              const residentId = String(node?.id || "").trim();
              const title = String(node?.title || residentId);
              return window.PortalI18n.html`<option value="${esc(residentId)}">${esc(title)} · ${esc(residentId)}</option>`;
            }).join("") || window.PortalI18n.markup('<option value="">该 Lineage 没有可用的 Resident 节点</option>');
        residentSelect.value = (modal.nodes || []).some(node => (
          String(node?.id || "") === modal.residentId
        )) ? modal.residentId : "";
        lineageSelect.disabled = !!modal.busy || !targets.length;
        residentSelect.disabled = !!modal.busy || !!modal.loading || !(modal.nodes || []).length;
        $id("crossLineageForkHint").textContent = modal.error
          || window.PortalI18n.t("分支操作会创建继承当前对话的普通 Resident，并挂到所选 Resident 节点下。");
        $id("crossLineageForkSubmit").disabled = (
          !!modal.busy
          || !!modal.loading
          || !modal.lineageId
          || !modal.residentId
        );
      }

      async function loadCrossLineageForkNodes(lineageId) {
        const targetLineageId = String(lineageId || "").trim();
        if (!targetLineageId) return;
        state.crossLineageFork.lineageId = targetLineageId;
        state.crossLineageFork.residentId = "";
        state.crossLineageFork.nodes = [];
        state.crossLineageFork.loading = true;
        state.crossLineageFork.error = "";
        renderCrossLineageForkModal();
        try {
          const lineage = await societyService.loadLineage(targetLineageId);
          if (
            !state.crossLineageFork?.open
            || state.crossLineageFork.lineageId !== targetLineageId
          ) return;
          const nodes = (lineage?.nodes || []).filter(node => (
            String(node?.id || "").trim()
          ));
          state.crossLineageFork.nodes = nodes;
          state.crossLineageFork.residentId = String(nodes[0]?.id || "");
        } catch (err) {
          if (
            state.crossLineageFork?.open
            && state.crossLineageFork.lineageId === targetLineageId
          ) {
            state.crossLineageFork.error = `目标 HATG 读取失败：${err.message || err}`;
          }
        } finally {
          if (
            state.crossLineageFork?.open
            && state.crossLineageFork.lineageId === targetLineageId
          ) {
            state.crossLineageFork.loading = false;
            renderCrossLineageForkModal();
          }
        }
      }

      function closeCrossLineageForkModal() {
        if (state.crossLineageFork?.busy) return;
        resetCrossLineageForkState();
        renderCrossLineageForkModal();
      }

      async function submitCrossLineageFork(event) {
        event.preventDefault();
        const modal = state.crossLineageFork || {};
        if (modal.busy || !modal.messageKey || !modal.lineageId || !modal.residentId) return;
        state.crossLineageFork.busy = true;
        renderCrossLineageForkModal();
        try {
          await performForkResidentFromMessage(modal.messageKey, {
            lineageId: modal.lineageId,
            residentId: modal.residentId,
          }, modal.sourceWorkspace);
          resetCrossLineageForkState();
          renderCrossLineageForkModal();
        } catch (err) {
          state.crossLineageFork.busy = false;
          state.crossLineageFork.error = `跨 Lineage 创建分支失败：${err.message || err}`;
          renderCrossLineageForkModal();
        }
      }

      async function performForkResidentFromMessage(key, target = {}, workspace = agentChatWorkspace) {
        const residentId = residentIdForSelection(chatTargetForPanel(workspace));
        const item = workspaceMessageByKey(key, workspace);
        if (!residentId || !item || messageRole(item) !== "human") return;
        let sourceLineageId = activeLineageId();
        if (!sourceLineageId && !target.lineageId) {
          const navigationTarget = await residentDiscovery.resolveNavigationTarget(residentId);
          sourceLineageId = String(navigationTarget?.lineage_id || "").trim();
        }
        const targetLineageId = String(target.lineageId || sourceLineageId).trim();
        const targetResidentId = String(target.residentId || residentId).trim();
        if (!targetLineageId) {
          throw new Error(window.PortalI18n.t("当前 Resident 尚未登记到 Lineage，无法创建 HATG 分支"));
        }
        const message = {
          message_id: item.id,
        };
        const targetLineage = await societyService.loadLineage(targetLineageId);
        const graphParent = (targetLineage?.nodes || []).find(node => (
          String(node?.id || "") === targetResidentId
        ));
        if (!graphParent) {
          throw new Error(window.PortalI18n.t("所选 Resident 不是目标 Lineage HATG 中的正式节点"));
        }
        const graphParentId = String(graphParent.id || "").trim();
        if (!graphParentId) throw new Error(window.PortalI18n.t("当前 HATG 节点缺少 canonical node ID"));
        const response = await societyService.branchNode({
          lineage_id: targetLineageId,
          source_resident_id: graphParentId,
          context_source_resident_id: residentId,
          fork_mode: "message",
          message,
        });
        await completeResidentFork(response, targetLineageId);
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

      function safeDecodeLink(value) {
        try {
          return decodeURI(String(value || ""));
        } catch {
          return String(value || "");
        }
      }

      function linkPathWithoutQuery(value) {
        return String(value || "").split("#", 1)[0].split("?", 1)[0].trim();
      }

      function stripLocalLinkLineSuffix(value) {
        const text = String(value || "").trim();
        if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(text) && !text.startsWith("file:")) return text;
        return text.replace(/:\d+(?::\d+)?$/, "");
      }

      function normalizeLinkFsPath(value) {
        return stripLocalLinkLineSuffix(linkPathWithoutQuery(safeDecodeLink(value))).replace(/\\/g, "/").replace(/\/+$/, "");
      }

      function linkLooksLikeRelativeLocalPath(href) {
        const raw = String(href || "").trim();
        if (!raw || raw.startsWith("#") || raw.startsWith("//")) return false;
        if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) return false;
        return !raw.startsWith("/");
      }

      function normalizeJoinedPath(baseDir, relPath, absolute = false) {
        const parts = [];
        const base = String(baseDir || "").replace(/\\/g, "/");
        const rel = String(relPath || "").replace(/\\/g, "/");
        `${base}/${rel}`.split("/").forEach(part => {
          if (!part || part === ".") return;
          if (part === "..") {
            parts.pop();
            return;
          }
          parts.push(part);
        });
        return `${absolute ? "/" : ""}${parts.join("/")}`;
      }

      function previewRelativeTargetFromLink(href, payload = activePreviewPayload()) {
        if (!payload || !linkLooksLikeRelativeLocalPath(href)) return null;
        if (String(payload.source || "") === "resource") return null;
        const relTarget = normalizeLinkFsPath(href);
        if (!relTarget || relTarget.startsWith("/") || relTarget.startsWith("~")) return null;
        if (String(payload.source || "") === "workspace_link") {
          const basePath = normalizeLinkFsPath(payload.path || "");
          if (!basePath.startsWith("/")) return null;
          const baseDir = basePath.includes("/") ? basePath.split("/").slice(0, -1).join("/") : "";
          return { type: "local", path: normalizeJoinedPath(baseDir, relTarget, true) };
        }
        if (payload.resident_id || payload.directory_id) {
          const directoryId = String(payload.directory_id || residentFileParts(payload.path || "").directoryId || "").trim();
          const relPath = String(payload.rel_path || residentFileParts(payload.path || "").relPath || "").trim();
          if (!directoryId) return null;
          const baseDir = relPath.includes("/") ? relPath.split("/").slice(0, -1).join("/") : "";
          const nextRel = normalizeJoinedPath(baseDir, relTarget, false);
          return { type: "resident", path: nextRel ? `${directoryId}/${nextRel}` : directoryId };
        }
        return null;
      }

      function residentRootsForLinkPreview() {
        const roots = [];
        const seen = new Set();
        const addRoots = list => {
          (Array.isArray(list) ? list : []).forEach(root => {
            const directoryId = String(root?.directory_id || "").trim();
            if (!directoryId || seen.has(directoryId)) return;
            seen.add(directoryId);
            roots.push(root);
          });
        };
        addRoots(state.folder?.roots);
        addRoots(state.detail?.resident?.workspace_roots);
        addRoots(currentResident()?.workspace_roots);
        return roots;
      }

      function primaryResidentRootForLinkPreview(roots = residentRootsForLinkPreview()) {
        const primaryDirectoryId = String(state.folder?.primary_directory_id || "").trim();
        return roots.find(root => String(root?.directory_id || "") === primaryDirectoryId)
          || roots.find(root => root?.is_primary === true)
          || (roots.length === 1 ? roots[0] : null);
      }

      function residentVirtualPathFromLink(href) {
        if (!currentResident()) return "";
        const rawHref = String(href || "").trim();
        if (!rawHref) return "";
        const roots = residentRootsForLinkPreview();
        const rootById = new Map(roots.map(root => [String(root.directory_id || ""), root]));
        const candidates = [];
        const pushCandidate = value => {
          const clean = normalizeLinkFsPath(value);
          if (clean) candidates.push(clean);
        };
        pushCandidate(rawHref);
        try {
          const parsed = new URL(rawHref, window.location.href);
          if (parsed.protocol === "file:") {
            pushCandidate(parsed.pathname);
          } else if (parsed.origin === window.location.origin) {
            pushCandidate(parsed.pathname);
          }
        } catch {}
        for (const candidate of candidates) {
          const noLeadingSlash = candidate.replace(/^\/+/, "");
          const first = noLeadingSlash.split("/", 1)[0] || "";
          if (rootById.has(first)) return noLeadingSlash;
          if (
            !candidate.startsWith("/")
            && linkLooksLikeRelativeLocalPath(candidate)
            && window.PortalPreviewContract?.hasFileShape?.(candidate)
          ) {
            const primaryRoot = primaryResidentRootForLinkPreview(roots);
            const primaryDirectoryId = String(primaryRoot?.directory_id || "").trim();
            const rel = normalizeJoinedPath("", candidate, false);
            if (primaryDirectoryId && rel) return `${primaryDirectoryId}/${rel}`;
          }
          if (!candidate.startsWith("/")) continue;
          // The deepest root holding the path: a Workspace inside the Resident Folder wins.
          const holding = roots
            .map(root => ({ root, rootPath: normalizeLinkFsPath(root.path || "") }))
            .filter(({ rootPath }) => rootPath && (candidate === rootPath || candidate.startsWith(`${rootPath}/`)))
            .sort((a, b) => b.rootPath.length - a.rootPath.length)[0];
          if (holding) {
            const rel = candidate.slice(holding.rootPath.length + 1);
            return rel ? `${holding.root.directory_id}/${rel}` : String(holding.root.directory_id || "");
          }
        }
        return "";
      }

      async function localFilePathFromLink(href, options = {}) {
        const rawHref = String(href || "").trim();
        if (!rawHref) return "";
        const locator = window.PortalFileLinks?.classifyLocator?.(rawHref);
        if (locator?.kind === "local" && locator.path) {
          if (options.explicitPath === true || locator.requiresProbe !== true) return locator.path;
          return await window.PortalFileLinks.probe(locator.path) ? locator.path : "";
        }
        if (options.explicitPath !== true) return "";
        const normalized = window.PortalFileLinks?.normalizePath?.(rawHref) || "";
        return normalized.startsWith("/") || normalized.startsWith("~/") ? normalized : "";
      }

      async function loadLocalLinkPreview(path, label = "", options = {}) {
        const targetPath = String(path || "").trim();
        if (!targetPath) return;
        ensurePreviewVisible();
        stopPreviewRefresh();
        await openAgentPreviewDocument({
          key: `link:${targetPath}`,
          mode: options.mode,
          path: targetPath,
          title: label || fileBaseName(targetPath),
          sourceType: "link",
          locator: options.locator || "",
          line: options.line,
          lineStart: options.lineStart,
          onLoading: () => {
            setPreviewTitle(label || fileBaseName(targetPath));
            $id("previewPath").textContent = targetPath;
            setPreviewOpenInput(targetPath);
            $id("previewBox").className = "previewBox";
            $id("previewBox").textContent = window.PortalI18n.t("加载中…");
          },
          load: async () => {
            const payload = await window.PortalFileLinks.load(targetPath);
            if (options.previewTitle) payload.preview_title = String(options.previewTitle || "");
            return withInitialPreviewLine(agentPreviewSource.normalize(payload, {
              path: String(payload?.path || targetPath),
              source: "workspace_link",
            }), options);
          },
          onError: err => {
          $id("previewBox").textContent = window.PortalI18n.text`预览失败：${err.message || err}`;
          },
        });
      }

      async function openWorkspaceResource(workspaceId, { isCurrent = () => true } = {}) {
        const resolved = await apiGet(`/api/resources/resolve?address=${encodeURIComponent(workspaceId)}`);
        if (!isCurrent()) return false;
        const path = String(resolved?.record?.attributes?.path || "");
        if (resolved?.resource_type !== "workspace" || !resolved.exists || !path) {
          throw new Error("Workspace 已删除或不可用");
        }
        setSideView("workspaces", { userInitiated: true, openingResource: true });
        lineageWorkbench?.focusPanel("navigation");
        await showPersonalWorkspaces().openResource({ workspaceId, title: resolved.record.title, path });
        return isCurrent();
      }

      async function openResourcePreview(context = {}, options = {}) {
        const address = String(context.address || "").trim();
        if (!address) return false;
        ensurePreviewVisible();
        stopPreviewRefresh();
        const loadingPayload = {
          source: "resource",
          resource_address: address,
          path: address,
          preview_title: address,
          loading: true,
        };
        const result = await openAgentPreviewDocument({
          key: `resource:${address}`,
          mode: context.mode || options.mode,
          path: address,
          title: address,
          sourceType: "resource",
          resourceAddress: address,
          loadingPayload,
          onLoading: () => {
            setPreviewTitle(address);
            $id("previewPath").textContent = address;
            setPreviewOpenInput(address);
            $id("previewBox").className = "previewBox";
            $id("previewBox").textContent = window.PortalI18n.t("正在读取资源…");
          },
          load: async () => {
            const payload = await (typeof context.load === "function"
              ? context.load()
              : window.PortalResourceChips.loadPreview(address));
            return payload == null ? null : window.PortalPreviewContract.normalizeDocument({
              ...payload,
              source: "resource",
              path: String(payload.path || address),
            });
          },
          onEmpty: () => {
            if (options.navigateOnUnsupported !== false && typeof context.navigate === "function") {
              context.navigate();
            } else {
              $id("previewBox").textContent = window.PortalI18n.t("这个资源没有可在 Preview 中显示的内容。");
            }
          },
          onError: error => {
            setPreviewTitle(address);
            $id("previewPath").textContent = address;
            $id("previewBox").className = "previewBox";
            $id("previewBox").textContent = window.PortalI18n.text`资源预览失败：${error.message || error}`;
          },
        });
        return result.accepted !== false;
      }

      function openResourceChipPreview(context = {}) {
        return openResourcePreview(context, { navigateOnUnsupported: true });
      }

      async function renderChatLinkPreview(href, label = "", options = {}) {
        const displayHref = safeDecodeLink(href);
        const displayLabel = String(label || "").trim();
        return openAgentPreviewDocument({
          key: `link-card:${href}`,
          mode: options.mode,
          path: displayHref || href,
          title: displayLabel || window.PortalI18n.t("链接"),
          sourceType: "link-card",
          href,
          load: async () => window.PortalPreviewContract.normalizeDocument({
            source: "remote_link_card",
            path: displayHref || href,
            href,
            title: displayLabel || window.PortalI18n.t("链接"),
            kind: "remote-link",
            binary: true,
          }),
        });
      }

      async function openChatMarkdownLink(href, label = "", options = {}) {
        const hrefLocator = String(href || "").trim().match(/(#L\d+(?:-L\d+)?|:\d+(?::\d+)?)$/i)?.[1] || "";
        const previewOptions = {
          ...options,
          locator: options.locator || hrefLocator,
        };
        const resourceAddress = String(
          window.PortalResourceChips?.resourceAddressFromPreviewRequest?.({
            resourceAddress: options.resourceAddress,
            href,
          })
          || "",
        ).trim();
        if (resourceAddress) {
          return openResourcePreview({
            mode: options.mode,
            address: resourceAddress,
            load: () => window.PortalResourceChips.loadPreview(resourceAddress),
            navigate: () => window.location.assign(
              href || `/r/${encodeURIComponent(resourceAddress)}`,
            ),
          }, { navigateOnUnsupported: true });
        }
        const relativeTarget = options.previewPayload
          ? previewRelativeTargetFromLink(href, options.previewPayload)
          : null;
        if (relativeTarget?.type === "local") {
          return loadLocalLinkPreview(relativeTarget.path, label || fileBaseName(relativeTarget.path), previewOptions);
        }
        if (relativeTarget?.type === "resident") {
          return loadResidentFile(relativeTarget.path, previewOptions);
        }
        const targetFile = residentVirtualPathFromLink(href);
        if (targetFile) {
          return loadResidentFile(targetFile, previewOptions);
        }
        const localPath = await localFilePathFromLink(href);
        if (localPath) {
          return loadLocalLinkPreview(localPath, label, previewOptions);
        }
        return renderChatLinkPreview(href, label, previewOptions);
      }

      function bindChatMarkdownLinks(root, previewDocument = null) {
        root?.querySelectorAll(".messageContent a[href], .previewBox.markdownBody a[href]").forEach(anchor => {
          if (anchor.matches(".portalResourceChip")) return;
          if (anchor.dataset.portalResourceLinkBound === "1") return;
          if (anchor.dataset.portalFileLinkBound === "1") return;
          if (anchor.dataset.portalPreviewLinkBound === "1") return;
          if (anchor.dataset.chatLinkPreviewBound === "1") return;
          anchor.dataset.chatLinkPreviewBound = "1";
          anchor.addEventListener("contextmenu", event => {
            const href = anchor.getAttribute("href") || "";
            if (!window.PortalFileLinks.describe(href, { allowAnyLocalPath: true })
              && !linkLooksLikeRelativeLocalPath(href)) return;
            window.PortalPreview.openFileMenu({
              event, trigger: anchor, request: { href, title: anchor.textContent || "" },
              onOpen: request => openChatMarkdownLink(request.href, request.title, {
                ...request, previewPayload: previewDocument,
              }),
            });
          });
          anchor.addEventListener("click", event => {
            if (
              event.button !== 0
              || event.metaKey
              || event.ctrlKey
              || event.shiftKey
              || event.altKey
            ) return;
            event.preventDefault();
            event.stopPropagation();
            const href = anchor.getAttribute("href") || "";
            const descriptor = window.PortalFileLinks?.describe?.(href, { allowAnyLocalPath: true });
            openChatMarkdownLink(href, anchor.textContent || "", {
              previewPayload: previewDocument,
              locator: descriptor?.locator || "",
            });
          });
        });
      }

      function bindWorkspacePreviewHost() {
        lineageWorkbench?.getPanel("preview")?.registerPreviewHost("lineage", async request => {
          if (String(request.locatorKind || "") === "lineage-workspace") {
            const lineageId = String(request.lineageId || "").trim();
            const residentId = String(request.residentId || "").trim();
            const path = String(request.path || "").trim();
            if (!lineageId || !residentId || !path) return false;
            ensurePreviewVisible();
            await openAgentPreviewDocument({
              key: `lineage:${lineageId}:${residentId}:${path}`,
              mode: request.mode,
              path,
              title: request.title || fileBaseName(path),
              sourceType: "lineage-workspace",
              lineageId,
              residentId,
              locator: request.locator || "",
              line: request.line,
              lineStart: request.lineStart,
              reload: request.reload === true,
              load: async () => withInitialPreviewLine(agentPreviewSource.normalize(
                await residentService.file({ residentId, ...residentFilePartsForService(path) }),
                {
                  path,
                  lineage_id: lineageId,
                  residentId,
                  source: "lineage-workspace",
                },
              ), request),
            });
            return true;
          }
          await openChatMarkdownLink(
            request.href || request.path || "",
            request.title || "",
            {
              resourceAddress: request.resourceAddress || "",
              mode: request.mode,
              locator: request.locator || "",
              line: request.line,
              lineStart: request.lineStart,
            },
          );
          return true;
        }, { priority: 100 });
      }

      function syncLineageChatWorkspaceProjection(detail) {
        const workspaceItems = Array.isArray(detail?.chat_workspace?.messages)
          ? detail.chat_workspace.messages
          : [];
        state.workspaceMessageItems = workspaceItems.map((item, idx) => ({
          key: window.PortalChatWorkspace.messageIdentity(item),
          item,
          idx,
        }));
      }

      function refreshLineageChatView() {
        const detail = agentChatPanel?.refreshView?.();
        if (!detail) return null;
        state.detail = detail;
        syncLineageChatWorkspaceProjection(detail);
        return detail;
      }

      const messageRole = item => window.PortalChatWorkspace.messageRole(item);

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

      function fileBaseName(path) {
        const clean = String(path || "").replace(/\\/g, "/").replace(/\/+$/, "");
        return clean.split("/").filter(Boolean).pop() || clean || "预览";
      }

      function markdownDocumentTitle(content) {
        for (const raw of String(content || "").split(/\r?\n/).slice(0, 80)) {
          const match = raw.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/);
          if (match?.[1]) return match[1].trim();
        }
        return "";
      }

      function previewTitleForPayload(payload) {
        if (!payload) return window.PortalI18n.t("预览");
        if (payload.preview_title) return String(payload.preview_title || "");
        if (payload.title) return String(payload.title || "");
        if (payload.knowledge_item?.title) return String(payload.knowledge_item.title || "");
        const fallback = payload.entry?.label || payload.entry?.name || fileBaseName(payload.path);
        if (String(payload.kind || "") === "markdown") {
          return markdownDocumentTitle(payload.content) || fallback;
        }
        return fallback;
      }

      function setPreviewTitle(title) {
        const text = String(title || window.PortalI18n.t("预览")).trim() || window.PortalI18n.t("预览");
        const el = $id("previewTitle");
        el.textContent = text;
        el.title = text;
      }


      function residentEntryCanAutoPreview(item) {
        if (!item || item.type === "directory" || item.type === "symlink") return false;
        const name = String(item.name || item.label || "").trim().toLowerCase();
        if (!name || name.startsWith("._")) return false;
        return !new Set([".ds_store", ".localized", "thumbs.db", "desktop.ini"]).has(name);
      }

      function topLevelResidentFilePath(folder, preferredPath = "") {
        const roots = folder?.roots || [];
        const preferred = String(preferredPath || "");
        if (preferred) {
          for (const rootInfo of roots) {
            const item = (rootInfo.entries || []).find(entry => (
              String(entry.path || "") === preferred && residentEntryCanAutoPreview(entry)
            ));
            if (item) return item.path || "";
          }
        }
        for (const rootInfo of folder?.roots || []) {
          if (String(rootInfo.root_kind || "") !== "resident_folder") continue;
          for (const item of rootInfo.entries || []) {
            if (
              Number(item.depth || 0) === 0
              && residentEntryCanAutoPreview(item)
              && String(item.name || item.label || "").toLowerCase() === "readme.md"
            ) {
              return item.path || "";
            }
          }
        }
        for (const rootInfo of roots) {
          for (const item of rootInfo.entries || []) {
            if (Number(item.depth || 0) === 0 && residentEntryCanAutoPreview(item)) {
              return item.path || "";
            }
          }
        }
        return "";
      }

      function residentFileParts(virtualPath) {
        const clean = String(virtualPath || "").replace(/\\/g, "/").replace(/^\/+/, "");
        const parts = clean.split("/").filter(Boolean);
        const directoryId = parts.shift() || "";
        return { directoryId, relPath: parts.join("/") };
      }

      function residentFilePartsForService(virtualPath) {
        const { directoryId, relPath } = residentFileParts(virtualPath);
        return { directoryId, path: relPath };
      }

      async function loadFolder() {
        if (currentResident()) {
          const residentId = selectedResidentId();
          if (!residentId || unavailableResidentBindings.has(residentId)) return;
          if (
            state.lineageDeleteSaving
            && lineageIdForResident(currentResident()) === state.lineageDeleteSaving
          ) return;
          const requestSequence = ++state.folderRequestSequence;
          try {
            const folder = await residentService.folder(residentId);
            if (requestSequence !== state.folderRequestSequence || residentId !== selectedResidentId()) return;
            state.folder = folder;
            personalWorkspaces?.controller.setResident({ id: residentId, roots: folder.roots || [] });
            const first = topLevelResidentFilePath(state.folder, state.selectedFile);
            if (!activePreviewPayload() && !activeAgentPreviewDocument()) {
              if (first) {
                await loadResidentFile(first, {
                  activatePreview: false,
                  revealPanel: false,
                });
              } else {
                state.selectedFile = "";
                resetAgentPreviewToEmpty();
              }
            }
          } catch (err) {
            if (requestSequence !== state.folderRequestSequence || residentId !== selectedResidentId()) return;
            console.warn("Resident workspace metadata unavailable", err);
            state.folder = { resident_id: residentId, roots: [], error: String(err.message || err) };
            personalWorkspaces?.controller.setResident({ id: residentId, error: state.folder.error });
          }
          return;
        }
      }

      async function loadResidentFileDocument(path, residentId) {
        const { directoryId, relPath } = residentFileParts(path);
        if (!residentId || !directoryId || !path) return null;
        const payload = await residentService.file({
          residentId,
          directoryId,
          path: relPath,
        });
        if (String(payload?.source || "") === "workspace_link") {
          return agentPreviewSource.normalize(payload, {
            path: String(payload?.path || path),
            source: "workspace_link",
          });
        }
        return agentPreviewSource.normalize(payload, {
          path: String(payload?.path || path),
          residentId,
          directoryId,
          relPath,
          source: "resident",
        });
      }

      async function loadResidentFile(path, options = {}) {
        const residentId = selectedResidentId();
        if (!residentId || !path) return;
        if (options.revealPanel !== false) ensurePreviewVisible();
        const { directoryId, relPath } = residentFileParts(path);
        if (!directoryId) return;
        stopPreviewRefresh();
        await openAgentPreviewDocument({
          key: `resident:${residentId}:${directoryId}:${relPath}`,
          mode: options.mode,
          path,
          title: fileBaseName(path),
          sourceType: "resident",
          residentId,
          directoryId,
          relPath,
          locator: options.locator || "",
          line: options.line,
          lineStart: options.lineStart,
          onLoading: () => {
            state.selectedFile = path;
            setPreviewTitle(fileBaseName(path));
            $id("previewPath").textContent = path;
            $id("previewBox").className = "previewBox";
            $id("previewBox").textContent = window.PortalI18n.t("加载中…");
          },
          load: async () => withInitialPreviewLine(
            await loadResidentFileDocument(path, residentId),
            options,
          ),
          onError: err => {
            $id("previewBox").textContent = window.PortalI18n.text`预览失败：${err.message || err}`;
          },
        });
      }

      function previewIsEditable(payload = activePreviewPayload()) {
        return Boolean(payload && agentPreviewSource?.canEdit?.(payload));
      }

      function activeAgentPreviewDocument() {
        return previewTabsController?.active?.() || null;
      }

      function agentPreviewSessionIsVisible(documentState) {
        if (documentState) return previewTabsController?.isActive?.(documentState.key) === true;
        return !activeAgentPreviewDocument();
      }

      function initialPreviewLine(request = {}) {
        const explicit = Number(request.lineStart ?? request.line);
        if (Number.isInteger(explicit) && explicit > 0) return explicit;
        const match = String(request.locator || "").trim().match(/(?:#L|:)(\d+)/i);
        const parsed = Number(match?.[1] || 0);
        return Number.isInteger(parsed) && parsed > 0 ? parsed : 0;
      }

      function withInitialPreviewLine(payload, request = {}) {
        const lineStart = initialPreviewLine(request);
        return payload && lineStart ? { ...payload, lineStart } : payload;
      }

      function focusInitialPreviewLine(request = {}) {
        const lineNumber = initialPreviewLine(request);
        if (!lineNumber) return false;
        const target = $id("previewBox")?.querySelector(`[data-preview-line="${lineNumber}"]`);
        if (!target) return false;
        target.scrollIntoView({ block: "center", inline: "nearest" });
        target.focus({ preventScroll: true });
        return true;
      }

      function createAgentPreviewSession(documentState = null) {
        return lineageWorkbench.getPanel("preview").createDocumentSession({
          canEdit: payload => previewIsEditable(payload),
          onStateChange: snapshot => {
            if (agentPreviewSessionIsVisible(documentState)) {
              renderPreviewActions();
            }
            previewTabsController?.render?.();
          },
          onPresent: (payload, _snapshot, meta) => {
            if (payload && agentPreviewSessionIsVisible(documentState)) paintPreview(payload, meta);
          },
          onEditStart: snapshot => {
            if (!agentPreviewSessionIsVisible(documentState)) return;
            renderPreviewEditor();
          },
        });
      }

      function activateAgentPreviewDocument(documentState, snapshot = documentState?.session?.snapshot?.()) {
        if (!documentState?.session) return false;
        previewController = documentState.session;
        stopPreviewRefresh();
        const sourceType = String(documentState.sourceType || "");
        if (
          sourceType === "resident"
          && documentState.residentId === selectedResidentId()
        ) {
          state.selectedFile = documentState.path;
        }
        if (snapshot?.payload) {
          paintPreview(snapshot.payload);
          if (snapshot.editing) renderPreviewEditor();
        } else {
          const path = documentState.title || documentState.path || documentState.key;
          setPreviewTitle(documentState.label || fileBaseName(path));
          $id("previewPath").textContent = path;
          setPreviewOpenInput(path);
          $id("previewBox").className = "previewBox";
          $id("previewBox").textContent = snapshot?.status === "error"
            ? window.PortalI18n.text`预览失败：${snapshot.error || window.PortalI18n.t("无法读取文件")}`
            : window.PortalI18n.t("加载中…");
        }
        previewTabsController?.render?.();
        renderPreviewActions();
        return true;
      }

      function resetAgentPreviewToEmpty() {
        previewAuxiliaryController?.destroy?.();
        previewAuxiliaryController = createAgentPreviewSession();
        previewController = previewAuxiliaryController;
        stopPreviewRefresh();
        setPreviewTitle(window.PortalI18n.t("预览"));
        $id("previewPath").textContent = "";
        setPreviewOpenInput("");
        previewSelectionController?.hide?.();
        $id("previewBox").className = "previewBox";
        $id("previewBox").textContent = window.PortalI18n.t("选择文件后显示内容。");
        previewTabsController?.render?.();
        renderPreviewActions();
      }

      function connectPreviewSurface() {
        agentPreviewPanelComponent = lineageWorkbench.getPanel("preview")?.surface || null;
        return agentPreviewPanelComponent;
      }

      function previewPersistenceLocator(request = {}) {
        const embedded = request?.document || request?.payload || request?.request || {};
        const sourceValue = String(
          request.sourceType
          || embedded.sourceType
          || embedded.previewSource
          || embedded.source
          || "",
        ).trim();
        const sourceType = ({
          workspace_link: "link",
          remote_link_card: "link-card",
        })[sourceValue] || sourceValue;
        const supported = new Set([
          "resident",
          "link",
          "resource",
          "link-card",
          "lineage-workspace",
        ]);
        if (!supported.has(sourceType)) return null;
        const path = String(request.path || embedded.path || "").trim();
        const parts = residentFileParts(path);
        const locator = {
          key: String(request.key || embedded.key || "").trim(),
          path,
          title: String(request.title || request.label || embedded.title || embedded.label || path || "").trim(),
          sourceType,
        };
        locator.residentId = String(
          request.residentId || request.resident_id || embedded.residentId || embedded.resident_id || "",
        ).trim();
        locator.directoryId = String(
          request.directoryId || request.directory_id || embedded.directoryId || embedded.directory_id || parts.directoryId || "",
        ).trim();
        locator.relPath = String(
          request.relPath || request.rel_path || embedded.relPath || embedded.rel_path || parts.relPath || "",
        ).trim();
        locator.lineageId = String(
          request.lineageId || request.lineage_id || embedded.lineageId || embedded.lineage_id || "",
        ).trim();
        locator.href = String(request.href || embedded.href || "").trim();
        locator.resourceAddress = String(
          request.resourceAddress
          || request.resource_address
          || embedded.resourceAddress
          || embedded.resource_address
          || embedded.address
          || "",
        ).trim();
        Object.keys(locator).forEach(key => {
          if (locator[key] === "") delete locator[key];
        });
        if (!locator.key && path) {
          if (sourceType === "resident") locator.key = `resident:${locator.residentId}:${locator.directoryId}:${locator.relPath}`;
          else if (sourceType === "resource") locator.key = `resource:${locator.resourceAddress || path}`;
          else if (sourceType === "link-card") locator.key = `link-card:${locator.href || path}`;
          else if (sourceType === "lineage-workspace") locator.key = `lineage:${locator.lineageId}:${locator.residentId}:${path}`;
          else locator.key = `link:${path}`;
        }
        return locator.key && locator.path ? locator : null;
      }

      function restorePreviewDocumentRequest(locator = {}) {
        const restored = previewPersistenceLocator(locator);
        if (!restored) return null;
        const path = restored.path;
        if (restored.sourceType === "resident") {
          if (!restored.residentId) return null;
          return {
            ...restored,
            load: () => loadResidentFileDocument(path, restored.residentId),
          };
        }
        if (restored.sourceType === "link") {
          return {
            ...restored,
            load: async () => {
              const payload = await window.PortalFileLinks.load(path);
              return agentPreviewSource.normalize(payload, {
                path: String(payload?.path || path),
                source: "workspace_link",
              });
            },
          };
        }
        if (restored.sourceType === "resource") {
          const address = restored.resourceAddress || path;
          return {
            ...restored,
            load: async () => {
              const payload = await window.PortalResourceChips.loadPreview(address);
              return payload == null ? null : window.PortalPreviewContract.normalizeDocument({
                ...payload,
                source: "resource",
                path: String(payload.path || address),
              });
            },
          };
        }
        if (restored.sourceType === "link-card") {
          const href = restored.href || path;
          return {
            ...restored,
            href,
            load: async () => window.PortalPreviewContract.normalizeDocument({
              source: "remote_link_card",
              path,
              href,
              title: restored.title || window.PortalI18n.t("链接"),
              kind: "remote-link",
              binary: true,
            }),
          };
        }
        if (!restored.lineageId || !restored.residentId) return null;
        return {
          ...restored,
          load: async () => agentPreviewSource.normalize(
            await residentService.file({ residentId: restored.residentId, ...residentFilePartsForService(path) }),
            {
              path,
              lineage_id: restored.lineageId,
              residentId: restored.residentId,
              source: "lineage-workspace",
            },
          ),
        };
      }

      function initPreviewController() {
        const previewRuntime = lineageWorkbench.getPanel("preview");
        agentPreviewSource = window.PortalAgentPreviewSource.create({
          apiGet,
          apiPost,
          residentService,
          entryVersion: entryMtimeToken,
          linkFileUrl: window.PortalFileLinks.apiUrl,
          resourceLoader: (address, options) => (
            window.PortalResourceChips.loadPreview(address, options)
          ),
          residentFileParts,
          selectedResidentId,
        });
        previewRuntime.setNavigationAdapter({
          id: "lineage-preview",
          serialize: resource => previewPersistenceLocator(resource),
          restore: locator => restorePreviewDocumentRequest(locator),
        });
        previewEditorTools = previewRuntime.createDocumentEditorTools({
          storageKey: window.PortalCompatibility.identifier("preview_word_wrap"),
          defaultWordWrap: true,
        });
        previewAuxiliaryController = createAgentPreviewSession();
        previewController = previewAuxiliaryController;
        previewTabsController = previewRuntime.createDocumentTabsController({
          shell: "#agentPreviewTabsShell",
          root: "#agentPreviewTabs",
          pinButton: "#pinAgentPreviewBtn",
          createDocument: request => ({
            label: String(request.title || request.label || request.path || request.key || "").split("/").pop() || window.PortalI18n.t("预览"),
            title: String(request.path || request.title || request.key || ""),
            path: String(request.path || ""),
            sourceType: String(request.sourceType || ""),
            residentId: String(request.residentId || ""),
            directoryId: String(request.directoryId || ""),
            relPath: String(request.relPath || ""),
            restoreLocator: previewPersistenceLocator(request),
          }),
          createSession: documentState => createAgentPreviewSession(documentState),
          serializeDocument: documentState => documentState.restoreLocator,
          restoreDocument: locator => restorePreviewDocumentRequest(locator),
          label: documentState => documentState.label,
          title: (documentState, snapshot) => (
            snapshot?.payload?.path
            || documentState.title
            || documentState.path
          ),
          onActivate: (documentState, snapshot) => activateAgentPreviewDocument(documentState, snapshot),
          onEmpty: () => resetAgentPreviewToEmpty(),
          onRender: snapshot => {
            const shell = $id("agentPreviewTabsShell");
            if (!snapshot.documents.length && shell) shell.hidden = true;
          },
          onError: error => window.PortalUI.toast(
            window.PortalI18n.text`Preview 操作失败：${error.message || error}`,
            "error",
          ),
        });
        previewTabsController.render();
        previewSelectionController?.destroy?.();
        previewSelectionController = previewRuntime.createPreviewSelectionController({
          root: "#previewBox",
          toolbar: "#previewSelectionToolbar",
          payload: () => activePreviewPayload(),
          path: payload => String(payload?.path || $id("previewPath")?.textContent || "").trim(),
          title: payload => previewTitleForPayload(payload || {}) || window.PortalI18n.t("预览"),
          chatWorkspace: () => agentChatWorkspace,
          selection: () => previewCodeEditor?.selection?.() || null,
          clearSelection: () => previewCodeEditor?.clearSelection?.(),
          disabled: payload => !payload,
          label: window.PortalI18n.t("添加到当前聊天"),
          ariaLabel: window.PortalI18n.t("把选中的文件内容添加到当前 Chat Workspace"),
          onAdd: (_selection, attachment) => window.PortalUI?.toast?.(
            `已添加 ${attachment?.name || "文件选段"} 到当前消息上下文`,
            "success",
          ),
        });
        applyPreviewWordWrap();
      }

      async function openAgentPreviewDocument(request = {}) {
        return await (previewTabsController
          ? previewTabsController.open(request)
          : previewController.open(request));
      }

      function previewDownloadUrl(payload = activePreviewPayload()) {
        return payload ? agentPreviewSource?.downloadUrl?.(payload) || "" : "";
      }

      const previewRefreshCoordinator = window.PortalRefreshCoordinator.create({
        key: () => previewRefreshKey(),
        interval: false,
        fetch: async ({ force, isCurrent }) => {
          const payload = activePreviewPayload();
          const session = previewController;
          if (!previewCanAutoRefresh(payload, { allowCleanEditor: force })) return null;
          try {
            if (!force) {
              const meta = await fetchPreviewRefreshMeta(payload);
              if (!isCurrent() || !meta || !previewPayloadMtimeToken(meta)
                || previewPayloadMtimeToken(meta) === previewPayloadMtimeToken(payload)) return null;
            }
            const refreshed = await fetchPreviewRefreshPayload(payload);
            return refreshed ? { payload: refreshed, source: payload, session } : null;
          } catch (error) {
            if (isCurrent() && previewController === session && activePreviewPayload() === payload) {
              session.setReadError(error);
            }
            throw error;
          }
        },
        apply: ({ payload, source, session }, { force } = {}) => {
          if (previewController !== session || activePreviewPayload() !== source) return;
          if (!previewCanAutoRefresh(activePreviewPayload(), { allowCleanEditor: force })) return;
          const editing = activePreviewSnapshot().editing;
          renderPreview(payload, { preserveScroll: true, afterRender: () => {
            if (editing && previewController === session && activePreviewPayload() === payload
              && !activePreviewSnapshot().dirty) enterPreviewEdit();
          } });
        },
        onError: err => console.debug?.("Preview auto refresh failed", err),
      });

      function stopPreviewRefresh() {
        previewRefreshCoordinator.stop();
      }

      function previewRefreshKey(payload = activePreviewPayload()) {
        return payload ? agentPreviewSource?.refreshKey?.(payload) || "" : "";
      }

      function previewPayloadMtimeToken(payload = activePreviewPayload()) {
        return String(entryMtimeToken(payload?.entry || {}) || "");
      }

      function previewCanAutoRefresh(payload = activePreviewPayload(), { allowCleanEditor = false } = {}) {
        const snapshot = activePreviewSnapshot();
        if (!payload || (snapshot.editing && !allowCleanEditor) || snapshot.dirty || snapshot.saving) return false;
        const kind = String(payload.kind || "");
        if (!kind || kind === "directory" || kind === "symlink") return false;
        return !!previewRefreshKey(payload);
      }

      async function fetchPreviewRefreshMeta(payload) {
        return payload ? agentPreviewSource?.fetchMeta?.(payload) || null : null;
      }

      async function fetchPreviewRefreshPayload(payload) {
        return payload ? agentPreviewSource?.reload?.(payload) || null : null;
      }

      function schedulePreviewRefresh(delay = 120) {
        if (!previewCanAutoRefresh()) return;
        previewRefreshCoordinator.start();
        previewRefreshCoordinator.invalidate({ delay });
      }

      async function refreshPreviewIfChanged(options = {}) {
        const force = options?.force === true;
        if (!previewCanAutoRefresh(activePreviewPayload(), { allowCleanEditor: force })) return;
        previewRefreshCoordinator.start();
        try {
          await previewRefreshCoordinator.refresh({ force });
        } catch (err) {
          if (force) {
            window.PortalUI.toast(window.PortalI18n.text`Preview 刷新失败：${err.message || err}`, "error");
          } else {
            console.debug?.("Preview auto refresh failed", err);
          }
        }
      }

      function previewChangeMatches(detail = {}) {
        if (!previewCanAutoRefresh()) return false;
        if (detail.snapshot_required) return true;
        const payload = activePreviewPayload() || {};
        const domains = new Set(detail.domains || []);
        if (String(payload.source || "") === "workspace_link") {
          return domains.has("resource");
        }
        const residentId = String(payload.resident_id || selectedResidentId() || "");
        const residents = Array.isArray(detail?.changed?.resident)
          ? detail.changed.resident
          : [];
        return Boolean(residentId && residents.includes(residentId));
      }

      function setPreviewOpenInput(value) {
        const input = $id("previewOpenInput");
        if (input) input.value = String(value || "");
      }

      function entryMtimeToken(entry) {
        return entry?.mtime_ns_text || entry?.mtime_ns_token || entry?.mtime_ns || 0;
      }

      function previewUsesTextLayout(payload = activePreviewPayload()) {
        if (!payload || payload.binary) return false;
        return ["markdown", "json", "jsonl", "text", "code"].includes(String(payload.kind || ""));
      }

      function destroyPreviewCodeEditor() {
        previewCodeEditorGeneration += 1;
        previewCodeEditor?.destroy?.();
        previewCodeEditor = null;
      }

      function applyPreviewWordWrap() {
        previewEditorTools?.applyWordWrap?.({
          root: "#agentPreviewPanel",
          button: "#wrapPreviewBtn",
        });
        previewCodeEditor?.setWordWrap?.(previewEditorTools?.wordWrap?.() !== false);
      }

      function togglePreviewWordWrap() {
        previewEditorTools?.toggleWordWrap?.();
        applyPreviewWordWrap();
      }

      function previewExternalUrl(payload = activePreviewPayload()) {
        if (!payload) return "";
        if (String(payload.source || "") === "remote_link_card") return String(payload.href || payload.path || "");
        return window.PortalPreviewRenderers.previewUrl(payload, {
          rawUrl: current => agentPreviewSource?.rawUrl?.(current) || "",
        });
      }

      function openPreviewInNewTab() {
        const payload = activePreviewPayload();
        if (previewUsesTextLayout(payload)) {
          return window.PortalPreviewRenderers.openRenderedDocument($id("previewBox"), {
            title: previewTitleForPayload(payload),
          });
        }
        const url = previewExternalUrl();
        if (!url) return false;
        window.open(url, "_blank", "noopener,noreferrer");
        return true;
      }

      function renderPreviewActions() {
        agentPreviewPanelComponent?.setReadError(activePreviewSnapshot().readError);
        const editBtn = $id("editPreviewBtn");
        const visualBtn = $id("visualPreviewBtn");
        const saveBtn = $id("savePreviewBtn");
        const undoBtn = $id("undoPreviewBtn");
        const redoBtn = $id("redoPreviewBtn");
        const wrapBtn = $id("wrapPreviewBtn");
        const externalBtn = $id("openPreviewExternalBtn");
        const refreshBtn = $id("refreshPreviewBtn");
        const panelActive = false;
        const action = previewController?.actionState?.() || {
          hasPayload: !!activePreviewPayload(),
          loading: false,
          editable: previewIsEditable(),
          editing: activePreviewSnapshot().editing,
          dirty: activePreviewSnapshot().dirty,
          saving: activePreviewSnapshot().saving,
          canEdit: previewIsEditable() && !activePreviewSnapshot().saving,
          canSave: activePreviewSnapshot().dirty && !activePreviewSnapshot().saving,
        };
        const downloadUrl = previewDownloadUrl();
        agentPreviewPanelComponent?.setDownload?.(panelActive ? "" : downloadUrl);
        const hasPayload = action.hasPayload ?? !!activePreviewPayload();
        const externalUrl = previewExternalUrl();
        if (refreshBtn) {
          refreshBtn.disabled = (
            panelActive
            || !hasPayload
            || !previewRefreshKey()
            || action.loading
            || action.dirty
            || action.saving
          );
        }
        editBtn.disabled = panelActive || !action.canEdit;
        editBtn.classList.toggle("active", action.editing);
        editBtn.setAttribute("aria-pressed", action.editing ? "true" : "false");
        visualBtn.disabled = panelActive || !hasPayload || action.saving;
        visualBtn.classList.toggle("active", !action.editing);
        visualBtn.setAttribute("aria-pressed", action.editing ? "false" : "true");
        saveBtn.disabled = panelActive || !action.canSave;
        undoBtn.disabled = panelActive || !action.editing || action.saving || !previewCodeEditor;
        redoBtn.disabled = panelActive || !action.editing || action.saving || !previewCodeEditor;
        wrapBtn.disabled = panelActive || !previewUsesTextLayout();
        externalBtn.disabled = panelActive || action.saving || !(previewUsesTextLayout() || externalUrl);
        const previewPath = hasPayload
          ? String(activePreviewPayload()?.path || activePreviewPayload()?.resourceAddress || "")
          : "";
        agentPreviewPanelComponent?.setPath?.(previewPath);
        applyPreviewWordWrap();
        previewTabsController?.render?.();
      }

      function renderPreview(payload, options = {}) {
        if (previewController) return previewController.present(payload, options);
        return paintPreview(payload, options);
      }

      function paintPreview(payload, options = {}) {
        destroyPreviewCodeEditor();
        const box = $id("previewBox");
        const preserveScroll = options.preserveScroll === true;
        const previousScrollTop = box ? box.scrollTop : 0;
        const restorePreviewScroll = () => {
          if (!preserveScroll || !box) return;
          window.requestAnimationFrame(() => {
            box.scrollTop = previousScrollTop;
          });
        };
        previewSelectionController?.hide?.();
        setPreviewTitle(previewTitleForPayload(payload));
        $id("previewPath").textContent = payload.path || window.PortalI18n.t("预览");
        setPreviewOpenInput(payload.path || "");
        const renderPromise = lineageWorkbench?.getPanel("preview")?.setDocument(payload);
        previewTabsController?.render?.();
        applyPreviewWordWrap();
        renderPreviewActions();
        Promise.resolve(renderPromise)
          .then(() => {
            restorePreviewScroll();
            if (!preserveScroll) focusInitialPreviewLine(payload);
            options.afterRender?.();
          })
          .catch(error => window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("预览"), error), "error"));
        return renderPromise;
      }

      async function renderPreviewEditor() {
        const current = previewController?.snapshot?.();
        const payload = current?.payload || activePreviewPayload();
        if (!previewIsEditable(payload)) return;
        destroyPreviewCodeEditor();
        const generation = previewCodeEditorGeneration;
        const session = previewController;
        const box = $id("previewBox");
        box.className = "previewBox editing";
        box.innerHTML = window.PortalI18n.markup('<div class="previewMonacoEditor" id="previewMonacoEditor"></div>');
        applyPreviewWordWrap();
        renderPreviewActions();
        try {
          const editor = await window.PortalCodeEditor.mount({
            root: "#previewMonacoEditor",
            statusBar: true,
            path: payload.path || payload.rel_path || "",
            value: current?.draft ?? activePreviewSnapshot().draft,
            wordWrap: previewEditorTools?.wordWrap?.() !== false,
            onSelectionChange: () => previewSelectionController?.show?.(),
            onChange: value => {
              if (previewController !== session) return;
              session?.updateDraft?.(value);
            },
            onSave: () => {
              if (previewController === session) savePreviewEdit();
            },
          });
          if (
            generation !== previewCodeEditorGeneration
            || previewController !== session
            || !session?.snapshot?.().editing
          ) {
            editor.destroy();
            return;
          }
          previewCodeEditor = editor;
          editor.focus();
          renderPreviewActions();
        } catch (error) {
          if (generation !== previewCodeEditorGeneration) return;
          box.className = "previewBox";
          box.textContent = window.PortalI18n.text`编辑器加载失败：${error.message || error}`;
          window.PortalUI.toast(window.PortalI18n.t("编辑器加载失败，请重试。"), "error");
          renderPreviewActions();
        }
      }

      function enterPreviewEdit() {
        if (!previewIsEditable()) return;
        if (!previewController.snapshot().editing) previewController.beginEdit();
      }

      function cancelPreviewEdit() {
        return previewController?.cancelEdit?.();
      }

      async function setPreviewMode(mode) {
        const editing = !!previewController?.snapshot?.().editing;
        if (mode === "code") {
          if (!editing) enterPreviewEdit();
          return;
        }
        if (mode === "visual" && editing) await cancelPreviewEdit();
      }

      function runPreviewEditorCommand(command) {
        if (!previewCodeEditor || !["undo", "redo"].includes(command)) return false;
        previewCodeEditor[command]?.();
        return true;
      }

      async function savePreviewEdit() {
        const result = await previewController.save({
          save: ({ payload, draft }) => agentPreviewSource.save(payload, draft),
          onSaved: async updated => {
          if (String(updated?.source || "") !== "workspace_link") await loadFolder();
          },
          onError: err => window.PortalUI.toast(window.PortalUI.errorMessage("save", err), "error"),
        });
        return result;
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
            rawUrl: current => agentPreviewSource?.rawUrl?.(current) || "",
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
        $id("pinAgentPreviewBtn").addEventListener("click", () => previewTabsController?.togglePin?.());
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
        lineageActivityRail?.setActiveView?.(next);
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
        lineageActivityRail?.destroy?.();
        lineageEditorOpenCommands?.destroy?.();
        agentFloatingMenuOverlay?.destroy?.();
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
