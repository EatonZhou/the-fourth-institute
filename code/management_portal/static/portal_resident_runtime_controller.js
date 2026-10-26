(function () {
  "use strict";
  if (window.PortalResidentRuntimeController) return;

  const active = restart => ["accepted", "submitting", "confirming", "cancelling"].includes(restart?.status);
  const equal = (a, b) => {
    if (a === b) return true;
    if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every(k => Object.hasOwn(b, k) && equal(a[k], b[k]));
  };
  const clone = value => JSON.parse(JSON.stringify(value));
  const routeKey = detail => JSON.stringify([detail?.resident?.backend, detail?.execution_account?.account_id,
    detail?.provider_session?.provider, detail?.provider_session?.account_id]);
  const mayEdit = detail => !!detail?.chat_workspace?.actions?.runtime_settings
    && detail?.allows?.UpdateResidentSettings !== false;
  const denied = () => new Error(window.PortalI18n.t("无权限"));
  const stale = () => new Error(window.PortalI18n.t("成员的 Backend 或账号已变更，请重新打开设置。"));

  function create({ service: suppliedService, dialogs: suppliedDialogs } = {}) {
    const entries = new Map();
    let identityRevision = 0;
    const service = () => suppliedService || window.PortalResidentService;
    const dialogs = suppliedDialogs || {
      settings: options => window.PortalChatWorkspace.createRuntimeSettingsDialog(options),
      restart: options => window.PortalChatWorkspace.createSessionRestartDialog(options),
    };
    const state = e => ({ residentId: e.id, detail: e.detail, settings: e.settings, catalog: e.catalog,
      canEdit: mayEdit(e.detail), canRestart: e.detail?.allows?.RestartSession === true,
      loading: !!e.loading, busy: !!e.saving || !!e.opening, restart: e.restart, error: e.error });
    const notify = e => e.bindings.forEach(b => b.adapters.onState?.(state(e)));
    const changed = (e, result) => {
      window.PortalResidentProjection?.invalidate?.(e.id);
      e.bindings.forEach(b => b.adapters.onChanged?.({ ...result, resident_id: e.id }));
    };
    const invalidate = id => {
      const e = entries.get(id);
      if (!e) return;
      e.revision++; e.valid = false;
      if (e.bindings.size) void load(e).catch(() => {});
    };
    const observe = e => {
      if (e.detail?.allows?.RestartSession !== true) { e.stop?.(); e.stop = null; return; }
      if (e.stop) return;
      e.stop = service().observeSessionRestart(e.id, view => {
        const wasActive = active(e.restart);
        e.restart = view.restart;
        notify(e);
        if (wasActive && !active(e.restart)) { invalidate(e.id); changed(e, { restart: e.restart }); }
      });
    };
    async function load(e) {
      if (e.loading) { await e.loading; return e.valid ? state(e) : load(e); }
      if (e.valid) return state(e);
      const revision = e.revision;
      e.loading = (async () => {
        const detail = await service().projection({ residentId: e.id });
        const [result, catalog] = mayEdit(detail) ? await Promise.all([
          service().runtimeSettings(e.id), service().runtimeCatalog({ residentId: e.id }),
        ]) : [{ settings: detail.runtime_settings || {} }, null];
        if (e.revision !== revision) return;
        e.detail = detail; e.settings = clone(result.settings || result.runtime_settings || {});
        if (e.pendingSave && Object.entries(e.pendingSave.changes).every(([k, v]) => equal(v, e.settings[k]))) e.pendingSave = null;
        e.catalog = catalog; e.valid = true; e.error = "";
        if (e.bindings.size) observe(e);
      })();
      notify(e);
      try { await e.loading; }
      catch (error) { e.error = error.message; throw error; }
      finally { e.loading = null; notify(e); }
      return e.valid ? state(e) : load(e);
    }
    const onChange = event => {
      const d = event.detail || {};
      for (const id of entries.keys()) {
        const scopes = d.resident_changes?.[id] || [];
        if (d.snapshot_required || scopes.some(s => ["runtime", "identity", "session_restart"].includes(s))) invalidate(id);
      }
    };
    window.addEventListener("portal:change", onChange);
    window.addEventListener("portal:identity-changed", () => {
      identityRevision++;
      entries.forEach(e => invalidate(e.id));
    });

    function bind(residentId, adapters = {}) {
      const id = String(residentId || "").trim();
      if (!id) throw new TypeError("resident_id required");
      if (!entries.has(id)) entries.set(id, { id, bindings: new Set(), revision: 0, settings: {}, valid: false });
      const e = entries.get(id), b = { adapters, disposed: false, generation: 0, dialog: null };
      e.bindings.add(b);
      const live = token => !b.disposed && token === b.generation;
      const close = () => {
        b.generation++;
        b.dialog?.close?.(); b.dialog?.destroy?.(); b.dialog = null;
        if (e.presentation === b) e.presentation = null;
      };
      const check = (token, identity, allowActive = false) => {
        // Cache refreshes do not change the dialog's authority. The fresh
        // projection below validates the actual route and current permission.
        if (!live(token) || identity !== identityRevision || (!allowActive && active(e.restart))) throw stale();
      };
      const freshDetail = async (token, identity, route, operation = "settings") => {
        const allowActive = operation === "cancel";
        check(token, identity, allowActive);
        const detail = await service().projection({ residentId: id });
        check(token, identity, allowActive);
        if (routeKey(detail) !== route) { invalidate(id); throw stale(); }
        if (operation === "settings" ? !mayEdit(detail) : detail?.allows?.RestartSession !== true) {
          invalidate(id); throw denied();
        }
      };
      async function save(selected, initial, token, identity, route) {
        check(token, identity);
        if (e.saving) return e.saving;
        const changes = Object.fromEntries(Object.entries(selected).filter(([k, v]) => !equal(v, initial[k])));
        if (!Object.keys(changes).length) return { settings: e.settings };
        e.saving = (async () => {
          await freshDetail(token, identity, route);
          if (e.pendingSave && !equal(changes, e.pendingSave.changes)) throw stale();
          e.pendingSave ||= { changes: clone(changes), key: window.PortalUIPrimitives.randomUUID() };
          try {
            const result = await service().updateRuntimeSettings(id, changes, e.pendingSave.key);
            e.pendingSave = null;
            invalidate(id); await load(e).catch(() => {}); changed(e, result);
            window.PortalChatWorkspace?.notifyRuntimeSettingsSaved?.(result);
            return result;
          } catch (error) {
            if (Number(error.status) >= 400 && Number(error.status) < 500 && Number(error.status) !== 408) e.pendingSave = null;
            throw error;
          }
        })();
        notify(e);
        try { return await e.saving; } finally { e.saving = null; notify(e); }
      }
      function present(kind, token, focusTarget, config) {
        const identity = identityRevision, route = routeKey(e.detail);
        const onClose = () => {
          b.generation++;
          if (e.presentation === b) e.presentation = null;
          adapters.onClose?.();
          const target = typeof focusTarget === "function" ? focusTarget() : focusTarget;
          if (!b.disposed && target?.isConnected) target.focus();
        };
        b.dialog = dialogs[kind]({ onClose }); e.presentation = b; adapters.onOpen?.();
        if (kind === "settings") {
          const initial = clone(e.settings);
          b.dialog.open(initial, selected => save(selected, initial, token, identity, route), {
            catalog: e.catalog, residentId: id, residentSettings: initial,
            accountId: e.detail.execution_account?.account_id,
            title: window.PortalI18n.t("Runtime Settings"),
            hint: window.PortalI18n.t("保存不会重启 Session，设置从下一轮生效。"),
          });
        } else {
          let submission = null;
          const initial = clone(e.settings);
          b.dialog.open({ ...config, runtime_settings: initial, runtime_catalog: e.catalog,
            resident_id: id, choose_runtime_settings: true,
            onSave: mayEdit(e.detail) ? async selected => {
              const result = await save(selected, initial, token, identity, route);
              Object.assign(initial, result?.settings || selected);
              return result;
            } : null,
          }, values => {
            if (submission) return submission;
            submission = (async () => {
              await freshDetail(token, identity, route, "restart");
              const result = await service().restartSession({ ...values, resident_id: id });
              if (!["accepted", "completed"].includes(result?.status)) throw new Error(window.PortalI18n.t("Session 切换结果尚未确认，请刷新查看状态"));
              if (result.status === "completed") { invalidate(id); changed(e, result); }
              return result;
            })();
            return submission.finally(() => { submission = null; });
          }, () => { invalidate(id); return load(e); }, async values => {
            await freshDetail(token, identity, route, "cancel");
            return service().cancelSessionRestart({ resident_id: id, restart_id: values.restart_id });
          });
        }
      }
      async function open(kind, focusTarget) {
        // Progress remains readable during migration; settings and conflicting
        // submissions stay locked, and cancellation retains its own validation.
        if (b.disposed || e.opening || e.saving) return;
        const previous = e.presentation;
        previous?.dialog?.close?.(); previous?.dialog?.destroy?.();
        if (previous) previous.dialog = null;
        b.dialog?.destroy?.(); b.dialog = null;
        const token = ++b.generation, identity = identityRevision;
        e.opening = (async () => {
          // Re-check permissions and current Provider even when another entry primed the cache.
          e.valid = false; await load(e);
          if (!live(token) || identity !== identityRevision) return;
          if (kind === "restart" && e.detail?.allows?.RestartSession !== true) throw denied();
          kind = e.detail?.allows?.RestartSession === true ? "restart" : "settings";
          if (kind === "settings" && !mayEdit(e.detail)) throw denied();
          if (kind === "restart" && e.detail?.allows?.RestartSession !== true) throw denied();
          const config = kind === "restart" ? await service().sessionRestartOptions(id) : null;
          if (!live(token) || identity !== identityRevision) return;
          present(kind, token, focusTarget, config);
        })();
        notify(e);
        try { await e.opening; } finally { e.opening = null; notify(e); }
      }
      const handle = { load: () => {
        if (b.disposed) return Promise.resolve(state(e));
        // A view may have reset its DOM since the previous delivery. Replay a
        // cache hit to this subscriber without rereading or repainting peers.
        if (e.valid && !e.loading) {
          const view = state(e);
          adapters.onState?.(view);
          return Promise.resolve(view);
        }
        return load(e);
      }, state: () => state(e),
        openSettings: focusTarget => open("settings", focusTarget), openRestart: focusTarget => open("restart", focusTarget),
        close, destroy() {
          if (b.disposed) return;
          b.disposed = true; close(); e.bindings.delete(b);
          if (!e.bindings.size) { e.stop?.(); e.stop = null; e.valid = false; }
        },
      };
      // Every new view starts with the current snapshot, including an in-flight
      // load or its last error; it must not depend on the next global refresh.
      adapters.onState?.(state(e));
      return handle;
    }
    return { bind, invalidate };
  }
  window.PortalResidentRuntimeController = Object.freeze({ ...create(), create });
})();
