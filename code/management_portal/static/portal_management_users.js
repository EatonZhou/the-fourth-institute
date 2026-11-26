(function () {
  "use strict";

  if (window.PortalManagementUsers) return;

  // The users view: the notice, the user list, the selected user's
  // editor and the access audit, reading the directory and saving a
  // user's profile.

  const DEPENDENCIES = Object.freeze([
    "$", "baselines", "invalidate", "me", "openSection", "panelState", "remember", "saveUserDraft", "state",
    "userDrafts",
  ]);

  function createUsers(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalManagementUsers is missing: ${missing.join(", ")}`);
    const {
      $, baselines, invalidate, me, openSection, panelState, remember, saveUserDraft, state, userDrafts,
    } = dependencies;

    const userCategoryTitle = category => window.PortalI18n.t(
      {portal_admin: "Portal 管理员", member: "一般用户", intern: "实习用户"}[category] || category);

    const notice = (message, tone = "") => {
      if (panelState.destroyed) return;
      const box = $("notice");
      box.hidden = !message;
      box.className = `settingsNotice${tone ? ` ${tone}` : ""}`;
      box.textContent = message || "";
    };
    const accessUser = (username = state.selectedUsername) => (
      state.access?.users?.find(user => user.username === username) || null
    );
    const isPortalAdmin = me.access?.user_category === "portal_admin";
    const isOwnUser = user => user.username.toLowerCase() === String(me.username || "").toLowerCase();
    const isAdministratorLocked = user => user.category === "portal_admin" && (!isPortalAdmin || isOwnUser(user));
    const mayEditAccess = user => isPortalAdmin && !isAdministratorLocked(user);
    let saving = false;
    const renderEditability = user => {
      const mayUpdate = user?.allows?.UpdateUser === true;
      const mayEdit = Boolean(user && mayEditAccess(user));
      ["display-name", "email"].forEach(name => { $(name).disabled = saving || !mayUpdate; });
      ["status", "category"].forEach(name => { $(name).disabled = saving || !mayEdit; });
      $("editor").querySelector('button[type="submit"]').disabled = saving || (!mayUpdate && !mayEdit);
    };
    const renderUserList = () => {
      const host = $("user-list");
      host.replaceChildren();
      for (const user of state.access?.users || []) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "accessUserRow";
        button.setAttribute("aria-current", user.username === state.selectedUsername ? "true" : "false");
        const name = document.createElement("strong");
        name.textContent = user.display_name || user.username;
        const meta = document.createElement("small");
        meta.textContent = user.category && user.category !== "member"
          ? `${user.username} · ${userCategoryTitle(user.category)}`
          : `${user.username} · ${user.status}`;
        button.append(name, meta);
        button.addEventListener("click", () => openSection({view: "users", principalId: user.username}));
        host.append(button);
      }
    };
    const renderEditor = () => {
      const user = accessUser();
      $("editor").hidden = !user;
      $("empty").hidden = Boolean(user);
      if (!user) return;
      $("editor-name").textContent = user.display_name || user.username;
      $("editor-meta").textContent = user.username;
      $("display-name").value = user.display_name || user.username;
      $("email").value = user.email || "";
      $("status").value = user.status || "active";
      $("category").value = user.category || "member";
      const rowAdmin = user.category === "portal_admin";
      const listLocked = isAdministratorLocked(user);
      renderEditability(user);
      $("category").querySelector('option[value="portal_admin"]').disabled = !isPortalAdmin && !rowAdmin;
      $("remove-user-button").hidden = listLocked;
      $("admin-note").hidden = !listLocked;
      $("admin-note").textContent = !listLocked ? ""
        : window.PortalI18n.t(isOwnUser(user) ? "不能取消、停用或删除自己的 Portal 管理员身份；需要时由另一位 Portal 管理员操作。"
          : "只有 Portal 管理员能修改管理员名单：这位 Portal 管理员的分类和状态不能在这里修改，也不能删除。");
      $("reset-password-button").hidden = user.username === me.username;
      const draft = userDrafts.get(user.username);
      if (draft) {
        if (saving) remember($("editor"));
        const values = JSON.parse(draft.values);
        $("editor").querySelectorAll("input,select,textarea").forEach((input, index) => {
          if (input.type === "checkbox" || input.type === "radio") input.checked = values[index];
          else input.value = values[index];
        });
        if (!saving) baselines.set($("editor"), draft.baseline);
      } else remember($("editor"));
    };
    const renderAudit = () => {
      const host = $("audit-list");
      host.replaceChildren();
      for (const event of state.access?.audit || []) {
        const row = document.createElement("div");
        row.className = "accessAuditEvent";
        const at = document.createElement("span");
        at.textContent = window.PortalUI.formatDateTime(event.at);
        const actor = document.createElement("code");
        actor.textContent = event.actor || "system";
        const action = document.createElement("span");
        action.textContent = `${event.action || "update"}${event.target ? ` · ${event.target}` : ""}`;
        row.append(at, actor, action);
        host.append(row);
      }
    };
    const renderAccess = () => {
      renderUserList();
      renderEditor();
      renderAudit();
    };
    const loadAccess = async (preferredUsername = "", discardUser = "") => {
      saveUserDraft();
      if (discardUser) userDrafts.delete(discardUser);
      const access = await window.PortalAPI.get("/api/admin/access-control");
      if (panelState.destroyed) return;
      state.access = access;
      $("password-reset-status").textContent = state.access.password_reset?.email_configured
        ? window.PortalI18n.t("Email recovery is enabled.")
        : window.PortalI18n.t("Email recovery is waiting for SMTP configuration.");
      if (preferredUsername && accessUser(preferredUsername)) state.selectedUsername = preferredUsername;
      if (!accessUser()) state.selectedUsername = state.access.users?.[0]?.username || "";
      renderAccess();
    };
    // Profile updates and removal use HATG ISA; password reset and
    // session revocation use the account security route.
    const userInstruction = (name, args) => window.PortalInstructionClient.execute(name, args, {
      idempotencyKey: `user:${name}:${globalThis.PortalUIPrimitives.randomUUID()}`,
    });
    const saveAccess = async event => {
      event.preventDefault();
      const user = accessUser();
      if (!user || saving || panelState.destroyed) return;
      const userId = user.username;
      const displayName = $("display-name").value.trim();
      const email = $("email").value.trim();
      const category = $("category").value;
      const status = $("status").value;
      const changes = {};
      if (displayName !== (user.display_name || userId)) changes.display_name = displayName;
      if (email !== (user.email || "")) changes.recovery_email = email;
      const calls = [];
      if (user.allows?.UpdateUser === true && Object.keys(changes).length) calls.push(["UpdateUser", {changes}]);
      if (mayEditAccess(user)) {
        if (status !== (user.status || "active")) calls.push(["UpdateUserStatus", {status}]);
        if (category !== (user.category || "member")) calls.push(["UpdateUserCategory", {category}]);
      }
      if (!calls.length) return;
      saving = true;
      panelState.pending++;
      renderEditability(user);
      try {
        for (const [name, args] of calls) {
          if (panelState.destroyed) return;
          await userInstruction(name, {user_id: userId, ...args});
        }
        if (panelState.destroyed) return;
        await loadAccess(userId, userId);
        notice(window.PortalI18n.text`Access saved for ${userId}.`, "success");
      } catch (error) {
        if (panelState.destroyed) return;
        // An earlier field may already have committed. Keep the draft and compare
        // against the refreshed row so retrying does not repeat a confirmed change.
        await loadAccess(userId).catch(() => {});
        notice(error.message || window.PortalI18n.t("Unable to save access."), "error");
      } finally {
        saving = false;
        panelState.pending--;
        if (!panelState.destroyed) {
          renderEditability(accessUser());
          invalidate({views: ["entries"], principalId: userId, reason: "user.updated"});
        }
      }
    };

    return {
      accessUser, loadAccess, notice, renderEditor, renderUserList, saveAccess, userInstruction,
    };
  }

  window.PortalManagementUsers = Object.freeze({ createUsers, dependencies: DEPENDENCIES });
})();
