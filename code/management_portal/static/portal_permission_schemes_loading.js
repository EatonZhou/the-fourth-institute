(() => {
  "use strict";

  if (window.PortalPermissionSchemesLoading) return;

  // Reading the permission system and the scheme list, opening a scheme
  // into the editor, asking before unsaved rules are given up, and running
  // one action at a time while the page shows it is busy.

  const DEPENDENCIES = Object.freeze([
    "clone", "dirty", "freshInstanceState", "mountState", "nodeTitles", "onSystemChange", "render", "run",
    "scheduleCompile", "setNotice", "state", "t", "tx",
  ]);
  // mount's onSystemChange, which a caller may leave out.
  const OPTIONAL = Object.freeze(["onSystemChange"]);

  function createLoading(dependencies) {
    const missing = DEPENDENCIES.filter(name => !OPTIONAL.includes(name) && dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesLoading is missing: ${missing.join(", ")}`);
    const {
      clone, dirty, freshInstanceState, mountState, nodeTitles, onSystemChange, render, run, scheduleCompile,
      setNotice, state, t, tx,
    } = dependencies;

    // ---- loading ------------------------------------------------------------------
    async function load(selectId = "") {
      const revision = mountState.systemRevision;
      state.loading = true;
      render();
      try {
        const [system, list] = await Promise.all([run("GetPermissionSystem"), run("ListPermissionSchemes")]);
        if (state.destroyed) return;
        state.system = revision === mountState.systemRevision ? system : {...system, enabled: state.system?.enabled};
        onSystemChange?.(state.system.enabled);
        state.schemes = list.schemes || [];
        if (!Array.isArray(list.schemes)) setNotice(t("服务端没有返回方案列表。"), "error");
        const target = selectId || state.current?.scheme_id || system.scheme?.scheme_id || state.schemes[0]?.scheme_id;
        state.loading = false;
        if (target) await open(target);
        else render();
      } catch (error) {
        if (state.destroyed) return;
        state.loading = false;
        if (error?.details?.permission_system === "open") state.system = {enabled: false};
        setNotice(tx`读取权限方案失败：${error.message}`, "error");
        render();
      }
    }

    // Refresh only runtime state so a tab revisit never discards a rules draft.
    async function refreshSystem() {
      const revision = mountState.systemRevision;
      try {
        const system = await run("GetPermissionSystem");
        if (state.destroyed || revision !== mountState.systemRevision) return;
        state.system = system;
        onSystemChange?.(system.enabled);
        render();
      } catch (error) {
        if (!state.destroyed) { setNotice(error.message, "error"); render(); }
      }
    }

    async function open(schemeId) {
      const result = await run("GetPermissionScheme", {scheme_id: schemeId, vocabulary: true});
      if (state.destroyed) return;
      // A compile scheduled for the previous draft must not run against this one.
      clearTimeout(mountState.compileTimer);
      mountState.compileSerial += 1;
      state.current = {scheme_id: result.scheme_id || schemeId, version: result.version ?? null, builtin: Boolean(result.builtin), active: Boolean(result.active)};
      state.vocabulary = result.vocabulary || state.vocabulary;
      state.draft = clone(result.rules);
      state.base = JSON.stringify(state.draft);
      state.selected = "";
      state.serverErrors = [];
      state.pendingSave = null;
      state.pendingActivate = null;
      state.source = result.source || null;
      state.ownMerge = result.builtin ? result.last_merge || null : null;
      state.review = false;
      state.merge = null;
      state.merged = null;
      state.unmapped = result.unmapped || null;
      state.stale = false;
      state.checkResult = null;
      state.instance = freshInstanceState();
      for (const titles of Object.values(nodeTitles)) titles.clear();
      // Saved rules need no check; only a draft with changes is compiled.
      scheduleCompile();
      // The instance view loads its page when it is drawn.
      render();
    }

    async function confirmDiscard() {
      if (!dirty()) return true;
      return window.PortalUI.confirm({title: t("放弃未保存的修改？"), message: t("当前方案规则的修改尚未保存。"), confirmLabel: t("放弃修改"), tone: "danger"});
    }

    async function busy(callback) {
      if (state.busy || state.destroyed) return;
      state.busy = true;
      render();
      try { await callback(); }
      catch (error) { if (!state.destroyed) setNotice(error.message || String(error), "error"); }
      finally {
        state.busy = false;
        if (!state.destroyed) render();
      }
    }

    return {
      busy, confirmDiscard, load, open, refreshSystem,
    };
  }

  window.PortalPermissionSchemesLoading = Object.freeze({ createLoading, dependencies: DEPENDENCIES });
})();
