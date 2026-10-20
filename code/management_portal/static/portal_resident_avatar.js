(() => {
  "use strict";
  // An uploaded avatar: {kind: "upload", url} as saved, or the avatar_url that
  // run and conversation entries carry beside their own kind and url.
  const IMAGE_ADDRESS = /^\/api\/account\/avatar\/image\?[A-Za-z0-9_.~%=&-]+$/;
  function imageUrl(avatar) {
    const url = avatar?.kind === "upload" ? avatar.url : avatar?.avatar_url;
    return typeof url === "string" && IMAGE_ADDRESS.test(url) ? url : "";
  }
  function presentation(avatar, size = 34) {
    const pixels = Number.isFinite(size) ? Math.max(16, Math.min(136, size)) : 34;
    const image = imageUrl(avatar);
    if (image) {
      return { valid: true, rarity: "", className: "portalResidentAvatar is-image", style: `--avatar-size:${pixels}px;--avatar-image:url("${image}")` };
    }
    const index = avatar?.sprite_index;
    const valid = Number.isInteger(index) && index >= 0 && index < 42;
    // Missing or invalid bindings reuse an ordinary creature without assigning
    // a new identity or implying a rarity. The persisted binding stays untouched.
    const displayIndex = valid ? index : 0;
    const rarity = valid && ["R", "SR", "SSR", "SSSR"].includes(avatar.sprite_rarity) ? avatar.sprite_rarity : "";
    return {
      valid,
      rarity,
      className: "portalResidentAvatar",
      style: `--avatar-size:${pixels}px;--sprite-position:${(displayIndex % 8) * -pixels}px ${Math.floor(displayIndex / 8) * -pixels}px`,
    };
  }
  function markup(avatar, { label = "Resident", size = 34 } = {}) {
    const view = presentation(avatar, size);
    const escape = window.PortalUIPrimitives.escapeHtml;
    const title = view.rarity ? `${label} · ${view.rarity}` : label;
    return window.PortalI18n.html`<span class="${view.className}" style="${view.style}" role="img" aria-label="${escape(title)}" title="${escape(title)}" data-sprite-rarity="${view.rarity}"></span>`;
  }
  window.PortalResidentAvatar = Object.freeze({ presentation, markup });
})();
