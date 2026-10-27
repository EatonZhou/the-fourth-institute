const homeApi = window.PortalAPI.createClient({ cache: "no-store" });
const ACTIVE_AGENT_STATUS_LOADING_RETRY_MS = 750;
const ACTIVE_AGENT_STATUS_RETRY_BASE_MS = 1000;
const ACTIVE_AGENT_STATUS_RETRY_MAX_MS = 30000;
let portalUsername = "anonymous";
let canSetScheduler = false;
let canRegisterAccount = false;
let accountRegistrationPending = false;
const AGENT_STATUS_CACHE_HITS_KEY = window.PortalCompatibility.identifier("usage_cached_input");
const AGENT_STATUS_GROUPING_KEY = window.PortalCompatibility.identifier("usage_grouping");
// Usage grouping -> its payload fields, broadest first.
const AGENT_STATUS_GROUPINGS = {
  project: {label: "Project", series: "project_chart_series", ranking: "projects"},
  lineage: {label: "Lineage", series: "lineage_chart_series", ranking: "lineages"},
  resident: {label: "Resident", series: "chart_series", ranking: "residents"},
};
let agentStatusGrouping = "lineage";
let usageBackendFilter = "";
let usageAccountFilter = "";
const USAGE_FILTER_KEY = window.PortalCompatibility.identifier("usage_filters");
let agentStatusChartRange = "today";
let agentStatusIncludeCachedInput = false;
let agentStatusDetailsOpen = false;
let agentStatusData = null;
let agentStatusSummaryLoaded = false;
let agentStatusRefresh = null;
let usageData = null;
let usageRefresh = null;
let usageError = "";
let usageRequestPending = false;
let usageRenderedSignature = "";
let diskStatusRefresh = null;
let diskStatusData = null;
let gpuStatusData = null;
let scheduledTriggeredStatusData = null;
let activeAgentStatus = null;
let activeResidentStateCollection = null;
let activeResidentStateSignature = "";
let activeResidentStateFrame = 0;
let activeAgentStatusRefresh = null;
let activeAgentStatusRetryAttempt = 0;
let agentStatusRunboardHtml = "";
const homeRoute = window.PortalHomeComponents.resolveRoute(window.location.search);
const scheduledTriggeredExpanded = new Set();
let portalPulseData = null;
let portalPulsePublicRange = "daily";
let portalPulsePersonalRange = "daily";
let agentAccessCatalog = null;
let personalAmaResidentId = "";
let agentAccessLoadSeq = 0;
const agentAccessSummaryById = new Map();
const agentAccessSummaryPending = new Set();
const RESIDENT_USAGE_RANK_LIMIT = 20;
const AGENT_STATUS_USAGE_RANGES = ["today", "week", "month", "year"];

function formatPortalTime(value) {
  return window.PortalUI.formatDateTime(value, { style: "compact" });
}

function esc(value) {
  return window.PortalUIPrimitives.escapeHtml(value);
}

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
  if (activeAgentStatus) renderActiveAgentStatus(activeAgentStatus);
});

function homeStatusHtml(label, tone = "neutral", className = "") {
  return window.PortalUIPrimitives.statusHtml(tone, {
    className,
    label,
    tone,
  });
}

function pulsePeriodLabel(period, data = portalPulseData) {
  const item = data?.periods?.[period]?.public || {};
  if (item.label) return item.label;
  return period === "weekly" ? "Week" : period === "monthly" ? "Month" : "Day";
}

function pulseTabsHtml(scope, range) {
  return window.PortalI18n.html`
    <div class="pulse-tabs" role="tablist" aria-label="Pulse range">
      ${["daily", "weekly", "monthly"].map(period => window.PortalI18n.html`
        <button
          class="portalButton is-compact is-quiet pulse-tab ${range === period ? "active" : ""}"
          type="button"
          role="tab"
          aria-selected="${range === period ? "true" : "false"}"
          aria-pressed="${range === period ? "true" : "false"}"
          data-pulse-scope="${scope}"
          data-pulse-range="${period}"
        >
          ${period === "daily" ? "Day" : period === "weekly" ? "Week" : "Month"}
        </button>
      `).join("")}
    </div>
  `;
}

function pulseEvidenceHtml(paths = []) {
  const clean = (Array.isArray(paths) ? paths : []).filter(Boolean).slice(0, 2);
  if (!clean.length) return "";
  return window.PortalI18n.html`
    <div class="pulse-evidence" aria-label="Evidence">
      <span class="pulse-evidence-label">Evidence</span>
      ${clean.map(path => {
        const address = String(path).trim();
        const relative = address.replace(/^\/share\/Projects\//, "");
        const parts = relative.split("/").filter(Boolean);
        const label = parts.slice(-2).join("/") || relative;
        return window.PortalI18n.html`<a class="pulse-evidence-chip" href="/r/${encodeURIComponent(address)}" data-resource-address="${esc(address)}" title="${esc(relative)}">${esc(label)}</a>`;
      }).join("")}
    </div>
  `;
}

function renderPulseProjects(publicPulse) {
  const projects = Array.isArray(publicPulse?.project_updates) ? publicPulse.project_updates : [];
  if (!projects.length) return window.PortalI18n.html`<div class="pulse-empty">当前时间段没有项目更新摘要。</div>`;
  return window.PortalI18n.html`
    <div class="pulse-section">
      <div class="pulse-section-head">
        <div class="pulse-section-title">Project changes</div>
        ${homeStatusHtml(`${projects.length} projects`, "neutral")}
      </div>
      <div class="pulse-list">
        ${projects.slice(0, 5).map(item => window.PortalI18n.html`
          <article class="pulse-row">
            <div class="pulse-row-head">
              <div class="pulse-row-title" title="${esc(item.project || "")}">${esc(item.project || window.PortalI18n.t("Project"))}</div>
              ${homeStatusHtml(`${Number(item.change_count || 0)} updates`, "info", "pulse-update-count")}
            </div>
            <div class="pulse-row-text" title="${esc(item.summary || "")}">${esc(item.summary || "")}</div>
            ${pulseEvidenceHtml(item.evidence_paths)}
          </article>
        `).join("")}
      </div>
    </div>
  `;
}

function renderPulsePublications(data = portalPulseData) {
  const publications = Array.isArray(data?.publications) ? data.publications : [];
  if (!publications.length) return "";
  return window.PortalI18n.html`
    <div class="pulse-section">
      <div class="pulse-section-head">
        <div class="pulse-section-title">Published</div>
        ${homeStatusHtml(`${publications.length} items`, "neutral")}
      </div>
      <div class="pulse-list">
        ${publications.slice(0, 4).map(item => window.PortalI18n.html`
          <article class="pulse-row">
            <div class="pulse-row-head">
              <div class="pulse-row-title" title="${esc(item.project || "")}">${esc(item.project || item.owner || "Published")}</div>
              <div class="pulse-meta">${esc(item.owner || "")}</div>
            </div>
            <div class="pulse-row-text">${esc(item.text || "")}</div>
            ${pulseEvidenceHtml(item.evidence_paths)}
          </article>
        `).join("")}
      </div>
    </div>
  `;
}

function renderPulseMine(minePulse) {
  const items = Array.isArray(minePulse?.items) ? minePulse.items : [];
  if (!items.length) {
    return window.PortalI18n.html`<div class="pulse-empty">当前时间段没有识别到只属于你的进展条目。</div>`;
  }
  return window.PortalI18n.html`
    <div class="pulse-list">
      ${items.slice(0, 10).map(item => window.PortalI18n.html`
        <div class="pulse-row">
          <div class="pulse-row-head">
            <div class="pulse-row-title" title="${esc(item.project || "")}">${esc(item.project || "Personal")}</div>
            ${item.published
              ? window.PortalI18n.html`<button class="portalButton is-compact is-active pulse-action" type="button" data-pulse-unpublish="1" data-source-period="${esc(item.source_period || "")}" data-source-key="${esc(item.source_key || "")}" data-item-id="${esc(item.id || "")}">Retract</button>`
              : window.PortalI18n.html`<button class="portalButton is-compact pulse-action" type="button" data-pulse-publish="1" data-source-period="${esc(item.source_period || "")}" data-source-key="${esc(item.source_key || "")}" data-item-id="${esc(item.id || "")}">Publish</button>`}
          </div>
          <div class="pulse-row-text">${esc(item.text || "")}</div>
          ${pulseEvidenceHtml(item.evidence_paths)}
        </div>
      `).join("")}
    </div>
  `;
}

function attachPulseHandlers() {
  for (const button of document.querySelectorAll("[data-pulse-range]")) {
    button.addEventListener("click", () => {
      const nextRange = button.getAttribute("data-pulse-range") || "daily";
      if (button.getAttribute("data-pulse-scope") === "personal") {
        portalPulsePersonalRange = nextRange;
      } else {
        portalPulsePublicRange = nextRange;
      }
      renderPortalPulse();
    });
  }
  for (const button of document.querySelectorAll("[data-pulse-publish], [data-pulse-unpublish]")) {
    button.addEventListener("click", () => publishPortalPulseItem({
      published: button.hasAttribute("data-pulse-publish"),
      source_period: button.getAttribute("data-source-period") || "",
      source_key: button.getAttribute("data-source-key") || "",
      item_id: button.getAttribute("data-item-id") || "",
    }));
  }
}

function renderDailyNews(data) {
  const node = document.querySelector("[data-daily-news]");
  if (!node) return;
  if (data?.status === "missing") {
    node.innerHTML = window.PortalI18n.html`<div class="daily-news-empty">今天的新闻报告还没有生成。</div>`;
    return;
  }
  if (data?.status !== "ok") {
    node.innerHTML = window.PortalI18n.html`<div class="daily-news-error">${esc(data?.error || window.PortalI18n.t("今日新闻读取失败"))}</div>`;
    return;
  }
  const content = String(data.content || "").trim();
  const updatedLabel = [
    data.stale ? window.PortalI18n.t("最近有效报告") : "",
    data.updated_at ? window.PortalI18n.text`更新于 ${formatPortalTime(data.updated_at)}` : "",
  ].filter(Boolean).join(" · ");
  node.innerHTML = window.PortalI18n.html`
    <div class="daily-news-panel">
      <div class="daily-news-toolbar">
        ${homeStatusHtml(data.date || "Today", "info", "daily-news-date")}
        ${updatedLabel ? window.PortalI18n.html`<div class="daily-news-meta">${esc(updatedLabel)}</div>` : ""}
      </div>
      <div class="daily-news-body">${content && window.MD?.render ? window.MD.render(content) : window.PortalI18n.html`<div class="daily-news-empty">今天的新闻报告为空。</div>`}</div>
    </div>
  `;
}

async function loadDailyNews() {
  const node = document.querySelector("[data-daily-news]");
  try {
    renderDailyNews(await homeApi.get("/api/daily-news"));
  } catch (err) {
    if (node) node.innerHTML = window.PortalI18n.html`<div class="daily-news-error">${esc(window.PortalUI.errorMessage(window.PortalI18n.t("读取今日新闻"), err))}</div>`;
  }
}

function renderPortalPulse(data = portalPulseData) {
  portalPulseData = data || portalPulseData;
  const publicNode = document.querySelector('[data-summary-value="urgent-work"]');
  const mineNode = document.querySelector('[data-summary-value="contribution-window"]');
  if (portalPulseData?.enabled === false) {
    if (publicNode) publicNode.innerHTML = window.PortalI18n.html`<div class="pulse-empty">Portal Pulse 当前不可用。</div>`;
    if (mineNode) mineNode.innerHTML = window.PortalI18n.html`<div class="pulse-empty">Portal Pulse 当前不可用。</div>`;
    return;
  }
  const publicPulse = portalPulseData?.periods?.[portalPulsePublicRange]?.public || {};
  const minePulse = portalPulseData?.periods?.[portalPulsePersonalRange]?.mine || {};
  if (publicNode) {
    const hasSummary = Boolean(publicPulse.global_summary || (publicPulse.project_updates || []).length);
    const periodMeta = `${pulsePeriodLabel(portalPulsePublicRange)}${publicPulse.generated_at ? ` · ${formatPortalTime(publicPulse.generated_at)}` : ""}`;
    publicNode.innerHTML = window.PortalI18n.html`
      <div class="pulse-panel">
        <div class="pulse-toolbar">
          ${pulseTabsHtml("public", portalPulsePublicRange)}
          <div class="pulse-toolbar-meta">
            ${homeStatusHtml(periodMeta, "neutral", "pulse-period-status")}
          </div>
        </div>
        ${hasSummary ? window.PortalI18n.html`
          <div class="pulse-brief">
            <span class="pulse-brief-label">Overview</span>
            <p>${esc(publicPulse.global_summary || "")}</p>
          </div>
        ` : window.PortalI18n.html`<div class="pulse-empty">还没有生成这个时间段的 Pulse 摘要。</div>`}
        ${renderPulseProjects(publicPulse)}
        ${renderPulsePublications(portalPulseData)}
      </div>
    `;
  }
  if (mineNode) {
    mineNode.innerHTML = window.PortalI18n.html`
      <div class="pulse-panel">
        ${pulseTabsHtml("personal", portalPulsePersonalRange)}
        ${minePulse.summary ? window.PortalI18n.html`<div class="pulse-summary">${esc(minePulse.summary)}</div>` : ""}
        ${renderPulseMine(minePulse)}
      </div>
    `;
  }
  attachPulseHandlers();
}

async function loadPortalPulse() {
  const publicNode = document.querySelector('[data-summary-value="urgent-work"]');
  const mineNode = document.querySelector('[data-summary-value="contribution-window"]');
  try {
    // GetPulse goes through the permission system, which decides who may read Pulse.
    const userId = await window.PortalIdentity.userId();
    const result = await window.PortalInstructionClient.execute("GetPulse", { user_id: userId });
    renderPortalPulse(result.pulse);
  } catch (err) {
    if (publicNode) publicNode.innerHTML = window.PortalI18n.html`<div class="pulse-error">${esc(window.PortalUI.errorMessage(window.PortalI18n.t("读取 Pulse"), err))}</div>`;
    if (mineNode) mineNode.innerHTML = window.PortalI18n.html`<div class="pulse-error">个人 Pulse 读取失败。</div>`;
  }
}

async function publishPortalPulseItem(payload) {
  try {
    const published = payload?.published === true;
    const result = await window.PortalInstructionClient.execute(
      "PublishPulseItem",
      { source_period: payload.source_period, source_key: payload.source_key, item_id: payload.item_id, published },
      { idempotencyKey: `pulse:${globalThis.PortalUIPrimitives.randomUUID()}` },
    );
    portalPulseData = result.pulse;
    renderPortalPulse(portalPulseData);
    window.PortalUI.toast(
      published ? window.PortalI18n.t("Pulse 已发布") : window.PortalI18n.t("Pulse 已撤回"),
      "success",
    );
  } catch (err) {
    window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("发布 Pulse"), err), "error");
  }
}

function formatTokens(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number <= 0) return "0";
  if (number >= 1_000_000_000) return `${(number / 1_000_000_000).toFixed(1)}B`;
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
  if (number >= 1_000) return `${(number / 1_000).toFixed(1)}K`;
  return String(Math.round(number));
}

function formatBytes(value) {
  return window.PortalUI.formatBytes(value, { precision: "single" });
}

function formatDateTime(value) {
  return window.PortalUI.formatDateTime(value, { style: "status", invalidFallback: false });
}

function displayDateTime(value) {
  const formatted = formatDateTime(value);
  return formatted || String(value || "").trim();
}

function gpuCount(gpu) {
  return Number(gpu?.gpu_count) || 0;
}

function gpuAvailableCount(gpu) {
  return Number(gpu?.available_gpu_count) || 0;
}

function gpuSummaryText(gpu) {
  if (!gpu || gpu.status === "loading") return window.PortalI18n.t("GPU: loading");
  if (gpu.status === "not_checked") return window.PortalI18n.t("GPU: not checked");
  if (gpu.status === "error") return window.PortalI18n.t("GPU: unavailable");
  const unknown = Number(gpu?.unknown_gpu_count || 0);
  if (gpu.stale || gpu.refresh_error) {
    return window.PortalI18n.text`GPU: stale · ${gpuAvailableCount(gpu)}/${gpuCount(gpu)} available${unknown ? ` · ${unknown} unknown` : ""}`;
  }
  return window.PortalI18n.text`GPU: ${gpuAvailableCount(gpu)}/${gpuCount(gpu)} available${unknown ? ` · ${unknown} unknown` : ""}`;
}

function gpuResourceHtml(gpu) {
  return window.PortalI18n.html`<span>${esc(gpuSummaryText(gpu))}</span>`;
}

function gpuCommandText(process) {
  return String(process?.command_line || process?.process_name || process?.command || "").trim();
}

function gpuOwnerFromRunId(command) {
  const runId = command.match(/--run-id\s+([^\s]+)/)?.[1] || command.match(/\/live\/([^/\s]+)/)?.[1] || "";
  if (!runId) return "";
  return runId;
}

function gpuProcessOwnerInfo(process) {
  const command = gpuCommandText(process);
  const runOwner = gpuOwnerFromRunId(command);
  if (runOwner) return {label: runOwner, source: "run id"};
  const user = String(process?.user || "").trim();
  if (user) return {label: user, source: "linux user"};
  return {label: "unknown", source: "unknown"};
}

function gpuProcessHtml(process) {
  const owner = gpuProcessOwnerInfo(process);
  const pid = process?.pid || "";
  const memory = process?.used_memory_mb != null ? `${process.used_memory_mb} MB` : "";
  const elapsed = process?.elapsed ? ` · ${esc(process.elapsed)}` : "";
  const ownerText = owner.label && owner.label !== "unknown" ? ` · owner ${esc(owner.label)}` : "";
  return window.PortalI18n.html`
    <div class="agent-status-gpu-process">
      ${pid ? `pid ${esc(pid)}` : "process"}${memory ? window.PortalI18n.html` · <strong>${esc(memory)}</strong>` : ""}${elapsed}${ownerText}
    </div>`;
}

function gpuStatusLabel(item) {
  const state = String(item?.state || "").trim();
  if (["empty", "idle", "busy", "unknown"].includes(state)) return state;
  return "unknown";
}

function gpuMemoryMeta(item) {
  if (item?.memory_supported && item?.memory_used_mb != null && Number(item?.memory_total_mb) > 0) {
    return `${item.memory_used_mb}/${item.memory_total_mb} MB`;
  }
  if (item?.unified_memory_used_percent != null) return `unified memory ${item.unified_memory_used_percent}%`;
  if (item?.memory_util_percent != null) return `memory ${item.memory_util_percent}%`;
  if (item?.memory_supported) return "memory telemetry unknown";
  return "memory total unsupported";
}

function gpuRowHtml(item) {
  const compute = Array.isArray(item.compute_processes) ? item.compute_processes : [];
  const graphics = Array.isArray(item.graphics_processes) ? item.graphics_processes : [];
  const processes = [...compute, ...graphics].filter(process => Number(process?.used_memory_mb || 0) > 0);
  const meta = [
    item.gpu_util_percent != null ? `${item.gpu_util_percent}% util` : "",
    gpuMemoryMeta(item),
    item.temperature_c != null ? `${item.temperature_c}C` : "",
    item.power_draw_w != null ? `${item.power_draw_w}W` : "",
  ].filter(Boolean).join(" · ");
  return window.PortalI18n.html`
    <div class="agent-status-gpu-row">
      <div class="agent-status-gpu-title">
        <span class="agent-status-gpu-name"><strong>GPU ${esc(item.index ?? "")}</strong>${item.name ? ` ${esc(item.name)}` : ""}</span>
        <span>${esc(gpuStatusLabel(item))}</span>
      </div>
      <div class="agent-status-gpu-meta">${esc(meta || window.PortalI18n.t("no telemetry"))}</div>
      ${processes.length ? processes.map(gpuProcessHtml).join("") : window.PortalI18n.html`<div class="agent-status-muted">No process using GPU memory.</div>`}
    </div>`;
}

