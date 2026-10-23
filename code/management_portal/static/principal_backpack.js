(() => {
  "use strict";
  if (window.PortalPrincipalBackpack) return;
  let active = null;
  function create({ mode = "details", container = null, managementView = "entries" } = {}) {
  // Permission schemes are their own page (portal_permission_schemes.js).
  if (managementView !== "entries") throw new TypeError("Unknown permission management view");
  const lifetime = new AbortController();
  let titleController = new AbortController();
  const titleCache = new Map();
  let entryCard = null;
  let releasePrincipalEvents = null;
  const root = document.createElement("div");
  root.className = container ? "backpackEmbedded" : "portalDetailOverlay backpackOverlay";
  root.dataset.backpackMode = mode;
  root.hidden = true;
  if (!container) root.dataset.portalDetailOverlay = "";
  root.innerHTML = window.PortalI18n.html`<section class="portalDetailSurface backpackSurface" ${container ? "" : "data-portal-detail-surface"} aria-label="${mode === "management" ? window.PortalI18n.t("Permission Management") : container ? window.PortalI18n.t("Package") : window.PortalI18n.t("Menu")}">
    <header class="portalDetailHeader backpackHeader"><div><h2>${mode === "management" ? window.PortalI18n.t("Permission Management") : window.PortalI18n.t("Menu")}</h2><p class="backpackSubtitle">角色档案与持有物</p></div><button type="button" class="portalDetailClose" data-portal-detail-close aria-label="关闭 ${mode === "management" ? window.PortalI18n.t("Permission Management") : window.PortalI18n.t("Menu")}" data-portal-icon="close"></button></header>
    <div class="portalDetailBody backpackBody backpackDetailBody"><button class="portalButton quiet" type="button" id="backpackRetry" hidden>重试读取</button><p id="backpackStatus" role="status" aria-live="polite">正在读取角色档案…</p>
    <div class="backpackLayout">
      <aside class="portalCard backpackPanel backpackRoster"><h2>角色名册</h2><form id="rosterSearch"><label for="principalSearch">名称或身份 ID</label><div class="backpackSearch"><input id="principalSearch" maxlength="160" placeholder="寻找 User / Resident" /><button class="portalButton" type="submit">查找</button></div></form><div id="principalRoster"></div><div class="backpackPager"><button class="portalButton" id="previousPage" type="button" disabled>上一页</button><span id="pageIndicator"></span><button class="portalButton" id="nextPage" type="button" disabled>下一页</button></div></aside>
      <section id="principalDetail" class="portalCard backpackPanel" aria-label="角色档案"><p class="backpackEmpty">选择一位角色，查看 Package。</p></section>
    </div>
  </div>
  </section>`;
  (container || document.body).append(root);
  root.querySelector(".backpackRoster").hidden = true;
  if (container) root.querySelector(".backpackHeader").hidden = true;
  function decorateControls() {
    root.querySelectorAll(".packageItem:not([data-package-ready])").forEach(item => {
      item.dataset.packageReady = "true";
      item.addEventListener("pointerenter", () => loadResourceTitle(item), { signal: lifetime.signal });
      item.addEventListener("focus", () => loadResourceTitle(item), { signal: lifetime.signal });
      item.addEventListener("click", () => {
        loadResourceTitle(item);
        if (item.hasAttribute("data-entry-id")) openEntryCard(item);
        else window.PortalButtonTooltips.show(item, { immediate: true });
      }, { signal: lifetime.signal });
    });
    root.querySelectorAll("button").forEach(button => {
      if (!button.matches("[data-portal-detail-close]")) button.classList.add("portalButton");
    });
    root.querySelectorAll("input, select, textarea").forEach(input => {
      if (input.closest(".portalField")) return;
      const field = document.createElement("div");
      field.className = "portalField";
      input.before(field);
      field.append(input);
    });
  }
  decorateControls();
  const surface = container ? null : window.PortalDetailSurface.mount(root.firstElementChild);
  const dialog = container ? null : window.PortalUI.createDialogController({
    modal: root, backdropClose: true, escapeClose: true,
    initialFocus: () => root.querySelector("[data-portal-detail-close]"),
    onClose: () => queueMicrotask(destroy),
  });
  root.querySelector("[data-portal-detail-close]").addEventListener("click", () => dialog?.close());
  function destroy() {
    if (lifetime.signal.aborted) return;
    lifetime.abort();
    releasePrincipalEvents?.();
    releasePrincipalEvents = null;
    closeEntryCard();
    window.PortalButtonTooltips.hide();
    titleController.abort(); titleCache.clear();
    state.detailRequest += 1; state.catalogRequest += 1;
    dialog?.destroy(); surface?.destroy(); root.remove();
    if (active?.root === root) active = null;
  }
  const $ = id => root.querySelector(`#${id}`);
  const escape = value => window.PortalUIPrimitives.escapeHtml(value);
  // A User shows its category; a Resident shows its place in the Principal hierarchy.
  const names = {
    user: { portal_admin: "Portal 管理员", member: "一般用户", intern: "实习用户" },
    resident: { user: "个人 Resident", project: "Project Leader", lineage: "Lineage Leader", resident: "Lineage 成员", unassigned: "层级尚未登记" },
  };
  const levelName = row => names[row.kind]?.[row.level] ? window.PortalI18n.t(names[row.kind][row.level]) : row.level;
  const state = { offset: 0, query: "", principal: "", catalog: null, detailRequest: 0, catalogRequest: 0, requestedPrincipal: "", detailReady: false, permissionRequest: 0, permissionRows: null, possessions: [], packageCategory: "", packageCategoryExplicit: false };
  const userAvatarUpdates = new Map();
  let detailPrincipal = null;
  const renderPortrait = row => {
    const avatar = row.kind === "user" && userAvatarUpdates.has(row.id) ? userAvatarUpdates.get(row.id) : row.avatar;
    $("principalDetail").querySelector("[data-backpack-avatar]").innerHTML = (row.kind === "resident" ? window.PortalResidentAvatar : window.PortalUserAvatar).markup(avatar, { label: row.title, size: 80 });
  };
  window.addEventListener("portal:user-avatar-changed", event => {
    const { username, avatar } = event.detail || {};
    if (typeof username !== "string" || username !== state.requestedPrincipal || !avatar) return;
    userAvatarUpdates.set(username, avatar);
    if (state.detailReady && detailPrincipal?.kind === "user" && detailPrincipal.id === username) renderPortrait(detailPrincipal);
  }, { signal: lifetime.signal });
  function setResourceTitle(item, title) {
    item.dataset.packageTitle = title;
    if (entryCard?.item === item) entryCard.title.textContent = title;
    const description = title + item.dataset.titleSuffix;
    item.dataset.portalTooltip = description;
    item.setAttribute("aria-label", description);
  }
  function resetResourceTitles() {
    closeEntryCard();
    window.PortalButtonTooltips.hide();
    titleController.abort();
    titleController = new AbortController();
    titleCache.clear();
    root.querySelectorAll("[data-title-address]").forEach(item => {
      delete item.dataset.titleRequested;
      setResourceTitle(item, item.dataset.titleAddress);
    });
  }
  async function loadResourceTitle(item) {
    const address = item.dataset.titleAddress;
    if (!address || item.dataset.titleRequested || lifetime.signal.aborted) return;
    item.dataset.titleRequested = "true";
    const controller = titleController;
    if (!titleCache.has(address)) {
      const pending = window.PortalAPI.request(`/api/resources/reference?address=${encodeURIComponent(address)}`, { signal: controller.signal, cache: "no-store" })
        .then(result => result.resource_id === address && typeof result.title === "string" && result.title.trim() ? result.title : null)
        .catch(() => null);
      titleCache.set(address, pending);
    }
    const title = await titleCache.get(address);
    if (!title || controller !== titleController || controller.signal.aborted || lifetime.signal.aborted || !root.contains(item)) return;
    setResourceTitle(item, title);
  }
  function closeEntryCard() {
    if (!entryCard) return;
    const card = entryCard;
    entryCard = null;
    card.dialog.close({ force: true });
    card.dispose();
  }
  function openEntryCard(item) {
    const entry = state.permissionRows?.find(row => row.entry_id === item.dataset.entryId);
    if (!entry || lifetime.signal.aborted) return;
    closeEntryCard();
    window.PortalButtonTooltips.hide();
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay entryDetailOverlay";
    overlay.dataset.portalDetailOverlay = "";
    overlay.hidden = true;
    overlay.innerHTML = window.PortalI18n.html`<section class="portalDetailSurface entryDetailSurface" data-portal-detail-surface aria-label="权限表项"><header class="portalDetailHeader"><h2>权限表项</h2><button type="button" data-portal-detail-close data-portal-icon="close" aria-label="关闭权限表项"></button></header><div class="portalDetailBody"><dl class="entryDetailFields"></dl></div></section>`;
    const fields = overlay.querySelector("dl");
    const addField = (label, value) => {
      const term = document.createElement("dt"), description = document.createElement("dd");
      term.textContent = window.PortalI18n.t(label);
      description.textContent = String(value ?? window.PortalI18n.t("未提供"));
      fields.append(term, description);
      return description;
    };
    const title = addField("资源名称", item.dataset.packageTitle);
    addField("资源 ID", entry.resource_id);
    addField("A 节点", entry.a_title ? `${entry.a_title}（${entry.a}）` : entry.a);
    addField("效果", entry.effect === "deny" ? "deny" : "permit");
    addField("来源", window.PortalI18n.t(sourceLabel(entry)));
    addField("主体节点", entry.p || state.requestedPrincipal);
    if (entry.issued_by) addField("签发者", entry.issued_by);
    if (entry.expires_at) addField("到期时间", new Date(entry.expires_at * 1000).toLocaleString());
    // Lent with a message: removed when the recipient finishes that input.
    if (entry.message_id) addField("随消息借出", entry.message_id);
    addField("表项 ID", entry.entry_id);
    document.body.append(overlay);
    const surface = window.PortalDetailSurface.mount(overlay.firstElementChild);
    const close = overlay.querySelector("[data-portal-detail-close]");
    const card = { item, title, dispose() {
      if (!overlay.isConnected) return;
      card.dialog.destroy(); surface.destroy(); overlay.remove();
    } };
    card.dialog = window.PortalUI.createDialogController({
      modal: overlay, backdropClose: true, escapeClose: true, initialFocus: () => close,
      onClose: () => {
        if (entryCard === card) entryCard = null;
        queueMicrotask(() => card.dispose());
      },
    });
    close.addEventListener("click", () => card.dialog.close());
    entryCard = card;
    card.dialog.open();
  }
  // Instance rows record no origin (decision_b820faf42218); only temporary credentials carry an issuer and expiry.
  const sourceLabel = entry => entry.expires_at || entry.issued_by || entry.message_id ? "临时凭证" : "权限表项";
  // Titles of the active scheme's class nodes per tree, read once per scheme; object IDs have none.
  let schemeTitles = null;
  function readSchemeTitles(schemeId) {
    if (schemeTitles?.schemeId !== schemeId) {
      const titles = window.PortalInstructionClient.execute("GetPermissionScheme", { scheme_id: schemeId })
        .then(result => Object.fromEntries(Object.entries(result.rules?.trees || {}).map(([tree, spec]) =>
          [tree, new Map((spec.nodes || []).filter(node => node.title).map(node => [node.id, node.title]))])))
        .catch(() => ({}));
      schemeTitles = { schemeId, titles };
    }
    return schemeTitles.titles;
  }
  const isClassNode = id => String(id).startsWith("@");
  function packageItems(items, permissions, grouped = true) {
    const groups = new Map();
    for (const item of items) {
      const type = item.resource_type || item.kind || "other";
      const group = ["project", "lineage"].includes(type) ? "Project / Lineage" : type === "device" ? "Device" : type;
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push({ ...item, type });
    }
    const groupRank = key => key === "Project / Lineage" ? 0 : key === "Device" ? 1 : 2;
    const content = [...groups.entries()].sort(([a], [b]) => groupRank(a) - groupRank(b) || a.localeCompare(b)).map(([name, rows]) => { const cards = rows.sort((a, b) => String(a.resource_id || a.id).localeCompare(String(b.resource_id || b.id))).map(item => {
      const id = item.resource_id || item.id;
      const title = item.resource_title || item.title || id;
      const access = permissions ? item.a_title || item.a : window.PortalI18n.t("关联资源");
      const icon = ({ project: "folder", lineage: "network", device: "settings", resident: "user", workspace: "folder", paper: "file-text" })[item.type] || "archive";
      const suffix = permissions
        ? ` · ${item.type} · ${access}${item.effect === "deny" ? " · deny" : ""} · ${window.PortalI18n.t(sourceLabel(item))}`
        : window.PortalI18n.text` · ${item.type} · ${id} · ${access} · 来源：${item.source || window.PortalI18n.t("已登记关联")}`;
      const description = title + suffix;
      const titleAttributes = permissions && !isClassNode(id) && !item.resource_title && !item.title ? ` data-title-address="${escape(id)}" data-title-suffix="${escape(suffix)}"` : "";
      return window.PortalI18n.html`<button type="button" class="packageItem" data-package-title="${escape(title)}" ${permissions ? `data-entry-id="${escape(item.entry_id)}"` : "data-associated-resource"}${titleAttributes} data-effect="${permissions ? (item.effect === "deny" ? "deny" : "permit") : "none"}" data-portal-tooltip="${escape(description)}" aria-label="${escape(description)}"><span class="packageGlyph" data-portal-icon="${icon}" aria-hidden="true"></span></button>`;
    }).join(""); return grouped ? window.PortalI18n.html`<section class="packageGroup"><h4>${escape(window.PortalI18n.t(name))}</h4><div class="packageGrid">${cards}</div></section>` : cards; }).join("");
    return content ? grouped ? content : window.PortalI18n.html`<section class="packageGroup"><div class="packageGrid">${content}</div></section>` : window.PortalI18n.markup('<p class="backpackEmpty">当前没有可显示的持有物。</p>');
  }
  const packageCategories = [
    { id: "all", label: window.PortalI18n.t("All") },
    { id: "project-lineage", label: window.PortalI18n.t("Project & Lineage") },
    { id: "device", label: window.PortalI18n.t("Device") },
    { id: "resident", label: "Resident" },
    { id: "other", label: window.PortalI18n.t("Others") },
  ];
  // Display families only; these do not describe permission parents or authority.
  const projectPackageTypes = new Set(["project", "lineage", "workbench", "article", "paper", "concept", "decision", "document", "experiment", "idea", "information", "insight", "material", "meeting", "patent", "plan", "question", "review", "risk", "thinking", "todo"]);
  const packageCategoryFor = type => projectPackageTypes.has(type) ? "project-lineage" : ["device", "resident"].includes(type) ? type : "other";
  function renderEntryInventory() {
    if (!$("entryItems") || !state.permissionRows) return;
    if (!packageCategories.some(category => category.id === state.packageCategory)) state.packageCategory = "all";
    const labels = Object.fromEntries(packageCategories.map(category => [category.id, category.label]));
    const includesType = type => state.packageCategory === "all" || packageCategoryFor(type) === state.packageCategory;
    const rows = state.permissionRows.filter(item => includesType(item.resource_type || item.kind));
    const associations = state.possessions.filter(item => includesType(item.resource_type || item.kind)).map(item => ({ ...item, source: item.source || window.PortalI18n.t("已登记关联") }));
    const categoryScroll = $("entryItems").querySelector(".packageCategories")?.scrollLeft || 0;
    $("entryItems").innerHTML = window.PortalI18n.html`<nav class="backpackTabs packageCategories" role="tablist" aria-label="资源类别">${packageCategories.map(({ id, label }) => window.PortalI18n.html`<button type="button" role="tab" data-package-category="${id}" aria-selected="${id === state.packageCategory}" tabindex="${id === state.packageCategory ? 0 : -1}" aria-controls="packageCategoryPanel">${label}</button>`).join("")}</nav><div id="packageCategoryPanel" role="tabpanel" aria-label="${escape(labels[state.packageCategory])}">${rows.length ? packageItems(rows, true, false) : associations.length ? "" : window.PortalI18n.markup('<p class="backpackEmpty">暂无资源。</p>')}${associations.length ? window.PortalI18n.html`<h4>关联资源</h4>${packageItems(associations, false, false)}` : ""}</div>`;
    decorateControls();
    $("entryItems").querySelector(".packageCategories").scrollLeft = categoryScroll;
  }
  root.addEventListener("click", event => {
    const tab = event.target.closest("[data-package-category]");
    if (!tab || !root.contains(tab) || tab.disabled) return;
    window.PortalButtonTooltips.hide();
    state.packageCategory = tab.dataset.packageCategory;
    renderEntryInventory();
    const selected = root.querySelector(`[data-package-category="${CSS.escape(state.packageCategory)}"]`);
    selected?.focus({ preventScroll: true });
    selected?.scrollIntoView({ block: "nearest", inline: "nearest" });
  });
  root.addEventListener("keydown", event => {
    const tab = event.target.closest("[data-package-category]");
    if (!tab || !root.contains(tab) || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...tab.parentElement.querySelectorAll("[role=tab]")];
    const index = tabs.indexOf(tab);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].click();
  });
  const notice = (id, text, error = false) => { $(id).textContent = text; $(id).classList.toggle("backpackError", error); };
  function request(path, payload) {
    return window.PortalAPI.request(`/api/principal-backpack/${path}`, {
      signal: lifetime.signal, cache: "no-store",
      ...(payload ? { method: "POST", body: payload } : {}),
    });
  }
  function renderRoster() {
    const group = (title, rows) => window.PortalI18n.html`<h3>${title}</h3>${rows.map(row => window.PortalI18n.html`<button class="backpackPerson" type="button" data-principal="${escape(row.id)}" aria-pressed="${row.id === state.principal}">${escape(row.title)}<small>${escape(row.kind)} · ${escape(row.id)}</small></button>`).join("") || window.PortalI18n.markup('<p>暂无匹配角色</p>')}`;
    $("principalRoster").innerHTML = group("Users", state.catalog.users) + group("Residents", state.catalog.residents);
    decorateControls();
    $("previousPage").disabled = state.offset === 0;
    $("nextPage").disabled = !state.catalog.has_more;
    $("pageIndicator").textContent = window.PortalI18n.text`Residents · 第 ${Math.floor(state.offset / 60) + 1} 页`;
  }
  function renderVisibility() {
    root.querySelector(".backpackRoster").hidden = mode !== "management";
  }
  async function loadCatalog() {
    const sequence = ++state.catalogRequest;
    try {
      const result = await request(`catalog?q=${encodeURIComponent(state.query)}&offset=${state.offset}&limit=60`);
      if (sequence !== state.catalogRequest) return;
      state.catalog = result;
      renderVisibility();
      renderEntryInventory();
      renderRoster();
      if (!state.requestedPrincipal) await loadDetail(result.principal);
    } catch (error) { if (!lifetime.signal.aborted && sequence === state.catalogRequest) { notice("backpackStatus", error.message, true); $("backpackRetry").hidden = false; } }
  }
  async function loadDetail(principal, { force = false } = {}) {
    if (lifetime.signal.aborted) return false;
    if (!force && state.requestedPrincipal === principal && state.detailReady) return true;
    if (state.requestedPrincipal !== principal) {
      releasePrincipalEvents?.();
      releasePrincipalEvents = String(principal || "").startsWith("resident_")
        ? window.PortalEvents?.watchResident?.(principal) : null;
      state.packageCategory = "";
    }
    resetResourceTitles();
    state.permissionRows = null;
    state.possessions = [];
    state.requestedPrincipal = principal;
    state.detailReady = false;
    const sequence = ++state.detailRequest;
    $("principalDetail").innerHTML = window.PortalI18n.markup('<div id="principalProfile"></div><div id="livePermissions" aria-label="权限表项"></div>');
    window.PortalUIPrimitives.renderAsyncState($("principalProfile"), { state: "loading", title: window.PortalI18n.t("正在读取角色档案…") });
    // A known target needs neither the roster nor its profile to read its entries.
    if (principal) void loadPermissions(principal, sequence);
    notice("backpackStatus", window.PortalI18n.t("正在读取角色档案…"));
    $("backpackRetry").hidden = true;
    try {
      const result = await request(`detail?principal=${encodeURIComponent(principal)}`);
      if (lifetime.signal.aborted || sequence !== state.detailRequest) return false;
      const row = result.principal;
      state.principal = row.id;
      state.requestedPrincipal = row.id;
      state.possessions = result.possessions || [];
      $("principalProfile").innerHTML = window.PortalI18n.html`<div class="backpackIdentity"><div class="backpackPortrait" data-backpack-avatar>${row.kind === "user" ? '<span data-portal-icon="user" aria-hidden="true"></span>' : ""}</div><div><span class="backpackBadge">${escape(row.kind === "resident" ? "RESIDENT" : "USER")}</span><h2>${escape(row.title)}</h2><div class="backpackIdentifier">${escape(row.id)}</div><p>${escape(levelName(row))} · ${escape(row.status || window.PortalI18n.t("已登记"))}</p></div></div>`;
      renderVisibility();
      detailPrincipal = row;
      renderPortrait(row);
      decorateControls();
      state.detailReady = true;
      notice("backpackStatus", window.PortalI18n.t("角色资料来自当前登记。"));
      renderEntryInventory();
      if (!principal) void loadPermissions(row.id, sequence);
      if (state.catalog) renderRoster();
      return true;
    } catch (error) { if (!lifetime.signal.aborted && sequence === state.detailRequest) {
      notice("backpackStatus", error.message, true);
      window.PortalUIPrimitives.renderAsyncState($("principalProfile"), { state: "error", title: window.PortalI18n.t("无法读取角色档案"), message: error.message });
      $("backpackRetry").hidden = false;
    } return false; }
  }
  // A principal's permission entries and live temporary credentials, read through
  // GetEffectivePermissions (decision_b820faf42218 §3.6); management is not a Package action.
  // Class nodes show their titles in the active scheme.
  function entryRows(result, titles) {
    const rows = (result.entries || []).map(entry => ({
      entry_id: entry.entry_id, resource_id: entry.r, a: entry.a, effect: entry.effect,
      p: entry.p, issued_by: entry.issued_by || "", expires_at: entry.expires_at || null, message_id: entry.message_id || "",
      resource_title: titles.R?.get(entry.r) || "", a_title: titles.A?.get(entry.a) || "",
      resource_type: isClassNode(entry.r) ? "Portal" : String(entry.r).replace(/_[0-9a-f]{12}$/, "").replace(/:.*$/, ""),
    }));
    return rows;
  }
  async function loadPermissions(principal, sequence) {
    const permissionSequence = ++state.permissionRequest;
    const current = () => !lifetime.signal.aborted && sequence === state.detailRequest && permissionSequence === state.permissionRequest;
    if (!current()) return;
    if (await window.PortalIdentity.permissionSystemOff().catch(() => false)) {
      if (!current()) return;
      // Off, entries neither apply nor load; the Package still lists the principal's associated resources.
      resetResourceTitles();
      $("livePermissions").innerHTML = window.PortalI18n.markup('<p class="backpackEmpty" role="status">权限系统已关闭：已登录的 User 与 Resident 可以查看、使用和管理所有对象。已有的权限表项保留，开启权限系统后重新生效并在这里显示。</p><div id="entryInventory"><div id="entryItems"></div></div>');
      state.permissionRows = [];
      renderEntryInventory();
      return true;
    }
    resetResourceTitles();
    $("livePermissions").innerHTML = window.PortalI18n.html`${mode === "management" ? window.PortalI18n.markup('<h3>权限表项</h3><button type="button" id="refreshPermissions">刷新</button>') : ""}<p id="permissionStatus" role="status"></p><div id="entryInventory"><div id="entryItems"><p role="status">正在读取权限…</p></div></div>`;
    renderVisibility();
    decorateControls();
    $("refreshPermissions")?.addEventListener("click", () => loadPermissions(principal, sequence));
    try {
      const result = await window.PortalInstructionClient.execute("GetEffectivePermissions", { principal_id: principal });
      const titles = await readSchemeTitles(result.scheme_id);
      if (!current()) return;
      state.permissionRows = entryRows(result, titles);
      renderEntryInventory();
      decorateControls();
      return true;
    } catch (error) {
      if (!current()) return false;
      state.permissionRows = null;
      $("entryItems").textContent = window.PortalI18n.text`权限表项不可读取：${error.message}`;
      return false;
    }
  }
  $("principalRoster").addEventListener("click", event => { const button = event.target.closest("[data-principal]"); if (button) loadDetail(button.dataset.principal); });
  $("rosterSearch").addEventListener("submit", event => { event.preventDefault(); state.query = $("principalSearch").value.trim(); state.offset = 0; loadCatalog(); });
  $("nextPage").addEventListener("click", () => { state.offset += 60; loadCatalog(); });
  $("previousPage").addEventListener("click", () => { state.offset = Math.max(0, state.offset - 60); loadCatalog(); });
  $("backpackRetry").addEventListener("click", () => {
    if (state.requestedPrincipal) loadDetail(state.requestedPrincipal);
    loadCatalog();
  });
  // The Package only reads; nothing is ever pending or unsaved.
  const isBusy = () => false;
  const hasUnsavedChanges = () => false;
  async function refresh({ principalId = state.requestedPrincipal } = {}) {
    if (lifetime.signal.aborted) return false;
    const id = String(principalId || state.requestedPrincipal).trim();
    return loadDetail(id, { force: true });
  }
  window.addEventListener("portal:change", event => {
    const detail = event.detail || {};
    const scopes = detail.resident_changes?.[state.requestedPrincipal] || [];
    if (state.requestedPrincipal && (detail.snapshot_required
        || scopes.includes("identity") || scopes.includes("workspace"))) void refresh();
  }, { signal: lifetime.signal });
  window.addEventListener("portal:events-ready", event => {
    if (state.requestedPrincipal && event.detail?.reconnected) void refresh();
  }, { signal: lifetime.signal });
  return { root, mode, managementView, dialog, destroy, getPrincipalId: () => state.requestedPrincipal, setPrincipalId: principalId => {
    const id = String(principalId || "").trim();
    return id ? loadDetail(id) : Promise.resolve(false);
  }, refresh, hasUnsavedChanges, isBusy, open(principalId) {
    if (dialog) dialog.open();
    else root.hidden = false;
    if (mode === "management") {
      if (principalId) void loadDetail(principalId);
      if (!state.catalog) void loadCatalog();
    } else void loadDetail(principalId);
  }};
  }
  async function open({ principalId = "", mode = "details", managementView = "entries" } = {}) {
    mode = mode === "management" ? "management" : "details";
    if (active && (active.mode !== mode || active.managementView !== managementView)) {
      const previous = active;
      await previous.dialog.close();
      if (previous.root.isConnected && !previous.root.hidden) return previous;
      previous.destroy();
    }
    if (!active) active = create({ mode, managementView });
    active.open(String(principalId).trim());
    return active;
  }
  function mount({ container, principalId, mode = "details", managementView = "entries" }) {
    if (!(container instanceof HTMLElement)) throw new TypeError("Package requires a container");
    const instance = create({ container, managementView, mode: mode === "management" ? "management" : "details" });
    instance.open(String(principalId || "").trim());
    return instance;
  }
  window.PortalPrincipalBackpack = Object.freeze({ open, mount, close: () => active?.dialog.close() });
})();
