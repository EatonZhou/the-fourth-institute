(function () {
  if (window.PortalCommandPalette) return;

  const RESOURCE_ADDRESS_EXACT_RE = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*_[0-9a-f]{12}(?:\/[^\s/]+)*$/;
  const RESOURCE_CACHE_LIMIT = 20;
  const state = {
    open: false,
    query: "",
    resourceType: "",
    resourceTypesLoaded: false,
    resourceTypesPromise: null,
    selected: 0,
    entries: [],
    error: "",
    loading: false,
    loadPromise: null,
    requestSerial: 0,
    searchTimer: 0,
    requestController: null,
    navigating: false,
    identityRevision: 0,
    entryCache: new Map(),
  };
  let paletteDialog = null;

  const esc = value => window.PortalUIPrimitives.escapeHtml(value);

  function cancelPendingSearch() {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = 0;
    state.requestController?.abort();
    state.requestController = null;
    state.requestSerial += 1;
    state.loadPromise = null;
    state.loading = false;
    state.error = "";
  }

  function normalizeEntry(item) {
    const kind = String(item?.resource_type || "").trim();
    const cleanId = String(item?.resource_id || "").trim();
    const address = String(item?.address || cleanId).trim();
    const title = String(item?.title || cleanId || kind).trim();
    const summary = String(item?.summary || "").trim();
    const subtitle = [address, summary].filter(Boolean).join(" · ");
    return {
      kind,
      id: cleanId,
      address,
      title,
      subtitle,
      url: String(item?.url || "").trim(),
    };
  }

  function resourceQueryKey(query = state.query) {
    return String(query || "").trim();
  }

  function cacheKey(query = state.query, resourceType = state.resourceType) {
    return `${resourceType}\0${resourceQueryKey(query)}`;
  }

  function shouldQueryResources(query = state.query) {
    const key = resourceQueryKey(query);
    return !!key;
  }

  function cachedEntries(query = state.query) {
    return [...(state.entryCache.get(cacheKey(query)) || [])];
  }

  function rememberEntries(query, entries) {
    if (!resourceQueryKey(query)) return;
    const key = cacheKey(query);
    state.entryCache.delete(key);
    state.entryCache.set(key, [...entries]);
    while (state.entryCache.size > RESOURCE_CACHE_LIMIT) {
      state.entryCache.delete(state.entryCache.keys().next().value);
    }
  }

  async function loadEntries(options = {}) {
    const refresh = !!options.refresh;
    const requestedQuery = resourceQueryKey();
    const requestedType = state.resourceType;
    state.requestController?.abort();
    const controller = new AbortController();
    state.requestController = controller;
    const serial = ++state.requestSerial;
    state.error = "";
    state.loading = shouldQueryResources(requestedQuery) && !state.entries.length;
    render();
    state.loadPromise = (async () => {
      if (refresh) {
        await window.PortalAPI.get("/api/resource-index/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ wait: true, timeout: 60 }),
          timeoutMs: 65_000,
          signal: controller.signal,
        });
      }
      if (!shouldQueryResources(requestedQuery)) {
        if (serial === state.requestSerial) {
          state.entries = cachedEntries(requestedQuery);
          state.loading = false;
          render();
        }
        return state.entries;
      }
      let payload;
      if (RESOURCE_ADDRESS_EXACT_RE.test(requestedQuery)) {
        try {
          const params = new URLSearchParams({ address: requestedQuery });
          const resource = await window.PortalAPI.get(`/api/resources/reference?${params}`, {
            signal: controller.signal,
          });
          payload = { resources: !requestedType || resource.resource_type === requestedType ? [resource] : [] };
        } catch (error) {
          if (error.status === 404) payload = { resources: [] };
          else throw error;
        }
      } else {
        const params = new URLSearchParams({ q: requestedQuery, limit: "30" });
        if (requestedType) params.set("resource_type", requestedType);
        payload = await window.PortalAPI.get(`/api/resource-index/suggest?${params}`, {
          signal: controller.signal,
        });
      }
      if (serial !== state.requestSerial) return state.entries;
      state.entries = (payload.resources || []).map(normalizeEntry).filter(item => item.id && item.url);
      rememberEntries(requestedQuery, state.entries);
      state.loading = false;
      render();
      return state.entries;
    })();
    try {
      return await state.loadPromise;
    } catch (error) {
      if (serial === state.requestSerial && error?.name !== "AbortError") {
        state.entries = [];
        state.error = window.PortalI18n.t("Search unavailable. Please try again.");
      }
      throw error;
    } finally {
      if (serial === state.requestSerial) {
        state.requestController = null;
        state.loadPromise = null;
        state.loading = false;
        render();
      }
    }
  }

  async function activate(item) {
    if (!item) return;
    navigate(item);
  }

  function resourceUrlNeedsCanonicalLookup(item) {
    const address = String(item?.address || item?.id || "").trim();
    const target = String(item?.url || "").trim();
    if (!address || !target) return false;
    try {
      const url = new URL(target, window.location.origin);
      if (url.origin !== window.location.origin) return false;
      let decodedPath = url.pathname;
      try {
        decodedPath = decodeURIComponent(decodedPath);
      } catch {}
      const cleanPath = decodedPath.replace(/^\/+|\/+$/g, "");
      const rootId = address.split("/", 1)[0];
      if (url.pathname.startsWith("/r/")) return true;
      if (
        ["/resource", "/resource/", "/resource.html"].includes(url.pathname)
        && url.searchParams.has("address")
      ) {
        return true;
      }
      return !url.search && [address, rootId].includes(cleanPath);
    } catch {
      return false;
    }
  }

  async function navigate(item) {
    if (!item?.url || state.navigating) return;
    state.navigating = true;
    const identityRevision = state.identityRevision;
    let target = String(item.url || "").trim();
    try {
      if (resourceUrlNeedsCanonicalLookup(item)) {
        const params = new URLSearchParams({
          address: String(item.address || item.id || ""),
        });
        const reference = await window.PortalAPI.get(`/api/resources/reference?${params}`);
        target = String(reference?.url || target).trim();
      }
      if (identityRevision !== state.identityRevision) return;
      if (!target) {
        state.navigating = false;
        return;
      }
      window.location.assign(target);
    } catch {
      if (identityRevision !== state.identityRevision) return;
      if (target) window.location.assign(target);
      else state.navigating = false;
    }
  }

  function syncSelectionHighlight(list) {
    const root = list || document.getElementById("portalCommandList");
    if (!root) return;
    root.querySelectorAll("[data-command-index]").forEach(btn => {
      const active = Number(btn.dataset.commandIndex || 0) === state.selected;
      btn.classList.toggle("active", active);
      const shortcut = btn.querySelector(".portalCommandShortcut");
      if (shortcut) shortcut.textContent = active ? "Enter" : "";
    });
  }

  function render() {
    const overlay = document.getElementById("portalCommandOverlay");
    if (!overlay) return;
    const input = document.getElementById("portalCommandInput");
    const list = document.getElementById("portalCommandList");
    if (!input || !list) return;
    input.value = state.query;
    if (state.loading && !state.entries.length) {
      list.innerHTML = window.PortalI18n.html`<div class="portalCommandEmpty">Loading…</div>`;
      return;
    }
    if (state.error) {
      list.innerHTML = window.PortalI18n.html`<div class="portalCommandEmpty">${esc(state.error)}</div>`;
      return;
    }
    const items = state.entries;
    state.selected = Math.max(0, Math.min(state.selected, Math.max(0, items.length - 1)));
    if (!items.length) {
      list.innerHTML = shouldQueryResources()
        ? window.PortalI18n.html`<div class="portalCommandEmpty" role="status">No results.</div>`
        : window.PortalI18n.html`<div class="portalCommandEmpty" role="status">Search Portal objects by ID or name</div>`;
      return;
    }
    list.innerHTML = items.map((item, idx) => window.PortalI18n.html`
      <button class="portalCommandItem ${idx === state.selected ? "active" : ""}" type="button" data-command-index="${idx}">
        <span class="portalCommandKind">${esc(item.kind)}</span>
        <span class="portalCommandBody">
          <span class="portalCommandTitle">${esc(item.title)}</span>
          <span class="portalCommandSub">${esc(item.subtitle || item.id)}</span>
        </span>
        <span class="portalCommandShortcut">${idx === state.selected ? "Enter" : ""}</span>
      </button>
    `).join("");
    list.querySelectorAll("[data-command-index]").forEach(btn => {
      const item = items[Number(btn.dataset.commandIndex || 0)];
      btn.addEventListener("mouseenter", () => {
        state.selected = Number(btn.dataset.commandIndex || 0);
        syncSelectionHighlight(list);
      });
      btn.addEventListener("pointerdown", event => {
        if (event.button !== 0) return;
        event.preventDefault();
      });
      btn.addEventListener("click", () => activate(item));
    });
  }

  function openPalette() {
    state.open = true;
    state.selected = 0;
    state.entries = cachedEntries();
    state.loading = false;
    render();
    paletteDialog?.open();
    loadResourceTypes().catch(() => {});
  }

  async function loadResourceTypes() {
    if (state.resourceTypesLoaded) return;
    if (state.resourceTypesPromise) return state.resourceTypesPromise;
    state.resourceTypesPromise = (async () => {
      const payload = await window.PortalAPI.get("/api/resource-index/status");
      const select = document.getElementById("portalCommandType");
      if (!select) return;
      const types = Array.isArray(payload.resource_types) ? payload.resource_types : [];
      select.replaceChildren(new Option(window.PortalI18n.t("全部类型"), ""));
      for (const type of [...new Set(types.map(value => String(value || "").trim()).filter(Boolean))].sort()) {
        const label = type.replaceAll("_", " ").replace(/\b\w/g, value => value.toUpperCase());
        select.add(new Option(label, type));
      }
      select.value = state.resourceType;
      state.resourceTypesLoaded = true;
    })();
    try {
      return await state.resourceTypesPromise;
    } finally {
      state.resourceTypesPromise = null;
    }
  }

  function closePalette() {
    cancelPendingSearch();
    state.open = false;
    state.selected = 0;
    render();
    paletteDialog?.close();
  }

  function resetIdentity() {
    state.identityRevision += 1;
    state.navigating = false;
    cancelPendingSearch();
    state.entryCache.clear();
    state.entries = [];
    state.selected = 0;
    render();
    if (state.open && shouldQueryResources()) loadEntries().catch(() => {});
  }

  function mount() {
    const overlay = document.createElement("div");
    overlay.id = "portalCommandOverlay";
    overlay.className = "portalDetailOverlay portalCommandOverlay";
    overlay.hidden = true;
    overlay.dataset.portalOverlayLayer = "dialog";
    overlay.innerHTML = window.PortalI18n.html`
      <div class="portalDetailSurface portalCommandBox" data-portal-detail-surface role="dialog" aria-modal="true" aria-label="Portal search">
        <div data-portal-search-field data-portal-search-density="command">
          <input data-portal-search-input type="search" id="portalCommandInput" placeholder="Search" aria-label="Search Portal objects by ID or name" autocomplete="off" />
          <button class="portalButton is-compact is-quiet portalCommandClose" type="button" aria-label="Close"><kbd>Esc</kbd></button>
        </div>
        <div class="portalCommandList" id="portalCommandList"></div>
        <div class="portalCommandFooter">
          <select class="portalCommandType" id="portalCommandType" aria-label="资源类别"><option value="">全部类型</option></select>
          <span class="portalCommandKeys" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd><span><kbd>Enter</kbd> Open</span></span>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector(".portalCommandClose").addEventListener("click", closePalette);
    paletteDialog = window.PortalUI.createDialogController({
      modal: overlay,
      bodyClass: null,
      initialFocus: "portalCommandInput",
      backdropClose: true,
      escapeClose: true,
      requestClose: closePalette,
    });
    const launcher = document.createElement("button");
    launcher.className = "portalButton is-compact is-icon is-quiet portalCommandLauncher";
    launcher.type = "button";
    launcher.setAttribute("aria-label", window.PortalI18n.t("Search"));
    const launcherIcon = document.createElement("span");
    launcherIcon.dataset.portalIcon = "search";
    launcherIcon.setAttribute("aria-hidden", "true");
    launcher.append(launcherIcon);
    launcher.title = window.PortalI18n.t("Search Portal objects (Ctrl/⌘ K)");
    launcher.addEventListener("click", openPalette);
    // Pages loading their scripts deferred may run this before the site
    // header renders; the launcher then joins it once the document is parsed.
    const placeLauncher = () => {
      const target = document.querySelector(".portalUser") || document.querySelector(".portalNavTop");
      if (target && !launcher.isConnected) target.insertBefore(launcher, target.querySelector(":scope > [data-portal-user-menu]"));
      return Boolean(target);
    };
    if (!placeLauncher()) document.addEventListener("DOMContentLoaded", placeLauncher, { once: true });
    document.getElementById("portalCommandInput")?.addEventListener("input", event => {
      state.query = String(event.target.value || "");
      state.selected = 0;
      cancelPendingSearch();
      state.entries = cachedEntries();
      state.loading = shouldQueryResources() && !state.entries.length;
      render();
      if (!shouldQueryResources()) return;
      state.searchTimer = window.setTimeout(() => {
        state.searchTimer = 0;
        loadEntries().catch(() => {});
      }, 120);
    });
    document.getElementById("portalCommandType")?.addEventListener("change", event => {
      state.resourceType = String(event.target.value || "");
      state.selected = 0;
      cancelPendingSearch();
      state.entries = cachedEntries();
      if (shouldQueryResources()) loadEntries().catch(() => {});
      else render();
    });
    window.PortalCommandRegistry.root.register({
      id: "portal.command-palette.open",
      title: window.PortalI18n.t("打开全局搜索"),
      description: window.PortalI18n.t("搜索 Portal 资源"),
      category: "Portal",
      icon: "search",
      keybindings: ["Ctrl+K", "Meta+K"],
      priority: 1000,
      allowInInput: true,
      execute: openPalette,
    });
    document.addEventListener("keydown", event => {
      if (!state.open || event.isComposing || event.keyCode === 229) return;
      // Native select and button keyboard actions belong to their controls.
      if (event.target !== document.getElementById("portalCommandInput")) return;
      const items = state.entries;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        state.selected = Math.min(items.length - 1, state.selected + 1);
        render();
        document.querySelector(".portalCommandItem.active")?.scrollIntoView({ block: "nearest" });
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        state.selected = Math.max(0, state.selected - 1);
        render();
        document.querySelector(".portalCommandItem.active")?.scrollIntoView({ block: "nearest" });
      } else if (event.key === "Enter") {
        event.preventDefault();
        activate(items[state.selected]);
      }
    });
  }

  window.PortalCommandPalette = {
    open: openPalette,
    close: closePalette,
    isOpen: () => state.open,
    reload: () => loadEntries({ refresh: true }),
  };
  window.addEventListener(window.PortalIdentity?.eventName || "portal:identity-changed", resetIdentity);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