function gpuResourceDetailHtml(resource) {
  if (!resource || resource.status === "loading") {
    return window.PortalI18n.html`<div class="agent-status-gpu-row"><div class="agent-status-muted">GPU status is loading.</div></div>`;
  }
  if (resource.status === "not_checked") {
    return window.PortalI18n.html`<div class="agent-status-gpu-row"><div class="agent-status-muted">No GPU observation yet. Use Refresh to collect it.</div></div>`;
  }
  if (resource.status === "error") {
    return window.PortalI18n.html`<div class="agent-status-gpu-row"><div class="agent-status-error">Status unavailable: ${esc(resource.error || resource.refresh_error || window.PortalI18n.t("unknown error"))}</div></div>`;
  }
  const gpus = Array.isArray(resource.gpus) ? resource.gpus : [];
  return gpus.length ? gpus.map(gpuRowHtml).join("") : window.PortalI18n.html`<div class="agent-status-muted">No GPU data.</div>`;
}

function gpuSingleResourceHtml(resource) {
  const checked = formatDateTime(resource?.updated_at || resource?.generated_at);
  const resourceTitle = `${resource?.display_name || resource?.device_id || window.PortalI18n.t("GPU server")}${resource?.ip ? ` | IP: ${resource.ip}` : ""}`;
  const statusText = resource?.status === "not_checked"
    ? window.PortalI18n.t("not checked")
    : `${Number(resource?.available_gpu_count || 0)}/${Number(resource?.gpu_count ?? resource?.expected_gpu_count ?? 0) || 0} available${Number(resource?.unknown_gpu_count || 0) ? ` · ${Number(resource.unknown_gpu_count)} unknown` : ""}`;
  return window.PortalI18n.html`
    <div class="agent-status-gpu-resource-card">
      <div class="agent-status-gpu-head">
        <span>${esc(resourceTitle)}</span>
        <span class="agent-status-muted">${esc(statusText)}${checked ? ` · ${esc(checked)}` : ""}</span>
      </div>
      ${resource?.refresh_error ? window.PortalI18n.html`<div class="agent-status-error">Status is stale: ${esc(resource.refresh_error)}</div>` : ""}
      <div class="agent-status-gpu-grid">${gpuResourceDetailHtml(resource)}</div>
    </div>`;
}

function resourceRefreshWarningHtml(resource, label) {
  if (!resource?.stale && !resource?.refresh_error) return "";
  const detail = String(resource?.refresh_error || "latest refresh failed");
  return window.PortalI18n.html`<div class="agent-status-error">${esc(label)} data is stale: ${esc(detail)}</div>`;
}

function gpuDetailsHtml(gpu) {
  if (!gpu || gpu.status === "loading") {
    return window.PortalI18n.html`<div class="agent-status-gpu-card"><div class="agent-status-muted">GPU status is loading.</div></div>`;
  }
  const servers = Array.isArray(gpu?.servers) ? gpu.servers : [];
  if (servers.length) {
    return window.PortalI18n.html`
      <div class="agent-status-gpu-card">
        ${resourceRefreshWarningHtml(gpu, "GPU")}
        <div class="agent-status-gpu-resource-list">${servers.map(gpuSingleResourceHtml).join("")}</div>
      </div>`;
  }
  if (gpu.status === "error") {
    return window.PortalI18n.html`<div class="agent-status-gpu-card"><div class="agent-status-error">GPU status unavailable: ${esc(gpu.error || window.PortalI18n.t("unknown error"))}</div></div>`;
  }
  return window.PortalI18n.html`<div class="agent-status-gpu-card"><div class="agent-status-muted">No GPU data.</div></div>`;
}

function diskSummaryText(disk) {
  if (!disk || disk.status === "loading") return window.PortalI18n.t("Disk: loading");
  if (disk.status === "error") return window.PortalI18n.t("Disk: unavailable");
  if (disk.stale || disk.refresh_error) return window.PortalI18n.t("Disk: stale");
  const filesystems = Array.isArray(disk?.filesystems) ? disk.filesystems : [];
  const critical = filesystems.filter(item => item?.status === "critical").length;
  const warn = filesystems.filter(item => item?.status === "warn").length;
  if (critical) return window.PortalI18n.text`Disk: critical ${critical}`;
  if (warn) return window.PortalI18n.text`Disk: warn ${warn}`;
  if (disk.status === "partial") return window.PortalI18n.t("Disk: partial");
  if (!filesystems.length) return window.PortalI18n.t("Disk: not checked");
  return window.PortalI18n.t("Disk: ok");
}

function diskResourceHtml(disk) {
  return window.PortalI18n.html`<span>${esc(diskSummaryText(disk))}${disk?.refreshing ? window.PortalI18n.t(" · 采集中") : ""}</span>`;
}

function diskStageHtml(resource, name, title) {
  const stage = resource?.stages?.[name];
  if (!stage) return "";
  const labels = { pending: window.PortalI18n.t("等待采集"), running: window.PortalI18n.t("采集中"), ok: window.PortalI18n.t("已完成"), partial: window.PortalI18n.t("部分完成"), error: window.PortalI18n.t("失败"), skipped: window.PortalI18n.t("未执行") };
  const detail = [stage.error || stage.reason, formatDateTime(stage.updated_at)].filter(Boolean).join(" · ");
  return window.PortalI18n.html`<div class="agent-status-muted" data-disk-stage="${esc(name)}" data-stage-status="${esc(stage.status)}">
    ${esc(title)} · ${esc(labels[stage.status] || stage.status)}${detail ? ` · ${esc(detail)}` : ""}
  </div>`;
}

function diskRowHtml(item) {
  const percent = Number(item?.used_percent || 0);
  const mounts = Array.isArray(item?.mounts) ? item.mounts.filter(Boolean) : item?.mount ? [item.mount] : [];
  const mountText = mounts.length ? `mounted at ${mounts.join(", ")}` : "";
  const title = item?.volume_label || item?.filesystem || item?.path || item?.mount || "volume";
  const meta = [
    mountText,
    item?.fstype || "",
    `${formatBytes(item?.available_bytes)} free`,
  ].filter(Boolean).join(" · ");
  return window.PortalI18n.html`
    <div class="agent-status-gpu-row">
      <div class="agent-status-gpu-title">
        <span>${esc(title)}</span>
        <span>${esc(item?.status || "ok")} · ${Number.isFinite(percent) ? percent.toFixed(1) : "0.0"}%</span>
      </div>
      <div class="agent-status-progress" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, percent))}%"></span></div>
      <div class="agent-status-gpu-meta">${esc(meta)}</div>
    </div>`;
}

function diskTargetHtml(item) {
  const label = [item?.label, item?.model, item?.mount].filter(Boolean).join(" · ");
  return window.PortalI18n.html`
    <div class="agent-status-gpu-process">
      <strong>${esc(label || window.PortalI18n.t("External disk"))}</strong>
      <div class="agent-status-gpu-command">${esc(item?.mount || "")}${item?.available_bytes ? ` · ${formatBytes(item.available_bytes)} free` : ""}</div>
    </div>`;
}

function diskTriageFindingHtml(item) {
  const rows = Array.isArray(item?.items) ? item.items.slice(0, 5) : [];
  return window.PortalI18n.html`
    <div class="agent-status-gpu-process">
      <strong>${esc(item?.title || "Finding")}</strong>
      ${item?.detail ? window.PortalI18n.html`<div class="agent-status-gpu-command">${esc(item.detail)}</div>` : ""}
      ${rows.map(row => window.PortalI18n.html`<div class="agent-status-gpu-command">- ${esc(row.label || "")}${row.detail ? ` · ${esc(row.detail)}` : ""}</div>`).join("")}
    </div>`;
}

function diskTriageHtml(resource) {
  const triage = resource?.triage && typeof resource.triage === "object" ? resource.triage : {};
  const findings = Array.isArray(triage.findings) ? triage.findings : [];
  const recommendations = Array.isArray(triage.recommendations) ? triage.recommendations : [];
  const errors = Array.isArray(triage.errors) ? triage.errors : [];
  const checks = [["deleted_open_files", "已删除仍占用"], ["journal_usage", "系统日志"], ["docker_usage", "容器存储"], ["large_files", "大文件扫描"]];
  const states = checks.map(([name]) => resource?.stages?.[name]?.status).filter(Boolean);
  const emptyText = states.some(state => ["pending", "running"].includes(state))
    ? window.PortalI18n.t("诊断采集中，结果将逐段补充。")
    : states.some(state => ["error", "partial"].includes(state))
      ? window.PortalI18n.t("诊断未完整取得，请查看各段错误。")
      : !states.length || states.every(state => state === "skipped")
        ? window.PortalI18n.t("按需诊断尚未执行。")
        : window.PortalI18n.t("No quick cleanup candidate detected.");
  return window.PortalI18n.html`
    <div class="agent-status-gpu-row">
      <div class="agent-status-gpu-title"><span>Lightweight triage</span><span>${findings.length}</span></div>
      ${checks.map(([name, title]) => diskStageHtml(resource, name, title)).join("")}
      ${findings.length ? findings.map(diskTriageFindingHtml).join("") : window.PortalI18n.html`<div class="agent-status-muted">${esc(emptyText)}</div>`}
      ${recommendations.length ? window.PortalI18n.html`<div class="agent-status-gpu-process"><strong>Optimization plan</strong>${recommendations.map(item => window.PortalI18n.html`<div class="agent-status-gpu-command">- ${esc(item)}</div>`).join("")}</div>` : ""}
      ${errors.length ? window.PortalI18n.html`<div class="agent-status-muted">Skipped: ${esc(errors.join("; "))}</div>` : ""}
    </div>`;
}

function diskSingleResourceHtml(resource) {
  const checked = formatDateTime(resource?.updated_at);
  const rows = Array.isArray(resource?.filesystems) ? resource.filesystems : [];
  const targets = Array.isArray(resource?.transfer_targets) ? resource.transfer_targets : [];
  const suggestions = Array.isArray(resource?.suggestions) ? resource.suggestions : [];
  const statusText = resource?.status === "loading" ? "loading" : resource?.status === "error" ? "unavailable" : resource?.status || window.PortalI18n.t("not checked");
  return window.PortalI18n.html`
    <div class="agent-status-gpu-resource-card" data-disk-resource="${esc(resource?.resource_id || "")}">
      <div class="agent-status-gpu-head">
        <span>${esc(resource?.display_name || resource?.resource_id || window.PortalI18n.t("Disk resource"))}</span>
        <span class="agent-status-muted">${esc(statusText)}${resource?.stale ? window.PortalI18n.t(" · 旧快照") : ""}${resource?.refreshing ? window.PortalI18n.t(" · 采集中") : ""}${checked ? ` · ${esc(checked)}` : ""}</span>
      </div>
      ${resource?.error ? window.PortalI18n.html`<div class="agent-status-error">${esc(resource.error)}</div>` : ""}
      ${resource?.refresh_error ? window.PortalI18n.html`<div class="agent-status-error">${esc(resource.refresh_error)}</div>` : ""}
      ${resource?.warning ? window.PortalI18n.html`<div class="agent-status-muted">${esc(resource.warning)}</div>` : ""}
      ${diskStageHtml(resource, "capacity", window.PortalI18n.t("文件系统容量"))}
      ${rows.length && ["pending", "running"].includes(resource?.stages?.capacity?.status) ? window.PortalI18n.html`<div class="agent-status-muted">显示上次容量，正在更新。</div>` : ""}
      <div class="agent-status-gpu-grid">${rows.length ? rows.map(diskRowHtml).join("") : window.PortalI18n.html`<div class="agent-status-muted">No filesystem data.</div>`}</div>
      <div class="agent-status-gpu-row">
        <div class="agent-status-gpu-title"><span>External targets</span><span>${targets.length}</span></div>
        ${diskStageHtml(resource, "transfer_targets", window.PortalI18n.t("外接目标"))}
        ${targets.length ? targets.map(diskTargetHtml).join("") : resource?.stages?.transfer_targets?.status === "ok" ? window.PortalI18n.html`<div class="agent-status-muted">No mounted external disk detected.</div>` : ""}
      </div>
      ${diskTriageHtml(resource)}
      ${suggestions.length || resource?.stages?.suggestions ? window.PortalI18n.html`<div class="agent-status-gpu-row">
        <div class="agent-status-gpu-title"><span>Lazy transfer suggestions</span><span>${suggestions.length}</span></div>
        ${diskStageHtml(resource, "suggestions", window.PortalI18n.t("迁移建议"))}
        ${suggestions.map(item => window.PortalI18n.html`<div class="agent-status-gpu-process">${esc(item)}</div>`).join("")}
      </div>` : ""}
    </div>`;
}

function diskDetailsHtml(disk) {
  const resources = Array.isArray(disk?.resources) ? disk.resources : [];
  if (!disk || (disk.status === "loading" && !resources.length)) {
    return window.PortalI18n.html`<div class="agent-status-gpu-card"><div class="agent-status-muted">Disk status is loading.</div></div>`;
  }
  if (resources.length) {
    const checked = formatDateTime(disk.updated_at);
    return window.PortalI18n.html`
      <div class="agent-status-gpu-card">
        ${resourceRefreshWarningHtml(disk, "Disk")}
        <div class="agent-status-gpu-head">
          <span>${esc(diskSummaryText(disk))}</span>
          <span class="agent-status-muted">${checked ? `Checked ${esc(checked)}` : window.PortalI18n.t("Click refreshes once")}</span>
        </div>
        <div class="agent-status-gpu-resource-list">${resources.map(diskSingleResourceHtml).join("")}</div>
      </div>`;
  }
  return window.PortalI18n.html`<div class="agent-status-gpu-card">${resourceRefreshWarningHtml(disk, "Disk")}${diskSingleResourceHtml(disk)}</div>`;
}

function rateLimitWindowLabel(quotaWindow, fallbackName = "Limit") {
  const minutes = Math.max(0, Number(quotaWindow?.window_duration_mins || 0));
  if (minutes === 7 * 24 * 60) return "Weekly";
  if (minutes && minutes % (24 * 60) === 0) return window.PortalI18n.text`${minutes / (24 * 60)}d`;
  if (minutes && minutes % 60 === 0) return window.PortalI18n.text`${minutes / 60}h`;
  if (minutes) return window.PortalI18n.text`${minutes}m`;
  return fallbackName;
}

function executionAccounts(data) {
  return Array.isArray(data?.accounts) ? data.accounts : [];
}

// A reading stays current until a read of it fails; age never expires it.
function accountQuotaWindowIsStale(window) {
  return Boolean(window?.stale);
}

// A failed summary read leaves the shown readings in place, marked stale.
function staleExecutionAccounts(accounts) {
  return accounts.map(account => ({
    ...account,
    rate_limits: (account.rate_limits || []).map(window => ({...window, stale: true})),
    ...(account.balance ? {balance: {...account.balance, stale: true}} : {}),
    ...(account.credits ? {credits: {...account.credits, stale: true}} : {}),
  }));
}

function accountQuotaWindows(account) {
  const windows = Array.isArray(account.rate_limits) ? account.rate_limits : [];
  const quota = account.quota_reserve || {};
  if (quota.status === "reserved" && quota.remaining_percent === 0
      && (!windows.length || windows.every(window => accountQuotaWindowIsStale(window)))) {
    return (quota.windows || []).filter(quotaWindow => quotaWindow.remaining_percent === 0).map(quotaWindow => ({
      id: quotaWindow.window, label: rateLimitWindowLabel(quotaWindow, window.PortalI18n.t("额度")), remaining_percent: 0, stale: true, resets_at: quotaWindow.resets_at,
    }));
  }
  return windows;
}

// An open account dialog shows the same windows as its row; replace them with
// the latest summary without re-rendering the rest of the dialog.
function refreshAccountQuotaWindows() {
  const accounts = new Map(executionAccounts(agentStatusData).map(account => [account.account_id, account]));
  for (const node of document.querySelectorAll("[data-account-quota-account]")) {
    const account = accounts.get(node.dataset.accountQuotaAccount);
    const quotaWindow = account && accountQuotaWindows(account).find(window => window.id === node.dataset.accountQuotaWindow);
    if (quotaWindow) node.outerHTML = accountQuotaWindow(account, quotaWindow);
    else if (account) node.remove();
  }
}

function accountQuotaWindow(account, quotaWindow) {
  const value = quotaWindow?.remaining_percent;
  const known = typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100;
  const remaining = known ? Number(value.toFixed(2)) : null;
  const stale = accountQuotaWindowIsStale(quotaWindow);
  const label = String(quotaWindow?.label || quotaWindow?.id || window.PortalI18n.t("额度"));
  const reset = quotaWindow?.resets_at ? formatDateTime(quotaWindow.resets_at * 1000) : "";
  const observed = quotaWindow?.observed_at;
  const sampled = formatDateTime(typeof observed === "number" ? observed * 1000 : observed || "");
  const title = [stale ? window.PortalI18n.t("数据已过期，百分比为上次采样") : !known ? window.PortalI18n.t("额度数据不可用") : window.PortalI18n.t("剩余额度"),
    sampled && window.PortalI18n.text`采样于 ${sampled}`, reset && window.PortalI18n.text`重置 ${reset}`, account?.error].filter(Boolean).join(" · ");
  return window.PortalI18n.html`<div class="agent-status-account-window" data-account-quota-account="${esc(account.account_id)}" data-account-quota-window="${esc(quotaWindow.id || "")}" title="${esc(title)}">
    <span class="agent-status-window-name">${esc(label)}</span>
    <span class="agent-status-limit-track${!known ? " is-unknown" : stale ? " is-stale" : ""}" ${known && !stale ? `role="meter" aria-label="${esc(window.PortalI18n.text`${account.account_id} ${label}剩余额度`)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${remaining}"` : `role="img" aria-label="${esc(title)}"`}>
      ${known ? window.PortalI18n.html`<span class="agent-status-limit-fill" style="width:${remaining}%"></span>` : ""}
    </span><span class="agent-status-limit-meta">${known ? `${remaining}%${stale ? "*" : ""}` : "—"}</span>
  </div>`;
}

function accountBalanceHtml(account) {
  const balance = account.balance;
  if (!balance) return "";
  const fresh = balance.status === "ok" && !balance.stale;
  const available = balance.metric_kind === "availability";
  const hasValue = balance.display !== undefined && balance.display !== null && String(balance.display) !== "";
  const missing = balance.status === "not_supported" || balance.reason === "unsupported_endpoint"
    ? "此连接未提供余额查询" : balance.reason === "unauthorized" ? "余额查询未获授权"
    : balance.reason === "invalid_response" ? "余额返回格式无法识别" : "余额暂不可用";
  const value = available ? (balance.status === "ok" ? "API 可用 · 余额未提供" : "API 状态不可用 · 余额未提供")
    : balance.status === "loading" ? "正在读取余额…" : hasValue ? `${balance.display}${fresh ? "" : "（上次记录）"}` : missing;
  const amount = balance.comparison_amount;
  const coinUnits = !available && hasValue && balance.comparison_currency === "CNY" && typeof amount === "number" && Number.isFinite(amount) && amount > 0
    ? Math.min(24, Math.max(1, 2 * Math.log2(1 + amount))) : 0;
  const coinCount = Math.ceil(coinUnits);
  const coins = coinUnits ? window.PortalI18n.html`<span class="agent-status-balance-coins${balance.comparison_stale ? " is-stale" : ""}" style="width:${(5 + 10 * coinUnits).toFixed(2)}px" aria-hidden="true">${Array.from({length: coinCount}, (_, index) => window.PortalI18n.html`<i style="--coin-position:${coinCount > 1 ? index / (coinCount - 1) : 0}"></i>`).join("")}</span>` : "";
  const sampled = formatDateTime(balance.updated_at || "");
  const comparison = typeof amount === "number" && balance.comparison_currency === "CNY"
    ? `金币按人民币等值比较${balance.currency !== "CNY" ? ` · 约 ${amount.toFixed(2)} CNY · 汇率 ${balance.exchange_rate_date || ""}` : ""}` : "";
  const title = [balance.source_account_id && window.PortalI18n.text`计费账号 ${balance.source_account_id}`, comparison, sampled && window.PortalI18n.text`更新于 ${sampled}`, balance.error].filter(Boolean).join(" · ");
  return window.PortalI18n.html`<div class="agent-status-account-balance${balance.stale ? " is-stale" : ""}" data-account-balance title="${esc(title)}"><span class="agent-status-muted">${available ? window.PortalI18n.t("账号状态") : window.PortalI18n.t("剩余余额")}</span><span class="agent-status-balance-value${available ? " is-availability" : ""}">${esc(value)}</span>${coins}</div>`;
}

