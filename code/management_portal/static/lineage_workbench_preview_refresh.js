(() => {
  "use strict";

  if (window.PortalLineageWorkbenchPreviewRefresh) return;

  // Refreshing the open document when its file changes: the refresh
  // coordinator, which documents can refresh, and which changes concern
  // the open document.

  const DEPENDENCIES = Object.freeze([
    "activePreviewPayload", "activePreviewSnapshot", "enterPreviewEdit", "entryMtimeToken", "previewState",
    "renderPreview", "selectedResidentId",
  ]);

  function createPreviewRefresh(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchPreviewRefresh is missing: ${missing.join(", ")}`);
    const {
      activePreviewPayload, activePreviewSnapshot, enterPreviewEdit, entryMtimeToken, previewState, renderPreview,
      selectedResidentId,
    } = dependencies;

    const previewRefreshCoordinator = window.PortalRefreshCoordinator.create({
      key: () => previewRefreshKey(),
      interval: false,
      fetch: async ({ force, isCurrent }) => {
        const payload = activePreviewPayload();
        const session = previewState.previewController;
        if (!previewCanAutoRefresh(payload, { allowCleanEditor: force })) return null;
        try {
          if (!force) {
            const meta = await fetchPreviewRefreshMeta(payload);
            if (!isCurrent() || !meta || !previewPayloadMtimeToken(meta)
              || previewPayloadMtimeToken(meta) === previewPayloadMtimeToken(payload)) return null;
          }
          const refreshed = await fetchPreviewRefreshPayload(payload);
          return refreshed ? { payload: refreshed, source: payload, session } : null;
        } catch (error) {
          if (isCurrent() && previewState.previewController === session && activePreviewPayload() === payload) {
            session.setReadError(error);
          }
          throw error;
        }
      },
      apply: ({ payload, source, session }, { force } = {}) => {
        if (previewState.previewController !== session || activePreviewPayload() !== source) return;
        if (!previewCanAutoRefresh(activePreviewPayload(), { allowCleanEditor: force })) return;
        const editing = activePreviewSnapshot().editing;
        renderPreview(payload, { preserveScroll: true, afterRender: () => {
          if (editing && previewState.previewController === session && activePreviewPayload() === payload
            && !activePreviewSnapshot().dirty) enterPreviewEdit();
        } });
      },
      onError: err => console.debug?.("Preview auto refresh failed", err),
    });

    function stopPreviewRefresh() {
      previewRefreshCoordinator.stop();
    }

    function previewRefreshKey(payload = activePreviewPayload()) {
      return payload ? previewState.agentPreviewSource?.refreshKey?.(payload) || "" : "";
    }

    function previewPayloadMtimeToken(payload = activePreviewPayload()) {
      return String(entryMtimeToken(payload?.entry || {}) || "");
    }

    function previewCanAutoRefresh(payload = activePreviewPayload(), { allowCleanEditor = false } = {}) {
      const snapshot = activePreviewSnapshot();
      if (!payload || (snapshot.editing && !allowCleanEditor) || snapshot.dirty || snapshot.saving) return false;
      const kind = String(payload.kind || "");
      if (!kind || kind === "directory" || kind === "symlink") return false;
      return !!previewRefreshKey(payload);
    }

    async function fetchPreviewRefreshMeta(payload) {
      return payload ? previewState.agentPreviewSource?.fetchMeta?.(payload) || null : null;
    }

    async function fetchPreviewRefreshPayload(payload) {
      return payload ? previewState.agentPreviewSource?.reload?.(payload) || null : null;
    }

    function schedulePreviewRefresh(delay = 120) {
      if (!previewCanAutoRefresh()) return;
      previewRefreshCoordinator.start();
      previewRefreshCoordinator.invalidate({ delay });
    }

    async function refreshPreviewIfChanged(options = {}) {
      const force = options?.force === true;
      if (!previewCanAutoRefresh(activePreviewPayload(), { allowCleanEditor: force })) return;
      previewRefreshCoordinator.start();
      try {
        await previewRefreshCoordinator.refresh({ force });
      } catch (err) {
        if (force) {
          window.PortalUI.toast(window.PortalI18n.text`Preview 刷新失败：${err.message || err}`, "error");
        } else {
          console.debug?.("Preview auto refresh failed", err);
        }
      }
    }

    function previewChangeMatches(detail = {}) {
      if (!previewCanAutoRefresh()) return false;
      if (detail.snapshot_required) return true;
      const payload = activePreviewPayload() || {};
      const domains = new Set(detail.domains || []);
      if (String(payload.source || "") === "workspace_link") {
        return domains.has("resource");
      }
      const residentId = String(payload.resident_id || selectedResidentId() || "");
      const residents = Array.isArray(detail?.changed?.resident)
        ? detail.changed.resident
        : [];
      return Boolean(residentId && residents.includes(residentId));
    }

    return {
      previewChangeMatches, previewRefreshCoordinator, previewRefreshKey, refreshPreviewIfChanged,
      schedulePreviewRefresh, stopPreviewRefresh,
    };
  }

  window.PortalLineageWorkbenchPreviewRefresh = Object.freeze({ createPreviewRefresh, dependencies: DEPENDENCIES });
})();
