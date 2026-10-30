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
      // Saving runs UpdateUser on this username, category included; each user row carries the
      // server's answer for the caller (allows.UpdateUser), and the form is editable only when it allows it.
      const mayUpdate = user.allows?.UpdateUser === true;
      ["display-name", "email", "status", "category"].forEach(name => { $(name).disabled = !mayUpdate; });
      $("editor").querySelector('button[type="submit"]').disabled = !mayUpdate;
      // The administrator list is for Portal 管理员 alone and never their own row, in either mode
      // and under every scheme (b820 §3.1): the category and status of an administrator's row,
      // its deletion, and making anyone an administrator.
      const callerAdmin = me.access?.user_category === "portal_admin";
      const rowAdmin = user.category === "portal_admin";
      const own = user.username.toLowerCase() === String(me.username || "").toLowerCase();
      const listLocked = rowAdmin && (!callerAdmin || own);
      if (listLocked) { $("status").disabled = true; $("category").disabled = true; }
      $("category").querySelector('option[value="portal_admin"]').disabled = !callerAdmin && !rowAdmin;
      $("remove-user-button").hidden = listLocked;
      $("admin-note").hidden = !listLocked;
      $("admin-note").textContent = !listLocked ? ""
        : window.PortalI18n.t(own ? "不能取消、停用或删除自己的 Portal 管理员身份；需要时由另一位 Portal 管理员操作。"
          : "只有 Portal 管理员能修改管理员名单：这位 Portal 管理员的分类和状态不能在这里修改，也不能删除。");
      $("reset-password-button").hidden = user.username === me.username;
      const draft = userDrafts.get(user.username);
      if (draft) {
        const values = JSON.parse(draft.values);
        $("editor").querySelectorAll("input,select,textarea").forEach((input, index) => {
          if (input.type === "checkbox" || input.type === "radio") input.checked = values[index];
          else input.value = values[index];
        });
        baselines.set($("editor"), draft.baseline);
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
      if (!user) return;
      try {
        // Status and category go only when changed: on an administrator's row either one is a
        // change to the administrator list, which a profile edit must not ask for.
        const category = $("category").value;
        const status = $("status").value;
        await userInstruction("UpdateUser", {
          user_id: user.username,
          changes: {
            display_name: $("display-name").value.trim(),
            recovery_email: $("email").value.trim(),
            ...(status !== (user.status || "active") ? {status} : {}),
            ...(category !== (user.category || "member") ? {category} : {}),
          },
        });
        await loadAccess(user.username, user.username);
        invalidate({views: ["entries"], principalId: user.username, reason: "user.updated"});
        notice(window.PortalI18n.text`Access saved for ${user.username}.`, "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to save access."), "error");
      }
    };

    return {
      accessUser, loadAccess, notice, renderEditor, renderUserList, saveAccess, userInstruction,
    };
  }

  window.PortalManagementUsers = Object.freeze({ createUsers, dependencies: DEPENDENCIES });
})();