// Codex credits pay for work after the plan quota runs out. Portal pauses the
// account's automatic work at that point, so they are shown, not spent.
function accountCreditsHtml(account) {
  const credits = account.credits;
  if (!credits || typeof credits !== "object") return "";
  const stale = Boolean(credits.stale) || account.status === "unavailable";
  const known = typeof credits.balance === "number" && Number.isFinite(credits.balance);
  // Balances run to tens of thousands; fractions only matter below 100.
  const value = credits.unlimited ? window.PortalI18n.t("不限") : known ? credits.balance.toLocaleString(undefined, {maximumFractionDigits: credits.balance < 100 ? 2 : 0}) : "—";
  const observed = credits.observed_at;
  const sampled = formatDateTime(typeof observed === "number" ? observed * 1000 : observed || "");
  const title = [window.PortalI18n.t("Codex credits 余额；额度用完时 Portal 暂停该账号的自动工作，不使用 credits"),
    sampled ? window.PortalI18n.text`采样于 ${sampled}` : window.PortalI18n.t("采样时间未知"),
    stale && window.PortalI18n.t("数据已过期，数值为上次采样"), account.error].filter(Boolean).join(" · ");
  return window.PortalI18n.html`<div class="agent-status-account-credits${stale ? " is-stale" : ""}" data-account-credits title="${esc(title)}"><span class="agent-status-window-name">Credits</span><span class="agent-status-account-credits-value">${esc(value)}${stale && known ? "*" : ""}</span></div>`;
}

