(function () {
  "use strict";

  if (window.PortalManagementGuards) return;

  // Unsaved changes and work in progress: the saved state of each form,
  // the account dialogs, the user drafts kept while switching users,
  // confirming before unsaved changes are left, and running an account
  // action only when nothing else is.

  const DEPENDENCIES = Object.freeze([
    "$", "enrollment", "entries", "notice", "panelState", "root", "state", "userDrafts",
  ]);

  function createGuards(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementGuards is missing: ${missing.join(", ")}`);
    const {
      $, enrollment, entries, notice, panelState, root, state, userDrafts,
    } = dependencies;

    const baselines = new WeakMap();
    const snapshot = form => JSON.stringify([...form.querySelectorAll("input,select,textarea")].map(input => (
      input.type === "checkbox" || input.type === "radio" ? input.checked : input.value
    )));
    const remember = form => baselines.set(form, snapshot(form));
    const dirty = form => baselines.has(form) && snapshot(form) !== baselines.get(form);
    const accountDialogs = new Map([...root.querySelectorAll("dialog")].map(dialog => [dialog,
      window.PortalUI.createDialogController({
        modal: dialog, escapeClose: true, initialFocus: () => dialog.querySelector("input"),
        requestClose: async () => {
          const form = dialog.querySelector("form");
          if (!(await allowLeave([form]))) return;
          closeAccountDialog(dialog); form.reset();
        },
      }),
    ]));
    const openAccountDialog = dialog => accountDialogs.get(dialog).open();
    const closeAccountDialog = dialog => accountDialogs.get(dialog).close({force: true});
    const activeForms = () => [
      ...(!$("editor").hidden ? [$("editor")] : []),
      ...[...root.querySelectorAll("dialog[open] form")],
    ];
    const isBusy = () => panelState.pending > 0 || enrollment.isBusy() || [...entries.values()].some(entry => entry.syncing || entry.instance?.isBusy?.() === true);
    const hasUnsavedChanges = () => {
      saveUserDraft();
      return enrollment.hasUnsavedChanges() || activeForms().some(dirty) || userDrafts.size > 0 || [...entries.values()].some(entry => entry.instance?.hasUnsavedChanges?.() === true);
    };
    const saveUserDraft = () => {
      if (!state.selectedUsername || $("editor").hidden) return;
      const form = $("editor");
      if (dirty(form)) userDrafts.set(state.selectedUsername, {values: snapshot(form), baseline: baselines.get(form)});
      else userDrafts.delete(state.selectedUsername);
    };
    const allowLeave = async (forms = null) => {
      if (panelState.destroyed || panelState.confirming) return false;
      if (isBusy()) { notice(window.PortalI18n.t("操作正在处理，请稍后再试。"), "error"); return false; }
      if (!(forms ? forms.some(dirty) : hasUnsavedChanges())) return true;
      panelState.confirming = true;
      const dialogs = [...root.querySelectorAll("dialog[open]")];
      dialogs.forEach(closeAccountDialog);
      try {
        const approved = await window.PortalUI.confirm({title: window.PortalI18n.t("放弃未保存的修改？"), message: window.PortalI18n.t("离开后将清除未保存的修改。"), confirmLabel: window.PortalI18n.t("放弃修改"), tone: "danger"});
        return approved && !panelState.destroyed && !isBusy();
      } finally {
        if (!panelState.destroyed) dialogs.forEach(openAccountDialog);
        panelState.confirming = false;
      }
    };
    const run = callback => async event => {
      event?.preventDefault?.();
      if (panelState.destroyed || panelState.confirming || isBusy()) return;
      panelState.pending++;
      try { await callback(event); } finally { panelState.pending--; }
    };

    return {
      accountDialogs, allowLeave, baselines, closeAccountDialog, hasUnsavedChanges, isBusy, openAccountDialog,
      remember, run, saveUserDraft,
    };
  }

  window.PortalManagementGuards = Object.freeze({ createGuards, dependencies: DEPENDENCIES });
})();
