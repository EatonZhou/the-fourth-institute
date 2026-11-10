(function () {
  "use strict";
  if (window.PortalMailboxAlerts) return;
  let instance = null;

  function mount() {
    if (instance || window.top !== window) return;
    const store = window.PortalConversationStore;
    const controller = new AbortController();
    const { signal } = controller;
    const t = source => window.PortalI18n.t(source);
    const username = () => String(window.PortalIdentity.username() || "").toLowerCase();
    let actor = username(), generation = 0, verified = false, current = [], signature = "", opening = false;
    const storageKey = () => `tfi.mailbox.mention-dismissals.v1:${encodeURIComponent(actor)}`;
    function readDismissals() {
      try { return new Set(JSON.parse(localStorage.getItem(storageKey()) || "[]")); }
      catch (_) { return new Set(); }
    }
    let dismissed = readDismissals();
    const root = document.createElement("aside");
    root.className = "portalMailboxAlert";
    root.hidden = true;
    root.setAttribute("aria-label", t("聊天室提及提醒"));
    root.innerHTML = '<button class="portalMailboxAlertOpen" type="button" data-mailbox-alert-open>' +
      '<span class="portalMailboxAlertIcon" aria-hidden="true">@</span>' +
      '<span class="portalMailboxAlertText" role="status" aria-live="polite" aria-atomic="true">' +
      '<span class="portalMailboxAlertTitle"></span><span class="portalMailboxAlertCount"></span></span>' +
      '<svg class="portalMailboxAlertArrow" aria-hidden="true" viewBox="0 0 16 16"><path d="m6 4 4 4-4 4"/></svg></button>' +
      '<button class="portalMailboxAlertDismiss" type="button" data-mailbox-alert-dismiss>' +
      '<svg aria-hidden="true" viewBox="0 0 16 16"><path d="m4 4 8 8m0-8-8 8"/></svg></button>';
    const open = root.querySelector("[data-mailbox-alert-open]");
    open.title = t("查看 @ 我的消息");
    const close = root.querySelector("[data-mailbox-alert-dismiss]");
    close.setAttribute("aria-label", t("收起提醒"));
    close.title = t("仅收起提醒，消息仍保持未读");
    document.body.appendChild(root);

    function reset() {
      actor = username(); generation += 1; verified = false; current = []; signature = "";
      dismissed = readDismissals(); root.hidden = true; opening = false; open.disabled = false;
    }
    function update(snapshot, reason) {
      if (actor !== username() || reason === "identity") reset();
      // Cached snapshots are useful in Mailbox, but cannot authorize a content preview.
      if (reason === "index" && !snapshot.error) verified = true;
      if (!actor || !verified || snapshot.error) { root.hidden = true; return; }
      current = snapshot.entries.filter(entry => entry.kind === "chatroom" && entry.unread)
        .map(entry => ({ entry, ids: (entry.unread_mentions || []).map(item => String(item.message_id || ""))
          .filter(id => id && !dismissed.has(`${entry.key}/${id}`)) }))
        .filter(item => item.ids.length);
      const next = JSON.stringify(current.map(({ entry, ids }) => [entry.key, entry.title, ids]));
      root.hidden = !current.length;
      if (next === signature) return;
      signature = next;
      if (current.length) {
        const first = current[0].entry.title || t("聊天室");
        const title = current.length > 1 ? window.PortalI18n.text`${first} 等 ${current.length} 个聊天室` : first;
        const count = current.reduce((n, item) => n + item.ids.length, 0);
        root.querySelector(".portalMailboxAlertTitle").textContent = title;
        const badge = root.querySelector(".portalMailboxAlertCount");
        badge.textContent = String(count);
        badge.setAttribute("aria-label", window.PortalI18n.text`${count} 条 @ 我`);
        open.setAttribute("aria-label", window.PortalI18n.text`${title} · ${count} 条 @ 我`);
        open.title = `${title} · ${t("查看 @ 我的消息")}`;
      }
    }
    function dismiss(items) {
      for (const { entry, ids } of items) for (const id of ids) dismissed.add(`${entry.key}/${id}`);
      // This is a presentation preference, never a read receipt or message store.
      try { localStorage.setItem(storageKey(), JSON.stringify([...dismissed].slice(-2000))); } catch (_) {}
      update(store.snapshot(), "dismiss");
    }
    close.addEventListener("click", () => dismiss(current), { signal });
    open.addEventListener("click", async () => {
      if (opening || !current.length) return;
      const target = current[0], revision = generation, user = actor;
      const id = String(target.entry.chatroom_id || target.entry.key.slice(9));
      if (!/^chatroom_[0-9a-f]{12}$/.test(id)) return;
      opening = true; open.disabled = true;
      try {
        // Recheck membership through the same owner before entering the room.
        const payload = await store.readChatrooms(id);
        if (signal.aborted || revision !== generation || user !== username()) return;
        if (!payload.chatroom?.unread_mentions?.length) return;
        const entry = { ...store.entry(target.entry.key), kind: "chatroom", surface: "lineage", chatroom_id: id,
          url: `/lineage?chatroom=${encodeURIComponent(id)}` };
        const request = { entry, handled: false };
        window.dispatchEvent(new CustomEvent("portal:agent-conversation-open", { detail: request }));
        dismiss([target]);
        if (!request.handled) window.location.assign(entry.url);
      } catch (_) {
        if (!signal.aborted && revision === generation && user === username()) {
          root.hidden = true; verified = false;
          window.PortalUI?.toast?.(t("暂时无法打开聊天室，请从 Mailbox 重试。"), "error");
          void store.refresh({ force: true }).catch(() => {});
        }
      } finally {
        if (revision === generation) { opening = false; open.disabled = false; }
      }
    }, { signal });
    const unsubscribe = store.subscribe(update);
    window.addEventListener("storage", event => {
      if (event.key !== storageKey()) return;
      dismissed = readDismissals(); update(store.snapshot(), "dismiss");
    }, { signal });
    window.addEventListener(window.PortalIdentity.eventName || "portal:identity-changed", reset, { signal });
    instance = { destroy() { controller.abort(); unsubscribe(); root.remove(); } };
    void store.refresh({ force: true }).catch(() => {});
  }
  window.PortalMailboxAlerts = Object.freeze({ mount, destroy() { instance?.destroy(); instance = null; } });
}());