function accountLimitRow(account) {
  const accountId = String(account.account_id || "");
  const windows = accountQuotaWindows(account);
  const quota = account.quota_reserve || {};
  const reserved = quota.status === "reserved";
  const blocked = (quota.windows || []).filter(window => typeof window.remaining_percent === "number" && window.remaining_percent <= quota.reserve_percent);
  const recovery = blocked.length && blocked.every(window => window.resets_at > 0) ? Math.max(...blocked.map(window => window.resets_at)) * 1000 : 0;
  const recoveryPending = recovery > 0 && recovery <= Date.now();
  const authenticationFailed = account.error_code === "provider_authentication_failed";
  const authenticationLabel = authenticationFailed ? window.PortalI18n.t('<span class="agent-status-muted" data-account-status-error>登录凭据失效</span>') : "";
  const content = authenticationLabel + (windows.length ? windows.map(window => accountQuotaWindow(account, window)).join("")
    : authenticationFailed ? "" : account.balance ? accountBalanceHtml(account) : window.PortalI18n.html`<span class="agent-status-muted"${reserved ? "" : " data-account-limit-stale"}>${esc(account.status === "loading" ? window.PortalI18n.t("正在读取额度…") : account.error || window.PortalI18n.t("额度数据不可用"))}</span>`);
  const resets = windows.filter(w => w.resets_at > Date.now() / 1000).map(w => w.resets_at);
  const refreshLabel = authenticationFailed ? window.PortalI18n.t("请重新登录该账号") : account.balance && !windows.length ? (account.balance.updated_at ? window.PortalI18n.text`更新 ${formatDateTime(account.balance.updated_at)}` : account.balance.status === "loading" ? window.PortalI18n.t("正在刷新余额…") : "") : reserved ? recovery > Date.now() ? window.PortalI18n.text`预计恢复 ${formatDateTime(recovery)}` : window.PortalI18n.t("等待额度恢复确认") : account.refreshing ? window.PortalI18n.t("正在刷新用量…") : account.error ? window.PortalI18n.t("用量刷新失败 · 查看详情")
    : resets.length ? window.PortalI18n.text`预计刷新 ${formatDateTime(Math.min(...resets) * 1000)}` : window.PortalI18n.t("等待用量刷新");
  return window.PortalI18n.html`<div class="agent-status-account-limit" data-account-limit-account="${esc(accountId)}">
    <span class="agent-status-account-name">${esc(accountId)}
      ${account.enabled === false ? window.PortalI18n.html`<small>已停用</small>` : ""}
      ${account.login_state === "login_expired" ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-account-relogin="${esc(accountId)}" ${canSetScheduler ? "" : "disabled"}>重新登录</button>` : ""}
    </span>
    <div class="agent-status-account-windows">
      <div class="agent-status-account-rate-windows">${content}</div>
      <button class="portalButton is-compact agent-status-account-details" type="button" data-account-details="${esc(accountId)}" aria-haspopup="dialog" aria-expanded="false" aria-controls="account-details">详情</button>

      <span class="agent-status-account-refresh">${reserved && quota.remaining_percent !== 0 ? window.PortalI18n.html`<span data-account-limit-stale>${recoveryPending ? window.PortalI18n.t("额度恢复待确认") : window.PortalI18n.t("额度不足，等待恢复")}</span> · ` : ""}${esc(refreshLabel)}</span>
    </div></div>`;
}

const accountIntakePending = new Map();
function accountSchedulerSettings(account) {
  const accountId = String(account.account_id || "");
  if (account.capabilities?.scheduler_settings === false) return window.PortalI18n.html`<p class="account-details-note">Close 阈值 · 未关联执行账号</p>`;
  const maxConcurrency = Number.isInteger(account.max_concurrency) && account.max_concurrency > 0 ? account.max_concurrency : null;
  const intervalMin = Number(account.dispatch_interval_min_seconds);
  const intervalMax = Number(account.dispatch_interval_max_seconds);
  const hasSchedulerSettings = account.capabilities?.scheduler_settings === true && maxConcurrency !== null
    && Number.isFinite(intervalMin) && Number.isFinite(intervalMax) && intervalMin >= 0 && intervalMax >= intervalMin;
  const threshold = account.close_threshold;
  const canSetThreshold = hasSchedulerSettings && threshold?.supported === true && typeof threshold.value === "number" && Number.isFinite(threshold.value);
  const pending = accountSchedulerPending.has(accountId);
  return window.PortalI18n.html`<form class="account-details-settings" data-account-scheduler-settings-form="${esc(accountId)}" novalidate>
    <h4>调度设置</h4><fieldset ${pending ? "disabled" : ""}>
        <div class="agent-status-account-concurrency" title="${canSetScheduler ? window.PortalI18n.t("设置该账号的最大并发数") : window.PortalI18n.t("你没有修改账号调度设置的权限（UpdateProviderAccount）")}">
          <label for="account-concurrency-${esc(accountId)}">并发上限</label>
          <input id="account-concurrency-${esc(accountId)}" data-account-max-concurrency name="concurrency" type="number" min="1" max="256" step="1" value="${esc(maxConcurrency === null ? "" : String(maxConcurrency))}" ${(canSetScheduler && hasSchedulerSettings) ? "" : "disabled"} aria-label="${esc(accountId)} 并发上限" />
        </div>
        <div class="agent-status-account-threshold" title="${!canSetThreshold ? window.PortalI18n.t("未提供可用的余额或额度单位，暂不可设置") : canSetScheduler ? window.PortalI18n.t("剩余额度或原币余额小于等于正阈值时自动 Close；0 表示不设阈值，余额账号自然用光，额度账号在额度用完时暂停") : window.PortalI18n.t("你没有修改账号调度设置的权限（UpdateProviderAccount）")}">
          <label for="account-threshold-${esc(accountId)}">Close 阈值</label>
          <input id="account-threshold-${esc(accountId)}" data-account-close-threshold name="threshold" data-close-threshold-unit="${esc(threshold?.unit || "")}" type="number" min="0" ${threshold?.metric === "percent" ? 'max="100"' : ""} step="any" placeholder="—" value="${esc(canSetThreshold ? String(threshold.value) : "")}" ${(canSetScheduler && canSetThreshold) ? "" : "disabled"} aria-label="${esc(accountId)} Close 阈值" />
          <span>${esc(threshold?.unit || "")}</span>
        </div>
        <div class="agent-status-account-interval" title="${canSetScheduler ? window.PortalI18n.t("设置同一账号相邻消息的随机最小间隔") : window.PortalI18n.t("你没有修改账号调度设置的权限（UpdateProviderAccount）")}">
          <label for="account-interval-min-${esc(accountId)}">间隔(秒)</label>
          <input id="account-interval-min-${esc(accountId)}" data-account-dispatch-min name="intervalMin" type="number" min="0" max="3600" step="0.1" value="${esc(hasSchedulerSettings ? String(intervalMin) : "")}" ${(canSetScheduler && hasSchedulerSettings) ? "" : "disabled"} aria-label="${esc(accountId)} 最小间隔秒数" />
          <span aria-hidden="true">–</span>
          <input data-account-dispatch-max name="intervalMax" type="number" min="0" max="3600" step="0.1" value="${esc(hasSchedulerSettings ? String(intervalMax) : "")}" ${(canSetScheduler && hasSchedulerSettings) ? "" : "disabled"} aria-label="${esc(accountId)} 最大间隔秒数" />
        </div>
    <button class="portalButton is-compact" type="submit" data-account-action="save-settings" ${canSetScheduler && hasSchedulerSettings ? "" : "disabled"}>${pending ? window.PortalI18n.t("保存中…") : window.PortalI18n.t("保存")}</button>
    </fieldset></form>`;
}

let accountDetails = null;
const accountSchedulerPending = new Set();

function accountManagementActions(hasAccounts) {
  return window.PortalI18n.html`
    ${canSetScheduler || canRegisterAccount ? window.PortalI18n.html`<button class="portalButton" type="button" data-account-manage="">管理 AI 连接</button>` : ""}
    ${hasAccounts && canRegisterAccount ? window.PortalI18n.html`<button class="portalButton is-primary" type="button" data-account-register ${accountRegistrationPending ? "disabled" : ""}>新增 AI 账号</button>` : ""}`;
}

function accountEmptyState(data) {
  if (data?.status === "loading" || data?.refreshing === true) {
    return window.PortalI18n.html`<div class="agent-status-muted">Loading limits…</div>`;
  }
  const message = data?.accounts_error || data?.accounts_refresh_error
    ? window.PortalI18n.t("Limit data unavailable.") : window.PortalI18n.t("尚未添加 AI 账号。");
  return window.PortalI18n.html`<div class="agent-status-empty agent-status-muted"><p>${esc(message)}</p>
    ${canRegisterAccount ? window.PortalI18n.html`<button class="portalButton is-primary" type="button" data-account-register ${accountRegistrationPending ? "disabled" : ""}>新增 AI 账号</button>` : ""}</div>`;
}

async function openAccountRegistration(button) {
  if (!canRegisterAccount || accountRegistrationPending) return;
  accountRegistrationPending = true;
  button.disabled = true;
  try {
    await window.PortalNav.openPermissionManagement({view: "backend", createAccount: true});
  } catch (error) { window.PortalUI.toast(error.message, "error"); }
  finally {
    accountRegistrationPending = false;
    document.querySelectorAll("[data-account-register]").forEach(el => { el.disabled = !canRegisterAccount; });
  }
}

function syncAccountDetailsButtons() {
  for (const button of document.querySelectorAll("[data-account-register]:not([data-backend-bound])")) {
    button.dataset.backendBound = "true";
    button.addEventListener("click", () => void openAccountRegistration(button));
  }
  for (const link of document.querySelectorAll("[data-account-manage]:not([data-backend-bound])")) {
    link.dataset.backendBound = "true";
    link.addEventListener("click", async event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      accountDetails?.popover.close();
      try { await window.PortalNav.openPermissionManagement({view: "backend", accountId: link.dataset.accountManage}); }
      catch (error) { window.PortalUI.toast(error.message, "error"); }
    });
  }
  for (const button of document.querySelectorAll("[data-account-relogin]:not([data-backend-bound])")) {
    button.dataset.backendBound = "true";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { (await import("/static/portal_management_backend.js")).openLogin(button.dataset.accountRelogin); }
      catch (error) { window.PortalUI.toast(error.message, "error"); }
      finally { button.disabled = !canSetScheduler; }
    });
  }
  for (const button of document.querySelectorAll("[data-account-details]")) {
    if (!button.dataset.detailsBound) {
      button.dataset.detailsBound = "true";
      button.addEventListener("click", () => openAccountDetails(button));
    }
    const active = accountDetails?.popover.isOpen() && accountDetails.accountId === button.dataset.accountDetails;
    button.setAttribute("aria-expanded", String(Boolean(active)));
    if (active) accountDetails.trigger = button;
  }
}

function ensureAccountDetails() {
  if (accountDetails) return accountDetails;
  const panel = document.createElement("section");
  panel.id = "account-details";
  panel.className = "portalDetailSurface account-details";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", window.PortalI18n.t("详情"));
  panel.tabIndex = -1;
  document.body.appendChild(panel);
  const details = { panel, accountId: "", snapshot: null, loading: false, error: "", result: "", requestVersion: 0, trigger: null };
  details.popover = window.PortalOverlay.bindFloatingPopover({
    panel, placement: "bottom", align: "start",
    onClose: event => {
      details.requestVersion += 1;
      details.abort?.abort();
      details.trigger?.setAttribute("aria-expanded", "false");
      if ((event?.key === "Escape" || panel.contains(event?.target)) && details.trigger?.isConnected) details.trigger.focus({ preventScroll: true });
    },
  });
  accountDetails = details;
  return details;
}

function openAccountDetails(button) {
  const details = ensureAccountDetails();
  const accountId = String(button.dataset.accountDetails || "");
  if (details.popover.isOpen() && details.accountId === accountId) {
    details.popover.close();
    return;
  }
  details.trigger?.setAttribute("aria-expanded", "false");
  details.panel.replaceChildren();
  Object.assign(details, { accountId, trigger: button, snapshot: null, error: "", result: "", moreExpanded: false });
  const bounds = button.getBoundingClientRect();
  details.popover.open({ x: bounds.left, y: bounds.bottom });
  syncAccountDetailsButtons();
  loadAccountDetails(details);
  details.panel.focus({ preventScroll: true });
}

async function loadAccountDetails(details) {
  const accountId = details.accountId;
  const version = ++details.requestVersion;
  details.abort?.abort();
  details.abort = new AbortController();
  if (executionAccounts(agentStatusData).find(item => item.account_id === accountId)?.capabilities?.resident_details === false) {
    details.loading = false;
    details.error = "";
    details.snapshot = null;
    renderAccountDetails();
    return;
  }
  details.loading = true;
  details.error = "";
  renderAccountDetails();
  try {
    const snapshot = await homeApi.get(`/api/agent-status/account-residents?account_id=${encodeURIComponent(accountId)}`, { signal: details.abort.signal });
    if (version !== details.requestVersion || accountDetails !== details || !details.popover.isOpen()) return;
    if (snapshot?.account_id !== accountId) throw new Error("账号详情响应不匹配，请刷新重试");
    details.snapshot = snapshot;
  } catch (error) {
    if (version !== details.requestVersion || accountDetails !== details || !details.popover.isOpen()) return;
    details.snapshot = null;
    details.error = window.PortalUI.errorMessage(window.PortalI18n.t("读取账号详情"), error);
  } finally {
    if (version === details.requestVersion && accountDetails === details && details.popover.isOpen()) {
      details.loading = false;
      renderAccountDetails();
    }
  }
}

function renderAccountDetails() {
  const details = accountDetails;
  if (!details?.popover.isOpen()) return;
  details.moreExpanded = details.panel.querySelector(".account-details-more")?.open ?? details.moreExpanded;
  const scrollTop = details.panel.querySelector(".account-details-body")?.scrollTop || 0;
  const snapshot = details.snapshot;
  const pendingAction = accountIntakePending.get(details.accountId);
  const busy = Boolean(pendingAction);
  const metric = (name, label, count) => window.PortalI18n.html`<div><dt>${esc(label)}</dt><dd data-account-metric="${esc(name)}">${Number(count || 0)}</dd></div>`;
  let body = details.loading ? window.PortalI18n.markup('<p role="status">正在读取账号节点状态…</p>') : details.error ? window.PortalI18n.html`<p role="alert">${esc(details.error)}</p>` : "";
  if (snapshot && !details.loading && !details.error) {
    const quota = snapshot.quota || {};
    const available = quota.status === "available" && quota.automatic_paused !== true;
    const unit = quota.close_threshold?.unit || "";
    const remaining = typeof quota.remaining === "number" ? `${quota.remaining.toFixed(1)}${unit === "%" ? "%" : ` ${unit}`}` : "未知";
    const quotaText = quota.used_up === true ? window.PortalI18n.t("额度已用完 · 暂停自动工作，等待额度恢复") : quota.threshold_enabled === false ? "不设 Close 阈值限制 · 按账号实际可用额度运行" : quota.status === "unknown" || !quota.status ? "额度状态未知" : available ? `额度可用 · 最低剩余 ${remaining}` : `额度保护中 · 最低剩余 ${remaining}`;
    body = window.PortalI18n.html`<p class="account-details-note">${window.PortalI18n.t("统计范围：你可见的 Resident")}</p>
      <p class="account-details-quota">${esc(quotaText)}</p>
      <dl class="account-details-metrics">${metric("total", window.PortalI18n.t("节点总数"), snapshot.total)}${metric("queued_messages", window.PortalI18n.t("待发消息"), snapshot.queued_messages)}</dl>
      <h4>接单状态</h4><dl class="account-details-metrics is-intake">${metric("open", window.PortalI18n.t("Open"), snapshot.intake?.open)}${metric("closed", "Closed", snapshot.intake?.closed)}${snapshot.intake?.unknown ? metric("intake_unknown", window.PortalI18n.t("状态未知"), snapshot.intake.unknown) : ""}</dl>
      <h4>工作状态</h4><dl class="account-details-metrics is-work">${metric("running", window.PortalI18n.t("运行中"), snapshot.work?.running)}${metric("queued", window.PortalI18n.t("排队中"), snapshot.work?.queued)}${metric("idle", window.PortalI18n.t("空闲"), snapshot.work?.idle)}${metric("stopping", window.PortalI18n.t("停止中"), snapshot.work?.stopping)}${snapshot.work?.unknown ? metric("work_unknown", window.PortalI18n.t("状态未知"), snapshot.work.unknown) : ""}</dl>
      <details class="account-details-more" ${details.moreExpanded ? "open" : ""}><summary data-account-action="more">补充统计</summary>
        <p class="account-details-note">${esc(formatDateTime(snapshot.observed_at) || "")}<br>未创建 Session ${Number(snapshot.without_session || 0)} · 人工额度放行 ${Number(snapshot.quota_exempt || 0)}<br>最近输入失败 ${Number(snapshot.latest_input?.failed || 0)} · 已取消 ${Number(snapshot.latest_input?.cancelled || 0)}</p>
      </details>`;
  }
  const account = executionAccounts(agentStatusData).find(item => item.account_id === details.accountId);
  const balance = account?.balance;
  const billingLink = balance?.source_account_id && balance.source_account_id !== account.account_id
    ? window.PortalI18n.html`<span class="agent-status-muted" data-account-billing-link>共用 ${esc(balance.source_account_id)} 余额</span>` : "";
  if (account) body = window.PortalI18n.html`${accountBalanceHtml(account)}${billingLink ? window.PortalI18n.html`<p class="account-details-note">${billingLink}</p>` : ""}<div class="agent-status-account-rate-windows">${accountQuotaWindows(account).map(window => accountQuotaWindow(account, window)).join("")}${accountCreditsHtml(account)}</div>${account.error ? window.PortalI18n.html`<p role="alert">${esc(account.error)}</p>` : ""}${body}`;
  const focusedElement = details.panel.contains(document.activeElement) ? document.activeElement : null;
  const focused = focusedElement?.dataset.accountAction;
  const focusedInput = focusedElement?.name;
  const previousForm = details.panel.querySelector("[data-account-scheduler-settings-form]");
  const drafts = previousForm?.dataset.accountSchedulerSettingsForm === details.accountId
    ? Array.from(previousForm.querySelectorAll("input")).filter(input => input.value !== input.defaultValue)
      .map(input => ({ name: input.name, value: input.value })) : [];
  details.panel.innerHTML = window.PortalI18n.html`<header><strong>${esc(details.accountId)} · ${window.PortalI18n.t("详情")}</strong><button class="portalButton is-compact" type="button" data-account-action="close" aria-label="关闭账号详情">×</button></header>
    <div class="account-details-body">${account ? accountSchedulerSettings(account) : ""}${body}${details.result ? window.PortalI18n.html`<p role="status" data-account-result>${esc(details.result)}</p>` : ""}</div>
    <footer><div class="account-details-actions"><button class="portalButton is-compact" type="button" data-account-action="refresh" ${details.loading || busy ? "disabled" : ""}>刷新</button>
      <a class="portalButton is-compact" data-account-action="backend" data-account-manage="${esc(details.accountId)}" href="/portal-management?view=backend#account_id=${encodeURIComponent(details.accountId)}">AI Backend</a>
      ${account?.balance?.billing_url ? window.PortalI18n.html`<a class="portalButton is-compact" data-account-action="billing" href="${esc(account.balance.billing_url)}" target="_blank" rel="noopener noreferrer">账单</a>` : ""}</div>
      ${account?.capabilities?.resident_details === false ? "" : window.PortalI18n.html`<div class="account-details-actions"><button class="portalButton is-compact" type="button" data-account-action="close-all" data-account-close-residents="${esc(details.accountId)}" ${!snapshot?.open_resident_ids?.length || details.loading || details.error || busy ? "disabled" : ""} title="将当前账号下 Resident 设为手动 Closed，保留队列与正在执行的任务">${pendingAction === "close" ? window.PortalI18n.t("关闭中…") : window.PortalI18n.t("全部关闭")}</button>
      <button class="portalButton is-compact" type="button" data-account-action="open-all" data-account-open-residents="${esc(details.accountId)}" ${!snapshot?.closed_resident_ids?.length || details.loading || details.error || busy ? "disabled" : ""} title="将当前账号下全部关闭的 Resident 按人工 Open 打开">${pendingAction === "open" ? window.PortalI18n.t("打开中…") : window.PortalI18n.t("全部打开")}</button></div>`}</footer>`;
  details.panel.querySelector('[data-account-action="close"]').addEventListener("click", event => details.popover.close(event));
  details.panel.querySelector('[data-account-action="refresh"]').addEventListener("click", () => { loadAgentStatus(true); loadAccountDetails(details); });
  details.panel.querySelector('[data-account-open-residents]')?.addEventListener("click", event => changeAccountResidentsIntake(event.currentTarget, "open"));
  details.panel.querySelector('[data-account-close-residents]')?.addEventListener("click", event => changeAccountResidentsIntake(event.currentTarget, "close"));
  details.panel.querySelector(".account-details-more")?.addEventListener("toggle", event => {
    if (!event.currentTarget.isConnected) return;
    details.moreExpanded = event.currentTarget.open;
    details.popover.reposition();
  });
  const form = details.panel.querySelector("[data-account-scheduler-settings-form]");
  if (form) {
    for (const draft of drafts) form.elements.namedItem(draft.name).value = draft.value;
    form.addEventListener("input", event => event.target.setCustomValidity?.(""));
    form.addEventListener("submit", event => {
      event.preventDefault();
      saveAccountSchedulerSettings(form);
    });
  }
  details.panel.querySelector(".account-details-body").scrollTop = scrollTop;
  syncAccountDetailsButtons();
  if (focusedInput) form?.elements.namedItem(focusedInput)?.focus({ preventScroll: true });
  if (focused) details.panel.querySelector(`[data-account-action="${focused}"]:not(:disabled)`)?.focus({ preventScroll: true });
  details.popover.reposition();
}

async function changeAccountResidentsIntake(button, action) {
  const opening = action === "open";
  const label = opening ? window.PortalI18n.t("打开") : window.PortalI18n.t("关闭");
  const accountId = String((opening ? button.dataset.accountOpenResidents : button.dataset.accountCloseResidents) || "");
  // The visible Residents not yet in the target state; each is checked on its own resident_id.
  const snapshot = accountDetails?.accountId === accountId ? accountDetails.snapshot : null;
  const residentIds = [...((opening ? snapshot?.closed_resident_ids : snapshot?.open_resident_ids) || [])];
  if (!accountId || !residentIds.length || accountIntakePending.has(accountId)) return;
  accountIntakePending.set(accountId, action);
  renderAccountDetails();
  try {
    const result = await window.PortalInstructionClient.execute("OpenIntake", { resident_ids: residentIds, open: opening }, {
      idempotencyKey: `account-intake:${accountId}:${globalThis.PortalUIPrimitives.randomUUID()}`,
    });
    const changed = Number(result.changed_count || 0);
    const unchanged = Number(result.unchanged_count || 0);
    const failed = Number(result.failed_count || 0);
    const effect = window.PortalI18n.t(opening ? "待发消息将继续调度，已取消的执行不会重放。" : "待发消息保留在队列中，正在执行的任务继续。");
    const message = changed
      ? window.PortalI18n.text`${accountId} 已${label} ${changed} 个 Resident；${effect}`
      : failed ? window.PortalI18n.text`${accountId} 本次未${label}任何节点。`
      : window.PortalI18n.text`${accountId} 当前没有需要${label}的节点。`;
    const feedback = [message,
      unchanged ? window.PortalI18n.text`${unchanged} 个节点本来就已${label}。` : "",
      failed ? window.PortalI18n.text`${failed} 个节点${label}失败，可重试。` : "",
      result.quota_override_pending ? window.PortalI18n.t("人工额度放行设置尚待同步，系统将自动重试。") : ""].filter(Boolean).join(" ");
    if (accountDetails?.accountId === accountId) accountDetails.result = feedback;
    window.PortalUI.toast(feedback, failed ? "error" : "success");
  } catch (err) {
    const feedback = window.PortalUI.errorMessage(window.PortalI18n.text`${label}账号 Resident`, err);
    if (accountDetails?.accountId === accountId) accountDetails.result = feedback;
    window.PortalUI.toast(feedback, "error");
  } finally {
    accountIntakePending.delete(accountId);
    if (accountDetails?.accountId === accountId && accountDetails.popover.isOpen()) loadAccountDetails(accountDetails);
    activeAgentStatusRefresh?.refresh({ force: true }).catch(() => {});
  }
}

async function saveAccountSchedulerSettings(form) {
  const accountId = String(form.getAttribute("data-account-scheduler-settings-form") || "");
  if (!canSetScheduler || accountSchedulerPending.has(accountId) || !form.isConnected) return;
  const concurrencyInput = form.querySelector("[data-account-max-concurrency]");
  const intervalMinInput = form.querySelector("[data-account-dispatch-min]");
  const intervalMaxInput = form.querySelector("[data-account-dispatch-max]");
  const thresholdInput = form.querySelector("[data-account-close-threshold]");
  let closeThreshold;
  const button = form.querySelector('button[type="submit"]');
  const maxConcurrency = Number(concurrencyInput?.value || 0);
  const intervalMin = Number(intervalMinInput?.value);
  const intervalMax = Number(intervalMaxInput?.value);
  if (!Number.isInteger(maxConcurrency) || maxConcurrency < 1 || maxConcurrency > 256) {
    concurrencyInput?.setCustomValidity("请输入 1–256 的整数");
    concurrencyInput?.reportValidity();
    concurrencyInput?.focus();
    return;
  }
  concurrencyInput?.setCustomValidity("");
  if (!intervalMinInput?.value.trim() || !Number.isFinite(intervalMin) || intervalMin < 0 || intervalMin > 3600) {
    intervalMinInput?.setCustomValidity("请输入 0–3600 秒之间的数字");
    intervalMinInput?.reportValidity();
    intervalMinInput?.focus();
    return;
  }
  intervalMinInput?.setCustomValidity("");
  if (!intervalMaxInput?.value.trim() || !Number.isFinite(intervalMax) || intervalMax < intervalMin || intervalMax > 3600) {
    intervalMaxInput?.setCustomValidity("最大间隔须不小于最小间隔，且不超过 3600 秒");
    intervalMaxInput?.reportValidity();
    intervalMaxInput?.focus();
    return;
  }
  intervalMaxInput?.setCustomValidity("");
  if (thresholdInput && !thresholdInput.disabled) {
    const value = Number(thresholdInput.value);
    const unit = thresholdInput.dataset.closeThresholdUnit;
    if (!thresholdInput.value.trim() || !Number.isFinite(value) || value < 0 || (unit === "%" && value > 100)) {
      thresholdInput.setCustomValidity(unit === "%" ? "请输入 0–100 之间的百分比" : "请输入非负的原币金额");
      thresholdInput.reportValidity();
      thresholdInput.focus();
      return;
    }
    thresholdInput.setCustomValidity("");
    closeThreshold = {value, unit};
  }
  if (concurrencyInput.disabled || intervalMinInput.disabled || intervalMaxInput.disabled) return;
  accountSchedulerPending.add(accountId);
  form.querySelector("fieldset").disabled = true;
  if (button) button.textContent = window.PortalI18n.t("保存中…");
  try {
    const result = (await window.PortalInstructionClient.execute("UpdateProviderAccount", {
      account_id: accountId,
      max_concurrency: maxConcurrency,
      dispatch_interval_min_seconds: intervalMin,
      dispatch_interval_max_seconds: intervalMax,
      ...(closeThreshold
        ? {[closeThreshold.unit === "%" ? "close_threshold_percent" : "close_threshold_amount"]: closeThreshold.value}
        : {}),
    }, {idempotencyKey: `scheduler:${accountId}:${globalThis.PortalUIPrimitives.randomUUID()}`}))?.settings || {};
    const account = executionAccounts(agentStatusData).find(
      item => String(item?.account_id || "") === accountId,
    );
    if (account) {
      if (result?.close_threshold) account.close_threshold = {...account.close_threshold, ...result.close_threshold};
      else if (closeThreshold) account.close_threshold = {...account.close_threshold, ...closeThreshold};
      account.max_concurrency = Number(result?.max_concurrency ?? maxConcurrency);
      account.dispatch_interval_min_seconds = Number(
        result?.dispatch_interval_min_seconds ?? intervalMin,
      );
      account.dispatch_interval_max_seconds = Number(
        result?.dispatch_interval_max_seconds ?? intervalMax,
      );
    }
    window.PortalUI.toast(window.PortalI18n.text`${accountId} 调度设置已更新`, "success");
    const displayedForm = accountDetails?.accountId === accountId
      ? accountDetails.panel.querySelector("[data-account-scheduler-settings-form]") : null;
    // A detail refresh may replace the disabled form while this save is pending.
    for (const input of (displayedForm || form).querySelectorAll("input")) input.defaultValue = input.value;
    renderAgentStatus(agentStatusData);
  } catch (err) {
    window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("保存账号调度设置"), err), "error");
  } finally {
    accountSchedulerPending.delete(accountId);
    if (accountDetails?.accountId === accountId) renderAccountDetails();
  }
}

function agentStatusColorMap(keys) {
  const fixed = {
    unregistered: "#c0c6cf",
    other_residents: "#64748b",
  };
  const palette = [
    "#1e5eb8", "#e1475a", "#1eb838", "#8e47e1", "#b8921e",
    "#47dae1", "#b81e85", "#80e147", "#1e2bb8", "#e16747",
    "#1eb86b", "#c147e1", "#abb81e", "#47a7e1", "#b81e51",
    "#4de147", "#451eb8", "#e19b47", "#1eb89f", "#e147cd",
  ];
  const colors = new Map();
  let residentIndex = 0;
  for (const key of keys) {
    if (fixed[key]) {
      colors.set(key, fixed[key]);
    } else {
      colors.set(key, palette[residentIndex % palette.length]);
      residentIndex += 1;
    }
  }
  return colors;
}

function agentStatusColor(key, index, colorMap) {
  if (colorMap?.has(key)) return colorMap.get(key);
  const fallback = ["#1e5eb8", "#e1475a", "#1eb838", "#8e47e1", "#b8921e", "#47dae1"];
  return fallback[index % fallback.length];
}

function chartSegments(point) {
  return Array.isArray(point?.segments) ? point.segments : [];
}

function chartSeries(data) {
  const seriesByRange = data?.[AGENT_STATUS_GROUPINGS[agentStatusGrouping].series] || {};
  return Array.isArray(seriesByRange[agentStatusChartRange]) ? seriesByRange[agentStatusChartRange] : [];
}

function selectedTokenUsage(data) {
  const scopes = data?.scopes || [];
  const tokenUsage = usageBackendFilter || usageAccountFilter
    ? scopes.find(scope => scope.provider === usageBackendFilter && scope.account_id === (usageAccountFilter === "__unrecorded__" ? "" : usageAccountFilter || null)) || {status: "loading", windows: {}}
    : data || {};
  const variants = tokenUsage.variants || {};
  const key = agentStatusIncludeCachedInput ? "including_cached_input" : "excluding_cached_input";
  return {...(variants[key] || tokenUsage), account_history_incomplete: tokenUsage.account_history_incomplete};
}

function usageWindowValue(window, key) {
  const value = Number(window?.[key] || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function usageWindowSummary(tokenUsage) {
  const windows = tokenUsage?.windows || {};
  return AGENT_STATUS_USAGE_RANGES.map(range => {
    const usageWindow = windows[range] || {};
    const total = usageWindowValue(usageWindow, "total_tokens");
    const label = range[0].toUpperCase() + range.slice(1);
    return window.PortalI18n.html`
      <div class="agent-status-usage-pill">
        <strong>${esc(label)} ${Number.isFinite(usageWindow.total_tokens) ? formatTokens(total) : "--"}</strong>
      </div>
    `;
  }).join("");
}

function usageChartNote(tokenUsage) {
  if (tokenUsage?.status === "error") {
    return "Usage unavailable.";
  }
  if (tokenUsage?.status === "partial") {
    return "Usage data incomplete.";
  }
  if (tokenUsage?.status === "loading") {
    return "Loading usage…";
  }
  return "";
}

function agentStatusRunHref(run) {
  if (!run || run.type !== "resident" || !run.resident_id) return "";
  const explicitUrl = String(run.url || "").trim();
  return residentNavigationUrl({
    residentId: run.resident_id,
    lineageId: run.lineage_id,
    url: explicitUrl,
    openChat: true,
  });
}

function agentStatusCreature(index, activityState, label, run = null) {
  const residentSnapshot = run?.type === "resident"
    ? window.PortalResidentProtocol.stateSnapshot(run.resident_id)
    : null;
  const stateLabel = { running: window.PortalI18n.t("运行中"), queued: window.PortalI18n.t("排队"), unread: window.PortalI18n.t("未读回复") }[activityState] || activityState;
  if (residentSnapshot) {
    // This icon represents one message. Resident work_state is aggregate state and
    // must not overwrite a queued message while another message is running.
    label = `${label} · ${stateLabel}${residentSnapshot.state?.intake_state === "closed" ? " · 接单关闭" : ""}`;
  }
  const avatar = window.PortalResidentAvatar.presentation(run);
  const delay = `${index * -0.18}s`;
  const href = agentStatusRunHref(run);
  const rarity = avatar.rarity;
  const accessibleLabel = rarity ? `${label} · ${rarity}` : label;
  const rarityClass = rarity === "SSSR"
    ? " rarity-sssr"
    : rarity === "SSR" ? " rarity-ssr" : "";
  const attrs = `class="agent-status-creature ${avatar.className} ${esc(activityState)}${rarityClass}" style="${esc(avatar.style)}; --creature-delay:${delay}" title="${esc(accessibleLabel)}" data-sprite-rarity="${esc(rarity)}"`;
  const content = rarity === "SSSR" && activityState === "running"
    ? window.PortalI18n.markup('<span class="agent-status-creature-sssr-glow outer"><span class="agent-status-creature-sssr-glow-source"></span></span><span class="agent-status-creature-sssr-glow inner"><span class="agent-status-creature-sssr-glow-source"></span></span>')
    : "";
  return href
    ? window.PortalI18n.html`<a ${attrs} href="${esc(href)}" aria-label="${esc(accessibleLabel)}" data-agent-status-resident="${esc(run.resident_id)}" data-agent-status-lineage="${esc(run.lineage_id || "")}">${content}</a>`
    : window.PortalI18n.html`<span ${attrs} role="img" aria-label="${esc(accessibleLabel)}">${content}</span>`;
}

function agentStatusCreatureGroup(count, state, offset, runs = []) {
  const total = Math.max(0, Number(count || 0));
  const visible = Math.min(total, runs.length, 18);
  const icons = [];
  for (let i = 0; i < visible; i += 1) {
    const run = runs[i];
    const fullTitle = String(run?.title || "").trim();
    const label = String(run?.session_title || fullTitle || "Agent").trim();
    icons.push(agentStatusCreature(offset + i, state, label, run));
  }
  if (total > visible) icons.push(window.PortalI18n.html`<span class="agent-status-creature-more">+${total - visible}</span>`);
  return icons.join("");
}

function agentStatusAccountDivider(leftAccountId, rightAccountId) {
  const left = String(leftAccountId || "all");
  const right = String(rightAccountId || "all");
  return window.PortalI18n.html`
    <span class="agent-status-account-divider" role="separator" aria-label="${esc(`${left} and ${right} account divider`)}" data-account-divider-from="${esc(left)}" data-account-divider-to="${esc(right)}">
      <span class="agent-status-account-divider-line" aria-hidden="true"></span>
    </span>
  `;
}

function agentStatusRunboard(active) {
  const running = Math.max(0, Number(active?.running || 0));
  const queued = Math.max(0, Number(active?.queued || 0));
  const maxConcurrency = Math.max(1, Number(active?.max_concurrency || 8));
  // The activity owner selects queue entries before limiting the response.
  const sourceGroups = Array.isArray(active?.account_groups) && active.account_groups.length
    ? active.account_groups
    : [{
        account_id: "",
        running,
        queued,
        queued_display_count: active?.queued_display_count,
        running_runs: Array.isArray(active?.running_runs) ? active.running_runs : [],
        queued_runs: Array.isArray(active?.queued_runs) ? active.queued_runs : [],
      }];
  const activeResidentIds = new Set(sourceGroups.flatMap(group => [
    ...(Array.isArray(group?.running_runs) ? group.running_runs : []),
    ...(Array.isArray(group?.queued_runs) ? group.queued_runs : []),
  ]).filter(run => run?.type === "resident" && run.resident_id).map(run => String(run.resident_id)));
  const unreadRuns = (window.PortalConversationStore?.entries?.() || [])
    .filter(entry => (
      entry?.unread === true
      && entry?.resident_id
      && !activeResidentIds.has(String(entry.resident_id))
      && !["queued", "running", "stopping"].includes(String(entry.status || "").toLowerCase())
    ))
    .map(entry => ({
      ...entry,
      type: "resident",
      title: String(entry.title || entry.resident_title || entry.resident_id),
      session_title: String(entry.title || entry.resident_title || entry.resident_id),
      status: "unread",
    }));
  const groupsByAccount = new Map(sourceGroups.map(group => [
    String(group?.account_id || ""), { ...group, unread_runs: [] },
  ]));
  for (const run of unreadRuns) {
    const accountId = String(run.account_id || "other");
    if (!groupsByAccount.has(accountId)) groupsByAccount.set(accountId, { account_id: accountId, unread_runs: [] });
    groupsByAccount.get(accountId).unread_runs.push(run);
  }
  const catalogOrder = new Map(executionAccounts(agentStatusData).map((account, index) => [account.account_id, index]));
  const groups = [...groupsByAccount.values()].sort((left, right) =>
    (catalogOrder.get(left.account_id) ?? Number.MAX_SAFE_INTEGER)
      - (catalogOrder.get(right.account_id) ?? Number.MAX_SAFE_INTEGER));
  const renderedGroups = [];
  let offset = 0;
  for (const group of groups) {
    const accountId = String(group?.account_id || "");
    const groupRunning = Math.max(0, Number(group?.running || 0));
    const groupQueued = Math.max(0, Number(group?.queued_display_count || 0));
    const runningRuns = Array.isArray(group?.running_runs) ? group.running_runs : [];
    const queuedRuns = Array.isArray(group?.queued_runs) ? group.queued_runs : [];
    const icons = [
      agentStatusCreatureGroup(groupRunning, "running", offset, runningRuns),
      agentStatusCreatureGroup(groupQueued, "queued", offset + groupRunning, queuedRuns),
      agentStatusCreatureGroup(group.unread_runs.length, "unread", offset + groupRunning + groupQueued, group.unread_runs),
    ].filter(Boolean).join("");
    if (!icons) continue;
    renderedGroups.push({
      accountId: accountId || "all",
      html: window.PortalI18n.html`
        <span class="agent-status-account-group" data-agent-account-group="${esc(accountId || "all")}">
          <span class="agent-status-account-icons">${icons}</span>
          ${accountId ? window.PortalI18n.html`<span class="agent-status-account-label">${esc(accountId)}</span>` : ""}
        </span>
      `,
    });
    offset += groupRunning + groupQueued + group.unread_runs.length;
  }
  if (!renderedGroups.length) return "";
  const icons = renderedGroups.map((group, index) => {
    if (!index) return group.html;
    return `${agentStatusAccountDivider(renderedGroups[index - 1].accountId, group.accountId)}${group.html}`;
  }).join("");
  const summary = `${running} running, ${queued} queued, ${unreadRuns.length} unread, concurrency limit ${maxConcurrency}`;
  return window.PortalI18n.html`
    <div class="agent-status-creature-strip" aria-label="${esc(summary)}" title="${esc(summary)}">
      ${icons}
    </div>
  `;
}

function scheduledTriggeredScheduleText(source) {
  const config = source?.config || {};
  if (source?.event_type === "file.changed") return `文件变化 · ${config.path || "观察工作区"}`;
  if (config.schedule_mode === "at") return `指定时间 · ${displayDateTime(config.scheduled_at) || config.scheduled_at || "待设置"}`;
  if (config.schedule_mode === "countdown") return `倒计时 · ${config.delay_seconds || 0} 秒`;
  const pattern = config.recurrence_kind === "interval"
    ? `每 ${config.interval_seconds || 0} 秒`
    : `${config.calendar_frequency || "daily"} ${config.calendar_time || ""} · ${config.timezone || "UTC"}`;
  return `${pattern}${Number(config.jitter_seconds) ? ` · 随机延迟最多 ${config.jitter_seconds} 秒` : ""}`;
}

// When a source next fires: its due time includes the random delay of a
// recurring schedule; a paused source has none (its stored plan no longer applies).
function scheduledTriggeredNextAt(source) {
  const runtime = source?.runtime || {};
  if (source?.paused || runtime.paused || runtime.exhausted || ["paused", "exhausted", "blocked", "error"].includes(runtime.state)) return "";
  return runtime.next_due_at || runtime.next_scheduled_at || "";
}

function scheduledTriggeredWaitingForPlan(source) {
  const runtime = source?.runtime || {};
  return source?.event_type === "time.reached" && runtime.state === "listening"
    && !source.paused && !runtime.paused && !runtime.exhausted && !scheduledTriggeredNextAt(source);
}

function scheduledTriggeredSourceState(source) {
  const runtime = source.runtime || {};
  if (source.paused || runtime.paused || runtime.state === "paused") return "已暂停";
  if (runtime.exhausted || runtime.state === "exhausted") return "次数耗尽";
  if (["blocked", "error"].includes(runtime.state)) {
    return `${runtime.state === "blocked" ? "自动触发受阻" : "运行错误"} · ${runtime.error || "请查看 Lineage 诊断"}`;
  }
  if (scheduledTriggeredWaitingForPlan(source)) return "等待调度生成计划";
  return "";
}

function scheduledTriggeredItemId(item) {
  return item.row_id || item.resident_id || `${item.lineage_id}/${item.effect_id}`;
}

function scheduledTriggeredScriptStatus(status) {
  return ({running: "运行中", succeeded: "成功", failed: "失败", timed_out: "超时", cancelled: "已取消", lost: "运行丢失"})[status] || status || "尚未运行";
}

// Each button asks for the one instruction it runs (row.allows).
const scheduledTriggeredAllows = (item, instruction) => item?.allows?.[instruction] === true;

function scheduledTriggeredSourceHtml(item, source) {
  const config = source.config || {};
  const runtime = source.runtime || {};
  const id = esc(source.component_id);
  const isTime = source.event_type === "time.reached";
  const editable = scheduledTriggeredAllows(item, "UpdateComponent") && isTime && ["at", "countdown", "recurring"].includes(config.schedule_mode);
  const editField = config.schedule_mode === "at"
    ? window.PortalI18n.html`<label>触发时间（ISO 8601）<input type="text" data-scheduled-triggered-schedule-field="scheduled_at" value="${esc(config.scheduled_at || "")}"></label>`
    : config.schedule_mode === "countdown"
      ? window.PortalI18n.html`<label>倒计时（秒）<input type="number" min="1" data-scheduled-triggered-schedule-field="delay_seconds" value="${esc(config.delay_seconds || 1)}"></label>`
      : config.schedule_mode === "recurring"
        ? config.recurrence_kind === "interval"
          ? window.PortalI18n.html`<label>间隔（秒）<input type="number" min="1" max="31536000" data-scheduled-triggered-schedule-field="interval_seconds" value="${esc(config.interval_seconds || 60)}"></label>`
          : window.PortalI18n.html`<label>日历时间<input type="time" step="1" data-scheduled-triggered-schedule-field="calendar_time" value="${esc(config.calendar_time || "09:00")}"></label>`
        : "";
  const jitterField = config.schedule_mode === "recurring" ? window.PortalI18n.html`<label>最大随机延迟（秒）<input type="number" min="0" data-scheduled-triggered-schedule-field="jitter_seconds" value="${esc(config.jitter_seconds || 0)}"></label>` : "";
  return window.PortalI18n.html`<div class="scheduled-triggered-schedule" data-scheduled-triggered-source="${id}">
    <div class="scheduled-triggered-unit">${esc(source.title || source.component_id)}</div>
    ${scheduledTriggeredSourceState(source) ? window.PortalI18n.html`<div class="scheduled-triggered-subtle" data-scheduled-triggered-source-state="${esc(runtime.state || "paused")}">${esc(scheduledTriggeredSourceState(source))}</div>` : ""}
    ${["blocked", "error"].includes(runtime.state) ? window.PortalI18n.html`<a class="scheduled-triggered-action" href="/lineage?lineage=${encodeURIComponent(item.lineage_id)}&program_item=${encodeURIComponent(source.component_id)}">打开事件与运行身份设置</a>` : ""}
    <div class="scheduled-triggered-subtle">${esc(scheduledTriggeredScheduleText(source))}</div>
    ${scheduledTriggeredNextAt(source) ? window.PortalI18n.html`<div class="scheduled-triggered-subtle">下一次 ${esc(displayDateTime(scheduledTriggeredNextAt(source)) || scheduledTriggeredNextAt(source))}</div>` : ""}
    <div class="scheduled-triggered-actions">
      ${editField}${jitterField}
      ${editable ? window.PortalI18n.html`<button type="button" class="scheduled-triggered-action" data-scheduled-triggered-action="update_schedule" data-scheduled-triggered-row-id="${esc(scheduledTriggeredItemId(item))}" data-scheduled-triggered-source-id="${id}">修改定时</button>` : ""}
      ${scheduledTriggeredAllows(item, "PauseComponent") ? window.PortalI18n.html`<button type="button" class="scheduled-triggered-action ${source.paused ? "start" : "stop"}" data-scheduled-triggered-action="${source.paused ? "resume_schedule" : "pause_schedule"}" data-scheduled-triggered-row-id="${esc(scheduledTriggeredItemId(item))}" data-scheduled-triggered-source-id="${id}">${source.paused ? window.PortalI18n.t("恢复调度") : window.PortalI18n.t("暂停调度")}</button>` : ""}
    </div>
  </div>`;
}

function setScheduledTriggeredTarget(agentId) {
  if (agentId) scheduledTriggeredExpanded.add(agentId);
}

function scheduledTriggeredLineageHtml(monitor) {
  if (!monitor?.loaded) {
    const failed = monitor?.status === "error";
    return window.PortalI18n.html`<div class="scheduled-triggered-workbench" data-scheduled-triggered-workbench><div class="${failed ? "agent-status-error" : "agent-status-muted"}">${failed ? window.PortalI18n.text`状态不可用：${esc(monitor?.error || window.PortalI18n.t("unknown error"))}` : window.PortalI18n.t("正在读取定时与自动触发…")}</div></div>`;
  }
  const items = Array.isArray(monitor.items) ? monitor.items : [];
  const errors = Array.isArray(monitor.lineage_errors) ? monitor.lineage_errors : [];
  const errorHtml = errors.map(item => window.PortalI18n.html`<div class="agent-status-error">${esc(item.title || item.lineage_id)} · ${esc(item.error || window.PortalI18n.t("Lineage 读取失败"))}</div>`).join("");
  const rowHtml = items.map((item, index) => {
    const residentId = esc(scheduledTriggeredItemId(item));
    const isEffect = item.kind === "effect";
    const runningScripts = isEffect ? [...new Map([item.last_run, ...(item.recent_runs || [])]
      .filter(run => run?.script_run_id && run.status === "running")
      .map(run => [run.script_run_id, run])).values()] : [];
    const lineageId = esc(item.lineage_id);
    const state = item.state || {};
    const health = ["ok", "warn", "paused", "error"].includes(item.health) ? item.health : "warn";
    const expanded = scheduledTriggeredExpanded.has(scheduledTriggeredItemId(item));
    const detailId = `scheduled-triggered-detail-${index}`;
    const sources = Array.isArray(item.sources) ? item.sources : [];
    const next = sources.map(scheduledTriggeredNextAt).filter(Boolean).sort()[0];
    const unavailable = sources.filter(source => ["blocked", "error"].includes(source.runtime?.state));
    const waitingForPlan = sources.some(scheduledTriggeredWaitingForPlan);
    const summary = unavailable.length ? unavailable.map(scheduledTriggeredSourceState).join("；")
      : !sources.length ? window.PortalI18n.t("无定时 Event")
        : sources.every(source => scheduledTriggeredSourceState(source)) ? sources.map(scheduledTriggeredSourceState).join("；")
          : next ? `${displayDateTime(next) || next}${waitingForPlan ? `；${window.PortalI18n.t("等待调度生成计划")}` : ""}`
            : waitingForPlan ? window.PortalI18n.t("等待调度生成计划")
              : sources.map(scheduledTriggeredScheduleText).join("；") || window.PortalI18n.t("等待事件");
    return window.PortalI18n.html`<div class="scheduled-triggered-row is-${esc(health)}" data-scheduled-triggered-row="${residentId}">
      <div class="scheduled-triggered-main">
        <span class="scheduled-triggered-status-dot ${esc(health)}" role="img" aria-label="${esc(health)}"></span>
        <div class="scheduled-triggered-identity"><div class="scheduled-triggered-title">${esc(item.title || item.effect_id || item.resident_id)}</div><div class="scheduled-triggered-kind">${esc(item.effect_id || item.resident_id)}</div>${isEffect ? window.PortalI18n.html`<div class="scheduled-triggered-subtle">${esc((item.script_paths || []).join("；"))}</div>` : ""}</div>
        <div class="scheduled-triggered-primary"><div class="scheduled-triggered-summary-label">下次触发</div><div class="scheduled-triggered-summary-value">${esc(summary)}</div></div>
        <div class="scheduled-triggered-recent"><div class="scheduled-triggered-summary-label">当前工作</div><div class="scheduled-triggered-summary-value">${esc(isEffect ? scheduledTriggeredScriptStatus(item.last_run?.status) : state.work_state || window.PortalI18n.t("未知"))}${isEffect && item.last_run?.started_at ? ` · ${esc(displayDateTime(item.last_run.started_at) || item.last_run.started_at)}` : ""}${Number(state.queued_count) ? window.PortalI18n.text` · 等待 ${esc(state.queued_count)}` : ""}</div></div>
        <div class="scheduled-triggered-row-tools">
          ${health === "error" ? window.PortalI18n.html`<span class="scheduled-triggered-badge error">异常</span>` : ""}
          <div class="scheduled-triggered-actions">
            ${scheduledTriggeredAllows(item, "TriggerEvent") ? (item.manual_sources?.length
              ? window.PortalI18n.html`<button type="button" class="scheduled-triggered-action run-once" data-scheduled-triggered-action="run_once" data-scheduled-triggered-row-id="${residentId}" data-scheduled-triggered-source-id="${esc(item.manual_sources[0].component_id)}">运行一次</button>`
              : window.PortalI18n.html`<button type="button" class="scheduled-triggered-action run-once" disabled title="Lineage 中没有连到该对象的手动事件源">运行一次</button>`) : ""}
            ${scheduledTriggeredAllows(item, "CancelScriptRun") ? runningScripts.map(run => window.PortalI18n.html`<button type="button" class="scheduled-triggered-action stop" data-scheduled-triggered-action="cancel_script" data-scheduled-triggered-row-id="${residentId}" data-scheduled-triggered-script-run-id="${esc(run.script_run_id)}">取消当前运行</button>`).join("") : ""}
            ${scheduledTriggeredAllows(item, "CancelInput") && item.active_message_id ? window.PortalI18n.html`<button type="button" class="scheduled-triggered-action stop" data-scheduled-triggered-action="cancel_run" data-scheduled-triggered-row-id="${residentId}">取消当前运行</button>` : ""}
            <a class="scheduled-triggered-action" href="/lineage?lineage=${lineageId}${isEffect ? "" : `&resident=${residentId}`}">打开 Lineage</a>
          </div>
          <button class="scheduled-triggered-toggle" type="button" data-scheduled-triggered-toggle="${residentId}" data-scheduled-triggered-title="${esc(item.title || item.effect_id || item.resident_id)}" aria-expanded="${expanded ? "true" : "false"}" aria-controls="${detailId}" aria-label="${expanded ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}${esc(item.title || item.effect_id || item.resident_id)}详情"></button>
        </div>
      </div>
      <div class="scheduled-triggered-detail" id="${detailId}"${expanded ? "" : " hidden"}>
        <section class="scheduled-triggered-detail-section"><div class="scheduled-triggered-detail-title">Event 来源</div>${sources.length ? sources.map(source => scheduledTriggeredSourceHtml(item, source)).join("") : window.PortalI18n.html`<div class="scheduled-triggered-subtle">此 Resident 当前没有连接的时间或文件 Event；运行状态仍来自所属 Lineage。</div>`}</section>
        <section class="scheduled-triggered-detail-section"><div class="scheduled-triggered-detail-title">任务说明</div>${(item.tasks || []).length ? item.tasks.map(task => window.PortalI18n.html`<pre class="scheduled-triggered-task">${esc(task.text)}</pre>`).join("") : window.PortalI18n.html`<div class="scheduled-triggered-subtle">连线上没有写任务说明。</div>`}</section>
        <section class="scheduled-triggered-detail-section"><div class="scheduled-triggered-detail-title">运行与设置</div>${isEffect ? window.PortalI18n.html`<div class="scheduled-triggered-subtle">最近脚本文件 ${esc(item.last_run?.path || window.PortalI18n.t("尚无记录"))}</div>` : window.PortalI18n.html`<div class="scheduled-triggered-subtle">Provider ${esc(item.resident?.provider || window.PortalI18n.t("未知"))} · 账号 ${esc(item.resident?.account || window.PortalI18n.t("默认"))}${item.settings?.model ? window.PortalI18n.text` · 模型 ${esc(item.settings.model)}` : ""}${item.settings?.reasoning_effort ? window.PortalI18n.text` · 推理 ${esc(item.settings.reasoning_effort)}` : ""}</div>
          ${scheduledTriggeredAllows(item, "UpdateResidentSettings") ? window.PortalI18n.html`<div class="scheduled-triggered-actions" data-scheduled-triggered-settings><label>模型<input type="text" data-scheduled-triggered-setting="model" value="${esc(item.settings?.model || "")}"></label><label>推理强度<input type="text" data-scheduled-triggered-setting="reasoning_effort" value="${esc(item.settings?.reasoning_effort || "")}"></label><button type="button" class="scheduled-triggered-action" data-scheduled-triggered-action="update_settings" data-scheduled-triggered-row-id="${residentId}">修改模型</button></div>` : ""}`}
          <div class="scheduled-triggered-subtle">最近运行 ${esc(isEffect ? scheduledTriggeredScriptStatus(item.last_run?.status) : item.last_run?.status || window.PortalI18n.t("无记录"))}${item.last_run?.started_at ? window.PortalI18n.text` · 开始 ${esc(displayDateTime(item.last_run.started_at) || item.last_run.started_at)}` : ""}${item.last_run?.finished_at ? window.PortalI18n.text` · 结束 ${esc(displayDateTime(item.last_run.finished_at) || item.last_run.finished_at)}` : ""}${Number(item.consecutive_failures) ? window.PortalI18n.text` · 连续失败 ${esc(item.consecutive_failures)} 次` : ""}</div>
          ${(item.recent_runs || []).slice(1).map(run => window.PortalI18n.html`<div class="scheduled-triggered-subtle">${esc(run.script_run_id || run.trigger_path_id)} · ${esc(isEffect ? scheduledTriggeredScriptStatus(run.status) : run.status)}${run.started_at ? ` · ${esc(displayDateTime(run.started_at) || run.started_at)}` : ""}</div>`).join("")}
          ${(item.diagnostics || []).map(entry => window.PortalI18n.html`<div class="${String(entry.severity || entry.level || "").toLowerCase() === "error" ? "agent-status-error" : "scheduled-triggered-subtle"}">${esc(entry.message || entry.code || "")}</div>`).join("")}
          ${(item.warnings || []).map(warning => window.PortalI18n.html`<div class="agent-status-error">${esc(warning)}</div>`).join("")}</section>
      </div>
    </div>`;
  });
  // Rows are grouped by Lineage; the member count shows whether the Resident
  // is one member of a team Lineage or the only Resident of its own Lineage.
  const groups = new Map();
  items.forEach((item, index) => {
    const key = item.lineage_id || item.resident_id;
    if (!groups.has(key)) groups.set(key, { item, html: [] });
    groups.get(key).html.push(rowHtml[index]);
  });
  const rows = [...groups.values()].map(({ item, html }) => {
    const members = Number(item.lineage_resident_count) || 0;
    return window.PortalI18n.html`<div class="scheduled-triggered-lineage" data-scheduled-triggered-lineage="${esc(item.lineage_id)}">
      <div class="scheduled-triggered-lineage-title">${esc(item.lineage_title || item.lineage_id)}${members ? window.PortalI18n.html`<span class="scheduled-triggered-subtle"> · Lineage 共 ${esc(members)} 个 Resident</span>` : ""}</div>
      ${html.join("")}
    </div>`;
  }).join("");
  return window.PortalI18n.html`<div class="scheduled-triggered-workbench" data-scheduled-triggered-workbench>
    <div class="scheduled-triggered-workbench-summary">${groups.size} 个 Lineage · ${items.filter(item => item.kind !== "effect").length} 个 Resident · ${items.filter(item => item.kind === "effect").length} 个脚本 Effect · ${items.reduce((count, item) => count + (item.sources?.length || 0), 0)} 个事件源</div>
    ${errorHtml}${rows ? window.PortalI18n.html`<div class="scheduled-triggered-grid">${rows}</div>` : window.PortalI18n.html`<div class="agent-status-muted">当前没有接到 Resident 或脚本 Effect 的时间或文件 Event。</div>`}
  </div>`;
}

function ensureAgentStatusComponentHosts(node) {
  const required = ["agent-accounts", "system-resources", "scheduled-triggered", "usage-attribution"];
  const hosts = Object.fromEntries(required.map(id => [id, node?.querySelector?.(`[data-home-component="${id}"]`)]));
  const missing = required.filter(id => !hosts[id]);
  if (missing.length) {
    throw new Error(`Agent Status Workbench is missing component hosts: ${missing.join(", ")}`);
  }
  return hosts;
}

function renderUsageAttribution() {
  const node = document.querySelector('[data-home-component="usage-attribution"]');
  if (!node) return;
  const data = usageData || {};
  const statusDetails = node.querySelector(".agent-status-details");
  if (statusDetails) agentStatusDetailsOpen = statusDetails.open;
  const componentHosts = {"usage-attribution": node};
  const backends = data.filters?.backends || [];
  if (data.filters && usageBackendFilter && !backends.includes(usageBackendFilter)) {
    usageBackendFilter = "";
    usageAccountFilter = "";
  }
  const accounts = (data.filters?.accounts || []).filter(item => !usageBackendFilter || item.provider === usageBackendFilter);
  const hasUnknown = (data.scopes || []).some(item => item.provider === usageBackendFilter && item.account_id === "");
  if (data.filters && usageAccountFilter && !(usageAccountFilter === "__unrecorded__" ? hasUnknown : accounts.some(item => item.account_id === usageAccountFilter))) usageAccountFilter = "";
  const accountOption = account => window.PortalI18n.html`<option value="${esc(usageBackendFilter ? account.account_id : JSON.stringify([account.provider, account.account_id]))}" ${usageAccountFilter === account.account_id ? "selected" : ""}>${esc(account.account_id)}</option>`;
  const tokenUsage = selectedTokenUsage(data);
  const windows = tokenUsage.windows || {};
  const ranking = tokenUsage[AGENT_STATUS_GROUPINGS[agentStatusGrouping].ranking];
  const residents = Array.isArray(ranking) ? ranking : [];
  const groupingLabel = AGENT_STATUS_GROUPINGS[agentStatusGrouping].label;
  const visibleSeries = chartSeries(tokenUsage);
  const legendSource = visibleSeries.flatMap(item => chartSegments(item));
  const legendMap = new Map();
  for (const segment of legendSource) {
    if (!legendMap.has(segment.key)) legendMap.set(segment.key, segment.label);
  }
  const colorMap = agentStatusColorMap([...legendMap.keys()]);
  const maxTokens = Math.max(1, ...visibleSeries.map(item => Number(item.tokens || 0)));
  const bars = visibleSeries.map(item => {
    const tokens = Number(item.tokens || 0);
    const height = Math.max(2, Math.round((tokens / maxTokens) * 78));
    const segments = chartSegments(item);
    const totalForSegments = Math.max(1, tokens);
    const segmentHtml = segments.map((segment, index) => {
      const value = Number(segment.tokens || 0);
      const pct = Math.max(0, Math.min(100, (value / totalForSegments) * 100));
      return window.PortalI18n.html`<span class="agent-status-bar-assigned agent-status-segment-color" style="height:${pct}%; --agent-status-segment-color:${agentStatusColor(segment.key, index, colorMap)}"></span>`;
    }).join("");
    const tooltip = [
      item.label || item.date,
      window.PortalI18n.text`Total ${formatTokens(tokens)}`,
      ...segments.map(segment => `${segment.label || segment.key} ${formatTokens(segment.tokens)}`),
    ].filter(Boolean).join(" · ");
    return window.PortalI18n.html`<div class="agent-status-bar" data-agent-chart-bar data-portal-tooltip="${esc(tooltip)}" role="img" tabindex="0" aria-label="${esc(tooltip)}" style="height:${height}px">${segmentHtml}</div>`;
  }).join("");
  const detailRange = AGENT_STATUS_USAGE_RANGES.includes(agentStatusChartRange)
    ? agentStatusChartRange
    : "today";
  const detailRangeLabel = detailRange[0].toUpperCase() + detailRange.slice(1);
  const topResidents = [...residents]
    .filter(item => Number(item.periods?.[detailRange] || 0) > 0)
    .sort((left, right) => (
      Number(right.periods?.[detailRange] || 0)
      - Number(left.periods?.[detailRange] || 0)
    ))
    .slice(0, RESIDENT_USAGE_RANK_LIMIT);
  const maxResident = Math.max(1, ...topResidents.map(item => Number(item.periods?.[detailRange] || 0)));
  const residentRows = topResidents.length ? topResidents.map(item => {
    const tokens = Number(item.periods?.[detailRange] || 0);
    const width = Math.max(2, Math.round((tokens / maxResident) * 100));
    return window.PortalI18n.html`
      <div class="agent-status-row">
        <span class="agent-status-row-name" title="${esc(item.title || item.project_id || item.lineage_id || item.resident_id)}">${esc(item.title || item.project_id || item.lineage_id || item.resident_id)}</span>
        <span class="agent-status-row-value">${formatTokens(tokens)}</span>
      </div>
      <div class="agent-status-progress" aria-hidden="true"><span style="width:${width}%"></span></div>
    `;
  }).join("") : window.PortalI18n.html`<div class="agent-status-muted">No ${groupingLabel.toLowerCase()} token data yet.</div>`;
  const detailWindow = windows[detailRange] || {};
  const detailTotal = usageWindowValue(detailWindow, "total_tokens");
  const detailSummary = window.PortalI18n.text`Total ${formatTokens(detailTotal)}`;
  // A partial summary names the accounts whose native usage could not be
  // read; everything else in it is current.
  const statusNote = tokenUsage?.status === "partial" && data.warning ? data.warning : usageChartNote(tokenUsage);
  const legend = [...legendMap.entries()].map(([key, label], index) => window.PortalI18n.html`
    <span class="agent-status-legend-item"><span class="agent-status-swatch agent-status-segment-color" style="--agent-status-segment-color:${agentStatusColor(key, index, colorMap)}"></span><span class="agent-status-legend-label">${esc(label)}</span></span>
  `).join("");
  componentHosts["usage-attribution"].innerHTML = window.PortalI18n.html`
      <div class="agent-status-section">
        <div class="agent-status-head">
          <div class="agent-status-usage-controls">
            <label class="agent-status-usage-filter">Backend
              <select data-usage-backend aria-label="AI Backend">
                <option value="">全部 Backend</option>
                ${backends.map(backend => window.PortalI18n.html`<option value="${esc(backend)}" ${usageBackendFilter === backend ? "selected" : ""}>${esc(backend)}</option>`).join("")}
              </select>
            </label>
            <label class="agent-status-usage-filter">账号
              <select data-usage-account aria-label="用量账号">
                <option value="">全部账号</option>
                ${usageBackendFilter ? accounts.map(accountOption).join("") : backends.map(backend => window.PortalI18n.html`<optgroup label="${esc(backend)}">${accounts.filter(account => account.provider === backend).map(accountOption).join("")}</optgroup>`).join("")}
                ${hasUnknown ? window.PortalI18n.html`<option value="__unrecorded__" ${usageAccountFilter === "__unrecorded__" ? "selected" : ""}>未记录账号</option>` : ""}
              </select>
            </label>
            <label class="agent-status-cache-toggle">
              <input type="checkbox" data-agent-status-cache-hits ${agentStatusIncludeCachedInput ? "checked" : ""} />
              <span>Include cache</span>
            </label>
            <div class="agent-status-tabs" role="tablist" aria-label="Usage time range">
              ${AGENT_STATUS_USAGE_RANGES.map(range => window.PortalI18n.html`<button class="agent-status-tab ${agentStatusChartRange === range ? "active" : ""}" type="button" data-agent-status-range="${range}">${range[0].toUpperCase()}${range.slice(1)}</button>`).join("")}
            </div>
            <div class="agent-status-tabs" role="group" aria-label="Usage grouping">
              ${Object.entries(AGENT_STATUS_GROUPINGS).map(([group, grouping]) => window.PortalI18n.html`<button class="agent-status-tab ${agentStatusGrouping === group ? "active" : ""}" type="button" data-agent-status-grouping="${group}" aria-pressed="${agentStatusGrouping === group}">${grouping.label}</button>`).join("")}
            </div>
          </div>
          <button class="agent-status-refresh" type="button" data-usage-refresh data-portal-icon="refresh" title="刷新用量"><span data-usage-refresh-label>刷新</span></button>
        </div>
        <div class="agent-status-usage-summary">${usageWindowSummary(tokenUsage)}</div>
        <div class="agent-status-chart" style="grid-template-columns:repeat(${Math.max(1, visibleSeries.length)}, minmax(10px, 1fr))" aria-label="Model token usage">${bars || ""}</div>
        <div class="agent-status-legend">${legend}</div>
        ${statusNote ? window.PortalI18n.html`<div class="agent-status-muted" role="status">${esc(statusNote)}</div>` : ""}
        ${tokenUsage.account_history_incomplete ? window.PortalI18n.html`<div class="agent-status-muted" data-usage-history-note>部分历史用量未记录账号，可在“未记录账号”中查看。</div>` : ""}
        ${tokenUsage.account_totals_omitted ? window.PortalI18n.html`<div class="agent-status-muted" data-usage-cache-note>账号自报的日用量含缓存且无法拆分，此视图不计入；勾选 Include cache 后计入。</div>` : ""}
      </div>
      ${usageError || data.refresh_error ? window.PortalI18n.html`<div class="agent-status-error" role="status">Usage refresh failed: ${esc(usageError || data.refresh_error)}</div>` : ""}
      ${data.manual_refresh_skipped ? window.PortalI18n.html`<div class="agent-status-muted">Please retry in ${Math.ceil(data.manual_refresh_retry_after_seconds / 60)} minutes.</div>` : ""}
      <details class="agent-status-details"${agentStatusDetailsOpen ? " open" : ""}>
        <summary><span>Details</span><span data-portal-icon="chevron-down" data-portal-icon-class="statusDisclosureIcon" class="statusDisclosureIcon" aria-hidden="true"></span></summary>
        <div class="agent-status-section">
          <div class="agent-status-section-title">${groupingLabel} attributed usage · ${esc(detailRangeLabel)}</div>
          <div class="agent-status-rows">${residentRows}</div>
        </div>
        <div class="agent-status-muted">${esc(detailSummary)}</div>
        <div class="agent-status-muted">Updated ${esc(formatDateTime(data?.updated_at) || window.PortalI18n.t("just now"))}</div>
      </details>
  `;
  for (const button of node.querySelectorAll("[data-agent-status-range]")) {
    button.addEventListener("click", () => {
      agentStatusChartRange = button.getAttribute("data-agent-status-range") || "week";
      renderUsageAttribution();
    });
  }
  for (const button of node.querySelectorAll("[data-agent-status-grouping]")) {
    button.addEventListener("click", () => {
      agentStatusGrouping = button.getAttribute("data-agent-status-grouping");
      window.PortalState.storage.writeText(
        `${AGENT_STATUS_GROUPING_KEY}:${portalUsername}`,
        agentStatusGrouping,
      );
      renderUsageAttribution();
    });
  }
  const cacheHitsInput = node.querySelector("[data-agent-status-cache-hits]");
  if (cacheHitsInput) {
    cacheHitsInput.addEventListener("change", () => {
      agentStatusIncludeCachedInput = Boolean(cacheHitsInput.checked);
      window.PortalState.storage.writeText(
        `${AGENT_STATUS_CACHE_HITS_KEY}:${portalUsername}`,
        agentStatusIncludeCachedInput ? "1" : "0",
      );
      renderUsageAttribution();
    });
  }
  const nextDetails = node.querySelector(".agent-status-details");
  if (nextDetails) {
    nextDetails.addEventListener("toggle", () => {
      agentStatusDetailsOpen = nextDetails.open;
    });
  }

  const saveFilters = () => {
    window.PortalState.storage.writeText(`${USAGE_FILTER_KEY}:${portalUsername}`, JSON.stringify({backend: usageBackendFilter, account: usageAccountFilter}));
    renderUsageAttribution();
  };
  node.querySelector("[data-usage-backend]").addEventListener("change", event => {
    usageBackendFilter = event.target.value;
    usageAccountFilter = "";
    saveFilters();
  });
  node.querySelector("[data-usage-account]").addEventListener("change", event => {
    const value = event.target.value;
    if (!usageBackendFilter && value) {
      [usageBackendFilter, usageAccountFilter] = JSON.parse(value);
    } else {
      usageAccountFilter = value;
    }
    saveFilters();
  });
  node.querySelector("[data-usage-refresh]").addEventListener("click", () => loadUsageAttribution(true));
  updateUsageRefreshButton();
  window.PortalButtonTooltips?.enhance?.(node);
}

function focusedResidentIcon(root) {
  const focused = document.activeElement;
  return root?.contains(focused) ? focused.getAttribute("data-agent-status-resident") : "";
}

function restoreResidentIconFocus(root, residentId) {
  if (!residentId) return;
  root?.querySelector(`[data-agent-status-resident="${CSS.escape(residentId)}"]`)
    ?.focus({ preventScroll: true });
}

function renderScheduledTriggeredStatus(node) {
  const signature = JSON.stringify(scheduledTriggeredStatusData);
  if (node.dataset.scheduledTriggeredSignature === signature) return;
  const drafts = [...node.querySelectorAll("input")].filter(input =>
    !input.disabled && (input.value !== input.defaultValue || input === document.activeElement)
  ).map(input => ({
    row: input.closest("[data-scheduled-triggered-row]").dataset.scheduledTriggeredRow,
    source: input.closest("[data-scheduled-triggered-source]")?.dataset.scheduledTriggeredSource,
    field: input.dataset.scheduledTriggeredScheduleField,
    setting: input.dataset.scheduledTriggeredSetting,
    value: input.value, focused: input === document.activeElement,
    start: input.selectionStart, end: input.selectionEnd,
  }));
  node.innerHTML = scheduledTriggeredLineageHtml(scheduledTriggeredStatusData);
  node.dataset.scheduledTriggeredSignature = signature;
  for (const button of node.querySelectorAll("[data-scheduled-triggered-toggle]")) {
    button.addEventListener("click", () => {
      const agentId = button.getAttribute("data-scheduled-triggered-toggle") || "";
      const detailId = button.getAttribute("aria-controls") || "";
      const title = button.getAttribute("data-scheduled-triggered-title") || "Agent";
      const detail = detailId ? document.getElementById(detailId) : null;
      const expanded = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", expanded ? "true" : "false");
      button.setAttribute("aria-label", window.PortalI18n.text`${expanded ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开")}${title}详情`);
      button.setAttribute("title", expanded ? window.PortalI18n.t("收起详情") : window.PortalI18n.t("展开详情"));
      if (detail) detail.hidden = !expanded;
      if (agentId) {
        if (expanded) scheduledTriggeredExpanded.add(agentId);
        else scheduledTriggeredExpanded.delete(agentId);
      }
    });
  }
  for (const button of node.querySelectorAll("[data-scheduled-triggered-action]")) {
    button.addEventListener("click", () => controlScheduledTriggered(button));
  }
  for (const draft of drafts) {
    const row = node.querySelector(`[data-scheduled-triggered-row="${CSS.escape(draft.row)}"]`);
    const container = draft.source ? row?.querySelector(`[data-scheduled-triggered-source="${CSS.escape(draft.source)}"]`) : row;
    const input = container?.querySelector(draft.field
      ? `[data-scheduled-triggered-schedule-field="${CSS.escape(draft.field)}"]`
      : `[data-scheduled-triggered-setting="${CSS.escape(draft.setting)}"]`);
    if (!input) continue;
    input.value = draft.value;
    if (draft.focused) {
      input.focus({preventScroll: true});
      if (draft.start !== null) input.setSelectionRange(draft.start, draft.end);
    }
  }
}

