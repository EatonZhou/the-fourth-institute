(function () {
  "use strict";

  if (window.PortalNav) return;

  // The installed features' entries come with the page (web.navigation, frontend_manifest.py):
  // with an order they join the main entries, with a section that entry's secondary menu.
  const FEATURE_NAVIGATION = window.PortalI18n.definition(
    Array.isArray(window.__PORTAL_FRONTEND__?.navigation) ? window.__PORTAL_FRONTEND__.navigation : [],
  ).map(item => ({ id: String(item?.id || ""), label: String(item?.label || ""), href: String(item?.href || ""),
    order: Number(item?.order) || 0, section: String(item?.section || "") })).filter(item => item.id && item.label && item.href);
  const featureSectionItems = section => FEATURE_NAVIGATION.filter(item => item.section === section)
    .map(({ id, label, href }) => ({ id, label, href }));
  const ITEMS = [
    { id: "resource", label: window.PortalI18n.t("Resource"), href: "/resource/knowledge", order: 10 },
    { id: "lineage", label: window.PortalI18n.t("Society"), href: "/lineage", order: 30 },
    { id: "workbenches", label: window.PortalI18n.t("Workbenches"), href: "/workbenches", order: 60 },
    { id: "development", label: window.PortalI18n.t("Portal 开发投影"), href: "/development", builtin: true, order: 70 },
    ...FEATURE_NAVIGATION.filter(item => !item.section).map(({ id, label, href, order }) => ({ id, label, href, order })),
  ].sort((left, right) => left.order - right.order);
  const SECONDARY_NAV_POPOVER_ID = "portalSecondaryNavPopover";
  // Label vocabulary also provides the initial/error navigation before declarations arrive.
  const RESOURCE_SECTION_LABELS = {knowledge: "Knowledge", skill: "Skill", repo: "Repo", article: "Article", experiment: "Experiment", device: "Device"};
  const secondaryNavs = new Map([
    ["resource", {
      label: window.PortalI18n.t("Resource sections"),
      items: [
        ...Object.entries(RESOURCE_SECTION_LABELS).map(([id, label]) => ({id, label: window.PortalI18n.t(label), href: `/resource/${id}`})),
        ...featureSectionItems("resource"),
      ],
    }],
  ]);
  const DEPLOYMENT_PREFERENCE_PATH = "/__portal_router/preference";
  const NAV_STATE_PAGE = "portal-nav";
  const USER_SETTINGS_EVENT = "portal:user-settings-changed";
  let pagePathsPromise;

  function pageError(code, message) {
    return Object.assign(new Error(message), { code: `portal.${code}` });
  }

  async function pageTarget(value) {
    if (typeof value !== "string" || !value || value.length > 4096 || /[\s\\\u0000-\u001f\u007f]/.test(value)
        || value.startsWith("//") || !(/^(?:https?:\/\/|\/(?!\/))/.test(value))) {
      throw pageError("page_url", "体验入口必须是当前 Portal 的页面地址");
    }
    let target;
    try { target = new URL(value, location.origin); }
    catch { throw pageError("page_url", "体验入口地址无效"); }
    // Reject ambiguous path encodings before URL normalizes dot segments.
    const rawPath = value.replace(/^https?:\/\/[^/]+/, "").split(/[?#]/, 1)[0];
    if (target.origin !== location.origin || target.username || target.password
        || /%|(?:^|\/)\.{1,2}(?:\/|$)/.test(rawPath)) {
      throw pageError("page_url", "体验入口必须是当前 Portal 的已登记页面");
    }
    if (!pagePathsPromise) {
      pagePathsPromise = fetch("/api/navigation/pages", { credentials: "same-origin", cache: "no-store", redirect: "error" })
        .then(async response => {
          if (!response.ok) throw pageError("page_unavailable", "页面导航目录读取失败");
          const data = await response.json();
          if (!Array.isArray(data.paths) || !data.paths.every(path => typeof path === "string" && path.startsWith("/"))) throw pageError("page_unavailable", "页面导航目录无效");
          return new Set(data.paths);
        }).catch(error => { pagePathsPromise = null; throw pageError("page_unavailable", error.message); });
    }
    if (!(await pagePathsPromise).has(target.pathname)) throw pageError("page_url", "体验入口不是已登记的 Portal 页面");
    return target;
  }

  async function openPage(value, { handleCurrent } = {}) {
    const target = await pageTarget(value);
    const url = target.pathname + target.search + target.hash;
    // A page owns its mounted view state. false means a layout refusal, never
    // permission to fall through to a destructive full-document navigation.
    const local = handleCurrent ? await handleCurrent(target) : null;
    if (local !== null) return { opened: local === true, mode: "local", url };
    // Give iframe callers a chance to receive their reply before unloading.
    setTimeout(() => location.assign(url), 0);
    return { opened: true, mode: "page", url };
  }
  const DEFAULT_USER_SETTINGS = {
    appearance: { motion: "system", language: "auto" },
  };
  let currentIdentity = window.PortalIdentity?.peek?.() || {};
  let currentUsername = String(currentIdentity.username || "").trim();
  let currentItemOrder = readPersistedItemOrder(currentUsername);
  let currentUserPreferences = normalizeUserPreferences({});
  let currentPortalDeployments = [];
  let navOrderInteracted = false;
  let identityEffectsPromise = null;
  let secondaryPopover = null;
  let secondaryTrigger = null;
  let secondaryModuleId = "";
  let secondaryOpenTimer = 0;
  let secondaryCloseTimer = 0;
  let secondaryOverlayController = null;
  let accountPanelRuntime = null;
  let accountPanelRequest = 0;
  const userMenuPopovers = new WeakMap();

  function esc(value) {
    return window.PortalUIPrimitives.escapeHtml(value);
  }

  function activeIdFor(root) {
    const workbench = ITEMS.find(item => (item.projection || item.builtin) && (secondaryItemIsCurrent(item)
      || (item.workbenchId && location.pathname === "/workbenches/view" && new URLSearchParams(location.search).get("id") === item.workbenchId)));
    if (workbench) return workbench.id;
    return String(root?.dataset?.portalNavActive || "").trim();
  }

  function normalizeSecondaryNav(moduleId, definition = {}) {
    const cleanModuleId = String(moduleId || "").trim();
    const items = (Array.isArray(definition.items) ? definition.items : [])
      .map(item => ({
        id: String(item?.id || "").trim(),
        label: String(item?.label || "").trim(),
        href: String(item?.href || "").trim(),
      }))
      .filter(item => item.id && item.label && item.href);
    if (!cleanModuleId || !items.length) return null;
    return {
      label: String(definition.label || `${cleanModuleId} navigation`).trim(),
      items,
    };
  }

  function secondaryNav(moduleId) {
    return normalizeSecondaryNav(moduleId, secondaryNavs.get(String(moduleId || "").trim()));
  }

  function secondaryItems(moduleId) {
    return secondaryNav(moduleId)?.items.map(item => ({ ...item })) || [];
  }

  function clearSecondaryTimers() {
    window.clearTimeout(secondaryOpenTimer);
    window.clearTimeout(secondaryCloseTimer);
    secondaryOpenTimer = 0;
    secondaryCloseTimer = 0;
  }

  function ensureSecondaryPopover() {
    if (secondaryPopover?.isConnected) return secondaryPopover;
    secondaryPopover = document.createElement("div");
    secondaryPopover.id = SECONDARY_NAV_POPOVER_ID;
    secondaryPopover.className = "portalSecondaryNavPopover";
    secondaryPopover.hidden = true;
    secondaryOverlayController = window.PortalOverlay.register(secondaryPopover, {
      kind: "popover",
      closeOnEscape: true,
      onRequestClose: event => closeSecondaryNav({
        restoreFocus: event instanceof KeyboardEvent && event.key === "Escape",
      }),
    });
    secondaryPopover.addEventListener("mouseenter", () => {
      window.clearTimeout(secondaryCloseTimer);
      secondaryCloseTimer = 0;
    });
    secondaryPopover.addEventListener("mouseleave", scheduleSecondaryClose);
    secondaryPopover.addEventListener("focusin", () => {
      window.clearTimeout(secondaryCloseTimer);
      secondaryCloseTimer = 0;
    });
    secondaryPopover.addEventListener("focusout", () => {
      window.setTimeout(() => {
        if (
          secondaryPopover?.contains(document.activeElement)
          || secondaryTrigger === document.activeElement
        ) return;
        closeSecondaryNav();
      }, 0);
    });
    secondaryPopover.addEventListener("click", event => {
      if (event.target.closest(".portalSecondaryNavItem")) closeSecondaryNav();
    });
    document.body.appendChild(secondaryPopover);
    return secondaryPopover;
  }

  function secondaryItemIsCurrent(item) {
    const current = new URL(window.location.href);
    const target = new URL(item.href, window.location.origin);
    if (current.pathname.replace(/\/+$/, "") !== target.pathname.replace(/\/+$/, "")) return false;
    return [...target.searchParams].every(([key, value]) => current.searchParams.get(key) === value);
  }

  function secondaryMarkup(moduleId, definition) {
    return window.PortalI18n.html`
      <nav class="portalSecondaryNav" aria-label="${esc(definition.label)}" data-portal-secondary-nav="${esc(moduleId)}">
        ${definition.items.map(item => {
          const active = secondaryItemIsCurrent(item);
          return window.PortalI18n.html`<a
            class="portalSecondaryNavItem${active ? " active" : ""}"
            href="${esc(item.href)}"
            data-portal-secondary-item="${esc(item.id)}"
            ${active ? 'aria-current="page"' : ""}
          >${esc(item.label)}</a>`;
        }).join("")}
      </nav>`;
  }

  function positionSecondaryPopover() {
    if (!secondaryTrigger || !secondaryPopover || secondaryPopover.hidden) return;
    window.PortalOverlay.positionAnchored(secondaryTrigger, secondaryPopover, {
      placement: "bottom-start",
      margin: 12,
      gap: 9,
    });
  }

  function closeSecondaryNav({ restoreFocus = false } = {}) {
    clearSecondaryTimers();
    const previousTrigger = secondaryTrigger;
    if (previousTrigger) previousTrigger.setAttribute("aria-expanded", "false");
    secondaryTrigger = null;
    secondaryModuleId = "";
    if (secondaryPopover) {
      secondaryOverlayController?.deactivate();
      secondaryPopover.hidden = true;
      secondaryPopover.innerHTML = "";
      secondaryPopover.style.visibility = "";
    }
    if (restoreFocus && previousTrigger) {
      previousTrigger.dataset.portalSecondaryRestoringFocus = "1";
      previousTrigger.focus({ preventScroll: true });
      delete previousTrigger.dataset.portalSecondaryRestoringFocus;
    }
  }

  function openSecondaryNav(trigger, moduleId) {
    const definition = secondaryNav(moduleId);
    if (!trigger || !definition) return;
    if (trigger.classList.contains("active")) {
      closeSecondaryNav();
      return;
    }
    clearSecondaryTimers();
    if (secondaryTrigger && secondaryTrigger !== trigger) {
      secondaryTrigger.setAttribute("aria-expanded", "false");
    }
    secondaryTrigger = trigger;
    secondaryModuleId = moduleId;
    const popover = ensureSecondaryPopover();
    popover.innerHTML = secondaryMarkup(moduleId, definition);
    popover.hidden = false;
    secondaryOverlayController?.activate();
    trigger.setAttribute("aria-expanded", "true");
    window.requestAnimationFrame(positionSecondaryPopover);
  }

  function scheduleSecondaryOpen(trigger, moduleId) {
    window.clearTimeout(secondaryCloseTimer);
    secondaryCloseTimer = 0;
    if (secondaryTrigger === trigger && !secondaryPopover?.hidden) return;
    window.clearTimeout(secondaryOpenTimer);
    secondaryOpenTimer = window.setTimeout(() => openSecondaryNav(trigger, moduleId), 90);
  }

  function scheduleSecondaryClose() {
    window.clearTimeout(secondaryOpenTimer);
    secondaryOpenTimer = 0;
    window.clearTimeout(secondaryCloseTimer);
    secondaryCloseTimer = window.setTimeout(() => closeSecondaryNav(), 180);
  }

  function readPersistedItemOrder(username) {
    const value = String(username || "").trim();
    if (!value) return ITEMS.map(item => item.id);
    const saved = window.PortalState?.read?.(NAV_STATE_PAGE, value, {}) || {};
    return normalizedItemOrder(saved.itemOrder);
  }

  function loadIdentity() {
    if (!identityEffectsPromise) {
      identityEffectsPromise = window.PortalIdentity.load()
        .then(data => {
          currentIdentity = data && typeof data === "object" ? data : {};
          const username = String(data?.username || "").trim();
          if (username) currentUsername = username;
          renderReleaseStamp(data);
          // Identity changes account-only state. Rebuilding navigation here replaces
          // already-focusable links and creates an avoidable click/accessibility race.
          // User-specific ordering is applied incrementally by hydrateItemOrder().
          return data;
        });
    }
    return identityEffectsPromise;
  }

  function renderReleaseStamp(data = {}) {
    document.querySelectorAll("[data-portal-release-stamp]").forEach(node => node.remove());
    if (String(data.portal_instance || "").toLowerCase() !== "release") return;
    const builtAt = new Date(String(data.built_at || ""));
    if (Number.isNaN(builtAt.getTime())) return;
    // This bootstrap stamp runs before portal_ui.js; its pre-Portal formatter is intentional.
    const updated = new Intl.DateTimeFormat(window.PortalI18n.locale(), {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(builtAt);
    const stamp = document.createElement("div");
    stamp.className = "portalReleaseStamp";
    stamp.dataset.portalReleaseStamp = "";
    stamp.textContent = window.PortalI18n.text`Release · Updated ${updated}`;
    stamp.title = [String(data.build_id || ""), String(data.source_commit || "")].filter(Boolean).join(" · ");
    document.body.appendChild(stamp);
  }

  function normalizedItemOrder(value) {
    const defaults = ITEMS.map(item => item.id);
    const seen = new Set();
    const saved = Array.isArray(value) ? value : [];
    return [...saved, ...defaults]
      .map(item => String(item || "").trim())
      .map(item => item === "knowledge" ? "resource" : item)
      .filter(item => item && !seen.has(item) && seen.add(item));
  }

  function orderedItems() {
    const byId = new Map(ITEMS.map(item => [item.id, item]));
    return normalizedItemOrder(currentItemOrder)
      .map(id => byId.get(id))
      .filter(Boolean);
  }

  function linkAttrs(item, activeId) {
    const active = item.id === activeId;
    const attrs = [
      `class="portalModuleLink${active ? " active" : ""}"`,
      `data-portal-path="${esc(item.href)}"`,
      `data-module-link="${esc(item.id)}"`,
      `href="${esc(item.href)}"`,
    ];
    if (active) attrs.push('aria-current="page"');
    if (secondaryNav(item.id)) {
      attrs.push(
        'aria-haspopup="true"',
        'aria-expanded="false"',
        `aria-controls="${SECONDARY_NAV_POPOVER_ID}"`,
        `data-portal-secondary-trigger="${esc(item.id)}"`,
      );
    }
    return attrs.join(" ");
  }

  function render(root) {
    if (!root) return;
    const activeId = activeIdFor(root);
    root.innerHTML = orderedItems().map(item => (
      window.PortalI18n.html`<a ${linkAttrs(item, activeId)}><span class="portalModuleLinkLabel">${esc(item.label)}</span></a>`
    )).join("");
    bindNavScrolling(root);
    bindNavOrdering(root);
    bindSecondaryNav(root);
  }

  function navLinks(root) {
    return Array.from(root?.querySelectorAll?.(":scope > .portalModuleLink[data-module-link]") || []);
  }

  function revealNavLink(root, link) {
    if (!link || link.parentElement !== root) return;
    const viewport = root.getBoundingClientRect();
    const bounds = link.getBoundingClientRect();
    const offset = bounds.left < viewport.left
      ? bounds.left - viewport.left
      : Math.max(0, bounds.right - viewport.right);
    root.scrollLeft += offset;
  }

  function bindNavScrolling(root) {
    bindNavOverflow(root);
    navLinks(root).forEach(link => {
      link.addEventListener("focus", () => {
        requestAnimationFrame(() => {
          if (document.activeElement !== link) return;
          revealNavLink(root, link);
        });
      });
    });
  }

  const navOverflowBindings = new WeakMap();
  function navLinkLabelBounds(link) {
    return link.querySelector(":scope > .portalModuleLinkLabel")?.getBoundingClientRect()
      || link.getBoundingClientRect();
  }

  function clippedNavLinks(root) {
    const left = root.getBoundingClientRect().left + root.clientLeft;
    const right = left + root.clientWidth;
    return navLinks(root).filter(link => {
      const bounds = navLinkLabelBounds(link);
      return bounds.width > 0 && (bounds.left < left - 1 || bounds.right > right + 1);
    });
  }

  function bindNavOverflow(root) {
    const existing = navOverflowBindings.get(root);
    if (existing) { existing.update(); return; }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "portalNavOverflowToggle";
    button.textContent = window.PortalI18n.t("更多 ▾");
    button.setAttribute("aria-label", window.PortalI18n.t("更多导航"));
    button.setAttribute("aria-haspopup", "true");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", SECONDARY_NAV_POPOVER_ID);
    button.dataset.portalSecondaryTrigger = "portal-overflow";
    button.hidden = true;
    root.after(button);
    let renderedItems = "";
    const refreshMenu = (open = false) => {
      if (!open && secondaryTrigger !== button) return;
      const definition = {
        label: window.PortalI18n.t("被裁切的导航入口"),
        items: clippedNavLinks(root).map(link => ({
          id: link.dataset.moduleLink, label: link.textContent.trim(), href: link.getAttribute("href"),
        })),
      };
      if (!definition.items.length) {
        if (secondaryTrigger === button) closeSecondaryNav();
        return;
      }
      const signature = JSON.stringify(definition.items);
      if (open || signature !== renderedItems) {
        const focusedId = secondaryPopover?.contains(document.activeElement)
          ? document.activeElement.dataset.portalSecondaryItem : null;
        secondaryNavs.set("portal-overflow", definition);
        openSecondaryNav(button, "portal-overflow");
        renderedItems = signature;
        if (focusedId) {
          const target = Array.from(secondaryPopover.querySelectorAll(".portalSecondaryNavItem"))
            .find(item => item.dataset.portalSecondaryItem === focusedId);
          (target || button).focus({ preventScroll: true });
        }
      } else positionSecondaryPopover();
    };
    button.addEventListener("click", () => {
      if (secondaryTrigger === button && !secondaryPopover?.hidden) closeSecondaryNav();
      else refreshMenu(true);
    });
    button.addEventListener("keydown", event => {
      if (event.key !== "ArrowDown") return;
      event.preventDefault();
      refreshMenu(true);
      secondaryPopover?.querySelector(".portalSecondaryNavItem")?.focus({ preventScroll: true });
    });
    let pending = false;
    const update = () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        button.hidden = clippedNavLinks(root).length === 0;
        revealNavLink(root, document.activeElement);
        if (button.hidden && secondaryTrigger === button) {
          closeSecondaryNav();
        } else refreshMenu();
      });
    };
    const observer = new ResizeObserver(update);
    observer.observe(root);
    const mutations = new MutationObserver(update);
    mutations.observe(root, { childList: true, subtree: true, characterData: true });
    root.addEventListener("scroll", update, { passive: true });
    navOverflowBindings.set(root, { observer, mutations, update });
    document.fonts?.ready.then(update);
    update();
  }

  function applyItemOrder(root, value) {
    if (!root) return;
    const links = new Map(navLinks(root).map(link => [String(link.dataset.moduleLink || ""), link]));
    normalizedItemOrder(value).forEach(id => {
      const link = links.get(id);
      if (link) root.appendChild(link);
    });
  }

  function syncMountedNavOrder(value) {
    currentItemOrder = normalizedItemOrder(value);
    document.querySelectorAll("[data-portal-nav]").forEach(root => applyItemOrder(root, currentItemOrder));
  }

  async function persistItemOrder() {
    const identity = currentUsername ? { username: currentUsername } : await loadIdentity();
    const username = String(identity?.username || "").trim();
    if (!username) return;
    currentUsername = username;
    window.PortalState?.write?.(NAV_STATE_PAGE, username, { itemOrder: currentItemOrder });
  }

  async function hydrateItemOrder() {
    const identity = await loadIdentity();
    const username = String(identity?.username || "").trim();
    if (!username) return;
    currentUsername = username;
    if (navOrderInteracted) {
      await persistItemOrder();
      return;
    }
    const saved = window.PortalState?.read?.(NAV_STATE_PAGE, username, {}) || {};
    syncMountedNavOrder(saved.itemOrder);
  }

  function bindNavOrdering(root) {
    let draggedLink = null;
    let dropTarget = null;
    let dropAfter = false;
    let suppressClickUntil = 0;

    const clearDropTarget = () => {
      navLinks(root).forEach(link => link.classList.remove("portalNavDropBefore", "portalNavDropAfter"));
      dropTarget = null;
      dropAfter = false;
    };

    navLinks(root).forEach(link => {
      link.draggable = true;
      link.addEventListener("click", event => {
        if (performance.now() >= suppressClickUntil) return;
        event.preventDefault();
        event.stopImmediatePropagation();
      }, true);
      link.addEventListener("dragstart", event => {
        draggedLink = link;
        link.classList.add("portalNavDragging");
        link.setAttribute("aria-grabbed", "true");
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData(window.PortalCompatibility.identifier("nav_transfer"), link.dataset.moduleLink || "");
          event.dataTransfer.setData("text/plain", link.textContent || "");
        }
      });
      link.addEventListener("dragover", event => {
        if (!draggedLink || draggedLink === link) return;
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
        const rect = link.getBoundingClientRect();
        const after = event.clientX >= rect.left + rect.width / 2;
        if (dropTarget === link && dropAfter === after) return;
        clearDropTarget();
        dropTarget = link;
        dropAfter = after;
        link.classList.add(after ? "portalNavDropAfter" : "portalNavDropBefore");
      });
      link.addEventListener("dragleave", event => {
        if (event.relatedTarget instanceof Node && link.contains(event.relatedTarget)) return;
        if (dropTarget === link) clearDropTarget();
      });
      link.addEventListener("drop", event => {
        if (!draggedLink || !dropTarget) return;
        event.preventDefault();
        const reference = dropAfter ? dropTarget.nextElementSibling : dropTarget;
        root.insertBefore(draggedLink, reference);
        currentItemOrder = navLinks(root).map(item => String(item.dataset.moduleLink || "")).filter(Boolean);
        navOrderInteracted = true;
        suppressClickUntil = performance.now() + 250;
        document.querySelectorAll("[data-portal-nav]").forEach(other => {
          if (other !== root) applyItemOrder(other, currentItemOrder);
        });
        persistItemOrder();
        clearDropTarget();
      });
      link.addEventListener("dragend", () => {
        link.classList.remove("portalNavDragging");
        link.removeAttribute("aria-grabbed");
        draggedLink = null;
        clearDropTarget();
      });
    });
  }

  function bindSecondaryNav(root) {
    navLinks(root).forEach(link => {
      const moduleId = String(link.dataset.portalSecondaryTrigger || "").trim();
      if (!moduleId || link.dataset.portalSecondaryBound === "1") return;
      link.dataset.portalSecondaryBound = "1";
      link.addEventListener("mouseenter", () => scheduleSecondaryOpen(link, moduleId));
      link.addEventListener("mouseleave", scheduleSecondaryClose);
      link.addEventListener("focus", () => {
        if (link.dataset.portalSecondaryRestoringFocus === "1") return;
        openSecondaryNav(link, moduleId);
      });
      link.addEventListener("blur", () => {
        window.setTimeout(() => {
          if (secondaryPopover?.contains(document.activeElement)) return;
          scheduleSecondaryClose();
        }, 0);
      });
      link.addEventListener("keydown", event => {
        if (event.key !== "ArrowDown") return;
        event.preventDefault();
        openSecondaryNav(link, moduleId);
        secondaryPopover?.querySelector(".portalSecondaryNavItem")?.focus({ preventScroll: true });
      });
    });
    navLinks(root).filter(link => !link.dataset.portalSecondaryTrigger).forEach(link => {
      link.addEventListener("mouseenter", scheduleSecondaryClose);
      link.addEventListener("focus", closeSecondaryNav);
    });
  }

  function userInitial(username) {
    return Array.from(String(username || "?").trim())[0]?.toUpperCase() || "?";
  }

  function normalizeUserPreferences(value = {}) {
    const source = value && typeof value === "object" ? value : {};
    const appearance = source.appearance && typeof source.appearance === "object"
      ? source.appearance
      : {};
    const motion = ["system", "full", "reduced"].includes(appearance.motion)
      ? appearance.motion
      : DEFAULT_USER_SETTINGS.appearance.motion;
    return {
      appearance: { motion, language: window.PortalI18n.normalize(appearance.language),
        auto_colors: window.PortalTheme.normalizeAutoColors(appearance.auto_colors) },
    };
  }

  function mergeUserPreferences(current, patch) {
    const next = normalizeUserPreferences(current);
    const preferences = patch && typeof patch === "object" ? patch : {};
    const appearance = preferences.appearance && typeof preferences.appearance === "object"
      ? preferences.appearance
      : {};
    if ("language" in appearance) next.appearance.language = window.PortalI18n.normalize(appearance.language);
    if ("auto_colors" in appearance) {
      next.appearance.auto_colors = window.PortalTheme.normalizeAutoColors({ ...next.appearance.auto_colors, ...appearance.auto_colors });
    }
    if ("motion" in appearance) {
      next.appearance.motion = ["system", "full", "reduced"].includes(appearance.motion)
        ? appearance.motion
        : DEFAULT_USER_SETTINGS.appearance.motion;
    }
    return next;
  }

  function userPreferences() {
    return normalizeUserPreferences(currentUserPreferences);
  }

  function publishUserPreferences() {
    window.dispatchEvent(new CustomEvent(USER_SETTINGS_EVENT, {
      detail: { preferences: userPreferences() },
    }));
  }

  async function saveUserPreferences(menu, patch) {
    const previous = userPreferences();
    currentUserPreferences = mergeUserPreferences(currentUserPreferences, patch);
    publishUserPreferences();
    try {
      const preferences = await window.PortalIdentity.savePreferences(
        patch,
        { immediate: true },
      );
      currentUserPreferences = normalizeUserPreferences(preferences || currentUserPreferences);
      publishUserPreferences();
      return userPreferences();
    } catch (error) {
      currentUserPreferences = previous;
      publishUserPreferences();
      throw error;
    }
  }

  function instanceLabel(instance) {
    const normalized = String(instance || "").toLowerCase();
    const registered = currentPortalDeployments.find(item => item.id === normalized);
    if (registered) return registered.label;
    if (normalized === "dev") return "Dev";
    if (normalized === "release") return "Release";
    return "Portal";
  }

  function normalizePortalDeployments(route = null) {
    const source = Array.isArray(route?.instances) && route.instances.length
      ? route.instances
      : [];
    const seen = new Set();
    return source.map(item => ({
      id: String(item?.id || "").trim().toLowerCase(),
      label: String(item?.label || item?.id || "").trim(),
      kind: String(item?.kind || "exploration").trim().toLowerCase(),
      buildId: String(item?.build_id || "").trim(),
    })).filter(item => {
      if (!item.id || !item.label || seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
  }

  function userMenuPopover(menu) {
    return userMenuPopovers.get(menu)?.element
      || menu?.querySelector("[data-portal-user-popover]")
      || null;
  }

  function userMenuQueryAll(menu, selector) {
    if (!menu) return [];
    const nodes = Array.from(menu.querySelectorAll(selector));
    const popover = userMenuPopover(menu);
    if (!popover || menu.contains(popover)) return nodes;
    if (popover.matches(selector)) nodes.push(popover);
    nodes.push(...popover.querySelectorAll(selector));
    return nodes;
  }

  function userMenuQuery(menu, selector) {
    return userMenuQueryAll(menu, selector)[0] || null;
  }

  function renderPortalDeploymentButtons(menu, route, selectedInstance) {
    const selector = userMenuQuery(menu, "[data-portal-version-selector]");
    if (!selector) return;
    currentPortalDeployments = normalizePortalDeployments(route);
    selector.innerHTML = currentPortalDeployments.map(item => window.PortalI18n.html`
      <button
        type="button"
        data-portal-instance="${esc(item.id)}"
        data-portal-instance-kind="${esc(item.kind)}"
        aria-pressed="${item.id === selectedInstance ? "true" : "false"}"
      >${esc(item.label)}</button>`).join("");
    selector.querySelectorAll("[data-portal-instance]").forEach(button => {
      button.addEventListener("click", () => {
        switchPortalInstance(menu, button.dataset.portalInstance || "");
      });
    });
  }

  function closeUserMenu(menu) {
    if (!menu) return;
    const controller = userMenuPopovers.get(menu);
    if (controller) {
      controller.close();
      return;
    }
    const trigger = userMenuQuery(menu, "[data-portal-user-trigger]");
    const popover = userMenuPopover(menu);
    trigger?.setAttribute("aria-expanded", "false");
    if (popover) popover.hidden = true;
  }

  function openUserMenu(menu) {
    if (!menu) return;
    document.querySelectorAll("[data-portal-user-menu]").forEach(other => {
      if (other !== menu) closeUserMenu(other);
    });
    userMenuPopovers.get(menu)?.open();
    // Fetch the Menu card's styles while the account menu is open, so Menu opens at full size at once.
    void ensureAccountPanelStyles("settings").catch(() => {});
  }

  function ensureAccountPanelStyles(kind) {
    const paths = ["/static/settings.css"];
    if (kind === "management") paths.push("/static/portal_management.css");
    else paths.push("/static/portal_resident_menu.css");
    return Promise.all(paths.map(path => loadBackpackAsset(path)));
  }

  let backpackOpenSequence = 0;

  function loadBackpackAsset(path, globalName) {
    const ready = globalName ? () => window[globalName] : undefined;
    return window.PortalUIPrimitives.loadAsset(path, { ready });
  }

  async function loadPrincipalDetails() {
      const scripts = [
        ["portal_overlay", "PortalOverlay"], ["portal_ui", "PortalUI"],
        ["portal_button_tooltips", "PortalButtonTooltips"],
        ["portal_instruction_client", "PortalInstructionClient"],
        ["portal_detail_surface", "PortalDetailSurface"],
        ["portal_resident_avatar", "PortalResidentAvatar"], ["portal_user_avatar", "PortalUserAvatar"], ["principal_backpack", "PortalPrincipalBackpack"],
      ];
      await Promise.all([
        loadBackpackAsset("/static/principal_backpack.css"),
        loadBackpackAsset("/static/portal_resident_avatar.css"),
        ...scripts.map(([path, name]) => loadBackpackAsset(`/static/${path}.js`, name)),
      ]);
  }

  async function mountPrincipalDetails({ container, principalId }) {
    await loadPrincipalDetails();
    if (!container.isConnected) return null;
    container.replaceChildren();
    return window.PortalPrincipalBackpack.mount({ container, principalId });
  }

  async function mountPermissionManagement({ container, principalId, view = "entries", onSystemChange }) {
    if (!["entries", "schemes"].includes(view)) throw new TypeError("Unknown permission management view");
    await loadPrincipalDetails();
    if (!container.isConnected) return null;
    if (view === "schemes") {
      // The permission scheme page edits the schemes' rules and instances (decision_b820faf42218 §5).
      await Promise.all([
        loadBackpackAsset("/static/portal_permission_schemes.css"),
        loadBackpackAsset("/static/portal_permission_schemes.js", "PortalPermissionSchemes"),
      ]);
      if (!container.isConnected) return null;
      container.replaceChildren();
      return window.PortalPermissionSchemes.mount({ container, onSystemChange });
    }
    container.replaceChildren();
    return window.PortalPrincipalBackpack.mount({ container, principalId, mode: "management", managementView: view });
  }

  async function openPermissionManagement({ principalId, view = "entries", accountId, createAccount = false } = {}) {
    backpackOpenSequence += 1;
    return openAccountPanel("management", document.querySelector("[data-portal-user-menu]"), { principalId, view, accountId, createAccount });
  }

  async function removeFamily(options = {}) {
    try {
      await loadBackpackAsset("/static/portal_resident_menu.js", "PortalResidentMenu");
    } catch (error) {
      window.PortalUI.toast(window.PortalI18n.text`无法加载 Family 操作：${error.message || error}`, "error");
      throw error;
    }
    return window.PortalResidentMenu.removeFamily(options);
  }

  async function openResidentMenu(options = {}) {
    try {
      await loadPrincipalDetails();
      await Promise.all([
        loadBackpackAsset("/static/portal_resident_menu.css"),
        ...[
          ["portal_society_service", "PortalSocietyService"],
          ["portal_resident_service", "PortalResidentService"],
          ["resident_projection", "PortalResidentProjection"],
          ["portal_resource_routes", "PortalResourceRoutes"],
          ["portal_resource_chips", "PortalResourceChips"],
          ["portal_preview_contract", "PortalPreviewContract"],
          ["portal_file_links", "PortalFileLinks"],
          ["portal_resident_runtime_controller", "PortalResidentRuntimeController"],
          ["portal_chat_workspace", "PortalChatWorkspace"],
          ["hatg_mailbox_panel", "PortalMailboxPanel"],
          ["portal_resident_menu", "PortalResidentMenu"],
        ].map(([path, name]) => loadBackpackAsset(`/static/${path}.js`, name)),
      ]);
      return await window.PortalResidentMenu.open(options);
    } catch (error) {
      window.PortalUI.toast(error.message || window.PortalI18n.t("无法打开 Resident Menu"), "error");
      return null;
    }
  }

  // Project and Lineage Menus: openScopeMenu({kind: "project"|"lineage", id, ...}).
  async function openScopeMenu(options = {}) {
    try {
      await loadPrincipalDetails();
      await Promise.all([
        loadBackpackAsset("/static/portal_resident_menu.css"),
        loadBackpackAsset("/static/portal_scope_menu.js", "PortalScopeMenu"),
      ]);
      return await window.PortalScopeMenu.open(options);
    } catch (error) {
      window.PortalUI.toast(error.message || window.PortalI18n.t("无法打开 Menu"), "error");
      return null;
    }
  }

  async function openPrincipalBackpack(options = {}) {
    if (options.mode === "management") return openPermissionManagement(options);
    if (String(options.principalId || "").startsWith("resident_")) return openResidentMenu(options);
    const sequence = ++backpackOpenSequence;
    try {
      await loadPrincipalDetails();
      if (sequence !== backpackOpenSequence) return null;
      return window.PortalPrincipalBackpack.open(options);
    } catch (error) {
      const message = error.message || window.PortalI18n.t("无法打开 Menu");
      if (typeof window.PortalUI?.toast === "function") window.PortalUI.toast(message, "error");
      else window.alert(message);
      return null;
    }
  }

  function accountPanelDefinition(kind) {
    return kind === "management"
      ? {
          title: window.PortalI18n.t("Portal Management"),
          script: "/static/portal_management.js",
          globalName: "PortalManagementPanel",
        }
      : {
          title: window.PortalI18n.t("Menu"),
          script: "/static/settings.js",
          globalName: "PortalSettingsPanel",
        };
  }

  function loadAccountPanelModule(kind) {
    const definition = accountPanelDefinition(kind);
    return window.PortalUIPrimitives.loadAsset(definition.script, {
      ready: () => !!window[definition.globalName]?.mount,
    }).then(() => {
      const module = window[definition.globalName];
      if (!module?.mount) throw new Error(`${definition.title} is unavailable.`);
      return module;
    });
  }

  function ensureAccountPanelRuntime() {
    if (accountPanelRuntime?.overlay?.isConnected) return accountPanelRuntime;
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay portalAccountOverlay";
    overlay.hidden = true;
    overlay.dataset.portalAccountOverlay = "1";
    overlay.innerHTML = window.PortalI18n.html`
      <section class="portalDetailSurface portalAccountSurface" data-portal-account-surface role="dialog" aria-modal="true" aria-labelledby="portalAccountPanelTitle" tabindex="-1">
        <header class="portalDetailHeader portalAccountHeader">
          <div>
            <h2 id="portalAccountPanelTitle" data-portal-account-title>Menu</h2>
            <p data-portal-account-subtitle hidden></p>
          </div>
          <button class="portalDetailClose" data-portal-icon="close" data-portal-account-close type="button" aria-label="Close" title="Close"></button>
        </header>
        <div class="portalDetailBody portalAccountBody" data-portal-account-body></div>
      </section>`;
    document.body.appendChild(overlay);
    const runtime = {
      overlay,
      surface: overlay.querySelector("[data-portal-account-surface]"),
      title: overlay.querySelector("[data-portal-account-title]"),
      subtitle: overlay.querySelector("[data-portal-account-subtitle]"),
      body: overlay.querySelector("[data-portal-account-body]"),
      controller: null,
      instance: null,
      transition: false,
    };
    runtime.controller = window.PortalUI.createDialogController({
      modal: overlay,
      backdropClose: true,
      escapeClose: true,
      initialFocus: runtime.surface,
      beforeClose: () => {
        if (runtime.body.querySelector("dialog[open]")) return false;
        const request = accountPanelRequest;
        void leaveAccountPanel(runtime).then(allowed => {
          if (allowed && request === accountPanelRequest) runtime.controller.close({ force: true });
        });
        return false;
      },
      onClose: () => {
        accountPanelRequest += 1;
        runtime.instance?.destroy?.();
        runtime.instance = null;
        runtime.body.replaceChildren();
        runtime.surface.classList.remove("is-management");
      },
    });
    overlay.querySelector("[data-portal-account-close]").addEventListener("click", () => runtime.controller.close());
    accountPanelRuntime = runtime;
    return runtime;
  }

  async function leaveAccountPanel(runtime) {
    if (runtime.transition || runtime.instance?.isBusy?.()) return false;
    const request = accountPanelRequest;
    runtime.transition = true;
    try {
      if (runtime.instance?.hasUnsavedChanges?.() && !await window.PortalUI.confirm({
        title: window.PortalI18n.t("放弃未保存的修改？"), message: window.PortalI18n.t("关闭 Menu 将丢失尚未保存的修改。"),
        confirmLabel: window.PortalI18n.t("放弃修改"), tone: "danger",
      })) return false;
      return request === accountPanelRequest && !runtime.instance?.isBusy?.();
    } finally {
      runtime.transition = false;
    }
  }

  async function openAccountPanel(kind, menu, options = {}) {
    const cleanKind = kind === "management" ? "management" : "settings";
    const definition = accountPanelDefinition(cleanKind);
    const returnTarget = userMenuQuery(menu, "[data-portal-user-trigger]");
    closeUserMenu(menu);
    returnTarget?.focus?.({ preventScroll: true });

    const runtime = ensureAccountPanelRuntime();
    if (runtime.controller.isOpen() && !await leaveAccountPanel(runtime)) return;
    runtime.instance?.destroy?.();
    runtime.instance = null;
    const request = ++accountPanelRequest;
    runtime.title.textContent = definition.title;
    // The User Menu shows its owner in the identity row, so only management keeps a subtitle.
    runtime.subtitle.hidden = cleanKind !== "management";
    runtime.subtitle.textContent = cleanKind === "management" ? window.PortalI18n.t("Portal administration") : "";
    runtime.surface.classList.toggle("is-management", cleanKind === "management");
    runtime.body.innerHTML = window.PortalI18n.markup('<div class="portalAccountLoading" role="status">Loading...</div>');
    // The card's size comes from these styles; showing it earlier flashes a small card first.
    await ensureAccountPanelStyles(cleanKind).catch(() => {});
    if (request !== accountPanelRequest) return;
    runtime.controller.open();

    try {
      const [me, module] = await Promise.all([
        loadIdentity(),
        Promise.all([ensureAccountPanelStyles(cleanKind), loadAccountPanelModule(cleanKind)]).then(([, loaded]) => loaded),
      ]);
      if (request !== accountPanelRequest || !runtime.controller.isOpen()) return;
      const username = String(me?.username || currentIdentity?.username || "").trim();
      if (cleanKind === "management") {
        runtime.subtitle.textContent = window.PortalI18n.text`Portal administration${username ? window.PortalI18n.text` for ${username}` : ""}`;
      }
      const host = document.createElement("div");
      runtime.body.replaceChildren(host);
      const instance = await module.mount(host, { ...options, me: me || currentIdentity });
      if (request !== accountPanelRequest || !runtime.controller.isOpen()) {
        instance?.destroy?.();
        return;
      }
      runtime.instance = instance;
      return instance;
    } catch (error) {
      if (request !== accountPanelRequest || !runtime.controller.isOpen()) return;
      runtime.body.replaceChildren();
      const notice = document.createElement("div");
      notice.className = "settingsNotice error";
      notice.setAttribute("role", "alert");
      notice.textContent = error?.message || window.PortalI18n.text`Unable to load ${definition.title}.`;
      runtime.body.append(notice);
    }
  }

  const userAvatarRequests = new WeakMap();

  async function renderUserAvatar(menu, username, avatar) {
    const request = (userAvatarRequests.get(menu) || 0) + 1;
    userAvatarRequests.set(menu, request);
    userMenuQueryAll(menu, "[data-portal-user-initial]").forEach(node => { node.textContent = userInitial(username); });
    if (!avatar) return;
    try {
      await loadBackpackAsset("/static/portal_resident_avatar.css");
      await loadBackpackAsset("/static/portal_resident_avatar.js", "PortalResidentAvatar");
      await loadBackpackAsset("/static/portal_user_avatar.js", "PortalUserAvatar");
      if (userAvatarRequests.get(menu) !== request) return;
      userMenuQueryAll(menu, "[data-portal-user-initial]").forEach(node => {
        node.innerHTML = window.PortalUserAvatar.markup(avatar, { label: username, size: node.classList.contains("portalUserAvatarLarge") ? 40 : 32 });
      });
    } catch {}
  }

  window.addEventListener("portal:user-avatar-changed", event => {
    const { username, avatar } = event.detail || {};
    if (!username || username !== currentIdentity?.username) return;
    currentIdentity.avatar = avatar;
    document.querySelectorAll("[data-portal-user-menu]").forEach(menu => renderUserAvatar(menu, username, avatar));
  });

  const WELCOME_WINDOW_MS = 14 * 24 * 60 * 60 * 1000;

  // A new account sees one welcome card pointing at the guide until it is dismissed;
  // the guide stays in the account menu afterwards.
  function renderWelcomeCallout(data = {}) {
    const createdAt = Date.parse(String(data.access?.created_at || ""));
    const dismissed = data.preferences?.onboarding?.welcome_dismissed === true;
    const existing = document.querySelector("[data-portal-welcome]");
    if (dismissed || !Number.isFinite(createdAt) || Date.now() - createdAt > WELCOME_WINDOW_MS) {
      existing?.remove();
      return;
    }
    if (existing || !document.body) return;
    const card = document.createElement("aside");
    card.className = "portalWelcomeCallout";
    card.dataset.portalWelcome = "";
    card.setAttribute("aria-labelledby", "portalWelcomeTitle");
    card.innerHTML = window.PortalI18n.html`
      <strong id="portalWelcomeTitle">${window.PortalI18n.t("欢迎加入 Portal")}</strong>
      <p>${window.PortalI18n.t("第一次使用可以先读 Guide。之后随时可以在右上角的账号菜单里找到它。")}</p>
      <div class="portalWelcomeActions">
        <a class="portalButton is-primary is-compact" href="/guide" target="_blank" rel="noopener" data-portal-welcome-open>${window.PortalI18n.t("打开 Guide")}</a>
        <button class="portalButton is-compact" type="button" data-portal-welcome-dismiss>${window.PortalI18n.t("知道了")}</button>
      </div>`;
    const dismiss = () => {
      card.remove();
      window.PortalIdentity?.savePreferences?.({ onboarding: { welcome_dismissed: true } }, { immediate: true }).catch(() => {});
    };
    card.querySelector("[data-portal-welcome-open]").addEventListener("click", dismiss);
    card.querySelector("[data-portal-welcome-dismiss]").addEventListener("click", dismiss);
    document.body.append(card);
    // Every account gets its own Ask Me Anything Resident; offer it as the first conversation.
    window.PortalIdentity?.listUIAssociations?.("lineage-workbench.personal-ama").then(data => {
      const items = Array.isArray(data?.items) ? data.items : [];
      const residentId = String((items.find(item => item?.key === "primary") || items[0])?.value?.resident_id || "").trim();
      if (!residentId || !card.isConnected) return;
      const link = document.createElement("a");
      link.className = "portalButton is-compact";
      link.href = `/lineage?resident=${encodeURIComponent(residentId)}`;
      link.dataset.portalWelcomeAma = "";
      link.textContent = window.PortalI18n.t("和 Ask Me Anything 对话");
      card.querySelector(".portalWelcomeActions").insertBefore(link, card.querySelector("[data-portal-welcome-dismiss]"));
    }).catch(() => {});
  }

  function setUserMenuData(menu, data = {}, route = null) {
    const username = String(data.username || route?.username || "User").trim() || "User";
    const instance = String(data.portal_instance || route?.effective_instance || "").toLowerCase();
    const buildId = String(data.build_id || "").trim();
    // The management page holds accounts and execution settings; show it to whoever may use either.
    const allows = data.access?.allows || {};
    const canManage = ["CreateUser", "GetExecutionSettings", "UpdateExecutionSettings", "ListResourceTypes", "ListProviders", "RegisterProviderAccount"].some(name => allows[name] === true);
    if (data.preferences) currentUserPreferences = normalizeUserPreferences(data.preferences);
    menu.dataset.portalInstance = instance;
    renderPortalDeploymentButtons(menu, route, instance);
    userMenuQueryAll(menu, "[data-portal-user-name]").forEach(node => { node.textContent = username; });
    renderUserAvatar(menu, username, data.avatar);
    userMenuQueryAll(menu, "[data-portal-instance-label]").forEach(node => { node.textContent = instanceLabel(instance); });
    const build = userMenuQuery(menu, "[data-portal-build-id]");
    if (build) {
      build.textContent = buildId && buildId !== "development" ? buildId : instanceLabel(instance);
      build.title = buildId || instanceLabel(instance);
    }
    userMenuQueryAll(menu, "[data-portal-instance]").forEach(button => {
      const selected = button.dataset.portalInstance === instance;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    userMenuQueryAll(menu, "[data-portal-admin-link]").forEach(link => {
      link.hidden = !canManage;
    });
    renderWelcomeCallout(data);
    userMenuQueryAll(menu, "[data-portal-instance]").forEach(button => {
      button.disabled = false;
    });
    const note = userMenuQuery(menu, "[data-portal-version-note]");
    if (note) {
      note.hidden = !!route?.ok;
      note.textContent = route?.ok ? "" : String(route?.error || window.PortalI18n.t("Unable to load Portal versions."));
    }
    publishUserPreferences();
  }

  async function loadUserMenu(menu) {
    let data = await loadIdentity();
    let route = { ok: false, error: "Unable to load Portal versions." };
    try {
      const result = await window.PortalAPI.requestStatus(DEPLOYMENT_PREFERENCE_PATH, {
        method: "GET",
        cache: "no-store",
        detectLoginRedirect: false,
      });
      route = result.ok
        ? { ...result.data, ok: true }
        : {
            ok: false,
            error: String(result.data?.error || `Unable to load Portal versions (${result.status}).`),
          };
    } catch (error) {
      route = {
        ok: false,
        error: String(error?.message || "Unable to load Portal versions."),
      };
    }
    setUserMenuData(menu, data, route);
  }

  async function switchPortalInstance(menu, instance) {
    const available = new Set(
      userMenuQueryAll(menu, "[data-portal-instance]")
        .map(button => String(button.dataset.portalInstance || "")),
    );
    if (!menu || !available.has(instance)) return;
    if (menu.dataset.portalInstance === instance) {
      closeUserMenu(menu);
      return;
    }
    const buttons = userMenuQueryAll(menu, "[data-portal-instance]");
    const note = userMenuQuery(menu, "[data-portal-version-note]");
    buttons.forEach(button => { button.disabled = true; });
    menu.classList.add("switching");
    if (note) {
      note.hidden = false;
      note.textContent = window.PortalI18n.text`Switching to ${instanceLabel(instance)}...`;
    }
    try {
      await window.PortalAPI.post(
        DEPLOYMENT_PREFERENCE_PATH,
        { instance },
        { detectLoginRedirect: false },
      );
      window.location.reload();
    } catch (error) {
      buttons.forEach(button => { button.disabled = false; });
      menu.classList.remove("switching");
      if (note) {
        note.hidden = false;
        note.textContent = error?.message || window.PortalI18n.t("Unable to switch Portal version.");
      }
    }
  }

  function renderUserMenu(root) {
    if (!root || root.dataset.portalUserMenuMounted === "1") return;
    root.dataset.portalUserMenuMounted = "1";
    const themePicker = root.querySelector(".portalThemePicker");
    root.innerHTML = window.PortalI18n.html`
      <div class="portalUserMenu" data-portal-user-menu>
        <button class="portalUserMenuTrigger" type="button" aria-haspopup="menu" aria-expanded="false" data-portal-user-trigger>
          <span class="portalUserAvatar" aria-hidden="true" data-portal-user-initial>?</span>
          <span class="portalUserMenuTriggerText">
            <span class="portalUsername" id="portalUsername" data-portal-user-name>User</span>
            <span class="portalUserMenuInstance" data-portal-instance-label>Portal</span>
          </span>
          <span class="portalUserMenuChevron" aria-hidden="true"></span>
        </button>
        <div class="portalUserMenuPopover" role="menu" data-portal-user-popover hidden>
          <div class="portalUserMenuHeader">
            <span class="portalUserAvatar portalUserAvatarLarge" aria-hidden="true" data-portal-user-initial>?</span>
            <span class="portalUserMenuAccount">
              <strong data-portal-user-name>User</strong>
              <span data-portal-build-id>Portal</span>
            </span>
          </div>
          <div class="portalUserMenuSection">
            <div class="portalUserMenuSectionTitle">Portal version</div>
            <div class="portalVersionSelector" aria-label="Portal version" data-portal-version-selector>
              <button type="button" data-portal-instance="dev" aria-pressed="false" disabled>Dev</button>
              <button type="button" data-portal-instance="release" aria-pressed="false" disabled>Release</button>
            </div>
            <div class="portalUserMenuNote" role="status" data-portal-version-note hidden></div>
          </div>
          <div class="portalUserMenuSection">${window.PortalI18n.picker()}</div>
          <div class="portalUserMenuFooter">
            <button type="button" role="menuitem" data-portal-account-panel="settings">Menu</button>
            <button type="button" role="menuitem" data-portal-account-panel="management" data-portal-admin-link hidden>Portal Management</button>
            <a role="menuitem" href="/guide" target="_blank" rel="noopener" data-portal-guide-link>${"Guide"}</a>
            <form method="post" action="/logout"><button type="submit" role="menuitem" data-portal-logout>Log out</button></form>
          </div>
        </div>
      </div>`;
    if (themePicker) root.appendChild(themePicker);
    const languageSelect = root.querySelector("[data-portal-language]");
    if (languageSelect) languageSelect.value = window.PortalI18n.preference();
    const menu = root.querySelector("[data-portal-user-menu]");
    root.appendChild(menu);
    const trigger = menu?.querySelector("[data-portal-user-trigger]");
    const popover = menu?.querySelector("[data-portal-user-popover]");
    let hoverOpened = false;
    let hoverCloseTimer = 0;
    const cancelHoverClose = () => {
      clearTimeout(hoverCloseTimer);
      hoverCloseTimer = 0;
    };
    if (menu && trigger && popover) {
      userMenuPopovers.set(menu, window.PortalOverlay.bindPopover({
        trigger,
        panel: popover,
        placement: "bottom-end",
        margin: 8,
        gap: 7,
        portal: true,
        onClose: () => {
          cancelHoverClose();
          hoverOpened = false;
        },
      }));
      trigger.addEventListener("pointerenter", event => {
        if (event.pointerType !== "mouse") return;
        cancelHoverClose();
        if (trigger.getAttribute("aria-expanded") !== "true") {
          openUserMenu(menu);
          hoverOpened = true;
        }
      });
      const scheduleHoverClose = () => {
        cancelHoverClose();
        if (!hoverOpened) return;
        hoverCloseTimer = setTimeout(() => {
          if (trigger.matches(":hover") || popover.matches(":hover")
            || popover.contains(document.activeElement)) return;
          closeUserMenu(menu);
        }, 140);
      };
      trigger.addEventListener("pointerleave", scheduleHoverClose);
      popover.addEventListener("pointerenter", cancelHoverClose);
      popover.addEventListener("pointerleave", scheduleHoverClose);
      popover.addEventListener("focusout", scheduleHoverClose);
    }
    trigger?.addEventListener("click", event => {
      event.stopPropagation();
      cancelHoverClose();
      const wasHoverOpened = hoverOpened;
      hoverOpened = false;
      if (trigger.getAttribute("aria-expanded") === "true" && !wasHoverOpened) closeUserMenu(menu);
      else openUserMenu(menu);
    });
    menu?.querySelectorAll("[data-portal-instance]").forEach(button => {
      button.addEventListener("click", () => switchPortalInstance(menu, button.dataset.portalInstance || ""));
    });
    menu?.querySelectorAll("[data-portal-account-panel]").forEach(button => {
      button.addEventListener("click", () => openAccountPanel(button.dataset.portalAccountPanel || "settings", menu));
    });
    menu?.querySelector('form[action="/logout"]')?.addEventListener("submit", async event => {
      event.preventDefault();
      try {
        const response = await window.PortalAPI.raw("/logout", { method: "POST", detectLoginRedirect: false });
        await response.body?.cancel();
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        window.PortalIdentity.clear();
        window.location.assign("/login");
      } catch (error) { window.PortalUI.toast(error.message, "error"); }
    });
    loadUserMenu(menu).catch(error => window.PortalUI.toast(error.message, "error"));
  }

  function renderSiteHeader(root) {
    if (!root || root.dataset.portalSiteHeaderMounted === "1") return;
    const active = String(root.dataset.portalNavActive || "");
    const themePicker = root.querySelector(".portalThemePicker");
    root.dataset.portalSiteHeaderMounted = "1";
    root.innerHTML = window.PortalI18n.html`
      <div class="portalNavShell">
        <div class="portalNavTop">
          <div class="portalNavMain">
            <a class="portalBrand" data-portal-home="1" href="/" data-portal-site-name></a>
            <nav class="portalModuleNav" aria-label="Primary" data-portal-nav></nav>
          </div>
          <div class="portalUser"></div>
        </div>
      </div>`;
    window.PortalSite.apply(root);
    const nav = root.querySelector("[data-portal-nav]");
    if (nav) nav.dataset.portalNavActive = active;
    if (themePicker) root.querySelector(".portalUser")?.appendChild(themePicker);
  }

  function mountSiteHeaders(scope = document) {
    if (scope instanceof Element && scope.matches("[data-portal-site-header]")) {
      renderSiteHeader(scope);
    }
    scope.querySelectorAll?.("[data-portal-site-header]").forEach(renderSiteHeader);
  }

  let mounted = false;
  function mount() {
    if (mounted) return;
    mounted = true;
    mountSiteHeaders();
    document.querySelectorAll("[data-portal-nav]").forEach(render);
    ensureSecondaryPopover();
    document.querySelectorAll(".portalUser").forEach(renderUserMenu);
    hydrateItemOrder().catch(console.error);
    hydrateWorkbenches();
    hydrateResourceTypes();
    window.PortalUIPrimitives.loadAsset("/static/portal_projections.js").catch(console.error);
    mountAgentInbox();
    document.querySelectorAll("[data-portal-site-header]").forEach(root => window.PortalPageBoot?.ready(root));
  }

  async function hydrateWorkbenches() {
    try {
      const result = await window.PortalAPI.get("/api/workbenches");
      for (const item of ITEMS.filter(item => item.builtin)) {
        item.workbenchId = (result.workbenches || []).find(app => app.builtin === item.id)?.workbench_id || "";
      }
      const apps = (result.workbenches || []).filter(app => !app.builtin && app.published_revision && app.navigation !== "hidden");
      for (let index = ITEMS.length - 1; index >= 0; index--) if (ITEMS[index].projection) ITEMS.splice(index, 1);
      apps.filter(app => app.navigation === "top").slice(0, 4).forEach(app => ITEMS.push({ id: app.workbench_id, label: app.title, href: app.url, projection: true }));
      secondaryNavs.set("workbenches", { label: window.PortalI18n.t("Workbenches"), items: [{ id: "catalog", label: window.PortalI18n.t("全部工作台"), href: "/workbenches" }, ...apps.map(app => ({ id: app.workbench_id, label: app.title, href: app.url }))] });
      document.querySelectorAll("[data-portal-nav]").forEach(render);
    } catch { }
  }
  window.addEventListener("portal:workbenches-changed", hydrateWorkbenches);

  let resourceTypesRequest = 0;
  async function hydrateResourceTypes() {
    const request = ++resourceTypesRequest;
    try {
      const result = await window.PortalInstructionClient.execute("ListResourceTypes", {});
      if (request !== resourceTypesRequest || !Array.isArray(result.types)) return;
      const sections = new Map();
      for (const type of result.types) {
        const id = type.catalog_section;
        if (type.state !== "enabled" || type.enabled === false || type.installed === false || !id) continue;
        const order = Number(type.catalog_order) || 0;
        const existing = sections.get(id);
        if (existing && existing.order <= order) continue;
        const label = RESOURCE_SECTION_LABELS[id] ? window.PortalI18n.t(RESOURCE_SECTION_LABELS[id])
          : (type.title?.[window.PortalI18n.locale() === "en" ? "en" : "zh"] || id);
        sections.set(id, {id, label, order, href: `/resource/${encodeURIComponent(id)}`});
      }
      const items = [...sections.values()].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
      // Feature pages without a resource type of their own, such as Backup.
      items.push(...featureSectionItems("resource"));
      secondaryNavs.set("resource", {label: window.PortalI18n.t("Resource sections"), items});
      if (secondaryModuleId === "resource") closeSecondaryNav();
      document.querySelectorAll("[data-portal-nav]").forEach(render);
      window.dispatchEvent(new CustomEvent("portal:resource-sections-changed"));
    } catch { /* Keep the last known categories available when the registry cannot be read. */ }
  }
  window.addEventListener("portal:resource-types-changed", hydrateResourceTypes);

  let personalAssistantRequest = null;
  function personalAssistantTarget() {
    if (personalAssistantRequest) return personalAssistantRequest;
    personalAssistantRequest = (async () => {
      const associations = await window.PortalIdentity.listUIAssociations("lineage-workbench.personal-assistant");
      const value = associations?.items?.find(item => item.key === "primary")?.value;
      if (!/^resident_[0-9a-f]{12}$/.test(value?.resident_id || "")) return null;
      // The title is read alongside the check that the facade still lives in the recorded Lineage.
      const read = lineageId => window.PortalInstructionClient.execute("GetLineage", { lineage_id: lineageId });
      const recorded = /^lineage_[0-9a-f]{12}$/.test(value.lineage_id || "") ? read(value.lineage_id) : null;
      recorded?.catch(() => {});
      const target = await window.PortalResidentService.navigationTarget(value.resident_id);
      if (target.resident_id !== value.resident_id || !/^lineage_[0-9a-f]{12}$/.test(target.lineage_id || "")) return null;
      const { lineage } = await (target.lineage_id === value.lineage_id ? recorded : read(target.lineage_id));
      return { resident_id: target.resident_id, lineage_id: target.lineage_id,
        family_id: value.family_id, title: lineage?.title || target.lineage_id };
    })().finally(() => { personalAssistantRequest = null; });
    return personalAssistantRequest;
  }

  // Lazy loading keeps pages without an open assistant free of Chat's dependencies.
  let chatWorkspaceAssets = null;
  function loadChatWorkspace() {
    if (!chatWorkspaceAssets) chatWorkspaceAssets = (async () => {
      await loadBackpackAsset("/static/portal_workbench.css");
      for (const [path, name] of [
        ["portal_page_state", "PortalPageState"], ["portal_ui", "PortalUI"],
        ["portal_resource_routes", "PortalResourceRoutes"], ["portal_preview_contract", "PortalPreviewContract"],
        ["portal_resource_chips", "PortalResourceChips"], ["portal_file_links", "PortalFileLinks"],
        ["portal_editor_open_service", "PortalEditorOpenService"], ["portal_command_registry", "PortalCommandRegistry"],
        ["portal_workbench_context", "PortalWorkbenchContext"], ["portal_workbench", "PortalWorkbench"],
        ["portal_society_service", "PortalSocietyService"], ["resident_projection", "PortalResidentProjection"],
        ["markdown", "MD"], ["portal_resident_runtime_controller", "PortalResidentRuntimeController"],
        ["portal_chat_workspace", "PortalChatWorkspace"],
      ]) await loadBackpackAsset(`/static/${path}.js`, name);
    })().catch(error => { chatWorkspaceAssets = null; throw error; });
    return chatWorkspaceAssets;
  }

  async function mountAgentInbox() {
    if (window.top !== window) return;
    try {
      if (!window.PortalConversationStore) throw new Error("PortalConversationStore must load before PortalNav.");
      await window.PortalUIPrimitives.loadAsset("/static/portal_resident_discovery_service.js", { ready: () => !!window.PortalResidentDiscoveryService });
      await window.PortalUIPrimitives.loadAsset("/static/portal_mailbox_todos.js", { ready: () => !!window.PortalMailboxTodos });
      await window.PortalUIPrimitives.loadAsset("/static/portal_agent_inbox.js", { ready: () => !!window.PortalAgentInbox });
      window.PortalAgentInbox.mount();
    } catch (error) { console.error(error); }
  }

  function agentEntryDefaults(source = {}) {
    const url = String(source?.url || "").trim();
    const surface = String(source?.surface || "lineage").trim() || "lineage";
    return {
      url,
      surface,
      kind: String(source?.kind || "resident-main"),
      subtitle: String(source?.subtitle || "Resident"),
      surface_label: surface === "lineage" && source?.kind !== "chatroom"
          ? window.PortalI18n.t("Society")
          : String(source?.surface_label || "Portal"),
    };
  }

  window.PortalNav = {
    openPage,
    get items() { return ITEMS.slice(); },
    mount,
    render,
    secondaryItems,
    agentEntryDefaults,
    personalAssistantTarget,
    loadChatWorkspace,
    openPrincipalBackpack,
    openResidentMenu,
    removeFamily,
    openScopeMenu,
    mountPrincipalDetails,
    mountPermissionManagement,
    openPermissionManagement,
    // A main entry's secondary menu and the account menu, opened and closed as
    // their triggers do (the Live shell demonstration drives and restores them).
    secondaryNav: Object.freeze({
      open: openSecondaryNav,
      close: closeSecondaryNav,
      openModule: () => (secondaryPopover && !secondaryPopover.hidden ? secondaryModuleId : ""),
    }),
    userMenu: Object.freeze({ open: openUserMenu, close: closeUserMenu }),
  };
  window.PortalUserSettings = {
    eventName: USER_SETTINGS_EVENT,
    read: userPreferences,
    update: (patch) => saveUserPreferences(document.querySelector("[data-portal-user-menu]"), patch),
  };

  document.addEventListener("click", event => {
    if (
      secondaryTrigger
      && !secondaryTrigger.contains(event.target)
      && !secondaryPopover?.contains(event.target)
    ) closeSecondaryNav();
  });
  document.addEventListener("dragstart", closeSecondaryNav);
  window.addEventListener("resize", positionSecondaryPopover);
  window.addEventListener("scroll", positionSecondaryPopover, true);

  // Page scripts create workbenches before DOMContentLoaded. Discover their
  // header host as the parser inserts it, before those scripts choose a host.
  window.PortalUIPrimitives.registerEnhancer({
    id: "site-headers", selector: "[data-portal-site-header]", enhance: renderSiteHeader,
  });
  mountSiteHeaders();

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
