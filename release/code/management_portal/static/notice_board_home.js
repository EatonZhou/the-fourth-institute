// The Notice Board panel on Home and its post and compose dialogs (web.home_panel,
// notice_board_home.html). Home inserts this script before its own and connects the
// panel through PortalHomeComponents.registerPanel.
(() => {
"use strict";

const noticeBoardRoot = document.querySelector("[data-notice-board]");
const noticeBoardDetailTitle = document.getElementById("noticeBoardDetailTitle");
const noticeBoardDetailBody = document.getElementById("noticeBoardDetailBody");
const noticeBoardPinBtn = document.getElementById("noticeBoardPinBtn");
const noticeBoardFeaturedBtn = document.getElementById("noticeBoardFeaturedBtn");
const noticeBoardEditPostBtn = document.getElementById("noticeBoardEditPostBtn");
const noticeBoardDeletePostBtn = document.getElementById("noticeBoardDeletePostBtn");
const noticeBoardReplyForm = document.getElementById("noticeBoardReplyForm");
const noticeBoardReplyInput = document.getElementById("noticeBoardReplyInput");
const noticeBoardReplyImageTray = document.getElementById("noticeBoardReplyImageTray");
const noticeBoardReplyUploadStatus = document.getElementById("noticeBoardReplyUploadStatus");
const noticeBoardReplyFileInput = document.getElementById("noticeBoardReplyFileInput");
const noticeBoardReplyAttach = document.getElementById("noticeBoardReplyAttach");
const noticeBoardReplyMention = document.getElementById("noticeBoardReplyMention");
const noticeBoardReplyMentionMenu = document.getElementById("noticeBoardReplyMentionMenu");
const noticeBoardReplySubmit = document.getElementById("noticeBoardReplySubmit");
const noticeBoardComposeForm = document.getElementById("noticeBoardComposeForm");
const noticeBoardComposeTitle = document.getElementById("noticeBoardComposeTitle");
const noticeBoardComposeTitleInput = document.getElementById("noticeBoardComposeTitleInput");
const noticeBoardComposeCategoryInput = document.getElementById("noticeBoardComposeCategoryInput");
const noticeBoardComposeContentInput = document.getElementById("noticeBoardComposeContentInput");
const noticeBoardComposeImageTray = document.getElementById("noticeBoardComposeImageTray");
const noticeBoardComposeUploadStatus = document.getElementById("noticeBoardComposeUploadStatus");
const noticeBoardComposeFileInput = document.getElementById("noticeBoardComposeFileInput");
const noticeBoardComposeAttach = document.getElementById("noticeBoardComposeAttach");
const noticeBoardComposeMention = document.getElementById("noticeBoardComposeMention");
const noticeBoardComposeMentionMenu = document.getElementById("noticeBoardComposeMentionMenu");
const noticeBoardComposeSubmit = document.getElementById("noticeBoardComposeSubmit");
const noticeBoardApi = window.PortalAPI.createClient({ cache: "no-store" });
const NOTICE_BOARD_IMAGE_UPLOAD_TIMEOUT_MS = 60 * 60 * 1000;
let portalUsername = "anonymous";
let noticeBoardData = null;
let noticeBoardCurrentPost = null;
let noticeBoardComposeMode = "create";
let noticeBoardComposeAttachments = null;
let noticeBoardReplyAttachments = null;
let noticeBoardMentionUsersPromise = null;
let noticeBoardShowFolded = false;
let noticeBoardCategoryFilter = "all";
let noticeBoardDetailRequest = 0;
let noticeBoardRefresh = null;
let noticeBoardHtml = "";
const noticeBoardDetailDialog = window.PortalUI.createDialogController({
  modal: "noticeBoardDetailOverlay",
  initialFocus: "noticeBoardDetailTitle",
  backdropClose: true,
  escapeClose: true,
  onClose: () => { noticeBoardDetailRequest += 1; },
});
const noticeBoardComposeDialog = window.PortalUI.createDialogController({
  modal: "noticeBoardComposeOverlay",
  initialFocus: "noticeBoardComposeTitleInput",
  backdropClose: true,
  escapeClose: true,
});
// /?notice_post=<post ID> opens that post once the board has loaded.
const requestedNoticePost = String(new URLSearchParams(window.location.search).get("notice_post") || "").trim();
let requestedNoticePostOpened = false;

function formatPortalTime(value) {
  return window.PortalUI.formatDateTime(value, { style: "compact" });
}

function esc(value) {
  return window.PortalUIPrimitives.escapeHtml(value);
}

function noticeBoardMarkdown(content) {
  const text = String(content || "").trim();
  if (!text) return "";
  return window.MD?.render
    ? window.MD.render(text)
    : `<p>${esc(text).replace(/\n/g, "<br>")}</p>`;
}

function loadNoticeBoardMentionUsers() {
  if (!noticeBoardMentionUsersPromise) {
    noticeBoardMentionUsersPromise = noticeBoardApi.get("/api/users").then(payload => {
      const users = Array.isArray(payload) ? payload : [];
      const names = users.map(user => String(
        user?.id || user?.username || user?.name || ""
      ).trim()).filter(Boolean);
      return [...new Set(names)].sort((left, right) => left.localeCompare(right));
    }).catch(error => {
      noticeBoardMentionUsersPromise = null;
      throw error;
    });
  }
  return noticeBoardMentionUsersPromise;
}

function enhanceNoticeBoardMentions(root) {
  if (!root) return;
  const nodes = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue?.includes("@")) return NodeFilter.FILTER_REJECT;
      if (node.parentElement?.closest("a, code, pre, .notice-board-mention")) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(node => {
    const text = node.nodeValue || "";
    const pattern = /(?<![A-Za-z0-9_@])@([A-Za-z0-9_+\-]+(?:\.[A-Za-z0-9_+\-]+)*)/g;
    let cursor = 0;
    let matched = false;
    const fragment = document.createDocumentFragment();
    for (const match of text.matchAll(pattern)) {
      matched = true;
      if (match.index > cursor) fragment.append(text.slice(cursor, match.index));
      const mention = document.createElement("span");
      mention.className = "notice-board-mention";
      mention.textContent = match[0];
      fragment.append(mention);
      cursor = match.index + match[0].length;
    }
    if (!matched) return;
    if (cursor < text.length) fragment.append(text.slice(cursor));
    node.replaceWith(fragment);
  });
}

function noticeBoardToolbarHtml(postCount = 0, featuredCount = 0, unreadCount = 0) {
  return window.PortalI18n.html`
    <div class="notice-board-toolbar">
      <span class="notice-board-count">
        ${unreadCount ? window.PortalI18n.html`<span class="notice-board-unread-dot" aria-hidden="true"></span><span class="notice-board-unread-count">${Number(unreadCount)} 未读</span><span>·</span>` : ""}
        <span>${Number(postCount || 0)} 帖${featuredCount ? window.PortalI18n.text` · ${Number(featuredCount)} 精华` : ""}</span>
      </span>
      <div class="notice-board-actions">
        <button class="portalButton is-icon is-quiet" type="button" data-notice-refresh aria-label="刷新 Notice Board" title="刷新" data-portal-icon="refresh"></button>
        <button class="portalButton is-compact is-primary" type="button" data-notice-compose data-portal-icon="plus">发布新帖</button>
      </div>
    </div>
  `;
}

function noticeBoardCategories() {
  const categories = Array.isArray(noticeBoardData?.categories)
    ? noticeBoardData.categories.filter(category => category?.id && category?.label).map(category => ({ ...category, label: window.PortalI18n.t(category.label) }))
    : [];
  return categories.length ? categories : [{ id: "other", label: window.PortalI18n.t("其他") }];
}

function noticeBoardCategoryLabel(categoryId) {
  return noticeBoardCategories().find(category => category.id === categoryId)?.label || window.PortalI18n.t("其他");
}

function noticeBoardCategoryFiltersHtml() {
  const categories = [{ id: "all", label: window.PortalI18n.t("全部") }, ...noticeBoardCategories()];
  return window.PortalI18n.html`<div class="notice-board-categories" aria-label="帖子分类">${categories.map(category => window.PortalI18n.html`
    <button class="notice-board-category-filter${noticeBoardCategoryFilter === category.id ? " is-active" : ""}" type="button" data-notice-category="${esc(category.id)}" aria-pressed="${noticeBoardCategoryFilter === category.id ? "true" : "false"}">${esc(category.label)}</button>
  `).join("")}</div>`;
}

function noticeBoardRowHtml(post) {
  const updated = post.updated_at ? formatPortalTime(post.updated_at) : "";
  const replies = Number(post.reply_count || 0);
  const flags = [
    window.PortalI18n.html`<span class="notice-board-category-badge">${esc(window.PortalI18n.t(post.category_label || noticeBoardCategoryLabel(post.category)))}</span>`,
    post.pinned ? window.PortalI18n.html`<span class="notice-board-row-pin">置顶</span>` : "",
    post.featured ? window.PortalI18n.html`<span class="notice-board-row-featured">精华</span>` : "",
  ].filter(Boolean).join("");
  return window.PortalI18n.html`
    <button class="notice-board-row${post.unread ? " is-unread" : ""}" type="button" data-notice-post-id="${esc(post.post_id || "")}"${post.unread ? ` aria-label="${esc(post.unread_kind === "reply" ? window.PortalI18n.t("有新回复") : window.PortalI18n.t("新帖子"))}：${esc(post.title || window.PortalI18n.t("未命名帖子"))}"` : ""}>
      <span class="notice-board-row-title-wrap">
        ${post.unread ? window.PortalI18n.html`<span class="notice-board-unread-dot" title="${post.unread_kind === "reply" ? window.PortalI18n.t("有新回复") : window.PortalI18n.t("新帖子")}" aria-hidden="true"></span>` : ""}
        <span class="notice-board-row-title">${esc(post.title || window.PortalI18n.t("未命名帖子"))}</span>
      </span>
      <span class="notice-board-row-meta">${esc(post.author || "anonymous")} · ${replies} 回复${updated ? ` · ${esc(updated)}` : ""}</span>
      ${flags ? window.PortalI18n.html`<span class="notice-board-row-flags">${flags}</span>` : ""}
    </button>
  `;
}

function renderNoticeBoard(data) {
  if (!noticeBoardRoot) return;
  noticeBoardData = data || { posts: [], post_count: 0 };
  const posts = Array.isArray(noticeBoardData.posts) ? noticeBoardData.posts : [];
  if (noticeBoardCategoryFilter !== "all" && !noticeBoardCategories().some(category => category.id === noticeBoardCategoryFilter)) {
    noticeBoardCategoryFilter = "all";
  }
  const visiblePosts = noticeBoardCategoryFilter === "all"
    ? posts
    : posts.filter(post => (post.category || "other") === noticeBoardCategoryFilter);
  const activePosts = visiblePosts.filter(post => !post.folded);
  const foldedPosts = visiblePosts.filter(post => post.folded);
  const foldedUnread = foldedPosts.filter(post => post.unread).length;
  if (!foldedPosts.length) noticeBoardShowFolded = false;
  const staleDays = Number(noticeBoardData.stale_after_days || 30);
  const foldedSection = foldedPosts.length ? window.PortalI18n.html`
    <button class="notice-board-folded-toggle" type="button" data-notice-folded-toggle aria-expanded="${noticeBoardShowFolded ? "true" : "false"}">
      <span class="notice-board-folded-label"><span data-portal-icon="${noticeBoardShowFolded ? "chevron-down" : "chevron-right"}" aria-hidden="true"></span>较早帖子${foldedUnread ? window.PortalI18n.html`<span class="notice-board-unread-dot" title="${foldedUnread} 条未读" aria-hidden="true"></span>` : ""}</span>
      <span class="notice-board-folded-meta">${foldedPosts.length} 帖 · ${staleDays} 天未更新</span>
    </button>
    <div class="notice-board-folded-list" ${noticeBoardShowFolded ? "" : "hidden"}>
      ${noticeBoardShowFolded ? foldedPosts.map(noticeBoardRowHtml).join("") : ""}
    </div>
  ` : "";
  const html = window.PortalI18n.html`
    <div class="notice-board-panel">
      ${noticeBoardCategoryFiltersHtml()}
      <div class="notice-board-list">
        ${activePosts.map(noticeBoardRowHtml).join("")}
        ${foldedSection}
        ${visiblePosts.length ? "" : window.PortalI18n.html`<div class="notice-board-empty">${posts.length ? window.PortalI18n.t("这个分类还没有帖子。") : window.PortalI18n.t("还没有帖子。")}</div>`}
      </div>
      ${noticeBoardToolbarHtml(
        visiblePosts.length,
        visiblePosts.filter(post => post.featured).length,
        visiblePosts.filter(post => post.unread).length,
      )}
    </div>
  `;
  if (html !== noticeBoardHtml) {
    noticeBoardRoot.innerHTML = html;
    noticeBoardHtml = html;
  }
}

async function loadNoticeBoard() {
  if (!noticeBoardRoot) return;
  try {
    const coordinator = ensureNoticeBoardRefresh();
    coordinator.start();
    await coordinator.refresh({ force: true });
    if (requestedNoticePost && !requestedNoticePostOpened) {
      requestedNoticePostOpened = true;
      openNoticeBoardPost(requestedNoticePost);
    }
  } catch (err) {
    noticeBoardHtml = "";
    noticeBoardRoot.innerHTML = window.PortalI18n.html`
      <div class="notice-board-panel">
        <div class="notice-board-error">${esc(window.PortalUI.errorMessage(window.PortalI18n.t("读取 Notice Board"), err))}</div>
        ${noticeBoardToolbarHtml(0)}
      </div>
    `;
  }
}

function ensureNoticeBoardRefresh() {
  if (noticeBoardRefresh) return noticeBoardRefresh;
  noticeBoardRefresh = window.PortalRefreshCoordinator.create({
    key: () => `notice-board:${portalUsername || "anonymous"}`,
    interval: 60000,
    initialDelay: 60000,
    fetch: () => noticeBoardInstruction("ListPosts"),
    apply: renderNoticeBoard,
    subscribe: window.PortalRefreshCoordinator.portalSubscription({
      domains: ["projection"],
      refreshOnAttach: false,
      debounceMs: 200,
      matches: detail => {
        if (detail?.snapshot_required) return true;
        const changes = detail?.projection_changes?.["notice-board"] || {};
        return changes && typeof changes === "object" && Object.keys(changes).length > 0;
      },
    }),
  });
  return noticeBoardRefresh;
}

function renderNoticeBoardPost(payload) {
  const post = payload?.post || {};
  noticeBoardCurrentPost = post;
  noticeBoardDetailTitle.textContent = post.title || window.PortalI18n.t("帖子");
  noticeBoardPinBtn.hidden = false;
  noticeBoardFeaturedBtn.hidden = false;
  noticeBoardPinBtn.textContent = post.pinned ? window.PortalI18n.t("取消置顶") : window.PortalI18n.t("置顶");
  noticeBoardPinBtn.dataset.pinned = post.pinned ? "1" : "0";
  noticeBoardFeaturedBtn.textContent = post.featured ? window.PortalI18n.t("取消精华") : window.PortalI18n.t("设为精华");
  noticeBoardFeaturedBtn.dataset.featured = post.featured ? "1" : "0";
  noticeBoardEditPostBtn.hidden = !post.can_edit;
  noticeBoardDeletePostBtn.hidden = !post.can_delete;
  noticeBoardReplyForm.hidden = false;
  const replies = Array.isArray(post.replies) ? post.replies : [];
  noticeBoardDetailBody.innerHTML = window.PortalI18n.html`
    <article>
      <div class="notice-board-post-meta"><span class="notice-board-category-badge">${esc(window.PortalI18n.t(post.category_label || noticeBoardCategoryLabel(post.category)))}</span> · ${esc(post.author || "anonymous")} · ${esc(formatPortalTime(post.created_at) || "")}${post.edited_at ? window.PortalI18n.text` · 编辑于 ${esc(formatPortalTime(post.edited_at) || "")}` : ""}${post.pinned ? window.PortalI18n.text` · 由 ${esc(post.pinned_by || window.PortalI18n.t("管理员"))} 置顶` : ""}${post.featured ? window.PortalI18n.text` · 由 ${esc(post.featured_by || window.PortalI18n.t("管理员"))} 设为精华` : ""}</div>
      <div class="notice-board-markdown">${noticeBoardMarkdown(post.content)}</div>
    </article>
    <section class="notice-board-replies" aria-label="回复">
      <div class="notice-board-replies-title">${replies.length} 条回复</div>
      ${replies.length ? replies.map(reply => window.PortalI18n.html`
        <article class="notice-board-reply">
          <div class="notice-board-floor">#${Number(reply.floor || 0)}</div>
          <div class="notice-board-reply-content">
            <div class="notice-board-reply-meta-row">
              <div class="notice-board-reply-meta">${esc(reply.author || "anonymous")} · ${esc(formatPortalTime(reply.created_at) || "")}</div>
              ${reply.can_delete ? window.PortalI18n.html`<button class="portalButton is-icon is-quiet is-compact notice-board-delete-action" type="button" data-notice-delete-reply="${esc(reply.reply_id || "")}" aria-label="删除第 ${Number(reply.floor || 0)} 楼回复" title="删除回复" data-portal-icon="trash"></button>` : ""}
            </div>
            <div class="notice-board-markdown">${noticeBoardMarkdown(reply.content)}</div>
          </div>
        </article>
      `).join("") : window.PortalI18n.html`<div class="notice-board-empty">还没有回复。</div>`}
    </section>
  `;
  enhanceNoticeBoardMentions(noticeBoardDetailBody);
  window.PortalChatWorkspace.bindImagePreview(noticeBoardDetailBody);
}

// Notice board reads and changes are HATG ISA instructions.
function noticeBoardInstruction(name, args = {}) {
  const mutates = !["ListPosts", "GetPost"].includes(name);
  return window.PortalInstructionClient.execute(name, args, mutates
    ? { idempotencyKey: `notice:${name}:${globalThis.PortalUIPrimitives.randomUUID()}` }
    : {});
}

function applyNoticeBoardReadState(postId, readResult) {
  if (!noticeBoardData || readResult?.unread) return;
  const posts = Array.isArray(noticeBoardData.posts) ? noticeBoardData.posts : [];
  const post = posts.find(item => item.post_id === postId);
  if (!post?.unread) return;
  post.unread = false;
  post.unread_kind = "";
  noticeBoardData.unread_count = Math.max(
    0,
    Number(noticeBoardData.unread_count || 0) - 1,
  );
  renderNoticeBoard(noticeBoardData);
}

async function openNoticeBoardPost(postId) {
  const cleanPostId = String(postId || "").trim();
  if (!cleanPostId) return;
  const requestId = ++noticeBoardDetailRequest;
  noticeBoardCurrentPost = null;
  noticeBoardDetailTitle.textContent = window.PortalI18n.t("正在读取帖子…");
  noticeBoardDetailBody.innerHTML = window.PortalI18n.html`<div class="notice-board-loading">正在读取帖子…</div>`;
  noticeBoardPinBtn.hidden = true;
  noticeBoardFeaturedBtn.hidden = true;
  noticeBoardEditPostBtn.hidden = true;
  noticeBoardDeletePostBtn.hidden = true;
  noticeBoardReplyForm.hidden = true;
  noticeBoardReplyAttachments.clear();
  noticeBoardDetailDialog.open();
  try {
    const payload = await noticeBoardInstruction("GetPost", { post_id: cleanPostId });
    if (requestId !== noticeBoardDetailRequest) return;
    renderNoticeBoardPost(payload);
    const notificationRevision = Number(payload?.post?.notification_revision || 0);
    if (notificationRevision > 0) {
      try {
        const readResult = await noticeBoardInstruction("MarkPostRead", {
          post_id: cleanPostId,
          notification_revision: notificationRevision,
        });
        if (requestId === noticeBoardDetailRequest) {
          applyNoticeBoardReadState(cleanPostId, readResult);
        }
      } catch {
        // Reading the post remains successful even if its reminder state cannot persist.
      }
    }
  } catch (err) {
    if (requestId !== noticeBoardDetailRequest) return;
    noticeBoardDetailTitle.textContent = window.PortalI18n.t("帖子读取失败");
    noticeBoardDetailBody.innerHTML = window.PortalI18n.html`<div class="notice-board-error">${esc(window.PortalUI.errorMessage(window.PortalI18n.t("读取帖子"), err))}</div>`;
  }
}

function noticeBoardImageName(value) {
  return String(value || "pasted-image")
    .replace(/[\[\]\\\r\n]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 240) || "pasted-image";
}

function extractNoticeBoardComposeContent(content) {
  const images = [];
  // Post images are uploads; images of older posts keep their original address.
  const pattern = /!\[([^\]\n]*)\]\((\/api\/(?:notice-board\/image\?image_id=(notice_image_[0-9a-f]{16}\.(?:png|jpe?g|gif|webp))|uploads\/(upload_[0-9a-f]{24})))\)/gi;
  const text = String(content || "").replace(pattern, (_match, label, url, noticeImageId, uploadId) => {
    const imageId = noticeImageId || uploadId;
    images.push({
      id: imageId,
      image_id: imageId,
      kind: "image",
      name: noticeBoardImageName(label || imageId),
      path: url,
      url,
      preview_url: url,
      size: 0,
      uploading: false,
      error: "",
    });
    return `[[portal-image:${imageId}]]`;
  }).trim();
  return { text, images };
}

