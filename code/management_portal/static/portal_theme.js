(function () {
  const STORAGE_KEY = window.PortalCompatibility.identifier("theme");
  const MODE_NAMES = {
    auto: "Auto",
    light: "Light",
    dark: "Dark",
    fire: "Fire",
    deepsea: "Deep Sea",
    forest: "Forest",
    scifi: "Sci-Fi",
    moonwalker: "Moon Walker",
    "liquid-glass": "Liquid Glass",
  };
  const MODES = Object.keys(MODE_NAMES);
  const DARK_MODES = new Set(["dark", "fire", "deepsea", "forest", "scifi", "moonwalker"]);
  const modeLabel = mode => window.PortalI18n.t(MODE_NAMES[mode]);
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
    options: () => MODES.map(id => ({id, label: modeLabel(id)})),
    mode: readMode,
    setMode(mode) { writeMode(mode); applyMode(mode); },
    normalizeAutoColors,
    paintAutoColors,
  });
  window.addEventListener("portal:language-changed", () => {
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

  // Encode the bevel's inward displacement in red and green for feDisplacementMap.
  // The flat center has no offset. A gradual curve at the rim keeps the
  // projection from folding back onto itself and duplicating the backdrop.
  function glassDisplacementMap(width, height, cornerRadius) {
    const canvas = document.createElement("canvas");
    canvas.width = Math.min(1536, Math.ceil(width / 2));
    canvas.height = Math.min(512, Math.ceil(height / 2));
    const context = canvas.getContext("2d");
    if (!context) return "";
    const pixels = context.createImageData(canvas.width, canvas.height);
    const radius = Math.min(cornerRadius, width / 2, height / 2);
    const straightX = width / 2 - radius;
    const straightY = height / 2 - radius;
    const bevel = Math.min(height <= 44 ? 10 : 14, radius);
    for (let row = 0; row < canvas.height; row++) {
      const y = (row + .5) * height / canvas.height - height / 2;
      for (let column = 0; column < canvas.width; column++) {
        const x = (column + .5) * width / canvas.width - width / 2;
        const edgeX = Math.abs(x) - straightX;
        const edgeY = Math.abs(y) - straightY;
        const normalX = Math.sign(x) * Math.max(0, edgeX);
        const normalY = Math.sign(y) * Math.max(0, edgeY);
        const length = Math.hypot(normalX, normalY) + Math.min(0, Math.max(edgeX, edgeY));
        const depth = radius - length;
        const bend = depth > 0 && depth < bevel
          ? -.18 * (1 - depth / bevel) ** 1.45
          : 0;
        const offset = (row * canvas.width + column) * 4;
        pixels.data[offset] = 128 + 255 * bend * normalX / (length || 1);
        pixels.data[offset + 1] = 128 + 255 * bend * normalY / (length || 1);
        pixels.data[offset + 3] = 255;
      }
    }
    context.putImageData(pixels, 0, 0);
    return canvas.toDataURL("image/png");
  }

  function attachGlassRefraction(svgElement, header, reducedTransparency, signal) {
    const surfaceSelector = [
      ".portalActivityRail",
      ".portalNavGlassDroplet",
      ".portalAgentInbox",
      ".graphNavigationBar",
      '[data-portal-overlay-layer="popover"]',
      ".portalThemeMenu",
      ".portalUserMenuPopover",
      ".portalSecondaryNavPopover",
      ".portalToolbarOverflowPanel",
      ".portalWorkbenchMenu",
      ".portalMessageMenuPanel",
      ".filterPopover",
    ].join(", ");
    const MAX_REFRACTION_SURFACES = 10;
    const RESIZE_DEBOUNCE_MS = 80;
    const svg = svgElement("svg");
    svg.setAttribute("class", "portalGlassFilters");
    svg.setAttribute("aria-hidden", "true");
    header.after(svg);
    let nextId = 0;
    let syncFrame = 0;
    const surfaces = new Map();

    function cancelTimer(optics) {
      if (optics.timer) {
        clearTimeout(optics.timer);
        optics.timer = 0;
      }
      if (optics.raf) {
        cancelAnimationFrame(optics.raf);
        optics.raf = 0;
      }
    }

    function isTrackable(target) {
      if (!target || !target.isConnected) return false;
      if (target === header) return true;
      if (target.hidden) return false;
      if (!(target.offsetWidth > 0 && target.offsetHeight > 0)) return false;
      return getComputedStyle(target).display !== "none";
    }

    function applyRebuild(target) {
      const optics = surfaces.get(target);
      if (!optics || !isTrackable(target) || reducedTransparency.matches) return;
      const pane = getComputedStyle(target, target === header ? "::before" : null);
      const rawWidth = target === header ? parseFloat(pane.width) : target.offsetWidth;
      const rawHeight = target === header ? parseFloat(pane.height) : target.offsetHeight;
      const width = Math.round(rawWidth / 2) * 2;
      const height = Math.round(rawHeight / 2) * 2;
      const radius = Math.round(parseFloat(pane.borderTopLeftRadius));
      if (!(width > 0 && height > 0 && radius > 0)) return;
      const size = [width, height, radius].join(":");
      if (optics.size === size) return;
      const map = glassDisplacementMap(width, height, radius);
      if (!map) return;
      optics.size = size;
      optics.image.setAttribute("href", map);
      target.style.setProperty("--liquid-glass-filter", `url("#${optics.filter.id}")`);
    }

    function scheduleRebuild({ target }) {
      const optics = surfaces.get(target);
      if (!optics || reducedTransparency.matches) return;
      cancelTimer(optics);
      if (!optics.size) {
        optics.raf = requestAnimationFrame(() => {
          optics.raf = 0;
          applyRebuild(target);
        });
        return;
      }
      optics.timer = setTimeout(() => {
        optics.timer = 0;
        applyRebuild(target);
      }, RESIZE_DEBOUNCE_MS);
    }
    const resize = new ResizeObserver(entries => entries.forEach(scheduleRebuild));

    function removeSurface(target, optics) {
      cancelTimer(optics);
      resize.unobserve(target);
      target.style.removeProperty("--liquid-glass-filter");
      optics.filter.remove();
      surfaces.delete(target);
    }
    function trackSurfaces() {
      for (const [target, optics] of surfaces) {
        if (!isTrackable(target)) removeSurface(target, optics);
      }
      for (const target of [header, ...document.querySelectorAll(surfaceSelector)]) {
        if (surfaces.has(target) || !isTrackable(target)) continue;
        if (surfaces.size >= MAX_REFRACTION_SURFACES) break;
        const filter = svgElement("filter");
        const image = svgElement("feImage");
        const displacement = svgElement("feDisplacementMap");
        filter.id = `portal-liquid-glass-bevel-${++nextId}`;
        filter.setAttribute("x", "0");
        filter.setAttribute("y", "0");
        filter.setAttribute("width", "1");
        filter.setAttribute("height", "1");
        filter.setAttribute("color-interpolation-filters", "sRGB");
        image.setAttribute("preserveAspectRatio", "none");
        image.setAttribute("result", "bevel");
        displacement.setAttribute("in", "SourceGraphic");
        displacement.setAttribute("in2", "bevel");
        const isGentle = target.classList.contains("portalNavGlassDroplet");
        displacement.setAttribute("scale", isGentle ? "14" : "22");
        displacement.setAttribute("xChannelSelector", "R");
        displacement.setAttribute("yChannelSelector", "G");
        filter.append(image, displacement);
        svg.append(filter);
        surfaces.set(target, { filter, image, size: "", timer: 0, raf: 0 });
        if (!reducedTransparency.matches) {
          resize.observe(target);
          applyRebuild(target);
        }
      }
    }
    function queueTrackSurfaces() {
      if (syncFrame) return;
      syncFrame = requestAnimationFrame(() => {
        syncFrame = 0;
        trackSurfaces();
      });
    }
    const mounts = new MutationObserver(records => {
      const hasSurface = node => node instanceof Element &&
        (node.matches(surfaceSelector) || node.querySelector(surfaceSelector));
      const changed = records.some(record => (
        record.type === "attributes"
          ? record.target instanceof Element && (surfaces.has(record.target) || record.target.matches(surfaceSelector))
          : [...record.addedNodes, ...record.removedNodes].some(hasSurface)
      ));
      if (changed) queueTrackSurfaces();
    });
    mounts.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["hidden", "data-portal-overlay-layer", "class"],
    });
    document.addEventListener("portal:overlaychange", queueTrackSurfaces, { signal });
    reducedTransparency.addEventListener("change", () => {
      if (reducedTransparency.matches) {
        resize.disconnect();
        surfaces.forEach((optics, surface) => {
          cancelTimer(optics);
          surface.style.removeProperty("--liquid-glass-filter");
        });
      } else {
        surfaces.forEach((optics, surface) => {
          optics.size = "";
          resize.observe(surface);
          applyRebuild(surface);
        });
      }
    }, { signal });
    trackSurfaces();
    return () => {
      if (syncFrame) {
        cancelAnimationFrame(syncFrame);
        syncFrame = 0;
      }
      mounts.disconnect();
      for (const [target, optics] of surfaces) removeSurface(target, optics);
      svg.remove();
    };
  }

  function attachNavGlassDroplet(header, reduceMotion, signal) {
    let nav = null;
    let droplet = null;
    let navController = null;
    let resizeObserver = null;
    let raf = 0;
    let lastTime = 0;

    let visible = false;
    let initialized = false;
    let hoveredLink = null;
    let pressed = false;
    let dragging = false;

    let curX = 0;
    let curY = 5;
    let curW = 0;
    let curH = 28;
    let curPress = 0;
    let vx = 0;
    let vw = 0;

    let targetX = 0;
    let targetY = 5;
    let targetW = 0;
    let targetH = 28;

    const isReducedMotion = () => {
      const motion = window.PortalMotion.mode();
      return motion === "reduced" || (motion === "system" && reduceMotion.matches);
    };

    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

    const measureLink = link => {
      if (!nav || !link || !link.isConnected) return null;
      const label = link.querySelector(":scope > .portalModuleLinkLabel") || link;
      const navRect = nav.getBoundingClientRect();
      const rect = label.getBoundingClientRect();
      if (!(rect.width > 0 && rect.height > 0)) return null;
      const padX = 2;
      const padY = 1;
      return {
        x: rect.left - navRect.left + nav.scrollLeft - padX,
        y: rect.top - navRect.top + nav.scrollTop - padY,
        w: rect.width + padX * 2,
        h: rect.height + padY * 2,
      };
    };

    const getLinks = () => (nav ? Array.from(nav.querySelectorAll(":scope > .portalModuleLink[data-module-link]")) : []);

    const findNearestLink = localX => {
      const links = getLinks();
      let best = null;
      let bestDist = Infinity;
      for (const link of links) {
        const box = measureLink(link);
        if (!box) continue;
        const center = box.x + box.w / 2;
        const dist = Math.abs(localX - center);
        if (dist < bestDist) {
          bestDist = dist;
          best = { link, box };
        }
      }
      return best;
    };

    const paint = (sx, sy, skew, light) => {
      if (!droplet) return;
      droplet.style.setProperty("--lg-drop-x", `${curX.toFixed(2)}px`);
      droplet.style.setProperty("--lg-drop-y", `${curY.toFixed(2)}px`);
      droplet.style.setProperty("--lg-drop-w", `${Math.max(0, curW).toFixed(2)}px`);
      droplet.style.setProperty("--lg-drop-h", `${Math.max(0, curH).toFixed(2)}px`);
      droplet.style.setProperty("--lg-drop-sx", sx.toFixed(4));
      droplet.style.setProperty("--lg-drop-sy", sy.toFixed(4));
      droplet.style.setProperty("--lg-drop-skew", `${skew.toFixed(2)}deg`);
      droplet.style.setProperty("--lg-drop-light", `${light.toFixed(1)}%`);
      droplet.style.setProperty("--lg-drop-opacity", visible ? "1" : "0");
    };

    const stopLoop = () => {
      if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
      lastTime = 0;
    };

    const step = now => {
      raf = 0;
      if (!droplet || !droplet.isConnected) return;
      if (isReducedMotion()) {
        curX = targetX;
        curY = targetY;
        curW = targetW;
        curH = targetH;
        curPress = 0;
        vx = 0;
        vw = 0;
        paint(1, 1, 0, 50);
        return;
      }
      const dt = lastTime ? clamp((now - lastTime) / 16.667, 0.35, 2.0) : 1;
      lastTime = now;

      const prevX = curX;
      const followRate = dragging ? 0.44 : 0.30;
      const stepAlpha = 1 - Math.exp(-followRate * dt);
      curX += (targetX - curX) * stepAlpha;
      curW += (targetW - curW) * stepAlpha;
      curY += (targetY - curY) * Math.min(1, 0.42 * dt);
      curH += (targetH - curH) * Math.min(1, 0.42 * dt);

      const rawVx = (curX - prevX) / dt;
      vx += (rawVx - vx) * Math.min(1, 0.48 * dt);
      vw = 0;

      const pressTarget = pressed ? 0.055 : 0;
      curPress += (pressTarget - curPress) * Math.min(1, 0.35 * dt);

      const speed = Math.min(Math.abs(vx), 40);
      const dragStretch = dragging ? Math.min(0.14, speed * 0.006 + 0.045) : 0;
      const stretch = Math.min(0.28, speed * 0.0085 + dragStretch);
      const sx = 1 + stretch + curPress;
      const sy = 1 / Math.sqrt(sx);
      const skew = clamp(-vx * (dragging ? 0.42 : 0.28), -9, 9);
      const light = clamp(50 + vx * 1.6, 16, 84);

      paint(sx, sy, skew, light);

      const settled = Math.abs(targetX - curX) < 0.08
        && Math.abs(targetW - curW) < 0.08
        && Math.abs(vx) < 0.06
        && Math.abs(pressTarget - curPress) < 0.002;
      if (!settled) {
        raf = requestAnimationFrame(step);
      } else {
        curX = targetX;
        curY = targetY;
        curW = targetW;
        curH = targetH;
        vx = 0;
        vw = 0;
        const restSx = 1 + curPress;
        paint(restSx, 1 / Math.sqrt(restSx), 0, 50);
        lastTime = 0;
      }
    };

    const wake = () => {
      if (isReducedMotion()) {
        stopLoop();
        curX = targetX;
        curY = targetY;
        curW = targetW;
        curH = targetH;
        curPress = 0;
        vx = 0;
        vw = 0;
        paint(1, 1, 0, 50);
        return;
      }
      if (!raf) raf = requestAnimationFrame(step);
    };

    const setTargetBox = (box, immediate) => {
      if (!box) return;
      targetX = box.x;
      targetY = box.y;
      targetW = box.w;
      targetH = box.h;
      if (!initialized || immediate || !visible) {
        curX = box.x;
        curY = box.y;
        curW = box.w;
        curH = box.h;
        vx = 0;
        vw = 0;
        initialized = true;
      }
      visible = true;
      if (immediate || isReducedMotion()) {
        stopLoop();
        paint(1, 1, 0, 50);
      } else {
        wake();
      }
    };

    const syncRestingTarget = immediate => {
      if (!nav || !droplet) return;
      const active = hoveredLink && hoveredLink.isConnected
        ? hoveredLink
        : nav.querySelector(":scope > .portalModuleLink.active");
      const box = active ? measureLink(active) : null;
      if (box) {
        setTargetBox(box, immediate);
      } else {
        visible = false;
        initialized = false;
        stopLoop();
        paint(1, 1, 0, 50);
      }
    };

    const setDraggingState = next => {
      dragging = next;
      if (!droplet) return;
      if (next) droplet.dataset.lgDragging = "true";
      else delete droplet.dataset.lgDragging;
    };

    const handlePointerAtX = (clientX, dragForce) => {
      if (!nav) return;
      const navRect = nav.getBoundingClientRect();
      const localX = clientX - navRect.left + nav.scrollLeft;
      const nearest = findNearestLink(localX);
      if (!nearest) return;
      hoveredLink = nearest.link;
      const { box } = nearest;
      if (dragForce) {
        const pointerBoxX = localX - box.w / 2;
        const blendedX = pointerBoxX * 0.72 + box.x * 0.28;
        const minX = 0;
        const maxX = Math.max(0, nav.scrollWidth - box.w);
        setTargetBox({
          x: clamp(blendedX, minX, maxX),
          y: box.y,
          w: box.w,
          h: box.h,
        }, false);
      } else {
        setTargetBox({
          x: box.x,
          y: box.y,
          w: box.w,
          h: box.h,
        }, false);
      }
    };

    const unmountNav = () => {
      stopLoop();
      navController?.abort();
      navController = null;
      resizeObserver?.disconnect();
      resizeObserver = null;
      if (nav) delete nav.dataset.lgDropletReady;
      droplet?.remove();
      droplet = null;
      nav = null;
      hoveredLink = null;
      pressed = false;
      dragging = false;
      visible = false;
      initialized = false;
    };

    const mountNav = candidate => {
      if (!candidate || candidate === nav) return;
      unmountNav();
      nav = candidate;
      droplet = document.createElement("span");
      droplet.className = "portalNavGlassDroplet";
      droplet.setAttribute("aria-hidden", "true");
      // Append at the end so portal_nav.js applyItemOrder(root.firstElementChild)
      // stays completely untouched.
      nav.append(droplet);
      nav.dataset.lgDropletReady = "true";

      navController = new AbortController();
      const passiveOpts = { passive: true, signal: navController.signal };

      nav.addEventListener("pointerover", event => {
        const link = event.target.closest?.(".portalModuleLink[data-module-link]");
        if (!link || !nav.contains(link)) return;
        hoveredLink = link;
        const box = measureLink(link);
        if (box) setTargetBox(box, false);
      }, passiveOpts);

      nav.addEventListener("pointermove", event => {
        const isHeldDrag = Boolean(event.buttons & 1);
        if (isHeldDrag && !dragging) setDraggingState(true);
        else if (!isHeldDrag && dragging && !pressed) setDraggingState(false);
        handlePointerAtX(event.clientX, isHeldDrag);
      }, passiveOpts);

      nav.addEventListener("pointerdown", event => {
        if (!event.isPrimary || event.button !== 0) return;
        pressed = true;
        handlePointerAtX(event.clientX, false);
        wake();
      }, passiveOpts);

      nav.addEventListener("pointerleave", () => {
        hoveredLink = null;
        if (!dragging) syncRestingTarget(false);
      }, passiveOpts);

      nav.addEventListener("dragstart", () => {
        setDraggingState(true);
        pressed = true;
        wake();
      }, passiveOpts);

      nav.addEventListener("dragover", event => {
        if (!dragging) setDraggingState(true);
        handlePointerAtX(event.clientX, true);
      }, passiveOpts);

      const finishDrag = () => {
        if (!dragging && !pressed) return;
        pressed = false;
        setDraggingState(false);
        hoveredLink = null;
        syncRestingTarget(false);
      };
      nav.addEventListener("drop", finishDrag, passiveOpts);
      nav.addEventListener("dragend", finishDrag, passiveOpts);
      window.addEventListener("pointerup", () => {
        if (!pressed && !dragging) return;
        pressed = false;
        setDraggingState(false);
        wake();
      }, passiveOpts);
      window.addEventListener("pointercancel", finishDrag, passiveOpts);

      nav.addEventListener("focusin", event => {
        const link = event.target.closest?.(".portalModuleLink[data-module-link]");
        if (!link || !nav.contains(link)) return;
        hoveredLink = link;
        const box = measureLink(link);
        if (box) setTargetBox(box, false);
      }, passiveOpts);

      nav.addEventListener("focusout", () => {
        hoveredLink = null;
        syncRestingTarget(false);
      }, passiveOpts);

      nav.addEventListener("scroll", () => {
        if (visible) syncRestingTarget(true);
      }, passiveOpts);

      if (window.ResizeObserver) {
        resizeObserver = new ResizeObserver(() => syncRestingTarget(true));
        resizeObserver.observe(nav);
      }

      syncRestingTarget(true);
    };

    const ensureMounted = () => {
      const candidate = header.querySelector(".portalModuleNav");
      if (!candidate) {
        unmountNav();
        return;
      }
      if (candidate !== nav) {
        mountNav(candidate);
      } else {
        if (droplet && !droplet.isConnected) nav.append(droplet);
        if (!dragging) syncRestingTarget(false);
      }
    };

    const observer = new MutationObserver(() => ensureMounted());
    observer.observe(header, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class"],
    });

    reduceMotion.addEventListener("change", () => syncRestingTarget(true), { signal });
    ensureMounted();

    return () => {
      observer.disconnect();
      unmountNav();
    };
  }

  function attachComposerOverlap() {
    const tracked = new Map();
    const updateRoot = root => {
      const composer = root.querySelector(":scope > .composer");
      if (!composer || composer.hidden || !(composer.offsetHeight > 0)) {
        root.style.removeProperty("--lg-composer-overlap");
        return;
      }
      const splitter = root.querySelector(":scope > .composerSplitter");
      const cs = getComputedStyle(composer);
      const marginY = (parseFloat(cs.marginTop) || 0) + (parseFloat(cs.marginBottom) || 0);
      const splitterH = splitter && !splitter.hidden ? splitter.offsetHeight : 0;
      const overlap = Math.max(0, Math.round(composer.offsetHeight + splitterH + marginY));
      root.style.setProperty("--lg-composer-overlap", `${overlap}px`);
    };

    const resize = window.ResizeObserver
      ? new ResizeObserver(entries => {
        for (const entry of entries) {
          const root = entry.target.closest?.(".portalChatWorkspaceRoot");
          if (root && tracked.has(root)) updateRoot(root);
        }
      })
      : null;

    const syncRoots = () => {
      for (const [root, observed] of tracked) {
        if (!root.isConnected) {
          if (observed) resize?.unobserve(observed);
          root.style.removeProperty("--lg-composer-overlap");
          tracked.delete(root);
        }
      }
      for (const root of document.querySelectorAll(".portalChatWorkspaceRoot")) {
        const composer = root.querySelector(":scope > .composer");
        const prev = tracked.get(root);
        if (prev && prev !== composer) resize?.unobserve(prev);
        if (composer && prev !== composer) resize?.observe(composer);
        tracked.set(root, composer || null);
        updateRoot(root);
      }
    };

    const observer = new MutationObserver(() => syncRoots());
    if (document.body) observer.observe(document.body, { subtree: true, childList: true });
    syncRoots();

    return () => {
      observer.disconnect();
      resize?.disconnect();
      for (const root of tracked.keys()) root.style.removeProperty("--lg-composer-overlap");
      tracked.clear();
    };
  }

  let disposeGlass = null;
  function syncGlassOptics(mode) {
    if (mode !== "liquid-glass") {
      disposeGlass?.();
      disposeGlass = null;
      return;
    }
    if (disposeGlass) return;
    const header = document.querySelector(".portalSiteHeader");
    if (!header) return;
    const controller = new AbortController();
    const listener = { passive: true, signal: controller.signal };
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let lightFrame = 0;
    let pendingClientX = 0;
    const resetLight = () => {
      if (lightFrame) {
        cancelAnimationFrame(lightFrame);
        lightFrame = 0;
      }
      header.style.removeProperty("--liquid-glass-light");
    };
    const flushLight = () => {
      lightFrame = 0;
      const bounds = header.getBoundingClientRect();
      if (bounds.width) {
        const ratio = Math.min(100, Math.max(0, 100 * (pendingClientX - bounds.left) / bounds.width));
        header.style.setProperty("--liquid-glass-light", `${ratio.toFixed(2)}%`);
      }
    };
    header.addEventListener("pointermove", event => {
      const motion = window.PortalMotion.mode();
      if (motion === "reduced" || (motion === "system" && reduceMotion.matches)) {
        resetLight();
        return;
      }
      pendingClientX = event.clientX;
      if (!lightFrame) lightFrame = requestAnimationFrame(flushLight);
    }, listener);
    header.addEventListener("pointerleave", resetLight, listener);
    reduceMotion.addEventListener("change", resetLight, listener);

    const disposeDroplet = attachNavGlassDroplet(header, reduceMotion, controller.signal);
    const disposeComposerOverlap = attachComposerOverlap();

    // Chromium bends the live backdrop. Standard blur and reflections also
    // render in engines where the displacement is ignored.
    const svgElement = name => document.createElementNS("http://www.w3.org/2000/svg", name);
    const reducedTransparency = window.matchMedia("(prefers-reduced-transparency: reduce), (prefers-contrast: more), (forced-colors: active)");
    const disposeRefraction = window.CSS?.supports("backdrop-filter", 'url("#portal-glass-filter")') && window.ResizeObserver
      ? attachGlassRefraction(svgElement, header, reducedTransparency, controller.signal)
      : null;
    disposeGlass = () => {
      controller.abort();
      disposeRefraction?.();
      disposeComposerOverlap?.();
      disposeDroplet?.();
      resetLight();
    };
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
    document.documentElement.style.colorScheme = next === "light" || next === "liquid-glass"
      ? "light"
      : DARK_MODES.has(next) ? "dark" : "light dark";
    syncThemeScene(next);
    syncGlassOptics(next);
    const name = modeLabel(next);
    document.querySelectorAll("[data-portal-theme-toggle]").forEach((button) => {
      button.dataset.themeMode = next;
      button.setAttribute("aria-label", window.PortalI18n.text`Theme: ${name}`);
      button.setAttribute("title", window.PortalI18n.text`Theme: ${name}`);
      const icon = button.querySelector("[data-theme-icon]");
      const label = button.querySelector("[data-theme-label]");
      if (icon) {
        icon.dataset.portalIcon = `theme-${next}`;
        window.PortalUIPrimitives?.mountIcon?.(icon);
      }
      if (label) label.textContent = name;
    });
    document.querySelectorAll("[data-portal-theme-option]").forEach((option) => {
      const mode = option.dataset.portalThemeOption;
      const active = mode === next;
      option.setAttribute("aria-checked", active ? "true" : "false");
      const label = option.querySelector("[data-theme-option-label]");
      if (label) label.textContent = modeLabel(mode);
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
    return button;
  }

  function bindThemeMenu(menu, select) {
    const options = Array.from(menu.querySelectorAll("[data-portal-theme-option]"));
    menu.addEventListener("click", event => {
      const option = event.target.closest("[data-portal-theme-option]");
      if (option) select(option);
    });
    menu.addEventListener("keydown", event => {
      const index = options.indexOf(event.target);
      const offsets = { ArrowUp: index - 1, ArrowDown: index + 1, Home: 0, End: options.length - 1 };
      if (!Object.hasOwn(offsets, event.key)) return;
      event.preventDefault();
      options[(offsets[event.key] + options.length) % options.length].focus();
    });
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
        <span data-theme-option-label>${modeLabel(mode)}</span>
      </button>
    `).join("");
    bindThemeMenu(menu, target => {
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
      portal: true,
    });
    button.addEventListener("click", event => {
      if (readMode() === "liquid-glass") {
        themePopover.open();
        if (event.detail === 0) menu.querySelector('[aria-checked="true"]')?.focus();
        return;
      }
      window.PortalTheme.setMode(nextMode(readMode()));
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
      if (!picker.contains(document.activeElement) && !menu.contains(document.activeElement)) {
        themePopover.close();
      }
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
    menu.addEventListener("focusout", closeIfFocusLeft);
    return picker;
  }

  function mountToggle() {
    syncThemeScene(readMode());
    syncGlassOptics(readMode());
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