function renderAgentStatus(data) {
  const summary = {...(data || {})};
  delete summary.active_agents;
  delete summary.token_usage;
  agentStatusData = {
    ...summary,
    disk: diskStatusData,
    gpu: gpuStatusData,
    scheduled_triggered: scheduledTriggeredStatusData,
    active_agents: activeAgentStatus || {},
  };
  data = agentStatusData;
  const node = document.querySelector('[data-home-component="agent-status"]');
  if (!node) return;
  const componentHosts = ensureAgentStatusComponentHosts(node);
  const currentRunboard = componentHosts["agent-accounts"].querySelector("[data-agent-status-runboard]");
  const focusedResident = focusedResidentIcon(currentRunboard);
  const active = data?.active_agents || {};
  const gpu = data?.gpu || {};
  const disk = data?.disk || {};
  const accounts = executionAccounts(data);
  const refreshRetry = Number(data?.refresh_retry_after_seconds || 0);
  const refreshNote = refreshRetry > 0 && !data?.refreshing
    ? window.PortalI18n.html`<div class="agent-status-muted">余额和账号资料刷新有间隔限制，请在 ${Math.ceil(refreshRetry / 60)} 分钟后重试。</div>`
    : "";
  const statusRefreshing = data?.refreshing === true;
  const runboardHtml = agentStatusRunboard(active);
  if (agentStatusSummaryLoaded) componentHosts["agent-accounts"].innerHTML = window.PortalI18n.html`
      <div class="agent-status-head">
        <div data-agent-status-runboard>${runboardHtml}</div>
        ${accountManagementActions(accounts.length > 0)}
        <button
          class="agent-status-refresh${statusRefreshing ? " is-refreshing" : ""}"
          type="button"
          data-agent-status-refresh
          data-portal-icon="refresh"
          aria-busy="${statusRefreshing ? "true" : "false"}"
          ${statusRefreshing ? "disabled" : ""}
        ><span data-agent-status-refresh-label>${statusRefreshing ? window.PortalI18n.t("刷新中…") : window.PortalI18n.t("刷新")}</span></button>
      </div>
      <div class="agent-status-limit-list">

        ${accounts.length
          ? accounts.map(accountLimitRow).join("")
          : accountEmptyState(data)}
      </div>
      ${refreshNote}
      ${data?.accounts_refresh_error || data?.accounts_error ? window.PortalI18n.html`<div class="agent-status-error" role="alert" data-account-refresh-error>Agent status unavailable: ${esc(data.accounts_refresh_error || data.accounts_error)}</div>` : ""}
  `;
  componentHosts["system-resources"].innerHTML = window.PortalI18n.html`
      <div class="agent-status-resource-line">
        ${gpuResourceHtml(gpu)}
        ${diskResourceHtml(disk)}
      </div>
      ${gpuDetailsHtml(gpu)}
      ${diskDetailsHtml(disk)}
  `;
  renderScheduledTriggeredStatus(componentHosts["scheduled-triggered"]);
  const nextRunboard = componentHosts["agent-accounts"].querySelector("[data-agent-status-runboard]");
  if (currentRunboard && nextRunboard && agentStatusRunboardHtml === runboardHtml) {
    nextRunboard.replaceWith(currentRunboard);
  }
  restoreResidentIconFocus(componentHosts["agent-accounts"], focusedResident);
  agentStatusRunboardHtml = runboardHtml;
  const refreshButton = node.querySelector("[data-agent-status-refresh]");
  if (refreshButton) {
    refreshButton.addEventListener("click", async () => {
      if (homeController.isComponentVisible("agent-accounts")) await loadAgentStatus(true);
      const reads = [];
      if (homeController.isComponentVisible("system-resources")) reads.push(loadGpuStatus(), loadDiskStatus());
      if (homeController.isComponentVisible("scheduled-triggered")) reads.push(loadScheduledTriggeredStatus());
      await Promise.allSettled(reads);
    });
  }
  syncAccountDetailsButtons();
  window.PortalButtonTooltips?.enhance?.(node);
  refreshAccountQuotaWindows();
  }

