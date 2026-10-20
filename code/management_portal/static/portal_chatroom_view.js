/* Chatroom view: a conversation among Users that can call any Resident (decision_a6bebf4ac283).
 *
 * The view is a Chat Workspace instance: messages, Markdown, resource chips and
 * attachments render exactly as in a Resident Chat. It reads and writes only
 * through the public discussion instructions. Messages are kept by position
 * (ordinal); new ones arrive by reading after the newest position, older ones
 * by the server's page token. Change notifications only wake the view; each
 * read is authoritative.
 *
 * Right-click on a message quotes it into the next message or forwards it to a
 * Resident, through the same instructions a Resident Chat uses (CreateChatroomMessage
 * with quote, ForwardMessages with chatroom_id). Messages from anyone but the
 * viewer sit on the left; only the viewer's own sit on the right.
 */
(function () {
  if (window.PortalChatroomView) return;

  const PAGE_SIZE = 100;
  const READ_DELAY_MS = 600;
  const REACTIONS = ["👍", "❤️", "😂", "🎉", "👀", "✅"];

  function execute(instruction, args = {}, options = {}) {
    return window.PortalInstructionClient.execute(instruction, args, options);
  }

  function esc(value) {
    return window.PortalUIPrimitives.escapeHtml(value);
  }

  function messageText(item) {
    return (Array.isArray(item?.content_parts) ? item.content_parts : [])
      .filter(part => part?.type === "text" && part.text)
      .map(part => String(part.text))
      .join("\n")
      .trim();
  }

  // A shown message with text can be quoted or forwarded; the server fills the quote's author and excerpt.
  function actionable(item) {
    return !item?.retracted_at && !item?.pending && Boolean(String(item?.message_id || "").trim()) && Boolean(messageText(item));
  }

  function sameUser(left, right) {
    return Boolean(left) && String(left).toLowerCase() === String(right || "").toLowerCase();
  }

  class ChatroomView {
    constructor(options = {}) {
      this.options = options;
      this.root = options.root;
      this.chatroomId = "";
      this.room = null;
      this.messages = new Map();
      this.olderToken = "";
      this.changeCursor = undefined;
      this.revision = 0;
      this.editDrafts = new Map();
      this.mutations = new Set();
      this.readTimer = null;
      this.lastReadOrdinal = 0;
      this.readPendingRevision = null;
      this.refresh = null;
      this.root.classList.add("portalChatroomView");
      this.controller = window.PortalChatWorkspace.createWorkspace({
        root: this.root,
        id: String(options.id || "chatroom-view"),
        draftStore: window.PortalChatWorkspace.createDraftStore({
          owner: () => this.me(),
          namespace: "chatroom",
        }),
        emptyText: window.PortalI18n.t("还没有消息。"),
        extraActionsHtml: window.PortalI18n.markup('<button type="button" class="portalButton is-compact" data-chatroom-next-mention hidden>下一条 @ 我</button><button type="button" class="portalButton is-icon is-quiet iconBtn" data-chatroom-header-menu aria-label="打开聊天室操作菜单" title="打开聊天室操作菜单" aria-haspopup="menu" aria-expanded="false" data-portal-icon="more-horizontal"></button>'),
        onSend: (payload, _workspace, submitOptions) => this.send(payload, submitOptions).catch(error => this.reportError(error)),
        onLoadOlder: () => this.loadOlder(),
        // What this member posted here, newest first, for the input history;
        // a retracted message comes back as it was.
        onLoadInputHistory: async (scope, pageToken) => {
          const id = this.chatroomId;
          if (!id || scope !== `chatroom:${id}`) return null;
          const result = await execute("ListInputHistory", { chatroom_id: id, ...(pageToken ? { cursor: pageToken } : {}), limit: 50 });
          if (id !== this.chatroomId) return null;
          return {
            messages: result.messages.map(message => window.PortalChatWorkspace.residentProtocolMessageToWorkspace(message)),
            next_page_token: result.next_cursor || "",
          };
        },
        onError: error => this.reportError(error),
        onSearchMessages: async (query, paging) => {
          const result = await execute("SearchMessages", {
            chatroom_id: this.chatroomId, query, ...(paging.pageToken ? { cursor: paging.pageToken } : {}), limit: paging.limit,
          });
          return { messages: result.messages.map(message => {
            const item = this.workspaceMessage(message);
            const files = (message.content_parts || []).filter(part => ["image", "file"].includes(part.type)).map(part => part.name || "");
            return { ...item, content: [item.content, ...files].filter(Boolean).join("\n") };
          }), next_page_token: result.next_cursor || "" };
        },
        attachmentUploadContext: () => ({ upload_target: this.chatroomId }),
        previewUrlForPath: path => window.PortalFileLinks.imagePreviewUrl(path),
        renderMarkdown: text => window.MD?.render?.(text, { linkifyResources: false }) || esc(text).replace(/\n/g, window.PortalI18n.markup("<br>")),
        formatTime: value => window.PortalUI?.formatDateTime?.(value, { style: "compact" }) || String(value || ""),
        enableLinkPreview: true,
        showMessageActions: () => true,
        renderMessageMenuItems: item => this.messageMenuHtml(item),
        onMessageAction: ({ action, item }) => this.messageAction(action, item),
        messageClass: item => [item?.role === "user" && !sameUser(item.author_id, this.me()) ? "messageIncoming" : "", item?.retracted_at ? "chatroomRetracted" : ""].filter(Boolean).join(" "),
        renderMessageFooter: item => this.messageFooterHtml(item),
        renderAfterMessage: (item, index, items, context) => this.callProgressHtml(item, context),
        renderMentions: html => this.renderMentions(html),
        mentionTitle: "提及成员或召唤 Resident",
        mentionCandidates: (query, _controller, request) => this.mentionCandidates(query, request),
        onMessagesRendered: () => this.scheduleRead(),
      });
      this.root.querySelector("[data-chatroom-header-menu]").addEventListener("click", event => this.options.onMenu?.(event.currentTarget, this.chatroomId, event));
      this.root.querySelector("[data-chatroom-next-mention]").addEventListener("click", () => this.revealNextMention().catch(error => this.reportError(error)));
      this.visibilityHandler = () => this.scheduleRead();
      document.addEventListener("visibilitychange", this.visibilityHandler);
      this.unsubscribeReadState = window.PortalConversationStore.subscribe(snapshot => {
        const entry = snapshot.entries.find(item => item.key === `chatroom:${this.chatroomId}`);
        if (entry && this.room) {
          for (const key of ["muted", "unread_count", "unread_mentions", "unread_mention_count", "attention_unread"]) {
            if (key in entry) this.room[key] = entry[key];
          }
          this.renderActions();
        }
        if (snapshot.entries.some(entry => entry.key === `chatroom:${this.chatroomId}` && entry.unread)) {
          this.scheduleRead();
        }
      });
    }

    me() {
      return String(typeof this.options.me === "function" ? this.options.me() : this.options.me || "");
    }

    reportError(error) {
      if (this.options.onError) this.options.onError(error);
      else window.PortalUI?.toast?.(error?.message || String(error), "error");
    }

    authorName(authorId) {
      if (authorId === "portal") return "Portal";
      return String(this.options.authorName?.(authorId) || authorId || "");
    }

    // Presentation only: names never replace the identity in stored text or delivery.
    // Resource labels reuse the shared resolver/cache, including Residents outside this page.
    renderMentions(html) {
      const template = document.createElement("template");
      template.innerHTML = html;
      const users = new Map((this.room?.members || []).map(id => [id.toLocaleLowerCase(), id]));
      users.set("全员", "全员");
      const escapeRe = value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const names = [...users.keys()].sort((a, b) => b.length - a.length).map(escapeRe);
      const pattern = new RegExp(`(?<![\\w@.+-])@(resident_[0-9a-f]{12}|${names.join("|")})(?![\\w@+-]|\\.[\\w])`, "gi");
      const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) {
        if (!walker.currentNode.parentElement?.closest("a,code,pre,blockquote,script,style,[data-resource-chip-skip],[data-mention-value],[data-portal-composer-attachment-token]")) nodes.push(walker.currentNode);
      }
      for (const node of nodes) {
        const text = node.textContent;
        const fragment = document.createDocumentFragment();
        let cursor = 0;
        for (const match of text.matchAll(pattern)) {
          fragment.append(text.slice(cursor, match.index));
          const id = users.get(match[1].toLocaleLowerCase()) || match[1];
          const mention = document.createElement("span");
          mention.className = "portalMention";
          mention.dataset.mentionValue = match[0];
          mention.setAttribute("data-resource-chip-skip", "");
          mention.title = match[0];
          mention.append("@");
          if (/^resident_[0-9a-f]{12}$/.test(id)) {
            const label = document.createElement("template");
            label.innerHTML = window.PortalResourceChips.resourceLabelHtml(id, { focusable: false });
            label.content.firstElementChild.textContent = this.authorName(id);
            const link = document.createElement("template");
            link.innerHTML = window.PortalResourceChips.resourceChipHtml(id);
            link.content.firstElementChild.replaceChildren(label.content);
            mention.append(link.content);
          } else mention.append(id === "全员" ? id : this.authorName(id));
          fragment.append(mention);
          cursor = match.index + match[0].length;
        }
        if (cursor) {
          fragment.append(text.slice(cursor));
          node.replaceWith(fragment);
        }
      }
      return template.innerHTML;
    }

    workspaceMessage(message) {
      const display = message.retracted_at ? { ...message, quote: undefined, content_parts: [{ type: "text", text: window.PortalI18n.t("这条消息已撤回") }] } : message;
      const projected = window.PortalChatWorkspace.residentProtocolMessageToWorkspace(display);
      const authorId = String(message.author_id || "");
      const name = this.authorName(authorId);
      const calls = (Array.isArray(this.room?.active_deliveries) ? this.room.active_deliveries : [])
        .filter(call => call?.trigger_message_id && call.trigger_message_id === message.message_id);
      return { ...display, ...projected, author_id: authorId, author: name, name, ...(calls.length ? { chatroom_calls: calls } : {}) };
    }

    // Typing @ lists all users, individual members, then Residents this
    // Chatroom has called (latest first) and others the member can message. A called
    // Resident the member cannot send to is listed but cannot be chosen, and
    // says why.
    async mentionCandidates(query, { signal, onUpdate } = {}) {
      const called = (Array.isArray(this.room?.called_residents) ? this.room.called_residents : []).slice().reverse();
      const item = (resident, group) => ({
        value: `@${resident.resident_id}`,
        title: String(resident.title || resident.resident_id),
        label: String(resident.resident_id),
        description: String(resident.lineage_title || ""),
        group,
        disabled: resident.can_send !== true,
        disabled_reason: String(resident.disabled_reason || ""),
      });
      const term = String(query || "").toLocaleLowerCase();
      const users = [
        { kind: "user", value: "@全员", label: window.PortalI18n.t("全员"), title: window.PortalI18n.t("全员"), description: window.PortalI18n.t("提醒本聊天室所有其他用户"), group: window.PortalI18n.t("本聊天室成员") },
        ...(this.room?.members || []).filter(id => !sameUser(id, this.me()))
          .map(id => ({ kind: "user", user_id: id, value: `@${id}`, label: id, title: this.authorName(id), group: window.PortalI18n.t("本聊天室成员") })),
      ]
        .filter(item => !term || `${item.label} ${item.title}`.toLocaleLowerCase().includes(term));
      const groups = [[], []];
      const combined = () => {
        const listed = new Set(groups[0].map(entry => entry.label));
        return [...users, ...groups[0], ...groups[1].filter(entry => !listed.has(entry.label))];
      };
      onUpdate?.(combined());
      const search = async (args, index, group) => {
        const result = await execute("SearchResidents", args, { signal });
        groups[index] = (result.residents || []).map(resident => item(resident, group));
        if (!signal?.aborted) onUpdate?.(combined());
      };
      // Each group is usable as soon as it arrives; one slow or failed read
      // must not withhold the other group's candidates.
      const results = await Promise.allSettled([
        called.length ? search({ q: query, resident_ids: called, limit: 50 }, 0, "本聊天室召唤过") : Promise.resolve(),
        search({ q: query, can_send: true, limit: 20 }, 1, "可以发消息的 Resident"),
      ]);
      const failure = results.find(result => result.status === "rejected");
      if (failure) throw failure.reason;
      return combined();
    }

    // Under a message that called Residents, each one shows how far it has got
    // - being handed the message, its place in the queue, or its live work -
    // as a Resident Chat does, until its reply arrives.  Its
    // intermediate replies are not shown here.
    callProgressHtml(item, context = {}) {
      const blocks = (Array.isArray(item?.chatroom_calls) ? item.chatroom_calls : [])
        .map(call => window.PortalChatWorkspace.requestProgressHtml(
          { ...call, title: this.authorName(call.resident_id), request_message_id: call.trigger_message_id },
          {},
          { actions: false },
        ))
        .join("");
      return blocks
        ? `<div class="chatroomCallProgress chatroomMessageAfter" data-message-key="${esc(context.key || "")}" data-message-after-of="${esc(context.anchorKey || "")}">${blocks}</div>` : "";
    }

    messageFooterHtml(item) {
      if (item.retracted_at) return "";
      const edited = item.edited_at
        ? `<span class="paneNote" title="${esc(item.edited_at)}">已编辑</span>` : "";
      const reactions = (item.reactions || []).map(reaction => `<button type="button" class="chatroomReaction ${reaction.active ? "active" : ""}" data-portal-message-action="react:${esc(reaction.emoji)}" aria-pressed="${reaction.active ? "true" : "false"}" aria-label="${esc(`${reaction.active ? "取消" : "添加"} ${reaction.emoji} 回应`)}" title="${esc(reaction.users.map(id => this.authorName(id)).join("、"))}">${esc(reaction.emoji)} ${reaction.count}</button>`).join("");
      return reactions || edited
        ? `<div class="chatroomMessageFooter">${reactions ? `<div class="chatroomReactions" role="group" aria-label="这条消息的表情回应">${reactions}</div>` : ""}${edited}</div>` : "";
    }

    messageMenuHtml(item) {
      if (item.retracted_at || item.pending) return "";
      const buttons = [];
      if (actionable(item)) buttons.push('<button type="button" data-portal-message-action="quote">引用回复</button><button type="button" data-portal-message-action="forward">转发…</button>');
      if (["user", "assistant"].includes(item.role)) buttons.push(`<div class="chatroomReactionChoices" role="group" aria-label="表情回应">${REACTIONS.map(emoji => `<button type="button" data-portal-message-action="react:${emoji}" aria-label="回应 ${emoji}" title="${emoji}">${emoji}</button>`).join("")}</div>`);
      if (item.can_edit) buttons.push('<button type="button" data-portal-message-action="edit">编辑消息</button>');
      if (item.can_retract) buttons.push('<button type="button" data-portal-message-action="retract">撤回消息</button>');
      return buttons.join("");
    }

    async mutateMessage(instruction, item, args, expectedVersion) {
      const roomId = this.chatroomId;
      const key = `${roomId}:${item.message_id}`;
      if (this.mutations.has(key)) return null;
      this.mutations.add(key);
      try {
        const result = await execute(instruction, { chatroom_id: roomId, message_id: item.message_id, ...args }, { idempotencyKey: `chatroom-change:${globalThis.PortalUIPrimitives.randomUUID()}`, expectedVersion });
        if (roomId === this.chatroomId) {
          this.mergeChanges([result.message]);
          this.apply();
          this.refresh?.invalidate?.({ delay: 0 });
        }
        return result;
      } finally { this.mutations.delete(key); }
    }

    async messageAction(action, item) {
      if (action === "edit" && item.can_edit) {
        const roomId = this.chatroomId;
        const key = `${roomId}:${item.message_id}`;
        const content = await window.PortalUI.prompt({ title: window.PortalI18n.t("编辑消息"), label: window.PortalI18n.t("消息正文"), multiline: true,
          value: this.editDrafts.get(key) ?? messageText(item), confirmLabel: window.PortalI18n.t("保存"),
          message: window.PortalI18n.t("附件会保留。编辑不会重新发送 @ 提醒，也不会修改已交给 Resident 的任务。") });
        if (content === null || roomId !== this.chatroomId) { this.editDrafts.delete(key); return; }
        this.editDrafts.set(key, content);
        if (await this.mutateMessage("UpdateChatroomMessage", item, { content }, item.version)) this.editDrafts.delete(key);
        return;
      }
      if (action === "retract" && item.can_retract) {
        const roomId = this.chatroomId;
        const yes = await window.PortalUI.confirm({ title: window.PortalI18n.t("撤回这条消息？"), message: window.PortalI18n.t("正文和附件会从聊天中隐藏，保留撤回提示。已交给 Resident 的任务仍会继续。"), confirmLabel: window.PortalI18n.t("撤回") });
        if (yes && roomId === this.chatroomId) await this.mutateMessage("RetractChatroomMessage", item, {}, item.version);
        return;
      }
      if (action.startsWith("react:") && !item.retracted_at) {
        const emoji = action.slice(6);
        return this.mutateMessage("UpdateChatroomReaction", item, { emoji, active: !item.reactions?.find(reaction => reaction.emoji === emoji)?.active });
      }
      if (!actionable(item)) return false;
      if (action === "quote") {
        const id = String(item.author_id || "");
        if (/^resident_[0-9a-f]{12}$/.test(id) && !this.controller.inputText().includes(`@${id}`)) {
          this.controller.setDraftText(`@${id} ${this.controller.draftText()}`);
        }
        return this.controller.setQuote({
          message_id: String(item.message_id),
          author_id: String(item.author_id || "portal"),
          text: window.PortalChatWorkspace.messageQuoteExcerpt(item),
        });
      }
      if (action !== "forward") return false;
      const chatroomId = this.chatroomId;
      return window.PortalChatWorkspace.openResidentForwardDialog([{
        chatroom_id: chatroomId,
        message_id: String(item.message_id),
        role: item.role,
        author: item.author || item.author_id || "",
        content: messageText(item),
        created_at: item.created_at || "",
      }], {
        summary: "将转发这条聊天室消息。",
        loadTargets: query => window.PortalResidentService.forwardTargets(query),
        loadChatrooms: () => window.PortalResidentService.forwardChatrooms(),
        submit: body => window.PortalResidentService.forward(body),
      });
    }

    newestOrdinal() {
      let newest = 0;
      for (const ordinal of this.messages.keys()) newest = Math.max(newest, ordinal);
      return newest;
    }

    contract() {
      const room = this.room || {};
      const permissions = room.permissions || {};
      const ordered = [...this.messages.entries()].sort((left, right) => left[0] - right[0]).map(entry => entry[1]);
      const members = (room.members || []).map(id => this.authorName(id));
      // The header shows the Chatroom ID the way a Resident Chat shows its resident_id: double-click copies it.
      const subtitleParts = [
        { text: this.chatroomId, identity: "chatroom", copy_value: this.chatroomId, copy_label: "chatroom_id" },
        { text: window.PortalI18n.text`${members.length} 位成员：${members.join("、")}` },
      ];
      return {
        schema: "portal.chat_workspace.v3",
        workspace_key: `chatroom:${this.chatroomId}`,
        title: String(this.options.title?.(room) || room.title || "Chatroom"),
        subtitle: subtitleParts.map(part => part.text).join(" · "),
        header: { subtitle_parts: subtitleParts },
        messages: ordered.map(message => this.workspaceMessage(message)),
        history: { can_load_older: Boolean(this.olderToken) },
        rendering: { user_name: this.me() || "用户", assistant_name: "Resident" },
        composer: {
          can_send: permissions.can_post === true,
          can_attach: permissions.can_post === true,
          can_stop: false,
          can_auto: false,
          refs_enabled: false,
          history_scope: `chatroom:${this.chatroomId}`,
          // The same right-click hint as a Resident Chat's composer; Chatrooms have no /portal-help.
          placeholder: window.PortalI18n.t("Ctrl/⌘ + Enter 发送；输入 @ 提及成员或召唤 Resident；右键消息可引用、转发"),
          disabled_reason: permissions.can_post === true ? "" : "只有聊天室成员可以发言",
        },
      };
    }

    apply(renderOptions = {}) {
      this.controller.applyContract(this.contract(), renderOptions);
      this.renderActions();
      this.options.onRoomChange?.(this.room, this);
    }

    renderActions() {
      this.root.querySelector("[data-chatroom-header-menu]").disabled = !this.room;
      const mention = this.root.querySelector("[data-chatroom-next-mention]");
      const count = Number(this.room?.unread_mention_count || 0);
      mention.hidden = !count;
      mention.textContent = window.PortalI18n.text`下一条 @ 我 (${count})`;
    }

    async revealNextMention() {
      const target = this.room?.unread_mentions?.[0];
      if (!target || this.navigatingMention) return;
      const revision = this.revision;
      this.navigatingMention = true;
      try {
        if (await this.controller.revealMessage(target.message_id) && revision === this.revision) {
          await this.markRead(target.ordinal);
        }
      } finally { this.navigatingMention = false; }
    }

    async setChatroom(chatroomId) {
      const id = String(chatroomId || "").trim();
      if (id === this.chatroomId && this.room) {
        this.refresh?.invalidate?.({ delay: 0 });
        await window.PortalConversationStore.readChatrooms(id);
        this.scheduleRead();
        return this.room;
      }
      this.refresh?.destroy?.();
      this.refresh = null;
      this.chatroomId = id;
      this.room = null;
      this.messages = new Map();
      this.olderToken = "";
      this.changeCursor = undefined;
      this.lastReadOrdinal = 0;
      const revision = ++this.revision;
      if (!id) return null;
      this.controller.setLoading("正在读取聊天室。");
      try {
        const [roomResult, page] = await Promise.all([
          window.PortalConversationStore.readChatrooms(id),
          execute("ListChatroomMessages", { chatroom_id: id, limit: PAGE_SIZE }),
        ]);
        if (revision !== this.revision) return null;
        this.room = roomResult.chatroom || null;
        this.mergeMessages(page.messages);
        this.changeCursor = page.change_cursor;
        this.olderToken = String(page.next_cursor || "");
        this.apply();
        this.watch(revision);
        return this.room;
      } catch (error) {
        if (revision === this.revision) {
          this.controller.clearLoading();
          this.controller.applyContract({
            schema: "portal.chat_workspace.v3",
            workspace_key: `chatroom:${id}`,
            title: window.PortalI18n.t("聊天室不可用"),
            messages: [],
            rendering: { empty_text: error?.message || "无法读取这个聊天室。" },
            composer: { can_send: false, can_attach: false, refs_enabled: false, can_auto: false },
          });
        }
        throw error;
      }
    }

    mergeMessages(messages) {
      for (const message of Array.isArray(messages) ? messages : []) {
        const ordinal = Number(message?.ordinal || 0);
        if (ordinal) this.messages.set(ordinal, message);
      }
    }

    mergeChanges(messages) {
      for (const changed of messages || []) {
        if (this.messages.has(Number(changed.ordinal))) this.messages.set(Number(changed.ordinal), changed);
        if (changed.retracted_at) {
          for (const [ordinal, message] of this.messages) {
            if (message.quote?.message_id === changed.message_id) this.messages.set(ordinal, { ...message, quote: { ...message.quote, text: window.PortalI18n.t("这条消息已撤回") } });
          }
        }
      }
    }

    watch(revision) {
      const id = this.chatroomId;
      this.refresh = window.PortalRefreshCoordinator.create({
        key: () => `chatroom:${id}`,
        interval: 30000,
        initialDelay: 30000,
        fetch: () => Promise.all([
          window.PortalConversationStore.readChatrooms(id),
          execute("ListChatroomMessages", { chatroom_id: id, after: this.newestOrdinal(), limit: 200, ...(this.changeCursor !== undefined ? { change_cursor: this.changeCursor } : {}) }),
        ]),
        apply: ([roomResult, page]) => {
          if (revision !== this.revision) return;
          this.room = roomResult.chatroom || this.room;
          this.mergeMessages(page.messages);
          this.mergeChanges(page.updated_messages);
          this.changeCursor = page.change_cursor;
          this.apply();
        },
        subscribe: window.PortalRefreshCoordinator.portalSubscription({
          domains: ["projection"],
          refreshOnAttach: false,
          debounceMs: 120,
          matches: detail => Boolean(detail?.snapshot_required || detail?.projection_changes?.chatroom?.[id]),
        }),
      });
      this.refresh.start();
    }

    async loadOlder() {
      const id = this.chatroomId;
      const revision = this.revision;
      if (!id || !this.olderToken) return null;
      const page = await execute("ListChatroomMessages", { chatroom_id: id, cursor: this.olderToken, limit: PAGE_SIZE });
      if (revision !== this.revision) return null;
      this.mergeMessages(page.messages);
      this.olderToken = String(page.next_cursor || "");
      this.controller.applyContract(this.contract());
      return page;
    }

    async send(payload, submitOptions = {}) {
      const id = this.chatroomId;
      if (!id) return null;
      const parts = window.PortalChatWorkspace.composerPayloadContentParts(payload);
      if (!parts.length) return null;
      // A quote names only the quoted message (decision_c524c775e0be); retries reuse the same key.
      const quote = submitOptions.quote === false ? null : this.controller.quote();
      const idempotencyKey = `chatroom-post:${globalThis.PortalUIPrimitives.randomUUID()}`;
      return this.controller.submit(async () => {
        const result = await execute("CreateChatroomMessage", {
          chatroom_id: id,
          content_parts: parts,
          ...(quote ? { quote: { message_id: quote.message_id } } : {}),
        }, { idempotencyKey });
        if (id === this.chatroomId) {
          this.mergeMessages([result.message]);
          this.lastReadOrdinal = Math.max(this.lastReadOrdinal, Number(result.message?.ordinal || 0));
          this.apply();
          this.refresh?.invalidate?.({ delay: 0 });
          const refused = (result.deliveries || []).filter(item => item.status === "undeliverable");
          if (refused.length) {
            window.PortalUI?.toast?.(`有 ${refused.length} 位 Resident 没有收到这条消息，原因见聊天室提示。`, "warning");
          }
        }
        return result;
      }, {
        payload,
        historyText: String(payload?.plainText || payload?.text || "").trim(),
        sentMessageId: result => result?.message?.message_id,
        clear: submitOptions.clear,
        focus: submitOptions.focus,
        clearRefsOnSuccess: submitOptions.clearRefsOnSuccess !== false,
        // Drop the quote only while the composer still quotes that message.
        onSuccess: () => {
          if (quote) this.controller.clearQuote({ messageId: quote.message_id });
        },
      });
    }

    scheduleRead() {
      if (this.readTimer) clearTimeout(this.readTimer);
      this.readTimer = setTimeout(() => {
        this.readTimer = null;
        this.markRead().catch(() => {});
      }, READ_DELAY_MS);
    }

    async markRead(through = null) {
      const id = this.chatroomId;
      const revision = this.revision;
      if (!id || !this.room || document.visibilityState === "hidden" || this.root.hidden || !this.root.isConnected) return;
      if (through === null && this.navigatingMention) return;
      if (this.readPendingRevision === revision) return;
      const firstMention = this.room.unread_mentions?.[0]?.ordinal;
      const newest = through ?? Math.min(this.newestOrdinal(), firstMention ? firstMention - 1 : Infinity);
      if (through === null && firstMention && newest <= this.lastReadOrdinal) return;
      if (newest <= this.lastReadOrdinal && !window.PortalConversationStore.isUnread(`chatroom:${id}`)) return;
      this.readPendingRevision = revision;
      try {
        const result = await window.PortalConversationStore.observeChatroomRead(id, newest);
        if (!result || revision !== this.revision || !this.room) return;
        this.lastReadOrdinal = Math.max(this.lastReadOrdinal, newest);
        Object.assign(this.room, result);
        this.renderActions();
      } finally {
        if (this.readPendingRevision === revision) this.readPendingRevision = null;
      }
    }

    destroy() {
      this.revision += 1;
      this.refresh?.destroy?.();
      if (this.readTimer) clearTimeout(this.readTimer);
      document.removeEventListener("visibilitychange", this.visibilityHandler);
      this.unsubscribeReadState();
      this.controller?.destroy?.();
      this.root.classList.remove("portalChatroomView");
    }
  }

  window.PortalChatroomView = Object.freeze({
    mount: options => new ChatroomView(options),
  });
}());
