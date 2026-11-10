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
      let bindingEpoch = 0;

      async function weixinOperation(operation, args = {}) {
        const payload = await residentService.watchboardApplication({
          application_id: "telepus", operation, resident_id: selectedResidentId(), args,
        });
        mergeWatchboardResponse(payload);
        return payload.binding;
      }

      async function bindWeixin(userId) {
        const epoch = ++bindingEpoch;
        try {
          let current = await weixinOperation("weixin-start", { user_id: userId });
          if (epoch !== bindingEpoch) return;
          showWatchboardModal("绑定微信", window.PortalI18n.html`
            <div class="watchboardWeixinBinding">
            <p data-weixin-account>绑定到 Portal 用户：<strong>${esc(userId)}</strong></p>
            <p class="paneNote">请由该用户使用自己的微信扫码。</p>
            <img data-weixin-qr width="240" height="240" alt="微信绑定二维码">
            <p data-weixin-status role="status" aria-live="polite"></p>
            <form data-weixin-pin-form hidden>
              <label>输入手机显示的数字<input data-weixin-pin inputmode="numeric" pattern="[0-9]+" autocomplete="off" required></label>
              <button class="portalButton" type="submit">提交数字</button>
            </form>
            <button class="portalButton" data-weixin-close>关闭</button></div>`, {
            onBind: overlay => {
              overlay.querySelector('.watchboardModalCard')?.classList.add('watchboardWeixinModal');
              let pin = "";
              const form = overlay.querySelector("[data-weixin-pin-form]");
              form.addEventListener("submit", event => {
                event.preventDefault();
                pin = overlay.querySelector("[data-weixin-pin]").value.trim();
                form.hidden = true;
              });
              overlay.querySelector("[data-weixin-close]").addEventListener("click", () => {
                bindingEpoch++; closeWatchboardModal();
              });
              async function poll() {
                if (epoch !== bindingEpoch || !overlay.isConnected) return;
                if (current.qr_image) overlay.querySelector("[data-weixin-qr]").src = current.qr_image;
                overlay.querySelector("[data-weixin-qr]").hidden = ["active", "expired", "unbound"].includes(current.status);
                const needsPin = String(current.status).includes("need_verifycode");
                form.hidden = !needsPin;
                const labels = { active: "微信已绑定，可以在微信发送 /help", expired: "二维码已过期，请重新开始", scaned: "已扫码，请在手机上确认" };
                overlay.querySelector("[data-weixin-status]").textContent = window.PortalI18n.t(
                  needsPin ? "输入手机显示的数字" : labels[current.status] || "请用微信扫码");
                if (["active", "expired", "unbound"].includes(current.status)) { renderWatchboard(); return; }
                window.setTimeout(async () => {
                  if (epoch !== bindingEpoch || !overlay.isConnected) return;
                  try {
                    if (!needsPin || pin) {
                      const submitted = pin;
                      current = await weixinOperation("weixin-get", { user_id: userId, session_id: current.session_id, ...(submitted ? { pin: submitted } : {}) });
                      if (!current.retryable) pin = "";
                    }
                    poll();
                  } catch (error) {
                    if (epoch !== bindingEpoch || !overlay.isConnected) return;
                    const status = Number(error?.status || error?.httpStatus || 0);
                    const transient = [0, 408, 429, 502, 503, 504].includes(status);
                    overlay.querySelector("[data-weixin-status]").textContent = transient
                      ? window.PortalI18n.t("连接暂时中断，正在重新确认绑定状态…")
                      : portalUi.errorMessage(window.PortalI18n.t("绑定微信"), error);
                    if (transient) window.setTimeout(poll, 1500);
                  }
                }, 1500);
              }
              poll();
            },
          });
        } catch (error) { portalUi.toast(portalUi.errorMessage(window.PortalI18n.t("绑定微信"), error), "error"); }
      }

      async function unbindWeixin(userId) {
        try { await weixinOperation("weixin-unbind", { user_id: userId }); renderWatchboard(); }
        catch (error) { portalUi.toast(portalUi.errorMessage(window.PortalI18n.t("解除微信绑定"), error), "error"); }
      }

      function watchboardTelepusUsersHtml(dashboard) {
        const payload = dashboard?.telegram_users || {};
        if (!payload.enabled) return "";
        const users = Array.isArray(payload.users) ? payload.users : [];
        const canManage = !!payload.permissions?.can_manage;
        const weixinUsers = dashboard.weixin_users?.users || [];
        return window.PortalI18n.html`<section class="watchboardSection">
          <div class="watchboardSectionHead">
            <span>Telegram</span>
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
        </section>
        <section class="watchboardSection">
          <div class="watchboardSectionHead"><span>WeChat · 微信</span></div>
          <div class="watchboardSectionBody">
            <p class="paneNote" data-weixin-current-user>当前 Portal 用户：${esc(dashboard.weixin_users?.current_user || "—")}</p>
            <p class="paneNote" data-weixin-scope>所有人可查看绑定列表；仅本人或 Portal 管理员可操作。微信消息默认发往绑定用户的 AMA。</p>
            <div class="watchboardCompactList">${weixinUsers.map(user => {
              const userId = user.portal_actor;
              const status = user.status;
              return window.PortalI18n.html`<div class="watchboardCompactItem" data-weixin-row="${esc(userId)}">
                <span><strong data-weixin-account>${esc(userId)}</strong>
                  <span>${esc(window.PortalI18n.t(status === "active" ? "微信已绑定" : status === "expired" ? "需要重新扫码" : "微信未绑定"))}</span></span>
                <span class="watchboardNotificationSettings">
                  <label><input type="checkbox" data-weixin-notification="mention" data-weixin-user="${esc(userId)}" ${user.notifications?.mention ? "checked" : ""} ${user.can_manage && status === "active" ? "" : "disabled"}> 聊天室 @ 我</label>
                  <label><input type="checkbox" data-weixin-notification="question" data-weixin-user="${esc(userId)}" ${user.notifications?.question ? "checked" : ""} ${user.can_manage && status === "active" ? "" : "disabled"}> 待我回答</label>
                </span>
                <span>${user.can_manage ? window.PortalI18n.html`
                  ${status !== "active" ? window.PortalI18n.html`<button class="portalButton is-compact" data-weixin-bind="${esc(userId)}">${esc(window.PortalI18n.t(status === "expired" ? "重新绑定微信" : "绑定微信"))}</button>` : ""}
                  ${["active", "expired"].includes(status) ? window.PortalI18n.html`<button class="portalButton is-compact" data-weixin-unbind="${esc(userId)}">解除微信绑定</button>` : ""}` : window.PortalI18n.html`<span>仅查看</span>`}</span>
              </div>`;
            }).join("") || window.PortalI18n.html`<div class="paneNote">暂无微信绑定。</div>`}</div>
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
        box.querySelectorAll("[data-weixin-notification]").forEach(input => {
          input.addEventListener("change", async () => {
            input.disabled = true;
            try {
              await weixinOperation("weixin-notifications", { user_id: input.dataset.weixinUser, [input.dataset.weixinNotification]: input.checked });
              renderWatchboard();
            } catch (err) {
              input.checked = !input.checked;
              input.disabled = false;
              portalUi.toast(portalUi.errorMessage(window.PortalI18n.t("更新微信提醒"), err), "error");
            }
          });
        });
        box.querySelectorAll("[data-weixin-bind]").forEach(button => {
          button.addEventListener("click", () => bindWeixin(button.dataset.weixinBind));
        });
        box.querySelectorAll("[data-weixin-unbind]").forEach(button => {
          button.addEventListener("click", () => unbindWeixin(button.dataset.weixinUnbind));
        });
        box.querySelector("[data-telepus-add-user]")?.addEventListener("click", () => addTelepusTelegramUser());
        box.querySelectorAll("[data-telepus-edit-user]").forEach(btn => {
          btn.addEventListener("click", () => openTelepusTelegramUserModal(btn.dataset.telepusEditUser || ""));
        });
        box.querySelectorAll("[data-telepus-delete-user]").forEach(btn => {
          btn.addEventListener("click", () => deleteTelepusTelegramUser(btn.dataset.telepusDeleteUser || ""));
        });
      }
      return { render: dashboard => context.components.generic(dashboard, watchboardTelepusUsersHtml(dashboard)), bind,
        destroy() { bindingEpoch++; }, key: dashboard => JSON.stringify(state) };
    },
  });
})();
