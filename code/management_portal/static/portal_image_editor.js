(function () {
  if (window.PortalImageEditor) return;
  const previewBindings = new WeakMap();

  function qs(root, selector) {
    return root ? root.querySelector(selector) : null;
  }

  function clearCanvas(canvas) {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.restore();
  }

  function lightboxPoint(event, canvas) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function drawLine(canvas, from, to, color, size) {
    const ctx = canvas.getContext("2d");
    if (!ctx || !from || !to) return;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = color || "#ff3b30";
    ctx.lineWidth = Number(size || 5);
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.stroke();
  }

  function drawRect(canvas, from, to, color, size) {
    const ctx = canvas.getContext("2d");
    if (!ctx || !from || !to) return;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = color || "#ff3b30";
    ctx.lineWidth = Number(size || 5);
    ctx.strokeRect(from.x, from.y, to.x - from.x, to.y - from.y);
  }

  function drawArrow(canvas, from, to, color, size) {
    const ctx = canvas.getContext("2d");
    if (!ctx || !from || !to) return;
    const width = Number(size || 5);
    const angle = Math.atan2(to.y - from.y, to.x - from.x);
    const headLength = Math.max(14, width * 4.2);
    const headAngle = Math.PI / 7;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = color || "#ff3b30";
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.moveTo(to.x, to.y);
    ctx.lineTo(to.x - headLength * Math.cos(angle - headAngle), to.y - headLength * Math.sin(angle - headAngle));
    ctx.moveTo(to.x, to.y);
    ctx.lineTo(to.x - headLength * Math.cos(angle + headAngle), to.y - headLength * Math.sin(angle + headAngle));
    ctx.stroke();
  }

  function drawShape(canvas, mode, from, to, color, size) {
    if (mode === "rect") drawRect(canvas, from, to, color, size);
    else if (mode === "arrow") drawArrow(canvas, from, to, color, size);
    else drawLine(canvas, from, to, color, size);
  }

  function capture(canvas) {
    const ctx = canvas?.getContext("2d");
    if (!ctx || !canvas.width || !canvas.height) return null;
    try {
      return ctx.getImageData(0, 0, canvas.width, canvas.height);
    } catch {
      return null;
    }
  }

  function restore(canvas, snapshot) {
    if (!canvas || !snapshot) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.putImageData(snapshot, 0, 0);
    ctx.restore();
  }

  function pushHistory(state, snapshot) {
    if (state.historyPushed || !snapshot) return state;
    const history = Array.isArray(state.history) ? state.history.slice() : [];
    history.push(snapshot);
    const truncated = history.length > 24;
    return {
      ...state,
      history: history.slice(-24),
      historyFloorDirty: !!state.historyFloorDirty || truncated,
      historyPushed: true,
    };
  }

  function updateControls(overlay) {
    const state = overlay?._portalImageDraw || {};
    const editable = !!state.editable;
    const ready = !!state.ready;
    const hasHistory = Array.isArray(state.history) && state.history.length > 0;
    const saving = !!state.saving;
    const undoBtn = qs(overlay, "[data-image-draw-undo]");
    const copyBtn = qs(overlay, "[data-image-copy]");
    const saveBtn = qs(overlay, "[data-image-draw-save]");
    const cancelBtn = qs(overlay, "[data-image-draw-cancel]");
    if (undoBtn) undoBtn.disabled = !editable || !ready || saving || !hasHistory;
    if (copyBtn) copyBtn.disabled = !editable || !ready || saving || !navigator.clipboard || typeof ClipboardItem === "undefined";
    if (saveBtn) {
      saveBtn.disabled = !editable || !ready || saving;
      saveBtn.textContent = saving ? window.PortalI18n.t("保存中…") : window.PortalI18n.t("保存");
    }
    if (cancelBtn) cancelBtn.disabled = saving;
    overlay?.querySelectorAll("[data-image-draw-mode], [data-image-draw-clear], [data-image-color-swatch], [data-image-draw-color], [data-image-draw-size]").forEach(el => {
      el.disabled = !editable || !ready || saving;
    });
    const color = String(qs(overlay, "[data-image-draw-color]")?.value || "#ff3b30").toLowerCase();
    overlay?.querySelectorAll("[data-image-color-swatch]").forEach(btn => {
      btn.classList.toggle("active", String(btn.dataset.imageColorSwatch || "").toLowerCase() === color);
    });
  }

  function setSaving(overlay, saving) {
    overlay._portalImageDraw = { ...(overlay?._portalImageDraw || {}), saving: !!saving };
    updateControls(overlay);
  }

  function close(overlay, options = {}) {
    if (!overlay) return true;
    return overlay._portalImageDialog?.close(options) !== false;
  }

  function setColor(overlay, color) {
    const input = qs(overlay, "[data-image-draw-color]");
    const clean = String(color || "").trim();
    if (input && clean) input.value = clean;
    updateControls(overlay);
  }

  function drawText(canvas, point, text, color, size) {
    const ctx = canvas.getContext("2d");
    if (!ctx || !point || !String(text || "").trim()) return;
    const fontSize = Math.max(18, Number(size || 5) * 4);
    ctx.fillStyle = color || "#ff3b30";
    ctx.font = `700 ${fontSize}px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    ctx.textBaseline = "top";
    ctx.fillText(String(text || "").trim(), point.x, point.y);
  }

  function normalizedRect(from, to) {
    const x = Math.min(from?.x || 0, to?.x || 0);
    const y = Math.min(from?.y || 0, to?.y || 0);
    const width = Math.abs((to?.x || 0) - (from?.x || 0));
    const height = Math.abs((to?.y || 0) - (from?.y || 0));
    return { x, y, width, height };
  }

  function drawMosaic(overlay, from, to, size) {
    const canvas = qs(overlay, "[data-lightbox-canvas]");
    const img = qs(overlay, "[data-lightbox-image]");
    const ctx = canvas?.getContext("2d");
    if (!canvas || !img || !ctx || !from || !to) return;
    const rect = normalizedRect(from, to);
    if (rect.width < 3 || rect.height < 3 || !img.naturalWidth || !img.naturalHeight) return;
    const displayWidth = Math.max(1, img.getBoundingClientRect().width);
    const displayHeight = Math.max(1, img.getBoundingClientRect().height);
    const sx = rect.x / displayWidth * img.naturalWidth;
    const sy = rect.y / displayHeight * img.naturalHeight;
    const sw = rect.width / displayWidth * img.naturalWidth;
    const sh = rect.height / displayHeight * img.naturalHeight;
    const block = Math.max(4, Number(size || 8) * 2);
    const temp = document.createElement("canvas");
    temp.width = Math.max(1, Math.round(rect.width / block));
    temp.height = Math.max(1, Math.round(rect.height / block));
    const tempCtx = temp.getContext("2d");
    if (!tempCtx) return;
    tempCtx.drawImage(img, sx, sy, sw, sh, 0, 0, temp.width, temp.height);
    ctx.save();
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(temp, 0, 0, temp.width, temp.height, rect.x, rect.y, rect.width, rect.height);
    ctx.restore();
  }

  function setMode(overlay, mode) {
    const current = overlay?._portalImageDraw || {};
    if (!current.editable || !current.ready) return;
    const nextMode = current.mode === mode ? "" : String(mode || "");
    overlay._portalImageDraw = {
      ...current,
      mode: nextMode,
      enabled: !!nextMode,
      drawing: false,
      startPoint: null,
      lastPoint: null,
      snapshot: null,
    };
    overlay.classList.toggle("drawing", !!nextMode);
    overlay.querySelectorAll("[data-image-draw-mode]").forEach(btn => {
      btn.classList.toggle("is-active", String(btn.dataset.imageDrawMode || "") === nextMode);
    });
    updateControls(overlay);
  }

  function resizeSnapshot(snapshot, width, height) {
    if (!snapshot || !width || !height) return null;
    if (snapshot.width === width && snapshot.height === height) return snapshot;
    const source = document.createElement("canvas");
    source.width = snapshot.width;
    source.height = snapshot.height;
    const sourceCtx = source.getContext("2d");
    if (!sourceCtx) return null;
    sourceCtx.putImageData(snapshot, 0, 0);
    const output = document.createElement("canvas");
    output.width = width;
    output.height = height;
    const outputCtx = output.getContext("2d");
    if (!outputCtx) return null;
    outputCtx.drawImage(source, 0, 0, width, height);
    return outputCtx.getImageData(0, 0, width, height);
  }

  function updateLightboxLayout(overlay) {
    const toolbar = qs(overlay, ".imageLightboxToolbar");
    const content = qs(overlay, ".imageLightboxContent");
    if (!overlay || !content) return;
    const overlayStyle = window.getComputedStyle(overlay);
    const contentStyle = window.getComputedStyle(content);
    const verticalPadding = (parseFloat(overlayStyle.paddingTop) || 0) + (parseFloat(overlayStyle.paddingBottom) || 0);
    const toolbarHeight = toolbar && window.getComputedStyle(toolbar).display !== "none"
      ? toolbar.getBoundingClientRect().height
      : 0;
    const gap = toolbarHeight ? (parseFloat(contentStyle.rowGap || contentStyle.gap) || 0) : 0;
    const available = Math.max(1, Math.floor(overlay.clientHeight - verticalPadding - toolbarHeight - gap));
    overlay.style.setProperty("--portal-image-lightbox-block-size", `${available}px`);
  }

  function syncCanvas(overlay, { reset = false } = {}) {
    const img = qs(overlay, "[data-lightbox-image]");
    const canvas = qs(overlay, "[data-lightbox-canvas]");
    const state = overlay?._portalImageDraw || {};
    if (!img || !canvas || !state.ready || !img.naturalWidth || !img.naturalHeight) return;
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    const pixelWidth = Math.max(1, Math.round(width * dpr));
    const pixelHeight = Math.max(1, Math.round(height * dpr));
    const previous = !reset && canvas.width && canvas.height ? capture(canvas) : null;
    const sameSize = canvas.width === pixelWidth && canvas.height === pixelHeight;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    if (!sameSize) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    const ctx = canvas.getContext("2d");
    if (!reset && !sameSize && previous) {
      const scaled = resizeSnapshot(previous, pixelWidth, pixelHeight);
      if (scaled) restore(canvas, scaled);
    }
    ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
    const history = reset
      ? []
      : (Array.isArray(state.history) ? state.history.map(item => resizeSnapshot(item, pixelWidth, pixelHeight)).filter(Boolean) : []);
    overlay._portalImageDraw = {
      ...state,
      dirty: reset ? false : !!state.dirty,
      drawing: false,
      startPoint: null,
      lastPoint: null,
      snapshot: null,
      history,
      historyFloorDirty: reset ? false : !!state.historyFloorDirty,
      historyPushed: false,
    };
    updateControls(overlay);
  }

  function scheduleLayout(overlay, { reset = false } = {}) {
    if (!overlay) return;
    overlay._portalImageResetPending = !!overlay._portalImageResetPending || reset;
    if (overlay._portalImageLayoutFrame) return;
    overlay._portalImageLayoutFrame = window.requestAnimationFrame(() => {
      overlay._portalImageLayoutFrame = 0;
      updateLightboxLayout(overlay);
      window.requestAnimationFrame(() => {
        const shouldReset = !!overlay._portalImageResetPending;
        overlay._portalImageResetPending = false;
        syncCanvas(overlay, { reset: shouldReset });
      });
    });
  }

  function editedImageBlob(overlay) {
    const img = qs(overlay, "[data-lightbox-image]");
    const canvas = qs(overlay, "[data-lightbox-canvas]");
    if (!img || !canvas || !img.naturalWidth || !img.naturalHeight) return Promise.resolve(null);
    const output = document.createElement("canvas");
    output.width = img.naturalWidth;
    output.height = img.naturalHeight;
    const ctx = output.getContext("2d");
    if (!ctx) return Promise.resolve(null);
    try {
      ctx.drawImage(img, 0, 0, output.width, output.height);
      ctx.drawImage(canvas, 0, 0, canvas.width, canvas.height, 0, 0, output.width, output.height);
    } catch (err) {
      return Promise.reject(err);
    }
    return new Promise(resolve => output.toBlob(resolve, "image/png"));
  }

  async function copyImage(overlay) {
    if (!navigator.clipboard || typeof ClipboardItem === "undefined") {
      window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("复制图片"), window.PortalI18n.t("当前浏览器不支持复制图片到剪切板。")), "error");
      return;
    }
    const copyBtn = qs(overlay, "[data-image-copy]");
    const oldText = copyBtn?.textContent || "复制";
    try {
      if (copyBtn) {
        copyBtn.disabled = true;
        copyBtn.textContent = window.PortalI18n.t("复制中");
      }
      const blob = await editedImageBlob(overlay);
      if (!blob) throw new Error("无法生成图片。");
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      if (copyBtn) copyBtn.textContent = window.PortalI18n.t("已复制");
      window.setTimeout(() => {
        if (copyBtn) copyBtn.textContent = oldText;
        updateControls(overlay);
      }, 900);
    } catch (err) {
      if (copyBtn) copyBtn.textContent = oldText;
      updateControls(overlay);
      window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("复制图片"), err), "error");
    }
  }

  function save(overlay) {
    const state = overlay?._portalImageDraw || {};
    if (!state.editable || !state.ready) return;
    if (!state.dirty) {
      close(overlay, { force: true });
      return;
    }
    setSaving(overlay, true);
    editedImageBlob(overlay).then(async blob => {
      if (!blob) {
        setSaving(overlay, false);
        window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("保存标注"), window.PortalI18n.t("无法生成图片。")), "error");
        return;
      }
      const baseName = String(state.name || state.title || "annotated-image").replace(/\.[^.]+$/, "") || "annotated-image";
      const file = new File([blob], `${baseName}-annotated.png`, { type: "image/png" });
      const previewUrl = URL.createObjectURL(file);
      try {
        if (typeof state.onSave === "function") {
          await state.onSave({ blob, file, previewUrl, state, overlay });
        }
        close(overlay, { force: true });
      } catch (err) {
        URL.revokeObjectURL(previewUrl);
        window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("保存标注"), err), "error");
      } finally {
        setSaving(overlay, false);
      }
    }).catch(err => {
      setSaving(overlay, false);
      window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("保存标注"), err), "error");
    });
  }

  function ensureOverlay() {
    let overlay = document.getElementById("portalImageEditorLightbox");
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.id = "portalImageEditorLightbox";
    overlay.className = "portalDetailOverlay imageLightbox portalImageEditor";
    overlay.hidden = true;
    overlay.dataset.portalOverlayLayer = "dialog";
    overlay.innerHTML = window.PortalI18n.html`
      <div class="portalDetailSurface is-media imageLightboxContent" data-portal-detail-surface>
        <div class="imageLightboxToolbar">
          <button class="portalButton is-compact" type="button" data-image-draw-mode="pen" title="画笔" aria-label="画笔" data-portal-icon="pen"></button>
          <button class="portalButton is-compact" type="button" data-image-draw-mode="arrow" title="箭头" aria-label="箭头" data-portal-icon="arrow-up-right"></button>
          <button class="portalButton is-compact" type="button" data-image-draw-mode="rect" title="方框" aria-label="方框" data-portal-icon="square"></button>
          <button class="portalButton is-compact" type="button" data-image-draw-mode="text" title="文字" aria-label="文字" data-portal-icon="type"></button>
          <button class="portalButton is-compact" type="button" data-image-draw-mode="mosaic" title="马赛克" aria-label="马赛克" data-portal-icon="mosaic"></button>
          <span class="imageDrawSwatches">
            ${["#ff3b30", "#00b578", "#ffc400", "#1da1f2", "#222222", "#ffffff"].map(color => window.PortalI18n.html`<button class="imageDrawSwatch ${color === "#ff3b30" ? "active" : ""}" type="button" data-image-color-swatch="${color}" style="--swatch-color:${color}" title="${color}" aria-label="颜色 ${color}"></button>`).join("")}
          </span>
          <input class="imageDrawColor" type="color" value="#ff3b30" data-image-draw-color title="画笔颜色" aria-label="画笔颜色" />
          <input class="imageDrawSize" type="range" min="2" max="24" value="5" data-image-draw-size title="画笔粗细" aria-label="画笔粗细" />
          <button class="portalButton is-compact" type="button" data-image-draw-undo title="撤销" aria-label="撤销">↶</button>
          <button class="portalButton is-compact" type="button" data-image-copy title="复制图片" aria-label="复制图片">复制</button>
          <button class="portalButton is-compact" type="button" data-image-draw-clear>清除</button>
          <button class="portalButton is-compact" type="button" data-image-draw-cancel>取消</button>
          <button class="portalButton is-compact is-primary" type="button" data-image-draw-save>保存</button>
        </div>
        <div class="imageLightboxImageWrap">
          <img alt="" data-lightbox-image />
          <canvas class="imageLightboxCanvas" data-lightbox-canvas></canvas>
          <div class="imageLightboxStatus" data-image-load-status role="status">图片加载中…</div>
        </div>
      </div>`;
    const canvas = qs(overlay, "[data-lightbox-canvas]");
    const colorInput = qs(overlay, "[data-image-draw-color]");
    const sizeInput = qs(overlay, "[data-image-draw-size]");
    overlay.querySelectorAll("[data-image-draw-mode]").forEach(btn => {
      btn.addEventListener("click", event => {
        event.stopPropagation();
        setMode(overlay, String(btn.dataset.imageDrawMode || "pen"));
      });
    });
    overlay.querySelectorAll("[data-image-color-swatch]").forEach(btn => {
      btn.addEventListener("click", event => {
        event.stopPropagation();
        setColor(overlay, String(btn.dataset.imageColorSwatch || "#ff3b30"));
      });
    });
    colorInput?.addEventListener("input", () => updateControls(overlay));
    qs(overlay, "[data-image-draw-undo]")?.addEventListener("click", event => {
      event.stopPropagation();
      const current = overlay._portalImageDraw || {};
      const history = Array.isArray(current.history) ? current.history.slice() : [];
      const previous = history.pop();
      if (!previous) return;
      restore(canvas, previous);
      overlay._portalImageDraw = { ...current, history, dirty: history.length > 0 || !!current.historyFloorDirty, drawing: false, startPoint: null, lastPoint: null, snapshot: null, historyPushed: false };
      updateControls(overlay);
    });
    qs(overlay, "[data-image-copy]")?.addEventListener("click", event => {
      event.stopPropagation();
      copyImage(overlay);
    });
    qs(overlay, "[data-image-draw-clear]")?.addEventListener("click", event => {
      event.stopPropagation();
      const current = overlay._portalImageDraw || {};
      clearCanvas(canvas);
      overlay._portalImageDraw = {
        ...current,
        dirty: false,
        history: [],
        historyFloorDirty: false,
        drawing: false,
        startPoint: null,
        lastPoint: null,
        snapshot: null,
        historyPushed: false,
      };
      updateControls(overlay);
    });
    qs(overlay, "[data-image-draw-save]")?.addEventListener("click", event => {
      event.stopPropagation();
      save(overlay);
    });
    qs(overlay, "[data-image-draw-cancel]")?.addEventListener("click", event => {
      event.stopPropagation();
      close(overlay);
    });
    canvas?.addEventListener("pointerdown", async event => {
      const current = overlay._portalImageDraw || {};
      const mode = String(current.mode || "");
      if (current.saving || !current.enabled || !mode) return;
      event.preventDefault();
      const point = lightboxPoint(event, canvas);
      if (mode === "text") {
        const text = await window.PortalUI.prompt({
          title: window.PortalI18n.t("添加文字标注"),
          label: window.PortalI18n.t("标注文字"),
          confirmLabel: "添加",
        });
        if (!String(text || "").trim()) return;
        const snapshot = capture(canvas);
        const nextState = pushHistory({ ...current, historyPushed: false }, snapshot);
        drawText(canvas, point, text, colorInput?.value || "#ff3b30", Number(sizeInput?.value || 5));
        overlay._portalImageDraw = { ...nextState, dirty: true, drawing: false, startPoint: null, lastPoint: null, snapshot: null, historyPushed: false };
        updateControls(overlay);
        return;
      }
      canvas.setPointerCapture?.(event.pointerId);
      overlay._portalImageDraw = {
        ...current,
        mode,
        drawing: true,
        color: colorInput?.value || "#ff3b30",
        size: Number(sizeInput?.value || 5),
        startPoint: point,
        lastPoint: point,
        snapshot: capture(canvas),
        historyPushed: false,
      };
    });
    canvas?.addEventListener("pointermove", event => {
      const current = overlay._portalImageDraw || {};
      const mode = String(current.mode || "pen");
      if (current.saving || !current.enabled || !current.drawing) return;
      event.preventDefault();
      const nextPoint = lightboxPoint(event, canvas);
      const nextState = pushHistory(current, current.snapshot);
      if (mode === "pen") {
        drawLine(canvas, nextState.lastPoint, nextPoint, nextState.color, nextState.size);
      } else if (mode === "mosaic") {
        restore(canvas, nextState.snapshot);
        drawRect(canvas, nextState.startPoint, nextPoint, "rgba(255,255,255,0.88)", Math.max(2, nextState.size));
      } else {
        restore(canvas, nextState.snapshot);
        drawShape(canvas, mode, nextState.startPoint, nextPoint, nextState.color, nextState.size);
      }
      overlay._portalImageDraw = { ...nextState, dirty: true, lastPoint: nextPoint };
      updateControls(overlay);
    });
    const stopDrawing = event => {
      const current = overlay._portalImageDraw || {};
      const mode = String(current.mode || "pen");
      if (!current.drawing) return;
      if (!current.saving && mode !== "pen" && event.type === "pointerup") {
        const nextPoint = lightboxPoint(event, canvas);
        const nextState = pushHistory(current, current.snapshot);
        restore(canvas, nextState.snapshot);
        if (mode === "mosaic") drawMosaic(overlay, nextState.startPoint, nextPoint, nextState.size);
        else drawShape(canvas, mode, nextState.startPoint, nextPoint, nextState.color, nextState.size);
        overlay._portalImageDraw = { ...nextState, dirty: true, lastPoint: nextPoint };
      }
      canvas?.releasePointerCapture?.(event.pointerId);
      overlay._portalImageDraw = { ...(overlay._portalImageDraw || current), drawing: false, startPoint: null, lastPoint: null, snapshot: null };
      updateControls(overlay);
    };
    canvas?.addEventListener("pointerup", stopDrawing);
    canvas?.addEventListener("pointercancel", stopDrawing);
    canvas?.addEventListener("pointerleave", stopDrawing);
    document.body.appendChild(overlay);
    if (typeof ResizeObserver === "function") {
      overlay._portalImageResizeObserver = new ResizeObserver(() => scheduleLayout(overlay));
      overlay._portalImageResizeObserver.observe(overlay);
      overlay._portalImageResizeObserver.observe(qs(overlay, ".imageLightboxToolbar"));
    } else {
      window.addEventListener("resize", () => scheduleLayout(overlay));
    }
    overlay._portalImageDialog = window.PortalUI.createDialogController({
      modal: overlay,
      bodyClass: null,
      backdropClose: true,
      escapeClose: true,
      beforeClose: () => !overlay._portalImageDraw?.saving,
      isBackdropTarget: event => event.target === overlay
        || event.target === overlay.querySelector(".imageLightboxContent")
        || event.target === overlay.querySelector(".imageLightboxImageWrap"),
      dirty: options => !options?.force && !!overlay._portalImageDraw?.dirty,
      discardMessage: window.PortalI18n.t("放弃尚未保存的图片标注？"),
      requestClose: context => close(overlay, context),
      onClose: () => overlay.classList.remove("drawing"),
    });
    return overlay;
  }

  function open(src, title = "", options = {}) {
    const url = String(src || "").trim();
    if (!url) return;
    const overlay = ensureOverlay();
    const img = qs(overlay, "[data-lightbox-image]");
    const canvas = qs(overlay, "[data-lightbox-canvas]");
    const status = qs(overlay, "[data-image-load-status]");
    const editable = options.editable === true;
    const sessionId = Number(overlay._portalImageDraw?.sessionId || 0) + 1;
    overlay._portalImageDraw = {
      title: String(title || ""),
      name: String(options.name || title || "annotated-image"),
      editable,
      ready: false,
      loadError: false,
      sessionId,
      enabled: false,
      mode: "",
      drawing: false,
      dirty: false,
      startPoint: null,
      lastPoint: null,
      snapshot: null,
      history: [],
      historyFloorDirty: false,
      historyPushed: false,
      onSave: options.onSave,
      meta: options.meta || {},
    };
    overlay.classList.remove("drawing");
    overlay.classList.toggle("editable", editable);
    overlay.querySelectorAll("[data-image-draw-mode]").forEach(btn => btn.classList.remove("is-active"));
    clearCanvas(canvas);
    canvas.style.width = "0px";
    canvas.style.height = "0px";
    canvas.width = 0;
    canvas.height = 0;
    img.hidden = false;
    if (status) {
      status.hidden = false;
      status.textContent = window.PortalI18n.t("图片加载中…");
      status.classList.remove("is-error");
    }
    setColor(overlay, "#ff3b30");
    updateControls(overlay);
    img.onload = () => {
      if (overlay._portalImageDraw?.sessionId !== sessionId) return;
      if (overlay._portalImageDraw?.ready) return;
      if (!img.naturalWidth || !img.naturalHeight) {
        img.onerror?.();
        return;
      }
      overlay._portalImageDraw = { ...overlay._portalImageDraw, ready: true, loadError: false };
      img.hidden = false;
      if (status) status.hidden = true;
      updateLightboxLayout(overlay);
      syncCanvas(overlay, { reset: true });
      updateControls(overlay);
      scheduleLayout(overlay);
    };
    img.onerror = () => {
      if (overlay._portalImageDraw?.sessionId !== sessionId) return;
      overlay._portalImageDraw = { ...overlay._portalImageDraw, ready: false, loadError: true };
      clearCanvas(canvas);
      canvas.style.width = "0px";
      canvas.style.height = "0px";
      canvas.width = 0;
      canvas.height = 0;
      img.hidden = true;
      if (status) {
        status.hidden = false;
        status.textContent = window.PortalI18n.t("图片加载失败");
        status.classList.add("is-error");
      }
      updateControls(overlay);
    };
    img.src = url;
    img.title = title || "";
    overlay._portalImageDialog.open();
    scheduleLayout(overlay);
    if (img.complete && img.naturalWidth) window.setTimeout(() => img.onload?.(), 0);
  }

  function bind(root, options = {}) {
    const nodes = root?.matches?.("[data-portal-preview-image]") ? [root] : [];
    root?.querySelectorAll?.("[data-portal-preview-image]").forEach(node => nodes.push(node));
    nodes.forEach(node => bindImage(node, options));
    return root;
  }

  function bindImage(node, options = {}) {
    if (node.matches("[data-message-attachment-path]")) return;
    const alreadyBound = previewBindings.has(node);
    previewBindings.set(node, { ...(previewBindings.get(node) || {}), ...options });
    if (alreadyBound) return;
    node.dataset.portalPreviewBound = "shared";
    node.addEventListener("click", event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (event.target.closest("[data-detail-remove-attachment], .composerImageRefRemove, .composerImageRemove")) return;
      event.preventDefault();
      event.stopPropagation();
      const current = previewBindings.get(node) || {};
      const src = node.dataset.portalPreviewImage || "";
      const title = node.getAttribute("title") || current.title || "";
      const tokenNode = node.closest("[data-portal-composer-attachment-token]");
      const chipNode = node.closest("[data-portal-composer-attachment-id]");
      const attachmentId = String(node.dataset.portalComposerAttachmentId
        || tokenNode?.dataset.portalComposerAttachmentToken
        || chipNode?.dataset.portalComposerAttachmentId || "");
      const attachment = attachmentId && current.attachmentById ? current.attachmentById(attachmentId) : null;
      const show = (overrides = {}) => {
        const config = { ...current, ...overrides };
        return open(src, title, {
          ...config,
          editable: config.editable === true,
          onSave: config.onSave,
        });
      };
      if (typeof current.onImagePreview === "function") {
        current.onImagePreview({ src, title, node, attachmentId, attachment, open: show });
      } else {
        show();
      }
    });
  }

  const initializeBindings = () => {
    window.PortalUIPrimitives.registerEnhancer({
      id: "preview-images", selector: "[data-portal-preview-image]", enhance: bindImage,
    });
  };

  window.PortalImageEditor = { open, close, bind };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initializeBindings, { once: true });
  else initializeBindings();
})();
