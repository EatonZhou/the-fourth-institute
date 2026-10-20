(function registerDesignUILive(global) {
  "use strict";

  const adapters = global.PortalDesignLiveAdapters = global.PortalDesignLiveAdapters || {};
  if (adapters["design-ui"]) return;
  let session = null;
  const nextFrame = () => new Promise(resolve => global.requestAnimationFrame(resolve));
  const check = (condition, message) => {
    if (!condition) throw new Error(`共享组件 Live 验收失败：${message}`);
  };
  const assertActive = signal => {
    if (signal?.aborted) throw new DOMException("Live 已取消", "AbortError");
    check(session && !session.restoring, "缺少有效 capture 或正在恢复");
  };
  async function settle(signal) {
    await nextFrame();
    await nextFrame();
    assertActive(signal);
  }

  function append(parent, tag, text, className = "") {
    const element = document.createElement(tag);
    element.className = className;
    if (text !== undefined) element.textContent = text;
    if (tag === "button") element.type = "button";
    parent.appendChild(element);
    return element;
  }

  function keyboard(target, key, extra = {}) {
    const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...extra });
    target.dispatchEvent(event);
    return event;
  }

  function requireTop(element) {
    check(global.PortalOverlay.top()?.element === element, "当前顶层已改变，停止发送键盘或关闭动作");
  }

  function ownErrorToasts(message) {
    const previousStack = document.querySelector("[data-portal-toast-stack]");
    const owned = () => [...document.querySelectorAll(".portalToast")].filter(item => item.querySelector(".portalToastMessage")?.textContent === message);
    session.disposers.push(() => {
      for (const item of owned()) item.querySelector(".portalToastClose")?.click();
      const stack = document.querySelector("[data-portal-toast-stack]");
      if (!previousStack && stack && !stack.childElementCount) stack.remove();
    });
    return owned;
  }

  function dialog(title, options = {}) {
    const modal = append(document.body, "div", undefined, "portalDetailOverlay");
    modal.dataset.designUiOwned = session.token;
    modal.hidden = true;
    const surface = append(modal, "section", undefined, "portalDetailSurface");
    surface.setAttribute("role", "dialog");
    surface.setAttribute("aria-modal", "true");
    surface.setAttribute("aria-label", title);
    append(append(surface, "header", undefined, "portalDetailHeader"), "h2", title);
    const body = append(surface, "div", undefined, "portalDetailBody");
    const first = append(body, "button", "第一个本地动作", "portalButton");
    const last = append(body, "button", "最后一个本地动作", "portalButton");
    let controller = null;
    session.disposers.push(() => {
      controller?.close({ force: true });
      controller?.destroy();
      modal.remove();
    });
    controller = global.PortalUI.createDialogController({ modal, escapeClose: true, ...options });
    check(controller, "Dialog controller 没有创建成功");
    return { modal, surface, body, first, last, controller };
  }

  const capabilities = {
    async "ui.action.error-retry"(body, signal) {
      const message = window.PortalI18n.text`本地读取暂时失败 ${session.token}`;
      const ownedToasts = ownErrorToasts(message);
      const button = append(body, "button", "读取本地演示数据", "portalButton");
      const output = append(body, "output", "尚未读取");
      const states = [];
      let calls = 0;
      let errorToast = null;
      let removedBeforeRetry = false;
      const result = await global.PortalUI.runMutation({
        button, errorMessage: message,
        run: async () => {
          calls += 1;
          if (calls === 1) throw new Error("Live 隔离数据首次读取失败");
          removedBeforeRetry = errorToast !== null && !errorToast.isConnected;
          return "本地读取完成";
        },
        onSuccess: value => { output.textContent = value; },
        onStateChange: state => states.push(state),
      });
      check(result === null && states.join(",") === "running,error", "失败没有完成 error 状态与空结果反馈");
      [errorToast] = ownedToasts();
      check(errorToast?.getAttribute("role") === "alert", "失败未创建局部错误 Toast");
      const retry = errorToast.querySelector(".portalToastRetry");
      check(retry && !retry.disabled, "默认失败提示未提供可执行重试");
      await settle(signal);
      retry.click();
      await settle(signal);
      check(removedBeforeRetry, "重试回调开始前没有移除旧错误提示");
      check(calls === 2 && states.join(",") === "running,error,running,success", "点击重试未执行一次真实恢复路径");
      check(output.textContent === "本地读取完成" && ownedToasts().length === 0, "重试成功后结果未更新或旧错误仍在");
      check(!button.disabled && !button.hasAttribute("aria-busy"), "重试完成后按钮仍然忙碌");
      return "首次本地读取失败可重试；旧提示在回调前移除，第二次读取更新结果并恢复按钮。";
    },

    async "ui.action.disabled-restoration"(body, signal) {
      for (const initiallyDisabled of [true, false]) {
        for (const outcome of ["failure", "cancelled", "success"]) {
          const button = append(body, "button", initiallyDisabled ? "原本禁用的动作" : "原本可用的动作", "portalButton");
          button.disabled = initiallyDisabled;
          const originalLabel = button.innerHTML;
          const states = [];
          let clicked = 0;
          let finalizers = 0;
          let resolveTask;
          let rejectTask;
          let execution;
          button.addEventListener("click", () => { clicked += 1; });
          const task = new Promise((resolve, reject) => { resolveTask = resolve; rejectTask = reject; });
          session.disposers.push(async () => { resolveTask("restored"); await execution; });
          execution = global.PortalUI.runMutation({
            button, busyLabel: "等待本地结果", notifyError: false,
            run: () => task,
            onStateChange: state => states.push(state),
            onFinally: () => { finalizers += 1; },
          });
          check(button.disabled && button.getAttribute("aria-busy") === "true", "执行期间未禁用控件");
          button.click();
          check(clicked === 0, "忙碌禁用控件仍可触发动作");
          await settle(signal);
          if (outcome === "success") resolveTask("本地成功");
          else rejectTask(outcome === "cancelled" ? new DOMException("本地操作取消", "AbortError") : new Error("本地操作失败"));
          const value = await execution;
          check(value === (outcome === "success" ? "本地成功" : null), "失败或取消结果未按 Mutation 契约返回");
          check(states.join(",") === `running,${outcome === "success" ? "success" : "error"}` && finalizers === 1, "失败、取消或成功没有且仅有一次终结反馈");
          check(button.disabled === initiallyDisabled, "动作结束改变了控件原有禁用状态");
          check(button.innerHTML === originalLabel && !button.hasAttribute("aria-busy"), "动作结束遗留忙碌标签");
          button.click();
          check(clicked === (initiallyDisabled ? 0 : 1), "恢复后的控件交互与原禁用状态不一致");
        }
      }
      return "成功、失败和取消均只终结一次；原禁用控件保持不可交互，原可用控件恢复可用。";
    },

    async "ui.form.search-clear-events"(body, signal) {
      const form = append(body, "div");
      form.dataset.portalSearchField = "1";
      const input = append(form, "input");
      input.type = "search";
      input.setAttribute("aria-label", window.PortalI18n.t("仅筛选本地示例"));
      const results = append(body, "ul");
      const events = [];
      const values = [];
      let committedQuery = "";
      const update = () => {
        results.replaceChildren();
        for (const text of ["本地草稿", "本地归档"].filter(text => text.includes(input.value))) append(results, "li", text);
      };
      input.addEventListener("input", () => { events.push("input"); values.push(input.value); update(); });
      input.addEventListener("change", () => { events.push("change"); values.push(input.value); committedQuery = input.value; });
      const controller = global.PortalFormControls.mountSearchField(form);
      check(controller?.input === input && global.PortalFormControls.mountSearchField(form) === controller, "搜索字段未复用生产 controller");
      input.value = "草稿";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      check(results.childElementCount === 1 && committedQuery === "草稿", "本地筛选初始行为未生效");
      events.length = 0;
      values.length = 0;
      const clear = form.querySelector(".portalClearableInputBtn");
      check(clear && !clear.hidden && !clear.disabled, "搜索清除控件不可操作");
      clear.click();
      await settle(signal);
      check(events.join(",") === "input,change" && values.every(value => value === ""), "清空值后没有依次发布一次 input 和 change");
      check(input.value === "" && committedQuery === "" && results.childElementCount === 2, "清除未同步输入、已提交条件和实际筛选结果");
      check(document.activeElement === input, "清除后未回到搜索输入");
      clear.click();
      check(events.length === 2, "空值重复清除仍产生多余字段变化");
      return "清除搜索依次发布 input 与 change，提交条件及本地结果同步清空；空值重复点击不再派发事件。";
    },

    async "ui.overlay.escape-top"(body, signal) {
      const lower = dialog("下层 Dialog");
      const upper = dialog("上层 Dialog");
      lower.controller.open();
      upper.controller.open();
      await settle(signal);
      requireTop(upper.modal);
      const event = keyboard(upper.first, "Escape");
      check(event.defaultPrevented, "Escape 未被顶层接收");
      check(!upper.controller.isOpen() && lower.controller.isOpen(), "一次 Escape 必须只关闭上层");
      requireTop(lower.modal);
      keyboard(lower.first, "Escape");
      check(!lower.controller.isOpen(), "第二次 Escape 应关闭剩余 Dialog");
      check(global.PortalOverlay.top() === null, "Dialog 关闭后仍占用 Overlay 栈");
      return "两次 Escape 分别关闭上层与下层，事件未穿透。";
    },

    async "ui.overlay.backdrop-gesture"(body, signal) {
      const view = dialog("背景点击保护", { backdropClose: true });
      view.controller.open();
      await settle(signal);
      requireTop(view.modal);
      const gesture = (start, end, { button = 0, cancel = false, moved = false } = {}) => {
        const coordinates = view.surface.getBoundingClientRect();
        const origin = { clientX: coordinates.left, clientY: coordinates.top, pointerId: 17, isPrimary: true, button, bubbles: true };
        start.dispatchEvent(new PointerEvent("pointerdown", origin));
        if (cancel) start.dispatchEvent(new PointerEvent("pointercancel", origin));
        end.dispatchEvent(new PointerEvent("pointerup", {
          ...origin,
          clientX: moved ? coordinates.right : origin.clientX,
        }));
      };
      for (const [start, end, options] of [
        [view.first, view.modal, {}],
        [view.modal, view.first, {}],
        [view.modal, view.modal, { button: 2 }],
        [view.modal, view.modal, { cancel: true }],
        [view.modal, view.modal, { moved: true }],
      ]) {
        gesture(start, end, options);
        check(view.controller.isOpen(), "拖拽、取消、非主键或跨越内容的手势误关了 Dialog");
      }
      gesture(view.modal, view.modal);
      check(!view.controller.isOpen(), "完整背景主键点击未关闭 Dialog");
      return "内容拖拽、右键和取消均保留 Dialog，完整背景主键点击才关闭。";
    },

    async "ui.popover.focus-return"(body, signal) {
      const trigger = append(body, "button", "打开本地菜单", "portalButton");
      const outside = append(body, "button", "其他本地控件", "portalButton");
      const panel = append(body, "div", undefined, "portalDetailSurface");
      const item = append(panel, "button", "菜单项", "portalButton");
      const controller = global.PortalOverlay.bindPopover({ trigger, panel, portal: true });
      check(controller, "Popover controller 没有创建成功");
      session.disposers.push(() => controller.destroy());
      trigger.addEventListener("click", () => controller.open());
      trigger.click();
      await settle(signal);
      requireTop(panel);
      check(trigger.getAttribute("aria-expanded") === "true", "Popover 打开未同步 aria-expanded");
      item.focus();
      keyboard(item, "Escape");
      await settle(signal);
      check(!controller.isOpen() && panel.hidden, "Escape 未关闭 Popover");
      check(document.activeElement === trigger, "Escape 关闭后未将焦点还给触发器");
      check(trigger.getAttribute("aria-expanded") === "false", "关闭未同步 aria-expanded");
      check(panel.parentElement === body, "portal Popover 关闭后未回到原 DOM");
      trigger.click();
      await settle(signal);
      outside.focus();
      outside.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0 }));
      await settle(signal);
      check(!controller.isOpen() && document.activeElement === outside, "外部点击关闭不应抢走焦点");
      return "Escape 恢复触发器焦点；外部点击保留目标焦点，并恢复菜单 DOM 位置。";
    },

    async "ui.dialog.keyboard-trap"(body, signal) {
      const view = dialog("键盘焦点范围");
      view.controller.open();
      await settle(signal);
      requireTop(view.modal);
      check(view.modal.contains(document.activeElement), "打开 Dialog 后焦点仍在页面中");
      view.last.focus();
      check(keyboard(view.last, "Tab").defaultPrevented && document.activeElement === view.first, "Tab 未从末尾循环到首项");
      check(keyboard(view.first, "Tab", { shiftKey: true }).defaultPrevented && document.activeElement === view.last, "Shift+Tab 未从首项循环到末尾");
      view.first.remove();
      view.last.remove();
      const event = keyboard(view.surface, "Tab");
      check(event.defaultPrevented && view.modal.contains(document.activeElement), "无控件时焦点逃离 Dialog");
      view.controller.close({ force: true });
      return "Tab 与 Shift+Tab 在 Dialog 内循环；无可聚焦控件时仍留在 Dialog。";
    },

    async "ui.dialog.dirty-confirmation"(body, signal) {
      const current = session;
      let confirmations = 0;
      const view = dialog("未提交的本地表单", {
        dirty: () => true,
        confirmDiscard: () => {
          confirmations += 1;
          const pending = global.PortalUI.confirm({
            title: current.confirmTitle,
            message: window.PortalI18n.t("只丢弃 Live 临时输入，不修改任何业务对象。"),
            confirmLabel: window.PortalI18n.t("丢弃临时输入"),
          });
          current.confirmOverlay = document.querySelector("[data-portal-confirmation]");
          return pending;
        },
      });
      const field = append(view.body, "textarea");
      field.value = "未保存的临时草稿";
      view.controller.open();
      await settle(signal);
      view.controller.close();
      view.controller.close();
      view.controller.close();
      await settle(signal);
      const confirmation = current.confirmOverlay;
      check(confirmations === 1 && view.controller.isOpen(), "重复关闭应共用一次确认并保留表单");
      check(confirmation?.querySelector('[role="alertdialog"]'), "未创建生产 Confirmation");
      requireTop(confirmation);
      confirmation.querySelector("[data-portal-confirm-cancel]").click();
      await settle(signal);
      check(view.controller.isOpen() && field.value === "未保存的临时草稿", "取消确认丢失了表单内容");
      view.controller.close();
      view.controller.close();
      await settle(signal);
      requireTop(confirmation);
      check(confirmations === 2, "取消后新一次关闭应重新确认且不重复排队");
      confirmation.querySelector("[data-portal-confirm-accept]").click();
      await settle(signal);
      check(!view.controller.isOpen() && confirmation.hidden, "批准后未完成原关闭动作或仍留有重复确认");
      return "重复关闭只出现一次确认，取消保留草稿，批准完成原关闭动作。";
    },

    async "ui.async.state-contract"(body, signal) {
      const host = append(body, "section");
      for (const state of ["loading", "empty", "error"]) {
        for (const density of ["panel", "compact", "inline"]) {
          const element = global.PortalUIPrimitives.renderAsyncState(host, { state, density });
          check(element?.dataset.state === state && element.dataset.density === density, "异步状态或密度未按明确契约渲染");
          check(element.getAttribute("role") === (state === "error" ? "alert" : "status"), "异步反馈缺少对应无障碍语义");
        }
      }
      const fallback = global.PortalUIPrimitives.renderAsyncState(host, { state: "unrecognized", density: "unrecognized" });
      check(fallback.dataset.state === "empty" && fallback.dataset.density === "panel", "未知状态或密度没有安全降级");
      let retries = 0;
      global.PortalUIPrimitives.renderAsyncState(host, {
        state: "error", onRetry: () => {
          retries += 1;
          global.PortalUIPrimitives.renderAsyncState(host, { state: "empty" });
        },
      });
      host.querySelector("button").click();
      await settle(signal);
      check(retries === 1 && host.firstElementChild.dataset.state === "empty", "错误态的本地重试未执行一次并更新内容");
      return "三种状态与密度、无障碍语义、未知值降级及本地重试全部符合契约。";
    },

    async "ui.resource-header.text-safety"(body, signal) {
      const host = append(body, "section");
      const title = window.PortalI18n.t('<img src="invalid-live-image" onerror="window.__designUiUnsafe=true"> & "业务标题"');
      const resourceId = currentCaseId();
      const identity = global.PortalResourceHeader.mount(host, { title, resource_id: resourceId, type: "test" });
      check(identity?.querySelector(".portalResourceIdentityTitle")?.textContent === title, "Resource Header 标题未按普通文本保留");
      check(!host.querySelector("img,script,iframe,[onerror]"), "业务文本变成了可执行标记");
      check(identity.querySelector("[data-portal-resource-id]")?.dataset.portalResourceId === resourceId, "Resource Header 丢失规范资源身份");
      global.PortalResourceHeader.mount(host, { title: window.PortalI18n.t("更新后的本地标题"), resource_id: resourceId, type: "test" });
      await settle(signal);
      check(host.querySelectorAll("[data-portal-resource-identity]").length === 1, "重新挂载产生重复 Header");
      check(host.querySelector(".portalResourceIdentityTitle")?.textContent === "更新后的本地标题", "重新挂载未更新标题");
      check(host.querySelector("[data-portal-resource-id]")?.textContent === resourceId, "标题更新改变了资源 ID");
      return "Resource Header 把业务文本安全转义，重挂载更新标题且保留资源身份。";
    },

    async "ui.action.inflight-dedup"(body, signal) {
      const button = append(body, "button", "本地计算", "portalButton");
      const original = button.textContent;
      let complete;
      let calls = 0;
      const states = [];
      const deferred = new Promise(resolve => { complete = resolve; });
      session.disposers.push(() => complete("cancelled"));
      const key = `design-ui:${session.token}`;
      const run = () => global.PortalUI.runMutation({
        key, button, notifyError: false,
        run: () => { calls += 1; return deferred; },
        onStateChange: state => states.push(state),
      });
      const first = run();
      const repeated = run();
      check(first === repeated && calls === 1, "相同 key 没有共用正在执行的 Promise");
      check(button.disabled && button.getAttribute("aria-busy") === "true", "执行中按钮没有忙碌保护");
      complete("local-only");
      check(await first === "local-only" && await repeated === "local-only", "重复调用未获得同一结果");
      await settle(signal);
      check(!button.disabled && button.textContent === original && button.getAttribute("aria-busy") !== "true", "结束后按钮未恢复");
      check(states.join(",") === "running,success", "局部状态重复或遗漏");
      await run();
      check(calls === 2, "完成后未释放 key，无法再次执行");
      return "同 key 进行中只执行一次、共享结果；结束恢复按钮并允许下一次本地计算。";
    },
  };

  function currentCaseId() {
    const caseId = String(session?.context?.caseId || "");
    check(/^test_[a-f0-9]{12}$/.test(caseId), "Resource Header 验收需要当前真实 TestCase ID");
    return caseId;
  }

  function confirmationSnapshot() {
    const overlay = document.querySelector("[data-portal-confirmation]");
    if (!overlay) return null;
    check(overlay.hidden, "请先处理页面已有的确认窗口");
    const selectors = ["#portalConfirmTitle", "#portalConfirmMessage", "[data-portal-confirm-description]", ".portalDetailFooter [data-portal-confirm-cancel]", "[data-portal-confirm-accept]"];
    return {
      overlay,
      fields: selectors.map(selector => {
        const element = overlay.querySelector(selector);
        return { element, text: element.textContent, hidden: element.hidden, className: element.getAttribute("class") };
      }),
      metadata: overlay.querySelector("[data-portal-confirm-metadata]"),
      metadataChildren: [...overlay.querySelector("[data-portal-confirm-metadata]").childNodes],
      metadataHidden: overlay.querySelector("[data-portal-confirm-metadata]").hidden,
      surface: overlay.querySelector(".portalConfirmSurface"),
      tone: overlay.querySelector(".portalConfirmSurface").dataset.tone,
    };
  }

  adapters["design-ui"] = Object.freeze({
    id: "design-ui",
    version: 1,
    ready: () => Boolean(global.PortalOverlay && global.PortalUI?.createDialogController && global.PortalUIPrimitives && global.PortalResourceHeader && global.PortalFormControls?.mountSearchField),
    capture(context = {}) {
      check(!session, "已有 Live 正在执行，请先恢复");
      check(global.PortalOverlay.top() === null, "请先关闭页面已有浮层，再启动共享组件 Live");
      const focus = document.activeElement;
      const selection = global.getSelection();
      const snapshot = {
        focus, scrollX: global.scrollX, scrollY: global.scrollY,
        selection: selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : [],
        fieldSelection: typeof focus?.selectionStart === "number" ? [focus.selectionStart, focus.selectionEnd, focus.selectionDirection] : null,
        fieldValue: focus && "value" in focus ? focus.value : null,
        scrollers: [...document.querySelectorAll("*")].filter(element => element.scrollHeight > element.clientHeight || element.scrollWidth > element.clientWidth).map(element => [element, element.scrollLeft, element.scrollTop]),
        confirmation: confirmationSnapshot(),
      };
      session = { context, snapshot, token: globalThis.PortalUIPrimitives.randomUUID(), root: null, body: null, disposers: [], restoring: false, confirmOverlay: null };
      session.confirmTitle = `丢弃 Live 临时输入 ${session.token.slice(0, 8)}？`;
      return snapshot;
    },
    async prepare(step, signal, context = {}) {
      assertActive(signal);
      check(Object.hasOwn(capabilities, step?.capability), `未登记能力：${step?.capability}`);
      if (!session.root) {
        session.root = append(document.body, "aside", undefined, "portalDetailSurface portalUiLiveStage");
        session.root.dataset.designUiOwned = session.token;
        session.root.setAttribute("aria-label", window.PortalI18n.t("共享组件只读 Live 验收"));
        const header = append(session.root, "header", undefined, "portalDetailHeader");
        append(header, "h2", "共享组件 · 只读验收");
        session.body = append(session.root, "div", undefined, "portalDetailBody");
      }
      session.context = { ...session.context, ...context };
      const target = append(session.body, "section");
      append(target, "p", step.caption || "执行组件契约验收");
      await settle(signal);
      return { target, capability: step.capability, token: session.token };
    },
    async commit(step, prepared, signal) {
      assertActive(signal);
      check(prepared?.token === session.token && prepared.capability === step?.capability && session.root?.contains(prepared.target), "prepare 结果不属于当前 Live 步骤");
      const caption = await capabilities[step.capability](prepared.target, signal);
      assertActive(signal);
      const result = append(prepared.target, "p", `通过：${caption}`);
      result.setAttribute("role", "status");
      prepared.target.dataset.designUiVerified = step.capability;
      return { target: prepared.target, caption };
    },
    async restore(snapshot) {
      if (!session) return;
      check(snapshot === session.snapshot, "不能用其他 Live 的快照恢复页面");
      const current = session;
      current.restoring = true;
      const failures = [];
      const confirmation = current.confirmOverlay;
      if (confirmation && !confirmation.hidden && confirmation.querySelector("#portalConfirmTitle")?.textContent === current.confirmTitle) {
        confirmation.querySelector("[data-portal-confirm-cancel]")?.click();
      }
      for (const dispose of current.disposers.splice(0).reverse()) {
        try { await dispose(); } catch (error) { failures.push(error); }
      }
      current.root?.remove();
      await nextFrame();
      await nextFrame();
      if (confirmation?.isConnected) {
        if (!confirmation.hidden) failures.push(new Error("另一个确认窗口已接管，保留用户当前确认"));
        else if (!snapshot.confirmation) {
          global.PortalOverlay.register(confirmation)?.destroy();
          confirmation.remove();
        } else {
          const saved = snapshot.confirmation;
          for (const field of saved.fields) {
            field.element.textContent = field.text;
            field.element.hidden = field.hidden;
            if (field.className === null) field.element.removeAttribute("class");
            else field.element.setAttribute("class", field.className);
          }
          saved.metadata.replaceChildren(...saved.metadataChildren);
          saved.metadata.hidden = saved.metadataHidden;
          saved.surface.dataset.tone = saved.tone;
        }
      }
      const draftChanged = snapshot.fieldSelection && snapshot.focus?.value !== snapshot.fieldValue;
      if (snapshot.selection.length && !draftChanged) {
        const selection = global.getSelection();
        selection.removeAllRanges();
        snapshot.selection.forEach(range => { if (range.commonAncestorContainer.isConnected) selection.addRange(range); });
      }
      if (snapshot.focus?.isConnected) snapshot.focus.focus({ preventScroll: true });
      if (snapshot.fieldSelection && snapshot.focus?.isConnected && !draftChanged) snapshot.focus.setSelectionRange(...snapshot.fieldSelection);
      for (const [element, left, top] of snapshot.scrollers) {
        if (element.isConnected) element.scrollTo({ left, top, behavior: "instant" });
      }
      global.scrollTo({ left: snapshot.scrollX, top: snapshot.scrollY, behavior: "instant" });
      session = null;
      if (failures.length) throw new AggregateError(failures, "共享组件 Live 恢复未完整完成");
    },
  });
})(window);
