(function () {
  const STORAGE_KEY = window.PortalCompatibility.identifier("theme");
  const MODES = ["auto", "light", "dark", "fire", "deepsea", "forest", "scifi", "moonwalker"];
  const DARK_MODES = new Set(["dark", "fire", "deepsea", "forest", "scifi", "moonwalker"]);
  const LABELS = { auto: window.PortalI18n.t("Auto"), light: window.PortalI18n.t("Light"), dark: window.PortalI18n.t("Dark"), fire: window.PortalI18n.t("Fire"), deepsea: window.PortalI18n.t("Deep Sea"), forest: window.PortalI18n.t("Forest"), scifi: window.PortalI18n.t("Sci-Fi"), moonwalker: window.PortalI18n.t("Moon Walker") };
  const MOTION_MODES = ["system", "full", "reduced"];
  function normalizeColor(value) {
    const color = typeof value === "string" ? value.trim().toLowerCase() : "";
    return /^#[0-9a-f]{6}$/.test(color) ? color : "";
  }
  function normalizeAutoColors(value = {}) {
    const strength = value?.bubble_intensity;
    return {
      accent: normalizeColor(value?.accent),
      quote_color: normalizeColor(value?.quote_color),
      bubble_intensity: typeof strength === "number" && Number.isFinite(strength)
        ? Math.min(100, Math.max(0, Math.round(strength))) : 18,
    };
  }

  function luminance(rgb) {
    return rgb.reduce((sum, value, index) => {
      const channel = value / 255;
      return sum + (channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4) * [.2126, .7152, .0722][index];
    }, 0);
  }

  const channels = hex => [1, 3, 5].map(start => parseInt(hex.slice(start, start + 2), 16));
  const hexColor = rgb => `#${rgb.map(channel => channel.toString(16).padStart(2, "0")).join("")}`;
  function contrast(a, b) {
    const first = luminance(a), second = luminance(b);
    return (Math.max(first, second) + .05) / (Math.min(first, second) + .05);
  }

  function readableAccent(hex, dark) {
    const original = channels(hex);
    const background = dark ? luminance([14, 19, 26]) : 1;
    let rgb = original;
    for (let step = 0; step <= 100; step++) {
      rgb = original.map(value => Math.round(value + ((dark ? 255 : 0) - value) * step / 100));
      const foreground = luminance(rgb);
      if ((Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05) >= 5) break;
    }
    const onAccent = (luminance(rgb) + .05) / (luminance([7, 16, 25]) + .05) > 1.05 / (luminance(rgb) + .05) ? "#071019" : "#ffffff";
    return { accent: hexColor(rgb), onAccent };
  }

  function surfaceText(background) {
    const white = [255, 255, 255], ink = [7, 16, 25], black = [0, 0, 0];
    // Prefer softer ink when possible; mid-tone backgrounds may require true black.
    return contrast(ink, background) >= 5 ? ink
      : contrast(white, background) >= contrast(black, background) ? white : black;
  }

  function bubblePalette(accent, strength, dark, quoteColor) {
    const panel = dark ? [14, 19, 26] : [255, 255, 255];
    const color = channels(accent);
    const background = panel.map((channel, index) => Math.round(channel + (color[index] - channel) * strength / 100));
    const text = surfaceText(background);
    const lightText = text[0] === 255;
    const inset = background.map(channel => Math.round(channel + ((lightText ? 0 : 255) - channel) * .06));
    // Quotes need a stronger recessed surface in dark themes and dark filled bubbles.
    const quote = quoteColor ? channels(quoteColor)
      : dark || lightText ? background.map(channel => Math.round(channel * .78)) : inset;
    const link = contrast(color, background) >= 5 && contrast(color, inset) >= 5 ? color : text;
    return { text: hexColor(text), link: hexColor(link), inset: hexColor(inset),
      quote: hexColor(quote), quoteText: hexColor(surfaceText(quote)) };
  }

  // The page and the settings preview share the same color projection.
  // Only Auto's CSS consumes these tokens; explicit themes keep their own palette.
  function paintAutoColors(target, value) {
    const colors = normalizeAutoColors(value);
    for (const [mode, dark] of [["light", false], ["dark", true]]) {
      const palette = colors.accent ? readableAccent(colors.accent, dark) : null;
      const bubble = bubblePalette(palette?.accent || (dark ? "#8ec7ff" : "#0f766e"), colors.bubble_intensity, dark, colors.quote_color);
      for (const [name, color] of [["accent", palette?.accent], ["on-accent", palette?.onAccent],
        ["bubble-text", bubble.text], ["bubble-link", bubble.link], ["bubble-inset", bubble.inset],
        ["quote-background", bubble.quote], ["quote-text", bubble.quoteText]]) {
        const key = `--portal-auto-${name}-${mode}`;
        target.style.setProperty(key, color || "initial");
      }
    }
    target.style.setProperty("--portal-auto-bubble-strength", `${colors.bubble_intensity}%`);
    return colors;
  }

  function applyAppearance(preferences) {
    applyMotionMode(preferences?.appearance?.motion);
    paintAutoColors(document.documentElement, preferences?.appearance?.auto_colors);
  }

  window.PortalTheme = Object.freeze({
    options: () => MODES.map(id => ({id, label: LABELS[id]})),
    mode: readMode,
    setMode(mode) { writeMode(mode); applyMode(mode); },
    normalizeAutoColors,
    paintAutoColors,
  });
  window.addEventListener("portal:language-changed", () => {
    for (const [mode, source] of Object.entries({auto: "Auto", light: "Light", dark: "Dark", fire: "Fire", deepsea: "Deep Sea", forest: "Forest", scifi: "Sci-Fi", moonwalker: "Moon Walker"})) LABELS[mode] = window.PortalI18n.t(source);
    applyMode(readMode());
  });

  function normalizeMotionMode(mode) {
    return MOTION_MODES.includes(mode) ? mode : "system";
  }

  function applyMotionMode(mode) {
    const next = normalizeMotionMode(mode);
    document.documentElement.dataset.portalMotion = next;
    return next;
  }

  async function syncAppearancePreferences() {
    if (!window.PortalIdentity?.loadPreferences) return;
    try {
      const preferences = await window.PortalIdentity.loadPreferences();
      applyAppearance(preferences);
    } catch {
      // Keep the system preference when identity is temporarily unavailable.
    }
  }

  window.PortalMotion = Object.freeze({
    apply: applyMotionMode,
    mode: () => normalizeMotionMode(document.documentElement.dataset.portalMotion),
    modes: MOTION_MODES.slice(),
  });

  function readMode() {
    const value = window.PortalState.storage.readText(STORAGE_KEY, "auto");
    return MODES.includes(value) ? value : "auto";
  }

  function writeMode(mode) {
    if (mode === "auto") window.PortalState.storage.remove(STORAGE_KEY);
    else window.PortalState.storage.writeText(STORAGE_KEY, mode);
  }

  // Theme scenery belongs to the shell, independent of page layouts or work state.
  function syncThemeScene(mode) {
    if (!document.body) return;
    const current = document.querySelector("[data-portal-theme-scene]");
    if (mode !== "moonwalker") {
      current?.remove();
      return;
    }
    if (current) return;
    const scene = document.createElement("div");
    scene.className = "portalThemeScene";
    scene.dataset.portalThemeScene = "moonwalker";
    scene.setAttribute("aria-hidden", "true");
    scene.inert = true;
    for (const layer of ["Stars", "Moon", "Dust", "Footsteps"]) {
      const element = document.createElement("span");
      element.className = `portalThemeScene${layer}`;
      scene.append(element);
    }
    document.body.append(scene);
  }

  function applyMode(mode) {
    const next = MODES.includes(mode) ? mode : "auto";
    document.documentElement.dataset.portalTheme = next;
    document.documentElement.style.colorScheme = next === "light"
      ? "light"
      : DARK_MODES.has(next) ? "dark" : "light dark";
    syncThemeScene(next);
    document.querySelectorAll("[data-portal-theme-toggle]").forEach((button) => {
      button.dataset.themeMode = next;
      button.setAttribute("aria-label", window.PortalI18n.text`Theme: ${LABELS[next]}`);
      button.setAttribute("title", window.PortalI18n.text`Theme: ${LABELS[next]}`);
      const icon = button.querySelector("[data-theme-icon]");
      const label = button.querySelector("[data-theme-label]");
      if (icon) {
        icon.dataset.portalIcon = `theme-${next}`;
        window.PortalUIPrimitives?.mountIcon?.(icon);
      }
      if (label) label.textContent = LABELS[next];
    });
    document.querySelectorAll("[data-portal-theme-option]").forEach((option) => {
      const active = option.dataset.portalThemeOption === next;
      option.setAttribute("aria-checked", active ? "true" : "false");
    });
    window.dispatchEvent(new CustomEvent("portal:theme-changed", { detail: { mode: next } }));
  }

  function nextMode(mode) {
    return MODES[(MODES.indexOf(mode) + 1) % MODES.length] || "auto";
  }

  function createButton() {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "portalButton is-compact is-quiet portalThemeButton";
    button.dataset.portalThemeToggle = "1";
    button.setAttribute("aria-haspopup", "menu");
    button.innerHTML = window.PortalI18n.markup('<span data-theme-icon data-portal-icon="theme-auto"></span><span data-theme-label></span>');
    button.addEventListener("click", () => {
      const mode = nextMode(readMode());
      writeMode(mode);
      applyMode(mode);
    });
    return button;
  }

  function createThemePicker() {
    const picker = document.createElement("div");
    picker.className = "portalThemePicker";
    const button = createButton();
    const menu = document.createElement("div");
    menu.className = "portalThemeMenu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", window.PortalI18n.t("Theme modes"));
    menu.hidden = true;
    menu.innerHTML = MODES.map((mode) => window.PortalI18n.html`
      <button class="portalThemeOption" data-portal-icon="theme-${mode}" type="button" role="menuitemradio" data-portal-theme-option="${mode}" aria-checked="false">
        <span>${LABELS[mode]}</span>
      </button>
    `).join("");
    menu.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target.closest("[data-portal-theme-option]") : null;
      if (!target) return;
      const mode = target.dataset.portalThemeOption || "auto";
      writeMode(mode);
      applyMode(mode);
      themePopover.close();
    });
    picker.append(button, menu);
    const themePopover = window.PortalOverlay.bindPopover({
      trigger: button,
      panel: menu,
      placement: "bottom-end",
      margin: 8,
      gap: 7,
    });
    let closeTimer = 0;
    let pointerInPicker = false;
    let pointerInMenu = false;
    let pointer = { x: 0, y: 0 };
    const trackPointer = event => {
      pointer = { x: event.clientX, y: event.clientY };
    };
    const rectContains = (rect, x, y, padding = 0) => (
      x >= rect.left - padding
      && x <= rect.right + padding
      && y >= rect.top - padding
      && y <= rect.bottom + padding
    );
    const pointerInHoverBridge = () => {
      if (menu.hidden) return false;
      const buttonRect = button.getBoundingClientRect();
      const menuRect = menu.getBoundingClientRect();
      const x = pointer.x;
      const y = pointer.y;
      if (rectContains(buttonRect, x, y, 2) || rectContains(menuRect, x, y, 2)) return true;
      const bridge = {
        left: Math.min(buttonRect.left, menuRect.left) - 6,
        right: Math.max(buttonRect.right, menuRect.right) + 6,
        top: Math.min(buttonRect.bottom, menuRect.bottom) - 2,
        bottom: Math.max(buttonRect.top, menuRect.top) + 2,
      };
      return bridge.top <= bridge.bottom && rectContains(bridge, x, y);
    };
    const cancelHoverClose = () => {
      if (!closeTimer) return;
      clearTimeout(closeTimer);
      closeTimer = 0;
    };
    const closeAfterHoverGap = () => {
      cancelHoverClose();
      closeTimer = setTimeout(() => {
        closeTimer = 0;
        if (pointerInPicker || pointerInMenu || pointerInHoverBridge()) return;
        themePopover.close();
      }, 140);
    };
    const syncHoverClose = () => {
      if (!themePopover.isOpen()) return;
      if (pointerInPicker || pointerInMenu || pointerInHoverBridge()) {
        cancelHoverClose();
      } else {
        closeAfterHoverGap();
      }
    };
    const eventHitThemeMenu = event => {
      const hit = document.elementFromPoint(event.clientX, event.clientY);
      return !!hit?.closest?.(".portalThemeMenu");
    };
    const closeIfFocusLeft = () => window.requestAnimationFrame(() => {
      if (!picker.contains(document.activeElement)) themePopover.close();
    });
    picker.addEventListener("pointerenter", (event) => {
      trackPointer(event);
      if (eventHitThemeMenu(event)) {
        pointerInMenu = true;
        cancelHoverClose();
        return;
      }
      pointerInPicker = true;
      cancelHoverClose();
      themePopover.open();
    });
    picker.addEventListener("pointerleave", (event) => {
      trackPointer(event);
      pointerInPicker = false;
      syncHoverClose();
    });
    menu.addEventListener("pointerenter", (event) => {
      trackPointer(event);
      pointerInMenu = true;
      cancelHoverClose();
    });
    menu.addEventListener("pointerleave", (event) => {
      trackPointer(event);
      pointerInMenu = false;
      syncHoverClose();
    });
    document.addEventListener("pointermove", (event) => {
      trackPointer(event);
      syncHoverClose();
    }, true);
    picker.addEventListener("focusin", () => {
      cancelHoverClose();
      themePopover.open();
    });
    picker.addEventListener("focusout", closeIfFocusLeft);
    return picker;
  }

  function mountToggle() {
    syncThemeScene(readMode());
    // Appearance can initialize even when this optional control's
    // dependency has not loaded successfully yet.
    if (!window.PortalOverlay) return;
    if (document.querySelector("[data-portal-theme-toggle]")) {
      applyMode(readMode());
      return;
    }
    const userArea = document.querySelector(".portalUser");
    const navTop = document.querySelector(".portalNavTop");
    const loginHeader = document.querySelector("header");
    const target = userArea || navTop || loginHeader;
    if (!target) return;
    target.insertBefore(createThemePicker(), target.querySelector(":scope > [data-portal-user-menu]"));
    applyMode(readMode());
  }

  window.addEventListener("portal:overlay-ready", () => {
    if (document.readyState !== "loading") mountToggle();
  });

  // A publication restarts Web with new assets. When the event stream reaches a
  // Web process started after the one this page met first, the page reloads.
  let serverStartedAt = 0;
  window.addEventListener("portal:events-ready", event => {
    const startedAt = Number(event?.detail?.server_started_at || 0);
    if (!startedAt) return;
    const restarted = serverStartedAt && startedAt > serverStartedAt + 0.001;
    serverStartedAt = Math.max(serverStartedAt, startedAt);
    if (restarted && window.location.pathname !== "/login") window.location.reload();
  });

  applyMode(readMode());
  applyAppearance(window.__PORTAL_IDENTITY_BOOTSTRAP__?.preferences);
  window.addEventListener("portal:user-settings-changed", event => {
    applyAppearance(event.detail?.preferences);
  });
  window.addEventListener("portal:identity-changed", event => {
    applyAppearance(event.detail?.identity?.preferences);
  });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      mountToggle();
      syncAppearancePreferences();
    });
  } else {
    mountToggle();
    syncAppearancePreferences();
  }
})();