function activeAgentStatusNeedsRetry() {
  return activeAgentStatus?.ready === false
    || activeAgentStatus?.refreshing === true
    || Boolean(activeAgentStatus?.error)
    || activeAgentStatus?.status === "loading";
}

function activeAgentStatusNextDelay(_data, error) {
  if (error || _data?.error) {
    const delay = Math.min(
      ACTIVE_AGENT_STATUS_RETRY_BASE_MS * (2 ** activeAgentStatusRetryAttempt),
      ACTIVE_AGENT_STATUS_RETRY_MAX_MS,
    );
    activeAgentStatusRetryAttempt += 1;
    return delay;
  }
  activeAgentStatusRetryAttempt = 0;
  return activeAgentStatusNeedsRetry() ? ACTIVE_AGENT_STATUS_LOADING_RETRY_MS : null;
}

function renderActiveAgentStatus(active) {
  activeAgentStatus = active || {};
  bindActiveResidentStates(activeAgentStatus);
  if (!agentStatusData) return;
  const nextData = {
    ...agentStatusData,
    active_agents: activeAgentStatus,
  };
  const node = document.querySelector('[data-home-component="agent-status"]');
  const runboard = node?.querySelector?.("[data-agent-status-runboard]");
  if (!runboard) {
    renderAgentStatus(nextData);
    return;
  }
  agentStatusData = nextData;
  const nextHtml = agentStatusRunboard(nextData.active_agents);
  if (nextHtml === agentStatusRunboardHtml) return;
  const focusedResident = focusedResidentIcon(runboard);
  runboard.innerHTML = nextHtml;
  restoreResidentIconFocus(runboard, focusedResident);
  agentStatusRunboardHtml = nextHtml;
}