function createNoticeBoardAttachmentComposer(options) {
  const chat = window.PortalChatWorkspace;
  const state = { items: [], pending: 0, epoch: 0 };

  const sync = () => {
    chat.renderComposerAttachmentTray(options.tray, state.items);
    chat.refreshComposerAttachmentRefs(options.input, state.items);
    options.input.classList.toggle("is-uploading", state.pending > 0);
    options.status.textContent = state.pending ? window.PortalI18n.text`正在上传 ${state.pending} 张图片…` : "";
    if (state.pending) options.submit.disabled = true;
    else if (!options.submit.dataset.submitting) options.submit.disabled = false;
  };

  const remove = id => {
    const item = state.items.find(candidate => candidate.id === id);
    if (String(item?.preview_url || "").startsWith("blob:")) URL.revokeObjectURL(item.preview_url);
    state.items = state.items.filter(candidate => candidate.id !== id);
    chat.removeComposerAttachmentRef(options.input, id);
    sync();
  };

  const upload = async (file, existingItem = null) => {
    const epoch = state.epoch;
    const token = existingItem?.id || `notice-upload-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const filename = noticeBoardImageName(file.name);
    const item = existingItem || {
      id: token,
      image_id: "",
      kind: "image",
      name: filename,
      file,
      path: "",
      url: "",
      preview_url: URL.createObjectURL(file),
      size: Number(file.size || 0),
      uploading: true,
      progress: 36,
      error: "",
    };
    if (existingItem) {
      item.file = file;
      item.uploading = true;
      item.progress = 36;
      item.error = "";
    } else {
      state.items.push(item);
      chat.insertComposerAttachmentRef(options.input, item);
    }
    state.pending += 1;
    sync();
    try {
      const payload = await window.PortalInstructionClient.upload(file, {
        purpose: "post_image",
        fileName: filename,
        mediaType: file.type || "image/png",
        timeoutMs: NOTICE_BOARD_IMAGE_UPLOAD_TIMEOUT_MS,
      });
      if (epoch !== state.epoch) return;
      const current = state.items.find(candidate => candidate.id === token);
      if (!current) return;
      if (String(current.preview_url || "").startsWith("blob:")) URL.revokeObjectURL(current.preview_url);
      current.image_id = payload.upload_id;
      current.path = payload.url;
      current.url = current.path;
      current.preview_url = current.path;
      current.size = Number(payload.size || current.size || 0);
      current.uploading = false;
      current.progress = 100;
      delete current.file;
    } catch (err) {
      if (epoch === state.epoch) {
        const current = state.items.find(candidate => candidate.id === token);
        if (current) {
          current.uploading = false;
          current.progress = 0;
          current.error = window.PortalUI.errorMessage(window.PortalI18n.t("上传图片"), err);
        }
        window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("上传图片"), err), "error");
      }
    } finally {
      if (epoch === state.epoch) {
        state.pending = Math.max(0, state.pending - 1);
        sync();
      }
    }
  };

  const retry = id => {
    const item = state.items.find(candidate => candidate.id === id);
    if (!item?.file || item.uploading || !item.error) return;
    upload(item.file, item);
  };

  const addFiles = files => {
    const images = Array.from(files || []).filter(file => String(file?.type || "").toLowerCase().startsWith("image/"));
    const available = Math.max(0, 8 - state.items.length);
    if (images.length > available) window.PortalUI.toast(window.PortalI18n.t("每条内容最多附加 8 张图片"), "info");
    images.slice(0, available).forEach(upload);
  };

  const mentionState = { range: null, length: 0, request: 0, blurTimer: 0 };

  const activeMention = () => {
    const selection = window.getSelection?.();
    if (!selection?.rangeCount) return null;
    const range = selection.getRangeAt(0);
    if (!range.collapsed || !options.input.contains(range.commonAncestorContainer)) return null;
    const before = document.createRange();
    before.selectNodeContents(options.input);
    before.setEnd(range.endContainer, range.endOffset);
    const match = before.toString().match(/(?:^|[^A-Za-z0-9_@])@([A-Za-z0-9_+\-]*(?:\.[A-Za-z0-9_+\-]+)*)$/);
    if (!match) return null;
    return { query: match[1], range: range.cloneRange(), length: match[1].length + 1 };
  };

  const hideMentions = () => {
    if (options.mentionMenu) options.mentionMenu.hidden = true;
  };

  const showMentions = async () => {
    if (!options.mentionMenu) return;
    window.clearTimeout(mentionState.blurTimer);
    const active = activeMention();
    if (!active) {
      hideMentions();
      return;
    }
    mentionState.range = active.range;
    mentionState.length = active.length;
    const request = ++mentionState.request;
    try {
      const users = await loadNoticeBoardMentionUsers();
      if (request !== mentionState.request) return;
      const query = active.query.toLowerCase();
      const matches = users.filter(username => username.toLowerCase().includes(query)).slice(0, 12);
      options.mentionMenu.innerHTML = matches.length
        ? matches.map(username => window.PortalI18n.html`<button class="notice-board-mention-option" type="button" data-notice-mention-user="${esc(username)}">@${esc(username)}</button>`).join("")
        : window.PortalI18n.html`<div class="notice-board-empty">没有匹配的用户</div>`;
      options.mentionMenu.hidden = false;
    } catch (error) {
      hideMentions();
      window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("读取用户列表"), error), "error");
    }
  };

  const insertMention = username => {
    const range = mentionState.range;
    if (!range) return;
    const count = mentionState.length;
    if (range.startContainer.nodeType === Node.TEXT_NODE && range.startOffset >= count) {
      range.setStart(range.startContainer, range.startOffset - count);
    }
    range.deleteContents();
    const node = document.createTextNode(`@${username} `);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    const selection = window.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
    options.input.dispatchEvent(new Event("input", { bubbles: true }));
    hideMentions();
    options.input.focus();
  };

  const clear = () => {
    state.epoch += 1;
    state.items.forEach(item => {
      if (String(item.preview_url || "").startsWith("blob:")) URL.revokeObjectURL(item.preview_url);
    });
    state.items = [];
    state.pending = 0;
    options.input.innerHTML = "";
    hideMentions();
    sync();
  };

  const setContent = content => {
    clear();
    const parsed = extractNoticeBoardComposeContent(content);
    state.items = parsed.images;
    chat.setComposerDraftInput(options.input, parsed.text, state.items, { focus: false });
    sync();
  };

  const serialize = () => {
    const byId = new Map(state.items.map(item => [String(item.id), item]));
    return chat.serializeComposerInput(options.input, state.items, { mode: "draft" })
      .replace(/\[\[portal-image:([^\]]+)\]\]/g, (_match, id) => {
        const item = byId.get(String(id));
        return item?.url && !item.uploading && !item.error
          ? `![${noticeBoardImageName(item.name)}](${item.url})`
          : "";
      })
      .trim();
  };

  chat.bindComposerAttachmentActions(options.input, { onRemove: remove, onRetry: retry });
  chat.bindComposerAttachmentActions(options.tray, { onRemove: remove, onRetry: retry });
  options.input.addEventListener("paste", event => {
    chat.handleAttachmentPaste(options.input, event, { onFiles: addFiles });
  });
  options.input.addEventListener("dragover", chat.handleAttachmentDragOver);
  options.input.addEventListener("drop", event => chat.handleAttachmentDrop(event, { onFiles: addFiles }));
  options.input.addEventListener("input", showMentions);
  options.input.addEventListener("blur", () => {
    mentionState.blurTimer = window.setTimeout(hideMentions, 120);
  });
  options.mentionButton?.addEventListener("click", () => {
    window.clearTimeout(mentionState.blurTimer);
    options.input.focus();
    chat.insertPlainText(options.input, "@");
    showMentions();
  });
  options.mentionMenu?.addEventListener("mousedown", event => {
    window.clearTimeout(mentionState.blurTimer);
    event.preventDefault();
  });
  options.mentionMenu?.addEventListener("click", event => {
    const button = event.target.closest("[data-notice-mention-user]");
    if (button) insertMention(button.dataset.noticeMentionUser || "");
  });
  options.attachButton.addEventListener("click", () => options.fileInput.click());
  options.fileInput.addEventListener("change", () => {
    addFiles(options.fileInput.files);
    options.fileInput.value = "";
  });
  sync();
  return { clear, setContent, serialize, isUploading: () => state.pending > 0 };
}

noticeBoardComposeAttachments = createNoticeBoardAttachmentComposer({
  input: noticeBoardComposeContentInput,
  tray: noticeBoardComposeImageTray,
  status: noticeBoardComposeUploadStatus,
  fileInput: noticeBoardComposeFileInput,
  attachButton: noticeBoardComposeAttach,
  mentionButton: noticeBoardComposeMention,
  mentionMenu: noticeBoardComposeMentionMenu,
  submit: noticeBoardComposeSubmit,
});
noticeBoardReplyAttachments = createNoticeBoardAttachmentComposer({
  input: noticeBoardReplyInput,
  tray: noticeBoardReplyImageTray,
  status: noticeBoardReplyUploadStatus,
  fileInput: noticeBoardReplyFileInput,
  attachButton: noticeBoardReplyAttach,
  mentionButton: noticeBoardReplyMention,
  mentionMenu: noticeBoardReplyMentionMenu,
  submit: noticeBoardReplySubmit,
});

function openNoticeBoardCompose(post = null) {
  const editing = Boolean(post?.post_id && post?.can_edit);
  noticeBoardComposeAttachments.clear();
  noticeBoardComposeMode = editing ? "edit" : "create";
  noticeBoardComposeForm.reset();
  noticeBoardComposeTitle.textContent = editing ? window.PortalI18n.t("编辑帖子") : window.PortalI18n.t("发布新帖");
  noticeBoardComposeSubmit.textContent = editing ? window.PortalI18n.t("保存") : window.PortalI18n.t("发布");
  noticeBoardComposeCategoryInput.innerHTML = noticeBoardCategories().map(category =>
    window.PortalI18n.html`<option value="${esc(category.id)}">${esc(category.label)}</option>`
  ).join("");
  noticeBoardComposeCategoryInput.value = editing ? String(post.category || "other") : "other";
  if (editing) {
    noticeBoardComposeTitleInput.value = String(post.title || "");
    noticeBoardComposeAttachments.setContent(post.content);
  }
  noticeBoardComposeDialog.open();
}

async function submitNoticeBoardPost(event) {
  event.preventDefault();
  const title = noticeBoardComposeTitleInput.value.trim();
  const content = noticeBoardComposeAttachments.serialize();
  const category = noticeBoardComposeCategoryInput.value;
  if (content.length > 30000) {
    window.PortalUI.toast(window.PortalI18n.t("帖子正文不能超过 30000 个字符"), "error");
    return;
  }
  if (!title || !content || noticeBoardComposeSubmit.disabled) return;
  const editingPost = noticeBoardComposeMode === "edit" ? noticeBoardCurrentPost : null;
  if (noticeBoardComposeMode === "edit" && (!editingPost?.post_id || !editingPost.can_edit)) return;
  noticeBoardComposeSubmit.dataset.submitting = "1";
  noticeBoardComposeSubmit.disabled = true;
  noticeBoardComposeSubmit.textContent = editingPost ? window.PortalI18n.t("保存中…") : window.PortalI18n.t("发布中…");
  try {
    const payload = editingPost
      ? await noticeBoardInstruction("UpdatePost", {
          post_id: editingPost.post_id,
          changes: { title, content, category },
        })
      : await noticeBoardInstruction("CreatePost", { title, content, category });
    noticeBoardComposeDialog.close();
    await loadNoticeBoard();
    if (editingPost) {
      renderNoticeBoardPost(await noticeBoardInstruction("GetPost", { post_id: editingPost.post_id }));
      window.PortalUI.toast(window.PortalI18n.t("帖子已更新"), "success");
    } else {
      window.PortalUI.toast(window.PortalI18n.t("帖子已发布"), "success");
      openNoticeBoardPost(payload?.post?.post_id);
    }
  } catch (err) {
    window.PortalUI.toast(
      window.PortalUI.errorMessage(editingPost ? window.PortalI18n.t("更新帖子") : window.PortalI18n.t("发布帖子"), err),
      "error",
    );
  } finally {
    delete noticeBoardComposeSubmit.dataset.submitting;
    noticeBoardComposeSubmit.textContent = noticeBoardComposeMode === "edit" ? window.PortalI18n.t("保存") : window.PortalI18n.t("发布");
    noticeBoardComposeSubmit.disabled = noticeBoardComposeAttachments.isUploading();
  }
}

async function submitNoticeBoardReply(event) {
  event.preventDefault();
  const postId = noticeBoardCurrentPost?.post_id || "";
  const content = noticeBoardReplyAttachments.serialize();
  if (content.length > 8000) {
    window.PortalUI.toast(window.PortalI18n.t("回复不能超过 8000 个字符"), "error");
    return;
  }
  if (!postId || !content || noticeBoardReplySubmit.disabled) return;
  noticeBoardReplySubmit.dataset.submitting = "1";
  noticeBoardReplySubmit.disabled = true;
  noticeBoardReplySubmit.textContent = window.PortalI18n.t("回复中…");
  try {
    await noticeBoardInstruction("CreateReply", { post_id: postId, content });
    noticeBoardReplyAttachments.clear();
    renderNoticeBoardPost(await noticeBoardInstruction("GetPost", { post_id: postId }));
    await loadNoticeBoard();
  } catch (err) {
    window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("回复帖子"), err), "error");
  } finally {
    delete noticeBoardReplySubmit.dataset.submitting;
    noticeBoardReplySubmit.disabled = noticeBoardReplyAttachments.isUploading();
    noticeBoardReplySubmit.textContent = window.PortalI18n.t("回复");
  }
}

async function toggleNoticeBoardPin() {
  const postId = noticeBoardCurrentPost?.post_id || "";
  if (!postId || noticeBoardPinBtn.disabled) return;
  const pinned = noticeBoardPinBtn.dataset.pinned !== "1";
  noticeBoardPinBtn.disabled = true;
  try {
    await noticeBoardInstruction("PinPost", { post_id: postId, pinned });
    renderNoticeBoardPost(await noticeBoardInstruction("GetPost", { post_id: postId }));
    await loadNoticeBoard();
    window.PortalUI.toast(pinned ? window.PortalI18n.t("帖子已置顶") : window.PortalI18n.t("已取消置顶"), "success");
  } catch (err) {
    window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("更新置顶状态"), err), "error");
  } finally {
    noticeBoardPinBtn.disabled = false;
  }
}

async function toggleNoticeBoardFeatured() {
  const postId = noticeBoardCurrentPost?.post_id || "";
  if (!postId || noticeBoardFeaturedBtn.disabled) return;
  const featured = noticeBoardFeaturedBtn.dataset.featured !== "1";
  noticeBoardFeaturedBtn.disabled = true;
  try {
    await noticeBoardInstruction("FeaturePost", { post_id: postId, featured });
    renderNoticeBoardPost(await noticeBoardInstruction("GetPost", { post_id: postId }));
    await loadNoticeBoard();
    window.PortalUI.toast(featured ? window.PortalI18n.t("帖子已设为精华") : window.PortalI18n.t("已取消精华"), "success");
  } catch (err) {
    window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("更新精华状态"), err), "error");
  } finally {
    noticeBoardFeaturedBtn.disabled = false;
  }
}

async function deleteNoticeBoardPost() {
  const post = noticeBoardCurrentPost;
  if (!post?.post_id || !post.can_delete || noticeBoardDeletePostBtn.disabled) return;
  const confirmed = await window.PortalUI.confirmAction("delete", post.title || window.PortalI18n.t("这条帖子"), {
    description: window.PortalI18n.t("帖子中的全部回复也会被删除。"),
  });
  if (!confirmed) return;
  noticeBoardDeletePostBtn.disabled = true;
  try {
    await noticeBoardInstruction("DeletePost", { post_id: post.post_id });
    noticeBoardCurrentPost = null;
    noticeBoardDetailDialog.close();
    await loadNoticeBoard();
    window.PortalUI.toast(window.PortalI18n.t("帖子已删除"), "success");
  } catch (err) {
    window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("删除帖子"), err), "error");
  } finally {
    noticeBoardDeletePostBtn.disabled = false;
  }
}

async function deleteNoticeBoardReply(replyId) {
  const post = noticeBoardCurrentPost;
  const cleanReplyId = String(replyId || "").trim();
  const reply = (post?.replies || []).find(item => item.reply_id === cleanReplyId);
  if (!post?.post_id || !reply?.can_delete) return;
  const confirmed = await window.PortalUI.confirmAction(
    "delete",
    window.PortalI18n.text`第 ${Number(reply.floor || 0)} 楼回复`,
  );
  if (!confirmed) return;
  const button = noticeBoardDetailBody.querySelector(
    `[data-notice-delete-reply="${CSS.escape(cleanReplyId)}"]`,
  );
  if (button) button.disabled = true;
  try {
    await noticeBoardInstruction("DeleteReply", {
      post_id: post.post_id,
      reply_id: cleanReplyId,
    });
    renderNoticeBoardPost(await noticeBoardInstruction("GetPost", { post_id: post.post_id }));
    await loadNoticeBoard();
    window.PortalUI.toast(window.PortalI18n.t("回复已删除"), "success");
  } catch (err) {
    if (button) button.disabled = false;
    window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("删除回复"), err), "error");
  }
}

function handleNoticeBoardRootClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  const postButton = target?.closest("[data-notice-post-id]");
  if (postButton) {
    openNoticeBoardPost(postButton.getAttribute("data-notice-post-id"));
    return;
  }
  if (target?.closest("[data-notice-refresh]")) {
    loadNoticeBoard();
    return;
  }
  const categoryButton = target?.closest("[data-notice-category]");
  if (categoryButton) {
    noticeBoardCategoryFilter = categoryButton.getAttribute("data-notice-category") || "all";
    renderNoticeBoard(noticeBoardData);
    return;
  }
  if (target?.closest("[data-notice-folded-toggle]")) {
    noticeBoardShowFolded = !noticeBoardShowFolded;
    renderNoticeBoard(noticeBoardData);
    return;
  }
  if (target?.closest("[data-notice-compose]")) openNoticeBoardCompose();
}

function handleNoticeBoardDetailClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  const deleteButton = target?.closest("[data-notice-delete-reply]");
  if (deleteButton) {
    deleteNoticeBoardReply(deleteButton.getAttribute("data-notice-delete-reply"));
  }
}

function listen(target, type, listener, signal) {
  target?.addEventListener?.(type, listener, { signal });
}

function connectNoticeBoard({ signal, context }) {
  listen(noticeBoardRoot, "click", handleNoticeBoardRootClick, signal);
  listen(noticeBoardDetailBody, "click", handleNoticeBoardDetailClick, signal);
  listen(document.getElementById("noticeBoardDetailClose"), "click", () => noticeBoardDetailDialog.close(), signal);
  listen(document.getElementById("noticeBoardComposeClose"), "click", () => noticeBoardComposeDialog.close(), signal);
  listen(document.getElementById("noticeBoardComposeCancel"), "click", () => noticeBoardComposeDialog.close(), signal);
  listen(noticeBoardComposeForm, "submit", submitNoticeBoardPost, signal);
  listen(noticeBoardReplyForm, "submit", submitNoticeBoardReply, signal);
  listen(noticeBoardPinBtn, "click", toggleNoticeBoardPin, signal);
  listen(noticeBoardFeaturedBtn, "click", toggleNoticeBoardFeatured, signal);
  listen(noticeBoardEditPostBtn, "click", () => openNoticeBoardCompose(noticeBoardCurrentPost), signal);
  listen(noticeBoardDeletePostBtn, "click", deleteNoticeBoardPost, signal);
  context.currentUserReady.then(({ username }) => {
    portalUsername = username || "anonymous";
    if (!signal.aborted) loadNoticeBoard();
  });
  return () => {
    noticeBoardRefresh?.destroy();
    noticeBoardRefresh = null;
  };
}

window.PortalHomeComponents.registerPanel({
  id: "notice-board",
  group: "get-me-aligned",
  order: 10,
  title: window.PortalI18n.t("Notice Board"),
  icon: "chat",
  source: '[data-home-component="notice-board"]',
  connect: connectNoticeBoard,
  // A link to a post opens the board even where the saved layout hides it.
  requested: Boolean(requestedNoticePost),
});
})();
