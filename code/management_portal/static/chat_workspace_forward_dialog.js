(() => {
  if (window.PortalChatWorkspaceForwardDialog) return;

  // Forwarding chosen messages: the dialog picks a Resident in a Lineage or
  // project, by resident_id, or a Chatroom, with the targets the caller loads,
  // and hands the messages and a note to the caller's submit.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml",
  ]);

  function createForwardDialog(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceForwardDialog is missing: ${missing.join(", ")}`);
    const {
      escapeHtml,
    } = dependencies;

    let residentForwardDialog = null;

    function ensureResidentForwardDialog() {
      if (residentForwardDialog?.overlay?.isConnected) return residentForwardDialog;
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay residentForwardOverlay";
      overlay.dataset.portalDetailOverlay = "";
      overlay.hidden = true;
      overlay.setAttribute("aria-hidden", "true");
      overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface residentForwardSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="residentForwardTitle">
      <div class="portalDetailHeader" data-portal-detail-header>
        <div class="residentModalTitle" id="residentForwardTitle">转发</div>
        <button class="portalDetailClose" data-portal-detail-close data-portal-icon="close" type="button" aria-label="关闭转发窗口" title="关闭"></button>
      </div>
      <form class="portalDetailForm residentForwardForm" data-resident-forward-form>
        <div class="residentForwardField" data-forward-destination-field hidden>
          <label for="residentForwardDestination">转发到</label>
          <select class="portalControl" id="residentForwardDestination" data-forward-destination>
            <option value="resident">Resident</option><option value="chatroom">聊天室</option>
          </select>
        </div>
        <div class="residentForwardField" data-forward-chatroom-field hidden>
          <label for="residentForwardChatroom">聊天室</label>
          <select class="portalControl" id="residentForwardChatroom" data-forward-chatroom></select>
          <small data-forward-chatroom-hint></small>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardScope">问答 / 项目</label>
          <select class="portalControl" id="residentForwardScope" data-resident-forward-scope></select>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardLineage">Lineage</label>
          <select class="portalControl" id="residentForwardLineage" data-resident-forward-lineage></select>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardTarget">Resident</label>
          <select class="portalControl" id="residentForwardTarget" data-resident-forward-target></select>
          <small data-resident-forward-target-hint>先选择范围和 Lineage，再选择具体 Resident。</small>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardDirectId">按 Resident ID 定位</label>
          <div class="residentForwardDirectRow">
            <input class="portalControl" id="residentForwardDirectId" data-resident-forward-direct-id autocomplete="off" spellcheck="false" placeholder="resident_…">
            <button class="portalButton" type="button" data-resident-forward-direct-locate>定位</button>
          </div>
          <small data-resident-forward-direct-hint>也可以跳过上方选择，直接输入你有权访问的 resident_id。</small>
        </div>
        <div class="residentForwardField">
          <label for="residentForwardMessage">附加消息</label>
          <textarea class="portalControl" id="residentForwardMessage" data-resident-forward-message placeholder="随转发附加的信息"></textarea>
        </div>
        <div class="residentForwardSummary" data-resident-forward-summary></div>
        <div class="portalDetailFooter" data-portal-detail-footer>
          <button class="portalButton" type="button" data-resident-forward-cancel>取消</button>
          <button class="portalButton is-primary" type="submit" data-resident-forward-submit>发送</button>
        </div>
      </form>
    </div>`;
      document.body.appendChild(overlay);
      window.PortalDetailSurface?.mountAll?.(overlay);
      const dialog = window.PortalUI.createDialogController({
        modal: overlay,
        backdropClose: true,
        escapeClose: true,
        requestClose: () => dialog.close(),
      });
      overlay.querySelector("[data-portal-detail-close]")?.addEventListener("click", () => dialog.close());
      overlay.querySelector("[data-resident-forward-cancel]")?.addEventListener("click", () => dialog.close());
      residentForwardDialog = { overlay, dialog };
      return residentForwardDialog;
    }

    async function openResidentForwardDialog(items = [], options = {}) {
      const messages = Array.isArray(items) ? items.filter(item => String(item?.content || "").trim()) : [];
      if (!messages.length) {
        window.PortalUI?.toast?.("请先选择至少一条消息。", "warning");
        return false;
      }
      const runtime = ensureResidentForwardDialog();
      const overlay = runtime.overlay;
      const form = overlay.querySelector("[data-resident-forward-form]");
      const scope = overlay.querySelector("[data-resident-forward-scope]");
      const lineage = overlay.querySelector("[data-resident-forward-lineage]");
      const target = overlay.querySelector("[data-resident-forward-target]");
      const targetHint = overlay.querySelector("[data-resident-forward-target-hint]");
      const directId = overlay.querySelector("[data-resident-forward-direct-id]");
      const directLocate = overlay.querySelector("[data-resident-forward-direct-locate]");
      const directHint = overlay.querySelector("[data-resident-forward-direct-hint]");
      const message = overlay.querySelector("[data-resident-forward-message]");
      const summary = overlay.querySelector("[data-resident-forward-summary]");
      const submit = overlay.querySelector("[data-resident-forward-submit]");
      const destination = overlay.querySelector("[data-forward-destination]");
      const chatroom = overlay.querySelector("[data-forward-chatroom]");
      const chatroomHint = overlay.querySelector("[data-forward-chatroom-hint]");
      const loadVersion = Number(form.__residentForwardLoadVersion || 0) + 1;
      form.__residentForwardLoadVersion = loadVersion;
      const isCurrentLoad = () => Number(form.__residentForwardLoadVersion || 0) === loadVersion;
      let currentTargets = [];
      let directTarget = null;
      let chatrooms = [];
      let chatroomLoadVersion = 0;
      let sending = false;
      const isChatroom = () => destination.value === "chatroom";

      const syncSubmit = () => {
        submit.disabled = sending || (isChatroom() ? chatroom.disabled || !chatroom.value
          : !String(directTarget?.resident_id || target.value || "").trim());
      };

      destination.value = "resident";
      overlay.querySelector("[data-forward-destination-field]").hidden = !options.loadChatrooms;
      overlay.querySelector("[data-forward-chatroom-field]").hidden = true;
      for (const field of [scope, lineage, target, directId]) field.closest(".residentForwardField").hidden = false;
      chatroom.onchange = syncSubmit;
      destination.onchange = async () => {
        for (const field of [scope, lineage, target, directId]) field.closest(".residentForwardField").hidden = isChatroom();
        overlay.querySelector("[data-forward-chatroom-field]").hidden = !isChatroom();
        if (!isChatroom()) { syncSubmit(); return; }
        const version = ++chatroomLoadVersion;
        chatroom.disabled = true;
        chatroom.innerHTML = window.PortalI18n.markup('<option value="">正在读取聊天室…</option>');
        chatroomHint.textContent = "";
        syncSubmit();
        try {
          const payload = await options.loadChatrooms();
          if (!isCurrentLoad() || version !== chatroomLoadVersion) return;
          chatrooms = (payload?.chatrooms || []).filter(room => room.permissions?.can_post);
          chatroom.innerHTML = chatrooms.length
            ? chatrooms.map(room => window.PortalI18n.html`<option value="${escapeHtml(room.chatroom_id)}">${escapeHtml(room.title || room.members?.join("、") || room.chatroom_id)}</option>`).join("")
            : window.PortalI18n.markup('<option value="">暂无可发送的聊天室</option>');
          chatroom.disabled = !chatrooms.length;
          chatroomHint.textContent = window.PortalI18n.t("以你的身份发送到聊天室；原消息中的 @ 作为引用保留。");
        } catch (error) {
          if (!isCurrentLoad() || version !== chatroomLoadVersion) return;
          chatroom.innerHTML = window.PortalI18n.markup('<option value="">聊天室加载失败</option>');
          chatroomHint.textContent = error?.message || String(error);
        }
        syncSubmit();
      };

      const clearDirectTarget = (clearInput = true) => {
        directTarget = null;
        if (clearInput) directId.value = "";
        directHint.textContent = window.PortalI18n.t("也可以跳过上方选择，直接输入你有权访问的 resident_id。");
        syncSubmit();
      };

      const renderTargets = (targets, selectedId = "") => {
        currentTargets = Array.isArray(targets) ? targets : [];
        if (!currentTargets.length) {
          target.innerHTML = window.PortalI18n.markup('<option value="">该 Lineage 暂无可发送的 Resident</option>');
          target.disabled = true;
          syncSubmit();
          targetHint.textContent = window.PortalI18n.t("该 Lineage 中没有当前用户可发送的 Resident。");
          return;
        }
        target.innerHTML = currentTargets.map(item => window.PortalI18n.html`<option value="${escapeHtml(item.resident_id || "")}">${escapeHtml(item.title || item.resident_id || "Resident")}</option>`).join("");
        if (selectedId && currentTargets.some(item => String(item?.resident_id || "") === selectedId)) {
          target.value = selectedId;
        }
        target.disabled = false;
        syncSubmit();
        targetHint.textContent = window.PortalI18n.text`${currentTargets.length} 个可发送的 Resident`;
      };

      const loadLineageTargets = async (lineageId, selectedId = "") => {
        const cleanLineageId = String(lineageId || "").trim();
        target.disabled = true;
        target.innerHTML = window.PortalI18n.markup('<option value="">正在读取 Resident…</option>');
        syncSubmit();
        targetHint.textContent = cleanLineageId ? window.PortalI18n.t("正在读取这个 Lineage 的联系人…") : window.PortalI18n.t("请先选择 Lineage。");
        if (!cleanLineageId) return [];
        const payload = await options.loadTargets?.({ lineageId: cleanLineageId });
        if (!isCurrentLoad()) return [];
        const targets = Array.isArray(payload?.targets) ? payload.targets : [];
        renderTargets(targets, selectedId);
        return targets;
      };

      const loadScopeLineages = async (scopeId, selectedLineageId = "") => {
        const cleanScopeId = String(scopeId || "").trim();
        lineage.disabled = true;
        lineage.innerHTML = window.PortalI18n.markup('<option value="">正在读取 Lineage…</option>');
        target.disabled = true;
        target.innerHTML = window.PortalI18n.markup('<option value="">请先选择 Lineage</option>');
        currentTargets = [];
        syncSubmit();
        if (!cleanScopeId) return [];
        const payload = await options.loadTargets?.({ scopeId: cleanScopeId });
        if (!isCurrentLoad()) return [];
        const lineages = Array.isArray(payload?.lineages) ? payload.lineages : [];
        if (!lineages.length) {
          lineage.innerHTML = window.PortalI18n.markup('<option value="">该范围暂无可发送的 Lineage</option>');
          targetHint.textContent = window.PortalI18n.t("该范围中没有当前用户可发送的 Resident。");
          return [];
        }
        lineage.innerHTML = lineages.map(item => window.PortalI18n.html`<option value="${escapeHtml(item.id || "")}">${escapeHtml(item.title || item.id || "Lineage")}</option>`).join("");
        if (selectedLineageId && lineages.some(item => String(item?.id || "") === selectedLineageId)) {
          lineage.value = selectedLineageId;
        }
        lineage.disabled = false;
        await loadLineageTargets(lineage.value);
        return lineages;
      };

      overlay.querySelector("#residentForwardTitle").textContent = options.title || window.PortalI18n.t("转发");
      submit.textContent = options.submitLabel || window.PortalI18n.t("发送");
      summary.textContent = options.summary || window.PortalI18n.text`将转发 ${messages.length} 条消息。`;
      message.closest(".residentForwardField").hidden = options.selectTargetOnly === true;
      message.value = "";
      directId.value = "";
      directHint.textContent = window.PortalI18n.t("也可以跳过上方选择，直接输入你有权访问的 resident_id。");
      scope.disabled = true;
      scope.innerHTML = window.PortalI18n.markup('<option value="">正在读取范围…</option>');
      lineage.disabled = true;
      lineage.innerHTML = window.PortalI18n.markup('<option value="">请先选择范围</option>');
      target.disabled = true;
      target.innerHTML = window.PortalI18n.markup('<option value="">请先选择 Lineage</option>');
      submit.disabled = true;
      runtime.dialog.open({ focusTarget: scope });

      const previousScopeChange = form.__residentForwardScopeChange;
      if (previousScopeChange) scope.removeEventListener("change", previousScopeChange);
      const handleScopeChange = () => {
        clearDirectTarget();
        loadScopeLineages(scope.value).catch(error => {
          if (!isCurrentLoad()) return;
          lineage.innerHTML = window.PortalI18n.markup('<option value="">Lineage 加载失败</option>');
          lineage.disabled = true;
          renderTargets([]);
          targetHint.textContent = window.PortalI18n.text`Lineage 加载失败：${error?.message || error}`;
        });
      };
      form.__residentForwardScopeChange = handleScopeChange;
      scope.addEventListener("change", handleScopeChange);

      const previousLineageChange = form.__residentForwardLineageChange;
      if (previousLineageChange) lineage.removeEventListener("change", previousLineageChange);
      const handleLineageChange = () => {
        clearDirectTarget();
        loadLineageTargets(lineage.value).catch(error => {
          if (!isCurrentLoad()) return;
          renderTargets([]);
          targetHint.textContent = window.PortalI18n.text`Resident 加载失败：${error?.message || error}`;
        });
      };
      form.__residentForwardLineageChange = handleLineageChange;
      lineage.addEventListener("change", handleLineageChange);

      const previousTargetChange = form.__residentForwardTargetChange;
      if (previousTargetChange) target.removeEventListener("change", previousTargetChange);
      const handleTargetChange = () => clearDirectTarget();
      form.__residentForwardTargetChange = handleTargetChange;
      target.addEventListener("change", handleTargetChange);

      const previousDirectLocate = form.__residentForwardDirectLocate;
      if (previousDirectLocate) directLocate.removeEventListener("click", previousDirectLocate);
      const handleDirectLocate = async () => {
        const residentId = String(directId.value || "").trim();
        if (!residentId) {
          directHint.textContent = window.PortalI18n.t("请输入 resident_id。");
          directId.focus();
          return;
        }
        window.PortalUI.setButtonBusy(directLocate, true, "定位中…");
        directHint.textContent = window.PortalI18n.t("正在校验权限并定位 Resident…");
        try {
          const payload = await options.loadTargets?.({ residentId });
          if (!isCurrentLoad()) return;
          const resolved = payload?.target && typeof payload.target === "object" ? payload.target : null;
          if (!resolved?.resident_id) throw new Error("未找到可发送的 Resident");
          directTarget = resolved;
          directHint.textContent = window.PortalI18n.text`已定位并将发送至：${resolved.title || resolved.resident_id}`;
          syncSubmit();
        } catch (error) {
          directTarget = null;
          syncSubmit();
          if (isCurrentLoad()) directHint.textContent = window.PortalI18n.text`定位失败：${error?.message || error}`;
        } finally {
          window.PortalUI.setButtonBusy(directLocate, false);
        }
      };
      form.__residentForwardDirectLocate = handleDirectLocate;
      directLocate.addEventListener("click", handleDirectLocate);

      const previousDirectKeydown = form.__residentForwardDirectKeydown;
      if (previousDirectKeydown) directId.removeEventListener("keydown", previousDirectKeydown);
      const handleDirectKeydown = event => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        directLocate.click();
      };
      form.__residentForwardDirectKeydown = handleDirectKeydown;
      directId.addEventListener("keydown", handleDirectKeydown);

      const previousDirectInput = form.__residentForwardDirectInput;
      if (previousDirectInput) directId.removeEventListener("input", previousDirectInput);
      const handleDirectInput = () => {
        if (String(directTarget?.resident_id || "") === String(directId.value || "").trim()) return;
        directTarget = null;
        directHint.textContent = window.PortalI18n.t("点击“定位”校验这个 resident_id；不定位则仍使用上方选择。");
        syncSubmit();
      };
      form.__residentForwardDirectInput = handleDirectInput;
      directId.addEventListener("input", handleDirectInput);

      const previousSubmit = form.__residentForwardSubmit;
      if (previousSubmit) form.removeEventListener("submit", previousSubmit);
      const handleSubmit = async event => {
        event.preventDefault();
        const targetResidentId = isChatroom() ? "" : String(directTarget?.resident_id || target.value || "").trim();
        const targetChatroomId = isChatroom() ? chatroom.value : "";
        if ((!targetResidentId && !targetChatroomId) || sending || submit.disabled) return;
        sending = true;
        window.PortalUI.setButtonBusy(submit, true, "发送中…");
        try {
          const result = await options.submit?.({
            ...(targetChatroomId ? { target_chatroom_id: targetChatroomId } : {
              target_resident_id: targetResidentId,
              target_lineage_id: String(directTarget?.lineage_id || lineage.value || ""),
            }),
            messages,
            message: String(message.value || ""),
          });
          const forwardedTarget = targetChatroomId ? chatrooms.find(room => room.chatroom_id === targetChatroomId)
            : directTarget || currentTargets.find(item => String(item?.resident_id || "") === targetResidentId);
          if (targetResidentId) window.PortalConversationStore?.remember?.({
            key: `resident:${targetResidentId}`, type: "resident", resident_id: targetResidentId,
            lineage_id: String(forwardedTarget?.lineage_id || lineage.value || ""),
            title: String(forwardedTarget?.title || targetResidentId), t: Date.now(),
          });
          runtime.dialog.close({ accepted: true });
          options.onSuccess?.(result, forwardedTarget || null);
          window.PortalUI.toast(options.successMessage || window.PortalI18n.t("已转发。"), "success");
        } catch (error) {
          window.PortalUI.toast(error?.message || String(error), "error");
        } finally {
          sending = false;
          if (isCurrentLoad()) {
            window.PortalUI.setButtonBusy(submit, false);
            syncSubmit();
          }
        }
      };
      form.__residentForwardSubmit = handleSubmit;
      form.addEventListener("submit", handleSubmit);
      try {
        const preferredLineageId = String(options.preferredLineageId || "").trim();
        const payload = await options.loadTargets?.({ preferredLineageId });
        if (!isCurrentLoad()) return false;
        const scopes = Array.isArray(payload?.scopes) ? payload.scopes : [];
        if (!scopes.length) {
          scope.innerHTML = window.PortalI18n.markup('<option value="">暂无可发送的 Resident</option>');
          scope.disabled = true;
          renderTargets([]);
        } else {
          scope.innerHTML = scopes.map(item => window.PortalI18n.html`<option value="${escapeHtml(item.id || "")}">${escapeHtml(item.title || item.id || window.PortalI18n.t("项目"))}</option>`).join("");
          const preferredScopeId = String(payload?.preferred_scope_id || "").trim();
          if (preferredScopeId && scopes.some(item => String(item?.id || "") === preferredScopeId)) {
            scope.value = preferredScopeId;
          }
          scope.disabled = false;
          await loadScopeLineages(scope.value, preferredLineageId);
        }
      } catch (error) {
        if (!isCurrentLoad()) return false;
        scope.innerHTML = window.PortalI18n.markup('<option value="">联系人加载失败</option>');
        scope.disabled = true;
        renderTargets([]);
        targetHint.textContent = window.PortalI18n.text`联系人加载失败：${error?.message || error}`;
      }
      return true;
    }

    return Object.freeze({
      openResidentForwardDialog,
    });
  }

  window.PortalChatWorkspaceForwardDialog = Object.freeze({
    createForwardDialog, dependencies: DEPENDENCIES,
  });
})();
