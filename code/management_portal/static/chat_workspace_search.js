(() => {
  if (window.PortalChatWorkspaceSearch) return;

  // Searching a conversation: the search owns only its query and results; the
  // messages and moving to a result stay with the Workspace and its history
  // loader.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml", "messageRenderKey", "messageRole", "messageTitle",
  ]);

  function createSearch(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceSearch is missing: ${missing.join(", ")}`);
    const {
      escapeHtml, messageRenderKey, messageRole, messageTitle,
    } = dependencies;

    // The search owns only its query/results. Conversation messages and navigation
    // remain owned by the Workspace and its existing history loader.
    function createConversationSearch({ root, scope, messages, rendering, search, loadOlder, canLoadOlder, reveal }) {
      const trigger = root.querySelector("[data-workspace-history-search]");
      const panel = document.createElement("section");
      panel.className = "portalConversationSearch";
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-label", window.PortalI18n.t("查找当前对话"));
      panel.innerHTML = window.PortalI18n.html`<div data-portal-search-field data-portal-search-density="compact"><label>查找当前对话</label><input data-portal-search-input type="search" aria-label="搜索当前对话" placeholder="搜索当前对话…" autocomplete="off" /></div>
      <div class="paneNote conversationSearchStatus" role="status"></div>
      <div class="conversationSearchResults" aria-label="对话片段"></div>
      <button class="portalButton is-quiet is-compact conversationSearchMore" type="button" hidden>加载更多</button>`;
      root.appendChild(panel);
      const input = panel.querySelector("input");
      const status = panel.querySelector('[role="status"]');
      const list = panel.querySelector(".conversationSearchResults");
      const more = panel.querySelector(".conversationSearchMore");
      let revision = 0, timer = 0, currentScope = scope(), query = "", rows = [], nextPage = "", busy = false, error = "";
      let disposed = false;
      const binding = window.PortalOverlay.bindPopover({
        trigger, panel, portal: true, placement: "bottom-end", gap: 6,
        onOpen: () => { refresh(); input.focus({ preventScroll: true }); },
        onClose: () => { revision += 1; clearTimeout(timer); busy = false; },
      });
      function snippet(text) {
        const value = String(text || "").replace(/\s+/g, " ").trim();
        const needle = query.toLocaleLowerCase();
        const index = needle ? value.toLocaleLowerCase().indexOf(needle) : 0;
        const start = Math.max(0, index - 45);
        const excerpt = value.slice(start, start + Math.max(210, query.length + 70));
        if (!needle) return escapeHtml(excerpt);
        let html = start ? "…" : "", offset = 0;
        const lower = excerpt.toLocaleLowerCase();
        for (let at = lower.indexOf(needle); at >= 0; at = lower.indexOf(needle, offset)) {
          html += escapeHtml(excerpt.slice(offset, at)) + window.PortalI18n.html`<mark>${escapeHtml(excerpt.slice(at, at + query.length))}</mark>`;
          offset = at + query.length;
        }
        return html + escapeHtml(excerpt.slice(offset));
      }
      function render() {
        const opts = rendering();
        const scrollTop = list.scrollTop;
        list.innerHTML = rows.map((item, index) => window.PortalI18n.html`<button type="button" class="conversationSearchResult" data-search-message="${escapeHtml(messageRenderKey(item))}" data-portal-tooltip="${escapeHtml(String(item.content || "").trim())}">
        <span class="conversationSearchMeta"><span>${escapeHtml(messageTitle(item, index, opts))}</span><time>${escapeHtml(opts.formatTime?.(item.created_at) || item.created_at || "")}</time></span>
        <span class="conversationSearchExcerpt">${snippet(item.content)}</span></button>`).join("");
        list.scrollTop = scrollTop;
        status.textContent = error || (busy ? window.PortalI18n.t("正在查找…") : rows.length ? `${query ? window.PortalI18n.t("匹配") : window.PortalI18n.t("对话片段")} · ${rows.length}` : query ? window.PortalI18n.t("没有匹配的片段") : window.PortalI18n.t("暂无对话片段"));
        more.hidden = !(error || (query && search ? nextPage : canLoadOlder()));
        more.disabled = busy;
        more.textContent = error ? window.PortalI18n.t("重试") : query ? window.PortalI18n.t("加载更多结果") : window.PortalI18n.t("加载更早片段");
      }
      async function refresh(append = false, preserveScroll = false) {
        const request = ++revision;
        clearTimeout(timer);
        error = "";
        if (!append) { nextPage = ""; rows = []; if (!preserveScroll) list.scrollTop = 0; }
        if (!query || !search) {
          const opts = rendering();
          rows = messages().filter((item, index, items) => String(item.content || "").trim()
            && (query ? String(item.content).toLocaleLowerCase().includes(query.toLocaleLowerCase())
              : messageRole(item) === "human"
                && !String(opts.messageClass?.(item, index, items) || "").split(/\s+/).includes("messageIncoming"))).slice().reverse();
          busy = false; render(); return;
        }
        busy = true; render();
        try {
          const result = await search(query, { pageToken: append ? nextPage : "", limit: 50 });
          if (disposed || request !== revision || currentScope !== scope()) return;
          const seen = new Set(rows.map(messageRenderKey));
          rows = [...rows, ...(result.messages || []).filter(item => !seen.has(messageRenderKey(item)))];
          nextPage = result.next_page_token || "";
        } catch (err) {
          if (disposed || request !== revision) return;
          error = `查找失败：${err.message || err}`;
        } finally {
          if (!disposed && request === revision) { busy = false; render(); binding.reposition(); }
        }
      }
      trigger.addEventListener("click", () => binding.toggle());
      input.addEventListener("input", () => {
        revision += 1;
        clearTimeout(timer);
        query = input.value.trim(); rows = []; nextPage = ""; error = "";
        busy = !!query && !!search; render();
        timer = window.setTimeout(() => refresh(), 220);
      });
      panel.addEventListener("keydown", event => {
        const buttons = [...list.querySelectorAll("button")];
        const index = buttons.indexOf(document.activeElement);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          buttons[Math.max(0, Math.min(buttons.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
        } else if (event.key === "Enter" && event.target === input && !busy) {
          event.preventDefault(); buttons[0]?.click();
        }
      });
      list.addEventListener("click", async event => {
        const button = event.target.closest("[data-search-message]");
        if (!button) return;
        binding.close();
        try { await reveal(button.dataset.searchMessage); }
        catch (err) { window.PortalUI.toast(window.PortalI18n.text`定位失败：${err.message || err}`, "error"); }
      });
      more.addEventListener("click", async () => {
        if (busy) return;
        if (query && search) { void refresh(!error); return; }
        const request = ++revision;
        busy = true; error = ""; render();
        try { await loadOlder(); }
        catch (err) { if (request === revision) error = `加载失败：${err.message || err}`; }
        if (disposed || request !== revision || currentScope !== scope()) return;
        busy = false;
        if (error) render(); else refresh();
      });
      return {
        update(messagesChanged) {
          if (currentScope !== scope()) {
            binding.close(); revision += 1; clearTimeout(timer);
            currentScope = scope(); input.value = query = ""; rows = []; nextPage = "";
            busy = false; error = ""; render();
          }
          if (messagesChanged && binding.isOpen() && !query && !busy) refresh(false, true);
        },
        close() { binding.close(); },
        destroy() { disposed = true; revision += 1; clearTimeout(timer); binding.destroy(); panel.remove(); },
      };
    }

    return Object.freeze({
      createConversationSearch,
    });
  }

  window.PortalChatWorkspaceSearch = Object.freeze({
    createSearch, dependencies: DEPENDENCIES,
  });
})();