function bindActiveResidentStates(active) {
  const groups = [active, ...(Array.isArray(active.account_groups) ? active.account_groups : [])];
  const residentIds = [...new Set(groups.flatMap(group => [
    ...(Array.isArray(group.running_runs) ? group.running_runs : []),
    ...(Array.isArray(group.queued_runs) ? group.queued_runs : []),
  ]).filter(run => run?.type === "resident" && run.resident_id).map(run => run.resident_id))].sort();
  const signature = JSON.stringify([portalUsername, residentIds]);
  if (signature === activeResidentStateSignature) return;
  activeResidentStateSignature = signature;
  const previousCollection = activeResidentStateCollection;
  activeResidentStateCollection = null;
  if (!residentIds.length) {
    previousCollection?.destroy();
    return;
  }
  activeResidentStateCollection = window.PortalResidentProtocol.acquireStates(residentIds, () => {
    if (activeResidentStateFrame) return;
    activeResidentStateFrame = window.requestAnimationFrame(() => {
      activeResidentStateFrame = 0;
      if (activeAgentStatus) renderActiveAgentStatus(activeAgentStatus);
    });
  });
  previousCollection?.destroy();
}

async function controlScheduledTriggered(button) {
  const rowId = button?.getAttribute?.("data-scheduled-triggered-row-id") || "";
  const action = button?.getAttribute?.("data-scheduled-triggered-action") || "";
  const supported = ["pause_schedule", "resume_schedule", "update_schedule", "run_once", "cancel_run", "cancel_script", "update_settings"];
  if (!rowId || !supported.includes(action)) return;
  const item = (agentStatusData?.scheduled_triggered?.items || []).find(entry => scheduledTriggeredItemId(entry) === rowId);
  if (!item) return;
  const sourceId = button.getAttribute("data-scheduled-triggered-source-id") || "";
  const source = (item.sources || []).find(entry => entry.component_id === sourceId);
  // Each page action is exactly one HATG ISA instruction (decision_481dc4e6292c “操作”).
  let instruction = "";
  let argumentsPayload = {};
  if (action === "run_once") {
    instruction = "TriggerEvent";
    argumentsPayload = {lineage_id: item.lineage_id, event_source_id: sourceId};
  } else if (action === "cancel_script") {
    const scriptRunId = button.getAttribute("data-scheduled-triggered-script-run-id");
    const run = [item.last_run, ...(item.recent_runs || [])].find(entry => entry?.script_run_id === scriptRunId && entry.status === "running");
    if (item.kind !== "effect" || !run) return;
    instruction = "CancelScriptRun";
    argumentsPayload = {lineage_id: item.lineage_id, script_run_id: scriptRunId};
  } else if (action === "cancel_run") {
    instruction = "CancelInput";
    argumentsPayload = {resident_id: item.resident_id, message_id: item.active_message_id};
  } else if (action === "pause_schedule" || action === "resume_schedule") {
    if (!source) return;
    instruction = "PauseComponent";
    argumentsPayload = {lineage_id: item.lineage_id, component_id: sourceId, paused: action === "pause_schedule"};
  } else if (action === "update_settings") {
    const changes = {};
    for (const input of button.closest("[data-scheduled-triggered-settings]")?.querySelectorAll("[data-scheduled-triggered-setting]") || []) {
      const value = input.value.trim();
      if (value) changes[input.getAttribute("data-scheduled-triggered-setting")] = value;
    }
    if (!Object.keys(changes).length) {
      window.PortalUI.toast(window.PortalI18n.t("请填写模型或推理强度"), "error");
      return;
    }
    instruction = "UpdateResidentSettings";
    argumentsPayload = {resident_id: item.resident_id, changes};
  } else if (action === "update_schedule" && source) {
    const config = {...source.config};
    const container = button.closest("[data-scheduled-triggered-source]");
    for (const input of container?.querySelectorAll("[data-scheduled-triggered-schedule-field]") || []) {
      const field = input.getAttribute("data-scheduled-triggered-schedule-field");
      if (field === "calendar_time" || field === "scheduled_at") {
        if (!input.value) {
          window.PortalUI.toast(window.PortalI18n.t("请填写日历时间"), "error");
          return;
        }
        config[field] = input.value;
      } else {
        const value = Number(input.value);
        if (!Number.isInteger(value) || value < (field === "interval_seconds" ? 1 : 0)) {
          window.PortalUI.toast(window.PortalI18n.t("请填写有效的时间范围"), "error");
          return;
        }
        config[field] = value;
      }
    }
    instruction = "UpdateComponent";
    argumentsPayload = {lineage_id: item.lineage_id, component_id: sourceId, changes: {config}};
  } else return;
  if (!scheduledTriggeredAllows(item, instruction)) return;
  const originalText = button.textContent;
  const row = button.closest("[data-scheduled-triggered-row]");
  const rowControls = [...(row?.querySelectorAll("button, select, input") || [])];
  const controlStates = rowControls.map(control => [control, control.disabled]);
  rowControls.forEach(control => { control.disabled = true; });
  button.textContent = window.PortalI18n.t("处理中");
  let committed = false;
  try {
    await window.PortalInstructionClient.execute(instruction, argumentsPayload, {
      idempotencyKey: `scheduled-triggered:${window.PortalUIPrimitives.randomUUID()}`,
    });
    committed = true;
    button.textContent = window.PortalI18n.t("已提交，正在刷新状态");
    await loadScheduledTriggeredStatus(agentStatusData, undefined, true, {afterMutation: true});
    if (!scheduledTriggeredStatusData?.loaded && scheduledTriggeredStatusData?.error) {
      throw new Error(scheduledTriggeredStatusData.error);
    }
    window.PortalUI.toast(window.PortalI18n.t(
      scheduledTriggeredStatusData?.refreshing || !scheduledTriggeredStatusData?.loaded
        ? "操作已提交，正在刷新状态" : "操作已完成",
    ), "success");
  } catch (err) {
    if (committed) {
      window.PortalUI.toast(window.PortalI18n.text`操作已提交，但状态刷新失败：${err?.message || String(err)}`, "warning");
    } else {
      const actionLabel = action === "run_once" ? window.PortalI18n.t("运行一次") : window.PortalI18n.t("定时控制");
      window.PortalUI.toast(window.PortalUI.errorMessage(actionLabel, err), "error");
    }
  } finally {
    controlStates.forEach(([control, disabled]) => { control.disabled = disabled; });
    button.textContent = originalText;
  }
}

// Follow active work quickly, then keep a low-frequency read while visible so
// an idle or empty panel also discovers newly configured schedules.
const SCHEDULED_TRIGGERED_FOLLOWUP_MS = 5_000;
const SCHEDULED_TRIGGERED_IDLE_REFRESH_MS = 30_000;
const SCHEDULED_TRIGGERED_FOLLOWUP_LIMIT = 120;
let scheduledTriggeredFollowupTimer = 0;
let scheduledTriggeredFollowupCount = 0;
let scheduledTriggeredLoadInFlight = null;

function scheduledTriggeredWorkInFlight(monitor) {
  return (Array.isArray(monitor?.items) ? monitor.items : []).some(item => item?.active_message_id
    || ["queued", "running", "stopping"].includes(String(item?.state?.work_state || ""))
    || String(item?.last_run?.status || "") === "running");
}

function scheduleScheduledTriggeredFollowup(monitor) {
  window.clearTimeout(scheduledTriggeredFollowupTimer);
  scheduledTriggeredFollowupTimer = 0;
  if (document.hidden || !homeController.isComponentVisible("scheduled-triggered")) {
    scheduledTriggeredFollowupCount = 0;
    return;
  }
  if (monitor?.refreshing || (monitor?.status === "loading" && !monitor.loaded)) {
    scheduledTriggeredFollowupTimer = window.setTimeout(() => {
      scheduledTriggeredFollowupTimer = 0;
      if (document.hidden || !homeController.isComponentVisible("scheduled-triggered")) return;
      void loadScheduledTriggeredStatus(agentStatusData, undefined, false, { quiet: true });
    }, SCHEDULED_TRIGGERED_FOLLOWUP_MS);
    return;
  }
  if (!monitor?.loaded) return;
  const active = scheduledTriggeredWorkInFlight(monitor);
  if (!active) scheduledTriggeredFollowupCount = 0;
  const fast = active && scheduledTriggeredFollowupCount < SCHEDULED_TRIGGERED_FOLLOWUP_LIMIT;
  if (fast) scheduledTriggeredFollowupCount += 1;
  scheduledTriggeredFollowupTimer = window.setTimeout(() => {
    scheduledTriggeredFollowupTimer = 0;
    if (document.hidden || !homeController.isComponentVisible("scheduled-triggered")) return;
    void loadScheduledTriggeredStatus(agentStatusData, undefined, true, { quiet: true });
  }, fast ? SCHEDULED_TRIGGERED_FOLLOWUP_MS : SCHEDULED_TRIGGERED_IDLE_REFRESH_MS);
}

async function loadScheduledTriggeredStatus(data, signal, force = false, { quiet = false, afterMutation = false } = {}) {
  if (scheduledTriggeredLoadInFlight) {
    if (!force) return scheduledTriggeredLoadInFlight;
    await scheduledTriggeredLoadInFlight;
    if (signal?.aborted) return;
    return loadScheduledTriggeredStatus(undefined, signal, true, {quiet, afterMutation});
  }
  const previous = scheduledTriggeredStatusData;
  if (!quiet && !previous?.loaded) {
    scheduledTriggeredStatusData = {status: "loading", loaded: false, items: []};
    renderAgentStatus(agentStatusData);
  }
  const request = (async () => {
    try {
      const query = afterMutation ? "?refresh=1&fresh=1" : force ? "?refresh=1" : "";
      const result = await homeApi.get(`/api/agent-status/scheduled-triggered${query}`, {signal});
      if (!signal?.aborted) scheduledTriggeredStatusData = {...result, status: result?.status || "ready", loaded: result?.loaded !== false};
      else scheduledTriggeredStatusData = previous;
    } catch (err) {
      scheduledTriggeredStatusData = signal?.aborted || (quiet && previous?.loaded) ? previous : {
        status: "error", loaded: false, items: [], error: err?.message || String(err),
      };
    }
    if (!signal?.aborted) {
      renderAgentStatus(agentStatusData);
      scheduleScheduledTriggeredFollowup(scheduledTriggeredStatusData);
    }
  })();
  scheduledTriggeredLoadInFlight = request;
  try { await request; }
  finally { if (scheduledTriggeredLoadInFlight === request) scheduledTriggeredLoadInFlight = null; }
}

function agentStatusNeedsFollowup(data = agentStatusData) {
  const accounts = executionAccounts(data);
  return !agentStatusSummaryLoaded || !data
    || data?.refreshing === true
    || data?.status === "loading"
    || accounts.some(account => account?.status === "loading");
}

function updateUsageRefreshButton() {
  const button = document.querySelector("[data-usage-refresh]");
  if (!button) return;
  const busy = usageRequestPending || (!usageError && usageData?.refreshing === true);
  button.disabled = busy;
  button.classList.toggle("is-refreshing", busy);
  button.setAttribute("aria-busy", String(busy));
  button.querySelector("[data-usage-refresh-label]").textContent = busy ? window.PortalI18n.t("刷新中…") : window.PortalI18n.t("刷新");
}

function hasUsageSnapshot(data) {
  return Object.values(data?.windows || {}).some(item => Number.isFinite(item?.total_tokens));
}

function storedUsageSnapshot(actor, snapshot) {
  // Usage scopes exceed localStorage quotas; keep one IndexedDB snapshot per user.
  return new Promise(resolve => {
    let request;
    try { request = window.indexedDB.open(window.PortalCompatibility.identifier("usage_database"), 1); }
    catch { resolve(null); return; }
    request.onupgradeneeded = () => request.result.createObjectStore("snapshots");
    request.onerror = request.onblocked = () => resolve(null);
    request.onsuccess = () => {
      const database = request.result;
      try {
        const transaction = database.transaction("snapshots", snapshot ? "readwrite" : "readonly");
        const store = transaction.objectStore("snapshots");
        const operation = snapshot ? store.put(snapshot, actor) : store.get(actor);
        transaction.oncomplete = () => { database.close(); resolve(operation.result || null); };
        transaction.onerror = transaction.onabort = () => { database.close(); resolve(null); };
      } catch { database.close(); resolve(null); }
    };
  });
}

function acceptUsageSnapshot(data) {
  const version = data?.snapshot_version;
  if (!Number.isSafeInteger(version) || version < 0) throw new Error("Invalid usage snapshot version");
  if (!hasUsageSnapshot(data) && hasUsageSnapshot(usageData)) {
    usageData = {...usageData, refreshing: data.refreshing === true};
    usageError = data.refresh_error || (data.status === "error" ? "Usage unavailable." : "");
    renderUsageAttribution();
    updateUsageRefreshButton();
    return;
  }
  if (usageData && version < usageData.snapshot_version) return;
  const previousVersion = usageData?.snapshot_version;
  const hadError = Boolean(usageError);
  usageData = data;
  usageError = "";
  const signature = JSON.stringify(data);
  if (hadError || signature !== usageRenderedSignature) {
    usageRenderedSignature = signature;
    renderUsageAttribution();
  }
  updateUsageRefreshButton();
  if (hasUsageSnapshot(data) && version !== previousVersion) {
    void storedUsageSnapshot(portalUsername, {...data, refreshing: false, refresh_error: ""});
  }
}

function ensureUsageRefresh() {
  if (usageRefresh) return usageRefresh;
  usageRefresh = window.PortalRefreshCoordinator.create({
    key: () => `home-token-usage:${portalUsername || "anonymous"}`,
    interval: () => !usageError && (usageData?.refreshing || usageData?.status === "loading") ? 3000 : 300000,
    hiddenInterval: 300000,
    pauseWhenHidden: false,
    initialDelay: 300000,
    fetch: ({force = false, signal} = {}) => force
      ? homeApi.request("/api/agent-status/usage/refresh", {method: "POST", signal})
      : homeApi.get("/api/agent-status/usage", {signal}),
    apply: acceptUsageSnapshot,
    onError: error => {
      usageError = error?.message || String(error);
      renderUsageAttribution();
    },
  });
  return usageRefresh;
}

async function loadUsageAttribution(force = false) {
  const coordinator = ensureUsageRefresh();
  coordinator.start();
  usageRequestPending = true;
  updateUsageRefreshButton();
  try {
    await coordinator.refresh({force});
  } catch {
  } finally {
    usageRequestPending = false;
    updateUsageRefreshButton();
  }
}

function ensureAgentStatusRefresh() {
  if (agentStatusRefresh) return agentStatusRefresh;
  const fetchAgentStatus = ({ force = false } = {}) => {
    const path = force ? "/api/agent-status/refresh" : "/api/agent-status";
    return force
      ? homeApi.request(path, { method: "POST" })
      : homeApi.get(path);
  };
  agentStatusRefresh = window.PortalRefreshCoordinator.create({
    key: () => `home-agent-status:${portalUsername || "anonymous"}`,
    // Quota percentages come from the local scheduler's 15-second samples;
    // slow provider balances keep their independent backend refresh policy.
    interval: () => agentStatusNeedsFollowup() ? 3000 : 30000,
    hiddenInterval: 300000,
    pauseWhenHidden: false,
    initialDelay: 30000,
    fetch: fetchAgentStatus,
    apply: data => {
      agentStatusSummaryLoaded = true;
      renderAgentStatus(data);
    },
      onError: err => {
        agentStatusSummaryLoaded = true;
        renderAgentStatus({...agentStatusData, accounts: staleExecutionAccounts(executionAccounts(agentStatusData)),
          refreshing: false, accounts_refresh_error: err?.message || String(err)});
      },
  });
  return agentStatusRefresh;
}

function ensureActiveAgentStatusRefresh() {
  if (activeAgentStatusRefresh) return activeAgentStatusRefresh;
  activeAgentStatusRefresh = window.PortalRefreshCoordinator.create({
    key: () => `home-active-agents:${portalUsername || "anonymous"}`,
    interval: 300000,
    pauseWhenHidden: true,
    fetch: () => window.PortalResidentService.activeAgents(),
    apply: renderActiveAgentStatus,
    nextDelay: activeAgentStatusNextDelay,
    subscribe: window.PortalRefreshCoordinator.portalSubscription({
      domains: ["projection", "resident"],
      refreshOnAttach: false,
      debounceMs: 1500,
      matches: detail => {
        if (detail?.snapshot_required) return true;
        if (Array.isArray(detail?.resident_events) && detail.resident_events.length > 0) return true;
        const changes = detail?.projection_changes?.["active-agents"] || {};
        return changes && typeof changes === "object" && Object.keys(changes).length > 0;
      },
    }),
  });
  return activeAgentStatusRefresh;
}

async function loadAgentStatus(force = false) {
  const node = document.querySelector('[data-home-component="agent-status"]');
  const button = node?.querySelector?.("[data-agent-status-refresh]");
  if (button) {
    button.disabled = true;
    button.classList.add("is-refreshing");
    button.setAttribute("aria-busy", "true");
    const label = button.querySelector("[data-agent-status-refresh-label]");
    if (label) label.textContent = window.PortalI18n.t("刷新中…");
  }
  const loadingTarget = node?.querySelector?.('[data-home-component="agent-accounts"]');
  if (loadingTarget && !loadingTarget.textContent.trim()) {
    loadingTarget.innerHTML = window.PortalI18n.html`<div class="agent-status-muted">Loading agent status…</div>`;
  }
  const coordinator = ensureAgentStatusRefresh();
  const activeCoordinator = ensureActiveAgentStatusRefresh();
  coordinator.start();
  activeCoordinator.start();
  await Promise.allSettled([
    coordinator.refresh({ force }),
    activeCoordinator.refresh({ force }),
  ]);
}

async function loadGpuStatus({force = true, signal} = {}) {
  const previous = gpuStatusData || {};
  gpuStatusData = {...previous, status: "loading"};
  renderAgentStatus(agentStatusData);
  try {
    gpuStatusData = await window.PortalInstructionClient.execute("GetDeviceStatus", force ? {kind: "gpu", refresh: true} : {kind: "gpu"}, {signal});
    if (!signal?.aborted) renderAgentStatus(agentStatusData);
  } catch (err) {
    if (signal?.aborted) { gpuStatusData = previous; return; }
    const message = err?.message || String(err);
    const hasPrevious = Array.isArray(previous.servers) && previous.servers.length > 0;
    gpuStatusData = hasPrevious
      ? {...previous, stale: true, refresh_error: message}
      : {...previous, status: "error", error: message, servers: []};
    renderAgentStatus(agentStatusData);
  }
}

function ensureDiskStatusRefresh() {
  if (diskStatusRefresh) return diskStatusRefresh;
  const delay = () => diskStatusData?.refreshing ? 1000 : 300000;
  diskStatusRefresh = window.PortalRefreshCoordinator.create({
    key: () => `home-disk-status:${portalUsername || "anonymous"}`,
    interval: delay,
    initialDelay: delay,
    pauseWhenHidden: true,
    fetch: ({ force, signal }) => window.PortalInstructionClient.execute("GetDeviceStatus", force ? { kind: "disk", refresh: true } : { kind: "disk" }, { signal }),
    apply: disk => {
      diskStatusData = disk;
      renderAgentStatus(agentStatusData);
    },
    onError: err => {
      const previous = diskStatusData || {};
      diskStatusData = {...previous, stale: true, refresh_error: err?.message || String(err)};
      renderAgentStatus(agentStatusData);
    },
  });
  return diskStatusRefresh;
}

