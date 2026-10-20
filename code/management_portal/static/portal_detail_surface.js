(function (global) {
  "use strict";

  if (global.PortalDetailSurface) return;

  const mounted = new WeakMap();

  const elementOf = ref => window.PortalUIPrimitives.elementOf(ref);

  function directChild(surface, selector) {
    return Array.from(surface.children).find(child => child.matches(selector)) || null;
  }

  function inferContentHost(surface, header, body, footer) {
    if (body) return body;
    const candidates = Array.from(surface.children).filter(child => (
      child !== header
      && child !== footer
      && !child.matches("script, style, template, input[type='hidden']")
    ));
    if (candidates.length !== 1) return null;
    return candidates[0].matches("form, main, article, section, nav, div, ul, ol")
      ? candidates[0]
      : null;
  }

  function mountMobileRegions(body) {
    if (!body) return null;
    const regions = Array.from(body.children).filter(child => child.hasAttribute("data-portal-mobile-region"));
    if (regions.length < 2) return null;

    const tabs = document.createElement("nav");
    tabs.className = "portalDetailMobileRegions";
    tabs.setAttribute("aria-label", window.PortalI18n.t("详情区域"));
    const primary = regions.find(region => region.hasAttribute("data-portal-mobile-primary")) || regions[0];
    let selected = primary.dataset.portalMobileRegion;

    const select = regionId => {
      if (!regions.some(region => region.dataset.portalMobileRegion === regionId)) return false;
      selected = regionId;
      regions.forEach(region => {
        region.dataset.portalMobileSelected = String(region.dataset.portalMobileRegion === selected);
      });
      tabs.querySelectorAll("button").forEach(button => {
        const active = button.dataset.portalMobileRegionTarget === selected;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", String(active));
      });
      body.dispatchEvent(new CustomEvent("portal:detail-mobile-region-change", {
        bubbles: true,
        detail: { region: selected },
      }));
      return true;
    };

    regions.forEach(region => {
      const id = String(region.dataset.portalMobileRegion || "").trim();
      if (!id) return;
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.portalMobileRegionTarget = id;
      button.setAttribute("role", "tab");
      button.textContent = region.dataset.portalMobileLabel || region.getAttribute("aria-label") || id;
      button.addEventListener("click", () => select(id));
      tabs.append(button);
    });
    body.prepend(tabs);
    select(selected);
    return { element: tabs, regions, select, get selected() { return selected; } };
  }

  let inferredTitleSequence = 0;

  function ensureAccessibleName(surface, header) {
    if (
      surface.hasAttribute("aria-labelledby")
      || surface.hasAttribute("aria-label")
    ) return;
    const title = header?.querySelector("[data-portal-detail-title], h1, h2, h3, h4");
    if (!title) return;
    if (!title.id) {
      const preferred = surface.id ? `${surface.id}Title` : "";
      if (preferred && !document.getElementById(preferred)) {
        title.id = preferred;
      } else {
        do {
          inferredTitleSequence += 1;
          title.id = `portalDetailTitle${inferredTitleSequence}`;
        } while (document.getElementById(title.id) !== title);
      }
    }
    surface.setAttribute("aria-labelledby", title.id);
  }

  function mount(ref, options = {}) {
    const surface = elementOf(ref);
    if (!surface) return null;
    const existing = mounted.get(surface);
    if (existing) return existing;

    surface.classList.add("portalDetailSurface");
    surface.dataset.portalDetailSurface = surface.dataset.portalDetailSurface || "";
    const header = directChild(surface, "[data-portal-detail-header], .portalDetailHeader");
    const body = directChild(surface, "[data-portal-detail-body], .portalDetailBody");
    const footer = directChild(surface, "[data-portal-detail-footer], .portalDetailFooter");
    const contentHost = inferContentHost(surface, header, body, footer);
    const delegatedBody = contentHost && contentHost !== body
      ? directChild(contentHost, "[data-portal-detail-body], .portalDetailBody")
      : null;
    const scrollRegion = body || (contentHost && !delegatedBody ? contentHost : null);
    const mobileRegions = mountMobileRegions(body);
    header?.classList.add("portalDetailHeader");
    body?.classList.add("portalDetailBody");
    footer?.classList.add("portalDetailFooter");
    if (contentHost) contentHost.dataset.portalDetailContentHost = "";
    if (scrollRegion) scrollRegion.dataset.portalDetailScrollRegion = "";
    surface.querySelectorAll("[data-portal-detail-close]").forEach(button => {
      button.classList.add("portalDetailClose");
    });
    const overlay = surface.closest("[data-portal-detail-overlay]");
    overlay?.classList.add("portalDetailOverlay");
    if (!(surface instanceof HTMLDialogElement) && !surface.hasAttribute("role")) {
      surface.setAttribute("role", "dialog");
    }
    if (!surface.hasAttribute("aria-modal")) surface.setAttribute("aria-modal", "true");
    ensureAccessibleName(surface, header);

    const controller = {
      body,
      contentHost,
      element: surface,
      footer,
      header,
      overlay,
      mobileRegions,
      scrollRegion,
      setState(state) {
        if (!body || !global.PortalUIPrimitives) return null;
        return global.PortalUIPrimitives.renderAsyncState(body, state);
      },
      destroy() {
        mobileRegions?.element.remove();
        mounted.delete(surface);
      },
    };
    mounted.set(surface, controller);
    options.onMount?.(controller);
    return controller;
  }

  function mountAll(root = document) {
    const surfaces = [];
    if (root instanceof Element && root.matches("[data-portal-detail-surface]")) surfaces.push(root);
    root.querySelectorAll?.("[data-portal-detail-surface]").forEach(surface => surfaces.push(surface));
    return surfaces.map(surface => mount(surface)).filter(Boolean);
  }

  function initialize() {
    global.PortalUIPrimitives.registerEnhancer({
      id: "detail-surfaces", selector: "[data-portal-detail-surface]", enhance: node => mount(node),
      remove: node => mounted.get(node)?.destroy(),
    });
  }

  global.PortalDetailSurface = Object.freeze({ mount, mountAll });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
})(window);
