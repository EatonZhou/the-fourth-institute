(() => {
  if (window.PortalChatWorkspaceMessages) return;

  // One message in the list: its role and identity, the author header and avatar,
  // the references and attachments it carries, its action menu, the quote and copy
  // text it gives, folding long replies, and the keys that name it in the list.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml", "messageAttachmentRefHtml", "messageAvatarPlaceholderHtml",
    "outwardToResidents", "resourceChips", "runtimeActivityPhase", "selectedRefLabels",
  ]);

  // Who a message speaks for: "agent", "human", "portal" or "error".  Messages carry
  // role user, assistant (agent), system or error; Portal's local messages use portal.
  function messageRole(item) {
    const role = String(item?.role || "").toLowerCase();
    if (role === "assistant" || role === "agent") return "agent";
    if (role === "user" || role === "human") return "human";
    if (role === "error") return "error";
    if (role === "system" || role === "portal") return "portal";
    return item?.error ? "error" : "human";
  }

  function newMessageIdentity() {
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return `msg_${Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("")}`;
  }

  function messageIdentity(item) {
    const identity = String(item?.id || "").trim();
    if (!identity) throw new Error(window.PortalI18n.t("MessageIdentity missing from Chat Workspace message"));
    return identity;
  }

  function messageAuthoredByViewer(item) {
    const viewer = String(window.PortalIdentity?.username?.() || "").trim().toLowerCase();
    const sender = item?.origin?.sender;
    const author = String(sender?.id || item?.author_id || "").trim().toLowerCase();
    return !!viewer && viewer === author && (!sender?.kind || sender.kind === "user")
      && messageRole(item) === "human";
  }

  function messageSystemPresentation(item) {
    const role = messageRole(item);
    if (role !== "portal" && role !== "error") return null;
    const principal = item?.origin?.sender;
    const author = String(principal?.id || item?.author_id || item?.author || "").toLowerCase();
    const harness = author === "agent_harness";
    return {
      source: harness ? "harness" : "portal",
      title: harness ? window.PortalI18n.t("Agent Harness") : author === "portal" ? "Portal" : "",
      badge: role === "error" ? window.PortalI18n.t("异常") : harness ? window.PortalI18n.t("运行调度") : window.PortalI18n.t("系统消息"),
    };
  }

  function messageTitle(item, index, options = {}) {
    const system = messageSystemPresentation(item);
    if (system?.title && messageRole(item) !== "error") return system.title;
    if (item?.origin?.sender?.kind === "mailbox") return "Mailbox";
    if (typeof item?.sender_label === "string" && item.sender_label) return item.sender_label;
    if (item?.origin?.sender?.id) return String(item.origin.sender.id);
    const role = messageRole(item);
    if (role === "agent") return item?.name || item?.author || options.assistantName || "Agent";
    if (role === "portal") return item?.name || item?.author || "Portal";
    if (role === "error") return item?.title || item?.name || item?.author || window.PortalI18n.t("执行失败");
    return item?.name || item?.author || window.PortalI18n.t("用户");
  }

  function messageResidentAuthorId(item) {
    const principal = item?.origin?.sender;
    const authorId = String(principal?.id || item?.author_id || item?.author || "").trim();
    return (!principal || principal.kind === "resident") && /^resident_[0-9a-f]{12}$/.test(authorId) ? authorId : "";
  }

  const MESSAGE_SUBMENU_CLOSE_DELAY_MS = 250;

  function setMessageSubmenuOpen(submenu, open) {
    if (!submenu) return;
    window.clearTimeout(submenu.portalCloseTimer);
    submenu.classList.toggle("open", open);
    submenu.querySelector("[data-portal-message-submenu-trigger]")?.setAttribute("aria-expanded", open ? "true" : "false");
    const panel = submenu.querySelector(".portalMessageSubmenuPanel");
    if (!open || !panel) return;
    // Beside the item, on whichever side has room, and inside the viewport.
    submenu.classList.remove("opensLeft");
    panel.style.top = "";
    const margin = 8;
    const width = document.documentElement.clientWidth;
    const height = document.documentElement.clientHeight;
    let rect = panel.getBoundingClientRect();
    if (rect.right > width - margin && submenu.getBoundingClientRect().left - rect.width >= margin) {
      submenu.classList.add("opensLeft");
      rect = panel.getBoundingClientRect();
    }
    if (rect.bottom > height - margin) {
      const shift = Math.min(rect.bottom - (height - margin), rect.top - margin);
      panel.style.top = `${panel.offsetTop - Math.max(0, shift)}px`;
    }
  }

  function closeMessageSubmenus(menu, except = null) {
    menu?.querySelectorAll(".portalMessageSubmenu.open").forEach(submenu => {
      if (submenu !== except) setMessageSubmenuOpen(submenu, false);
    });
  }

  function bindMessageSubmenus(menu) {
    if (!menu || menu.dataset.portalMessageSubmenusBound === "1") return;
    menu.dataset.portalMessageSubmenusBound = "1";
    const submenuOf = target => (target instanceof Element ? target.closest("[data-portal-message-submenu]") : null);
    // Leaving waits a moment, so the pointer can cross other items on its way to the choices.
    menu.addEventListener("pointerover", event => {
      const submenu = submenuOf(event.target);
      if (!submenu) return;
      closeMessageSubmenus(menu, submenu);
      if (submenu.classList.contains("open")) window.clearTimeout(submenu.portalCloseTimer);
      else setMessageSubmenuOpen(submenu, true);
    });
    menu.addEventListener("pointerout", event => {
      const submenu = submenuOf(event.target);
      if (!submenu || submenu.contains(event.relatedTarget)) return;
      window.clearTimeout(submenu.portalCloseTimer);
      submenu.portalCloseTimer = window.setTimeout(() => setMessageSubmenuOpen(submenu, false), MESSAGE_SUBMENU_CLOSE_DELAY_MS);
    });
    menu.addEventListener("click", event => {
      const trigger = event.target instanceof Element ? event.target.closest("[data-portal-message-submenu-trigger]") : null;
      if (!trigger) return;
      // A click (a tap, or Enter) opens it too; leaving it or choosing closes it.
      const submenu = submenuOf(trigger);
      setMessageSubmenuOpen(submenu, true);
      if (event.detail === 0) submenu.querySelector(".portalMessageSubmenuPanel button")?.focus();
    });
    menu.addEventListener("keydown", event => {
      const submenu = submenuOf(event.target);
      if (!submenu) return;
      const trigger = submenu.querySelector("[data-portal-message-submenu-trigger]");
      if (event.key === "ArrowRight" && event.target === trigger) {
        event.preventDefault();
        setMessageSubmenuOpen(submenu, true);
        submenu.querySelector(".portalMessageSubmenuPanel button")?.focus();
      } else if (event.key === "ArrowLeft" && event.target !== trigger) {
        event.preventDefault();
        setMessageSubmenuOpen(submenu, false);
        trigger?.focus();
      }
    });
  }

  // Quote replies (decision_f560803947c6, decision_c524c775e0be): a message with a body
  // can be quoted. The sender names only the quoted message; the platform fills in its
  // author and the opening of its text, shown above the new message's body.
  const MESSAGE_QUOTE_TEXT_LIMIT = 1000;

  function messageQuoteExcerpt(item) {
    const parts = Array.isArray(item?.content_parts) ? item.content_parts : null;
    const text = parts
      ? parts.filter(part => part?.type === "text").map(part => String(part.text || "")).join("\n")
      : String(item?.content || "");
    return text.trim().slice(0, MESSAGE_QUOTE_TEXT_LIMIT).trimEnd();
  }

  function messageQuotable(item) {
    return !item?.pending && !!String(item?.message_id || "").trim() && !!messageQuoteExcerpt(item);
  }

  function messageCopyText(article, raw) {
    const body = article?.querySelector(".messageContent");
    if (!body) return raw;
    if (body.getClientRects().length) return body.innerText;
    // Hidden group members need layout for innerText to preserve paragraph breaks.
    const copy = body.cloneNode(true);
    copy.setAttribute("aria-hidden", "true");
    copy.style.cssText = "position:fixed;left:0;top:0;opacity:0;pointer-events:none";
    document.body.append(copy);
    try {
      return copy.innerText;
    } finally {
      copy.remove();
    }
  }

  // Long bodies fold (decision_f560803947c6): a body taller than about
  // MESSAGE_FOLD_MIN_LINES lines shows its first lines fading out, and "展开全文".
  // The newest Agent reply and a running turn's output start expanded. A user's
  // choice is kept by message ID for this page and never saved.
  const MESSAGE_FOLD_MIN_LINES = 12;

  // Writes the same attributes and text messageFoldBodyHtml renders, so a later render reuses the node.
  function applyMessageFold(button, expanded) {
    const body = button?.previousElementSibling;
    if (!body?.dataset.messageFold) return;
    body.dataset.messageFold = expanded ? "expanded" : "collapsed";
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
    button.textContent = expanded ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开全文");
  }

  function messageRenderKey(item) {
    return messageIdentity(item);
  }

  // One member output can answer several Family requests. Each placement has
  // its own DOM identity; message actions and shared fold choices still use
  // the original message ID.
  function messageInstanceKey(item) {
    const request = String(item?.family_reply?.request_message_id || "");
    return request ? `${messageIdentity(item)}__request__${request}` : messageIdentity(item);
  }

  function messageNodeKey(node) {
    return String(node?.dataset?.messageInstance || node?.dataset?.messageKey || "");
  }

  function messageInstanceSelector(key) {
    const escaped = CSS.escape(key);
    return `[data-message-instance="${escaped}"], [data-message-key="${escaped}"]:not([data-message-instance])`;
  }

  // A block a renderer shows after a message (renderAfterMessage) is keyed
  // by that message, and its actions act on that message.
  function messageAfterKey(key) {
    return `${key}::after`;
  }

  function messageSenderIdentity(item) {
    const principal = item?.origin?.sender;
    const id = String(principal?.id || item?.author_id || "").trim();
    if (!id) return "";
    const kind = principal?.kind || (/^resident_[0-9a-f]{12}$/.test(id) ? "resident" : "user");
    return `${kind}:${kind === "user" ? id.toLowerCase() : id}`;
  }

  function createMessages(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceMessages is missing: ${missing.join(", ")}`);
    const {
      escapeHtml, messageAttachmentRefHtml, messageAvatarPlaceholderHtml, outwardToResidents,
      resourceChips, runtimeActivityPhase, selectedRefLabels,
    } = dependencies;

    function messageAuthorHtml(item, fallbackTitle, options = {}) {
      const authorId = messageResidentAuthorId(item);
      return authorId ? resourceChips.resourceLabelHtml(authorId, options) : escapeHtml(fallbackTitle);
    }

    function messageAuthorHeaderHtml(item, label, system, options = {}) {
      const residentId = !system && messageResidentAuthorId(item);
      // In a Resident's own Chat its name opens nothing: the Chat is already open.
      const own = Boolean(residentId) && residentId === options.chatResidentId;
      const linked = residentId && !own;
      const content = window.PortalI18n.html`${messageAvatarHtml(item, label)}<span class="messageTitle">${messageAuthorHtml(item, label, { focusable: !residentId })}</span>`;
      if (linked) {
        const href = `/lineage?${new URLSearchParams({ resident: residentId, open_chat: "1" })}`;
        return window.PortalI18n.html`<a class="messageAuthor messageAuthorLink" href="${escapeHtml(href)}" title="打开 Resident 对话">${content}</a>`;
      }
      return window.PortalI18n.html`<span class="messageAuthor">${content}${system ? window.PortalI18n.html`<span class="messageSystemBadge">${escapeHtml(system.badge)}</span>` : ""}</span>`;
    }

    let messageAvatarLoad;
    function bindMessageAvatars(root) {
      if (!root?.querySelector?.("[data-message-avatar-principal]")) return;
      if (!messageAvatarLoad) {
        const { loadAsset } = window.PortalUIPrimitives;
        void loadAsset("/static/portal_resident_avatar.css").catch(() => {});
        // An older copy of the avatar module (loaded by another part of the page)
        // lacks what Chat calls, so Chat loads the current one over it.
        messageAvatarLoad = loadAsset("/static/portal_resident_avatar.js", { ready: () => window.PortalResidentAvatar })
          .then(() => loadAsset("/static/portal_user_avatar.js", { ready: () => window.PortalUserAvatar?.messageAvatarHtml }))
          .catch(error => { messageAvatarLoad = null; throw error; });
      }
      messageAvatarLoad.then(() => window.PortalUserAvatar.bind(root)).catch(() => {});
    }

    function messageAvatarHtml(item, label) {
      if (messageSystemPresentation(item)) return "";
      const principal = item?.origin?.sender;
      const id = String(principal?.id || item?.author_id || (messageRole(item) === "human" ? item?.author : "") || "").trim();
      if (!id || messageRole(item) === "error" || (!principal && messageRole(item) !== "human" && !/^resident_[0-9a-f]{12}$/.test(id))) return "";
      // Only Users and Residents have avatars; a service sender (Portal) keeps its letter.
      if (principal?.kind && !["user", "resident"].includes(principal.kind)) {
        return window.PortalI18n.html`<span class="portalUserAvatar messageAuthorAvatar" aria-hidden="true">${escapeHtml(Array.from(label || id)[0]?.toUpperCase() || "?")}</span>`;
      }
      // Once the avatar is known, render it directly so a re-rendered message
      // matches the page and keeps its node instead of flashing the letter.
      const known = window.PortalUserAvatar?.messageAvatarHtml;
      if (known) return known(id, label);
      return messageAvatarPlaceholderHtml(id, label);
    }

    function messageContextRefsHtml(contextRefs, options = {}) {
      const labels = selectedRefLabels(contextRefs || {}, options.contextOptions || {});
      if (!labels.length) return "";
      return window.PortalI18n.html`<div class="messageRefs">
      ${labels.slice(0, 4).map(label => window.PortalI18n.html`<span class="messageRefChip" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`).join("")}
      ${labels.length > 4 ? window.PortalI18n.html`<span class="messageRefChip">+${labels.length - 4}</span>` : ""}
    </div>`;
    }

    function messageAttachmentsHtml(attachments, options = {}) {
      const items = Array.isArray(attachments) ? attachments : [];
      if (!items.length) return "";
      return window.PortalI18n.html`<div class="composerImageTray messageAttachmentTray">
      ${items.map(ref => {
        const kind = String(ref?.kind || ref?.mime || "").startsWith("image") ? "image" : "file";
        return messageAttachmentRefHtml(kind, ref?.path || ref?.url || "", options);
      }).join("")}
    </div>`;
    }

    // A message menu item whose choices open beside it while the pointer rests on it,
    // or on click or →; the choices are ordinary message actions.
    function messageSubmenuHtml(name, label, itemsHtml) {
      return window.PortalI18n.html`<span class="portalMessageSubmenu" data-portal-message-submenu="${escapeHtml(name)}"><button type="button" class="portalMessageSubmenuTrigger" data-portal-message-submenu-trigger aria-haspopup="menu" aria-expanded="false">${escapeHtml(label)}<span class="portalMessageSubmenuChevron" aria-hidden="true">›</span></button><span class="portalMessageSubmenuPanel" role="menu">${itemsHtml}</span></span>`;
    }

    function commonMessageActionsHtml(item, key, options = {}, firstAgentReply = false) {
      const content = String(item?.content || "");
      if (!content) return "";
      const customItems = options.renderMessageMenuItems
        ? String(options.renderMessageMenuItems(item, key) || "")
        : "";
      return window.PortalI18n.html`<span class="portalMessageMenuPanel" data-portal-message-menu="${escapeHtml(key)}" data-portal-message-key="${escapeHtml(key)}" hidden>
        ${customItems}
        ${options.pinMessages && messageRole(item) === "agent" && messageQuotable(item) ? window.PortalI18n.html`<button type="button" data-portal-message-action="pin">置顶消息</button>` : ""}
        <button type="button" data-portal-message-copy="text">复制为纯文本</button>
        <button type="button" data-portal-message-copy="markdown">复制为 Markdown</button>
        ${firstAgentReply ? window.PortalI18n.html`<button type="button" data-portal-message-copy="text" data-portal-message-copy-scope="replies">复制全部回复为纯文本</button>
        <button type="button" data-portal-message-copy="markdown" data-portal-message-copy-scope="replies">复制全部回复为 Markdown</button>` : ""}
    </span>`;
    }

    function messageQuoteAuthorHtml(authorId) {
      const id = String(authorId || "").trim();
      if (/^resident_[0-9a-f]{12}$/.test(id)) {
        return window.PortalI18n.html`<span data-resource-label="${escapeHtml(id)}" data-resource-chip-skip title="${escapeHtml(id)}">${escapeHtml(id)}</span>`;
      }
      return escapeHtml(!id || id === "portal" ? "Portal" : id);
    }

    function quoteTextHtml(text, options) {
      if (!options.renderMentions) return escapeHtml(text);
      const template = document.createElement("template");
      template.innerHTML = options.renderMentions(escapeHtml(text));
      // A quote has one action: jump to its source. Do not nest resource links inside its button.
      template.content.querySelectorAll("a").forEach(link => link.replaceWith(...link.childNodes));
      return template.innerHTML;
    }

    // The quote block above a message's body; one naming its message goes to that message.
    function messageQuoteHtml(quote, options = {}) {
      const text = String(quote?.text || "").trim();
      if (!text) return "";
      const messageId = String(quote?.message_id || "").trim();
      const inner = window.PortalI18n.html`<span class="messageQuoteAuthor">${messageQuoteAuthorHtml(quote?.author_id)}</span><span class="messageQuoteText">${quoteTextHtml(text, options)}</span>`;
      return messageId
        ? window.PortalI18n.html`<button class="messageQuote" type="button" data-message-quote="${escapeHtml(messageId)}" title="跳到被引用的消息">${inner}<span class="messageQuoteJump" aria-hidden="true">↑</span></button>`
        : window.PortalI18n.html`<div class="messageQuote">${inner}</div>`;
    }

    // The composer and sent message use the same single-line excerpt styling.
    // Keep the excerpt intact; visual clipping must not change the quoted content.
    function composerQuoteHtml(quote, options = {}) {
      const text = String(quote?.text || "").trim();
      return window.PortalI18n.html`<div class="composerQuote" data-composer-quote="${escapeHtml(quote.message_id)}" role="group" aria-label="引用的消息">
      <div class="composerQuoteBody"><span class="composerQuoteAuthor">${messageQuoteAuthorHtml(quote.author_id)}</span><span class="composerQuoteText">${quoteTextHtml(text, options)}</span></div>
      <button class="composerQuoteCancel" type="button" data-composer-quote-cancel aria-label="取消引用" title="取消引用" data-portal-icon="close"></button>
    </div>`;
    }

    const MESSAGE_FOLD_CARD_PARTS = new Set(["native_turn", "resource_changes", "subagent_run", "async_questions", "tool_call", "tool_result"]);
    const messageFoldChoices = new Map();

    // A reply is an Agent message with text of its own; cards and questions are not replies.
    function messageFoldReply(item) {
      if (messageRole(item) !== "agent" || outwardToResidents(item)) return false;
      const parts = Array.isArray(item?.content_parts) ? item.content_parts : null;
      if (!parts) return !!String(item?.content || "").trim();
      return parts.some(part => part?.type === "text" && String(part.text || "").trim())
        && !parts.some(part => MESSAGE_FOLD_CARD_PARTS.has(part?.type));
    }

    // The keys that start expanded: the newest reply, pending messages and, while a
    // turn runs, the Agent output after the last other message.
    function messageFoldDefaults(items, options = {}) {
      const expanded = new Set();
      let latest = null;
      let latestAt = -Infinity;
      for (const item of items) {
        if (item?.pending || item?.live_output) expanded.add(messageRenderKey(item));
        if (!messageFoldReply(item)) continue;
        // Newest by time: a Family facade shows each reply after the request it answers.
        const at = Date.parse(String(item?.created_at || ""));
        if (!(at < latestAt)) {
          latest = item;
          if (Number.isFinite(at)) latestAt = at;
        }
      }
      if (options.runtimeLive) {
        for (let index = items.length - 1; index >= 0 && messageRole(items[index]) === "agent"; index -= 1) {
          expanded.add(messageRenderKey(items[index]));
        }
      }
      if (latest) expanded.add(messageRenderKey(latest));
      return expanded;
    }

    function messageFoldBodyHtml(key, bodyHtml, expanded, size = "") {
      return window.PortalI18n.html`<div class="messageContent markdownBody" data-message-fold="${expanded ? "expanded" : "collapsed"}" data-message-fold-size="${escapeHtml(size)}">${bodyHtml}</div><button class="messageFoldToggle" type="button" data-message-fold-toggle="${escapeHtml(key)}" aria-expanded="${expanded ? "true" : "false"}">${expanded ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开全文")}</button>`;
    }

    function normalizeMessageStream(messages, options = {}) {
      const normalized = Array.isArray(messages) ? messages : [];
      const topLevelRuntimeLive = !!options.runtimeLive;
      const topLevelDetails = Array.isArray(options.runtimeDetails) ? options.runtimeDetails : [];
      return {
        messages: normalized,
        runtimeDetails: topLevelDetails,
        runtimeLive: topLevelRuntimeLive,
        runtimePhase: runtimeActivityPhase(topLevelDetails, topLevelRuntimeLive, options.runtimePhase),
      };
    }

    return Object.freeze({
      messageRole, newMessageIdentity, messageIdentity, messageAuthoredByViewer,
      messageSystemPresentation, messageTitle, messageAuthorHeaderHtml, bindMessageAvatars,
      messageContextRefsHtml, messageAttachmentsHtml, messageSubmenuHtml, closeMessageSubmenus,
      bindMessageSubmenus, commonMessageActionsHtml, messageQuoteExcerpt, messageQuotable,
      messageQuoteHtml, composerQuoteHtml, messageCopyText, MESSAGE_FOLD_MIN_LINES,
      messageFoldChoices, messageFoldDefaults, messageFoldBodyHtml,
      applyMessageFold, normalizeMessageStream, messageRenderKey, messageInstanceKey,
      messageNodeKey, messageInstanceSelector, messageAfterKey, messageSenderIdentity,
    });
  }

  window.PortalChatWorkspaceMessages = Object.freeze({
    createMessages, dependencies: DEPENDENCIES, messageRole, newMessageIdentity, messageIdentity,
    messageAuthoredByViewer, messageSystemPresentation, messageTitle, closeMessageSubmenus,
    bindMessageSubmenus, messageQuoteExcerpt, messageQuotable, messageCopyText,
    MESSAGE_FOLD_MIN_LINES, applyMessageFold, messageRenderKey, messageInstanceKey,
    messageNodeKey, messageInstanceSelector, messageAfterKey, messageSenderIdentity,
  });
})();
