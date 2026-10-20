(function () {
  "use strict";

  if (window.PortalUI) return;

  const el = ref => window.PortalUIPrimitives.elementOf(ref);

  const ACTION_LABELS = Object.freeze({
    refresh: window.PortalI18n.t("刷新"),
    delete: window.PortalI18n.t("删除"),
    cancel: window.PortalI18n.t("取消"),
    save: window.PortalI18n.t("保存"),
  });

  function actionLabel(action) {
    const key = String(action || "").trim().toLowerCase();
    return ACTION_LABELS[key] || String(action || window.PortalI18n.t("操作")).trim() || window.PortalI18n.t("操作");
  }

  function errorDetail(error) {
    if (error instanceof Error) return error.message || error.name || window.PortalI18n.t("未知错误");
    if (error && typeof error === "object" && error.message) return String(error.message);
    const detail = String(error ?? "").trim();
    return detail || window.PortalI18n.t("未知错误");
  }

  function successMessage(action, target = "") {
    const subject = String(target || "").trim();
    return window.PortalI18n.text`${actionLabel(action)}${subject ? `“${subject}”` : ""}成功。`;
  }

  function errorMessage(action, error) {
    return window.PortalI18n.text`${actionLabel(action)}失败：${errorDetail(error)}`;
  }

  function clampNumber(value, min, max) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(Number(min), Math.min(Number(max), number)) : Number(min);
  }

  function preserveScrollPosition(rootRef, render) {
    const root = el(rootRef);
    if (!root || typeof render !== "function") return typeof render === "function" ? render() : undefined;
    const scrollTop = root.scrollTop;
    const scrollLeft = root.scrollLeft;
    const result = render();
    root.scrollTop = scrollTop;
    root.scrollLeft = scrollLeft;
    return result;
  }

  async function copyText(text, label = window.PortalI18n.t("内容")) {
    const value = String(text ?? "");
    if (!value) {
      toast(window.PortalI18n.text`${label} 暂不可用。`, "warning");
      return false;
    }
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      const focused = document.activeElement;
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "readonly");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      let copied = false;
      try {
        document.body.appendChild(textarea);
        textarea.focus({ preventScroll: true });
        textarea.select();
        copied = document.execCommand("copy") === true;
      } catch {
        copied = false;
      } finally {
        textarea.remove();
        if (focused?.isConnected) focused.focus({ preventScroll: true });
      }
      if (!copied) toast(window.PortalI18n.t("无法自动复制，请手动选择并复制。"), "warning");
      return copied;
    }
  }

  function isNavigationFetchError(error, options = {}) {
    const name = String(error?.name || "");
    const message = String(error?.message || error || "");
    if (name === "AbortError") return true;
    const leaving = options.leaving === true;
    const hidden = options.hidden === undefined ? document.hidden : options.hidden === true;
    if (!leaving && !hidden) return false;
    return /Failed to fetch|NetworkError|Load failed|cancelled|canceled/i.test(message);
  }

  function alertError(error, options = {}) {
    if (isNavigationFetchError(error, options)) return false;
    toast(errorMessage(options.action || window.PortalI18n.t("操作"), error), "error", options);
    return true;
  }

  function formatDateTime(value, options = {}) {
    if (value === undefined || value === null || value === "") return "";
    const raw = options.unixSeconds ? Number(value) * 1000 : value;
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) {
      if (options.invalidFallback === false) return "";
      if (Object.prototype.hasOwnProperty.call(options, "invalidFallback")) {
        return String(options.invalidFallback ?? "");
      }
      return String(value || "");
    }
    const style = String(options.style || "default");
    if (!options.format && (style === "calendar-date" || style === "calendar-minute")) {
      const pad = number => String(number).padStart(2, "0");
      const year = options.shortYear
        ? String(date.getFullYear()).slice(-2)
        : String(date.getFullYear());
      const dateText = `${year}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
      if (style === "calendar-date") return dateText;
      const separator = options.separator === undefined ? " " : String(options.separator);
      return `${dateText}${separator}${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }
    const formats = {
      status: { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" },
      compact: { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" },
    };
    return date.toLocaleString(window.PortalI18n.locale(), options.format || formats[style]);
  }

  function formatBytes(value, options = {}) {
    const has = key => Object.prototype.hasOwnProperty.call(options, key);
    const fallback = key => has(key)
      ? String(options[key] ?? "")
      : (has("fallback") ? String(options.fallback ?? "") : "0 B");
    if (value === undefined || value === null || value === "") return fallback("emptyFallback");
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return fallback("invalidFallback");
    if (bytes === 0) return fallback("zeroFallback");
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    const maximumUnit = String(options.maximumUnit || "PB").toUpperCase();
    const maximumIndex = units.includes(maximumUnit) ? units.indexOf(maximumUnit) : units.length - 1;
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), maximumIndex);
    const scaled = bytes / (1024 ** index);
    const precision = String(options.precision || "adaptive");
    const decimals = precision === "single"
      ? (index === 0 ? 0 : 1)
      : precision === "compact"
        ? (index === 0 || scaled >= 10 ? 0 : 1)
        : (index === 0 || scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2);
    return `${scaled.toFixed(decimals)} ${units[index]}`;
  }

  function confirmMessage(action, target = "", options = {}) {
    const subject = String(target || "").trim();
    const consequence = String(options.consequence || "").trim();
    const prompt = window.PortalI18n.text`确定要${actionLabel(action)}${subject ? `“${subject}”` : ""}吗？`;
    return consequence ? `${prompt}\n\n${consequence}` : prompt;
  }

  async function loadCurrentUser(options = {}) {
    const fallback = options.fallbackUsername === undefined ? "anonymous" : options.fallbackUsername;
    try {
      const data = await window.PortalIdentity.load();
      return { ok: true, username: data?.username || fallback, data };
    } catch (error) {
      return { ok: false, username: fallback, data: null, error };
    }
  }

  const dialogBodyClasses = new Map();
  const DIALOG_FOCUSABLE = [
    "a[href]",
    "area[href]",
    "button:not([disabled])",
    "input:not([disabled]):not([type='hidden'])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "iframe",
    "object",
    "embed",
    "[contenteditable='true']",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");

  function isRendered(element) {
    if (!(element instanceof HTMLElement) || element.hidden || element.closest("[hidden], [inert]")) return false;
    const style = window.getComputedStyle(element);
    return style.display !== "none"
      && style.visibility !== "hidden"
      && element.getClientRects().length > 0;
  }

  function dialogFocusableElements(modal) {
    return Array.from(modal.querySelectorAll(DIALOG_FOCUSABLE)).filter(isRendered);
  }

  function focusDialog(modal, preferred) {
    const target = (
      preferred instanceof HTMLElement
      && modal.contains(preferred)
      && isRendered(preferred)
    )
      ? preferred
      : dialogFocusableElements(modal)[0]
        || modal.querySelector("[data-portal-detail-surface], .portalDetailSurface")
        || modal;
    if (target instanceof HTMLElement && !target.matches(DIALOG_FOCUSABLE) && !target.hasAttribute("tabindex")) {
      target.setAttribute("tabindex", "-1");
    }
    target?.focus?.();
    return target;
  }

  function syncDialogBodyClass(className, controller, open) {
    if (!className) return;
    if (!dialogBodyClasses.has(className)) dialogBodyClasses.set(className, new Set());
    const controllers = dialogBodyClasses.get(className);
    if (open) controllers.add(controller);
    else controllers.delete(controller);
    document.body.classList.toggle(className, controllers.size > 0);
    if (!controllers.size) dialogBodyClasses.delete(className);
  }

  function focusAbandoned(active) {
    if (!(active instanceof Element) || !active.isConnected) return true;
    if (active.closest("[hidden]")) return true;
    return typeof active.checkVisibility === "function" && !active.checkVisibility();
  }

  function createDialogController(options = {}) {
    const modal = el(options.modal || options.overlay);
    if (!modal) return null;
    if (!window.PortalOverlay) {
      throw new Error("PortalOverlay must be loaded before PortalUI dialogs.");
    }
    let returnFocus = null;
    let focusFrame = null;
    const cancelFocusFrame = () => {
      if (focusFrame !== null) window.cancelAnimationFrame(focusFrame);
      focusFrame = null;
    };
    let controller = null;
    let dirtyConfirmationPending = false;
    let requestClose = null;
    const bodyClass = options.bodyClass === undefined ? "modalOpen" : options.bodyClass;
    const managesHidden = options.manageHidden === true || modal.hasAttribute("hidden");
    const isOpen = () => modal.classList.contains(options.openClass || "open");
    const setOpen = (open, context) => {
      if (open === isOpen()) return true;
      const guard = open ? options.beforeOpen : options.beforeClose;
      if (!context?.force && typeof guard === "function" && guard(context) === false) return false;
      if (!open && !context?.force && typeof options.dirty === "function" && options.dirty(context)) {
        if (dirtyConfirmationPending) return false;
        const confirmDiscard = options.confirmDiscard || (() => confirmDialog({
          title: window.PortalI18n.t("放弃未保存的修改？"),
          message: options.discardMessage || window.PortalI18n.t("关闭后将丢失尚未保存的修改。"),
          confirmLabel: window.PortalI18n.t("放弃修改"),
          tone: "danger",
        }));
        const decision = confirmDiscard(context);
        if (decision && typeof decision.then === "function") {
          dirtyConfirmationPending = true;
          Promise.resolve(decision)
            .then(approved => {
              if (approved && isOpen()) {
                return requestClose({ ...context, force: true, dirtyConfirmed: true });
              }
              return false;
            })
            .finally(() => {
              dirtyConfirmationPending = false;
            });
          return false;
        }
        if (decision === false) return false;
      }
      cancelFocusFrame();
      const focusReturnTarget = open ? null : returnFocus;
      if (open) returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      else returnFocus = null;
      if (open && managesHidden) modal.hidden = false;
      if (modal instanceof HTMLDialogElement) {
        if (open && !modal.open) modal.showModal();
        else if (!open && modal.open) modal.close();
        modal.classList.toggle(options.openClass || "open", !!open);
      } else {
        modal.classList.toggle(options.openClass || "open", !!open);
      }
      if (!open && managesHidden) modal.hidden = true;
      syncDialogBodyClass(bodyClass, controller, open);
      if (options.ariaHidden !== false && modal.hasAttribute("aria-hidden")) {
        modal.setAttribute("aria-hidden", open ? "false" : "true");
      }
      const callback = open ? options.onOpen : options.onClose;
      if (typeof callback === "function") callback(context);
      if (open) {
        overlayController.activate(context);
        focusFrame = window.requestAnimationFrame(() => {
          focusFrame = null;
          if (!isOpen() || window.PortalOverlay.top() !== overlayController) return;
          const configuredFocus = typeof options.initialFocus === "function"
            ? options.initialFocus(context)
            : options.initialFocus;
          focusDialog(modal, el(context?.focusTarget) || el(configuredFocus));
        });
      } else {
        overlayController.deactivate();
        const active = document.activeElement;
        const remainingOverlay = window.PortalOverlay.top();
        // Finish the close before teardown or later input can change focus.
        // Focus left in a surface that just closed (such as an accepted
        // confirmation) has no owner yet, so the opener takes it back.
        if (options.restoreFocus !== false && focusReturnTarget?.isConnected
          && (active === document.body || modal.contains(active) || focusAbandoned(active))
          && (!remainingOverlay || remainingOverlay.element.contains(focusReturnTarget))) {
          focusReturnTarget.focus();
        }
      }
      return true;
    };
    const handleFocusTrap = event => {
      if (event.key !== "Tab" || !isOpen() || window.PortalOverlay.top() !== overlayController) return;
      const focusable = dialogFocusableElements(modal);
      if (!focusable.length) {
        event.preventDefault();
        focusDialog(modal);
        return;
      }
      const activeIndex = focusable.indexOf(document.activeElement);
      const target = event.shiftKey
        ? (activeIndex <= 0 ? focusable.at(-1) : null)
        : (activeIndex < 0 || activeIndex === focusable.length - 1 ? focusable[0] : null);
      if (!target) return;
      event.preventDefault();
      target.focus();
    };
    controller = {
      element: modal,
      isOpen,
      open: context => setOpen(true, context),
      close: context => setOpen(false, context),
      destroy() {
        cancelFocusFrame();
        returnFocus = null;
        overlayController.destroy();
        syncDialogBodyClass(bodyClass, controller, false);
      },
    };
    requestClose = event => (
      typeof options.requestClose === "function"
        ? options.requestClose(event, controller)
        : controller.close(event)
    );
    const overlayController = window.PortalOverlay.register(modal, {
      kind: options.layer || "dialog",
      closeOnBackdrop: !!options.backdropClose,
      closeOnEscape: !!options.escapeClose,
      isBackdropTarget: options.isBackdropTarget,
      onRequestClose: requestClose,
    });
    const handleNativeClose = () => {
      if (modal instanceof HTMLDialogElement && isOpen()) {
        controller.close({ force: true, nativeClose: true });
      }
    };
    document.addEventListener("keydown", handleFocusTrap, true);
    if (modal instanceof HTMLDialogElement) modal.addEventListener("close", handleNativeClose);
    const destroy = controller.destroy;
    controller.destroy = () => {
      document.removeEventListener("keydown", handleFocusTrap, true);
      if (modal instanceof HTMLDialogElement) modal.removeEventListener("close", handleNativeClose);
      destroy();
    };
    return controller;
  }

  const confirmationQueue = [];
  let confirmationRuntime = null;

  function ensureConfirmationDialog() {
    if (confirmationRuntime?.overlay?.isConnected) return confirmationRuntime;
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay";
    overlay.hidden = true;
    overlay.dataset.portalConfirmation = "1";
    overlay.dataset.portalDetailOverlay = "1";
    overlay.dataset.portalOverlayLayer = "dialog";
    overlay.innerHTML = window.PortalI18n.html`
      <form class="portalDetailSurface portalConfirmSurface" data-portal-detail-surface data-tone="primary" role="alertdialog" aria-modal="true" aria-labelledby="portalConfirmTitle" aria-describedby="portalConfirmMessage">
        <header class="portalDetailHeader portalConfirmHeader">
          <span class="portalConfirmIcon" data-portal-icon="alert-circle" aria-hidden="true"></span>
          <div class="portalConfirmHeading">
            <h2 id="portalConfirmTitle">请确认</h2>
          </div>
          <button class="portalDetailClose" data-portal-icon="close" type="button" data-portal-confirm-cancel aria-label="关闭确认窗口" title="关闭"></button>
        </header>
        <div class="portalDetailBody portalConfirmBody">
          <p id="portalConfirmMessage" class="portalConfirmMessage"></p>
          <p class="portalConfirmDescription" data-portal-confirm-description hidden></p>
          <dl class="portalConfirmMetadata" data-portal-confirm-metadata hidden></dl>
        </div>
        <footer class="portalDetailFooter">
          <button class="portalButton" type="button" data-portal-confirm-cancel>取消</button>
          <button class="portalButton is-primary" type="button" data-portal-confirm-accept>确认</button>
        </footer>
      </form>`;
    document.body.appendChild(overlay);

    const runtime = {
      current: null,
      overlay,
      surface: overlay.querySelector(".portalConfirmSurface"),
      title: overlay.querySelector("#portalConfirmTitle"),
      message: overlay.querySelector("#portalConfirmMessage"),
      description: overlay.querySelector("[data-portal-confirm-description]"),
      metadata: overlay.querySelector("[data-portal-confirm-metadata]"),
      accept: overlay.querySelector("[data-portal-confirm-accept]"),
      cancel: overlay.querySelector(".portalDetailFooter [data-portal-confirm-cancel]"),
      dialog: null,
    };
    const settle = accepted => {
      if (!runtime.current) return;
      const { resolve } = runtime.current;
      runtime.current = null;
      runtime.dialog.close({ force: true, accepted });
      resolve(Boolean(accepted));
      queueMicrotask(showNextConfirmation);
    };
    runtime.dialog = createDialogController({
      modal: overlay,
      backdropClose: true,
      escapeClose: true,
      requestClose: () => settle(false),
    });
    overlay.addEventListener("click", event => {
      if (event.target.closest("[data-portal-confirm-cancel]")) settle(false);
      // Confirmation resolves a local decision. The caller owns any mutation;
      // accepting it must not itself dispatch an HTML form submission.
      if (event.target.closest("[data-portal-confirm-accept]")) settle(true);
    });
    overlay.querySelector("form").addEventListener("submit", event => {
      event.preventDefault();
      settle(true);
    });
    confirmationRuntime = runtime;
    return runtime;
  }

  function renderConfirmationMetadata(root, value) {
    const items = Array.isArray(value)
      ? value
      : value && typeof value === "object"
        ? Object.entries(value).map(([label, itemValue]) => ({ label, value: itemValue }))
        : [];
    root.replaceChildren();
    items.forEach(item => {
      const label = String(item?.label || "").trim();
      const itemValue = String(item?.value ?? "").trim();
      if (!label || !itemValue) return;
      const row = document.createElement("div");
      row.className = "portalConfirmMetadataRow";
      const term = document.createElement("dt");
      term.textContent = label;
      const description = document.createElement("dd");
      description.textContent = itemValue;
      row.append(term, description);
      root.appendChild(row);
    });
    root.hidden = !root.childElementCount;
  }

  function showNextConfirmation() {
    const runtime = ensureConfirmationDialog();
    if (runtime.current || !confirmationQueue.length) return;
    runtime.current = confirmationQueue.shift();
    const options = runtime.current.options;
    const tone = ["danger", "warning"].includes(String(options.tone || ""))
      ? String(options.tone)
      : "primary";
    runtime.title.textContent = String(options.title || window.PortalI18n.t("请确认"));
    runtime.message.textContent = String(options.message || "");
    runtime.description.textContent = String(options.description || "");
    runtime.description.hidden = !runtime.description.textContent.trim();
    renderConfirmationMetadata(runtime.metadata, options.metadata);
    runtime.cancel.textContent = String(options.cancelLabel || window.PortalI18n.t("取消"));
    runtime.accept.textContent = String(options.confirmLabel || window.PortalI18n.t("确认"));
    const variant = tone === "danger"
      ? "is-danger"
      : tone === "warning"
        ? "is-warning"
        : "is-primary";
    runtime.surface.dataset.tone = tone;
    runtime.accept.className = `portalButton ${variant}`;
    runtime.dialog.open({ focusTarget: runtime.accept });
  }

  function confirmDialog(options = {}) {
    const normalized = typeof options === "string" ? { message: options } : { ...options };
    return new Promise(resolve => {
      confirmationQueue.push({ options: normalized, resolve });
      showNextConfirmation();
    });
  }

  function confirmAction(action, target = "", options = {}) {
    const label = actionLabel(action);
    return confirmDialog({
      title: options.title || window.PortalI18n.text`${label}确认`,
      message: confirmMessage(action, target, options),
      cancelLabel: options.cancelLabel,
      confirmLabel: options.confirmLabel || label,
      tone: options.tone || (String(action || "").toLowerCase() === "delete" ? "danger" : "primary"),
    });
  }

  const promptQueue = [];
  let promptRuntime = null;

  function ensurePromptDialog() {
    if (promptRuntime?.overlay?.isConnected) return promptRuntime;
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay";
    overlay.hidden = true;
    overlay.dataset.portalPrompt = "1";
    overlay.dataset.portalDetailOverlay = "1";
    overlay.dataset.portalOverlayLayer = "dialog";
    overlay.innerHTML = window.PortalI18n.html`
      <form class="portalDetailSurface portalPromptSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="portalPromptTitle">
        <header class="portalDetailHeader">
          <div>
            <div class="portalDetailEyebrow">INPUT</div>
            <h2 id="portalPromptTitle">请输入</h2>
          </div>
          <button class="portalDetailClose" type="button" data-portal-prompt-cancel aria-label="关闭输入窗口" title="关闭" data-portal-icon="close"></button>
        </header>
        <div class="portalDetailBody portalPromptBody">
          <p class="portalPromptMessage" data-portal-prompt-message hidden></p>
          <label class="portalField" data-portal-prompt-choice-field for="portalPromptChoice" hidden>
            <span data-portal-prompt-choice-label>选择</span>
            <select id="portalPromptChoice" data-portal-prompt-choice></select>
          </label>
          <label class="portalField" data-portal-prompt-field for="portalPromptInput">
            <span data-portal-prompt-label>内容</span>
            <input id="portalPromptInput" type="text" data-portal-prompt-input autocomplete="off" />
            <textarea id="portalPromptTextarea" data-portal-prompt-textarea hidden></textarea>
          </label>
        </div>
        <footer class="portalDetailFooter">
          <button class="portalButton" type="button" data-portal-prompt-cancel>取消</button>
          <button class="portalButton is-primary" type="submit" data-portal-prompt-accept>确定</button>
        </footer>
      </form>`;
    document.body.appendChild(overlay);
    const runtime = {
      current: null,
      overlay,
      title: overlay.querySelector("#portalPromptTitle"),
      message: overlay.querySelector("[data-portal-prompt-message]"),
      field: overlay.querySelector("[data-portal-prompt-field]"),
      label: overlay.querySelector("[data-portal-prompt-label]"),
      input: overlay.querySelector("[data-portal-prompt-input]"),
      textarea: overlay.querySelector("[data-portal-prompt-textarea]"),
      choiceField: overlay.querySelector("[data-portal-prompt-choice-field]"),
      choiceLabel: overlay.querySelector("[data-portal-prompt-choice-label]"),
      choice: overlay.querySelector("[data-portal-prompt-choice]"),
      accept: overlay.querySelector("[data-portal-prompt-accept]"),
      dialog: null,
    };
    const settle = accepted => {
      if (!runtime.current) return;
      const { resolve, control, choices } = runtime.current;
      // A prompt with choices answers with the chosen value and the text.
      const value = !accepted ? null : choices ? { choice: runtime.choice.value, value: control.value } : control.value;
      runtime.current = null;
      runtime.dialog.close({ force: true, accepted });
      resolve(value);
      queueMicrotask(showNextPrompt);
    };
    runtime.dialog = createDialogController({
      modal: overlay,
      backdropClose: true,
      escapeClose: true,
      requestClose: () => settle(false),
    });
    overlay.addEventListener("click", event => {
      if (event.target.closest("[data-portal-prompt-cancel]")) settle(false);
    });
    overlay.querySelector("form").addEventListener("submit", event => {
      event.preventDefault();
      settle(true);
    });
    promptRuntime = runtime;
    return runtime;
  }

  function showNextPrompt() {
    const runtime = ensurePromptDialog();
    if (runtime.current || !promptQueue.length) return;
    const request = promptQueue.shift();
    const options = request.options;
    const multiline = options.multiline === true;
    const control = multiline ? runtime.textarea : runtime.input;
    const choices = Array.isArray(options.choices) && options.choices.length ? options.choices : null;
    runtime.current = { ...request, control, choices };
    runtime.choiceField.hidden = !choices;
    runtime.choiceLabel.textContent = String(options.choiceLabel || window.PortalI18n.t("选择"));
    runtime.choice.replaceChildren(...(choices || []).map(choice => {
      const option = document.createElement("option");
      option.value = String(choice?.value ?? "");
      option.textContent = String(choice?.label ?? choice?.value ?? "");
      return option;
    }));
    if (choices) runtime.choice.value = String(options.choice ?? choices[0]?.value ?? "");
    runtime.title.textContent = String(options.title || window.PortalI18n.t("请输入"));
    runtime.message.textContent = String(options.message || "");
    runtime.message.hidden = !String(options.message || "").trim();
    runtime.label.textContent = String(options.label || window.PortalI18n.t("内容"));
    runtime.input.hidden = multiline;
    runtime.textarea.hidden = !multiline;
    runtime.field.htmlFor = control.id;
    control.value = String(options.value ?? "");
    control.placeholder = String(options.placeholder || "");
    runtime.accept.textContent = String(options.confirmLabel || window.PortalI18n.t("确定"));
    runtime.dialog.open({ focusTarget: choices ? runtime.choice : control });
    if (!choices) window.requestAnimationFrame(() => control.select?.());
  }

  function promptDialog(options = {}, defaultValue = "") {
    const normalized = typeof options === "string"
      ? { title: options, value: defaultValue }
      : { ...options };
    return new Promise(resolve => {
      promptQueue.push({ options: normalized, resolve });
      showNextPrompt();
    });
  }

  const mutationInflight = new Map();
  const busyButtons = new WeakMap();
  const busyLabelAttributes = ["aria-label", "title", "data-portal-tooltip"];

  function setButtonBusy(buttonRef, busy, label = "") {
    const button = el(buttonRef);
    if (!button) return;
    if (busy) {
      if (!busyButtons.has(button)) {
        busyButtons.set(button, {
          content: Array.from(button.childNodes),
          disabled: button.disabled,
          labels: busyLabelAttributes.map(name => [name, button.getAttribute(name)]),
        });
      }
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      if (label) {
        label = window.PortalI18n.message(String(label));
        // Keep icon and label separate so responsive/icon-only buttons retain
        // their layout. Bare text bypasses their label visibility rules.
        const icon = button.querySelector(":scope > svg, :scope > [data-portal-icon-node]");
        const text = document.createElement("span");
        text.className = "portalButtonBusyLabel";
        text.textContent = String(label);
        button.replaceChildren(...(icon ? [icon, text] : [text]));
        busyLabelAttributes.forEach(name => button.setAttribute(name, String(label)));
      }
      return;
    }
    button.removeAttribute("aria-busy");
    const idle = busyButtons.get(button);
    if (idle) {
      button.replaceChildren(...idle.content);
      button.disabled = idle.disabled;
      idle.labels.forEach(([name, value]) => {
        if (value === null) button.removeAttribute(name);
        else button.setAttribute(name, value);
      });
      busyButtons.delete(button);
    }
  }

  function ensureToastStack() {
    let stack = document.querySelector("[data-portal-toast-stack]");
    if (stack) return stack;
    stack = document.createElement("div");
    stack.className = "portalToastStack";
    stack.dataset.portalToastStack = "1";
    stack.dataset.portalOverlayLayer = "toast";
    stack.setAttribute("aria-live", "polite");
    stack.setAttribute("aria-relevant", "additions");
    document.body.appendChild(stack);
    return stack;
  }

  const toastTimers = new WeakMap();

  function toast(message, kind = "success", options = {}) {
    const stack = ensureToastStack();
    const key = String(options.key || "").trim();
    const existing = key
      ? stack.querySelector(`[data-portal-toast-key="${CSS.escape(key)}"]`)
      : null;
    const item = existing || document.createElement("div");
    const previousTimer = toastTimers.get(item);
    if (previousTimer) window.clearTimeout(previousTimer);
    item.replaceChildren();
    item.className = `portalToast ${String(kind || "success")}`;
    item.setAttribute("role", kind === "error" ? "alert" : "status");
    if (key) item.dataset.portalToastKey = key;
    const text = document.createElement("span");
    text.className = "portalToastMessage";
    text.textContent = String(message || "");
    item.appendChild(text);
    if (text.textContent) {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "portalToastCopy";
      copy.title = window.PortalI18n.t("复制");
      copy.setAttribute("aria-label", copy.title);
      copy.dataset.portalIcon = "copy";
      copy.addEventListener("click", async () => {
        if (!await copyText(text.textContent)) return;
        copy.title = window.PortalI18n.t("已复制");
        copy.setAttribute("aria-label", copy.title);
        copy.dataset.portalIcon = "check";
      });
      item.appendChild(copy);
    }
    const remove = () => {
      const timer = toastTimers.get(item);
      if (timer) window.clearTimeout(timer);
      toastTimers.delete(item);
      item.remove();
    };
    if (typeof options.retry === "function") {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "portalToastRetry";
      retry.textContent = String(options.retryLabel || window.PortalI18n.t("重试"));
      retry.addEventListener("click", () => {
        remove();
        options.retry();
      });
      item.appendChild(retry);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "portalToastClose";
    close.setAttribute("aria-label", window.PortalI18n.t("关闭提示"));
    close.dataset.portalIcon = "close";
    close.addEventListener("click", remove);
    item.appendChild(close);
    if (!existing) stack.appendChild(item);
    const duration = Number(options.duration ?? (kind === "error" ? 7000 : 3600));
    if (duration > 0) {
      const timer = window.setTimeout(() => {
        toastTimers.delete(item);
        item.remove();
      }, duration);
      toastTimers.set(item, timer);
    }
    return item;
  }

  function runMutation(options = {}) {
    if (typeof options.run !== "function") {
      return Promise.reject(new TypeError("PortalUI.runMutation requires run()."));
    }
    const key = String(options.key || "");
    if (key && mutationInflight.has(key)) return mutationInflight.get(key);
    const execute = async () => {
      setButtonBusy(options.button, true, options.busyLabel || window.PortalI18n.t("处理中…"));
      try {
        options.onStateChange?.("running");
        const result = await options.run();
        await options.onSuccess?.(result);
        if (options.successMessage) {
          toast(
            typeof options.successMessage === "function" ? options.successMessage(result) : options.successMessage,
            "success",
          );
        }
        options.onStateChange?.("success", result);
        return result;
      } catch (error) {
        await options.onError?.(error);
        const message = typeof options.errorMessage === "function"
          ? options.errorMessage(error)
          : options.errorMessage || errorMessage(options.action || window.PortalI18n.t("操作"), error);
        if (options.notifyError !== false) {
          const retry = options.retry === false
            ? null
            : (typeof options.retry === "function"
              ? () => options.retry(error)
              : () => runMutation(options));
          toast(message, "error", retry ? {
            retry,
            retryLabel: options.retryLabel,
          } : {});
        }
        options.onStateChange?.("error", error);
        if (options.rethrow) throw error;
        return null;
      } finally {
        setButtonBusy(options.button, false);
        options.onFinally?.();
      }
    };
    const promise = execute().finally(() => {
      if (key && mutationInflight.get(key) === promise) mutationInflight.delete(key);
    });
    if (key) mutationInflight.set(key, promise);
    return promise;
  }

  window.PortalUI = {
    actionLabel,
    alertError,
    confirm: confirmDialog,
    confirmAction,
    copyText,
    clampNumber,
    createDialogController,
    errorMessage,
    formatBytes,
    formatDateTime,
    loadCurrentUser,
    preserveScrollPosition,
    prompt: promptDialog,
    runMutation,
    setButtonBusy,
    successMessage,
    toast,
  };
})();