async function loadDiskStatus() {
  diskStatusData = {...(diskStatusData || {}), refreshing: true};
  renderAgentStatus(agentStatusData);
  await ensureDiskStatusRefresh().refresh({ force: true });
}
function listen(target, type, listener, signal) {
  target?.addEventListener?.(type, listener, { signal });
}

let portalPulseInitialLoadPromise = null;

function loadPortalPulseOnce() {
  if (!portalPulseInitialLoadPromise) portalPulseInitialLoadPromise = loadPortalPulse();
  return portalPulseInitialLoadPromise;
}

function connectHomeShell({ context }) {
  // The shell owns only the current user; every panel reads its own data.
  context.currentUserReady = window.PortalUI.loadCurrentUser()
    .then(({ username, data }) => {
      portalUsername = username || "anonymous";
      canSetScheduler = data?.access?.permission_system === "open" || data?.access?.allows?.UpdateProviderAccount === true;
      canRegisterAccount = data?.access?.permission_system === "open" || data?.access?.allows?.RegisterProviderAccount === true;
      return { username: portalUsername, data };
    });
}

function connectDailyNews() {
  loadDailyNews();
}

function connectAgentAccess({ context, signal }) {
  context.currentUserReady.then(() => {
    if (!signal.aborted) return loadAgentAccess();
  }).catch(() => {
    if (signal.aborted) return;
    const node = document.querySelector("[data-agent-access]");
    if (node) node.innerHTML = window.PortalI18n.html`<div class="agent-access-error">当前用户读取失败，无法加载个人 Agent 入口。</div>`;
  });
}

function connectPulse({ context, signal }) {
  context.currentUserReady.catch(() => {}).then(() => {
    if (!signal.aborted) loadPortalPulseOnce();
  });
}

const agentStatusQueueClears = new Set();

function bindAgentStatusResidentMenu(signal) {
  const menu = document.createElement("div");
  menu.className = "portalDetailSurface agent-status-resident-menu";
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", window.PortalI18n.t("Resident 排队消息操作"));
  menu.hidden = true;
  const title = document.createElement("strong");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "portalButton";
  button.setAttribute("role", "menuitem");
  const note = document.createElement("small");
  note.textContent = window.PortalI18n.t("不打断当前运行，不改变 Open / Closed 状态");
  menu.append(title, button, note);
  document.body.appendChild(menu);
  let selected = null;
  let trigger = null;
  const popover = window.PortalOverlay.bindFloatingPopover({
    panel: menu,
    placement: "bottom",
    align: "start",
    closeOnScroll: true,
    onClose: event => {
      if (event?.key === "Escape" && trigger?.isConnected) trigger.focus({ preventScroll: true });
    },
  });
  const syncButton = () => {
    const busy = selected && agentStatusQueueClears.has(selected.resident_id);
    button.disabled = !selected?.lineage_id || busy;
    button.textContent = busy ? window.PortalI18n.t("正在清除…") : window.PortalI18n.t("清除所有排队消息");
    button.setAttribute("aria-busy", String(Boolean(busy)));
  };
  listen(document, "portal:resident-queue-clear-settled", syncButton, signal);
  const open = event => {
    const link = event.target instanceof Element
      ? event.target.closest("[data-agent-status-resident]") : null;
    if (!link) return;
    event.preventDefault();
    selected = {
      resident_id: link.getAttribute("data-agent-status-resident"),
      lineage_id: link.getAttribute("data-agent-status-lineage"),
      title: link.getAttribute("aria-label") || "Resident",
    };
    trigger = link;
    title.textContent = selected.title;
    syncButton();
    const bounds = link.getBoundingClientRect();
    popover.open({
      x: event.clientX || bounds.left,
      y: event.clientY || bounds.bottom,
    });
    button.focus({ preventScroll: true });
  };
  listen(document, "contextmenu", open, signal);
  listen(document, "keydown", event => {
    if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) open(event);
    if (event.key === "Tab" && popover.isOpen()) popover.close(event);
  }, signal);
  listen(button, "click", async () => {
    const target = selected;
    if (!target?.lineage_id || agentStatusQueueClears.has(target.resident_id)) return;
    if (!window.confirm(window.PortalI18n.text`清除「${target.title}」当前所有排队消息？\n正在执行的消息不受影响，接单状态保持不变。`)) return;
    agentStatusQueueClears.add(target.resident_id);
    syncButton();
    try {
      const result = await window.PortalResidentProtocol.clearMailbox({
        resident_id: target.resident_id,
        lineage_id: target.lineage_id,
      });
      if (signal.aborted) return;
      if (selected?.resident_id === target.resident_id) popover.close();
      window.PortalChatWorkspace.noticeQueueCleared(result);
      try {
        await ensureActiveAgentStatusRefresh().refresh({ force: true });
      } catch (_error) {
        if (!signal.aborted) window.PortalUI.toast(window.PortalI18n.t("清理已完成，但状态刷新失败，请手动刷新。"), "error");
      }
    } catch (error) {
      if (!signal.aborted) window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("清除排队消息"), error), "error");
    } finally {
      agentStatusQueueClears.delete(target.resident_id);
      document.dispatchEvent(new Event("portal:resident-queue-clear-settled"));
    }
  }, signal);
  return () => {
    popover.destroy();
    menu.remove();
  };
}

function connectAgentStatus({ context, signal }) {
  const destroyResidentMenu = bindAgentStatusResidentMenu(signal);
  context.currentUserReady.catch(() => {}).then(() => {
    if (!signal.aborted) loadAgentStatus(false);
  });
  return () => {
    destroyResidentMenu();
    accountDetails?.popover.destroy();
    accountDetails?.panel.remove();
    accountDetails = null;
    agentStatusRefresh?.destroy?.();
    activeAgentStatusRefresh?.destroy?.();
    agentStatusRefresh = null;
    agentStatusSummaryLoaded = false;
    activeAgentStatusRefresh = null;
  };
}

function connectSystemResources({ context, signal }) {
  context.currentUserReady.catch(() => {}).then(() => {
    if (signal.aborted) return;
    const disk = ensureDiskStatusRefresh();
    disk.start();
    Promise.allSettled([loadGpuStatus({force: false, signal}), disk.refresh()]);
  });
  return () => {
    diskStatusRefresh?.destroy();
    diskStatusRefresh = null;
    diskStatusData = null;
  };
}

function connectScheduledTriggered({ root, context, signal }) {
  let ready = false;
  const suspend = () => {
    window.clearTimeout(scheduledTriggeredFollowupTimer);
    scheduledTriggeredFollowupTimer = 0;
    scheduledTriggeredFollowupCount = 0;
  };
  const resume = () => {
    suspend();
    if (ready && !signal.aborted && !document.hidden && homeController.isComponentVisible("scheduled-triggered")) {
      void loadScheduledTriggeredStatus(undefined, signal, true, { quiet: true });
    }
  };
  listen(root, "portal:home-panel-lifecycle", event => {
    if (event.detail?.phase === "show") resume();
    else if (["hide", "disconnect", "destroy"].includes(event.detail?.phase)) suspend();
  }, signal);
  listen(document, "visibilitychange", () => document.hidden ? suspend() : resume(), signal);
  context.currentUserReady.catch(() => {}).then(() => {
    ready = true;
    if (!signal.aborted) loadScheduledTriggeredStatus(undefined, signal, true);
  });
  return suspend;
}

function connectUsageAttribution({context, signal}) {
  context.currentUserReady.catch(() => {}).then(() => {
    if (!signal.aborted) {
      try {
        const filters = JSON.parse(window.PortalState.storage.readText(`${USAGE_FILTER_KEY}:${portalUsername}`, "{}"));
        usageBackendFilter = typeof filters.backend === "string" ? filters.backend : "";
        usageAccountFilter = typeof filters.account === "string" ? filters.account : "";
      } catch { usageBackendFilter = ""; usageAccountFilter = ""; }
      const storedGrouping = window.PortalState.storage.readText(
        `${AGENT_STATUS_GROUPING_KEY}:${portalUsername}`, "lineage",
      );
      agentStatusGrouping = Object.hasOwn(AGENT_STATUS_GROUPINGS, storedGrouping) ? storedGrouping : "lineage";
      agentStatusIncludeCachedInput = window.PortalState.storage.readText(
        `${AGENT_STATUS_CACHE_HITS_KEY}:${portalUsername}`, "",
      ) === "1";
      renderUsageAttribution();
      const actor = portalUsername;
      void storedUsageSnapshot(actor).then(snapshot => {
        if (signal.aborted || portalUsername !== actor || hasUsageSnapshot(usageData)
          || !hasUsageSnapshot(snapshot) || !Number.isSafeInteger(snapshot.snapshot_version)) return;
        usageData = {...snapshot, refreshing: usageRequestPending || usageData?.refreshing === true};
        renderUsageAttribution();
        updateUsageRefreshButton();
      });
      loadUsageAttribution();
    }
  });
  return () => {
    usageRefresh?.destroy?.();
    usageRefresh = null;
    usageData = null;
    usageError = "";
    usageRequestPending = false;
    usageRenderedSignature = "";
  };
}

// Installed features add panels to the Home groups (web.home_panel, PortalHomeComponents.registerPanel);
// their order places them among the group's own panels.
function groupPanels(group, panels) {
  return [...panels, ...window.PortalHomeComponents.panels(group)].sort((left, right) => left.order - right.order);
}
const homeGroupPanels = {
  "get-me-aligned": groupPanels("get-me-aligned", [
    { id: "daily-news", order: 20, title: window.PortalI18n.t("Daily News"), icon: "file-text", source: '[data-home-component="daily-news"]', connect: connectDailyNews },
    { id: "agent-access", order: 30, title: window.PortalI18n.t("Agent Access"), icon: "users", source: '[data-home-component="agent-access"]', connect: connectAgentAccess },
  ]),
  "my-pulse-publish": groupPanels("my-pulse-publish", [
    { id: "knowledge-pulse", order: 10, title: window.PortalI18n.t("Knowledge Pulse"), icon: "pulse", source: '[data-home-component="knowledge-pulse"]', connect: connectPulse },
    { id: "personal-pulse", order: 20, title: window.PortalI18n.t("Personal Pulse"), icon: "user", source: '[data-home-component="personal-pulse"]', connect: connectPulse },
  ]),
  "agent-status": groupPanels("agent-status", [
    { id: "agent-accounts", order: 10, title: "AI Activity & Accounts", icon: "pulse", source: '[data-home-component="agent-accounts"]', connect: connectAgentStatus },
    { id: "system-resources", order: 20, title: window.PortalI18n.t("GPU / Disk Resources"), icon: "server", source: '[data-home-component="system-resources"]', connect: connectSystemResources },
    { id: "scheduled-triggered", order: 30, title: window.PortalI18n.t("定时与自动触发"), icon: "bot", source: '[data-home-component="scheduled-triggered"]', connect: connectScheduledTriggered },
    { id: "usage-attribution", order: 40, title: window.PortalI18n.t("Usage Attribution"), icon: "list", source: '[data-home-component="usage-attribution"]', connect: connectUsageAttribution },
  ]),
};
// The nested workbench that shows each group's panels.
const HOME_GROUP_WORKBENCHES = { "get-me-aligned": "home-get-me-aligned", "my-pulse-publish": "home-my-pulse", "agent-status": "home-agent-status" };
const groupComponents = group => homeGroupPanels[group].map(({ id, source, connect }) => ({ id, root: source, connect }));
const groupWorkbenchPanels = group => homeGroupPanels[group].map(({ id, title, icon, source }) => ({ id, title, icon, source }));
const groupPanelIds = group => homeGroupPanels[group].map(panel => panel.id);

const homeComponentTree = Object.freeze({
  id: "home-shell",
  root: "#homeRoot",
  loadPolicy: "eager",
  connect: connectHomeShell,
  children: [
    {
      id: "get-me-aligned",
      root: "#homeOverviewCard",
      children: groupComponents("get-me-aligned"),
    },
    {
      id: "my-pulse-publish",
      root: '[data-home-component="my-pulse-publish"]',
      children: groupComponents("my-pulse-publish"),
    },
    {
      id: "agent-status",
      root: '[data-home-component="agent-status"]',
      children: groupComponents("agent-status"),
    },
  ],
});

const { split: homeSplit, stack: homeStack } = window.PortalWorkbench.model;
const getMeAlignedLayout = homeSplit("vertical", [
  homeSplit("horizontal", [homeStack(["notice-board"]), homeStack(["daily-news"])], [1, 1]),
  homeStack(["agent-access"]),
], [3, 2]);
const myPulseLayout = homeSplit("vertical", [
  homeStack(["knowledge-pulse"]),
  homeStack(["personal-pulse"]),
], [1, 1]);
const agentStatusLayout = homeSplit("vertical", [
  homeSplit("horizontal", [homeStack(["agent-accounts"]), homeStack(["system-resources"])], [3, 2]),
  homeSplit("horizontal", [homeStack(["scheduled-triggered"]), homeStack(["usage-attribution"])], [1, 2]),
], [1, 1]);
const agentStatusAccountsUsageLayout = homeSplit("vertical", [
  homeStack(["agent-accounts"]),
  homeStack(["usage-attribution"]),
], [1, 2]);
const homeMainLayout = homeSplit("vertical", [
  homeSplit("horizontal", [homeStack(["get-me-aligned"]), homeStack(["my-pulse-publish"])], [2, 1]),
  homeStack(["agent-status"]),
], [5, 6]);
const homeVerticalLayout = homeSplit("vertical", [
  homeStack(["get-me-aligned"]),
  homeStack(["my-pulse-publish"]),
  homeStack(["agent-status"]),
], [1, 1, 2]);

const homeWorkbench = window.PortalHomeComponents.createWorkbenchComposition({
  workbenches: [
    {
      id: "home-get-me-aligned",
      root: "#getMeAlignedWorkbench",
      staging: "#getMeAlignedStaging",
      label: window.PortalI18n.t("Get Me Aligned"),
      toolbarPlacement: "tabs",
      showSingleTabHeader: true,
      panels: groupWorkbenchPanels("get-me-aligned"),
      defaultLayout: getMeAlignedLayout,
      presets: [
        { id: "overview", name: window.PortalI18n.t("概览"), layout: getMeAlignedLayout },
        { id: "tabs", name: window.PortalI18n.t("标签组"), layout: homeStack(groupPanelIds("get-me-aligned")) },
      ],
      defaultPreset: "overview",
    },
    {
      id: "home-my-pulse",
      root: "#myPulseWorkbench",
      staging: "#myPulseStaging",
      label: window.PortalI18n.t("My Pulse / Publish"),
      toolbarPlacement: "tabs",
      showSingleTabHeader: true,
      panels: groupWorkbenchPanels("my-pulse-publish"),
      defaultLayout: myPulseLayout,
      presets: [
        { id: "stacked", name: window.PortalI18n.t("上下排列"), layout: myPulseLayout },
        { id: "tabs", name: window.PortalI18n.t("标签组"), layout: homeStack(groupPanelIds("my-pulse-publish")) },
      ],
      defaultPreset: "stacked",
    },
    {
      id: "home-agent-status",
      contentSchemaVersion: 2,
      migrateState: window.PortalHomeComponents.migrateAgentStatusState,
      root: "#agentStatusWorkbench",
      staging: "#agentStatusStaging",
      label: window.PortalI18n.t("Agent Status"),
      toolbarPlacement: "tabs",
      showSingleTabHeader: true,
      panels: groupWorkbenchPanels("agent-status"),
      defaultLayout: agentStatusLayout,
      presets: [
        { id: "overview", name: window.PortalI18n.t("运行概览"), layout: agentStatusLayout },
        { id: "tabs", name: window.PortalI18n.t("标签组"), layout: homeStack(groupPanelIds("agent-status")) },
      ],
      defaultPreset: "overview",
    },
    {
      id: "home",
      root: "#homeWorkbenchRoot",
      staging: "#homeWorkbenchStaging",
      label: window.PortalI18n.t("Home Workbench"),
      panels: [
        { id: "get-me-aligned", title: window.PortalI18n.t("Get Me Aligned"), icon: "semantic", source: '#homeOverviewCard', minHeight: 320 },
        { id: "my-pulse-publish", title: window.PortalI18n.t("My Pulse / Publish"), icon: "send", source: '[data-home-component="my-pulse-publish"]', minHeight: 240 },
        { id: "agent-status", title: window.PortalI18n.t("Agent Status"), icon: "pulse", source: '[data-home-component="agent-status"]', minHeight: 420 },
      ],
      defaultLayout: homeMainLayout,
      presets: [
        {
          id: "daily-overview",
          name: window.PortalI18n.t("每日概览"),
          layout: homeMainLayout,
          workbenches: {
            "home-get-me-aligned": {
              id: "daily-overview",
              name: window.PortalI18n.t("每日概览"),
              layout: homeSplit("horizontal", [homeStack(["notice-board"]), homeStack(["daily-news"])], [1, 1]),
              hidden: ["agent-access"],
            },
            "home-my-pulse": {
              id: "daily-overview",
              name: window.PortalI18n.t("每日概览"),
              layout: myPulseLayout,
            },
            "home-agent-status": {
              id: "daily-overview",
              name: window.PortalI18n.t("每日概览"),
              layout: homeSplit("vertical", [homeStack(["agent-accounts"]), homeStack(["scheduled-triggered"])], [1, 1]),
              hidden: ["system-resources", "usage-attribution"],
            },
          },
        },
        { id: "overview", name: window.PortalI18n.t("概览"), layout: homeMainLayout },
        { id: "vertical", name: window.PortalI18n.t("从上到下"), layout: homeVerticalLayout },
        {
          id: "accounts-usage",
          name: window.PortalI18n.t("账号与用量"),
          layout: homeStack(["agent-status"]),
          hidden: ["get-me-aligned", "my-pulse-publish"],
          workbenches: {
            "home-agent-status": {
              id: "accounts-usage",
              name: window.PortalI18n.t("账号与用量"),
              layout: agentStatusAccountsUsageLayout,
              hidden: ["system-resources", "scheduled-triggered"],
            },
          },
        },
        { id: "tabs", name: window.PortalI18n.t("标签组"), layout: homeStack(["get-me-aligned", "my-pulse-publish", "agent-status"]) },
      ],
      defaultPreset: "accounts-usage",
      mobilePrimaryPanelId: "get-me-aligned",
    },
  ],
});

const homeController = window.PortalHomeComponents.create({
  id: "home",
  root: "#homeRoot",
  context: {},
  ready: context => context.currentUserReady.catch(() => {}),
  tree: homeComponentTree,
});

window.PortalHome = Object.freeze({
  mount() {
    if (homeController.snapshot().mounted) return;
    homeWorkbench.mount();
    // An explicit deep link is an open request, even if the saved layout hides
    // its host. Ordinary visits never expand panels as a side effect of loading.
    for (const panel of window.PortalHomeComponents.panels().filter(item => item.requested)) {
      window.PortalWorkbench.get("home").focusPanel(panel.group);
      window.PortalWorkbench.get(HOME_GROUP_WORKBENCHES[panel.group]).focusPanel(panel.id);
    }
    if (homeRoute.scheduledTriggeredId) {
      window.PortalWorkbench.get("home").focusPanel("agent-status");
      window.PortalWorkbench.get("home-agent-status").focusPanel("scheduled-triggered");
    }
    homeController.mount();
  },
  destroy() {
    homeController.destroy();
    homeWorkbench.destroy();
  },
  snapshot: () => Object.freeze({
    ...homeController.snapshot(),
    ...homeWorkbench.snapshot(),
  }),
  tree: homeComponentTree,
});

setScheduledTriggeredTarget(homeRoute.scheduledTriggeredId);
window.PortalHome.mount();
window.addEventListener("pagehide", () => window.PortalHome.destroy());
window.addEventListener("pageshow", event => {
  if (event.persisted) window.PortalHome.mount();
});
