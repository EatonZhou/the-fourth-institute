(() => {
  "use strict";
  const assets = new Map();
  function loadAsset(url, style = false) {
    if (assets.has(url)) return assets.get(url);
    const existing = [...document.querySelectorAll(style ? "link[rel=stylesheet]" : "script[src]")]
      .some(node => new URL(style ? node.href : node.src).pathname === url);
    if (existing) return Promise.resolve();
    const pending = new Promise((resolve, reject) => {
      const node = document.createElement(style ? "link" : "script");
      if (style) { node.rel = "stylesheet"; node.href = url; } else { node.src = url; node.async = false; }
      node.onload = resolve;
      node.onerror = () => { node.remove(); assets.delete(url); reject(new Error(window.PortalI18n.text`无法加载 ${url}`)); };
      document.head.append(node);
    });
    assets.set(url, pending);
    return pending;
  }

  // A native view's own script registers its controller when it loads:
  // activate({ isVisible }), deactivate(), setVisible(visible) and destroy(), each optional.
  const controllers = new Map();
  function registerView(id, controller) { controllers.set(String(id), controller || {}); }

  // This owner composes the development projection with the native views of the
  // installed features (web.native_view: WALL, Test). Each view retains its domain
  // APIs, resource identities, Workbench stores and authorization.
  function mount(root, { openDevelopment, signal } = {}) {
    const DEVELOPMENT = { id: "development", icon: "network", label: window.PortalI18n.t("开发投影"), title: window.PortalI18n.t("架构、阶段进展与未来演化") };
    const PACKAGING = { id: "packaging", icon: "archive", label: window.PortalI18n.t("打包发布"), title: window.PortalI18n.t("打包发布") };
    const shell = document.createElement("div");
    shell.className = "portalWorkbenchPageLayout developmentWorkspace";
    const railRoot = document.createElement("aside"); railRoot.className = "portalActivityRail";
    const body = document.createElement("div"); body.className = "portalWorkbenchViewportRoot developmentViews";
    const views = {};
    function addView(name) {
      const element = document.createElement("section");
      element.className = "developmentView";
      element.dataset.developmentView = name; element.hidden = true;
      body.append(element); views[name] = element;
    }
    addView("development");
    addView("packaging");
    shell.append(railRoot, body); root.replaceChildren(shell);
    let selected = "development", generation = 0, disposed = false, development, nativeViews;
    const loaded = new Map(), templates = new Map();
    const projected = new Map(), facadeDetails = new Map();
    const requestedView = () => {
      const params = new URLSearchParams(location.search);
      return params.get("view") || (params.get("region") === "packaging" ? "packaging" : "development");
    };
    function updateFacade(view, detail) {
      if (detail) facadeDetails.set(view, detail);
      const target = selected === "packaging" ? "packaging" : "development";
      detail = facadeDetails.get(target);
      const state = detail?.state || {};
      rail.setItems([{ key: "facade", label: window.PortalI18n.t(target === "packaging" ? "打包发布 · 团队对话" : "开发团队对话"), glyph: window.PortalI18n.t("聊"), dismissible: false,
        running: state.working === true, queued: state.queued === true,
        unread: detail?.snapshot?.viewer_state?.unread === true }]);
    }
    const rail = window.PortalWorkbench.createActivityRail({
      id: "development-workspace", root: railRoot, label: window.PortalI18n.t("Portal 开发投影"), activeView: requestedView(),
      views: [DEVELOPMENT, PACKAGING],
      onViewSelect: view => void selectView(view, true),
      onItemSelect: async () => { const view = selected === "packaging" ? "packaging" : "development"; await selectView(view, true); await projected.get(view)?.focusChat(); },
    });
    window.PortalUIPrimitives?.mountIcons?.(railRoot);
    // The server names the installed native views; the rail lists them once they arrive.
    function listNativeViews() {
      if (!nativeViews) nativeViews = Promise.resolve().then(() => window.PortalAPI.get("/development/native-views")).then(result => {
        const list = window.PortalI18n.definition(Array.isArray(result?.views) ? result.views : []);
        if (!disposed) {
          for (const view of list) if (!Object.hasOwn(views, view.id)) addView(view.id);
          rail.setViews([DEVELOPMENT, ...list.map(({ id, icon, label, title }) => ({ id, icon, label, title })), PACKAGING]);
          window.PortalUIPrimitives?.mountIcons?.(railRoot);
        }
        return list;
      }).catch(error => { nativeViews = null; throw error; });
      return nativeViews;
    }
    function syncVisibility() {
      for (const [name, controller] of projected) controller.setVisible(!disposed && selected === name);
      for (const [name, controller] of controllers) if (Object.hasOwn(views, name)) controller.setVisible?.(!disposed && selected === name);
    }
    async function loadNative(view) {
      if (!templates.has(view)) templates.set(view, window.PortalAPI.get("/development/native-views?view=" + encodeURIComponent(view)).then(result => {
        const doc = new DOMParser().parseFromString(result.html, "text/html");
        return { template: doc.getElementById(view + "NativeView"), assets: JSON.parse(doc.getElementById(view + "NativeViewAssets").textContent) };
      }).catch(error => { templates.delete(view); throw error; }));
      const source = await templates.get(view);
      if (disposed) return;
      if (!views[view].dataset.mounted) {
        views[view].replaceChildren(source.template.content.cloneNode(true));
        window.PortalI18n.renderStatic(views[view]);
        views[view].dataset.mounted = "true";
        if (source.template.dataset.hostClass) views[view].classList.add(source.template.dataset.hostClass);
        // The template's first element is the view itself, which starts hidden.
        views[view].firstElementChild.hidden = false;
      }
      await Promise.all(source.assets.styles.map(url => loadAsset(url, true)));
      for (const url of source.assets.scripts) {
        if (disposed) return;
        await loadAsset(url);
      }
      window.PortalUIPrimitives?.mountIcons?.(views[view]);
    }
    function ensure(view) {
      if (!loaded.has(view)) loaded.set(view, (async () => {
        if (view === "development" || view === "packaging") {
          const controller = await openDevelopment(views[view], {
            externalRail: true, isVisible: () => selected === view,
            ...(view === "packaging" ? { region: "packaging" } : { excludeRegions: ["packaging"] }),
            onFacadeDetail: detail => updateFacade(view, detail),
            onMounted: controller => { projected.set(view, controller); syncVisibility(); },
          });
          projected.set(view, controller);
          if (view === "development") development = controller;
          if (disposed) controller.destroy();
          else syncVisibility();
          return controller;
        }
        await loadNative(view);
      })().catch(error => { loaded.delete(view); throw error; }));
      return loaded.get(view);
    }
    function showLoadError(host, error, retry) {
      const alert = document.createElement("p"); alert.dataset.developmentLoadError = "true"; alert.setAttribute("role", "alert"); alert.textContent = window.PortalI18n.text`工作区加载失败：${error.message}`;
      const button = document.createElement("button"); button.type = "button"; button.textContent = window.PortalI18n.t("重试");
      button.onclick = () => { alert.remove(); void retry(); }; alert.append(button); host.prepend(alert);
    }
    async function selectView(view, navigate = false) {
      if (disposed) return false;
      const revision = ++generation;
      if (!Object.hasOwn(views, view)) {
        try { await listNativeViews(); } catch (error) {
          if (!disposed && revision === generation) showLoadError(views[selected], error, () => selectView(view, navigate));
          return false;
        }
        if (disposed || revision !== generation) return false;
        // A view this Portal does not install opens the development projection.
        if (!Object.hasOwn(views, view)) view = "development";
      }
      selected = view;
      views[view].querySelector(":scope > [data-development-load-error]")?.remove();
      for (const [name, element] of Object.entries(views)) element.hidden = name !== view;
      rail.setActiveView(view);
      updateFacade(view);
      syncVisibility();
      for (const [name, controller] of controllers) if (name !== view) controller.deactivate?.();
      document.body.dataset.portalWorkbenchEntry = view;
      window.__PORTAL_WORKBENCH_ENTRY = view;
      window.dispatchEvent(new CustomEvent("portal:wall-view-changed", { detail: { view } }));
      if (navigate) {
        const url = new URL(location.href);
        if (view === "development") url.searchParams.delete("view"); else url.searchParams.set("view", view);
        if (url.searchParams.get("region") === "packaging") url.searchParams.delete("region");
        history.pushState({}, "", url);
      }
      try {
        await ensure(view);
        if (disposed || revision !== generation) return false;
        if (view !== "development") await controllers.get(view)?.activate?.({ isVisible: () => !disposed && selected === view });
        syncVisibility();
        return !disposed && revision === generation;
      } catch (error) {
        if (!disposed && revision === generation) showLoadError(views[view], error, () => selectView(view));
        return false;
      }
    }
    const onPopState = () => void selectView(requestedView());
    addEventListener("popstate", onPopState);
    function destroy() {
      if (disposed) return; disposed = true; ++generation;
      syncVisibility();
      removeEventListener("popstate", onPopState); signal?.removeEventListener("abort", destroy);
      for (const controller of projected.values()) controller.destroy();
      rail.destroy();
      for (const controller of controllers.values()) { controller.deactivate?.(); controller.destroy?.(); }
      shell.hidden = true;
      // Native owners run their own pagehide disposal against their mounted DOM.
      // Remove the composition after that event's listeners have all completed.
      setTimeout(() => shell.remove(), 0);
    }
    signal?.addEventListener("abort", destroy, { once: true });
    if (signal?.aborted) destroy();
    else {
      listNativeViews().catch(() => {});
      void selectView(requestedView());
    }
    return { destroy, selectView, get view() { return selected; }, get development() { return development; } };
  }
  window.PortalDevelopmentWorkspace = Object.freeze({ mount, registerView });
})();
