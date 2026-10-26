(() => {
  if (window.PortalChatWorkspaceInputs) return;

  // Inputs kept beside the conversation: the queue of sent inputs waiting for the
  // Resident, the Arrow-key history of what this person typed, and the drafts
  // saved per conversation.
  const DEPENDENCIES = Object.freeze([
    "attachmentKind", "COMPOSER_DRAFT_ATTACHMENT_RE", "escapeHtml", "inputCaretLineState",
    "isComposerImeActive", "normalizeComposerAttachments", "refsPayload",
    "renderMarkdownWithAttachmentRefs", "residentProtocolMessageToWorkspace",
  ]);

  function workspaceQueueActionId(item) {
    return String(item?.message_id || "").trim();
  }

  function workspaceQueueItemMessage(item) {
    const parts = Array.isArray(item?.content_parts) ? item.content_parts : [];
    const text = parts
      .filter(part => part?.type === "text")
      .map(part => String(part.text || ""))
      .join("\n")
      .trim() || String(item?.content_preview || "").trim();
    return text || window.PortalI18n.t("（空消息）");
  }

  function workspaceQueueItemLabel(item, options = {}) {
    const actor = String(item?.author_id || "user").trim();
    const timeValue = item?.enqueued_at || "";
    const time = options.formatTime ? options.formatTime(timeValue) : String(timeValue || "");
    const refs = Number(item?.context_ref_count || item?.refs_count || 0);
    return [actor, time, refs ? `${refs} refs` : ""].filter(Boolean).join(" · ");
  }

  function normalizeWorkspaceQueue(queue) {
    const cfg = Array.isArray(queue) ? { items: queue } : (queue && typeof queue === "object" ? queue : {});
    const rawItems = Array.isArray(cfg.items) ? cfg.items : [];
    return {
      ...cfg,
      items: rawItems,
      actionInFlight: !!(cfg.action_in_flight || cfg.actionInFlight || cfg.busy),
    };
  }

  function bindWorkspaceQueue(root, options = {}) {
    if (!root || root.dataset.portalWorkspaceQueueBound === "1") return;
    root.dataset.portalWorkspaceQueueBound = "1";
    root.addEventListener("click", event => {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest("button")?.disabled) return;
      if (target?.closest("[data-workspace-queue-merge-all]")) {
        event.preventDefault();
        options.onMergeAll?.();
        return;
      }
      const clearBtn = target?.closest("[data-workspace-queue-clear]");
      if (clearBtn) {
        event.preventDefault();
        options.onClear?.();
        return;
      }
      if (target?.closest("[data-workspace-queue-mailbox]")) {
        event.preventDefault();
        options.onMailbox?.();
        return;
      }
      const inject = target?.closest("[data-workspace-queue-inject]");
      if (inject) {
        event.preventDefault();
        options.onInject?.(inject.dataset.workspaceQueueInject || "");
        return;
      }
      const deleteBtn = target?.closest("[data-workspace-queue-delete]");
      if (deleteBtn) {
        event.preventDefault();
        options.onDelete?.(deleteBtn.dataset.workspaceQueueDelete || "");
      }
    });
  }

  function createInputs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceInputs is missing: ${missing.join(", ")}`);
    const {
      attachmentKind, COMPOSER_DRAFT_ATTACHMENT_RE, escapeHtml, inputCaretLineState,
      isComposerImeActive, normalizeComposerAttachments, refsPayload,
      renderMarkdownWithAttachmentRefs, residentProtocolMessageToWorkspace,
    } = dependencies;

    function workspaceQueueItemContentHtml(item, options = {}) {
      const contentParts = Array.isArray(item?.content_parts) ? item.content_parts : [];
      const message = contentParts.length
        ? residentProtocolMessageToWorkspace({
            message_id: workspaceQueueActionId(item),
            role: "user",
            content_parts: contentParts,
          })
        : { content: workspaceQueueItemMessage(item) };
      // Queue text is an inline summary, not a full Markdown message body.
      return renderMarkdownWithAttachmentRefs(message.content, {
        ...options,
        renderMarkdown: value => escapeHtml(value).replace(/\s+/g, " "),
      });
    }

    function workspaceQueueHtml(queue, options = {}) {
      const cfg = normalizeWorkspaceQueue(queue);
      if (!cfg.items.length) return "";
      const label = cfg.label || cfg.title || window.PortalI18n.t("排队消息");
      const canInject = cfg.active_turn_input_supported === true;
      // Merge all uses the Mailbox selection contract (decision_15f7df737ed5).
      // The Menu still provides selection, splitting and reordering.
      const mailbox = options.mailbox
        ? window.PortalI18n.html`<button class="workspaceQueueMailbox" type="button" data-workspace-queue-mailbox title="打开 Menu 的 Mailbox 页">在 Mailbox 中合并、拆分、调序 ›</button>`
        : "";
      const mergeAll = options.mergeAll && cfg.items.filter(item => item.kind !== "info").length >= 2
        ? window.PortalI18n.html`<button class="portalButton is-compact workspaceQueueBtn" type="button" data-workspace-queue-merge-all ${cfg.actionInFlight ? "disabled" : ""} title="合并全部待处理请求和群体消息，不包含背景 info；至少需要两项">合并所有</button>`
        : "";
      return window.PortalI18n.html`<div class="workspaceQueue" aria-label="${escapeHtml(cfg.aria_label || window.PortalI18n.t("排队消息"))}">
      <div class="workspaceQueueHeader"><div class="workspaceQueueTitle"><span>${escapeHtml(label)}</span>${mailbox}</div><div class="workspaceQueueHeaderActions">${mergeAll}<span>${cfg.items.length}</span>${cfg.items.length > 1 ? window.PortalI18n.html`<button class="portalButton is-compact workspaceQueueBtn" type="button" data-workspace-queue-clear ${cfg.actionInFlight ? "disabled" : ""} title="清除全部排队消息，不影响正在执行的消息">全部清除</button>` : ""}</div></div>
      ${cfg.items.map((item, idx) => {
        const actionId = workspaceQueueActionId(item);
        const message = workspaceQueueItemMessage(item);
        const contentHtml = workspaceQueueItemContentHtml(item, options);
        const disabled = !actionId || cfg.actionInFlight;
        return window.PortalI18n.html`<div class="workspaceQueueItem">
          <div class="workspaceQueueText" title="${escapeHtml(message)}"><span class="workspaceQueueMeta">#${idx + 1} ${escapeHtml(workspaceQueueItemLabel(item, options))}</span><div class="workspaceQueueContent">${contentHtml}</div></div>
          ${canInject ?window.PortalI18n.html`<button class="portalButton is-compact workspaceQueueBtn" type="button" data-workspace-queue-inject="${escapeHtml(actionId)}" ${disabled ? "disabled" : ""} title="立即移出队列；当前执行可接收时注入，否则下一轮处理">立即注入</button>` : ""}
          <button class="portalButton is-compact workspaceQueueBtn" type="button" data-workspace-queue-delete="${escapeHtml(actionId)}" ${disabled ? "disabled" : ""} title="${escapeHtml(cfg.delete_title || window.PortalI18n.t("删除这条排队消息"))}">${escapeHtml(cfg.delete_label || window.PortalI18n.t("删除"))}</button>
        </div>`;
      }).join("")}
    </div>`;
    }

    // The composer's input history, newest first: this person's
    // inputs still queued for a turn and the ones just sent that the server
    // does not list yet, then what the server lists of everything they typed in
    // the conversation, a page at a time (``loadPage``).
    function createInputHistory(options = {}) {
      const cursor = { scope: "", index: -1, draft: "", draftMeta: null };
      let loadingOlder = null;
      const scopes = new Map();
      const scopeState = scope => {
        const key = String(scope || "");
        if (!scopes.has(key)) {
          scopes.set(key, { sent: [], queued: [], listed: [], nextPage: "", loaded: false, stale: false, loading: null });
        }
        return scopes.get(key);
      };
      const normalizeItem = item => {
        const raw = item && typeof item === "object" ? item : null;
        const text = String(raw ? raw.text ?? raw.content ?? "" : item || "").trim();
        if (!text) return null;
        const identity = String(raw?.id || raw?.message_id || "").trim();
        const meta = raw?.meta && typeof raw.meta === "object" ? raw.meta : null;
        return { key: identity ? `message:${identity}` : `text:${text}`, text, meta };
      };
      const normalizeItems = items => (Array.isArray(items) ? items : []).map(normalizeItem).filter(Boolean);
      // A sent input leaves once the queue or the server lists it.
      const dropListedSent = state => {
        const listed = new Set([...state.queued, ...state.listed].map(item => item.key));
        state.sent = state.sent.filter(item => !listed.has(item.key));
      };
      const readEntries = (scope = cursor.scope) => {
        const state = scopeState(scope);
        const seen = new Set();
        return [...state.sent, ...state.queued, ...state.listed].filter(item => !seen.has(item.key) && seen.add(item.key));
      };
      // The next page of the server's list, or its first page again when ``fresh``.
      const load = (scope, { fresh = false } = {}) => {
        const state = scopeState(scope);
        if (!options.loadPage) return Promise.resolve(false);
        if (state.loading) return state.loading;
        if (!fresh && state.loaded && !state.nextPage) return Promise.resolve(false);
        const pageToken = fresh || !state.loaded ? "" : state.nextPage;
        const loading = Promise.resolve()
          .then(() => options.loadPage(scope, pageToken))
          .then(page => {
            if (!page) return false;
            const items = normalizeItems(page.items);
            state.listed = pageToken ? [...state.listed, ...items] : items;
            state.nextPage = String(page.next_page_token || "");
            state.loaded = true;
            if (!pageToken) state.stale = false;
            dropListedSent(state);
            return items.length > 0;
          })
          .catch(error => {
            options.onError?.(error);
            return false;
          })
          .finally(() => {
            if (state.loading === loading) state.loading = null;
          });
        state.loading = loading;
        return loading;
      };
      // The first page, once a scope is shown.
      const prepare = scope => {
        const state = scopeState(scope);
        return state.loaded || state.loading ? Promise.resolve(false) : load(scope);
      };
      // The first page again: the person typed something new (here or elsewhere).
      const refresh = scope => {
        const state = scopeState(scope);
        state.stale = true;
        const run = () => (state.stale ? load(scope, { fresh: true }) : false);
        return state.loading ? state.loading.then(run) : run();
      };
      const setQueuedItems = (scope, items) => {
        const state = scopeState(scope);
        const queued = normalizeItems(items);
        const queuedKeys = new Set(queued.map(item => item.key));
        // Undelivered inputs can leave the queue without entering the server's
        // trace (withdrawal). Keep their full drafts in this page's history too.
        const removed = state.queued.filter(item => !queuedKeys.has(item.key));
        state.sent = [...removed, ...state.sent];
        state.queued = queued;
        dropListedSent(state);
      };
      const reset = (scope = cursor.scope) => {
        cursor.scope = scope || "";
        cursor.index = -1;
        cursor.draft = "";
        cursor.draftMeta = null;
      };
      const remember = (scope, value) => {
        const entry = normalizeItem(value);
        if (!entry) return;
        const state = scopeState(scope);
        state.sent = [entry, ...state.sent.filter(item => item.key !== entry.key)];
        reset(scope);
      };
      const handleKey = (event, input, scope = cursor.scope) => {
        if (
          isComposerImeActive(input, event)
          || event.altKey
          || event.ctrlKey
          || event.metaKey
          || event.shiftKey
        ) return false;
        if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return false;
        if (cursor.scope !== scope) reset(scope);
        const history = readEntries(scope);
        // With nothing listed yet, Up reads a page from the server like at the end of the list.
        if (!history.length && (event.key !== "ArrowUp" || !options.loadPage)) return false;
        const currentText = String(options.serializeDraft(input) || "").trim();
        // Start over when the input changed or its entry left the history (a queued input ran).
        if (cursor.index >= 0 && (!history[cursor.index] || String(history[cursor.index].text || "").trim() !== currentText)) {
          cursor.index = -1;
          cursor.draft = "";
          cursor.draftMeta = null;
        }
        const browsingHistory = cursor.index >= 0;
        const position = inputCaretLineState(input);
        if (!position.selectable) return false;
        if (!browsingHistory && event.key === "ArrowUp" && !position.atFirstLine) return false;
        if (!browsingHistory && event.key === "ArrowDown" && !position.atLastLine) return false;
        if (event.key === "ArrowDown" && !browsingHistory) return false;
        if (cursor.index < 0) {
          cursor.draft = String(options.serializeDraft(input) || "");
          cursor.draftMeta = options.serializeDraftMeta(input);
          if (event.key === "ArrowUp") {
            const currentHistoryIndex = history.findIndex(item => String(item?.text || "").trim() === currentText);
            if (currentHistoryIndex >= 0) cursor.index = currentHistoryIndex;
          }
        }
        const nextIndex = event.key === "ArrowUp"
          ? Math.min(cursor.index + 1, history.length - 1)
          : Math.max(cursor.index - 1, -1);
        event.preventDefault();
        if (nextIndex === cursor.index) {
          if (event.key === "ArrowUp" && options.loadPage && !loadingOlder) {
            const expectedText = currentText;
            loadingOlder = load(scope).then(() => {
              if (cursor.scope !== scope || !input.isConnected
                || readEntries(scope).length <= history.length
                || String(options.serializeDraft(input) || "").trim() !== expectedText) return;
              const next = readEntries(scope)[cursor.index + 1];
              if (!next) return;
              cursor.index += 1;
              options.restoreDraft(input, next.text, next.meta);
              input.dispatchEvent(new CustomEvent("input", {
                bubbles: true, detail: { source: "history" },
              }));
            }).catch(error => options.onError?.(error)).finally(() => { loadingOlder = null; });
          }
          return true;
        }
        cursor.index = nextIndex;
        const nextValue = cursor.index >= 0 ? history[cursor.index].text : cursor.draft;
        const nextMeta = cursor.index >= 0 ? history[cursor.index].meta : cursor.draftMeta;
        options.restoreDraft(input, nextValue, nextMeta);
        return true;
      };
      return { remember, reset, handleKey, prepare, refresh, setQueuedItems };
    }

    function persistedDraftAttachments(items) {
      return normalizeComposerAttachments(items).map(item => ({
        id: String(item.id || item.file_id || item.path || ""),
        upload_id: String(item.upload_id || ""),
        file_id: String(item.file_id || ""),
        kind: attachmentKind(item),
        name: String(item.name || item.filename || "attachment"),
        path: String(item.path || ""),
        preview_url: String(item.preview_url || "").startsWith("blob:") ? "" : String(item.preview_url || ""),
        uploading: false,
        error: String(item.error || (item.uploading || !(item.upload_id || item.file_id || item.path)
          ? "附件上传未完成或已中断，请移除后重新选择文件。"
          : "")),
        mime: String(item.mime || item.content_type || ""),
        content_type: String(item.content_type || item.mime || ""),
        size: Number(item.size || item.size_bytes || 0),
        size_bytes: Number(item.size_bytes || item.size || 0),
        reference_text: String(item.reference_text || ""),
        preview_selection: item.preview_selection && typeof item.preview_selection === "object"
          ? { ...item.preview_selection }
          : null,
        original: item.original && typeof item.original === "object" ? { ...item.original } : null,
      }));
    }

    function normalizeWorkspaceDraft(value) {
      const draft = value && typeof value === "object" && !Array.isArray(value) ? value : {};
      const text = String(draft.text || "");
      const attachments = persistedDraftAttachments(draft.attachments);
      const attachmentIds = new Set(attachments.flatMap(item => [item.id, item.file_id, item.path, item.preview_url]).filter(Boolean));
      COMPOSER_DRAFT_ATTACHMENT_RE.lastIndex = 0;
      for (const match of text.matchAll(COMPOSER_DRAFT_ATTACHMENT_RE)) {
        const id = match[2];
        if (attachmentIds.has(id)) continue;
        attachments.push(...persistedDraftAttachments([{
          id,
          kind: match[1],
          name: match[1] === "image" ? window.PortalI18n.t("未恢复的图片") : window.PortalI18n.t("未恢复的文件"),
          error: "附件信息已丢失，请移除后重新选择文件。",
        }]));
        attachmentIds.add(id);
      }
      return {
        text,
        attachments,
        refs: refsPayload(draft.refs),
        selection: draft.selection && typeof draft.selection === "object" ? draft.selection : null,
        input_height: Math.max(0, Number(draft.input_height || 0)),
        submitted_message_id: String(draft.submitted_message_id || ""),
        updated_at: String(draft.updated_at || ""),
      };
    }

    function workspaceDraftHasContent(draft) {
      const value = normalizeWorkspaceDraft(draft);
      return !!(
        value.text
        || value.attachments.length
        || value.refs.skills.length
        || value.refs.agents.length
      );
    }

    // Compositions of the same Chat share a recovery draft. Coordinate pending
    // edits by the full storage key so timer/unmount order cannot pick the winner.
    const pendingWorkspaceDrafts = new Map();

    function createDraftStore(options = {}) {
      const namespace = String(options.namespace || "workspace").trim() || "workspace";
      const owner = () => {
        const value = typeof options.owner === "function" ? options.owner() : options.owner;
        return String(value || "anonymous").trim() || "anonymous";
      };
      const storageKey = scope => {
        if (typeof options.storageKey === "function") return String(options.storageKey(scope, owner()) || "");
        return ["portal.chatDraft.v1", owner(), namespace, String(scope || "default")]
          .map(part => encodeURIComponent(part))
          .join(":");
      };
      const pending = new Map();
      let timer = null;
      const armTimer = () => {
        if (timer || !pending.size) return;
        timer = setTimeout(() => flush(), Math.max(0, Number(options.delay || 180)));
      };
      const read = scope => {
        const key = storageKey(scope);
        if (!key) return normalizeWorkspaceDraft({});
        if (pendingWorkspaceDrafts.has(key)) return normalizeWorkspaceDraft(pendingWorkspaceDrafts.get(key).value);
        return normalizeWorkspaceDraft(window.PortalState.storage.readJson(key, {}));
      };
      const persist = (key, value) => {
        if (workspaceDraftHasContent(value)) window.PortalState.storage.writeJson(key, value);
        else window.PortalState.storage.remove(key);
      };
      const write = (scope, draft) => {
        const pendingKey = String(scope || "");
        const value = normalizeWorkspaceDraft({ ...draft, updated_at: new Date().toISOString() });
        pending.delete(pendingKey);
        const key = storageKey(pendingKey);
        if (!key) return;
        pendingWorkspaceDrafts.delete(key);
        persist(key, value);
        if (!pending.size && timer) {
          clearTimeout(timer);
          timer = null;
        }
      };
      const flush = scope => {
        const entries = scope === undefined
          ? [...pending.entries()]
          : [[String(scope || ""), pending.get(String(scope || ""))]];
        for (const [key, entry] of entries) {
          if (!entry) continue;
          pending.delete(key);
          if (pendingWorkspaceDrafts.get(entry.storageKey) !== entry) continue;
          pendingWorkspaceDrafts.delete(entry.storageKey);
          persist(entry.storageKey, entry.value);
        }
        if (!pending.size && timer) {
          clearTimeout(timer);
          timer = null;
        }
      };
      const schedule = (scope, draft) => {
        const key = String(scope || "");
        if (!key) return;
        const entry = {
          storageKey: storageKey(key),
          value: normalizeWorkspaceDraft({ ...draft, updated_at: new Date().toISOString() }),
        };
        if (!entry.storageKey) return;
        pending.set(key, entry);
        pendingWorkspaceDrafts.set(entry.storageKey, entry);
        if (timer) clearTimeout(timer);
        timer = null;
        armTimer();
      };
      const clear = scope => {
        const key = String(scope || "");
        pending.delete(key);
        const storage = storageKey(key);
        if (!storage) return;
        pendingWorkspaceDrafts.delete(storage);
        window.PortalState.storage.remove(storage);
      };
      return {
        read,
        write,
        schedule,
        flush,
        clear,
        storageKey,
      };
    }

    return Object.freeze({
      workspaceQueueActionId, normalizeWorkspaceQueue, workspaceQueueHtml, bindWorkspaceQueue,
      createInputHistory, persistedDraftAttachments, normalizeWorkspaceDraft,
      workspaceDraftHasContent, createDraftStore,
    });
  }

  window.PortalChatWorkspaceInputs = Object.freeze({
    createInputs, dependencies: DEPENDENCIES, workspaceQueueActionId, normalizeWorkspaceQueue,
    bindWorkspaceQueue,
  });
})();
