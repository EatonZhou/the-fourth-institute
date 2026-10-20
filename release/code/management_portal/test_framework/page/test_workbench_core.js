(() => {
  "use strict";
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const aspectLabels = Object.freeze({ functional: window.PortalI18n.t("功能"), ui: "UI", behavior: window.PortalI18n.t("行为"), performance: window.PortalI18n.t("性能"), "architecture-conformance": window.PortalI18n.t("架构约束") });
  const riskLabels = Object.freeze({ "no-external-side-effects": window.PortalI18n.t("无外部副作用"), unspecified: window.PortalI18n.t("未声明"), isolated: window.PortalI18n.t("隔离环境"), "live-account": window.PortalI18n.t("真实账号"), "read-only": window.PortalI18n.t("只读") });
  const terminalStatuses = new Set(["passed", "failed", "cancelled", "error", "unsupported"]);
  const statusLabels = Object.freeze({ queued: window.PortalI18n.t("排队中"), running: window.PortalI18n.t("运行中"), passed: window.PortalI18n.t("通过"), failed: window.PortalI18n.t("失败"), cancelled: window.PortalI18n.t("已中止"), error: window.PortalI18n.t("错误"), unsupported: window.PortalI18n.t("不支持") });
  const failureClassLabels = Object.freeze({ product: window.PortalI18n.t("产品缺陷"), test: window.PortalI18n.t("测试问题"), environment: window.PortalI18n.t("环境问题"), precondition: window.PortalI18n.t("前提未满足"), "concurrent-change": window.PortalI18n.t("并行修改"), unknown: window.PortalI18n.t("未分类") });
  let lastNotice = { key: "", at: 0 };
  const notify = (message, tone = "info") => {
    const text = String(message || "");
    const key = `${tone}:${text}`;
    const now = Date.now();
    if (key === lastNotice.key && now - lastNotice.at < 10_000) return;
    lastNotice = { key, at: now };
    window.PortalUI?.toast?.(text, tone);
  };

  async function api(path, options = {}) {
    const { timeoutMs = 30_000, ...requestOptions } = options;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json", ...(requestOptions.headers || {}) }, ...requestOptions, signal: controller.signal });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(payload.error || `HTTP ${response.status}`);
        error.status = response.status;
        throw error;
      }
      return payload;
    } catch (error) {
      if (controller.signal.aborted) throw new Error(`Portal DEV 请求超时：${path}`);
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  class TestWorkspaceStore {
    constructor() { this.catalog = null; this.catalogStatus = "idle"; this.catalogError = ""; this.cases = []; this.selectedId = ""; this.selectedImplementation = new Map(); this.selection = new Set(); this.runs = []; this.listeners = new Set(); this.caseDetailRequests = new Map(); }
    subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
    emit(event, detail = {}) { this.listeners.forEach(listener => listener(event, detail)); }
    case(caseId = this.selectedId) { return this.cases.find(item => item.id === caseId) || null; }
    moduleLabel(item) {
      if (item?.module_path?.module_label) return item.module_path.module_label;
      for (const group of this.catalog?.filters?.modules || []) {
        const module = (group.modules || []).find(candidate => candidate.id === item?.module);
        if (module) return module.label || module.id;
      }
      return item?.module || "—";
    }
    canOperate() { return this.catalog?.access?.can_operate !== false; }
    async ensureCaseDetail(caseId) {
      const current = this.case(caseId);
      if (!current || current.detail_loaded !== false) return current;
      if (this.caseDetailRequests.has(caseId)) return this.caseDetailRequests.get(caseId);
      const request = api(`/test/api/catalog/case?case_id=${encodeURIComponent(caseId)}`)
        .then(payload => {
          const detail = payload?.case;
          if (!detail || detail.id !== caseId) throw new Error("测试用例详情响应无效。");
          const index = this.cases.findIndex(item => item.id === caseId);
          const loaded = { ...detail, detail_loaded: true };
          if (index >= 0) this.cases.splice(index, 1, loaded);
          this.emit("case-detail", { caseId });
          return loaded;
        })
        .finally(() => this.caseDetailRequests.delete(caseId));
      this.caseDetailRequests.set(caseId, request);
      return request;
    }
    async load() {
      this.catalogStatus = "loading";
      this.catalogError = "";
      this.emit("catalog");
      try {
        this.catalog = await api("/test/api/catalog?projection=workspace");
        this.cases = this.catalog.cases || [];
        const durable = (this.catalog.recent_runs || []).map(run => ({ ...run, caseIds: [...new Set((run.plan?.selected || []).map(item => item.case_id).filter(Boolean))] }));
        const current = new Map(this.runs.map(run => [run.run_id, run]));
        this.runs = durable.map(run => ({ ...(current.get(run.run_id) || {}), ...run }));
        const requested = new URLSearchParams(location.search).get("case") || this.selectedId;
        this.selectedId = this.case(requested) ? requested : (this.cases[0]?.id || "");
        this.catalogStatus = "loaded";
        this.emit("catalog");
        return this.catalog;
      } catch (error) {
        this.catalogStatus = "error";
        this.catalogError = error?.message || "测试目录加载失败。";
        this.emit("catalog");
        throw error;
      }
    }
    removeRuns(runIds) {
      const deleting = new Set(runIds);
      this.runs = this.runs.filter(run => !deleting.has(run.run_id));
      this.emit("runs-deleted", { runIds: [...deleting] });
    }
  }

  const store = new TestWorkspaceStore();
  const panelDefinitions = new Map();
  let workbench = null;
  function registerPanel(definition) {
    if (!definition?.type || typeof definition.create !== "function") throw new TypeError("测试面板必须提供 type 和 create。" );
    if (panelDefinitions.has(definition.type)) throw new Error(`测试面板重复登记：${definition.type}`);
    const registered = Object.freeze({ ...definition });
    window.PortalWorkbench.registerPanelPackage({
      type: definition.type,
      version: 1,
      component: definition.component,
      presentation: { title: definition.title || definition.type, icon: definition.icon || "panel" },
      sizing: { minWidth: definition.minWidth || 180, minHeight: definition.minHeight || 140 },
      instances: definition.singleton ? "single" : "multiple",
      capabilities: definition.capabilities || [],
      behavior: {
        canHide: definition.canHide !== false,
        canCollapse: definition.canCollapse !== false,
        canMove: definition.canMove !== false,
        closable: definition.closable !== false,
        locked: definition.locked === true,
      },
      context: { channels: definition.contextChannels || ["*"] },
      persistence: { schemaVersion: 1 },
      create: definition.create,
    });
    panelDefinitions.set(definition.type, registered);
  }
  function panelController(root, render, methods = {}) { root.classList.add("testPanel"); const unsubscribe = store.subscribe(() => render()); render(); return { element: root, destroy: unsubscribe, ...methods }; }
  function showPanel(panelId) {
    if (panelId === "run-console" && store.selectedId) {
      const item = store.case();
      const implementationId = store.selectedImplementation.get(store.selectedId) || item?.implementations?.[0]?.id || "";
      workbench?.invokePanel("run-console", "ensureDraft", { caseIds: [store.selectedId], implementation: implementationId ? { implementation_ids: [implementationId] } : {} });
    }
    // A visible Panel already has a placement. Calling showPanel again removes and
    // reinserts it, which needlessly rebuilds layout and resets sibling UI state.
    if (!workbench?.isPanelVisible(panelId)) workbench?.showPanel(panelId);
    workbench?.focusPanel(panelId);
  }
  function openCase(caseId, options = {}) {
    const selected = store.case(caseId);
    if (caseId && !selected) return;
    store.selectedId = selected?.id || "";
    const url = new URL(location.href);
    if (store.selectedId) url.searchParams.set("case", store.selectedId);
    else url.searchParams.delete("case");
    history.replaceState({}, "", url);
    workbench?.invokePanel("test-case", "setCase", store.selectedId);
    if (store.selectedId && options.reveal !== false) showPanel("test-case");
    store.emit("selection", { caseId: store.selectedId });
    if (store.selectedId) store.ensureCaseDetail(store.selectedId).catch(error => notify(`测试用例详情不可用：${error.message}`, "error"));
  }
  function prepareRun(caseIds, implementation = {}) { workbench?.invokePanel("run-console", "setDraft", { caseIds, implementation }); showPanel("run-console"); }
  function badges(item) {
    return [window.PortalI18n.html`<span class="testBadge is-important">${escapeHtml(item.importance)}</span>`, window.PortalI18n.html`<span class="testBadge">${escapeHtml(store.moduleLabel(item))}</span>`, ...(item.aspects || []).map(aspect => window.PortalI18n.html`<span class="testBadge">${escapeHtml(aspectLabels[aspect] || aspect)}</span>`), ...(item.is_design_point ? [window.PortalI18n.markup('<span class="testBadge is-important">设计点</span>')] : []), ...(item.live ? [window.PortalI18n.markup('<span class="testBadge is-live">Live</span>')] : [])].join("");
  }
  function hasRunner(item, runnerId) {
    return item?.runners?.includes(runnerId)
      || item?.implementations?.some(implementation => implementation.runner === runnerId)
      || false;
  }
  function caseLabel(item) {
    const key = String(item?.key || "");
    const separator = key.lastIndexOf("::");
    return separator >= 0 && key.slice(separator + 2).trim()
      ? key.slice(separator + 2).trim()
      : String(item?.title || item?.description || key || window.PortalI18n.t("未命名测试用例"));
  }
  function caseSource(item) {
    const key = String(item?.key || "");
    const separator = key.lastIndexOf("::");
    return separator >= 0 ? key.slice(0, separator) : key;
  }
  function panelHead(title, note = "", actions = "") {
    return window.PortalI18n.html`<header class="testPanelHead paneHead portalWorkspaceHead"><div class="chatHeadText"><h2 class="paneTitle">${escapeHtml(title)}</h2><div class="chatSubline"><div class="paneNote">${escapeHtml(note)}</div></div></div>${actions ? window.PortalI18n.html`<div class="actionGroup">${actions}</div>` : ""}</header>`;
  }
  function statusLabel(status) { return statusLabels[status] || status || ""; }
  function failureClassLabel(value) { return failureClassLabels[value] || value || ""; }
  function riskLabel(risk) { return riskLabels[risk] || risk || riskLabels.unspecified; }
  function riskTone(risk) { return risk === "no-external-side-effects" || risk === "isolated" || risk === "read-only" ? "is-safe" : risk === "unspecified" || !risk ? "is-undeclared" : "is-risk"; }
  function section(name, title, summary, content, open = false, status = "") { return window.PortalI18n.html`<details class="testSection" data-section="${escapeHtml(name)}" ${open ? "open" : ""}><summary><span><span class="testSectionTitle">${escapeHtml(title)}</span><span class="testSectionSummary">${escapeHtml(summary)}</span></span>${status ? window.PortalI18n.html`<span class="testStatus" data-status="${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>` : ""}</summary><div class="testSectionBody">${content}</div></details>`; }

  window.PortalTest = Object.freeze({ api, aspectLabels, badges, caseLabel, caseSource, escapeHtml, failureClassLabel, hasRunner, notify, openCase, panelController, panelDefinitions, panelHead, prepareRun, registerPanel, riskLabel, riskTone, section, setWorkbench(value) { workbench = value; }, showPanel, statusLabel, store, terminalStatuses, workbench: () => workbench });
})();
