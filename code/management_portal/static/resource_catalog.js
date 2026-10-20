(function () {
  "use strict";

  const CATEGORIES = Object.freeze({
    skill: {
      label: window.PortalI18n.t("Skill"),
      plural: "Skills",
      search: "Skill 标题 / 预览 / ID",
    },
    experiment: {
      label: window.PortalI18n.t("Experiment"),
      plural: "Experiments",
      search: "Experiment 标题 / 预览 / ID",
    },
    device: {
      label: window.PortalI18n.t("Device"),
      plural: "Devices",
      search: "设备标题 / 预览 / ID",
    },
  });
  const SHARED_DETAIL_CATEGORIES = new Set(["skill", "experiment"]);
  const SEMANTIC_CATEGORIES = new Set(["skill", "experiment"]);
  const resourceStateHtml = (message, options = {}) => window.PortalUIPrimitives.asyncStateHtml({
    state: options.state || "empty",
    title: options.title || "",
    message,
    density: options.density || "panel",
    icon: options.icon,
  });

  const elements = {
    count: document.getElementById("resourceCount"),
    query: document.getElementById("resourceQuery"),
    filterToggle: document.getElementById("resourceFilterToggle"),
    filterPanel: document.getElementById("resourceFilterPanel"),
    projectOptions: document.getElementById("resourceProjectOptions"),
    filterSummary: document.getElementById("resourceFilterSummary"),
    reload: document.getElementById("resourceReload"),
    semanticButton: document.getElementById("resourceSemanticButton"),
    semanticPanel: document.getElementById("resourceSemanticPanel"),
    semanticIdentity: document.getElementById("resourceSemanticIdentity"),
    deviceRegisterOpen: document.getElementById("deviceRegisterOpen"),
    deviceRegisterModal: document.getElementById("deviceRegisterModal"),
    deviceRegisterForm: document.getElementById("deviceRegisterForm"),
    deviceRegisterName: document.getElementById("deviceRegisterName"),
    deviceRegisterClose: document.getElementById("deviceRegisterClose"),
    deviceRegisterCancel: document.getElementById("deviceRegisterCancel"),
    deviceTerminalOverlay: document.getElementById("deviceTerminalOverlay"),
    deviceTerminalMount: document.getElementById("deviceTerminalMount"),
    deviceTerminalTitle: document.getElementById("deviceTerminalTitle"),
    deviceTerminalInterrupt: document.getElementById("deviceTerminalInterrupt"),
    deviceTerminalRestart: document.getElementById("deviceTerminalRestart"),
    deviceTerminalClose: document.getElementById("deviceTerminalClose"),
    results: document.getElementById("resourceResults"),
    detail: document.getElementById("resourceDetail"),
    detailIdentity: document.getElementById("resourceDetailIdentity"),
    detailType: document.getElementById("resourceDetailType"),
    detailTitle: document.getElementById("resourceDetailTitle"),
    detailBody: document.getElementById("resourceDetailBody"),
    detailClose: document.getElementById("resourceDetailClose"),
    detailPanel: document.getElementById("resourceDetailPanel"),
    frameDetail: document.getElementById("resourceFrameDetail"),
    frameDetailFrame: document.getElementById("resourceFrameDetailFrame"),
    frameDetailLoading: document.getElementById("resourceFrameDetailLoading"),
    tabs: document.querySelector("[data-resource-tabs]"),
  };
  const deviceRegisterDialog = window.PortalUI.createDialogController({
    modal: elements.deviceRegisterModal,
    initialFocus: elements.deviceRegisterName,
    backdropClose: true,
    escapeClose: true,
  });
  const deviceTerminalDialog = window.PortalUI.createDialogController({
    modal: elements.deviceTerminalOverlay,
    backdropClose: true,
    escapeClose: true,
    requestClose: () => {
      closeDeviceTerminal().catch(() => {});
    },
  });

  const state = {
    category: currentCategory(),
    records: [],
    lastPayload: null,
    collectionQuery: null,
    semanticSearch: null,
    projects: new Set(),
    requestSequence: 0,
    requestController: null,
    detailSequence: 0,
    queryTimer: 0,
    selectedId: "",
    terminalController: null,
    terminalDeviceId: "",
  };
  const detailHistory = window.PortalResourceDetail.createDetailHistory({
    owner: `resource-catalog:${state.category}`,
    parameter: "item",
  });
  let frameDetailHost = null;
  let localDetailHost = null;
  let projectFilterDropdown = null;
  const resourceServiceControls = () => document.querySelectorAll([
    "[data-portal-board-toolbar] button",
    "[data-portal-board-toolbar] input",
    "[data-portal-board-toolbar] select",
    "#resourceResults button",
    "#resourceResults input",
    "#resourceResults select",
    "#resourceResults textarea",
    "#resourceDetail button",
    "#resourceDetail input",
    "#resourceDetail select",
    "#resourceDetail textarea",
    "#deviceRegisterModal button",
    "#deviceRegisterModal input",
    "#deviceRegisterModal select",
    "#deviceRegisterModal textarea",
  ].join(","));
  const recoveryLoadResources = () => loadResources({
    keepDetail: true,
    propagateServiceUnavailable: true,
  });
  const resourceServiceRecovery = window.PortalComponentRecovery.create({
    endpoint: "/resource/api/service-status",
    serviceName: "ResourceService",
    unavailableCodes: ["resource_service_unavailable"],
    surfaceName: "Resource 页面",
    host: ".resourcePage",
    id: "resourceServiceRecovery",
    controls: resourceServiceControls,
    onReady: recoveryLoadResources,
  });

  function currentCategory() {
    const pathMatch = window.location.pathname.match(/^\/resource\/([^/]+)/);
    const fromPath = String(pathMatch?.[1] || "").toLowerCase();
    if (CATEGORIES[fromPath]) return fromPath;
    const fromQuery = String(new URLSearchParams(window.location.search).get("category") || "").toLowerCase();
    return CATEGORIES[fromQuery] ? fromQuery : "skill";
  }

  const esc = value => window.PortalUIPrimitives.escapeHtml(value);
  const objectOf = window.PortalUIPrimitives.objectOf;

  function asArray(value) {
    return Array.isArray(value) ? value.filter(item => item != null && String(item).trim()) : [];
  }

  function declaredSshOf(record) {
    const accessMethods = objectOf(record?.attributes).access_methods;
    if (!accessMethods || typeof accessMethods !== "object") return {};
    const ssh = accessMethods.ssh;
    return ssh && typeof ssh === "object" ? ssh : {};
  }

  function projectIds(record) {
    return asArray(objectOf(record?.attributes).project_ids).map(String);
  }

  function displayValue(value, fallback = "—") {
    if (Array.isArray(value)) return value.map(String).filter(Boolean).join(", ") || fallback;
    if (value === false) return "No";
    if (value === true) return "Yes";
    if (value === 0) return "0";
    return String(value || "").trim() || fallback;
  }

  const formatModified = value => window.PortalUI.formatDateTime(value, {
    style: "calendar-date", shortYear: true,
  });

  const RESOURCE_QUERY_LIMIT = 500;

  function cancelResourceQuery() {
    state.requestController?.abort();
    state.requestController = null;
    state.requestSequence += 1;
  }

  const getJson = (url, options = {}) => resourceServiceRecovery.guard(() => window.PortalAPI.get(url, options));

  // Page-only services (semantic search jobs, device terminal) keep their routes.
  const postJson = (url, payload = {}) => resourceServiceRecovery.guard(() => window.PortalAPI.post(url, payload));

  // Registry changes are HATG ISA instructions.
  const resourceInstruction = (name, args) => resourceServiceRecovery.guard(() => window.PortalInstructionClient.execute(name, args, {
    idempotencyKey: `resource:${name}:${globalThis.PortalUIPrimitives.randomUUID()}`,
  }));

  async function runResourceAction(resourceId, action, payload = {}) {
    return (await resourceInstruction("RunResourceAction", { resource_id: resourceId, action, arguments: payload })).result;
  }

  function showToast(message, kind = "") {
    return window.PortalUI.toast(message, kind || "success");
  }

  function runCatalogMutation(options) {
    return window.PortalUI.runMutation({
      retryLabel: "重试",
      ...options,
      errorMessage: options.errorMessage || (error => window.PortalUI.errorMessage(options.action || window.PortalI18n.t("操作"), error)),
    });
  }

  function queryState() {
    const params = new URLSearchParams(window.location.search);
    return {
      q: String(params.get("q") || "").trim(),
      project: String(params.get("project") || "").trim(),
      item: String(params.get("item") || "").trim(),
      semantic: String(params.get("semantic") || "").trim(),
      chat: String(params.get("chat") || "").trim(),
    };
  }

  function writeQuery(patch) {
    detailHistory.replace(patch);
  }

  function openFrameDetail(id, options = {}) {
    const category = state.category;
    setFilterOpen(false);
    return frameDetailHost.open({
      resourceId: id,
      resourceType: category,
      detailKind: category,
    }, {
      updateLocation: options.updateLocation !== false,
      searchParams: ["1", "true", "open"].includes(queryState().chat.toLowerCase())
        ? { chat: "1" }
        : {},
    });
  }

  function closeFrameDetail(options = {}) {
    return frameDetailHost.close(options);
  }

  function configurePage() {
    const config = CATEGORIES[state.category];
    document.body.dataset.resourceCategory = state.category;
    elements.tabs.dataset.resourceCategory = state.category;
    window.PortalResourceTabs?.render(elements.tabs);
    elements.query.setAttribute("data-portal-i18n-attrs", JSON.stringify({ placeholder: config.search }));
    elements.query.placeholder = window.PortalI18n.t(config.search);
    elements.deviceRegisterOpen.hidden = state.category !== "device";
    elements.semanticButton.hidden = !SEMANTIC_CATEGORIES.has(state.category);
    elements.semanticPanel.hidden = !SEMANTIC_CATEGORIES.has(state.category);
    if (state.category === "device" && new URLSearchParams(window.location.search).has("device_view")) {
      writeQuery({ device_view: "" });
    }
    elements.detailClose.setAttribute("aria-label", window.PortalI18n.text`关闭 ${config.label} 详情`);
    window.PortalSite.setTitle(() => `${window.PortalI18n.t(config.label)} — ${window.PortalI18n.t("Resource")}`);
    const current = queryState();
    elements.query.value = current.q;
    state.collectionQuery = current;
  }

  function initializeSemanticSearch() {
    if (!SEMANTIC_CATEGORIES.has(state.category)) return;
    state.semanticSearch = window.PortalSemanticSearch?.create({
      input: elements.query,
      button: elements.semanticButton,
      panel: elements.semanticPanel,
      identity: elements.semanticIdentity,
      identityOptions: {
        titleId: "resourceSemanticSearchTitle",
        idId: "resourceSemanticSearchId",
      },
      client: {
        get: getJson,
        post: postJson,
      },
      initialActiveId: queryState().semantic,
      urls: {
        list: `/api/resource-semantic-searches?resource_scope=${encodeURIComponent(state.category)}`,
        start: "/api/resource-semantic-search/start",
        refresh: "/api/resource-semantic-search/refresh",
        delete: "/api/resource-semantic-search/delete",
      },
      startPayload: () => ({ resource_scope: state.category }),
      actionPayload: () => ({ resource_scope: state.category }),
      onStart: () => {
        closeDetail({ updateUrl: false });
        elements.query.value = "";
        writeQuery({ q: "", semantic: "", item: "" });
        loadResources({ keepDetail: false });
      },
      onActiveChange: searchId => {
        if (searchId) {
          elements.query.value = "";
          writeQuery({ q: "", semantic: searchId, item: "" });
        } else {
          writeQuery({ semantic: "" });
        }
        if (state.lastPayload) renderResults(state.lastPayload);
      },
      onError: (_error, message) => showToast(message, "error"),
    });
  }

  function cardBadgesFor(record) {
    const attrs = objectOf(record?.attributes);
    const definitions = {
      skill: [
        ["files", attrs.file_count],
        ["importance", attrs.importance],
        ["status", attrs.status],
      ],
      experiment: [
        ["nodes", attrs.node_count],
        ["status", attrs.status],
        ["importance", attrs.importance],
      ],
      device: [
        ["status", attrs.status],
        ["kind", attrs.device_kind],
      ],
    };
    return (definitions[state.category] || [])
      .filter(([label, value]) => {
        if (label === "importance") {
          const importance = Number(value);
          return Number.isFinite(importance) && importance > 0;
        }
        return value === 0 || value === false || (Array.isArray(value) ? value.length : String(value || "").trim());
      })
      .slice(0, 2);
  }

  function renderCardBadges(record) {
    return cardBadgesFor(record).map(([label, value]) => {
      const visibleLabel = ({
        files: window.PortalI18n.t("文件"),
        importance: window.PortalI18n.t("重要度"),
        status: window.PortalI18n.t("状态"),
        ownership: window.PortalI18n.t("归属"),
        nodes: window.PortalI18n.t("节点"),
        kind: window.PortalI18n.t("类型"),
      })[label] || label;
      const display = displayValue(value);
      return window.PortalI18n.html`<span class="portalCollectionBadge" title="${esc(`${visibleLabel}：${display}`)}">${esc(visibleLabel)} ${esc(display)}</span>`;
    }).join("");
  }

  function modifiedTime(record) {
    const value = String(objectOf(record?.source).modified_at || "").trim();
    if (!value) return 0;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function projectFor(record) {
    const attrs = objectOf(record?.attributes);
    if (record?.resource_type === "skill" && attrs.read_scope === "global") {
      if (attrs.source === "shared") return "Shared Skills";
      if (attrs.source === "third_party") return "Third-party Skills";
    }
    const projects = projectIds(record);
    const selected = queryState().project;
    if (selected && projects.includes(selected)) return selected;
    return projects[0] || "Unassigned";
  }

  function groupByProject(records) {
    const groups = new Map();
    records.forEach(record => {
      const project = projectFor(record);
      if (!groups.has(project)) groups.set(project, []);
      groups.get(project).push(record);
    });
    groups.forEach(items => items.sort((left, right) => {
      const timeDifference = modifiedTime(right) - modifiedTime(left);
      if (timeDifference) return timeDifference;
      return String(left.title || left.resource_id || "").localeCompare(String(right.title || right.resource_id || ""));
    }));
    return [...groups.entries()].sort((left, right) => {
      if (state.category === "skill") {
        const sourceRank = group => ({ shared: 0, project: 1, third_party: 2 })[
          String(objectOf(group[1][0]?.attributes).source || "project")
        ] ?? 1;
        const rankDifference = sourceRank(left) - sourceRank(right);
        if (rankDifference) return rankDifference;
        return left[0].localeCompare(right[0]);
      }
      if (left[0] === "Unassigned" && right[0] !== "Unassigned") return 1;
      if (right[0] === "Unassigned" && left[0] !== "Unassigned") return -1;
      const newestLeft = Math.max(0, ...left[1].map(modifiedTime));
      const newestRight = Math.max(0, ...right[1].map(modifiedTime));
      return newestRight - newestLeft || left[0].localeCompare(right[0]);
    });
  }

  function renderCard(record) {
    const id = String(record.resource_id || "");
    const modified = formatModified(objectOf(record?.source).modified_at);
    const type = String(record.resource_type || state.category);
    const semanticResult = state.semanticSearch?.resultFor(id) || null;
    return window.PortalI18n.html`
      <article
        class="portalItemCard portalCollectionCard${semanticResult ? " is-semantic-match" : ""}"
        role="button"
        tabindex="0"
        title="${esc(id)}"
        aria-label="查看 ${esc(record.title || id)}"
        data-resource-open="${esc(id)}"
      >
        <h3 class="portalItemTitle">${esc(record.title || id)}</h3>
        <div class="portalCollectionCardMeta">
          <span class="portalCollectionItemMeta">${esc(modified)}</span>
          <span class="portalCollectionBadges">${renderCardBadges(record)}</span>
        </div>
        <p class="portalItemPreview">${esc(record.summary || "")}</p>
        ${semanticResult ? window.PortalI18n.html`<div class="portalCollectionSemanticMatch"><strong>${Number(semanticResult.score || 0)}%</strong><span>${esc(semanticResult.reason || window.PortalI18n.t("语义匹配"))}</span></div>` : ""}
        <span class="portalCollectionTypeChip" data-type="${esc(type)}">${esc(type)}</span>
      </article>`;
  }

  function renderProject(project, records) {
    return window.PortalI18n.html`
      <section class="portalProjectGroup" data-resource-project="${esc(project)}">
        <header class="portalProjectHeader">
          <h2 class="portalProjectTitle">${esc(project)}</h2>
          <div class="portalProjectActions">
            <span class="portalProjectCount">${records.length} 项</span>
            <button
              class="portalProjectToggle"
              type="button"
              aria-expanded="true"
              aria-label="折叠 ${esc(project)}"
              data-resource-project-toggle
              data-portal-icon="chevron-down"
            ></button>
          </div>
        </header>
        <div class="portalProjectItems">${records.map(renderCard).join("")}</div>
      </section>`;
  }

  function numberValue(value) {
    if (value == null || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function percentValue(value) {
    const parsed = numberValue(value);
    return parsed == null ? null : Math.max(0, Math.min(100, parsed));
  }

  function deviceKind(record) {
    const attrs = objectOf(record?.attributes);
    return String(attrs.device_kind || "device")
      .trim()
      .toLowerCase();
  }

  function deviceKindLabel(record) {
    const kind = deviceKind(record);
    const known = {
      device: window.PortalI18n.t("设备"),
      gpu: "GPU",
      gpu_server: window.PortalI18n.t("GPU 服务器"),
      robot: window.PortalI18n.t("机器人"),
      storage: window.PortalI18n.t("存储设备"),
      network: window.PortalI18n.t("网络设备"),
      sensor: window.PortalI18n.t("传感器"),
      workstation: window.PortalI18n.t("工作站"),
    };
    return known[kind] || kind
      .split(/[_-]+/)
      .filter(Boolean)
      .map(part => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
      .join(" ");
  }

  function deviceStatus(record) {
    const attrs = objectOf(record?.attributes);
    const raw = String(attrs.status || attrs.state || "").trim().toLowerCase();
    const labels = {
      offline: window.PortalI18n.t("离线"),
      fault: window.PortalI18n.t("故障"),
      failed: window.PortalI18n.t("失败"),
      error: window.PortalI18n.t("错误"),
      unreachable: window.PortalI18n.t("不可达"),
      critical: window.PortalI18n.t("严重"),
      degraded: window.PortalI18n.t("性能下降"),
      warning: window.PortalI18n.t("警告"),
      maintenance: window.PortalI18n.t("维护中"),
      busy: window.PortalI18n.t("使用中"),
      empty: window.PortalI18n.t("可用"),
      available: window.PortalI18n.t("可用"),
      idle: window.PortalI18n.t("空闲"),
      ok: window.PortalI18n.t("在线"),
      online: window.PortalI18n.t("在线"),
      healthy: window.PortalI18n.t("健康"),
      active: window.PortalI18n.t("活跃"),
      ready: window.PortalI18n.t("就绪"),
    };
    if (["offline", "fault", "failed", "error", "unreachable", "critical"].includes(raw)) {
      return { key: "danger", label: labels[raw] || window.PortalI18n.t("故障") };
    }
    if (["degraded", "warning", "maintenance"].includes(raw)) {
      return { key: "warning", label: labels[raw] };
    }
    if (raw === "busy" || attrs.busy === true) {
      return { key: "busy", label: window.PortalI18n.t("使用中") };
    }
    if (["empty", "available"].includes(raw)) {
      return { key: "available", label: window.PortalI18n.t("可用") };
    }
    if (raw === "idle") {
      return { key: "available", label: window.PortalI18n.t("空闲") };
    }
    if (["ok", "online", "healthy", "active", "ready"].includes(raw)) {
      return { key: "online", label: labels[raw] };
    }
    return {
      key: "unknown",
      label: raw
        ? raw.split(/[_-]+/).map(part => `${part.charAt(0).toUpperCase()}${part.slice(1)}`).join(" ")
        : window.PortalI18n.t("未知"),
    };
  }

  function deviceObservedAt(record) {
    return String(objectOf(record?.attributes).observed_at || objectOf(record?.source).modified_at || "").trim();
  }

  function deviceObservedTime(record) {
    const parsed = Date.parse(deviceObservedAt(record));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function deviceObservationLabel(record) {
    const observed = deviceObservedTime(record);
    if (!observed) return window.PortalI18n.t("无观测数据");
    const elapsed = Math.max(0, Date.now() - observed);
    const minutes = Math.floor(elapsed / 60000);
    if (minutes < 1) return window.PortalI18n.t("刚刚观测");
    if (minutes < 60) return window.PortalI18n.text`${minutes} 分钟前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return window.PortalI18n.text`${hours} 小时前`;
    return window.PortalI18n.text`${Math.floor(hours / 24)} 天前`;
  }

  function deviceObservationStale(record) {
    const observed = deviceObservedTime(record);
    return !observed || Date.now() - observed > 30 * 60 * 1000;
  }

  function renderResourceDetailIdentity(record, type = "") {
    window.PortalResourceHeader?.mount?.(elements.detailIdentity, {
      title: record?.title || record?.resource_id || window.PortalI18n.t("Resource detail"),
      type: type || record?.resource_type || state.category || "resource",
      resource_id: record?.resource_id || "",
    }, {
      headingTag: "h2",
      titleId: "resourceDetailTitle",
      typeId: "resourceDetailType",
      idId: "resourceDetailId",
    });
    elements.detailTitle = document.getElementById("resourceDetailTitle");
    elements.detailType = document.getElementById("resourceDetailType");
  }

  function deviceGpuUtilization(record) {
    return percentValue(objectOf(record?.attributes).gpu_util_percent);
  }

  function deviceMemoryUtilization(record) {
    const attrs = objectOf(record?.attributes);
    const direct = percentValue(attrs.memory_util_percent ?? attrs.unified_memory_used_percent);
    if (direct != null) return direct;
    const used = numberValue(attrs.memory_used_mb ?? attrs.unified_memory_used_mb);
    const total = numberValue(attrs.memory_total_mb ?? attrs.unified_memory_total_mb);
    return used != null && total ? Math.max(0, Math.min(100, used / total * 100)) : null;
  }

  function deviceSummary(records) {
    const roots = records.filter(record => !String(objectOf(record?.attributes).parent_device_id || "").trim());
    const busy = records.filter(record => deviceStatus(record).key === "busy");
    const available = records.filter(record => deviceStatus(record).key === "available");
    const attention = records.filter(record => ["danger", "warning"].includes(deviceStatus(record).key));
    const stale = records.filter(deviceObservationStale);
    return {
      total: records.length,
      roots: roots.length,
      components: Math.max(0, records.length - roots.length),
      kinds: new Set(records.map(deviceKind)).size,
      busy: busy.length,
      available: available.length,
      attention: attention.length,
      stale: stale.length,
    };
  }

  function renderDeviceStatus(record, { freshness = false } = {}) {
    const status = deviceStatus(record);
    const stale = freshness && deviceObservationStale(record);
    return window.PortalI18n.html`<span class="deviceStatusPill ${esc(status.key)}${stale ? " stale" : ""}">
      <span class="deviceStatusDot" aria-hidden="true"></span>
      ${esc(stale ? window.PortalI18n.text`${status.label} · 观测过期` : status.label)}
    </span>`;
  }

  function renderDeviceStat(label, value, detail, tone = "") {
    return window.PortalI18n.html`<div class="deviceStatCard ${esc(tone)}">
      <span>${esc(label)}</span>
      <strong>${esc(value)}</strong>
      <small>${esc(detail)}</small>
    </div>`;
  }

  function childDevices(record, records) {
    const id = String(record.resource_id || "");
    return records.filter(candidate => String(objectOf(candidate?.attributes).parent_device_id || "") === id);
  }

  function serverCapacity(record, records) {
    const attrs = objectOf(record?.attributes);
    const children = childDevices(record, records);
    const total = numberValue(attrs.gpu_count ?? attrs.expected_gpu_count) ?? children.length;
    const busy = numberValue(attrs.busy_gpu_count)
      ?? children.filter(candidate => deviceStatus(candidate).key === "busy").length;
    const available = numberValue(attrs.available_gpu_count ?? attrs.empty_gpu_count)
      ?? children.filter(candidate => deviceStatus(candidate).key === "available").length;
    return {
      total: Math.max(0, total),
      busy: Math.max(0, busy),
      available: Math.max(0, available),
    };
  }

  function renderServerCapacity(record, records) {
    const id = String(record.resource_id || "");
    const capacity = serverCapacity(record, records);
    const busyPercent = capacity.total ? Math.min(100, capacity.busy / capacity.total * 100) : 0;
    return window.PortalI18n.html`<article
      class="deviceCapacityRow"
      role="button"
      tabindex="0"
      data-resource-open="${esc(id)}"
      aria-label="查看 ${esc(record.title || id)}"
    >
      <div class="deviceCapacityIdentity">
        <strong>${esc(record.title || id)}</strong>
        <span>${esc(objectOf(record?.attributes).hostname || objectOf(record?.attributes).ip || deviceKindLabel(record))}</span>
      </div>
      <div class="deviceCapacityTrack" aria-label="${capacity.total} 个 GPU 中有 ${capacity.busy} 个正在使用">
        <span style="width:${busyPercent.toFixed(2)}%"></span>
      </div>
      <div class="deviceCapacityNumbers">
        <strong>${capacity.busy}/${capacity.total}</strong>
        <span>${capacity.available} 个可用</span>
      </div>
    </article>`;
  }

  function renderMetricGauge(label, value, suffix = "%") {
    if (value == null) return window.PortalI18n.html`<div class="deviceMetricGauge empty"><span>${esc(label)}</span><strong>—</strong></div>`;
    const percent = percentValue(value);
    return window.PortalI18n.html`<div class="deviceMetricGauge">
      <div><span>${esc(label)}</span><strong>${esc(Math.round(value))}${esc(suffix)}</strong></div>
      ${suffix === "%" ? window.PortalI18n.html`<div class="deviceMetricTrack"><span style="width:${percent.toFixed(2)}%"></span></div>` : ""}
    </div>`;
  }

  function renderTelemetryDevice(record) {
    const attrs = objectOf(record?.attributes);
    const id = String(record.resource_id || "");
    const gpu = deviceGpuUtilization(record);
    const memory = deviceMemoryUtilization(record);
    const temperature = numberValue(attrs.temperature_c);
    return window.PortalI18n.html`<article
      class="deviceTelemetryCard"
      role="button"
      tabindex="0"
      data-resource-open="${esc(id)}"
      aria-label="查看 ${esc(record.title || id)}"
    >
      <header>
        <div>
          <span>${esc(deviceKindLabel(record))}</span>
          <h3>${esc(record.title || id)}</h3>
        </div>
        ${renderDeviceStatus(record)}
      </header>
      <div class="deviceTelemetryMetrics">
        ${renderMetricGauge("GPU", gpu)}
        ${renderMetricGauge(window.PortalI18n.t("显存"), memory)}
        ${renderMetricGauge(window.PortalI18n.t("温度"), temperature, "°C")}
      </div>
      <footer>
        <span>${esc(objectOf(record?.attributes).hostname || objectOf(record?.attributes).model || "")}</span>
        <span class="${deviceObservationStale(record) ? "stale" : ""}">${esc(deviceObservationLabel(record))}</span>
      </footer>
    </article>`;
  }

  function deviceAttentionReason(record) {
    const status = deviceStatus(record);
    const temperature = numberValue(objectOf(record?.attributes).temperature_c);
    if (status.key === "danger") return status.label;
    if (status.key === "warning") return status.label;
    if (temperature != null && temperature >= 80) return `温度 ${Math.round(temperature)}°C`;
    if (deviceObservationStale(record)) return window.PortalI18n.text`观测于${deviceObservationLabel(record)}`;
    return "";
  }

  function renderDeviceRosterCard(record, recordById) {
    const attrs = objectOf(record?.attributes);
    const id = String(record.resource_id || "");
    const parent = recordById.get(String(attrs.parent_device_id || ""));
    const context = parent
      ? window.PortalI18n.text`属于 ${parent.title || parent.resource_id}`
      : attrs.hostname || attrs.location || projectIds(record)[0] || "已注册设备";
    return window.PortalI18n.html`<button
      class="deviceRosterCard"
      type="button"
      data-resource-open="${esc(id)}"
      aria-label="查看 ${esc(record.title || id)}"
    >
      <span>${esc(deviceKindLabel(record))}</span>
      ${renderDeviceStatus(record, { freshness: true })}
      <strong>${esc(record.title || id)}</strong>
      <small>${esc(context)}</small>
      <small>${esc(deviceObservationLabel(record))}</small>
    </button>`;
  }

  function renderDeviceOperationsView(records) {
    const summary = deviceSummary(records);
    const recordById = new Map(records.map(record => [String(record.resource_id || ""), record]));
    const roots = records.filter(record => !String(objectOf(record?.attributes).parent_device_id || "").trim());
    const serverRecords = roots.filter(record => deviceKind(record) === "gpu_server");
    const telemetryRecords = records.filter(record => (
      deviceGpuUtilization(record) != null
      || deviceMemoryUtilization(record) != null
      || numberValue(objectOf(record?.attributes).temperature_c) != null
    ));
    const attentionRecords = records
      .map(record => ({ record, reason: deviceAttentionReason(record) }))
      .filter(item => item.reason)
      .sort((left, right) => {
        const leftStatus = deviceStatus(left.record).key;
        const rightStatus = deviceStatus(right.record).key;
        const priority = { danger: 0, warning: 1 };
        return (priority[leftStatus] ?? 2) - (priority[rightStatus] ?? 2)
          || deviceObservedTime(left.record) - deviceObservedTime(right.record);
      });
    return window.PortalI18n.html`
      <section class="devicePageView deviceOperationsView" data-device-view-panel="operations" aria-label="设备运行">
        <header class="deviceViewHeadline">
          <div>
            <span class="deviceViewEyebrow">设备运行</span>
            <h1>设备运行总览</h1>
            <p>在一个范围内比较当前容量、资源占用、观测新鲜度和异常状态。</p>
          </div>
        </header>
        <div class="deviceStatsGrid">
          ${renderDeviceStat(window.PortalI18n.t("设备"), summary.total, window.PortalI18n.text`${summary.roots} 个根设备`)}
          ${renderDeviceStat(window.PortalI18n.t("使用中"), summary.busy, window.PortalI18n.t("正在使用的组件"), "busy")}
          ${renderDeviceStat(window.PortalI18n.t("可用"), summary.available, window.PortalI18n.t("已就绪的组件"), "available")}
          ${renderDeviceStat(window.PortalI18n.t("需关注"), summary.attention, window.PortalI18n.t("设备上报的警告"), summary.attention ? "warning" : "")}
          ${renderDeviceStat(window.PortalI18n.t("观测过期"), summary.stale, window.PortalI18n.t("超过 30 分钟未更新"), summary.stale ? "stale" : "")}
        </div>
        <div class="deviceOperationsLayout">
          <section class="deviceOperationsPanel deviceCapacityPanel">
            <header>
              <div><span>容量</span><h2>计算资源池</h2></div>
              <small>${serverRecords.length} 台服务器</small>
            </header>
            <div class="deviceCapacityList">
              ${serverRecords.length
                ? serverRecords.map(record => renderServerCapacity(record, records)).join("")
                : window.PortalI18n.t('<div class="devicePanelEmpty">暂无可统计容量的根设备。</div>')}
            </div>
          </section>
          <section class="deviceOperationsPanel deviceAttentionPanel">
            <header>
              <div><span>关注</span><h2>待处理</h2></div>
              <small>${attentionRecords.length} 项</small>
            </header>
            <div class="deviceAttentionList">
              ${attentionRecords.length
                ? attentionRecords.slice(0, 6).map(({ record, reason }) => window.PortalI18n.html`
                  <button type="button" data-resource-open="${esc(record.resource_id)}">
                    <span class="deviceAttentionDot ${esc(deviceStatus(record).key)}"></span>
                    <span><strong>${esc(record.title || record.resource_id)}</strong><small>${esc(reason)}</small></span>
                  </button>
                `).join("")
                : window.PortalI18n.t('<div class="devicePanelEmpty">当前没有警告或过期观测。</div>')}
            </div>
          </section>
          <section class="deviceOperationsPanel deviceTelemetryPanel">
            <header>
              <div><span>当前观测</span><h2>设备遥测</h2></div>
              <small>${telemetryRecords.length} 个设备正在上报</small>
            </header>
            <div class="deviceTelemetryGrid">
              ${telemetryRecords.length
                ? telemetryRecords.map(renderTelemetryDevice).join("")
                : window.PortalI18n.t('<div class="devicePanelEmpty">暂无可用的当前遥测数据。</div>')}
            </div>
          </section>
          <section class="deviceOperationsPanel deviceInventoryPanel">
            <header>
              <div><span>设备清单</span><h2>已注册设备</h2></div>
              <small>${records.length} 个设备</small>
            </header>
            <div class="deviceRosterGrid">
              ${records.map(record => renderDeviceRosterCard(record, recordById)).join("")}
            </div>
          </section>
        </div>
      </section>`;
  }

  function renderProjectOptions(records) {
    records.flatMap(projectIds).forEach(project => state.projects.add(project));
    const current = queryState().project;
    if (current) state.projects.add(current);
    const options = [...state.projects].sort((left, right) => left.localeCompare(right));
    elements.projectOptions.innerHTML = window.PortalI18n.html`
      <label class="portalCollectionFilterOption">
        <input type="radio" name="resourceProject" value="" ${current ? "" : "checked"} />
        <span>全部 Project</span>
      </label>
      ${options.map(project => window.PortalI18n.html`
        <label class="portalCollectionFilterOption">
          <input type="radio" name="resourceProject" value="${esc(project)}" ${project === current ? "checked" : ""} />
          <span>${esc(project)}</span>
        </label>
      `).join("")}`;
    const filterLabel = current ? window.PortalI18n.text`Project · ${current}` : window.PortalI18n.t("筛选");
    const filterLabelNode = elements.filterToggle.querySelector("span");
    if (filterLabelNode) filterLabelNode.textContent = filterLabel;
    else elements.filterToggle.textContent = filterLabel;
    elements.filterToggle.title = current ? window.PortalI18n.text`当前 Project：${current}` : window.PortalI18n.t("按 Project 筛选资源");
    elements.filterToggle.classList.toggle("active", Boolean(current) || !elements.filterPanel.hidden);
    projectFilterSummary?.sync?.();
  }

  function renderResults(payload) {
    state.lastPayload = payload;
    const allRecords = Array.isArray(payload.resources) ? payload.resources : [];
    state.records = allRecords;
    renderProjectOptions(allRecords);
    const records = state.semanticSearch
      ? state.semanticSearch.filterAndSort(
          allRecords,
          record => record.resource_id,
        )
      : allRecords;
    const parsedTotal = Number(payload.total ?? allRecords.length);
    const total = Number.isFinite(parsedTotal) ? parsedTotal : allRecords.length;
    elements.count.textContent = state.semanticSearch?.activeId()
      ? window.PortalI18n.text`${records.length} 个语义匹配`
      : state.category === "device"
        ? total === allRecords.length
          ? window.PortalI18n.text`${total} 个设备`
          : window.PortalI18n.text`${allRecords.length} / ${total} 个设备`
        : total === allRecords.length
          ? window.PortalI18n.text`${total} 项`
          : window.PortalI18n.text`${allRecords.length} / ${total} 项`;
    if (!records.length) {
      elements.results.innerHTML = resourceStateHtml(
        state.semanticSearch?.activeId()
          ? window.PortalI18n.text`当前语义检索没有匹配的 ${CATEGORIES[state.category].label} 资源。`
          : window.PortalI18n.text`ResourceService 当前没有返回匹配的 ${CATEGORIES[state.category].label} 资源。`,
      );
      return;
    }
    if (state.category === "device") {
      elements.results.innerHTML = renderDeviceOperationsView(records);
      return;
    }
    elements.results.innerHTML = window.PortalI18n.html`<div class="portalProjectCollection">${groupByProject(records).map(([project, items]) => renderProject(project, items)).join("")}</div>`;
  }

  async function loadResources({
    keepDetail = true,
    propagateServiceUnavailable = false,
  } = {}) {
    cancelResourceQuery();
    const controller = new AbortController();
    state.requestController = controller;
    const requestSequence = state.requestSequence;
    const current = queryState();
    state.collectionQuery = current;
    elements.query.value = current.q;
    if (!state.lastPayload) {
      elements.results.innerHTML = resourceStateHtml(
        state.category === "device" ? "正在加载设备。" : "正在加载资源…",
        { state: "loading" },
      );
    }
    elements.count.textContent = window.PortalI18n.t("正在加载…");
    const params = new URLSearchParams({
      resource_type: state.category,
      include_attributes: "1",
      limit: String(RESOURCE_QUERY_LIMIT),
    });
    if (current.q) params.set("q", current.q);
    if (current.project) params.set("project", current.project);
    try {
      const payload = await getJson(`/api/resource-index/query?${params}`, {
        signal: controller.signal,
      });
      if (requestSequence !== state.requestSequence) return;
      renderResults(payload);
      if (keepDetail && current.item) await openInitialDetail(current.item);
      else if (!current.item) closeDetail({ updateUrl: false });
    } catch (error) {
      if (requestSequence !== state.requestSequence) return;
      if (resourceServiceRecovery.isUnavailable(error)) {
        if (propagateServiceUnavailable) throw error;
        void resourceServiceRecovery.check();
        return;
      }
      elements.count.textContent = window.PortalI18n.t("暂不可用");
      elements.results.innerHTML = resourceStateHtml(error.message || error, {
        state: "error",
        title: state.category === "device" ? window.PortalI18n.t("设备加载失败") : window.PortalI18n.t("Resource 加载失败"),
      });
    } finally {
      if (requestSequence === state.requestSequence) state.requestController = null;
    }
  }

  function formatDateTime(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    return window.PortalUI.formatDateTime(text, {
            format: {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      },
    });
  }

  function formatMemoryMb(value) {
    const megabytes = numberValue(value);
    if (megabytes == null) return "";
    if (megabytes < 1024) return `${Math.round(megabytes)} MB`;
    const gibibytes = megabytes / 1024;
    return `${gibibytes >= 10 ? Math.round(gibibytes) : gibibytes.toFixed(1)} GiB`;
  }

  function renderDeviceDetailMetric({ label, value, unit = "", detail = "", percent = null, tone = "" }) {
    const normalizedPercent = percentValue(percent);
    return window.PortalI18n.html`<article class="deviceDetailMetric ${esc(tone)}">
      <span>${esc(label)}</span>
      <strong>${esc(value)}${unit ? window.PortalI18n.html`<small>${esc(unit)}</small>` : ""}</strong>
      ${normalizedPercent == null ? "" : window.PortalI18n.html`
        <div class="deviceDetailMetricTrack" aria-hidden="true">
          <span style="width:${normalizedPercent.toFixed(2)}%"></span>
        </div>`}
      <p>${esc(detail)}</p>
    </article>`;
  }

  function deviceDetailMetrics(record) {
    const attrs = objectOf(record?.attributes);
    const kind = deviceKind(record);
    const metrics = [];
    if (kind === "gpu_server") {
      const capacity = serverCapacity(record, state.records);
      if (capacity.total || childDevices(record, state.records).length) {
        metrics.push({
          label: window.PortalI18n.t("GPU 容量"),
          value: `${capacity.busy} / ${capacity.total}`,
          unit: "使用中",
          detail: `${capacity.available} 个可用`,
          percent: capacity.total ? capacity.busy / capacity.total * 100 : 0,
          tone: capacity.busy ? "busy" : capacity.available ? "available" : "",
        });
        metrics.push({
          label: window.PortalI18n.t("可用 GPU"),
          value: capacity.available,
          detail: "可分配任务",
          tone: capacity.available ? "available" : "",
        });
        metrics.push({
          label: window.PortalI18n.t("组件"),
          value: childDevices(record, state.records).length,
          detail: "已索引的子设备",
        });
      }
    }
    const gpu = deviceGpuUtilization(record);
    if (gpu != null) {
      metrics.push({
        label: window.PortalI18n.t("GPU 利用率"),
        value: Math.round(gpu),
        unit: "%",
        detail: gpu >= 80 ? "当前负载较高" : gpu > 0 ? "正在运行" : "当前空闲",
        percent: gpu,
        tone: gpu >= 80 ? "busy" : "",
      });
    }
    const memory = deviceMemoryUtilization(record);
    if (memory != null) {
      const used = formatMemoryMb(attrs.memory_used_mb ?? attrs.unified_memory_used_mb);
      const total = formatMemoryMb(attrs.memory_total_mb ?? attrs.unified_memory_total_mb);
      metrics.push({
        label: window.PortalI18n.t("GPU 显存"),
        value: Math.round(memory),
        unit: "%",
        detail: used && total ? `${used} / ${total}` : "当前分配",
        percent: memory,
      });
    }
    const temperature = numberValue(attrs.temperature_c);
    if (temperature != null) {
      metrics.push({
        label: window.PortalI18n.t("温度"),
        value: Math.round(temperature),
        unit: "°C",
        detail: temperature >= 80 ? "需要关注" : "当前读数",
        tone: temperature >= 80 ? "warning" : "",
      });
    }
    const power = numberValue(attrs.power_draw_w);
    const powerLimit = numberValue(attrs.power_limit_w);
    if (power != null) {
      metrics.push({
        label: window.PortalI18n.t("功耗"),
        value: Math.round(power),
        unit: "W",
        detail: powerLimit ? `上限 ${Math.round(powerLimit)} W` : "当前功耗",
        percent: powerLimit ? power / powerLimit * 100 : null,
      });
    }
    return metrics;
  }

  function renderDeviceFactsPanel(eyebrow, title, facts) {
    const visible = facts.filter(([, value]) => (
      value === 0
      || value === false
      || (Array.isArray(value) ? value.length : String(value || "").trim())
    ));
    if (!visible.length) return "";
    return window.PortalI18n.html`<section class="deviceDetailPanel">
      <header><span>${esc(eyebrow)}</span><h3>${esc(title)}</h3></header>
      <dl class="deviceDetailFacts">
        ${visible.map(([label, value, mono = false]) => window.PortalI18n.html`
          <div>
            <dt>${esc(label)}</dt>
            <dd class="${mono ? "mono" : ""}">${esc(displayValue(value))}</dd>
          </div>
        `).join("")}
      </dl>
    </section>`;
  }

  function renderDeviceComponent(record) {
    const attrs = objectOf(record?.attributes);
    const id = String(record.resource_id || "");
    const metrics = [];
    const gpu = deviceGpuUtilization(record);
    const memory = deviceMemoryUtilization(record);
    const temperature = numberValue(attrs.temperature_c);
    if (gpu != null) metrics.push(`GPU ${Math.round(gpu)}%`);
    if (memory != null) metrics.push(`显存 ${Math.round(memory)}%`);
    if (temperature != null) metrics.push(`${Math.round(temperature)}°C`);
    return window.PortalI18n.html`<button
      class="deviceDetailComponent"
      type="button"
      data-device-detail-open="${esc(id)}"
      aria-label="打开 ${esc(record.title || id)}"
    >
      <span class="deviceDetailComponentIndex">${esc(
        numberValue(attrs.gpu_index) == null ? deviceKindLabel(record) : `GPU ${attrs.gpu_index}`,
      )}</span>
      <strong>${esc(record.title || id)}</strong>
      ${renderDeviceStatus(record, { freshness: true })}
      <small>${esc(metrics.join(" · ") || deviceObservationLabel(record))}</small>
    </button>`;
  }

  function renderDeviceComponentReference(resourceId) {
    return window.PortalI18n.html`<button
      class="deviceDetailComponent"
      type="button"
      data-device-detail-open="${esc(resourceId)}"
      aria-label="打开 ${esc(resourceId)}"
    >
      <span class="deviceDetailComponentIndex">组件</span>
      <strong>${esc(resourceId)}</strong>
      <small>打开已索引设备</small>
    </button>`;
  }

  function renderDeviceTopology(record) {
    const attrs = objectOf(record?.attributes);
    const parentId = String(attrs.parent_device_id || "").trim();
    const parent = state.records.find(item => item.resource_id === parentId);
    const children = childDevices(record, state.records).sort((left, right) => {
      const leftIndex = numberValue(objectOf(left?.attributes).gpu_index);
      const rightIndex = numberValue(objectOf(right?.attributes).gpu_index);
      return (leftIndex ?? Number.MAX_SAFE_INTEGER) - (rightIndex ?? Number.MAX_SAFE_INTEGER)
        || String(left.title || left.resource_id).localeCompare(String(right.title || right.resource_id));
    });
    const knownChildIds = new Set(children.map(child => String(child.resource_id || "")));
    const missingChildIds = asArray(attrs.gpu_device_ids)
      .map(String)
      .filter(id => id && id !== record.resource_id && !knownChildIds.has(id));
    const componentCount = children.length + missingChildIds.length;
    if (!parentId && !componentCount) return "";
    return window.PortalI18n.html`<section class="deviceDetailSection deviceDetailTopology">
      <header>
        <div><span>关联关系</span><h3>设备拓扑</h3></div>
        <small>${componentCount ? window.PortalI18n.text`${componentCount} 个子设备` : window.PortalI18n.t("1 个父设备")}</small>
      </header>
      ${parentId ? window.PortalI18n.html`
        <div class="deviceDetailParent">
          <span>父设备</span>
          <button type="button" data-device-detail-open="${esc(parentId)}">
            <strong>${esc(parent?.title || parentId)}</strong>
            <small>${esc(parent ? objectOf(parent?.attributes).hostname || parentId : window.PortalI18n.t("打开父设备"))}</small>
            <span aria-hidden="true">→</span>
          </button>
        </div>
      ` : ""}
      ${componentCount ? window.PortalI18n.html`
        <div class="deviceDetailComponents">
          ${children.map(renderDeviceComponent).join("")}
          ${missingChildIds.map(renderDeviceComponentReference).join("")}
        </div>
      ` : ""}
    </section>`;
  }

  function deviceProcesses(record) {
    const attrs = objectOf(record?.attributes);
    const compute = Array.isArray(attrs.compute_processes) ? attrs.compute_processes : [];
    const graphics = Array.isArray(attrs.graphics_processes) ? attrs.graphics_processes : [];
    const seen = new Set();
    return [
      ...compute.map(process => ({ ...process, _kind: process?.type || "计算" })),
      ...graphics.map(process => ({ ...process, _kind: process?.type || "图形" })),
    ].filter(process => {
      if (!process || typeof process !== "object") return false;
      const key = String(process.pid ?? `${process.user || ""}:${process.command || ""}`);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function renderDeviceProcesses(record) {
    const processes = deviceProcesses(record);
    if (!processes.length) return "";
    const processKind = value => ({
      compute: "计算",
      graphics: "图形",
      graphic: "图形",
    }[String(value || "").toLowerCase()] || value);
    return window.PortalI18n.html`<section class="deviceDetailSection deviceDetailWorkloads">
      <header>
        <div><span>实时活动</span><h3>活跃工作负载</h3></div>
        <small>${processes.length} 项上报</small>
      </header>
      <div class="deviceDetailWorkloadTableWrap">
        <table class="deviceDetailWorkloadTable">
          <thead><tr><th>工作负载</th><th>类型</th><th>运行时长</th><th>CPU</th></tr></thead>
          <tbody>
            ${processes.slice(0, 10).map(process => window.PortalI18n.html`
              <tr>
                <td><strong>${esc(process.command || window.PortalI18n.t("进程"))}</strong><small>${esc(
                  [process.user, process.pid == null ? "" : `PID ${process.pid}`].filter(Boolean).join(" · "),
                )}</small></td>
                <td>${esc(processKind(process._kind))}</td>
                <td>${esc(process.elapsed || "—")}</td>
                <td>${esc(numberValue(process.cpu_percent) == null ? "—" : `${Math.round(process.cpu_percent)}%`)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </section>`;
  }

  function deviceSshInfo(record, parent = null, resolution = null) {
    const own = declaredSshOf(record);
    const inherited = parent ? declaredSshOf(parent) : {};
    const resolvedAccessMethods = resolution?.access_methods;
    const hasResolution = Boolean(
      resolution
      && typeof resolution === "object",
    );
    const effective = (
      hasResolution
      && resolvedAccessMethods
      && resolvedAccessMethods.ssh
      && typeof resolvedAccessMethods.ssh === "object"
    ) ? resolvedAccessMethods.ssh : {};
    const fallback = own.host ? own : inherited;
    const source = hasResolution ? effective : fallback;
    const viaResourceId = String(source.via_resource_id || "");
    const inheritedVia = Boolean(
      viaResourceId
      && viaResourceId !== record.resource_id,
    );
    return {
      host: source.host || "",
      user: source.user || "",
      port: source.port || "",
      config: source.config_path || "",
      identityFile: source.identity_file || "",
      inherited: inheritedVia || (!hasResolution && source === inherited && Boolean(source.host)),
      inheritedFrom: (
        viaResourceId === parent?.resource_id
          ? parent?.title || parent?.resource_id
          : viaResourceId
      ) || parent?.title || parent?.resource_id || "",
      credentialSaved: Boolean(source.credential_saved),
    };
  }

  function renderDeviceSshPanel(record, parent = null, resolution = null) {
    const ssh = deviceSshInfo(record, parent, resolution);
    const credentialSaved = ssh.credentialSaved;
    return window.PortalI18n.html`<section class="deviceDetailPanel deviceDetailSsh">
      <header><span>远程访问</span><h3>SSH 连接</h3></header>
      ${ssh.host ? window.PortalI18n.html`
        <dl class="deviceDetailFacts">
          <div><dt>SSH Host</dt><dd class="mono">${esc(ssh.host)}</dd></div>
          ${ssh.user ? window.PortalI18n.html`<div><dt>用户</dt><dd class="mono">${esc(ssh.user)}</dd></div>` : ""}
          <div><dt>端口</dt><dd class="mono">${esc(ssh.port || window.PortalI18n.t("由 SSH config 决定"))}</dd></div>
          ${ssh.config ? window.PortalI18n.html`<div><dt>SSH config</dt><dd class="mono">${esc(ssh.config)}</dd></div>` : ""}
          ${ssh.identityFile ? window.PortalI18n.html`<div><dt>密钥文件</dt><dd class="mono">${esc(ssh.identityFile)}</dd></div>` : ""}
          ${ssh.inherited ? window.PortalI18n.html`<div><dt>连接来源</dt><dd>${esc(window.PortalI18n.text`继承自 ${ssh.inheritedFrom}`)}</dd></div>` : ""}
          <div><dt>SSH 密码</dt><dd data-device-ssh-password-status>${credentialSaved ? window.PortalI18n.t("已保存（仅 host 可读）") : window.PortalI18n.t("未保存")}</dd></div>
        </dl>
        <div class="deviceSshActions">
          <button class="portalButton" type="button" data-device-ssh-test="${esc(record.resource_id)}">测试 SSH 连通性</button>
          ${window.PortalIdentity?.peek()?.access?.allows?.OpenTerminal === true ? window.PortalI18n.html`<button class="portalButton is-primary" type="button" data-device-terminal-open="${esc(record.resource_id)}">打开 Terminal</button>` : ""}
          <span class="deviceSshTestResult" role="status"></span>
        </div>
        <form class="deviceSshPasswordForm" data-device-ssh-password-form="${esc(record.resource_id)}">
          <label>
            <span>${credentialSaved ? window.PortalI18n.t("更新 SSH 密码") : window.PortalI18n.t("保存 SSH 密码")}</span>
            <input name="password" type="password" required autocomplete="new-password" placeholder="密码只写入 Portal host 本地 secret 文件" />
          </label>
          <button class="portalButton" type="submit">${credentialSaved ? window.PortalI18n.t("更新密码") : window.PortalI18n.t("保存密码")}</button>
          ${credentialSaved ? window.PortalI18n.html`<button class="portalButton is-danger" type="button" data-device-ssh-password-delete="${esc(record.resource_id)}">删除密码</button>` : ""}
          <span class="deviceSshPasswordMessage" role="status"></span>
        </form>
      ` : window.PortalI18n.html`
        <div class="deviceSshEmpty">
          <p>该设备尚未配置 SSH 连接。</p>
          <small>可在设备注册时填写 SSH Host，或由父设备提供连接。</small>
        </div>
      `}
    </section>`;
  }

  function renderDeviceDetail(record, resolution = null, warning = "") {
    const resolvedDevice = resolution?.record && typeof resolution.record === "object"
      ? resolution.record
      : {};
    const observation = resolution?.observation && typeof resolution.observation === "object"
      ? resolution.observation
      : {};
    const hasResolution = Boolean(resolution && typeof resolution === "object");
    const detailRecord = hasResolution ? {
      resource_id: String(resolution.device_id || record.resource_id || ""),
      resource_type: "device",
      title: String(
        resolvedDevice.name
        || resolution.device_id
        || record.resource_id
        || "",
      ),
      summary: String(
        resolvedDevice.description
        || resolvedDevice.model
        || "",
      ),
      source: {
        adapter: "device",
        modified_at: String(
          observation.updated_at
          || resolvedDevice.updated_at
          || "",
        ),
      },
      attributes: {
        ...resolvedDevice,
        ...observation,
      },
    } : record;
    const attrs = objectOf(detailRecord?.attributes);
    const source = objectOf(detailRecord?.source);
    const parent = state.records.find(item => item.resource_id === String(attrs.parent_device_id || ""));
    const projects = projectIds(detailRecord);
    const observedAt = deviceObservedAt(detailRecord);
    const observedLabel = deviceObservationLabel(detailRecord);
    const stale = deviceObservationStale(detailRecord);
    const network = resolvedDevice.network && typeof resolvedDevice.network === "object"
      ? resolvedDevice.network
      : {};
    const context = [
      parent ? window.PortalI18n.text`属于 ${parent.title || parent.resource_id}` : "",
      attrs.hostname,
      attrs.location,
    ].filter(Boolean).join(" · ");
    const metrics = deviceDetailMetrics(detailRecord);

    renderResourceDetailIdentity(detailRecord, "device");
    destroyDetailWorkbench();
    elements.detailBody.innerHTML = window.PortalI18n.html`
      <section data-portal-detail-panel="overview" data-panel-type="document" data-panel-title="设备概览" data-panel-icon="device">
      <div class="deviceDetailWorkspace">
      <section class="deviceDetailHero">
        <div class="deviceDetailHeroMain">
          <div class="deviceDetailHeroBadges">
            ${renderDeviceStatus(detailRecord)}
            <span class="deviceDetailKind">${esc(deviceKindLabel(detailRecord))}</span>
            <span class="deviceDetailFreshness ${stale ? "stale" : ""}">
              ${esc(stale ? window.PortalI18n.t("观测已过期") : window.PortalI18n.t("当前观测"))}
            </span>
          </div>
          <p>${esc(detailRecord.summary || attrs.description || attrs.model || window.PortalI18n.t("已注册设备"))}</p>
          ${context ? window.PortalI18n.html`<div class="deviceDetailHeroMeta"><small>${esc(context)}</small></div>` : ""}
        </div>
        <div class="deviceDetailObserved">
          <span>最近观测</span>
          <strong>${esc(observedLabel)}</strong>
          <small>${esc(formatDateTime(observedAt) || window.PortalI18n.t("未上报时间"))}</small>
        </div>
      </section>

      ${metrics.length ? window.PortalI18n.html`
        <section class="deviceDetailMetrics" aria-label="设备当前指标">
          ${metrics.map(renderDeviceDetailMetric).join("")}
        </section>
      ` : ""}

      <div class="deviceDetailInfoGrid">
        ${renderDeviceFactsPanel(window.PortalI18n.t("设备上下文"), window.PortalI18n.t("概览"), [
          [window.PortalI18n.t("设备类型"), deviceKindLabel(detailRecord)],
          [window.PortalI18n.t("型号"), attrs.model],
          [window.PortalI18n.t("主机"), attrs.hostname || attrs.host],
          [window.PortalI18n.t("IP 地址"), attrs.ip || network.primary_ip, true],
          [window.PortalI18n.t("位置"), attrs.location],
          [window.PortalI18n.t("所属 Project"), projects],
        ])}
        ${renderDeviceFactsPanel(window.PortalI18n.t("资源身份"), window.PortalI18n.t("注册信息"), [
          [window.PortalI18n.t("统一 ID"), detailRecord.resource_id, true],
          [window.PortalI18n.t("GPU 索引"), attrs.gpu_index],
          [window.PortalI18n.t("GPU UUID"), attrs.gpu_uuid || attrs.uuid, true],
          [window.PortalI18n.t("生命周期"), attrs.lifecycle],
          [window.PortalI18n.t("来源适配器"), source.adapter],
          [window.PortalI18n.t("修改时间"), formatDateTime(source.modified_at || attrs.updated_at)],
        ])}
      </div>
      </div>
      </section>
      <section data-portal-detail-panel="access" data-panel-type="terminal" data-panel-title="连接" data-panel-icon="terminal">
        <div class="deviceDetailWorkspace">${renderDeviceSshPanel(detailRecord, parent, resolution)}</div>
      </section>
      <section data-portal-detail-panel="operations" data-panel-type="preview" data-panel-title="运行状态" data-panel-icon="activity">
      <div class="deviceDetailWorkspace">
      ${renderDeviceTopology(detailRecord)}
      ${renderDeviceProcesses(detailRecord)}
      ${warning ? window.PortalI18n.html`<section class="deviceDetailSection"><div class="resourceError">${esc(warning)}</div></section>` : ""}
      </div>
      </section>`;
    mountDetailWorkbench({
      id: "resource-device-detail",
      label: window.PortalI18n.t("设备详情"),
      weights: [1.2, 0.85, 1.15],
    });
  }

  function renderDeviceDetailLoading(record) {
    destroyDetailWorkbench();
    renderResourceDetailIdentity(record, "device");
    elements.detailBody.innerHTML = resourceStateHtml(window.PortalI18n.t("正在读取设备详情。"), {
      state: "loading",
      density: "compact",
    });
  }

  function destroyDetailWorkbench() {
    window.PortalDetailWorkbench?.destroy(elements.detailBody);
  }

  function mountDetailWorkbench(options = {}) {
    if (!window.PortalDetailWorkbench) return null;
    return window.PortalDetailWorkbench.mount({
      root: elements.detailBody,
      toolbarHost: "#resourceDetailWorkbenchToolbar",
      ...options,
    });
  }

  const detailRefreshRevisions = new Map();
  async function refreshDetailCard(snapshot) {
    const id = snapshot.resourceId;
    const revision = (detailRefreshRevisions.get(id) || 0) + 1;
    detailRefreshRevisions.set(id, revision);
    const sequence = state.requestSequence;
    try {
      const record = await getJson(`/api/resource-index/resources/${encodeURIComponent(id)}`);
      if (sequence !== state.requestSequence || detailRefreshRevisions.get(id) !== revision) return;
      state.records = state.records.map(item => item.resource_id === id ? record : item);
      if (state.lastPayload) state.lastPayload.resources = state.records;
      const byId = new Map(state.records.map(item => [item.resource_id, item]));
      elements.results.querySelectorAll(`[data-resource-open="${CSS.escape(id)}"]`).forEach(node => {
        const markup = node.classList.contains("deviceRosterCard") ? renderDeviceRosterCard(record, byId)
          : node.classList.contains("deviceTelemetryCard") ? renderTelemetryDevice(record)
          : node.classList.contains("deviceCapacityRow") ? renderServerCapacity(record, state.records)
          : renderCard(record);
        const template = document.createElement("template");
        template.innerHTML = markup;
        const replacement = template.content.firstElementChild;
        const focused = node.contains(document.activeElement);
        node.replaceWith(replacement);
        if (focused) replacement.focus({ preventScroll: true });
      });
    } catch (error) { showToast(error.message || error, "error"); }
  }

  function removeDetailCard(snapshot) {
    const id = snapshot.resourceId;
    detailRefreshRevisions.set(id, (detailRefreshRevisions.get(id) || 0) + 1);
    const existed = state.records.some(item => item.resource_id === id);
    state.records = state.records.filter(item => item.resource_id !== id);
    if (state.lastPayload) {
      state.lastPayload.resources = state.records;
      if (existed) state.lastPayload.total = Math.max(0, Number(state.lastPayload.total || state.records.length + 1) - 1);
    }
    elements.results.querySelectorAll(`[data-resource-open="${CSS.escape(id)}"]`).forEach(node => node.remove());
    elements.count.textContent = state.category === "device"
      ? window.PortalI18n.text`${state.records.length} 个设备`
      : window.PortalI18n.text`${state.records.length} 项`;
    if (frameDetailHost.snapshot().resourceId === id) frameDetailHost.close();
    if (localDetailHost.snapshot().resourceId === id) localDetailHost.close({ force: true });
  }

  function initDetailHosts() {
    frameDetailHost = window.PortalResourceDetail.createFrameHost({
      overlay: elements.frameDetail,
      frame: elements.frameDetailFrame,
      loading: elements.frameDetailLoading,
      title: resource => `${CATEGORIES[resource.detailKind]?.label || window.PortalI18n.t("Resource")} detail`,
      loadingText: resource => window.PortalI18n.text`正在打开 ${CATEGORIES[resource.detailKind]?.label || window.PortalI18n.t("Resource")}…`,
      updateLocation: resourceId => {
        if (resourceId) detailHistory.open(resourceId);
        else if (queryState().item) detailHistory.close();
      },
      onStateChange: (snapshot, reason) => {
        if (reason !== "loading") return;
        state.selectedId = snapshot.resourceId;
        state.detailSequence += 1;
        detailHistory.acceptCurrent();
      },
      onChanged: refreshDetailCard,
      onDeleted: removeDetailCard,
      onClose: () => {
        destroyDetailWorkbench();
        state.selectedId = "";
        state.detailSequence += 1;
      },
      onCloseTimeout: () => {
        detailHistory.restoreAccepted();
        window.PortalUI.toast(window.PortalI18n.t("资源详情暂未响应关闭请求，请稍后重试。"), "error");
      },
      onCloseBlocked: () => {
        detailHistory.restoreAccepted();
      },
    });
    localDetailHost = window.PortalResourceDetail.createLocalHost({
      modal: elements.detail,
      initialFocus: elements.detailPanel,
      updateLocation: resourceId => {
        if (resourceId) detailHistory.open(resourceId);
        else if (queryState().item) detailHistory.close();
      },
      onLoading: resource => {
        state.selectedId = resource.resourceId;
        state.detailSequence += 1;
        setFilterOpen(false);
        renderDeviceDetailLoading(resource.payload);
      },
      load: async resource => {
        const resolutionResult = await getJson(
          `/api/resources/resolve?address=${encodeURIComponent(resource.resourceId)}`,
        ).catch(error => ({ __error: error }));
        return {
          record: resource.payload,
          resolution: resolutionResult?.__error ? null : resolutionResult,
          warning: resolutionResult?.__error?.message || String(resolutionResult?.__error || ""),
        };
      },
      present: payload => {
        renderDeviceDetail(payload.record, payload.resolution, payload.warning);
      },
      onError: (error, resource) => {
        renderDeviceDetail(resource.payload, null, error.message || String(error));
      },
      onChanged: refreshDetailCard,
      onDeleted: removeDetailCard,
      onClose: () => {
        destroyDetailWorkbench();
        state.selectedId = "";
        state.detailSequence += 1;
      },
    });
  }

  function closeDetail({ updateUrl = true } = {}) {
    if (SHARED_DETAIL_CATEGORIES.has(state.category) && frameDetailHost?.snapshot().status !== "empty") {
      closeFrameDetail({ updateLocation: updateUrl });
      return;
    }
    localDetailHost?.close({ updateLocation: updateUrl });
  }

  async function openDetail(record, options = {}) {
    if (!record) return;
    const id = String(record.resource_id || "");
    if (SHARED_DETAIL_CATEGORIES.has(state.category)) {
      openFrameDetail(id, options);
      return;
    }
    return localDetailHost.open({
      resourceId: id,
      resourceType: state.category,
      detailKind: state.category,
      payload: record,
    }, {
      updateLocation: options.updateLocation !== false,
    });
  }

  async function refreshSelectedDeviceDetail() {
    if (state.category !== "device" || !state.selectedId) return;
    const id = state.selectedId;
    const detailSequence = state.detailSequence;
    const record = state.records.find(item => item.resource_id === id);
    if (!record) return;
    const resolution = await getJson(
      `/api/resources/resolve?address=${encodeURIComponent(id)}`,
    );
    if (detailSequence !== state.detailSequence || state.selectedId !== id) return;
    renderDeviceDetail(record, resolution);
  }

  async function openInitialDetail(id, options = {}) {
    let record = state.records.find(item => item.resource_id === id);
    if (!record) {
      try {
        const candidate = await getJson(`/api/resource-index/resources/${encodeURIComponent(id)}`);
        if (candidate.resource_type === state.category) record = candidate;
      } catch (_) {
        return;
      }
    }
    if (record) await openDetail(record, {
      updateLocation: options.updateLocation === true,
    });
  }

  function closeDeviceRegisterModal() {
    deviceRegisterDialog.close();
  }

  function openDeviceRegisterModal() {
    if (state.category !== "device") return;
    setFilterOpen(false);
    deviceRegisterDialog.open();
  }

  async function registerDevice(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const submit = form.querySelector('button[type="submit"]');
    return runCatalogMutation({
      key: "resource:device:register",
      button: submit,
      busyLabel: "注册中…",
      action: "注册设备",
      run: async () => {
      const ssh = {
        host: String(data.get("ssh_host") || "").trim(),
        user: String(data.get("ssh_user") || "").trim(),
        port: String(data.get("ssh_port") || "").trim(),
        config_path: String(data.get("ssh_config") || "").trim(),
        identity_file: String(data.get("identity_file") || "").trim(),
      };
      if (ssh.port) ssh.port = Number(ssh.port);
      const password = String(data.get("ssh_password") || "");
      if (password && !ssh.host) {
        throw new Error("保存 SSH 密码前需要填写 SSH Host。");
      }
      const result = await window.PortalResourceRegistrationService.register({
        resource_type: "device",
        name: String(data.get("name") || "").trim(),
        device_kind: String(data.get("device_kind") || "device").trim(),
        description: String(data.get("description") || "").trim(),
        hostname: String(data.get("hostname") || "").trim(),
        model: String(data.get("model") || "").trim(),
        primary_ip: String(data.get("primary_ip") || "").trim(),
        location: String(data.get("location") || "").trim(),
        access_methods: ssh.host ? { ssh } : {},
      });
      const deviceId = String(result.resource_id || "");
      let credentialError = null;
      if (password) {
        try {
          await runResourceAction(
            deviceId,
            "ssh.credential.save",
            { password },
          );
        } catch (error) {
          credentialError = error;
        }
      }
      return { deviceId, credentialError };
      },
      onSuccess: async ({ deviceId, credentialError }) => {
        form.reset();
        closeDeviceRegisterModal();
        detailHistory.open(deviceId, { q: "", project: "" });
        await loadResources();
        showToast(
          credentialError
            ? window.PortalI18n.text`设备已注册，但 SSH 密码保存失败：${credentialError.message || credentialError}`
            : window.PortalI18n.t("设备已注册。"),
          credentialError ? "error" : "success",
        );
      },
    });
  }

  async function testDeviceSsh(deviceId, button) {
    const resultNode = button?.parentElement?.querySelector(".deviceSshTestResult");
    if (resultNode) {
      resultNode.className = "deviceSshTestResult testing";
      resultNode.textContent = window.PortalI18n.t("正在测试…");
    }
    return runCatalogMutation({
      key: `resource:${deviceId}:ssh-test`,
      button,
      busyLabel: "测试中…",
      action: "测试 SSH",
      notifyError: false,
      retry: false,
      run: () => runResourceAction(
        deviceId,
        "ssh.test",
        { timeout_seconds: 5 },
      ),
      onSuccess: result => {
        if (resultNode) {
          resultNode.className = `deviceSshTestResult ${result.reachable ? "success" : "error"}`;
          resultNode.textContent = result.reachable
            ? window.PortalI18n.text`连接成功 · ${result.latency_ms} ms`
            : window.PortalI18n.text`连接失败 · ${result.message || window.PortalI18n.t("未知错误")}`;
        }
      },
      onError: error => {
        if (resultNode) {
          resultNode.className = "deviceSshTestResult error";
          resultNode.textContent = window.PortalI18n.text`测试失败 · ${error.message || error}`;
        }
      },
    });
  }

  async function saveDeviceSshPassword(form) {
    const deviceId = String(form.dataset.deviceSshPasswordForm || "");
    const input = form.elements.password;
    const message = form.querySelector(".deviceSshPasswordMessage");
    const submit = form.querySelector('button[type="submit"]');
    if (message) {
      message.className = "deviceSshPasswordMessage";
      message.textContent = "";
    }
    return runCatalogMutation({
      key: `resource:${deviceId}:ssh-password-save`,
      button: submit,
      busyLabel: "保存中…",
      action: "保存 SSH 密码",
      notifyError: false,
      retry: false,
      run: () => runResourceAction(
        deviceId,
        "ssh.credential.save",
        { password: String(input.value || "") },
      ),
      onSuccess: async () => {
        input.value = "";
        await refreshSelectedDeviceDetail().catch(() => {});
        localDetailHost.markChanged({ operation: "ssh.credential.save" });
      },
      onError: error => {
        if (message) {
          message.className = "deviceSshPasswordMessage error";
          message.textContent = error.message || error;
        }
      },
      successMessage: "SSH 密码已保存。",
    });
  }

  async function deleteDeviceSshPassword(deviceId, button) {
    return runCatalogMutation({
      key: `resource:${deviceId}:ssh-password-delete`,
      button,
      busyLabel: "删除中…",
      action: "删除 SSH 密码",
      run: () => runResourceAction(deviceId, "ssh.credential.delete"),
      onSuccess: async () => {
        await refreshSelectedDeviceDetail().catch(() => {});
        localDetailHost.markChanged({ operation: "ssh.credential.delete" });
      },
      successMessage: "SSH 密码已删除。",
    });
  }

  function ensureDeviceTerminal() {
    if (state.terminalController) return state.terminalController;
    state.terminalController = window.PortalTerminal.create({
      mount: elements.deviceTerminalMount,
      apiGet: getJson,
      apiPost: postJson,
      startUrl: "/api/terminal/start",
      startPayload: () => ({ device_id: state.terminalDeviceId }),
      isActive: () => !elements.deviceTerminalOverlay.hidden,
      TerminalClass: window.Terminal,
      FitAddonClass: window.FitAddon?.FitAddon,
      messages: {
        starting: "正在建立 SSH 终端…",
        startFailed: "SSH 终端启动失败",
      },
    });
    return state.terminalController;
  }

  async function openDeviceTerminal(deviceId) {
    const record = state.records.find(item => item.resource_id === deviceId);
    if (state.terminalDeviceId && state.terminalDeviceId !== deviceId && state.terminalController) {
      await state.terminalController.reset();
    }
    state.terminalDeviceId = deviceId;
    elements.deviceTerminalTitle.textContent = `${record?.title || deviceId} · Terminal`;
    deviceTerminalDialog.open();
    const terminal = ensureDeviceTerminal();
    window.requestAnimationFrame(() => {
      terminal.fit();
      terminal.start();
    });
  }

  async function closeDeviceTerminal() {
    deviceTerminalDialog.close();
    if (state.terminalController) await state.terminalController.close().catch(() => {});
  }

  elements.results.addEventListener("click", event => {
    const projectToggle = event.target.closest("[data-resource-project-toggle]");
    if (projectToggle) {
      const project = projectToggle.closest(".portalProjectGroup");
      const collapsed = project.classList.toggle("collapsed");
      projectToggle.setAttribute("aria-expanded", String(!collapsed));
      projectToggle.setAttribute("aria-label", `${collapsed ? window.PortalI18n.t("展开") : window.PortalI18n.t("折叠")} ${project.dataset.resourceProject || window.PortalI18n.t("Project")}`);
      return;
    }
    const open = event.target.closest("[data-resource-open]");
    if (open) {
      const record = state.records.find(item => item.resource_id === open.dataset.resourceOpen);
      openDetail(record).catch(error => console.error(error));
    }
  });

  elements.results.addEventListener("keydown", event => {
    if (!['Enter', ' '].includes(event.key)) return;
    const open = event.target.closest("[data-resource-open]");
    if (!open) return;
    event.preventDefault();
    const record = state.records.find(item => item.resource_id === open.dataset.resourceOpen);
    openDetail(record).catch(error => console.error(error));
  });

  elements.detailBody.addEventListener("click", event => {
    const relatedDevice = event.target.closest("[data-device-detail-open]");
    if (relatedDevice) {
      openInitialDetail(String(relatedDevice.dataset.deviceDetailOpen || ""), {
        updateLocation: true,
      })
        .catch(error => showToast(error.message || error, "error"));
      return;
    }
    const sshTest = event.target.closest("[data-device-ssh-test]");
    if (sshTest) {
      testDeviceSsh(String(sshTest.dataset.deviceSshTest || ""), sshTest)
        .catch(error => showToast(error.message || error, "error"));
      return;
    }
    const terminalOpen = event.target.closest("[data-device-terminal-open]");
    if (terminalOpen) {
      openDeviceTerminal(String(terminalOpen.dataset.deviceTerminalOpen || ""))
        .catch(error => showToast(error.message || error, "error"));
      return;
    }
    const passwordDelete = event.target.closest("[data-device-ssh-password-delete]");
    if (passwordDelete) {
      deleteDeviceSshPassword(
        String(passwordDelete.dataset.deviceSshPasswordDelete || ""),
        passwordDelete,
      ).catch(error => showToast(error.message || error, "error"));
      return;
    }
  });
  elements.detailBody.addEventListener("submit", event => {
    const sshPasswordForm = event.target.closest("[data-device-ssh-password-form]");
    if (sshPasswordForm) {
      event.preventDefault();
      saveDeviceSshPassword(sshPasswordForm)
        .catch(error => showToast(error.message || error, "error"));
      return;
    }
  });

  elements.detailClose.addEventListener("click", () => closeDetail());

  function setFilterOpen(open) {
    const next = Boolean(open);
    if (next) projectFilterDropdown?.open();
    else projectFilterDropdown?.close();
    elements.filterToggle.classList.toggle("active", next || Boolean(queryState().project));
  }

  elements.query.addEventListener("input", () => {
    if (state.semanticSearch?.activeId()) {
      state.semanticSearch.clearActive({ notify: false });
      writeQuery({ semantic: "" });
      if (state.lastPayload) renderResults(state.lastPayload);
    }
    window.clearTimeout(state.queryTimer);
    cancelResourceQuery();
    state.queryTimer = window.setTimeout(() => {
      closeDetail({ updateUrl: false });
      writeQuery({ q: elements.query.value, item: "" });
      loadResources({ keepDetail: false });
    }, 220);
  });

  projectFilterDropdown = window.PortalFormControls.bindFilterDropdown({
    button: elements.filterToggle,
    panel: elements.filterPanel,
    onToggle: open => {
      elements.filterToggle.classList.toggle("active", open || Boolean(queryState().project));
    },
  });
  const projectFilterSummary = window.PortalFormControls?.bindFilterSummary?.({
    target: elements.filterSummary,
    items: () => {
      const project = queryState().project;
      return project ? [{ label: window.PortalI18n.t("Project"), value: project }] : [];
    },
    onClear: () => {
      closeDetail({ updateUrl: false });
      writeQuery({ project: "", item: "" });
      setFilterOpen(false);
      return loadResources({ keepDetail: false });
    },
  });
  elements.projectOptions.addEventListener("change", event => {
    const input = event.target.closest('input[name="resourceProject"]');
    if (!input) return;
    closeDetail({ updateUrl: false });
    writeQuery({ project: input.value, item: "" });
    setFilterOpen(false);
    loadResources({ keepDetail: false });
  });
  elements.reload.addEventListener("click", () => loadResources());
  elements.deviceRegisterOpen.addEventListener("click", openDeviceRegisterModal);
  elements.deviceRegisterClose.addEventListener("click", closeDeviceRegisterModal);
  elements.deviceRegisterCancel.addEventListener("click", closeDeviceRegisterModal);
  elements.deviceRegisterForm.addEventListener("submit", registerDevice);
  elements.deviceTerminalClose.addEventListener("click", () => {
    closeDeviceTerminal().catch(error => showToast(error.message || error, "error"));
  });
  elements.deviceTerminalInterrupt.addEventListener("click", () => {
    ensureDeviceTerminal().control("interrupt")
      .catch(error => showToast(error.message || error, "error"));
  });
  elements.deviceTerminalRestart.addEventListener("click", () => {
    ensureDeviceTerminal().control("restart")
      .catch(error => showToast(error.message || error, "error"));
  });
  window.addEventListener("popstate", () => {
    window.clearTimeout(state.queryTimer);
    if (currentCategory() !== state.category) {
      window.location.reload();
      return;
    }
    const current = queryState();
    state.semanticSearch?.setActiveId(current.semantic, { notify: false });
    elements.query.value = current.q;
    if (current.q !== state.collectionQuery?.q || current.project !== state.collectionQuery?.project) {
      void loadResources();
      return;
    }
    if (current.item) {
      openInitialDetail(current.item, { updateLocation: false })
        .catch(error => showToast(error.message || error, "error"));
      return;
    }
    closeDetail({ updateUrl: false });
  });

  initDetailHosts();
  configurePage();
  initializeSemanticSearch();
  window.PortalUI?.loadCurrentUser?.().then(user => {
    const username = document.getElementById("portalUsername");
    if (username && !username.textContent) username.textContent = user?.username || "";
  }).catch(() => {});
  void resourceServiceRecovery.run();
  state.semanticSearch?.load().catch(error => {
    showToast(window.PortalUI?.errorMessage?.(window.PortalI18n.t("加载语义检索"), error) || error, "error");
  });
})();
