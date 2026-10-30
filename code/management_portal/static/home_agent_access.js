(() => {
  if (window.PortalHomeAgentAccess) return;

  // Home's Agent access panel: the user's Resident shortcuts (personal Agent,
  // recommendation, recent conversations) with their activity, kept current
  // as the conversation store changes.
  const DEPENDENCIES = Object.freeze([
    "currentActiveAgentStatus", "esc", "homeApi", "renderActiveAgentStatus",
  ]);

  function createAgentAccess(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHomeAgentAccess is missing: ${missing.join(", ")}`);
    const {
      currentActiveAgentStatus, esc, homeApi, renderActiveAgentStatus,
    } = dependencies;

    let agentAccessCatalog = null;
    let personalAmaResidentId = "";
    let agentAccessLoadSeq = 0;
    const agentAccessSummaryById = new Map();
    const agentAccessSummaryPending = new Set();

    function residentShortcutId(item) {
      return String(item?.resident_id || item?.residentId || item?.id || "").trim();
    }

    function residentShortcutSummary(item, fallback = window.PortalI18n.t("暂无任务摘要")) {
      const residentId = residentShortcutId(item);
      const generated = agentAccessSummaryById.get(residentId);
      const structured = item?.summary && typeof item.summary === "object" && !Array.isArray(item.summary)
        ? item.summary
        : {};
      return String(
        generated?.current_focus
        || item?.current_focus
        || structured.current_focus
        || structured.current_goal
        || structured.summary
        || (typeof item?.summary === "string" ? item.summary : "")
        || item?.description
        || item?.current_job
        || item?.last_user_message
        || fallback,
      ).replace(/\s+/g, " ").trim();
    }

    function residentShortcutEntries(residents) {
      const entries = new Map();
      const add = (item, parent = null) => {
        const residentId = residentShortcutId(item);
        if (!residentId) return;
        const title = String(item?.title || item?.label || residentId).trim();
        entries.set(residentId, {
          residentId,
          title,
          subtitle: parent ? String(parent.title || residentShortcutId(parent)).trim() : "Resident",
          summary: residentShortcutSummary(item),
          lineageId: String(item?.lineage_id || parent?.lineage_id || "").trim(),
        });
      };
      for (const resident of (Array.isArray(residents) ? residents : [])) {
        add(resident);
        const derivedResidents = [
          resident?.family_residents_preview,
          resident?.family_residents,
        ].find(Array.isArray) || [];
        derivedResidents.forEach(derivedResident => add(derivedResident, resident));
      }
      return entries;
    }

    function residentNavigationUrl({ residentId = "", lineageId = "", url = "", openChat = false } = {}) {
      const provided = String(url || "").trim();
      if (provided.startsWith("/") && !provided.startsWith("//")) {
        if (!openChat) return provided;
        const target = new URL(provided, window.location.origin);
        target.searchParams.set("open_chat", "1");
        return `${target.pathname}${target.search}${target.hash}`;
      }
      const id = String(residentId || "").trim();
      if (!id) return "";
      const params = new URLSearchParams({ resident: id });
      const targetLineageId = String(lineageId || "").trim();
      if (targetLineageId) params.set("lineage", targetLineageId);
      if (openChat) params.set("open_chat", "1");
      return `/lineage?${params.toString()}`;
    }

    function residentShortcutUrl(entry) {
      const provided = String(entry?.url || "").trim();
      return residentNavigationUrl({
        residentId: entry?.residentId,
        lineageId: entry?.lineageId,
        url: provided,
      });
    }

    function agentAccessActivityLabel(status) {
      return {
        queued: window.PortalI18n.t("排队中"),
        dispatching: window.PortalI18n.t("启动中"),
        running: window.PortalI18n.t("运行中"),
      }[String(status || "").trim().toLowerCase()] || "";
    }

    function agentAccessLinkHtml(entry, { command = false, meta = "" } = {}) {
      const activityLabel = agentAccessActivityLabel(entry.activityStatus);
      const entryMeta = `${activityLabel ? `${activityLabel} · ` : ""}${meta || entry.summary || "暂无任务摘要"}`;
      return window.PortalI18n.html`<a class="${command ? "agent-access-command" : "agent-access-recent-item"}" href="${esc(residentShortcutUrl(entry))}" data-resident-id="${esc(entry.residentId)}">
    <span class="agent-access-copy">
      <span class="agent-access-title">${esc(entry.title)}</span>
      <span class="agent-access-meta">${esc(entryMeta)}</span>
    </span>
    <span class="agent-access-arrow" data-portal-icon="arrow-right" aria-hidden="true"></span>
  </a>`;
    }

    function renderAgentAccess(payload) {
      const root = document.querySelector("[data-agent-access]");
      if (!root) return;
      const residents = Array.isArray(payload?.residents) ? payload.residents : [];
      const entries = residentShortcutEntries(residents);
      const personalAma = entries.get(personalAmaResidentId) || null;
      const recommendationResident = entries.get(residentShortcutId(payload?.recommendation_resident)) || null;
      const recent = [];
      const seenRecent = new Set();
      for (const item of (window.PortalConversationStore?.entries?.() || [])) {
        const key = String(item?.key || "");
        if (!key.startsWith("resident:")) continue;
        const residentId = key.slice("resident:".length).trim();
        if (!residentId || seenRecent.has(residentId)) continue;
        seenRecent.add(residentId);
        const catalogEntry = entries.get(residentId) || {};
        recent.push({
          ...catalogEntry,
          residentId,
          title: String(item?.title || catalogEntry.title || residentId).trim(),
          subtitle: String(item?.subtitle || item?.surface_label || catalogEntry.subtitle || "Resident").trim(),
          summary: residentShortcutSummary({ ...item, resident_id: residentId }, catalogEntry.summary),
          url: String(item?.url || "").trim(),
          activityStatus: String(item?.status || "").trim().toLowerCase(),
        });
      }
      const amaEntry = personalAma || {
        residentId: "",
        title: window.PortalI18n.t("Ask Me Anything"),
        subtitle: window.PortalI18n.t("个人持续对话"),
        lineageId: "",
      };
      const amaHtml = personalAma
        ? agentAccessLinkHtml({ ...amaEntry, title: window.PortalI18n.t("Ask Me Anything") }, { command: true, meta: "打开个人 Agent" })
        : window.PortalI18n.html`<div class="agent-access-command"><span class="agent-access-copy"><span class="agent-access-title">Ask Me Anything</span><span class="agent-access-meta">当前暂不可用</span></span></div>`;
      const whichResidentHtml = recommendationResident
        ? agentAccessLinkHtml(
          { ...recommendationResident, title: window.PortalI18n.t("Which resident?") },
          { command: true, meta: "根据任务推荐合适的 Resident" },
        )
        : window.PortalI18n.html`<div class="agent-access-command"><span class="agent-access-copy"><span class="agent-access-title">Which resident?</span><span class="agent-access-meta">当前暂不可用</span></span></div>`;
      root.innerHTML = window.PortalI18n.html`<div class="agent-access-panel">
    ${amaHtml}
    ${whichResidentHtml}
    <div class="agent-access-recent">
      <div class="agent-access-recent-label">Recently used</div>
      <div class="agent-access-recent-list">
        ${recent.length
              ? recent.slice(0, 6).map(entry => agentAccessLinkHtml(entry)).join("")
              : window.PortalI18n.t('<div class="agent-access-empty">最近还没有打开过 Resident。</div>')}
      </div>
    </div>
  </div>`;
      const visible = recent.slice(0, 6);
      return visible;
    }

    async function loadAgentAccessSummaries(entries) {
      const residentIds = entries
        .map(entry => String(entry?.residentId || "").trim())
        .filter(residentId => (
          residentId
          && !agentAccessSummaryById.has(residentId)
          && !agentAccessSummaryPending.has(residentId)
        ));
      if (!residentIds.length) return;
      residentIds.forEach(residentId => agentAccessSummaryPending.add(residentId));
      try {
        const payload = await homeApi.post("/api/agent-workbench/resident-discovery/contexts", {
          resident_ids: residentIds,
        });
        for (const item of (Array.isArray(payload?.residents) ? payload.residents : [])) {
          const residentId = residentShortcutId(item);
          const summary = item?.summary && typeof item.summary === "object" ? item.summary : {};
          if (residentId) agentAccessSummaryById.set(residentId, summary);
        }
        residentIds.forEach(residentId => {
          if (!agentAccessSummaryById.has(residentId)) agentAccessSummaryById.set(residentId, {});
        });
        if (agentAccessCatalog) renderAgentAccess(agentAccessCatalog);
      } catch (_error) {
        // Static catalog descriptions remain available when the derived summary service is offline.
        residentIds.forEach(residentId => agentAccessSummaryById.set(residentId, {}));
      } finally {
        residentIds.forEach(residentId => agentAccessSummaryPending.delete(residentId));
      }
    }

    async function loadAgentAccess() {
      const root = document.querySelector("[data-agent-access]");
      const loadSeq = ++agentAccessLoadSeq;
      if (root && !agentAccessCatalog) {
        root.innerHTML = window.PortalI18n.markup('<div class="agent-access-empty">正在读取个人 Agent…</div>');
      }
      try {
        const [catalog, shortcuts, personalAmaData] = await Promise.all([
          window.PortalResidentService.list(),
          window.PortalResidentService.shortcuts(),
          window.PortalIdentity.listUIAssociations("lineage-workbench.personal-ama")
            .catch(() => ({ items: [] })),
        ]);
        let payload = catalog;
        let retryAfterMs = 250;
        while (payload?.ready === false && loadSeq === agentAccessLoadSeq) {
          await new Promise(resolve => window.setTimeout(resolve, retryAfterMs));
          payload = await window.PortalResidentService.list();
          retryAfterMs = Math.min(2000, retryAfterMs * 2);
        }
        if (loadSeq !== agentAccessLoadSeq) return;
        const personalAmaAssociation = (Array.isArray(personalAmaData?.items) ? personalAmaData.items : [])
          .find(item => String(item?.key || "") === "primary")
          || (Array.isArray(personalAmaData?.items) ? personalAmaData.items[0] : null);
        personalAmaResidentId = String(personalAmaAssociation?.value?.resident_id || "").trim();
        // The navigation directory contains leaders; personal and discovery targets
        // are resolved by the separate, actor-scoped shortcuts endpoint.
        agentAccessCatalog = {
          ...payload,
          recommendation_resident: shortcuts?.recommendation_resident,
          residents: [
            ...(Array.isArray(payload?.residents) ? payload.residents : []),
            ...(Array.isArray(shortcuts?.residents) ? shortcuts.residents : []),
          ],
        };
        const visibleEntries = renderAgentAccess(agentAccessCatalog);
        void loadAgentAccessSummaries(visibleEntries);
      } catch (error) {
        if (root && loadSeq === agentAccessLoadSeq) {
          root.innerHTML = window.PortalI18n.html`<div class="agent-access-empty">Resident 入口读取失败：${esc(error?.message || error)}</div>`;
        }
      }
    }

    window.PortalConversationStore?.subscribe?.(() => {
      if (agentAccessCatalog) {
        const visibleEntries = renderAgentAccess(agentAccessCatalog);
        void loadAgentAccessSummaries(visibleEntries);
      }
      if (currentActiveAgentStatus()) renderActiveAgentStatus(currentActiveAgentStatus());
    });

    return Object.freeze({
      residentNavigationUrl, loadAgentAccess,
    });
  }

  window.PortalHomeAgentAccess = Object.freeze({ createAgentAccess, dependencies: DEPENDENCIES });
})();
