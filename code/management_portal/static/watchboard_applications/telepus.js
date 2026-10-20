(() => {
  "use strict";
  window.PortalWatchboardApplications.register({
    id: "telepus",
    matches: dashboard => dashboard.telegram_users?.enabled === true,
    create(context) {
      const state = {};
      const esc = context.escapeHtml;
      const portalUi = context.ui;
      const residentService = context.api;
      const watchboardPayload = context.view.dashboard;
      const selectedResidentId = context.view.residentId;
      const renderWatchboard = context.view.render;
      const mergeWatchboardResponse = context.view.accept;
      const showWatchboardModal = context.modal.open;
      const closeWatchboardModal = context.modal.close;

      function watchboardTelepusUsersHtml(dashboard) {
        const payload = dashboard?.telegram_users || {};
        if (!payload.enabled) return "";
        const users = Array.isArray(payload.users) ? payload.users : [];
        const canManage = !!payload.permissions?.can_manage;
        return window.PortalI18n.html`<section class="watchboardSection">
          <div class="watchboardSectionHead">
            <span>Contacts</span>
            <button class="watchboardIconBtn" type="button" data-telepus-add-user aria-label="Add user" title="Add user" ${canManage ? "" : "disabled"}>${watchboardIconSvg("add")}</button>
          </div>
          <div class="watchboardSectionBody">
            <div class="watchboardCompactList">
              ${users.map(user => {
                const userId = String(user.telegram_user_id || "");
                const displayId = String(user.display_id || userId || "-");
                const name = String(user.name || user.portal_actor || "-");
                return window.PortalI18n.html`<div class="watchboardCompactItem">
                  <span>
                    <strong>${esc(name)}</strong>
                    <span title="${esc(userId)}">Telegram ${esc(displayId)}</span>
                  </span>
                  <span class="watchboardUserActions">
                    <button class="watchboardIconBtn" type="button" data-telepus-edit-user="${esc(userId)}" aria-label="Edit user" title="Edit" ${canManage && user.current_actor ? "" : "disabled"}>${watchboardIconSvg("edit")}</button>
                    <button class="watchboardIconBtn danger" type="button" data-telepus-delete-user="${esc(userId)}" aria-label="删除用户" title="删除" ${canManage && user.current_actor ? "" : "disabled"}>${watchboardIconSvg("delete")}</button>
                  </span>
                </div>`;
              }).join("") || window.PortalI18n.html`<div class="paneNote">还没有已注册 Telegram 用户。</div>`}
            </div>
          </div>
        </section>`;
      }

      function watchboardIconSvg(kind) {
        if (kind === "edit") {
          return window.PortalUIPrimitives.iconHtml("edit");
        }
        if (kind === "delete") {
          return window.PortalUIPrimitives.iconHtml("trash");
        }
        return window.PortalUIPrimitives.iconHtml("plus");
      }

      async function addTelepusTelegramUser() {
        openTelepusTelegramUserModal("");
      }

      function telepusTelegramUsers() {
        const dashboard = watchboardPayload();
        const users = dashboard?.telegram_users?.users;
        return Array.isArray(users) ? users : [];
      }

      function telepusTelegramUserById(userId) {
        const target = String(userId || "");
        return telepusTelegramUsers().find(user => String(user.telegram_user_id || "") === target) || null;
      }

      function openTelepusTelegramUserModal(userId = "") {
        const user = userId ? telepusTelegramUserById(userId) : null;
        const isEdit = !!user;
        const name = String(watchboardPayload()?.telegram_users?.current_user || "");
        const token = String(user?.telegram_user_id || "");
        const body = window.PortalI18n.html`
          <div class="watchboardControlRow">
            <span>name</span>
            <span data-telepus-bound-user>${esc(name)}</span>
          </div>
          <div class="watchboardControlRow">
            <span>telegram id</span>
            <input type="text" inputmode="numeric" pattern="[0-9]*" data-telepus-user-token autocomplete="off" value="${esc(token)}">
          </div>
          <div class="watchboardMiniActions">
            <button class="portalButton is-compact is-primary watchboardCommandBtn" type="button" data-telepus-user-save>保存</button>
            <button class="portalButton is-compact watchboardCommandBtn" type="button" data-watchboard-modal-close>Close</button>
          </div>`;
        showWatchboardModal(isEdit ? "Edit user" : "Add user", body, {
          onBind: overlay => {
            const save = () => saveTelepusTelegramUserFromModal(overlay, isEdit ? token : "");
            overlay.querySelector("[data-telepus-user-save]")?.addEventListener("click", save);
            overlay.querySelectorAll("input").forEach(input => {
              input.addEventListener("keydown", event => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  save();
                }
              });
            });
            overlay.querySelectorAll("[data-watchboard-modal-close]").forEach(btn => btn.addEventListener("click", closeWatchboardModal));
            window.setTimeout(() => overlay.querySelector("[data-telepus-user-token]")?.focus(), 0);
          },
        });
      }

      async function saveTelepusTelegramUserFromModal(overlay, previousIdToken = "") {
        const residentId = selectedResidentId();
        if (!residentId || state.telepusUserSaving) return;
        const idToken = String(overlay?.querySelector("[data-telepus-user-token]")?.value || "").trim();
        if (!idToken) {
          portalUi.toast(window.PortalI18n.t("请填写 Telegram id token。"), "warning");
          return;
        }
        const saveButton = overlay?.querySelector("[data-telepus-user-save]");
        state.telepusUserSaving = true;
        if (saveButton) saveButton.disabled = true;
        try {
          const payload = await residentService.watchboardApplication({
            application_id: "telepus", operation: "save",
            resident_id: residentId,
            args: { id_token: idToken, previous_id_token: previousIdToken || "" },
          });
          if (!mergeWatchboardResponse(payload)) return;
          state.telepusUserSaving = false;
          closeWatchboardModal();
          renderWatchboard();
        } catch (err) {
          state.telepusUserSaving = false;
          if (saveButton) saveButton.disabled = false;
          portalUi.toast(portalUi.errorMessage(window.PortalI18n.t("保存 Telepus 用户"), err), "error");
        }
      }

      async function deleteTelepusTelegramUser(userId) {
        const residentId = selectedResidentId();
        const user = telepusTelegramUserById(userId);
        const token = String(user?.telegram_user_id || userId || "").trim();
        if (!residentId || !token) return;
        const label = String(user?.name || user?.display_id || window.PortalI18n.t("this user"));
        const displayId = String(user?.display_id || "");
        if (!await portalUi.confirmAction("delete", `Telepus 用户 ${label}${displayId ? ` (${displayId})` : ""}`)) return;
        try {
          const payload = await residentService.watchboardApplication({
            application_id: "telepus", operation: "delete",
            resident_id: residentId,
            args: { id_token: token },
          });
          if (!mergeWatchboardResponse(payload)) return;
          renderWatchboard();
        } catch (err) {
          portalUi.toast(portalUi.errorMessage(window.PortalI18n.t("删除 Telepus 用户"), err), "error");
        }
      }

      function bind(box, dashboard) {
        box.querySelector("[data-telepus-add-user]")?.addEventListener("click", () => addTelepusTelegramUser());
        box.querySelectorAll("[data-telepus-edit-user]").forEach(btn => {
          btn.addEventListener("click", () => openTelepusTelegramUserModal(btn.dataset.telepusEditUser || ""));
        });
        box.querySelectorAll("[data-telepus-delete-user]").forEach(btn => {
          btn.addEventListener("click", () => deleteTelepusTelegramUser(btn.dataset.telepusDeleteUser || ""));
        });
      }
      return { render: dashboard => context.components.generic(dashboard, watchboardTelepusUsersHtml(dashboard)), bind, key: dashboard => JSON.stringify(state) };
    },
  });
})();
