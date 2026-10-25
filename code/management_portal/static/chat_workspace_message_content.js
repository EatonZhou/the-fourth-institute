(() => {
  if (window.PortalChatWorkspaceMessageContent) return;

  // What a chat message shows besides its text: Resident handoff cards and
  // forwarded references, the runtime status of a reply and its response sources,
  // attachment and resource references in Markdown, deferred images, and link
  // and image previews.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml", "resourceChips",
  ]);

  const MESSAGE_ATTACHMENT_REF_RE = /\[(image|file):\s*([^\]]+)\]/g;
  const MESSAGE_REFERENCE_REF_RE = /\[引用[:：]\s*([^\]\n]+)\]/g;
  const MARKDOWN_IMAGE_RE = /!\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+["'][^"']*["'])?\)/g;

  const FORWARD_MESSAGE_TAG_RE = /^<\s*(\/?)\s*forward(?:[\s_-]+)message\s*>$/i;
  const FORWARD_REFERENCE_SPEAKER_RE = /^([A-Za-z][A-Za-z0-9_. -]{0,79}):[ \t]*(.*)$/;

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

  function runtimeStatusLabel(status) {
    const value = String(status || "").trim();
    return value === "in_progress" ? "running" : value;
  }

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

  function loadDeferredImage(image) {
    if (!image || image.hasAttribute("src")) return;
    const source = String(image.dataset.portalDeferredImage || "").trim();
    if (source) image.setAttribute("src", source);
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

  function mediaPartPath(part) {
    const source = String(part?.source || part?.path || part?.url || "").trim();
    if (source) return source;
    const uploadId = String(part?.upload_id || "").trim();
    return /^upload_[0-9a-f]+$/.test(uploadId) ? `/api/uploads/${uploadId}` : "";
  }

  function createMessageContent(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceMessageContent is missing: ${missing.join(", ")}`);
    const {
      escapeHtml, resourceChips,
    } = dependencies;

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

    let imageEditorLoad = null;

    async function requireImageEditor() {
      if (window.PortalImageEditor?.open) return window.PortalImageEditor;
      if (!imageEditorLoad) {
        const { loadAsset } = window.PortalUIPrimitives;
        void loadAsset("/static/portal_image_editor.css").catch(() => {});
        imageEditorLoad = loadAsset("/static/portal_image_editor.js", { ready: () => window.PortalImageEditor?.open })
          .then(() => {
            if (!window.PortalImageEditor?.open) throw new Error("图片查看器初始化失败");
            return window.PortalImageEditor;
          }, () => { throw new Error("图片查看器加载失败，请重试"); })
          .catch(error => { imageEditorLoad = null; throw error; });
      }
      return imageEditorLoad;
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

    function bindImagePreview(root, options = {}) {
      if (window.PortalImageEditor) return window.PortalImageEditor.bind(root, options);
      return requireImageEditor().then(editor => editor.bind(root, options))
        .catch(error => window.PortalUI.toast(error.message, "error"));
    }

    return Object.freeze({
      residentHandoffCards, residentHandoffParts, renderResidentHandoffs, parseForwardReference,
      requireImageEditor, runtimeStatusLabel, RUNTIME_PHASE_STATUS, PRE_PROVIDER_RUNTIME_PHASES,
      runtimeActivityPhase, runtimePhaseStatusHtml, runtimeDetailsHtml, responseSourcesHtml,
      requestWorkspacePreview, bindResponseSourcePaths, messageAttachmentRefHtml,
      resourceSupportsTextPreview, loadResourcePreview, bindResourceChips,
      linkifyResourceAddresses, renderMarkdownWithAttachmentRefs, bindAttachmentRefs,
      openImagePreview, closeImagePreview, bindLinkPreview, bindImagePreview,
      attachmentPreviewUrl, attachmentKind, mediaPartPath,
    });
  }

  window.PortalChatWorkspaceMessageContent = Object.freeze({
    createMessageContent, dependencies: DEPENDENCIES, residentHandoffCards, residentHandoffParts,
    parseForwardReference, runtimeStatusLabel, runtimeActivityPhase, requestWorkspacePreview,
    bindResponseSourcePaths, closeImagePreview, bindLinkPreview, attachmentPreviewUrl,
    attachmentKind, mediaPartPath,
  });
})();
