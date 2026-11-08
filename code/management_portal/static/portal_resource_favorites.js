(() => {
  "use strict";
  if (window.PortalResourceFavorites) return;
  const endpoint = "/api/resources/favorites";
  const knowledgeTypes = new Set(window.PortalResourceRoutes.routes
    .filter(route => route.detail_kind === "knowledge").map(route => route.resource_type));
  const isKnowledge = address => knowledgeTypes.has(window.PortalResourceChips.resourceTypeFromAddress(address));
  const buttons = new Map(), lists = new Map(), pending = new Set();
  const text = value => window.PortalI18n.t(value);
  let items = [], loaded = false, loading = null, error = "", revision = 0, identity = 0;
  const channel = typeof BroadcastChannel === "function" ? new BroadcastChannel("portal-resource-favorites") : null;
  const node = (tag, className, value) => {
    const result = document.createElement(tag); result.className = className || "";
    if (value) result.textContent = value;
    return result;
  };
  const notifyError = error => window.PortalUI?.toast?.(error.message || String(error), "error");
  const contains = address => items.some(item => item.address === address);

  function syncButtons() {
    for (const [container, button] of buttons) {
      const address = container.dataset.resourceFavorite;
      const selected = contains(address);
      const label = selected ? "取消收藏此资源" : "收藏此资源到 My Favorite";
      button.textContent = selected ? "★" : "☆";
      button.title = text(label); button.setAttribute("aria-label", text(label));
      button.setAttribute("aria-pressed", String(selected));
      button.disabled = pending.has(address) || (!loaded && !!loading);
    }
  }

  async function refresh() {
    if (loading) return loading;
    const current = ++revision;
    loading = window.PortalAPI.get(endpoint).then(payload => {
      if (current !== revision) return;
      items = payload.items || []; loaded = true; error = "";
    }).catch(failure => {
      if (current === revision) error = failure.message || String(failure);
    }).finally(() => {
      if (current !== revision) return;
      loading = null; render();
    });
    syncButtons();
    return loading;
  }

  async function set(address, favorite) {
    if (pending.has(address)) return;
    pending.add(address); syncButtons();
    const owner = identity;
    try {
      const result = await window.PortalAPI.post(endpoint, { address, favorite });
      if (owner !== identity) return;
      // Invalidate earlier list reads; only server-committed state is shown.
      revision++; loading = null;
      if (!favorite) items = items.filter(item => item.address !== result.address);
      else if (!contains(result.address)) items.push({ address: result.address, status: "available" });
      render(); channel?.postMessage("changed");
      window.dispatchEvent(new CustomEvent("portal:resource-favorite-changed", { detail: result }));
      await refresh();
    } finally {
      if (owner === identity) { pending.delete(address); syncButtons(); }
    }
  }

  function mountButton(container, address = container.dataset.resourceFavorite) {
    if (container.dataset.resourceFavorite !== address) container.dataset.resourceFavorite = address;
    container.hidden = !isKnowledge(address);
    if (container.hidden) {
      removeButton(container); container.replaceChildren();
      return () => removeButton(container);
    }
    if (buttons.has(container)) { syncButtons(); return () => removeButton(container); }
    const button = node("button", "portalButton is-icon is-quiet resourceFavoriteButton");
    button.type = "button";
    button.addEventListener("click", async event => {
      event.preventDefault(); event.stopPropagation();
      const owner = identity;
      try {
        if (!loaded) { await refresh(); if (!loaded) throw new Error(error); }
        if (owner !== identity) return;
        const target = container.dataset.resourceFavorite;
        await set(target, !contains(target));
      } catch (error) { notifyError(error); }
    });
    // Keep a search result selected while using its star.
    button.addEventListener("pointerdown", event => event.stopPropagation());
    container.replaceChildren(button); buttons.set(container, button);
    if (!loaded) void refresh();
    syncButtons();
    return () => removeButton(container);
  }
  function removeButton(container) { buttons.delete(container); }

  async function open(address) {
    const owner = identity;
    const reference = await window.PortalAPI.get(`/api/resources/reference?${new URLSearchParams({ address })}`);
    if (owner !== identity) return;
    if (window.PortalResourceChips?.resourceSupportsPreview(address)
      && await window.PortalPreview?.request({ resourceAddress: address, title: reference.title, source: "resource", mode: "new-tab" })) return;
    if (owner !== identity) return;
    if (!reference.url) throw new Error(text("此资源没有可打开的页面"));
    window.location.assign(reference.url);
  }

  function renderList(root, body) {
    const knowledge = items.filter(item => isKnowledge(item.address));
    const count = root.querySelector("[data-resource-favorite-count]");
    count.textContent = String(knowledge.length);
    count.hidden = !knowledge.length;
    body.replaceChildren();
    if (error || !knowledge.length) {
      const hint = node("div", "residentEmpty", error || text(loading ? "加载知识收藏…" : "暂无知识收藏。在知识条目或其文件旁点击星标即可添加。"));
      body.append(hint);
      if (error) {
        const retry = node("button", "portalButton is-quiet", text("重试"));
        retry.type = "button"; retry.onclick = () => void refresh(); body.append(retry);
      }
      return;
    }
    for (const item of knowledge) {
      const row = node("div", "portalNavRow resourceFavoriteRow");
      const button = node("button", "portalNavRowMain resourceFavoriteOpen"); button.type = "button";
      const title = item.title || item.address;
      const status = item.status === "denied" ? text("无访问权限") : item.status === "missing" ? text("资源不存在") : "";
      button.append(node("span", "portalNavRowLabel resourceFavoriteTitle", status ? `${status} · ${title}` : title));
      const resourceType = item.resource_type || window.PortalResourceChips?.resourceTypeFromAddress?.(item.address);
      const meta = [resourceType, item.project || item.project_id, status].filter(Boolean).join(" · ");
      button.title = [title, meta, item.address].filter(Boolean).join("\n");
      button.disabled = !!status;
      button.addEventListener("click", () => open(item.address).catch(notifyError));
      const star = node("span"); star.dataset.resourceFavorite = item.address;
      row.append(button, star); body.append(row);
    }
  }
  function mountList(root) {
    if (lists.has(root)) return;
    const section = node("details", "residentSection favoriteDisclosure portalNavSection resourceFavorites"); section.open = true;
    const summary = node("summary", "residentGroupHead portalNavSectionHead");
    summary.append(node("span", "residentGroupName portalNavSectionLabel", "Knowledge"));
    const count = node("span", "portalNavSectionCount"); count.dataset.resourceFavoriteCount = "";
    summary.append(count);
    const body = node("div", "residentGroupBody portalNavSectionBody"); section.append(summary, body); root.replaceChildren(section);
    lists.set(root, body); renderList(root, body); void refresh();
  }
  function render() { syncButtons(); for (const [root, body] of lists) renderList(root, body); }
  const enhancer = window.PortalUIPrimitives.registerEnhancer;
  enhancer({ id: "resource-favorite-buttons", selector: "[data-resource-favorite]", attributes: ["data-resource-favorite"], enhance: mountButton, remove: removeButton });
  enhancer({ id: "resource-favorite-lists", selector: "[data-resource-favorites]", enhance: mountList, remove: root => lists.delete(root) });
  const reload = () => { if (buttons.size || lists.size) void refresh(); };
  channel?.addEventListener("message", reload);
  window.addEventListener("focus", reload);
  window.addEventListener("portal:history-restored", reload);
  window.addEventListener("portal:user-navigation-changed", reload);
  window.addEventListener("portal:paper-favorite-changed", () => { channel?.postMessage("changed"); reload(); });
  window.addEventListener("portal:identity-changed", () => {
    identity++; revision++; loading = null; loaded = false; items = []; error = ""; pending.clear(); render(); reload();
  });
  document.addEventListener("click", event => { if (event.target.closest("#favoriteRefreshBtn")) reload(); });
  window.PortalResourceFavorites = Object.freeze({ mountButton, refresh, set, open });
})();
