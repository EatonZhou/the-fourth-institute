(() => {
  "use strict";
  if (window.PortalProjections) return;
  const apps = new Map();
  const packages = new Set();
  const connections = new Map();
  const isEditorType = type => /^projection:workbench_[0-9a-f]{12}$/.test(String(type));
  async function request(path, payload) {
    const response = await fetch(`/api/workbenches${path ? (path.startsWith("?") ? path : `/${path}`) : ""}`, { credentials: "same-origin", ...(payload === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }) });
    const result = await response.json();
    if (!response.ok) throw Object.assign(new Error(result.error || `HTTP ${response.status}`), { code: result.code });
    return result;
  }
  async function initializeNative(names) {
    const results = await Promise.allSettled(names.map(name => request("initialize", { name })));
    if (results.some(result => result.status === "fulfilled")) {
      dispatchEvent(new Event("portal:workbenches-changed"));
    }
    return results;
  }
  function resource(type) {
    if (!isEditorType(type)) return null;
    const app = apps.get(type);
    return { kind: type, workbench_id: type.slice("projection:".length), title: app?.title || window.PortalI18n.t("投影工作台") };
  }
  function descriptor({ instanceId, type, resource: binding }) {
    if (!isEditorType(type)) return null;
    const target = { ...resource(type), ...binding, kind: type };
    if (!packages.has(type)) {
      window.PortalWorkbench.registerPanelPackage({
        type, version: 1, component: "PortalProjectionContent",
        presentation: { title: target.title, icon: "window" }, sizing: { minWidth: 220, minHeight: 180 },
        instances: "single", capabilities: [], behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
        context: { channels: [] }, persistence: { schemaVersion: 1 },
        create: options => window.PortalProjectionHost.createPanel(options),
      });
      packages.add(type);
    }
    return { id: instanceId, type, title: target.title, singleton: true, binding: target, options: { projectionId: target.workbench_id } };
  }
  async function lineage(workbench, editorTypes, lineageId) {
    if (!workbench || !editorTypes) return;
    if (connections.get(workbench)?.lineageId === lineageId) return;
    const connection = { lineageId, editorTypes };
    connections.set(workbench, connection);
    workbench.setEditorTypes(editorTypes);
    if (!lineageId) return;
    try {
      const result = await request(`?lineage_id=${encodeURIComponent(lineageId)}`);
      if (connections.get(workbench) !== connection) return;
      const projected = [];
      for (const app of result.workbenches) {
        if (app.builtin || !app.published_revision) continue;
        const type = `projection:${app.workbench_id}`;
        apps.set(type, app);
        projected.push({ type, title: app.title, icon: "window" });
      }
      workbench.setEditorTypes([...editorTypes, ...projected]);
      dispatchEvent(new CustomEvent("portal:projection-components-ready", { detail: { lineageId } }));
    } catch (error) {
      if (connections.get(workbench) !== connection) return;
      connections.delete(workbench);
      window.PortalUI?.toast?.(`投影组件读取失败：${error.message}`, "error");
    }
  }
  window.addEventListener("portal:workbenches-changed", () => {
    for (const [workbench, connection] of [...connections]) {
      connections.delete(workbench);
      if (workbench.root && !workbench.root.dataset.portalWorkbenchId) continue;
      void lineage(workbench, connection.editorTypes, connection.lineageId);
    }
  });
  window.PortalProjections = Object.freeze({ lineage, resource, descriptor, request, initializeNative });
  const initialized = new Set();
  function initializeEntry(name) {
    if (!name || initialized.has(name)) return;
    initialized.add(name);
    initializeNative([name]).then(([result]) => {
      if (result.status === "rejected") throw result.reason;
      if (result.value.binding_notice) window.PortalUI?.toast?.(result.value.binding_notice, "warning");
    }).catch(error => {
      initialized.delete(name);
      window.PortalUI?.toast?.(`工作台初始化失败：${error.message}`, "error");
    });
  }
  // Opening a feature's business workbench initializes its entry (workbench.builtin, frontend_manifest.py).
  // A path with a view is a view of the development projection page, chosen after the page loads.
  const page = location.pathname.replace(/\/$/, "");
  const entryViews = new Map();
  for (const [name, workbench] of Object.entries(window.__PORTAL_FRONTEND__?.workbenches || {})) {
    if (!workbench?.initialize) continue;
    const target = new URL(String(workbench.path || ""), location.origin);
    if (target.pathname.replace(/\/$/, "") !== page) continue;
    const view = target.searchParams.get("view");
    if (view) entryViews.set(view, name);
    else initializeEntry(name);
  }
  if (entryViews.size) {
    window.addEventListener("portal:wall-view-changed", event => initializeEntry(entryViews.get(event.detail.view)));
  }
})();
