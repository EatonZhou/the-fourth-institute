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
    // Appearance and restart observation can initialize even when this
    // optional control's dependency has not loaded successfully yet.
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

  const restartState = {
    noticeId: "",
    payload: null,
    timer: null,
    pollTimer: null,
    seenActive: false,
    restartAt: 0,
    reloading: false,
    serverStartedAt: 0,
    recoveryTimer: null,
    recoveryInFlight: false,
    overlayController: null,
    pageHidden: false,
  };

  function stopRestartCountdown() {
    if (restartState.timer) clearInterval(restartState.timer);
    restartState.timer = null;
  }

  function startRestartCountdown() {
    stopRestartCountdown();
    if (!restartState.payload || restartState.pageHidden) return;
    restartState.timer = setInterval(renderRestartNotice, 1000);
  }

  function restartNoticeEstimatedNow(payload = null) {
    const serverNow = Number(payload?.now || 0);
    const localNowAtFetch = Number(payload?.localNowAtFetch || Date.now() / 1000);
    return serverNow ? serverNow + (Date.now() / 1000 - localNowAtFetch) : Date.now() / 1000;
  }

  function waitingForServerRestart(payload = null) {
    if (restartState.reloading || !restartState.seenActive) return false;
    const restartAt = Number(restartState.restartAt || 0);
    return !!restartAt && restartNoticeEstimatedNow(payload) >= restartAt;
  }

  function ensureRestartOverlay() {
    let overlay = document.getElementById("portalRestartOverlay");
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.id = "portalRestartOverlay";
    overlay.className = "portalDetailOverlay portalRestartOverlay";
    overlay.hidden = true;
    overlay.dataset.portalOverlayLayer = "critical";
    overlay.innerHTML = window.PortalI18n.html`
      <div class="portalDetailSurface portalRestartDialog" data-portal-detail-surface role="alertdialog" aria-live="assertive" aria-labelledby="portalRestartTitle">
        <h2 class="portalRestartTitle" id="portalRestartTitle">系统即将重启</h2>
        <p class="portalRestartMessage" id="portalRestartMessage">系统将在倒计时结束后重启。</p>
        <div class="portalRestartCountdown">
          <span class="portalRestartSeconds" id="portalRestartSeconds">5</span>
          <span class="portalRestartUnit" id="portalRestartUnit">秒后重启</span>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    restartState.overlayController = window.PortalOverlay?.register(overlay, {
      kind: "critical",
      closeOnEscape: false,
      closeOnBackdrop: false,
    }) || null;
    return overlay;
  }

  function renderRestartNotice() {
    const payload = restartState.payload;
    const overlay = ensureRestartOverlay();
    const waiting = waitingForServerRestart(payload);
    if ((!payload || !payload.active) && !waiting) {
      stopRestartRecovery();
      overlay.hidden = true;
      restartState.overlayController?.deactivate();
      return;
    }
    const restartAt = Number(payload?.restart_at || restartState.restartAt || 0);
    const remaining = Math.max(0, Math.ceil(restartAt - restartNoticeEstimatedNow(payload)));
    const message = waiting ? window.PortalI18n.t("请等待后台重启") : window.PortalI18n.message(String(payload?.message || "系统将在倒计时结束后重启。"));
    const msgEl = document.getElementById("portalRestartMessage");
    const secondsEl = document.getElementById("portalRestartSeconds");
    const unitEl = document.getElementById("portalRestartUnit");
    if (msgEl) msgEl.textContent = message;
    if (secondsEl) secondsEl.textContent = String(remaining);
    if (unitEl) unitEl.textContent = waiting ? window.PortalI18n.t("后台重启中") : window.PortalI18n.t("秒后重启");
    overlay.hidden = false;
    if (!restartState.overlayController && window.PortalOverlay) {
      restartState.overlayController = window.PortalOverlay.register(overlay, {
        kind: "critical",
        closeOnEscape: false,
        closeOnBackdrop: false,
      });
    }
    restartState.overlayController?.activate();
    if (waiting) startRestartRecovery();
  }

  function setRestartNotice(payload) {
    const active = !!payload?.active;
    if (active) {
      restartState.seenActive = true;
      restartState.restartAt = Number(payload.restart_at || restartState.restartAt || 0);
    }
    restartState.payload = active || waitingForServerRestart(payload)
      ? { ...payload, localNowAtFetch: Date.now() / 1000 }
      : null;
    restartState.noticeId = active ? String(payload.notice_id || "") : "";
    stopRestartCountdown();
    renderRestartNotice();
    startRestartCountdown();
  }

  function maybeReloadAfterServerRestart(payload) {
    const serverStartedAt = Number(payload?.server_started_at || 0);
    if (!serverStartedAt) return false;
    if (!restartState.serverStartedAt) {
      restartState.serverStartedAt = serverStartedAt;
      return false;
    }
    if (restartState.reloading || window.location.pathname === "/login") return false;
    if (serverStartedAt <= restartState.serverStartedAt + 0.001) return false;
    restartState.reloading = true;
    window.location.reload();
    return true;
  }

  function stopRestartRecovery() {
    if (restartState.recoveryTimer) clearTimeout(restartState.recoveryTimer);
    restartState.recoveryTimer = null;
  }

  function startRestartRecovery() {
    if (
      restartState.reloading
      || restartState.recoveryInFlight
      || restartState.recoveryTimer
    ) return;
    restartState.recoveryTimer = setTimeout(async () => {
      restartState.recoveryTimer = null;
      if (!waitingForServerRestart()) return;
      restartState.recoveryInFlight = true;
      try {
        const health = await window.PortalAPI.get(`/api/health?restart_probe=${Date.now()}`);
        maybeReloadAfterServerRestart({ server_started_at: health?.started_at });
      } catch {
        // The expected restart outage ends when the next probe reaches the new Web process.
      } finally {
        restartState.recoveryInFlight = false;
      }
      if (!restartState.reloading && waitingForServerRestart()) {
        restartState.recoveryTimer = setTimeout(() => {
          restartState.recoveryTimer = null;
          startRestartRecovery();
        }, 500);
      }
    }, 0);
  }

  async function pollRestartNotice() {
    if (document.hidden) return;
    try {
      const payload = await window.PortalAPI.get("/api/restart-notice");
      maybeReloadAfterServerRestart(payload);
      const nextId = payload?.active ? String(payload.notice_id || "") : "";
      if (nextId !== restartState.noticeId || !!payload?.active !== !!restartState.payload?.active) {
        setRestartNotice(payload);
      } else if (payload?.active || waitingForServerRestart(payload)) {
        restartState.payload = { ...payload, localNowAtFetch: Date.now() / 1000 };
      }
    } catch {
      // Restart notice polling is best-effort and should not disturb the page.
    }
  }

  function restartNoticePollDelay() {
    if (document.hidden || window.PortalEvents?.isConnected?.()) return 300000;
    return 2000;
  }

  function scheduleRestartNoticePoll() {
    if (restartState.pollTimer) clearTimeout(restartState.pollTimer);
    restartState.pollTimer = null;
    if (restartState.pageHidden) return;
    restartState.pollTimer = setTimeout(async () => {
      await pollRestartNotice();
      scheduleRestartNoticePoll();
    }, restartNoticePollDelay());
  }

  function startRestartNoticePolling() {
    if (restartState.pollTimer) return;
    pollRestartNotice();
    scheduleRestartNoticePoll();
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollRestartNotice();
    scheduleRestartNoticePoll();
  });
  window.addEventListener("portal:change", event => {
    const detail = event?.detail || {};
    const projection = detail?.projection_details?.["restart-notice"]?.current;
    const notice = projection?.notice;
    if (notice && typeof notice === "object") {
      maybeReloadAfterServerRestart(notice);
      setRestartNotice(notice);
      scheduleRestartNoticePoll();
      return;
    }
    const targets = detail?.projections?.["restart-notice"];
    if (Array.isArray(targets) && targets.includes("current")) pollRestartNotice();
  });
  window.addEventListener("portal:events-ready", () => {
    pollRestartNotice();
    scheduleRestartNoticePoll();
  });
  window.addEventListener("pagehide", () => {
    restartState.pageHidden = true;
    stopRestartCountdown();
    if (restartState.pollTimer) clearTimeout(restartState.pollTimer);
    restartState.pollTimer = null;
    stopRestartRecovery();
  });
  window.addEventListener("pageshow", event => {
    if (!event.persisted) return;
    restartState.pageHidden = false;
    startRestartCountdown();
    pollRestartNotice();
    scheduleRestartNoticePoll();
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
      startRestartNoticePolling();
      syncAppearancePreferences();
    });
  } else {
    mountToggle();
    startRestartNoticePolling();
    syncAppearancePreferences();
  }
})();
