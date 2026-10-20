(function (global) {
  "use strict";

  const adapterId = "design-lineage";
  const primary = "resident_100000000001";
  const derived = "resident_100000000002";
  const capabilities = Object.freeze([
    "lineage.intake-work", "lineage.derived-isolation", "lineage.queue-receipts",
    "lineage.cursor-recovery", "lineage.session-rebinding", "lineage.identity-race", "lineage.stop-receipt",
  ]);
  let active = null;
  const clone = value => JSON.parse(JSON.stringify(value));
  function requireCondition(condition, message) {
    if (!condition) throw new Error(`Lineage Live 验收失败：${message}`);
  }
  function checkSignal(signal) {
    if (signal?.aborted) throw new DOMException("Lineage Live 已取消", "AbortError");
  }
  function validateStep(step) {
    requireCondition(step && capabilities.includes(step.capability)
      && Object.keys(step).every(key => ["capability", "caption"].includes(key))
      && (step.caption === undefined || typeof step.caption === "string"), "只接受已登记 capability/caption");
  }
  function ready() {
    return !!(document.body && global.PortalChatWorkspace?.createWorkspace && global.PortalChatWorkspace?.createDraftStore);
  }
  function capture() {
    requireCondition(ready() && !active, "组件未就绪或上次演示尚未恢复");
    active = { id: globalThis.PortalUIPrimitives.randomUUID(), focus: document.activeElement, aborter: new AbortController(),
      subscriptions: [], controllers: [], abortListeners: [], disposed: false, writeAttempts: 0, prepared: null };
    return active;
  }
  function snapshotFor(residentId, state = {}) {
    return { resident: { resident_id: residentId }, cursor: 0, messages: [], input_states: [], activities: [],
      runtime_settings: {}, state: { intake_state: "open", work_state: "idle", ...state }, controls: controlsFor(state), queue: { resident_id: residentId, items: [] } };
  }
  function controlsFor(state) {
    const closed = state.intake_state === "closed";
    return { can_send: !closed, can_stop: false, disabled_reason: closed ? "Resident 已关闭接收" : "", send_hint: "本地演示，不发送" };
  }
  function bindAbort(snapshot, signal) {
    checkSignal(signal);
    if (!signal) return snapshot.aborter.signal;
    const listener = () => restore(snapshot);
    signal.addEventListener("abort", listener, { once: true });
    snapshot.abortListeners.push([signal, listener]);
    return AbortSignal.any([signal, snapshot.aborter.signal]);
  }
  async function until(predicate, signal, message) {
    const deadline = performance.now() + 3000;
    while (!predicate()) {
      checkSignal(signal);
      requireCondition(performance.now() < deadline, message);
      await new Promise(resolve => setTimeout(resolve, 15));
    }
    checkSignal(signal);
  }
  function show(prepared, label, value) {
    requireCondition(prepared.target.isConnected, "演示目标已被移除");
    const row = document.createElement("li");
    row.textContent = `${label}：${typeof value === "string" ? value : JSON.stringify(value)}`;
    prepared.output.append(row);
  }
  function contractFor(snapshot, residentId, state, messages = [], controls = controlsFor(state)) {
    const view = snapshot.frame.contentWindow.PortalResidentProtocol.stateView(state);
    return { schema: "portal.chat_workspace.v3", workspace_key: `lineage-live:${snapshot.id}:${residentId}`,
      title: window.PortalI18n.t("隔离 Resident 协议投影"), messages: messages.map(message => ({ ...message, id: message.message_id })),
      composer: { can_edit: true, can_send: controls.can_send, can_stop: false, can_attach: false,
        can_auto: false, refs_enabled: false, history_scope: residentId }, runtime: { live: view.working } };
  }
  function mountWorkspace(snapshot, residentId = primary) {
    const root = document.createElement("section");
    snapshot.surface.append(root);
    const rejectAction = () => { snapshot.writeAttempts += 1; throw new Error("隔离 Live 禁止真实业务操作"); };
    const controller = global.PortalChatWorkspace.createWorkspace({ root, id: `lineage-live-${snapshot.id}-${snapshot.controllers.length}`,
      draftStore: global.PortalChatWorkspace.createDraftStore({ owner: "lineage-live", namespace: snapshot.id, storageKey: () => "" }),
      composerHeightKey: "", composerResize: false, contract: contractFor(snapshot, residentId, {}),
      onSend: rejectAction, onStop: rejectAction, onAuto: rejectAction, upload: rejectAction,
      onRefresh: rejectAction, onQueueInject: rejectAction, onQueueDelete: rejectAction, onQueueClear: rejectAction });
    snapshot.controllers.push(controller);
    return controller;
  }
  async function loadScript(frame, source, signal) {
    checkSignal(signal);
    await new Promise((resolve, reject) => {
      const script = frame.contentDocument.createElement("script");
      const abort = () => finish(new DOMException("Lineage Live 已取消", "AbortError"));
      const finish = error => {
        clearTimeout(timer);
        signal.removeEventListener("abort", abort);
        script.onload = null;
        script.onerror = null;
        if (error) { script.remove(); reject(error); } else resolve();
      };
      const timer = setTimeout(() => finish(new Error(`生产脚本加载超时：${source}`)), 5000);
      signal.addEventListener("abort", abort, { once: true });
      script.onload = () => finish();
      script.onerror = () => finish(new Error(`生产脚本加载失败：${source}`));
      script.src = source;
      frame.contentDocument.head.append(script);
    });
  }
  async function prepare(step, externalSignal) {
    const snapshot = active;
    try {
      requireCondition(snapshot && !snapshot.disposed && !snapshot.prepared, "缺少 capture 或步骤尚未结束");
      validateStep(step);
      const signal = bindAbort(snapshot, externalSignal);
      const surface = document.createElement("section");
      surface.dataset.designLiveLineage = snapshot.id;
      surface.setAttribute("role", "region");
      surface.setAttribute("aria-label", window.PortalI18n.t("Lineage 生产协议隔离验收"));
      surface.style.cssText = "position:fixed;inset:10vh 5vw;z-index:1000;overflow:auto;padding:1rem;background:var(--panel,#fff);color:var(--text,#172033);border:1px solid var(--border,#ccc);border-radius:8px";
      const heading = document.createElement("h3");
      heading.textContent = window.PortalI18n.t("Lineage 核心行为 · 本地数据 / 原始生产组件 / 不连接真实 Resident");
      const output = document.createElement("ol");
      output.setAttribute("role", "status");
      surface.append(heading, output);
      document.body.append(surface);
      snapshot.surface = surface;
      const frame = document.createElement("iframe");
      frame.title = window.PortalI18n.t("Lineage 隔离协议执行环境");
      frame.hidden = true;
      frame.setAttribute("sandbox", "allow-scripts allow-same-origin");
      surface.append(frame);
      snapshot.frame = frame;
      const isolated = frame.contentWindow;
      const transport = { snapshots: new Map([[primary, snapshotFor(primary)], [derived, snapshotFor(derived)]]),
        snapshotReads: 0, projectionReads: 0, eventReads: 0, stopRequests: [],
        projection: residentId => Promise.resolve({ resident_id: residentId, session_id: "session-before", title: window.PortalI18n.t("稳定 Resident") }),
        events: residentId => Promise.resolve({ resident_id: residentId, cursor: 0, events: [] }),
        stop: payload => Promise.resolve({ resident_id: payload.resident_id, cursor: 1, state: { intake_state: "open", work_state: "stopping", active_message_id: payload.message_id } }) };
      const denyNetwork = () => { snapshot.writeAttempts += 1; throw new Error("隔离协议不允许网络或业务写入"); };
      isolated.fetch = denyNetwork;
      isolated.XMLHttpRequest = denyNetwork;
      isolated.WebSocket = denyNetwork;
      isolated.EventSource = denyNetwork;
      isolated.PortalResidentService = Object.freeze({
        snapshot: residentId => { transport.snapshotReads += 1; return Promise.resolve(clone(transport.snapshots.get(residentId) || snapshotFor(residentId))); },
        projection: ({ residentId }) => { transport.projectionReads += 1; return transport.projection(residentId); },
        events: (residentId, options) => { transport.eventReads += 1; return transport.events(residentId, options); },
        cancelInput: payload => { transport.stopRequests.push(clone(payload)); return transport.stop(payload); },
        sendInput: denyNetwork, injectInput: denyNetwork, clearMailbox: denyNetwork,
      });
      await loadScript(frame, "/static/resident_protocol.js", signal);
      await loadScript(frame, "/static/resident_projection.js", signal);
      checkSignal(signal);
      requireCondition(isolated.PortalResidentProtocol?.acquire && isolated.PortalResidentProjection?.load, "未加载真实生产协议");
      const prepared = { snapshot, target: surface, output, transport, signal, capability: step.capability,
        protocol: isolated.PortalResidentProtocol, projection: isolated.PortalResidentProjection, errors: [] };
      prepared.controller = mountWorkspace(snapshot);
      snapshot.prepared = prepared;
      return prepared;
    } catch (error) { restore(snapshot); throw error; }
  }
  async function acquire(prepared, residentId = primary) {
    const subscription = prepared.protocol.acquire(residentId, update => {
      if (prepared.snapshot.disposed) return;
      if (update.type === "error") prepared.errors.push(update.error?.message || String(update.error));
      if (residentId === primary && update.snapshot) prepared.controller.applyContract(contractFor(prepared.snapshot, residentId, update.snapshot.state, update.snapshot.messages, update.snapshot.controls));
    }, { load: false });
    prepared.snapshot.subscriptions.push(subscription);
    await subscription.load();
    checkSignal(prepared.signal);
    return subscription;
  }
  function emit(prepared, cursor, eventType, payload, residentId = primary) {
    const isolated = prepared.snapshot.frame.contentWindow;
    if (eventType === "resident.state.changed") payload = { ...payload, controls: controlsFor(payload.state || {}) };
    isolated.dispatchEvent(new isolated.CustomEvent("portal:change", { detail: { resident_events: [{ resident_id: residentId, cursor, event_type: eventType, payload }] } }));
  }
  async function rejects(operation, pattern, message) {
    let failure = "";
    try { await operation(); } catch (error) { failure = error.message; }
    requireCondition(pattern.test(failure), message);
  }
  const actions = {
    async "lineage.intake-work"(prepared) {
      const subscription = await acquire(prepared);
      prepared.controller.setDraftText("本地草稿，不发送");
      prepared.controller.els.input.dispatchEvent(new Event("input", { bubbles: true }));
      for (const [cursor, intake, work, working] of [[1, "closed", "running", true], [2, "open", "queued", false], [3, "closed", "idle", false], [4, "open", "idle", false]]) {
        emit(prepared, cursor, "resident.state.changed", { state: { intake_state: intake, work_state: work, active_message_id: working ? "input-local" : "" } });
        const view = prepared.protocol.stateView(subscription.snapshot().state);
        requireCondition(view.intake_state === intake && view.working === working, "接收状态错误覆盖工作状态");
        requireCondition(prepared.controller.els.send.disabled === (intake === "closed"), "生产 Composer 未随接收状态更新");
        show(prepared, "协议事件后的生产控件", { intake, work, send_disabled: prepared.controller.els.send.disabled });
      }
      requireCondition(prepared.controller.draftText() === "本地草稿，不发送", "状态切换丢失本地草稿");
    },
    async "lineage.derived-isolation"(prepared) {
      const parent = await acquire(prepared);
      const child = await acquire(prepared, derived);
      emit(prepared, 1, "conversation.message.appended", { message: { message_id: "same-local-id", role: "assistant", content: "父 Resident 消息" } });
      emit(prepared, 1, "conversation.message.appended", { message: { message_id: "same-local-id", role: "assistant", content: "派生 Resident 消息" } }, derived);
      requireCondition(parent.snapshot().messages[0].content === "父 Resident 消息" && child.snapshot().messages[0].content === "派生 Resident 消息", "派生与父节点消息相互覆盖");
      child.acceptAcknowledgement({ resident_id: primary, state: { intake_state: "closed" } });
      requireCondition(child.snapshot().state.intake_state === "open", "错目标回执被派生节点接受");
      child.destroy();
      emit(prepared, 2, "conversation.message.updated", { message: { message_id: "same-local-id", role: "assistant", content: "父节点继续更新" } });
      requireCondition(parent.snapshot().messages[0].content === "父节点继续更新", "关闭派生订阅影响父节点");
      show(prepared, "统一 Resident Store 隔离", "父/派生相同消息ID互不覆盖；错目标回执被拒绝；销毁派生订阅不停止父节点更新");
    },
    async "lineage.queue-receipts"(prepared) {
      const subscription = await acquire(prepared);
      const message = { message_id: "queued-local", role: "human", content: "隔离排队输入" };
      subscription.acceptAcknowledgement({ resident_id: primary, cursor: 1, message, input_state: { message_id: message.message_id, state: "queued" }, queue: { resident_id: primary, items: [message] } });
      requireCondition(subscription.snapshot().messages.length === 0 && subscription.snapshot().queue.items.length === 1, "排队回执提前进入正文");
      show(prepared, "排队反馈", subscription.snapshot().queue);
      emit(prepared, 1, "resident.queue.changed", { queue: { resident_id: primary, items: [message] } });
      emit(prepared, 2, "conversation.message.appended", { message });
      emit(prepared, 3, "resident.queue.changed", { queue: { resident_id: primary, items: [] } });
      subscription.acceptAcknowledgement({ resident_id: primary, cursor: 2, message, queue: { resident_id: primary, items: [message] } });
      requireCondition(subscription.snapshot().messages.length === 1 && subscription.snapshot().queue.items.length === 0, "旧回执复活队列或重复正文");
      requireCondition(prepared.controller.messages().length === 1, "生产 Chat 未呈现唯一确认消息");
      show(prepared, "确认与过期回执", "消息只出现一次；已清空队列不会被旧回执恢复");
    },
    async "lineage.cursor-recovery"(prepared) {
      const subscription = await acquire(prepared);
      prepared.transport.events = (residentId, options) => {
        requireCondition(options.afterCursor === 0, "补齐未使用规范游标");
        return Promise.resolve({ resident_id: residentId, cursor: 2, events: [
          { resident_id: residentId, cursor: 2, event_type: "conversation.message.updated", payload: { message: { message_id: "cursor-local", role: "assistant", content: "补齐后的完整消息" } } },
          { resident_id: residentId, cursor: 1, event_type: "conversation.message.appended", payload: { message: { message_id: "cursor-local", role: "assistant", content: "消息初稿" } } },
        ] });
      };
      emit(prepared, 2, "conversation.message.updated", { message: { message_id: "cursor-local", content: "通知不能跳过游标" } });
      await until(() => subscription.snapshot().cursor === 2, prepared.signal, "事件缺口没有补齐");
      requireCondition(prepared.transport.eventReads === 1 && subscription.snapshot().messages[0].content === "补齐后的完整消息", "补齐事件未排序合并");
      emit(prepared, 1, "conversation.message.appended", { message: { message_id: "stale", content: "过期重复" } });
      requireCondition(subscription.snapshot().messages.length === 1, "过期事件污染正文");
      prepared.transport.events = () => Promise.resolve({ resident_id: derived, cursor: 4, events: [] });
      emit(prepared, 4, "resident.state.changed", { state: { intake_state: "closed" } });
      await until(() => prepared.errors.some(message => message.includes("target mismatch")), prepared.signal, "错目标恢复响应未报错");
      requireCondition(subscription.snapshot().cursor === 2, "错误恢复推进了规范游标");
      show(prepared, "游标补齐与失败", "缺口按顺序补齐；旧事件忽略；错目标响应报错且保留最后有效消息");
    },
    async "lineage.session-rebinding"(prepared) {
      const updates = [];
      const before = await prepared.projection.load(primary);
      const unsubscribe = prepared.projection.subscribe(primary, update => updates.push(update));
      prepared.snapshot.subscriptions.push({ destroy: unsubscribe });
      await until(() => updates.length > 0, prepared.signal, "身份订阅未交付初始投影");
      prepared.transport.projection = residentId => Promise.resolve({ resident_id: residentId, session_id: "session-after", title: window.PortalI18n.t("同一 Resident 新 Session"), workspace_id: "workspace_100000000001" });
      prepared.snapshot.frame.contentWindow.dispatchEvent(new prepared.snapshot.frame.contentWindow.CustomEvent("portal:change", { detail: { resident_changes: { [primary]: ["identity"] } } }));
      await until(() => updates.some(update => update.detail?.session_id === "session-after"), prepared.signal, "身份事件没有刷新会话绑定");
      requireCondition(prepared.projection.peek(primary).resident_id === before.resident_id, "会话更新改变 Resident ID");
      prepared.transport.projection = () => Promise.reject(new Error("隔离绑定读取失败"));
      await rejects(() => prepared.projection.load(primary, { force: true }), /隔离绑定读取失败/, "绑定读取失败被静默吞掉");
      requireCondition(prepared.projection.peek(primary).session_id === "session-after", "读取失败清除了有效绑定");
      show(prepared, "会话绑定刷新", "身份不变，订阅收到新Session；刷新失败保留最后有效投影，不执行真实重启");
    },
    async "lineage.identity-race"(prepared) {
      let release;
      prepared.transport.projection = () => new Promise(resolve => { release = resolve; });
      const oldRequest = prepared.projection.load(primary);
      const sharedRequest = prepared.projection.load(primary);
      requireCondition(prepared.transport.projectionReads === 1, "同目标并发读取未合并");
      prepared.projection.invalidate(primary);
      prepared.transport.projection = residentId => Promise.resolve({ resident_id: residentId, title: window.PortalI18n.t("新身份资料"), session_id: "current-session" });
      release({ resident_id: primary, title: window.PortalI18n.t("过期身份资料"), session_id: "old-session" });
      const values = await Promise.all([oldRequest, sharedRequest]);
      requireCondition(values.every(value => value.session_id === "current-session") && prepared.transport.projectionReads === 2, "失效后的旧响应被提交");
      values[0].title = window.PortalI18n.t("调用方局部修改");
      requireCondition(prepared.projection.peek(primary).title === "新身份资料", "调用方污染共享身份缓存");
      prepared.transport.projection = () => Promise.resolve({ resident_id: derived, title: window.PortalI18n.t("错目标资料") });
      await rejects(() => prepared.projection.load(primary, { force: true }), /target mismatch/, "错身份投影未拒绝");
      requireCondition(prepared.projection.peek(primary).resident_id === primary, "错目标取代稳定身份");
      show(prepared, "身份读取竞争", "并发共享一次读取；失效请求追到新代际；返回副本隔离；错目标被拒绝");
    },
    async "lineage.stop-receipt"(prepared) {
      prepared.transport.snapshots.set(primary, snapshotFor(primary, { work_state: "running", active_message_id: "old-input" }));
      const subscription = await acquire(prepared);
      subscription.acceptAcknowledgement({ resident_id: primary, cursor: 2, state: { intake_state: "open", work_state: "running", active_message_id: "new-input" } });
      emit(prepared, 1, "resident.state.changed", { state: { intake_state: "open", work_state: "running", active_message_id: "old-input" } });
      requireCondition(subscription.snapshot().state.active_message_id === "new-input", "旧事件将Stop目标回退到上一轮");
      prepared.transport.stop = payload => Promise.resolve({ resident_id: payload.resident_id, cursor: 3, state: { intake_state: "open", work_state: "stopping", active_message_id: payload.message_id } });
      await prepared.protocol.cancelInput({ resident_id: primary, message_id: subscription.snapshot().state.active_message_id });
      requireCondition(prepared.transport.stopRequests[0].message_id === "new-input" && subscription.snapshot().state.work_state === "stopping", "Stop回执未指向当前目标或没有反馈");
      prepared.transport.stop = () => Promise.reject(new Error("隔离Stop确认失败"));
      await rejects(() => prepared.protocol.cancelInput({ resident_id: primary, message_id: "new-input" }), /隔离Stop确认失败/, "Stop失败未向调用方报告");
      requireCondition(subscription.snapshot().state.work_state === "stopping", "失败回执改变了已确认的停止状态");
      show(prepared, "Stop确认与旧事件", "只在本地模拟传输：旧事件不回退目标；确认呈现stopping；失败保持有效状态");
    },
  };
  async function commit(step, prepared, externalSignal) {
    const snapshot = active;
    try {
      checkSignal(externalSignal);
      validateStep(step);
      requireCondition(snapshot && prepared === snapshot.prepared && prepared.capability === step?.capability, "步骤与准备结果不匹配");
      const signal = bindAbort(snapshot, externalSignal);
      prepared.signal = signal;
      checkSignal(signal);
      await actions[step.capability](prepared);
      checkSignal(signal);
      requireCondition(snapshot.writeAttempts === 0 && prepared.target.isConnected, "出现非预期写操作或目标失效");
      prepared.target.dataset.designLiveLineageVerified = step.capability;
      snapshot.prepared = null;
      return { target: prepared.target, caption: `${step.caption || step.capability}：生产协议及隔离组件断言全部通过；未运行真实 Provider。` };
    } catch (error) { restore(snapshot); throw error; }
  }
  function restore(snapshot) {
    if (!snapshot || snapshot.disposed) return;
    snapshot.disposed = true;
    const restoreFocus = snapshot.surface?.contains(document.activeElement) || document.activeElement === document.body;
    snapshot.aborter.abort();
    for (const [signal, listener] of snapshot.abortListeners) signal.removeEventListener("abort", listener);
    let failure = null;
    for (const subscription of snapshot.subscriptions) {
      try { subscription.destroy(); } catch (error) { failure ||= error; }
    }
    for (const controller of snapshot.controllers) {
      try { controller.destroy(); } catch (error) { failure ||= error; }
    }
    snapshot.surface?.remove();
    if (restoreFocus && snapshot.focus?.isConnected) snapshot.focus.focus({ preventScroll: true });
    if (active === snapshot) active = null;
    if (failure) throw failure;
  }
  global.PortalDesignLiveAdapters = global.PortalDesignLiveAdapters || {};
  global.PortalDesignLiveAdapters[adapterId] = Object.freeze({ id: adapterId, version: 1, capabilities, ready, capture, prepare, commit, restore });
}(window));
