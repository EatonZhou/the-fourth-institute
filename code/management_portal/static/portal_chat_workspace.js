(function () {
  const escapeHtml = value => window.PortalUIPrimitives.escapeHtml(value);

  const MESSAGE_ATTACHMENT_REF_RE = /\[(image|file):\s*([^\]]+)\]/g;
  const MESSAGE_REFERENCE_REF_RE = /\[引用[:：]\s*([^\]\n]+)\]/g;
  const MARKDOWN_IMAGE_RE = /!\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+["'][^"']*["'])?\)/g;
  const COMPOSER_DRAFT_ATTACHMENT_RE = /\[\[portal-(image|file):([^\]]+)\]\]/g;
  const resourceChips = window.PortalResourceChips;
  if (!resourceChips) throw new Error("PortalResourceChips must load before PortalChatWorkspace");
  const FORWARD_MESSAGE_TAG_RE = /^<\s*(\/?)\s*forward(?:[\s_-]+)message\s*>$/i;
  const FORWARD_REFERENCE_SPEAKER_RE = /^([A-Za-z][A-Za-z0-9_. -]{0,79}):[ \t]*(.*)$/;
  const WORKSPACE_PASTE_HANDLED_FLAG = "__portalWorkspacePasteHandled";
  const DEFAULT_RESIDENT_HISTORY_PAGE_SIZE = 50;
  const RESIDENT_INITIAL_LOAD_TIMEOUT_MS = 90_000;
  const RESIDENT_CHAT_WORKSPACE_SCHEMA = "portal.chat_workspace.v3";

  function residentHandoffCards(value) {
    let parsed;
    try { parsed = JSON.parse(value); } catch { return []; }
    return (Array.isArray(parsed) ? parsed : [parsed]).filter(item => item && typeof item === "object"
      && /^resident_[0-9a-f]{12}$/.test(item.resident_id || "")).map(item => ({
      resident_id: item.resident_id,
      lineage_id: /^lineage_[0-9a-f]{12}$/.test(item.lineage_id || "") ? item.lineage_id : "",
      title: typeof item.title === "string" && item.title.trim() ? item.title.trim() : item.resident_id,
      lineage_title: typeof item.lineage_title === "string" ? item.lineage_title : "",
      reason: typeof item.reason === "string" ? item.reason : "",
      recommended_action: item.recommended_action === "ask" ? "ask" : "open",
      ask_message: typeof item.ask_message === "string" ? item.ask_message.trim() : "",
    }));
  }

  // Only complete, top-level fences are actionable; examples inside other fences stay code.
  function residentHandoffParts(content) {
    const lines = String(content || "").split("\n");
    const parts = [];
    let start = 0;
    for (let index = 0; index < lines.length; index += 1) {
      const opening = lines[index].match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
      if (!opening) continue;
      const close = new RegExp(`^ {0,3}${opening[1][0]}{${opening[1].length},}\\s*$`);
      let end = index + 1;
      while (end < lines.length && !close.test(lines[end])) end += 1;
      if (end === lines.length) break;
      if (opening[2].trim() === "portal-resident-handoff") {
        const cards = residentHandoffCards(lines.slice(index + 1, end).join("\n"));
        if (cards.length) {
          parts.push({ text: lines.slice(start, index).join("\n") }, { cards });
          start = end + 1;
        }
      }
      index = end;
    }
    parts.push({ text: lines.slice(start).join("\n") });
    return parts;
  }

  function residentHandoffHtml(card) {
    const params = new URLSearchParams({ resident: card.resident_id });
    if (card.lineage_id) params.set("lineage", card.lineage_id);
    const recommended = card.ask_message ? card.recommended_action : "open";
    return `<section class="portalResidentHandoff" data-resident-handoff="${escapeHtml(card.resident_id)}">
      <strong>${escapeHtml(card.title)}</strong><small>${escapeHtml(card.lineage_title || card.lineage_id)}</small>
      <p>${escapeHtml(card.reason)}</p><div class="portalResidentHandoffActions">
      <a href="/lineage?${escapeHtml(params.toString())}" class="${recommended === "open" ? "recommended" : ""}">${window.PortalI18n.t("打开对话")}${recommended === "open" ? window.PortalI18n.t("（推荐）") : ""}</a>
      ${card.ask_message ? `<button type="button" class="${recommended === "ask" ? "recommended" : ""}" data-portal-message-action="resident-handoff-ask" data-handoff-card="${escapeHtml(JSON.stringify(card))}">${window.PortalI18n.t("替我问一下")}${recommended === "ask" ? window.PortalI18n.t("（推荐）") : ""}</button>` : ""}
      </div></section>`;
  }

  function renderResidentHandoffs(content, render) {
    return residentHandoffParts(content).map(part => part.cards
      ? part.cards.map(residentHandoffHtml).join("") : render(part.text)).join("");
  }

  function parseForwardReferenceEntries(reference) {
    const entries = [];
    let current = null;
    for (const line of String(reference || "").split("\n")) {
      const match = line.match(FORWARD_REFERENCE_SPEAKER_RE);
      if (match) {
        current = { speaker: match[1].trim(), content: match[2] || "" };
        entries.push(current);
      } else if (current) {
        current.content += `${current.content ? "\n" : ""}${line}`;
      }
    }
    return entries.map(entry => ({
      ...entry,
      content: entry.content.trim(),
    })).filter(entry => entry.speaker && entry.content);
  }

  function parseForwardReference(text) {
    const source = String(text || "").replace(/\r\n?/g, "\n");
    const lines = source.split("\n");
    const openingIndex = lines.findIndex(line => String(line || "").trim());
    if (openingIndex < 0) return null;
    const opening = lines[openingIndex].trim().match(FORWARD_MESSAGE_TAG_RE);
    if (!opening || opening[1]) return null;
    let closingIndex = -1;
    for (let index = lines.length - 1; index > openingIndex; index -= 1) {
      if (lines[index].trim().match(FORWARD_MESSAGE_TAG_RE)) {
        closingIndex = index;
        break;
      }
    }
    if (closingIndex < 0) return null;
    const reference = lines.slice(openingIndex + 1, closingIndex).join("\n").trim();
    if (!reference) return null;
    return {
      reference,
      entries: parseForwardReferenceEntries(reference),
      note: lines.slice(closingIndex + 1).join("\n").trim(),
    };
  }

  let imageEditorLoad = null;

  async function requireImageEditor() {
    if (window.PortalImageEditor?.open) return window.PortalImageEditor;
    if (!imageEditorLoad) {
      imageEditorLoad = new Promise((resolve, reject) => {
        if (!document.querySelector('link[href="/static/portal_image_editor.css"]')) {
          const style = document.createElement("link");
          style.rel = "stylesheet";
          style.href = "/static/portal_image_editor.css";
          document.head.append(style);
        }
        const script = document.createElement("script");
        script.src = "/static/portal_image_editor.js";
        script.onload = () => window.PortalImageEditor?.open
          ? resolve(window.PortalImageEditor)
          : reject(new Error("图片查看器初始化失败"));
        script.onerror = () => { script.remove(); reject(new Error("图片查看器加载失败，请重试")); };
        document.head.append(script);
      }).catch(error => { imageEditorLoad = null; throw error; });
    }
    return imageEditorLoad;
  }

  function runtimeStatusLabel(status) {
    const value = String(status || "").trim();
    return value === "in_progress" ? "running" : value;
  }

  const RUNTIME_PHASE_STATUS = Object.freeze({
    starting_session: { label: window.PortalI18n.t("正在启动 Session"), className: "startingSession" },
    waiting_capacity: { label: window.PortalI18n.t("等待 Worker/账号并发容量"), className: "waitingCapacity" },
    waiting_dispatch: { label: window.PortalI18n.t("等待账号派发间隔"), className: "waitingCapacity" },
    waiting_quota: { label: window.PortalI18n.t("自动任务已暂停：账号额度已用尽、达到配置的保护阈值或尚未确认"), className: "waitingCapacity" },
    stopping: { label: window.PortalI18n.t("正在停止当前任务"), className: "stopping" },
    reconnecting: { label: window.PortalI18n.t("正在重新连接"), className: "reconnecting" },
    compacting: { label: window.PortalI18n.t("正在压缩上下文"), className: "compacting" },
    // A Family facade (decision_44cf587c1721) works through its members.
    family_routing: { label: window.PortalI18n.t("Family Router 正在选择成员"), className: "startingSession" },
    family_waiting: { label: window.PortalI18n.t("已转交成员，等待成员开始处理"), className: "waitingCapacity" },
  });

  const PRE_PROVIDER_RUNTIME_PHASES = new Set([
    "starting_session",
    "waiting_capacity",
    "waiting_dispatch",
    "waiting_quota",
    "family_routing",
    "family_waiting",
  ]);

  function runtimeActivityPhase(details, live = false, requestedPhase = "") {
    const requested = String(requestedPhase || "").trim().toLowerCase();
    const canonicalPhase = ["starting_session", "waiting_capacity", "waiting_dispatch", "waiting_quota", "stopping", "family_routing", "family_waiting"].includes(requested)
      ? requested
      : "";
    if (!live || canonicalPhase === "stopping") return canonicalPhase;
    const items = Array.isArray(details) ? details : [];
    const latest = items.length && items[items.length - 1] && typeof items[items.length - 1] === "object"
      ? items[items.length - 1]
      : null;
    const phase = String(latest?.phase || "").trim().toLowerCase();
    const kind = String(latest?.kind || "").trim().toLowerCase();
    const status = runtimeStatusLabel(latest?.status || "").toLowerCase();
    const isActive = ["running", "inprogress"].includes(status.replaceAll("_", ""));
    if (phase === "reconnecting" && isActive) {
      return "reconnecting";
    }
    if (kind === "compact" && isActive) {
      return "compacting";
    }
    return canonicalPhase;
  }

  function runtimePhaseStatusHtml(phase) {
    const status = RUNTIME_PHASE_STATUS[String(phase || "")];
    if (!status) return "";
    return window.PortalI18n.html`<div class="chatRuntimeStatus ${status.className}" data-message-key="__runtime_phase__" role="status" aria-live="polite">
      <span class="chatRuntimeStatusDot" aria-hidden="true"></span>
      <span>${escapeHtml(status.label)}</span>
    </div>`;
  }

  function runtimeDetailsHtml(details, options = {}) {
    const items = Array.isArray(details) ? details : [];
    const isLive = !!options.live;
    if (!items.length) return isLive && options.emptyLiveHtml ? String(options.emptyLiveHtml) : "";
    // These are historical events: a start row keeps its original running status.
    // Summarize the last displayed event, including its status, just like the expanded list.
    const latest = isLive ? items[items.length - 1] : null;
    const latestExcerpt = latest ? String(latest.content || "").trim().split("\n")[0].slice(0, 80) : "";
    const latestStatus = runtimeStatusLabel(latest?.status || "");
    const latestLabel = latest
      ? ` · ${latest.title || latest.kind || window.PortalI18n.t("活动")}${latestStatus ? ` · ${latestStatus}` : ""}${latestExcerpt ? ` · ${latestExcerpt}` : ""}`
      : "";
    const summary = isLive ? window.PortalI18n.text`${options.assistantName || "Agent"} 活动${latestLabel}` : window.PortalI18n.text`活动 · ${items.length}`;
    const rows = items.map((detail) => {
      const title = detail?.title || detail?.kind || window.PortalI18n.t("活动");
      const status = runtimeStatusLabel(detail?.status || "");
      const activityTime = detail?.updated_at || detail?.started_at || detail?.created_at || "";
      const created = options.formatTime ? options.formatTime(activityTime) : String(activityTime);
      const meta = [status, created].filter(Boolean).join(" · ");
      const content = String(detail?.content || "").trim();
      return window.PortalI18n.html`<div class="${options.itemClass || "runtimeDetailItem"}">
        <div class="${options.kindClass || "runtimeDetailKind"}" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
        <div class="${options.textClass || "runtimeDetailText"}">
          ${content ? window.PortalI18n.html`<div>${escapeHtml(content)}</div>` : ""}
          ${meta ? window.PortalI18n.html`<div class="${options.metaClass || "runtimeDetailMeta"}">${escapeHtml(meta)}</div>` : ""}
        </div>
      </div>`;
    }).join("");
    return window.PortalI18n.html`<details class="${options.rootClass || "messageRuntimeDetails"}">
      <summary>${escapeHtml(summary)}</summary>
      <div class="${options.listClass || "runtimeDetailList"}">${rows}</div>
    </details>`;
  }

  function responseSourcesHtml(rawSources) {
    const sources = rawSources && typeof rawSources === "object" ? rawSources : {};
    const groups = [
      { key: "tools", label: window.PortalI18n.t("工具") },
      { key: "mcps", label: "MCP" },
      { key: "skills", label: window.PortalI18n.t("技能") },
      { key: "knowledge", label: window.PortalI18n.t("知识") },
    ].map(group => ({
      ...group,
      items: (() => {
        const items = Array.isArray(sources[group.key])
          ? sources[group.key].filter(item => item && typeof item === "object")
          : [];
        if (group.key !== "knowledge") return items;
        const byResourceId = new Map();
        items.forEach(item => {
          const resourceId = String(item.resource_id || item.address || "").trim();
          if (!resourceChips.isRootId(resourceId)) return;
          const existing = byResourceId.get(resourceId);
          if (existing) {
            existing.count += Math.max(1, Number(item.count || 1));
            return;
          }
          byResourceId.set(resourceId, {
            ...item,
            name: resourceId,
            resource_id: resourceId,
            count: Math.max(1, Number(item.count || 1)),
          });
        });
        return [...byResourceId.values()];
      })(),
    })).filter(group => group.items.length);
    if (!groups.length) return "";
    const skillGroup = groups.find(group => group.key === "skills");
    const otherGroups = groups.filter(group => group.key !== "skills");
    const skillUsage = skillGroup ? window.PortalI18n.html`<div class="messageSkillUsage">
      <span class="messageSkillUsageLabel">Skills</span>
      <span class="messageSkillUsageChips">${skillGroup.items.map(item => {
        const count = Math.max(1, Number(item.count || 1));
        const suffix = count > 1 ? ` ×${count}` : "";
        const name = String(item.name || window.PortalI18n.t("技能"));
        const path = String(item.path || "").trim();
        const description = String(item.description || "").trim();
        const title = description || window.PortalI18n.t("暂无技能说明");
        return path
          ? window.PortalI18n.html`<button class="messageSkillChip" type="button" data-portal-source-path="${escapeHtml(path)}" title="${escapeHtml(title)}">${escapeHtml(name + suffix)}</button>`
          : window.PortalI18n.html`<span class="messageSkillChip" title="${escapeHtml(title)}">${escapeHtml(name + suffix)}</span>`;
      }).join("")}</span>
      <span class="messageSkillUsageHint">Hover a Skill to see its description.</span>
    </div>` : "";
    if (!otherGroups.length) return skillUsage;
    const summary = otherGroups.map(group => {
      const count = group.key === "knowledge"
        ? group.items.length
        : group.items.reduce((total, item) => total + Math.max(1, Number(item.count || 1)), 0);
      return `${group.label} ${count}`;
    }).join(" · ");
    const rows = otherGroups.map(group => {
      if (group.key === "knowledge") {
        const chips = group.items.map(item => {
          const resourceId = String(item.resource_id || "").trim();
          return resourceChips.resourceChipHtml(resourceId, "messageKnowledgeResourceChip");
        }).join("");
        return window.PortalI18n.html`<div class="messageSourceGroup">
          <span class="messageSourceKind">${escapeHtml(group.label)}</span>
          <span class="messageSourceChips">${chips}</span>
        </div>`;
      }
      const chips = group.items.map(item => {
        const count = Math.max(1, Number(item.count || 1));
        const suffix = count > 1 ? ` ×${count}` : "";
        const namespace = String(item.server || item.namespace || "").replace(/^mcp__/, "").replace(/_+$/, "");
        const name = String(item.name || group.label);
        const label = namespace ? `${namespace} · ${name}${suffix}` : `${name}${suffix}`;
        const path = String(item.path || "").trim();
        if (path) {
          return window.PortalI18n.html`<button class="messageSourceChip path" type="button" data-portal-source-path="${escapeHtml(path)}" title="${escapeHtml(path)}">${escapeHtml(label)}</button>`;
        }
        return window.PortalI18n.html`<span class="messageSourceChip" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`;
      }).join("");
      return window.PortalI18n.html`<div class="messageSourceGroup">
        <span class="messageSourceKind">${escapeHtml(group.label)}</span>
        <span class="messageSourceChips">${chips}</span>
      </div>`;
    }).join("");
    return window.PortalI18n.html`${skillUsage}<details class="messageResponseSources" data-message-disclosure="response-sources">
      <summary><span>Used</span><span>${escapeHtml(summary)}</span></summary>
      <div class="messageSourceList">${rows}</div>
    </details>`;
  }

  function requestWorkspacePreview(request = {}) {
    if (!window.PortalPreview) return Promise.resolve(false);
    return window.PortalPreview.request(request);
  }

  function bindResponseSourcePaths(root, options = {}) {
    root?.querySelectorAll?.("[data-portal-source-path]").forEach(node => {
      if (node.dataset.portalSourcePathBound === "1") return;
      node.dataset.portalSourcePathBound = "1";
      node.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        const path = String(node.dataset.portalSourcePath || "").trim();
        if (!path) return;
        const request = {
          href: path,
          path,
          title: node.textContent?.trim() || window.PortalI18n.t("来源"),
          node,
          source: "response-source",
          target: root,
        };
        if (typeof options.onLinkPreview === "function") options.onLinkPreview(request);
        else requestWorkspacePreview(request);
      });
    });
  }

  function extractAttachedFileRefs(text) {
    const refs = [];
    String(text || "").replace(/<attached_files>\s*([\s\S]*?)\s*<\/attached_files>/gi, (_match, body) => {
      for (const line of String(body || "").split(/\r?\n/)) {
        const match = line.match(/^\s*(image|file)(?:\s+\d+)?\s*:\s*(.+?)\s*$/i);
        if (match) refs.push({ kind: match[1].toLowerCase(), path: match[2] });
      }
      return "";
    });
    return refs;
  }

  function messageAttachmentRefHtml(kind, path, options = {}) {
    const attachmentKind = String(kind || "") === "file" ? "file" : "image";
    const attachmentPath = String(path || "").trim();
    const previewUrl = attachmentKind === "image" && options.previewUrlForPath
      ? options.previewUrlForPath(attachmentPath)
      : "";
    const label = String(options.label || attachmentKind);
    const title = String(options.title || attachmentPath);
    return window.PortalI18n.html`<button class="${options.buttonClass || "composerImageRef messageImageRef"}" type="button" data-message-attachment-kind="${escapeHtml(attachmentKind)}" data-message-attachment-path="${escapeHtml(attachmentPath)}" ${previewUrl ? `data-portal-preview-image="${escapeHtml(previewUrl)}" aria-label="${escapeHtml(window.PortalI18n.text`查看图片：${title}`)}"` : ""} title="${escapeHtml(title)}">
      ${previewUrl ? window.PortalI18n.html`<img class="${options.thumbClass || "composerImageRefThumb"}" data-portal-deferred-image="${escapeHtml(previewUrl)}" alt="" loading="lazy" decoding="async" fetchpriority="low" />` : ""}
      ${previewUrl ? "" : window.PortalI18n.html`<span class="${options.iconClass || "composerFileRefIcon"}">${attachmentKind === "file" ? "F" : "I"}</span>`}
      ${previewUrl ? "" : window.PortalI18n.html`<span class="${options.labelClass || "composerImageRefLabel"}">${escapeHtml(label)}</span>`}
    </button>`;
  }

  function splitReferenceLocator(value) {
    const raw = String(value || "").trim();
    const match = raw.match(/^(.*?)(#L(\d+)(?:-L(\d+))?)$/);
    if (!match) return { path: raw, lineLabel: "" };
    const start = match[3] || "";
    const end = match[4] || "";
    return {
      path: String(match[1] || "").trim(),
      lineLabel: start ? (end && end !== start ? `${start}-${end}` : start) : "",
    };
  }

  function referenceAttachmentLabel(path) {
    const ref = splitReferenceLocator(path);
    const raw = String(ref.path || path || "").trim();
    const base = raw.split("/").filter(Boolean).pop() || raw || window.PortalI18n.t("引用");
    return ref.lineLabel ? `${base} L${ref.lineLabel}` : base;
  }

  function cleanMarkdownImageUrl(value) {
    return String(value || "").trim().replace(/^<([\s\S]*)>$/, "$1").trim();
  }

  function resourceSupportsTextPreview(address) {
    return resourceChips.resourceSupportsTextPreview(address);
  }

  async function loadResourcePreview(address, options = {}) {
    return resourceChips.loadPreview(address, options);
  }


  function bindResourceChips(root, options = {}) {
    const previewOptions = {
      ...options,
      onResourceLabelError({ node, error }) {
        // Keep historical authors visible when their resource cannot be resolved.
        // Transport failures do not establish that a Resident is unavailable.
        if (error?.status !== 404 || error?.data?.code !== "resource_not_found") return;
        const link = node.closest(".messageAuthorLink");
        if (!link) return;
        const author = document.createElement("span");
        author.className = "messageAuthor messageAuthorUnavailable";
        author.title = window.PortalI18n.t("Resident 暂不可用");
        author.append(...link.childNodes);
        link.replaceWith(author);
      },
      onResourcePreview: typeof options.onResourcePreview === "function"
        ? options.onResourcePreview
        : context => requestWorkspacePreview({
          ...context,
          resourceAddress: context.address,
          path: context.address,
          title: context.node?.textContent?.trim() || context.address,
          source: "chat",
          target: root,
        }),
    };
    resourceChips.bindResourceLinks(root, previewOptions);
    return resourceChips.bind(root, previewOptions);
  }

  function linkifyResourceAddresses(html) {
    return resourceChips.linkifyHtml(html);
  }

  function renderMarkdownWithAttachmentRefs(text, options = {}) {
    const refs = [];
    function stashAttachmentRef(html) {
      // Markdown can linkify resources before attachments are restored. Object
      // replacement characters keep this token outside a neighboring address.
      const token = `\uFFFCPORTAL_ATTACHMENT_REF_${refs.length}\uFFFC`;
      refs.push({ token, html });
      return token;
    }
    let source = String(text || "").replace(MESSAGE_ATTACHMENT_REF_RE, (_match, kind, path) => {
      return stashAttachmentRef(messageAttachmentRefHtml(kind, path, options));
    });
    source = source.replace(MARKDOWN_IMAGE_RE, (match, rawPath) => {
      const path = cleanMarkdownImageUrl(rawPath);
      const previewUrl = options.previewUrlForPath ? options.previewUrlForPath(path) : "";
      if (!previewUrl) return match;
      return stashAttachmentRef(messageAttachmentRefHtml("image", path, options));
    });
    source = source.replace(MESSAGE_REFERENCE_REF_RE, (match, rawPath) => {
      const path = cleanMarkdownImageUrl(rawPath);
      const ref = splitReferenceLocator(path);
      const previewUrl = options.previewUrlForPath ? options.previewUrlForPath(ref.path) : "";
      const title = [ref.path, ref.lineLabel ? `L${ref.lineLabel}` : ""].filter(Boolean).join(" ");
      return stashAttachmentRef(previewUrl
          ? messageAttachmentRefHtml("image", ref.path, { ...options, title })
          : messageAttachmentRefHtml("file", ref.path, { ...options, label: referenceAttachmentLabel(path), title }));
    });
    const attachedRefs = options.includeAttachedFiles === false ? [] : extractAttachedFileRefs(source);
    source = source.replace(/<attached_files>\s*[\s\S]*?\s*<\/attached_files>/gi, "").trim();
    for (const ref of attachedRefs) {
      const token = stashAttachmentRef(messageAttachmentRefHtml(ref.kind, ref.path, options));
      source = `${source}${source ? "\n\n" : ""}${token}`;
    }
    const renderMarkdown = options.renderMarkdown || ((value) => escapeHtml(value).replace(/\n/g, "<br>"));
    let html = renderMarkdown(source);
    refs.forEach((item) => {
      html = html.split(item.token).join(item.html);
    });
    return linkifyResourceAddresses(options.renderMentions ? options.renderMentions(html) : html);
  }

  const deferredImageObservers = new WeakMap();

  function loadDeferredImage(image) {
    if (!image || image.hasAttribute("src")) return;
    const source = String(image.dataset.portalDeferredImage || "").trim();
    if (source) image.setAttribute("src", source);
  }

  function bindDeferredImages(root) {
    const images = [...(root?.querySelectorAll?.("img[data-portal-deferred-image]:not([src])") || [])];
    deferredImageObservers.get(root)?.disconnect?.();
    if (!images.length) {
      deferredImageObservers.delete(root);
      return;
    }
    if (!("IntersectionObserver" in window)) {
      images.forEach(loadDeferredImage);
      return;
    }
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        loadDeferredImage(entry.target);
        observer.unobserve(entry.target);
      });
    }, {
      root: root?.nodeType === 1 ? root : null,
      rootMargin: "320px 0px",
    });
    deferredImageObservers.set(root, observer);
    images.forEach(image => observer.observe(image));
  }

  function bindAttachmentRefs(root, options = {}) {
    bindDeferredImages(root);
    root?.querySelectorAll("[data-message-attachment-path]").forEach((btn) => {
      if (btn.dataset.portalAttachmentBound === "1") return;
      btn.dataset.portalAttachmentBound = "1";
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const kind = btn.dataset.messageAttachmentKind || "";
        const path = btn.dataset.messageAttachmentPath || "";
        if (options.onOpen) {
          options.onOpen(kind, path, btn);
          return;
        }
        const previewUrl = kind === "image" && options.previewUrlForPath ? options.previewUrlForPath(path) : "";
        if (previewUrl) openImagePreview(previewUrl, path);
        else void window.PortalUI.copyText(path, window.PortalI18n.t("附件路径"));
      });
    });
    bindImagePreview(root, options);
  }

  function openImagePreview(src, title = "", options = {}) {
    const show = editor => editor.open(src, title || "image", {
      ...options,
      editable: options.editable === true,
      onSave: options.onSave,
    });
    if (window.PortalImageEditor) return show(window.PortalImageEditor);
    return requireImageEditor().then(show).catch(error => window.PortalUI.toast(error.message, "error"));
  }

  function closeImagePreview() {
    return window.PortalImageEditor?.close(document.getElementById("portalImageEditorLightbox"));
  }

  function bindLinkPreview(root, options = {}) {
    window.PortalFileLinks?.enhance?.(root, {
      basePath: options.basePath || "",
      source: "chat",
      onOpen: request => {
        if (typeof options.onLinkPreview === "function") return options.onLinkPreview(request);
        return requestWorkspacePreview(request);
      },
    });
    root?.querySelectorAll("[data-portal-preview-link]").forEach(node => {
      if (
        node.dataset.portalPreviewLinkBound === "1"
        || node.dataset.portalFileLinkBound === "1"
        || node.dataset.portalResourceLinkBound === "1"
      ) return;
      node.dataset.portalPreviewLinkBound = "1";
      node.addEventListener("contextmenu", event => {
        const href = node.dataset.portalPreviewLink || node.getAttribute("href") || "";
        if (/^(?:https?:|#)/i.test(href)) return;
        window.PortalPreview?.openFileMenu?.({
          event, trigger: node,
          request: { href, path: href, title: node.textContent?.trim() || "", node, source: "chat", target: root },
          onOpen: request => typeof options.onLinkPreview === "function"
            ? options.onLinkPreview(request) : requestWorkspacePreview(request),
        });
      });
      node.addEventListener("click", event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        event.stopPropagation();
        const href = node.dataset.portalPreviewLink || node.getAttribute("href") || "";
        const title = node.textContent?.trim() || node.getAttribute("title") || window.PortalI18n.t("链接预览");
        const request = { href, path: href, title, node, source: "chat", target: root };
        if (typeof options.onLinkPreview === "function") options.onLinkPreview(request);
        else requestWorkspacePreview(request);
      });
    });
  }

  function bindImagePreview(root, options = {}) {
    if (window.PortalImageEditor) return window.PortalImageEditor.bind(root, options);
    return requireImageEditor().then(editor => editor.bind(root, options))
      .catch(error => window.PortalUI.toast(error.message, "error"));
  }

  // A draft restored after a reload keeps its upload but not the page's own
  // blob preview, so an uploaded attachment falls back to the upload itself.
  function attachmentPreviewUrl(item) {
    return String(item?.preview_url || item?.data_url || item?.url || "").trim() || mediaPartPath({ upload_id: item?.upload_id });
  }

  function attachmentKind(item) {
    const kind = String(item?.kind || "").toLowerCase();
    if (kind === "file") return "file";
    const type = String(item?.content_type || item?.mime || "").toLowerCase();
    return type && !type.startsWith("image/") ? "file" : "image";
  }

  function normalizeComposerAttachment(item, index = 0) {
    if (!item || typeof item !== "object") return null;
    const fileId = String(item.file_id || "").trim();
    const path = String(item.path || item.url || "").trim();
    const dataUrl = String(item.data_url || "").trim();
    const previewUrl = String(item.preview_url || dataUrl || item.url || "").trim();
    const contentType = String(item.content_type || item.mime || "").trim();
    const kind = String(item.kind || (contentType && !contentType.toLowerCase().startsWith("image/") ? "file" : "image")).toLowerCase() === "file" ? "file" : "image";
    const id = String(item.id || fileId || path || previewUrl || `attachment-${index}`).trim();
    if (!id) return null;
    return {
      ...item,
      id,
      file_id: fileId,
      kind,
      name: String(item.name || item.filename || (kind === "image" ? window.PortalI18n.t("pasted image") : "attachment")),
      path,
      preview_url: previewUrl,
      data_url: dataUrl,
      mime: String(item.mime || contentType),
      content_type: contentType,
      size: Number(item.size || item.size_bytes || 0),
      size_bytes: Number(item.size_bytes || item.size || 0),
      uploading: !!item.uploading,
      progress: Math.max(0, Math.min(100, Number(item.progress || 0))),
      error: String(item.error || ""),
    };
  }

  function normalizeComposerAttachments(items) {
    return (Array.isArray(items) ? items : [])
      .map((item, index) => normalizeComposerAttachment(item, index))
      .filter(Boolean);
  }

  function createConversationSelectionReference(selection = {}) {
    const text = String(selection.text || "").trim();
    if (!text) return null;
    const title = String(selection.title || window.PortalI18n.t("对话选段")).trim() || window.PortalI18n.t("对话选段");
    const path = String(selection.path || title).trim() || title;
    return normalizeComposerAttachment({
      id: `conversation-reference:${Date.now()}:${Math.random().toString(16).slice(2)}`,
      kind: "file",
      name: title,
      path,
      content_type: "text/x-portal-preview-reference",
      reference_text: text,
      preview_selection: {
        title,
        path,
        text,
        line_start: Number(selection.line_start || 0),
        line_end: Number(selection.line_end || selection.line_start || 0),
        source_message_ids: Array.isArray(selection.source_message_ids)
          ? selection.source_message_ids.map(value => String(value || "").trim()).filter(Boolean)
          : [],
      },
    });
  }

  function inputMediaPayload(items) {
    return normalizeComposerAttachments(items)
      .filter(item => item.upload_id && !item.uploading && !item.error)
      .map(item => ({
        media_type: attachmentKind(item),
        upload_id: String(item.upload_id),
        mime_type: String(item.content_type || item.mime || ""),
        name: String(item.name || (attachmentKind(item) === "image" ? "image" : "attachment")),
      }));
  }

  function mediaOnlyCaption(parts) {
    return parts.filter(part => part.type === "image" || part.type === "file")
      .map(part => window.PortalI18n.text`${part.type === "image" ? window.PortalI18n.t("图片") : window.PortalI18n.t("文件")}附件：${part.name || part.type}`).join("；");
  }

  function composerPayloadContentParts(payload = {}) {
    const parts = Array.isArray(payload.content_parts)
      ? payload.content_parts.map(part => ({ ...part }))
      : [
        { type: "text", text: String(payload.text || payload.plainText || "") },
        ...inputMediaPayload(payload.attachments).map(media => ({ ...media, type: media.media_type })),
      ];
    if (!parts.some(part => part.type === "text" && String(part.text || "").trim())) {
      const text = mediaOnlyCaption(parts);
      if (text) parts.unshift({ type: "text", text });
    }
    return parts.filter(part => part.type !== "text" || String(part.text || ""));
  }

  function composerPreviewReference(item) {
    if (!item || typeof item !== "object") return null;
    const isPreviewRef = item.preview_selection
      || item.reference_text
      || String(item.content_type || "").toLowerCase() === "text/x-portal-preview-reference";
    if (!isPreviewRef) return null;
    const ref = item.preview_selection || item.reference || {};
    const path = String(ref.path || item.path || item.url || "").trim();
    const text = String(ref.text || item.reference_text || "").trim();
    const start = Number(ref.line_start || ref.lineStart || item.line_start || item.lineStart || 0);
    const end = Number(ref.line_end || ref.lineEnd || item.line_end || item.lineEnd || start || 0);
    return {
      path,
      text,
      title: String(ref.title || item.name || path || window.PortalI18n.t("预览")).trim(),
      start,
      end,
      sourceMessageIds: Array.isArray(ref.source_message_ids)
        ? ref.source_message_ids.map(value => String(value || "").trim()).filter(Boolean)
        : [],
    };
  }

  function composerReferenceLineLabel(ref) {
    if (!ref?.start) return "";
    return ref.end && ref.end !== ref.start ? `${ref.start}-${ref.end}` : String(ref.start);
  }

  function composerReferenceDisplayName(ref, item) {
    if (ref?.sourceMessageIds?.length && ref.title) return ref.title;
    const raw = String(ref?.path || ref?.title || item?.name || "预览").trim();
    const base = raw.split("/").filter(Boolean).pop() || raw || "预览";
    return base.replace(/^引用\s+/, "").replace(/#L\d+(?:-L\d+)?$/, "");
  }

  function composerAttachmentRefNode(item, options = {}) {
    const id = String(item?.id || "");
    const kind = attachmentKind(item);
    const preview = attachmentPreviewUrl(item);
    const previewRef = composerPreviewReference(item);
    const span = document.createElement("span");
    span.className = `composerImageRef ${item?.uploading ? "uploading" : ""} ${item?.error ? "error" : ""}`;
    span.contentEditable = "false";
    span.draggable = true;
    span.dataset.portalComposerAttachmentToken = id;
    span.dataset.composerAttachmentKind = kind;
    if (item?.path) span.dataset.portalAttachmentPath = item.path;
    if (preview && kind === "image") span.dataset.portalPreviewImage = preview;
    span.title = item?.error || item?.path || item?.name || kind;
    let label = item?.name || kind;
    if (previewRef) {
      const lineLabel = composerReferenceLineLabel(previewRef);
      span.title = [previewRef.path || previewRef.title, lineLabel ? window.PortalI18n.text`L${lineLabel}` : ""].filter(Boolean).join(" ");
      label = `${composerReferenceDisplayName(previewRef, item)}${lineLabel ? ` L${lineLabel}` : ""}`;
    }
    const media = preview && kind === "image"
      ? window.PortalI18n.html`<img class="${options.thumbClass || "composerImageRefThumb"}" src="${escapeHtml(preview)}" alt="" />`
      : window.PortalI18n.html`<span class="${options.iconClass || "composerFileRefIcon"}">${kind === "file" ? "F" : "I"}</span>`;
    span.innerHTML = window.PortalI18n.html`${media}<span class="${options.labelClass || "composerImageRefLabel"}">${escapeHtml(label)}</span><button class="${options.removeClass || "composerImageRefRemove"}" type="button" data-remove-portal-composer-attachment="${escapeHtml(id)}" aria-label="移除附件" data-portal-icon="close"></button>`;
    return span;
  }

  function composerAttachmentPlaceholder(itemOrKind, id = "") {
    const kind = typeof itemOrKind === "string" ? itemOrKind : String(itemOrKind?.kind || "image");
    const safeKind = kind === "file" ? "file" : "image";
    const attachmentId = typeof itemOrKind === "string" ? id : (itemOrKind?.id || itemOrKind?.file_id || "");
    return `[[portal-${safeKind}:${String(attachmentId || "").replace(/\]/g, "")}]]`;
  }

  function composerReferenceText(item) {
    const ref = item?.preview_selection || item?.reference || null;
    const text = String(ref?.text || item?.reference_text || "").trim();
    if (!ref && !text) return "";
    const path = String(ref?.path || item?.path || item?.url || item?.name || "").trim();
    if (!text && !path) return "";
    const start = Number(ref?.line_start || ref?.lineStart || item?.line_start || item?.lineStart || 0);
    const end = Number(ref?.line_end || ref?.lineEnd || item?.line_end || item?.lineEnd || start || 0);
    const linePart = start ? `#L${start}${end && end !== start ? `-L${end}` : ""}` : "";
    const header = `[引用: ${path || "预览"}${linePart}]`;
    if (!text) return header;
    const quote = text.split(/\r?\n/).map(line => `> ${line}`).join("\n");
    return `${header}\n${quote}`;
  }

  function composerAttachmentTokenOrder(input) {
    const ids = [];
    input?.querySelectorAll?.("[data-portal-composer-attachment-token]")?.forEach(node => {
      const id = String(node.dataset.portalComposerAttachmentToken || "").trim();
      if (id) ids.push(id);
    });
    return ids;
  }

  function insertComposerAttachmentRef(input, item, options = {}) {
    if (!input || !item?.id) return "none";
    input.focus?.();
    const node = composerAttachmentRefNode(item, options);
    if (!options.forceDom) {
      try {
        if (document.execCommand?.("insertHTML", false, `${node.outerHTML} `)) {
          bindImagePreview(input, options);
          return "native";
        }
      } catch {}
    }
    const spacer = document.createTextNode(" ");
    const selection = window.getSelection?.();
    let range = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
    if (!range || !input.contains(range.commonAncestorContainer)) {
      range = document.createRange();
      range.selectNodeContents(input);
      range.collapse(false);
    }
    range.deleteContents();
    range.insertNode(spacer);
    range.insertNode(node);
    range.setStartAfter(spacer);
    range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
    input.dispatchEvent?.(new Event("input", { bubbles: true }));
    bindImagePreview(input, options);
    return "manual";
  }

  function removeComposerAttachmentRef(input, id) {
    if (!input || !id) return;
    input.querySelectorAll(`[data-portal-composer-attachment-token="${CSS.escape(String(id))}"]`).forEach(node => node.remove());
    input.dispatchEvent?.(new Event("input", { bubbles: true }));
  }

  function moveComposerAttachmentRef(input, token, event) {
    const id = String(token || "").trim();
    if (!input || !id) return false;
    const node = input.querySelector(`[data-portal-composer-attachment-token="${CSS.escape(id)}"]`);
    if (!node) return false;
    const range = inputRangeFromPoint(event, input);
    if (!range || !input.contains(range.commonAncestorContainer) || node.contains(range.startContainer)) return false;
    event?.preventDefault?.();
    const spacer = document.createTextNode(" ");
    node.remove();
    range.insertNode(spacer);
    range.insertNode(node);
    range.setStartAfter(spacer);
    range.collapse(true);
    const selection = window.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
    input.dispatchEvent?.(new Event("input", { bubbles: true }));
    bindImagePreview(input);
    return true;
  }

  function refreshComposerAttachmentRefs(input, items, options = {}) {
    if (!input) return;
    const byId = new Map((Array.isArray(items) ? items : []).map(item => [String(item?.id || ""), item]));
    input.querySelectorAll("[data-portal-composer-attachment-token]").forEach(node => {
      const id = String(node.dataset.portalComposerAttachmentToken || "");
      const item = byId.get(id);
      if (!item) {
        node.remove();
        return;
      }
      node.replaceWith(composerAttachmentRefNode(item, options));
    });
    bindImagePreview(input, options);
  }

  function ensureComposerAttachmentRefs(input, items, options = {}) {
    if (!input) return;
    const existing = new Set();
    input.querySelectorAll("[data-portal-composer-attachment-token]").forEach(node => {
      const id = String(node.dataset.portalComposerAttachmentToken || "");
      if (id) existing.add(id);
    });
    let inserted = false;
    for (const item of Array.isArray(items) ? items : []) {
      const id = String(item?.id || "");
      if (!id || existing.has(id)) continue;
      if (input.childNodes.length) input.appendChild(document.createTextNode(" "));
      input.appendChild(composerAttachmentRefNode(item, options));
      input.appendChild(document.createTextNode(" "));
      existing.add(id);
      inserted = true;
    }
    if (inserted) {
      bindImagePreview(input, options);
      if (options.dispatchInput) input.dispatchEvent?.(new Event("input", { bubbles: true }));
    }
  }

  function syncComposerAttachmentRefs(input, items, options = {}) {
    refreshComposerAttachmentRefs(input, items, options);
    ensureComposerAttachmentRefs(input, items, options);
  }

  function serializeComposerInput(input, items, options = {}) {
    const itemMap = new Map((Array.isArray(items) ? items : []).map(item => [String(item?.id || ""), item]));
    const mode = options.mode || "final";
    const isBlockNode = (node) => node?.nodeType === Node.ELEMENT_NODE && (node.tagName === "DIV" || node.tagName === "P");
    const appendSerializedChild = (text, child) => {
      if (isBlockNode(child) && text && !text.endsWith("\n")) text += "\n";
      return text + walk(child);
    };
    const walk = (node) => {
      if (!node) return "";
      if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
      if (node.nodeType !== Node.ELEMENT_NODE) return "";
      const el = node;
      if (el.dataset?.mentionValue) return el.dataset.mentionValue;
      const token = el.dataset?.portalComposerAttachmentToken || "";
      if (token) {
        if (mode === "plain") return "";
        const item = itemMap.get(String(token));
        const kind = attachmentKind(item || { kind: el.dataset?.composerAttachmentKind || "image" });
        if (mode === "draft") return composerAttachmentPlaceholder(kind, token);
        if (!item || item.uploading || item.error) return "";
        const referenceText = composerReferenceText(item);
        if (referenceText) return referenceText;
        const path = String(item?.path || item?.url || "").trim();
        return path ? `[${kind}: ${path}]` : "";
      }
      if (el.tagName === "BR") return "\n";
      let text = "";
      el.childNodes.forEach(child => { text = appendSerializedChild(text, child); });
      if (el.tagName === "DIV" || el.tagName === "P") text += "\n";
      return text;
    };
    if (!input) return "";
    if ("value" in input) return String(input.value || "").trim();
    let text = "";
    input.childNodes.forEach(child => { text = appendSerializedChild(text, child); });
    const normalized = text.replace(/\u00a0/g, " ");
    return mode === "draft" ? normalized : normalized.trim();
  }

  function serializeComposerContentParts(input, items) {
    const text = serializeComposerInput(input, items, { mode: "draft" });
    const byId = new Map(normalizeComposerAttachments(items).map(item => [item.id, item]));
    const parts = [];
    const appendText = value => {
      if (!value) return;
      const previous = parts[parts.length - 1];
      if (previous?.type === "text") previous.text += value;
      else parts.push({ type: "text", text: value });
    };
    let cursor = 0;
    COMPOSER_DRAFT_ATTACHMENT_RE.lastIndex = 0;
    for (const match of text.matchAll(COMPOSER_DRAFT_ATTACHMENT_RE)) {
      appendText(text.slice(cursor, match.index));
      const item = byId.get(match[2]);
      if (item && !item.uploading && !item.error) {
        const reference = composerReferenceText(item);
        const media = reference ? null : inputMediaPayload([item])[0];
        if (media) parts.push({ ...media, type: media.media_type });
        else appendText(reference || (item.path ? `[${attachmentKind(item)}: ${item.path}]` : ""));
      }
      cursor = match.index + match[0].length;
    }
    appendText(text.slice(cursor));
    return parts;
  }

  function renderComposerAttachmentTray(tray, items, options = {}) {
    if (!tray) return;
    const attachments = (Array.isArray(items) ? items : []).filter(item => !composerPreviewReference(item));
    const chipHtml = (item) => {
      const id = String(item?.id || "");
      const kind = attachmentKind(item);
      const preview = attachmentPreviewUrl(item);
      const progress = Math.max(0, Math.min(100, Number(item?.progress || 0)));
      const status = item?.error || (item?.uploading ? `上传中 ${Math.round(progress)}%` : (item?.path || item?.name || ""));
      const media = preview && kind === "image"
        ? window.PortalI18n.html`<button class="composerImageChipPreview" type="button" data-portal-preview-image="${escapeHtml(preview)}" title="${escapeHtml(item?.name || "image")}" aria-label="放大图片"><img class="composerImageThumb" src="${escapeHtml(preview)}" alt="" /></button>`
        : window.PortalI18n.html`<span class="composerFileThumb">${kind === "file" ? window.PortalI18n.t("文件") : window.PortalI18n.t("图片")}</span>`;
      return window.PortalI18n.html`<div class="composerImageChip ${item?.uploading ? "uploading" : ""} ${item?.error ? "error" : ""}" data-portal-composer-attachment-id="${escapeHtml(id)}" ${item?.path ? `data-portal-attachment-path="${escapeHtml(item.path)}"` : ""} title="${escapeHtml(status)}">
        ${media}
        <div>
          <div class="composerImageName">${escapeHtml(item?.name || kind)}</div>
          <div class="composerImagePath">${escapeHtml(status)}</div>
        </div>
        <span class="composerImageChipActions">${item?.error ? window.PortalI18n.html`<button class="composerImageRetry" type="button" data-retry-portal-composer-attachment="${escapeHtml(id)}" aria-label="重新上传附件" title="重新上传" data-portal-icon="refresh"></button>` : ""}<button class="composerImageRemove" type="button" data-remove-portal-composer-attachment="${escapeHtml(id)}" aria-label="移除附件" title="移除" data-portal-icon="close"></button></span>
        ${item?.uploading ? window.PortalI18n.html`<span class="composerUploadProgress" aria-label="${escapeHtml(status)}"><span style="width: ${progress}%"></span></span>` : ""}
      </div>`;
    };
    tray.innerHTML = attachments.map(chipHtml).join("");
    bindImagePreview(tray, options);
  }

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

  function normalizeSlashCommands(commands) {
    // The session gives the commands it answers; a session that gives none has no menu.
    const raw = Array.isArray(commands) ? commands : [];
    const seen = new Set();
    return raw.map(item => {
      const command = String(item?.command || item?.value || item?.name || "").trim();
      if (!command || !command.startsWith("/") || seen.has(command)) return null;
      seen.add(command);
      return {
        command,
        title: String(item?.title || item?.label || command.slice(1) || command).trim(),
        description: String(item?.description || item?.hint || "").trim(),
      };
    }).filter(Boolean);
  }

  function slashCommandQuery(input, attachments = []) {
    const text = serializeComposerInput(input, attachments, { mode: "plain" });
    if (!String(text || "").startsWith("/")) return null;
    if (String(text || "").includes("\n")) return null;
    if (/\s/.test(String(text || "").slice(1))) return null;
    return String(text || "").trim().toLowerCase();
  }

  function slashCommandMatches(input, attachments = [], commands = []) {
    const query = slashCommandQuery(input, attachments);
    if (query === null) return [];
    const normalized = normalizeSlashCommands(commands);
    const exact = normalized.filter(item => item.command.toLowerCase() === query);
    const prefix = normalized.filter(item => item.command.toLowerCase().startsWith(query));
    const title = normalized.filter(item => (
      query.length > 1
      && !item.command.toLowerCase().startsWith(query)
      && item.title.toLowerCase().includes(query.slice(1))
    ));
    const merged = [...prefix, ...title];
    return merged.length ? merged.slice(0, 16) : exact.slice(0, 16);
  }

  function slashCommandMenuHtml(items, activeIndex = 0) {
    if (!items.length) return "";
    return window.PortalI18n.html`<div class="composerSlashCommandMenu" role="listbox" aria-label="Portal 命令">
      <div class="composerSlashCommandHeader">Portal 命令</div>
      ${items.map((item, index) => window.PortalI18n.html`<button class="composerSlashCommandItem ${index === activeIndex ? "active" : ""}" type="button" role="option" aria-selected="${index === activeIndex ? "true" : "false"}" data-portal-slash-command="${escapeHtml(item.command)}">
        <span class="composerSlashCommandName">${escapeHtml(item.command)}</span>
        <span class="composerSlashCommandBody">
          <span class="composerSlashCommandTitle">${escapeHtml(item.title)}</span>
          ${item.description ? window.PortalI18n.html`<span class="composerSlashCommandDescription">${escapeHtml(item.description)}</span>` : ""}
        </span>
      </button>`).join("")}
    </div>`;
  }

  // A workspace that offers @ candidates (options.mentionCandidates) lists
  // them while the word before the caret starts with "@"; choosing one
  // replaces that word (decision_a6bebf4ac283).
  const MENTION_TOKEN_RE = /(?:^|[\s(（，,。:：])@([^\s@]{0,40})$/u;

  function composerMentionToken(input) {
    const selection = window.getSelection?.();
    if (!input || !selection || !selection.rangeCount || !selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    const node = range.startContainer;
    if (node?.nodeType !== Node.TEXT_NODE || !input.contains(node)) return null;
    const match = MENTION_TOKEN_RE.exec(String(node.textContent || "").slice(0, range.startOffset));
    if (!match) return null;
    return { node, start: range.startOffset - match[1].length - 1, end: range.startOffset, query: match[1] };
  }

  // Items: {value, title, label, description, group, user_id, disabled, disabled_reason}.
  // A disabled item shows why it cannot be chosen.
  function mentionMenuHtml(items, activeIndex = 0, title = "", status = "") {
    if (!items.length && !status) return "";
    let group = null;
    return window.PortalI18n.html`<div class="composerSlashCommandMenu composerMentionMenu" role="listbox" aria-label="${escapeHtml(title || window.PortalI18n.t("提及"))}">
      ${items.map((item, index) => {
        const header = item.group !== group || index === 0
          ? window.PortalI18n.html`<div class="composerSlashCommandHeader">${escapeHtml(item.group || title || window.PortalI18n.t("提及"))}</div>`
          : "";
        group = item.group;
        const description = item.disabled ? item.disabled_reason || item.description : item.description;
        const name = String(item.title || item.value);
        const label = String(item.label || item.value);
        const secondary = label.toLocaleLowerCase() === name.toLocaleLowerCase() ? "" : label;
        const tooltip = [name, secondary, description].filter(Boolean).join(" · ");
        // Keep this markup stable as portraits load: arrow navigation must not
        // rebuild the menu. The shared avatar binder fills these placeholders.
        const avatar = item.user_id ? messageAvatarPlaceholderHtml(item.user_id, name)
          : item.kind === "user" ? window.PortalI18n.html`<span class="composerMentionAvatar" aria-hidden="true">${escapeHtml(name.slice(0, 1))}</span>` : "";
        return window.PortalI18n.html`${header}<button class="composerSlashCommandItem composerMentionItem ${index === activeIndex && !item.disabled ? "active" : ""}" type="button" role="option" aria-selected="${index === activeIndex && !item.disabled ? "true" : "false"}" title="${escapeHtml(tooltip)}"${item.disabled ? ' disabled aria-disabled="true"' : ""} data-portal-mention-index="${index}" data-portal-mention="${escapeHtml(item.value)}">
          ${avatar}<span class="composerSlashCommandName">${escapeHtml(name)}</span>
          ${secondary ? window.PortalI18n.html`<span class="composerSlashCommandTitle">${escapeHtml(secondary)}</span>` : ""}
          ${description ? window.PortalI18n.html`<span class="composerSlashCommandDescription">${escapeHtml(description)}</span>` : ""}
        </button>`;
      }).join("")}
      ${status ? window.PortalI18n.html`<div class="composerMentionStatus" role="status">${escapeHtml(status)}</div>` : ""}
    </div>`;
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

  function bindComposerAttachmentActions(root, options = {}) {
    if (!root || root.dataset.portalComposerAttachmentActionsBound === "1") return;
    root.dataset.portalComposerAttachmentActionsBound = "1";
    root.addEventListener("click", event => {
      const retry = event.target.closest("[data-retry-portal-composer-attachment]");
      if (retry) {
        event.preventDefault();
        event.stopPropagation();
        options.onRetry?.(retry.dataset.retryPortalComposerAttachment || "");
        return;
      }
      const btn = event.target.closest("[data-remove-portal-composer-attachment]");
      if (!btn) return;
      event.preventDefault();
      event.stopPropagation();
      options.onRemove?.(btn.dataset.removePortalComposerAttachment || "");
    });
  }

  function inputText(input) {
    if (!input) return "";
    if ("value" in input) return String(input.value || "").replace(/\u00a0/g, " ").trim();
    return String(input.innerText || input.textContent || "").replace(/\u00a0/g, " ").trim();
  }

  function inputMutationValue(input) {
    if (!input) return "";
    return "value" in input ? String(input.value || "") : String(input.innerHTML || "");
  }

  function setInputText(input, value, { focus = true } = {}) {
    if (!input) return;
    const text = String(value || "");
    if ("value" in input) {
      input.value = text;
      if (!focus) return;
      input.focus?.();
      const end = input.value.length;
      try {
        input.selectionStart = end;
        input.selectionEnd = end;
      } catch {}
      return;
    }
    input.textContent = text;
    if (!focus) return;
    input.focus?.();
    const range = document.createRange();
    range.selectNodeContents(input);
    range.collapse(false);
    const selection = window.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  function setComposerDraftInput(input, value, items, options = {}) {
    if (!input) return;
    const text = String(value || "");
    if ("value" in input) {
      input.value = text;
      return;
    }
    const attachments = normalizeComposerAttachments(items);
    const byId = new Map();
    attachments.forEach((item) => {
      const ids = [item.id, item.file_id, item.path, item.preview_url].map(value => String(value || "").trim()).filter(Boolean);
      ids.forEach(id => byId.set(id, item));
    });
    input.innerHTML = "";
    let cursor = 0;
    COMPOSER_DRAFT_ATTACHMENT_RE.lastIndex = 0;
    for (const match of text.matchAll(COMPOSER_DRAFT_ATTACHMENT_RE)) {
      const before = text.slice(cursor, match.index);
      if (before) input.appendChild(document.createTextNode(before));
      const item = byId.get(String(match[2] || ""));
      if (item) input.appendChild(composerAttachmentRefNode(item, options));
      else input.appendChild(document.createTextNode(match[0]));
      cursor = match.index + match[0].length;
    }
    const rest = text.slice(cursor);
    if (rest) input.appendChild(document.createTextNode(rest));
    // Older drafts can store attachments separately from their text. Restore
    // those references here instead of relying on a later composer render.
    ensureComposerAttachmentRefs(input, attachments, options);
    // The domain supplies identity labels; the composer owns editing and stable serialization.
    if (options.renderMentions) {
      input.innerHTML = options.renderMentions(input.innerHTML);
      input.querySelectorAll("[data-mention-value]").forEach(node => { node.contentEditable = "false"; });
      bindResourceChips(input, options);
    }
    if (options.focus !== false) {
      input.focus?.();
      const range = document.createRange();
      range.selectNodeContents(input);
      range.collapse(false);
      const selection = window.getSelection?.();
      selection?.removeAllRanges();
      selection?.addRange(range);
    }
    bindImagePreview(input, options);
  }

  function nodePathWithin(root, node) {
    if (!root || !node) return null;
    if (node === root) return [];
    const path = [];
    let current = node;
    while (current && current !== root) {
      const parent = current.parentNode;
      if (!parent) return null;
      path.unshift(Array.prototype.indexOf.call(parent.childNodes, current));
      current = parent;
    }
    return current === root ? path : null;
  }

  function nodeFromPath(root, path) {
    let current = root;
    for (const index of Array.isArray(path) ? path : []) {
      const next = current?.childNodes?.[Number(index)];
      if (!next) return null;
      current = next;
    }
    return current || null;
  }

  function composerSelectionSnapshot(input) {
    if (!input) return null;
    if ("selectionStart" in input && "selectionEnd" in input) {
      return {
        mode: "value",
        start: Number(input.selectionStart || 0),
        end: Number(input.selectionEnd || input.selectionStart || 0),
      };
    }
    const selection = window.getSelection?.();
    if (!selection || selection.rangeCount !== 1) return null;
    const range = selection.getRangeAt(0);
    if (!input.contains(range.startContainer) && input !== range.startContainer) return null;
    if (!input.contains(range.endContainer) && input !== range.endContainer) return null;
    const startPath = nodePathWithin(input, range.startContainer);
    const endPath = nodePathWithin(input, range.endContainer);
    if (!startPath || !endPath) return null;
    return {
      mode: "dom",
      startPath,
      startOffset: range.startOffset,
      endPath,
      endOffset: range.endOffset,
    };
  }

  function placeInputCaretAtEnd(input) {
    if (!input) return;
    input.focus?.();
    if ("selectionStart" in input && "selectionEnd" in input) {
      const end = String(input.value || "").length;
      try {
        input.selectionStart = end;
        input.selectionEnd = end;
      } catch {}
      return;
    }
    const range = document.createRange();
    range.selectNodeContents(input);
    range.collapse(false);
    const selection = window.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  function inputRangeFromPoint(event, input) {
    if (!event || !input) return null;
    if (document.caretRangeFromPoint) return document.caretRangeFromPoint(event.clientX, event.clientY);
    if (document.caretPositionFromPoint) {
      const position = document.caretPositionFromPoint(event.clientX, event.clientY);
      if (!position) return null;
      const range = document.createRange();
      range.setStart(position.offsetNode, position.offset);
      range.collapse(true);
      return range;
    }
    return null;
  }

  function placeInputCaretFromPoint(input, event) {
    if (!input || "selectionStart" in input) {
      input?.focus?.();
      return false;
    }
    const range = inputRangeFromPoint(event, input);
    if (!range || !input.contains(range.commonAncestorContainer)) {
      input.focus?.();
      return false;
    }
    input.focus?.();
    const selection = window.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
    return true;
  }

  function restoreComposerSelection(input, snapshot) {
    if (!input) return false;
    input.focus?.();
    if (snapshot?.mode === "value" && "selectionStart" in input && "selectionEnd" in input) {
      const length = String(input.value || "").length;
      const start = Math.max(0, Math.min(length, Number(snapshot.start || 0)));
      const end = Math.max(start, Math.min(length, Number(snapshot.end || start)));
      try {
        input.selectionStart = start;
        input.selectionEnd = end;
        return true;
      } catch {}
    }
    if (snapshot?.mode === "dom") {
      const startNode = nodeFromPath(input, snapshot.startPath);
      const endNode = nodeFromPath(input, snapshot.endPath);
      if (startNode && endNode) {
        try {
          const range = document.createRange();
          range.setStart(startNode, Math.min(Number(snapshot.startOffset || 0), startNode.length ?? startNode.childNodes?.length ?? 0));
          range.setEnd(endNode, Math.min(Number(snapshot.endOffset || 0), endNode.length ?? endNode.childNodes?.length ?? 0));
          const selection = window.getSelection?.();
          selection?.removeAllRanges();
          selection?.addRange(range);
          return true;
        } catch {}
      }
    }
    placeInputCaretAtEnd(input);
    return false;
  }

  function inputCaretLineState(input) {
    if (!input) return { selectable: false };
    if ("selectionStart" in input && "selectionEnd" in input) {
      const start = Number(input.selectionStart || 0);
      const end = Number(input.selectionEnd || start);
      if (start !== end) return { selectable: false };
      const text = String(input.value || "");
      return {
        selectable: true,
        atFirstLine: !text.slice(0, start).includes("\n"),
        atLastLine: !text.slice(end).includes("\n"),
      };
    }
    const selection = window.getSelection?.();
    if (document.activeElement === input && !String(input.innerText || input.textContent || "").trim()) {
      return { selectable: true, atFirstLine: true, atLastLine: true };
    }
    if (!selection || selection.rangeCount !== 1 || !selection.isCollapsed) return { selectable: false };
    const range = selection.getRangeAt(0);
    if (!input.contains(range.startContainer)) return { selectable: false };
    // Wrapped contenteditable lines have no newline, so compare rendered line positions.
    const style = window.getComputedStyle?.(input);
    const fontSize = Number.parseFloat(style?.fontSize || "") || 13;
    const lineHeight = Number.parseFloat(style?.lineHeight || "") || fontSize * 1.4;
    const usableRects = targetRange => Array.from(targetRange.getClientRects?.() || []).filter(rect => (
      Number.isFinite(rect.top)
      && Number.isFinite(rect.bottom)
      && (rect.width > 0 || rect.height > 0)
      && rect.height <= lineHeight * 1.75
    ));
    const caretRects = usableRects(range);
    const caretRect = caretRects[caretRects.length - 1];
    if (!caretRect) {
      const content = document.createRange();
      content.selectNodeContents(input);
      const lineCenters = usableRects(content)
        .map(rect => (rect.top + rect.bottom) / 2)
        .sort((left, right) => left - right)
        .filter((center, index, centers) => index === 0 || center - centers[index - 1] > lineHeight * 0.45);
      if (lineCenters.length <= 1) return { selectable: true, atFirstLine: true, atLastLine: true };
      const rootOffset = range.startContainer === input ? Number(range.startOffset || 0) : null;
      return {
        selectable: true,
        atFirstLine: rootOffset === 0,
        atLastLine: rootOffset === input.childNodes.length,
        positionUnknown: true,
      };
    }
    const before = range.cloneRange();
    before.selectNodeContents(input);
    before.setEnd(range.startContainer, range.startOffset);
    const after = range.cloneRange();
    after.selectNodeContents(input);
    after.setStart(range.endContainer, range.endOffset);
    const caretCenter = (caretRect.top + caretRect.bottom) / 2;
    const differentLineThreshold = Math.max(4, lineHeight * 0.45);
    return {
      selectable: true,
      atFirstLine: !usableRects(before).some(rect => (
        (rect.top + rect.bottom) / 2 < caretCenter - differentLineThreshold
      )),
      atLastLine: !usableRects(after).some(rect => (
        (rect.top + rect.bottom) / 2 > caretCenter + differentLineThreshold
      )),
    };
  }

  function isComposerImeActive(input, event = null) {
    return Boolean(
      event?.isComposing
      || event?.keyCode === 229
      || input?.dataset?.portalWorkspaceComposerComposing === "1"
    );
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
    const suspendedValues = new Map();
    let timer = null;
    let suspensionDepth = 0;
    const armTimer = () => {
      if (timer || suspensionDepth || !pending.size) return;
      timer = setTimeout(() => flush(), Math.max(0, Number(options.delay || 180)));
    };
    const read = scope => {
      const pendingKey = String(scope || "");
      if (suspensionDepth) {
        return normalizeWorkspaceDraft(suspendedValues.get(pendingKey) || {});
      }
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
      if (suspensionDepth) {
        suspendedValues.set(pendingKey, value);
        return;
      }
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
      if (suspensionDepth) return;
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
      if (suspensionDepth) {
        suspendedValues.set(key, normalizeWorkspaceDraft(draft));
        return;
      }
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
      if (suspensionDepth) {
        suspendedValues.set(key, normalizeWorkspaceDraft({}));
        return;
      }
      pending.delete(key);
      const storage = storageKey(key);
      if (!storage) return;
      pendingWorkspaceDrafts.delete(storage);
      window.PortalState.storage.remove(storage);
    };
    const suspend = (seed = []) => {
      suspensionDepth += 1;
      if (suspensionDepth > 1) return suspensionDepth;
      if (timer) clearTimeout(timer);
      timer = null;
      suspendedValues.clear();
      for (const [scope, draft] of seed || []) {
        suspendedValues.set(String(scope || ""), normalizeWorkspaceDraft(draft));
      }
      return suspensionDepth;
    };
    const resume = () => {
      if (!suspensionDepth) return false;
      suspensionDepth -= 1;
      if (suspensionDepth) return true;
      suspendedValues.clear();
      armTimer();
      return true;
    };
    return {
      read,
      write,
      schedule,
      flush,
      clear,
      storageKey,
      suspend,
      resume,
      isSuspended: () => suspensionDepth > 0,
    };
  }

  function clipboardAttachmentFiles(clipboardData) {
    const files = [];
    const seen = new Set();
    const pushFile = (file) => {
      if (!file) return;
      const type = String(file.type || "").toLowerCase();
      const size = Number(file.size || 0);
      const name = String(file.name || "").trim();
      const key = type.startsWith("image/")
        ? `image:${type}:${size}`
        : `file:${name}:${type}:${size}:${file.lastModified || 0}`;
      if (seen.has(key)) return;
      seen.add(key);
      files.push(file);
    };
    Array.from(clipboardData?.files || []).forEach(pushFile);
    Array.from(clipboardData?.items || []).forEach((item) => {
      if (item.kind && item.kind !== "file") return;
      pushFile(item.getAsFile?.());
    });
    return files;
  }

  function clipboardPlainText(clipboardData) {
    const text = String(clipboardData?.getData?.("text/plain") || "");
    if (text) return text;
    const html = String(clipboardData?.getData?.("text/html") || "");
    if (!html) return "";
    const node = document.createElement("div");
    node.innerHTML = html;
    return String(node.innerText || node.textContent || "");
  }

  function consumeWorkspaceInputEvent(event) {
    if (!event) return;
    event[WORKSPACE_PASTE_HANDLED_FLAG] = true;
    event.preventDefault?.();
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
  }

  function insertPlainText(input, text, options = {}) {
    const value = String(text || "");
    if (!input || !value) return "none";
    if ("selectionStart" in input && "selectionEnd" in input) {
      const start = Number(input.selectionStart || 0);
      const end = Number(input.selectionEnd || start);
      const current = String(input.value || "");
      input.value = `${current.slice(0, start)}${value}${current.slice(end)}`;
      const next = start + value.length;
      try {
        input.selectionStart = next;
        input.selectionEnd = next;
      } catch {}
      input.dispatchEvent?.(new Event("input", { bubbles: true }));
      return "manual";
    }
    input.focus?.();
    if (!options.forceDom) {
      try {
        if (document.execCommand?.("insertText", false, value)) return "native";
      } catch {}
    }
    const selection = window.getSelection?.();
    let range = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
    if (!range || !input.contains(range.commonAncestorContainer)) {
      range = document.createRange();
      range.selectNodeContents(input);
      range.collapse(false);
    }
    range.deleteContents();
    const node = document.createTextNode(value);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
    input.dispatchEvent?.(new Event("input", { bubbles: true }));
    return "manual";
  }

  function applyAttachmentPaste(input, event, files, text, options = {}, textOptions = {}) {
    consumeWorkspaceInputEvent(event);
    options.onBeforePaste?.({ files, text, event });
    const manualTextUndoFrame = !files.length && text ? options.onBeforeManualTextPaste?.() : null;
    if (files.length && options.onFiles) options.onFiles(files, event);
    if (text) {
      const insertMode = options.onText
        ? options.onText(text, event, { forceDom: false, ...textOptions })
        : insertPlainText(input, text, { forceDom: false, ...textOptions });
      if (manualTextUndoFrame) {
        if (insertMode === "native") options.onCancelManualTextPaste?.(manualTextUndoFrame);
        else options.onAfterManualTextPaste?.(manualTextUndoFrame);
      }
    }
    return true;
  }

  function handleAttachmentPaste(input, event, options = {}) {
    if (event && event[WORKSPACE_PASTE_HANDLED_FLAG]) return false;
    const files = clipboardAttachmentFiles(event?.clipboardData);
    const text = clipboardPlainText(event?.clipboardData);
    const interceptTextOnly = options.interceptTextOnlyPaste !== false;
    if (!files.length && (!text || !interceptTextOnly)) return false;
    if (!files.length && !text) return false;
    return applyAttachmentPaste(input, event, files, text, options);
  }

  async function systemClipboardPayload() {
    const files = [];
    let text = "";
    let readError = null;
    const pushBlob = (blob, name = "") => {
      if (!blob) return;
      const type = String(blob.type || "application/octet-stream");
      if (type === "text/html" || type === "text/rtf") return;
      const fallbackName = type.startsWith("image/") ? "clipboard-image" : "clipboard-attachment";
      const ext = type.split("/")[1]?.split(";")[0] || "bin";
      files.push(new File([blob], name || `${fallbackName}.${ext}`, { type }));
    };
    if (navigator.clipboard?.read) {
      try {
        const items = await navigator.clipboard.read();
        for (const item of items || []) {
          for (const type of item.types || []) {
            const blob = await item.getType(type);
            if (type === "text/plain" && !text) {
              text = await blob.text();
            } else {
              pushBlob(blob);
            }
          }
        }
      } catch (error) {
        readError = error;
      }
    }
    if (!text && navigator.clipboard?.readText) {
      try {
        text = await navigator.clipboard.readText();
      } catch {
        // Binary clipboard reads can succeed even when readText is unavailable.
      }
    }
    if (!files.length && !text && readError) throw readError;
    return { files, text };
  }

  async function pasteInputFromSystemClipboard(input, event, options = {}) {
    if (!input) return false;
    placeInputCaretFromPoint(input, event);
    const targetSelection = composerSelectionSnapshot(input);
    const payload = await systemClipboardPayload();
    const files = Array.isArray(payload.files) ? payload.files : [];
    const text = String(payload.text || "");
    if (!files.length && !text) return false;
    if (targetSelection) restoreComposerSelection(input, targetSelection);
    if (files.length) options.onFiles?.(files, event);
    if (text) {
      const manualTextUndoFrame = !files.length ? options.onBeforeManualTextPaste?.() : null;
      let insertMode = "custom";
      if (options.onPasteText) {
        insertMode = options.onPasteText(text, event) || "custom";
      }
      else insertMode = insertPlainText(input, text, { forceDom: false });
      if (manualTextUndoFrame) {
        if (insertMode === "native") options.onCancelManualTextPaste?.(manualTextUndoFrame);
        else options.onAfterManualTextPaste?.(manualTextUndoFrame);
      }
      const afterTextSelection = composerSelectionSnapshot(input);
      const afterPasteSignature = inputMutationValue(input);
      const restoreAfterContextMenu = () => {
        if (document.activeElement !== input) return;
        if (inputMutationValue(input) !== afterPasteSignature) return;
        if (afterTextSelection) restoreComposerSelection(input, afterTextSelection);
        else placeInputCaretAtEnd(input);
      };
      [0, 50].forEach(delay => window.setTimeout(restoreAfterContextMenu, delay));
    }
    return true;
  }

  function handleAttachmentDrop(event, options = {}) {
    const files = Array.from(event?.dataTransfer?.files || []).filter(Boolean);
    if (!files.length) return false;
    event.preventDefault();
    if (options.onFiles) options.onFiles(files, event);
    return true;
  }

  function handleAttachmentDragOver(event) {
    if (Array.from(event?.dataTransfer?.types || []).includes("text/plain")) {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      return true;
    }
    if (!event?.dataTransfer?.files?.length) return false;
    event.preventDefault();
    return true;
  }

  function bindWorkspaceComposer(options = {}) {
    const input = options.input || null;
    if (!input) return;
    input.__portalWorkspaceComposerOptions = options;
    const currentOptions = () => input.__portalWorkspaceComposerOptions || {};
    const bindControl = (node, key, handler) => {
      if (!node || node.dataset[key] === "1") return;
      node.dataset[key] = "1";
      node.addEventListener("click", handler);
    };
    bindControl(options.attachButton, "portalWorkspaceAttachBound", () => currentOptions().fileInput?.click());
    if (options.fileInput && options.fileInput.dataset.portalWorkspaceFileBound !== "1") {
      options.fileInput.dataset.portalWorkspaceFileBound = "1";
      options.fileInput.addEventListener("change", () => {
        const active = currentOptions();
        const files = Array.from(active.fileInput?.files || []);
        if (files.length) active.onFiles?.(files);
        if (active.fileInput) active.fileInput.value = "";
      });
    }
    const submit = event => {
      if (isComposerImeActive(input, event)) return;
      const active = currentOptions();
      if (active.canSend && !active.canSend()) return;
      active.onSend?.();
    };
    bindControl(options.sendButton, "portalWorkspaceSendBound", submit);
    bindControl(options.stopButton, "portalWorkspaceStopBound", () => currentOptions().onStop?.());
    if (input.dataset.portalWorkspaceComposerBound === "1") return;
    input.dataset.portalWorkspaceComposerBound = "1";
    input.dataset.portalWorkspaceComposerFocused = document.activeElement === input ? "1" : "0";
    input.dataset.portalWorkspaceComposerComposing = "0";
    const scope = () => currentOptions().historyScope ? String(currentOptions().historyScope() || "default") : "default";
    const notifyActivity = (event = null) => {
      currentOptions().onInputActivityChange?.({
        input,
        event,
        focused: document.activeElement === input,
        composing: input.dataset.portalWorkspaceComposerComposing === "1",
      });
    };
    const setFocused = (value, event) => {
      input.dataset.portalWorkspaceComposerFocused = value ? "1" : "0";
      notifyActivity(event);
    };
    const setComposing = (value, event) => {
      input.dataset.portalWorkspaceComposerComposing = value ? "1" : "0";
      notifyActivity(event);
    };
    input.addEventListener("dragstart", event => {
      const target = event.target instanceof Element ? event.target : null;
      const node = target?.closest?.("[data-portal-composer-attachment-token]");
      if (!node || !input.contains(node)) return;
      const token = node.dataset.portalComposerAttachmentToken || "";
      if (!token) return;
      if (!event.dataTransfer) return;
      event.dataTransfer.setData("text/plain", `portal-attachment:${token}`);
      event.dataTransfer.effectAllowed = "move";
    });
    input.addEventListener("dragover", event => handleAttachmentDragOver(event));
    input.addEventListener("drop", event => {
      const raw = event.dataTransfer?.getData("text/plain") || "";
      if (raw.startsWith("portal-attachment:") && currentOptions().onMoveAttachment?.(raw.slice("portal-attachment:".length), event)) return;
      handleAttachmentDrop(event, {
        onFiles: files => currentOptions().onFiles?.(files),
      });
    });
    input.addEventListener("focus", event => setFocused(true, event));
    input.addEventListener("blur", event => setFocused(false, event));
    input.addEventListener("compositionstart", event => setComposing(true, event));
    input.addEventListener("compositionend", event => setComposing(false, event));
    const pasteOptions = {
      onBeforePaste: context => currentOptions().onBeforePaste?.(context),
      onFiles: files => currentOptions().onFiles?.(files),
      onText: (text, _event, textOptions = {}) => {
        if (currentOptions().onPasteText) return currentOptions().onPasteText(text, _event) || "custom";
        return insertPlainText(input, text, { ...textOptions });
      },
      onBeforeManualTextPaste: context => currentOptions().onBeforeManualTextPaste?.(context),
      onCancelManualTextPaste: context => currentOptions().onCancelManualTextPaste?.(context),
      onAfterManualTextPaste: context => currentOptions().onAfterManualTextPaste?.(context),
    };
    input.addEventListener("paste", event => handleAttachmentPaste(input, event, pasteOptions));
    input.addEventListener("contextmenu", event => {
      if (currentOptions().rightClickPaste === false) return;
      // An HTTP page off localhost has no Clipboard API: the browser's own
      // menu still pastes there, through the paste event.
      if (!navigator.clipboard?.read && !navigator.clipboard?.readText) return;
      event.preventDefault();
      pasteInputFromSystemClipboard(input, event, {
        onFiles: files => currentOptions().onFiles?.(files),
        // An input without its own text handler takes the text as plain text.
        onPasteText: currentOptions().onPasteText ? (...args) => currentOptions().onPasteText(...args) : null,
        onBeforeManualTextPaste: (...args) => currentOptions().onBeforeManualTextPaste?.(...args),
        onCancelManualTextPaste: (...args) => currentOptions().onCancelManualTextPaste?.(...args),
        onAfterManualTextPaste: (...args) => currentOptions().onAfterManualTextPaste?.(...args),
      }).catch(error => {
        if (currentOptions().onClipboardPasteError) currentOptions().onClipboardPasteError(error);
        else window.PortalUI.toast(window.PortalI18n.t("浏览器没有开放剪贴板读取权限，请使用 Ctrl/⌘ + V 粘贴。"), "warning");
      });
    });
    input.addEventListener("keydown", event => {
      if (isComposerImeActive(input, event)) return;
      if (currentOptions().onBeforeKeydown?.(event)) return;
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && String(event.key || "").toLowerCase() === "z") {
        if (currentOptions().onUndo?.(event)) {
          event.preventDefault();
          return;
        }
      }
      if (currentOptions().history?.handleKey?.(event, input, scope())) {
        currentOptions().onInput?.(event, { source: "history" });
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        submit(event);
      }
    });
    input.addEventListener("input", event => {
      currentOptions().onInput?.(event, {
        source: event.detail?.source === "history" ? "history" : "input",
      });
    });
  }

  function iconSvg(name) {
    if (name === "refresh") {
      return window.PortalUIPrimitives?.iconHtml?.("refresh") || "";
    }
    if (name === "collapse") {
      return window.PortalUIPrimitives?.iconHtml?.("chevron-right") || "";
    }
    return "";
  }

  function normalizeRefs(selection) {
    const refs = selection && typeof selection === "object" ? selection : {};
    const out = {};
    for (const key of ["skills", "agents", "resources"]) {
      if (refs[key] instanceof Set) out[key] = new Set(refs[key]);
      else out[key] = new Set(Array.isArray(refs[key]) ? refs[key] : []);
    }
    return out;
  }

  function refCount(selection) {
    const refs = normalizeRefs(selection);
    return (refs.skills?.size || 0)
      + (refs.agents?.size || 0)
      + (refs.resources?.size || 0);
  }

  function refsPayload(selection) {
    const refs = normalizeRefs(selection);
    const payload = {
      skills: [...refs.skills],
      agents: [...refs.agents],
    };
    const resources = [...refs.resources].map(ref => {
      if (!ref || typeof ref !== "object") return ref;
      return {
        address: String(ref.address || ref.resource_id || ref.id || ""),
      };
    }).filter(ref => typeof ref === "string" ? !!ref : !!ref.address);
    if (resources.length) payload.resources = resources;
    return payload;
  }

  const activeRefsPopoverBindings = new Set();

  function isThirdPartySkillOption(opt) {
    return String(opt?.id || "").startsWith("third-party/");
  }

  function refsPanelHtml(options = {}) {
    return window.PortalI18n.html`<div class="refsSearch">
      <input type="search" class="refsSearchInput" data-refs-search
        aria-label="搜索引用" placeholder="搜索名称、ID、路径或描述"
        autocomplete="off" spellcheck="false" value="${escapeHtml(options.searchQuery || "")}" />
      </div><div data-refs-results>${refsResultsHtml(options)}</div>`;
  }

  function refsResultsHtml(options = {}) {
    const refs = normalizeRefs(options.selection || {});
    const scope = String(options.scope || "default");
    if (options.catalogStatus === "idle" || options.catalogStatus === "loading") {
      return window.PortalI18n.markup('<div class="refsEmpty" role="status">正在加载引用选项…</div>');
    }
    if (options.catalogStatus === "error") {
      return window.PortalI18n.html`<div class="refsEmpty" role="alert">引用选项加载失败，请重试。</div>
        <button type="button" class="portalButton is-compact" data-refs-retry="${escapeHtml(scope)}">重试</button>`;
    }
    const contextOptions = options.contextOptions || {};
    const collapsed = options.collapsed || {};
    const query = String(options.searchQuery || "").trim().toLocaleLowerCase();
    const skillOptions = Array.isArray(contextOptions.skills) ? contextOptions.skills : [];
    const thirdPartySkills = skillOptions.filter(isThirdPartySkillOption);
    const sharedSkills = skillOptions.filter(opt => !isThirdPartySkillOption(opt));
    const sectionHtml = (groupId, key, title, items) => {
      if (query) {
        items = items.filter(item => [item?.name, item?.id, item?.path, item?.description]
          .some(value => String(value || "").toLocaleLowerCase().includes(query)));
        if (!items.length) return "";
      }
      const isCollapsed = !query && !!collapsed[groupId];
      const body = !items.length
        ? window.PortalI18n.html`<div class="refsEmpty">无可选项</div>`
        : window.PortalI18n.html`<div class="refsOptions">
          ${items.map(item => {
            const id = String(item?.id || "");
            const name = String(item?.name || id);
            const titleText = [name, item?.path || "", item?.description || ""].filter(Boolean).join(" · ");
            return window.PortalI18n.html`<label class="refsOption" title="${escapeHtml(titleText)}">
              <input type="checkbox" data-portal-chat-ref="${escapeHtml(key)}" data-ref-scope="${escapeHtml(scope)}" value="${escapeHtml(id)}" ${refs[key]?.has(id) ? "checked" : ""} />
              <span>${escapeHtml(name)}</span>
            </label>`;
          }).join("")}
        </div>`;
      return window.PortalI18n.html`<div class="refsGroup" data-refs-group="${escapeHtml(groupId)}">
        <button type="button" class="refsGroupHead" data-refs-group-toggle="${escapeHtml(groupId)}" data-ref-scope="${escapeHtml(scope)}" aria-expanded="${isCollapsed ? "false" : "true"}" ${query ? "disabled" : ""}>
          <span class="refsGroupLabel">${escapeHtml(title)}</span>
          <span data-portal-icon="${isCollapsed ? "chevron-down" : "chevron-up"}" data-portal-icon-class="refsGroupChevron"></span>
        </button>
        ${isCollapsed ? "" : body}
      </div>`;
    };
    const groups = [
      sectionHtml("thirdPartySkills", "skills", "THIRD-PARTY SKILLS", thirdPartySkills),
      sectionHtml("skills", "skills", "技能", sharedSkills),
      sectionHtml("agents", "agents", "Agent", Array.isArray(contextOptions.agents) ? contextOptions.agents : []),
    ].filter(Boolean).join(window.PortalI18n.markup('<hr class="refsDivider" />'));
    return window.PortalI18n.html`${groups || window.PortalI18n.markup('<div class="refsEmpty" role="status">没有匹配的引用</div>')}
      <div class="refsActions">
        <button type="button" class="portalButton is-compact" data-refs-clear="${escapeHtml(scope)}">Clear</button>
        <button type="button" class="portalButton is-compact" data-refs-done="${escapeHtml(scope)}">Done</button>
      </div>`;
  }

  function selectedRefLabels(selection, contextOptions = {}) {
    const refs = normalizeRefs(selection);
    const out = [];
    for (const [key, label] of [["skills", window.PortalI18n.t("技能")], ["agents", "Agent"]]) {
      const map = new Map((Array.isArray(contextOptions[key]) ? contextOptions[key] : []).map(item => [String(item?.id || ""), item]));
      for (const ref of refs[key] || []) {
        const refObject = ref && typeof ref === "object" ? ref : null;
        const id = String(refObject?.id || refObject?.path || refObject?.name || ref || "");
        const item = id ? map.get(id) : null;
        const name = String(item?.name || refObject?.name || refObject?.path || refObject?.id || id || "");
        if (name) out.push(`${label}: ${name}`);
      }
    }
    for (const ref of refs.resources || []) {
      const refObject = ref && typeof ref === "object" ? ref : null;
      const address = String(refObject?.address || refObject?.resource_id || refObject?.id || ref || "");
      const status = String(refObject?.status || "").trim().toLowerCase();
      const title = String(
        status && status !== "resolved"
          ? address
          : refObject?.title || address || "",
      );
      if (title) out.push(window.PortalI18n.text`Resource: ${title}`);
    }
    return out;
  }

  function refsControlHtml(options = {}) {
    const scope = String(options.scope || "default");
    const count = refCount(options.selection || {});
    return window.PortalI18n.html`<div class="refsWrap">
      <button class="portalButton is-compact ${count ? "is-active" : ""}" type="button" data-refs-toggle="${escapeHtml(scope)}" aria-label="选择引用的 Skills 和 Agents" title="选择引用的 Skills 和 Agents">Refs${count ? ` ${count}` : ""}</button>
      <div class="refsPanel" data-refs-panel="${escapeHtml(scope)}" role="dialog" aria-label="选择 Chat 引用" aria-hidden="true">${refsPanelHtml(options)}</div>
    </div>`;
  }

  function bindRefsControl(root, options = {}) {
    if (!root) return null;
    const scope = String(options.scope || "default");
    const panel = root.querySelector(`[data-refs-panel="${CSS.escape(scope)}"]`);
    const toggle = root.querySelector(`[data-refs-toggle="${CSS.escape(scope)}"]`);
    if (!panel || !toggle) return null;
    const existing = panel.__portalRefsPopoverBinding || toggle.__portalRefsPopoverBinding;
    if (existing) return existing.update(options);

    let currentOptions = options;
    const getSelection = () => normalizeRefs(
      currentOptions.getSelection ? currentOptions.getSelection() : currentOptions.selection || {},
    );
    const setSelection = refs => currentOptions.setSelection?.(normalizeRefs(refs));
    const getCollapsed = () => currentOptions.getCollapsed
      ? (currentOptions.getCollapsed() || {})
      : (currentOptions.collapsed || {});
    const setCollapsed = collapsed => currentOptions.setCollapsed?.(collapsed);
    const searchInput = panel.querySelector("[data-refs-search]");
    let searchQuery = searchInput.value;
    let binding = null;

    const popover = window.PortalOverlay.bindPopover({
      trigger: toggle,
      panel,
      placement: "top-end",
      boundary: root,
      ariaHaspopup: "dialog",
      portal: true,
      closeOnOutside: true,
      closeOnEscape: true,
      onOpen: () => {
        activeRefsPopoverBindings.forEach(other => {
          if (other !== binding) other.close();
        });
        currentOptions.onLoad?.();
        binding.refresh();
        panel.classList.add("open");
        panel.setAttribute("aria-hidden", "false");
      },
      onClose: () => {
        panel.classList.remove("open");
        panel.setAttribute("aria-hidden", "true");
      },
    });

    const bindPanelContents = () => {
      panel.querySelector(`[data-refs-retry="${CSS.escape(scope)}"]`)?.addEventListener("click", () => {
        currentOptions.onLoad?.();
      });
      panel.querySelectorAll(`[data-refs-group-toggle][data-ref-scope="${CSS.escape(scope)}"]`).forEach(btn => {
        btn.addEventListener("click", event => {
          event.preventDefault();
          const groupId = btn.dataset.refsGroupToggle || "";
          const collapsed = { ...getCollapsed(), [groupId]: !getCollapsed()[groupId] };
          setCollapsed(collapsed);
          binding.refresh();
        });
      });
      panel.querySelectorAll(`[data-portal-chat-ref][data-ref-scope="${CSS.escape(scope)}"]`).forEach(input => {
        input.addEventListener("change", () => {
          const refs = getSelection();
          const key = input.dataset.portalChatRef || "";
          if (!refs[key]) refs[key] = new Set();
          if (input.checked) refs[key].add(input.value);
          else refs[key].delete(input.value);
          setSelection(refs);
          renderRefsSummary(root, {
            scope,
            selection: refs,
            contextOptions: currentOptions.contextOptions || {},
          });
          currentOptions.onChange?.(refs);
        });
      });
      panel.querySelector(`[data-refs-done="${CSS.escape(scope)}"]`)?.addEventListener("click", event => {
        binding.close(event);
      });
      panel.querySelector(`[data-refs-clear="${CSS.escape(scope)}"]`)?.addEventListener("click", () => {
        const refs = { skills: new Set(), agents: new Set(), resources: new Set() };
        setSelection(refs);
        binding.refresh();
        currentOptions.onChange?.(refs);
      });
    };

    const refreshPanel = () => {
      panel.querySelector("[data-refs-results]").innerHTML = refsResultsHtml({
        scope,
        selection: getSelection(),
        searchQuery,
        contextOptions: currentOptions.contextOptions || {},
        catalogStatus: currentOptions.catalogStatus,
        collapsed: getCollapsed(),
      });
      bindPanelContents();
      renderRefsSummary(root, {
        scope,
        selection: getSelection(),
        contextOptions: currentOptions.contextOptions || {},
      });
      popover.reposition();
      requestAnimationFrame(() => popover.reposition());
    };

    const handleToggle = event => popover.toggle(event);
    const handleSearch = () => {
      searchQuery = searchInput.value;
      refreshPanel();
      panel.scrollTop = 0;
    };
    searchInput.addEventListener("input", handleSearch);
    toggle.addEventListener("click", handleToggle);
    binding = {
      panel,
      trigger: toggle,
      popover,
      update(nextOptions = {}) {
        currentOptions = nextOptions;
        return binding;
      },
      refresh() {
        refreshPanel();
        return binding;
      },
      open(context) {
        popover.open(context);
        return binding;
      },
      close(context) {
        popover.close(context);
        return binding;
      },
      isOpen: () => popover.isOpen(),
      destroy() {
        searchInput.removeEventListener("input", handleSearch);
        toggle.removeEventListener("click", handleToggle);
        activeRefsPopoverBindings.delete(binding);
        popover.destroy();
        delete panel.__portalRefsPopoverBinding;
        delete toggle.__portalRefsPopoverBinding;
      },
    };
    panel.__portalRefsPopoverBinding = binding;
    toggle.__portalRefsPopoverBinding = binding;
    activeRefsPopoverBindings.add(binding);
    bindPanelContents();
    renderRefsSummary(root, {
      scope,
      selection: getSelection(),
      contextOptions: currentOptions.contextOptions || {},
    });
    return binding;
  }

  function renderRefsSummary(root, options = {}) {
    const scope = String(options.scope || "default");
    const btn = root?.querySelector?.(`[data-refs-toggle="${CSS.escape(scope)}"]`);
    const summary = root?.querySelector?.(`[data-refs-summary="${CSS.escape(scope)}"]`);
    const labels = selectedRefLabels(options.selection || {}, options.contextOptions || {});
    if (btn) {
      btn.textContent = labels.length ? window.PortalI18n.text`Refs ${labels.length}` : "Refs";
      btn.classList.toggle("is-active", labels.length > 0);
    }
    if (!summary) return;
    summary.innerHTML = labels.length
      ? labels.slice(0, 4).map(label => window.PortalI18n.html`<span class="refsChip" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`).join("") + (labels.length > 4 ? window.PortalI18n.html`<span class="refsChip">+${labels.length - 4}</span>` : "")
      : "";
  }

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

  function mediaPartPath(part) {
    const source = String(part?.source || part?.path || part?.url || "").trim();
    if (source) return source;
    const uploadId = String(part?.upload_id || "").trim();
    return /^upload_[0-9a-f]+$/.test(uploadId) ? `/api/uploads/${uploadId}` : "";
  }

  function messageHistoryMediaAttachments(item, options = {}) {
    const messageId = messageIdentity(item);
    const contentParts = Array.isArray(item?.content_parts) ? item.content_parts : [];
    const media = Array.isArray(item?.input_media) && item.input_media.length
      ? item.input_media
      : contentParts.filter(part => part?.type === "image" || part?.type === "file");
    return media.map((part, index) => {
      const source = mediaPartPath(part);
      if (!source) return null;
      const type = String(part?.type || part?.media_type || "image").toLowerCase();
      const kind = type === "file" ? "file" : "image";
      const name = String(part?.name || part?.filename || source.split("/").filter(Boolean).pop() || kind);
      return normalizeComposerAttachment({
        id: String(part?.file_id || part?.id || `${messageId}:media:${index}`),
        file_id: String(part?.file_id || ""),
        upload_id: String(part?.upload_id || ""),
        kind,
        name,
        path: source,
        preview_url: String(part?.preview_url || (kind === "image" ? options.previewUrlForPath?.(source) || "" : "")),
        content_type: String(part?.mime_type || part?.content_type || ""),
        size_bytes: Number(part?.size_bytes || part?.size || 0),
      });
    }).filter(Boolean);
  }

  function messageHistoryDraft(item, options = {}) {
    const attachments = messageHistoryMediaAttachments(item, options);
    let text = String(item?.content || "");
    for (const attachment of attachments) {
      const kind = attachmentKind(attachment);
      const placeholder = composerAttachmentPlaceholder(attachment);
      const references = [
        `[${kind}: ${attachment.path}]`,
        `[${kind}:${attachment.path}]`,
        `[attached ${kind}: ${attachment.name}]`,
      ];
      let replaced = false;
      for (const reference of references) {
        if (!reference || !text.includes(reference)) continue;
        text = text.split(reference).join(placeholder);
        replaced = true;
      }
      if (!replaced) text = `${text}${text.trim() ? "\n" : ""}${placeholder}`;
    }
    return { text: text.trim(), attachments };
  }

  function messageAuthoredByViewer(item) {
    const viewer = String(window.PortalIdentity?.username?.() || "").trim().toLowerCase();
    const sender = item?.origin?.sender;
    const author = String(sender?.id || item?.author_id || "").trim().toLowerCase();
    return !!viewer && viewer === author && (!sender?.kind || sender.kind === "user")
      && messageRole(item) === "human";
  }

  // One input as the history recalls it: its text with the attachments in place.
  function messageInputHistoryItem(item, options = {}) {
    const draft = messageHistoryDraft(item, options);
    return { id: messageIdentity(item), text: draft.text, meta: { attachments: draft.attachments } };
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

  function messageAvatarPlaceholderHtml(id, label) {
    return window.PortalI18n.html`<span class="portalUserAvatar messageAuthorAvatar" data-message-avatar-principal="${escapeHtml(id)}" data-message-avatar-label="${escapeHtml(label)}" aria-hidden="true">${escapeHtml(Array.from(label || id)[0]?.toUpperCase() || "?")}</span>`;
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

  function commonMessageActionsHtml(item, key, options = {}, firstAgentReply = false) {
    const content = String(item?.content || "");
    if (!content) return "";
    const customItems = options.renderMessageMenuItems
      ? String(options.renderMessageMenuItems(item, key) || "")
      : "";
    return window.PortalI18n.html`<span class="portalMessageMenuPanel" data-portal-message-menu="${escapeHtml(key)}" data-portal-message-key="${escapeHtml(key)}" hidden>
        ${customItems}
        <button type="button" data-portal-message-copy="text">复制为纯文本</button>
        <button type="button" data-portal-message-copy="markdown">复制为 Markdown</button>
        ${firstAgentReply ? window.PortalI18n.html`<button type="button" data-portal-message-copy="text" data-portal-message-copy-scope="replies">复制全部回复为纯文本</button>
        <button type="button" data-portal-message-copy="markdown" data-portal-message-copy-scope="replies">复制全部回复为 Markdown</button>` : ""}
    </span>`;
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
    options.onLatestReply?.(latest, latestAt);
    return expanded;
  }

  function messageFoldBodyHtml(key, bodyHtml, expanded, size = "") {
    return window.PortalI18n.html`<div class="messageContent markdownBody" data-message-fold="${expanded ? "expanded" : "collapsed"}" data-message-fold-size="${escapeHtml(size)}">${bodyHtml}</div><button class="messageFoldToggle" type="button" data-message-fold-toggle="${escapeHtml(key)}" aria-expanded="${expanded ? "true" : "false"}">${expanded ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开全文")}</button>`;
  }

  // Writes the same attributes and text messageFoldBodyHtml renders, so a later render reuses the node.
  function applyMessageFold(button, expanded) {
    const body = button?.previousElementSibling;
    if (!body?.dataset.messageFold) return;
    body.dataset.messageFold = expanded ? "expanded" : "collapsed";
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
    button.textContent = expanded ? window.PortalI18n.t("收起") : window.PortalI18n.t("展开全文");
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

  // Human chat bursts periodically repeat their header. Agent output uses its
  // consecutive reply group instead, regardless of elapsed time. Compare stable
  // identities, not display names. Recompute from the full ordered list so
  // prepend, deletion and suffix rendering share the same group boundaries.
  function compactHumanHeaders(items) {
    const compact = new Set();
    let start = NaN, previousTime = NaN, previousSender = "", previousRole = "", previousHistory = "";
    items.forEach((item, index) => {
      const role = messageRole(item), sender = messageSenderIdentity(item);
      const time = Date.parse(item?.created_at || "");
      const delayed = Date.parse(item?.activated_at || "") - time >= 1000;
      const eligible = role === "human" && sender && Number.isFinite(time)
        && !delayed && !messageSystemPresentation(item) && !item?.recipients?.length;
      const history = String(item?.family_history?.resident_id || "");
      const continues = eligible && history === previousHistory && sender === previousSender && role === previousRole
        && time >= previousTime && time - start <= 5 * 60 * 1000
        && new Date(time).toDateString() === new Date(start).toDateString();
      if (continues) compact.add(index);
      else start = eligible ? time : NaN;
      previousTime = time;
      previousSender = eligible ? sender : "";
      previousRole = role;
      previousHistory = history;
    });
    return compact;
  }

  // A Family facade answers requests routed together separately: replies to
  // different requests are separate runs (decision_44cf587c1721).
  function sameAgentRun(previous, next) {
    if (String(previous?.family_history?.resident_id || "") !== String(next?.family_history?.resident_id || "")) return false;
    const previousSender = messageSenderIdentity(previous), nextSender = messageSenderIdentity(next);
    if ((previousSender || nextSender) && previousSender !== nextSender) return false;
    // A message sent to someone stands on its own, apart from any reply (decision_c524c775e0be).
    if (previous?.recipients?.length || next?.recipients?.length) return false;
    return String(previous?.family_reply?.request_message_id || "")
      === String(next?.family_reply?.request_message_id || "");
  }

  function consecutiveAgentGroups(items, options = {}) {
    const groups = new Map();
    const collapsed = options.collapsedAgentGroups instanceof Set
      ? options.collapsedAgentGroups
      : new Set(options.collapsedAgentGroups || []);
    const expanded = options.expandedAgentGroups instanceof Set
      ? options.expandedAgentGroups
      : new Set(options.expandedAgentGroups || []);
    const autoCollapse = options.autoCollapseAgentGroups === true;
    let index = 0;
    while (index < items.length) {
      if (messageRole(items[index]) !== "agent") {
        index += 1;
        continue;
      }
      const start = index;
      while (
        index + 1 < items.length
        && messageRole(items[index + 1]) === "agent"
        && sameAgentRun(items[index], items[index + 1])
      ) index += 1;
      const end = index;
      if (end > start) {
        const id = `agent:${messageRenderKey(items[start])}`;
        const group = {
          id,
          start,
          end,
          intermediateCount: end - start,
          collapsed: autoCollapse ? !expanded.has(id) : collapsed.has(id),
        };
        for (let member = start; member <= end; member += 1) groups.set(member, group);
      }
      index += 1;
    }
    return groups;
  }

  // A facade work item keeps its identity across interleaved replies, paging
  // and live updates. It is not the consecutive intermediate-output group.
  function familyWorkGroups(items, collapsed = new Set()) {
    const groups = new Map();
    for (const item of items) {
      const id = String(item?.family_reply?.request_message_id || "");
      if (!id || messageRole(item) !== "agent" || groups.has(id)) continue;
      groups.set(id, { id, headKey: messageRenderKey(item), collapsed: collapsed.has(id) });
    }
    return groups;
  }

  function messageListHtml(messages, options = {}) {
    const stream = normalizeMessageStream(messages, options);
    const items = stream.messages;
    const runtimeDetails = stream.runtimeDetails;
    const runtimeLive = stream.runtimeLive;
    const runtimePhase = stream.runtimePhase;
    const history = options.history && typeof options.history === "object" ? options.history : {};
    const inheritanceHtml = !history.can_load_older && !(Number(options.renderStartIndex) > 0)
      ? inheritanceCardHtml(options.inheritance, options.inheritanceView, options)
      : "";
    if (!items.length && !runtimeDetails.length && !runtimeLive && !runtimePhase) {
      if (inheritanceHtml) return window.PortalI18n.html`<div class="messageList">${inheritanceHtml}</div>`;
      if (!window.PortalUIPrimitives?.asyncStateHtml) {
        throw new Error(window.PortalI18n.t("PortalUIPrimitives must be loaded before PortalChatWorkspace."));
      }
      return window.PortalUIPrimitives.asyncStateHtml({
        state: "empty",
        title: "",
        message: options.emptyText || window.PortalI18n.t("暂无对话。"),
        icon: "chat",
        className: "portalWorkspaceEmpty",
      });
    }
    const agentGroups = consecutiveAgentGroups(items, options);
    const collapsedWorkGroups = options.collapsedWorkGroups instanceof Set
      ? options.collapsedWorkGroups : new Set(options.collapsedWorkGroups || []);
    const workGroups = familyWorkGroups(options.allMessages || items, collapsedWorkGroups);
    const compactHeaders = compactHumanHeaders(items);
    const foldDefaults = options.messageFoldDefaults instanceof Set
      ? options.messageFoldDefaults
      : messageFoldDefaults(items, { runtimeLive });
    const foldSizes = options.messageFoldSizes instanceof Map ? options.messageFoldSizes : new Map();
    const renderStartIndex = Math.max(0, Math.min(
      items.length,
      Number.isFinite(Number(options.renderStartIndex)) ? Number(options.renderStartIndex) : 0,
    ));
    const renderEndIndex = Math.max(renderStartIndex, Math.min(
      items.length,
      Number.isFinite(Number(options.renderEndIndex)) ? Number(options.renderEndIndex) : items.length,
    ));
    const historyHtml = renderStartIndex === 0 && history.can_load_older
      ? window.PortalI18n.html`<div class="workspaceHistoryLoader" data-message-key="__history__"><button class="portalButton is-compact" type="button" data-workspace-load-older ${history.loading ? "disabled" : ""}>${escapeHtml(history.loading ? window.PortalI18n.t("正在加载…") : window.PortalI18n.t("加载更早消息"))}</button></div>`
      : "";
    const html = items.slice(renderStartIndex, renderEndIndex).map((item, localIndex) => {
      const idx = renderStartIndex + localIndex;
      const callbackIndex = idx + (Number(options.messageIndexOffset) || 0);
      const callbackMessages = options.allMessages || items;
      const role = messageRole(item);
      const key = messageRenderKey(item);
      const historical = item.family_history;
      const historyBoundary = !historical && callbackMessages[callbackIndex - 1]?.family_history;
      const historyPrefix = historical
        ? `<div class="personalAssistantHistorySource" data-family-history="${escapeHtml(historical.resident_id)}">${escapeHtml(window.PortalI18n.text`${historical.title || "AMA"} 的历史`)}</div>`
        : historyBoundary ? `<div class="personalAssistantHistoryBoundary" data-family-history-boundary role="separator">${window.PortalI18n.t("个人助手从此处接续对话")}${historyBoundary.before_at ? ` · ${escapeHtml(footerTime(historyBoundary.before_at))}` : ""}</div>` : "";
      if (role === "human" && item?.withdrawn) {
        const edit = options.enableWithdrawnEdit && messageAuthoredByViewer(item)
          ? window.PortalI18n.markup('<button type="button" class="messageWithdrawalEdit" data-portal-message-action="withdrawn-edit">重新编辑</button>') : "";
        return window.PortalI18n.html`<article class="messageItem messageWithdrawal" data-message-key="${escapeHtml(key)}">${historyPrefix}<small>用户已撤回一条信息</small>${edit}</article>`;
      }
      const work = role === "agent" ? workGroups.get(String(item?.family_reply?.request_message_id || "")) : null;
      const isWorkHead = !!work && work.headKey === key;
      const system = messageSystemPresentation(item);
      const previousRole = idx > 0 ? messageRole(items[idx - 1]) : "";
      const continued = work ? !isWorkHead : role === "agent" && previousRole === "agent" && sameAgentRun(items[idx - 1], item);
      const group = work ? null : agentGroups.get(idx) || null;
      const isGroupEnd = !!group && group.end === idx;
      const hiddenByGroup = (!!work?.collapsed && !isWorkHead) || (!!group?.collapsed && !isGroupEnd);
      const compactHeader = (continued || compactHeaders.has(idx)) && !(group?.collapsed && isGroupEnd);
      const content = String(item?.content || "");
      const created = item?.created_at || "";
      const renderMarkdown = options.renderMarkdown || ((value) => (
        window.MD?.render?.(value) || escapeHtml(value).replace(/\n/g, "<br>")
      ));
      const renderContent = value => renderResidentHandoffs(value, text => renderMarkdownWithAttachmentRefs(
        text, { ...options, renderMarkdown }
      ));
      const defaultBodyHtml = renderContent(content);
      const customBodyHtml = options.renderMessageBody
        ? options.renderMessageBody(item, {
          content,
          bodyHtml: defaultBodyHtml,
          role,
          index: callbackIndex,
          messages: callbackMessages,
          renderContent,
        })
        : null;
      const bodyHtml = customBodyHtml == null ? defaultBodyHtml : String(customBodyHtml);
      // Only a plain body folds; cards, questions and forwarded references keep their own display.
      const bodyBlockHtml = options.foldLongMessages !== false && !residentHandoffParts(content).some(part => part.cards)
        && content.trim() && bodyHtml && bodyHtml === defaultBodyHtml
        ? messageFoldBodyHtml(key, bodyHtml, messageFoldChoices.has(key)
          ? messageFoldChoices.get(key) === "expanded"
          : foldDefaults.has(key), foldSizes.get(key))
        : window.PortalI18n.html`<div class="messageContent markdownBody">${bodyHtml || window.PortalI18n.markup("<span class=\"paneNote\">无正文。</span>")}</div>`;
      const prefixHtml = historyPrefix + (options.renderMessagePrefix ? String(options.renderMessagePrefix(item, callbackIndex, callbackMessages) || "") : "");
      const showDefaultActions = options.defaultMessageActions !== false
        && (
          typeof options.showMessageActions !== "function"
          || options.showMessageActions(item, key, callbackIndex, callbackMessages) !== false
        );
      const actionsHtml = options.renderMessageActions
        ? String(options.renderMessageActions(item, callbackIndex, callbackMessages) || "")
        : (showDefaultActions ? commonMessageActionsHtml(item, key, options, role === "agent" && !continued) : "");
      const footerHtml = options.renderMessageFooter ? String(options.renderMessageFooter(item, callbackIndex, callbackMessages) || "") : "";
      const formatTime = options.formatTime || (value => String(value || ""));
      const activated = String(item?.activated_at || "");
      const queuedDelay = Date.parse(activated) - Date.parse(created);
      // Input order is when the Resident receives it, even if it waited in the queue.
      const delayedInput = role === "human" && Number.isFinite(queuedDelay) && queuedDelay >= 1000;
      const formattedCreated = delayedInput
        ? window.PortalI18n.text`${formatTime(activated)} 接单 · ${formatTime(created)} 发出`
        : formatTime(created);
      const workToggleHtml = isWorkHead
        ? window.PortalI18n.html`<button class="messageGroupToggle messageWorkToggle" data-portal-icon="${work.collapsed ? "chevron-down" : "chevron-up"}" type="button" data-portal-work-group-toggle="${escapeHtml(work.id)}" aria-expanded="${work.collapsed ? "false" : "true"}" aria-label="${window.PortalI18n.t(work.collapsed ? "展开这项工作的全部输出" : "折叠这项工作的全部输出")}" title="${window.PortalI18n.t(work.collapsed ? "展开这项工作的全部输出" : "折叠这项工作的全部输出")}"></button>` : "";
      const headRight = window.PortalI18n.html`<span class="messageHeadRight"><span>${escapeHtml(formattedCreated)}</span>${workToggleHtml}${actionsHtml}</span>`;
      const groupToggleHtml = isGroupEnd
          ? window.PortalI18n.html`<button class="messageGroupToggle" data-portal-icon="${group.collapsed ? "chevron-down" : "chevron-up"}" type="button" data-portal-agent-group-toggle="${escapeHtml(group.id)}" aria-expanded="${group.collapsed ? "false" : "true"}" aria-label="${group.collapsed ? window.PortalI18n.text`展开 ${group.intermediateCount} 条中间输出` : window.PortalI18n.text`折叠 ${group.intermediateCount} 条中间输出`}" title="${group.collapsed ? window.PortalI18n.text`展开 ${group.intermediateCount} 条中间输出` : window.PortalI18n.text`折叠 ${group.intermediateCount} 条中间输出`}"></button>`
        : "";
      const showHeader = !compactHeader;
      const continuationHtml = showHeader
        ? window.PortalI18n.html`<div class="messageHead">${messageAuthorHeaderHtml(item, messageTitle(item, callbackIndex, options), system, options)}${headRight}${continued ? groupToggleHtml : ""}</div>`
        : continued
        ? window.PortalI18n.html`<div class="messageDividerRow"><div class="messageDivider" role="separator" aria-label="${escapeHtml(options.assistantName || "Agent")} 继续回复"></div>${groupToggleHtml}${actionsHtml}</div>`
        : actionsHtml;
      const messageBodyHtml = `${continued ? "" : messageQuoteHtml(item?.quote, options)}${bodyBlockHtml}
        ${responseSourcesHtml(item?.response_sources)}
        ${messageContextRefsHtml(item?.context_refs || {}, options)}
        ${messageAttachmentsHtml(item?.context_refs?.attachments || item?.attachments || [], options)}`;
      // Delivered Family requests retain their body after cancellation. Keep
      // the member's terminal state visible even when the sender header folds.
      const cancellationHtml = role === "human" && item?.relay?.state === "cancelled"
        ? window.PortalI18n.html`<small class="messageCancelledBadge">已撤销</small>` : "";
      // Local human submissions are pending too; only Agent output gets the runtime accent.
      const runtimeClass = role === "agent" && item?.pending ? "liveRuntime" : "";
      return window.PortalI18n.html`<article class="messageItem ${escapeHtml(role)}${system ? " chatSystemCard" : ""} ${continued ? "continued" : ""}${compactHeader ? " senderContinued" : ""}${work?.collapsed ? " is-work-collapsed" : ""} ${runtimeClass} ${options.messageClass ? escapeHtml(options.messageClass(item, callbackIndex, callbackMessages) || "") : ""}" data-message-key="${escapeHtml(key)}" data-message-instance="${escapeHtml(messageInstanceKey(item))}"${work ? ` data-work-group-id="${escapeHtml(work.id)}"${isWorkHead ? ' data-work-group-head="true"' : ""}` : ""}${compactHeader ? ` aria-label="${escapeHtml(`${messageTitle(item, callbackIndex, options)} · ${formattedCreated}`)}" title="${escapeHtml(formattedCreated)}"` : ""}${system ? ` data-system-source="${system.source}"` : ""}${group ? ` data-agent-group-id="${escapeHtml(group.id)}"` : ""}${hiddenByGroup ? " hidden" : ""}>
        ${prefixHtml}
        ${continuationHtml}
        ${role === "human" && !system ? window.PortalI18n.html`<div class="messageBubble">${cancellationHtml}${messageBodyHtml}</div>` : messageBodyHtml}
        ${footerHtml}
      </article>${options.renderAfterMessage
        ? String(options.renderAfterMessage(item, callbackIndex, callbackMessages, { key: messageAfterKey(messageInstanceKey(item)), anchorKey: key,
          workCollapsed: collapsedWorkGroups.has(String(item?.family_status?.request_message_id || work?.id || "")) }) || "")
        : ""}`;
    }).join("");
    const tailHtml = renderEndIndex === items.length
      ? window.PortalI18n.html`${runtimeStreamHtml(runtimeDetails, runtimeLive, options)}<div class="messageScrollAnchor" data-message-key="__scroll_anchor__" data-message-scroll-anchor aria-hidden="true"></div>`
      : "";
    return window.PortalI18n.html`<div class="messageList">${historyHtml}${renderStartIndex === 0 ? inheritanceHtml : ""}${html}${tailHtml}</div>`;
  }

  const FORK_MODE_LABELS = Object.freeze({
    full: "完整上下文",
    compacted: "压缩继承",
    "recent-5": "最近 5 轮",
    "recent-10": "最近 10 轮",
    message: window.PortalI18n.t("从这里 Fork"),
  });

  // Where a Fork's inherited context came from, above the first inherited
  // message (decision_5bf5bfbd52cb). Its trace starts at the source's latest
  // compaction, so the source's earlier conversation is not in this Chat.
  function inheritanceCardHtml(inheritance, view = {}, options = {}) {
    if (!inheritance || typeof inheritance !== "object" || !inheritance.source_resident_id) return "";
    const formatTime = value => (value ? String(options.formatTime ? options.formatTime(value) : value) : "");
    const anchorPrefix = inheritance.fork_mode === "message" ? "context_source" : "source";
    const anchorMessage = inheritance.fork_mode === "message" ? inheritance.source_message_id : inheritance.last_message_id;
    const anchorHref = inheritance[`${anchorPrefix}_visible`] && anchorMessage
      ? `/lineage?${new URLSearchParams({
        ...(inheritance[`${anchorPrefix}_lineage_id`] ? { lineage: inheritance[`${anchorPrefix}_lineage_id`] } : {}),
        resident: inheritance[`${anchorPrefix}_resident_id`],
        message: anchorMessage,
        open_chat: "1",
      }).toString()}`
      : "";
    // The source's name opens its Chat at the Fork point when the caller can see it.
    const sourceHtml = (prefix) => {
      const id = String(inheritance[`${prefix}_resident_id`] || "");
      const label = inheritance[`${prefix}_visible`] && inheritance[`${prefix}_title`] ? inheritance[`${prefix}_title`] : id;
      if (!anchorHref || id !== String(inheritance[`${anchorPrefix}_resident_id`] || "")) {
        return `<span class="chatInheritanceSource" title="${escapeHtml(id)}">${escapeHtml(label)}</span>`;
      }
      const title = `${label === id ? "" : `${id} · `}${window.PortalI18n.t("在来源对话中查看 Fork 位置")}`;
      return `<a class="chatInheritanceSource" href="${escapeHtml(anchorHref)}" title="${escapeHtml(title)}">${escapeHtml(label)}</a>`;
    };
    const mode = FORK_MODE_LABELS[inheritance.fork_mode] ? window.PortalI18n.t(FORK_MODE_LABELS[inheritance.fork_mode]) : "";
    const meta = [mode, formatTime(inheritance.forked_from_at)].filter(Boolean).join(" · ");
    const contextSourceHtml = inheritance.context_source_resident_id
      && inheritance.context_source_resident_id !== inheritance.source_resident_id
      ? window.PortalI18n.html`<p class="chatInheritanceText">上下文来自 ${sourceHtml("context_source")}</p>`
      : "";
    const compaction = inheritance.opening_compaction && typeof inheritance.opening_compaction === "object"
      ? inheritance.opening_compaction
      : null;
    const count = Number(inheritance.message_count || 0);
    const compactedAt = formatTime(compaction?.created_at);
    let description = "";
    if (inheritance.context_cleared) {
      description = window.PortalI18n.t("继承的上下文已在之后清除，下方只显示清除之后的对话。");
    } else if (compaction && count) {
      description = window.PortalI18n.text`继承的上下文以来源 ${compactedAt} 的一次压缩开头，之后的 ${count} 条消息显示在下方。更早的对话只以压缩摘要的形式进入上下文。`;
    } else if (compaction) {
      description = window.PortalI18n.text`继承的上下文是来源 ${compactedAt} 的一次压缩。更早的对话只以压缩摘要的形式进入上下文。`;
    } else if (count) {
      description = window.PortalI18n.text`继承了来源的 ${count} 条消息，显示在下方。`;
    } else if (inheritance.recorded) {
      description = window.PortalI18n.t("继承的上下文中没有对话消息。");
    }
    const expanded = !!view?.expanded && !!compaction && !inheritance.context_cleared;
    const summaryButton = compaction && !inheritance.context_cleared
      ? `<button type="button" class="portalButton is-compact" data-chat-inheritance-summary aria-expanded="${expanded ? "true" : "false"}">${escapeHtml(expanded ? window.PortalI18n.t("收起压缩摘要") : window.PortalI18n.t("查看压缩摘要"))}</button>`
      : "";
    return window.PortalI18n.html`<section class="chatInheritanceCard" data-message-key="__inheritance__" aria-label="Fork">
      <div class="chatInheritanceHead"><strong>从 ${sourceHtml("source")} Fork 而来</strong>${meta ? `<span class="chatInheritanceMeta">${escapeHtml(meta)}</span>` : ""}</div>
      ${contextSourceHtml}
      ${description ? `<p class="chatInheritanceText">${escapeHtml(description)}</p>` : ""}
      ${summaryButton ? `<div class="chatInheritanceActions">${summaryButton}</div>` : ""}
      ${expanded ? inheritanceSummaryHtml(view, options) : ""}
    </section>`;
  }

  function inheritanceSummaryHtml(view = {}, options = {}) {
    if (view.status === "loading") {
      return `<div class="chatInheritanceSummary" aria-busy="true">${escapeHtml(window.PortalI18n.t("正在读取压缩摘要…"))}</div>`;
    }
    if (view.status === "error") {
      return `<div class="chatInheritanceSummary is-error" role="alert">${escapeHtml(window.PortalI18n.text`读取压缩摘要失败：${view.error || ""}`)}</div>`;
    }
    const compaction = view.compaction && typeof view.compaction === "object" ? view.compaction : {};
    const items = Array.isArray(compaction.items) ? compaction.items : [];
    const render = value => (options.renderMarkdown ? options.renderMarkdown(value) : escapeHtml(value).replace(/\n/g, "<br>"));
    const labeled = items.length > 1;
    const blocks = items.map(item => {
      const label = item.role === "agent" ? "Agent" : window.PortalI18n.t("用户");
      const images = Number(item.image_count || 0)
        ? `<p class="chatInheritanceNote">${escapeHtml(window.PortalI18n.text`另有 ${Number(item.image_count)} 张图片未显示`)}</p>`
        : "";
      return `<div class="chatInheritanceSummaryItem">${labeled ? `<div class="chatInheritanceSummaryRole">${escapeHtml(label)}</div>` : ""}<div class="messageContent">${render(String(item.text || ""))}</div>${images}</div>`;
    }).join("");
    const encrypted = compaction.encrypted
      ? `<p class="chatInheritanceNote">${escapeHtml(items.length
        ? window.PortalI18n.t("这次压缩还有一段由 Provider 加密保存的摘要，无法显示。")
        : window.PortalI18n.t("这次压缩的摘要由 Provider 加密保存，无法显示。"))}</p>`
      : "";
    const empty = !blocks && !encrypted
      ? `<p class="chatInheritanceNote">${escapeHtml(window.PortalI18n.t("这次压缩没有可显示的内容。"))}</p>`
      : "";
    return `<div class="chatInheritanceSummary">${blocks}${encrypted}${empty}</div>`;
  }

  function runtimeStreamHtml(details, live, options = {}) {
    const stream = normalizeMessageStream([], {
      runtimeDetails: details,
      runtimeLive: live,
      runtimePhase: options.runtimePhase,
    });
    const showRuntimeActivity = !PRE_PROVIDER_RUNTIME_PHASES.has(stream.runtimePhase)
      && (stream.runtimeLive || stream.runtimeDetails.length);
    const runtimeHtml = showRuntimeActivity
      ? window.PortalI18n.html`<article class="messageItem agent liveRuntime" data-message-key="__runtime__">
          <div class="messageHead"><span class="messageTitle">${escapeHtml(options.assistantName || "Agent")}</span><span>${stream.runtimeLive ? window.PortalI18n.t("运行中") : window.PortalI18n.t("活动")}</span></div>
          ${runtimeDetailsHtml(stream.runtimeDetails, {
            live: stream.runtimeLive,
            formatTime: options.formatTime,
            assistantName: options.assistantName,
            emptyLiveHtml: options.emptyLiveHtml || window.PortalI18n.html`<div class="paneNote">${escapeHtml(options.assistantName || "Agent")} 已开始运行，等待活动事件。</div>`,
          })}
        </article>`
      : "";
    return `${runtimePhaseStatusHtml(stream.runtimePhase)}${stream.runtimeLive ? subagentLiveHtml(stream.runtimeDetails) : ""}${runtimeHtml}`;
  }

  function messageSelectionRanges() {
    const selection = window.getSelection?.();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return [];
    return Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index));
  }

  function selectionIntersectsNode(node, ranges = messageSelectionRanges()) {
    if (!node) return false;
    for (const range of ranges) {
      try {
        if (range.intersectsNode(node)) return true;
      } catch {
        // Ignore a range that became detached while the message list was updating.
      }
    }
    return false;
  }

  function selectedMessageText(range, content) {
    const segment = range.cloneRange();
    if (!content.contains(range.startContainer)) segment.setStart(content, 0);
    if (!content.contains(range.endContainer)) segment.setEnd(content, content.childNodes.length);
    return String(segment.toString() || "").trim();
  }

  function messageContentAtBoundary(root, node) {
    const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
    const content = element?.closest?.(".messageItem .messageContent");
    return content && root.contains(content) ? content : null;
  }

  function messageSelectionCandidate(root, selection = window.getSelection?.()) {
    if (!root || !selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
    const range = selection.getRangeAt(0);
    const startContent = messageContentAtBoundary(root, range.startContainer);
    const endContent = messageContentAtBoundary(root, range.endContainer);
    const contents = startContent && startContent === endContent
      ? [startContent]
      : [...root.querySelectorAll(".messageItem .messageContent")].filter(content => {
          try {
            return range.intersectsNode(content);
          } catch {
            return false;
          }
        });
    if (!contents.length) return null;
    const segments = contents.map(content => {
      const article = content.closest(".messageItem");
      const text = selectedMessageText(range, content);
      if (!article || !text) return null;
      const fallbackTitle = article.classList.contains("agent")
        ? "Agent"
        : article.classList.contains("portal")
          ? "Portal"
          : article.classList.contains("human") || article.classList.contains("user")
          ? window.PortalI18n.t("用户")
            : window.PortalI18n.t("消息");
      return {
        message_id: String(article.dataset.messageKey || "").trim(),
        title: String(article.querySelector(".messageTitle")?.textContent || fallbackTitle).trim(),
        text,
      };
    }).filter(Boolean);
    if (!segments.length) return null;
    const text = segments.map(segment => `${segment.title}: ${segment.text}`).join("\n\n");
    const rects = range.getClientRects();
    const rect = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
    // A selection running into a folded body's hidden part points at the body's visible end.
    const visibleBottom = endContent?.dataset.messageFold === "collapsed" && endContent.dataset.messageFoldSize === "long"
      ? endContent.getBoundingClientRect().bottom
      : Number.POSITIVE_INFINITY;
    return {
      text,
      title: window.PortalI18n.t("对话选段"),
      path: "Chat Workspace",
      source_message_ids: segments.map(segment => segment.message_id).filter(Boolean),
      rect: {
        left: Number(rect?.left || 0),
        right: Number(rect?.right || 0),
        top: Math.min(Number(rect?.top || 0), visibleBottom),
        bottom: Math.min(Number(rect?.bottom || 0), visibleBottom),
      },
    };
  }

  function createMessageSelectionController(options = {}) {
    const root = options.root;
    if (!root) return null;
    const toolbar = document.createElement("div");
    toolbar.className = "chatSelectionContextAction";
    toolbar.hidden = true;
    toolbar.innerHTML = window.PortalI18n.html`<button type="button" data-portal-icon="plus" aria-label="${escapeHtml(options.label || window.PortalI18n.t("添加到上下文"))}"><span>${escapeHtml(options.label || window.PortalI18n.t("添加到上下文"))}</span></button>`;
    document.body.appendChild(toolbar);
    let candidate = null;
    let selectionFrame = 0;

    const clear = ({ preserveSelection = false } = {}) => {
      candidate = null;
      toolbar.hidden = true;
      if (!preserveSelection) window.getSelection?.()?.removeAllRanges?.();
    };
    const position = rect => {
      toolbar.hidden = false;
      const box = toolbar.getBoundingClientRect();
      const gap = 8;
      const left = Math.min(
        Math.max(gap, Number(rect.right || rect.left || gap) - box.width),
        Math.max(gap, window.innerWidth - box.width - gap),
      );
      const preferredTop = Number(rect.bottom || rect.top || gap) + gap;
      const top = preferredTop + box.height <= window.innerHeight - gap
        ? preferredTop
        : Math.max(gap, Number(rect.top || gap) - box.height - gap);
      toolbar.style.left = `${Math.round(left)}px`;
      toolbar.style.top = `${Math.round(top)}px`;
    };
    const refresh = () => {
      window.cancelAnimationFrame(selectionFrame);
      selectionFrame = window.requestAnimationFrame(() => {
        const next = messageSelectionCandidate(root);
        if (!next) {
          clear({ preserveSelection: true });
          return;
        }
        candidate = next;
        position(next.rect);
      });
    };
    const handleSelectionChange = () => {
      const selection = window.getSelection?.();
      if (!selection || selection.isCollapsed || selection.rangeCount !== 1) {
        if (!toolbar.hidden) clear({ preserveSelection: true });
        return;
      }
      const range = selection.getRangeAt(0);
      if (!root.contains(range.startContainer) && !root.contains(range.endContainer)) {
        if (!toolbar.hidden) clear({ preserveSelection: true });
        return;
      }
      refresh();
    };
    const handleToolbarPointerDown = event => event.preventDefault();
    const handleToolbarClick = () => {
      if (!candidate) return;
      const selected = candidate;
      Promise.resolve()
        .then(() => options.onAdd?.(selected))
        .then(() => clear())
        .catch(error => options.onError?.(error));
    };
    const handleKeyDown = event => {
      // Escape dismisses a shown action and its message selection; elsewhere,
      // such as the composer caret, the selection is not this controller's.
      if (event.key === "Escape" && !toolbar.hidden) clear();
    };
    const handleViewportChange = () => clear({ preserveSelection: true });
    root.addEventListener("mouseup", refresh);
    root.addEventListener("keyup", refresh);
    root.addEventListener("scroll", handleViewportChange, { passive: true });
    toolbar.addEventListener("pointerdown", handleToolbarPointerDown);
    toolbar.addEventListener("click", handleToolbarClick);
    document.addEventListener("selectionchange", handleSelectionChange);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", handleViewportChange);

    return {
      clear,
      refresh,
      destroy() {
        window.cancelAnimationFrame(selectionFrame);
        root.removeEventListener("mouseup", refresh);
        root.removeEventListener("keyup", refresh);
        root.removeEventListener("scroll", handleViewportChange);
        toolbar.removeEventListener("pointerdown", handleToolbarPointerDown);
        toolbar.removeEventListener("click", handleToolbarClick);
        document.removeEventListener("selectionchange", handleSelectionChange);
        document.removeEventListener("keydown", handleKeyDown);
        window.removeEventListener("resize", handleViewportChange);
        toolbar.remove();
      },
    };
  }

  function preserveRuntimeDisclosureState(current, next) {
    const currentDetails = current?.querySelector?.("details.messageRuntimeDetails");
    const nextDetails = next?.querySelector?.("details.messageRuntimeDetails");
    if (currentDetails && nextDetails) nextDetails.open = currentDetails.open;
    const currentDisclosures = new Map(
      [...(current?.querySelectorAll?.("details[data-message-disclosure]") || [])]
        .map(details => [String(details.dataset.messageDisclosure || ""), details])
        .filter(([key]) => key)
    );
    for (const nextDisclosure of next?.querySelectorAll?.("details[data-message-disclosure]") || []) {
      const previous = currentDisclosures.get(String(nextDisclosure.dataset.messageDisclosure || ""));
      if (previous) nextDisclosure.open = previous.open;
    }
  }

  function preserveDeferredImageState(current, next) {
    const currentImages = [...(current?.querySelectorAll?.("img[data-portal-deferred-image]") || [])];
    const nextImages = [...(next?.querySelectorAll?.("img[data-portal-deferred-image]") || [])];
    nextImages.forEach((nextImage, index) => {
      const currentImage = currentImages[index];
      if (
        !currentImage?.hasAttribute("src")
        || currentImage.dataset.portalDeferredImage !== nextImage.dataset.portalDeferredImage
      ) return;
      nextImage.setAttribute("src", currentImage.getAttribute("src") || "");
    });
  }

  // Question cards own their mounted DOM, including unconfirmed input and IME
  // state. Refresh the message around those mounts without detaching them.
  // A removed host is still removed; card status/permissions remain its owner's
  // responsibility in ResidentQuestionCards.render after message reconciliation.
  function reconcileQuestionMounts(current, next) {
    const hosts = node => node.nodeType !== 1 ? [] : node.hasAttribute("data-resident-question")
      ? [node] : [...node.querySelectorAll("[data-resident-question]")];
    if (current.hasAttribute("data-resident-question")
      && current.dataset.residentQuestion === next.dataset.residentQuestion) return;
    for (const attribute of [...current.attributes]) {
      if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    }
    for (const attribute of [...next.attributes]) current.setAttribute(attribute.name, attribute.value);
    if (!current.querySelector("[data-resident-question]") || !next.querySelector("[data-resident-question]")) {
      current.replaceChildren(...next.childNodes);
      return;
    }
    const branches = new Map();
    for (const child of current.children) {
      for (const host of hosts(child)) branches.set(host.dataset.residentQuestion, child);
    }
    const retained = new Set();
    const children = [...next.childNodes].map(child => {
      const branch = hosts(child).map(host => branches.get(host.dataset.residentQuestion))
        .find(candidate => candidate && candidate.tagName === child.tagName && !retained.has(candidate));
      if (!branch) return child;
      retained.add(branch);
      reconcileQuestionMounts(branch, child);
      return branch;
    });
    // Remove obsolete siblings before positioning retained branches; inserting
    // a retained branch before a soon-to-be-removed sibling would blur inputs.
    for (const child of [...current.childNodes]) if (!retained.has(child)) child.remove();
    let cursor = null;
    for (const child of children) {
      const position = cursor ? cursor.nextSibling : current.firstChild;
      if (child !== position) current.insertBefore(child, position);
      cursor = child;
    }
  }

  function reconcileMessageList(container, html, options = {}) {
    if (!container) return false;
    const selectionRanges = messageSelectionRanges();
    const template = document.createElement("template");
    template.innerHTML = String(html || "").trim();
    const nextRoot = template.content.firstElementChild;
    const currentRoot = container.firstElementChild;
    if (!nextRoot?.classList.contains("messageList") || !currentRoot?.classList.contains("messageList")) {
      container.replaceChildren(...template.content.childNodes);
      return { changedRoots: [container] };
    }
    const currentByKey = new Map();
    for (let node = options.currentStartNode || currentRoot.firstElementChild;
      node; node = node.nextElementSibling) {
      const key = messageNodeKey(node);
      if (key) currentByKey.set(key, node);
    }
    const retained = new Set();
    const changedRoots = [];
    // The prefix ends after the block shown after its last message, if any.
    let cursor = options.prefixNode || (options.prefixKey
      ? currentByKey.get(messageAfterKey(String(options.prefixKey))) || currentByKey.get(String(options.prefixKey)) || null
      : null);
    for (const nextNode of [...nextRoot.children]) {
      const key = messageNodeKey(nextNode);
      if (!key) continue;
      const current = currentByKey.get(key) || null;
      const preserveSelectedNode = current && selectionIntersectsNode(current, selectionRanges);
      const preserveOpenMenu = current?.classList.contains("menuOpen");
      if (current && !preserveSelectedNode && !preserveOpenMenu) {
        preserveRuntimeDisclosureState(current, nextNode);
        preserveDeferredImageState(current, nextNode);
      }
      let node = nextNode;
      if (current) {
        node = current;
        if (!preserveSelectedNode && !preserveOpenMenu && current.outerHTML !== nextNode.outerHTML) {
          reconcileQuestionMounts(current, nextNode);
          changedRoots.push(current);
        }
      } else {
        changedRoots.push(node);
      }
      const position = cursor ? cursor.nextSibling : currentRoot.firstChild;
      if (position !== node) currentRoot.insertBefore(node, position);
      cursor = node;
      retained.add(node);
    }
    for (const key of options.replacedKeys || currentByKey.keys()) {
      const node = currentByKey.get(String(key));
      if (node && !retained.has(node) && !selectionIntersectsNode(node, selectionRanges)) node.remove();
    }
    return { changedRoots };
  }

  function messageViewportAtTail(root, threshold = 12) {
    if (!root) return true;
    return root.scrollHeight - root.scrollTop - root.clientHeight <= threshold;
  }

  function workspaceShellHtml(options = {}) {
    const title = options.title || window.PortalI18n.t("Chat Workspace");
    const subtitle = options.subtitle || "";
    const refreshId = options.refreshId || "";
    const messageListId = options.messageListId || "";
    const composerId = options.composerId || "";
    const composerSplitterId = options.composerSplitterId || "";
    const titleId = options.titleId || "";
    const statusId = options.statusId || "";
    const bodyId = options.bodyId || "";
    const loadingId = options.loadingId || "";
    return window.PortalI18n.html`<div class="paneHead chatHead portalWorkspaceHead">
      <div class="paneHeadText chatHeadText">
        <div class="paneTitle" ${titleId ? `id="${escapeHtml(titleId)}"` : ""}>${escapeHtml(title)}</div>
        <div class="chatSubline"><div class="paneNote" ${statusId ? `id="${escapeHtml(statusId)}"` : ""} data-workspace-status>${escapeHtml(subtitle)}</div></div>
      </div>
      <div class="paneHeadActions actionGroup">
        <button class="portalButton is-icon is-quiet iconBtn" type="button" data-workspace-history-search aria-label="查找当前对话" title="浏览当前对话片段，输入关键词查找并跳转" aria-expanded="false">${window.PortalUIPrimitives.iconHtml("search")}</button>
        ${refreshId ? window.PortalI18n.html`<button class="portalButton is-icon is-quiet iconBtn" id="${escapeHtml(refreshId)}" type="button" aria-label="刷新 Chat Workspace" title="刷新">${iconSvg("refresh")}</button>` : ""}
        ${options.extraActionsHtml || ""}
      </div>
    </div>
    <div class="chatWorkspaceBody portalWorkspaceBody" ${bodyId ? `id="${escapeHtml(bodyId)}"` : ""}>
      ${options.bodyPrefixHtml || ""}
      <div class="paneBody" id="${escapeHtml(messageListId)}"></div>
      ${options.bodySuffixHtml || ""}
    </div>
    ${options.afterBodyHtml || ""}
    ${composerSplitterId ? window.PortalI18n.html`<div class="composerSplitter" id="${escapeHtml(composerSplitterId)}" role="separator" aria-orientation="horizontal" aria-label="调整发送框高度" title="拖动调整发送框高度"></div>` : ""}
    <div class="composer" id="${escapeHtml(composerId)}"></div>
    <div class="portalWorkspaceLoading" ${loadingId ? `id="${escapeHtml(loadingId)}"` : ""} data-workspace-loading hidden></div>`;
  }

  function normalizeWorkspaceSubtitleParts(parts = []) {
    return (Array.isArray(parts) ? parts : [])
      .map(part => {
        if (typeof part === "string") return { text: String(part).trim() };
        if (!part || typeof part !== "object") return null;
        const rawText = String(part.text || "").trim();
        const accountPrefix = part.identity === "account" && rawText.startsWith("账号 ");
        const text = accountPrefix ? window.PortalI18n.text`账号 ${rawText.slice(3)}`
          : part.identity === "family" ? window.PortalI18n.message(rawText) : rawText;
        if (!text) return null;
        return {
          text,
          copy_value: String(part.copy_value || part.copyValue || "").trim(),
          copy_label: window.PortalI18n.t(String(part.copy_label || part.copyLabel || "").trim()),
          identity: String(part.identity || "").trim(),
        };
      })
      .filter(Boolean);
  }

  // The composer's accessible name states how to send: plain Enter keeps a new line.
  const COMPOSER_INPUT_LABEL = window.PortalI18n.t("消息输入（Ctrl/⌘ + Enter 发送）");

  function workspaceComposerHtml(options = {}) {
    const scope = String(options.refsScope || "default");
    const inputId = options.inputId || "";
    const fileInputId = options.fileInputId || "";
    const trayId = options.trayId || "";
    const attachId = options.attachId || "";
    const sendId = options.sendId || "";
    const stopId = options.stopId || "";
    const autoId = options.autoId || "";
    const sendLabel = String(options.send_label || "").trim();
    const placeholder = window.PortalI18n.t(options.placeholder || "Ctrl/⌘ + Enter 发送");
    // The input's placeholder already states how to send; a note repeating it is left out.
    const meta = window.PortalI18n.t(String(options.meta || "").trim());
    const metaHtml = options.metaHtml || (meta && meta !== String(placeholder).trim()
      ? window.PortalI18n.html`<span class="paneNote composerMeta">${escapeHtml(meta)}</span>` : "");
    const sendTitle = sendLabel || window.PortalI18n.t("发送消息");
    const sendClass = sendLabel ? "portalButton is-compact is-primary" : "portalButton is-compact is-primary sendIconBtn";
    const sendContent = sendLabel
      ? escapeHtml(sendLabel)
      : window.PortalI18n.html`${window.PortalUIPrimitives.iconHtml("arrow-up")}<span class="srOnly">发送</span>`;
    return window.PortalI18n.html`<div class="workspaceComposerExtension" data-workspace-composer-extension="before">${options.beforeComposerHtml || ""}</div><div class="workspaceQueueHost" data-workspace-queue-host>${options.queueHtml || ""}</div><div class="composerImageTray" id="${escapeHtml(trayId)}"></div><div class="composerQuoteHost" data-composer-quote-host hidden></div>
      <div class="composerInputWrap">
        <div class="composerInput composerRichInput" id="${escapeHtml(inputId)}" contenteditable="${options.editable === false ? "false" : "true"}" role="textbox" aria-multiline="true" aria-label="${escapeHtml(COMPOSER_INPUT_LABEL)}" data-placeholder="${escapeHtml(placeholder)}"></div>
        <div class="composerSlashCommandHost" data-composer-slash-command-host></div>
        <div class="composerSlashCommandHost composerMentionHost" data-composer-mention-host hidden></div>
      </div>
      <div class="composerActions">
        <div class="workspaceComposerMetaHost" data-workspace-composer-meta>${metaHtml}</div>
        <div class="actionGroup" data-workspace-composer-controls>
          ${stopId ? window.PortalI18n.html`<button class="portalButton is-compact is-danger" id="${escapeHtml(stopId)}" type="button" aria-label="停止当前任务" title="停止当前任务" ${options.stopDisabled === false ? "" : "disabled"}>Stop</button>` : ""}
          ${fileInputId ? window.PortalI18n.html`<input id="${escapeHtml(fileInputId)}" type="file" multiple hidden />` : ""}
          ${attachId ? window.PortalI18n.html`<button class="portalButton is-compact" id="${escapeHtml(attachId)}" type="button" title="添加附件" aria-label="添加附件" data-portal-icon="plus"></button>` : ""}
          ${options.refs === false ? "" : refsControlHtml({ scope, selection: options.refsSelection || {}, contextOptions: options.contextOptions || {}, collapsed: options.refsCollapsed || {} })}
          ${sendId ? window.PortalI18n.html`<button class="${sendClass}" id="${escapeHtml(sendId)}" type="button" aria-label="${escapeHtml(sendTitle)}" aria-keyshortcuts="Control+Enter Meta+Enter" title="${escapeHtml(sendTitle)}" ${options.sendDisabled ? "disabled" : ""}>${sendContent}</button>` : ""}
          ${autoId ? window.PortalI18n.html`<button class="portalButton is-compact" id="${escapeHtml(autoId)}" type="button" aria-label="进入自动继续模式" title="进入自动继续模式" ${options.autoDisabled === false ? "" : "disabled"}>Auto</button>` : ""}
        </div>
      </div>
      ${options.refs === false ? "" : window.PortalI18n.html`<div class="refsSummary" data-refs-summary="${escapeHtml(scope)}"></div>`}
      <div class="workspaceComposerExtension" data-workspace-composer-extension="after">${options.afterComposerHtml || ""}</div>`;
  }

  function workspaceComposerCapabilities(composer = {}) {
    return new Set((Array.isArray(composer.capabilities) ? composer.capabilities : [])
      .map(item => String(item || "").trim().toLowerCase())
      .filter(Boolean));
  }

  // Chat attachments are uploads (CreateUpload + its data channel); the
  // message then refers to the upload ID (decision_ad3e21094f14).
  async function defaultWorkspaceUpload(file, options = {}) {
    const kind = String(file?.type || "").toLowerCase().startsWith("image/") ? "image" : "file";
    // The receiving Resident, or the Chatroom a message is posted to.
    const target = options.upload_target || options.resident_id || "";
    const uploaded = await window.PortalInstructionClient.upload(file, {
      purpose: "conversation_attachment",
      target,
      fileName: file.name || (kind === "image" ? "pasted-image.png" : "attachment"),
      mediaType: file.type || (kind === "image" ? "image/png" : "application/octet-stream"),
      onProgress: progress => options.workspace?.updateAttachment?.(options.item?.id, { progress }),
    });
    options.workspace?.updateAttachment?.(options.item?.id, { progress: 100 });
    return {
      upload_id: uploaded.upload_id,
      filename: uploaded.file_name,
      content_type: uploaded.media_type,
      size: uploaded.size,
      kind,
    };
  }

  function defaultWorkspaceDeleteAttachment(item = {}) {
    const uploadId = String(item.upload_id || "").trim();
    if (!uploadId) return Promise.resolve({ deleted: false });
    return window.PortalInstructionClient.execute("DeleteUpload", { upload_id: uploadId }, {
      idempotencyKey: `upload-delete:${uploadId}`,
    });
  }

  function residentRuntimeSettingRows(settings = {}, catalog = {}) {
    const data = settings && typeof settings === "object" ? settings : {};
    const model = String(data.model || catalog.default_model || window.PortalI18n.t("Provider default")).trim();
    const fields = catalog.runtime_capabilities?.settings || [];
    const rows = fields.includes("model") ? [["Model", model]] : [];
    const effort = data.reasoning_effort
      || (catalog.default_efforts_by_model?.[model] ?? catalog.default_reasoning_effort);
    if (fields.includes("reasoning_effort") && effort) rows.push(["Effort", effort]);
    const modelTiers = catalog.service_tiers_by_model?.[model] || [];
    if (fields.includes("service_tier") && modelTiers.length) rows.push(["Speed", data.service_tier === "priority" ? window.PortalI18n.t("Fast on") : window.PortalI18n.t("Fast off")]);
    if (fields.includes("permissions")) {
      const capabilities = catalog.runtime_capabilities || {};
      const preset = String(data.permission_preset || "").trim();
      const label = capabilities.permission_labels?.[preset] || capabilities.default_permission_label;
      if (label) rows.push(["Permissions", label]);
    }
    if (fields.includes("collaboration_mode")) rows.push(["Mode", data.collaboration_mode === "plan" ? "Plan" : window.PortalI18n.t("Default")]);
    if (catalog.api_settings) {
      rows.push(["API 账号", catalog.account_id || catalog.backend_route_id || "—"]);
      rows.push(["上下文", data.prompt_context?.history?.enabled ? window.PortalI18n.t("携带会话历史") : window.PortalI18n.t("单轮独立")]);
      if (catalog.api_settings.view === "jev") rows.push(["Jev 判定", `${Object.keys(data.backend_options?.parameters?.questions || {}).length} ${window.PortalI18n.t("个问题")}`]);
      rows.push(["高级", `${data.backend_options?.timeout_seconds ?? 360}s · ${data.backend_options?.max_retries ?? 0} ${window.PortalI18n.t("次重试")}`]);
    }
    return rows;
  }

  function residentRuntimeStatusText(settings = {}, catalog = {}) {
    return residentRuntimeSettingRows(settings, catalog)
      .filter(([label]) => label !== "Mode" || settings.collaboration_mode === "plan")
      .map(([, value]) => value).join(" · ");
  }

  function residentRuntimeMetaHtml(settings = {}, options = {}) {
    const label = residentRuntimeStatusText(settings, options.catalog || {});
    if (options.interactive === false) {
      return window.PortalI18n.html`<span class="paneNote composerMeta" title="${escapeHtml(options.title || window.PortalI18n.t("当前会话运行设置"))}">${escapeHtml(label)}</span>`;
    }
    const attribute = String(options.openAttribute || "data-runtime-settings-open").trim();
    const title = options.title || window.PortalI18n.t("切换模型与运行设置");
    return window.PortalI18n.html`<button class="paneNote composerMeta composerRuntimeBtn" type="button" ${escapeHtml(attribute)} aria-haspopup="dialog" title="${escapeHtml(`${title}：${label}`)}"><span class="composerRuntimeLabel">${escapeHtml(label)}</span></button>`;
  }

  function notifyRuntimeSettingsSaved(result) {
    const liveUpdate = result?.active_turn_settings_update;
    const changedFields = Array.isArray(result?.next_turn_fields) ? result.next_turn_fields : [];
    if (liveUpdate?.status === "delivered") {
      window.PortalUI?.toast?.("Runtime 设置已保存；Speed 已原生应用到当前 turn 的后续步骤，全部设置从下一条 turn 生效。", "success");
    } else if (liveUpdate?.status === "pending") {
      window.PortalUI?.toast?.("Runtime 设置已保存；Speed 正在应用到当前 turn，全部设置从下一条 turn 生效。", "success");
    } else if (liveUpdate?.status === "failed") {
      window.PortalUI?.toast?.(`Runtime 设置已保存并从下一条 turn 生效；当前 turn 的 Speed 更新失败：${liveUpdate.error || "unknown error"}`, "warning");
    } else if (changedFields.length) {
      window.PortalUI?.toast?.("Runtime 设置已保存，从下一轮生效。", "success");
    }
  }

  async function showResidentBackendNotices(container, residentId, { settings, accountId } = {}) {
    const request = {};
    container.backendNoticeRequest = request;
    container.replaceChildren();
    container.hidden = true;
    if (!residentId) return;
    const current = () => container.isConnected && container.backendNoticeRequest === request;
    const service = window.PortalResidentService;
    try {
      const [stored, boundAccountId, result] = await Promise.all([
        settings || service.runtimeSettings(residentId).then(result => result.settings || {}),
        accountId || window.PortalResidentProjection.load(residentId).then(detail => detail.execution_account?.account_id),
        service.runtimeAccounts(),
      ]);
      if (!current()) return;
      const messages = [];
      const account = result.accounts?.find(row => row.account_id === boundAccountId);
      if (account?.enabled === false) messages.push(window.PortalI18n.t("账号已停用"));
      const fallback = stored.backend_role_fallback;
      if (fallback?.used === "default" && typeof fallback.requested === "string" && fallback.requested) {
        messages.push(window.PortalI18n.text`角色 ${fallback.requested} 未映射，使用站点默认`);
      }
      for (const message of messages) {
        const note = document.createElement("p");
        note.className = "paneNote";
        note.textContent = message;
        container.append(note);
      }
      container.hidden = !messages.length;
    } catch (_) {
      if (!current()) return;
      container.textContent = window.PortalI18n.t("账号与角色状态读取失败，请重新打开设置。");
      container.hidden = false;
    }
  }

  function createRuntimeSettingsDialog(options = {}) {
    const id = String(options.id || `portalRuntimeSettings_${Math.random().toString(16).slice(2)}`);
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay portalRuntimeSettingsOverlay";
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    overlay.dataset.portalDetailOverlay = "1";
    overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface portalRuntimeSettingsSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="${escapeHtml(id)}Title">
      <div class="portalDetailHeader" data-portal-detail-header>
        <div class="portalRuntimeSettingsTitle" id="${escapeHtml(id)}Title">Runtime Settings</div>
        <button class="portalDetailClose" data-portal-icon="close" data-runtime-settings-close type="button" aria-label="关闭 runtime 设置" title="关闭"></button>
      </div>
      <form class="portalDetailForm portalRuntimeSettingsForm" data-runtime-settings-form>
        <div data-resident-backend-notices role="status" hidden></div>
        <div class="portalRuntimeSettingsGrid">
          <label class="portalRuntimeSettingsField" hidden><span>API 账号</span><select data-runtime-setting="backend_route_id"></select></label>
          <label class="portalRuntimeSettingsField"><span>Model</span><select data-runtime-setting="model"></select></label>
          <label class="portalRuntimeSettingsField"><span>Mode</span><select data-runtime-setting="collaboration_mode"><option value="default">Default · 默认执行</option><option value="plan">Plan · 先制定计划</option></select></label>
          <label class="portalRuntimeSettingsField"><span>Effort</span><select data-runtime-setting="reasoning_effort"></select></label>
          <label class="portalRuntimeSettingsField"><span>Speed</span><select data-runtime-setting="service_tier"><option value="">Standard (default)</option><option value="priority">Fast</option></select></label>
          <label class="portalRuntimeSettingsField"><span>Permissions</span><select data-runtime-setting="permission_preset"><option value="">Full Access (default)</option><option value="default">Ask</option><option value="auto-review">Auto-review</option><option value="full-access">Full Access</option></select></label>
        </div>
        <p class="portalRuntimeSettingsHint" data-runtime-settings-hint>设置从下一轮生效。</p>
        <button class="portalButton is-quiet" data-runtime-settings-restart type="button" hidden>切换 Backend／账号或重启…</button>
        <div class="portalDetailFooter" data-portal-detail-footer>
          <button class="portalButton" data-runtime-settings-close type="button">取消</button>
          <button class="portalButton is-primary" data-runtime-settings-submit type="submit">保存</button>
        </div>
      </form>
    </div>`;
    document.body.appendChild(overlay);
    const form = overlay.querySelector("[data-runtime-settings-form]");
    const submit = overlay.querySelector("[data-runtime-settings-submit]");
    const apiFields = window.PortalModelApiSettings.create();
    form.querySelector(".portalRuntimeSettingsGrid").after(apiFields.element);
    const model = overlay.querySelector('[data-runtime-setting="model"]');
    const apiAccount = overlay.querySelector('[data-runtime-setting="backend_route_id"]');
    let apiRoutes = [], initialApiRoute = "";
    const mode = overlay.querySelector('[data-runtime-setting="collaboration_mode"]');
    const effort = overlay.querySelector('[data-runtime-setting="reasoning_effort"]');
    const tier = overlay.querySelector('[data-runtime-setting="service_tier"]');
    const permissions = overlay.querySelector('[data-runtime-setting="permission_preset"]');
    const setOptions = (select, values, current, defaultLabel) => {
      const choices = [...new Set(["", ...values, current].filter(value => value !== undefined && value !== null).map(value => String(value)))];
      select.innerHTML = choices.map(value => window.PortalI18n.html`<option value="${escapeHtml(value)}">${escapeHtml(value || defaultLabel)}</option>`).join("");
      select.value = String(current || "");
    };
    let currentSettings = {};
    let initialSettings = {};
    let providerCatalog = null;
    const syncModelControls = () => {
      if (!providerCatalog) return;
      const selectedModel = model.value || providerCatalog.default_model;
      const knownModel = providerCatalog.models.includes(selectedModel);
      model.title = knownModel ? "" : window.PortalI18n.t("Provider 目录未返回此模型的能力信息");
      const efforts = providerCatalog.efforts_by_model?.[selectedModel] || [];
      const currentEffort = currentSettings.reasoning_effort || "";
      const defaultEffort = providerCatalog.default_efforts_by_model?.[selectedModel] || providerCatalog.default_reasoning_effort || "默认";
      setOptions(effort, efforts, currentEffort, `${defaultEffort} (default)`);
      for (const option of effort.options) {
        if (!efforts.includes(option.value || defaultEffort)) option.title = knownModel ? window.PortalI18n.t("Provider 未声明此模型支持该 Effort") : window.PortalI18n.t("Provider 目录未返回此模型的能力信息");
      }
      effort.closest(".portalRuntimeSettingsField").hidden = knownModel && !efforts.length && !currentEffort;
      const tiers = providerCatalog.service_tiers_by_model?.[selectedModel] || [];
      const tierIds = tiers.map(item => typeof item === "string" ? item : item.id).filter(Boolean);
      const currentTier = String(currentSettings.service_tier || "").toLowerCase() === "priority"
        ? "priority"
        : String(currentSettings.service_tier || "");
      const tierField = tier.closest(".portalRuntimeSettingsField");
      setOptions(tier, tierIds, currentTier, tierIds.length ? "Standard (default)" : "Unavailable");
      for (const option of tier.options) {
        if (option.value === "priority") option.textContent = tierIds.includes("priority") ? "Fast" : window.PortalI18n.t("Fast (saved; unavailable)");
      }
      tier.disabled = !tierIds.length;
      tierField.hidden = !tierIds.length;
      tierField.title = tierIds.length
        ? ""
        : knownModel
          ? window.PortalI18n.t("Provider 未声明此模型支持 Speed")
          : window.PortalI18n.text`当前账号${providerCatalog.account_id ? ` ${providerCatalog.account_id}` : ""}的 Provider 目录未返回 ${selectedModel}，无法确定 Speed 能力`;
    };
    model.addEventListener("change", () => {
      currentSettings = { ...currentSettings, reasoning_effort: effort.value, service_tier: tier.value };
      syncModelControls();
      apiFields.syncModel(model.value);
    });
    apiAccount.addEventListener("change", () => {
      const route = apiRoutes.find(item => item.backend_route_id === apiAccount.value);
      if (!route) return;
      providerCatalog = route;
      currentSettings = apiAccount.value === initialApiRoute ? {...initialSettings} : {
        ...initialSettings, model: route.default_model,
        backend_options: {parameters: {}, timeout_seconds: Math.min(initialSettings.backend_options?.timeout_seconds ?? 360, route.api_settings.timeout_max), max_retries: initialSettings.backend_options?.max_retries ?? 0},
      };
      setOptions(model, route.models, currentSettings.model, `${route.default_model || "Provider"} (default)`);
      apiFields.open(currentSettings, route);
      syncModelControls();
    });
    let save = null;
    let restartSession = null;
    const dialog = window.PortalUI.createDialogController({
      modal: overlay,
      manageHidden: true,
      escapeClose: true,
      backdropClose: true,
      initialFocus: () => model,
      onClose: context => options.onClose?.(context),
    });
    const close = context => dialog?.close?.(context);
    const restartButton = overlay.querySelector("[data-runtime-settings-restart]");
    restartButton.addEventListener("click", async () => {
      if (!restartSession || submit.disabled) return;
      const restart = restartSession;
      close({ source: "session-restart" });
      try { await restart(); }
      catch (error) { window.PortalUI.toast(error.message, "error"); }
    });
    overlay.querySelectorAll("[data-runtime-settings-close]").forEach(button => {
      button.addEventListener("click", () => close({ source: "button" }));
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!save || submit.disabled) return;
      submit.disabled = true;
      submit.textContent = window.PortalI18n.t("保存中");
      try {
        const selected = { model: String(model.value || "").trim() };
        const modelChanged = selected.model !== String(initialSettings.model || "");
        const effortChanged = effort.value !== String(initialSettings.reasoning_effort || "");
        if (!mode.closest(".portalRuntimeSettingsField").hidden) {
          selected.collaboration_mode = mode.value;
        }
        if (modelChanged && !model.closest(".portalRuntimeSettingsField").hidden && providerCatalog && model.value && !providerCatalog.models.includes(model.value)) {
          throw new Error("当前模型未出现在此账号的 Provider 能力列表中，请选择可用模型。");
        }
        {
          if (!permissions.closest(".portalRuntimeSettingsField").hidden
            && permissions.value !== String(initialSettings.permission_preset || "")) {
            selected.permission_preset = String(permissions.value || "").trim();
          }
          if (providerCatalog && (modelChanged || effortChanged)) {
            const selectedModel = model.value || providerCatalog.default_model;
            if (!providerCatalog.models.includes(selectedModel)) throw new Error("默认模型未出现在此账号的 Provider 能力列表中，请选择可用模型。");
            const selectedEffort = effort.value || providerCatalog.default_efforts_by_model?.[selectedModel] || providerCatalog.default_reasoning_effort;
            const supported = providerCatalog.efforts_by_model?.[selectedModel] || [];
            if (selectedEffort && !effort.closest(".portalRuntimeSettingsField").hidden && !supported.includes(selectedEffort)) {
              throw new Error(`Provider 未声明当前模型支持 ${selectedEffort}，请选择可用的 Effort；原设置未修改。`);
            }
          }
          if (!effort.closest(".portalRuntimeSettingsField").hidden) selected.reasoning_effort = String(effort.value || "").trim();
          if (!tier.disabled && !tier.closest(".portalRuntimeSettingsField").hidden) selected.service_tier = String(tier.value || "").trim();
        }
        Object.assign(selected, apiFields.read());
        if (initialApiRoute && apiAccount.value !== initialApiRoute) selected.backend_route_id = apiAccount.value;
        await save(selected);
        close({ accepted: true });
      } catch (error) {
        window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("保存 runtime 设置"), error), "error");
      } finally {
        submit.disabled = false;
        submit.textContent = window.PortalI18n.t("保存");
      }
    });
    return {
      open(settings = {}, onSave = null, dialogOptions = {}) {
        currentSettings = settings && typeof settings === "object" ? settings : {};
        initialSettings = { ...currentSettings };
        void showResidentBackendNotices(overlay.querySelector("[data-resident-backend-notices]"), dialogOptions.residentId, {
          settings: dialogOptions.residentSettings, accountId: dialogOptions.accountId,
        });
        overlay.querySelector(".portalRuntimeSettingsTitle").textContent = dialogOptions.title || window.PortalI18n.t("Runtime Settings");
        providerCatalog = dialogOptions.catalog || null;
        apiFields.open(currentSettings, providerCatalog);
        apiRoutes = providerCatalog?.api_settings ? providerCatalog.api_routes || [providerCatalog] : [];
        initialApiRoute = providerCatalog?.api_settings ? providerCatalog.backend_route_id || providerCatalog.account_id : "";
        apiAccount.closest(".portalRuntimeSettingsField").hidden = !apiRoutes.length;
        apiAccount.replaceChildren(...apiRoutes.map(route => new Option(route.account_id || route.backend_route_id, route.backend_route_id || route.account_id)));
        apiAccount.value = initialApiRoute;
        model.title = "";
        save = onSave;
        restartSession = dialogOptions.onSessionRestart || null;
        restartButton.hidden = !restartSession;
        const models = Array.isArray(dialogOptions.models) && dialogOptions.models.length
          ? dialogOptions.models.map(item => Array.isArray(item) ? item[0] : item)
          : providerCatalog?.models || [];
        setOptions(model, models, currentSettings.model || "", dialogOptions.defaultModelLabel || `${providerCatalog?.default_model || "Provider"} (default)`);
        setOptions(effort, dialogOptions.efforts || [], currentSettings.reasoning_effort || "", "Provider default");
        tier.value = String(currentSettings.service_tier || "").toLowerCase() === "priority" ? "priority" : String(currentSettings.service_tier || "");
        tier.disabled = false;
        tier.closest(".portalRuntimeSettingsField").title = "";
        const permissionPresets = providerCatalog?.runtime_capabilities?.permission_presets || [];
        const defaultPermissionLabel = providerCatalog?.runtime_capabilities?.default_permission_label;
        setOptions(permissions, permissionPresets, currentSettings.permission_preset || "",
          defaultPermissionLabel ? `${defaultPermissionLabel} (default)` : "Provider default");
        for (const option of permissions.options) {
          option.textContent = option.value
            ? providerCatalog?.runtime_capabilities?.permission_labels?.[option.value] || option.value
            : defaultPermissionLabel ? `${defaultPermissionLabel} (default)` : window.PortalI18n.t("Provider default");
        }
        mode.value = currentSettings.collaboration_mode === "plan" ? "plan" : "default";
        syncModelControls();
        const fields = providerCatalog?.runtime_capabilities?.settings || [];
        for (const [control, field] of [[mode, "collaboration_mode"], [effort, "reasoning_effort"], [tier, "service_tier"], [permissions, "permissions"]]) {
          const modelUnavailable = control === effort && !(providerCatalog?.efforts_by_model?.[model.value || providerCatalog?.default_model] || []).length
            || control === tier && !(providerCatalog?.service_tiers_by_model?.[model.value || providerCatalog?.default_model] || []).length;
          control.closest(".portalRuntimeSettingsField").hidden = !fields.includes(field) || modelUnavailable;
        }
        overlay.querySelector("[data-runtime-settings-hint]").textContent = dialogOptions.hint || window.PortalI18n.t("设置从下一轮生效。");
        dialog?.open?.({ focusTarget: model });
      },
      close,
      destroy() {
        dialog?.destroy?.();
        overlay.remove();
      },
    };
  }

  const sessionRestartActive = restart => ["accepted", "submitting", "confirming", "cancelling"].includes(restart?.status);

  const acknowledgedSessionRestarts = new Map();

  function formatSessionRestartDuration(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainder = total % 60;
    return [hours ? `${hours} 小时` : "", minutes ? `${minutes} 分` : "",
      remainder || !total ? `${remainder} 秒` : ""].filter(Boolean).join(" ");
  }

  function createSessionRestartProgress(options = {}) {
    const compact = !!options.compact;
    const element = document.createElement("div");
    element.className = "portalSessionRestartProgress";
    element.classList.toggle("is-compact", compact);
    element.hidden = true;
    const heading = document.createElement("div");
    heading.setAttribute("role", "status");
    heading.setAttribute("aria-live", "polite");
    const timing = document.createElement("div");
    timing.className = "paneNote";
    const message = document.createElement("div");
    message.className = "paneNote";
    const diagnostics = document.createElement("details");
    diagnostics.className = "portalSessionRestartDiagnostics";
    const summary = document.createElement("summary");
    summary.textContent = window.PortalI18n.t("技术诊断");
    const technicalError = document.createElement("pre");
    diagnostics.append(summary, technicalError);
    if (compact) element.append(heading);
    else element.append(heading, timing, message, diagnostics);
    const detailsButton = document.createElement("button");
    detailsButton.type = "button";
    detailsButton.className = "portalButton is-compact";
    detailsButton.dataset.sessionRestartProgressOpen = "1";
    detailsButton.addEventListener("click", () => options.onOpen?.());
    const dismissButton = document.createElement("button");
    dismissButton.type = "button";
    dismissButton.className = "portalButton is-compact";
    dismissButton.textContent = window.PortalI18n.t("已知悉");
    dismissButton.setAttribute("aria-label", window.PortalI18n.t("已知悉，关闭本次迁移失败提示"));
    dismissButton.dataset.sessionRestartDismiss = "1";
    if (compact) element.append(detailsButton, dismissButton);
    let current = {};
    let timer = null;
    const acknowledgementKey = () => {
      const residentId = current.config?.resident_id;
      const owner = typeof options.owner === "function" ? options.owner() : options.owner;
      return residentId ? ["portal.sessionRestartAcknowledged.v1", owner || "anonymous", residentId]
        .map(part => encodeURIComponent(part)).join(":") : "";
    };
    const labels = {
      submitting: window.PortalI18n.t("正在提交，等待受理回执"), confirming: window.PortalI18n.t("受理结果待确认，请勿重复提交"),
      accepted: window.PortalI18n.t("已受理，正在准备"), queued: window.PortalI18n.t("已受理，等待调度"), waiting_current_execution: window.PortalI18n.t("已受理，等待本轮完成后迁移"),
      waiting_quota_reserve: window.PortalI18n.t("已受理，等待额度"), waiting_capacity: window.PortalI18n.t("已受理，等待执行容量"),
      waiting_dispatch_interval: window.PortalI18n.t("已受理，等待调度间隔"), waiting_session_binding: window.PortalI18n.t("已受理，等待 Session 空闲"),
      creating_session: window.PortalI18n.t("正在创建新 Session"), initializing: window.PortalI18n.t("正在初始化交接"),
      switching: window.PortalI18n.t("正在切换绑定"), cancelling: window.PortalI18n.t("正在取消迁移"),
      completed: window.PortalI18n.t("迁移已完成"), failed: window.PortalI18n.t("迁移失败"), cancelled: window.PortalI18n.t("迁移已取消"),
    };
    const render = () => {
      const restart = current.restart;
      const failed = restart?.status === "failed";
      const key = compact ? acknowledgementKey() : "";
      const acknowledged = key && (acknowledgedSessionRestarts.get(key) || window.PortalState.storage.readText(key, ""));
      element.dataset.restartStatus = restart?.status || "";
      element.hidden = !restart?.status || ["completed", "cancelled"].includes(restart.status)
        || !!(failed && restart.restart_id && acknowledged === restart.restart_id);
      if (element.hidden) return;
      detailsButton.textContent = failed ? window.PortalI18n.t("查看详情") : window.PortalI18n.t("查看迁移进度");
      dismissButton.hidden = !failed || !key || !restart.restart_id;
      const label = endpoint => endpoint?.account_id || endpoint?.provider || window.PortalI18n.t("当前 Session");
      const phase = restart.status === "accepted" ? restart.phase || "accepted" : restart.status;
      heading.textContent = `Session：${label(restart.source)} → ${label(restart.target)} · ${labels[phase] || window.PortalI18n.t("迁移进行中")}`;
      const started = Date.parse(restart.submitted_at || "");
      const ended = Date.parse(restart.restarted_at || restart.configured_at || "");
      const end = Number.isFinite(ended) ? ended : sessionRestartActive(restart) ? Date.now() : NaN;
      timing.textContent = Number.isFinite(started) && Number.isFinite(end)
        ? `${failed ? window.PortalI18n.t("耗时") : window.PortalI18n.t("已耗时")} ${formatSessionRestartDuration((end - started) / 1000)}`
        : failed ? window.PortalI18n.t("耗时未记录") : "";
      if (current.lastSyncedAt && sessionRestartActive(restart)) {
        timing.textContent += window.PortalI18n.text` · 最近同步 ${formatSessionRestartDuration((Date.now() - current.lastSyncedAt) / 1000)}前`;
      }
      message.textContent = failed
        ? (restart.previous_session_retained === true
          ? window.PortalI18n.t("迁移未完成，原会话已保留。请核对目标设置后重新迁移。")
          : window.PortalI18n.t("迁移未完成。请核对当前会话与目标设置后重新迁移。"))
        : current.error ? window.PortalI18n.t("暂时无法同步迁移状态，请稍后查看；请勿重复提交。") : sessionRestartActive(restart)
        ? (restart.status === "cancelling" ? window.PortalI18n.t("取消请求已受理，原 Session 将继续保留。") : window.PortalI18n.t("关闭窗口不会取消迁移；需要终止时请使用“取消迁移”。"))
        : restart.configuration_only ? window.PortalI18n.t("账号配置已保存，尚未创建 Session。") : window.PortalI18n.t("迁移已结束。");
      technicalError.textContent = String(restart.error || current.error || "");
      diagnostics.hidden = !technicalError.textContent;
    };
    dismissButton.addEventListener("click", () => {
      const key = acknowledgementKey();
      if (!key || current.restart?.status !== "failed" || !current.restart.restart_id) return;
      acknowledgedSessionRestarts.set(key, current.restart.restart_id);
      window.PortalState.storage.writeText(key, current.restart.restart_id);
      window.dispatchEvent(new CustomEvent("portal-session-restart-acknowledged"));
      options.onDismiss?.();
    });
    const syncAcknowledgement = event => {
      if (!event.key || event.key === window.PortalState.storage.key(acknowledgementKey())) acknowledgedSessionRestarts.delete(acknowledgementKey());
      render();
    };
    if (compact) {
      window.addEventListener("portal-session-restart-acknowledged", render);
      window.addEventListener("storage", syncAcknowledgement);
    }
    return {
      element,
      update(view) {
        if (current.restart?.restart_id !== view?.restart?.restart_id) diagnostics.open = false;
        current = view || {};
        clearInterval(timer);
        render();
        if (sessionRestartActive(current.restart)) timer = setInterval(render, 1000);
      },
      destroy() {
        clearInterval(timer);
        window.removeEventListener("portal-session-restart-acknowledged", render);
        window.removeEventListener("storage", syncAcknowledgement);
        element.remove();
      },
    };
  }

  function createSessionRestartDialog(options = {}) {
    const id = String(options.id || `portalSessionRestart_${Math.random().toString(16).slice(2)}`);
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay portalRuntimeSettingsOverlay portalSessionRestartOverlay";
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    overlay.dataset.portalDetailOverlay = "1";
    overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface portalRuntimeSettingsSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="${escapeHtml(id)}Title">
      <div class="portalDetailHeader" data-portal-detail-header>
        <div class="portalRuntimeSettingsTitle" id="${escapeHtml(id)}Title">Session 操作</div>
        <button class="portalDetailClose" data-portal-icon="close" data-session-restart-close type="button" aria-label="关闭 Session 操作" title="关闭"></button>
      </div>
      <form class="portalDetailForm portalRuntimeSettingsForm" data-session-restart-form>
        <p class="portalRuntimeSettingsHint" data-session-restart-source></p>
        <div data-resident-backend-notices role="status" hidden></div>
        <details class="portalSessionBindingSnapshot" data-session-binding-snapshot><summary>当前会话绑定快照</summary><dl data-session-binding-fields></dl><p class="portalRuntimeSettingsHint">状态版本（Generation）用于核验此会话绑定是否变化，不是 Worker 进程代际；这里展示打开弹窗时的权威快照。</p></details>
        <div class="portalRuntimeSettingsGrid">
          <label class="portalRuntimeSettingsField"><span>目标 Provider</span><select data-session-restart="provider"></select></label>
          <label class="portalRuntimeSettingsField" data-session-restart-account-field><span>Account / Route</span><select data-session-restart="account"></select></label>
          <label class="portalRuntimeSettingsField" data-session-restart-model-field hidden><span>目标模型</span><select data-session-restart="model"></select></label>
          <label class="portalRuntimeSettingsField" data-session-restart-effort-field hidden><span>推理强度</span><select data-session-restart="effort"></select></label>
          <label class="portalRuntimeSettingsField"><span>上下文方案</span><select data-session-restart="strategy"></select></label>
          <label class="portalRuntimeSettingsField"><span>正在运行的工作</span><select data-session-restart="active-execution"><option value="wait">等待完成</option><option value="cancel">取消后重启</option></select></label>
          <label class="portalRuntimeSettingsField"><span>失败的工作</span><span><input type="checkbox" data-session-restart="resume-failed" checked> 切换成功后继续最近的失败输入</span></label>
        </div>
        <p class="portalRuntimeSettingsHint" data-session-restart-hint></p>
        <div class="portalDetailFooter" data-portal-detail-footer>
          <button class="portalButton" data-session-reload type="button">只刷新数据</button>
          <button class="portalButton is-danger" data-session-restart-cancel type="button" hidden>取消迁移</button>
          <button class="portalButton" data-session-restart-close type="button">关闭</button>
          <button class="portalButton is-primary" data-session-restart-submit type="submit">重启 Session</button>
        </div>
      </form>
    </div>`;
    document.body.appendChild(overlay);
    const form = overlay.querySelector("[data-session-restart-form]");
    const submit = overlay.querySelector("[data-session-restart-submit]");
    const cancelMigration = overlay.querySelector("[data-session-restart-cancel]");
    const provider = overlay.querySelector('[data-session-restart="provider"]');
    const account = overlay.querySelector('[data-session-restart="account"]');
    const model = overlay.querySelector('[data-session-restart="model"]');
    const effort = overlay.querySelector('[data-session-restart="effort"]');
    const modelField = overlay.querySelector('[data-session-restart-model-field]');
    const effortField = overlay.querySelector('[data-session-restart-effort-field]');
    const strategy = overlay.querySelector('[data-session-restart="strategy"]');
    const activeExecution = overlay.querySelector('[data-session-restart="active-execution"]');
    const resumeFailed = overlay.querySelector('[data-session-restart="resume-failed"]');
    const accountField = overlay.querySelector("[data-session-restart-account-field]");
    const source = overlay.querySelector("[data-session-restart-source]");
    const bindingFields = overlay.querySelector("[data-session-binding-fields]");
    const hint = overlay.querySelector("[data-session-restart-hint]");
    const apiFields = window.PortalModelApiSettings.create();
    hint.before(apiFields.element);
    let restart = null;
    let cancelRestart = null;
    let reload = null;
    let current = {};
    let requestKey = "";
    let submitting = false;
    let tracking = false;
    let restartView = {};
    let unobserve = null;
    let modelCatalog = null, catalogLoading = false, catalogRevision = 0;
    const createRequestKey = () => `portal-session-restart:${globalThis.PortalUIPrimitives.randomUUID()}`;
    const restartService = window.PortalResidentService;
    const progress = createSessionRestartProgress();
    source.after(progress.element);
    const dialog = window.PortalUI.createDialogController({
      modal: overlay,
      manageHidden: true,
      escapeClose: true,
      backdropClose: true,
      initialFocus: () => provider,
      onClose: context => { unobserve?.(); unobserve = null; options.onClose?.(context); },
    });
    const close = context => dialog?.close?.(context);
    const optionsHtml = (items, fallback = []) => {
      const values = Array.isArray(items) && items.length ? items : fallback;
      return values.map(item => {
        const value = typeof item === "string" ? item : String(item?.id || "");
        const label = typeof item === "string" ? item : String(item?.label || value);
        return window.PortalI18n.html`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
      }).join("");
    };
    const update = () => {
      const routed = !!current.accounts?.[provider.value]?.some(row => row.id);
      const selectedDefinition = current.providers?.find(row => row.id === provider.value);
      const nativeOption = [...strategy.options].find(option => option.value === "native");
      if (nativeOption) nativeOption.disabled = selectedDefinition?.operations?.includes("fork") === false;
      if (strategy.value === "native" && nativeOption?.disabled) strategy.value = "smart";
      const hasSession = !!current.source?.has_session;
      const apiMigration = current.source?.provider === "provider_api" || provider.value === "provider_api";
      const locked = submitting || tracking;
      const retry = restartView.restart?.status === "confirming";
      accountField.hidden = !routed;
      accountField.style.display = routed ? "" : "none";
      provider.disabled = locked;
      account.disabled = !routed || locked;
      strategy.disabled = (!hasSession && current.source?.provider !== "provider_api" && provider.value !== "provider_api") || locked;
      activeExecution.disabled = locked;
      model.disabled = locked || catalogLoading;
      effort.disabled = locked || catalogLoading;
      submit.disabled = submitting || catalogLoading || !restart || (tracking && !retry) || (routed && !account.value)
        || (provider.value === "provider_api" && !modelCatalog);
      cancelMigration.hidden = !tracking;
      cancelMigration.disabled = submitting || restartView.restart?.status === "cancelling";
      submit.textContent = retry && !submitting ? window.PortalI18n.t("重试同一请求") : tracking && !submitting ? window.PortalI18n.t("查看上方迁移状态") : submitting
        ? (hasSession || apiMigration ? window.PortalI18n.t("正在切换…") : window.PortalI18n.t("正在保存…"))
        : apiMigration ? window.PortalI18n.t("迁移 Backend")
        : (hasSession ? restartView.restart?.status === "failed" ? window.PortalI18n.t("重新迁移") : window.PortalI18n.t("重启 Session") : routed ? window.PortalI18n.t("保存账号设置") : window.PortalI18n.t("保存 Provider"));
      const selectedStrategy = strategy.value || "smart";
      hint.textContent = provider.value === "provider_api"
        ? window.PortalI18n.t("切换到直接调用模型 API，不创建原生 Session。按下方历史设置发送上下文；选择全新上下文时从切换后开始积累历史，页面旧对话仍保留。")
        : !hasSession && current.source?.provider !== "provider_api"
        ? window.PortalI18n.t("仅保存 Provider / 账号选择，已有对话和 Fork 上下文保持不变；下次运行时使用新配置。")
        : selectedStrategy === "native"
        ? window.PortalI18n.t("原生 Fork 仅适用于同一 Provider/Account；旧 session 会保留为只读 generation。")
        : selectedStrategy === "fresh"
          ? window.PortalI18n.t("只初始化新的系统上下文，不带入旧对话；旧 session 会保留。")
          : selectedStrategy === "visible"
            ? window.PortalI18n.t("把 Portal 当前可见对话作为资料交给新 session，适合跨 Provider 迁移。")
            : selectedStrategy === "compact"
              ? window.PortalI18n.t("迁移最近的重要对话与当前工作，适合跨 Provider，开销较小。")
              : window.PortalI18n.t("系统优先使用同 Provider 原生 Fork，否则使用精简交接。");
    };
    overlay.querySelectorAll("[data-session-restart-close]").forEach(button => {
      button.addEventListener("click", () => close({ source: "button" }));
    });
    const populateAccounts = (selected = "") => {
      account.innerHTML = optionsHtml(current.accounts?.[provider.value], [{ id: "", label: window.PortalI18n.t("自动选择") }]);
      if ([...account.options].some(option => option.value === selected)) account.value = selected;
    };
    const populateEfforts = (selected = "") => {
      const values = modelCatalog?.efforts_by_model?.[model.value] || [];
      effort.innerHTML = optionsHtml(values, [{ id: "", label: window.PortalI18n.t("默认") }]);
      effort.value = values.includes(selected) ? selected : modelCatalog?.default_efforts_by_model?.[model.value] || values[0] || "";
      effortField.hidden = !modelCatalog || !values.length;
    };
    const loadModelCatalog = async () => {
      const revision = ++catalogRevision;
      modelCatalog = null; modelField.hidden = true; effortField.hidden = true;
      apiFields.open({}, {});
      if (!current.choose_runtime_settings && provider.value !== "provider_api") { catalogLoading = false; return; }
      catalogLoading = true; update();
      const route = current.accounts?.[provider.value]?.find(row => row.id === account.value);
      const query = new URLSearchParams({ backend: provider.value });
      if (provider.value === "provider_api") query.set("account_id", account.value);
      else if (route?.account_id) query.set("account_id", route.account_id);
      try {
        const catalog = await window.PortalResidentService.runtimeCatalog({
          backend: query.get("backend"), accountId: query.get("account_id") || "",
        });
        if (revision !== catalogRevision) return;
        modelCatalog = catalog;
        model.innerHTML = optionsHtml(catalog.models);
        const saved = current.runtime_settings || {};
        model.value = catalog.models?.includes(saved.model) ? saved.model : catalog.default_model || model.options[0]?.value || "";
        modelField.hidden = !model.value;
        populateEfforts(saved.reasoning_effort);
        if (catalog.api_settings) {
          const sameRoute = current.source?.provider === "provider_api" && account.value === current.source?.account_id;
          apiFields.open(sameRoute ? saved : {model: model.value, prompt_context: {history: {
            enabled: catalog.api_settings.default_history, max_tokens: catalog.api_settings.history_max_tokens,
          }}}, catalog);
        }
      } catch (error) {
        if (revision === catalogRevision) window.PortalUI.toast(provider.value === "provider_api"
          ? window.PortalI18n.text`无法读取目标模型，请重新选择账号后重试：${error.message}`
          : window.PortalI18n.text`无法读取目标模型，迁移将使用目标账号默认设置：${error.message}`, "error");
      } finally {
        if (revision === catalogRevision) { catalogLoading = false; update(); }
      }
    };
    provider.addEventListener("change", () => {
      populateAccounts();
      void loadModelCatalog();
      update();
    });
    account.addEventListener("change", () => { void loadModelCatalog(); });
    model.addEventListener("change", () => { populateEfforts(); apiFields.syncModel(model.value); });
    strategy.addEventListener("change", update);
    overlay.querySelector("[data-session-reload]").addEventListener("click", async () => {
      close({ source: "reload" });
      await reload?.();
    });
    cancelMigration.addEventListener("click", async () => {
      const restartId = String(restartView.restart?.restart_id || current.restart?.restart_id || "");
      if (!restartId || !cancelRestart || cancelMigration.disabled) return;
      submitting = true;
      update();
      try {
        const result = await cancelRestart({ restart_id: restartId });
        current.restart = result;
        restartView.restart = result;
        tracking = sessionRestartActive(result);
        window.PortalUI.toast(window.PortalI18n.t("Session 迁移取消请求已受理，原 Session 将保留。"), "success");
      } catch (error) {
        window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("取消 Session 迁移"), error), "error");
      } finally {
        submitting = false;
        update();
      }
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!restart || submit.disabled) return;
      submitting = true;
      tracking = true;
      update();
      source.textContent = current.source?.has_session || current.source?.provider === "provider_api"
        ? window.PortalI18n.t("正在提交迁移请求，请等待后台受理回执；请勿重复操作。")
        : window.PortalI18n.t("正在保存 Provider / 账号选择，不创建 Session。");
      try {
        const result = restartView.restart?.status === "confirming"
          ? await restartService.retrySessionRestart(current.resident_id)
          : await restart({
          target_provider: provider.value,
          target_account: account.value,
          context_strategy: strategy.value || "smart",
          active_execution_policy: activeExecution.value || "wait",
          resume_failed_input: resumeFailed.checked,
          ...apiFields.read(),
          ...(modelCatalog && model.value ? { model: model.value, ...(effort.value ? { reasoning_effort: effort.value } : {}) } : {}),
          idempotency_key: requestKey,
        });
        current.restart = result;
        if (result?.status === "completed") close({ accepted: true });
      } catch (error) {
        tracking = sessionRestartActive(restartView.restart);
        if (!error.restartPending) {
          requestKey = createRequestKey();
          window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("重启 Session"), error), "error");
        }
      } finally {
        submitting = false;
        update();
      }
    });
    return {
      open(config = {}, onRestart = null, onReload = null, onCancel = null) {
        current = config && typeof config === "object" ? config : {};
        void showResidentBackendNotices(overlay.querySelector("[data-resident-backend-notices]"), current.resident_id, {
          accountId: current.source?.account_id, settings: current.runtime_settings || {},
        });
        submitting = false;
        tracking = sessionRestartActive(current.restart);
        restartView = { restart: current.restart };
        unobserve?.();
        requestKey = createRequestKey();
        restart = onRestart;
        cancelRestart = onCancel;
        reload = onReload;
        provider.innerHTML = optionsHtml(current.providers);
        const sourceProvider = String(current.source?.provider || "");
        provider.value = sourceProvider;
        // The server's Provider list names each Provider.
        const sourceLabel = current.providers?.find(row => row.id === sourceProvider)?.label || sourceProvider;
        const sourceAccount = String(current.source?.account_id || "");
        populateAccounts(sourceAccount);
        strategy.innerHTML = optionsHtml(current.strategies);
        strategy.value = "smart";
        activeExecution.value = "wait";
        resumeFailed.checked = current.restart?.resume_failed_input !== false;
        if (current.restart?.status === "failed") {
          const failedTarget = current.restart.target || {};
          if ([...provider.options].some(option => option.value === failedTarget.provider)) provider.value = failedTarget.provider;
          populateAccounts(failedTarget.account_id);
          const requestedStrategy = current.restart.requested_strategy || current.restart.context_strategy;
          if ([...strategy.options].some(option => option.value === requestedStrategy)) strategy.value = requestedStrategy;
        }
        const sourceBinding = current.source || {};
        const bindingStatus = { current: "当前绑定", unbound: "尚未绑定" }[sourceBinding.binding_status] || "未提供";
        bindingFields.innerHTML = [
          ["Provider", sourceLabel],
          ["账号", sourceAccount || "未提供"],
          ["绑定状态", bindingStatus],
          ["Session ID", sourceBinding.backend_session_id || "未绑定或未提供"],
          ["Provider Session 引用", sourceBinding.provider_session_ref || "未绑定或未提供"],
          ["状态版本（Generation）", sourceBinding.generation || "未提供"],
        ].map(([label, value]) => window.PortalI18n.html`<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
        source.textContent = current.source?.has_session
          ? window.PortalI18n.text`当前：${sourceLabel}${sourceAccount ? ` · ${sourceAccount}` : ""}。切换成功前会继续保留当前 session。`
          : sourceProvider === "provider_api"
          ? window.PortalI18n.text`当前：${sourceLabel}${sourceAccount ? ` · ${sourceAccount}` : ""}，通过 API 直接调用。迁移到原生 Backend 时会按上下文方案初始化新 Session，成功后再切换。`
          : window.PortalI18n.text`尚未创建 Provider session。当前配置：${sourceLabel}${sourceAccount ? ` · ${sourceAccount}` : ""}。可以直接修改，不会启动模型。`;
        update();
        dialog?.open?.({ focusTarget: provider });
        void loadModelCatalog();
        if (current.resident_id && restartService) {
          unobserve = restartService.observeSessionRestart(current.resident_id, view => {
            restartView = view;
            const restartState = view.restart || current.restart;
            const wasTracking = tracking;
            tracking = sessionRestartActive(restartState);
            if (tracking && restartState?.target) {
              provider.value = restartState.target.provider || provider.value;
              populateAccounts(restartState.target.account_id);
              source.textContent = restartState.status === "cancelling"
                ? window.PortalI18n.t("正在取消已提交的迁移；原 Session 将继续保留。")
                : window.PortalI18n.t("正在跟踪已提交的迁移；目标已锁定。关闭窗口不会取消，可使用“取消迁移”终止。");
            } else if (wasTracking && restartState?.status !== "completed") {
              source.textContent = window.PortalI18n.t("迁移已结束，请核对当前会话状态后再操作。");
              if (restartState?.status === "failed") requestKey = createRequestKey();
            }
            progress.update({ ...view, restart: restartState });
            update();
            if (wasTracking && ["completed", "cancelled"].includes(restartState?.status)) close({ accepted: true });
          });
        }
      },
      close,
      destroy() {
        catalogRevision += 1;
        unobserve?.();
        progress.destroy();
        dialog?.destroy?.();
        overlay.remove();
      },
    };
  }

  function residentAgentHarnessSettings(settings = {}, defaults = {}) {
    const source = settings && typeof settings === "object" ? settings : {};
    const raw = source.agent_harness && typeof source.agent_harness === "object"
      ? source.agent_harness
      : {};
    const overrides = raw.features && typeof raw.features === "object" ? raw.features : {};
    const serverDefaults = defaults && typeof defaults === "object" ? defaults : {};
    const featureIds = new Set([
      ...Object.keys(serverDefaults),
      ...Object.keys(overrides),
    ]);
    const features = Object.fromEntries([...featureIds].map(featureId => {
      const baseline = serverDefaults[featureId] && typeof serverDefaults[featureId] === "object"
        ? serverDefaults[featureId] : {};
      const override = overrides[featureId] && typeof overrides[featureId] === "object"
        ? overrides[featureId] : {};
      return [featureId, {
        ...baseline,
        ...override,
        enabled: typeof override.enabled === "boolean" ? override.enabled : baseline.enabled === true,
      }];
    }));
    return {
      mode: String(raw.mode || "").trim().toLowerCase() === "manual" ? "manual" : "auto",
      features,
    };
  }

  function residentAgentHarnessFeatureConfigUpdate(settings = {}, featureId = "", patch = {}, defaults = {}) {
    const harness = residentAgentHarnessSettings(settings, defaults);
    const id = String(featureId || "").trim();
    if (!id || !Object.prototype.hasOwnProperty.call(harness.features, id)) return {};
    const update = patch && typeof patch === "object" ? patch : {};
    return {
      agent_harness: {
        ...harness,
        features: {
          ...harness.features,
          [id]: {
            ...harness.features[id],
            ...update,
          },
        },
      },
    };
  }

  function residentAgentHarnessFeatureUpdate(settings = {}, featureId = "", enabled = false, defaults = {}) {
    return residentAgentHarnessFeatureConfigUpdate(
      settings,
      featureId,
      { enabled: enabled === true },
      defaults,
    );
  }

  function residentAgentHarnessModeUpdate(settings = {}, automatic = true, defaults = {}) {
    const harness = residentAgentHarnessSettings(settings, defaults);
    return {
      agent_harness: {
        ...harness,
        mode: automatic === true ? "auto" : "manual",
      },
    };
  }

  function residentAgentHarnessFeatureTooltip(featureId = "", config = {}, options = {}) {
    const feature = config && typeof config === "object" ? config : {};
    const supported = options.supported !== false;
    const unsupported = supported
      ? ""
      : window.PortalI18n.t(" 当前后端不具备这个组件需要的能力，因此开关不可用。");
    const thresholdKiB = Math.round(Number(feature.trigger_inline_media_bytes) / 1024);
    const keepRecentImages = Number(feature.keep_recent_images);
    const offloadKiB = Math.round(Number(feature.offload_trigger_bytes) / 1024);
    const minDuplicateTokens = Number(feature.min_output_tokens);
    const descriptions = {
      inline_media_history: window.PortalI18n.text`当内联图片合计超过 ${thresholdKiB} KiB 时，只让模型继续看到最近 ${keepRecentImages} 张。“工具返回后”在下一次模型调用前治理可拦截的 Harness 工具图片。旧图片仍留在 Portal 对话里，只退出模型的活跃上下文；通常能显著降低长图片会话的输入量和等待时间，但重新分析旧图时需要再次把它带回上下文。`,
      tool_call_granularity: window.PortalI18n.t("在每轮 Prompt 中加入通用执行策略，鼓励模型合并或并行互不依赖的只读搜索、读取和检查，从而减少模型与工具之间的往返轮次。它不会强制模型采纳，也不会并行写入、破坏性操作或有先后依赖的步骤。"),
      tool_output_offloading: window.PortalI18n.text`当单条工具输出超过 ${offloadKiB} KiB 时，活跃上下文只保留首尾预览、哈希和安全句柄，精确原文完整存入 Portal，并可通过 read_harness_artifact 分块召回。“工具返回后”会在下一次模型调用前立即治理 Harness 工具结果，召回工具在新会话开始时挂载；后端自己执行的内置 shell/read 工具结果不经过 Portal，不能拦截。它通常能大幅减少超长日志或命令输出的 tokens；如果关键细节不在预览中，模型会多一次召回操作。`,
      artifact_search_and_slice: window.PortalI18n.t("让模型先在已外置的大型工具输出中做本地关键词搜索，只接收少量命中片段和精确偏移，再按需读取目标切片。它不会搜索未授权给当前 session 的产物，通常比从头分块读取整份日志省很多 tokens；仅对已被“限制并外置大型工具输出”保存的内容有收益，工具在新会话开始时挂载。"),
      hash_aware_read_dedup: window.PortalI18n.text`同一只读工具的工具名、参数和内容哈希完全相同，且输出至少约 ${minDuplicateTokens} tokens 时，只保留一份精确原文。“工具返回后”会在同一 Turn 的下一次模型调用前立即去重 Portal 执行的工具结果；后端自己执行的内置 shell/read 工具结果不经过 Portal，不能拦截。它能安全移除重复读取产生的 tokens；写入操作、参数不同或内容变化的结果不会合并。`,
      unified_resource_recall: window.PortalI18n.t("当当前消息明确引用统一资源 ID 时，让模型先通过 ResourceService 在正文中本地搜索，再只读取命中附近的精确切片。正文不会预先塞进 Prompt，仍按当前用户重新鉴权；它适合大型 Decision、文件或会话归档，能避免整份内容进入上下文。工具在新会话开始时挂载，关闭时 Portal 原有资源读取方式不受影响。"),
      early_context_compaction: window.PortalI18n.text`压缩阈值由后端策略参数计算。“Turn 前检查”会在发送新 Prompt 前检查并按需 compact 已有 Session；“Agent Loop 自动”会配置 Provider 原生阈值，让工具结果推高上下文后在下一次模型调用前自动 compact。它不引入 Portal 自己的摘要模型，但 compact 仍可能丢失细节，保真度取决于 Provider 原生机制。`,
      native_tool_loop_governor: window.PortalI18n.text`观察后端的原生工具事件，维护语义读取账本。后端支持运行中输入时，在工具调用、重复读取或大型输出达到阈值时注入最多 ${Number(feature.max_checkpoints)} 次软检查点；不支持运行中输入的后端只记账，供下一轮策略使用。它会统计内置 shell/read 的输出压力，但不能改写后端自己执行的工具结果。`,
      provider_error_continue: window.PortalI18n.t("独立开关，在智能选择与手动模式下都生效，自动策略不会覆盖你的选择。开启后，Provider 接手这一轮之后出错时，在报错 3 秒后于同一会话补发一次 continue。用户取消（包括这 3 秒内取消）、额度用完、凭证或安全类错误、上下文超长都不补发。补发后再失败，这一轮按失败结束，不会循环。"),
    };
    return `${descriptions[String(featureId || "").trim()] || ""}${unsupported}`.trim();
  }

  const PROMPT_INJECTION_PRESET_LABELS = {
    standard: "标准", full: "每轮完整", minimal: "精简", pure: "纯 TUI", custom: "自定义",
  };
  const PROMPT_INJECTION_PREVIEW_TURNS = [
    ["first", "会话第一轮"],
    ["later", "之后的轮次"],
    ["after_compaction", "上下文压缩后的第一轮"],
  ];

  function promptInjectionPresetLabel(value) {
    const preset = String(value?.preset || "standard");
    return PROMPT_INJECTION_PRESET_LABELS[preset] || preset;
  }

  // The Prompt 注入 panel: a preset, each category's timing with the place the
  // Provider puts it, and a preview of what a turn would carry.
  function residentPromptInjectionPanelHtml(view = {}, options = {}) {
    const busy = options.busy === true;
    const preview = options.preview || null;
    const turn = options.previewTurn || "first";
    const presets = (view.presets || []).filter(item => item.id !== "custom" || view.preset === "custom");
    const option = (value, label, selected, disabled = false) =>
      `<option value="${escapeHtml(value)}" ${selected ? "selected" : ""} ${disabled ? "disabled" : ""}>${escapeHtml(label)}</option>`;
    const rows = (view.parts || []).map(part => `<label class="chatHarnessTiming chatPromptInjectionRow">
        <span class="chatHarnessTimingText"><strong>${escapeHtml(part.label)}</strong><small>${escapeHtml(part.description)}</small><small class="chatPromptInjectionPlace">位置：${escapeHtml(part.placement || "")}</small></span>
        <select data-prompt-injection-part="${escapeHtml(part.id)}" aria-label="${escapeHtml(part.label)}注入时机" ${busy ? "disabled" : ""}>
          ${(part.choices || []).map(choice => option(choice.id, choice.label, choice.id === part.choice)).join("")}
        </select>
      </label>`).join("");
    const previewParts = preview
      ? (preview.parts || []).map(part => `<li class="${part.sent ? "sent" : ""}">${part.sent ? "发送" : "不发送"} · ${escapeHtml(part.label)} · ${Number(part.chars || 0)} 字</li>`).join("")
      : "";
    const previewText = preview
      ? `【系统位置】\n${preview.system_prompt || "（无）"}\n\n【本轮消息】\n${preview.prompt || ""}`
      : (options.previewError ? `预览失败：${options.previewError}` : "正在生成预览...");
    return `<div class="chatPromptInjection">
      <button type="button" class="chatPromptInjectionBack" data-prompt-injection-back>‹ Prompt 注入</button>
      <label class="chatHarnessTiming chatPromptInjectionRow">
        <span class="chatHarnessTimingText"><strong>预设</strong><small>纯 TUI 不注入任何 Portal 内容；改单个类别后记为自定义</small></span>
        <select data-prompt-injection-preset aria-label="Prompt 注入预设" ${busy ? "disabled" : ""}>
          ${presets.map(item => option(item.id, item.label, item.id === view.preset, item.id === "custom")).join("")}
        </select>
      </label>
      ${rows}
      <div class="chatPromptInjectionPreview">
        <label class="chatHarnessTiming chatPromptInjectionRow">
          <span class="chatHarnessTimingText"><strong>预览</strong><small>消息正文用占位文字</small></span>
          <select data-prompt-injection-preview-turn aria-label="预览的轮次">
            ${PROMPT_INJECTION_PREVIEW_TURNS.map(([value, label]) => option(value, label, value === turn)).join("")}
          </select>
        </label>
        ${previewParts ? `<ul class="chatPromptInjectionParts">${previewParts}</ul>` : ""}
        <pre class="chatPromptInjectionText" data-prompt-injection-preview>${escapeHtml(previewText)}</pre>
      </div>
    </div>`;
  }

  // The Resident Menu's Harness page: Prompt 注入, sub agent, 失败续跑 and
  // the Harness features, saved as the Resident's own settings.
  function mountResidentHarnessPanel({ container, residentId, onChanged } = {}) {
    if (!container || !residentId) throw new TypeError("container and residentId are required");
    let detail = null;
    let loadError = "";
    let busy = false;
    let destroyed = false;
    let promptInjection = null;
    const execute = (instruction, args, options) => window.PortalInstructionClient.execute(instruction, args, options);
    const load = async ({ force = false } = {}) => {
      try {
        detail = await window.PortalResidentProjection.load(residentId, { force });
        loadError = "";
      } catch (error) {
        loadError = error?.message || String(error);
      }
      render();
    };
    const run = async action => {
      if (busy || destroyed) return;
      busy = true;
      render();
      try {
        await action();
      } catch (error) {
        if (!destroyed) window.PortalUI?.toast?.(error?.message || String(error), "error");
      } finally {
        busy = false;
        render();
      }
    };
    const refresh = async () => {
      window.PortalResidentProjection?.invalidate?.(residentId);
      await onChanged?.();
      await load({ force: true });
    };
    const save = changes => run(async () => {
      await execute("UpdateResidentSettings", { resident_id: residentId, changes });
      await refresh();
    });

    function render() {
      if (destroyed) return;
      if (promptInjection) {
        renderPromptInjection();
        return;
      }
      if (loadError) {
        container.innerHTML = window.PortalI18n.html`<div class="paneNote" role="status">Harness 设置读取失败：${escapeHtml(loadError)}</div>`;
        return;
      }
      if (!detail) {
        container.innerHTML = window.PortalI18n.markup('<div class="chatHarnessLoading">正在读取 Harness 设置...</div>');
        return;
      }
      const settings = detail.runtime_settings || {};
      const harnessDefaults = detail.agent_harness_defaults || {};
      container.innerHTML = `<div class="residentHarnessPanel">${residentHarnessMenuHtml(settings, {
        busy,
        runtimeCapabilities: detail.backend_capabilities?.runtime_capabilities,
        harnessDefaults,
      })}</div>`;
      const root = container.firstElementChild;
      root.querySelector("[data-sub-agent-toggle]")?.addEventListener("change", event => {
        save({ allow_subagents: !!event.currentTarget.checked });
      });
      root.querySelector("[data-prompt-injection-open]")?.addEventListener("click", () => openPromptInjection());
      root.querySelector("[data-agent-harness-mode-toggle]")?.addEventListener("change", event => {
        save(residentAgentHarnessModeUpdate(settings, !!event.currentTarget.checked, harnessDefaults));
      });
      root.querySelectorAll("[data-agent-harness-feature-toggle]").forEach(control => {
        control.addEventListener("change", () => save(residentAgentHarnessFeatureUpdate(settings, control.dataset.agentHarnessFeatureToggle, control.checked, harnessDefaults)));
      });
      root.querySelectorAll("[data-agent-harness-feature-timing]").forEach(control => {
        control.addEventListener("change", () => save(residentAgentHarnessFeatureConfigUpdate(settings, control.dataset.agentHarnessFeatureTiming, { timing: control.value }, harnessDefaults)));
      });
    }

    async function openPromptInjection() {
      if (busy) return;
      const state = { view: null, preview: null, previewTurn: "first", previewError: "" };
      promptInjection = state;
      await run(async () => {
        const result = await execute("GetResidentSettings", { resident_id: residentId });
        if (promptInjection === state) state.view = result?.prompt_injection || null;
      });
      if (promptInjection !== state) return;
      if (!state.view) {
        // A failed read goes back to the Harness rows instead of a stuck loading line.
        promptInjection = null;
        render();
        return;
      }
      loadPromptInjectionPreview();
    }

    async function loadPromptInjectionPreview() {
      const state = promptInjection;
      if (!state || destroyed) return;
      const turn = state.previewTurn;
      try {
        const preview = await execute("PreviewPromptInjection", { resident_id: residentId, turn });
        if (promptInjection !== state || state.previewTurn !== turn) return;
        state.preview = preview;
        state.previewError = "";
      } catch (error) {
        if (promptInjection !== state) return;
        state.preview = null;
        state.previewError = error?.message || String(error);
      }
      render();
    }

    function renderPromptInjection() {
      const state = promptInjection;
      if (!state.view) {
        container.innerHTML = window.PortalI18n.markup('<div class="chatHarnessLoading">正在读取 Prompt 注入设置...</div>');
        return;
      }
      container.innerHTML = `<div class="residentHarnessPanel">${residentPromptInjectionPanelHtml(state.view, {
        busy,
        preview: state.preview,
        previewTurn: state.previewTurn,
        previewError: state.previewError,
      })}</div>`;
      const root = container.firstElementChild;
      const update = changes => run(async () => {
        const result = await execute("UpdateResidentSettings", {
          resident_id: residentId, changes: { prompt_injection: changes },
        }, { idempotencyKey: `prompt-injection:${globalThis.PortalUIPrimitives.randomUUID()}` });
        if (promptInjection !== state) return;
        state.view = result.prompt_injection;
        state.preview = null;
        await refresh();
        loadPromptInjectionPreview();
      });
      root.querySelector("[data-prompt-injection-back]")?.addEventListener("click", () => {
        promptInjection = null;
        render();
      });
      root.querySelector("[data-prompt-injection-preset]")?.addEventListener("change", event => {
        update({ preset: event.currentTarget.value });
      });
      root.querySelectorAll("[data-prompt-injection-part]").forEach(control => {
        control.addEventListener("change", () => update({ parts: { [control.dataset.promptInjectionPart]: control.value } }));
      });
      root.querySelector("[data-prompt-injection-preview-turn]")?.addEventListener("change", event => {
        state.previewTurn = event.currentTarget.value;
        state.preview = null;
        render();
        loadPromptInjectionPreview();
      });
    }

    render();
    void load();
    return {
      reload: () => load({ force: true }),
      destroy() {
        destroyed = true;
        container.replaceChildren();
      },
    };
  }

  function residentHarnessMenuHtml(settings = {}, options = {}) {
    if (!options.harnessDefaults || !Object.keys(options.harnessDefaults).length) {
      return window.PortalI18n.markup('<div class="chatHarnessLoading">正在读取扩展配置...</div>');
    }
    const busy = options.busy === true;
    const supportedFeatures = new Set(options.runtimeCapabilities?.harness_features || []);
    const harness = residentAgentHarnessSettings(settings, options.harnessDefaults || {});
    const harnessAutomatic = harness.mode === "auto";
    const harnessControlsDisabled = busy || harnessAutomatic;
    const inlineMedia = harness.features.inline_media_history;
    const toolGranularity = harness.features.tool_call_granularity;
    const toolOutputOffloading = harness.features.tool_output_offloading;
    const artifactSearchAndSlice = harness.features.artifact_search_and_slice;
    const readDedup = harness.features.hash_aware_read_dedup;
    const unifiedResourceRecall = harness.features.unified_resource_recall;
    const earlyCompaction = harness.features.early_context_compaction;
    const nativeToolLoop = harness.features.native_tool_loop_governor;
    const nativeToolLoopBudgetKnown = Number.isFinite(nativeToolLoop.checkpoint_interval)
      && Number.isFinite(nativeToolLoop.max_checkpoints);
    const providerErrorContinue = harness.features.provider_error_continue;
    const inlineMediaSupported = supportedFeatures.has("inline_media_history");
    const toolOutputOffloadingSupported = supportedFeatures.has("tool_output_offloading");
    const artifactSearchAndSliceSupported = supportedFeatures.has("artifact_search_and_slice");
    const readDedupSupported = supportedFeatures.has("hash_aware_read_dedup");
    const unifiedResourceRecallSupported = supportedFeatures.has("unified_resource_recall");
    const earlyCompactionSupported = supportedFeatures.has("early_context_compaction");
    const nativeToolLoopSupported = supportedFeatures.has("native_tool_loop_governor");
    const providerErrorContinueSupported = supportedFeatures.has("provider_error_continue");
    // Only a Provider whose model can start subagents has the switch (capability "subagents").
    const subagentsSupported = (options.runtimeCapabilities?.harness_capabilities || []).includes("subagents");
    const inlineMediaPolicy = `超过 ${Math.round(inlineMedia.trigger_inline_media_bytes / 1024)} KiB 时仅保留最新 ${inlineMedia.keep_recent_images} 张，旧图片退出活跃上下文`;
    const harnessTooltip = (featureId, featureConfig, supported = true) => escapeHtml(
      residentAgentHarnessFeatureTooltip(featureId, featureConfig, { supported }),
    );
    const harnessTimingControl = (featureId, featureConfig, label, options = {}) => {
      const supported = options.supported !== false;
      const selectable = options.selectable !== false;
      const timing = ["before_turn", "after_tool_batch", "both"].includes(String(featureConfig?.timing || ""))
        ? String(featureConfig.timing)
        : String(options.defaultTiming || "both");
      const detail = String(options.detail || "工具返回后目前覆盖 Harness 工具");
      if (!selectable) {
        const fixedLabel = String(options.fixedLabel || ({
          before_turn: window.PortalI18n.t("发送前"),
          after_tool_batch: window.PortalI18n.t("工具返回后"),
          both: window.PortalI18n.t("发送前 + 工具返回后"),
        }[timing] || timing));
        return window.PortalI18n.html`<div class="chatHarnessTiming" data-agent-harness-timing-display="${escapeHtml(featureId)}" ${supported ? "" : "aria-disabled=\"true\""}>
          <span class="chatHarnessTimingText"><strong>运行时机</strong><small>${escapeHtml(detail)}</small></span>
          <output class="chatHarnessTimingValue" aria-label="${escapeHtml(label)}">${escapeHtml(fixedLabel)}</output>
        </div>`;
      }
      const timingOptions = Array.isArray(options.timingOptions) && options.timingOptions.length
        ? options.timingOptions
        : [
          ["both", "两处都处理（推荐）"],
          ["before_turn", "仅发送前历史"],
          ["after_tool_batch", "仅工具返回后"],
        ];
      const option = (value, text) => window.PortalI18n.html`<option value="${value}" ${timing === value ? "selected" : ""}>${text}</option>`;
      return window.PortalI18n.html`<label class="chatHarnessTiming">
        <span class="chatHarnessTimingText"><strong>运行时机</strong><small>${escapeHtml(detail)}</small></span>
        <select data-agent-harness-feature-timing="${escapeHtml(featureId)}" aria-label="${escapeHtml(label)}" ${harnessControlsDisabled || !supported ? "disabled" : ""}>
          ${timingOptions.map(([value, text]) => option(value, text)).join("")}
        </select>
      </label>`;
    };
    return [
      window.PortalI18n.html`<button type="button" class="chatSessionSwitch chatSessionNav" data-prompt-injection-open ${busy ? "disabled" : ""}>
        <span class="chatSessionSwitchText"><strong>Prompt 注入</strong><small>${escapeHtml(promptInjectionPresetLabel(settings.prompt_injection))} · 按类别选择 Portal 规则、身份与授权等内容的注入时机</small></span>
        <span class="chatSessionNavArrow" aria-hidden="true">›</span>
      </button>`,
      subagentsSupported ? window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>允许开启 sub agent</strong><small>关闭时模型不能启动子 agent</small></span>
        <input type="checkbox" role="switch" data-sub-agent-toggle ${settings.allow_subagents ? "checked" : ""} ${busy ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>` : "",
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>失败后续跑一次</strong><small>${providerErrorContinueSupported ? window.PortalI18n.t("独立开关；失败后自动发送一次 continue，主动取消不触发。不受智能选择模式限制") : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="provider_error_continue" data-portal-tooltip="${harnessTooltip("provider_error_continue", providerErrorContinue, providerErrorContinueSupported)}" ${providerErrorContinue.enabled ? "checked" : ""} ${busy || !providerErrorContinueSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      window.PortalI18n.html`<div class="chatExtensionDivider" role="separator"></div>`,
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>智能选择 Harness</strong><small>根据本 Session 的上下文压力、图片、大型输出、重复读取和工具调用反馈，逐 Turn 选择功能、时机与预算；开启时下方只显示保留的手动预设</small></span>
        <input type="checkbox" role="switch" data-agent-harness-mode-toggle ${harnessAutomatic ? "checked" : ""} ${busy ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>治理原生工具循环</strong><small>${nativeToolLoopSupported ? (nativeToolLoopBudgetKnown ? window.PortalI18n.text`每 ${nativeToolLoop.checkpoint_interval} 次工具调用检查一次，最多软干预 ${nativeToolLoop.max_checkpoints} 次` : window.PortalI18n.t("使用服务端默认预算")) : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="native_tool_loop_governor" data-portal-tooltip="${harnessTooltip("native_tool_loop_governor", nativeToolLoop, nativeToolLoopSupported)}" ${nativeToolLoop.enabled ? "checked" : ""} ${harnessControlsDisabled || !nativeToolLoopSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      harnessTimingControl(
        "native_tool_loop_governor",
        nativeToolLoop,
        "原生工具循环治理运行时机",
        {
          supported: nativeToolLoopSupported,
          selectable: false,
          fixedLabel: "Agent Loop · 原生事件 + steer",
          detail: "共享 reader 只记账；实际 steer 由 Turn 执行线程发送",
        },
      ),
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>治理历史内联图片</strong><small>${inlineMediaSupported ? inlineMediaPolicy : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="inline_media_history" data-portal-tooltip="${harnessTooltip("inline_media_history", inlineMedia, inlineMediaSupported)}" ${inlineMedia.enabled ? "checked" : ""} ${harnessControlsDisabled || !inlineMediaSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      harnessTimingControl(
        "inline_media_history",
        inlineMedia,
        "内联图片治理运行时机",
        {
          selectable: false, fixedLabel: "工具返回后",          supported: inlineMediaSupported,
          detail: "工具返回后仅覆盖可拦截的 Harness 工具图片",
        },
      ),
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>批量化独立工具调用</strong><small>鼓励合并或并行只读的搜索、读取与检查；写入和依赖步骤仍保持串行</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="tool_call_granularity" data-portal-tooltip="${harnessTooltip("tool_call_granularity", toolGranularity)}" ${toolGranularity.enabled ? "checked" : ""} ${harnessControlsDisabled ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      harnessTimingControl(
        "tool_call_granularity",
        toolGranularity,
        "批量化独立工具调用运行时机",
        {
          selectable: false,
          fixedLabel: "发送前 · Prompt 策略",
          detail: "每轮 Prompt 组装时注入通用执行策略",
        },
      ),
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>限制并外置大型工具输出</strong><small>${toolOutputOffloadingSupported ? window.PortalI18n.text`单条超过 ${Math.round(toolOutputOffloading.offload_trigger_bytes / 1024)} KiB 时，仅保留预览与可召回的精确原文` : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="tool_output_offloading" data-portal-tooltip="${harnessTooltip("tool_output_offloading", toolOutputOffloading, toolOutputOffloadingSupported)}" ${toolOutputOffloading.enabled ? "checked" : ""} ${harnessControlsDisabled || !toolOutputOffloadingSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      harnessTimingControl(
        "tool_output_offloading",
        toolOutputOffloading,
        "大型工具输出运行时机",
        { supported: toolOutputOffloadingSupported },
      ),
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>搜索并切片外置输出</strong><small>${artifactSearchAndSliceSupported ? window.PortalI18n.t("先搜索命中位置，再读取少量精确片段；需配合已外置的工具输出") : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="artifact_search_and_slice" data-portal-tooltip="${harnessTooltip("artifact_search_and_slice", artifactSearchAndSlice, artifactSearchAndSliceSupported)}" ${artifactSearchAndSlice.enabled ? "checked" : ""} ${harnessControlsDisabled || !artifactSearchAndSliceSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      harnessTimingControl(
        "artifact_search_and_slice",
        artifactSearchAndSlice,
        "外置输出搜索运行时机",
        {
          supported: artifactSearchAndSliceSupported,
          selectable: false,
          fixedLabel: "发送前 · 工具注册",
          detail: "进入 Provider 前注册搜索与切片召回工具",
        },
      ),
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>去重重复只读结果</strong><small>${readDedupSupported ? window.PortalI18n.text`同一只读调用返回完全相同内容时，仅保留最新原文（至少 ${readDedup.min_output_tokens} tokens）` : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="hash_aware_read_dedup" data-portal-tooltip="${harnessTooltip("hash_aware_read_dedup", readDedup, readDedupSupported)}" ${readDedup.enabled ? "checked" : ""} ${harnessControlsDisabled || !readDedupSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      harnessTimingControl(
        "hash_aware_read_dedup",
        readDedup,
        "只读结果去重运行时机",
        { supported: readDedupSupported },
      ),
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>按统一资源 ID 召回正文</strong><small>${unifiedResourceRecallSupported ? window.PortalI18n.t("先搜索明确引用的资源，再按偏移读取精确切片") : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="unified_resource_recall" data-portal-tooltip="${harnessTooltip("unified_resource_recall", unifiedResourceRecall, unifiedResourceRecallSupported)}" ${unifiedResourceRecall.enabled ? "checked" : ""} ${harnessControlsDisabled || !unifiedResourceRecallSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      harnessTimingControl(
        "unified_resource_recall",
        unifiedResourceRecall,
        "统一资源召回运行时机",
        {
          supported: unifiedResourceRecallSupported,
          selectable: false,
          fixedLabel: "发送前 · 仅新 Session",
          detail: "创建新 Backend Session 前注册资源召回工具",
        },
      ),
      window.PortalI18n.html`<label class="chatSessionSwitch">
        <span class="chatSessionSwitchText"><strong>按后端策略提前 compact</strong><small>${earlyCompactionSupported ? window.PortalI18n.text`配置后端原生 compact 的触发位置` : window.PortalI18n.t("当前后端不具备这个组件需要的能力")}</small></span>
        <input type="checkbox" role="switch" data-agent-harness-feature-toggle="early_context_compaction" data-portal-tooltip="${harnessTooltip("early_context_compaction", earlyCompaction, earlyCompactionSupported)}" ${earlyCompaction.enabled ? "checked" : ""} ${harnessControlsDisabled || !earlyCompactionSupported ? "disabled" : ""} />
        <span class="chatSessionSwitchTrack" aria-hidden="true"></span>
      </label>`,
      harnessTimingControl(
        "early_context_compaction",
        earlyCompaction,
        "提前 compact 运行时机",
        {
          supported: earlyCompactionSupported,
          defaultTiming: "after_tool_batch",
          detail: "阈值固定为 70%；Agent Loop 模式使用 Provider 原生 compact",
          timingOptions: [
            ["after_tool_batch", "Agent Loop 自动（推荐）"],
            ["before_turn", "仅 Turn 前检查"],
            ["both", "两处都检查"],
          ],
        },
      ),
    ].join("");
  }

  let workspaceInstanceSequence = 0;

  function workspaceInstanceId(options = {}, prefix = "workspace") {
    const explicit = String(
      options.id
      || [options.workbenchId, options.panelId].filter(Boolean).join("-")
      || "",
    ).trim();
    if (explicit) return explicit;
    workspaceInstanceSequence += 1;
    return `${prefix}_${workspaceInstanceSequence}`;
  }

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

  function createWorkspace(options = {}) {
    const root = typeof options.root === "string" ? document.querySelector(options.root) : options.root;
    if (!root) throw new Error("PortalChatWorkspace.createWorkspace root required");
    root.__portalChatWorkspaceController?.destroy?.();
    const id = workspaceInstanceId(options);
    const refsScope = String(options.refsScope || id);
    const ownedAttachmentPreviews = new Set();
    const releaseAttachmentPreview = value => {
      if (!ownedAttachmentPreviews.delete(value)) return;
      URL.revokeObjectURL(value);
    };
    const releaseAttachmentPreviews = items => {
      for (const item of items || []) releaseAttachmentPreview(item?.preview_url);
    };
    const composerResizeEnabled = options.composerResize !== false;
    const composerHeightStorageKey = String(options.composerHeightKey || `portal.workspaceComposerHeight.${id}`);
    const defaultComposerInputHeight = Math.max(56, Number(options.composerInputHeight || 86));
    const readStoredComposerInputHeight = () => {
      const value = Number(window.PortalState.storage.readText(composerHeightStorageKey, ""));
      return Number.isFinite(value) && value > 0 ? value : defaultComposerInputHeight;
    };
    const writeStoredComposerInputHeight = (value) => {
      if (!composerHeightStorageKey) return;
      window.PortalState.storage.writeText(composerHeightStorageKey, Math.round(value));
    };
    root.classList.add("portalChatWorkspaceRoot");
    // The shared Workspace owns its catalog lifecycle, independently of host loading.
    const refsCatalog = {
      status: options.contextOptions ? "ready" : "idle",
      value: {},
      request: null,
      disposed: false,
    };
    const contextOptions = () => typeof options.contextOptions === "function"
      ? (options.contextOptions() || {})
      : (options.contextOptions || refsCatalog.value);
    const loadRefsCatalog = () => {
      if (refsCatalog.disposed || refsCatalog.status === "ready" || refsCatalog.request) return;
      refsCatalog.status = "loading";
      controller.refreshRefs();
      refsCatalog.request = Promise.resolve()
        .then(() => window.PortalAPI.get("/api/agent-context-options", { cache: "no-store" }))
        .then(payload => {
          if (refsCatalog.disposed) return;
          if (!Array.isArray(payload?.skills) || !Array.isArray(payload?.agents)) {
            throw new Error("Invalid context options catalog");
          }
          refsCatalog.value = { skills: payload.skills, agents: payload.agents };
          refsCatalog.status = "ready";
        })
        .catch(() => {
          if (!refsCatalog.disposed) refsCatalog.status = "error";
        })
        .finally(() => {
          refsCatalog.request = null;
          if (!refsCatalog.disposed) controller.refreshRefs();
        });
    };
    const state = {
      attachments: [],
      refs: normalizeRefs(options.refsSelection || {}),
      refsCollapsed: options.refsCollapsed || {},
      messages: [],
      runtimeDetails: [],
      runtimeLive: false,
      runtimePhase: "",
      loading: false,
      loadingMessage: "",
      busy: false,
      contract: null,
      composer: {},
      composerSignature: "",
      composerQueueSignature: "",
      composerInputHeight: readStoredComposerInputHeight(),
      messageRenderSignature: "",
      messageItemSignatures: [],
      messageRenderContextSignature: "",
      messageRendererRefs: [],
      messageRenderOptions: {},
      collapsedAgentGroups: new Set(),
      collapsedWorkGroups: new Set(),
      expandedAgentGroups: new Set(),
      messageFoldDefaults: new Set(),
      messageFoldSizes: new Map(),
      latestFoldReplyKey: "",
      latestFoldReplyAt: -Infinity,
      undoStack: [],
      recentFileSignatures: new Map(),
      composerInputFocused: false,
      composerImeActive: false,
      slashCommandItems: [],
      slashCommandActiveIndex: 0,
      mentionItems: [],
      mentionActiveIndex: 0,
      mentionQuery: null,
      mentionDismissed: null,
      activeDraftScope: "",
      recoveredSubmission: null,
      restoringDraft: false,
      submissionSequence: 0,
      updateRevision: "",
      messageByKey: new Map(),
      messageScrollScope: null,
      messageTailFollowIntent: true,
      subtitle: String(options.subtitle || ""),
      subtitleParts: normalizeWorkspaceSubtitleParts(options.subtitleParts),
    };
    const dom = {
      messageListId: `${id}Messages`,
      composerId: `${id}Composer`,
      inputId: `${id}Input`,
      trayId: `${id}AttachmentTray`,
      fileInputId: `${id}FileInput`,
      attachId: `${id}Attach`,
      sendId: `${id}Send`,
      stopId: `${id}Stop`,
      autoId: `${id}Auto`,
      refreshId: `${id}Refresh`,
      composerSplitterId: `${id}ComposerSplitter`,
      titleId: `${id}Title`,
      statusId: `${id}Status`,
      bodyId: `${id}Body`,
      loadingId: `${id}Loading`,
    };
    root.innerHTML = workspaceShellHtml({
      title: options.title || window.PortalI18n.t("Chat Workspace"),
      subtitle: options.subtitle || "",
      refreshId: options.onRefresh && !options.onMenu ? dom.refreshId : "",
      messageListId: dom.messageListId,
      composerId: dom.composerId,
      composerSplitterId: composerResizeEnabled ? dom.composerSplitterId : "",
      titleId: dom.titleId,
      statusId: dom.statusId,
      bodyId: dom.bodyId,
      loadingId: dom.loadingId,
      extraActionsHtml: options.extraActionsHtml || "",
      bodyPrefixHtml: options.bodyPrefixHtml || "",
      bodySuffixHtml: options.bodySuffixHtml || "",
      afterBodyHtml: options.afterBodyHtml || "",
    });
    // A tabpanel belongs to its host tab; preserve that accessible name.
    const hostLabelledBy = (root.getAttribute("aria-labelledby") || "").trim().split(/\s+/)
      .some(labelId => root.ownerDocument.getElementById(labelId));
    const hostNamesTabPanel = root.getAttribute("role") === "tabpanel"
      && (hostLabelledBy || root.getAttribute("aria-label")?.trim());
    if (!hostNamesTabPanel) {
      root.setAttribute("aria-labelledby", dom.titleId);
      root.removeAttribute("aria-label");
    }
    const composer = root.querySelector(`#${CSS.escape(dom.composerId)}`);
    let refsControlBinding = null;
    let conversationSearch = null;
    let runtimeSettingsDialog = null;
    let sessionRestartDialog = null;
    let runtimeCatalogEntry = null;
    let residentRuntime = null, runtimeResidentId = "", runtimeTargetPending = false;
    const releaseResidentRuntime = () => {
      residentRuntime?.destroy(); residentRuntime = null; runtimeResidentId = ""; runtimeCatalogEntry = null;
    };
    const residentRuntimeController = () => {
      if (runtimeTargetPending) return null;
      const residentId = contractResidentId();
      if (runtimeResidentId !== residentId) releaseResidentRuntime();
      if (!residentId) return null;
      if (!residentRuntime) {
        runtimeResidentId = residentId;
        residentRuntime = window.PortalResidentRuntimeController.bind(residentId, {
          onState: view => {
            if (contractResidentId() !== residentId) return;
            if (view.detail) state.composer.runtime_settings = view.settings;
            if (view.catalog && runtimeCatalogEntry) runtimeCatalogEntry.catalog = view.catalog;
            const button = composer.querySelector("[data-workspace-runtime-settings-open]");
            if (button) {
              button.disabled = view.loading || view.busy || sessionRestartActive(view.restart) || (!view.canEdit && !view.canRestart);
              const label = button.querySelector(".composerRuntimeLabel");
              if (label && view.detail) label.textContent = residentRuntimeStatusText(view.settings, view.catalog || {});
            }
          },
          onChanged: result => {
            if (contractResidentId() !== residentId) return;
            options.onRuntimeSettingsSaved?.(result, controller);
            void options.onRefresh?.(controller);
          },
        });
      }
      return residentRuntime;
    };
    // The Resident whose Chat this is; empty in other Chats (a Chatroom, an article).
    const contractResidentId = () => {
      const target = state.contract?.target || {};
      return String(target.resident_id || (target.kind === "resident" ? target.id : "") || "");
    };
    const runtimeCatalogUrl = (cfg = state.composer) => {
      const residentId = contractResidentId()
        || state.contract?.actions?.runtime_settings?.payload?.resident_id || "";
      // A Resident's own Provider wins on the server; the composer names it otherwise.
      const query = new URLSearchParams(cfg.runtime_backend ? { backend: cfg.runtime_backend } : {});
      if (residentId) query.set("resident_id", residentId);
      if (cfg.runtime_account_id) query.set("account_id", cfg.runtime_account_id);
      return `/api/agent-workbench/agent-runtime-catalog?${query}`;
    };
    const loadWorkspaceRuntimeCatalog = (cfg = state.composer, retry = false) => {
      if (runtimeTargetPending) return { catalog: null, promise: Promise.resolve({}) };
      const key = runtimeCatalogUrl(cfg);
      if (runtimeCatalogEntry?.key === key && !(retry && runtimeCatalogEntry.error)) return runtimeCatalogEntry;
      const entry = { key, catalog: cfg.runtime_catalog || null, error: null, promise: null };
      runtimeCatalogEntry = entry;
      entry.promise = (contractResidentId() ? residentRuntimeController().load().then(view => view.catalog || {})
        : entry.catalog ? Promise.resolve(entry.catalog) : window.PortalAPI.get(key))
        .then(catalog => {
          entry.catalog = catalog;
          // A late response from the previous Resident must not change this composer.
          if (runtimeCatalogEntry !== entry || runtimeCatalogUrl() !== key) return catalog;
          const button = composer.querySelector("[data-workspace-runtime-settings-open]");
          const label = button?.querySelector(".composerRuntimeLabel");
          if (label) {
            const text = residentRuntimeStatusText(state.composer.runtime_settings, catalog);
            label.textContent = text;
            button.title = window.PortalI18n.text`切换模型与运行设置：${text}`;
          }
          return catalog;
        }).catch(error => { entry.error = error; throw error; });
      // The footer can still show saved settings while a catalog is unavailable.
      entry.promise.catch(() => {});
      return entry;
    };
    const composerShellOptions = (composerOptions = {}) => {
      const cfg = {
        ...options,
        ...state.composer,
        ...composerOptions,
      };
      const canAttach = cfg.can_attach !== false;
      const refsEnabled = cfg.refs_enabled !== false;
      const editable = cfg.can_edit !== false;
      const dynamicCanStop = !state.contract && typeof options.canStop === "function"
        ? options.canStop() === true
        : false;
      const showStop = cfg.can_stop === true || dynamicCanStop;
      const capabilities = workspaceComposerCapabilities(cfg);
      const runtimeSettingsEnabled = (capabilities.has("runtime_settings")
        && cfg.runtime_settings && typeof cfg.runtime_settings === "object")
        || (contractResidentId() && state.contract?.actions?.session_restart);
      const runtimeMetaHtml = cfg.metaHtml || (
        runtimeSettingsEnabled
          ? residentRuntimeMetaHtml(cfg.runtime_settings || {}, {
              catalog: loadWorkspaceRuntimeCatalog(cfg).catalog || {},
              openAttribute: "data-workspace-runtime-settings-open",
            })
          : ""
      );
      return {
        inputId: dom.inputId,
        trayId: dom.trayId,
        fileInputId: canAttach ? dom.fileInputId : "",
        attachId: canAttach ? dom.attachId : "",
        sendId: dom.sendId,
        stopId: showStop ? dom.stopId : "",
        autoId: cfg.can_auto === false ? "" : (cfg.onAuto || options.onAuto ? dom.autoId : ""),
        refs: refsEnabled,
        editable,
        refsScope,
        placeholder: cfg.placeholder || window.PortalI18n.t("Ctrl/⌘ + Enter 发送"),
        meta: cfg.meta || "",
        metaHtml: runtimeMetaHtml,
        beforeComposerHtml: cfg.beforeComposerHtml || "",
        queueHtml: workspaceQueueHtml(cfg.queue, {
          formatTime: options.formatTime,
          previewUrlForPath: options.previewUrlForPath,
          renderMarkdown: options.renderMarkdown,
          mailbox: typeof options.onQueueMailbox === "function",
          mergeAll: typeof options.onQueueMergeAll === "function",
        }),
        afterComposerHtml: cfg.afterComposerHtml || "",
        send_label: cfg.send_label || "",
        contextOptions: contextOptions(),
        refsSelection: state.refs,
        refsCollapsed: state.refsCollapsed,
        stopDisabled: true,
        autoDisabled: !(cfg.onAuto || options.onAuto),
      };
    };
    const renderComposerShell = (composerOptions = {}) => {
      refsControlBinding?.destroy();
      refsControlBinding = null;
      composer.innerHTML = workspaceComposerHtml(composerShellOptions(composerOptions));
    };
    const els = {
      title: root.querySelector(".portalWorkspaceHead .paneTitle"),
      body: root.querySelector(".portalWorkspaceBody"),
      messages: root.querySelector(`#${CSS.escape(dom.messageListId)}`),
      composer,
      input: root.querySelector(`#${CSS.escape(dom.inputId)}`),
      tray: root.querySelector(`#${CSS.escape(dom.trayId)}`),
      fileInput: root.querySelector(`#${CSS.escape(dom.fileInputId)}`),
      attach: root.querySelector(`#${CSS.escape(dom.attachId)}`),
      send: root.querySelector(`#${CSS.escape(dom.sendId)}`),
      stop: root.querySelector(`#${CSS.escape(dom.stopId)}`),
      auto: root.querySelector(`#${CSS.escape(dom.autoId)}`),
      refresh: root.querySelector(`#${CSS.escape(dom.refreshId)}`),
      composerSplitter: root.querySelector(`#${CSS.escape(dom.composerSplitterId)}`),
      slashCommands: root.querySelector("[data-composer-slash-command-host]"),
      mentions: root.querySelector("[data-composer-mention-host]"),
      status: root.querySelector("[data-workspace-status]"),
      loading: root.querySelector("[data-workspace-loading]"),
    };
    const restartService = window.PortalResidentService;
    const restartProgress = createSessionRestartProgress({
      compact: true,
      owner: options.draftOwner,
      onOpen: () => openWorkspaceSessionRestart().catch(error => window.PortalUI.toast(error.message || String(error), "error")),
      onDismiss: () => els.refresh?.focus(),
    });
    els.status?.closest(".chatHeadText")?.append(restartProgress.element);
    let restartUnobserve = null;
    let restartResidentId = "";
    let lastRestartView = null;
    function syncSessionRestartObservation() {
      const residentId = sessionRestartAvailable() ? String(controller.target?.()?.resident_id || "") : "";
      if (residentId === restartResidentId || !restartService) return;
      restartUnobserve?.();
      restartResidentId = residentId;
      lastRestartView = null;
      restartProgress.update({});
      if (!residentId) return;
      restartUnobserve = restartService.observeSessionRestart(residentId, view => {
        const previous = lastRestartView?.restart;
        lastRestartView = view;
        restartProgress.update(view);
          if (["accepted", "cancelling"].includes(previous?.status) && previous.restart_id === view.restart?.restart_id
          && ["completed", "failed", "cancelled"].includes(view.restart.status)) {
          options.onSessionRestarted?.(view.restart, controller);
          Promise.resolve(options.onRefresh?.(controller)).catch(error => options.onError?.(error));
        }
      });
    }
    const renderSubtitle = (transientStatus = "") => {
      if (els.refresh) {
        const label = sessionRestartAvailable() ? window.PortalI18n.t("Session 操作") : window.PortalI18n.t("刷新 Chat Workspace");
        els.refresh.setAttribute("aria-label", label);
        els.refresh.setAttribute("data-portal-tooltip", label);
        els.refresh.title = label;
      }
      if (!els.status) return;
      if (!transientStatus && !state.loading) {
        if (state.composer.can_send === false) {
          transientStatus = state.composer.disabled_reason || window.PortalI18n.t("当前对话暂不可发送");
        } else {
          transientStatus = String(state.composer.send_hint || "").trim();
        }
      }
      if (state.composer.execution_gate) {
        const gate = window.PortalResidentProtocol.executionGateView(state.composer);
        transientStatus = [gate.text, transientStatus].filter(Boolean).join(" · ");
      }
      if (!state.subtitleParts.length) {
        els.status.classList.remove("hasSessionIdentity");
        els.status.textContent = transientStatus || state.subtitle;
        return;
      }
      els.status.classList.toggle(
        "hasSessionIdentity",
        state.subtitleParts.length > 0,
      );
      const fragment = document.createDocumentFragment();
      const familyButton = els.status.querySelector("[data-family-status-open]");
      state.subtitleParts.forEach((part, index) => {
        if (index) {
          const separator = document.createElement("span");
          separator.className = "chatHeaderSeparator";
          separator.textContent = "·";
          separator.dataset.nextIdentity = part.identity || "text";
          fragment.append(separator);
        }
        const opensFamily = part.identity === "family" && typeof options.onFamilyStatus === "function";
        const span = opensFamily ? familyButton || document.createElement("button") : document.createElement("span");
        if (opensFamily && !familyButton) {
          span.type = "button";
          span.dataset.familyStatusOpen = "1";
          span.title = window.PortalI18n.t("查看 Family 成员状态");
          span.setAttribute("aria-haspopup", "dialog");
          span.setAttribute("aria-expanded", "false");
          span.addEventListener("click", () => options.onFamilyStatus(span));
        }
        span.textContent = part.text;
        if (part.identity) span.dataset.workspaceIdentity = part.identity;
        if (part.copy_value) {
          span.className = "chatHeaderCopyId";
          span.dataset.workspaceCopyValue = part.copy_value;
          span.dataset.workspaceCopyLabel = part.copy_label || window.PortalI18n.t("值");
          span.title = window.PortalI18n.text`${span.dataset.workspaceCopyLabel}：${part.copy_value} · 双击复制`;
          if (part.identity === "resident") span.dataset.workspaceResidentId = part.copy_value;
          if (part.identity === "session") span.dataset.workspaceSessionId = part.copy_value;
          if (part.identity === "account") span.dataset.workspaceAccountId = part.copy_value;
        }
        fragment.append(span);
      });
      if (transientStatus) {
        const separator = document.createElement("span");
        separator.className = "chatHeaderSeparator";
        separator.textContent = "·";
        fragment.append(separator);
        const status = document.createElement("span");
        status.setAttribute("role", "status");
        status.textContent = transientStatus;
        fragment.append(status);
      }
      els.status.replaceChildren(fragment);
    };
    const handleSubtitleCopy = event => {
      const target = event.target instanceof Element
        ? event.target.closest("[data-workspace-copy-value]")
        : null;
      if (!target || !els.status?.contains(target)) return;
      const value = String(target.dataset.workspaceCopyValue || "").trim();
      if (!value) return;
      event.preventDefault();
      event.stopPropagation();
      window.PortalUI?.copyText?.(
        value,
        String(target.dataset.workspaceCopyLabel || window.PortalI18n.t("值")).trim() || window.PortalI18n.t("值"),
      );
    };
    els.status?.addEventListener("dblclick", handleSubtitleCopy);
    let messageScrollGesturePending = false;
    let messageScrollPointerActive = false;
    let messageScrollTouchActive = false;
    const messageViewportMeasurable = () => !!(
      els.messages?.isConnected
      && els.messages.clientHeight > 0
      && els.messages.getClientRects().length
    );
    const syncMessageScrollMode = () => {
      els.messages?.classList.toggle("is-following-tail", state.messageTailFollowIntent);
      els.messages?.classList.toggle("is-reading-history", !state.messageTailFollowIntent);
    };
    syncMessageScrollMode();
    const expectMessageScrollFromGesture = () => {
      messageScrollGesturePending = true;
    };
    const endMessageScrollPointerGesture = () => {
      if (!messageScrollPointerActive) return;
      messageScrollPointerActive = false;
    };
    const handleMessageScrollPointerDown = event => {
      if (event.target !== els.messages) return;
      messageScrollPointerActive = true;
      expectMessageScrollFromGesture();
    };
    const beginMessageScrollTouchGesture = () => {
      messageScrollTouchActive = true;
      expectMessageScrollFromGesture();
    };
    const endMessageScrollTouchGesture = () => {
      messageScrollTouchActive = false;
    };
    const handleMessageScrollKeyDown = event => {
      if (!["ArrowUp", "ArrowDown", "PageUp", "PageDown", window.PortalI18n.t("Home"), "End", " "].includes(event.key)) return;
      expectMessageScrollFromGesture();
    };
    const handleMessageViewportScroll = () => {
      if (!messageViewportMeasurable()) return;
      if (!messageScrollGesturePending && !messageScrollPointerActive && !messageScrollTouchActive) return;
      // A user scroll supersedes the viewport captured before a panel layout.
      // Otherwise its delayed restoration can snap a new reading position back.
      if (pendingWorkbenchViewport) {
        els.messages.style.overflowAnchor = pendingWorkbenchViewport.overflowAnchorStyle;
        pendingWorkbenchViewport = null;
        window.cancelAnimationFrame(workbenchViewportRestoreFrame);
        window.cancelAnimationFrame(workbenchViewportSettleFrame);
        workbenchViewportRestoreFrame = 0;
        workbenchViewportSettleFrame = 0;
      }
      state.messageTailFollowIntent = messageViewportAtTail(els.messages);
      workbenchAnchorKey = "";
      if (!messageScrollPointerActive && !messageScrollTouchActive) messageScrollGesturePending = false;
      syncMessageScrollMode();
    };
    let workbenchAnchorKey = "";
    let pendingWorkbenchViewport = null;
    let workbenchViewportRestoreFrame = 0;
    let workbenchViewportSettleFrame = 0;
    let windowResizeFollowFrame = 0;
    const captureWorkbenchViewport = () => {
      if (!messageViewportMeasurable()) return null;
      const viewportBox = els.messages.getBoundingClientRect();
      const rows = [...els.messages.querySelectorAll("[data-message-key]")]
        .filter(row => row.dataset.messageKey !== "__scroll_anchor__");
      // A partially clipped message can become shorter than its clipped region
      // after text reflow, so it cannot remain visible while retaining its top
      // offset. Anchor the first row whose leading edge is visible; fall back to
      // the intersecting row only when the viewport contains no such row. Until
      // the user scrolls, later layout changes keep the same row, so opening and
      // closing a sibling Panel brings the reader back to the same place.
      const leadingEdgeVisible = row => {
        const box = row.getBoundingClientRect();
        return box.top >= viewportBox.top + 1 && box.top < viewportBox.bottom;
      };
      const anchor = rows.find(row => workbenchAnchorKey && messageNodeKey(row) === workbenchAnchorKey && leadingEdgeVisible(row))
        || rows.find(leadingEdgeVisible) || rows.find(row => {
        const box = row.getBoundingClientRect();
        return box.bottom >= viewportBox.top + 1 && box.top < viewportBox.bottom;
      });
      const anchorBox = anchor?.getBoundingClientRect();
      workbenchAnchorKey = messageNodeKey(anchor);
      const anchorProgress = anchorBox?.top < viewportBox.top && anchorBox.bottom > viewportBox.bottom
        ? (viewportBox.top - anchorBox.top) / anchorBox.height
        : null;
      return {
        followTail: state.messageTailFollowIntent,
        scrollTop: els.messages.scrollTop,
        anchor,
        anchorKey: messageNodeKey(anchor),
        anchorOffset: anchorBox ? anchorBox.top - viewportBox.top : 0,
        anchorProgress,
        overflowAnchorStyle: els.messages.style.overflowAnchor,
      };
    };
    const handleBeforeWorkbenchLayout = () => {
      // Several synchronous Workbench mutations may precede one painted layout.
      // Keep the first stable viewport instead of recapturing an already-clamped
      // one, unless a user action or history-scope change has since changed the
      // authoritative tail-following intent.
      const captured = captureWorkbenchViewport();
      if (!pendingWorkbenchViewport) {
        pendingWorkbenchViewport = captured || {
          followTail: state.messageTailFollowIntent,
          scrollTop: 0,
          anchor: null,
          anchorKey: "",
          anchorOffset: 0,
          overflowAnchorStyle: els.messages.style.overflowAnchor,
        };
        els.messages.style.overflowAnchor = "none";
        return;
      }
      if (pendingWorkbenchViewport.followTail !== state.messageTailFollowIntent) {
        pendingWorkbenchViewport = captured || {
          followTail: state.messageTailFollowIntent,
          scrollTop: pendingWorkbenchViewport.scrollTop,
          anchor: null,
          anchorKey: "",
          anchorOffset: 0,
          overflowAnchorStyle: pendingWorkbenchViewport.overflowAnchorStyle,
        };
      }
    };
    const restoreWorkbenchViewport = snapshot => {
      if (!snapshot || !messageViewportMeasurable()) return;
      if (snapshot.followTail) {
        els.messages.scrollTop = els.messages.scrollHeight;
        return;
      }
      const keyedAnchor = snapshot.anchorKey
        ? els.messages.querySelector(messageInstanceSelector(snapshot.anchorKey))
        : null;
      const anchor = snapshot.anchor?.isConnected && els.messages.contains(snapshot.anchor)
        ? snapshot.anchor
        : keyedAnchor;
      if (anchor) {
        const viewportTop = els.messages.getBoundingClientRect().top;
        const anchorBox = anchor.getBoundingClientRect();
        const nextOffset = anchorBox.top - viewportTop;
        const targetOffset = snapshot.anchorProgress === null || snapshot.anchorProgress === undefined
          ? snapshot.anchorOffset
          : -snapshot.anchorProgress * anchorBox.height;
        els.messages.scrollTop += nextOffset - targetOffset;
        return;
      }
      els.messages.scrollTop = snapshot.scrollTop;
    };
    const handleWorkbenchResize = () => {
      if (!pendingWorkbenchViewport) return;
      window.cancelAnimationFrame(workbenchViewportRestoreFrame);
      window.cancelAnimationFrame(workbenchViewportSettleFrame);
      workbenchViewportRestoreFrame = window.requestAnimationFrame(() => {
        workbenchViewportRestoreFrame = 0;
        if (!pendingWorkbenchViewport || !messageViewportMeasurable()) return;
        const snapshot = pendingWorkbenchViewport;
        restoreWorkbenchViewport(snapshot);
        workbenchViewportSettleFrame = window.requestAnimationFrame(() => {
          workbenchViewportSettleFrame = 0;
          if (pendingWorkbenchViewport !== snapshot) return;
          restoreWorkbenchViewport(snapshot);
          pendingWorkbenchViewport = null;
          els.messages.style.overflowAnchor = snapshot.overflowAnchorStyle;
        });
      });
    };
    els.messages?.addEventListener("scroll", handleMessageViewportScroll, { passive: true });
    els.messages?.addEventListener("wheel", expectMessageScrollFromGesture, { passive: true });
    els.messages?.addEventListener("touchstart", beginMessageScrollTouchGesture, { passive: true });
    els.messages?.addEventListener("touchmove", beginMessageScrollTouchGesture, { passive: true });
    els.messages?.addEventListener("touchend", endMessageScrollTouchGesture, { passive: true });
    els.messages?.addEventListener("touchcancel", endMessageScrollTouchGesture, { passive: true });
    els.messages?.addEventListener("pointerdown", handleMessageScrollPointerDown, { passive: true });
    els.messages?.addEventListener("keydown", handleMessageScrollKeyDown);
    window.addEventListener("pointerup", endMessageScrollPointerGesture, { passive: true });
    window.addEventListener("pointercancel", endMessageScrollPointerGesture, { passive: true });
    root.addEventListener("portal:workbench-before-layout", handleBeforeWorkbenchLayout);
    root.addEventListener("portal:workbench-resize", handleWorkbenchResize);
    // A body folds by its rendered height: measured after each render, when the list
    // changes width or becomes visible, and when an image in it loads.
    const measureMessageFolds = (roots = [els.messages], { reading = false } = {}) => {
      const bodies = roots.flatMap(node => node ? [
        ...(node.matches?.(".messageContent[data-message-fold]") ? [node] : []),
        ...(node.querySelectorAll?.(".messageContent[data-message-fold]") || []),
      ] : []);
      const viewport = reading ? els.messages.getBoundingClientRect() : null;
      // Read every height before writing any, so the list lays out once.
      const measured = bodies.map(body => {
        if (!body.getClientRects().length) return null;
        const lineHeight = Number.parseFloat(getComputedStyle(body).lineHeight) || 21;
        const box = viewport ? body.getBoundingClientRect() : null;
        return {
          size: body.scrollHeight > lineHeight * MESSAGE_FOLD_MIN_LINES + 1 ? "long" : "short",
          inView: !!box && box.bottom > viewport.top && box.top < viewport.bottom,
        };
      });
      bodies.forEach((body, index) => {
        const key = body.closest("[data-message-key]")?.dataset.messageKey || "";
        const result = measured[index];
        if (!result || !key) return;
        // A body that grows long while the reader looks at it stays open.
        if (result.inView && result.size === "long" && body.dataset.messageFoldSize !== "long"
          && body.dataset.messageFold === "collapsed" && !messageFoldChoices.has(key)) {
          messageFoldChoices.set(key, "expanded");
          applyMessageFold(body.nextElementSibling, true);
        }
        state.messageFoldSizes.set(key, result.size);
        if (body.dataset.messageFoldSize !== result.size) body.dataset.messageFoldSize = result.size;
      });
    };
    const firstVisibleMessageRow = () => {
      const top = els.messages.getBoundingClientRect().top + 1;
      const rows = [...els.messages.querySelectorAll(".messageList > [data-message-key]")];
      return rows.find(row => row.getBoundingClientRect().top >= top)
        || rows.find(row => row.getBoundingClientRect().bottom >= top) || null;
    };
    let messageFoldWidth = 0;
    const messageFoldObserver = els.messages && typeof ResizeObserver === "function"
      ? new ResizeObserver(() => {
        const width = els.messages.clientWidth;
        if (width === messageFoldWidth) return;
        messageFoldWidth = width;
        if (!width) return;
        // While the user reads history, the first message starting in view keeps its place.
        const reading = !state.messageTailFollowIntent && messageViewportMeasurable();
        const anchor = reading ? firstVisibleMessageRow() : null;
        const before = anchor?.getBoundingClientRect().top;
        measureMessageFolds([els.messages], { reading });
        if (anchor) els.messages.scrollTop += anchor.getBoundingClientRect().top - before;
      })
      : null;
    messageFoldObserver?.observe(els.messages);
    const measureLoadedMessageImage = event => {
      const body = event.target?.tagName === "IMG" ? event.target.closest(".messageContent[data-message-fold]") : null;
      if (body) measureMessageFolds([body]);
    };
    els.messages?.addEventListener("load", measureLoadedMessageImage, true);
    // A newer reply or the end of a turn changes which messages start expanded. A message
    // the user is reading stays open, so its text is not pulled away.
    const updateMessageFoldDefaults = () => {
      const previous = state.messageFoldDefaults;
      const next = messageFoldDefaults(state.messages, {
        runtimeLive: state.runtimeLive,
        onLatestReply: (item, at) => {
          state.latestFoldReplyKey = item ? messageRenderKey(item) : "";
          state.latestFoldReplyAt = at;
        },
      });
      state.messageFoldDefaults = next;
      const shown = new Set(state.messages.map(messageRenderKey));
      const changed = [...previous].filter(key => !next.has(key))
        .concat([...next].filter(key => !previous.has(key)))
        .filter(key => shown.has(key) && !messageFoldChoices.has(key));
      if (changed.length && !state.messageTailFollowIntent && messageViewportMeasurable()) {
        const viewport = els.messages.getBoundingClientRect();
        for (const key of changed) {
          if (next.has(key)) continue;
          const box = els.messages.querySelector(`[data-message-key="${CSS.escape(key)}"]`)?.getBoundingClientRect();
          if (box && box.bottom > viewport.top && box.top < viewport.bottom) messageFoldChoices.set(key, "expanded");
        }
      }
      return changed.filter(key => !messageFoldChoices.has(key));
    };
    const toggleMessageFold = button => {
      const key = String(button.dataset.messageFoldToggle || "");
      const body = button.previousElementSibling;
      if (!key || !body?.dataset.messageFold) return;
      const expanded = body.dataset.messageFold !== "expanded";
      messageFoldChoices.set(key, expanded ? "expanded" : "collapsed");
      // Opening keeps the text in place to read on; closing keeps the button under the pointer.
      const anchor = expanded ? body : button;
      const before = anchor.getBoundingClientRect().top;
      document.querySelectorAll(`[data-message-fold-toggle="${CSS.escape(key)}"]`)
        .forEach(other => applyMessageFold(other, expanded));
      if (!messageViewportMeasurable()) return;
      els.messages.scrollTop += anchor.getBoundingClientRect().top - before;
      state.messageTailFollowIntent = messageViewportAtTail(els.messages);
      syncMessageScrollMode();
    };
    const markComposerInputActivity = (activity = {}) => {
      state.composerInputFocused = !!activity.focused;
      state.composerImeActive = !!activity.composing;
      updateControls();
    };
    const slashCommandConfig = () => {
      const configured = state.composer.slash_commands
        || options.slash_commands
;
      return normalizeSlashCommands(configured);
    };
    const setSlashCommandMenu = (items, activeIndex = 0) => {
      state.slashCommandItems = Array.isArray(items) ? items : [];
      state.slashCommandActiveIndex = state.slashCommandItems.length
        ? Math.max(0, Math.min(Number(activeIndex || 0), state.slashCommandItems.length - 1))
        : 0;
      if (!els.slashCommands) return;
      const previousMenu = els.slashCommands.querySelector(".composerSlashCommandMenu");
      const previousScrollTop = previousMenu ? previousMenu.scrollTop : 0;
      els.slashCommands.innerHTML = slashCommandMenuHtml(state.slashCommandItems, state.slashCommandActiveIndex);
      els.slashCommands.hidden = !state.slashCommandItems.length;
      const menu = els.slashCommands.querySelector(".composerSlashCommandMenu");
      const active = menu?.querySelector(".composerSlashCommandItem.active");
      if (menu && active) {
        const padding = 6;
        menu.scrollTop = Math.max(0, Math.min(previousScrollTop, menu.scrollHeight - menu.clientHeight));
        const itemTop = active.offsetTop;
        const itemBottom = itemTop + active.offsetHeight;
        const viewTop = menu.scrollTop;
        const viewBottom = viewTop + menu.clientHeight;
        if (itemTop < viewTop) {
          menu.scrollTop = Math.max(0, itemTop - padding);
        } else if (itemBottom > viewBottom) {
          menu.scrollTop = itemBottom - menu.clientHeight + padding;
        }
      }
    };
    const refreshSlashCommandMenu = () => {
      if (!els.input) return;
      const items = slashCommandMatches(els.input, state.attachments, slashCommandConfig());
      const active = state.slashCommandItems[state.slashCommandActiveIndex]?.command;
      const nextIndex = active ? Math.max(0, items.findIndex(item => item.command === active)) : 0;
      setSlashCommandMenu(items, nextIndex);
    };
    const closeSlashCommandMenu = () => setSlashCommandMenu([]);
    const chooseSlashCommand = (command) => {
      if (!els.input || !command) return false;
      setComposerDraftInput(els.input, `${command} `, state.attachments, {
        ...attachmentPreviewOptions(),
        focus: true,
      });
      closeSlashCommandMenu();
      updateControls();
      saveActiveDraft();
      options.onInput?.(controller.payload(), controller);
      return true;
    };
    const handleSlashCommandKey = (event) => {
      if (!state.slashCommandItems.length) return false;
      const key = String(event.key || "");
      if (key === "ArrowDown" || key === "ArrowUp") {
        event.preventDefault();
        const delta = key === "ArrowDown" ? 1 : -1;
        const next = (state.slashCommandActiveIndex + delta + state.slashCommandItems.length) % state.slashCommandItems.length;
        setSlashCommandMenu(state.slashCommandItems, next);
        return true;
      }
      if (key === "Enter" || key === "Tab") {
        if (event.ctrlKey || event.metaKey) return false;
        event.preventDefault();
        return chooseSlashCommand(state.slashCommandItems[state.slashCommandActiveIndex]?.command || "");
      }
      if (key === "Escape") {
        event.preventDefault();
        closeSlashCommandMenu();
        return true;
      }
      return false;
    };
    let mentionTimer = 0;
    let mentionRequest = 0;
    let mentionAbort = null;
    let mentionStatus = "";
    let mentionRenderFrame = 0;
    let mentionMarkup = "";
    const renderMentionMenu = () => {
      mentionRenderFrame = 0;
      if (!els.mentions || isComposerImeActive(els.input)) return;
      // Coalesce loading and result updates outside the input handler. Changing
      // the selection only updates two rows, preserving the list and its scroll.
      const html = mentionMenuHtml(state.mentionItems, -1, options.mentionTitle, mentionStatus);
      if (html !== mentionMarkup || (html && !els.mentions.firstElementChild)) {
        const scrollTop = els.mentions.firstElementChild?.scrollTop || 0;
        els.mentions.innerHTML = html;
        mentionMarkup = html;
        if (els.mentions.firstElementChild) els.mentions.firstElementChild.scrollTop = scrollTop;
        bindMessageAvatars(els.mentions);
      }
      els.mentions.hidden = !state.mentionItems.length && !mentionStatus;
      const previous = els.mentions.querySelector(".composerMentionItem.active");
      const active = els.mentions.querySelector(`[data-portal-mention-index="${state.mentionActiveIndex}"]:not(:disabled)`);
      if (previous !== active) {
        previous?.classList.remove("active");
        previous?.setAttribute("aria-selected", "false");
        active?.classList.add("active");
        active?.setAttribute("aria-selected", "true");
      }
      // Only scroll the candidate list, never the surrounding workspace or page.
      const menu = els.mentions.firstElementChild;
      if (active && menu) {
        const top = active.offsetTop;
        if (top < menu.scrollTop) menu.scrollTop = top;
        else if (top + active.offsetHeight > menu.scrollTop + menu.clientHeight) {
          menu.scrollTop = top + active.offsetHeight - menu.clientHeight;
        }
      }
    };
    const scheduleMentionRender = () => {
      if (!mentionRenderFrame) mentionRenderFrame = window.requestAnimationFrame(renderMentionMenu);
    };
    const setMentionMenu = (items, activeIndex = 0, status = mentionStatus) => {
      mentionStatus = status;
      state.mentionItems = Array.isArray(items) ? items : [];
      const enabled = index => Boolean(state.mentionItems[index]) && !state.mentionItems[index].disabled;
      state.mentionActiveIndex = enabled(activeIndex) ? activeIndex : Math.max(0, state.mentionItems.findIndex(item => !item.disabled));
      scheduleMentionRender();
    };
    const closeMentionMenu = () => {
      window.clearTimeout(mentionTimer);
      mentionRequest += 1;
      mentionAbort?.abort();
      mentionAbort = null;
      state.mentionQuery = null;
      setMentionMenu([], 0, "");
      if (els.mentions) els.mentions.hidden = true;
    };
    const refreshMentionMenu = () => {
      if (typeof options.mentionCandidates !== "function" || !els.input) return;
      if (isComposerImeActive(els.input)) return;
      const token = state.slashCommandItems.length ? null : composerMentionToken(els.input);
      const dismissed = state.mentionDismissed;
      if (!token) {
        state.mentionDismissed = null;
        closeMentionMenu();
        return;
      }
      if (dismissed && dismissed.node === token.node && dismissed.start === token.start) {
        closeMentionMenu();
        return;
      }
      state.mentionDismissed = null;
      if (token.query === state.mentionQuery) return;
      state.mentionQuery = token.query;
      window.clearTimeout(mentionTimer);
      const request = ++mentionRequest;
      mentionAbort?.abort();
      const abort = new AbortController();
      mentionAbort = abort;
      setMentionMenu([], 0, "正在查找 Resident…");
      const update = (items, status) => {
        if (request !== mentionRequest || abort.signal.aborted) return;
        const selected = state.mentionItems[state.mentionActiveIndex]?.value;
        setMentionMenu(items, items.findIndex(item => item.value === selected), status);
      };
      // @ itself responds immediately; coalesce keyword edits even while
      // the first search is still loading. Old results are never selectable.
      mentionTimer = window.setTimeout(() => {
        Promise.resolve().then(() => options.mentionCandidates(token.query, controller, {
          signal: abort.signal,
          onUpdate: items => update(items, "正在查找 Resident…"),
        }))
          .then(items => update(items, items.length ? "" : "没有匹配的 Resident"))
          .catch(error => {
            if (request !== mentionRequest || abort.signal.aborted) return;
            setMentionMenu(state.mentionItems, state.mentionActiveIndex,
              state.mentionItems.length ? "部分候选加载失败，请重新输入重试" : "候选加载失败，请重新输入重试");
            options.onError ? options.onError(error) : window.PortalUI?.toast?.(error?.message || String(error), "error");
          });
      }, token.query ? 150 : 0);
    };
    const chooseMention = index => {
      const item = state.mentionItems[index];
      if (!item || item.disabled || !els.input) return false;
      const token = composerMentionToken(els.input);
      if (token) {
        const range = document.createRange();
        range.setStart(token.node, token.start);
        range.setEnd(token.node, token.end);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      closeMentionMenu();
      if (els.input.isContentEditable) {
        const selection = window.getSelection();
        const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
        if (!range || !els.input.contains(range.commonAncestorContainer)) return false;
        const node = document.createElement("span");
        node.className = "portalMention";
        node.contentEditable = "false";
        node.dataset.mentionValue = item.value;
        node.textContent = `@${item.title || item.label}`;
        node.title = item.value;
        range.deleteContents();
        range.insertNode(node);
        const space = document.createTextNode(" ");
        node.after(space);
        range.setStartAfter(space);
        range.collapse(true);
        selection.removeAllRanges();
        selection.addRange(range);
        els.input.dispatchEvent(new Event("input", { bubbles: true }));
      } else insertPlainText(els.input, `${item.value} `);
      return true;
    };
    const handleMentionKey = (event) => {
      if (state.mentionQuery === null || event.isComposing) return false;
      const key = String(event.key || "");
      if ((key === "ArrowDown" || key === "ArrowUp") && state.mentionItems.length) {
        event.preventDefault();
        const delta = key === "ArrowDown" ? 1 : -1;
        const count = state.mentionItems.length;
        let next = state.mentionActiveIndex;
        for (let step = 0; step < count; step += 1) {
          next = (next + delta + count) % count;
          if (!state.mentionItems[next].disabled) break;
        }
        setMentionMenu(state.mentionItems, next);
        return true;
      }
      if (key === "Enter" || key === "Tab") {
        if (event.ctrlKey || event.metaKey || !state.mentionItems.some(item => !item.disabled)) return false;
        event.preventDefault();
        return chooseMention(state.mentionActiveIndex);
      }
      if (key === "Escape") {
        event.preventDefault();
        const token = composerMentionToken(els.input);
        state.mentionDismissed = token ? { node: token.node, start: token.start } : null;
        closeMentionMenu();
        return true;
      }
      return false;
    };
    const configuredHistoryScope = (composerState = state.composer) => {
      const optionScope = typeof options.historyScope === "function"
        ? options.historyScope()
        : options.historyScope;
      return String(optionScope || composerState?.history_scope || id);
    };
    const historyScope = () => configuredHistoryScope();
    // One Fork card per conversation: whether its compaction is open, and what was read.
    let inheritanceView = null;
    const inheritanceViewFor = inheritance => {
      if (!inheritance || typeof inheritance !== "object") return null;
      const key = `${historyScope()}\n${inheritance.source_resident_id || ""}\n${inheritance.forked_from_at || ""}`;
      if (inheritanceView?.key !== key) inheritanceView = { key, expanded: false, status: "idle", compaction: null, error: "" };
      return inheritanceView;
    };
    const renderInheritanceCard = () => {
      const node = els.messages?.querySelector('.messageList > [data-message-key="__inheritance__"]');
      if (!node) return;
      const renderOptions = { ...options, ...state.messageRenderOptions };
      const template = document.createElement("template");
      template.innerHTML = inheritanceCardHtml(renderOptions.inheritance, inheritanceViewFor(renderOptions.inheritance), renderOptions).trim();
      if (template.content.firstElementChild) node.replaceWith(template.content.firstElementChild);
    };
    const toggleInheritedSummary = async () => {
      const view = inheritanceViewFor(state.messageRenderOptions?.inheritance);
      if (!view) return;
      view.expanded = !view.expanded;
      const load = view.expanded && !["loading", "ready"].includes(view.status) && options.onLoadInheritedContext;
      if (load) Object.assign(view, { status: "loading", error: "" });
      renderInheritanceCard();
      if (!load) return;
      try {
        const inheritance = await options.onLoadInheritedContext(controller);
        const compaction = inheritance?.opening_compaction;
        Object.assign(view, compaction && typeof compaction === "object"
          ? { status: "ready", compaction }
          : { status: "error", error: window.PortalI18n.t("这次 Fork 已没有可读取的压缩。") });
      } catch (error) {
        Object.assign(view, { status: "error", error: error?.message || String(error) });
      }
      if (inheritanceView === view) renderInheritanceCard();
    };
    const notifyAttachmentsChange = () => {
      saveActiveDraft();
      options.onAttachmentsChange?.(controller.payload(), controller);
    };
    const attachmentOriginal = item => {
      const raw = item && typeof item === "object" ? item : {};
      return raw.original && typeof raw.original === "object" ? raw.original : {
        upload_id: String(raw.upload_id || ""),
        file_id: String(raw.file_id || ""),
        kind: String(raw.kind || "image"),
        name: String(raw.name || ""),
        path: String(raw.path || ""),
        preview_url: String(raw.preview_url || raw.data_url || ""),
        content_type: String(raw.content_type || raw.mime || ""),
        size: Number(raw.size || raw.size_bytes || 0),
      };
    };
    const replaceEditedAttachment = async (context, file, previewUrl) => {
      const attachmentId = String(context?.attachmentId || "").trim();
      const current = state.attachments.find(item => String(item?.id || "") === attachmentId);
      if (!attachmentId || !current) throw new Error("未找到需要替换的图片附件");
      if (current.uploading) throw new Error("图片还在上传，请稍后编辑。");
      const previous = { ...current };
      const scope = historyScope();
      if (String(previewUrl || "").startsWith("blob:")) ownedAttachmentPreviews.add(previewUrl);
      controller.updateAttachment(attachmentId, {
        upload_id: "",
        file_id: "",
        original: attachmentOriginal(current),
        name: file.name,
        path: "",
        url: "",
        preview_url: previewUrl,
        data_url: "",
        mime: "image/png",
        content_type: "image/png",
        size: file.size,
        size_bytes: file.size,
        uploading: true,
        progress: 0,
        error: "",
      });
      try {
        const upload = options.uploadAttachment || defaultWorkspaceUpload;
        const result = await upload(file, {
          ...attachmentUploadContext(),
          item: current,
          workspace: controller,
          editedAttachmentId: attachmentId,
        });
        if (historyScope() !== scope || !state.attachments.some(item => String(item.id || "") === attachmentId)) {
          await deleteUploadedAttachment({ ...result, kind: "image" });
          releaseAttachmentPreview(previewUrl);
          return;
        }
        const uploadedPreview = result?.preview_url || result?.data_url || mediaPartPath({ upload_id: result?.upload_id }) || previewUrl;
        if (uploadedPreview !== previous.preview_url) releaseAttachmentPreview(previous.preview_url);
        if (uploadedPreview !== previewUrl) releaseAttachmentPreview(previewUrl);
        controller.updateAttachment(attachmentId, {
          upload_id: result?.upload_id || "",
          file_id: result?.file_id || "",
          original: attachmentOriginal(current),
          name: result?.filename || file.name,
          path: result?.path || "",
          url: result?.url || "",
          preview_url: uploadedPreview,
          data_url: result?.data_url || "",
          mime: result?.mime || result?.content_type || "image/png",
          content_type: result?.content_type || "image/png",
          size: Number(result?.size || file.size || 0),
          size_bytes: Number(result?.size_bytes || result?.size || file.size || 0),
          uploading: false,
          progress: 100,
          error: "",
          file: null,
        });
      } catch (error) {
        releaseAttachmentPreview(previewUrl);
        if (historyScope() === scope) controller.updateAttachment(attachmentId, previous);
        throw error;
      }
    };
    const openComposerAttachmentEditor = async context => {
      try {
        await requireImageEditor();
        const attachment = context?.attachment || {};
        context.open?.({
          editable: true,
          name: attachment.name || context.title || "annotated-image",
          meta: { attachmentId: context.attachmentId || "" },
          onSave: ({ file, previewUrl }) => replaceEditedAttachment(context, file, previewUrl),
        });
      } catch (error) {
        if (options.onError) options.onError(error);
        else window.PortalUI.toast(error.message || error, "error");
      }
    };
    const insertEditedMessageImage = async ({ file }) => {
      if (!file) return;
      await controller.addFiles([file]);
      els.input?.focus?.();
    };
    const openMessageImageEditor = async (src, title = "") => {
      const url = String(src || "").trim();
      if (!url) return;
      try {
        await requireImageEditor();
        openImagePreview(url, title || "image", {
          editable: true,
          name: title || "annotated-image",
          onSave: insertEditedMessageImage,
        });
      } catch (error) {
        if (options.onError) options.onError(error);
        else window.PortalUI.toast(error.message || error, "error");
      }
    };
    const attachmentPreviewOptions = () => ({
      renderMentions: options.renderMentions,
      attachmentById: id => state.attachments.find(item => String(item?.id || "") === String(id || "")) || null,
      onImagePreview: context => openComposerAttachmentEditor({ ...context, workspace: controller }),
    });
    const bindDefaultMessageActions = () => {
      if (!els.messages || els.messages.dataset.portalMessageActionsBound === "1") return;
      els.messages.dataset.portalMessageActionsBound = "1";
      const messageMenuPopovers = new WeakMap();
      let openMessageMenu = null;
      let handleMessagesClick = null;
      const messageMenuPopover = menu => {
        if (!menu) return null;
        const existing = messageMenuPopovers.get(menu);
        if (existing) return existing;
        const messageItem = menu.closest(".messageItem");
        if (!messageItem || !window.PortalOverlay) return null;
        menu.addEventListener("click", event => {
          if (menu.parentNode !== document.body) return;
          event.stopPropagation();
          handleMessagesClick?.(event);
        });
        const popover = window.PortalOverlay.bindPopover({
          trigger: messageItem,
          panel: menu,
          placement: "bottom-end",
          closeOnEscape: true,
          closeOnOutside: true,
          closeOnTrigger: true,
          portal: true,
          onOpen: () => {
            openMessageMenu = menu;
            messageItem?.classList.add("menuOpen");
            options.onInteractionChange?.(true, menu);
          },
          onClose: () => {
            if (openMessageMenu === menu) openMessageMenu = null;
            messageItem?.classList.remove("menuOpen");
            options.onInteractionChange?.(false, menu);
          },
        });
        messageMenuPopovers.set(menu, popover);
        return popover;
      };
      const setMessageMenuOpen = (menu, open, context) => {
        if (!menu) return;
        bindMessageSubmenus(menu);
        closeMessageSubmenus(menu);
        const popover = messageMenuPopover(menu);
        if (popover) {
          if (open) popover.open(context);
          else popover.close();
          return;
        }
        menu.hidden = !open;
      };
      const closeMessageMenus = (except = null) => {
        if (openMessageMenu && openMessageMenu !== except) setMessageMenuOpen(openMessageMenu, false);
        els.messages.querySelectorAll("[data-portal-message-menu]").forEach(menu => {
          if (menu !== except) setMessageMenuOpen(menu, false);
        });
      };
      handleMessagesClick = event => {
        const workToggle = event.target.closest("[data-portal-work-group-toggle]");
        if (workToggle) {
          event.preventDefault();
          event.stopPropagation();
          const groupId = workToggle.dataset.portalWorkGroupToggle;
          if (state.collapsedWorkGroups.has(groupId)) state.collapsedWorkGroups.delete(groupId);
          else state.collapsedWorkGroups.add(groupId);
          controller.setMessages(state.messages, {
            ...state.messageRenderOptions, runtimeDetails: state.runtimeDetails,
            runtimeLive: state.runtimeLive, runtimePhase: state.runtimePhase, force: true,
          });
          els.messages.querySelector(`[data-portal-work-group-toggle="${CSS.escape(groupId)}"]`)?.focus({ preventScroll: true });
          return;
        }
        const foldToggle = event.target.closest("[data-message-fold-toggle]");
        if (foldToggle) {
          event.preventDefault();
          event.stopPropagation();
          toggleMessageFold(foldToggle);
          return;
        }
        const groupToggle = event.target.closest("[data-portal-agent-group-toggle]");
        if (groupToggle) {
          event.preventDefault();
          event.stopPropagation();
          const groupId = String(groupToggle.dataset.portalAgentGroupToggle || "").trim();
          if (!groupId) return;
          if (state.messageRenderOptions?.autoCollapseAgentGroups === true) {
            if (state.expandedAgentGroups.has(groupId)) state.expandedAgentGroups.delete(groupId);
            else state.expandedAgentGroups.add(groupId);
          } else if (state.collapsedAgentGroups.has(groupId)) state.collapsedAgentGroups.delete(groupId);
          else state.collapsedAgentGroups.add(groupId);
          controller.setMessages(state.messages, {
            ...state.messageRenderOptions,
            runtimeDetails: state.runtimeDetails,
            runtimeLive: state.runtimeLive,
            runtimePhase: state.runtimePhase,
            force: true,
          });
          return;
        }
        const openLinkButton = event.target.closest("[data-portal-message-open-link]");
        if (openLinkButton) {
          event.preventDefault();
          setMessageMenuOpen(openLinkButton.closest("[data-portal-message-menu]"), false);
          window.open(openLinkButton.dataset.portalMessageOpenLink, "_blank", window.PortalI18n.t("noopener,noreferrer"));
          return;
        }
        const actionButton = event.target.closest("[data-portal-message-action]");
        if (actionButton) {
          const menu = actionButton.closest("[data-portal-message-menu]");
          const article = actionButton.closest("[data-message-key]");
          const key = String(menu?.dataset.portalMessageKey || article?.dataset.messageAfterOf || article?.dataset.messageKey || "");
          const item = state.messageByKey.get(key);
          const action = String(actionButton.dataset.portalMessageAction || "");
          setMessageMenuOpen(menu, false);
          if (action === "resident-handoff-ask") {
            const card = residentHandoffCards(actionButton.dataset.handoffCard)[0];
            if (!card?.ask_message || actionButton.disabled || !options.onSend || state.composer.can_send === false) return;
            actionButton.disabled = true;
            // This explicit action owns its payload; an unfinished draft remains in the composer.
            Promise.resolve(options.onSend({ text: `替我问一下 ${card.title}（${card.resident_id}）：${card.ask_message}`, refs: [], attachments: [] }, controller,
              { clear: false, focus: false, quote: false, includeContext: false, clearRefsOnSuccess: false }))
              .catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || String(error), "error"))
              .finally(() => { actionButton.disabled = false; });
            return;
          }
          const handler = state.messageRenderOptions?.onMessageAction || options.onMessageAction;
          if (handler && item && action) {
            Promise.resolve(handler({ action, key, item, button: actionButton, workspace: controller }))
              .catch(error => options.onError?.(error));
          }
          return;
        }
        const copyButton = event.target.closest("[data-portal-message-copy]");
        if (!copyButton) return;
        const menu = copyButton.closest("[data-portal-message-menu]");
        const key = String(menu?.dataset.portalMessageKey || "");
        const item = state.messageByKey.get(key);
        if (!item) return;
        let items = [item];
        if (copyButton.dataset.portalMessageCopyScope === "replies") {
          if (messageRole(item) !== "agent") return;
          const index = state.messages.findIndex(message => messageRenderKey(message) === key);
          const group = consecutiveAgentGroups(state.messages).get(index);
          if (group) items = state.messages.slice(group.start, group.end + 1);
        }
        const format = copyButton.dataset.portalMessageCopy === "markdown" ? "markdown" : "text";
        const valueResolver = state.messageRenderOptions?.messageCopyValue || options.messageCopyValue;
        Promise.all(items.map(item => {
          const key = messageRenderKey(item);
          const article = els.messages.querySelector(`[data-message-key="${CSS.escape(key)}"]`);
          const raw = String(item?.content || "");
          return valueResolver
            ? valueResolver({ format, item, key, article, workspace: controller })
            : (format === "markdown" ? raw : messageCopyText(article, raw));
        }))
          .then(values => window.PortalUI.copyText(values.map(value => String(value ?? "")).join("\n\n"), window.PortalI18n.t("消息")))
          .catch(error => options.onError?.(error));
        setMessageMenuOpen(menu, false);
      };
      els.messages.addEventListener("click", handleMessagesClick);
      els.messages.addEventListener("contextmenu", event => {
        if (event.target.closest("input, textarea, select") || event.target.isContentEditable) return;
        // The reading pane owns its context menu, including blank space and
        // status blocks. Only an actual message may supply message actions.
        event.preventDefault();
        event.stopPropagation();
        let article = event.target.closest(".messageItem[data-message-key]");
        const list = event.target;
        if (!article && list.matches(".messageList") && list.parentElement === els.messages) {
          // The flex gap after a message belongs to that message, up to the
          // next visible message. Do not claim side gutters, history controls,
          // runtime cards, or the empty space before/after the conversation.
          let previous = null;
          for (const row of list.children) {
            if (!row.getClientRects().length) continue;
            const bounds = row.getBoundingClientRect();
            if (bounds.top > event.clientY) {
              if (previous?.matches(".messageItem[data-message-key]")
                && row.matches(".messageItem[data-message-key]")
                && state.messageByKey.has(row.dataset.messageKey)) {
                const before = previous.getBoundingClientRect();
                if (event.clientY >= before.bottom && event.clientX >= before.left && event.clientX <= before.right) article = previous;
              }
              break;
            }
            previous = row;
          }
        }
        const key = article && els.messages.contains(article) ? article.dataset.messageKey : "";
        const menu = key && state.messageByKey.has(key)
          ? article.querySelector("[data-portal-message-menu]")
            || (openMessageMenu?.dataset.portalMessageKey === key ? openMessageMenu : null)
          : null;
        if (!menu) {
          closeMessageMenus();
          return;
        }
        menu.querySelector("[data-portal-message-open-link]")?.remove();
        const link = event.target.closest("a[href]");
        if (link && article.contains(link) && ["http:", "https:"].includes(link.protocol)) {
          const button = document.createElement("button");
          button.type = "button";
          button.dataset.portalMessageOpenLink = link.href;
          button.textContent = window.PortalI18n.t("打开链接");
          button.title = window.PortalI18n.t("在新标签页打开这个网页链接");
          menu.insertBefore(button, menu.querySelector("[data-portal-message-copy]"));
        }
        closeMessageMenus(menu);
        setMessageMenuOpen(menu, true, { point: { x: event.clientX, y: event.clientY } });
      });
    };
    const historyPreviewUrl = () => state.messageRenderOptions?.previewUrlForPath || options.previewUrlForPath;
    const history = createInputHistory({
      // The host reads the person's inputs from the server (ListInputHistory with
      // resident_id or chatroom_id); a Chat without that has no input history.
      loadPage: options.onLoadInputHistory ? async (scope, pageToken) => {
        const page = await options.onLoadInputHistory(scope, pageToken);
        if (!page) return null;
        return {
          items: (Array.isArray(page.messages) ? page.messages : [])
            .map(item => messageInputHistoryItem(item, { previewUrlForPath: historyPreviewUrl() })),
          next_page_token: page.next_page_token,
        };
      } : null,
      onError: error => options.onError?.(error),
      serializeDraft: input => serializeComposerInput(input, state.attachments, { mode: "draft" }),
      serializeDraftMeta: () => ({
        attachments: state.attachments.map(item => ({ ...item })),
      }),
      restoreDraft: (input, value, meta = null) => {
        state.attachments = normalizeComposerAttachments(meta && Array.isArray(meta.attachments) ? meta.attachments : []);
        setComposerDraftInput(input, value, state.attachments, attachmentPreviewOptions());
        renderAttachmentTrayOnly({ notify: true });
      },
    });
    const draftStore = options.draftStore || createDraftStore({
      namespace: String(options.draftNamespace || id),
      owner: options.draftOwner || "anonymous",
      delay: options.draftSaveDelay || 180,
    });
    // Keep the input history with the conversation shown: this person's inputs
    // still queued for a turn come first, and when the Chat
    // shows a new input of theirs, from here or another browser, the history
    // reads the server's first page again.
    const ownInputMarks = new Map();
    const syncHistory = (scope = historyScope()) => {
      const scopeKey = String(scope || "");
      const viewer = String(window.PortalIdentity?.username?.() || "").trim().toLowerCase();
      const queued = normalizeWorkspaceQueue(state.composer?.queue).items
        .filter(item => workspaceQueueActionId(item) && Array.isArray(item.content_parts) && item.content_parts.length
          && (!item.author_id || String(item.author_id).trim().toLowerCase() === viewer))
        .map(item => messageInputHistoryItem(residentProtocolMessageToWorkspace({ ...item, role: "user" }), {
          previewUrlForPath: historyPreviewUrl(),
        }));
      history.setQueuedItems(scopeKey, queued.reverse());
      const ownInputs = (state.messages || []).filter(item => !item.local_delivery && messageAuthoredByViewer(item));
      const latest = ownInputs.length ? messageIdentity(ownInputs[ownInputs.length - 1]) : "";
      const previous = ownInputMarks.get(scopeKey);
      ownInputMarks.set(scopeKey, latest);
      if (previous !== undefined && previous !== latest) history.refresh(scopeKey);
      else history.prepare(scopeKey);
    };
    let draftPersistenceSuspension = null;
    let draftBaseline = null;
    const renderAttachments = (renderOptions = {}) => {
      const selection = document.activeElement === els.input
        ? composerSelectionSnapshot(els.input)
        : null;
      state.attachments = normalizeComposerAttachments(state.attachments);
      renderComposerAttachmentTray(els.tray, state.attachments, attachmentPreviewOptions());
      syncComposerAttachmentRefs(els.input, state.attachments, attachmentPreviewOptions());
      if (selection) restoreComposerSelection(els.input, selection);
      updateControls();
      if (renderOptions.notify) notifyAttachmentsChange();
    };
    const renderAttachmentTrayOnly = (renderOptions = {}) => {
      state.attachments = normalizeComposerAttachments(state.attachments);
      renderComposerAttachmentTray(els.tray, state.attachments, attachmentPreviewOptions());
      updateControls();
      if (renderOptions.notify) notifyAttachmentsChange();
    };
    const canSubmit = () => {
      const uploading = state.attachments.some(item => item.uploading);
      const failed = state.attachments.some(item => item.error);
      const text = serializeComposerInput(els.input, state.attachments);
      const canSendByContract = state.composer.can_send !== false;
      const textRequiredByContract = state.composer.requires_text === true;
      const localSendBlocked = options.canSend && !options.canSend({
        text,
        attachments: state.attachments.slice(),
        refs: normalizeRefs(state.refs),
      });
      return !state.loading
        && !state.busy
        && !isComposerImeActive(els.input)
        && !uploading
        && !failed
        && canSendByContract
        && !(textRequiredByContract && !String(text || "").trim())
        && !localSendBlocked;
    };
    const setWorkspaceLoading = (message = "正在加载…") => {
      conversationSearch?.close();
      root.querySelector("[data-workspace-history-search]").disabled = true;
      saveActiveDraft({ immediate: true });
      state.loading = true;
      state.loadingMessage = String(message || "正在加载…");
      root.classList.add("is-workspace-loading");
      root.setAttribute("aria-busy", "true");
      for (const node of [els.body, els.composer, els.composerSplitter]) {
        if (!node) continue;
        node.inert = true;
        node.setAttribute("aria-hidden", "true");
      }
      if (els.loading) {
        els.loading.innerHTML = window.PortalUIPrimitives.asyncStateHtml({
          state: "loading",
          title: "",
          message: state.loadingMessage,
          icon: "loading",
          className: "portalWorkspaceLoadingState",
        });
        els.loading.hidden = false;
        window.PortalUIPrimitives?.mountIcons?.(els.loading);
      }
      updateControls();
    };
    const clearWorkspaceLoading = () => {
      root.querySelector("[data-workspace-history-search]").disabled = false;
      state.loading = false;
      state.loadingMessage = "";
      root.classList.remove("is-workspace-loading");
      root.removeAttribute("aria-busy");
      for (const node of [els.body, els.composer, els.composerSplitter]) {
        if (!node) continue;
        node.inert = false;
        node.removeAttribute("aria-hidden");
      }
      if (els.loading) {
        els.loading.hidden = true;
        els.loading.replaceChildren();
      }
      updateControls();
    };
    // The message the composition quotes, per conversation; not saved with the draft.
    const composerQuotes = new Map();
    const composerQuoteScope = () => historyScope() || state.activeDraftScope || "";
    const composerQuote = () => composerQuotes.get(composerQuoteScope()) || null;
    const renderComposerQuote = () => {
      const host = els.composer?.querySelector("[data-composer-quote-host]");
      if (!host) return;
      const quote = composerQuote();
      const messageId = quote ? quote.message_id : "";
      if ((host.dataset.composerQuote || "") === messageId) return;
      host.dataset.composerQuote = messageId;
      host.innerHTML = quote ? composerQuoteHtml(quote, options) : "";
      if (quote) bindResourceChips(host, options);
      host.hidden = !quote;
      if (quote) window.PortalUIPrimitives?.mountIcons?.(host);
    };
    let stopPending = false;
    const updateControls = () => {
      const uploading = state.attachments.some(item => item.uploading);
      const failed = state.attachments.some(item => item.error);
      const canStopByContract = !!state.composer.can_stop;
      if (els.send) {
        els.send.disabled = !canSubmit();
        els.send.title = state.composer.can_send === false ? state.composer.disabled_reason || window.PortalI18n.t("当前对话暂不可发送") : window.PortalI18n.t("发送");
      }
      if (els.stop) {
        els.stop.disabled = stopPending || (state.contract ? !canStopByContract : !(canStopByContract || (options.canStop && options.canStop())));
        els.stop.textContent = stopPending ? window.PortalI18n.t("正在停止…") : window.PortalI18n.t("Stop");
        els.stop.title = stopPending ? window.PortalI18n.t("已请求停止，等待当前任务确认") : window.PortalI18n.t("停止当前任务");
        els.stop.setAttribute("aria-label", els.stop.title);
      }
      if (els.status) {
        const transientStatus = state.busy
          ? (options.busyText || "Sending...")
          : uploading
            ? "附件上传中..."
            : failed
              ? "有附件上传失败"
              : "";
        renderSubtitle(transientStatus);
      }
      renderComposerQuote();
    };
    const composerInputHeightLimits = () => {
      const rootHeight = root.getBoundingClientRect().height || window.innerHeight || 800;
      const min = Math.max(40, Number(options.composerMinHeight || 56));
      const configuredMax = Math.max(min, Number(options.composerMaxHeight || 360));
      const max = Math.max(min, Math.min(configuredMax, Math.round(rootHeight * 0.46)));
      return { min, max };
    };
    const applyComposerInputLayout = ({ persist = false } = {}) => {
      if (!composerResizeEnabled) return;
      const limits = composerInputHeightLimits();
      state.composerInputHeight = Math.max(limits.min, Math.min(limits.max, Number(state.composerInputHeight || defaultComposerInputHeight)));
      root.style.setProperty("--portal-composer-input-h", `${Math.round(state.composerInputHeight)}px`);
      if (persist) {
        writeStoredComposerInputHeight(state.composerInputHeight);
        saveActiveDraft();
      }
    };
    const handleWindowResize = () => {
      applyComposerInputLayout();
      window.cancelAnimationFrame(windowResizeFollowFrame);
      if (!state.messageTailFollowIntent) return;
      windowResizeFollowFrame = window.requestAnimationFrame(() => {
        windowResizeFollowFrame = 0;
        if (state.messageTailFollowIntent && messageViewportMeasurable()) {
          els.messages.scrollTop = els.messages.scrollHeight;
        }
      });
    };
    const bindComposerSplitter = () => {
      const handle = els.composerSplitter;
      if (!composerResizeEnabled || !handle) return;
      if (!window.PortalWorkbench.layout) {
        throw new Error("PortalWorkbench.layout must be loaded before resizable Chat Workspaces.");
      }
      window.PortalWorkbench.layout.bindSplitter({
        handle,
        axis: "y",
        direction: -1,
        bodyClass: "resizingComposer",
        boundKey: "portalComposerSplitterBound",
        min: () => composerInputHeightLimits().min,
        max: () => composerInputHeightLimits().max,
        readSize: () => state.composerInputHeight,
        applySize: height => {
          state.composerInputHeight = height;
          applyComposerInputLayout();
        },
        onDone: () => applyComposerInputLayout({ persist: true }),
      });
      window.addEventListener("resize", handleWindowResize);
      applyComposerInputLayout();
    };
    let suppressAttachmentReconcile = false;
    const fileSignature = (file) => {
      const type = String(file?.type || "").toLowerCase();
      const name = String(file?.name || "").trim();
      const size = Number(file?.size || 0);
      const kind = type.startsWith("image/") ? "image" : "file";
      return kind === "image"
        ? `image:${type}:${size}:${name}`
        : `file:${name}:${type}:${size}:${file?.lastModified || 0}`;
    };
    const attachmentUploadContext = () => {
      const configured = typeof options.attachmentUploadContext === "function"
        ? options.attachmentUploadContext(controller)
        : options.attachmentUploadContext;
      const contractContext = state.composer.attachment_upload_context;
      return {
        ...(configured && typeof configured === "object" ? configured : {}),
        ...(contractContext && typeof contractContext === "object" ? contractContext : {}),
      };
    };
    const composerSnapshot = () => ({
      inputValue: els.input && "value" in els.input ? String(els.input.value || "") : "",
      inputHtml: els.input && !("value" in els.input) ? String(els.input.innerHTML || "") : "",
      plainText: serializeComposerInput(els.input, state.attachments, { mode: "plain" }),
      selection: composerSelectionSnapshot(els.input),
      attachments: state.attachments.map(item => ({ ...item })),
    });
    const composerMutationSignature = () => JSON.stringify({
      draftText: serializeComposerInput(els.input, state.attachments, { mode: "draft" }),
      attachmentIds: state.attachments.map(item => String(item?.id || "")),
      refs: refsPayload(state.refs),
    });
    const pushComposerUndoSnapshot = () => {
      if (!els.input) return null;
      const snapshot = composerSnapshot();
      const previous = state.undoStack[state.undoStack.length - 1];
      const signature = JSON.stringify({
        inputValue: snapshot.inputValue,
        inputHtml: snapshot.inputHtml,
        attachments: snapshot.attachments.map(item => [item.id, item.path, item.preview_url, item.uploading, item.error]),
      });
      if (previous?.signature === signature) return previous;
      const frame = { ...snapshot, signature, afterSignature: "" };
      state.undoStack.push(frame);
      state.undoStack = state.undoStack.slice(-20);
      return frame;
    };
    const dropComposerUndoSnapshot = (frame) => {
      if (!frame) return;
      if (state.undoStack[state.undoStack.length - 1] === frame) {
        state.undoStack.pop();
        return;
      }
      state.undoStack = state.undoStack.filter(item => item !== frame);
    };
    const markComposerUndoAfterSnapshot = (frame) => {
      if (!frame || !els.input) return;
      frame.afterSignature = composerMutationSignature();
    };
    const restoreComposerSnapshot = (snapshot) => {
      if (!snapshot || !els.input) return false;
      suppressAttachmentReconcile = true;
      try {
        state.attachments = normalizeComposerAttachments(snapshot.attachments || []);
        if ("value" in els.input) els.input.value = String(snapshot.inputValue || "");
        else els.input.innerHTML = String(snapshot.inputHtml || "");
        restoreComposerSelection(els.input, snapshot.selection);
        renderAttachmentTrayOnly({ notify: true });
        bindImagePreview(els.input, attachmentPreviewOptions());
        updateControls();
        options.onInput?.(controller.payload(), controller);
        return true;
      } finally {
        suppressAttachmentReconcile = false;
      }
    };
    const captureWorkspaceDraft = () => {
      const draft = {
        text: serializeComposerInput(els.input, state.attachments, { mode: "draft" }),
        attachments: persistedDraftAttachments(state.attachments),
        refs: refsPayload(state.refs),
        selection: composerSelectionSnapshot(els.input),
        input_height: state.composerInputHeight,
      };
      if (state.recoveredSubmission?.content !== draftContentSignature(draft)) {
        state.recoveredSubmission = null;
      }
      return { ...draft, submitted_message_id: state.recoveredSubmission?.messageId || "" };
    };
    const draftContentSignature = draft => JSON.stringify({
      text: draft.text,
      attachments: draft.attachments,
      refs: draft.refs,
    });
    const draftSignatures = draft => ({
      content: draftContentSignature(draft),
      submission: draft.submitted_message_id || "",
      view: JSON.stringify({ selection: draft.selection, input_height: draft.input_height }),
    });
    const saveActiveDraft = ({ immediate = false } = {}) => {
      if (state.restoringDraft || !state.activeDraftScope) return;
      const draft = captureWorkspaceDraft();
      const signature = draftSignatures(draft);
      // Loading, hiding and destroying an untouched composition are not edits.
      // Selection/layout changes may only update the draft they belong to.
      if (
        signature.content !== draftBaseline?.content
        || signature.submission !== draftBaseline?.submission
        || (signature.view !== draftBaseline?.view
          && signature.content === draftContentSignature(draftStore.read(state.activeDraftScope)))
      ) {
        draftStore.schedule(state.activeDraftScope, draft);
      }
      draftBaseline = signature;
      if (immediate) draftStore.flush(state.activeDraftScope);
    };
    const saveDraftBeforePageLeave = () => saveActiveDraft({ immediate: true });
    const saveDraftWhenHidden = () => {
      if (document.visibilityState === "hidden") saveDraftBeforePageLeave();
    };
    const restoreDraftScope = (scope, { force = false, focus = document.activeElement === els.input } = {}) => {
      const nextScope = String(scope || "").trim();
      if (!nextScope || !els.input) return false;
      if (!force && state.activeDraftScope === nextScope) return false;
      closeMentionMenu();
      if (state.activeDraftScope && state.activeDraftScope !== nextScope) saveActiveDraft({ immediate: true });
      state.activeDraftScope = nextScope;
      const draft = draftStore.read(nextScope);
      state.recoveredSubmission = draft.submitted_message_id
        ? { messageId: draft.submitted_message_id, content: draftContentSignature(draft) }
        : null;
      state.restoringDraft = true;
      suppressAttachmentReconcile = true;
      try {
        state.attachments = normalizeComposerAttachments(draft.attachments);
        state.refs = normalizeRefs(draft.refs);
        if (draft.input_height > 0) state.composerInputHeight = draft.input_height;
        setComposerDraftInput(els.input, draft.text, state.attachments, {
          ...attachmentPreviewOptions(),
          focus,
        });
        renderAttachmentTrayOnly({ notify: false });
        refreshSlashCommandMenu();
        controller.refreshRefs();
        applyComposerInputLayout();
        if (focus && draft.selection) restoreComposerSelection(els.input, draft.selection);
        updateControls();
      } finally {
        suppressAttachmentReconcile = false;
        state.restoringDraft = false;
      }
      draftBaseline = draftSignatures(captureWorkspaceDraft());
      return true;
    };
    const clearActiveDraft = () => {
      if (state.activeDraftScope) draftStore.clear(state.activeDraftScope);
      draftBaseline = draftSignatures(captureWorkspaceDraft());
    };
    const clearComposerInput = ({ notify = true, focus = true } = {}) => {
      const restoring = state.restoringDraft;
      state.restoringDraft = true;
      state.recoveredSubmission = null;
      try {
        setInputText(els.input, "", { focus });
        releaseAttachmentPreviews(state.attachments);
        state.attachments = [];
        state.undoStack = [];
        renderAttachments({ notify });
        closeSlashCommandMenu();
        closeMentionMenu();
      } finally {
        state.restoringDraft = restoring;
      }
    };
    const deleteUploadedAttachment = item => {
      if (!String(item?.upload_id || "").trim()) return Promise.resolve({ deleted: false });
      const remove = options.deleteAttachment || defaultWorkspaceDeleteAttachment;
      return Promise.resolve(remove(item, attachmentUploadContext())).catch(error => {
        if (options.onError) options.onError(error);
        else window.PortalUI.toast(error.message || error, "error");
        return { deleted: false, error };
      });
    };
    const discardComposerAttachments = items => {
      const removed = Array.isArray(items) ? items.filter(Boolean) : [];
      releaseAttachmentPreviews(removed);
      removed.forEach(item => { deleteUploadedAttachment(item); });
    };
    const undoComposerAttachmentInsert = (snapshot) => {
      const ids = new Set((snapshot?.attachmentIds || []).map(id => String(id || "")).filter(Boolean));
      if (!ids.size || !els.input) return false;
      let removed = false;
      for (const id of ids) {
        els.input.querySelectorAll(`[data-portal-composer-attachment-token="${CSS.escape(id)}"]`).forEach(node => {
          const next = node.nextSibling;
          const previous = node.previousSibling;
          if (next?.nodeType === Node.TEXT_NODE && !String(next.textContent || "").trim()) next.remove();
          else if (previous?.nodeType === Node.TEXT_NODE && !String(previous.textContent || "").trim()) previous.remove();
          node.remove();
          removed = true;
        });
      }
      if (!/\s$/.test(String(snapshot?.plainText || ""))) {
        let node = els.input.lastChild;
        while (node?.nodeType === Node.TEXT_NODE && !String(node.textContent || "").trim()) {
          const previous = node.previousSibling;
          node.remove();
          node = previous;
        }
        if (node?.nodeType === Node.TEXT_NODE) {
          node.textContent = String(node.textContent || "").replace(/\s+$/g, "");
        }
      }
      const removedItems = state.attachments.filter(item => ids.has(String(item?.id || "")));
      discardComposerAttachments(removedItems);
      state.attachments = state.attachments.filter(item => !ids.has(String(item?.id || "")));
      renderAttachmentTrayOnly({ notify: true });
      bindImagePreview(els.input, attachmentPreviewOptions());
      updateControls();
      if (removed) els.input.dispatchEvent?.(new Event("input", { bubbles: true }));
      return true;
    };
    const undoComposerMutation = () => {
      const snapshot = state.undoStack[state.undoStack.length - 1];
      if (!snapshot) return false;
      if (snapshot.afterSignature && composerMutationSignature() !== snapshot.afterSignature) return false;
      state.undoStack.pop();
      if (snapshot.kind === "attachment-insert") return undoComposerAttachmentInsert(snapshot);
      return restoreComposerSnapshot(snapshot);
    };
    const reconcileAttachmentsFromInput = (reconcileOptions = {}) => {
      if (suppressAttachmentReconcile || !els.input || "value" in els.input || !state.attachments.length) return false;
      const order = composerAttachmentTokenOrder(els.input);
      const present = new Set(order);
      const byId = new Map(state.attachments.map(item => [String(item?.id || ""), item]));
      const next = order.map(id => byId.get(id)).filter(Boolean);
      const currentIds = state.attachments.map(item => String(item?.id || ""));
      const nextIds = next.map(item => String(item?.id || ""));
      if (next.length === state.attachments.length && nextIds.every((id, idx) => id === currentIds[idx])) return false;
      discardComposerAttachments(state.attachments.filter(item => !present.has(String(item?.id || ""))));
      state.attachments = normalizeComposerAttachments(next);
      renderAttachmentTrayOnly({ notify: reconcileOptions.notify });
      return true;
    };
    const uploadComposerAttachment = async attachmentId => {
      const target = state.attachments.find(item => String(item.id || "") === String(attachmentId || ""));
      if (!target?.file) throw new Error("原始附件已不可用，请重新选择文件。");
      state.attachments = normalizeComposerAttachments(state.attachments.map(item => item.id === target.id ? {
        ...item,
        uploading: true,
        progress: 0,
        error: "",
      } : item));
      renderAttachments({ notify: true });
      try {
        const upload = options.uploadAttachment || defaultWorkspaceUpload;
        const result = await upload(target.file, {
          ...attachmentUploadContext(),
          item: target,
          workspace: controller,
        });
        if (!state.attachments.some(item => String(item?.id || "") === String(target.id || ""))) {
          await deleteUploadedAttachment({ ...target, ...result, kind: target.kind });
          return;
        }
        if (result?.preview_url && result.preview_url !== target.preview_url) {
          releaseAttachmentPreview(target.preview_url);
        }
        state.attachments = normalizeComposerAttachments(state.attachments.map(item => item.id === target.id ? {
          ...item,
          upload_id: result?.upload_id || "",
          file_id: result?.file_id || "",
          name: result?.filename || item.name,
          path: result?.path || item.path || "",
          preview_url: result?.preview_url || item.preview_url || "",
          data_url: result?.data_url || item.data_url || "",
          mime: result?.mime || result?.content_type || item.mime || item.content_type || "",
          content_type: result?.content_type || item.content_type || "",
          size: Number(result?.size || item.size || 0),
          size_bytes: Number(result?.size_bytes || result?.size || item.size_bytes || item.size || 0),
          uploading: false,
          progress: 100,
          error: "",
          file: null,
        } : item));
      } catch (error) {
        state.attachments = normalizeComposerAttachments(state.attachments.map(item => item.id === target.id ? {
          ...item,
          uploading: false,
          error: error.message || String(error || "上传失败"),
        } : item));
        throw error;
      } finally {
        renderAttachments({ notify: true });
      }
    };
    const addFiles = async (files) => {
      const now = Date.now();
      const seen = new Set();
      const list = Array.from(files || []).filter(file => {
        if (!file) return false;
        const signature = fileSignature(file);
        if (seen.has(signature)) return false;
        seen.add(signature);
        const previous = Number(state.recentFileSignatures.get(signature) || 0);
        if (previous && now - previous < 900) return false;
        state.recentFileSignatures.set(signature, now);
        return true;
      });
      state.recentFileSignatures.forEach((timestamp, signature) => {
        if (now - Number(timestamp || 0) > 3000) state.recentFileSignatures.delete(signature);
      });
      if (!list.length) return;
      const maxFileSize = Number(options.maxFileSize || 512 * 1024 * 1024);
      const tooLarge = list.find(file => Number(file.size || 0) > maxFileSize);
      if (tooLarge) throw new Error(`文件过大：${tooLarge.name || "attachment"}，上限 ${Math.round(maxFileSize / 1024 / 1024)} MB`);
      const undoFrame = pushComposerUndoSnapshot();
      const drafts = list.map(file => {
        const kind = String(file.type || "").toLowerCase().startsWith("image/") ? "image" : "file";
        const previewUrl = kind === "image" ? URL.createObjectURL(file) : "";
        if (previewUrl) ownedAttachmentPreviews.add(previewUrl);
        return {
          id: `uploading:${Date.now()}:${Math.random().toString(16).slice(2)}`,
          kind,
          name: file.name || (kind === "image" ? window.PortalI18n.t("pasted image") : "attachment"),
          content_type: file.type || "",
          size: Number(file.size || 0),
          preview_url: previewUrl,
          path: "",
          uploading: true,
          progress: 0,
          error: "",
          file,
        };
      });
      if (undoFrame) {
        undoFrame.kind = "attachment-insert";
        undoFrame.attachmentIds = drafts.map(item => item.id);
      }
      state.attachments = normalizeComposerAttachments([...state.attachments, ...drafts]);
      suppressAttachmentReconcile = true;
      try {
        drafts.forEach(item => insertComposerAttachmentRef(els.input, item, { forceDom: true }));
      } finally {
        suppressAttachmentReconcile = false;
      }
      renderAttachments({ notify: true });
      markComposerUndoAfterSnapshot(undoFrame);
      for (const draft of drafts) {
        try {
          await uploadComposerAttachment(draft.id);
        } catch {}
      }
    };
    const addReference = (reference) => {
      const item = reference?.content_type === "text/x-portal-preview-reference"
        ? normalizeComposerAttachment(reference)
        : createConversationSelectionReference(reference);
      if (!item || !composerPreviewReference(item)) return null;
      const undoFrame = pushComposerUndoSnapshot();
      if (undoFrame) {
        undoFrame.kind = "attachment-insert";
        undoFrame.attachmentIds = [item.id];
      }
      state.attachments = normalizeComposerAttachments([...state.attachments, item]);
      suppressAttachmentReconcile = true;
      try {
        insertComposerAttachmentRef(els.input, item, { forceDom: true });
      } finally {
        suppressAttachmentReconcile = false;
      }
      renderAttachments({ notify: true });
      markComposerUndoAfterSnapshot(undoFrame);
      saveActiveDraft();
      return item;
    };
    const patchComposerElement = (current, desired) => {
      if (!current || !desired) return;
      for (const attribute of [...current.attributes]) {
        if (attribute.name.endsWith("-bound") || attribute.name === "data-portal-icon-mounted") continue;
        if (!desired.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
      }
      for (const attribute of [...desired.attributes]) {
        current.setAttribute(attribute.name, attribute.value);
      }
      if (current.innerHTML !== desired.innerHTML) current.replaceChildren(...desired.cloneNode(true).childNodes);
    };
    const composerControlKey = node => {
      if (!node) return "";
      if (node.classList?.contains("refsWrap")) return "__refs__";
      return String(node.id || node.dataset?.workspaceComposerControl || "");
    };
    const patchComposerShell = (composerOptions = {}) => {
      const template = document.createElement("template");
      template.innerHTML = window.PortalI18n.html`<div>${workspaceComposerHtml(composerShellOptions(composerOptions))}</div>`;
      const desired = template.content.firstElementChild;
      if (!desired || !els.input) return false;

      const desiredInput = desired.querySelector(`#${CSS.escape(dom.inputId)}`);
      if (desiredInput) {
        els.input.setAttribute("data-placeholder", desiredInput.dataset.placeholder || "");
        els.input.setAttribute("contenteditable", desiredInput.getAttribute("contenteditable") || "true");
        els.input.setAttribute("role", desiredInput.getAttribute("role") || "textbox");
        els.input.setAttribute("aria-multiline", desiredInput.getAttribute("aria-multiline") || "true");
      }

      for (const position of ["before", "after"]) {
        const selector = `[data-workspace-composer-extension="${position}"]`;
        const current = composer.querySelector(selector);
        const next = desired.querySelector(selector);
        if (current && next && current.innerHTML !== next.innerHTML) current.innerHTML = next.innerHTML;
      }
      for (const selector of ["[data-workspace-queue-host]", "[data-workspace-composer-meta]"]) {
        const current = composer.querySelector(selector);
        const next = desired.querySelector(selector);
        if (current && next && current.innerHTML !== next.innerHTML) current.innerHTML = next.innerHTML;
      }

      const currentControls = composer.querySelector("[data-workspace-composer-controls]");
      const desiredControls = desired.querySelector("[data-workspace-composer-controls]");
      if (currentControls && desiredControls) {
        const currentByKey = new Map(
          [...currentControls.children].map(node => [composerControlKey(node), node]).filter(([key]) => key),
        );
        const retained = new Set();
        [...desiredControls.children].forEach((next, index) => {
          const key = composerControlKey(next);
          let current = currentByKey.get(key) || null;
          if (current && key === "__refs__") {
            retained.add(current);
          } else if (current) {
            patchComposerElement(current, next);
            retained.add(current);
          } else {
            current = next.cloneNode(true);
            retained.add(current);
          }
          const position = currentControls.children[index] || null;
          if (position !== current) currentControls.insertBefore(current, position);
        });
        [...currentControls.children].forEach(node => {
          if (retained.has(node)) return;
          if (node.classList.contains("refsWrap")) {
            refsControlBinding?.destroy();
            refsControlBinding = null;
          }
          node.remove();
        });
      }

      const currentSummary = composer.querySelector(`[data-refs-summary="${CSS.escape(refsScope)}"]`);
      const desiredSummary = desired.querySelector(`[data-refs-summary="${CSS.escape(refsScope)}"]`);
      if (!desiredSummary && currentSummary) currentSummary.remove();
      else if (desiredSummary && !currentSummary) {
        const after = composer.querySelector('[data-workspace-composer-extension="after"]');
        composer.insertBefore(desiredSummary.cloneNode(true), after || null);
      }
      window.PortalUIPrimitives?.mountIcons?.(composer);
      return true;
    };
    const renderComposerNow = (normalizedComposer, signature) => {
      state.composer = normalizedComposer;
      state.composerSignature = signature;
      const existingInput = els.input;
      const composerOptions = {
        placeholder: state.composer.placeholder,
        meta: state.composer.meta,
        metaHtml: state.composer.metaHtml || options.metaHtml || "",
        beforeComposerHtml: state.composer.beforeComposerHtml ?? options.beforeComposerHtml ?? "",
        afterComposerHtml: state.composer.afterComposerHtml || options.afterComposerHtml || "",
        send_label: state.composer.send_label,
        queue: state.composer.queue || null,
        can_attach: state.composer.can_attach,
        refs_enabled: state.composer.refs_enabled,
        can_edit: state.composer.can_edit,
        can_stop: state.composer.can_stop,
        requires_text: state.composer.requires_text,
        capabilities: state.composer.capabilities,
      };
      if (existingInput) {
        if (!patchComposerShell(composerOptions)) {
          throw new Error("PortalChatWorkspace failed to patch the mounted composer");
        }
      } else {
        renderComposerShell(composerOptions);
      }
      state.composerQueueSignature = JSON.stringify(state.composer.queue || null);
      Object.assign(els, {
        composer,
        input: root.querySelector(`#${CSS.escape(dom.inputId)}`),
        tray: root.querySelector(`#${CSS.escape(dom.trayId)}`),
        fileInput: root.querySelector(`#${CSS.escape(dom.fileInputId)}`),
        attach: root.querySelector(`#${CSS.escape(dom.attachId)}`),
        send: root.querySelector(`#${CSS.escape(dom.sendId)}`),
        stop: root.querySelector(`#${CSS.escape(dom.stopId)}`),
        auto: root.querySelector(`#${CSS.escape(dom.autoId)}`),
        slashCommands: root.querySelector("[data-composer-slash-command-host]"),
        mentions: root.querySelector("[data-composer-mention-host]"),
      });
      if (existingInput && els.input !== existingInput) {
        throw new Error("PortalChatWorkspace contract update replaced the composer input");
      }
      if (!existingInput) {
        state.composerInputFocused = false;
        state.composerImeActive = false;
      }
      bindComposer();
      bindAttachmentRefs(els.composer, {
        previewUrlForPath: options.previewUrlForPath,
      });
      applyComposerInputLayout();
      renderAttachments();
      refreshSlashCommandMenu();
      controller.refreshRefs();
      refsControlBinding?.refresh?.();
      options.onComposerRendered?.(controller);
      updateControls();
    };
    const controller = {
      root,
      els,
      state,
      setLoading(message) {
        setWorkspaceLoading(message);
      },
      clearLoading() {
        clearWorkspaceLoading();
      },
      isLoading: () => state.loading,
      addFiles,
      addReference,
      retryAttachment(id) {
        return uploadComposerAttachment(id);
      },
      attachments: () => state.attachments.slice(),
      messages: () => state.messages.slice(),
      setAttachments(items) {
        state.attachments = normalizeComposerAttachments(items);
        renderAttachments({ notify: true });
      },
      setTitle(title) {
        const node = root.querySelector(".portalWorkspaceHead .paneTitle");
        if (node) node.textContent = String(title || window.PortalI18n.t("Chat Workspace"));
      },
      setSubtitle(subtitle, parts = []) {
        state.subtitle = String(subtitle || "");
        state.subtitleParts = normalizeWorkspaceSubtitleParts(parts);
        renderSubtitle();
      },
      setStopPending(value) { stopPending = Boolean(value); updateControls(); },
      setComposer(nextComposer = {}) {
        if (!state.contract) {
          throw new Error("PortalChatWorkspace composer requires a Chat Workspace contract");
        }
        const rawNext = nextComposer || {};
        const { force_render: _forceRenderSnake, forceRender: _forceRenderCamel, ...next } = { ...rawNext };
        const defaultSendLabel = String(options.send_label || "").trim();
        const normalizedComposer = {
          ...next,
          can_attach: next.can_attach !== false,
          refs_enabled: next.refs_enabled !== false,
          can_edit: next.can_edit !== false,
          can_stop: !!next.can_stop || !!next.canStop,
          can_auto: next.can_auto !== false && next.canAuto !== false,
          requires_text: next.requires_text === true,
          send_label: String(next.send_label ?? defaultSendLabel).trim(),
          capabilities: Array.isArray(next.capabilities) ? next.capabilities.map(item => String(item || "").trim()).filter(Boolean) : [],
          slash_commands: normalizeSlashCommands(next.slash_commands || options.slash_commands),
        };
        const nextDraftScope = configuredHistoryScope(normalizedComposer).trim() || id;
        const draftScopeChanged = state.activeDraftScope !== nextDraftScope;
        if (draftScopeChanged && state.activeDraftScope) saveActiveDraft({ immediate: true });
        const signature = JSON.stringify({
          placeholder: normalizedComposer.placeholder || "",
          meta: normalizedComposer.meta || "",
          can_attach: normalizedComposer.can_attach,
          refs_enabled: normalizedComposer.refs_enabled,
          can_edit: normalizedComposer.can_edit,
          can_stop: normalizedComposer.can_stop,
          can_auto: normalizedComposer.can_auto,
          requires_text: normalizedComposer.requires_text,
          capabilities: normalizedComposer.capabilities,
          runtime_settings: normalizedComposer.runtime_settings || null,
          runtime_backend: normalizedComposer.runtime_backend || "",
          runtime_catalog_key: runtimeCatalogUrl(normalizedComposer),
          metaHtml: normalizedComposer.metaHtml || options.metaHtml || "",
          beforeComposerHtml: normalizedComposer.beforeComposerHtml ?? options.beforeComposerHtml ?? "",
          afterComposerHtml: normalizedComposer.afterComposerHtml || options.afterComposerHtml || "",
          send_label: normalizedComposer.send_label,
          slash_commands: normalizedComposer.slash_commands,
          queue: JSON.stringify(normalizedComposer.queue || null),
        });
        state.composer = normalizedComposer;
        renderSubtitle();
        syncHistory();
        if (signature === state.composerSignature) {
          if (draftScopeChanged) restoreDraftScope(nextDraftScope, { force: true });
          updateControls();
          return;
        }
        renderComposerNow(normalizedComposer, signature);
        if (draftScopeChanged) restoreDraftScope(nextDraftScope, { force: true });
        updateControls();
      },
      updateAttachment(id, patch = {}) {
        const target = String(id || "");
        if (!target) return;
        state.attachments = normalizeComposerAttachments(state.attachments.map(item => String(item?.id || "") === target ? { ...item, ...patch } : item));
        renderAttachments({ notify: true });
      },
      refs: () => normalizeRefs(state.refs),
      setRefs(refs) {
        state.refs = normalizeRefs(refs);
        renderRefsSummary(els.composer, { scope: refsScope, selection: state.refs, contextOptions: contextOptions() });
        saveActiveDraft();
      },
      payload() {
        const nextScope = historyScope();
        if (nextScope && state.activeDraftScope !== nextScope) restoreDraftScope(nextScope, { force: true });
        reconcileAttachmentsFromInput();
        return {
          text: serializeComposerInput(els.input, state.attachments, { mode: "final" }),
          plainText: serializeComposerInput(els.input, state.attachments, { mode: "plain" }),
          content_parts: serializeComposerContentParts(els.input, state.attachments),
          attachments: state.attachments.filter(item => !item.uploading && !item.error),
          refs: normalizeRefs(state.refs),
        };
      },
      refreshRefs() {
        const bindingOptions = {
          scope: refsScope,
          contextOptions: contextOptions(),
          catalogStatus: refsCatalog.status,
          onLoad: loadRefsCatalog,
          getSelection: () => state.refs,
          setSelection: refs => { state.refs = normalizeRefs(refs); },
          getCollapsed: () => state.refsCollapsed,
          setCollapsed: collapsed => { state.refsCollapsed = collapsed || {}; },
          onChange: refs => {
            saveActiveDraft();
            options.onRefsChange?.(refs, controller);
          },
        };
        refsControlBinding = refsControlBinding || bindRefsControl(els.composer, bindingOptions);
        refsControlBinding?.update(bindingOptions).refresh();
      },
      clear(clearOptions = {}) {
        const preserveDraft = clearOptions.clearDraft === false;
        if (preserveDraft) saveActiveDraft({ immediate: true });
        clearComposerInput({ notify: !preserveDraft });
        if (!preserveDraft) clearActiveDraft();
        else draftBaseline = draftSignatures(captureWorkspaceDraft());
      },
      inputText() {
        return serializeComposerInput(els.input, state.attachments, { mode: "plain" });
      },
      draftText() {
        reconcileAttachmentsFromInput();
        return serializeComposerInput(els.input, state.attachments, { mode: "draft" });
      },
      setInputText(value) {
        state.recoveredSubmission = null;
        setInputText(els.input, value || "");
        refreshSlashCommandMenu();
        updateControls();
      },
      setDraftText(value, attachments = null) {
        state.recoveredSubmission = null;
        suppressAttachmentReconcile = true;
        try {
          if (attachments !== null) state.attachments = normalizeComposerAttachments(attachments);
          setComposerDraftInput(els.input, value || "", state.attachments, attachmentPreviewOptions());
          renderAttachments({ notify: true });
          refreshSlashCommandMenu();
        } finally {
          suppressAttachmentReconcile = false;
        }
        updateControls();
        saveActiveDraft();
      },
      draftScope() {
        return state.activeDraftScope || historyScope();
      },
      suspendDraftPersistence() {
        if (
          typeof draftStore.suspend !== "function"
          || typeof draftStore.resume !== "function"
        ) {
          throw new Error("Chat Workspace draft store cannot enter ephemeral mode");
        }
        if (draftPersistenceSuspension) {
          draftPersistenceSuspension.depth += 1;
          draftStore.suspend();
          return false;
        }
        const activeDraftScope = state.activeDraftScope || historyScope();
        const inputSnapshot = composerSnapshot();
        const draftSnapshot = captureWorkspaceDraft();
        draftPersistenceSuspension = {
          depth: 1,
          snapshot: {
            activeDraftScope,
            recoveredSubmission: state.recoveredSubmission,
            draftBaseline,
            composer: {
              ...state.composer,
              capabilities: Array.isArray(state.composer.capabilities)
                ? [...state.composer.capabilities]
                : [],
            },
            composerImeActive: state.composerImeActive,
            composerInputFocused: state.composerInputFocused,
            composerInputHeight: state.composerInputHeight,
            composerQueueSignature: state.composerQueueSignature,
            composerSignature: state.composerSignature,
            inputFocused: document.activeElement === els.input,
            inputSnapshot,
            refs: normalizeRefs(state.refs),
            refsCollapsed: { ...(state.refsCollapsed || {}) },
            undoStack: state.undoStack.slice(),
          },
        };
        draftStore.suspend([[activeDraftScope, draftSnapshot]]);
        return true;
      },
      resumeDraftPersistence() {
        const suspension = draftPersistenceSuspension;
        if (!suspension) return false;
        if (suspension.depth > 1) {
          suspension.depth -= 1;
          draftStore.resume();
          return false;
        }
        const snapshot = suspension.snapshot;
        try {
          const restoredComposer = {
            ...snapshot.composer,
            capabilities: Array.isArray(snapshot.composer?.capabilities)
              ? [...snapshot.composer.capabilities]
              : [],
          };
          renderComposerNow(restoredComposer, snapshot.composerSignature);
          state.activeDraftScope = snapshot.activeDraftScope;
          draftBaseline = snapshot.draftBaseline;
          state.recoveredSubmission = snapshot.recoveredSubmission;
          state.attachments = normalizeComposerAttachments(
            snapshot.inputSnapshot.attachments || [],
          );
          state.refs = normalizeRefs(snapshot.refs);
          state.refsCollapsed = { ...(snapshot.refsCollapsed || {}) };
          state.composerInputHeight = snapshot.composerInputHeight;
          state.composerQueueSignature = snapshot.composerQueueSignature;
          state.undoStack = snapshot.undoStack.slice();
          state.composerInputFocused = snapshot.composerInputFocused;
          state.composerImeActive = snapshot.composerImeActive;
          suppressAttachmentReconcile = true;
          try {
            if (els.input && "value" in els.input) {
              els.input.value = String(snapshot.inputSnapshot.inputValue || "");
            } else if (els.input) {
              els.input.innerHTML = String(snapshot.inputSnapshot.inputHtml || "");
            }
            renderAttachmentTrayOnly({ notify: false });
            controller.refreshRefs();
            applyComposerInputLayout();
            updateControls();
            if (snapshot.inputFocused) {
              els.input?.focus?.({ preventScroll: true });
              restoreComposerSelection(els.input, snapshot.inputSnapshot.selection);
            }
          } finally {
            suppressAttachmentReconcile = false;
          }
        } finally {
          draftPersistenceSuspension = null;
          draftStore.resume();
        }
        return true;
      },
      draftPersistenceSuspended() {
        return !!draftPersistenceSuspension;
      },
      saveDraft(saveOptions = {}) {
        saveActiveDraft({ immediate: saveOptions.immediate !== false });
      },
      restoreDraft(scope = historyScope(), restoreOptions = {}) {
        return restoreDraftScope(scope, { force: restoreOptions.force !== false });
      },
      clearDraft(scope = state.activeDraftScope || historyScope(), expected = null) {
        if (expected && draftContentSignature(draftStore.read(scope)) !== draftContentSignature(normalizeWorkspaceDraft(expected))) return false;
        draftStore.clear(scope);
        if (scope === state.activeDraftScope) draftBaseline = draftSignatures(captureWorkspaceDraft());
        return true;
      },
      confirmSubmittedMessages(messageIds, scope = state.activeDraftScope || historyScope()) {
        const accepted = new Set((messageIds || []).filter(Boolean));
        if (!accepted.size) return;
        const storedDraft = draftStore.read(scope);
        if (accepted.has(storedDraft.submitted_message_id)) draftStore.clear(scope);
        if (state.activeDraftScope !== scope) return;
        const draft = captureWorkspaceDraft();
        if (!accepted.has(draft.submitted_message_id)) return;
        // Clear only this unedited recovery, leaving a newer shared draft intact.
        clearComposerInput({ focus: false });
        state.refs = normalizeRefs({});
        controller.refreshRefs();
        draftBaseline = draftSignatures(captureWorkspaceDraft());
      },
      quote: () => composerQuote(),
      setQuote(quote) {
        const scope = composerQuoteScope();
        const messageId = String(quote?.message_id || "").trim();
        const text = String(quote?.text || "").trim();
        if (!scope || !messageId || !text) return false;
        composerQuotes.set(scope, { message_id: messageId, author_id: String(quote.author_id || ""), text });
        renderComposerQuote();
        els.input?.focus?.();
        return true;
      },
      // Drops a conversation's quote; given a message ID, only while it still quotes that message.
      clearQuote({ scope = composerQuoteScope(), messageId = "" } = {}) {
        const current = composerQuotes.get(scope);
        if (!current || (messageId && current.message_id !== messageId)) return false;
        composerQuotes.delete(scope);
        renderComposerQuote();
        return true;
      },
      // Shows a message of this conversation, loading earlier history until it is there.
      async revealMessage(messageId) {
        const key = String(messageId || "").trim();
        if (!key || !els.messages) return false;
        const scope = historyScope();
        const find = () => els.messages.querySelector(`.messageList > [data-message-key="${CSS.escape(key)}"]`);
        let row = find();
        while (!row) {
          if (!options.onLoadOlder || !state.messageRenderOptions?.history?.can_load_older) break;
          const before = `${state.messages.length}:${messageRenderKey(state.messages[0] || {})}`;
          await options.onLoadOlder(controller);
          await new Promise(resolve => window.requestAnimationFrame(resolve));
          if (refsCatalog.disposed || historyScope() !== scope) return false;
          row = find();
          if (!row && before === `${state.messages.length}:${messageRenderKey(state.messages[0] || {})}`) break;
        }
        if (row?.dataset.workGroupId && state.collapsedWorkGroups.has(row.dataset.workGroupId)) {
          els.messages.querySelector(`[data-portal-work-group-toggle="${CSS.escape(row.dataset.workGroupId)}"]`)?.click();
          row = find();
        }
        if (row?.hidden && row.dataset.agentGroupId) {
          els.messages.querySelector(`[data-portal-agent-group-toggle="${CSS.escape(row.dataset.agentGroupId)}"]`)?.click();
          row = find();
        }
        if (!row || row.hidden) {
          window.PortalUI?.toast?.("这条消息已不在当前对话中。", "warning");
          return false;
        }
        const fold = row.querySelector('[data-message-fold-toggle][aria-expanded="false"]');
        if (fold) toggleMessageFold(fold);
        const box = row.getBoundingClientRect();
        const view = els.messages.getBoundingClientRect();
        els.messages.scrollTop += box.top - view.top - Math.max(12, (view.height - box.height) / 2);
        state.messageTailFollowIntent = messageViewportAtTail(els.messages);
        syncMessageScrollMode();
        row.classList.remove("messageQuoteTarget");
        void row.offsetWidth;
        row.classList.add("messageQuoteTarget");
        window.setTimeout(() => row.classList.remove("messageQuoteTarget"), 1600);
        return true;
      },
      async submit(send, submitOptions = {}) {
        if (typeof send !== "function") throw new Error("workspace.submit requires a send function");
        const nextScope = historyScope();
        if (nextScope && state.activeDraftScope !== nextScope) restoreDraftScope(nextScope, { force: true });
        const scope = state.activeDraftScope || historyScope();
        const sequence = ++state.submissionSequence;
        const snapshot = composerSnapshot();
        const draftSnapshot = captureWorkspaceDraft();
        const payload = submitOptions.payload && typeof submitOptions.payload === "object"
          ? submitOptions.payload
          : controller.payload();
        const refsSnapshot = refsPayload(payload.refs || state.refs);
        const historyText = String(submitOptions.historyText ?? payload.plainText ?? payload.text ?? "").trim();
        const historyDraftText = String(draftSnapshot.text || historyText).trim();
        const context = {
          scope,
          sequence,
          payload,
          refs: refsSnapshot,
          snapshot,
          workspace: controller,
          messageIdentity: newMessageIdentity(),
        };
        const optimistic = submitOptions.optimistic === true;
        if (submitOptions.clear !== false) controller.clear();
        if (optimistic && submitOptions.clear !== false) {
          state.refs = normalizeRefs({});
          controller.refreshRefs();
          controller.clearQuote({ scope });
          clearActiveDraft();
        }
        const submittedSignature = composerMutationSignature();
        if (!optimistic && submitOptions.busy !== false) controller.setBusy(true);
        if (optimistic && submitOptions.focus !== false) els.input?.focus?.();
        let committed = false;
        const accept = () => {
          const clearedSignature = composerMutationSignature();
          if (!optimistic && submitOptions.clearRefsOnSuccess && state.activeDraftScope === scope) {
            const refsUnchanged = JSON.stringify(refsPayload(state.refs)) === JSON.stringify(refsSnapshot);
            if (refsUnchanged) {
              state.refs = normalizeRefs({});
              controller.refreshRefs();
            }
            if (composerMutationSignature() === clearedSignature && refsUnchanged) clearActiveDraft();
            else saveActiveDraft({ immediate: true });
          }
        };
        try {
          const result = await send(payload, context);
          committed = true;
          context.result = result;
          accept();
          if (submitOptions.rememberHistory !== false && historyDraftText) {
            history.remember(scope, {
              // A conversation that names the message itself (a Chatroom) gives its ID back.
              id: submitOptions.sentMessageId?.(result) || context.messageIdentity,
              text: historyDraftText,
              meta: { attachments: draftSnapshot.attachments },
            });
          }
          await submitOptions.onSuccess?.(result, context);
          return result;
        } catch (error) {
          context.error = error;
          const sameScope = state.activeDraftScope === scope;
          const composerUntouched = composerMutationSignature() === submittedSignature;
          if (!committed && !optimistic && submitOptions.clear !== false && sameScope && composerUntouched && state.submissionSequence === sequence) {
            restoreComposerSnapshot(snapshot);
            state.refs = normalizeRefs(refsSnapshot);
            controller.refreshRefs();
            state.recoveredSubmission = {
              messageId: context.messageIdentity,
              content: draftContentSignature(captureWorkspaceDraft()),
            };
            saveActiveDraft({ immediate: true });
          } else if (!committed && !optimistic && submitOptions.clear !== false && !sameScope) {
            if (!workspaceDraftHasContent(draftStore.read(scope))) {
              draftStore.write(scope, { ...draftSnapshot, submitted_message_id: context.messageIdentity });
            }
          }
          if (!committed) await submitOptions.onRollback?.(error, context);
          else await submitOptions.onSuccessError?.(error, context);
          throw error;
        } finally {
          if (!optimistic && submitOptions.busy !== false && state.submissionSequence === sequence) controller.setBusy(false);
          if (!optimistic && submitOptions.focus !== false && state.activeDraftScope === scope) els.input?.focus?.();
        }
      },
      setBusy(value) {
        state.busy = !!value;
        updateControls();
      },
      updateControls,
      setStatus(text) {
        state.subtitle = String(text || "");
        state.subtitleParts = [];
        renderSubtitle();
      },
      followLatest() {
        state.messageTailFollowIntent = true;
        syncMessageScrollMode();
        if (messageViewportMeasurable()) {
          els.messages.scrollTop = els.messages.scrollHeight;
        }
      },
      appendMessage(message) {
        const previous = state.messages;
        const list = els.messages?.firstElementChild;
        if (!message?.id || state.messageByKey.has(String(message.id))
          || !previous.length || !list?.classList.contains("messageList")
          || state.messageItemSignatures.length !== previous.length
          || state.messageScrollScope !== String(historyScope() || "")
          || (state.runtimeLive && messageRole(message) !== "agent")) return false;
        const role = messageRole(message);
        let start = previous.length - 1;
        while (start > 0 && messageRole(previous[start - 1]) === messageRole(previous[start])) start -= 1;
        const first = list.querySelector(messageInstanceSelector(messageInstanceKey(previous[start])));
        if (!first) return false;
        const prefixNode = first.previousElementSibling;
        const wasFollowing = state.messageTailFollowIntent;
        const oldLatest = state.latestFoldReplyKey;
        state.messages = [...previous, message];
        state.contract = { ...state.contract, messages: state.messages };
        syncHistory();
        state.messageItemSignatures.push(JSON.stringify(message));
        state.messageByKey.set(messageIdentity(message), message);
        const key = messageRenderKey(message);
        if (message.pending || message.live_output || (state.runtimeLive && role === "agent")) {
          state.messageFoldDefaults.add(key);
        }
        if (messageFoldReply(message)) {
          const at = Date.parse(String(message.created_at || ""));
          if (!(at < state.latestFoldReplyAt)) {
            state.latestFoldReplyKey = key;
            if (Number.isFinite(at)) state.latestFoldReplyAt = at;
            state.messageFoldDefaults.add(key);
            if (oldLatest && oldLatest !== key && !state.runtimeLive) {
              const oldMessage = state.messageByKey.get(oldLatest);
              if (!oldMessage?.pending && !oldMessage?.live_output) {
                state.messageFoldDefaults.delete(oldLatest);
                if (!messageFoldChoices.has(oldLatest)) {
                  const oldNode = list.querySelector(`[data-message-key="${CSS.escape(oldLatest)}"]`);
                  applyMessageFold(oldNode?.querySelector("[data-message-fold-toggle]"), false);
                }
              }
            }
          }
        }
        conversationSearch?.update(true);
        const renderOptions = state.messageRenderOptions;
        const html = messageListHtml(state.messages.slice(start), {
          ...options, ...renderOptions,
          chatResidentId: contractResidentId(),
          inheritance: null,
          history: {},
          runtimeDetails: state.runtimeDetails,
          runtimeLive: state.runtimeLive,
          runtimePhase: state.runtimePhase,
          autoCollapseAgentGroups: renderOptions.autoCollapseAgentGroups === true,
          collapsedAgentGroups: state.collapsedAgentGroups,
          collapsedWorkGroups: state.collapsedWorkGroups,
          expandedAgentGroups: state.expandedAgentGroups,
          messageFoldDefaults: state.messageFoldDefaults,
          messageFoldSizes: state.messageFoldSizes,
          messageIndexOffset: start,
          allMessages: state.messages,
        });
        const replacedKeys = previous.slice(start).flatMap(item => [messageInstanceKey(item), messageAfterKey(messageInstanceKey(item))]);
        replacedKeys.push("__runtime_phase__", "__subagents__", "__runtime__", "__scroll_anchor__");
        const changed = reconcileMessageList(els.messages, html, {
          currentStartNode: first,
          prefixNode,
          replacedKeys,
        })?.changedRoots || [];
        bindDefaultMessageActions();
        for (const root of changed) {
          bindAttachmentRefs(root, {
            previewUrlForPath: renderOptions.previewUrlForPath || options.previewUrlForPath,
            onOpen: (kind, path, node) => {
              const attachmentKind = String(kind || "") === "file" && window.PortalFileLinks?.kindForPath(path) !== "image" ? "file" : "image";
              const attachmentPath = String(path || "").trim();
              if (attachmentKind === "file") {
                const descriptor = window.PortalFileLinks?.describe(attachmentPath) || { path: attachmentPath };
                const request = { ...descriptor, href: descriptor.path, title: node?.textContent?.trim() || attachmentPath, node, source: "chat", target: els.messages };
                const open = renderOptions.onLinkPreview || options.onLinkPreview || requestWorkspacePreview;
                Promise.resolve().then(() => open(request)).catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || String(error), "error"));
                return;
              }
              const previewUrl = attachmentKind === "image" && (renderOptions.previewUrlForPath || options.previewUrlForPath)
                ? (renderOptions.previewUrlForPath || options.previewUrlForPath)(attachmentPath) : "";
              if (previewUrl) openMessageImageEditor(previewUrl, attachmentPath);
              else void window.PortalUI.copyText(attachmentPath, window.PortalI18n.t("附件路径"));
            },
            onImagePreview: context => openMessageImageEditor(context?.src, context?.title),
          });
          bindResponseSourcePaths(root, { onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview });
          bindResourceChips(root, { onResourcePreview: renderOptions.onResourcePreview || options.onResourcePreview });
          bindMessageAvatars(root);
          if (options.enableLinkPreview || renderOptions.enableLinkPreview) {
            bindLinkPreview(root, { onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview });
          }
        }
        measureMessageFolds(changed);
        revealFinishedSubagents(els.messages, options.onSubagentFinished);
        options.onMessagesRendered?.(els.messages, controller, true, changed);
        syncMessageScrollMode();
        if (wasFollowing && messageViewportMeasurable()) els.messages.scrollTop = els.messages.scrollHeight;
        return true;
      },
      setMessages(messages, renderOptions = {}) {
        const previousMessages = state.messages;
        const previousRuntimeSignature = JSON.stringify({
          details: state.runtimeDetails,
          live: state.runtimeLive,
          phase: state.runtimePhase,
        });
        const previousScrollHeight = Number(els.messages?.scrollHeight || 0);
        const previousScrollTop = Number(els.messages?.scrollTop || 0);
        const previousItemSignatures = state.messageItemSignatures;
        const previousRenderContextSignature = state.messageRenderContextSignature;
        const previousRendererRefs = state.messageRendererRefs;
        const previousDraftScope = String(state.activeDraftScope || "");
        const nextScope = String(historyScope() || "");
        const scopeChanged = state.messageScrollScope === null || state.messageScrollScope !== nextScope;
        state.messageScrollScope = nextScope;
        if (nextScope && previousDraftScope !== nextScope) restoreDraftScope(nextScope, { force: true });
        state.messages = Array.isArray(messages) ? messages : [];
        state.runtimeDetails = Array.isArray(renderOptions.runtimeDetails) ? renderOptions.runtimeDetails : [];
        state.runtimeLive = !!renderOptions.runtimeLive;
        state.runtimePhase = String(renderOptions.runtimePhase || "");
        const nextRuntimeSignature = JSON.stringify({
          details: state.runtimeDetails,
          live: state.runtimeLive,
          phase: state.runtimePhase,
        });
        state.messageRenderOptions = { ...renderOptions };
        syncHistory(nextScope);
        const foldChanges = updateMessageFoldDefaults();
        const nextItemSignatures = state.messages.map(item => JSON.stringify(item));
        const nextRendererRefs = [
          renderOptions.renderMarkdown || options.renderMarkdown,
          renderOptions.formatTime || options.formatTime,
          renderOptions.renderMessagePrefix || options.renderMessagePrefix,
          renderOptions.renderMessageBody || options.renderMessageBody,
          renderOptions.renderMessageActions || options.renderMessageActions,
          renderOptions.renderMessageMenuItems || options.renderMessageMenuItems,
          renderOptions.showMessageActions || options.showMessageActions,
          renderOptions.messageCopyValue || options.messageCopyValue,
          renderOptions.renderMessageFooter || options.renderMessageFooter,
          renderOptions.renderAfterMessage || options.renderAfterMessage,
          renderOptions.messageClass || options.messageClass,
          renderOptions.previewUrlForPath || options.previewUrlForPath,
        ];
        const nextRenderContextSignature = JSON.stringify({
          ...renderOptions,
          chatResidentId: contractResidentId(),
          messageRenderState: options.messageRenderState?.(),
          runtimeDetails: undefined,
          runtimeLive: undefined,
          runtimePhase: undefined,
          force: undefined,
          collapsedAgentGroups: [...state.collapsedAgentGroups].sort(),
          collapsedWorkGroups: [...state.collapsedWorkGroups].sort(),
          expandedAgentGroups: [...state.expandedAgentGroups].sort(),
        });
        const nextRenderSignature = JSON.stringify({
          emptyText: renderOptions.emptyText || options.emptyText || "",
          userName: renderOptions.userName || options.userName || "",
          assistantName: renderOptions.assistantName || options.assistantName || "",
          autoCollapseAgentGroups: renderOptions.autoCollapseAgentGroups === true,
          collapsedAgentGroups: [...state.collapsedAgentGroups].sort(),
          collapsedWorkGroups: [...state.collapsedWorkGroups].sort(),
          expandedAgentGroups: [...state.expandedAgentGroups].sort(),
        });
        const messageItemsUnchanged = previousItemSignatures.length === nextItemSignatures.length
          && previousItemSignatures.every(
            (signature, index) => signature === nextItemSignatures[index],
          );
        const rendererRefsUnchanged = previousRendererRefs.length === nextRendererRefs.length
          && nextRendererRefs.every((ref, index) => ref === previousRendererRefs[index]);
        conversationSearch?.update(!messageItemsUnchanged);
        if (
          nextRenderSignature === state.messageRenderSignature
          && messageItemsUnchanged
          && previousRenderContextSignature === nextRenderContextSignature
          && rendererRefsUnchanged
          && !renderOptions.force
          && !foldChanges.length
        ) {
          if (previousRuntimeSignature !== nextRuntimeSignature) {
            controller.setRuntime(state.runtimeDetails, state.runtimeLive, renderOptions);
          }
          return;
        }
        state.messageRenderSignature = nextRenderSignature;
        const canReconcileSuffix = !renderOptions.force
          && !scopeChanged
          && previousMessages.length > 0
          && state.messages.length > 0
          && previousRenderContextSignature === nextRenderContextSignature
          && rendererRefsUnchanged
          && els.messages.firstElementChild?.classList.contains("messageList")
          && previousItemSignatures.length === previousMessages.length;
        const prependedCount = state.messages.length - previousMessages.length;
        const prependOnly = !renderOptions.force
          && !scopeChanged
          && prependedCount > 0
          && previousItemSignatures.length === previousMessages.length
          && previousItemSignatures.every(
            (signature, index) => signature === nextItemSignatures[index + prependedCount],
          );
        state.messageItemSignatures = nextItemSignatures;
        state.messageRenderContextSignature = nextRenderContextSignature;
        state.messageRendererRefs = nextRendererRefs;
        const renderedStream = normalizeMessageStream(state.messages, { ...options, ...renderOptions });
        state.messageByKey = new Map(renderedStream.messages.map((item) => [
          messageIdentity(item),
          item,
        ]));
        const followTail = scopeChanged || state.messageTailFollowIntent;
        state.messageTailFollowIntent = followTail;
        if (scopeChanged) {
          messageScrollGesturePending = false;
          messageScrollPointerActive = false;
          messageScrollTouchActive = false;
        }
        syncMessageScrollMode();
        let renderStartIndex = 0;
        let renderEndIndex = state.messages.length;
        const canReconcilePrepend = canReconcileSuffix && prependOnly
          && previousRuntimeSignature === nextRuntimeSignature;
        if (canReconcilePrepend) {
          renderEndIndex = prependedCount;
          // Loading the first history page introduces a boundary on the existing facade message.
          if (state.messages[prependedCount - 1]?.family_history && !state.messages[prependedCount]?.family_history) {
            renderEndIndex = Math.min(state.messages.length, prependedCount + 1);
          }
          // Older pages may move a work's header past intervening requests.
          const nextWorkGroups = familyWorkGroups(state.messages);
          for (const [id, work] of familyWorkGroups(previousMessages)) {
            if (nextWorkGroups.get(id)?.headKey === work.headKey) continue;
            const oldHeadIndex = state.messages.findIndex(item => messageRenderKey(item) === work.headKey);
            renderEndIndex = Math.max(renderEndIndex, oldHeadIndex + 1);
          }
          for (const key of foldChanges) {
            const index = state.messages.findIndex(item => messageRenderKey(item) === key);
            renderEndIndex = Math.max(renderEndIndex, index + 1);
          }
          // A prepend may join the first retained reply group or change the
          // compact headers of its first human burst. Stop at a role boundary.
          while (renderEndIndex < state.messages.length
            && messageRole(state.messages[renderEndIndex]) === messageRole(state.messages[renderEndIndex - 1])) {
            renderEndIndex += 1;
          }
        } else if (canReconcileSuffix) {
          const sharedLength = Math.min(previousItemSignatures.length, nextItemSignatures.length);
          while (renderStartIndex < sharedLength
            && previousItemSignatures[renderStartIndex] === nextItemSignatures[renderStartIndex]) {
            renderStartIndex += 1;
          }
          for (const key of foldChanges) {
            const index = state.messages.findIndex(item => messageRenderKey(item) === key);
            if (index >= 0) renderStartIndex = Math.min(renderStartIndex, index);
          }
          // Editing or removing a group's tail changes its preceding members'
          // disclosure state too. Preserve only the prefix outside that group.
          if (
            messageRole(state.messages[renderStartIndex]) === "agent"
            || messageRole(previousMessages[renderStartIndex]) === "agent"
            || renderStartIndex === state.messages.length
          ) {
            while (renderStartIndex > 0 && messageRole(state.messages[renderStartIndex - 1]) === "agent") {
              renderStartIndex -= 1;
            }
          }
        }
        rememberSubagentPlaces(els.messages);
        const messageHtml = messageListHtml(state.messages, {
          ...options,
          ...renderOptions,
          chatResidentId: contractResidentId(),
          inheritanceView: inheritanceViewFor(renderOptions.inheritance),
          runtimeDetails: state.runtimeDetails,
          runtimeLive: state.runtimeLive,
          runtimePhase: state.runtimePhase,
          autoCollapseAgentGroups: renderOptions.autoCollapseAgentGroups === true,
          collapsedAgentGroups: state.collapsedAgentGroups,
          collapsedWorkGroups: state.collapsedWorkGroups,
          expandedAgentGroups: state.expandedAgentGroups,
          messageFoldDefaults: state.messageFoldDefaults,
          messageFoldSizes: state.messageFoldSizes,
          renderStartIndex,
          renderEndIndex,
        });
        const partialRange = canReconcilePrepend || (canReconcileSuffix && renderStartIndex > 0);
        const reconciledRange = reconcileMessageList(els.messages, messageHtml, partialRange ? {
          prefixKey: renderStartIndex > 0 ? messageInstanceKey(state.messages[renderStartIndex - 1]) : "",
          replacedKeys: [
            ...previousMessages.slice(renderStartIndex, canReconcilePrepend ? renderEndIndex - prependedCount : undefined)
              .flatMap(item => [messageInstanceKey(item), messageAfterKey(messageInstanceKey(item))]),
            ...(canReconcilePrepend ? ["__history__", "__inheritance__"] : ["__runtime_phase__", "__subagents__", "__runtime__", "__scroll_anchor__"]),
          ],
        } : {});
        bindDefaultMessageActions();
        const previewUrlForPath = renderOptions.previewUrlForPath || options.previewUrlForPath;
        // Only new/replaced message subtrees need binding and measurement.
        const bindingRoots = reconciledRange ? reconciledRange.changedRoots : [els.messages];
        for (const bindingRoot of bindingRoots) {
          bindAttachmentRefs(bindingRoot, {
            previewUrlForPath,
            onOpen: (kind, path, node) => {
              const attachmentKind = String(kind || "") === "file" && window.PortalFileLinks?.kindForPath(path) !== "image" ? "file" : "image";
              const attachmentPath = String(path || "").trim();
              if (attachmentKind === "file") {
                const descriptor = window.PortalFileLinks?.describe(attachmentPath) || { path: attachmentPath };
                const request = { ...descriptor, href: descriptor.path, title: node?.textContent?.trim() || attachmentPath, node, source: "chat", target: els.messages };
                const open = renderOptions.onLinkPreview || options.onLinkPreview || requestWorkspacePreview;
                Promise.resolve().then(() => open(request)).catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || String(error), "error"));
                return;
              }
              const previewUrl = attachmentKind === "image" && previewUrlForPath
                ? previewUrlForPath(attachmentPath)
                : "";
              if (previewUrl) {
                openMessageImageEditor(previewUrl, attachmentPath);
                return;
              }
              void window.PortalUI.copyText(attachmentPath, window.PortalI18n.t("附件路径"));
            },
            onImagePreview: context => openMessageImageEditor(context?.src, context?.title),
          });
          bindResponseSourcePaths(bindingRoot, {
            onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview,
          });
          bindResourceChips(bindingRoot, {
            onResourcePreview: renderOptions.onResourcePreview || options.onResourcePreview,
          });
          bindMessageAvatars(bindingRoot);
          if (options.enableLinkPreview || renderOptions.enableLinkPreview) {
            bindLinkPreview(bindingRoot, { onLinkPreview: renderOptions.onLinkPreview || options.onLinkPreview });
          }
        }
        measureMessageFolds(bindingRoots);
        revealFinishedSubagents(els.messages, options.onSubagentFinished);
        options.onMessagesRendered?.(els.messages, controller);
        if (followTail && messageViewportMeasurable()) {
          els.messages.scrollTop = els.messages.scrollHeight;
        } else if (prependOnly && messageViewportMeasurable()) {
          els.messages.scrollTop = previousScrollTop + Math.max(
            0,
            els.messages.scrollHeight - previousScrollHeight,
          );
        }
      },
      setRuntime(details, live, renderOptions = {}) {
        state.runtimeDetails = Array.isArray(details) ? details : [];
        state.runtimeLive = !!live;
        state.runtimePhase = String(renderOptions.runtimePhase ?? state.runtimePhase ?? "");
        state.messageRenderOptions = { ...state.messageRenderOptions, ...renderOptions };
        const list = els.messages?.firstElementChild;
        if (!list?.classList.contains("messageList")) {
          controller.setMessages(state.messages, {
            ...state.messageRenderOptions,
            runtimeDetails: state.runtimeDetails,
            runtimeLive: state.runtimeLive,
            runtimePhase: state.runtimePhase,
          });
          return;
        }
        const template = document.createElement("template");
        template.innerHTML = runtimeStreamHtml(
          state.runtimeDetails,
          state.runtimeLive,
          { ...options, ...state.messageRenderOptions },
        );
        const nextByKey = new Map(
          [...template.content.children].map(node => [String(node.dataset.messageKey || ""), node]),
        );
        const scrollAnchor = list.querySelector("[data-message-scroll-anchor]");
        rememberSubagentPlaces(list);
        const runtimeKeys = ["__runtime_phase__", "__subagents__", "__runtime__"];
        runtimeKeys.forEach((key, index) => {
          const current = list.querySelector(`[data-message-key="${key}"]`);
          const next = nextByKey.get(key);
          if (!next) {
            current?.remove();
            return;
          }
          if (current?.outerHTML === next.outerHTML) return;
          if (current && key === "__subagents__") {
            patchSubagentLive(current, next);
          } else if (current) {
            preserveRuntimeDisclosureState(current, next);
            current.replaceWith(next);
          } else {
            const later = runtimeKeys.slice(index + 1)
              .map(laterKey => list.querySelector(`[data-message-key="${laterKey}"]`))
              .find(Boolean);
            list.insertBefore(next, later || scrollAnchor);
          }
        });
        revealFinishedSubagents(els.messages, options.onSubagentFinished);
        // A turn that starts or ends changes which messages start expanded.
        const foldDefaults = messageFoldDefaults(state.messages, { runtimeLive: state.runtimeLive });
        if (foldDefaults.size !== state.messageFoldDefaults.size
          || [...foldDefaults].some(key => !state.messageFoldDefaults.has(key))) {
          controller.setMessages(state.messages, {
            ...state.messageRenderOptions,
            runtimeDetails: state.runtimeDetails,
            runtimeLive: state.runtimeLive,
            runtimePhase: state.runtimePhase,
          });
        }
      },
      applyUpdate(update, renderOptions = {}) {
        if (!update || typeof update !== "object") return false;
        const workspaceKey = String(update.workspace_key || "");
        const contractKey = String(state.contract?.workspace_key || "");
        if (workspaceKey && contractKey && workspaceKey !== contractKey) return false;
        const revision = String(update.revision || update.content_signature || "");
        if (revision && revision === state.updateRevision && update.force !== true) return false;
        if (update.contract && typeof update.contract === "object") {
          controller.applyContract(update.contract, renderOptions);
        } else {
          if (update.composer && typeof update.composer === "object") controller.setComposer({ ...state.composer, ...update.composer });
          const runtime = update.runtime && typeof update.runtime === "object" ? update.runtime : {};
          const incoming = Array.isArray(update.messages) ? update.messages : [];
          if (Array.isArray(update.messages) || update.reset === true || update.mode === "reset") {
            const messages = update.reset === true || update.mode === "reset"
              ? incoming
              : [...state.messages, ...incoming];
            controller.setMessages(messages, {
              ...renderOptions,
              runtimeDetails: Array.isArray(runtime.details) ? runtime.details : state.runtimeDetails,
              runtimeLive: runtime.live === undefined ? state.runtimeLive : !!runtime.live,
              runtimePhase: runtime.phase === undefined ? state.runtimePhase : String(runtime.phase || ""),
            });
          } else if (update.runtime && typeof update.runtime === "object") {
            controller.setRuntime(
              Array.isArray(runtime.details) ? runtime.details : state.runtimeDetails,
              runtime.live === undefined ? state.runtimeLive : !!runtime.live,
              {
                ...renderOptions,
                runtimePhase: runtime.phase === undefined ? state.runtimePhase : String(runtime.phase || ""),
              },
            );
          }
        }
        state.updateRevision = revision;
        return true;
      },
      applyContract(contract, renderOptions = {}) {
        if (!contract || typeof contract !== "object") return;
        state.contract = contract;
        runtimeTargetPending = false;
        if (runtimeResidentId && runtimeResidentId !== contractResidentId()) releaseResidentRuntime();
        const runtime = contract.runtime && typeof contract.runtime === "object" ? contract.runtime : {};
        const rendering = contract.rendering && typeof contract.rendering === "object" ? contract.rendering : {};
        const composerCfg = contract.composer && typeof contract.composer === "object" ? { ...contract.composer } : {};
        if (!Array.isArray(composerCfg.capabilities) && Array.isArray(contract.capabilities)) {
          composerCfg.capabilities = contract.capabilities;
        }
        const header = contract.header && typeof contract.header === "object" ? contract.header : {};
        controller.setTitle(contract.title || options.title || "Chat Workspace");
        controller.setSubtitle(
          contract.subtitle || composerCfg.meta || options.subtitle || "",
          header.subtitle_parts || [],
        );
        controller.setComposer(composerCfg);
        controller.setMessages(Array.isArray(contract.messages) ? contract.messages : [], {
          ...renderOptions,
          runtimeDetails: Array.isArray(runtime.details) ? runtime.details : [],
          runtimeLive: !!runtime.live,
          runtimePhase: String(runtime.phase || ""),
          emptyText: window.PortalI18n.t(rendering.empty_text || renderOptions.emptyText || options.emptyText || "暂无对话。"),
          userName: rendering.user_name || renderOptions.userName || options.userName,
          assistantName: rendering.assistant_name || renderOptions.assistantName || options.assistantName || "Agent",
          history: contract.history && typeof contract.history === "object" ? contract.history : {},
          inheritance: contract.inheritance && typeof contract.inheritance === "object" ? contract.inheritance : null,
          autoCollapseAgentGroups: rendering.auto_collapse_agent_groups === undefined
            ? renderOptions.autoCollapseAgentGroups
            : !!rendering.auto_collapse_agent_groups,
        });
        clearWorkspaceLoading();
        updateControls();
        options.onContractApplied?.(contract, controller);
        syncSessionRestartObservation();
      },
      releaseResidentRuntime() {
        releaseResidentRuntime(); runtimeTargetPending = true;
        const button = composer.querySelector("[data-workspace-runtime-settings-open]");
        if (button) button.disabled = true;
      },
      destroy() {
        releaseResidentRuntime();
        conversationSearch?.destroy();
        refsCatalog.disposed = true;
        closeMentionMenu();
        window.cancelAnimationFrame(mentionRenderFrame);
        mentionRenderFrame = 0;
        restartUnobserve?.();
        restartProgress.destroy();
        saveActiveDraft({ immediate: true });
        ownedAttachmentPreviews.forEach(releaseAttachmentPreview);
        window.removeEventListener("pagehide", saveDraftBeforePageLeave);
        document.removeEventListener("visibilitychange", saveDraftWhenHidden);
        refsControlBinding?.destroy();
        refsControlBinding = null;
        els.messages?.removeEventListener("scroll", handleMessageViewportScroll);
        els.messages?.removeEventListener("wheel", expectMessageScrollFromGesture);
        els.messages?.removeEventListener("touchstart", beginMessageScrollTouchGesture);
        els.messages?.removeEventListener("touchmove", beginMessageScrollTouchGesture);
        els.messages?.removeEventListener("touchend", endMessageScrollTouchGesture);
        els.messages?.removeEventListener("touchcancel", endMessageScrollTouchGesture);
        els.messages?.removeEventListener("pointerdown", handleMessageScrollPointerDown);
        els.messages?.removeEventListener("keydown", handleMessageScrollKeyDown);
        els.messages?.removeEventListener("load", measureLoadedMessageImage, true);
        messageFoldObserver?.disconnect();
        window.removeEventListener("pointerup", endMessageScrollPointerGesture);
        window.removeEventListener("pointercancel", endMessageScrollPointerGesture);
        root.removeEventListener("portal:workbench-before-layout", handleBeforeWorkbenchLayout);
        root.removeEventListener("portal:workbench-resize", handleWorkbenchResize);
        window.cancelAnimationFrame(workbenchViewportRestoreFrame);
        window.cancelAnimationFrame(workbenchViewportSettleFrame);
        window.cancelAnimationFrame(windowResizeFollowFrame);
        if (pendingWorkbenchViewport) {
          els.messages.style.overflowAnchor = pendingWorkbenchViewport.overflowAnchorStyle;
        }
        pendingWorkbenchViewport = null;
        window.removeEventListener("resize", handleWindowResize);
        if (root.__portalChatWorkspaceController === controller) {
          delete root.__portalChatWorkspaceController;
        }
        runtimeSettingsDialog?.destroy?.();
        runtimeSettingsDialog = null;
        sessionRestartDialog?.destroy?.();
        sessionRestartDialog = null;
      },
    };
    const saveWorkspaceRuntimeSettings = async runtimeSettings => {
      const action = state.contract?.actions?.runtime_settings;
      let result;
      if (typeof options.onRuntimeSettingsSave === "function") {
        result = await options.onRuntimeSettingsSave(runtimeSettings, controller, action);
      } else {
        result = await requestWorkspaceAction(state.contract, "runtime_settings", {
          changes: runtimeSettings,
        });
      }
      const contract = result?.chat_workspace || result?.contract;
      if (contract && typeof contract === "object") {
        controller.applyContract(contract, state.messageRenderOptions || {});
      } else {
        controller.setComposer({
          ...state.composer,
          runtime_settings: { ...state.composer.runtime_settings, ...runtimeSettings },
        });
      }
      notifyRuntimeSettingsSaved(result);
      options.onRuntimeSettingsSaved?.(result, controller);
      return result;
    };
    const openWorkspaceRuntimeSettings = async () => {
      if (runtimeTargetPending) return;
      const resident = residentRuntimeController();
      if (resident) {
        try { await resident.openSettings(() => composer.querySelector("[data-workspace-runtime-settings-open]")); }
        catch (error) { window.PortalUI.toast(error.message, "error"); }
        return;
      }
      const workspaceCatalogKey = runtimeCatalogUrl();
      runtimeSettingsDialog ||= createRuntimeSettingsDialog({ id: `${id}RuntimeSettings` });
      const suppliedOptions = typeof options.runtimeSettingsDialogOptions === "function"
        ? await options.runtimeSettingsDialogOptions(controller)
        : (options.runtimeSettingsDialogOptions || {});
      const dialogOptions = {
        backend: state.composer.runtime_backend, ...suppliedOptions,
        residentId: contractResidentId() || state.contract?.actions?.runtime_settings?.payload?.resident_id,
        accountId: state.composer.runtime_account_id,
      };
      if (!dialogOptions.catalog) {
        try {
          dialogOptions.catalog = await loadWorkspaceRuntimeCatalog({ ...state.composer, runtime_backend: dialogOptions.backend }, true).promise;
        } catch (error) {
          window.PortalUI?.toast?.(`无法读取当前 Provider 能力，未打开设置：${error.message}`, "error");
          return;
        }
      }
      if (workspaceCatalogKey !== runtimeCatalogUrl()) return;
      runtimeSettingsDialog.open(
        state.composer.runtime_settings || {},
        saveWorkspaceRuntimeSettings,
        dialogOptions,
      );
    };
    function sessionRestartAvailable() {
      if (runtimeTargetPending) return false;
      return (typeof options.onSessionRestartOptions === "function" && !!controller.target?.()?.resident_id)
        || !!state.contract?.actions?.session_restart?.instruction
        || !!state.contract?.actions?.session_restart?.url;
    }
    const openWorkspaceSessionRestart = async () => {
      if (runtimeTargetPending) return;
      const resident = residentRuntimeController();
      if (resident) {
        try { await resident.openRestart(() => composer.querySelector("[data-workspace-runtime-settings-open]")); }
        catch (error) { window.PortalUI.toast(error.message, "error"); }
        return;
      }
      const action = state.contract?.actions?.session_restart;
      const config = typeof options.onSessionRestartOptions === "function"
        ? await options.onSessionRestartOptions(controller, action)
        : await window.PortalAPI.get(String(action?.options_url || ""));
      sessionRestartDialog ||= createSessionRestartDialog({ id: `${id}SessionRestart` });
      sessionRestartDialog.open(
        config,
        async values => {
          const result = typeof options.onSessionRestart === "function"
              ? await options.onSessionRestart(values, controller, action)
              : await requestWorkspaceAction(state.contract, "session_restart", values);
          if (!["accepted", "completed"].includes(result?.status)) throw new Error("Session 切换结果尚未确认，请刷新查看状态");
          restartService?.rememberSessionRestart(config.resident_id, result);
          window.PortalUI.toast(result.status === "accepted" ? window.PortalI18n.t("迁移请求已受理，可在聊天顶部持续查看进度。") : result.configuration_only
            ? window.PortalI18n.t("Provider / 账号设置已保存；尚未创建 Session。")
            : window.PortalI18n.t("Session 已切换，旧 session 已保留。"), "success");
          if (result.status === "completed") {
            options.onSessionRestarted?.(result, controller);
            await options.onRefresh?.(controller);
          }
          return result;
        },
        () => options.onRefresh?.(controller),
        values => {
          if (typeof options.onSessionRestartCancel !== "function") {
            throw new Error("当前 Workspace 未提供 Session 迁移取消入口");
          }
          return options.onSessionRestartCancel(values, controller, action);
        },
      );
    };
    const bindComposer = () => {
      bindWorkspaceComposer({
        input: els.input,
        sendButton: els.send,
        stopButton: els.stop,
        attachButton: els.attach,
        fileInput: els.fileInput,
        historyScope,
        history,
        canSend: canSubmit,
        onSend: () => {
          const payload = controller.payload();
          options.onSend?.(payload, controller);
        },
        onStop: () => options.onStop?.(controller),
        onFiles: files => addFiles(files).catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || error, "error")),
        onMoveAttachment: (token, event) => {
          const moved = moveComposerAttachmentRef(els.input, token, event);
          if (moved) {
            reconcileAttachmentsFromInput({ notify: true });
            options.onInput?.(controller.payload(), controller);
          }
          return moved;
        },
        onBeforeManualTextPaste: () => pushComposerUndoSnapshot(),
        onCancelManualTextPaste: frame => dropComposerUndoSnapshot(frame),
        onAfterManualTextPaste: frame => markComposerUndoAfterSnapshot(frame),
        onUndo: () => undoComposerMutation(),
        onBeforeKeydown: event => handleSlashCommandKey(event) || handleMentionKey(event),
        onInputActivityChange: markComposerInputActivity,
        onInput: (_event, meta = {}) => {
          state.recoveredSubmission = null;
          const recalled = meta?.source === "history";
          if (!recalled) history.reset(historyScope());
          reconcileAttachmentsFromInput({ notify: true });
          if (recalled) {
            // A recalled input is not being typed: its "/" or "@" opens no
            // menu, so the next Arrow key goes on through the history.
            closeSlashCommandMenu();
            closeMentionMenu();
          } else {
            refreshSlashCommandMenu();
            refreshMentionMenu();
          }
          updateControls();
          saveActiveDraft();
          options.onInput?.(controller.payload(), controller);
        },
      });
      bindComposerAttachmentActions(els.composer, {
        onRetry: id => uploadComposerAttachment(id).catch(error => options.onError ? options.onError(error) : window.PortalUI.toast(error.message || error, "error")),
        onRemove: id => {
          discardComposerAttachments(state.attachments.filter(item => item.id === id));
          state.attachments = state.attachments.filter(item => item.id !== id);
          removeComposerAttachmentRef(els.input, id);
          renderAttachments({ notify: true });
          options.onInput?.(controller.payload(), controller);
        },
      });
      controller.refreshRefs();
      bindWorkspaceQueue(els.composer, {
        onInject: id => options.onQueueInject?.(id, controller),
        onDelete: id => options.onQueueDelete?.(id, controller),
        onClear: () => options.onQueueClear?.(controller),
        onMergeAll: () => options.onQueueMergeAll?.(controller),
        onMailbox: () => options.onQueueMailbox?.(controller),
      });
      if (els.mentions && els.mentions.dataset.workspaceMentionsBound !== "1") {
        els.mentions.dataset.workspaceMentionsBound = "1";
        // Choosing keeps the caret in the input.
        els.mentions.addEventListener("mousedown", event => {
          if (event.target.closest("[data-portal-mention-index]")) event.preventDefault();
        });
        els.mentions.addEventListener("click", event => {
          const button = event.target.closest("[data-portal-mention-index]");
          if (button) chooseMention(Number(button.dataset.portalMentionIndex));
        });
      }
      if (els.input && typeof options.mentionCandidates === "function" && els.input.dataset.workspaceMentionBlurBound !== "1") {
        els.input.dataset.workspaceMentionBlurBound = "1";
        // Copying a labelled mention must preserve its target when pasted elsewhere.
        const copyMentions = event => {
          const selection = window.getSelection();
          if (!event.clipboardData || !selection?.rangeCount || selection.isCollapsed) return;
          const range = selection.getRangeAt(0);
          if (!els.input.contains(range.commonAncestorContainer)) return;
          const fragment = document.createElement("div");
          fragment.append(range.cloneContents());
          if (!fragment.querySelector("[data-mention-value]")) return;
          event.preventDefault();
          event.clipboardData.setData("text/plain", serializeComposerInput(fragment, state.attachments, { mode: "draft" }));
          if (event.type === "cut") {
            range.deleteContents();
            els.input.dispatchEvent(new Event("input", { bubbles: true }));
          }
        };
        els.input.addEventListener("copy", copyMentions);
        els.input.addEventListener("cut", copyMentions);
        els.input.addEventListener("blur", () => closeMentionMenu());
        els.input.addEventListener("compositionend", () => {
          refreshMentionMenu();
          scheduleMentionRender();
        });
      }
      if (els.slashCommands && els.slashCommands.dataset.workspaceSlashCommandsBound !== "1") {
        els.slashCommands.dataset.workspaceSlashCommandsBound = "1";
        els.slashCommands.addEventListener("mousedown", event => {
          if (event.target.closest("[data-portal-slash-command]")) event.preventDefault();
        });
        els.slashCommands.addEventListener("click", event => {
          const button = event.target.closest("[data-portal-slash-command]");
          if (!button) return;
          chooseSlashCommand(button.dataset.portalSlashCommand || "");
        });
      }
      const runtimeSettingsButton = els.composer.querySelector("[data-workspace-runtime-settings-open]");
      if (runtimeSettingsButton && runtimeSettingsButton.dataset.workspaceRuntimeSettingsBound !== "1") {
        runtimeSettingsButton.dataset.workspaceRuntimeSettingsBound = "1";
        runtimeSettingsButton.addEventListener("click", openWorkspaceRuntimeSettings);
        const view = residentRuntimeController()?.state();
        if (runtimeTargetPending) runtimeSettingsButton.disabled = true;
        else if (view) runtimeSettingsButton.disabled = view.loading || view.busy || sessionRestartActive(view.restart) || (!view.canEdit && !view.canRestart);
      }
      if (els.auto && els.auto.dataset.workspaceAutoBound !== "1") {
        els.auto.dataset.workspaceAutoBound = "1";
        els.auto.addEventListener("click", () => options.onAuto?.(controller));
      }
    };
    bindComposerSplitter();
    applyComposerInputLayout();
    conversationSearch = createConversationSearch({
      root, scope: historyScope, messages: () => state.messages,
      rendering: () => ({ ...options, ...state.messageRenderOptions }),
      search: options.onSearchMessages,
      canLoadOlder: () => !!options.onLoadOlder && !!state.messageRenderOptions?.history?.can_load_older,
      loadOlder: () => options.onLoadOlder?.(controller),
      reveal: key => controller.revealMessage(key),
    });
    if (els.refresh) els.refresh.addEventListener("click", event => {
      event.preventDefault();
      if (sessionRestartAvailable()) {
        openWorkspaceSessionRestart().catch(error => (
          options.onError
            ? options.onError(error)
            : window.PortalUI.toast(error.message || String(error), "error")
        ));
        return;
      }
      options.onRefresh?.(controller);
    });
    // The Fork card shows its opening compaction on request.
    els.messages?.addEventListener("click", event => {
      if (!event.target.closest("[data-chat-inheritance-summary]")) return;
      event.preventDefault();
      toggleInheritedSummary().catch(error => options.onError?.(error));
    });
    // A quote block goes to the quoted message; the quote bar's cancel drops the quote.
    els.messages?.addEventListener("click", event => {
      const quote = event.target.closest("[data-message-quote]");
      if (!quote) return;
      event.preventDefault();
      controller.revealMessage(quote.dataset.messageQuote).catch(error => options.onError?.(error));
    });
    root.addEventListener("click", event => {
      if (!event.target.closest("[data-composer-quote-cancel]")) return;
      event.preventDefault();
      controller.clearQuote();
      els.input?.focus?.();
    });
    els.messages?.addEventListener("click", event => {
      const button = event.target.closest("[data-workspace-load-older]");
      if (!button || !options.onLoadOlder || button.disabled) return;
      button.disabled = true;
      button.textContent = window.PortalI18n.t("正在加载…");
      Promise.resolve(options.onLoadOlder(controller)).catch(error => {
        if (options.onError) options.onError(error);
        else window.PortalUI?.toast?.(error?.message || String(error), "error");
      }).finally(() => {
        if (!button.isConnected) return;
        button.disabled = false;
        button.textContent = window.PortalI18n.t("加载更早消息");
      });
    });
    updateControls();
    root.__portalChatWorkspaceController = controller;
    if (options.contract && typeof options.contract === "object") {
      controller.applyContract(options.contract, options.renderOptions || {});
    }
    window.addEventListener("pagehide", saveDraftBeforePageLeave);
    document.addEventListener("visibilitychange", saveDraftWhenHidden);
    return controller;
  }

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
    setQuote(quote) { return this.controller?.setQuote?.(quote) || false; }
    clearQuote(options) { return this.controller?.clearQuote?.(options) || false; }
    revealMessage(messageId) { return this.controller?.revealMessage?.(messageId) || Promise.resolve(false); }
    clear(options) { return this.controller?.clear?.(options); }
    inputText() { return this.controller?.inputText?.() || ""; }
    draftText() { return this.controller?.draftText?.() || ""; }
    setInputText(value) { return this.controller?.setInputText?.(value); }
    setDraftText(value, attachments) { return this.controller?.setDraftText?.(value, attachments); }
    draftScope() { return this.controller?.draftScope?.() || ""; }
    suspendDraftPersistence() { return this.controller?.suspendDraftPersistence?.() || false; }
    resumeDraftPersistence() { return this.controller?.resumeDraftPersistence?.() || false; }
    draftPersistenceSuspended() { return this.controller?.draftPersistenceSuspended?.() || false; }
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

  function normalizeResidentTarget(value = {}) {
    const target = value && typeof value === "object" ? value : {};
    const residentId = String(target.resident_id || "").trim();
    return Object.freeze({
      resident_id: residentId,
      lineage_id: String(target.lineage_id || "").trim(),
      lineage_resident_id: String(target.lineage_resident_id || "").trim(),
      title: String(target.title || target.name || "").trim(),
      backend: String(target.backend || "").trim(),
    });
  }

  function residentSessionScope(value = {}) {
    const target = normalizeResidentTarget(value);
    return target.resident_id ? `resident-session:${target.resident_id}` : "resident-session:none";
  }

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

  function turnArtifactDisplay(part) {
    const hasPlan = !!(String(part.plan_text || "").trim() || String(part.explanation || "").trim() || part.plan?.length);
    const hasChanges = !!(String(part.diff || "").trim() || part.changes?.length);
    return {
      hasContent: hasPlan || hasChanges,
      title: hasPlan ? (hasChanges ? window.PortalI18n.t("本轮计划与修改") : window.PortalI18n.t("本轮计划")) : (hasChanges ? window.PortalI18n.t("本轮修改") : window.PortalI18n.t("本轮未记录到可展示的计划或文件差异")),
    };
  }

  // A subagent the Resident's model started: its own steps stay out of the
  // Resident's context; the Chat shows the run once the model has its result.
  // The built-in kinds of the Providers (Claude Code subagent_type, Codex
  // agent role, Kimi Code subagent name) by what they do; any other kind
  // shows as its Provider named it.
  const SUBAGENT_TYPE_LABELS = {
    "general-purpose": window.PortalI18n.t("通用"), default: window.PortalI18n.t("通用"),
    explore: window.PortalI18n.t("探索"), explorer: window.PortalI18n.t("探索"),
    plan: window.PortalI18n.t("规划"),
    coder: window.PortalI18n.t("编码"), worker: window.PortalI18n.t("执行"),
  };

  function subagentTypeLabel(type) {
    const raw = String(type || "").trim();
    return SUBAGENT_TYPE_LABELS[raw.toLowerCase()] || raw;
  }

  function subagentRunTitle(part) {
    const kind = subagentTypeLabel(part.agent_type);
    return window.PortalI18n.text`子 agent${kind ? ` · ${kind}` : ""}${part.description ? `：${part.description}` : ""}`;
  }

  function subagentRunDuration(part) {
    const seconds = Math.round((Date.parse(String(part.finished_at || "")) - Date.parse(String(part.started_at || ""))) / 1000);
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    return seconds < 60 ? `用时 ${seconds} 秒` : `用时 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  }

  // The tools a run used, most used first: "Bash ×3 · Read".
  function subagentRunTools(steps) {
    const counts = new Map();
    steps.filter(step => step.kind === "tool").forEach(step => {
      const name = String(step.tool_name || "tool");
      counts.set(name, (counts.get(name) || 0) + 1);
    });
    return [...counts].sort((a, b) => b[1] - a[1])
      .map(([name, count]) => window.PortalI18n.html`<code>${escapeHtml(name)}${count > 1 ? ` ×${count}` : ""}</code>`).join("");
  }

  // The folded card shows the result's opening words as plain text.
  function subagentRunPreview(result) {
    return String(result || "")
      .replace(/```[\s\S]*?(```|$)/g, " ")
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/^\s*(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
      .replace(/`|\*\*|__|~~/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 300);
  }

  function subagentRunCard(item, part, context) {
    const status = { completed: "已完成", running: "进行中", failed: "失败", cancelled: "已取消", stopped: "已停止", killed: "已停止" }[part.status] || String(part.status || "");
    const steps = Array.isArray(part.steps) ? part.steps : [];
    const total = Number(part.steps_total || steps.length);
    const stepHtml = steps.map(step => step.kind === "tool"
      ? window.PortalI18n.html`<li data-step-kind="tool"${step.error ? ' data-step-error="1"' : ""}><code>${escapeHtml(step.tool_name || "tool")}</code> ${escapeHtml(step.text || "")}</li>`
      : window.PortalI18n.html`<li data-step-kind="message">${escapeHtml(step.text || "")}</li>`).join("");
    const meta = [subagentRunDuration(part), total ? `${total} 步` : "", subagentTokens(part.usage)].filter(Boolean).join(" · ");
    const tools = subagentRunTools(steps);
    // Folded by default: one line of the result and the tools it used.
    const fold = escapeHtml(part.tool_use_id || item.id);
    const preview = part.result ? subagentRunPreview(part.result) : "子 agent 没有返回文字结果";
    const files = subagentFilesHtml(part.files);
    const prompt = String(part.prompt || "").trim();
    return window.PortalI18n.html`<section class="residentQuestionCard nativeTurnCard chatSystemCard subagentRunCard" data-subagent-run="${escapeHtml(item.id)}" data-subagent-tool-use="${escapeHtml(part.tool_use_id || "")}" data-status="${escapeHtml(part.status || "")}">
      <header class="nativeTurnHeader"><div class="nativeTurnIdentity"><div class="residentQuestionTitle"><span class="subagentRunMark" aria-hidden="true"></span>${escapeHtml(subagentRunTitle(part))}</div><span class="messageSystemBadge">${escapeHtml(part.background ? window.PortalI18n.t("后台子 agent") : window.PortalI18n.t("子 agent"))}${status ? ` · ${escapeHtml(status)}` : ""}</span></div>${meta ? window.PortalI18n.html`<span class="subagentRunMeta">${escapeHtml(meta)}</span>` : ""}</header>
      <details class="subagentRunBody" data-message-disclosure="subagent-result:${fold}">
        <summary><span class="subagentRunFold" aria-hidden="true"></span><span class="subagentRunPreview">${escapeHtml(preview)}</span><span class="subagentRunLabel">结果</span>${tools ? window.PortalI18n.html`<span class="subagentRunTools">${tools}</span>` : ""}<span class="subagentRunHint"><span class="subagentRunExpand">展开</span><span class="subagentRunCollapse">收起</span></span></summary>
        <div class="subagentRunResult">${part.result ? context.renderContent(part.result) : window.PortalI18n.html`<small class="nativeTurnEmpty">子 agent 没有返回文字结果</small>`}
        ${part.result_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">结果较长，这里只显示开头；复制或保存时取完整结果</small>` : ""}</div>
        ${files ? window.PortalI18n.html`<div class="subagentRunSection"><div class="subagentRunSectionLabel">改动的文件</div>${files}</div>` : ""}
        ${prompt ? window.PortalI18n.html`<details class="subagentRunPromptFold" data-message-disclosure="subagent-prompt:${fold}"><summary>它收到的任务</summary><div class="subagentRunPrompt">${escapeHtml(prompt)}</div>${part.prompt_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">任务较长，这里只显示开头</small>` : ""}</details>` : ""}
        ${steps.length ? window.PortalI18n.html`<details class="subagentRunStepsFold" data-message-disclosure="subagent-steps:${fold}"><summary>查看子 agent 的步骤（${total} 步${total > steps.length ? window.PortalI18n.text`，显示最后 ${steps.length} 步` : ""}）</summary><ol class="subagentRunSteps">${stepHtml}</ol></details>` : ""}
        <div class="subagentRunActions">
          <button type="button" class="portalButton is-compact" data-subagent-action="detail" title="打开这个子 agent 的完整步骤：每次工具调用的输入和输出">全部步骤</button>
          <button type="button" class="portalButton is-compact" data-subagent-action="quote" title="把结果摘录放进输入框，接着写要父 Resident 做什么">引用到输入框</button>
          <button type="button" class="portalButton is-compact" data-subagent-action="copy">复制结果</button>
          <button type="button" class="portalButton is-compact" data-subagent-action="save" title="把任务、结果和改动的文件保存为 Resident 自己 Workspace 中的 Markdown 文件">保存为文件</button>
        </div>
      </details>
      </section>`;
  }

  // While the model waits on a subagent, its live card shows among the turn's
  // activity: what it does now, its last steps and how long it has run.  The
  // finished card replaces it in the conversation once the model has the result.
  const SUBAGENT_LIVE_TRAIL = 4;
  let subagentClockTimer = 0;

  function subagentClockText(since) {
    const started = Date.parse(String(since || ""));
    if (!Number.isFinite(started)) return "";
    const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function startSubagentClocks() {
    if (!subagentClockTimer) subagentClockTimer = window.setInterval(tickSubagentClocks, 1000);
  }

  function tickSubagentClocks() {
    const clocks = document.querySelectorAll("[data-subagent-since]");
    if (!clocks.length) {
      window.clearInterval(subagentClockTimer);
      subagentClockTimer = 0;
      return;
    }
    clocks.forEach(clock => {
      const text = subagentClockText(clock.dataset.subagentSince);
      if (clock.textContent !== text) clock.textContent = text;
    });
  }

  function subagentLiveRuns(details) {
    return (Array.isArray(details) ? details : [])
      .filter(detail => detail?.kind === "subagent" && runtimeStatusLabel(detail.status) === "running"
        && detail.subagent && typeof detail.subagent === "object" && detail.subagent.tool_use_id)
      .map(detail => ({ ...detail.subagent, execution_id: String(detail.execution_id || "") }))
      .sort((a, b) => String(a.started_at || "").localeCompare(String(b.started_at || "")));
  }

  // Each part carries the data it shows (data-sig): an update replaces only
  // the parts that changed, so a card keeps its running animations.
  function subagentLivePart(name, data, tag, className, html) {
    return `<${tag} class="${className}" data-subagent-part="${name}" data-sig="${escapeHtml(JSON.stringify(data))}">${html}</${tag}>`;
  }

  function subagentLiveStepText(step) {
    return step.kind === "tool"
      ? window.PortalI18n.html`<code>${escapeHtml(step.tool_name || "tool")}</code><span class="subagentLiveText">${escapeHtml(step.text || "")}</span>`
      : window.PortalI18n.html`<span class="subagentLiveSay">说</span><span class="subagentLiveText">${escapeHtml(step.text || "")}</span>`;
  }

  function subagentLiveCardHtml(run) {
    const steps = Array.isArray(run.steps) ? run.steps : [];
    const now = steps.length ? steps[steps.length - 1] : null;
    const trail = steps.slice(Math.max(0, steps.length - 1 - SUBAGENT_LIVE_TRAIL), Math.max(0, steps.length - 1));
    const total = Number(run.steps_total || steps.length);
    const started = Date.parse(String(run.started_at || ""));
    const age = Number.isFinite(started) ? Math.max(0, (Date.now() - started) / 1000) : 0;
    const head = subagentLivePart("head", [run.agent_type, run.description, run.background, run.started_at], "header", "subagentLiveHead", window.PortalI18n.html`
        <span class="subagentLiveSpinner" aria-hidden="true"></span>
        <div class="subagentLiveIdentity">
          <div class="subagentLiveType" title="${escapeHtml(run.agent_type || "")}">${escapeHtml(subagentTypeLabel(run.agent_type) || window.PortalI18n.t("子 agent"))}<span class="subagentLiveMode">${run.background ? window.PortalI18n.t("后台") : window.PortalI18n.t("前台")}</span></div>
          <div class="subagentLiveTask" title="${escapeHtml(run.description || "")}">${escapeHtml(run.description || "")}</div>
        </div>
        <span class="subagentLiveClock" data-subagent-since="${escapeHtml(run.started_at || "")}">${escapeHtml(subagentClockText(run.started_at))}</span>`);
    const nowHtml = now
      ? `${now.kind === "tool" && !now.running ? window.PortalI18n.html`<span class="subagentLiveTick" data-step-error="${now.error ? "1" : ""}" aria-hidden="true"></span>` : ""}${subagentLiveStepText(now)}`
      : window.PortalI18n.html`<span class="subagentLiveText">正在启动…</span>`;
    // A call still running shows how long it has taken so far.
    const stepClock = now?.kind === "tool" && now.running && now.started_at
      ? window.PortalI18n.html`<span class="subagentLiveStepClock" data-subagent-since="${escapeHtml(now.started_at)}" title="这一步已用时">${escapeHtml(subagentClockText(now.started_at))}</span>`
      : "";
    const nowPart = subagentLivePart("now", now, "div", "subagentLiveNow", window.PortalI18n.html`${nowHtml}${stepClock}<span class="subagentLiveDots" aria-hidden="true"><i></i><i></i><i></i></span>`);
    const trailPart = subagentLivePart("trail", trail, "ol", "subagentLiveTrail", trail.map(step => window.PortalI18n.html`<li data-step-kind="${escapeHtml(step.kind || "")}"><span class="subagentLiveTick" data-step-error="${step.error ? "1" : ""}" aria-hidden="true"></span>${subagentLiveStepText(step)}</li>`).join(""));
    const tokens = subagentTokens(run.usage);
    const fileCount = Array.isArray(run.files) ? run.files.length : 0;
    const foot = subagentLivePart("foot", [total, run.model, tokens, fileCount], "footer", "subagentLiveFoot",
      window.PortalI18n.html`<span>${total ? window.PortalI18n.text`第 ${total} 步` : window.PortalI18n.t("等待第一步")}${tokens ? ` · ${escapeHtml(tokens)}` : ""}${fileCount ? window.PortalI18n.text` · 改了 ${fileCount} 个文件` : ""}</span>${run.model ? window.PortalI18n.html`<span>${escapeHtml(run.model)}</span>` : ""}`);
    // Stop shows where the Provider stops one run and the reader may stop the
    // turn, steering where the reader may send (the Chat root says which).
    const actions = subagentLivePart("actions", 0, "div", "subagentLiveActions", window.PortalI18n.html`<button type="button" class="subagentLiveAction" data-subagent-action="detail" title="打开这个子 agent 的任务、完整步骤和用量">详情</button><button type="button" class="subagentLiveAction" data-subagent-action="steer" title="在输入框写给父 Resident 的纠偏，发送后立即进入当前这一轮">纠偏</button><button type="button" class="subagentLiveAction" data-subagent-action="stop" title="只停止这个子 agent，父 Resident 的这一轮继续">停止</button>`);
    return window.PortalI18n.html`<section class="subagentLiveCard" data-subagent-live="${escapeHtml(run.tool_use_id)}"${age < 2 ? " data-fresh" : ""} style="--subagent-age: ${age.toFixed(2)}" aria-label="${escapeHtml(window.PortalI18n.text`子 agent${subagentTypeLabel(run.agent_type) ? ` ${subagentTypeLabel(run.agent_type)}` : ""} 正在运行：${run.description || ""}`)}">${head}${nowPart}${trailPart}${foot}${actions}</section>`;
  }

  function subagentLiveHtml(details) {
    const runs = subagentLiveRuns(details);
    if (!runs.length) return "";
    runs.forEach(run => subagentLiveById.set(run.tool_use_id, run));
    startSubagentClocks();
    const head = runs.length > 1
      ? subagentLivePart("group", runs.length, "div", "subagentLiveGroupHead", window.PortalI18n.html`<span class="subagentLivePulse" aria-hidden="true"></span>${runs.length} 个子 agent 正在并行工作`)
      : "";
    return window.PortalI18n.html`<div class="subagentLiveGroup" data-message-key="__subagents__" data-count="${runs.length}" role="status" aria-live="polite">${head}<div class="subagentLiveGrid">${runs.map(subagentLiveCardHtml).join("")}</div></div>`;
  }

  function patchSubagentLive(current, next) {
    current.dataset.count = next.dataset.count;
    const currentHead = current.querySelector(":scope > .subagentLiveGroupHead");
    const nextHead = next.querySelector(":scope > .subagentLiveGroupHead");
    if (!nextHead) currentHead?.remove();
    else if (!currentHead) current.prepend(nextHead);
    else if (currentHead.dataset.sig !== nextHead.dataset.sig) currentHead.replaceWith(nextHead);
    const grid = current.querySelector(".subagentLiveGrid");
    const cards = [...next.querySelectorAll(".subagentLiveCard")];
    const keep = new Set(cards.map(card => card.dataset.subagentLive));
    [...grid.children].forEach(card => { if (!keep.has(card.dataset.subagentLive)) card.remove(); });
    cards.forEach((card, index) => {
      const existing = [...grid.children].find(node => node.dataset.subagentLive === card.dataset.subagentLive);
      if (!existing) {
        grid.insertBefore(card, grid.children[index] || null);
        return;
      }
      for (const part of card.querySelectorAll("[data-subagent-part]")) {
        const old = existing.querySelector(`[data-subagent-part="${part.dataset.subagentPart}"]`);
        if (old && old.dataset.sig !== part.dataset.sig) old.replaceWith(part);
      }
      if (grid.children[index] !== existing) grid.insertBefore(existing, grid.children[index] || null);
    });
  }

  // Running subagents by their card's tool_use_id, as the Chat last drew
  // them: the detail dialog follows a run here, and its Stop names the run's
  // Execution from here.
  const subagentLiveById = new Map();
  // Where each running card last stood: the run's result card arrives from
  // there when it ends (decision_b1ab45c4b3ec).
  const subagentLivePlaces = new Map();

  function subagentTokens(usage) {
    const total = Number(usage?.total_tokens || 0) || Number(usage?.input_tokens || 0) + Number(usage?.output_tokens || 0);
    if (!(total > 0)) return "";
    return total >= 1000 ? `${(total / 1000).toFixed(total >= 100000 ? 0 : 1)}k tokens` : `${total} tokens`;
  }

  function subagentFileName(path) {
    return String(path || "").split("/").filter(Boolean).pop() || String(path || "");
  }

  // The files a run changed; each opens in the Workspace preview.
  function subagentFilesHtml(files) {
    const list = (Array.isArray(files) ? files : []).filter(file => String(file?.path || "").trim());
    if (!list.length) return "";
    return window.PortalI18n.html`<div class="subagentFiles">${list.map(file => window.PortalI18n.html`<button type="button" class="subagentFileChip" data-portal-source-path="${escapeHtml(file.path)}" title="${escapeHtml(window.PortalI18n.text`${file.path}（${(file.tools || []).join("、")}）`)}"><span>${escapeHtml(subagentFileName(file.path))}</span>${Number(file.count) > 1 ? `<small>×${Number(file.count)}</small>` : ""}</button>`).join("")}</div>`;
  }

  function rememberSubagentPlaces(root) {
    root?.querySelectorAll?.(".subagentLiveCard[data-subagent-live]").forEach(card => {
      const rect = card.getBoundingClientRect();
      if (rect.width && rect.height) subagentLivePlaces.set(card.dataset.subagentLive, { rect, at: Date.now() });
    });
  }

  // A run this Chat showed running has ended once its result card is drawn
  // and its running card is gone: the result card moves in from where the
  // running card stood and lights up once.
  function revealFinishedSubagents(root, onFinished) {
    if (!root || !subagentLivePlaces.size) return;
    const latest = new Map();
    root.querySelectorAll(".subagentRunCard[data-subagent-tool-use]").forEach(card => latest.set(card.dataset.subagentToolUse, card));
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    latest.forEach((card, id) => {
      const place = subagentLivePlaces.get(id);
      if (!id || !place || root.querySelector(`[data-subagent-live="${CSS.escape(id)}"]`)) return;
      subagentLivePlaces.delete(id);
      const run = subagentLiveById.get(id) || {};
      subagentLiveById.delete(id);
      const to = card.getBoundingClientRect();
      const dx = place.rect.left - to.left;
      const dy = place.rect.top - to.top;
      if (!reduced && typeof card.animate === "function" && Date.now() - place.at < 5000 && Math.abs(dy) < window.innerHeight) {
        card.animate([
          { transform: `translate(${dx}px, ${dy}px)`, opacity: 0.35 },
          { transform: "none", opacity: 1 },
        ], { duration: 420, easing: "cubic-bezier(.2, .8, .2, 1)" });
      }
      card.setAttribute("data-just-finished", "");
      window.setTimeout(() => card.removeAttribute("data-just-finished"), 2600);
      onFinished?.({ ...run, status: card.dataset.status || run.status });
    });
  }

  // While the page is hidden, its title counts the subagents that ended.
  let subagentTitleCount = 0;
  let subagentTitleBase = "";
  let subagentTitleShown = "";

  function badgeTitleForSubagent() {
    if (!subagentTitleCount) {
      subagentTitleBase = document.title;
      const restore = () => {
        if (document.hidden) return;
        document.removeEventListener("visibilitychange", restore);
        if (document.title === subagentTitleShown) document.title = subagentTitleBase;
        subagentTitleCount = 0;
      };
      document.addEventListener("visibilitychange", restore);
    }
    subagentTitleCount += 1;
    subagentTitleShown = window.PortalI18n.text`✓ ${subagentTitleCount} 个子 agent 已结束 · ${subagentTitleBase}`;
    document.title = subagentTitleShown;
  }

  const SUBAGENT_STATUS_LABELS = { completed: "已完成", running: "运行中", failed: "失败", cancelled: "已取消", stopped: "已停止", killed: "已停止" };
  const SUBAGENT_RUNNING_STEPS = new Set(["running", "inProgress", "in_progress"]);
  const SUBAGENT_DETAIL_POLL_MS = 2500;
  const SUBAGENT_TRACE_PAGE = 500;

  function subagentStatusLabel(status) {
    const label = SUBAGENT_STATUS_LABELS[String(status || "")];
    return label ? window.PortalI18n.t(label) : String(status || "");
  }

  function subagentStepDuration(step) {
    const ms = Date.parse(String(step.finished_at || "")) - Date.parse(String(step.started_at || ""));
    if (!Number.isFinite(ms) || ms < 0) return "";
    if (ms < 1000) return `${ms} ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
    return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  }

  function subagentStepInput(input) {
    if (input === null || input === undefined || input === "") return "";
    return typeof input === "string" ? input : JSON.stringify(input, null, 2);
  }

  function subagentStepSummary(step) {
    const input = step.input;
    const value = input && typeof input === "object"
      ? ["command", "file_path", "pattern", "path", "url", "query", "description", "prompt"]
        .map(key => input[key]).find(item => typeof item === "string" && item.trim()) || ""
      : String(input || step.text || "");
    return String(value).replace(/\s+/g, " ").trim().slice(0, 240);
  }

  function subagentMarkdown(text) {
    return window.MD?.render?.(String(text || "")) || escapeHtml(String(text || "")).replace(/\n/g, "<br>");
  }

  // One step of the timeline: a call opens to its whole input and output,
  // a failed one is red and open, what the subagent said reads in full.
  function subagentTimelineStepHtml(step) {
    const index = Number(step.index ?? 0);
    const kind = String(step.kind || "");
    const linkified = value => linkifyResourceAddresses(escapeHtml(String(value || "")));
    if (kind === "tool") {
      const running = SUBAGENT_RUNNING_STEPS.has(String(step.status || ""));
      const input = subagentStepInput(step.input);
      const when = running && step.started_at
        ? `<span class="subagentStepClock" data-subagent-since="${escapeHtml(step.started_at)}">${escapeHtml(subagentClockText(step.started_at))}</span>`
        : escapeHtml(subagentStepDuration(step));
      const files = subagentFilesHtml((step.files || []).map(path => ({ path, tools: [step.tool_name], count: 1 })));
      return window.PortalI18n.html`<li class="subagentStep" data-step-index="${index}" data-step-kind="tool"${step.error ? ' data-step-error="1"' : ""}${running ? " data-step-running" : ""}><details${step.error ? " open" : ""}>
        <summary><span class="subagentStepMark" aria-hidden="true"></span><code>${escapeHtml(step.tool_name || "tool")}</code><span class="subagentStepText">${escapeHtml(subagentStepSummary(step))}</span><span class="subagentStepWhen">${when}</span></summary>
        <div class="subagentStepBody">${files}
          ${input ? window.PortalI18n.html`<div class="subagentStepLabel">输入</div><pre class="subagentStepPre">${linkified(input)}</pre>` : ""}
          ${step.error_text ? window.PortalI18n.html`<div class="subagentStepLabel">错误</div><pre class="subagentStepPre" data-step-error="1">${linkified(step.error_text)}</pre>` : ""}
          ${step.output ? window.PortalI18n.html`<div class="subagentStepLabel">输出${step.output_truncated ? window.PortalI18n.t("（较长，只显示开头）") : ""}</div><pre class="subagentStepPre">${linkified(step.output)}</pre>` : running ? window.PortalI18n.html`<small class="nativeTurnEmpty">正在运行…</small>` : ""}
        </div></details></li>`;
    }
    const label = kind === "input" ? window.PortalI18n.t("任务") : kind === "thinking" ? window.PortalI18n.t("思考")
      : step.final ? window.PortalI18n.t("回答") : window.PortalI18n.t("说");
    const at = step.at ? window.PortalUI?.formatDateTime?.(step.at, { style: "compact" }) || "" : "";
    const body = kind === "message"
      ? `<div class="subagentStepMarkdown">${linkifyResourceAddresses(subagentMarkdown(step.text))}</div>`
      : `<div class="subagentStepPlain">${linkified(step.text)}</div>`;
    return window.PortalI18n.html`<li class="subagentStep" data-step-index="${index}" data-step-kind="${escapeHtml(kind)}"${step.final ? " data-step-final" : ""}><details${kind === "message" ? " open" : ""}>
      <summary><span class="subagentStepMark" aria-hidden="true"></span><span class="subagentStepKind">${escapeHtml(label)}</span><span class="subagentStepText">${escapeHtml(String(step.text || "").replace(/\s+/g, " ").trim().slice(0, 200))}</span><span class="subagentStepWhen">${escapeHtml(at)}</span></summary>
      <div class="subagentStepBody">${body}${step.text_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">较长，只显示开头</small>` : ""}</div></details></li>`;
  }

  // The steps of a run's own trace from ``offset`` on, every page of them.
  async function readSubagentTrace(residentId, traceRef, offset = 0) {
    let from = offset;
    const steps = [];
    for (;;) {
      const page = (await window.PortalInstructionClient.execute("GetSubagentRun", {
        resident_id: residentId, trace_ref: traceRef, offset: from, limit: SUBAGENT_TRACE_PAGE,
      }))?.run || {};
      steps.push(...(Array.isArray(page.steps) ? page.steps : []));
      const total = Number(page.total || 0);
      if (total <= from + SUBAGENT_TRACE_PAGE) return { steps, total };
      from += SUBAGENT_TRACE_PAGE;
    }
  }

  // A run's whole result: the card keeps its opening, the run's own trace
  // keeps what it last said.
  async function subagentFullResult(residentId, run) {
    const result = String(run?.result || "");
    if (!run?.result_truncated) return result;
    try {
      const head = (await window.PortalInstructionClient.execute("GetSubagentRun", {
        resident_id: residentId, trace_ref: run.trace_ref || run.tool_use_id, offset: 0, limit: 1,
      }))?.run || {};
      const { steps } = await readSubagentTrace(residentId, run.trace_ref || run.tool_use_id, Math.max(0, Number(head.total || 0) - 40));
      const said = steps.filter(step => step.kind === "message");
      const last = said.find(step => step.final) || said.at(-1);
      if (last && String(last.text || "").length > result.length) return String(last.text);
    } catch {}
    return result;
  }

  let subagentDetailRuntime = null;

  function ensureSubagentDetailDialog() {
    if (subagentDetailRuntime?.overlay?.isConnected) return subagentDetailRuntime;
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay subagentDetailOverlay";
    overlay.dataset.portalDetailOverlay = "";
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface subagentDetailSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="subagentDetailTitle">
      <div class="portalDetailHeader" data-portal-detail-header>
        <div class="subagentDetailHeading"><div class="subagentDetailTitle" id="subagentDetailTitle" data-subagent-detail-title></div><div class="subagentDetailMeta" data-subagent-detail-meta></div></div>
        <button class="portalDetailClose" data-portal-detail-close data-portal-icon="close" type="button" aria-label="关闭子 agent 详情" title="关闭"></button>
      </div>
      <div class="subagentDetailBody" data-subagent-detail-body>
        <section class="subagentDetailSection" data-subagent-detail-prompt></section>
        <section class="subagentDetailSection" data-subagent-detail-files></section>
        <section class="subagentDetailSection" data-subagent-detail-result></section>
        <section class="subagentDetailSection"><h3>步骤</h3><ol class="subagentTimeline" data-subagent-detail-steps></ol><p class="subagentDetailNote" data-subagent-detail-status role="status"></p></section>
      </div>
      <div class="portalDetailFooter subagentDetailFooter" data-subagent-detail-footer></div>
    </div>`;
    document.body.appendChild(overlay);
    window.PortalDetailSurface?.mountAll?.(overlay);
    const runtime = { overlay, session: null, dialog: null };
    runtime.dialog = window.PortalUI.createDialogController({
      modal: overlay,
      backdropClose: true,
      escapeClose: true,
      requestClose: () => runtime.dialog.close(),
      onClose: () => {
        runtime.session?.stop();
        runtime.session = null;
      },
    });
    overlay.querySelector("[data-portal-detail-close]")?.addEventListener("click", () => runtime.dialog.close());
    overlay.addEventListener("click", event => {
      const button = event.target instanceof Element ? event.target.closest("[data-subagent-detail-action]") : null;
      if (!button || button.disabled) return;
      if (button.dataset.subagentDetailAction === "close") runtime.dialog.close();
      else runtime.session?.action(button.dataset.subagentDetailAction);
    });
    subagentDetailRuntime = runtime;
    return runtime;
  }

  // The whole run of one subagent: its task, the files it changed, its token
  // use and every step from its own trace (GetSubagentRun), followed while
  // it runs.  ``actions(live)`` are the footer buttons of the Chat that
  // opened it, ``onAction(name, context)`` runs them.
  function openSubagentDetail(options = {}) {
    const runtime = ensureSubagentDetailDialog();
    runtime.session?.stop();
    const { overlay } = runtime;
    const residentId = String(options.residentId || "");
    const id = String(options.run?.tool_use_id || "");
    const traceRef = String(options.run?.trace_ref || id);
    const el = name => overlay.querySelector(`[data-subagent-detail-${name}]`);
    const scroller = el("body");
    const list = el("steps");
    let run = { ...(options.run || {}) };
    let steps = [];
    let total = 0;
    let timer = 0;
    let stopped = false;
    let signature = "";
    const isLive = () => options.live === true && !!document.querySelector(`[data-subagent-live="${CSS.escape(id)}"]`);
    const openPreview = request => {
      runtime.dialog.close();
      return requestWorkspacePreview(request);
    };
    const bindLinks = root => {
      bindResponseSourcePaths(root, { onLinkPreview: openPreview });
      bindResourceChips(root, { onResourcePreview: context => openPreview({ ...context, resourceAddress: context.address, path: context.address, title: context.node?.textContent?.trim() || context.address, source: "chat" }) });
      bindLinkPreview(root, { onLinkPreview: openPreview });
    };
    const renderRun = () => {
      const live = isLive();
      if (live) run = { ...run, ...(subagentLiveById.get(id) || {}) };
      const prompt = String(run.prompt || steps.find(step => step.kind === "input")?.text || "").trim();
      const next = JSON.stringify([live, run.status, run.description, run.agent_type, run.model, run.usage, run.files, run.result, prompt, run.finished_at]);
      if (next === signature) return;
      signature = next;
      el("title").textContent = subagentRunTitle(run);
      el("meta").innerHTML = [
        `<span data-subagent-detail-state="${live ? "running" : escapeHtml(run.status || "")}">${escapeHtml(live ? window.PortalI18n.t("运行中") : subagentStatusLabel(run.status))}</span>`,
        live && run.started_at
          ? `<span data-subagent-since="${escapeHtml(run.started_at)}">${escapeHtml(subagentClockText(run.started_at))}</span>`
          : subagentRunDuration(run) ? `<span>${escapeHtml(subagentRunDuration(run))}</span>` : "",
        subagentTokens(run.usage) ? `<span>${escapeHtml(subagentTokens(run.usage))}</span>` : "",
        run.usage?.tool_uses ? `<span>${escapeHtml(window.PortalI18n.text`${run.usage.tool_uses} 次工具调用`)}</span>` : "",
        run.model ? `<span>${escapeHtml(run.model)}</span>` : "",
        run.background ? `<span>${escapeHtml(window.PortalI18n.t("后台"))}</span>` : "",
      ].filter(Boolean).join("");
      el("prompt").innerHTML = window.PortalI18n.html`<h3>它收到的任务</h3>${prompt
        ? window.PortalI18n.html`<div class="subagentDetailPrompt">${linkifyResourceAddresses(escapeHtml(prompt))}</div>${run.prompt_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">较长，只显示开头</small>` : ""}`
        : window.PortalI18n.html`<small class="nativeTurnEmpty">这个 Provider 没有给出任务全文</small>`}`;
      const files = subagentFilesHtml(run.files);
      el("files").hidden = !files;
      el("files").innerHTML = files ? window.PortalI18n.html`<h3>改动的文件</h3>${files}` : "";
      const result = !live && String(run.result || "").trim();
      el("result").hidden = !result;
      el("result").innerHTML = result
        ? window.PortalI18n.html`<h3>结果</h3><div class="subagentStepMarkdown">${linkifyResourceAddresses(subagentMarkdown(run.result))}</div>${run.result_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">卡片只保留开头；完整回答见步骤中的最后一条</small>` : ""}`
        : "";
      el("footer").innerHTML = (options.actions?.(live) || []).filter(action => !action.hidden)
        .map(action => `<button type="button" class="portalButton${action.danger ? " is-danger" : ""}" data-subagent-detail-action="${escapeHtml(action.action)}"${action.title ? ` title="${escapeHtml(action.title)}"` : ""}>${escapeHtml(action.label)}</button>`).join("")
        + window.PortalI18n.html`<button type="button" class="portalButton" data-subagent-detail-action="close">关闭</button>`;
      for (const name of ["prompt", "files", "result"]) bindLinks(el(name));
      if (live) startSubagentClocks();
    };
    const renderSteps = (from, fresh) => {
      const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
      const open = new Set([...list.querySelectorAll(":scope > li > details")]
        .map(details => [details.parentElement.dataset.stepIndex, details.open]).filter(([, isOpen]) => isOpen).map(([index]) => index));
      [...list.children].forEach(item => { if (Number(item.dataset.stepIndex) >= from) item.remove(); });
      const template = document.createElement("template");
      template.innerHTML = fresh.map(subagentTimelineStepHtml).join("");
      const added = [...template.content.children];
      added.forEach(item => {
        if (open.has(item.dataset.stepIndex)) item.querySelector(":scope > details")?.setAttribute("open", "");
        list.append(item);
        bindLinks(item);
      });
      if (added.some(item => item.hasAttribute("data-step-running"))) startSubagentClocks();
      // A reader who scrolled down to the latest step keeps following it.
      if (nearBottom && scroller.scrollTop > 0 && isLive()) scroller.scrollTop = scroller.scrollHeight;
    };
    const load = async () => {
      // A call still running reads again from there; the rest of the trace only grows.
      const running = steps.find(step => step.kind === "tool" && SUBAGENT_RUNNING_STEPS.has(String(step.status || "")));
      const from = running ? Number(running.index) : total;
      try {
        const page = await readSubagentTrace(residentId, traceRef, from);
        if (stopped) return;
        total = page.total;
        steps = [...steps.filter(step => Number(step.index) < from), ...page.steps];
        renderSteps(from, page.steps);
        el("status").textContent = steps.length ? "" : window.PortalI18n.t("还没有记录的步骤");
      } catch (error) {
        if (stopped) return;
        el("status").textContent = error?.code === "instruction.not_found"
          ? window.PortalI18n.t("这个子 agent 还没有记录的步骤")
          : window.PortalI18n.text`步骤读取失败：${error?.message || String(error)}`;
      }
    };
    const tick = async () => {
      if (stopped) return;
      const live = isLive();
      renderRun();
      await load();
      if (stopped) return;
      renderRun();
      // A run that ended gets its last steps once more, then stays still.
      if (live) timer = window.setTimeout(tick, SUBAGENT_DETAIL_POLL_MS);
    };
    runtime.session = {
      stop() {
        stopped = true;
        window.clearTimeout(timer);
      },
      action(name) {
        return options.onAction?.(name, { run, steps, live: isLive(), close: () => runtime.dialog.close() });
      },
    };
    list.replaceChildren();
    el("status").textContent = window.PortalI18n.t("正在读取步骤…");
    scroller.scrollTop = 0;
    runtime.dialog.open();
    tick();
    return runtime;
  }

  function placeCaretAtEnd(input) {
    if (!input) return;
    input.focus?.();
    if ("value" in input) {
      const end = input.value.length;
      try {
        input.selectionStart = end;
        input.selectionEnd = end;
      } catch {}
      return;
    }
    const range = document.createRange();
    range.selectNodeContents(input);
    range.collapse(false);
    const selection = window.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  function subagentSlug(text) {
    return String(text || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "subagent";
  }

  function subagentResultMarkdown(run, result) {
    const lines = [`# ${subagentRunTitle(run)}`, ""];
    const facts = [
      [window.PortalI18n.t("状态"), subagentStatusLabel(run.status)],
      [window.PortalI18n.t("开始"), run.started_at],
      [window.PortalI18n.t("结束"), run.finished_at],
      [window.PortalI18n.t("模型"), run.model],
      [window.PortalI18n.t("用量"), subagentTokens(run.usage)],
      ["tool_use_id", run.tool_use_id],
    ].filter(([, value]) => String(value || "").trim());
    facts.forEach(([name, value]) => lines.push(`- ${name}：${value}`));
    if (String(run.prompt || "").trim()) lines.push("", `## ${window.PortalI18n.t("任务")}`, "", String(run.prompt).trim());
    lines.push("", `## ${window.PortalI18n.t("结果")}`, "", String(result || "").trim() || window.PortalI18n.t("子 agent 没有返回文字结果"));
    const files = Array.isArray(run.files) ? run.files : [];
    if (files.length) lines.push("", `## ${window.PortalI18n.t("改动的文件")}`, "", ...files.map(file => `- ${file.path}`));
    return `${lines.join("\n")}\n`;
  }

  const SUBAGENT_STOP_REASONS = {
    unknown_subagent: "Provider 还没有登记这个子 agent（可能刚启动或已经结束），稍后再试",
    subagent_stop_unsupported: "这个 Provider 不能单独停止子 agent",
    subagent_finished: "子 agent 已经结束，不用再停止",
    no_active_turn: "这一轮已经结束",
    no_session: "这一轮的会话已经结束",
    session_changed: "会话已经换成新的，这个子 agent 不在其中",
  };

  function residentProtocolMessageToWorkspace(message = {}) {
    const parts = Array.isArray(message.content_parts) ? message.content_parts : [];
    const media = parts.filter(part => part?.type === "image" || part?.type === "file");
    const text = parts.filter(part => part?.type === "text").map(part => String(part.text || "")).join("\n");
    const legacyMediaCaption = media.map(part => `[${part.type}: ${part.name || part.type}]`).join(" ");
    // Media-only delivery has a technical text part for Resident execution.
    // The thumbnail already labels the attachment in the conversation.
    const captionParts = media.length && [legacyMediaCaption, mediaOnlyCaption(media)].includes(text.trim())
      ? parts.filter(part => part.type !== "text")
      : parts;
    let previousType = "";
    const orderedContent = captionParts.map(part => {
      let value = "";
      if (part?.type === "text") {
        value = `${previousType === "text" ? "\n" : ""}${String(part.text || "")}`;
      } else if (part?.type === "image" || part?.type === "file") {
        const path = mediaPartPath(part);
        if (path && !text.includes(path)) value = `[${part.type}: ${path}]`;
      } else return "";
      previousType = part.type;
      return value;
    }).join("");
    // “审查这些修改” sends only the reviewed turn; any text beside it is Portal's wording for the agent.
    const content = (parts.some(part => part?.type === "turn_review") ? window.PortalI18n.t("请审查这一轮的修改") : "")
      || orderedContent
      || parts.filter(part => part?.type === "async_questions").flatMap(part => part.questions || []).map(question => String(question.title || "")).join("\n")
      || parts.filter(part => ["native_turn", "resource_changes"].includes(part.type)).map(part => turnArtifactDisplay(part).title).join("\n")
      || parts.filter(part => part?.type === "subagent_run").map(subagentRunTitle).join("\n")
      || parts.filter(part => nativeInteractionNames.has(part.tool_name)).map(part => part.type === "tool_call"
        ? part.interaction ? window.PortalI18n.t("原生交互请求") : (part.questions || []).map(question => question.question).join("\n")
        : ({ answered: window.PortalI18n.t("回答已送达"), cancelled: window.PortalI18n.t("提问已取消"), expired: window.PortalI18n.t("提问已失效") }[part.status] || window.PortalI18n.t("提问已结束"))).join("\n");
    return {
      ...message,
      ...(message.local_delivery ? { pending: true } : {}),
      id: String(message.message_id || ""),
      role: message.role === "agent" ? "assistant" : String(message.role || "system"),
      author: String(message.origin?.sender?.id || message.author_id || ""),
      content,
      input_media: media,
      token_usage: message.usage || {},
    };
  }

  // A Family facade shows each unfinished request's progress after
  // the request and the outputs written for it so far — routing, its place in
  // the member's queue, the member's live work — not one block below all
  // messages.  The block belongs to the request's last shown message, so a new
  // output moves it and re-renders both.
  const FAMILY_OPEN_REQUEST_STATES = new Set(["waiting_members", "delivering", "routing", "scope_review", "quota_review", "queued", "running", "recovery"]);
  const FAMILY_REQUEST_ACTIONS = Object.freeze({
    // CancelInput withdraws a waiting request and stops a running one.
    "family-request-withdraw": "cancelInput",
    "family-request-stop": "cancelInput",
    "family-request-inject": "injectInput",
  });
  // A withdrawn message never reaches the Resident. A Family facade, which
  // already displayed the request, keeps only a withdrawal annotation.
  // A message that started meanwhile is stopped instead (decision_2a3ff353165c).
  const QUEUE_WITHDRAWN_NOTICE = window.PortalI18n.t("已撤回，这条消息不会被处理");
  function noticeQueueWithdrawn(result) {
    if (result?.cancelled === true || String(result?.input_state?.state || "") === "cancelled") {
      window.PortalUI?.toast?.(QUEUE_WITHDRAWN_NOTICE, "success");
    }
  }

  function noticeQueueCleared(result) {
    const count = Number(result.cancelled);
    window.PortalUI?.toast?.(window.PortalI18n.text`已清除 ${count} 条排队消息，正在执行的消息不受影响。`, "success");
  }

  function withFamilyRequestStatus(messages) {
    const status = new Map();
    const live = new Set();
    messages.forEach((item, index) => {
      const relay = item?.relay && typeof item.relay === "object" ? item.relay : null;
      if (item?.role !== "user" || !relay || !FAMILY_OPEN_REQUEST_STATES.has(String(relay.state || ""))) return;
      let end = index;
      while (end + 1 < messages.length
        && String(messages[end + 1]?.family_reply?.request_message_id || "") === item.id) end += 1;
      status.set(end, { ...relay, request_message_id: item.id });
      // Outputs of a request its member is running stay open, like a running turn's.
      if (relay.state === "running") for (let output = index + 1; output <= end; output += 1) live.add(output);
    });
    if (!status.size) return messages;
    return messages.map((item, index) => (status.has(index) || live.has(index) ? {
      ...item,
      ...(live.has(index) ? { live_output: true } : {}),
      ...(status.has(index) ? { family_status: status.get(index) } : {}),
    } : item));
  }

  // Infos delivered with a request were read with it and show inside it (concept_43c793b4f3ab).
  // Every read folds them on the server, but an info enters the trace when its request
  // starts, so it arrives as an Event after that request: fold it here the same way.
  // An info waiting again (held_info) stays a message of its own.
  function foldDeliveredInfos(messages, { handedToSteward = false } = {}) {
    const present = new Set(messages.map(message => String(message?.message_id || "")));
    const infos = new Map();
    messages.forEach(message => {
      const request = String(message?.released_with || "");
      if (!request || message?.held_info === true || !present.has(request)) return;
      if (!infos.has(request)) infos.set(request, []);
      infos.get(request).push(message);
    });
    if (!infos.size) return messages;
    const folded = new Set([...infos.values()].flat());
    const view = info => Object.fromEntries(["message_id", "author_id", "origin", "created_at", "content_parts"]
      .filter(key => key in info).map(key => [key, info[key]]));
    return messages.filter(message => !folded.has(message)).map(message => {
      const attached = infos.get(String(message?.message_id || ""));
      if (!attached) return message;
      const shown = Array.isArray(message.attached_infos) ? message.attached_infos : [];
      const known = new Set(shown.map(info => String(info?.message_id || "")));
      const added = attached.filter(info => !known.has(String(info.message_id || ""))).map(view);
      return {
        ...message,
        attached_infos: [...shown, ...added],
        ...(handedToSteward ? { attached_infos_steward: true } : {}),
      };
    });
  }

  function projectResidentConversation(metadata = {}, snapshot = {}) {
    const base = metadata && typeof metadata === "object" ? metadata : {};
    const facade = !!(snapshot.family_facade && typeof snapshot.family_facade === "object");
    const familyMembers = Array.isArray(snapshot.family_facade?.members) ? snapshot.family_facade.members : null;
    const roleIssues = (snapshot.family_facade?.nodes || []).filter(node =>
      node.availability?.status && node.availability.status !== "available");
    const familyIssueLabel = [
      ...roleIssues.map(node => `${node.role === "router" ? "Router" : "Steward"}：${node.availability.reason}`),
      snapshot.family_facade?.alert_contact?.status === "missing" ? window.PortalI18n.t("未登记真人告警联系人") : "",
    ].filter(Boolean).join("；");
    const projected = foldDeliveredInfos(Array.isArray(snapshot.messages) ? snapshot.messages : [],
      { handedToSteward: facade }).map(residentProtocolMessageToWorkspace);
    const known = new Set(projected.map(message => message.id));
    for (const message of snapshot.local_submissions || []) {
      if (!known.has(message.message_id)) projected.push(residentProtocolMessageToWorkspace(message));
    }
    const messages = facade ? withFamilyRequestStatus(projected) : projected;
    const activities = !facade && Array.isArray(snapshot.activities) ? snapshot.activities : [];
    const state = window.PortalResidentProtocol.stateView(snapshot.state);
    const controls = snapshot.controls || {};
    // A facade's work shows under each request instead.
    const live = facade ? false : state.working;
    const phase = facade ? "" : (state.work_state === "stopping" ? "stopping" : state.work_phase);
    const queue = {
      ...(snapshot.queue && typeof snapshot.queue === "object"
        ? snapshot.queue
        : { resident_id: String(snapshot.resident?.resident_id || ""), items: [] }),
      active_turn_input_supported: controls.active_turn_input === true,
    };
    const runtimeSettings = snapshot.runtime_settings || {};
    const contract = base.chat_workspace && typeof base.chat_workspace === "object"
      ? {
        ...base.chat_workspace,
        messages,
        ...(facade && familyMembers ? { rendering: {
          ...(base.chat_workspace.rendering || {}),
          assistant_name: snapshot.family_facade.title || base.chat_workspace.rendering?.assistant_name || "Family",
          empty_text: window.PortalI18n.text`这是 Family「${snapshot.family_facade.title || base.chat_workspace.rendering?.assistant_name || "Family"}」的门面。发到这里的消息由 Family Router 交给最合适的成员处理，`
            + window.PortalI18n.t("成员的回复显示在这里。还没有经过门面的对话；直接发给成员的对话在各成员自己的对话里。")
            + (familyMembers.length ? window.PortalI18n.text`成员：${familyMembers.map(member => member.title || member.resident_id || "").join("、")}。` : ""),
        } } : {}),
        ...(facade && (familyMembers || state.family_activity || familyIssueLabel) ? { header: {
          ...(base.chat_workspace.header || {}),
          subtitle_parts: (base.chat_workspace.header?.subtitle_parts || []).map(part => {
            if (part.identity !== "family") return part;
            let text = familyMembers
              ? (familyMembers.length ? window.PortalI18n.text`Family 门面（${familyMembers.length} 位成员）` : window.PortalI18n.t("Family 门面"))
              : window.PortalI18n.message(part.text);
            if (state.family_activity) text += ` · ${{ running: window.PortalI18n.t("运行中"), queued: window.PortalI18n.t("排队中"), idle: window.PortalI18n.t("空闲") }[state.display_work_state]}${state.family_activity.running_count > 1 ? window.PortalI18n.text`（${state.family_activity.running_count} 个节点）` : ""}`;
            return { ...part, text };
          }).concat(familyIssueLabel ? [{ text: familyIssueLabel, identity: "family_availability" }] : []),
        } } : {}),
        runtime: { live, details: activities, phase },
        composer: {
          ...(base.chat_workspace.composer || {}),
          can_send: controls.can_send === true,
          disabled_reason: controls.disabled_reason || "",
          send_hint: controls.send_hint ? window.PortalI18n.t(controls.send_hint) : "",
          execution_gate: state.execution_gate,
          intake_state: state.intake_state,
          can_stop: controls.can_stop === true,
          queue,
          runtime_settings: runtimeSettings,
        },
        history: {
          can_load_older: Boolean(String(snapshot.next_page_token || "").trim()),
        },
        inheritance: snapshot.inheritance && typeof snapshot.inheritance === "object" ? snapshot.inheritance : null,
      }
      : null;
    return {
      ...base,
      ...(Object.prototype.hasOwnProperty.call(snapshot, "family_facade") ? { family_facade: snapshot.family_facade } : {}),
      messages,
      runtime_activity: { live, details: activities, phase },
      resident_state: state,
      queue,
      input_states: Array.isArray(snapshot.input_states) ? snapshot.input_states : [],
      runtime_settings: runtimeSettings,
      ...(contract ? { chat_workspace: contract } : {}),
    };
  }

  async function loadResidentConversation(target, options = {}) {
    const normalized = normalizeResidentTarget(target);
    if (!normalized.resident_id) throw new TypeError("resident_id required");
    const [projection, snapshot] = await Promise.all([
      window.PortalResidentProjection.load(normalized.resident_id, {
        force: options.force === true,
      }),
      window.PortalResidentProtocol.loadSnapshot(normalized.resident_id, {
        force: options.force === true,
        historyLimit: Math.max(
          1,
          Number(options.historyLimit || DEFAULT_RESIDENT_HISTORY_PAGE_SIZE),
        ),
      }),
    ]);
    return projectResidentConversation(projection, snapshot);
  }

  const nativeInteractionNames = new Set(["request_user_input", "request_approval", "mcp_elicitation"]);

  class ResidentQuestionCards {
    constructor(panel) {
      this.panel = panel;
      // Native approval and MCP cards, by request.
      this.cards = new Map();
      this.turnEditors = new Map();
      this.nativeOutcomes = new Map();
      this.sending = new Set();
      // Each open question group's chosen answers and current question, by
      // group key; kept per actor and conversation until the group closes.
      this.progress = new Map();
      // The card of the one open question group being answered, shown where
      // it was asked; ``chosen`` names an earlier open group picked for it.
      this.active = { key: "", element: null, error: "" };
      this.chosen = "";
      // Groups closed here, until the conversation shows their closed slot.
      this.closed = new Set();
      this.scope = "";
    }

    reset() {
      // Reloading the same conversation keeps its message nodes and their open
      // diffs, so only editors whose card has left the page are released here.
      this.releaseTurnEditors();
      this.scope = "";
      for (const card of this.cards.values()) card.form.remove();
      this.cards.clear();
      this.nativeOutcomes.clear();
      this.progress.clear();
      this.chosen = "";
      this.closed.clear();
      this.closeActive();
    }

    progressKey(scope = this.scope) {
      return ["portal.residentQuestionProgress.v1", this.panel.options.draftOwner || "anonymous", scope]
        .map(part => encodeURIComponent(part)).join(":");
    }

    saveProgress() {
      if (!this.scope) return;
      if (this.progress.size) window.PortalState.storage.writeJson(this.progressKey(), Object.fromEntries(this.progress));
      else window.PortalState.storage.remove(this.progressKey());
    }

    // A question group is the questions one Agent message asks (its
    // ``async_questions`` part) or one native ``request_user_input`` request.
    // A message's group is answered by one answer message and stays open
    // until then, whatever later messages and groups come, unless a person
    // closes it (its answer slot is then cancelled as question_set_closed);
    // the server applies the same rule.  A member reply shown on a Family
    // facade carries the state the member's own Conversation gives its group.
    questionGroups(snapshot) {
      const messages = snapshot.messages || [];
      const queued = new Set((snapshot.queue?.items || []).map(item => item.message_id));
      const states = new Map((snapshot.input_states || []).map(input => [input.message_id, input.state]));
      const closed = new Set([...this.closed, ...(snapshot.input_states || [])
        .filter(input => input.error?.code === "question_set_closed").map(input => input.message_id)]);
      const answers = new Map();
      for (const message of messages) {
        if (message.role !== "user") continue;
        for (const part of message.content_parts || []) {
          if (part.type !== "async_question_answer") continue;
          const merged = answers.get(part.source_message_id) || {};
          for (const entry of part.answers || []) merged[entry.question_index] = entry.answer;
          answers.set(part.source_message_id, merged);
        }
      }
      const nativeResults = new Map(messages.flatMap(message => (message.content_parts || [])
        .filter(part => part.type === "tool_result" && part.tool_name === "request_user_input")
        .map(part => [part.request_id, part])));
      const groups = [];
      for (const message of messages) {
        if (message.role !== "agent") continue;
        for (const part of message.content_parts || []) {
          if (part.type === "async_questions") {
            const answered = answers.has(message.message_id)
              || (!closed.has(part.answer_message_id) && (states.has(part.answer_message_id) || queued.has(part.answer_message_id)));
            groups.push({
              kind: "async", key: part.answer_message_id, source_message_id: message.message_id,
              // A member's set shown on a facade is closed in the member's own conversation.
              closable: !message.family_reply,
              // Permissions are lent to the Resident this conversation belongs to, never through a facade.
              lendable: !message.family_reply,
              questions: (part.questions || []).map(question => ({ id: question.question_index, title: question.title, options: question.options || [],
                credentials: Array.isArray(question.credentials) ? question.credentials : [] })),
              state: answered ? "answered" : closed.has(part.answer_message_id) ? "closed" : part.state || "open",
              answers: answers.get(message.message_id) || {}, delivery: states.get(part.answer_message_id) || "",
            });
          } else if (part.type === "tool_call" && part.tool_name === "request_user_input" && !part.interaction) {
            const result = nativeResults.get(part.request_id)
              || (part.status !== "pending" ? part : this.nativeOutcomes.get(part.request_id) || part);
            groups.push({
              kind: "native", key: part.request_id, source_message_id: message.message_id, native: part,
              questions: (part.questions || []).map(question => ({ id: question.id, title: question.question, options: (question.options || []).map(option => option.label) })),
              state: result.status === "pending" ? "open" : result.status,
              answers: Object.fromEntries(Object.entries(result.answers || {}).map(([id, answer]) => [id, (answer.answers || []).join("、")])),
              delivery: "",
            });
          }
        }
      }
      return groups;
    }

    render(snapshot) {
      const target = this.panel.workspace.target();
      const scope = residentSessionScope(target);
      if (this.scope !== scope) {
        this.reset();
        this.scope = scope;
        this.progress = new Map(Object.entries(window.PortalState.storage.readJson(this.progressKey(), {}) || {}));
      }
      const messages = new Map((snapshot.messages || []).map(message => [message.message_id, message]));
      this.bindTurnArtifacts(messages, target, snapshot.controls?.can_send === true);
      const hosts = new Map([...this.panel.workspace.els.messages.querySelectorAll("[data-resident-question]")]
        .map(element => [element.dataset.residentQuestion, element]));
      const canSend = snapshot.controls?.can_send === true;
      const groups = this.questionGroups(snapshot);
      // One open group at a time is answered where it was asked: the one
      // picked from its summary or already being edited, else the newest;
      // the others wait in summary.
      const answerable = canSend ? groups.filter(group => group.state === "open" && hosts.has(group.key)) : [];
      const open = answerable.find(group => group.key === this.chosen) || answerable.at(-1) || null;
      for (const group of groups) {
        const host = hosts.get(group.key);
        if (host && group !== open) this.renderSummary(host, group, canSend);
      }
      const progressCount = this.progress.size;
      for (const group of groups) {
        // Discard the choices of a group that has closed; a group off this
        // page keeps them until it is seen again.
        if (group.state !== "open") this.progress.delete(group.key);
      }
      if (this.progress.size !== progressCount) this.saveProgress();
      if (open) this.renderActive(open, hosts.get(open.key), target, scope);
      else this.closeActive();
      this.renderInteractions(messages, hosts, target, scope, canSend);
    }

    renderSummary(host, group, canSend) {
      const labels = {
        open: canSend ? window.PortalI18n.t("等待回答") : window.PortalI18n.t("只有拥有发送权限的用户可以回答。"),
        expired: group.kind === "native" ? window.PortalI18n.t("请求已失效") : window.PortalI18n.t("已失效：原对话已清空"),
        closed: window.PortalI18n.t("已关闭，不再回答"),
        cancelled: window.PortalI18n.t("请求已取消"),
        answered: ({
          queued: window.PortalI18n.t("回答已排队，将在可接收时送达"),
          failed: window.PortalI18n.t("回答已接收，但执行失败；请查看对话错误"),
          cancelled: window.PortalI18n.t("回答对应的执行已取消"),
        })[group.delivery] || window.PortalI18n.t("回答已送达"),
      };
      const status = labels[group.state] || window.PortalI18n.t("请求已结束");
      const pick = canSend && group.state === "open";
      const closing = this.sending.has(`${this.scope}:${group.key}`);
      const signature = JSON.stringify([group.state, status, group.answers, pick, closing]);
      if (host.dataset.questionSummary === signature) return;
      host.dataset.questionSummary = signature;
      host.innerHTML = window.PortalI18n.html`<div class="residentQuestionSummary" data-state="${escapeHtml(group.state)}">
        <div class="residentQuestionSummaryHead"><span class="residentQuestionTitle">${escapeHtml(window.PortalI18n.text`Agent 的问题 · ${group.questions.length}`)}</span><span class="residentQuestionStatus" role="status">${escapeHtml(status)}</span>${pick ? window.PortalI18n.html`<span class="residentQuestionSummaryActions"><button type="button" class="portalButton is-compact" data-question-pick ${closing ? "disabled" : ""}>回答</button>${group.closable ? window.PortalI18n.html`<button type="button" class="portalButton is-compact is-quiet" data-question-close ${closing ? "disabled" : ""}>不再回答</button>` : ""}</span>` : ""}</div>
        <ol>${group.questions.map(question => {
          const answer = group.answers[question.id];
          return window.PortalI18n.html`<li><span>${escapeHtml(question.title)}</span>${answer ? window.PortalI18n.html`<strong>${escapeHtml(answer)}</strong>` : ""}</li>`;
        }).join("")}</ol>
      </div>`;
      host.querySelector("[data-question-pick]")?.addEventListener("click", () => {
        this.chosen = group.key;
        this.render(this.panel.snapshot() || {});
        this.active.element?.querySelector(".residentQuestionChoice, [data-question-custom]")?.focus();
      });
      host.querySelector("[data-question-close]")?.addEventListener("click", () => void this.closeGroup(group));
    }

    closeActive() {
      this.active.element?.remove();
      this.active = { key: "", element: null, error: "" };
    }

    renderActive(group, host, target, scope) {
      if (this.active.key !== group.key) {
        this.closeActive();
        this.active = { key: group.key, element: document.createElement("div"), error: "" };
        this.active.element.className = "residentQuestionCard residentQuestionStepper";
        this.active.element.setAttribute("role", "group");
        this.active.element.setAttribute("aria-label", window.PortalI18n.t("Agent 的问题"));
        this.active.element.addEventListener("keydown", event => this.activeKey(event));
        this.active.element.addEventListener("focusin", () => { this.chosen = group.key; });
      }
      this.active.group = group;
      this.active.target = target;
      this.active.scope = scope;
      if (this.active.element.parentElement !== host) {
        delete host.dataset.questionSummary;
        host.replaceChildren(this.active.element);
      }
      const sending = this.sending.has(`${scope}:${group.key}`);
      const signature = JSON.stringify([this.progressOf(group), sending, this.active.error]);
      if (this.active.signature !== signature) this.drawActive();
    }

    progressOf(group) {
      if (!this.progress.has(group.key)) this.progress.set(group.key, { answers: {}, step: 0 });
      return this.progress.get(group.key);
    }

    drawActive() {
      const { group, element, scope } = this.active;
      const progress = this.progressOf(group);
      const sending = this.sending.has(`${scope}:${group.key}`);
      this.active.signature = JSON.stringify([progress, sending, this.active.error]);
      const step = Math.min(progress.step, group.questions.length - 1);
      const question = group.questions[step];
      const chosen = progress.answers[question.id] || "";
      const custom = chosen && !question.options.includes(chosen) ? chosen : "";
      // A permission request: its fixed options are the server's own words, the
      // first approves, and only the exact permissions it lists can be lent.
      const lending = question.credentials?.length > 0;
      const credentials = lending ? window.PortalI18n.html`<div class="residentQuestionCredentials">
          <span>${escapeHtml(group.lendable ? window.PortalI18n.t("同意后随这条回答交给 Resident 的权限：") : window.PortalI18n.t("成员在 Family 内提出的权限请求不能经门面交出"))}</span>
          <ul>${question.credentials.map(item => window.PortalI18n.html`<li><code>${escapeHtml(item.a)}</code> · <code>${escapeHtml(item.resource_id)}</code></li>`).join("")}</ul>
          <span class="residentQuestionCredentialScope">${escapeHtml(window.PortalI18n.t("只在 Resident 处理这条回答期间有效，处理完即失效。"))}</span>
        </div>` : "";
      const hint = this.active.error || (sending ? window.PortalI18n.t("正在提交…")
        : group.kind === "native" ? (group.native.is_blocking ? window.PortalI18n.t("Agent 正在等待你的回答") : window.PortalI18n.t("Agent 继续工作；请求结束后将不可回答。"))
        : window.PortalI18n.t("这组问题在回答前一直保留"));
      element.classList.toggle("has-error", Boolean(this.active.error));
      element.setAttribute("aria-busy", String(sending));
      element.innerHTML = window.PortalI18n.html`<div class="residentQuestionStepHead">
          <span class="residentQuestionStepProgress">${escapeHtml(window.PortalI18n.text`问题 ${step + 1}/${group.questions.length}`)}</span>
          <span class="residentQuestionStatus" role="status">${escapeHtml(hint)}</span>
          ${step > 0 ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-question-back>上一题</button>` : ""}
          ${group.kind === "native" ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-question-cancel>取消请求</button>` : ""}
          ${group.kind === "async" && group.closable ? window.PortalI18n.html`<button type="button" class="portalButton is-compact is-quiet" data-question-close>不再回答</button>` : ""}
        </div>
        <div class="residentQuestionTitle">${escapeHtml(question.title)}</div>
        ${credentials}
        <div class="residentQuestionChoices">${question.options.map((option, index) => window.PortalI18n.html`<button type="button" class="portalButton residentQuestionChoice" data-question-option="${index}" aria-pressed="${String(option === chosen)}">${index < 9 ? window.PortalI18n.html`<kbd>${index + 1}</kbd>` : ""}<span>${escapeHtml(lending ? window.PortalI18n.t(option) : option)}</span></button>`).join("")}</div>
        ${lending ? "" : window.PortalI18n.html`<div class="residentQuestionCustom"><input type="text" data-question-custom value="${escapeHtml(custom)}" placeholder="${escapeHtml(question.options.length ? window.PortalI18n.t("其他回答，按 Enter 确认") : window.PortalI18n.t("输入回答，按 Enter 确认"))}" aria-label="${escapeHtml(window.PortalI18n.text`回答：${question.title}`)}" /><button type="button" class="portalButton is-compact is-primary" data-question-confirm>${escapeHtml(step === group.questions.length - 1 ? window.PortalI18n.t("提交回答") : window.PortalI18n.t("下一题"))}</button></div>`}`;
      for (const control of element.querySelectorAll("button, input")) control.disabled = sending;
      if (lending && !group.lendable) element.querySelector('[data-question-option="0"]').disabled = true;
      element.querySelectorAll("[data-question-option]").forEach(button => button.addEventListener("click", () => {
        this.answer(question.options[Number(button.dataset.questionOption)]);
      }));
      const input = element.querySelector("[data-question-custom]");
      element.querySelector("[data-question-confirm]")?.addEventListener("click", () => this.answer(input.value.trim() || chosen));
      element.querySelector("[data-question-back]")?.addEventListener("click", () => {
        progress.step = step - 1;
        this.saveProgress();
        this.active.error = "";
        this.drawActive();
        element.querySelector(".residentQuestionChoice, [data-question-custom]")?.focus();
      });
      element.querySelector("[data-question-cancel]")?.addEventListener("click", () => {
        void this.sendNativeQuestions(group, "cancel");
      });
      element.querySelector("[data-question-close]")?.addEventListener("click", () => void this.closeGroup(group));
    }

    activeKey(event) {
      // Keys typed in the card answer the question; the conversation's own shortcuts do not see them.
      event.stopPropagation();
      if (event.isComposing) return;
      const input = event.target.closest?.("[data-question-custom]");
      if (input) {
        if (event.key === "Enter" && input.value.trim()) {
          event.preventDefault();
          this.answer(input.value.trim());
        }
        return;
      }
      const option = /^[1-9]$/.test(event.key) ? this.active.element.querySelector(`[data-question-option="${Number(event.key) - 1}"]`) : null;
      if (option && !option.disabled && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        option.click();
      }
    }

    answer(value) {
      const { group } = this.active;
      if (!group || !value || this.sending.has(`${this.active.scope}:${group.key}`)) return;
      const progress = this.progressOf(group);
      const step = Math.min(progress.step, group.questions.length - 1);
      progress.answers[group.questions[step].id] = value;
      this.active.error = "";
      // Go on to the next open question; a revised earlier answer walks the
      // following questions again, and the last answer submits the group.
      const unanswered = group.questions.map((question, index) => index).filter(index => !progress.answers[group.questions[index].id]);
      const next = unanswered.find(index => index > step) ?? unanswered[0] ?? (step + 1 < group.questions.length ? step + 1 : -1);
      if (next < 0) {
        progress.step = step;
        this.saveProgress();
        void (group.kind === "native" ? this.sendNativeQuestions(group, "answer") : this.sendAnswers(group));
        return;
      }
      progress.step = next;
      this.saveProgress();
      this.drawActive();
      this.active.element.querySelector(".residentQuestionChoice, [data-question-custom]")?.focus();
    }

    async sendAnswers(group) {
      const { target, scope } = this.active;
      const key = `${scope}:${group.key}`;
      if (this.sending.has(key)) return;
      const progress = this.progressOf(group);
      const answers = group.questions.map(question => ({ question_index: question.id, answer: progress.answers[question.id] }));
      // The user lends, as themself, exactly what each approved request lists.
      const lent = new Map();
      if (group.lendable) {
        for (const question of group.questions) {
          if (!question.credentials?.length || progress.answers[question.id] !== question.options[0]) continue;
          for (const item of question.credentials) lent.set(`${item.a}\n${item.resource_id}`, { a: item.a, resource_id: item.resource_id });
        }
      }
      this.sending.add(key);
      this.render(this.panel.snapshot() || {});
      try {
        const result = await window.PortalResidentProtocol.sendInput({
          resident_id: target.resident_id,
          lineage_id: target.lineage_id,
          ...(lent.size ? { temporary_credentials: [...lent.values()] } : {}),
          message: {
            message_id: group.key,
            role: "user",
            content_parts: [
              { type: "text", text: answers.map(entry => entry.answer).join("\n") },
              { type: "async_question_answer", source_message_id: group.source_message_id, answers },
            ],
          },
        });
        if (this.scope === scope) {
          this.progress.delete(group.key);
          this.saveProgress();
          this.panel.options.onAcknowledgement?.(result, { messageIdentity: group.key }, this.panel);
        }
      } catch (error) {
        if (this.scope === scope && this.active.key === group.key) this.active.error = error?.message || window.PortalI18n.t("回答发送失败，请重试");
      } finally {
        this.sending.delete(key);
        if (this.scope === scope) this.render(this.panel.snapshot() || {});
      }
    }

    // Closing a group leaves it unanswered and tells the Resident nothing; the
    // server records it on the group's answer slot (AnswerResidentRequest cancel).
    async closeGroup(group) {
      const target = this.panel.workspace.target();
      const scope = this.scope;
      const key = `${scope}:${group.key}`;
      if (this.sending.has(key)) return;
      this.sending.add(key);
      this.render(this.panel.snapshot() || {});
      try {
        await window.PortalResidentProtocol.respondUserInput({ resident_id: target.resident_id, message_id: group.source_message_id, action: "cancel" });
        if (this.scope === scope) this.closed.add(group.key);
      } catch (error) {
        if (this.scope === scope) window.PortalUI?.toast?.(error?.message || window.PortalI18n.t("关闭失败，请重试"), "error");
      } finally {
        this.sending.delete(key);
        if (this.scope === scope) this.render(this.panel.snapshot() || {});
      }
    }

    // A native request takes its answer through the Execution's control channel;
    // ``status`` reads whether the Provider has taken it.
    async respondNative(target, scope, sourceMessageId, payload) {
      let result = await window.PortalResidentProtocol.respondUserInput({ resident_id: target.resident_id, message_id: sourceMessageId, ...payload });
      for (let attempt = 0; result.status === "pending" && attempt < 60; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 500));
        if (this.scope !== scope) return null;
        result = await window.PortalResidentProtocol.respondUserInput({ resident_id: target.resident_id, message_id: sourceMessageId, action: "status" });
      }
      return result;
    }

    async sendNativeQuestions(group, action) {
      const { target, scope } = this.active;
      const key = `${scope}:${group.key}`;
      if (this.sending.has(key)) return;
      const progress = this.progressOf(group);
      const answers = action === "cancel" ? {} : Object.fromEntries(group.questions.map(question => [question.id, { answers: [progress.answers[question.id]] }]));
      this.sending.add(key);
      this.render(this.panel.snapshot() || {});
      try {
        const result = await this.respondNative(target, scope, group.source_message_id, { action, answers });
        if (!result) return;
        if (result.status === "expired" || (result.status === "cancelled" && action !== "cancel")) {
          this.nativeOutcomes.set(group.key, { status: "expired", answers: {} });
        }
        if (!(result.status === "answered" || (result.status === "cancelled" && action === "cancel"))) throw new Error(result.error || "提交尚未确认；可重试查询，请勿改选后重复提交。");
        this.nativeOutcomes.set(group.key, { status: action === "cancel" ? "cancelled" : "answered", answers });
        this.progress.delete(group.key);
        this.saveProgress();
        this.panel.protocol?.load?.({ force: true });
      } catch (error) {
        if (this.active.key === group.key) this.active.error = error?.message || window.PortalI18n.t("回答发送失败，请重试");
      } finally {
        this.sending.delete(key);
        if (this.scope === scope) this.render(this.panel.snapshot() || {});
      }
    }

    renderInteractions(messages, hosts, target, scope, canSend) {
      const questions = [];
      const nativeResults = new Map([...messages.values()].flatMap(message => (message.content_parts || [])
        .filter(part => part.type === "tool_result" && nativeInteractionNames.has(part.tool_name))
        .map(part => [part.request_id, part])));
      for (const message of messages.values()) {
        if (message.role !== "agent") continue;
        for (const part of message.content_parts || []) {
          if (part.type !== "tool_call" || !part.interaction || !nativeInteractionNames.has(part.tool_name)) continue;
          const result = nativeResults.get(part.request_id)
            || (part.status !== "pending" ? part : this.nativeOutcomes.get(part.request_id) || part);
          const titles = { command: "运行命令审批", file_change: window.PortalI18n.t("文件修改审批"), permissions: window.PortalI18n.t("额外权限审批"), mcp: window.PortalI18n.t("工具需要补充信息") };
          questions.push({ title: titles[part.interaction.kind], native: part, key: part.request_id,
            source_message_id: message.message_id, accepted: result.status !== "pending", state: result.status });
        }
      }
      const visible = questions.filter(question => hosts.has(question.key));
      const visibleIds = new Set(visible.map(question => question.key));
      for (const [key, card] of this.cards) {
        if (visibleIds.has(key)) continue;
        card.form.remove();
        this.cards.delete(key);
      }
      for (const question of visible) {
        let card = this.cards.get(question.key);
        if (!card) {
          card = this.createInteractionCard(question, target, scope);
          this.cards.set(question.key, card);
        }
        const host = hosts.get(question.key);
        if (card.form.parentElement !== host) host.append(card.form);
        card.question = question;
        card.canSend = canSend;
        // A later authoritative terminal result supersedes a delivery/check
        // error from the original response; it must not invite another answer.
        if (question.accepted) card.error = "";
        const sending = this.sending.has(`${scope}:${question.key}`);
        card.fields.hidden = question.accepted;
        for (const field of card.form.querySelectorAll("input, select, textarea, button")) field.disabled = !canSend || sending || question.accepted;
        card.form.setAttribute("aria-busy", String(sending));
        card.form.classList.toggle("answered", question.accepted);
        card.status.textContent = card.error || ({ answered: window.PortalI18n.t("已送达原请求"), declined: window.PortalI18n.t("已拒绝"), cancelled: window.PortalI18n.t("已取消"), expired: window.PortalI18n.t("请求已失效，不能再提交") }[question.state])
          || (!canSend ? window.PortalI18n.t("只有拥有发送权限的用户可以回答。") : !question.native.is_blocking ? window.PortalI18n.t("Agent 继续工作；请求结束后将不可回答。") : question.native.interaction.kind === "mcp" ? window.PortalI18n.t("等待你的回答") : window.PortalI18n.t("等待你的审批"));
      }
    }

    createInteractionCard(question, target, scope) {
      const interaction = question.native.interaction;
      const request = interaction.request;
      const form = document.createElement("form");
      form.className = "residentQuestionCard";
      form.dataset.questionId = question.key;
      const title = document.createElement("div");
      title.className = "residentQuestionTitle";
      title.textContent = question.title;
      const description = document.createElement("p");
      description.textContent = interaction.kind === "mcp" ? `${request.serverName}：${request.message}` : request.reason || window.PortalI18n.t("请核对操作范围。允许仅针对本次操作；额外权限仅授予本轮。");
      const fields = document.createElement("div");
      const inputs = [];
      if (interaction.kind !== "mcp") {
        const details = document.createElement("pre");
        details.className = "nativeApprovalDetails";
        details.textContent = JSON.stringify(Object.fromEntries(Object.entries(request).filter(([key]) =>
          ["command", "cwd", "permissions", "grantRoot", "networkApprovalContext", "additionalPermissions", "reason"].includes(key))), null, 2);
        fields.append(details);
        if (interaction.kind === "file_change") {
          const scope = interaction.file_change;
          const changes = scope && scope.thread_id === request.threadId && scope.turn_id === request.turnId
            && scope.item_id === request.itemId && Array.isArray(scope.changes) ? scope.changes : [];
          if (!changes.length) {
            const unavailable = document.createElement("p");
            unavailable.className = "nativeApprovalScopeMissing";
            unavailable.textContent = window.PortalI18n.t("Provider 未提供可核对的文件改动详情。");
            fields.append(unavailable);
          }
          for (const change of changes) {
            const file = document.createElement("section");
            file.className = "nativeApprovalFile";
            const heading = document.createElement("div");
            heading.className = "nativeApprovalFilePath";
            const kind = change.kind?.type;
            const operation = { add: "新增", delete: "删除", update: "修改" }[kind];
            heading.textContent = `${operation ? window.PortalI18n.t(operation) + " · " : ""}${change.path}`;
            if (change.kind?.move_path) heading.textContent += ` → ${change.kind.move_path}`;
            const diff = document.createElement("pre");
            diff.className = "nativeApprovalDetails";
            diff.textContent = typeof change.diff === "string" ? change.diff : window.PortalI18n.t("Provider 未提供此文件的差异内容。");
            file.append(heading, diff);
            fields.append(file);
          }
        }
      } else if (request.mode === "url") {
        const link = document.createElement("a");
        link.textContent = window.PortalI18n.t("在新窗口完成工具请求");
        try {
          const url = new URL(request.url);
          if (url.protocol === "https:" && !url.username && !url.password) link.href = url.href;
        } catch (_) { link.removeAttribute("href"); }
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        fields.append(link);
      } else {
        for (const [name, schema] of Object.entries(request.requestedSchema?.properties || {})) {
          const label = document.createElement("label");
          label.className = "residentMcpField";
          const heading = document.createElement("span");
          heading.textContent = schema.title || name;
          const alternatives = schema.oneOf || schema.anyOf || (schema.enum || []).map(value => ({ const: value, title: String(value) }));
          const options = schema.type === "array" ? (schema.items?.anyOf || schema.items?.oneOf || (schema.items?.enum || []).map(value => ({ const: value, title: String(value) }))) : alternatives;
          const field = document.createElement(options.length ? "select" : "input");
          field.setAttribute("aria-label", schema.title || name);
          field.name = name;
          field.required = (request.requestedSchema.required || []).includes(name);
          if (options.length) {
            field.multiple = schema.type === "array";
            if (!field.multiple) field.append(new Option("请选择…", ""));
            for (const option of options) field.append(new Option(option.title || String(option.const), JSON.stringify(option.const)));
          } else {
            field.type = schema.type === "boolean" ? "checkbox" : ["number", "integer"].includes(schema.type) ? "number" : "text";
            if (field.type === "number") field.step = schema.type === "integer" ? "1" : "any";
            if (schema.minimum != null) field.min = schema.minimum;
            if (schema.maximum != null) field.max = schema.maximum;
            if (schema.minLength != null) field.minLength = schema.minLength;
            if (schema.maxLength != null) field.maxLength = schema.maxLength;
            if (schema.type === "boolean") field.required = false;
          }
          if (schema.default != null) {
            if (schema.type === "boolean") field.checked = schema.default;
            else if (!field.multiple) field.value = options.length ? JSON.stringify(schema.default) : String(schema.default);
          }
          label.append(heading, field);
          if (schema.description) { const hint = document.createElement("small"); hint.textContent = schema.description; label.append(hint); }
          fields.append(label);
          inputs.push({ name, schema, field, options });
        }
      }
      const footer = document.createElement("div");
      footer.className = "residentQuestionFooter";
      const status = document.createElement("div");
      status.className = "residentQuestionStatus";
      status.setAttribute("role", "status");
      footer.append(status);
      const card = { form, fields, status, question, canSend: false, error: "" };
      card.content = () => {
        if (interaction.kind !== "mcp" || request.mode === "url") return null;
        return Object.fromEntries(inputs.filter(({ field, schema }) => schema.type === "boolean" || field.value !== "").map(({ name, schema, field, options }) => [name,
          field.multiple ? [...field.selectedOptions].map(option => JSON.parse(option.value)) : options.length ? JSON.parse(field.value) :
          schema.type === "boolean" ? field.checked : ["number", "integer"].includes(schema.type) ? Number(field.value) : field.value]));
      };
      for (const [action, text] of [["decline", "拒绝"], ["cancel", "取消请求"], ["accept", interaction.kind === "mcp" ? "提交" : "允许本次"]]) {
        if (Array.isArray(request.availableDecisions) && !request.availableDecisions.includes(action)) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.className = `portalButton${action === "accept" ? " is-primary" : ""}`;
        button.textContent = text;
        button.addEventListener("click", () => {
          if (action === "accept" && !form.reportValidity()) return;
          void this.sendNativeInteraction(card, target, scope, action);
        });
        footer.append(button);
      }
      form.addEventListener("submit", event => event.preventDefault());
      form.append(title, description, fields, footer);
      return card;
    }

    async sendNativeInteraction(card, target, scope, action) {
      const key = `${scope}:${card.question.key}`;
      if (!card.canSend || card.question.accepted || this.sending.has(key)) return;
      this.sending.add(key);
      card.error = "";
      this.render(this.panel.snapshot() || {});
      try {
        const result = await this.respondNative(target, scope, card.question.source_message_id, {
          action, content: action === "accept" ? card.content() : null,
        });
        if (!result) return;
        if (!["answered", "declined", "cancelled", "expired"].includes(result.status)) throw new Error(result.error || "提交尚未确认；可重试查询，请勿改选后重复提交。");
        const outcome = result.status === "answered"
          ? (action === "cancel" ? "cancelled" : action === "decline" ? "declined" : "answered")
          : result.status;
        this.nativeOutcomes.set(card.question.key, { status: outcome, answers: {} });
        card.question.accepted = true;
        this.panel.protocol?.load?.({ force: true });
      } catch (error) {
        card.error = error?.message || "回答发送失败，请重试";
      } finally {
        this.sending.delete(key);
        if (this.scope === scope) this.render(this.panel.snapshot() || {});
      }
    }

    destroy() {
      this.reset();
      this.releaseTurnEditors(true);
    }

    releaseTurnEditors(all = false) {
      for (const [root, editor] of this.turnEditors) {
        if (all || !root.isConnected) { editor?.destroy?.(); this.turnEditors.delete(root); }
      }
    }

    bindTurnArtifacts(messages, target, canSend) {
      this.releaseTurnEditors();
      for (const host of this.panel.workspace.els.messages.querySelectorAll("[data-native-turn]")) {
        const source = messages.get(host.dataset.nativeTurn);
        const part = source?.content_parts?.find(candidate => ["native_turn", "resource_changes"].includes(candidate.type));
        if (!part) continue;
        const review = host.querySelector("[data-turn-review]");
        if (review) review.disabled = !canSend || Boolean(part.diff_truncated) || review.dataset.sending === "1";
        if (host.dataset.bound) continue;
        host.dataset.bound = "1";
        const details = host.querySelector("details");
        const root = host.querySelector("[data-turn-diff]");
        const fileSelect = host.querySelector("[data-turn-diff-file]");
        const preview = host.querySelector("[data-turn-file-preview]");
        let files = [];
        let activeEditor = null;
        const selectedFile = () => fileSelect?.value !== "" ? files[Number(fileSelect?.value)] : null;
        const selectedDiff = () => fileSelect?.value ? files[Number(fileSelect.value)]?.diff || part.diff : part.diff;
        const showDiff = () => {
          const value = selectedDiff();
          root.style.height = `${Math.min(380, Math.max(96, value.split("\n").length * 19 + 20))}px`;
          fileSelect.title = fileSelect.selectedOptions[0]?.textContent || window.PortalI18n.t("本轮全部修改");
          const file = selectedFile();
          preview.disabled = !file?.path || file.deleted;
          preview.title = file?.deleted ? window.PortalI18n.t("文件已删除，仅可查看 Diff") : file?.path ? window.PortalI18n.t("在 Preview 中打开当前文件") : window.PortalI18n.t("请先选择一个文件");
          if (activeEditor) activeEditor.setValue(value);
          else root.textContent = value;
        };
        fileSelect?.addEventListener("change", showDiff);
        preview?.addEventListener("click", async () => {
          const file = selectedFile();
          if (!file?.path || file.deleted || preview.disabled) return;
          preview.disabled = true;
          try {
            const open = this.panel.workspace.options.onLinkPreview || requestWorkspacePreview;
            const opened = await open({
              href: file.path, path: file.path, title: file.path, node: preview,
              resourceAddress: file.resourceAddress || "",
              source: "chat", target: this.panel.workspace.els.messages,
            });
            if (opened === false) throw new Error("当前页面没有可用的 Preview");
          } catch (error) { this.panel.reportError(error); }
          finally { if (preview.isConnected) showDiff(); }
        });
        details?.addEventListener("toggle", async () => {
          if (!details.open || this.turnEditors.has(root)) return;
          this.turnEditors.set(root, null);
          try {
            if (!window.PortalCodeEditor) {
              window.portalTurnEditorLoading ||= new Promise((resolve, reject) => {
                const script = document.createElement("script");
                script.src = "/static/portal_code_editor.js";
                script.charset = "utf-8";
                script.onload = resolve; script.onerror = reject; document.head.append(script);
              });
              await window.portalTurnEditorLoading;
            }
            if (!root.isConnected) return;
            files = window.PortalCodeEditor.splitDiffFiles(part.diff);
            if (part.type === "resource_changes") {
              // Resource diffs have one block per write, in changes order. Their
              // address@change_id labels identify snapshots, not openable files.
              files = files.map((file, index) => {
                const change = part.changes?.[index];
                return { ...file, label: change?.path || change?.address || file.label,
                  path: change?.address || "", resourceAddress: change?.address || "" };
              });
            }
            fileSelect.replaceChildren(...(files.length > 1 ? [new Option(`全部修改（${files.length} 个文件）`, "")] : []), ...files.map((file, index) => new Option(file.label, String(index))));
            fileSelect.parentElement.hidden = !files.length;
            showDiff();
            root.textContent = "";
            const editor = await window.PortalCodeEditor.mount({ root, value: selectedDiff(), language: "diff", readOnly: true, ariaLabel: window.PortalI18n.t("本轮修改 Diff") });
            if (!root.isConnected) { editor.destroy(); return; }
            activeEditor = editor;
            this.turnEditors.set(root, editor);
          } catch (_) { root.textContent = selectedDiff(); }
          finally { fileSelect.disabled = false; }
        });
        review?.addEventListener("click", async () => {
          if (review.disabled) return;
          review.dataset.sending = "1"; review.disabled = true;
          try {
            host.dataset.reviewMessage ||= `msg_${globalThis.PortalUIPrimitives.randomUUID().replaceAll("-", "")}`;
            await window.PortalResidentProtocol.sendInput({ resident_id: target.resident_id, message: {
              message_id: host.dataset.reviewMessage, role: "user", content_parts: [{ type: "turn_review", segment_id: part.segment_id }],
            } });
            review.textContent = window.PortalI18n.t("审查已排队");
          } catch (error) { review.dataset.sending = ""; review.disabled = !canSend; review.textContent = window.PortalI18n.t("重试审查"); review.title = error.message; }
        });
      }
    }
  }

  function residentMessageBody(item, context = {}) {
    const subagent = item.content_parts?.find(part => part.type === "subagent_run");
    if (subagent && messageRole(item) !== "human") return subagentRunCard(item, subagent, context);
    const turn = item.content_parts?.find(part => ["native_turn", "resource_changes"].includes(part.type));
    if (turn && messageRole(item) !== "human") {
      const display = turnArtifactDisplay(turn);
      if (!display.hasContent) return window.PortalI18n.html`<small class="nativeTurnEmpty">${escapeHtml(display.title)}</small>`;
      const labels = { pending: window.PortalI18n.t("待处理"), inProgress: window.PortalI18n.t("进行中"), completed: window.PortalI18n.t("已完成") };
      return window.PortalI18n.html`<section class="residentQuestionCard nativeTurnCard chatSystemCard" data-native-turn="${escapeHtml(item.id)}">
        <header class="nativeTurnHeader"><div class="nativeTurnIdentity"><div class="residentQuestionTitle">${escapeHtml(display.title)}</div><span class="messageSystemBadge">执行记录</span></div>${turn.diff ? window.PortalI18n.html`<button type="button" class="portalButton" data-turn-review title="审查本轮全部修改">审查这些修改</button>` : ""}</header>
        ${turn.type === "resource_changes" ? window.PortalI18n.html`<ul class="resourceTurnChanges">${(turn.changes || []).map(change => {
          const version = snapshot => snapshot?.number ? `v${snapshot.number}` : "";
          const before = version(change.before_version) || String(change.before_sha256 || "").slice(0, 8);
          const after = version(change.after_version) || String(change.after_sha256 || "").slice(0, 8);
          return window.PortalI18n.html`<li>${escapeHtml(change.address)} <small>${escapeHtml(before)} → ${escapeHtml(after)}</small></li>`;
        }).join("")}</ul>` : ""}
        ${turn.plan_text ? context.renderContent(turn.plan_text) : ""}
        ${turn.explanation ? window.PortalI18n.html`<p>${escapeHtml(turn.explanation)}</p>` : ""}
        ${turn.plan?.length ? window.PortalI18n.html`<ol>${turn.plan.map(step => window.PortalI18n.html`<li data-step-status="${escapeHtml(step.status)}"><span>${escapeHtml(labels[step.status] || step.status)}</span> ${escapeHtml(step.step)}</li>`).join("")}</ol>` : ""}
        ${turn.diff ? window.PortalI18n.html`<details><summary>查看本轮修改${turn.diff_truncated ? window.PortalI18n.t("（已截断）") : ""}</summary><div class="nativeTurnDiffToolbar" hidden><select data-turn-diff-file aria-label="修改文件" disabled></select><button type="button" class="portalButton is-icon is-quiet" data-turn-file-preview aria-label="在 Preview 中打开当前文件" title="请先选择一个文件" disabled>${window.PortalUIPrimitives.iconHtml("external-link")}</button></div><div data-turn-diff>${escapeHtml(turn.diff)}</div></details>` : ""}
        </section>`;
    }
    if (messageRole(item) !== "human" && item.content_parts?.some(part => nativeInteractionNames.has(part.tool_name))) {
      return item.content_parts.map(part => {
        if (!nativeInteractionNames.has(part.tool_name)) return "";
        if (part.type === "tool_call" && part.interaction) return window.PortalI18n.html`<div class="residentQuestionHost" data-resident-question="${escapeHtml(part.request_id)}"></div>`;
        if (part.type === "tool_call") return window.PortalI18n.html`<div class="residentQuestionHost" data-resident-question="${escapeHtml(part.request_id)}"></div>`;
        const label = { answered: window.PortalI18n.t("回答已送达"), cancelled: window.PortalI18n.t("请求已取消"), expired: window.PortalI18n.t("请求已失效") }[part.status] || window.PortalI18n.t("请求已结束");
        const answers = Object.values(part.answers || {}).flatMap(answer => answer.answers || []).join("；");
        return window.PortalI18n.html`<div class="residentQuestionStatus">${escapeHtml(label)}${answers ? `：${escapeHtml(answers)}` : ""}</div>`;
      }).join("");
    }
    if (messageRole(item) !== "human" && item.content_parts?.some(part => part.type === "async_questions")) {
      return item.content_parts.map(part => {
        if (part.type === "text") return context.renderContent(part.text || "");
        if (part.type !== "async_questions") return "";
        return window.PortalI18n.html`<div class="residentQuestionHost" data-resident-question="${escapeHtml(part.answer_message_id || "")}"></div>`;
      }).join("");
    }
    if (outwardToResidents(item)) return outwardRecordHtml(item, context.bodyHtml || "");
    if (item.expanded_members?.length) return mailboxGroupBody(item.expanded_members, context);
    const parsed = messageRole(item) === "human" ? parseForwardReference(context.content || "") : null;
    if (!parsed) return context.bodyHtml || "";
    const render = context.renderContent;
    const reference = parsed.entries?.length
      ? parsed.entries.map(entry => window.PortalI18n.html`<section class="forwardReferenceEntry"><div class="forwardReferenceSpeaker">${escapeHtml(entry.speaker)}</div><div class="forwardReferenceEntryContent markdownBody">${render(entry.content)}</div></section>`).join("")
      : render(parsed.reference);
    return window.PortalI18n.html`<div class="forwardReferenceMessage"><details class="forwardReferenceDetails" data-message-disclosure="forward-reference"><summary><span>转发参考</span><span class="forwardReferenceHint"><span class="forwardReferenceExpand">展开</span><span class="forwardReferenceCollapse">收起</span></span></summary><div class="forwardReferenceBody markdownBody">${reference}</div></details>${parsed.note ? window.PortalI18n.html`<div class="forwardReferenceNote">${render(parsed.note)}</div>` : ""}</div>`;
  }

  // A Family facade Conversation reads like any Resident's (decision_44cf587c1721):
  // requests keep their sender, and member replies are the facade's replies.
  // Replies are projected from the member's Conversation, never copied.
  function familyRelayRoute(value) {
    const names = items => (Array.isArray(items) ? items : [])
      .map(item => String(item?.title || item?.resident_id || "")).filter(Boolean);
    const via = names(value?.via), moved = names(value?.reassigned_from);
    return (via.length ? `（经 ${via.join(" → ")}）` : "") + (moved.length ? `（改派自 ${moved[moved.length - 1]}）` : "");
  }

  // What a facade's Stop stops: every request the Family is executing - run by a
  // member or still being routed - and, for the confirmation, what stays queued.
  function familyExecution(snapshot) {
    const requests = (Array.isArray(snapshot?.messages) ? snapshot.messages : [])
      .filter(message => message?.role === "user" && message.relay && typeof message.relay === "object" && message.message_id)
      .map(message => ({ message_id: String(message.message_id), state: String(message.relay.state || ""),
        stopping: message.relay.work_state === "stopping",
        member: String(message.relay.title || message.relay.resident_id || "成员") }));
    return {
      active: requests.filter(request => request.state === "routing" || (request.state === "running" && !request.stopping)),
      waiting: requests.filter(request => ["waiting_members", "scope_review", "quota_review", "delivering", "queued"].includes(request.state)).length,
    };
  }

  // A facade request given to a member can be reassigned to another (decision_44cf587c1721);
  // the members open from one 改派 item, beside it, like a bookmark folder's.
  function familyReassignMenuItems(item, family) {
    const current = String(item?.relay?.resident_id || "");
    if (messageRole(item) !== "human" || !current) return "";
    const members = (Array.isArray(family?.members) ? family.members : [])
      .filter(member => member?.resident_id && member.resident_id !== current)
      .map(member => window.PortalI18n.html`<button type="button" role="menuitem" data-portal-message-action="family-reassign" data-family-member="${escapeHtml(member.resident_id)}">${escapeHtml(member.title || member.resident_id)}</button>`)
      .join("");
    return members ? messageSubmenuHtml("family-reassign", window.PortalI18n.t("改派"), members) : "";
  }

  // Each reply follows the request it answers; the request's progress shows
  // where any Resident shows its work, below the messages.  The Conversation
  // answers one request at a time, so the member is named once, on the last
  // reply to a request, not on each intermediate output; while the request is
  // open its status block names the member.
  function familyReplyFooter(item, index = -1, items = []) {
    const reply = item && typeof item.family_reply === "object" ? item.family_reply : null;
    if (!reply || item.family_status) return "";
    const request = String(reply.request_message_id || "");
    const next = index >= 0 && Array.isArray(items) ? items[index + 1] : null;
    if (request && String(next?.family_reply?.request_message_id || "") === request) return "";
    const title = String(reply.title || reply.resident_id || window.PortalI18n.t("成员"));
    const source = messageRole(item) === "agent" ? `由 ${title} 回复` : `来自 ${title}`;
    const residentId = String(reply.resident_id || "");
    const messageId = String(reply.message_id || item.message_id || item.id || "");
    const label = escapeHtml(`${source}${familyRelayRoute(reply)}`);
    const href = `/lineage?${new URLSearchParams({ resident: residentId, message: messageId, open_chat: "1" })}`;
    const sourceLink = residentId && messageId
      ? window.PortalI18n.html`<a class="messageMetaLink" href="${escapeHtml(href)}" data-family-reply-source title="在成员对话中定位这条回复">${label}</a>`
      : label;
    return window.PortalI18n.html`<div class="familyRelay familyReply" data-family-reply-member="${escapeHtml(residentId)}"><div class="familyRelayStatus">${sourceLink}</div></div>`;
  }

  function heldInfoFooter(item) {
    const released = Boolean(item?.released_with);
    if (!released && item?.held_info !== true) return "";
    // A Family facade hands its infos to the steward at once (decision_44cf587c1721); a delivered
    // info whose request is not shown here was read with that request, not on its own.
    const status = item.held_info_steward
      ? "背景更新 · 已交给 steward"
      : released ? "背景更新 · 已作为背景随一条请求送达，没有单独打断工作" : "背景更新 · 随下一条任务送达";
    return window.PortalI18n.html`<div class="familyRelay heldInfo" data-held-info="${released ? "released" : "true"}"><div class="familyRelayStatus">${status}</div></div>`;
  }

  // A time inside a message reads like the message's own: local and compact.
  function footerTime(value) {
    return window.PortalUI?.formatDateTime?.(value, { style: "compact" }) || String(value || "");
  }

  // Infos delivered with a request were read as one input with it: shown inside it, collapsed (concept_43c793b4f3ab).
  function attachedInfosFooter(item) {
    const infos = Array.isArray(item?.attached_infos) ? item.attached_infos : [];
    if (!infos.length) return "";
    const rows = infos.map(info => {
      const text = (Array.isArray(info?.content_parts) ? info.content_parts : [])
        .filter(part => part && part.type === "text").map(part => String(part.text || "")).join("\n").trim();
      const authorId = String(info?.origin?.sender?.id || info?.author_id || "").trim();
      const author = /^resident_[0-9a-f]{12}$/.test(authorId) ? resourceChips.resourceLabelHtml(authorId) : escapeHtml(authorId);
      const time = footerTime(info?.created_at);
      return window.PortalI18n.html`<div class="attachedInfo" data-attached-info="${escapeHtml(String(info?.message_id || ""))}"><div class="attachedInfoMeta">${author}${time ? ` · ${escapeHtml(time)}` : ""}</div><div class="attachedInfoText">${escapeHtml(text.slice(0, 4000))}</div></div>`;
    }).join("");
    const note = item.attached_infos_steward ? " · 已交给 steward" : "";
    return window.PortalI18n.html`<details class="familyRelay heldInfo attachedInfos" data-message-disclosure="attached-infos"><summary class="familyRelayStatus">附带背景更新 ${infos.length} 条${note}</summary>${rows}</details>`;
  }

  function memberText(message) {
    return (Array.isArray(message?.content_parts) ? message.content_parts : [])
      .filter(part => part && part.type === "text").map(part => String(part.text || "")).join("\n").trim();
  }

  // A Mailbox group's body is its members (decision_b5d349190743), each with
  // its own sender, time and text; a long one opens from a one-line preview.
  function mailboxGroupBody(members, context) {
    const rows = members.map(member => {
      const id = escapeHtml(String(member?.message_id || ""));
      const text = memberText(member);
      const authorId = String(member?.origin?.sender?.id || member?.author_id || "").trim();
      const author = /^resident_[0-9a-f]{12}$/.test(authorId) ? resourceChips.resourceLabelHtml(authorId) : escapeHtml(authorId);
      const time = footerTime(member?.created_at);
      const meta = window.PortalI18n.html`<span class="mailboxMemberMeta">${author}${time ? ` · ${escapeHtml(time)}` : ""}</span>`;
      const body = window.PortalI18n.html`<div class="mailboxMemberText markdownBody">${context.renderContent(text)}</div>`;
      if (text.length <= 160 && !text.includes("\n")) {
        return window.PortalI18n.html`<section class="mailboxMember" data-mailbox-member="${id}">${meta}${body}</section>`;
      }
      const preview = text.split("\n").find(line => line.trim()) || "";
      return window.PortalI18n.html`<details class="mailboxMember" data-mailbox-member="${id}" data-message-disclosure="mailbox-member:${id}"><summary>${meta}<span class="mailboxMemberPreview">${escapeHtml(preview.slice(0, 200))}</span></summary>${body}</details>`;
    }).join("");
    return window.PortalI18n.html`<div class="mailboxGroupBody" data-mailbox-group="true"><div class="mailboxGroupNote">合并了 ${members.length} 条消息，作为一次输入处理</div>${rows}</div>`;
  }

  // A Mailbox group shows its members in its body; a member points at its group.
  function mailboxFooter(item) {
    if (item?.grouped_into_message_id) {
      return window.PortalI18n.markup('<div class="familyRelay heldInfo" data-mailbox-member="true"><div class="familyRelayStatus">已并入 Mailbox 群体消息</div></div>');
    }
    return "";
  }

  // A message a Resident sends to a user, or a Family sends out as its facade, names
  // its recipients; a Family's message also names the member that sent it (decision_c524c775e0be).
  function recipientsStatus(item) {
    const recipients = (Array.isArray(item?.recipients) ? item.recipients : []).filter(recipient => recipient?.id);
    if (!recipients.length) return null;
    const label = id => (/^resident_[0-9a-f]{12}$/.test(id) ? resourceChips.resourceLabelHtml(id) : escapeHtml(id));
    const names = recipients.map(recipient => {
      const display = item?.recipient_labels?.[recipient.kind]?.[recipient.id];
      return typeof display === "string" && display ? escapeHtml(display) : label(String(recipient.id));
    }).join("、");
    const sender = String(item?.origin?.sender?.id || "");
    const initiator = String(item?.origin?.initiated_by?.id || "");
    const by = /^resident_[0-9a-f]{12}$/.test(initiator) && initiator !== sender ? ` · 由 ${label(initiator)} 发出` : "";
    return { ids: recipients.map(recipient => recipient.id).join(","), html: `发给 ${names}${by}` };
  }

  // The record of a message a Family sent another Resident in its name: in the
  // facade's Conversation it stays one line and opens on demand.
  function outwardToResidents(item) {
    const recipients = (Array.isArray(item?.recipients) ? item.recipients : []).filter(recipient => recipient?.id);
    return messageRole(item) !== "human" && recipients.length > 0 && recipients.every(recipient => recipient.kind === "resident");
  }

  function outwardRecordHtml(item, bodyHtml) {
    const status = recipientsStatus(item);
    return window.PortalI18n.html`<details class="familyRelay outwardMessage outwardRecord" data-message-disclosure="outward-record" data-message-recipients="${escapeHtml(status.ids)}"><summary class="familyRelayStatus">${status.html}</summary><div class="outwardRecordBody markdownBody">${bodyHtml}</div></details>`;
  }

  // A message sent to a user - a member's question, say - is answered from the message
  // itself; the reply quotes it and goes straight to the member that wrote it.
  function recipientsFooter(item) {
    const status = recipientsStatus(item);
    if (!status || outwardToResidents(item)) return "";
    const toUser = item.recipients.some(recipient => recipient?.kind === "user");
    const reply = toUser && messageQuotable(item)
      ? window.PortalI18n.markup('<button class="portalButton is-compact outwardReply" type="button" data-portal-message-action="quote" title="引用这条消息回复">回复</button>')
      : "";
    return window.PortalI18n.html`<div class="familyRelay outwardMessage${reply ? " canReply" : ""}" data-message-recipients="${escapeHtml(status.ids)}"><div class="familyRelayStatus">${status.html}</div>${reply}</div>`;
  }

  function residentMessageFooter(item, index, items) {
    const delivery = item.local_delivery;
    const failed = delivery?.state === "failed";
    const confirming = delivery?.state === "confirming";
    const localStatus = delivery && delivery.state !== "accepted"
      ? window.PortalI18n.html`<div class="messageDeliveryStatus${failed ? " is-failed" : ""}" data-message-delivery="${delivery.state}" role="status">
          <span>${escapeHtml(failed ? window.PortalI18n.text`发送失败：${delivery.error}`
            : confirming ? window.PortalI18n.t("正在确认发送状态") : window.PortalI18n.t("发送中…"))}</span>
          ${failed ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-portal-message-action="submission-retry">${window.PortalI18n.t("重试")}</button><button type="button" class="portalButton is-compact" data-portal-message-action="submission-dismiss">${window.PortalI18n.t("移除")}</button>`
            : confirming ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-portal-message-action="submission-check">${window.PortalI18n.t("检查发送状态")}</button>` : ""}
        </div>` : "";
    return localStatus + attachedInfosFooter(item) + mailboxFooter(item) + heldInfoFooter(item) + familyReplyFooter(item, index, items)
      + recipientsFooter(item) + residentTokenFooter(item);
  }

  // The progress of a Family facade request, after its last shown message:
  // which member has it, its place in that member's queue, the live activity.
  // The sender (or the owner) withdraws it while queued and stops it while it runs.
  function residentMessageAfter(item, index, items, context = {}) {
    const status = item && typeof item.family_status === "object" ? item.family_status : null;
    return status ? requestProgressHtml(status, context) : "";
  }

  // How far one Resident has got with one request: being handed over, its
  // place in the Resident's queue, or the Resident's live work on it
  // (decision_44cf587c1721).  A Chatroom shows the same block, without the
  // withdraw and stop actions, for each Resident a message called
  // (decision_a6bebf4ac283).
  function requestProgressHtml(status, context = {}, { actions: withActions = true } = {}) {
    const state = String(status?.state || "");
    const member = `${String(status.title || status.resident_id || "成员")}${familyRelayRoute(status)}`;
    const phase = String(status.work_phase || "");
    const phaseLabel = PRE_PROVIDER_RUNTIME_PHASES.has(phase) ? RUNTIME_PHASE_STATUS[phase]?.label || "" : "";
    const stopping = status.work_state === "stopping";
    const button = (action, label, unavailable = "", title = "") => window.PortalI18n.html`<button class="portalButton is-compact familyRequestAction" type="button" data-portal-message-action="${action}"${unavailable ? ' disabled data-action-unavailable="true"' : ""}${unavailable || title ? ` title="${escapeHtml(unavailable || title)}"` : ""}>${escapeHtml(label)}</button>`;
    let label = "";
    let actions = "";
    let body = "";
    if (state === "recovery") {
      const labels = {
        selecting: "正在查找原请求", selected: "正在恢复原请求", routing: "正在恢复原请求的转交",
        queued: "原请求已在排队，无需重复启动", running: "原请求正在运行，无需重复启动",
        completed: "原请求已经完成", failed: "恢复未完成", cancelled: "原请求已停止",
        missing: "原请求的执行记录不可用", unavailable: "无法恢复原请求",
        choose: "选择要恢复的原请求，引用后发送 continue。",
      };
      label = window.PortalI18n.t(labels[status.status] || "正在恢复原请求");
      if (status.resident_id) label += ` · ${member}`;
      if (status.error) body += `<div class="paneNote">${escapeHtml(typeof status.error === "string" ? status.error : status.error.message || JSON.stringify(status.error))}</div>`;
      if (status.original_message_id) actions = button("family-recovery-view", "查看原请求");
      body += (status.candidates || []).map(candidate => `<div class="paneNote"><button type="button" class="portalButton is-compact" data-portal-message-action="family-recovery-choose" data-recovery-request="${escapeHtml(candidate.message_id)}">${escapeHtml(candidate.text || candidate.message_id)}</button></div>`).join("");
    } else if (state === "routing") {
      label = RUNTIME_PHASE_STATUS.family_routing.label;
      // Stopped while being routed, the request is withdrawn before any member gets it.
      actions = button("family-request-withdraw", "停止");
    } else if (state === "waiting_members") {
      label = "等待 Family 恢复成员";
      actions = button("family-request-withdraw", "撤回");
    } else if (state === "scope_review") {
      // No member's duties cover it: the steward decides who takes it, maybe a new member.
      label = "不在现有成员职责内，steward 正在安排成员";
      actions = button("family-request-withdraw", "撤回");
    } else if (state === "quota_review") {
      label = "steward 正在评估成员额度与任务强度";
    } else if (state === "delivering") {
      label = `正在交给 ${member}`;
    } else if (state === "queued") {
      const ahead = Number(status.ahead);
      const place = status.ahead === undefined || !Number.isFinite(ahead) ? "" : ahead > 0 ? `（前面还有 ${ahead} 条）` : "（下一条处理）";
      label = phaseLabel ? `已交给 ${member} · ${phaseLabel}` : `已交给 ${member} · 排队中${place}`;
      // Disputing the Router's choice names the member that should take it, and why.
      actions = button("family-request-inject", "立即注入",
        status.resident_id && status.message_id ? "" : "等待确认承接成员的排队消息",
        "当前工作可接收时立即注入，否则排到下一轮；暂停接单时仍会等待")
        + button("family-request-correct", "纠正 Router") + button("family-request-withdraw", "撤回");
    } else if (state === "running") {
      label = stopping ? `${member} · ${RUNTIME_PHASE_STATUS.stopping.label}` : phaseLabel ? `${member} · ${phaseLabel}` : `${member} 运行中`;
      actions = stopping ? "" : button("family-request-stop", "停止");
      body = phaseLabel ? "" : runtimeDetailsHtml(Array.isArray(status.activities) ? status.activities : [], {
        live: !stopping,
        formatTime: value => window.PortalUI?.formatDateTime?.(value, { style: "compact" }) || String(value || ""),
        assistantName: String(status.title || "成员"),
        emptyLiveHtml: window.PortalI18n.html`<div class="paneNote">${escapeHtml(member)} 已开始处理，等待活动事件。</div>`,
      });
    } else return "";
    const keyAttrs = context.key ? ` data-message-key="${escapeHtml(context.key)}" data-message-after-of="${escapeHtml(context.anchorKey || "")}"` : "";
    const residentAttr = status.resident_id ? ` data-request-resident="${escapeHtml(String(status.resident_id))}"` : "";
    const labelHtml = status.resident_id
      ? `<a class="messageMetaLink" data-family-request-source href="/lineage?${escapeHtml(new URLSearchParams({ resident: String(status.resident_id), open_chat: "1" }).toString())}" title="${escapeHtml(window.PortalI18n.t("打开执行成员的对话"))}">${escapeHtml(label)}</a>`
      : escapeHtml(label);
    return window.PortalI18n.html`<article class="messageItem agent liveRuntime familyRequestStatus"${keyAttrs}${residentAttr} data-family-request="${escapeHtml(String(status.request_message_id || ""))}" data-family-request-state="${escapeHtml(stopping ? "stopping" : state)}">
      <div class="messageHead"><span class="messageTitle familyRequestLabel">${labelHtml}</span><span class="messageHeadRight">${withActions ? actions : ""}</span></div>
      ${body ? `<div class="familyRequestActivity"${context.workCollapsed ? " hidden" : ""}>${body}</div>` : ""}
    </article>`;
  }

  function formatTokenCount(value) {
    const count = Number(value || 0);
    if (!Number.isFinite(count) || count <= 0) return "0";
    if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M`;
    if (count >= 1000) return `${(count / 1000).toFixed(1)}K`;
    return String(Math.round(count));
  }

  function residentTokenFooter(item) {
    return [
      item.token_usage,
    ].map(usage => {
      if (!usage) return "";
      const parts = [["total_tokens", ""], ["input_tokens", "in "], ["output_tokens", "out "], ["reasoning_output_tokens", "reason "]]
        .filter(([key]) => Number(usage[key]) > 0).map(([key, label]) => `${label}${formatTokenCount(usage[key])}`);
      return parts.length ? window.PortalI18n.html`<div class="messageTokenNote">${escapeHtml(`tokens ${parts.join(" · ")}`)}</div>` : "";
    }).join("");
  }

  class ResidentReadObserver {
    constructor(panel) {
      this.panel = panel;
      this.root = panel.workspace.els.messages;
      this.observation = null;
      this.frame = 0;
      this.pending = false;
      this.confirmAfterPending = false;
      this.retryAfter = 0;
      this.retryTimer = 0;
      this.confirmed = null;
      this.disposed = false;
      this.schedule = () => {
        if (this.disposed || this.frame) return;
        this.frame = window.requestAnimationFrame(() => {
          this.frame = 0;
          this.confirm();
        });
      };
      this.resume = () => {
        this.retryAfter = 0;
        this.schedule();
      };
      this.deferRetry = () => {
        this.retryAfter = Date.now() + 5000;
        if (!this.retryTimer) this.retryTimer = window.setTimeout(() => {
          this.retryTimer = 0;
          this.schedule();
        }, 5000);
      };
      this.unsubscribe = window.PortalConversationStore?.subscribe?.(() => {
        this.schedule();
      });
      this.resizeObserver = new ResizeObserver(this.schedule);
      this.resizeObserver.observe(this.root);
      document.addEventListener("visibilitychange", this.resume);
      window.addEventListener("focus", this.resume);
    }

    update(snapshot) {
      // A conversation view that projects the snapshot differently decides when it is presented.
      const presented = this.panel.conversationView()?.readReceipt?.(snapshot) !== false;
      const observation = snapshot && presented ? {
        residentId: this.panel.target().resident_id,
        cursor: Number(snapshot.cursor || 0),
        unread: snapshot.viewer_state?.unread === true,
      } : null;
      if (observation?.residentId !== this.observation?.residentId) this.confirmed = null;
      this.observation = observation;
      this.schedule();
    }

    confirm() {
      if (this.pending) {
        this.confirmAfterPending = true;
        return;
      }
      if (this.disposed || !this.observation || document.hidden || this.panel.presented === false
        || !this.root.isConnected || this.panel.pendingProtocol
        || this.panel.workspace.isLoading() || Date.now() < this.retryAfter
        || this.observation.residentId !== this.panel.target().resident_id) return;
      if (this.root.checkVisibility && !this.root.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return;
      const store = window.PortalConversationStore;
      if (!store?.observeResidentRead) return;
      const residentId = this.observation.residentId;
      const cursor = this.observation.cursor;
      const actor = String(window.PortalIdentity?.username?.() || "").trim();
      if (!actor) return;
      const item = store.entry?.(`resident:${residentId}`);
      const revision = Number(item?.read_revision || 0);
      if (this.confirmed?.residentId === residentId
        && this.confirmed.actor === actor
        && !item?.unread
        // HATG can present a new completion without loading its inbox entry.
        // Only reuse a receipt for the same unread presentation; an old or
        // absent index entry cannot suppress the next completion's receipt.
        && (!this.observation.unread || this.confirmed.cursor === cursor)
        && this.confirmed.revision >= revision) return;
      this.pending = true;
      Promise.resolve(store.observeResidentRead({
        residentId,
        actor,
      })).then(payload => {
        if (!payload) {
          this.deferRetry();
          return;
        }
        this.confirmed = {
          residentId,
          actor,
          cursor,
          revision: Math.max(revision, Number(payload?.item?.read_revision || 0)),
        };
        this.retryAfter = 0;
        if (this.retryTimer) window.clearTimeout(this.retryTimer);
        this.retryTimer = 0;
      }).catch(error => {
        this.deferRetry();
        console.warn("Resident read receipt failed", error);
      }).finally(() => {
        this.pending = false;
        if (this.confirmAfterPending) {
          this.confirmAfterPending = false;
          this.schedule();
        }
      });
    }

    destroy() {
      this.disposed = true;
      if (this.frame) window.cancelAnimationFrame(this.frame);
      if (this.retryTimer) window.clearTimeout(this.retryTimer);
      this.unsubscribe?.();
      this.resizeObserver.disconnect();
      document.removeEventListener("visibilitychange", this.resume);
      window.removeEventListener("focus", this.resume);
      this.observation = null;
    }
  }

  // The same Family view is opened from Chat and from a node's menu.
  function createFamilyStatusView(residentId, anchor, onSummary = null, getFamily = null, container = null) {
    const root = document.createElement("section");
    root.className = `familyStatusPanel portalHatgOverlayComponent${container ? " is-inline" : ""}`;
    root.hidden = true;
    root.setAttribute("role", container ? "region" : "dialog");
    root.setAttribute("aria-label", window.PortalI18n.t("Family 状态"));
    (container || document.body).append(root);
    const rows = new Map();
    let accounts = [], family = {}, quotaError = "", disposed = false, loading = false, reloadRequested = false;

    const popoverOptions = { trigger: anchor, panel: root, portal: true,
      placement: "bottom-start", ariaHaspopup: "dialog", closeOnOutside: true, closeOnEscape: true,
      onClose: () => {
        rows.forEach(row => row.stops?.forEach(stop => stop?.()));
        rows.clear();
        if (!onSummary) cleanup();
      } };
    const popover = container ? null : window.PortalOverlay.bindPopover(popoverOptions);
    const isOpen = () => container ? root.isConnected && !root.closest("[hidden]") : popover.isOpen();
    const weekly = account => {
      const q = account?.quota_reserve || {};
      if (q.fresh !== true || (Number.isFinite(q.valid_until) && q.valid_until * 1000 <= Date.now())) return null;
      const ws = (q.windows || []).filter(w => w.window === "seven_day" || w.window_duration_mins === 10080);
      return ws.length && ws.every(w => typeof w.remaining_percent === "number" && Number.isFinite(w.remaining_percent) && w.remaining_percent >= 0 && w.remaining_percent <= 100)
        ? Math.min(...ws.map(w => w.remaining_percent)) : null;
    };
    const accountOf = row => {
      const d = row.detail || {}, bound = d.execution_account || {};
      return accounts.find(a => a.account_id === bound.account_id
        || (a.provider === (d.provider_session?.provider || bound.provider || d.resident?.backend)
          && a.backend_route_id === (bound.backend_route_id || d.provider_session?.account_id))) || {};
    };
    const rowState = row => window.PortalResidentProtocol.stateView(row.snapshot?.state);
    const working = row => rowState(row).working;
    const failed = row => row.snapshot?.input_states?.some(input => input.state === "failed");
    const migration = row => sessionRestartActive(row.restart) ? row.restart : null;
    const label = row => migration(row)?.phase === "waiting_current_execution" ? window.PortalI18n.t("等待本轮后迁移")
      : migration(row) ? window.PortalI18n.t("迁移中") : failed(row) && !working(row) ? window.PortalI18n.t("失败")
      : ({ running: window.PortalI18n.t("工作中"), queued: window.PortalI18n.t("等待执行"), stopping: window.PortalI18n.t("停止中"), idle: window.PortalI18n.t("空闲") }[row.snapshot ? rowState(row).display_work_state : ""] || window.PortalI18n.t("加载中"));
    const runtimeKey = row => JSON.stringify([row.detail?.resident?.backend, row.detail?.execution_account?.account_id,
      row.detail?.provider_session?.provider, row.detail?.provider_session?.account_id]);
    const canEditRuntime = row => row.runtime?.state().canEdit === true;
    const runtimeLabel = row => {
      const settings = row.snapshot?.runtime_settings || {};
      const catalog = row.catalogKey === runtimeKey(row) ? row.catalog : null;
      const fields = catalog?.runtime_capabilities?.settings
        || row.detail?.backend_capabilities?.runtime_capabilities?.settings || [];
      return [settings.reasoning_effort || window.PortalI18n.t("默认强度"),
        fields.includes("service_tier") ? catalog && !(catalog.service_tiers_by_model?.[settings.model || catalog.default_model] || []).length
          ? window.PortalI18n.t("Fast 不支持") : settings.service_tier === "priority" ? window.PortalI18n.t("Fast 开") : window.PortalI18n.t("Fast 关") : ""].filter(Boolean).join(" · ");
    };
    const render = () => {
      if (disposed) return;
      const all = [...rows.values()];
      const running = all.filter(working).length;
      const queued = all.reduce((n, row) => n + Number(row.snapshot?.state?.queued_count || 0), 0);
      const attention = all.filter(row => failed(row) || migration(row) || (weekly(accountOf(row)) !== null
        && (row.role === "steward" ? weekly(accountOf(row)) <= 10 : weekly(accountOf(row)) < 10))).length;
      onSummary?.(`运行 ${running} · 排队 ${queued} · 需关注 ${attention}`);
      const rowHtml = row => {
        const id = row.resident_id, d = row.detail || {}, snap = row.snapshot || {}, account = accountOf(row);
        const remain = weekly(account), q = account.quota_reserve || {}, backend = d.provider_session?.provider || d.resident?.backend || "—";
        const accountLabel = account.label || d.execution_account?.label || d.execution_account?.account_id || window.PortalI18n.t("自动");
        const queue = Number(snap.state?.queued_count || 0), closed = snap.state?.intake_state === "closed";
        const gate = window.PortalResidentProtocol.executionGateView(snap.state);
        const settings = d.runtime_settings || {}, error = row.error || snap.input_states?.find(input => input.state === "failed")?.error?.message || row.restart?.error || "";
        const task = (snap.messages || []).find(m => m.message_id === snap.state?.active_message_id) || (snap.messages || []).find(m => m.role === "user");
        const taskText = (task?.content_parts || []).filter(p => p.type === "text").map(p => p.text).join("\n");
        const week = (q.windows || []).find(w => w.window === "seven_day" || w.window_duration_mins === 10080);
        const reset = week?.resets_at ? footerTime(typeof week.resets_at === "number" ? new Date(week.resets_at * 1000).toISOString() : week.resets_at) : "未知";
        const low = remain !== null && (row.role === "steward" ? remain <= 10 : remain < 10);
        const note = low ? row.role === "steward" ? "额度不足 · 平台负责迁移" : row.role === "member" ? "低于 10% · 新请求须 steward 评估" : "低额度" : "";
        const href = `/lineage?${new URLSearchParams({ resident: id, lineage: family.lineage_id || "", open_chat: "1" })}`;
        const intakeNote = migration(row) ? "迁移暂关" : closed ? "关" : "开";
        const status = migration(row) ? "queued" : failed(row) && !working(row) ? "failed" : rowState(row).status;
        return window.PortalI18n.html`<tr data-family-status-row="${escapeHtml(id)}"><td data-label="节点"><a class="messageMetaLink familyStatusName" href="${escapeHtml(href)}" title="${escapeHtml(row.title || id)}">${escapeHtml(row.title || id)}</a></td><td data-label="工作状态"><button class="portalButton is-quiet familyStatusWork" data-family-status-detail="${escapeHtml(id)}" title="${escapeHtml(label(row))} · 展开工作详情" aria-expanded="${row.expanded ? "true" : "false"}"><span class="status-badge ${escapeHtml(status)}">${escapeHtml(label(row))}</span>${gate.text ? `<span class="status-badge" data-execution-gate="${escapeHtml(gate.state)}" title="${escapeHtml(gate.text)}">${escapeHtml(gate.label)}</span>` : ""}${queue ? window.PortalI18n.html`<span class="familyStatusQueue"> · 排队 ${queue}</span>` : ""}</button></td><td data-label="Backend／账号"><button type="button" class="portalButton is-quiet familyStatusAccount" data-family-status-settings="${escapeHtml(id)}" aria-haspopup="dialog" title="${escapeHtml(window.PortalI18n.text`${row.title || id} · 成员运行设置`)}" ${!canEditRuntime(row) && row.runtime?.state().canRestart !== true || row.settingsLoading || migration(row) ? "disabled" : ""}><span><span>${escapeHtml(`${backend} · ${accountLabel}`)}</span>${canEditRuntime(row) || d.runtime_settings ? window.PortalI18n.html`<span class="familyStatusRuntime"><span>${escapeHtml(row.settingsLoading ? window.PortalI18n.t("读取设置…") : runtimeLabel(row))}</span></span>` : ""}</span><span aria-hidden="true">▾</span></button></td><td data-label="周剩余" ${low ? 'class="familyStatusAttention"' : ""} title="${escapeHtml(note || (remain === null ? q.fresh === false ? window.PortalI18n.t("观测过期，待刷新") : window.PortalI18n.t("无有效周额度观测") : window.PortalI18n.text`周额度重置：${reset}`))}"><span class="familyStatusQuotaValue">${remain === null ? q.currency && q.fresh === true ? escapeHtml(`${q.remaining_balance} ${q.currency}`) : window.PortalI18n.t("未知") : `${remain.toFixed(0)}%`}${low ? window.PortalI18n.markup('<span class="familyStatusQuotaWarning" aria-label="低额度">!</span>') : ""}</span>${remain !== null ? window.PortalI18n.html`<span class="familyStatusQuotaTrack" aria-hidden="true"><span style="width:${remain}%"></span></span>` : ""}</td><td data-label="接单"><button class="portalButton is-compact familyStatusIntake" role="switch" aria-checked="${closed ? "false" : "true"}" aria-label="${escapeHtml(window.PortalI18n.text`${row.title || id}接单`)}" aria-description="${escapeHtml(window.PortalI18n.t("关闭接单只暂停后续任务，当前工作继续"))}" data-portal-tooltip-opt-out="1" data-family-status-intake="${escapeHtml(id)}" ${d.allows?.OpenIntake !== true || migration(row) ? "disabled" : ""}>${intakeNote}</button></td></tr>${row.expanded ? window.PortalI18n.html`<tr><td colspan="5" class="familyStatusDetails"><dl>${gate.text ? `<dt>${escapeHtml(window.PortalI18n.t("执行保护"))}</dt><dd>${escapeHtml(gate.text)}</dd>` : ""}<dt>职责</dt><dd>${escapeHtml(d.resident?.description || window.PortalI18n.t("未填写"))}</dd><dt>模型</dt><dd>${escapeHtml([settings.model, settings.reasoning_effort].filter(Boolean).join(" · ") || window.PortalI18n.t("默认"))}</dd><dt>当前任务</dt><dd>${escapeHtml(taskText.slice(0, 800) || window.PortalI18n.t("无当前任务"))}</dd><dt>开始处理</dt><dd>${escapeHtml(footerTime(task?.activated_at || task?.created_at) || "—")}</dd><dt>最近活动</dt><dd>${escapeHtml(footerTime(snap.messages?.at(-1)?.created_at) || "—")}</dd><dt>周额度重置</dt><dd>${escapeHtml(reset)}</dd>${note ? window.PortalI18n.html`<dt>额度提示</dt><dd>${escapeHtml(note)}</dd>` : ""}${error ? window.PortalI18n.html`<dt>错误</dt><dd>${escapeHtml(typeof error === "string" ? error : JSON.stringify(error))}</dd>` : ""}</dl><a class="messageMetaLink" href="/lineage?${escapeHtml(new URLSearchParams({ lineage: family.lineage_id || "", resident: id }).toString())}">在画布定位</a></td></tr>` : ""}`;
      };
      const table = values => window.PortalI18n.html`<table><thead><tr><th scope="col">节点</th><th scope="col">工作状态</th><th scope="col">Backend／账号</th><th scope="col">周剩余</th><th scope="col">接单</th></tr></thead><tbody>${values.map(rowHtml).join("")}</tbody></table>`;
      // Preserve keyboard focus and detail expansion during live refresh.
      const active = document.activeElement;
      const focusKey = active?.closest("[data-family-status-row]")?.dataset.familyStatusRow;
      const action = active?.dataset.familyStatusDetail ? "detail" : active?.dataset.familyStatusSettings ? "settings" : active?.dataset.familyStatusIntake ? "intake" : "";
      const scrollTop = root.querySelector(".familyStatusBody")?.scrollTop || 0;
      root.setAttribute("aria-label", window.PortalI18n.text`${family.title || "Family"} · 成员状态`);
      root.innerHTML = window.PortalI18n.html`<div class="familyStatusBody">${quotaError ? window.PortalI18n.html`<p class="paneNote">额度暂不可用：${escapeHtml(quotaError)}</p>` : ""}${rows.size ? table(all) : window.PortalI18n.html`<p>${loading ? window.PortalI18n.t("正在加载…") : window.PortalI18n.t("暂无成员状态")}</p>`}</div>${rows.size ? window.PortalI18n.html`<p class="familyStatusIntakeNote">关闭接单只暂停后续任务，当前工作继续</p>` : ""}`;
      root.querySelector(".familyStatusBody").scrollTop = scrollTop;
      if (focusKey && action) root.querySelector(`[data-family-status-${action}="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    };
    let quotaRequest = null;
    const loadQuota = () => {
      if (quotaRequest) return quotaRequest;
      // Home owns quota sampling and freshness. Reads here reuse its cached summary.
      quotaRequest = window.PortalAPI.get("/api/agent-status").then(summary => {
        if (disposed) return;
        accounts = summary.accounts || []; quotaError = ""; render();
      }).catch(error => {
        if (disposed) return;
        accounts = []; quotaError = error.message; render();
      }).finally(() => { quotaRequest = null; });
      return quotaRequest;
    };
    const load = async () => {
      loading = true; render();
      void loadQuota();
      try {
        const knownFamily = getFamily?.();
        family = knownFamily || (await window.PortalResidentService.projection({ residentId })).family_facade || {};
        if (disposed || !isOpen()) return;
        const nodes = family.nodes || (family.members || []).map(m => ({ ...m, role: "member" }));
        const liveIds = new Set(nodes.map(node => node.resident_id));
        for (const [id, row] of rows) if (!liveIds.has(id)) {
          row.stops?.forEach(stop => stop?.()); rows.delete(id);
        }
        for (const node of nodes) {
          let row = rows.get(node.resident_id);
          if (!row) {
            row = { ...node, stops: [] }; rows.set(node.resident_id, row);
            row.runtime = window.PortalResidentRuntimeController.bind(node.resident_id, {
              onState: view => {
                if (disposed || rows.get(node.resident_id) !== row) return;
                if (view.detail) {
                  row.detail = { ...view.detail, runtime_settings: view.settings };
                  row.snapshot = { ...row.snapshot, runtime_settings: view.settings };
                  row.catalog = view.catalog; row.catalogKey = runtimeKey(row);
                }
                row.settingsLoading = view.loading || view.busy;
                row.restart = view.restart; render();
              },
              onOpen: () => { popoverOptions.closeOnOutside = false; },
              onClose: () => {
                popoverOptions.closeOnOutside = true;
                if (!disposed) root.querySelector(`[data-family-status-settings="${CSS.escape(node.resident_id)}"]`)?.focus();
              },
            });
            row.stops.push(() => row.runtime.destroy());
          } else Object.assign(row, node);
        }
        render();
        await Promise.all(nodes.map(async node => {
          const row = rows.get(node.resident_id);
          try {
            row.error = "";
            if (!row.subscription) {
              const subscription = window.PortalResidentProtocol.acquire(node.resident_id, update => {
                if (!disposed && rows.get(node.resident_id) === row && update.snapshot) { row.snapshot = update.snapshot; render(); }
              }, { historyLimit: 1, consumer: "family_member", observationRoot: root });
              row.subscription = subscription;
              row.stops.push(() => subscription.destroy());
            }
            const [memberDetail, snapshot] = await Promise.all([
              row.runtime.load().then(view => ({ ...view.detail, runtime_settings: view.settings })),
              row.subscription?.load({ historyLimit: 1 }).then(snapshot => {
                if (!disposed && rows.get(node.resident_id) === row) { row.snapshot = snapshot; render(); }
                return snapshot;
              }),
            ]);
            if (disposed || rows.get(node.resident_id) !== row) return;
            row.detail = memberDetail;
            if (snapshot) row.snapshot = snapshot;
            const activeId = row.snapshot?.state?.active_message_id;
            if (activeId && !row.snapshot.messages?.some(m => m.message_id === activeId)) {
              const result = await window.PortalInstructionClient.execute("GetMessage", { resident_id: node.resident_id, message_id: activeId });
              row.snapshot = { ...row.snapshot, messages: [...(row.snapshot.messages || []), result.message] };
            }
          } catch (error) { row.error = error.message; }
          render();
        }));
        loading = false; render();
      } catch (error) { root.innerHTML = window.PortalI18n.html`<p>${escapeHtml(error.message)}</p>`; }
      finally {
        loading = false;
        if (reloadRequested) {
          reloadRequested = false;
          if (!disposed && isOpen()) void load();
        }
      }
    };
    root.addEventListener("click", async event => {
      try {
        const detail = event.target.closest("[data-family-status-detail]");
        if (detail) { const row = rows.get(detail.dataset.familyStatusDetail); row.expanded = !row.expanded; render(); return; }
        const intake = event.target.closest("[data-family-status-intake]");
        if (intake) {
          const row = rows.get(intake.dataset.familyStatusIntake); intake.disabled = true;
          await window.PortalResidentService.setIntake({ resident_id: row.resident_id, open: row.snapshot.state.intake_state === "closed" });
          await load(); return;
        }
        const settingsButton = event.target.closest("[data-family-status-settings]");
        if (settingsButton) {
          const row = rows.get(settingsButton.dataset.familyStatusSettings);
          if (!row || settingsButton.disabled) return;
          await row.runtime.openSettings(() => root.querySelector(`[data-family-status-settings="${CSS.escape(row.resident_id)}"]`));
          return;
        }
      } catch (error) { window.PortalUI?.toast?.(error.message, "error"); }
    });
    const freshnessTimer = window.setInterval(() => { if (isOpen()) { render(); void loadQuota(); } }, 15000);
    function cleanup() {
      if (disposed) return;
      window.clearInterval(freshnessTimer);
      disposed = true;
      rows.forEach(row => row.stops?.forEach(stop => stop?.()));
      popover?.destroy(); root.remove();
    }
    return { open() { if (container) root.hidden = false; else popover.open(); if (!loading) void load(); },
      refresh() {
        if (disposed || !isOpen()) return;
        if (loading) reloadRequested = true;
        else void load();
      },
      close() { rows.forEach(row => row.runtime?.close()); if (container) root.hidden = true; else popover.close(); }, isOpen, destroy: cleanup };
  }

  function mountFamilyStatus(residentId, container) {
    const view = createFamilyStatusView(residentId, null, null, null, container);
    view.open();
    return view;
  }

  async function openFamilyStatus(residentId, options = {}) {
    const anchor = options.anchor || document.querySelector(".portalWorkspaceHead .paneHeadActions") || document.body;
    const view = createFamilyStatusView(residentId, anchor);
    view.open();
    return view;
  }

  class ResidentChatPanel {
    constructor(options = {}) {
      const hostOptions = Object.fromEntries([
        "root", "id", "panelId", "target", "autoLoad", "title", "emptyText", "placeholder",
        "draftOwner", "historyLimit", "residentFeatures", "pendingHistoryScope",
        "resolveResident", "resolveBeforeSend", "initialContext", "submissionContext", "onDetail", "onAcknowledgement", "emit",
        "onSent", "onRollback", "onError", "onRuntimeSettingsSaved", "onForkCreated", "conversationView", "onWatchboardAction", "onInteractionChange",
      ].filter(key => Object.prototype.hasOwnProperty.call(options, key)).map(key => [key, options[key]]));
      this.options = hostOptions;
      this.initialContext = null;
      this.selectionController = null;
      this.selectionRoot = null;
      this.actionInFlight = false;
      this.preparingResident = false;
      this.targetRevision = 0;
      this.detail = null;
      this.residentProjection = null;
      this.protocol = null;
      this.pendingProtocol = null;
      this.pendingSnapshot = null;
      this.unsubscribeProjection = null;
      this.pendingTurnSubmissions = new Map();
      this.withdrawnEdits = new Set();
      this.stopRequest = null;
      this.conversationResetInFlight = false;
      this.conversationResetSuppressSnapshots = false;
      this.conversationResetScope = "";
      this.surfaceRevision = 0;
      this.surfaceTarget = null;
      this.workspace = new ResidentChatWorkspace({
        ...hostOptions,
        onFamilyStatus: button => this.openFamilyStatus(button),
        onMenu: () => {
          const residentId = this.target().resident_id;
          if (!residentId) return;
          return window.PortalNav.openResidentMenu({
            residentId,
            onChanged: () => this.target().resident_id === residentId ? this.reload() : undefined,
          });
        },
        target: normalizeResidentTarget(options.target || options),
        onSend: (payload, _workspace, submitOptions) => this.send(payload, submitOptions).catch(error => this.reportError(error)),
        onStop: () => this.stop().catch(error => this.reportError(error)),
        onRefresh: () => this.reload().catch(error => this.reportError(error)),
        onLoadOlder: () => this.loadOlder(),
        onLoadInputHistory: (scope, pageToken) => this.loadInputHistory(scope, pageToken),
        onLoadInheritedContext: () => window.PortalResidentService.inheritedContext(this.target().resident_id),
        onQueueInject: id => this.queueAction("injectInput", id),
        onQueueDelete: id => this.queueAction("cancelInput", id, { withdraw: true }),
        onQueueClear: () => this.clearQueue(),
        onQueueMergeAll: () => this.mergeQueue(),
        onQueueMailbox: () => {
          const residentId = this.target().resident_id;
          if (!residentId || !window.PortalNav?.openResidentMenu) return undefined;
          return window.PortalNav.openResidentMenu({
            residentId,
            tab: "mailbox",
            onChanged: () => this.target().resident_id === residentId ? this.reload() : undefined,
          });
        },
        onCompact: () => this.runAction(() => this.send({ text: "/compact", refs: [], attachments: [] }, { clear: false, focus: false, rememberHistory: false })),
        onResetContext: () => this.resetContext(),
        onForward: items => openResidentForwardDialog(items, {
          preferredLineageId: this.target().lineage_id,
          loadTargets: query => window.PortalResidentService.forwardTargets(query),
          loadChatrooms: () => window.PortalResidentService.forwardChatrooms(),
          submit: body => window.PortalResidentService.forward({ ...body, source_resident_id: this.target().resident_id }),
          onSuccess: () => this.workspace.clearForwardSelection(),
        }),
        attachmentUploadContext: controller => ({ resident_id: controller.target().resident_id }),
        previewUrlForPath: path => window.PortalFileLinks.imagePreviewUrl(path),
        renderMarkdown: text => window.MD?.render?.(text) || escapeHtml(text).replace(/\n/g, window.PortalI18n.markup("<br>")),
        renderMessageBody: (item, context) => this.conversationView()?.renderMessageBody?.(item, context)
          ?? residentMessageBody(item, context),
        renderMessageFooter: residentMessageFooter,
        renderAfterMessage: residentMessageAfter,
        formatTime: value => window.PortalUI?.formatDateTime?.(value, { style: "compact" }) || String(value || ""),
        enableLinkPreview: true,
        enableWithdrawnEdit: true,
        showMessageActions: item => !item.local_delivery && this.conversationView()?.messageActions !== false,
        renderMessageMenuItems: item => (messageQuotable(item) && this.target().resident_id
          ? window.PortalI18n.markup('<button type="button" data-portal-message-action="quote">引用回复</button>')
          : "") + (messageRole(item) === "human" && this.target().resident_id
          && this.detail?.backend_capabilities?.portal_fork === true && this.detail?.allows?.ForkResident === true
          ? window.PortalI18n.markup('<button type="button" data-portal-message-action="fork-current">在当前 Lineage 中创建分支</button><button type="button" data-portal-message-action="fork-other">在其他 Lineage 中创建分支…</button>')
          : "") + familyReassignMenuItems(item, this.familyStatusFamily),
        onMessageAction: ({ action, key, item, button }) => (action === "submission-retry"
          ? window.PortalResidentProtocol.retrySubmission(this.target().resident_id, key).catch(error => this.reportError(error))
          : action === "submission-dismiss"
          ? window.PortalResidentProtocol.dismissSubmission(this.target().resident_id, key)
          : action === "submission-check"
          ? window.PortalResidentProtocol.checkSubmissions(this.target().resident_id)
          : action === "family-reassign"
          ? this.reassignFamilyRequest(item, String(button?.dataset.familyMember || ""))
          : action === "family-request-correct"
          ? this.correctFamilyRoute(item)
          : action === "family-recovery-choose"
          ? this.chooseFamilyRecovery(item, String(button?.dataset.recoveryRequest || ""))
          : action === "family-recovery-view"
          ? this.workspace.revealMessage(item?.family_status?.original_message_id)
          : FAMILY_REQUEST_ACTIONS[action]
          ? this.familyRequestAction(action, item)
          : this.messageAction(action, key)),
        onMessagesRendered: (root, _controller, _workspace, incremental = false) => {
          this.bindMessageSelection(root);
          if (!incremental) this.questions?.render(this.snapshot() || {});
          this.readObserver?.schedule();
          this.conversationView()?.onMessagesRendered?.(root, this);
        },
        onExtensionOpen: () => this.renderExtensions(),
        onRuntimeSettingsSaved: (result, controller) => {
          window.PortalResidentProjection?.invalidate?.(
            result.resident_id || this.workspace?.target?.().resident_id,
          );
          options.onRuntimeSettingsSaved?.(result, controller, this);
        },
        onSubagentFinished: run => this.noticeSubagentFinished(run),
      }).mount();
      this.workspace.root?.addEventListener("click", event => this.onSubagentClick(event));
      this.questions = new ResidentQuestionCards(this);
      this.readObserver = new ResidentReadObserver(this);
      if (options.autoLoad !== false && this.workspace.target().resident_id) {
        // Initial load failures already have a local, non-editable Panel state.
        // Restoring Editors must not turn that same failure into global toasts.
        this.setResident(this.workspace.target(), { load: true }).catch(() => {});
      }
    }

    reportError(error) {
      if (error?.localSubmission) return;
      if (this.options.onError) this.options.onError(error, this);
      else window.PortalUI?.toast?.(error?.message || String(error), "error");
    }

    reportBackgroundSyncError(error) {
      if (this.rejectUnavailableTarget(error)) return;
      if (window.PortalResidentProtocol.isTransientSyncError(error)) return;
      this.reportError(error);
    }

    bindMessageSelection(root) {
      if (this.selectionRoot === root) return;
      this.selectionController?.destroy();
      this.selectionRoot = root;
      this.selectionController = createMessageSelectionController({
        root,
        onAdd: selection => {
          if (!this.workspace.addReference(selection)) throw new Error("无法添加对话选段");
          this.workspace.element("input")?.focus();
        },
        onError: error => this.reportError(error),
      });
    }

    async runAction(callback) {
      if (this.actionInFlight) return null;
      this.actionInFlight = true;
      this.syncActions();
      try {
        return await callback();
      } catch (error) {
        this.reportError(error);
        return null;
      } finally {
        this.actionInFlight = false;
        this.syncActions();
      }
    }

    syncActions() {
      this.workspace.root?.querySelectorAll(".familyRequestAction").forEach(button => {
        button.disabled = this.actionInFlight || button.dataset.actionUnavailable === "true";
      });
      const board = this.detail?.watchboard_capabilities;
      const boardButton = this.workspace.element("extensionMenu")?.querySelector("[data-watchboard-action]");
      if (boardButton) {
        boardButton.disabled = this.actionInFlight || !(board?.installed ? board.can_remove : board?.can_install);
      }
      const composer = this.detail?.chat_workspace?.composer || {};
      if (composer.queue) {
        const queue = normalizeWorkspaceQueue(composer.queue);
        const actionInFlight = this.actionInFlight || queue.actionInFlight;
        // The contract already applied the queue. Only a local action changing
        // its busy state needs another composer update during streaming.
        if (actionInFlight !== normalizeWorkspaceQueue(this.workspace.state.composer.queue).actionInFlight) {
          this.workspace.setComposer({
            ...this.workspace.state.composer,
            queue: { ...queue, action_in_flight: actionInFlight },
          });
        }
      }
      const workState = this.snapshot()?.state?.work_state;
      const active = !!this.target().resident_id;
      this.workspace.root?.toggleAttribute("data-subagent-stop", this.canStopSubagents());
      this.workspace.root?.toggleAttribute("data-subagent-steer", this.canSteerSubagents());
      this.workspace.setResidentActionVisibility(residentSessionActions({
        active,
        runtimeCapabilities: this.detail?.backend_capabilities?.runtime_capabilities,
        compactBusy: this.actionInFlight || composer.can_send === false,
        resetContext: active,
        resetContextDisabled: this.actionInFlight || ["running", "queued", "stopping"].includes(workState),
      }));
    }

    renderExtensions() {
      const menu = this.workspace.element("extensionMenu");
      if (!menu) return;
      if (this.detail?.binding_state === "unbound") {
        menu.innerHTML = window.PortalI18n.markup('<div class="paneNote" role="status">发送第一条消息后可配置会话扩展。</div>');
        return;
      }
      menu.replaceChildren();
      const board = this.detail?.watchboard_capabilities;
      if (this.options.residentFeatures?.watchboard && board && this.options.onWatchboardAction) {
        const action = board.installed ? "remove" : "add";
        const allowed = action === "remove" ? board.can_remove : board.can_install;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portalButton";
        button.dataset.watchboardAction = action;
        button.textContent = board.installed ? window.PortalI18n.t("移除 Watchboard") : window.PortalI18n.t("安装 Watchboard");
        button.disabled = this.actionInFlight || !allowed;
        button.addEventListener("click", () => this.runAction(() =>
          this.options.onWatchboardAction(action, this.target().resident_id)));
        menu.append(button);
      }
      // Harness settings live on the Resident Menu's Harness page.
      const residentId = this.target().resident_id;
      if (!residentId || !window.PortalNav?.openResidentMenu) return;
      const harness = document.createElement("button");
      harness.type = "button";
      harness.className = "chatSessionSwitch chatSessionNav";
      harness.dataset.harnessMenuOpen = "";
      harness.innerHTML = window.PortalI18n.markup('<span class="chatSessionSwitchText"><strong>Harness</strong><small>Prompt 注入、sub agent、失败续跑与智能选择 Harness 在 Menu 的 Harness 页设置</small></span><span class="chatSessionNavArrow" aria-hidden="true">›</span>');
      harness.addEventListener("click", () => {
        this.workspace.closeExtensionMenu?.();
        return window.PortalNav.openResidentMenu({
          residentId,
          tab: "harness",
          onChanged: () => this.target().resident_id === residentId ? this.reload() : undefined,
        });
      });
      menu.append(harness);
    }

    queueAction(command, messageId, { withdraw = false } = {}) {
      const target = this.target();
      return this.runAction(async () => {
        const result = await window.PortalResidentProtocol[command]({
          resident_id: target.resident_id,
          lineage_id: target.lineage_id,
          message_id: messageId,
        });
        if (withdraw) noticeQueueWithdrawn(result);
        return result;
      });
    }

    mergeQueue() {
      const target = this.target();
      const revision = this.targetRevision;
      const isCurrent = () => revision === this.targetRevision
        && residentSessionScope(this.target()) === residentSessionScope(target);
      if (!target.resident_id) return null;
      return this.runAction(async () => {
        try {
          const view = await window.PortalResidentProtocol.loadMailbox(target.resident_id);
          if (!isCurrent()) return null;
          if (!view?.can_organize) throw new Error(window.PortalI18n.t("没有整理此 Mailbox 的权限"));
          // Exactly the Mailbox tab's Select all: pending requests and groups,
          // in queue order. Never merge only the Chat preview or expand groups here.
          const ids = (Array.isArray(view.pending) ? view.pending : [])
            .filter(item => item.kind !== "info").map(item => item.message_id);
          if (ids.length < 2) {
            window.PortalUI?.toast?.(window.PortalI18n.t("至少两项待处理请求或群体消息才能合并"));
            await this.protocol?.load({ force: true });
            return null;
          }
          const result = await window.PortalResidentProtocol.mergeMailboxItems({
            resident_id: target.resident_id,
            message_ids: ids,
          });
          if (!isCurrent()) return result;
          window.PortalUI?.toast?.(window.PortalI18n.t("已合并全部待处理请求和群体消息"), "success");
          await this.protocol?.load({ force: true });
          return result;
        } catch (error) {
          if (isCurrent()) throw error;
          return null;
        }
      });
    }

    clearQueue() {
      const target = this.target();
      const revision = this.targetRevision;
      const isCurrent = () => revision === this.targetRevision
        && residentSessionScope(this.target()) === residentSessionScope(target);
      return this.runAction(async () => {
        const confirmed = await window.PortalUI.confirm({
          title: window.PortalI18n.t("清除全部排队消息？"),
          message: window.PortalI18n.t("将清除当前 Resident 所有可撤回的排队消息。正在执行的消息不受影响，接单状态保持不变。"),
          confirmText: window.PortalI18n.t("全部清除"),
          danger: true,
        });
        if (!confirmed || !isCurrent()) return null;
        const result = await window.PortalResidentProtocol.clearMailbox({
          resident_id: target.resident_id,
          lineage_id: target.lineage_id,
        });
        if (!isCurrent()) return result;
        noticeQueueCleared(result);
        return result;
      });
    }

    async resetContext() {
      const target = this.target();
      const confirmed = await window.PortalUI.confirm({
        title: window.PortalI18n.t("清空 Resident 上下文？"),
        message: window.PortalI18n.t("这会清空此前对话和 Provider 上下文，并保留私有归档。"),
        confirmText: window.PortalI18n.t("清空上下文"),
        danger: true,
      });
      if (!confirmed || residentSessionScope(this.target()) !== residentSessionScope(target)) return null;
      return this.runAction(() => this.resetConversation(() => window.PortalResidentService.resetContext(target)));
    }

    // Cancellation resolves through the facade. Injection uses the exact member
    // copy in the relay projection; the facade itself has no active turn.
    familyRequestAction(action, item) {
      if (action === "family-request-inject") return this.injectFamilyRequest(item);
      const messageId = String(item?.family_status?.request_message_id || "");
      const target = this.target();
      if (!messageId || !target.resident_id) return null;
      return this.runAction(async () => {
        const result = await window.PortalResidentProtocol[FAMILY_REQUEST_ACTIONS[action]]({
          lineage_id: target.lineage_id,
          resident_id: target.resident_id,
          message_id: messageId,
        });
        if (action === "family-request-withdraw") noticeQueueWithdrawn(result);
        return result;
      });
    }

    injectFamilyRequest(item) {
      const status = item?.family_status || {};
      const residentId = String(status.resident_id || "");
      const messageId = String(status.message_id || "");
      const requestId = String(status.request_message_id || "");
      const revision = this.targetRevision;
      if (status.state !== "queued" || !residentId || !messageId || !requestId) return null;
      const isCurrent = () => revision === this.targetRevision && this.workspace.state.messages.some(message => {
        const current = message.family_status;
        return current?.request_message_id === requestId && current.state === "queued"
          && current.resident_id === residentId && current.message_id === messageId;
      });
      return this.runAction(async () => {
        // Read the actual member's current capability, not the facade's Provider.
        const result = await window.PortalResidentService.loadStates([residentId], { includeViewerState: false });
        if (!isCurrent()) return null;
        const member = result.states?.find(entry => entry.resident_id === residentId);
        if (!member) throw new Error(window.PortalI18n.t("暂时无法读取承接成员状态，请稍后重试"));
        if (typeof member.controls?.active_turn_input !== "boolean") {
          throw new Error(window.PortalI18n.t("暂时无法确认承接成员的注入能力，请稍后重试"));
        }
        if (member.controls?.active_turn_input !== true) {
          throw new Error(window.PortalI18n.t("承接成员不支持运行中接收消息；消息保留在队列中"));
        }
        // A state snapshot can lag completion, stopping or queue promotion.
        // The service decides current-turn delivery versus next-turn admission.
        const receipt = await window.PortalResidentProtocol.injectInput({ resident_id: residentId, message_id: messageId });
        if (revision !== this.targetRevision) return receipt;
        const delivered = receipt.injected === true || receipt.status === "delivered";
        const nextTurn = receipt.scheduled_next_turn === true || receipt.status === "next_turn";
        window.PortalUI?.toast?.(window.PortalI18n.t(delivered ? "已交给承接成员当前的工作"
          : nextTurn ? "消息已排到下一轮；暂停接单时会等待恢复" : "已提交注入请求，等待承接成员当前的工作接收"), "success");
        await this.protocol?.load({ force: true });
        return receipt;
      });
    }

    chooseFamilyRecovery(item, messageId) {
      const candidate = (item?.family_status?.candidates || []).find(entry => entry.message_id === messageId);
      if (!candidate) return false;
      this.workspace.setQuote({ message_id: candidate.message_id, author_id: candidate.author_id, text: candidate.text || candidate.message_id });
      if (!this.workspace.inputText().trim()) this.workspace.setInputText("continue");
      return true;
    }

    async reassignFamilyRequest(item, memberId) {
      const target = this.target();
      const member = (this.familyStatusFamily?.members || []).find(entry => entry?.resident_id === memberId);
      const messageId = String(item?.message_id || "");
      if (!member || !messageId || !target.resident_id) return null;
      const name = member.title || memberId;
      const reason = await window.PortalUI.prompt({
        title: window.PortalI18n.text`改派给 ${name}`,
        message: window.PortalI18n.t("这条请求会交给所选成员重新处理。原成员还没开始时会撤回原副本；正在处理时请先停止本轮。理由会记入改派纠正，供以后选人参考。"),
        label: window.PortalI18n.t("理由（可留空）"),
        placeholder: window.PortalI18n.t("例如：这是 steward 设计，应由 family 设计负责"),
        confirmLabel: "改派",
      });
      if (reason === null) return null;
      return this.submitFamilyReassign(messageId, member, reason);
    }

    // 纠正 Router on a queued request's progress: the user picks the member that
    // should take it and writes the correction, which the reassignment records for the Router.
    async correctFamilyRoute(item) {
      const status = item?.family_status || {};
      const messageId = String(status.request_message_id || "");
      const current = String(status.resident_id || "");
      if (!messageId || !this.target().resident_id) return null;
      const members = (this.familyStatusFamily?.members || []).filter(member => member?.resident_id && member.resident_id !== current);
      if (!members.length) {
        window.PortalUI?.toast?.(window.PortalI18n.t("这个 Family 没有其他成员可以改派"), "error");
        return null;
      }
      const answer = await window.PortalUI.prompt({
        title: window.PortalI18n.t("纠正 Router"),
        message: window.PortalI18n.text`这条请求现在交给了 ${String(status.title || current)}。选择应该处理它的成员并写下纠正内容：请求改派给所选成员，原成员的排队副本撤回；纠正内容记入 Family 的路由纠正并告诉 steward，Router 以后选人时参考。`,
        choiceLabel: window.PortalI18n.t("改派给"),
        choices: members.map(member => ({ value: member.resident_id, label: member.title || member.resident_id })),
        label: window.PortalI18n.t("纠正内容（可留空）"),
        placeholder: window.PortalI18n.t("例如：登录页样式问题归 family 设计负责"),
        multiline: true,
        confirmLabel: window.PortalI18n.t("改派"),
      });
      if (answer === null) return null;
      const member = members.find(entry => entry.resident_id === answer.choice);
      return member ? this.submitFamilyReassign(messageId, member, answer.value) : null;
    }

    submitFamilyReassign(messageId, member, reason) {
      const target = this.target();
      const name = member.title || member.resident_id;
      return this.runAction(async () => {
        const result = await window.PortalResidentService.reassignFamilyRequest({
          resident_id: target.resident_id, message_id: messageId, member_id: member.resident_id, reason,
        });
        window.PortalUI?.toast?.(`已改派给 ${name}`, "success");
        await this.reload();
        return result;
      });
    }

    // Subagent cards (decision_b1ab45c4b3ec): a running card opens its run,
    // stops it or starts a correction; a result card opens its run, quotes,
    // copies or saves its result.
    canStopSubagents() {
      const harness = this.detail?.backend_capabilities?.runtime_capabilities?.harness_capabilities || [];
      return harness.includes("subagent_stop") && this.snapshot()?.controls?.can_stop === true;
    }

    canSteerSubagents() {
      return this.snapshot()?.controls?.can_send === true;
    }

    onSubagentClick(event) {
      const target = event.target instanceof Element ? event.target : null;
      const button = target?.closest("[data-subagent-action]");
      const liveCard = target?.closest("[data-subagent-live]");
      if (!button && !(liveCard && !target.closest("a, button, summary, input, textarea"))) return;
      if (button?.disabled) return;
      const resultCard = target?.closest("[data-subagent-run]");
      const run = liveCard
        ? subagentLiveById.get(liveCard.dataset.subagentLive)
        : (this.workspace.state?.messages || []).find(item => String(item?.id || "") === resultCard?.dataset.subagentRun)
          ?.content_parts?.find(part => part?.type === "subagent_run");
      if (!run) return;
      event.preventDefault();
      event.stopPropagation();
      return this.subagentAction(button?.dataset.subagentAction || "detail", run, { live: !!liveCard });
    }

    subagentAction(action, run, { live = false } = {}) {
      if (action === "detail") return this.openSubagentRun(run, live);
      if (action === "stop") return this.stopSubagent(run);
      if (action === "steer") return this.steerSubagent(run);
      if (action === "quote") return this.quoteSubagentResult(run);
      if (action === "copy") return this.copySubagentResult(run);
      if (action === "save") return this.saveSubagentResult(run);
      return null;
    }

    openSubagentRun(run, live) {
      return openSubagentDetail({
        residentId: this.target().resident_id,
        run,
        live,
        actions: isLive => isLive
          ? [
            { action: "steer", label: window.PortalI18n.t("纠偏"), title: window.PortalI18n.t("在输入框写给父 Resident 的纠偏，发送后立即进入当前这一轮"), hidden: !this.canSteerSubagents() },
            { action: "stop", label: window.PortalI18n.t("停止这个子 agent"), danger: true, hidden: !this.canStopSubagents() },
          ]
          : [
            { action: "quote", label: window.PortalI18n.t("引用到输入框") },
            { action: "copy", label: window.PortalI18n.t("复制结果") },
            { action: "save", label: window.PortalI18n.t("保存为文件") },
          ],
        onAction: (name, context) => {
          if (name === "steer" || name === "quote") context.close();
          return this.subagentAction(name, context.run, { live: context.live });
        },
      });
    }

    subagentName(run) {
      return String(run?.description || "").trim() || subagentTypeLabel(run?.agent_type) || window.PortalI18n.t("子 agent");
    }

    async stopSubagent(run) {
      const target = this.target();
      if (!target.resident_id || !run?.tool_use_id || !run.execution_id) return null;
      const confirmed = await window.PortalUI.confirm({
        title: window.PortalI18n.t("停止这个子 agent？"),
        message: window.PortalI18n.text`只停止子 agent「${this.subagentName(run)}」。父 Resident 的这一轮继续，它会得知这个子 agent 已停止。`,
        confirmLabel: window.PortalI18n.t("停止"),
        tone: "danger",
      });
      if (!confirmed || residentSessionScope(this.target()) !== residentSessionScope(target)) return null;
      return this.runAction(async () => {
        const result = await window.PortalInstructionClient.execute("CancelSubagent", {
          resident_id: target.resident_id, execution_id: run.execution_id, tool_use_id: run.tool_use_id,
        }, { idempotencyKey: `subagent-stop-${run.tool_use_id}-${Date.now().toString(36)}` });
        if (result?.status === "failed") {
          const reason = SUBAGENT_STOP_REASONS[result.error];
          throw new Error(reason ? window.PortalI18n.t(reason) : window.PortalI18n.text`没有停下：${result.error || "Provider 没有说明原因"}`);
        }
        window.PortalUI?.toast?.(window.PortalI18n.t("已请求停止；子 agent 停下后卡片显示已停止"), "success");
        return result;
      });
    }

    // Text the reader goes on writing: after the draft, the caret at its end.
    appendToComposer(text) {
      const draft = this.workspace.draftText();
      this.workspace.setDraftText(draft.trim() ? `${draft.replace(/\s+$/, "")}\n\n${text}` : text, this.workspace.attachments());
      const input = this.workspace.element("input");
      placeCaretAtEnd(input);
      input?.dispatchEvent(new Event("input", { bubbles: true }));
      this.workspace.updateControls();
    }

    steerSubagent(run) {
      if (!this.canSteerSubagents()) {
        window.PortalUI?.toast?.(window.PortalI18n.t("当前对话不能发送消息"), "warning");
        return null;
      }
      const prefix = window.PortalI18n.text`关于子 agent「${this.subagentName(run)}」：`;
      this.subagentSteer = { scope: residentSessionScope(this.target()), prefix };
      this.appendToComposer(prefix);
      return prefix;
    }

    // Send now: a correction about a running subagent leaves the queue for the
    // current turn when the Provider takes input mid-turn; otherwise it waits
    // its turn like any message.
    sendSubagentSteerNow(target, messageId) {
      const snapshot = this.protocol?.snapshot?.() || {};
      if (!messageId || snapshot.controls?.active_turn_input !== true || !snapshot.state?.active_message_id) return;
      window.PortalResidentProtocol.injectInput({
        resident_id: target.resident_id, lineage_id: target.lineage_id, message_id: messageId,
      }).catch(() => window.PortalUI?.toast?.(window.PortalI18n.t("纠偏没能进入当前这一轮，已排队在这一轮之后处理"), "warning"));
    }

    quoteSubagentResult(run) {
      const result = String(run?.result || "").trim();
      const excerpt = result.length > 600 ? `${result.slice(0, 600).trimEnd()}…` : result;
      const lines = [window.PortalI18n.text`子 agent「${this.subagentName(run)}」的结果：`, ...(excerpt ? excerpt.split("\n") : [window.PortalI18n.t("子 agent 没有返回文字结果")])];
      this.appendToComposer(`${lines.map(line => `> ${line}`.trimEnd()).join("\n")}\n\n`);
      return true;
    }

    async copySubagentResult(run) {
      const text = await subagentFullResult(this.target().resident_id, run);
      return window.PortalUI.copyText(text, window.PortalI18n.t("子 agent 结果"));
    }

    saveSubagentResult(run) {
      const residentId = this.target().resident_id;
      if (!residentId) return null;
      return this.runAction(async () => {
        const workspace = await window.PortalResidentService.workspaces(residentId);
        const workspaceId = String(workspace?.workspace_id || "");
        if (!workspaceId) throw new Error(window.PortalI18n.t("找不到这个 Resident 自己的 Workspace"));
        const moment = new Date(Date.parse(String(run.finished_at || "")) || Date.now());
        const stamp = moment.toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
        const path = `subagent-results/${stamp}-${subagentSlug(run.description || run.agent_type)}.md`;
        const result = await subagentFullResult(residentId, run);
        await window.PortalResidentService.saveFile({ address: `${workspaceId}/file/${path}`, content: subagentResultMarkdown(run, result) });
        window.PortalUI?.toast?.(window.PortalI18n.text`已保存到 Workspace：${path}`, "success");
        return path;
      });
    }

    // A run this Chat showed running ended: a reader looking elsewhere hears it.
    noticeSubagentFinished(run) {
      const status = run?.status === "failed" ? window.PortalI18n.t("失败") : run?.status === "stopped" || run?.status === "cancelled" ? window.PortalI18n.t("已停止") : window.PortalI18n.t("已完成");
      if (document.hidden) {
        badgeTitleForSubagent();
        return;
      }
      const root = this.workspace.root;
      const rect = root?.getBoundingClientRect?.();
      const visible = !!rect && rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight;
      if (!visible) window.PortalUI?.toast?.(window.PortalI18n.text`子 agent「${this.subagentName(run)}」${status}`, run?.status === "failed" ? "warning" : "success");
    }

    // Quote replies (decision_f560803947c6): the quote bar shows the quoted message until it is sent.
    quoteMessage(key) {
      const item = this.workspace.state?.messages?.find(message => messageIdentity(message) === key);
      if (!item || !messageQuotable(item)) return false;
      const author = String(item.origin?.sender?.id || item.author_id
        || (messageRole(item) === "agent" ? this.target().resident_id : "") || "");
      return this.workspace.setQuote({ message_id: String(item.message_id), author_id: author, text: messageQuoteExcerpt(item) });
    }

    async reeditWithdrawnMessage(key) {
      const item = this.workspace.state?.messages?.find(message => messageIdentity(message) === key);
      const target = this.target();
      const revision = this.targetRevision;
      if (!item?.withdrawn || !messageAuthoredByViewer(item) || !target.resident_id
        || this.withdrawnEdits.has(key)) return;
      this.withdrawnEdits.add(key);
      try {
        // The conversation keeps only the annotation; fetch the original only on demand.
        const { message } = await window.PortalInstructionClient.execute("GetMessage", {
          resident_id: target.resident_id, message_id: item.message_id,
        });
        if (revision !== this.targetRevision || residentSessionScope(target) !== residentSessionScope(this.target())) return;
        if (message?.message_id !== item.message_id || !messageAuthoredByViewer(message)) return;
        const restored = messageHistoryDraft(residentProtocolMessageToWorkspace(message), {
          previewUrlForPath: path => window.PortalFileLinks.imagePreviewUrl(path),
        });
        // Keep anything typed while the read was in flight, including its attachments and quote.
        const draft = this.workspace.draftText();
        const attachments = [...new Map([...this.workspace.attachments(), ...restored.attachments]
          .map(attachment => [attachment.id, attachment])).values()];
        this.workspace.setDraftText(draft ? `${draft}\n\n${restored.text}` : restored.text, attachments);
        if (message.quote && !this.workspace.quote()) this.workspace.setQuote(message.quote);
        const input = this.workspace.element("input");
        placeCaretAtEnd(input);
        input?.dispatchEvent(new Event("input", { bubbles: true }));
      } finally {
        this.withdrawnEdits.delete(key);
      }
    }

    messageAction(action, key) {
      if (action === "withdrawn-edit") return this.reeditWithdrawnMessage(key);
      if (action === "quote") return this.quoteMessage(key);
      if (!["fork-current", "fork-other"].includes(action)) return;
      const source = { ...this.target() };
      const item = this.workspace.state?.messages?.find(message => messageIdentity(message) === key);
      if (!item || messageRole(item) !== "human") return;
      const fork = async target => {
        const lineageId = target.lineage_id;
        if (!lineageId) throw new Error("Resident 缺少所属 Lineage");
        const societyService = window.PortalSocietyService.createClient();
        const graph = await societyService.loadLineage(lineageId);
        const nodes = graph?.graph?.nodes || graph?.nodes || [];
        const parent = nodes.find(node => node.id === target.resident_id);
        if (!parent) throw new Error("目标 Lineage 中找不到父 Resident");
        const result = await societyService.branchNode({
          lineage_id: lineageId,
          source_resident_id: parent.id,
          context_source_resident_id: source.resident_id,
          fork_mode: "message",
          message: { message_id: item.id },
        });
        const residentId = String(result?.resident_id || "");
        if (this.options.onForkCreated) {
          await this.options.onForkCreated({ lineageId, residentId, lineage: result?.lineage, result }, this);
        } else if (!residentId || result?.lineage?.lineage_id !== lineageId) {
          window.PortalUI?.toast?.("分支已创建，但返回结果不完整；请刷新 Lineage 后定位新 Resident。", "warning");
        } else {
          window.PortalUI?.toast?.("分支已创建。", "success");
        }
        return result;
      };
      if (action === "fork-current") return this.runAction(() => fork(source));
      return openResidentForwardDialog([{ content: item.content || "对话分支" }], {
        title: window.PortalI18n.t("在其他 Lineage 中创建分支"),
        submitLabel: "创建分支",
        summary: "选择新分支所属的 Lineage 和父 Resident。",
        selectTargetOnly: true,
        successMessage: "已创建分支。",
        preferredLineageId: source.lineage_id,
        loadTargets: query => window.PortalResidentService.forwardTargets(query),
        submit: body => fork({ lineage_id: body.target_lineage_id, resident_id: body.target_resident_id }),
      });
    }

    rejectUnavailableTarget(error) {
      const status = Number(error?.status || 0);
      const deleted = error?.code === "resident.deleted";
      if (!deleted && status !== 404 && status !== 403) return false;
      if (deleted || status === 404) this.options.emit?.("binding-unavailable");
      this.questions.reset();
      const target = this.workspace.target();
      window.PortalConversationStore?.discardResident?.(target.resident_id);
      const currentDetail = this.detail?.resident?.resident_id === target.resident_id ? this.detail : null;
      const title = currentDetail?.chat_workspace?.title || target.title || target.resident_id || window.PortalI18n.t("Resident 不可用");
      // Detach this target before rendering its error contract. Late protocol
      // events and metadata responses must not restore the previous capabilities.
      this.targetRevision += 1;
      this.unavailableTargetRevision = this.targetRevision;
      this.unsubscribeProjection?.();
      this.unsubscribeProjection = null;
      this.pendingProtocol?.destroy?.();
      this.pendingProtocol = null;
      this.protocol?.destroy?.();
      this.protocol = null;
      this.pendingSnapshot = null;
      this.detail = null;
      this.residentProjection = null;
      this.showError(
        deleted || status === 404 ? window.PortalI18n.t("该 Resident 已删除或不存在。") : window.PortalI18n.t("没有查看这个 Resident 的权限。"),
        {
          title,
          subtitle: window.PortalI18n.t("Resident 不可用"),
          placeholder: window.PortalI18n.t("Resident 不可用"),
          meta: window.PortalI18n.t(deleted ? "请打开其他 Resident。" : "请打开其他 Resident，或刷新后重试。"),
          historyScope: residentSessionScope(target),
        },
      );
      return true;
    }

    instanceId() {
      return String(this.options.panelId || this.workspace?.id || "");
    }

    isReadyFor(target = {}) {
      return Boolean(
        this.protocol
        && residentSessionScope(this.workspace.target()) === residentSessionScope(target),
      );
    }

    async requestDetail(target, options = {}) {
      if (options.detail && typeof options.detail === "object") {
        if (this.contractFromDetail(options.detail, target)) return options.detail;
        return window.PortalResidentProjection.load(target.resident_id, {
          force: options.force === true,
        });
      }
      return window.PortalResidentProjection.load(target.resident_id, {
        force: options.force === true,
      });
    }

    contractFromDetail(detail, target = this.workspace.target()) {
      const contract = detail?.chat_workspace;
      if (!contract || typeof contract !== "object") return null;
      const contractTarget = contract.target && typeof contract.target === "object"
        ? contract.target
        : {};
      if (contractTarget.kind !== "resident") return null;
      if (
        String(contractTarget.kind || "") === "resident"
        && String(contractTarget.id || "") !== String(target?.resident_id || "")
      ) {
        throw new Error("Resident projection returned a mismatched Chat target");
      }
      if (Array.isArray(contract.messages)) return contract;
      return { ...contract, messages: [] };
    }

    conversationView() {
      if (!this.workspace) return null;
      return this.options.conversationView?.(this.target(), this) || null;
    }

    projectContract(contract, detail, snapshot = null, update = null) {
      // A capability may present its actor-scoped requests in the same Resident
      // Editor. Identity, permissions, session controls and menus remain native.
      const projected = this.conversationView()?.project?.(contract, detail, snapshot, update) || contract;
      const composer = projected.composer || {};
      // Reconcile this Editor's accepted input before rendering. Applying an
      // idle server composer first would remove and recreate an actionable Stop,
      // losing its focus/pointer target on every unrelated stream update.
      return { ...projected, composer: { ...composer,
        can_stop: this.pendingTurnStopCapability(composer, snapshot),
      } };
    }

    async setResident(residentId, options = {}) {
      const target = normalizeResidentTarget(
        residentId && typeof residentId === "object"
          ? residentId
          : { ...options, resident_id: residentId },
      );
      const scope = target.resident_id && options.load !== false ? residentSessionScope(target) : "";
      const installed = Boolean(this.protocol) && residentSessionScope(this.workspace.target()) === scope;
      // A repeated request for a Resident whose first load is still running joins
      // that load. Restarting it on every page refresh never finishes when one load
      // takes longer than the refresh interval, and the Chat stays "loading".
      if (scope && !installed && options.force !== true && this.initialLoad?.scope === scope) {
        return this.initialLoad.promise;
      }
      const promise = this.loadResidentTarget(residentId, options);
      if (scope && !installed) {
        const entry = { scope, promise };
        this.initialLoad = entry;
        const settle = () => { if (this.initialLoad === entry) this.initialLoad = null; };
        promise.then(settle, settle);
      } else {
        this.initialLoad = null;
      }
      return promise;
    }

    async loadResidentTarget(residentId, options = {}) {
      const target = normalizeResidentTarget(
        residentId && typeof residentId === "object"
          ? residentId
          : { ...options, resident_id: residentId },
      );
      const current = this.workspace.target();
      const sameTarget = residentSessionScope(current) === residentSessionScope(target);
      this.workspace.setHistoryTarget(target);
      if (sameTarget && this.protocol && options.force !== true) {
        if (this.pendingProtocol) {
          this.targetRevision += 1;
          const pendingProtocol = this.pendingProtocol;
          this.pendingProtocol = null;
          pendingProtocol.destroy?.();
          this.workspace.clearLoading?.();
        }
        if (options.detail && typeof options.detail === "object") {
          this.applyDetail(options.detail);
        }
        return target;
      }
      const revision = ++this.targetRevision;
      const releaseInstalledTarget = () => {
        if (!sameTarget) this.updateFamilyStatus(null);
        this.readObserver.observation = null;
        this.selectionController?.clear();
        this.workspace.extensionPopover?.close?.();
        if (!sameTarget) this.initialContext = null;
        this.questions.reset();
        this.unsubscribeProjection?.();
        this.unsubscribeProjection = null;
      };
      this.pendingProtocol?.destroy?.();
      this.pendingProtocol = null;
      if (!target.resident_id || options.load === false) {
        releaseInstalledTarget();
        this.protocol?.destroy?.();
        this.protocol = null;
        this.pendingSnapshot = null;
        this.detail = null;
        this.residentProjection = null;
        this.workspace.setTarget(target);
        this.showEmpty();
        return target;
      }
      const previousProtocol = this.protocol;
      if (!sameTarget || !previousProtocol) this.workspace.setLoading("正在加载 Resident 和对话状态；加载完成后恢复本地草稿…");
      if (!sameTarget && previousProtocol) {
        // The old conversation is no longer the navigation target. Keeping its
        // subscription until the new snapshot finishes lets a stream reset
        // start another GetConversation for a Resident that is already gone.
        previousProtocol.destroy?.();
        this.protocol = null;
        this.unsubscribeProjection?.();
        this.unsubscribeProjection = null;
      }
      if (!window.PortalResidentProtocol?.acquire) {
        const error = new Error("PortalResidentProtocol is unavailable");
        releaseInstalledTarget();
        this.workspace.setTarget(target);
        this.protocol = null;
        this.detail = null;
        this.residentProjection = null;
        this.showError(error, {
          title: target.title || this.options.title,
          historyScope: residentSessionScope(target),
        });
        previousProtocol?.destroy?.();
        throw error;
      }
      let stagedSnapshot = null;
      let stagedProtocol = null;
      let loadTimer = 0;
      try {
        stagedProtocol = window.PortalResidentProtocol.acquire(
          target.resident_id,
          update => {
            const installed = this.protocol === stagedProtocol;
            const pending = revision === this.targetRevision && this.pendingProtocol === stagedProtocol;
            if (!installed && !pending) return;
            if (installed && this.pendingProtocol && this.pendingProtocol !== stagedProtocol) return;
            if (update.type === "error") {
              // While staged, load() owns the error through its rejected promise.
              // Only an installed protocol owns asynchronous error notifications.
              if (installed) this.reportBackgroundSyncError(update.error);
            } else if (installed && update.snapshot) {
              this.applySnapshot(update.snapshot, update);
            } else if (pending && update.snapshot) {
              stagedSnapshot = update.snapshot;
            }
          },
          { load: false, consumer: "chat", observationRoot: this.workspace.root },
        );
        this.pendingProtocol = stagedProtocol;
        const detailRequest = this.requestDetail(target, options);
        const [detail, loadedSnapshot] = await Promise.race([
          Promise.all([
            detailRequest,
            stagedProtocol.load({
              force: options.force === true,
              historyLimit: Number(
                this.options.historyLimit || DEFAULT_RESIDENT_HISTORY_PAGE_SIZE,
              ),
            }),
          ]),
          new Promise((_resolve, reject) => {
            loadTimer = window.setTimeout(() => {
              reject(new Error("Resident 和对话加载超时，请重试。"));
            }, RESIDENT_INITIAL_LOAD_TIMEOUT_MS);
          }),
        ]);
        if (revision !== this.targetRevision) {
          stagedProtocol.destroy?.();
          return target;
        }
        releaseInstalledTarget();
        this.workspace.setTarget({
          ...target,
          lineage_id: String(detail?.resident?.lineage_id || detail?.chat_workspace?.target?.lineage_id || loadedSnapshot?.resident?.lineage_id || target.lineage_id || ""),
        });
        this.protocol = stagedProtocol;
        this.pendingProtocol = null;
        this.pendingSnapshot = stagedProtocol.snapshot?.() || stagedSnapshot || loadedSnapshot || null;
        this.applyDetail(detail && typeof detail === "object" ? detail : {});
        this.options.emit?.("binding-ready");
        this.unsubscribeProjection = window.PortalResidentProjection?.subscribe?.(
          target.resident_id,
          update => {
            if (this.protocol !== stagedProtocol) return;
            if (this.pendingProtocol && this.pendingProtocol !== stagedProtocol) return;
            if (update.error) this.reportBackgroundSyncError(update.error);
            else this.applyDetail(update.detail);
          },
        );
        previousProtocol?.destroy?.();
        return target;
      } catch (error) {
        if (this.pendingProtocol === stagedProtocol) this.pendingProtocol = null;
        stagedProtocol?.destroy?.();
        if (revision === this.targetRevision) {
          releaseInstalledTarget();
          this.workspace.setTarget(target);
          this.protocol = null;
          this.pendingSnapshot = null;
          this.detail = null;
          this.residentProjection = null;
          if (!this.rejectUnavailableTarget(error)) {
            this.showError(error, {
              title: target.title || this.options.title,
              historyScope: residentSessionScope(target),
            });
          }
          previousProtocol?.destroy?.();
        }
        throw error;
      } finally {
        window.clearTimeout(loadTimer);
      }
    }

    openResource(resource = {}) {
      return this.setResident(resource, { ...resource, load: true });
    }

    setContext(context = {}) {
      const candidate = context.conversationTarget
        || context.resident
        || (context.residentId ? { resident_id: context.residentId } : null);
      if (!candidate) return this.workspace.target();
      const target = normalizeResidentTarget(candidate);
      const current = this.workspace.target();
      const targetPanelId = String(candidate.panelId || candidate.panel_id || "").trim();
      if (current.resident_id && targetPanelId !== this.instanceId()) return current;
      if (!target.resident_id || residentSessionScope(target) === this.workspace.sessionScope()) {
        return current;
      }
      return this.setResident(target, { load: true });
    }

    async openSurface(surfaceTarget = {}, options = {}) {
      if (typeof this.options.resolveResident !== "function") {
        throw new Error("ResidentChatPanel surface binding requires resolveResident");
      }
      const revision = ++this.surfaceRevision;
      const requested = Object.freeze({ ...(surfaceTarget || {}) });
      if (
        options.force !== true
        && this.surfaceTarget
        && JSON.stringify(this.surfaceTarget) === JSON.stringify(requested)
        && this.workspace.target().resident_id
      ) {
        return this.detail;
      }
      this.surfaceTarget = null;
      void this.setResident("", { load: false });
      const binding = { revision, targetRevision: this.targetRevision };
      this.pendingSurfaceBinding = binding;
      this.workspace.setLoading("正在解析当前页面的 Resident 和对话…");
      const isCurrent = () => revision === this.surfaceRevision && binding.targetRevision === this.targetRevision;
      try {
        const detail = await this.options.resolveResident(requested, this, options);
        if (!isCurrent()) return null;
        const residentId = String(
          detail?.resident_id
          || detail?.resident?.resident_id
          || (detail?.chat_workspace?.target?.kind === "resident" ? detail.chat_workspace.target.id : "")
          || "",
        ).trim();
        if (!residentId) {
          if (detail?.binding_state !== "unbound" || !this.options.resolveBeforeSend) {
            throw new Error("Resident Chat surface did not resolve resident_id");
          }
          this.surfaceTarget = requested;
          this.detail = detail;
          this.initialContext = this.options.initialContext?.(requested, detail) || null;
          this.showState(detail.chat_workspace);
          this.workspace.clearLoading();
          this.options.onDetail?.(detail, this);
          return detail;
        }
        this.surfaceTarget = requested;
        const loading = this.setResident({
          resident_id: residentId,
          lineage_id: String(detail?.lineage_id || detail?.resident?.lineage_id || ""),
        }, {
          ...options,
          load: true,
        });
        binding.targetRevision = this.targetRevision;
        await loading;
        if (!isCurrent()) return null;
        this.initialContext = this.options.initialContext?.(requested, detail) || null;
        return this.detail;
      } catch (error) {
        if (!isCurrent()) return null;
        this.surfaceTarget = null;
        this.workspace.clearLoading();
        this.showError(error);
        throw error;
      } finally {
        if (this.pendingSurfaceBinding === binding) this.pendingSurfaceBinding = null;
      }
    }

    closeSurface() {
      this.surfaceRevision += 1;
      this.surfaceTarget = null;
      return this.setResident("", { load: false });
    }

    updateFamilyStatus(detail) {
      const family = detail?.family_facade;
      const roster = value => JSON.stringify([value?.family_id, value?.title,
        (value?.nodes || value?.members || []).map(node => [node.resident_id, node.title, node.role])]);
      const changed = roster(this.familyStatusFamily) !== roster(family);
      const residentId = String(detail?.resident?.resident_id || detail?.resident_id || "");
      if (!family?.family_id || !residentId || this.familyStatusResidentId !== residentId) {
        this.familyStatusView?.destroy(); this.familyStatusView = null;
        this.familyStatusButton = null;
      }
      this.familyStatusFamily = family?.family_id ? family : null;
      this.familyStatusResidentId = this.familyStatusFamily ? residentId : "";
      if (changed) this.familyStatusView?.refresh();
    }

    openFamilyStatus(button) {
      if (!this.familyStatusResidentId) return;
      if (this.familyStatusButton !== button) { this.familyStatusView?.destroy(); this.familyStatusView = null; }
      this.familyStatusButton = button;
      this.familyStatusView ||= createFamilyStatusView(this.familyStatusResidentId, button,
        summary => { button.title = window.PortalI18n.text`查看 Family 成员状态 · ${summary}`; }, () => this.familyStatusFamily);
      if (this.familyStatusView.isOpen()) this.familyStatusView.close();
      else this.familyStatusView.open();
    }

    applySnapshot(snapshot, update = null) {
      if (
        this.conversationResetSuppressSnapshots
        && this.conversationResetScope === this.workspace.sessionScope()
      ) {
        return this.detail;
      }
      if (!this.residentProjection) {
        this.pendingSnapshot = snapshot;
        return null;
      }
      this.pendingSnapshot = null;
      if (this.appendSnapshotMessage(snapshot, update)) return this.detail;
      const detail = this.residentProjection;
      const contract = this.contractFromDetail(detail);
      if (!contract) throw new Error("Resident projection is missing Chat Workspace contract");
      const projected = projectResidentConversation({ ...detail, chat_workspace: contract }, snapshot);
      const chatContract = this.projectContract(projected.chat_workspace, projected, snapshot, update);
      this.detail = {
        ...projected,
        chat_workspace: chatContract,
        snapshot,
        state: projected.resident_state,
      };
      this.updateFamilyStatus(this.detail);
      this.workspace.applyContract(chatContract);
      this.questions.render(snapshot);
      this.confirmSubmittedDraft(snapshot);
      this.refreshPendingTurnStopCapability();
      this.readObserver.update(snapshot);
      this.options.onDetail?.(this.detail, this, update);
      this.syncActions();
      return this.detail;
    }

    appendSnapshotMessage(snapshot, update) {
      if (update?.type !== "event" || update.event?.event_type !== "conversation.message.appended"
        || snapshot.local_submissions?.length || this.workspace.state.messages.some(item => item.local_delivery)
        || snapshot.family_facade || this.conversationView()?.project || !this.detail?.chat_workspace) return false;
      const messages = snapshot.messages;
      const prior = this.detail.messages;
      const incoming = update.event?.payload?.message;
      if (!Array.isArray(messages) || !Array.isArray(prior) || !incoming?.message_id
        || incoming.released_with || messages.length !== prior.length + 1
        || String(messages[messages.length - 1]?.message_id || "") !== String(incoming.message_id)
        || (prior.length && String(messages[messages.length - 2]?.message_id || "")
          !== String(prior[prior.length - 1]?.message_id || ""))) return false;
      const projected = residentProtocolMessageToWorkspace(incoming);
      if (!this.workspace.appendMessage(projected)) return false;
      this.detail = {
        ...this.detail,
        messages: this.workspace.state.messages,
        chat_workspace: this.workspace.contract(),
        snapshot,
      };
      const parts = Array.isArray(incoming.content_parts) ? incoming.content_parts : [];
      if (parts.some(part => part?.type === "async_questions" || nativeInteractionNames.has(part?.tool_name))) {
        this.questions.render(snapshot);
      }
      this.workspace.confirmSubmittedMessages([incoming.message_id]);
      this.refreshPendingTurnStopCapability();
      this.readObserver.update(snapshot);
      this.options.onDetail?.(this.detail, this, update);
      this.syncActions();
      return true;
    }

    confirmSubmittedDraft(snapshot, scope) {
      if (!snapshot) return;
      this.workspace.confirmSubmittedMessages([
        ...(snapshot.messages || []).map(message => message.message_id),
        ...(snapshot.input_states || []).map(input => input.message_id),
      ], scope);
    }

    applyDetail(detail, expectedScope = this.workspace.sessionScope()) {
      if (expectedScope !== this.workspace.sessionScope()) return null;
      this.residentProjection = detail;
      this.updateFamilyStatus(detail);
      this.detail = detail;
      const snapshot = this.protocol?.snapshot?.() || this.pendingSnapshot;
      if (snapshot) return this.applySnapshot(snapshot);
      const contract = this.contractFromDetail(detail);
      if (contract) {
        const chatContract = this.projectContract(contract, detail, null, null);
        this.detail = { ...detail, chat_workspace: chatContract };
        this.workspace.applyContract(chatContract);
        this.refreshPendingTurnStopCapability();
      }
      this.options.onDetail?.(this.detail, this, null);
      this.syncActions();
      return this.detail;
    }

    async reload(options = {}) {
      if (!this.workspace.target().resident_id) return null;
      return this.setResident(this.workspace.target(), {
        load: true,
        force: options.force !== false,
      });
    }

    // What this person typed in the conversation, newest first, a page at a
    // time for the input history: the server lists it.
    async loadInputHistory(scope, pageToken = "") {
      const residentId = this.workspace.target().resident_id;
      const revision = this.targetRevision;
      if (!residentId || scope !== this.workspace.inputHistoryScope()) return null;
      // Rendering the unavailable state can refresh input history as the old
      // messages disappear. Keep its identity without reading that binding again;
      // an explicit reload or target switch creates a new revision.
      if (revision === this.unavailableTargetRevision) return null;
      const isCurrent = () => revision === this.targetRevision
        && residentId === this.workspace.target().resident_id
        && scope === this.workspace.inputHistoryScope();
      let page;
      try {
        page = await window.PortalResidentService.inputHistory(residentId, { pageToken });
      } catch (error) {
        if (!isCurrent()) return null;
        if ((error?.code === "resident.deleted" || Number(error?.status) === 404)
          && this.rejectUnavailableTarget(error)) return null;
        throw error;
      }
      if (!isCurrent()) return null;
      return {
        messages: (Array.isArray(page?.messages) ? page.messages : []).map(residentProtocolMessageToWorkspace),
        next_page_token: String(page?.next_page_token || ""),
      };
    }

    async loadOlder() {
      if (!this.protocol) return null;
      return this.protocol.loadOlder({
        limit: Number(
          this.options.historyLimit
          || DEFAULT_RESIDENT_HISTORY_PAGE_SIZE,
        ),
      });
    }

    async resetConversation(operation) {
      if (this.conversationResetInFlight) return null;
      if (typeof operation !== "function") {
        throw new Error("Resident Chat reset requires an operation");
      }
      this.questions.reset();
      const targetScope = this.workspace.sessionScope();
      const contract = this.contractFromDetail(this.detail || {});
      this.conversationResetInFlight = true;
      this.conversationResetSuppressSnapshots = true;
      this.conversationResetScope = targetScope;
      this.pendingSnapshot = null;
      if (contract) {
        const resetContract = {
          ...contract,
          messages: [],
          runtime: { live: false, details: [] },
          composer: {
            ...(contract.composer || {}),
            can_send: false,
            can_stop: false,
          },
        };
        this.detail = { ...(this.detail || {}), chat_workspace: resetContract };
        this.workspace.applyContract(resetContract);
        this.options.onDetail?.(this.detail, this, { type: "conversation.reset.pending" });
      }
      try {
        const result = await operation(this);
        window.PortalResidentProjection?.invalidate?.(
          this.workspace.target().resident_id,
        );
        if (targetScope === this.workspace.sessionScope()) {
          this.conversationResetSuppressSnapshots = false;
          await this.reload({ force: true });
        }
        return result;
      } catch (error) {
        if (targetScope === this.workspace.sessionScope()) {
          this.conversationResetSuppressSnapshots = false;
          try {
            await this.reload({ force: true });
          } catch (_reloadError) {
            // Preserve the original reset failure; normal protocol recovery remains active.
          }
        }
        throw error;
      } finally {
        this.conversationResetSuppressSnapshots = false;
        this.conversationResetInFlight = false;
        this.conversationResetScope = "";
      }
    }

    refreshView() {
      const projection = this.residentProjection;
      if (!projection) return null;
      const snapshot = this.protocol?.snapshot?.() || this.pendingSnapshot;
      const contract = this.contractFromDetail(projection);
      if (!contract) return this.detail;
      const detail = snapshot
        ? projectResidentConversation({ ...projection, chat_workspace: contract }, snapshot)
        : projection;
      const chatContract = this.projectContract(
        detail.chat_workspace || contract,
        detail,
        snapshot,
        { type: "view.refreshed" },
      );
      this.detail = { ...detail, chat_workspace: chatContract };
      this.workspace.applyContract(chatContract);
      this.readObserver.update(snapshot);
      return this.detail;
    }

    showState(contract = {}) {
      const next = contract && typeof contract === "object" ? contract : {};
      if (next.schema !== RESIDENT_CHAT_WORKSPACE_SCHEMA) {
        throw new Error(`Resident Chat Workspace requires schema ${RESIDENT_CHAT_WORKSPACE_SCHEMA}`);
      }
      const normalized = {
        schema: RESIDENT_CHAT_WORKSPACE_SCHEMA,
        title: String(next.title || this.options.title || window.PortalI18n.t("Chat Workspace")),
        subtitle: String(next.subtitle || ""),
        header: next.header && typeof next.header === "object" ? next.header : {},
        messages: Array.isArray(next.messages) ? next.messages : [],
        runtime: next.runtime && typeof next.runtime === "object"
          ? next.runtime
          : { live: false, details: [] },
        rendering: next.rendering && typeof next.rendering === "object"
          ? next.rendering
          : {},
        composer: next.composer && typeof next.composer === "object"
          ? next.composer
          : { can_send: false, can_stop: false },
        actions: next.actions && typeof next.actions === "object" ? next.actions : {},
      };
      this.detail = { ...(this.detail || {}), chat_workspace: normalized };
      this.workspace.applyContract(normalized);
      return this.detail;
    }

    showEmpty(options = {}) {
      return this.showState({
        schema: RESIDENT_CHAT_WORKSPACE_SCHEMA,
        title: options.title || this.options.title || window.PortalI18n.t("Chat Workspace"),
        subtitle: options.subtitle || "",
        rendering: {
          empty_text: options.emptyText || "暂无对话。",
          user_name: options.userName,
          assistant_name: options.assistantName,
        },
        composer: {
          placeholder: options.placeholder || window.PortalI18n.t("选择会话后开始对话"),
          meta: options.meta || "当前没有可用会话",
          can_send: false,
          can_edit: false,
          can_stop: false,
          can_attach: false,
          refs_enabled: false,
          can_auto: false,
          history_scope: options.historyScope || "",
        },
      });
    }

    showError(error, options = {}) {
      const message = String(error?.message || error || window.PortalI18n.t("Resident Chat 加载失败"));
      return this.showState({
        schema: RESIDENT_CHAT_WORKSPACE_SCHEMA,
        title: options.title || this.options.title || window.PortalI18n.t("Chat Workspace"),
        subtitle: options.subtitle || window.PortalI18n.t("Resident Chat 加载失败"),
        messages: [{
          id: newMessageIdentity(),
          role: "portal",
          name: "Portal",
          content: message,
        }],
        rendering: {
          user_name: options.userName,
          assistant_name: options.assistantName,
        },
        composer: {
          placeholder: options.placeholder || window.PortalI18n.t("加载失败"),
          meta: options.meta || "等待服务恢复…",
          can_send: false,
          can_edit: false,
          can_stop: false,
          can_attach: false,
          refs_enabled: false,
          can_auto: false,
          history_scope: options.historyScope || "",
        },
      });
    }

    pendingTurnForTarget(target = this.workspace.target()) {
      return [...this.pendingTurnSubmissions.values()].find(pending => pending.scope === residentSessionScope(target)) || null;
    }

    pendingTurnStopCapability(composer, snapshot) {
      const scope = residentSessionScope(this.workspace.target());
      for (const [id, pending] of this.pendingTurnSubmissions) {
        if (!pending.acknowledged || pending.scope !== scope) continue;
        // DeliverMessage can commit before its input state exists. Keep this
        // Editor's exact-message cancellation handle until an authoritative
        // active control, visible queue item or terminal state takes over.
        // A receipt or an unrelated event cursor alone is not that handoff.
        const terminal = (snapshot?.input_states || []).some(input => input.message_id === id
          && ["completed", "failed", "cancelled"].includes(input.state));
        const active = snapshot?.state?.active_message_id === id && snapshot?.controls?.can_stop === true;
        const queued = (snapshot?.queue?.items || []).some(input => input.message_id === id);
        if (terminal || active || queued) this.pendingTurnSubmissions.delete(id);
      }
      const stop = this.stopRequest;
      if (stop && (stop.scope !== residentSessionScope(this.workspace.target())
        || (snapshot?.input_states || []).some(input => input.message_id === stop.messageId
          && ["completed", "failed", "cancelled"].includes(input.state))
        || (Number(snapshot?.cursor || 0) > stop.cursor
          && snapshot?.state?.active_message_id !== stop.messageId
          && this.pendingTurnForTarget()?.messageId !== stop.messageId))) {
        this.stopRequest = null;
      }
      const stopping = Boolean(this.stopRequest);
      this.workspace.setStopPending(stopping);
      const protocolCanStop = snapshot
        ? snapshot.controls?.can_stop === true
        : Boolean(composer.can_stop);
      return stopping || protocolCanStop || Boolean(this.pendingTurnForTarget());
    }

    refreshPendingTurnStopCapability() {
      const composer = this.workspace?.state?.composer;
      if (!this.workspace?.state?.contract || !composer) return;
      const canStop = this.pendingTurnStopCapability(composer, this.protocol?.snapshot?.() || this.pendingSnapshot);
      if (Boolean(composer.can_stop) === canStop) {
        this.workspace.updateControls();
        return;
      }
      this.workspace.setComposer({ ...composer, can_stop: canStop });
    }

    async send(payload = this.workspace.payload(), submitOptions = {}) {
      if (this.pendingSurfaceBinding?.targetRevision === this.targetRevision) return null;
      // An attachment-only message still names what it sends, so it has a text part.
      const attachmentText = composerPayloadContentParts(payload).filter(part => part.type === "text")
        .map(part => String(part.text || "")).join("\n").trim();
      if (!String(payload?.text || payload?.plainText || "").trim() && attachmentText) {
        payload = { ...payload, text: attachmentText, plainText: attachmentText };
      }
      if (!String(payload?.text || payload?.plainText || "").trim()) return null;
      const view = this.conversationView();
      if (view?.send && !String(payload.text || payload.plainText || "").startsWith("/")) return view.send(payload, submitOptions, this);
      if (!this.target().resident_id && this.options.resolveBeforeSend) {
        if (this.preparingResident) return null;
        const revision = this.targetRevision;
        let preparationRevision = revision;
        const initialDraft = this.workspace.draftText();
        this.preparingResident = true;
        this.workspace.setBusy(true);
        this.workspace.setStatus("正在准备 Resident…");
        try {
          const surface = this.surfaceTarget;
          const identity = await this.options.resolveBeforeSend(surface, payload);
          if (revision !== this.targetRevision) return null;
          const draft = this.workspace.draftText();
          const attachments = this.workspace.attachments();
          const refs = this.workspace.refs();
          const pendingDraftScope = this.workspace.draftScope();
          if (draft !== initialDraft) submitOptions = { ...submitOptions, clear: false };
          const binding = this.setResident(identity);
          preparationRevision = this.targetRevision;
          try {
            await binding;
          } finally {
            if (preparationRevision === this.targetRevision) {
              this.workspace.setDraftText(draft, attachments);
              this.workspace.setRefs(refs);
            }
          }
          if (preparationRevision !== this.targetRevision) return null;
          // Move the pre-Resident composition to its conversation. Leaving a
          // copy under the host/card scope would revive it after a later send.
          this.workspace.saveDraft({ immediate: true });
          if (pendingDraftScope !== this.workspace.draftScope()) {
            this.workspace.clearDraft(pendingDraftScope, { text: draft, attachments, refs });
          }
          this.initialContext = this.options.initialContext?.(surface || identity, surface ? identity : this.detail) || null;
        } catch (error) {
          if (preparationRevision === this.targetRevision) {
            this.workspace.setStatus(`Resident 准备失败，结果未确认：${error?.message || "准备请求未完成"}`);
          }
          // Preserve the original receipt for the host's error/recovery path;
          // preparation failure is not a confirmed message or a retry request.
          throw error;
        } finally {
          this.preparingResident = false;
          if (preparationRevision === this.targetRevision) this.workspace.setBusy(false);
        }
      }
      const target = this.workspace.target();
      const content = String(payload?.text || payload?.plainText || "").trim();
      if (!target.resident_id || !content) return null;
      const submittedTarget = normalizeResidentTarget({
        ...target,
        title: this.detail?.resident?.title || target.title,
      });
      const pending = {
        scope: residentSessionScope(target),
        messageId: "",
        promise: null,
        stopPromise: null,
      };
      // A quote names only the quoted message; the platform fills in its author and excerpt (decision_c524c775e0be).
      const quote = content.startsWith("/") || submitOptions.quote === false ? null : this.workspace.quote();
      // A correction written from a running subagent's card goes into the current turn once sent.
      const steer = this.subagentSteer?.scope === residentSessionScope(target) && content.includes(this.subagentSteer.prefix)
        ? this.subagentSteer
        : null;
      if (steer) this.subagentSteer = null;
      const submission = this.workspace.submit(async (submitted, context) => {
        pending.messageId = context.messageIdentity;
        this.pendingTurnSubmissions.set(context.messageIdentity, pending);
        this.refreshPendingTurnStopCapability();
        const contentParts = composerPayloadContentParts(submitted);
        if (refCount(submitted.refs)) {
          contentParts.push({ type: "context_reference", context: refsPayload(submitted.refs) });
        }
        const initialContext = this.initialContext;
        const submittedDetail = this.detail;
        return window.PortalResidentProtocol.sendInput({
          lineage_id: target.lineage_id,
          resident_id: target.resident_id,
          message: {
            message_id: context.messageIdentity,
            role: "user",
            content_parts: contentParts,
            ...(quote ? { quote: { message_id: quote.message_id } } : {}),
          },
        }, {
          preview: quote ? { quote } : {},
          prepare: async (body, snapshot) => {
            if (submitOptions.includeContext === false) return;
            const currentContext = await this.options.submissionContext?.(submittedTarget, submittedDetail);
            const firstMessage = !(snapshot?.messages || []).some(item => messageRole(item) === "human");
            if (currentContext || (initialContext && firstMessage)) {
              body.message.content_parts.push({ type: "context_reference", context: currentContext || initialContext });
            }
          },
        });
      }, {
        payload,
        historyText: String(payload?.plainText || content).trim(),
        rememberHistory: submitOptions.rememberHistory,
        clear: submitOptions.clear,
        optimistic: true,
        focus: submitOptions.focus,
        clearRefsOnSuccess: submitOptions.clearRefsOnSuccess !== false,
        onSuccess: (result, context) => {
          pending.acknowledged = true;
          if (context.scope === this.workspace.sessionScope()) this.initialContext = null;
          if (steer) this.sendSubagentSteerNow(target, context.messageIdentity);
          // Host callbacks describe the submitted conversation even if this
          // Editor was retargeted while waiting for its acknowledgement.
          const sentContext = { ...context, target: submittedTarget };
          this.options.onAcknowledgement?.(result, sentContext, this);
          this.options.onSent?.(result, this, sentContext);
        },
        onRollback: (error, context) => {
          const snapshot = this.snapshot();
          if (snapshot?.resident_id === target.resident_id) this.confirmSubmittedDraft(snapshot, context.scope);
          return this.options.onRollback?.(error, context, this);
        },
      });
      pending.promise = submission;
      try {
        return await submission;
      } finally {
        if (!pending.acknowledged) this.pendingTurnSubmissions.delete(pending.messageId);
        this.refreshPendingTurnStopCapability();
      }
    }

    async stop() {
      if (this.familyStatusFamily) return this.stopFamily();
      const target = this.workspace.target();
      const snapshot = this.protocol?.snapshot?.() || {};
      const pending = this.pendingTurnForTarget(target);
      const messageId = String(pending?.messageId || snapshot?.state?.active_message_id || "").trim();
      if (!target.resident_id || !messageId) return null;
      const scope = residentSessionScope(target);
      if (this.stopRequest?.scope === scope && this.stopRequest.messageId === messageId) {
        return this.stopRequest.promise;
      }
      // This is this Editor's pending action, not a persisted message or an
      // optimistic change to the Resident's authoritative work/input state.
      const request = { scope, messageId, cursor: Number(snapshot.cursor || 0), promise: null };
      this.stopRequest = request;
      this.refreshPendingTurnStopCapability();
      request.promise = this.requestStop(target, pending, messageId).then(result => {
        if ((!result || ["completed", "failed", "cancelled"].includes(result.input_state?.state)) && this.stopRequest === request) {
          this.stopRequest = null;
          this.refreshPendingTurnStopCapability();
        }
        return result;
      }).catch(error => {
        if (this.stopRequest === request) {
          this.stopRequest = null;
          this.refreshPendingTurnStopCapability();
        }
        throw error;
      });
      return request.promise;
    }

    // A facade's Stop stops the whole Family's execution, so it asks first:
    // each running request is stopped at its member, each one being routed is withdrawn.
    async stopFamily() {
      const target = this.target();
      const { active, waiting } = familyExecution(this.protocol?.snapshot?.() || {});
      if (!target.resident_id || !active.length) return null;
      const counts = new Map();
      active.forEach(request => {
        const label = request.state === "routing" ? window.PortalI18n.t("正在选择成员") : request.member;
        counts.set(label, (counts.get(label) || 0) + 1);
      });
      const confirmed = await window.PortalUI.confirm({
        title: window.PortalI18n.t("停止整个 Family 的执行？"),
        message: window.PortalI18n.text`会停止这个 Family 正在执行的全部 ${active.length} 条请求：成员正在处理的停止本轮，正在选择成员的撤回。`,
        description: waiting ? window.PortalI18n.text`排队中的 ${waiting} 条请求不受影响，会照常开始；需要时可以逐条撤回。` : "",
        metadata: [...counts].map(([label, count]) => ({ label, value: `${count} 条` })),
        confirmLabel: window.PortalI18n.t("全部停止"),
        tone: "danger",
      });
      if (!confirmed) return null;
      return this.runAction(async () => {
        const results = await Promise.allSettled(active.map(request => window.PortalResidentProtocol.cancelInput(
          { lineage_id: target.lineage_id, resident_id: target.resident_id, message_id: request.message_id })));
        const failed = results.filter(result => result.status === "rejected");
        if (failed.length === results.length) throw failed[0].reason;
        if (failed.length) {
          window.PortalUI?.toast?.(`已停止 ${results.length - failed.length} 条，另有 ${failed.length} 条没有停止：${failed[0].reason?.message || failed[0].reason}`, "warning");
        }
        return results;
      });
    }

    async requestStop(target, pending, messageId) {
      if (pending?.messageId) {
        if (pending.stopPromise) return pending.stopPromise;
        pending.stopPromise = (async () => {
          try {
            await pending.promise;
          } catch (_error) {
            return null;
          }
          if (pending.scope !== residentSessionScope(this.workspace.target())) return null;
          return window.PortalResidentProtocol.cancelInput({
            lineage_id: target.lineage_id,
            resident_id: target.resident_id,
            message_id: pending.messageId,
          });
        })().catch(error => {
          pending.stopPromise = null;
          throw error;
        });
        return pending.stopPromise;
      }
      return window.PortalResidentProtocol.cancelInput({
        lineage_id: target.lineage_id,
        resident_id: target.resident_id,
        message_id: messageId,
      });
    }

    // Hover hosts change presentation without resizing the mounted workspace.
    setPresented(presented) {
      this.presented = Boolean(presented);
      this.readObserver.schedule();
    }
    target() { return this.workspace.target(); }
    surface() { return this.surfaceTarget; }
    currentDetail() { return this.detail; }
    sessionScope() { return this.workspace.sessionScope(); }
    snapshot() { return this.protocol?.snapshot?.() || this.pendingSnapshot; }
    restore(saved = {}) {
      const target = normalizeResidentTarget(saved.target || {});
      if (!target.resident_id) return;
      // Restore only the Editor binding. Conversation data and permissions must
      // come from a new server read, never from the saved protocol snapshot.
      this.workspace.setTarget(target);
      this.setResident(target, { load: true }).catch(() => {});
    }
    isConversationResetPending() {
      return Boolean(
        this.conversationResetInFlight
        && this.conversationResetScope === this.workspace.sessionScope()
      );
    }
    attachments() { return this.workspace.attachments(); }
    setBusy(value) { return this.workspace.setBusy(value); }
    setStatus(value) { return this.workspace.setStatus(value); }
    updateControls() { return this.workspace.updateControls(); }
    destroy() {
      this.readObserver.destroy();
      this.updateFamilyStatus(null);
      this.selectionController?.destroy();
      this.surfaceRevision += 1;
      this.targetRevision += 1;
      this.unsubscribeProjection?.();
      this.unsubscribeProjection = null;
      this.pendingTurnSubmissions.clear();
      this.pendingProtocol?.destroy?.();
      this.pendingProtocol = null;
      this.protocol?.destroy?.();
      this.protocol = null;
      this.workspace.destroy();
      this.questions.destroy();
    }
  }

  function createResidentPanel(options = {}) {
    return new ResidentChatPanel(options);
  }

  function requestWorkspaceAction(contract, actionName, payload = {}) {
    const action = contract?.actions?.[String(actionName || "")];
    const instruction = String(action?.instruction || "").trim();
    if (instruction) {
      const args = {
        ...(action.arguments && typeof action.arguments === "object" ? action.arguments : {}),
        ...(payload && typeof payload === "object" ? payload : {}),
      };
      // Session restarts keep their own pending-receipt bookkeeping.
      if (instruction === "RestartSession") {
        return window.PortalResidentService.restartSession({ ...(payload || {}), resident_id: args.resident_id });
      }
      return window.PortalInstructionClient.execute(instruction, args, {
        idempotencyKey: `workspace-action:${globalThis.PortalUIPrimitives.randomUUID()}`,
      });
    }
    const url = String(action?.url || "").trim();
    const method = String(action?.method || "POST").trim().toUpperCase();
    if (!url) throw new Error(`当前 Chat Workspace 没有提供 ${actionName} 能力`);
    if (method !== "POST") throw new Error(`Chat Workspace 暂不支持 ${method} action`);
    return window.PortalAPI.post(url, {
      ...(action.payload && typeof action.payload === "object" ? action.payload : {}),
      ...(payload && typeof payload === "object" ? payload : {}),
    });
  }

  window.PortalChatWorkspace = {
    residentHandoffParts,
    messageRole,
    messageQuoteExcerpt,
    residentMessageFooter,
    noticeQueueCleared,
    formatTokenCount,
    residentMessageBody,
    requestProgressHtml,
    messageAttachmentRefHtml,
    resourceSupportsTextPreview,
    loadResourcePreview,
    bindResourceChips,
    renderMarkdownWithAttachmentRefs,
    normalizeMessageStream,
    newMessageIdentity,
    messageIdentity,
    openImagePreview,
    closeImagePreview,
    bindImagePreview,
    attachmentKind,
    normalizeComposerAttachments,
    inputMediaPayload,
    composerPayloadContentParts,
    persistedDraftAttachments,
    composerAttachmentPlaceholder,
    insertComposerAttachmentRef,
    removeComposerAttachmentRef,
    refreshComposerAttachmentRefs,
    serializeComposerInput,
    setComposerDraftInput,
    renderComposerAttachmentTray,
    bindComposerAttachmentActions,
    inputText,
    setInputText,
    createDraftStore,
    insertPlainText,
    handleAttachmentPaste,
    handleAttachmentDrop,
    handleAttachmentDragOver,
    bindWorkspaceComposer,
    normalizeRefs,
    refCount,
    refsPayload,
    messageListHtml,
    createMessageSelectionController,
    defaultWorkspaceUpload,
    residentRuntimeStatusText,
    residentRuntimeSettingRows,
    showResidentBackendNotices,
    createRuntimeSettingsDialog,
    notifyRuntimeSettingsSaved,
    createSessionRestartDialog,
    openFamilyStatus,
    mountFamilyStatus,
    residentAgentHarnessSettings,
    residentHarnessMenuHtml,
    mountResidentHarnessPanel,
    residentSessionScope,
    openResidentForwardDialog,
    createWorkspace,
    residentProtocolMessageToWorkspace,
    projectResidentConversation,
    loadResidentConversation,
  };
  const panelPackage = {
    type: "chat",
    version: 1,
    component: "PortalChatWorkspace.ResidentChatPanel",
    presentation: { title: window.PortalI18n.t("Chat Workspace"), icon: "chat" },
    sizing: { minWidth: 280, minHeight: 220 },
    instances: "multiple",
    capabilities: ["conversation", "runtime-settings", "references", "snapshot", "restore"],
    behavior: { canHide: true, canCollapse: true, canMove: true, closable: true },
    context: { channels: ["conversationTarget", "resident", "residentId", "runtime"] },
    commands: [{
      id: "send",
      title: window.PortalI18n.t("发送 Chat 消息"),
      capability: "conversation",
      execute: ({ panel, args }) => panel.send(args?.payload),
    }, {
      id: "stop",
      title: window.PortalI18n.t("停止 Chat 任务"),
      capability: "conversation",
      execute: ({ panel }) => panel.stop(),
    }, {
      id: "refresh",
      title: window.PortalI18n.t("刷新 Chat Workspace"),
      capability: "conversation",
      execute: ({ panel }) => panel.reload(),
    }],
    persistence: {
      schemaVersion: 2,
      serialize: (_snapshot, { runtime }) => ({ target: runtime.target() }),
    },
    create: createResidentPanel,
  };
  window.PortalChatWorkspace.panelPackage = Object.freeze(panelPackage);
  if (window.PortalWorkbench) {
    if (typeof window.PortalWorkbench.registerPanelPackage !== "function") {
      throw new Error("PortalChatWorkspace requires the PortalWorkbench Panel Package registry.");
    }
    window.PortalWorkbench.registerPanelPackage(panelPackage);
  }
  requireImageEditor().catch(error => window.PortalUI.toast(error.message, "error"));
}());
