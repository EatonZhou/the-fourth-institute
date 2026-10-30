(() => {
  "use strict";

  if (window.PortalPermissionSchemesCompile) return;

  // A changed draft: marking it changed and the server's dry_run checks of
  // it, the quick compile while editing and the instance difference before
  // a save.

  const DEPENDENCIES = Object.freeze([
    "clone", "dirty", "may", "mountState", "render", "renderStatus", "run", "state",
  ]);

  function createCompile(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesCompile is missing: ${missing.join(", ")}`);
    const {
      clone, dirty, may, mountState, render, renderStatus, run, state,
    } = dependencies;

    // ---- editing --------------------------------------------------------------------
    function changed({rerender = true} = {}) {
      state.unmapped = null;
      state.pendingSave = null;
      scheduleCompile();
      if (rerender) render();
      else renderStatus();
    }

    // Two rules dry_runs: checkRules only compiles (errors, unmapped instructions, whether saving
    // regenerates the instance) and is quick; previewDiff also generates the instance from every
    // object to list the difference, which takes seconds, so it runs only when saving.
    const DIFF_LIMIT = 50;
    // A merge's dry_runs carry its source digest: the server refuses a digest the source has moved past and
    // reports what the merge writes back to the instance.
    const dryRun = (rules, extra) => run("UpdatePermissionScheme", {
      scheme_id: state.current.scheme_id, rules, dry_run: true, ...extra});
    const checkRules = (rules, extra = {}) => dryRun(rules, {diff: false, ...extra});
    const previewDiff = (rules, extra = {}) => dryRun(rules, {diff_limit: DIFF_LIMIT, ...extra});

    // The server's compiler is the only judge of a draft: its errors decide whether it can be saved.
    // The last check is kept with the rules it answered for, so saving them need not compile again.
    function scheduleCompile() {
      clearTimeout(mountState.compileTimer);
      state.compiling = false;
      if (!may("UpdatePermissionScheme") || !state.current || state.current.builtin || !dirty()) {
        if (!dirty()) state.serverErrors = [];
        return;
      }
      const serial = ++mountState.compileSerial;
      state.compiling = true;
      mountState.compileTimer = setTimeout(async () => {
        if (state.destroyed || serial !== mountState.compileSerial) return;
        const rules = clone(state.draft);
        try {
          const result = await checkRules(rules);
          if (state.destroyed || serial !== mountState.compileSerial) return;
          state.serverErrors = result.valid === false ? (result.errors || []) : [];
          state.unmapped = result.unmapped || null;
          mountState.lastCheck = {key: JSON.stringify(rules), result};
        } catch (error) {
          if (state.destroyed || serial !== mountState.compileSerial) return;
          state.serverErrors = error.details?.errors || (error.code === "instruction.conflict" ? [] : [{path: "rules", message: error.message}]);
        }
        state.compiling = false;
        renderStatus();
      }, 600);
    }

    return {
      DIFF_LIMIT, changed, checkRules, previewDiff, scheduleCompile,
    };
  }

  window.PortalPermissionSchemesCompile = Object.freeze({ createCompile, dependencies: DEPENDENCIES });
})();
