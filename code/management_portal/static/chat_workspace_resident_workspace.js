(() => {
  if (window.PortalChatWorkspaceResidentWorkspace) return;

  // The Chat of one Resident as a Workspace: its header chrome and ids, the
  // session actions it offers, and selecting messages to forward.
  const DEPENDENCIES = Object.freeze([
    "createWorkspace", "escapeHtml", "messageIdentity", "messageRole", "messageTitle",
    "normalizeRefs", "normalizeResidentTarget", "residentProtocolMessageToWorkspace",
    "residentSessionScope", "workspaceInstanceId",
  ]);

  const residentChromeIdSuffixes = Object.freeze({
    menu: "Menu",
    extensionTools: "ExtensionTools",
    extensionButton: "ExtensionButton",
    extensionMenu: "ExtensionMenu",
    forwardCancel: "ForwardCancel",
    compact: "Compact",
    resetContext: "ResetContext",
    watchboardSplitter: "WatchboardSplitter",
    watchboard: "Watchboard",
    forwardDock: "ForwardDock",
    forwardCount: "ForwardCount",
    forward: "Forward",
  });

  function residentChromeIds(options = {}) {
    const workspaceId = String(options.id || "residentChat").trim() || "residentChat";
    return Object.freeze(Object.fromEntries(
      Object.entries(residentChromeIdSuffixes).map(([name, suffix]) => [name, `${workspaceId}${suffix}`]),
    ));
  }

  function residentSessionActions(options = {}) {
    const active = options.active !== false;
    const compact = active && (options.runtimeCapabilities?.operations || []).includes("compact");
    return {
      extensions: active,
      forward: active,
      compact,
      compactDisabled: compact && !!options.compactBusy,
      resetContext: !!options.resetContext,
      resetContextDisabled: !!options.resetContextDisabled,
    };
  }

  function residentChatChrome(options = {}) {
    const chromeIds = residentChromeIds(options);
    const features = options.residentFeatures && typeof options.residentFeatures === "object"
      ? options.residentFeatures
      : {};
    const optionalVisible = name => features[name] === true ? "" : " hidden";
    return {
      extraActionsHtml: window.PortalI18n.html`
        <div class="chatExtensionTools" id="${chromeIds.extensionTools}">
          <button class="portalButton is-icon is-quiet iconBtn" id="${chromeIds.extensionButton}" type="button" aria-label="Chat Workspace 扩展" title="扩展" data-portal-icon="plus"></button>
          <div class="filterPopover chatExtensionMenu" id="${chromeIds.extensionMenu}" role="dialog" aria-label="Chat Workspace 扩展" aria-hidden="true"></div>
        </div>
        <button class="portalButton is-icon is-quiet iconBtn" id="${chromeIds.compact}" type="button" aria-label="压缩当前会话上下文" title="压缩当前会话上下文" hidden data-portal-icon="compact"></button>
        <button class="portalButton is-icon is-quiet iconBtn" id="${chromeIds.resetContext}" type="button" aria-label="清空 Resident 上下文" title="清空此前对话和上下文" hidden data-portal-icon="eraser"></button>
        ${options.extraActionsHtml || ""}
        ${options.onMenu ? window.PortalI18n.html`<button class="portalButton is-icon is-quiet iconBtn" id="${chromeIds.menu}" type="button" aria-label="Resident Menu" title="打开当前 Resident 的设置、Package、Mailbox 与工作日志" data-portal-icon="settings"></button>` : ""}`,
      bodySuffixHtml: window.PortalI18n.html`
        <div class="chatExtensionSplitter watchboardSplitter" id="${chromeIds.watchboardSplitter}" role="separator" aria-orientation="vertical" aria-label="调整 Watchboard 宽度" title="拖动调整 Watchboard 宽度"${optionalVisible("watchboard")}></div>
        <aside class="chatExtensionHost watchboard" id="${chromeIds.watchboard}" aria-label="Resident Watchboard"${optionalVisible("watchboard")}></aside>
        ${options.bodySuffixHtml || ""}`,
      afterBodyHtml: window.PortalI18n.html`
        <div class="forwardDock" id="${chromeIds.forwardDock}" role="group" aria-label="转发所选消息">
          <span class="forwardCount" id="${chromeIds.forwardCount}">已选择 0 条</span>
          <button class="portalButton is-quiet is-compact" id="${chromeIds.forwardCancel}" type="button" title="退出转发选择并清空所选消息">取消</button>
          <button class="portalButton is-primary is-compact" id="${chromeIds.forward}" type="button" title="选择 Resident 或聊天室并转发所选消息">转发</button>
        </div>
        ${options.afterBodyHtml || ""}`,
    };
  }

  function createResidentWorkspace(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceResidentWorkspace is missing: ${missing.join(", ")}`);
    const {
      createWorkspace, escapeHtml, messageIdentity, messageRole, messageTitle, normalizeRefs,
      normalizeResidentTarget, residentProtocolMessageToWorkspace, residentSessionScope,
      workspaceInstanceId,
    } = dependencies;

    class BaseChatWorkspace {
      constructor(options = {}) {
        this.options = options || {};
        this.controller = null;
      }

      extensionOptions() {
        return {};
      }

      mount() {
        const extension = this.extensionOptions() || {};
        this.controller = createWorkspace({
          ...this.options,
          ...extension,
        });
        return this;
      }

      get root() { return this.controller?.root || null; }
      get els() { return this.controller?.els || {}; }
      get state() { return this.controller?.state || {}; }
      setLoading(message) { return this.controller?.setLoading?.(message); }
      clearLoading() { return this.controller?.clearLoading?.(); }
      isLoading() { return this.controller?.isLoading?.() === true; }
      element(name) { return this.els?.[String(name || "")] || null; }
      addFiles(files) { return this.controller?.addFiles?.(files); }
      addReference(reference) { return this.controller?.addReference?.(reference); }
      retryAttachment(id) { return this.controller?.retryAttachment?.(id); }
      attachments() { return this.controller?.attachments?.() || []; }
      messages() { return this.controller?.messages?.() || []; }
      setAttachments(items) { return this.controller?.setAttachments?.(items); }
      updateAttachment(id, patch) { return this.controller?.updateAttachment?.(id, patch); }
      refs() { return this.controller?.refs?.() || normalizeRefs({}); }
      setRefs(refs) { return this.controller?.setRefs?.(refs); }
      payload() { return this.controller?.payload?.() || { text: "", plainText: "", attachments: [], refs: normalizeRefs({}) }; }
      refreshRefs() { return this.controller?.refreshRefs?.(); }
      quote() { return this.controller?.quote?.() || null; }
      messageQuoteTarget(key) { return this.controller?.messageQuoteTarget?.(key) || null; }
      setQuote(quote) { return this.controller?.setQuote?.(quote) || false; }
      clearQuote(options) { return this.controller?.clearQuote?.(options) || false; }
      revealMessage(messageId) { return this.controller?.revealMessage?.(messageId) || Promise.resolve(false); }
      clear(options) { return this.controller?.clear?.(options); }
      inputText() { return this.controller?.inputText?.() || ""; }
      draftText() { return this.controller?.draftText?.() || ""; }
      setInputText(value) { return this.controller?.setInputText?.(value); }
      setDraftText(value, attachments) { return this.controller?.setDraftText?.(value, attachments); }
      draftScope() { return this.controller?.draftScope?.() || ""; }
      saveDraft(options) { return this.controller?.saveDraft?.(options); }
      restoreDraft(scope, options) { return this.controller?.restoreDraft?.(scope, options); }
      clearDraft(scope, expected) { return this.controller?.clearDraft?.(scope, expected); }
      confirmSubmittedMessages(messageIds, scope) { return this.controller?.confirmSubmittedMessages?.(messageIds, scope); }
      submit(send, options) { return this.controller?.submit?.(send, options); }
      setBusy(value) { return this.controller?.setBusy?.(value); }
      updateControls() { return this.controller?.updateControls?.(); }
      setStatus(text) { return this.controller?.setStatus?.(text); }
      followLatest() { return this.controller?.followLatest?.(); }
      setTitle(title) { return this.controller?.setTitle?.(title); }
      setSubtitle(subtitle, parts) { return this.controller?.setSubtitle?.(subtitle, parts); }
      setComposer(composer) { return this.controller?.setComposer?.(composer); }
      setStopPending(value) { return this.controller?.setStopPending?.(value); }
      setMessages(messages, renderOptions) { return this.controller?.setMessages?.(messages, renderOptions); }
      appendMessage(message) { return this.controller?.appendMessage?.(message) === true; }
      setRuntime(details, live, renderOptions) { return this.controller?.setRuntime?.(details, live, renderOptions); }
      contract() { return this.state.contract || null; }
      applyUpdate(update, renderOptions) { return this.controller?.applyUpdate?.(update, renderOptions); }
      applyContract(contract, renderOptions) { return this.controller?.applyContract?.(contract, renderOptions); }
      destroy() {
        this.controller?.destroy?.();
        this.controller = null;
      }
    }

    class ResidentChatWorkspace extends BaseChatWorkspace {
      constructor(options = {}) {
        const id = workspaceInstanceId(options, "residentChat");
        const instanceOptions = { ...options, id };
        super(instanceOptions);
        this.chromeIds = residentChromeIds(instanceOptions);
        this.residentTarget = normalizeResidentTarget(instanceOptions.target || instanceOptions.residentTarget || {});
        this.historyTarget = this.residentTarget;
        this.forwardEnabled = true;
        this.forwardSelecting = false;
        this.forwardSelected = new Set();
        this.extensionPopover = null;
      }

      element(name) {
        if (name === "extensionMenu" && this.extensionPopover) return this.extensionPopover.element;
        const chromeId = this.chromeIds?.[String(name || "")];
        if (chromeId) return this.root?.querySelector(`#${CSS.escape(chromeId)}`) || null;
        return super.element(name);
      }

      messageKey(item) {
        return messageIdentity(item);
      }

      forwardSelectionGroup(key) {
        const items = Array.isArray(this.state.messages) ? this.state.messages : [];
        const index = items.findIndex((item, itemIndex) => this.messageKey(item, itemIndex, items) === key);
        if (index < 0 || messageRole(items[index]) !== "human") return [key];
        let lastAgentKey = "";
        for (let cursor = index + 1; cursor < items.length; cursor += 1) {
          if (messageRole(items[cursor]) === "human") break;
          if (messageRole(items[cursor]) === "agent") lastAgentKey = this.messageKey(items[cursor], cursor, items);
        }
        return lastAgentKey ? [key, lastAgentKey] : [key];
      }

      refreshForwardControls() {
        const count = this.forwardSelected.size;
        const dock = this.root?.querySelector(`#${CSS.escape(this.chromeIds.forwardDock)}`);
        const label = this.root?.querySelector(`#${CSS.escape(this.chromeIds.forwardCount)}`);
        const submit = this.root?.querySelector(`#${CSS.escape(this.chromeIds.forward)}`);
        dock?.classList.toggle("open", this.forwardSelecting);
        if (label) label.textContent = window.PortalI18n.text`已选择 ${count} 条`;
        if (submit) submit.disabled = count <= 0;
      }

      clearForwardSelection() {
        this.forwardSelecting = false;
        this.forwardSelected.clear();
        this.rerenderForwardSelection();
      }

      startForwardSelection(key) {
        if (!this.forwardEnabled || !this.state.messages?.some(item => this.messageKey(item) === key && !item.pending)) return;
        this.forwardSelecting = true;
        this.toggleForwardSelection(key, true);
      }

      forwardSelectionState() {
        return Object.freeze({
          active: this.forwardSelecting,
          count: this.forwardSelected.size,
        });
      }

      closeExtensionMenu() {
        this.extensionPopover?.close?.();
      }

      repositionExtensionMenu() {
        this.extensionPopover?.reposition?.();
      }

      focusComposer() {
        this.element("input")?.focus?.();
      }

      rerenderForwardSelection() {
        this.controller?.setMessages?.(this.state.messages || [], this.state.messageRenderOptions || {});
        this.refreshForwardControls();
      }

      toggleForwardSelection(key, checked) {
        const selected = new Set(this.forwardSelected);
        this.forwardSelectionGroup(key).forEach(groupKey => {
          if (checked) selected.add(groupKey);
          else selected.delete(groupKey);
        });
        this.forwardSelected = selected;
        this.rerenderForwardSelection();
      }

      bindForwardInputs(root) {
        root?.querySelectorAll?.("[data-resident-forward-select-key]").forEach(input => {
          if (input.dataset.residentForwardBound === "1") return;
          input.dataset.residentForwardBound = "1";
          input.addEventListener("change", () => {
            this.toggleForwardSelection(input.dataset.residentForwardSelectKey || "", input.checked);
          });
        });
      }

      selectedForwardMessages() {
        const items = Array.isArray(this.state.messages) ? this.state.messages : [];
        return items.map((item, index) => ({ item, index, key: this.messageKey(item, index, items) }))
          .filter(entry => this.forwardSelected.has(entry.key))
          .map(entry => ({
            // ForwardMessages reads each selected message by its IDs.
            resident_id: String(this.residentTarget?.resident_id || ""),
            message_id: String(entry.item?.message_id || entry.item?.id || ""),
            role: messageRole(entry.item),
            author: messageTitle(entry.item, entry.index, this.state.messageRenderOptions || {}),
            content: String(entry.item?.content || ""),
            created_at: entry.item?.created_at || "",
          }))
          .filter(item => item.content.trim() && item.message_id);
      }

      bindResidentChrome() {
        const extensionButton = this.root?.querySelector(`#${CSS.escape(this.chromeIds.extensionButton)}`);
        const extensionMenu = this.root?.querySelector(`#${CSS.escape(this.chromeIds.extensionMenu)}`);
        if (extensionButton && extensionMenu && window.PortalOverlay) {
          this.extensionPopover = window.PortalOverlay.bindPopover({
            trigger: extensionButton,
            panel: extensionMenu,
            boundary: this.root,
            placement: "bottom-end",
            ariaHaspopup: "dialog",
            portal: true,
            closeOnOutside: true,
            closeOnEscape: true,
            onOpen: context => {
              extensionMenu.classList.add("open");
              extensionMenu.setAttribute("aria-hidden", "false");
              extensionButton.classList.add("is-active");
              this.options.onExtensionOpen?.(extensionMenu, this, context);
              if (!extensionMenu.childElementCount) {
                const notice = document.createElement("p");
                notice.className = "paneNote";
                notice.textContent = window.PortalI18n.t("当前对话没有可用扩展。");
                extensionMenu.append(notice);
              }
            },
            onClose: context => {
              extensionMenu.classList.remove("open");
              extensionMenu.setAttribute("aria-hidden", "true");
              extensionButton.classList.remove("is-active");
              this.options.onExtensionClose?.(extensionMenu, this, context);
            },
          });
          extensionButton.addEventListener("click", event => this.extensionPopover?.toggle(event));
        }
        this.root?.querySelector(`#${CSS.escape(this.chromeIds.forwardCancel)}`)?.addEventListener("click", () => this.clearForwardSelection());
        this.root?.querySelector(`#${CSS.escape(this.chromeIds.forward)}`)?.addEventListener("click", () => {
          const items = this.selectedForwardMessages();
          if (!items.length) return window.PortalUI?.toast?.("请先选择至少一条消息。", "warning");
          this.options.onForward?.(items, this);
        });
        this.root?.querySelector(`#${CSS.escape(this.chromeIds.compact)}`)?.addEventListener("click", () => this.options.onCompact?.(this));
        this.root?.querySelector(`#${CSS.escape(this.chromeIds.resetContext)}`)?.addEventListener("click", () => this.options.onResetContext?.(this));
        this.root?.querySelector(`#${CSS.escape(this.chromeIds.menu)}`)?.addEventListener("click", () => this.options.onMenu?.(this));
        this.refreshForwardControls();
      }

      extensionOptions() {
        const options = this.options;
        return {
          ...residentChatChrome(options),
          draftNamespace: "resident-session",
          historyScope: () => this.inputHistoryScope(),
          composerInputHeight: Number(options.composerInputHeight || 72),
          composerMinHeight: Number(options.composerMinHeight || 66),
          composerMaxHeight: Number(options.composerMaxHeight || 360),
          // Whether Forward is offered does not render the messages again:
          // syncForwardAvailability shows or hides the menu items in place.
          messageRenderState: () => ({
            forwardSelecting: this.forwardSelecting,
            forwardSelected: [...this.forwardSelected].sort(),
          }),
          renderMessageMenuItems: (item, key) => String(options.renderMessageMenuItems?.(item, key) || "") + (
            !item.pending
              ? '<button type="button" data-portal-message-action="forward"' + (this.forwardEnabled ? "" : " hidden") + ' title="选择这条消息，并可继续多选后向 Resident 或聊天室转发">Forward</button>'
              : ""
          ),
          onMessageAction: context => {
            if (context.action === "forward") return this.startForwardSelection(context.key);
            return options.onMessageAction?.(context);
          },
          onSearchMessages: async (query, paging) => {
            const residentId = this.target().resident_id;
            if (!residentId) return { messages: [] };
            const result = await window.PortalResidentService.searchMessages([residentId], query, paging);
            return {
              messages: (result.results || []).filter(item => item.resident_id === residentId)
                .map(item => residentProtocolMessageToWorkspace(item.message)),
              next_page_token: result.next_page_token || "",
            };
          },
          messageClass: (item, index, items) => [
            options.messageClass?.(item, index, items) || "",
            this.forwardSelecting ? "forwardSelectable" : "",
            this.forwardSelected.has(this.messageKey(item, index, items)) ? "forwardSelected" : "",
          ].filter(Boolean).join(" "),
          renderMessagePrefix: (item, index, items) => {
            const original = String(options.renderMessagePrefix?.(item, index, items) || "");
            if (!this.forwardSelecting) return original;
            const key = this.messageKey(item, index, items);
            return window.PortalI18n.html`<label class="forwardCheck" title="选择转发"><input type="checkbox" data-resident-forward-select-key="${escapeHtml(key)}" ${this.forwardSelected.has(key) ? "checked" : ""} /></label>${original}`;
          },
          onMessagesRendered: (root, controller, incremental = false, changedRoots = []) => {
            options.onMessagesRendered?.(root, controller, this, incremental);
            for (const bindingRoot of incremental ? changedRoots : [root]) this.bindForwardInputs(bindingRoot);
            this.refreshForwardControls();
          },
        };
      }

      mount() {
        super.mount();
        this.controller.target = () => this.target();
        this.controller.sessionScope = () => this.sessionScope();
        this.bindResidentChrome();
        this.syncForwardAvailability();
        return this;
      }

      syncForwardAvailability() {
        this.root?.querySelectorAll('[data-portal-message-action="forward"]').forEach(button => {
          button.hidden = !this.forwardEnabled;
        });
      }

      setTarget(value = {}) {
        const next = normalizeResidentTarget(value);
        const previousScope = residentSessionScope(this.residentTarget);
        const nextScope = residentSessionScope(next);
        if (previousScope !== nextScope) {
          this.controller?.releaseResidentRuntime?.();
          this.saveDraft({ immediate: true });
          this.followLatest();
        }
        this.residentTarget = next;
        this.historyTarget = next;
        this.options.onTargetChange?.(next, this);
        return next;
      }

      setHistoryTarget(value = {}) {
        this.historyTarget = normalizeResidentTarget(value);
        return this.historyTarget;
      }

      target() {
        return this.residentTarget;
      }

      sessionScope() {
        return residentSessionScope(this.residentTarget);
      }

      inputHistoryScope() {
        if (this.historyTarget?.resident_id) return residentSessionScope(this.historyTarget);
        const pending = typeof this.options.pendingHistoryScope === "function"
          ? this.options.pendingHistoryScope()
          : this.options.pendingHistoryScope;
        return String(pending || residentSessionScope(this.historyTarget));
      }

      setResidentActionVisibility(actions = {}) {
        const set = (id, visible, disabled = false) => {
          const element = this.root?.querySelector(`#${CSS.escape(id)}`);
          if (!element) return;
          element.hidden = visible === false;
          element.disabled = !!disabled;
        };
        set(this.chromeIds.extensionTools, actions.extensions !== false);
        set(this.chromeIds.menu, actions.extensions !== false);
        const forwardEnabled = actions.forward !== false;
        if (this.forwardEnabled !== forwardEnabled) {
          this.forwardEnabled = forwardEnabled;
          this.syncForwardAvailability();
          if (!forwardEnabled && (this.forwardSelecting || this.forwardSelected.size)) this.clearForwardSelection();
        }
        set(this.chromeIds.compact, !!actions.compact, actions.compactDisabled);
        set(this.chromeIds.resetContext, !!actions.resetContext, actions.resetContextDisabled);
      }
    }

    return Object.freeze({
      residentSessionActions, ResidentChatWorkspace,
    });
  }

  window.PortalChatWorkspaceResidentWorkspace = Object.freeze({
    createResidentWorkspace, dependencies: DEPENDENCIES, residentSessionActions,
  });
})();
