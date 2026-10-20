(() => {
  "use strict";
  // A read avatar stays current this long before a newly bound node asks again.
  const FRESH_MS = 300000;
  // A failed read is retried on these delays, so one restart or dropped request
  // never leaves a message showing its letter for the rest of the page's life.
  const RETRY_MS = [3000, 10000, 30000, 60000];

  function markup(avatar, {label = window.PortalI18n.t("User"), size = 34} = {}) {
    const escape = window.PortalUIPrimitives.escapeHtml;
    const pixels = Number.isFinite(size) ? Math.max(16, Math.min(136, size)) : 34;
    if (avatar?.kind === "upload" && typeof avatar.url === "string" && avatar.url.startsWith("/api/account/avatar/image?")) {
      return window.PortalI18n.html`<img src="${escape(avatar.url)}" alt="${escape(label)}" width="${pixels}" height="${pixels}" style="object-fit:cover;border-radius:8px" />`;
    }
    return window.PortalResidentAvatar.markup(avatar, {label, size: pixels});
  }

  function initial(label) {
    return Array.from(String(label || ""))[0]?.toUpperCase() || "?";
  }

  // principal -> {at, promise, avatar}; `avatar` is the last avatar read, kept
  // while a newer read is pending or failed so a re-render never falls back to
  // the letter.  `null` means the principal has no avatar to show.
  const portraits = new Map();

  function portrait(principal) {
    const cached = portraits.get(principal);
    if (cached && Date.now() - cached.at < FRESH_MS) return cached.promise;
    const entry = {at: Date.now(), promise: null, avatar: cached?.avatar};
    entry.promise = window.PortalAPI.get(`/api/account/avatar?principal=${encodeURIComponent(principal)}`)
      .then(result => {
        entry.avatar = result?.avatar || null;
        return entry.avatar;
      }, error => {
        // 400/404: this principal has no avatar; asking again changes nothing.
        if (error?.status === 400 || error?.status === 404) {
          entry.avatar = null;
          return null;
        }
        if (portraits.get(principal) === entry) entry.at = 0;
        throw error;
      });
    portraits.set(principal, entry);
    if (portraits.size > 200) portraits.delete(portraits.keys().next().value);
    return entry.promise;
  }

  function sameAvatar(left, right) {
    return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
  }

  // The complete message-header avatar.  With a known avatar this is exactly
  // what bind() leaves in the page, so re-rendering a message keeps its node.
  function messageAvatarHtml(principal, label) {
    const escape = window.PortalUIPrimitives.escapeHtml;
    const name = label || principal;
    const head = `<span class="portalUserAvatar messageAuthorAvatar" data-message-avatar-principal="${escape(principal)}" data-message-avatar-label="${escape(label)}" aria-hidden="true"`;
    const avatar = portraits.get(principal)?.avatar;
    if (!avatar) return `${head}>${escape(initial(name))}</span>`;
    return `${head} data-message-avatar-applied="${escape(principal)}">${markup(avatar, {label: name, size: 24})}</span>`;
  }

  const pending = new WeakMap();
  let retryTimer = 0;
  let retryStep = 0;

  function scheduleRetry() {
    if (retryTimer) return;
    const delay = RETRY_MS[Math.min(retryStep, RETRY_MS.length - 1)];
    retryStep += 1;
    retryTimer = window.setTimeout(() => {
      retryTimer = 0;
      bind(document);
    }, delay);
  }

  function showInitial(node, principal) {
    node.textContent = initial(node.dataset.messageAvatarLabel || principal);
    delete node.dataset.messageAvatarApplied;
  }

  function apply(node, principal, avatar) {
    if (!node.isConnected || node.dataset.messageAvatarPrincipal !== principal) return;
    if (!avatar) {
      showInitial(node, principal);
      return;
    }
    node.innerHTML = markup(avatar, {label: node.dataset.messageAvatarLabel || principal, size: 24});
    node.dataset.messageAvatarApplied = principal;
    const image = node.querySelector("img");
    if (!image) retryStep = 0;
    image?.addEventListener("load", () => { retryStep = 0; }, {once: true});
    image?.addEventListener("error", () => {
      if (node.dataset.messageAvatarPrincipal !== principal) return;
      showInitial(node, principal);
      const entry = portraits.get(principal);
      if (entry) entry.at = 0;
      scheduleRetry();
    }, {once: true});
  }

  function bind(root) {
    root?.querySelectorAll?.("[data-message-avatar-principal]").forEach(node => {
      const principal = node.dataset.messageAvatarPrincipal;
      if (!principal || pending.get(node) === principal) return;
      const entry = portraits.get(principal);
      const fresh = entry && Date.now() - entry.at < FRESH_MS && entry.avatar !== undefined;
      if (fresh && node.dataset.messageAvatarApplied === principal) return;
      pending.set(node, principal);
      const shown = node.dataset.messageAvatarApplied === principal ? entry?.avatar : undefined;
      portrait(principal).then(avatar => {
        pending.delete(node);
        if (shown !== undefined && sameAvatar(shown, avatar)) return;
        apply(node, principal, avatar);
      }, () => {
        pending.delete(node);
        scheduleRetry();
      });
    });
  }

  window.addEventListener("portal:user-avatar-changed", event => {
    const {username, avatar} = event.detail || {};
    if (!username) return;
    portraits.set(username, {at: Date.now(), promise: Promise.resolve(avatar || null), avatar: avatar || null});
    document.querySelectorAll("[data-message-avatar-principal]").forEach(node => {
      if (node.dataset.messageAvatarPrincipal === username) delete node.dataset.messageAvatarApplied;
    });
    bind(document);
  });
  window.PortalUserAvatar = Object.freeze({markup, bind, messageAvatarHtml});
})();
