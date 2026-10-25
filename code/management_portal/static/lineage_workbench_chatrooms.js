(() => {
  "use strict";

  if (window.PortalLineageWorkbenchChatrooms) return;

  // The Chatrooms side view: the list of Chatrooms the user belongs to with their
  // pins, unread state and menus, the open Chatroom shown over the chat area, and
  // the dialogs that create a Chatroom or change its members.
  const DEPENDENCIES = Object.freeze([
    "$id", "consoleStateHtml", "currentAgentChatPanel", "currentLineageWorkbench", "esc",
    "knownUsers", "renderChatHeader", "setSideView", "state",
  ]);

  // Personal Chatroom settings (pins); membership itself lives on the server.
  const CHATROOM_UI_NAMESPACE = "lineage-workbench.chatrooms";

  const chatroomOverlay = { host: null, view: null };

  function sameChatroomActor(left, right) {
    const clean = value => String(value || "").replace(/^[@＠]+/, "").trim().toLowerCase();
    return Boolean(clean(left)) && clean(left) === clean(right);
  }

  function chatroomActivity(chatroom) {
    return Date.parse(chatroom?.last_message?.created_at || chatroom?.updated_at || "") || 0;
  }

  // The card's time is secondary: as short as it can be (today HH:mm, this year M/D, else Y/M/D); the tooltip has it in full.
  function chatroomShortTime(timestamp, now = new Date()) {
    const date = new Date(timestamp);
    const pad = number => String(number).padStart(2, "0");
    if (date.toDateString() === now.toDateString()) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    const day = `${date.getMonth() + 1}/${date.getDate()}`;
    return date.getFullYear() === now.getFullYear() ? day : `${date.getFullYear()}/${day}`;
  }

  function openChatroomDialog(markup) {
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay lineageDialogOverlay chatroomDialogOverlay";
    overlay.hidden = true;
    overlay.innerHTML = markup;
    document.body.append(overlay);
    let dialog = null;
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      dialog?.close?.({ force: true });
      dialog?.destroy?.();
      overlay.remove();
    };
    dialog = window.PortalUI.createDialogController({
      modal: overlay,
      bodyClass: null,
      backdropClose: true,
      escapeClose: true,
      requestClose: close,
    });
    dialog.open();
    return { overlay, close };
  }

  function createChatrooms(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchChatrooms is missing: ${missing.join(", ")}`);
    const {
      $id, consoleStateHtml, currentAgentChatPanel, currentLineageWorkbench, esc, knownUsers,
      renderChatHeader, setSideView, state,
    } = dependencies;

    let chatroomMenuPopover = null;

    let chatroomMenuTarget = null;

    function chatroomUserName(userId) {
      const id = String(userId || "").trim();
      if (!id) return "";
      const listed = state.chatroomUsers.find(item => sameChatroomActor(item.username, id));
      if (listed) return String(listed.display_name || listed.username || id);
      const user = knownUsers().find(item => sameChatroomActor(item.id, id));
      return String(user?.name || user?.display_name || id);
    }

    async function loadChatroomUsers({ force = false } = {}) {
      if (state.chatroomUsers.length && !force) return state.chatroomUsers;
      const result = await window.PortalInstructionClient.execute("ListUsers", {}).catch(() => null);
      if (Array.isArray(result?.users)) state.chatroomUsers = result.users;
      return state.chatroomUsers;
    }

    function chatroomAuthorName(principalId) {
      const id = String(principalId || "").trim();
      if (!/^resident_[0-9a-f]{12}$/.test(id)) return chatroomUserName(id);
      const record = (state.residents || []).find(item => String(item?.resident_id || "") === id);
      return String(record?.title || record?.name || id);
    }

    function chatroomDisplayTitle(chatroom) {
      return String(chatroom?.title || "").trim()
        || (chatroom?.members || []).map(chatroomUserName).join("、")
        || window.PortalI18n.t("聊天室");
    }

    function chatroomEntries() {
      return state.chatroomRecords
        .map(chatroom => ({ chatroom, pinnedAt: String(state.chatroomPins.get(chatroom.chatroom_id) || "") }))
        .sort((left, right) => (Number(Boolean(right.pinnedAt)) - Number(Boolean(left.pinnedAt)))
          || (chatroomActivity(right.chatroom) - chatroomActivity(left.chatroom)));
    }

    function chatroomMenuHtml(chatroom) {
      const id = String(chatroom.chatroom_id || "");
      const pinnedAt = state.chatroomPins.get(id);
      const muted = window.PortalConversationStore.entry(`chatroom:${id}`)?.muted ?? chatroom.muted;
      const permissions = chatroom.permissions || {};
      const action = (name, label, extra = "") => window.PortalI18n.html`<button type="button" ${extra} data-chatroom-action="${name}" data-chatroom-id="${esc(id)}">${label}</button>`;
      return [
        action(pinnedAt ? "unpin" : "pin", pinnedAt ? "取消置顶" : "置顶"),
        action(muted ? "unmute" : "mute", muted ? "取消消息免打扰" : "消息免打扰"),
        permissions.can_rename ? action("rename", "改名") : "",
        permissions.can_add_members ? action("add-members", "添加成员") : "",
        permissions.can_leave ? action("leave", "退出聊天室", 'class="danger"') : "",
        permissions.can_delete ? action("delete", "删除聊天室", 'class="danger"') : "",
      ].join("");
    }

    function renderChatroomMenu() {
      if (!chatroomMenuPopover || !chatroomMenuTarget) return;
      const chatroom = chatroomRecord(chatroomMenuTarget.id);
      if (!chatroom) { chatroomMenuPopover.close(); return; }
      const panel = chatroomMenuPopover.element;
      const html = chatroomMenuHtml(chatroom);
      if (panel.renderedHtml !== html) {
        const focusedAction = panel.contains(document.activeElement) ? document.activeElement.dataset.chatroomAction : "";
        panel.innerHTML = html;
        panel.renderedHtml = html;
        if (focusedAction) panel.querySelector(`[data-chatroom-action="${focusedAction}"]`)?.focus();
      }
      chatroomMenuPopover.reposition();
    }

    function openChatroomMenu(trigger, id, event) {
      if (chatroomMenuTarget?.trigger === trigger && chatroomMenuPopover?.isOpen()) {
        chatroomMenuPopover.close(event);
        return;
      }
      chatroomMenuPopover?.close();
      chatroomMenuTarget = { trigger, id };
      if (!chatroomMenuPopover) {
        const panel = document.createElement("div");
        panel.className = "chatroomCardMenu";
        panel.setAttribute("role", "menu");
        panel.setAttribute("aria-label", window.PortalI18n.t("聊天室操作"));
        panel.hidden = true;
        document.body.append(panel);
        chatroomMenuPopover = window.PortalOverlay.bindDynamicPopover({
          panel, placement: "bottom-end", portal: true,
          onOpen: () => {
            chatroomMenuTarget.trigger.classList.add("active");
            chatroomMenuTarget.trigger.closest(".chatroomCard")?.classList.add("menuOpen");
          },
          onClose: () => {
            chatroomMenuTarget.trigger.classList.remove("active");
            chatroomMenuTarget.trigger.closest(".chatroomCard")?.classList.remove("menuOpen");
          },
        });
        panel.addEventListener("click", event => {
          const button = event.target.closest("[data-chatroom-action]");
          if (!button) return;
          chatroomMenuPopover.close(event);
          handleChatroomAction(button.dataset.chatroomAction, button.dataset.chatroomId)
            .catch(error => window.PortalUI.toast(window.PortalI18n.text`聊天室操作失败：${error.message || error}`, "error"));
        });
      }
      renderChatroomMenu();
      chatroomMenuPopover.open(trigger, event);
    }

    function renderChatroomCard({ chatroom, pinnedAt }) {
      const id = String(chatroom.chatroom_id || "");
      const active = state.activeChatroomId === id;
      const viewerState = window.PortalConversationStore.entry(`chatroom:${id}`) || {};
      const unread = Number(viewerState.unread_count || 0);
      const muted = Boolean(viewerState.muted ?? chatroom.muted);
      const mentions = Number(viewerState.unread_mention_count || 0);
      const attention = viewerState.attention_unread ?? Boolean(unread);
      const last = chatroom.last_message || {};
      const meta = last.preview
        ? `${last.author_id === "portal" ? "" : `${chatroomAuthorName(last.author_id)}：`}${last.preview}`
        : `${(chatroom.members || []).length} 位成员`;
      const activity = chatroomActivity(chatroom);
      const time = activity ? chatroomShortTime(activity) : "";
      const fullTime = activity
        ? (window.PortalUI?.formatDateTime?.(new Date(activity).toISOString(), { style: "calendar-minute" }) || "")
        : "";
      return window.PortalI18n.html`<div class="chatroomCardWrap" data-chatroom-card="${esc(id)}">
          <div class="agentItem chatroomCard ${active ? "active" : ""} ${pinnedAt ? "pinned" : ""} ${attention ? "unread" : ""}">
            <button type="button" class="agentSelect noStatusDot" data-chatroom-select="${esc(id)}">
              <span class="chatroomEntryIcon" data-portal-icon="users" aria-hidden="true"></span>
              <span class="agentMain">
                <span class="chatroomEntryHead">
                  <span class="agentTitle">${esc(chatroomDisplayTitle(chatroom))}</span>
                  ${unread ? window.PortalI18n.html`<span class="chatroomUnreadCount${muted ? " is-muted" : ""}" aria-label="${unread} 条未读" title="${unread} 条未读">${unread > 99 ? "99+" : unread}</span>` : ""}
                  ${mentions ? window.PortalI18n.html`<span class="chatroomMentionBadge" title="定位第一条未读 @ 消息">@我 ${mentions}</span>` : ""}
                  ${muted ? window.PortalI18n.t('<span class="chatroomMutedMark" data-portal-icon="bell-off" aria-label="消息免打扰" title="消息免打扰"></span>') : ""}
                  ${pinnedAt ? window.PortalI18n.t('<span class="chatroomPinnedMark" data-portal-icon="pin" role="img" aria-label="已置顶"></span>') : ""}
                </span>
                <span class="chatroomEntryLine">
                  <span class="agentMeta">${esc(meta)}</span>
                  ${time ? window.PortalI18n.html`<span class="chatroomEntryTime" title="${esc(fullTime)}">${esc(time)}</span>` : ""}
                </span>
              </span>
            </button>
            <button type="button" class="agentMenuBtn" data-chatroom-menu="${esc(id)}" aria-label="打开聊天室操作菜单" title="打开聊天室操作菜单" data-portal-icon="more-horizontal"></button>
          </div>
        </div>`;
    }

    function renderChatroomList() {
      const root = $id("chatroomList");
      if (!root) return;
      const entries = chatroomEntries();
      const ids = new Set(entries.map(entry => entry.chatroom.chatroom_id));
      let list = root.querySelector(".chatroomList");
      if (!entries.length) {
        if (chatroomMenuTarget?.trigger.closest("#chatroomList")) chatroomMenuPopover?.close();
        root.innerHTML = consoleStateHtml("还没有聊天室。点击加号新建。");
        return;
      }
      if (!list) {
        list = document.createElement("div");
        list.className = "agentList chatroomList";
        root.replaceChildren(list);
      }
      const cards = new Map(Array.from(list.children).map(card => [card.dataset.chatroomCard, card]));
      for (const [id, card] of cards) {
        if (ids.has(id)) continue;
        if (card.contains(chatroomMenuTarget?.trigger)) chatroomMenuPopover?.close();
        card.remove();
      }
      let before = list.firstElementChild;
      for (const entry of entries) {
        const id = entry.chatroom.chatroom_id;
        const html = renderChatroomCard(entry);
        let card = cards.get(id);
        if (!card || card.renderedHtml !== html) {
          const template = document.createElement("template");
          template.innerHTML = html;
          const next = template.content.firstElementChild;
          if (card) {
            // Keep the buttons and open popover anchor alive across refreshes.
            const item = card.querySelector(".chatroomCard");
            const menuOpen = item.classList.contains("menuOpen");
            item.className = next.querySelector(".chatroomCard").className;
            item.classList.toggle("menuOpen", menuOpen);
            card.querySelector("[data-chatroom-select]").innerHTML = next.querySelector("[data-chatroom-select]").innerHTML;
          } else {
            card = next;
            card.querySelector("[data-chatroom-select]").addEventListener("click", () => {
              selectChatroom(id).catch(error => window.PortalUI.toast(window.PortalI18n.text`打开聊天室失败：${error.message || error}`, "error"));
            });
            const trigger = card.querySelector("[data-chatroom-menu]");
            trigger.addEventListener("click", event => openChatroomMenu(trigger, id, event));
          }
          card.renderedHtml = html;
        }
        if (card !== before) list.insertBefore(card, before);
        before = card.nextElementSibling;
      }
      renderChatroomMenu();
    }

    function applyChatroomPins(payload) {
      state.chatroomPins = new Map((Array.isArray(payload?.items) ? payload.items : [])
        .map(item => [String(item?.value?.chatroom_id || item?.key || "").trim(), String(item?.value?.pinned_at || "")])
        .filter(([id, pinnedAt]) => /^chatroom_[0-9a-f]{12}$/.test(id) && pinnedAt));
    }

    function applyPersonalAmaAssociation(payload) {
      const association = (Array.isArray(payload?.items) ? payload.items : [])
        .find(item => String(item?.key || "") === "primary")
        || (Array.isArray(payload?.items) ? payload.items[0] : null);
      const residentId = String(association?.value?.resident_id || "").trim();
      state.personalAmaResidentId = state.residents.some(
        item => String(item?.resident_id || "").trim() === residentId,
      ) ? residentId : "";
    }

    async function loadChatrooms() {
      const requestSeq = ++state.chatroomRequestSeq;
      if ($id("chatroomList") && !state.chatroomRecords.length) {
        $id("chatroomList").innerHTML = consoleStateHtml("正在读取聊天室。", { state: "loading" });
      }
      try {
        const [pins, chatrooms] = await Promise.all([
          window.PortalIdentity.listUIAssociations(CHATROOM_UI_NAMESPACE),
          window.PortalConversationStore.readChatrooms(),
          loadChatroomUsers(),
        ]);
        if (requestSeq !== state.chatroomRequestSeq) return;
        applyChatroomPins(pins);
        applyChatroomRecords(chatrooms?.chatrooms);
        renderChatroomList();
      } catch (err) {
        if (requestSeq !== state.chatroomRequestSeq) return;
        $id("chatroomList").innerHTML = consoleStateHtml(`加载失败：${err.message || err}`, { state: "error" });
      }
    }

    function applyChatroomRecords(records) {
      const wasListed = Boolean(chatroomRecord(state.activeChatroomId));
      state.chatroomRecords = Array.isArray(records) ? records : [];
      const active = chatroomRecord(state.activeChatroomId);
      if (active) syncChatroomPanelTitle(active);
      else if (wasListed) {
        // The open Chatroom was deleted by its creator (or this user left it elsewhere).
        closeChatroomView();
        window.PortalUI?.toast?.("这个聊天室已被删除，或你已不再是成员。", "info");
      }
    }

    function chatroomRecord(chatroomId) {
      return state.chatroomRecords.find(item => String(item?.chatroom_id || "") === String(chatroomId || "")) || null;
    }

    // A Chatroom is shown over the Chat panel; the Resident conversation below it
    // keeps its own target and comes back when a Resident is selected again.
    function chatroomHost() {
      const panelRoot = currentAgentChatPanel()?.workspace?.root;
      const container = panelRoot?.parentElement;
      if (!container) return null;
      if (!chatroomOverlay.host) {
        chatroomOverlay.host = document.createElement("section");
        chatroomOverlay.host.className = "lineageChatroomHost";
        chatroomOverlay.host.setAttribute("aria-label", "Chatroom");
        chatroomOverlay.host.hidden = true;
      }
      if (chatroomOverlay.host.parentElement !== container) {
        container.classList.add("lineageChatroomContainer");
        container.append(chatroomOverlay.host);
      }
      if (!chatroomOverlay.view) {
        chatroomOverlay.view = window.PortalChatroomView.mount({
          root: chatroomOverlay.host,
          id: "lineage-chatroom",
          me: () => state.me || "",
          authorName: chatroomAuthorName,
          title: chatroomDisplayTitle,
          onMenu: (trigger, id, event) => openChatroomMenu(trigger, id, event),
          onRoomChange: room => {
            if (!room || room.chatroom_id !== state.activeChatroomId) return;
            const index = state.chatroomRecords.findIndex(item => item.chatroom_id === room.chatroom_id);
            if (index >= 0) state.chatroomRecords[index] = { ...state.chatroomRecords[index], ...room };
            syncChatroomPanelTitle(room);
            if (state.sideView === "chatrooms") renderChatroomList();
          },
          onError: error => window.PortalUI.toast(error.message || String(error), "error"),
        });
      }
      return chatroomOverlay.host;
    }

    function syncChatroomPanelTitle(chatroom) {
      if (!chatroom || chatroom.chatroom_id !== state.activeChatroomId) return;
      renderChatHeader();
    }

    function syncChatroomLocation() {
      const target = new URL(window.location.href);
      if (state.activeChatroomId) target.searchParams.set("chatroom", state.activeChatroomId);
      else target.searchParams.delete("chatroom");
      history.replaceState(history.state || {}, "", `${target.pathname}${target.search}${target.hash}`);
    }

    async function selectChatroom(chatroomId) {
      const id = String(chatroomId || "").trim();
      if (!/^chatroom_[0-9a-f]{12}$/.test(id)) return false;
      currentLineageWorkbench()?.focusPanel?.(currentAgentChatPanel()?.instanceId?.() || "chat");
      const host = chatroomHost();
      if (!host) throw new Error("Chat 面板尚未就绪");
      if (state.activeChatroomId !== id) chatroomMenuPopover?.close();
      state.activeChatroomId = id;
      host.hidden = false;
      // The Resident Chat below is not being read while a Chatroom covers it.
      host.parentElement?.classList.add("chatroomActive");
      syncChatroomLocation();
      if (state.sideView === "chatrooms") renderChatroomList();
      const room = await chatroomOverlay.view.setChatroom(id);
      if (state.activeChatroomId !== id) return false;
      if (room && !chatroomRecord(id)) {
        state.chatroomRecords = [room, ...state.chatroomRecords];
        if (state.sideView === "chatrooms") renderChatroomList();
      }
      syncChatroomPanelTitle(room || chatroomRecord(id));
      await chatroomOverlay.view.revealNextMention();
      return true;
    }

    function closeChatroomView() {
      if (!state.activeChatroomId) return;
      if (chatroomMenuTarget?.id === state.activeChatroomId) chatroomMenuPopover?.close();
      state.activeChatroomId = "";
      if (chatroomOverlay.host) {
        chatroomOverlay.host.hidden = true;
        chatroomOverlay.host.parentElement?.classList.remove("chatroomActive");
      }
      syncChatroomLocation();
      renderChatHeader();
      if (state.sideView === "chatrooms") renderChatroomList();
    }

    async function setChatroomPinned(chatroomId, pinned) {
      if (pinned) {
        await window.PortalIdentity.putUIAssociation(CHATROOM_UI_NAMESPACE, chatroomId, {
          chatroom_id: chatroomId, pinned_at: new Date().toISOString(),
        });
      } else {
        await window.PortalIdentity.removeUIAssociation(CHATROOM_UI_NAMESPACE, chatroomId);
      }
      await loadChatrooms();
    }

    // One user picker serves “新建聊天室” and “添加成员”.
    async function openChatroomUserPicker({ title, confirmLabel, exclude = [], withTitle = false, onConfirm }) {
      const candidates = (await loadChatroomUsers({ force: true }))
        .filter(user => !sameChatroomActor(user.username, state.me))
        .filter(user => !exclude.some(id => sameChatroomActor(id, user.username)))
        .map(user => ({
          id: String(user.username || ""),
          title: String(user.display_name || user.username || ""),
          subtitle: String(user.username || ""),
        }))
        .filter(item => item.id);
      const { overlay, close } = openChatroomDialog(window.PortalI18n.html`
          <div class="portalDetailSurface chatroomPicker" data-portal-detail-surface role="dialog" aria-modal="true" aria-label="${esc(title)}">
            <div class="portalDetailHeader">
              <h2>${esc(title)}</h2>
              <button class="portalDetailClose" data-portal-icon="close" type="button" data-chatroom-picker-close aria-label="关闭" title="关闭"></button>
            </div>
            <div class="portalDetailForm">
              ${withTitle ? window.PortalI18n.t('<label class="residentField"><span>聊天室名称（可选）</span><input class="residentInput" data-chatroom-picker-title maxlength="80" autocomplete="off" /></label>') : ""}
              <input class="residentInput" data-chatroom-picker-search type="search" placeholder="搜索用户" autocomplete="off" />
              <div class="chatroomPickerList" data-chatroom-picker-list role="listbox" aria-multiselectable="true"></div>
              <div class="chatroomPickerHint" data-chatroom-picker-hint></div>
            </div>
            <div class="portalDetailFooter">
              <button class="portalButton" type="button" data-chatroom-picker-close>取消</button>
              <button class="portalButton is-primary" type="button" data-chatroom-picker-confirm ${withTitle ? "" : "disabled"}>${esc(confirmLabel)}</button>
            </div>
          </div>`);
      const selected = new Set();
      const list = overlay.querySelector("[data-chatroom-picker-list]");
      const search = overlay.querySelector("[data-chatroom-picker-search]");
      const confirm = overlay.querySelector("[data-chatroom-picker-confirm]");
      const hint = overlay.querySelector("[data-chatroom-picker-hint]");
      const render = () => {
        const query = String(search.value || "").trim().toLowerCase();
        const visible = candidates.filter(item => !query
          || item.title.toLowerCase().includes(query)
          || item.subtitle.toLowerCase().includes(query)).slice(0, 200);
        list.innerHTML = visible.length ? visible.map(item => window.PortalI18n.html`
            <button type="button" class="chatroomPickerItem ${selected.has(item.id) ? "selected" : ""}" role="option"
              aria-selected="${selected.has(item.id) ? "true" : "false"}" data-chatroom-picker-item="${esc(item.id)}">
              <span class="chatroomEntryIcon" data-portal-icon="user" aria-hidden="true"></span>
              <span class="agentMain"><span class="agentTitle">${esc(item.title)}</span><span class="agentMeta">${esc(item.subtitle)}</span></span>
            </button>`).join("") : consoleStateHtml("没有匹配的用户。", { state: "empty" });
        confirm.disabled = !withTitle && !selected.size;
        hint.textContent = selected.size
          ? window.PortalI18n.text`已选择 ${selected.size} 位用户`
          : (withTitle ? window.PortalI18n.t("可以先只有自己，之后再添加成员；在消息中输入 @ 选择要召唤的 Resident。") : "");
      };
      list.addEventListener("click", event => {
        const button = event.target.closest("[data-chatroom-picker-item]");
        if (!button) return;
        const id = button.dataset.chatroomPickerItem || "";
        if (selected.has(id)) selected.delete(id);
        else selected.add(id);
        render();
      });
      search.addEventListener("input", render);
      overlay.querySelectorAll("[data-chatroom-picker-close]").forEach(button => button.addEventListener("click", close));
      confirm.addEventListener("click", async () => {
        confirm.disabled = true;
        try {
          const chosenTitle = String(overlay.querySelector("[data-chatroom-picker-title]")?.value || "").trim();
          await onConfirm([...selected], chosenTitle);
          close();
        } catch (error) {
          window.PortalUI.toast(error.message || String(error), "error");
          confirm.disabled = false;
        }
      });
      render();
      (overlay.querySelector("[data-chatroom-picker-title]") || search).focus();
    }

    function openCreateChatroomPicker() {
      return openChatroomUserPicker({
        title: window.PortalI18n.t("新建聊天室"),
        confirmLabel: "创建",
        withTitle: true,
        onConfirm: async (members, title) => {
          const result = await window.PortalInstructionClient.execute("CreateChatroom", {
            members,
            ...(title ? { title } : {}),
          }, { idempotencyKey: `chatroom-create:${globalThis.PortalUIPrimitives.randomUUID()}` });
          setSideView("chatrooms");
          await loadChatrooms();
          await selectChatroom(result?.chatroom?.chatroom_id);
        },
      });
    }

    async function afterChatroomChange(chatroomId) {
      await loadChatrooms();
      if (state.activeChatroomId === chatroomId) chatroomOverlay.view?.refresh?.invalidate?.({ delay: 0 });
    }

    async function handleChatroomAction(action, chatroomId) {
      const chatroom = chatroomRecord(chatroomId);
      if (!chatroom) return;
      if (action === "mute" || action === "unmute") {
        await window.PortalInstructionClient.execute("UpdateChatroom", { chatroom_id: chatroomId, muted: action === "mute" });
        await window.PortalConversationStore.readChatrooms(chatroomId);
        await afterChatroomChange(chatroomId);
        return;
      }
      if (action === "pin" || action === "unpin") {
        await setChatroomPinned(chatroomId, action === "pin");
      } else if (action === "rename") {
        const title = await window.PortalUI.prompt({
          title: window.PortalI18n.t("聊天室改名"),
          label: window.PortalI18n.t("聊天室名称"),
          value: chatroom.title || "",
          confirmLabel: window.PortalI18n.t("保存"),
        });
        if (title === null) return;
        await window.PortalInstructionClient.execute("UpdateChatroom", {
          chatroom_id: chatroomId, title: String(title).trim(),
        }, { idempotencyKey: `chatroom-rename:${globalThis.PortalUIPrimitives.randomUUID()}` });
        await afterChatroomChange(chatroomId);
      } else if (action === "add-members") {
        await openChatroomUserPicker({
          title: window.PortalI18n.t("添加成员"),
          confirmLabel: "添加",
          exclude: chatroom.members || [],
          onConfirm: async members => {
            await window.PortalInstructionClient.execute("UpdateChatroom", {
              chatroom_id: chatroomId, add_members: members,
            }, { idempotencyKey: `chatroom-add:${globalThis.PortalUIPrimitives.randomUUID()}` });
            await afterChatroomChange(chatroomId);
          },
        });
      } else if (action === "leave") {
        const ok = await window.PortalUI.confirm({
          title: window.PortalI18n.t("退出聊天室？"),
          message: window.PortalI18n.text`确定退出「${chatroomDisplayTitle(chatroom)}」吗？`,
          description: window.PortalI18n.t("退出后不再看到这个聊天室；你是最后一位成员时，聊天室会被删除。"),
          confirmLabel: window.PortalI18n.t("退出"),
          tone: "danger",
        });
        if (!ok) return;
        await window.PortalInstructionClient.execute("LeaveChatroom", { chatroom_id: chatroomId }, {
          idempotencyKey: `chatroom-leave:${globalThis.PortalUIPrimitives.randomUUID()}`,
        });
        await window.PortalIdentity.removeUIAssociation(CHATROOM_UI_NAMESPACE, chatroomId).catch(() => {});
        if (state.activeChatroomId === chatroomId) closeChatroomView();
        await loadChatrooms();
      } else if (action === "delete") {
        const ok = await window.PortalUI.confirm({
          title: window.PortalI18n.t("删除聊天室？"),
          message: window.PortalI18n.text`确定删除「${chatroomDisplayTitle(chatroom)}」吗？`,
          description: window.PortalI18n.t("删除后所有成员都不再看到这个聊天室和其中的消息，被 @ 过的 Resident 也不能再读取。此操作不能撤销。"),
          confirmLabel: window.PortalI18n.t("删除"),
          tone: "danger",
        });
        if (!ok) return;
        await window.PortalInstructionClient.execute("DeleteChatroom", { chatroom_id: chatroomId }, {
          idempotencyKey: `chatroom-delete:${globalThis.PortalUIPrimitives.randomUUID()}`,
        });
        await window.PortalIdentity.removeUIAssociation(CHATROOM_UI_NAMESPACE, chatroomId).catch(() => {});
        if (state.activeChatroomId === chatroomId) closeChatroomView();
        await loadChatrooms();
      }
    }

    return Object.freeze({
      chatroomOverlay, chatroomDisplayTitle, renderChatroomList, applyPersonalAmaAssociation,
      loadChatrooms, applyChatroomRecords, chatroomRecord, selectChatroom, closeChatroomView,
      openCreateChatroomPicker,
    });
  }

  window.PortalLineageWorkbenchChatrooms = Object.freeze({
    createChatrooms, dependencies: DEPENDENCIES, chatroomOverlay,
  });
})();
