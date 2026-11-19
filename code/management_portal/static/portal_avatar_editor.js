(() => {
  "use strict";
  // The avatar row of the User, Resident, Project and Lineage Menus: 编辑头像
  // unfolds the ocean sprites and an upload, the Menu's portrait previews the
  // choice, and the caller's Update instruction saves it.
  const t = value => window.PortalI18n.t(value);
  const AVATAR_SIZE = 256;
  const TYPES = ["image/png", "image/jpeg", "image/webp"];
  let assets = null;
  let spriteList = null;
  const reads = new Map();

  // Check container animation markers before rasterizing: a canvas must not
  // silently turn an unsupported animated avatar into a still image.
  async function checkStillImage(file) {
    const header = new Uint8Array(await file.slice(0, 32).arrayBuffer());
    const tag = (bytes, start) => String.fromCharCode(...bytes.slice(start, start + 4));
    const invalid = () => new Error(t("图片无法读取，请选择有效的 PNG、JPEG 或 WebP 图片。"));
    const animated = () => new Error(t("暂不支持动画头像，请选择静态图片。"));
    if (header[0] === 0xff && header[1] === 0xd8 && header[2] === 0xff) return;
    if (tag(header, 0) === "RIFF" && tag(header, 8) === "WEBP") {
      if (tag(header, 12) === "VP8X" && (header[20] & 2)) throw animated();
      return;
    }
    if (![137, 80, 78, 71, 13, 10, 26, 10].every((byte, index) => header[index] === byte)) throw invalid();
    for (let offset = 8; offset + 8 <= file.size;) {
      const chunk = new Uint8Array(await file.slice(offset, offset + 8).arrayBuffer());
      const length = new DataView(chunk.buffer).getUint32(0);
      if (offset + length + 12 > file.size) throw invalid();
      const type = tag(chunk, 4);
      if (type === "acTL") throw animated();
      if (type === "IDAT") return;
      offset += length + 12;
    }
    throw invalid();
  }

  async function prepareAvatar(file) {
    await checkStillImage(file);
    const bitmap = await createImageBitmap(file);
    const canvas = document.createElement("canvas");
    try {
      const scale = Math.min(1, AVATAR_SIZE / bitmap.width, AVATAR_SIZE / bitmap.height);
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const context = canvas.getContext("2d");
      if (!context) throw new Error(t("无法处理图片，请重试。"));
      context.imageSmoothingQuality = "high";
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error(t("图片压缩失败，请重试。"));
      // Only this normalized image crosses the upload boundary, never file.
      return new File([blob], "avatar.png", {type: "image/png"});
    } finally {
      bitmap.close();
      canvas.width = canvas.height = 0;
    }
  }

  function loadAssets() {
    const loadAsset = window.PortalUIPrimitives.loadAsset;
    assets ||= Promise.all([
      loadAsset("/static/portal_resident_avatar.css"),
      loadAsset("/static/portal_resident_avatar.js", {ready: () => window.PortalResidentAvatar}),
    ]).catch(error => { assets = null; throw error; });
    return assets;
  }

  // The 42 ocean sprites with their rarity, read once per page.
  function sprites() {
    spriteList ||= window.PortalAPI.get("/api/account/avatar").then(result => result.sprites || [])
      .catch(error => { spriteList = null; throw error; });
    return spriteList;
  }

  // A saved avatar: kind is user, resident, lineage or project; null when it has none.
  // Users and Residents are both principals. Lists reuse what this page read
  // before; an editor asks again ({fresh: true}).
  function read(kind, id, {fresh = false} = {}) {
    const key = `${kind}:${id}`;
    const field = kind === "resident" || kind === "user" ? "principal" : kind;
    if (fresh || !reads.has(key)) {
      reads.set(key, window.PortalAPI.get(`/api/account/avatar?${field}=${encodeURIComponent(id)}`)
        .then(result => result?.avatar || null, error => {
          if (error?.status === 404) return null;
          reads.delete(key);
          throw error;
        }));
      if (reads.size > 400) reads.delete(reads.keys().next().value);
    }
    return reads.get(key);
  }

  function remember(kind, id, avatar) {
    reads.set(`${kind}:${id}`, Promise.resolve(avatar || null));
    window.dispatchEvent(new CustomEvent("portal:avatar-changed", {detail: {kind, id, avatar: avatar || null}}));
  }

  // Avatar marks: <span class="portalAvatarMark" data-avatar-kind data-avatar-id
  // data-avatar-size data-avatar-glyph> holding the owner's initials. A saved
  // avatar replaces the initials; a mark whose avatar was removed shows them again.
  const MARK = "[data-avatar-kind][data-avatar-id]";
  function showMark(mark, avatar) {
    if (!mark.isConnected) return;
    const glyph = mark.dataset.avatarGlyph || "";
    if (!avatar) {
      if (mark.classList.contains("has-avatar")) mark.textContent = glyph;
      mark.classList.remove("has-avatar");
      return;
    }
    const size = Number(mark.dataset.avatarSize) || 24;
    mark.innerHTML = window.PortalResidentAvatar.markup(avatar, {label: mark.dataset.avatarLabel || glyph, size});
    mark.classList.add("has-avatar");
  }
  function hydrate(root) {
    const marks = [...(root?.querySelectorAll?.(MARK) || [])];
    if (root?.matches?.(MARK)) marks.unshift(root);
    if (!marks.length) return Promise.resolve();
    return loadAssets().then(() => Promise.all(marks.map(mark => read(mark.dataset.avatarKind, mark.dataset.avatarId)
      .then(avatar => showMark(mark, avatar), () => {}))));
  }
  window.addEventListener("portal:avatar-changed", event => {
    const {kind, id, avatar} = event.detail || {};
    if (!kind || !id) return;
    const marks = document.querySelectorAll(`[data-avatar-kind="${CSS.escape(kind)}"][data-avatar-id="${CSS.escape(id)}"]`);
    if (marks.length) void loadAssets().then(() => marks.forEach(mark => showMark(mark, avatar))).catch(() => {});
  });

  // The portrait of a saved avatar, or the initial when there is none.
  function paint(node, avatar, {label, size = 56} = {}) {
    if (!avatar) {
      node.replaceChildren(document.createTextNode(Array.from(String(label || "?"))[0]?.toUpperCase() || "?"));
      node.classList.add("is-initial");
      return;
    }
    node.classList.remove("is-initial");
    node.innerHTML = window.PortalResidentAvatar.markup(avatar, {label, size});
  }

  // mount(row, {portrait, label, load, save, clearable, hint, onSaved, notice})
  //   load() -> the saved avatar or null; save(change) -> the saved avatar or null,
  //   where change is {sprite_index}, {upload_id} or null (clearable only).
  function mount(row, options) {
    const {portrait, label = "", clearable = false} = options;
    row.classList.add("menuRow", "avatarEditorRow");
    row.innerHTML = window.PortalI18n.html`<div class="menuRowCopy"><strong>头像</strong><small data-avatar-hint></small></div>
      <button class="portalButton is-compact" data-avatar-open type="button" disabled>编辑头像</button>
      <div class="menuRowEditor avatarEditor" data-avatar-editor hidden>
        <div class="avatarEditorSprites" data-avatar-sprites role="group" aria-label="选择海洋头像"></div>
        <div class="avatarEditorActions">
          <label class="portalButton is-compact avatarEditorUpload"><input data-avatar-file type="file" accept="image/png,image/jpeg,image/webp" /><span>上传图片</span></label>
          <small>PNG / JPEG / WebP，自动压缩，仅上传处理后的头像</small>
          <button class="portalButton is-compact is-quiet" data-avatar-clear type="button" hidden>恢复默认</button>
          <button class="portalButton is-compact" data-avatar-cancel type="button">Cancel</button>
          <button class="portalButton is-compact is-primary" data-avatar-save type="button" disabled>保存头像</button>
        </div>
        <p class="avatarEditorStatus" data-avatar-status role="status" aria-live="polite"></p>
      </div>`;
    const $ = name => row.querySelector(`[data-avatar-${name}]`);
    const [hint, open, editor, grid, file, clear, cancel, saveButton, status] =
      ["hint", "open", "editor", "sprites", "file", "clear", "cancel", "save", "status"].map($);
    hint.textContent = options.hint || t("选择海洋头像，或上传自己的图片。");
    let current;
    let loaded = false;
    // undefined: no change; null: back to the default; otherwise a sprite or a file.
    let draft;
    let previewUrl = "";
    let saving = false;
    let destroyed = false;

    const releasePreview = () => { if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = ""; };
    const message = (text, tone = "") => { status.textContent = text; status.dataset.tone = tone; };
    const shown = () => draft === undefined ? current : draft;
    function render() {
      if (destroyed) return;
      if (previewUrl) {
        const image = document.createElement("img");
        image.src = previewUrl; image.alt = t("待保存的头像预览");
        image.width = options.size || 56; image.height = options.size || 56;
        portrait.classList.remove("is-initial");
        portrait.replaceChildren(image);
      } else if (loaded) {
        paint(portrait, shown(), {label, size: options.size || 56});
      }
      open.disabled = !loaded;
      saveButton.disabled = draft === undefined || saving;
      cancel.disabled = saving;
      file.disabled = !loaded || saving;
      clear.hidden = !clearable || !shown();
      clear.disabled = saving;
      const selected = shown();
      grid.querySelectorAll("button").forEach(button => {
        button.disabled = saving;
        button.setAttribute("aria-pressed", String(selected?.kind === "sprite" && selected.sprite_index === Number(button.dataset.avatarSprite)));
      });
    }
    function reset() {
      releasePreview(); draft = undefined; file.value = ""; message(""); render();
    }
    function close() {
      reset();
      editor.hidden = true;
      open.hidden = false;
    }
    open.addEventListener("click", () => {
      editor.hidden = false;
      open.hidden = true;
      (grid.querySelector('[aria-pressed="true"]') || grid.querySelector("button") || cancel).focus();
    });
    cancel.addEventListener("click", () => { if (!saving) close(); });
    clear.addEventListener("click", () => {
      if (saving) return;
      releasePreview(); file.value = "";
      draft = null;
      message(t("未保存：将恢复为名称首字，请点击保存头像。"));
      render();
    });
    grid.addEventListener("click", event => {
      const button = event.target.closest("[data-avatar-sprite]");
      if (!button || saving) return;
      releasePreview(); file.value = "";
      const index = Number(button.dataset.avatarSprite);
      const same = current?.kind === "sprite" && current.sprite_index === index;
      draft = same ? undefined : {kind: "sprite", sprite_index: index, sprite_rarity: button.dataset.avatarRarity};
      message(same ? "" : t("未保存：预览已更新，请点击保存头像。"));
      render();
    });
    file.addEventListener("change", () => {
      if (saving) return;
      const picked = file.files[0];
      releasePreview();
      if (!picked) { reset(); return; }
      if (!TYPES.includes(picked.type)) {
        reset();
        message(t("请选择 PNG、JPEG 或 WebP 图片。"), "error");
        return;
      }
      draft = {kind: "file", file: picked};
      previewUrl = URL.createObjectURL(picked);
      message(t("未保存：图片仅在本地预览，请点击保存头像。"));
      render();
    });
    saveButton.addEventListener("click", async () => {
      if (draft === undefined || saving) return;
      saving = true; message(t("保存中…")); render();
      try {
        let change = null;
        if (draft?.kind === "sprite") change = {sprite_index: draft.sprite_index};
        if (draft?.kind === "file") {
          message(t("正在压缩头像…"));
          const image = await prepareAvatar(draft.file);
          if (destroyed) return;
          message(t("保存中…"));
          const uploaded = await window.PortalInstructionClient.upload(image, {purpose: "avatar"});
          change = {upload_id: uploaded.upload_id};
        }
        if (destroyed) return;
        const saved = await options.save(change);
        if (destroyed) return;
        current = saved || null;
        saving = false;
        close();
        options.notice ? options.notice(t("头像已保存。"), "success") : message(t("头像已保存。"), "success");
        options.onSaved?.(current);
      } catch (error) {
        if (destroyed) return;
        message(window.PortalI18n.text`保存失败：${error.message || t("请重试")}。选择已保留。`, "error");
      } finally {
        saving = false;
        render();
      }
    });

    void (async () => {
      try {
        await loadAssets();
        const [avatar, list] = await Promise.all([options.load(), sprites()]);
        if (destroyed) return;
        current = avatar || null;
        for (const item of list) {
          const button = document.createElement("button");
          button.type = "button"; button.className = "portalButton";
          button.dataset.avatarSprite = String(item.sprite_index);
          button.dataset.avatarRarity = item.sprite_rarity || "";
          button.innerHTML = window.PortalResidentAvatar.markup(item, {label: window.PortalI18n.text`海洋头像 ${item.sprite_index + 1}`, size: 34});
          grid.append(button);
        }
        loaded = true;
        render();
      } catch (error) {
        if (destroyed) return;
        hint.textContent = error.message || t("头像加载失败");
        hint.dataset.tone = "error";
      }
    })();

    return {
      close,
      isBusy: () => saving,
      hasUnsavedChanges: () => draft !== undefined,
      destroy() { destroyed = true; releasePreview(); draft = undefined; },
    };
  }

  window.PortalAvatarEditor = Object.freeze({mount, read, remember, paint, hydrate, loadAssets});
})();
