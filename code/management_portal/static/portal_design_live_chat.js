(function (global) {
  "use strict";

  const capabilities = Object.freeze([
    "chat.loading-contract",
    "chat.target-transition",
    "chat.composer-refresh",
    "chat.draft-isolation",
    "chat.draft-recovery",
    "chat.message-reconciliation",
    "chat.identity-refresh",
    "chat.slash-navigation",
    "chat.workspace-update-isolation",
    "chat.history-reference",
    "chat.streaming-reading",
    "chat.input-send-guards",
  ]);
  let active = null;

  function requireCondition(condition, message) {
    if (!condition) throw new Error(`Chat Live 验收失败：${message}`);
  }

  function checkAbort(signal) {
    if (signal?.aborted) throw new DOMException("Chat Live 已取消", "AbortError");
  }

  function ready() {
    return !!(document.body && global.PortalChatWorkspace?.createWorkspace
      && global.PortalChatWorkspace?.createDraftStore && global.PortalState?.storage);
  }

  function capture() {
    requireCondition(ready(), "Chat Workspace 生产组件尚未加载");
    requireCondition(!active, "已有尚未恢复的 Chat 演示");
    const selection = global.getSelection();
    active = {
      prefix: `design-live-chat-${globalThis.PortalUIPrimitives.randomUUID()}`,
      focus: document.activeElement,
      ranges: Array.from({ length: selection?.rangeCount || 0 }, (_, index) => selection.getRangeAt(index).cloneRange()),
      scroll: [global.scrollX, global.scrollY],
      controls: [],
      cleanups: [],
      storage: new Map(),
      abortListeners: [],
      surface: null,
      disposed: false,
      actionAttempts: 0,
    };
    return active;
  }

  function preserveKey(snapshot, key) {
    if (!snapshot.storage.has(key)) {
      snapshot.storage.set(key, global.PortalState.storage.readText(key, null));
    }
  }

  function contract(scope = "alpha", overrides = {}) {
    return {
      schema: "portal.chat_workspace.v3",
      workspace_key: `design-live-chat:${scope}`,
      title: window.PortalI18n.text`Chat 只读验收 · ${scope}`,
      messages: [],
      ...overrides,
      composer: {
        can_edit: true,
        can_send: false,
        can_attach: false,
        can_stop: false,
        can_auto: false,
        refs_enabled: false,
        history_scope: scope,
        ...overrides.composer,
      },
    };
  }

  function createSurface(snapshot) {
    if (snapshot.surface) return snapshot.surface;
    const surface = document.createElement("section");
    surface.dataset.designLiveChat = snapshot.prefix;
    surface.setAttribute("role", "region");
    surface.setAttribute("aria-label", window.PortalI18n.t("Chat 生产组件只读验收"));
    surface.style.cssText = "position:fixed;inset:10vh 5vw;z-index:1000;display:flex;flex-direction:column;gap:1rem;padding:1rem;overflow:auto;background:var(--panel,#fff);color:var(--text,#172033);border:1px solid var(--border,#cbd5e1);border-radius:var(--radius,8px)";
    const label = document.createElement("strong");
    label.textContent = window.PortalI18n.t("Chat 行为验收：临时本地数据，不连接 Resident、不发送消息");
    surface.append(label);
    document.body.append(surface);
    snapshot.surface = surface;
    return surface;
  }

  function mount(snapshot, options = {}) {
    const root = document.createElement("section");
    root.style.cssText = "flex:1 0 auto;min-height:14rem;min-width:0";
    createSurface(snapshot).append(root);
    const owner = options.owner === undefined ? "design-live" : options.owner;
    const namespace = `${snapshot.prefix}:${options.namespace || "shared"}`;
    const draftStore = global.PortalChatWorkspace.createDraftStore({ owner, namespace });
    for (const scope of ["alpha", "beta"]) preserveKey(snapshot, draftStore.storageKey(scope));
    const id = `${snapshot.prefix}-${snapshot.controls.length}`;
    const heightKey = `${snapshot.prefix}:height:${id}`;
    preserveKey(snapshot, heightKey);
    const rejectAction = () => {
      snapshot.actionAttempts += 1;
      throw new Error("Chat Live 禁止发送、上传、停止或修改真实会话");
    };
    const controller = global.PortalChatWorkspace.createWorkspace({
      root,
      id,
      draftStore,
      composerHeightKey: heightKey,
      composerResize: false,
      onSend: rejectAction,
      onStop: rejectAction,
      upload: rejectAction,
      onAuto: rejectAction,
      onRefresh: rejectAction,
      onQueueInject: rejectAction,
      onQueueDelete: rejectAction,
      onQueueClear: rejectAction,
      ...(options.loading ? {} : { contract: options.contract || contract() }),
    });
    const instance = { root, controller, draftStore, destroyed: false };
    snapshot.controls.push(instance);
    return instance;
  }

  function destroyInstance(instance) {
    if (instance.destroyed) return;
    instance.destroyed = true;
    instance.controller.destroy();
    instance.root.remove();
  }

  function typeDraft(controller, text) {
    controller.setDraftText(text);
    controller.els.input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function selectText(input, start, end = start) {
    input.focus({ preventScroll: true });
    const range = document.createRange();
    range.setStart(input.firstChild, start);
    range.setEnd(input.firstChild, end);
    const selection = global.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function assertLocalOnly(snapshot) {
    requireCondition(snapshot.actionAttempts === 0, "发生了被禁止的会话动作");
    for (const { controller, destroyed } of snapshot.controls) {
      if (!destroyed && controller.els.send) {
        requireCondition(controller.els.send.disabled, "演示实例不应启用发送");
      }
    }
  }

  async function prepare(step, signal) {
    checkAbort(signal);
    requireCondition(active && !active.disposed, "必须先 capture 再 prepare");
    requireCondition(capabilities.includes(step.capability), `未知能力 ${step.capability}`);
    const snapshot = active;
    requireCondition(!snapshot.prepared, "一次演示只允许一个未完成的步骤");
    const onAbort = () => restore(snapshot);
    signal?.addEventListener("abort", onAbort, { once: true });
    snapshot.abortListeners.push([signal, onAbort]);
    const instance = mount(snapshot, { loading: step.capability === "chat.loading-contract" });
    const prepared = { target: instance.root, instance, snapshot, capability: step.capability };
    if (step.capability === "chat.loading-contract") instance.controller.setLoading("等待第一份 Chat 合同");
    snapshot.prepared = prepared;
    return prepared;
  }

  const checks = {
    "chat.workspace-update-isolation": async ({ instance }) => {
      const { controller, root } = instance;
      const workspaceKey = controller.state.contract.workspace_key;
      const first = { id: "update-original", role: "human", content: "当前会话已确认消息" };
      const appended = { id: "update-appended", role: "agent", content: "当前会话增量回复" };
      controller.setMessages([first]);
      const originalNode = root.querySelector('[data-message-key="update-original"]');
      const foreign = controller.applyUpdate({
        workspace_key: `${workspaceKey}:other`, revision: "foreign-1",
        reset: true, messages: [{ id: "foreign-update", role: "agent", content: "其他会话消息不得进入" }],
      });
      requireCondition(foreign === false && controller.messages().length === 1, "其他 Workspace 的更新被接收");
      requireCondition(root.querySelector('[data-message-key="update-original"]') === originalNode, "跨目标更新破坏了已有历史");
      const update = { workspace_key: workspaceKey, revision: "local-1", messages: [appended] };
      requireCondition(controller.applyUpdate(update) === true, "当前 Workspace 的增量未应用");
      requireCondition(controller.messages().map(message => message.id).join() === "update-original,update-appended", "增量更新没有追加到当前消息集合");
      requireCondition(controller.applyUpdate(update) === false && controller.messages().length === 2, "相同修订重复追加消息");
      const reset = { workspace_key: workspaceKey, content_signature: "reset-1", mode: "reset", messages: [appended] };
      requireCondition(controller.applyUpdate(reset) === true, "reset 未应用权威集合");
      requireCondition(controller.messages().length === 1 && !root.querySelector('[data-message-key="update-original"]'), "reset 没有替换旧消息");
      requireCondition(controller.applyUpdate(reset) === false, "相同内容签名没有去重");
      requireCondition(controller.applyUpdate({ ...reset, force: true }) === true && controller.messages().length === 1, "force 重放未遵守 reset 语义");
      return "当前目标接受增量、拒绝其他目标；修订/内容签名去重，reset 与 force 保持权威消息集合。";
    },
    "chat.history-reference": async ({ instance, snapshot }, signal) => {
      const { controller, root } = instance;
      const quote = "选中的重要上下文。完整引文必须保留，不能截断。".repeat(160);
      controller.setMessages([
        { id: "reference-original", role: "human", author: "Local reader", content: quote },
        { id: "reference-unselected", role: "agent", content: "未选中的消息不应进入引用" },
      ]);
      const existingToolbars = new Set(document.querySelectorAll(".chatSelectionContextAction"));
      const selectionController = global.PortalChatWorkspace.createMessageSelectionController({
        root: controller.els.messages,
        onAdd: selection => controller.addReference(selection),
        onError: error => { snapshot.referenceError = error; },
      });
      snapshot.cleanups.push(() => selectionController.destroy());
      const toolbar = [...document.querySelectorAll(".chatSelectionContextAction")].find(node => !existingToolbars.has(node));
      requireCondition(toolbar, "未创建当前验收实例的选段工具栏");
      const content = root.querySelector('[data-message-key="reference-original"] .messageContent');
      requireCondition(content, "没有可选择的消息正文");
      const range = document.createRange();
      range.selectNodeContents(content);
      const selection = global.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      content.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      await waitUntil(() => !toolbar.hidden, signal, "选段操作没有提供添加上下文入口");
      toolbar.querySelector("button").click();
      await waitUntil(() => !!snapshot.referenceError || controller.attachments().length === 1, signal, "选段未转为引用附件");
      requireCondition(!snapshot.referenceError, "添加上下文发生错误");
      const payload = controller.payload();
      const reference = payload.attachments[0];
      requireCondition(reference.content_type === "text/x-portal-preview-reference", "历史选段被当成普通上传文件");
      requireCondition(reference.preview_selection.text.includes(quote) && reference.preview_selection.path === "Chat Workspace", "完整引文或来源路径丢失");
      requireCondition(reference.preview_selection.source_message_ids.join() === "reference-original", "引用混入未选择消息或丢失来源 ID");
      requireCondition(!reference.uploading && !reference.error && payload.text.includes(quote), "只读引用错误进入图片上传流程或正文被截断");
      const refNode = root.querySelector(".composerImageRef");
      requireCondition(refNode && !refNode.isContentEditable, "引用没有作为不可编辑节点插入 Composer");
      controller.applyContract(contract("beta"));
      requireCondition(controller.attachments().length === 0 && !controller.payload().text.includes(quote), "引用泄漏到另一会话 scope");
      controller.applyContract(contract("alpha"));
      requireCondition(controller.attachments().length === 1 && controller.payload().text.includes(quote), "切回原会话丢失选段引用");
      return "真实选段操作生成完整引用与来源消息 ID，保留不可编辑引用节点，且不同会话上下文隔离。";
    },
    "chat.streaming-reading": async ({ instance }, signal) => {
      const { controller, root } = instance;
      root.style.flex = "0 0 50vh";
      root.style.height = "50vh";
      root.style.minHeight = "0";
      const messages = Array.from({ length: 36 }, (_, index) => ({
        id: `reading-message-${index}`, role: index % 2 ? "agent" : "human",
        content: `历史消息 ${index}：${"用于阅读位置验证的完整正文。".repeat(14)}`,
      }));
      controller.setMessages(messages);
      const viewport = controller.els.messages;
      await waitUntil(() => viewport.scrollHeight > viewport.clientHeight && viewport.clientHeight > 0, signal, "历史消息没有形成可阅读的滚动区域");
      await settleFrames(signal);
      viewport.scrollTop = viewport.scrollHeight;
      viewport.dispatchEvent(new Event("scroll"));
      await settleFrames(signal);
      viewport.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: -1 }));
      viewport.scrollTop = (viewport.scrollHeight - viewport.clientHeight) / 3;
      viewport.dispatchEvent(new Event("scroll"));
      await settleFrames(signal);
      const anchor = visibleMessage(viewport);
      const readingTop = viewport.scrollTop;
      requireCondition(anchor && viewport.scrollHeight - readingTop > viewport.clientHeight * 2, "未进入用户主动向上阅读状态");
      controller.setRuntime([], true, { runtimePhase: "running" });
      await settleFrames(signal);
      requireCondition(visibleMessage(viewport)?.dataset.messageKey === anchor.dataset.messageKey, "运行状态刷新抢走了阅读位置");
      controller.setMessages([...messages, { id: "reading-new-tail", role: "agent", content: "流式追加的新回复" }]);
      await settleFrames(signal);
      requireCondition(visibleMessage(viewport)?.dataset.messageKey === anchor.dataset.messageKey, "流式新消息将用户拉回尾部");
      root.style.height = "45vh";
      root.style.flexBasis = "45vh";
      await settleFrames(signal);
      requireCondition(visibleMessage(viewport)?.dataset.messageKey === anchor.dataset.messageKey, "容器变化丢失原消息锚点");
      requireCondition(root.querySelector('[data-message-key="reading-new-tail"]'), "阅读保持的同时新回复没有进入消息集合");
      return "主动向上阅读后，运行状态、新回复与容器变化均保持原消息锚点，新回复仍正常追加。";
    },
    "chat.input-send-guards": async ({ instance, snapshot }, signal) => {
      const { controller } = instance;
      const input = controller.els.input;
      const shortcut = (metaKey, extra = {}) => {
        const draft = controller.draftText();
        input.dispatchEvent(new KeyboardEvent("keydown", {
          key: "Enter", ctrlKey: !metaKey, metaKey, bubbles: true, cancelable: true, ...extra,
        }));
        requireCondition(controller.draftText() === draft, "被阻止的提交改写了当前草稿");
      };
      typeDraft(controller, "快捷键不能误发送的未完成输入");
      controller.applyContract(contract("alpha", { composer: { can_send: true } }));
      requireCondition(!controller.els.send.disabled, "允许发送的空闲合同没有启用发送控件");
      shortcut(false, { isComposing: true });
      shortcut(true, { keyCode: 229 });
      input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
      shortcut(false);
      shortcut(true);
      requireCondition(snapshot.actionAttempts === 0, "输入法组合期间快捷键触发了发送");
      input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
      requireCondition(!controller.els.send.disabled, "输入法组合结束后没有立即恢复发送控件");
      controller.applyContract(contract("alpha", { composer: { can_send: false } }));
      requireCondition(controller.els.send.disabled, "撤销发送能力后按钮仍可用");
      shortcut(false);
      controller.applyContract(contract("alpha", { composer: { can_send: true } }));
      controller.setBusy(true);
      requireCondition(controller.els.send.disabled, "忙碌中的 Workspace 未禁用重复提交");
      shortcut(true);
      controller.setBusy(false);
      controller.setAttachments([{ id: "local-pending-file", kind: "file", name: "pending.txt", uploading: true }]);
      requireCondition(controller.els.send.disabled, "存在上传中附件时仍允许发送");
      shortcut(false);
      controller.setAttachments([]);
      requireCondition(!controller.els.send.disabled, "临时阻塞解除后未恢复合同允许的可用状态");
      const restoredDraft = controller.draftText();
      controller.applyContract(contract("alpha", { composer: { can_send: false } }));
      requireCondition(snapshot.actionAttempts === 0 && controller.messages().length === 0, "只读按键验收触发了消息动作");
      requireCondition(controller.draftText() === restoredDraft, "更新发送权限清空了当前草稿");
      return "Ctrl/⌘+Enter 尊重输入法、权限、忙碌和上传阻塞；组合结束立即恢复发送可用性，全程不发送消息。";
    },
    "chat.loading-contract": async ({ instance }) => {
      const { controller, root } = instance;
      requireCondition(controller.isLoading(), "合同到达前没有 loading 状态");
      requireCondition(!root.querySelector(".composerInput"), "合同到达前已创建 Composer");
      controller.applyContract(contract("alpha", {
        title: window.PortalI18n.t("读取失败（本地验收合同）"),
        composer: { can_edit: false, can_send: false },
        rendering: { empty_text: "无法加载此会话" },
      }));
      requireCondition(!controller.isLoading(), "错误合同没有结束 loading");
      requireCondition(root.textContent.includes("无法加载此会话"), "读取失败没有可见反馈");
      requireCondition(!controller.els.input.isContentEditable, "错误合同仍可输入");
      requireCondition(controller.els.send.disabled, "错误合同仍可发送");
      return "合同到达前无输入框；失败合同显示错误并禁用编辑与发送。";
    },
    "chat.target-transition": async ({ instance }, signal) => {
      const { controller, root } = instance;
      controller.applyContract(contract("alpha", { messages: [{ id: "old-message", role: "human", content: "原会话正文" }] }));
      typeDraft(controller, "原会话未发送草稿");
      controller.setLoading("正在等待新目标的合同与快照");
      requireCondition(root.textContent.includes("原会话正文"), "目标尚未就绪时旧消息丢失");
      requireCondition(controller.els.composer.inert && controller.els.body.inert && controller.els.send.disabled, "切换期间旧目标未被冻结");
      await waitUntil(() => document.activeElement !== controller.els.input, signal, "旧目标冻结后仍保留输入焦点");
      controller.els.input.focus();
      requireCondition(document.activeElement !== controller.els.input, "切换期间旧输入仍可获得焦点");
      controller.applyContract(contract("beta", { messages: [{ id: "new-message", role: "human", content: "新会话正文" }] }));
      requireCondition(controller.draftScope() === "beta" && !controller.isLoading(), "新合同没有提交目标 scope");
      requireCondition(root.textContent.includes("新会话正文") && !root.textContent.includes("原会话正文"), "新旧目标的消息混合");
      requireCondition(controller.draftText() === "", "旧目标草稿泄漏到新目标");
      controller.applyContract(contract("alpha"));
      requireCondition(controller.draftText() === "原会话未发送草稿", "切回原目标后草稿丢失");
      return "目标切换冻结旧输入；新合同原子替换消息，切回后原草稿仍在。";
    },
    "chat.composer-refresh": async ({ instance }) => {
      const { controller } = instance;
      typeDraft(controller, "保留正在输入的草稿");
      const input = controller.els.input;
      selectText(input, 2, 5);
      input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
      const anchor = global.getSelection().anchorNode;
      controller.applyContract(contract("alpha", { composer: { placeholder: window.PortalI18n.t("已刷新合同") }, runtime: { live: true } }));
      requireCondition(controller.els.input === input, "同目标刷新重建了 Composer DOM");
      requireCondition(controller.draftText() === "保留正在输入的草稿", "刷新清空草稿");
      requireCondition(document.activeElement === input, "刷新抢走输入焦点");
      const selection = global.getSelection();
      requireCondition(selection.anchorNode === anchor && selection.anchorOffset === 2 && selection.focusOffset === 5, "刷新丢失选区");
      requireCondition(controller.state.composerImeActive, "刷新中断了输入法组合");
      input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
      return "同目标合同刷新保留输入 DOM、草稿、焦点、选区与输入法组合。";
    },
    "chat.draft-isolation": async ({ instance, snapshot }) => {
      const { controller, draftStore } = instance;
      typeDraft(controller, "owner A / namespace A / scope alpha 草稿");
      controller.saveDraft();
      const otherScope = mount(snapshot, { contract: contract("beta") });
      const otherOwner = mount(snapshot, { owner: "design-live-other" });
      const otherNamespace = mount(snapshot, { namespace: "other" });
      const anonymous = mount(snapshot, { owner: "" });
      for (const peer of [otherScope, otherOwner, otherNamespace, anonymous]) {
        requireCondition(peer.controller.draftText() === "", "草稿跨 owner、namespace 或 scope 泄漏");
      }
      requireCondition(anonymous.draftStore.storageKey("alpha").includes(":anonymous:"), "未知 owner 未使用 anonymous");
      typeDraft(otherScope.controller, "scope beta 独立草稿");
      otherScope.controller.saveDraft();
      requireCondition(draftStore.read("alpha").text === controller.draftText(), "另一 scope 覆盖原草稿");
      const restored = mount(snapshot);
      requireCondition(restored.controller.draftText() === controller.draftText(), "同一身份与 scope 无法共享恢复草稿");
      return "不同 owner、namespace、scope 草稿隔离；相同身份与 scope 正确恢复。";
    },
    "chat.draft-recovery": async ({ instance, snapshot }, signal) => {
      const { controller, draftStore } = instance;
      typeDraft(controller, "刷新后应恢复的本地草稿");
      selectText(controller.els.input, 1, 4);
      controller.els.input.dispatchEvent(new Event("keyup", { bubbles: true }));
      controller.saveDraft({ immediate: false });
      await waitUntil(() => {
        const saved = global.PortalState.storage.readJson(draftStore.storageKey("alpha"), {});
        return saved.text === "刷新后应恢复的本地草稿";
      }, signal, "草稿未在防抖后写入存储");
      destroyInstance(instance);
      const reopened = mount(snapshot);
      requireCondition(reopened.controller.draftText() === "刷新后应恢复的本地草稿", "新实例无法恢复已保存草稿");
      typeDraft(reopened.controller, "");
      reopened.controller.saveDraft();
      requireCondition(global.PortalState.storage.readText(draftStore.storageKey("alpha"), null) === null, "空草稿未删除存储项");
      return "输入防抖后保存，重建 Chat 后恢复，清空后移除草稿记录。";
    },
    "chat.message-reconciliation": async ({ instance }) => {
      const { controller, root } = instance;
      const first = { id: "message-human", role: "human", content: "稳定的人类消息" };
      const second = { id: "message-agent", role: "agent", content: "第一段回复" };
      controller.setMessages([first, second]);
      const human = root.querySelector('[data-message-key="message-human"]');
      const agent = root.querySelector('[data-message-key="message-agent"]');
      requireCondition(human && agent, "消息缺少稳定 ID 对应的 DOM");
      controller.setMessages([first, second, { id: "message-tail", role: "agent", content: "新增尾部回复" }]);
      requireCondition(root.querySelector('[data-message-key="message-human"]') === human, "尾部追加重建了无关历史消息");
      requireCondition(root.querySelector('[data-message-key="message-agent"]') === agent, "尾部追加重建了已有回复");
      controller.setMessages([first, { ...second, content: "第一段回复已更新" }, { id: "message-tail", role: "agent", content: "新增尾部回复" }]);
      requireCondition(root.querySelector('[data-message-key="message-human"]') === human, "更新 Agent 内容影响无关消息");
      requireCondition(root.querySelector('[data-message-key="message-agent"]').textContent.includes("第一段回复已更新"), "同 ID 的新内容未更新");
      requireCondition(root.querySelectorAll("article.messageItem").length === 3, "消息追加或更新造成重复节点");
      return "追加与内容更新保留无关消息 DOM，按稳定 ID 更新且不重复。";
    },
    "chat.identity-refresh": async ({ instance }) => {
      const { controller, root } = instance;
      const messages = [{ id: "identity-message", role: "human", content: "绑定前已有消息" }];
      controller.applyContract(contract("alpha", { title: window.PortalI18n.t("待绑定会话"), messages }));
      typeDraft(controller, "绑定期间的未发送内容");
      const input = controller.els.input;
      const message = root.querySelector('[data-message-key="identity-message"]');
      controller.applyContract(contract("alpha", {
        title: window.PortalI18n.t("已绑定的 Resident"),
        subtitle: window.PortalI18n.t("codex · 只读示例帐号"),
        header: { subtitle_parts: [
          { text: window.PortalI18n.t("Resident: design-live-resident"), identity: "resident", copy_value: "design-live-resident" },
          { text: window.PortalI18n.t("Session: design-live-session"), identity: "session", copy_value: "design-live-session" },
        ] },
        messages,
      }));
      const header = root.querySelector(".portalWorkspaceHead");
      requireCondition(header.textContent.includes("已绑定的 Resident") && header.textContent.includes("design-live-session"), "新合同未刷新已打开 Chat 的身份");
      requireCondition(!header.textContent.includes("待绑定会话"), "旧身份残留");
      requireCondition(controller.els.input === input && controller.draftText() === "绑定期间的未发送内容", "更新身份破坏了输入实例或草稿");
      requireCondition(root.querySelector('[data-message-key="identity-message"]') === message, "更新身份重建了消息历史");
      requireCondition(header.querySelector('[data-workspace-copy-value="design-live-session"]'), "Session 复制入口未更新");
      return "新身份合同就地更新标题、Session 与复制入口，并保留消息和输入草稿。";
    },
    "chat.slash-navigation": async ({ instance }) => {
      const { controller, root } = instance;
      const input = controller.els.input;
      const given = ["/status", "/goal", "/model", "/compact", "/portal-help"];
      controller.applyContract(contract("alpha", { composer: {
        slash_commands: given.map(command => ({ command, title: command.slice(1), description: "" })),
      } }));
      typeDraft(controller, "/");
      input.focus({ preventScroll: true });
      const commands = [...root.querySelectorAll("[data-portal-slash-command]")].map(button => button.dataset.portalSlashCommand);
      requireCondition(JSON.stringify(commands) === JSON.stringify(given), "斜杠菜单与会话给出的命令不一致");
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
      requireCondition(controller.draftText() === "/goal ", "方向键选择后 Enter 未插入命令和空格");
      requireCondition(controller.messages().length === 0, "选择斜杠命令不应生成消息");
      typeDraft(controller, "/");
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
      const menu = root.querySelector(".composerSlashCommandMenu");
      requireCondition(!menu || menu.hidden, "Escape 未关闭命令菜单");
      requireCondition(controller.draftText() === "/", "关闭菜单改变了未提交草稿");
      controller.applyContract(contract("alpha", { composer: { slash_commands: [] } }));
      typeDraft(controller, "/");
      const empty = root.querySelector(".composerSlashCommandMenu");
      requireCondition(!empty || empty.hidden, "没有给出命令的会话仍显示斜杠菜单");
      return "斜杠菜单只列会话给出的命令，支持方向键、Enter 插入和 Escape 关闭；没有命令的会话不显示菜单，全程未发送消息。";
    },
  };

  async function waitUntil(predicate, signal, message) {
    const deadline = performance.now() + 3000;
    while (!predicate()) {
      checkAbort(signal);
      requireCondition(performance.now() < deadline, message);
      await new Promise(resolve => global.setTimeout(resolve, 25));
    }
    checkAbort(signal);
  }

  function visibleMessage(viewport) {
    const top = viewport.getBoundingClientRect().top;
    return [...viewport.querySelectorAll("article[data-message-key]")]
      .find(row => row.getBoundingClientRect().bottom > top && row.getBoundingClientRect().top >= top)
      || null;
  }

  async function settleFrames(signal) {
    for (let frame = 0; frame < 3; frame += 1) {
      checkAbort(signal);
      await new Promise(resolve => global.requestAnimationFrame(resolve));
    }
    checkAbort(signal);
  }

  async function commit(step, prepared, signal) {
    checkAbort(signal);
    requireCondition(active && prepared === active.prepared && prepared.snapshot === active, "步骤与 capture 不匹配");
    requireCondition(step.capability === prepared.capability, "步骤能力与 prepare 不匹配");
    requireCondition(prepared.target.isConnected, "验收组件已离开页面");
    const snapshot = active;
    const caption = await checks[step.capability](prepared, signal);
    checkAbort(signal);
    assertLocalOnly(snapshot);
    snapshot.prepared = null;
    const target = prepared.target.isConnected ? prepared.target : snapshot.surface;
    target.dataset.designLiveChatVerified = step.capability;
    return { target, caption };
  }

  function restore(snapshot) {
    if (!snapshot || snapshot.disposed) return;
    snapshot.disposed = true;
    let failure = null;
    for (const [signal, listener] of snapshot.abortListeners) signal?.removeEventListener("abort", listener);
    for (const cleanup of snapshot.cleanups) {
      try { cleanup(); } catch (error) { failure ||= error; }
    }
    for (const instance of snapshot.controls) {
      try { destroyInstance(instance); } catch (error) { failure ||= error; }
    }
    for (const [key, value] of snapshot.storage) {
      if (value === null) global.PortalState.storage.remove(key);
      else global.PortalState.storage.writeText(key, value);
    }
    snapshot.surface?.remove();
    if (snapshot.focus?.isConnected) snapshot.focus.focus?.({ preventScroll: true });
    if (snapshot.ranges.every(range => range.startContainer.isConnected && range.endContainer.isConnected)) {
      const selection = global.getSelection();
      selection.removeAllRanges();
      for (const range of snapshot.ranges) selection.addRange(range);
    }
    global.scrollTo(...snapshot.scroll);
    if (active === snapshot) active = null;
    if (failure) throw failure;
  }

  global.PortalDesignLiveAdapters = global.PortalDesignLiveAdapters || {};
  global.PortalDesignLiveAdapters["design-chat"] = {
    id: "design-chat", version: 1, capabilities, ready, capture, prepare, commit, restore,
  };
}(window));
