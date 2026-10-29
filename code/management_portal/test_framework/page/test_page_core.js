(() => {
  "use strict";

  // Shared pieces of the Test page (decision_f2a6a5dd4646): reads from the
  // Test Framework API, the overview cache, the page addresses, the run draft
  // and the small renderers every view uses.  The views live in
  // test_page_*.js and the shell that routes between them in test_page.js.
  const i18n = () => window.PortalI18n;
  const t = value => window.PortalI18n.t(value);
  const escapeHtml = value => window.PortalUIPrimitives.escapeHtml(String(value ?? ""));

  const aspectLabels = Object.freeze({ functional: t("功能"), ui: "UI", behavior: t("行为"), performance: t("性能"), "architecture-conformance": t("架构约束") });
  const riskLabels = Object.freeze({ "no-external-side-effects": t("无外部副作用"), unspecified: t("未声明"), isolated: t("隔离环境"), "live-account": t("真实账号"), "read-only": t("只读"), "external-side-effects": t("有外部副作用") });
  const statusLabels = Object.freeze({ queued: t("排队中"), running: t("运行中"), cancelling: t("正在中止"), passed: t("通过"), failed: t("失败"), cancelled: t("已中止"), error: t("错误"), unsupported: t("不支持") });
  const failureClassLabels = Object.freeze({ product: t("产品缺陷"), test: t("测试问题"), environment: t("环境问题"), precondition: t("前提未满足"), "concurrent-change": t("并行修改"), unknown: t("未分类") });
  const runnerLabels = Object.freeze({ pytest: "pytest", tau: "TAU", none: t("缺实现") });
  const terminalStatuses = new Set(["passed", "failed", "cancelled", "error", "unsupported"]);
  const problemStatuses = new Set(["failed", "error", "cancelled", "unsupported"]);
  const activeStatuses = new Set(["queued", "running", "cancelling"]);

  let lastNotice = { key: "", at: 0 };
  function notify(message, tone = "info") {
    const text = String(message || "");
    const key = `${tone}:${text}`;
    if (key === lastNotice.key && Date.now() - lastNotice.at < 10_000) return;
    lastNotice = { key, at: Date.now() };
    window.PortalUI?.toast?.(text, tone);
  }

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
      if (controller.signal.aborted) throw new Error(i18n().text`请求超时：${path}`);
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  // Addresses.  Every Test page is /development?view=test plus what it shows:
  // page=cases|runs|plan|tau, case=<ID> or run=<ID>; the catalog's filters are
  // part of its address as well.
  const FILTER_KEYS = Object.freeze(["q", "module", "group", "importance", "design", "aspect", "live", "feature", "runner"]);
  const MULTIPLE = new Set(["module", "aspect"]);
  function href(params = {}) {
    const query = new URLSearchParams({ view: "test" });
    for (const [key, value] of Object.entries(params)) {
      for (const item of Array.isArray(value) ? value : [value]) {
        if (item === undefined || item === null || item === "" || item === false) continue;
        query.append(key, item === true ? "1" : String(item));
      }
    }
    return `/development?${query}`;
  }
  const caseHref = caseId => href({ case: caseId });
  const runHref = runId => href({ run: runId });
  const pageHref = (page, extra = {}) => href({ page, ...extra });
  const catalogHref = (filters = {}) => href({ page: "cases", ...filters });

  function addressed(search = window.location.search) {
    const params = new URLSearchParams(search);
    const value = name => String(params.get(name) || "").trim();
    const filters = {};
    for (const key of FILTER_KEYS) {
      const values = params.getAll(key).map(item => String(item).trim()).filter(Boolean);
      if (!values.length) continue;
      filters[key] = MULTIPLE.has(key) ? [...new Set(values)] : values[values.length - 1];
    }
    return { page: value("page"), caseId: value("case"), runId: value("run"), filters };
  }

  // The catalog filters of an address as Test Framework query parameters.
  function filterQuery(filters = {}) {
    const query = new URLSearchParams();
    if (filters.q) query.set("search", filters.q);
    if (filters.importance) query.append("importance", filters.importance);
    const modules = [].concat(filters.module || []);
    if (modules.length) modules.forEach(module => query.append("module", module));
    else if (filters.group) groupModules(filters.group).forEach(module => query.append("module", module.id));
    if (filters.design === "true" || filters.design === "false") query.set("is_design_point", filters.design);
    (filters.aspect || []).forEach(aspect => query.append("aspect", aspect));
    if (filters.live) query.set("has_live", "true");
    if (filters.feature) query.append("feature", filters.feature);
    if (filters.runner) query.append("runner", filters.runner);
    return query;
  }

  let navigateTo = url => { window.location.href = url; };
  const navigate = (url, options = {}) => navigateTo(url, options);

  // The overview: catalog figures per module, recent runs, TAU slots and the caller's access.
  let overview = null;
  let overviewRequest = null;
  const listeners = new Set();
  function emit(event, detail = {}) { listeners.forEach(listener => listener(event, detail)); }
  function subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }

  function loadOverview({ fresh = false } = {}) {
    if (overview && !fresh) return Promise.resolve(overview);
    if (overviewRequest && !fresh) return overviewRequest;
    const request = api("/test/api/catalog?projection=overview").then(payload => {
      if (overviewRequest === request) {
        overview = payload;
        overviewRequest = null;
        emit("overview");
      }
      return payload;
    }, error => {
      if (overviewRequest === request) overviewRequest = null;
      throw error;
    });
    overviewRequest = request;
    return request;
  }

  function moduleGroups() { return overview?.filters?.modules || []; }
  function groupModules(groupId) { return moduleGroups().find(group => group.id === groupId)?.modules || []; }
  function moduleInfo(moduleId) {
    for (const group of moduleGroups()) {
      const module = (group.modules || []).find(item => item.id === moduleId);
      if (module) return { ...module, group, stats: (overview.modules || []).find(item => item.id === moduleId) || null };
    }
    return null;
  }
  const moduleLabel = moduleId => moduleInfo(moduleId)?.label || moduleId || "—";
  const access = () => overview?.access || {};
  const canOperate = () => access().can_operate !== false;
  const runner = id => (overview?.capabilities?.runners || []).find(item => item.id === id) || null;

  // Recent runs as the overview holds them; a run the page starts, cancels or opens updates its entry.
  const recentRuns = () => overview?.recent_runs || [];
  function rememberRun(run) {
    if (!overview || !run?.run_id) return;
    const selected = [...new Set((run.plan?.selected || []).map(item => item.case_id).filter(Boolean))];
    const summary = {
      run_id: run.run_id, status: run.status, created_by: run.created_by, created_at: run.created_at,
      started_at: run.started_at || "", finished_at: run.finished_at || "",
      plan: { campaign: run.plan?.campaign || "", selected: (run.plan?.selected || []).map(item => ({ case_id: item.case_id, runner: { id: item.runner?.id || item.implementation?.runner || "" } })) },
      case_count: selected.length,
      title: selected.length === 1 ? (run.plan?.selected || [])[0]?.case_title || "" : "",
      module_ids: [...new Set((run.plan?.selected || []).map(item => item.module).filter(Boolean))].sort(),
    };
    const known = recentRuns().find(item => item.run_id === run.run_id);
    overview.recent_runs = [known ? { ...known, ...summary, title: summary.title || known.title } : summary, ...recentRuns().filter(item => item.run_id !== run.run_id)];
    emit("runs");
  }
  function forgetRuns(runIds) {
    if (!overview) return;
    const deleting = new Set(runIds);
    overview.recent_runs = recentRuns().filter(item => !deleting.has(item.run_id));
    emit("runs");
  }
  const runCaseIds = run => [...new Set((run?.plan?.selected || []).map(item => item.case_id).filter(Boolean))];
  const isTauRun = run => run?.plan?.campaign === "tau" || (run?.plan?.selected || []).some(item => (item.runner?.id || item.runner) === "tau");

  // A case's full definition, read once per page.
  const details = new Map();
  function caseDetail(caseId, { fresh = false } = {}) {
    if (!fresh && details.has(caseId)) return details.get(caseId);
    const request = api(`/test/api/catalog/case?case_id=${encodeURIComponent(caseId)}`).then(payload => {
      if (payload?.case?.id !== caseId) throw new Error(t("测试用例详情响应无效。"));
      return payload.case;
    });
    details.set(caseId, request);
    request.catch(() => { if (details.get(caseId) === request) details.delete(caseId); });
    return request;
  }

  // The run draft: the cases the plan page is about to run.  It lives in this
  // browser tab only; a plan that was never started is not kept anywhere else.
  const DRAFT_KEY = "portal-test-run-draft";
  function draft() {
    try {
      const value = JSON.parse(window.sessionStorage.getItem(DRAFT_KEY) || "null");
      if (value && Array.isArray(value.caseIds)) return { caseIds: value.caseIds.map(String), implementation: value.implementation || {}, source: String(value.source || "") };
    } catch (_error) { /* an unreadable draft is an empty one */ }
    return { caseIds: [], implementation: {}, source: "" };
  }
  function setDraft(caseIds, implementation = {}, source = "") {
    const value = { caseIds: [...new Set(caseIds.map(String).filter(Boolean))], implementation, source };
    try { window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(value)); } catch (_error) { /* storage full: the plan page reads none */ }
    return value;
  }
  function prepareRun(caseIds, implementation = {}, source = "") {
    setDraft(caseIds, implementation, source);
    navigate(pageHref("plan"));
  }

  // The catalog page's multi-selection, kept while the tab stays on the Test page.
  const selection = new Set();

  // Resident names for initiators and module Residents, resolved once each.
  const names = new Map();
  function residentName(id) {
    const key = String(id || "");
    if (!key.startsWith("resident_")) return Promise.resolve(key);
    if (!names.has(key)) {
      names.set(key, window.PortalInstructionClient.execute("ResolveResource", { address: key })
        .then(result => String(result?.resource?.title || result?.target?.detail?.title || key))
        .catch(() => key));
    }
    return names.get(key);
  }
  function fillNames(root) {
    root.querySelectorAll("[data-test-resident-name]").forEach(node => {
      const id = node.dataset.testResidentName;
      void residentName(id).then(name => { if (node.isConnected && name) node.textContent = name; });
    });
  }
  const personHtml = id => {
    const key = String(id || "");
    return key ? `<span data-test-resident-name="${escapeHtml(key)}">${escapeHtml(key)}</span>` : escapeHtml(t("未知发起人"));
  };

  // Renderers.
  const statusLabel = status => statusLabels[status] || status || "";
  const statusHtml = (status, extra = "") => status ? `<span class="testStatus${extra ? ` ${extra}` : ""}" data-status="${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>` : "";
  const failureClassLabel = value => failureClassLabels[value] || value || "";
  const riskLabel = risk => riskLabels[risk] || risk || riskLabels.unspecified;
  const riskTone = risk => ["no-external-side-effects", "isolated", "read-only"].includes(risk) ? "is-safe" : !risk || risk === "unspecified" ? "is-undeclared" : "is-risk";
  const chip = (text, tone = "", attributes = "") => `<span class="testChip${tone ? ` ${tone}` : ""}"${attributes ? ` ${attributes}` : ""}>${escapeHtml(text)}</span>`;
  const importanceHtml = value => value ? chip(value, `is-importance`, `data-importance="${escapeHtml(value)}"`) : "";
  function caseChipsHtml(item, { module = false } = {}) {
    const runners = item.runners || [...new Set((item.implementations || []).map(impl => impl.runner))];
    const aspects = (item.aspects || []).map(aspect => aspectLabels[aspect] || aspect).join(" · ");
    return [
      aspects ? `<span class="testAspects">${escapeHtml(aspects)}</span>` : "",
      importanceHtml(item.importance),
      module ? chip(moduleLabel(item.module)) : "",
      item.is_design_point ? chip(item.design_point_ref ? t("设计点 · 已绑定") : t("设计点"), "is-design") : "",
      item.live ? chip("Live", "is-live") : "",
      ...(runners.length ? runners.map(id => chip(runnerLabels[id] || id, "is-runner", `data-runner="${escapeHtml(id)}"`)) : [chip(runnerLabels.none, "is-missing")]),
      item.lifecycle && item.lifecycle !== "active" ? chip(item.lifecycle, "is-lifecycle") : "",
    ].join("");
  }

  function relativeTime(value, now = Date.now()) {
    const timestamp = new Date(String(value || "")).getTime();
    if (!Number.isFinite(timestamp)) return "";
    const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
    if (seconds < 60) return t("刚刚");
    if (seconds < 3600) return i18n().text`${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86_400) return i18n().text`${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 30 * 86_400) return i18n().text`${Math.floor(seconds / 86_400)} 天前`;
    return window.PortalUI.formatDateTime(timestamp, { format: { year: "numeric", month: "numeric", day: "numeric" } });
  }
  function duration(start, end) {
    const from = new Date(String(start || "")).getTime();
    const to = end ? new Date(String(end)).getTime() : Date.now();
    if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) return "";
    const seconds = Math.round((to - from) / 1000);
    if (seconds < 60) return i18n().text`${seconds} 秒`;
    if (seconds < 3600) return i18n().text`${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
    return i18n().text`${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
  }
  function runTitle(run) {
    const count = run?.case_count ?? runCaseIds(run).length;
    if (run?.title) return run.title;
    if (count === 1) return (run?.plan?.selected || [])[0]?.case_title || t("1 条用例");
    return i18n().text`${count} 条用例`;
  }
  const runKind = run => isTauRun(run) ? t("TAU") : t("本地运行");

  // One run in a list: its result, what it ran, who started it and when.
  function runRowHtml(run, { selectable = false, checked = false, disabled = false } = {}) {
    const modules = run.module_ids || [];
    const where = modules.length ? ` · ${escapeHtml(moduleLabel(modules[0]))}${modules.length > 1 ? escapeHtml(i18n().text` 等 ${modules.length} 个模块`) : ""}` : "";
    const time = run.finished_at ? `<small>${escapeHtml(duration(run.started_at || run.created_at, run.finished_at))}</small>` : "";
    const select = selectable
      ? `<input type="checkbox" data-select-run="${escapeHtml(run.run_id)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} aria-label="${escapeHtml(i18n().text`选择运行 ${run.run_id}`)}">`
      : "";
    return `<div class="testRunRow${selectable ? " is-selectable" : ""}" data-run-row="${escapeHtml(run.run_id)}">${select}<a class="testRunRowLink" href="${escapeHtml(runHref(run.run_id))}">
      ${statusHtml(run.status)}
      <span class="testRunRowMain"><strong>${escapeHtml(runTitle(run))}</strong><span class="testRunRowMeta">${escapeHtml(runKind(run))}${where} · ${personHtml(run.created_by)}</span></span>
      <span class="testRunRowTime">${escapeHtml(relativeTime(run.created_at))}${time}</span>
    </a></div>`;
  }

  function crumbsHtml(items) {
    return `<nav class="testCrumbs" aria-label="${escapeHtml(t("Test 导航"))}">${items.map((item, index) => {
      const last = index === items.length - 1;
      const label = escapeHtml(item.label);
      return `${index ? '<span aria-hidden="true">/</span>' : ""}${item.href && !last ? `<a href="${escapeHtml(item.href)}">${label}</a>` : `<span${last ? ' aria-current="page"' : ""}>${label}</span>`}`;
    }).join("")}</nav>`;
  }
  const stateHtml = (state, title, message = "", actionsHtml = "") => window.PortalUIPrimitives.asyncStateHtml({ state, title, message, actionsHtml });

  async function copyLink(url) {
    try {
      await window.navigator.clipboard.writeText(new URL(url, window.location.origin).href);
      notify(t("链接已复制"), "success");
    } catch (error) {
      notify(i18n().text`无法复制链接：${error.message || error}`, "error");
    }
  }

  // The conversation beside the page; the shell installs it.
  let chatOpener = () => false;

  window.PortalTest = Object.freeze({
    access, activeStatuses, addressed, api, aspectLabels, canOperate, caseChipsHtml, caseDetail, caseHref, catalogHref,
    chip, copyLink, crumbsHtml, draft, duration, escapeHtml, failureClassLabel, fillNames, filterQuery, FILTER_KEYS,
    forgetRuns, groupModules, href, importanceHtml, isTauRun, loadOverview, moduleGroups, moduleInfo, moduleLabel, navigate,
    notify, overview: () => overview, pageHref, personHtml, prepareRun, problemStatuses, recentRuns, relativeTime,
    rememberRun, residentName, riskLabel, riskTone, runCaseIds, runHref, runKind, runner, runnerLabels, runRowHtml, runTitle,
    selection, setDraft, stateHtml, statusHtml, statusLabel, subscribe, terminalStatuses,
    openChat: target => chatOpener(target),
    install({ navigate: nextNavigate, openChat }) {
      if (nextNavigate) navigateTo = nextNavigate;
      if (openChat) chatOpener = openChat;
    },
  });
})();
