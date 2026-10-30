(function () {
  "use strict";

  if (window.PortalManagementAccounts) return;

  // Account actions: resetting a password, revoking sessions, deleting
  // and creating users, and the buttons and dialogs that start them.

  const DEPENDENCIES = Object.freeze([
    "$", "accessUser", "allowLeave", "closeAccountDialog", "invalidate", "isBusy", "loadAccess", "me", "notice",
    "openAccountDialog", "openSection", "panelState", "remember", "run", "saveAccess", "userInstruction",
  ]);

  function createAccounts(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementAccounts is missing: ${missing.join(", ")}`);
    const {
      $, accessUser, allowLeave, closeAccountDialog, invalidate, isBusy, loadAccess, me, notice, openAccountDialog,
      openSection, panelState, remember, run, saveAccess, userInstruction,
    } = dependencies;

    const openResetPassword = () => {
      if (panelState.destroyed || panelState.confirming || isBusy()) return;
      const user = accessUser();
      if (!user || user.username === me.username) return;
      $("reset-password-form").reset();
      $("reset-password-user").textContent = window.PortalI18n.text`Set a new password for ${user.username}. Existing sessions will be revoked.`;
      remember($("reset-password-form"));
      openAccountDialog($("reset-password-dialog"));
      $("reset-password-input").focus();
    };
    const submitResetPassword = async event => {
      event.preventDefault();
      const user = accessUser();
      if (!user) return;
      const password = $("reset-password-input").value;
      const strengthError = window.PortalIdentity.passwordStrengthError(password);
      if (strengthError) {
        notice(strengthError, "error");
        $("reset-password-input").focus();
        return;
      }
      try {
        await userInstruction("ResetUserPassword", {
          user_id: user.username,
          new_password: password,
        });
        closeAccountDialog($("reset-password-dialog"));
        await loadAccess(user.username);
        notice(window.PortalI18n.text`Password reset and sessions revoked for ${user.username}.`, "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to reset password."), "error");
      }
    };
    const revokeSessions = async () => {
      const user = accessUser();
      if (!user) return;
      const approved = await window.PortalUI.confirm({
        title: window.PortalI18n.t("Revoke sessions?"),
        message: `${user.username} will need to sign in again on every device.`,
        confirmLabel: window.PortalI18n.t("Revoke sessions"),
        tone: "danger",
      });
      if (!approved) return;
      try {
        await userInstruction("RevokeUserSessions", {
          user_id: user.username,
        });
        await loadAccess(user.username);
        notice(window.PortalI18n.text`Sessions revoked for ${user.username}.`, "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to revoke sessions."), "error");
      }
    };
    const syncRemovalDialog = () => {
      const user = accessUser();
      $("remove-user-submit").disabled = !user || $("delete-confirm-input").value !== user.username;
    };
    const openRemoveUser = () => {
      if (panelState.destroyed || panelState.confirming || isBusy()) return;
      const user = accessUser();
      if (!user) return;
      $("remove-user-form").reset();
      $("remove-user-name").textContent = window.PortalI18n.text`Delete ${user.username}.`;
      $("delete-confirm-label").textContent = window.PortalI18n.text`Type ${user.username} to confirm permanent deletion`;
      syncRemovalDialog();
      remember($("remove-user-form"));
      openAccountDialog($("remove-user-dialog"));
    };
    const submitRemoveUser = async event => {
      event.preventDefault();
      const user = accessUser();
      if (!user) return;
      if ($("delete-confirm-input").value !== user.username) return;
      $("remove-user-submit").disabled = true;
      try {
        await userInstruction("DeleteUser", {user_id: user.username});
        closeAccountDialog($("remove-user-dialog"));
        await loadAccess();
        notice(window.PortalI18n.text`User ${user.username} and personal data deleted. Shared content and history were retained.`, "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to remove user."), "error");
        syncRemovalDialog();
      }
    };
    let creationSubmitted = false;
    const creationMessage = message => { if (!panelState.destroyed) $("create-permission-status").textContent = message; };
    const accountInput = () => ({
      username: $("create-username").value.trim(),
      display_name: $("create-display-name").value.trim() || $("create-username").value.trim(),
      email: $("create-email").value.trim(), status: "active",
    });
    const openCreateUser = () => {
      if (panelState.destroyed || panelState.confirming || isBusy()) return;
      $("create-user-form").reset();
      creationSubmitted = false;
      $("create-submit").disabled = false;
      $("create-open-credentials").hidden = true;
      $("create-open-credentials").dataset.principalId = "";
      remember($("create-user-form"));
      openAccountDialog($("create-user-dialog"));
      creationMessage(panelState.permissionsOff ? "" : window.PortalI18n.t("新账号的权限由启用的权限方案决定：本人对自己的账号和个人空间有 admin，其余按方案规则。"));
    };
    const createUser = async event => {
      event.preventDefault();
      if (creationSubmitted || !$("create-username").reportValidity()) return;
      const password = $("create-password").value;
      const strengthError = window.PortalIdentity.passwordStrengthError(password);
      if (strengthError) { creationMessage(strengthError); $("create-password").focus(); return; }
      try {
        const account = accountInput();
        const payload = {user_id: account.username, display_name: account.display_name,
          recovery_email: account.email, initial_password: password};
        $("create-submit").disabled = true;
        const result = await userInstruction("CreateUser", payload);
        if (panelState.destroyed) return;
        if (!result.user) throw new Error("服务端没有确认账号已创建，请核对后再操作。");
        creationSubmitted = true; $("create-password").value = "";
        const username = payload.user_id;
        const message = panelState.permissionsOff ? window.PortalI18n.text`账号 ${username} 已创建。`
          : window.PortalI18n.text`账号 ${username} 已创建；需要方案规则以外的权限时，在它的权限表项中添加。`;
        creationMessage(message);
        $("create-open-credentials").dataset.principalId = username;
        $("create-open-credentials").hidden = panelState.permissionsOff;
        remember($("create-user-form"));
        try { await loadAccess(username); }
        catch (error) { notice(window.PortalI18n.text`${message} 账户目录刷新失败：${error.message}`, "error"); }
        invalidate({views: ["entries"], principalId: username, reason: "user.created"});
        notice(message, "success");
      } catch (error) {
        $("create-submit").disabled = false;
        creationMessage(window.PortalI18n.text`${error.message || window.PortalI18n.t("创建未完成")}。请核对账号状态；不会自动重试。`);
      }
    };
    $("create-open-credentials").addEventListener("click", async () => {
      const principalId = $("create-open-credentials").dataset.principalId;
      if (!creationSubmitted || !principalId || !(await allowLeave([$("create-user-form")]))) return;
      if (await openSection({view: "entries", principalId})) closeAccountDialog($("create-user-dialog"));
    });

    $("editor").addEventListener("submit", run(saveAccess));
    $("reset-password-button").addEventListener("click", openResetPassword);
    $("reset-password-form").addEventListener("submit", run(submitResetPassword));
    $("revoke-sessions-button").addEventListener("click", run(revokeSessions));
    $("remove-user-button").addEventListener("click", openRemoveUser);
    $("remove-user-form").addEventListener("submit", run(submitRemoveUser));
    $("remove-user-form").addEventListener("change", syncRemovalDialog);
    $("delete-confirm-input").addEventListener("input", syncRemovalDialog);
    $("create-user-button").addEventListener("click", openCreateUser);
    $("create-user-form").addEventListener("submit", run(createUser));
    for (const name of ["close-create-user", "cancel-create-user"]) {
      $(name).addEventListener("click", async () => {
        if (await allowLeave([$("create-user-form")])) {
          closeAccountDialog($("create-user-dialog")); $("create-user-form").reset();
        }
      });
    }
    for (const name of ["close-reset-password", "cancel-reset-password"]) {
      $(name).addEventListener("click", async () => {
        if (await allowLeave([$("reset-password-form")])) { closeAccountDialog($("reset-password-dialog")); $("reset-password-form").reset(); }
      });
    }
    for (const name of ["close-remove-user", "cancel-remove-user"]) {
      $(name).addEventListener("click", async () => {
        if (await allowLeave([$("remove-user-form")])) { closeAccountDialog($("remove-user-dialog")); $("remove-user-form").reset(); }
      });
    }
  }

  window.PortalManagementAccounts = Object.freeze({ createAccounts, dependencies: DEPENDENCIES });
})();
