(function () {
  "use strict";

  // The Mailbox tab of the node dialog (decision_b5d349190743): what waits for a
  // Resident, the round in progress and the rounds that ended recently. Every
  // change goes through the same Resident queue instructions as the Chat queue;
  // the tab re-reads GetMailbox when the Resident's Conversation changes, and
  // keeps selection and scroll position across those reads. Each item opens a
  // card with its full text; the card can switch to the whole prompt the agent
  // was sent, so what Portal adds around the message is visible.
  if (window.PortalMailboxPanel) return;

  const REFRESH_DELAY_MS = 250;
  // PreviewPromptInjection marks where the message goes in the user-position prompt.
  const PREVIEW_MESSAGE_SLOT = "（本轮消息）";
  const TIMING_LABELS = Object.freeze({
    session_start_and_after_compaction: window.PortalI18n.t("会话开始和压缩后发送"),
    session_start: window.PortalI18n.t("会话开始时发送"),
    every_turn: window.PortalI18n.t("每轮发送"),
  });
  let cardSequence = 0;
  const KIND_LABELS = Object.freeze({ request: window.PortalI18n.t("请求"), group: window.PortalI18n.t("群体"), info: "info" });
  const STATE_LABELS = Object.freeze({ completed: window.PortalI18n.t("完成"), cancelled: window.PortalI18n.t("已停止"), failed: window.PortalI18n.t("失败"), running: window.PortalI18n.t("处理中") });

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
    })[character]);
  }

  function formatTime(value) {
    const date = new Date(String(value || ""));
    if (!value || Number.isNaN(date.getTime())) return "";
    return date.toLocaleString(window.PortalI18n.locale(), {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    });
  }

  function authorHtml(item) {
    const id = String(item?.origin?.sender?.id || item?.author_id || "").trim();
    if (id === "mailbox") return "Mailbox";
    const chips = window.PortalResourceChips;
    if (chips?.resourceLabelHtml && /^resident_[0-9a-f]{12}$/.test(id)) return chips.resourceLabelHtml(id);
    return escapeHtml(id || "未知发送者");
  }

  function timeHtml(value) {
    const label = formatTime(value);
    return label ? window.PortalI18n.html`<time class="mailboxTime" datetime="${escapeHtml(value)}">${escapeHtml(label)}</time>` : "";
  }

  function kindLabel(item) {
    if (item?.kind === "group") return window.PortalI18n.text`群体 · ${(item.members || []).length} 条`;
    return KIND_LABELS[item?.kind] || String(item?.kind || "");
  }

  function messageText(message) {
    return (Array.isArray(message?.content_parts) ? message.content_parts : [])
      .filter(part => part?.type === "text")
      .map(part => String(part.text || ""))
      .join("\n")
      .trim();
  }

  function attachmentsHtml(message) {
    const parts = (Array.isArray(message?.content_parts) ? message.content_parts : [])
      .filter(part => part && (part.type === "image" || part.type === "file"));
    if (!parts.length) return "";
    return window.PortalI18n.html`<ul class="mailboxAttachments">${parts.map(part => {
      const name = String(part.name || part.title || part.filename || part.path || part.source || part.type);
      return window.PortalI18n.html`<li>${part.type === "image" ? window.PortalI18n.t("图片") : window.PortalI18n.t("文件")}：${escapeHtml(name)}</li>`;
    }).join("")}</ul>`;
  }

  // Marks each original message text inside a prompt; everything else is what Portal added.
  function highlightedPrompt(content, texts) {
    const ranges = [];
    for (const text of texts) {
      const index = text ? content.indexOf(text) : -1;
      if (index >= 0) ranges.push([index, index + text.length]);
    }
    ranges.sort((left, right) => left[0] - right[0]);
    let markup = "";
    let cursor = 0;
    let found = 0;
    for (const [start, end] of ranges) {
      if (start < cursor) continue;
      markup += `${escapeHtml(content.slice(cursor, start))}<mark class="mailboxPromptOriginal">${escapeHtml(content.slice(start, end))}</mark>`;
      cursor = end;
      found += 1;
    }
    return { markup: markup + escapeHtml(content.slice(cursor)), found };
  }

  function mount({ container, residentId, toast = () => {} } = {}) {
    const protocol = window.PortalResidentProtocol;
    if (!container || !residentId || !protocol?.loadMailbox) return null;
    const state = {
      view: null,
      error: "",
      loading: false,
      reloadAgain: false,
      busy: false,
      destroyed: false,
      expanded: new Set(),
      selected: new Set(),
      details: new Map(),
      dragId: "",
      renderAfterDrag: false,
      card: null,
      showPrompt: false,
      systemOpen: false,
      prompts: new Map(),
      preview: null,
    };
    const root = document.createElement("div");
    root.className = "mailboxPanel";
    root.dataset.mailboxState = "loading";
    root.setAttribute("aria-busy", "true");
    container.replaceChildren(root);
    let timer = 0;

    function pendingItems() {
      return Array.isArray(state.view?.pending) ? state.view.pending : [];
    }

    // An item and the round that delivered it: none while it waits, the round itself, or the round an info rode with.
    function locate(id) {
      const view = state.view || {};
      const waiting = pendingItems().find(item => item.message_id === id);
      if (waiting) return { item: waiting, round: null, status: "pending" };
      const rounds = [...(view.active ? [{ ...view.active, state: "running" }] : []), ...(view.recent || [])];
      for (const round of rounds) {
        if (round.message_id === id) return { item: round, round, status: String(round.state || "") };
        const info = (round.infos || []).find(entry => entry.message_id === id);
        if (info) return { item: info, round, status: String(round.state || "") };
      }
      return null;
    }

    function movableIds() {
      return pendingItems().filter(item => item.kind !== "info").map(item => item.message_id);
    }

    function forgetMissing() {
      const present = new Set(pendingItems().map(item => item.message_id));
      for (const id of [...state.selected]) if (!present.has(id) || !movableIds().includes(id)) state.selected.delete(id);
    }

    function focusKey() {
      const active = document.activeElement;
      if (!active || !root.contains(active)) return null;
      return {
        item: active.closest("[data-mailbox-item]")?.dataset.mailboxItem || "",
        action: active.dataset.mailboxAction || (active.matches("[data-mailbox-select]") ? "select" : ""),
        row: active.matches("[data-mailbox-item]"),
      };
    }

    function restoreFocus(key) {
      if (!key) return;
      const item = key.item ? root.querySelector(`[data-mailbox-item="${CSS.escape(key.item)}"]`) : root;
      if (!item) return;
      const target = key.row ? item
        : key.action === "select" ? item.querySelector("[data-mailbox-select]")
          : key.action ? item.querySelector(`[data-mailbox-action="${CSS.escape(key.action)}"]`) : null;
      target?.focus?.({ preventScroll: true });
    }

    function detailHtml(item) {
      const detail = state.details.get(item.message_id);
      if (!detail || detail.status === "loading") return window.PortalI18n.html`<div class="mailboxDetail" role="status">正在读取全文…</div>`;
      if (detail.status === "error") return window.PortalI18n.html`<div class="mailboxDetail mailboxError" role="alert">全文读取失败：${escapeHtml(detail.error)}</div>`;
      const group = item.kind === "group";
      return window.PortalI18n.html`<div class="mailboxDetail">${detail.messages.map(message => window.PortalI18n.html`
        <div class="mailboxDetailMessage" data-mailbox-detail="${escapeHtml(message.message_id || "")}">
          ${group ? window.PortalI18n.html`<div class="mailboxDetailMeta"><span class="mailboxAuthor">${authorHtml(message)}</span>${timeHtml(message.created_at)}</div>` : ""}
          <div class="mailboxDetailText">${escapeHtml(messageText(message) || window.PortalI18n.t("（没有文字）"))}</div>
          ${attachmentsHtml(message)}
        </div>`).join("")}</div>`;
    }

    function messagePreviewHtml(item) {
      const label = window.PortalI18n.t("查看全文");
      return window.PortalI18n.html`<button class="mailboxRead" type="button" data-mailbox-action="open" aria-haspopup="dialog" aria-label="${label}"><span class="mailboxPreview">${escapeHtml(item.preview || window.PortalI18n.t("（没有文字）"))}${item.attachments ? window.PortalI18n.html` <span class="mailboxAttachCount">附件 ${Number(item.attachments)}</span>` : ""}</span><span class="mailboxReadLabel">${label}</span></button>`;
    }

    function pendingHtml(item, organize) {
      const id = item.message_id;
      const movable = organize && item.kind !== "info";
      const actions = organize ? window.PortalI18n.html`<span class="mailboxItemActions">
          ${item.kind === "group" ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-mailbox-action="split" ${state.busy ? "disabled" : ""}>拆分</button>` : ""}
          <button class="portalButton is-compact" type="button" data-mailbox-action="withdraw" ${state.busy ? "disabled" : ""}>撤回</button>
        </span>` : "";
      return window.PortalI18n.html`<li class="mailboxItem" data-mailbox-item="${escapeHtml(id)}" data-kind="${escapeHtml(item.kind)}"${movable
        ? window.PortalI18n.text` draggable="true" tabindex="0" aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown" aria-label="${escapeHtml(`${kindLabel(item)}：${item.preview || ""}`)}，可拖动或用 Alt+方向键调整顺序"`
        : ""}>
        <div class="mailboxRow">
          <span class="mailboxGrip" aria-hidden="true">${movable ? "⠿" : ""}</span>
          ${movable ? window.PortalI18n.html`<input type="checkbox" data-mailbox-select aria-label="选择这一项用于合并" ${state.selected.has(id) ? "checked" : ""} ${state.busy ? "disabled" : ""}>` : ""}
          <span class="mailboxKind" data-kind="${escapeHtml(item.kind)}">${escapeHtml(kindLabel(item))}</span>
          <span class="mailboxAuthor">${authorHtml(item)}</span>
          ${timeHtml(item.created_at)}
          ${actions}
        </div>
        ${messagePreviewHtml(item)}
      </li>`;
    }

    function infosHtml(item) {
      const infos = Array.isArray(item.infos) ? item.infos : [];
      if (!infos.length) return "";
      const key = `infos:${item.message_id}`;
      const open = state.expanded.has(key);
      return window.PortalI18n.html`<div class="mailboxInfos">
        <button class="mailboxInfosToggle" type="button" data-mailbox-action="infos" aria-expanded="${open}">随行 info ${infos.length} 条</button>
        ${open ? window.PortalI18n.html`<ul class="mailboxInfoList">${infos.map(info => window.PortalI18n.html`<li data-mailbox-info="${escapeHtml(info.message_id)}" data-mailbox-item="${escapeHtml(info.message_id)}">
          <span class="mailboxAuthor">${authorHtml(info)}</span>${timeHtml(info.created_at)}
          ${messagePreviewHtml(info)}</li>`).join("")}</ul>` : ""}
      </div>`;
    }

    function roundHtml(item, { active = false, organize = false } = {}) {
      const status = active ? "running" : String(item.state || "");
      const when = active ? (item.started_at || item.created_at) : (item.finished_at || item.created_at);
      const requeue = !active && organize && item.can_requeue
        ? window.PortalI18n.html`<span class="mailboxItemActions"><button class="portalButton is-compact" type="button" data-mailbox-action="requeue" ${state.busy ? "disabled" : ""}>放回队列</button></span>`
        : "";
      return window.PortalI18n.html`<li class="mailboxRound" data-mailbox-item="${escapeHtml(item.message_id)}" data-state="${escapeHtml(status)}">
        <div class="mailboxRow">
          <span class="mailboxState" data-state="${escapeHtml(status)}">${escapeHtml(STATE_LABELS[status] || status)}</span>
          <span class="mailboxKind" data-kind="${escapeHtml(item.kind)}">${escapeHtml(kindLabel(item))}</span>
          <span class="mailboxAuthor">${authorHtml(item)}</span>
          ${timeHtml(when)}
          ${requeue}
        </div>
        ${messagePreviewHtml(item)}
        ${item.error && status === "failed" ? window.PortalI18n.html`<div class="mailboxError">${escapeHtml(item.error)}</div>` : ""}
        ${infosHtml(item)}
      </li>`;
    }

    function viewHtml(view) {
      const pending = pendingItems();
      const recent = Array.isArray(view.recent) ? view.recent : [];
      const organize = Boolean(view.can_organize);
      const movable = movableIds();
      const selected = movable.filter(id => state.selected.has(id));
      const allSelected = movable.length > 0 && selected.length === movable.length;
      const status = state.error ? window.PortalI18n.text`刷新失败：${state.error}` : window.PortalI18n.text`${pending.length} 项等待`;
      return window.PortalI18n.html`
        <div class="mailboxToolbar">
          <span class="mailboxStatus" role="status">${escapeHtml(status)}</span>
          ${organize && movable.length >= 2 ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-mailbox-action="select-all" aria-pressed="${allSelected}" ${state.busy ? "disabled" : ""}>${escapeHtml(allSelected ? window.PortalI18n.t("取消全选") : window.PortalI18n.t("全选"))}</button>` : ""}
          ${organize ? window.PortalI18n.html`<button class="portalButton is-compact" type="button" data-mailbox-action="merge" ${selected.length >= 2 && !state.busy ? "" : "disabled"}>合并所选${selected.length ? `（${selected.length}）` : ""}</button>` : ""}
          <button class="portalButton is-compact" type="button" data-mailbox-action="refresh">刷新</button>
        </div>
        <section class="mailboxSection" data-mailbox-section="pending" aria-labelledby="mailboxPendingTitle">
          <h3 id="mailboxPendingTitle">待处理 <span class="mailboxCount">${pending.length}</span></h3>
          ${pending.length
            ? window.PortalI18n.html`<ol class="mailboxList" data-mailbox-list="pending">${pending.map(item => pendingHtml(item, organize)).join("")}</ol>`
            : window.PortalI18n.html`<p class="mailboxEmpty">没有等待中的消息。</p>`}
          ${organize && movableIds().length > 1 ? window.PortalI18n.html`<p class="mailboxHint">拖动请求或群体调整处理顺序；info 随下一条请求送达，不参与调序与合并。</p>` : ""}
        </section>
        <section class="mailboxSection" data-mailbox-section="active" aria-labelledby="mailboxActiveTitle">
          <h3 id="mailboxActiveTitle">处理中</h3>
          ${view.active
            ? window.PortalI18n.html`<ol class="mailboxList" data-mailbox-list="active">${roundHtml(view.active, { active: true })}</ol>`
            : window.PortalI18n.html`<p class="mailboxEmpty">当前没有正在处理的一轮。</p>`}
        </section>
        <section class="mailboxSection" data-mailbox-section="recent" aria-labelledby="mailboxRecentTitle">
          <h3 id="mailboxRecentTitle">最近已送达 <span class="mailboxCount">${recent.length}</span></h3>
          ${recent.length
            ? window.PortalI18n.html`<ol class="mailboxList" data-mailbox-list="recent">${recent.map(item => roundHtml(item, { organize })).join("")}</ol>`
            : window.PortalI18n.html`<p class="mailboxEmpty">还没有结束的轮次。</p>`}
          ${organize && recent.some(item => item.can_requeue) ? window.PortalI18n.html`<p class="mailboxHint">停止或失败的一轮可以放回队列：原消息与随行 info 以原发送者回到等待，停止或失败的记录留在对话中。</p>` : ""}
        </section>`;
    }

    function render() {
      if (state.destroyed) return;
      if (state.dragId) {
        // Re-rendering would drop the row being dragged; show the update after the drop.
        state.renderAfterDrag = true;
        return;
      }
      const scroller = root.closest("[data-portal-detail-body]") || container;
      const scrollTop = scroller.scrollTop;
      const focus = focusKey();
      root.dataset.mailboxState = state.view ? "ready" : (state.error ? "error" : "loading");
      root.setAttribute("aria-busy", String(state.loading || state.busy));
      root.innerHTML = state.view
        ? viewHtml(state.view)
        : window.PortalI18n.html`<p class="mailboxEmpty" role="status">${escapeHtml(state.error ? window.PortalI18n.text`Mailbox 读取失败：${state.error}` : window.PortalI18n.t("正在读取 Mailbox…"))}</p>`;
      window.PortalResourceChips?.bind?.(root);
      scroller.scrollTop = scrollTop;
      restoreFocus(focus);
    }

    async function load() {
      if (state.destroyed) return;
      if (state.loading) {
        state.reloadAgain = true;
        return;
      }
      state.loading = true;
      try {
        const view = await protocol.loadMailbox(residentId);
        if (state.destroyed) return;
        state.view = view || {};
        state.error = "";
        forgetMissing();
      } catch (error) {
        if (state.destroyed) return;
        state.error = error?.message || String(error);
      } finally {
        state.loading = false;
      }
      render();
      if (state.reloadAgain) {
        state.reloadAgain = false;
        schedule();
      }
    }

    function schedule() {
      if (state.destroyed) return;
      clearTimeout(timer);
      timer = setTimeout(load, REFRESH_DELAY_MS);
    }

    async function run(label, call, success = "") {
      if (state.busy || state.destroyed) return;
      state.busy = true;
      render();
      try {
        await call();
        if (success) toast(success);
      } catch (error) {
        toast(window.PortalI18n.text`${label}失败：${error?.message || error}`, "error", 5200);
      } finally {
        state.busy = false;
      }
      await load();
    }

    async function loadDetail(item) {
      const id = item.message_id;
      if (["ready", "loading"].includes(state.details.get(id)?.status)) return;
      state.details.set(id, { status: "loading" });
      try {
        const ids = item.kind === "group" ? (item.members || []).map(member => member.message_id) : [id];
        const messages = await Promise.all(ids.map(messageId => protocol.loadMessage(residentId, messageId)));
        state.details.set(id, { status: "ready", messages });
      } catch (error) {
        state.details.set(id, { status: "error", error: error?.message || String(error) });
      }
      renderCard();
    }

    // GetSentPrompt reads the Provider transcript; a group round may be recorded under its first member.
    async function loadSentPrompt(round) {
      const key = round.message_id;
      // An empty result is read again next time: the round in progress may not be in the transcript yet.
      if (state.prompts.get(key)?.status === "loading" || state.prompts.get(key)?.content) return;
      state.prompts.set(key, { status: "loading" });
      const ids = [...new Set([key, ...(round.members || []).map(member => member.message_id)].filter(Boolean))];
      let prompt = null;
      let failure = null;
      for (const messageId of ids) {
        try {
          prompt = await protocol.loadSentPrompt(residentId, messageId);
          if (prompt?.content) break;
        } catch (error) {
          failure = error;
        }
      }
      state.prompts.set(key, prompt
        ? { status: "ready", content: String(prompt.content || ""), source: String(prompt.source || "") }
        : { status: "error", error: failure?.message || String(failure) });
      renderCard();
    }

    // What Portal adds under the Resident's current Harness settings, for a later turn of the session.
    async function loadPreview() {
      if (["ready", "loading"].includes(state.preview?.status)) return;
      state.preview = { status: "loading" };
      try {
        state.preview = { status: "ready", data: await protocol.previewPromptInjection(residentId, "later") || {} };
      } catch (error) {
        state.preview = { status: "error", error: error?.message || String(error) };
      }
      renderCard();
    }

    function originalTexts(card) {
      const items = card.round ? [card.round, ...(card.round.infos || [])] : [card.item];
      return items.flatMap(item => {
        const detail = state.details.get(item.message_id);
        return detail?.status === "ready" ? detail.messages.map(messageText).filter(Boolean) : [];
      });
    }

    function promptTextHtml(content, texts, kind) {
      const { markup, found } = highlightedPrompt(content, texts);
      return window.PortalI18n.html`<p class="mailboxPromptLegend">${found
        ? window.PortalI18n.html`<span class="mailboxPromptSwatch" aria-hidden="true"></span>高亮部分是消息原文，其余是 Portal 加上的内容。`
        : window.PortalI18n.t("没有在其中定位到消息原文。")}</p>
        <pre class="mailboxPromptText" data-mailbox-prompt="${kind}">${markup}</pre>`;
    }

    function systemPromptHtml(card) {
      const preview = state.preview;
      if (preview?.status === "error") {
        return card.round ? window.PortalI18n.html`<p class="mailboxError" role="alert">系统位置的内容读取失败：${escapeHtml(preview.error)}</p>` : "";
      }
      if (preview?.status !== "ready") return "";
      const parts = (preview.data.parts || []).filter(part => part?.placement === "system" && part.sent !== false && part.content);
      if (!parts.length) return "";
      const content = String(preview.data.system_prompt || parts.map(part => part.content).join("\n\n"));
      const timing = [...new Set(parts.map(part => TIMING_LABELS[part.timing] || String(part.timing || "")))].filter(Boolean).join("、");
      return window.PortalI18n.html`<details class="mailboxPromptSystem" data-mailbox-prompt-system ${state.systemOpen ? "open" : ""}>
        <summary>系统位置的 Portal 内容：${escapeHtml(parts.map(part => part.label || part.id).join("、"))} · ${escapeHtml(timing)} · ${content.length} 字</summary>
        <p class="mailboxPromptNote">按当前设置预览。这部分不随每条消息重复发送，由 Provider 放在系统位置。</p>
        <pre class="mailboxPromptText" data-mailbox-prompt="system">${escapeHtml(content)}</pre>
      </details>`;
    }

    function promptHtml(card) {
      const texts = originalTexts(card);
      let main;
      if (card.round) {
        const entry = state.prompts.get(card.round.message_id);
        if (!entry || entry.status === "loading") {
          main = window.PortalI18n.html`<p class="mailboxPromptNote" role="status">正在读取实际发送的内容…</p>`;
        } else if (entry.status === "error") {
          main = window.PortalI18n.html`<p class="mailboxError" role="alert">实际发送的内容读取失败：${escapeHtml(entry.error)}</p>`;
        } else if (!entry.content) {
          main = window.PortalI18n.html`<p class="mailboxPromptNote" data-mailbox-prompt-source="missing">Provider 会话记录里没有找到这一轮实际发送的内容；会话重置后或较早的轮次可能已经查不到。</p>`;
        } else {
          main = window.PortalI18n.html`<p class="mailboxPromptNote" data-mailbox-prompt-source="sent">${card.item === card.round
            ? window.PortalI18n.t("这一轮实际发送给 agent 的全部内容，取自 Provider 会话记录。")
            : window.PortalI18n.t("这条 info 随一轮请求送达；下面是那一轮实际发送给 agent 的全部内容，取自 Provider 会话记录。")}</p>
            ${promptTextHtml(entry.content, texts, "sent")}`;
        }
      } else {
        const preview = state.preview;
        if (!preview || preview.status === "loading") {
          main = window.PortalI18n.html`<p class="mailboxPromptNote" role="status">正在生成预览…</p>`;
        } else if (preview.status === "error") {
          main = window.PortalI18n.html`<p class="mailboxError" role="alert">预览读取失败：${escapeHtml(preview.error)}</p>`;
        } else {
          const message = texts.join("\n\n");
          const content = String(preview.data.prompt || "").replace(PREVIEW_MESSAGE_SLOT, () => message || PREVIEW_MESSAGE_SLOT);
          main = window.PortalI18n.html`<p class="mailboxPromptNote" data-mailbox-prompt-source="preview">这条消息还没有发送。下面按当前设置预览发送时 Portal 加上的内容；随行 info、附带引用和 Harness 运行提示在发送时才确定，不在预览中。</p>
            ${promptTextHtml(content, texts, "preview")}`;
        }
      }
      return main + systemPromptHtml(card);
    }

    const cardTitleId = `mailboxCardTitle${++cardSequence}`;
    let cardOverlay = null;
    let cardDialog = null;
    let cardSurface = null;

    function ensureCard() {
      if (cardOverlay) return;
      cardOverlay = document.createElement("div");
      cardOverlay.className = "portalDetailOverlay mailboxCardOverlay";
      cardOverlay.hidden = true;
      cardOverlay.dataset.portalDetailOverlay = "";
      cardOverlay.innerHTML = window.PortalI18n.html`<section class="portalDetailSurface mailboxCard" data-portal-detail-surface data-mailbox-card role="dialog" aria-modal="true" aria-labelledby="${cardTitleId}">
        <header class="portalDetailHeader mailboxCardHeader" data-portal-detail-header>
          <div class="mailboxCardHeading"><h2 id="${cardTitleId}">消息全文</h2><div class="mailboxCardMeta" data-mailbox-card-meta></div>
            <button class="portalButton is-compact mailboxPromptToggle" type="button" data-mailbox-prompt-toggle aria-pressed="false">显示实际发送的全部 prompt</button></div>
          <button class="portalDetailClose" data-portal-detail-close data-portal-icon="close" type="button" aria-label="关闭"></button>
        </header>
        <div class="portalDetailBody mailboxCardBody" data-portal-detail-body data-mailbox-card-body></div>
      </section>`;
      document.body.append(cardOverlay);
      cardSurface = window.PortalDetailSurface?.mount?.(cardOverlay.firstElementChild) || null;
      cardDialog = window.PortalUI.createDialogController({
        modal: cardOverlay, openClass: "portalDialogOpen", backdropClose: true, escapeClose: true,
        manageHidden: true, restoreFocus: false,
        initialFocus: () => cardOverlay.querySelector("[data-mailbox-prompt-toggle]"),
        onClose: () => {
          const id = state.card?.id || "";
          state.card = null;
          // The list may have re-rendered while the card was open; return to the item's current button.
          requestAnimationFrame(() => {
            const item = id ? root.querySelector(`[data-mailbox-item="${CSS.escape(id)}"]`) : null;
            item?.querySelector('[data-mailbox-action="open"]')?.focus?.({ preventScroll: true });
          });
        },
      });
      cardOverlay.querySelector("[data-portal-detail-close]").addEventListener("click", () => cardDialog.close());
      cardOverlay.querySelector("[data-mailbox-prompt-toggle]").addEventListener("click", () => {
        state.showPrompt = !state.showPrompt;
        void loadCard();
      });
      cardOverlay.addEventListener("toggle", event => {
        if (event.target.matches?.("[data-mailbox-prompt-system]")) state.systemOpen = event.target.open;
      }, true);
    }

    function renderCard() {
      const card = state.card;
      if (!cardOverlay || !card || state.destroyed) return;
      const toggle = cardOverlay.querySelector("[data-mailbox-prompt-toggle]");
      toggle.setAttribute("aria-pressed", String(state.showPrompt));
      toggle.classList.toggle("is-active", state.showPrompt);
      const status = card.status === "pending" ? window.PortalI18n.t("等待中") : (STATE_LABELS[card.status] || card.status);
      cardOverlay.querySelector("[data-mailbox-card-meta]").innerHTML = window.PortalI18n.html`<span class="mailboxState" data-state="${escapeHtml(card.status)}">${escapeHtml(status)}</span>
        <span class="mailboxKind" data-kind="${escapeHtml(card.item.kind)}">${escapeHtml(kindLabel(card.item))}</span>
        <span class="mailboxAuthor">${authorHtml(card.item)}</span>${timeHtml(card.item.created_at)}`;
      const body = cardOverlay.querySelector("[data-mailbox-card-body]");
      const mode = state.showPrompt ? "prompt" : "text";
      const scrollTop = body.dataset.mailboxCardMode === mode ? body.scrollTop : 0;
      body.dataset.mailboxCardMode = mode;
      body.innerHTML = state.showPrompt ? promptHtml(card) : detailHtml(card.item);
      window.PortalResourceChips?.bind?.(cardOverlay);
      body.scrollTop = scrollTop;
    }

    async function loadCard() {
      const card = state.card;
      if (!card) return;
      renderCard();
      const loads = [loadDetail(card.item)];
      if (state.showPrompt) {
        if (card.round) {
          loads.push(...[card.round, ...(card.round.infos || [])].map(loadDetail), loadSentPrompt(card.round));
        }
        loads.push(loadPreview());
      }
      await Promise.all(loads);
    }

    function openCard(id) {
      const found = locate(id);
      if (!found) return undefined;
      ensureCard();
      state.card = { id, ...found };
      // Harness settings may change between cards; each card reads its own preview.
      if (state.preview?.status !== "loading") state.preview = null;
      renderCard();
      cardDialog.open();
      return loadCard();
    }

    function reorder(id, move) {
      return run("调整顺序", () => protocol.reorderMailbox({ resident_id: residentId, message_id: id, ...move }));
    }

    function act(action, id) {
      if (action === "refresh") return load();
      if (action === "open") return openCard(id);
      if (action === "infos") {
        const key = `infos:${id}`;
        if (state.expanded.has(key)) state.expanded.delete(key);
        else state.expanded.add(key);
        return render();
      }
      if (action === "select-all") {
        // Selects every request and group for merging; once all are selected, clears them.
        const ids = movableIds();
        if (ids.every(item => state.selected.has(item))) state.selected.clear();
        else ids.forEach(item => state.selected.add(item));
        return render();
      }
      if (action === "merge") {
        const ids = movableIds().filter(item => state.selected.has(item));
        return run("合并", async () => {
          await protocol.mergeMailboxItems({ resident_id: residentId, message_ids: ids });
          state.selected.clear();
        }, `已把 ${ids.length} 项合并为一条群体消息`);
      }
      if (action === "split") {
        return run("拆分", () => protocol.splitMailboxItem({ resident_id: residentId, message_id: id }), "群体已拆回原来的消息");
      }
      if (action === "withdraw") {
        return run("撤回", () => protocol.cancelInput({ resident_id: residentId, message_id: id }), "已撤回，这条消息不会被处理");
      }
      if (action === "requeue") {
        return run("放回队列", () => protocol.requeueMessage({ resident_id: residentId, message_id: id }), "已放回队列，排在最前面");
      }
      return undefined;
    }

    function clearDropMarks() {
      root.querySelectorAll("[data-drop-position]").forEach(node => node.removeAttribute("data-drop-position"));
      root.querySelectorAll(".is-dragging").forEach(node => node.classList.remove("is-dragging"));
    }

    function endDrag() {
      state.dragId = "";
      clearDropMarks();
      if (state.renderAfterDrag) {
        state.renderAfterDrag = false;
        render();
      }
    }

    function dropTarget(event) {
      const target = event.target?.closest?.('[data-mailbox-list="pending"] [data-mailbox-item][draggable="true"]');
      return target && root.contains(target) ? target : null;
    }

    root.addEventListener("click", event => {
      const button = event.target.closest("[data-mailbox-action]");
      if (!button || !root.contains(button) || button.disabled) return;
      void act(button.dataset.mailboxAction, button.closest("[data-mailbox-item]")?.dataset.mailboxItem || "");
    });
    root.addEventListener("change", event => {
      const box = event.target.closest("[data-mailbox-select]");
      if (!box) return;
      const id = box.closest("[data-mailbox-item]")?.dataset.mailboxItem || "";
      if (box.checked) state.selected.add(id);
      else state.selected.delete(id);
      render();
    });
    root.addEventListener("keydown", event => {
      const row = event.target.closest?.('[data-mailbox-item][draggable="true"]');
      if (!row || event.target !== row || !event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
      const ids = movableIds();
      const index = ids.indexOf(row.dataset.mailboxItem);
      const neighbor = ids[index + (event.key === "ArrowUp" ? -1 : 1)];
      event.preventDefault();
      if (index < 0 || !neighbor || state.busy) return;
      void reorder(ids[index], event.key === "ArrowUp" ? { before_message_id: neighbor } : { after_message_id: neighbor });
    });
    root.addEventListener("dragstart", event => {
      const row = event.target?.closest?.('[data-mailbox-item][draggable="true"]');
      if (!row || state.busy) return;
      state.dragId = row.dataset.mailboxItem;
      row.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", state.dragId);
    });
    root.addEventListener("dragover", event => {
      const target = dropTarget(event);
      if (!state.dragId || !target || target.dataset.mailboxItem === state.dragId) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      const rect = target.getBoundingClientRect();
      const position = event.clientY > rect.top + rect.height / 2 ? "after" : "before";
      root.querySelectorAll("[data-drop-position]").forEach(node => {
        if (node !== target) node.removeAttribute("data-drop-position");
      });
      target.dataset.dropPosition = position;
    });
    root.addEventListener("drop", event => {
      const target = dropTarget(event);
      const moving = state.dragId;
      const position = target?.dataset.dropPosition || "before";
      endDrag();
      if (!moving || !target || target.dataset.mailboxItem === moving) return;
      event.preventDefault();
      const anchor = target.dataset.mailboxItem;
      void reorder(moving, position === "after" ? { after_message_id: anchor } : { before_message_id: anchor });
    });
    root.addEventListener("dragend", endDrag);

    // The shared Resident store streams this Conversation's events; each change re-reads the Mailbox.
    const subscription = protocol.acquire?.(residentId, () => schedule()) || null;
    render();
    void load();

    return {
      element: root,
      reload: load,
      destroy() {
        state.destroyed = true;
        clearTimeout(timer);
        subscription?.destroy?.();
        cardDialog?.close?.({ force: true });
        cardDialog?.destroy?.();
        cardSurface?.destroy?.();
        cardOverlay?.remove();
        root.remove();
      },
    };
  }

  window.PortalMailboxPanel = Object.freeze({ mount });
})();
