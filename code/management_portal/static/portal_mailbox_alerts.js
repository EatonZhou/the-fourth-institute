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
    root.innerHTML = '<div class="portalMailboxAlertText" role="status" aria-live="polite"><strong></strong><p></p></div>' +
      '<button class="portalButton is-primary" type="button" data-mailbox-alert-open></button>' +
      '<button class="portalButton" type="button" data-mailbox-alert-dismiss></button>';
    root.querySelector("strong").textContent = t("有人在聊天室 @ 你");
    const open = root.querySelector("[data-mailbox-alert-open]");
    open.textContent = t("查看 @ 我的消息");
    const close = root.querySelector("[data-mailbox-alert-dismiss]");
    close.textContent = t("收起提醒");
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
      if (current.length) root.querySelector("p").textContent = window.PortalI18n.text`${current[0].entry.title || t("聊天室")} · ${current.reduce((n, item) => n + item.ids.length, 0)} 条 @ 我`;
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
