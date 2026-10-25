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
      let agentFloatingMenuOverlay = null;
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
      let personalAssistantRead = 0;
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
        currentLineageWorkbench: () => lineageWorkbench, esc, knownUsers, renderChatHeader, setSideView, state,
      });

      let lineageWorkbench = null;
      let lineageEditorOpenCommands = null;
      let lineageActivityRail = null;
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
        lineageSidebarRootByResidentId, registeredLineageIdForResident, renderAgentList,
        renderRegisteredResidentItem, residentDiscovery, residentSidebarPendingText, selectResident, state,
        writeLineageUiState, writeLineageUiStateSoon,
        // Resident dialogs is created after this part.
        openResidentForkCreateModal: (...args) => openResidentForkCreateModal(...args),
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

      function projectOptionById(projectId) {
        const wanted = String(projectId || "").trim();
        if (!wanted) return null;
        return (state.projects || []).find(project => project.id === wanted) || null;
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
        beginResidentSidebarMutation, chatTargetForPanel, commitChatTarget,
        currentAgentChatWorkspace: () => agentChatWorkspace, currentLineageWorkbench: () => lineageWorkbench,
        derivedResidentForSidebar, endResidentSidebarMutation, esc, lineageIdForResident,
        lineageLeaderResidentId, lineageSidebarRoots, loadDetail, loadFolder, messageRole,
        refreshResidentSidebarIfDue, registeredLineageIdForResident, rememberResidentShortcutSession,
        renderAgentList, renderFavoriteList, residentChatTarget, residentDiscovery, residentIdForSelection,
        residentSelectionId, residentService, revealResidentSidebarTarget, selectResident, societyService,
        state, syncSelectedResidentSurface, workspaceMessageByKey, writeLastSelection,
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