/*
 * Portal viewport capability.
 *
 * This is the single owner of compact/mobile mode. Components consume the
 * capability or the document data attribute; product pages must not infer a
 * device independently.
 */
(function installPortalResponsive(global) {
  "use strict";

  if (global.PortalResponsive) return;

  const media = global.matchMedia("(max-width: 820px)");
  const subscribers = new Set();
  let mode = media.matches ? "mobile" : "desktop";

  function snapshot() {
    return Object.freeze({
      mode,
      mobile: mode === "mobile",
      compact: mode === "mobile",
    });
  }

  function apply(nextMode, reason = "viewport") {
    const normalized = nextMode === "mobile" ? "mobile" : "desktop";
    const changed = normalized !== mode;
    mode = normalized;
    document.documentElement.dataset.portalViewport = mode;
    const detail = snapshot();
    if (!changed && reason !== "initialize") return detail;
    global.dispatchEvent(new CustomEvent("portal:responsive-change", {
      detail: Object.freeze({ ...detail, reason }),
    }));
    subscribers.forEach(listener => listener(detail));
    return detail;
  }

  function subscribe(listener, options = {}) {
    if (typeof listener !== "function") return () => {};
    subscribers.add(listener);
    if (options.immediate !== false) listener(snapshot());
    return () => subscribers.delete(listener);
  }

  const onMediaChange = event => apply(event.matches ? "mobile" : "desktop");
  if (typeof media.addEventListener === "function") media.addEventListener("change", onMediaChange);
  else media.addListener?.(onMediaChange);
  apply(mode, "initialize");

  global.PortalResponsive = Object.freeze({
    get mode() { return mode; },
    isMobile: () => mode === "mobile",
    snapshot,
    subscribe,
  });
})(window);
