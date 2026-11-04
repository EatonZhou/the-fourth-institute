(() => {
  "use strict";
  function createDialogue({ root, context }) {
    const node = (tag, text, cls) => { const el = document.createElement(tag); if (text) el.textContent = text; if (cls) el.className = cls; return el; };
    const state = window.PortalHomeDialogueState.create();
    // ResidentChatPanel creates a fresh submittedTarget for each send and passes
    // that same object to submissionContext and onAcknowledgement. Bind the
    // nonce to that submission, never to a Resident-wide FIFO or mutable target.
    const submissionRequests = new WeakMap();
    let destroyed = false, presented = true, chat = null, selection = null, assistant = null, lastDetail = null;
    let narrow = false, ratio = 40, dragging = null;
    root.classList.add("homeDialogue"); root.dataset.pane = "conversation";
    const bar = node("header", "", "homeDialogueToolbar"), tabs = node("div", "", "homeDialogueTabs");
    const columns = node("div", "", "homeDialogueColumns"), left = node("section", "", "homeDialogueConversation"), right = node("section", "", "homeDialogueDisplay");
    const divider = node("div", "", "homeDialogueDivider");
    divider.tabIndex = 0; divider.setAttribute("role", "separator"); divider.setAttribute("aria-orientation", "vertical");
    divider.setAttribute("aria-label", "调整对话与展示宽度");
    divider.setAttribute("aria-valuemin", "25"); divider.setAttribute("aria-valuemax", "70");
    const chatRoot = node("div", "", "homeDialogueChat"), chatStatus = node("p", "正在读取个人门面…"), status = node("p", "选择资源或在对话中请求展示", "homeDialogueStatus");
    status.setAttribute("role", "status"); chatStatus.setAttribute("role", "status");
    const heading = node("strong", "对话与展示"), current = node("span", "未选择展示", "homeDialogueCurrent");
    const history = node("select"), stage = node("div", "", "homeDialogueStage"); history.setAttribute("aria-label", "展示历史");
    function button(parent, title, fn) { const el = node("button", title); el.type = "button"; el.addEventListener("click", fn); parent.append(el); return el; }
    bar.append(heading);
    button(bar, "返回上一展示", () => back(true));
    const pin = button(bar, "固定展示", () => { state.pin(!state.pinned); refresh(); });
    const expand = button(bar, "展开", () => { const expanded = root.classList.toggle("homeDialogueExpanded"); expand.textContent = expanded ? "返回原布局" : "展开"; resize(); });
    button(tabs, "对话", () => setPane("conversation")); button(tabs, "展示", () => setPane("display"));
    const form = node("form", "", "homeDialogueChooser"), view = node("select"), kind = node("select"), target = node("input");
    view.setAttribute("aria-label", "展示类型"); kind.setAttribute("aria-label", "目标类型"); target.setAttribute("aria-label", "目标 ID 或文件资源地址");
    for (const [id, label] of [["report", "汇报"], ["map", "研究地图"], ["frontier", "Lineage 线头"], ["preview", "文件/资源"]]) view.add(new Option(label, id));
    for (const [id, label] of [["task", "任务"], ["lineage", "Lineage"], ["project", "Project"]]) kind.add(new Option(label, id));
    target.placeholder = "目标 ID 或资源地址"; target.required = true;
    form.append(view, kind, target); const openButton = node("button", "展示"); openButton.type = "submit"; form.append(openButton);
    view.onchange = () => { kind.hidden = view.value === "preview"; if (["map", "frontier"].includes(view.value)) kind.value = view.value === "map" ? "project" : "lineage"; };
    form.onsubmit = event => { event.preventDefault(); navigate({ view: view.value, target: view.value === "preview" ? { address: target.value } : { kind: kind.value, id: target.value } }, true); };
    const tools = node("div", "", "homeDialogueToolbar"); tools.append(history);
    const cite = button(tools, "引用到对话", () => {
      if (!selection || !chat) return;
      if (state.active?.intent.view === "preview") {
        chat.workspace.addReference({ title: selection.label || selection.address, path: selection.address, text: selection.text || selection.label || selection.address });
      } else {
        // Domain selections are quoted context, not filesystem locators. Preserve
        // target, focus and verified sources even when no resource address exists.
        const domain = { target: selection.target || state.active?.intent.target, focus: selection.focus,
          address: selection.address, version: selection.version, checked_at: selection.checked_at, sources: selection.sources || [] };
        chat.workspace.addReference({ title: `展示引用 · ${selection.label || domain.target?.id || "当前对象"}`,
          text: `${selection.text || selection.label || ""}\n来源与目标：${JSON.stringify(domain)}`,
          source_message_ids: domain.target?.kind === "task" ? [domain.target.id.split("/")[1]] : [] });
      }
      setPane("conversation");
    }); cite.disabled = true;
    button(tools, "关闭当前展示", async () => {
      const entry = state.active;
      if (!entry || (entry.adapter?.canClose && !await entry.adapter.canClose())) return;
      if (!entry.adapter?.canClose && entry.adapter?.snapshot?.().dirty && !await window.PortalUI.confirm({ title: "关闭有未保存修改的展示？", message: "关闭后将丢失尚未保存的修改。", confirmLabel: "关闭", tone: "danger" })) return;
      if (destroyed || entry !== state.active) return;
      state.manual(); entry.controller.abort(); entry.adapter?.destroy(); entry.root.remove();
      state.entries.splice(state.entries.indexOf(entry), 1); state.select(null, false);
      select(state.entries.at(-1) || null, true);
    });
    left.append(chatStatus, chatRoot); right.append(current, form, tools, status, stage); columns.append(left, divider, right); root.append(bar, tabs, columns);
    function setRatio(value) {
      ratio = Math.max(25, Math.min(70, value));
      root.style.setProperty("--home-dialogue-left", `${ratio}%`);
      divider.setAttribute("aria-valuenow", String(Math.round(ratio)));
      divider.setAttribute("aria-valuetext", `对话 ${Math.round(ratio)}%，展示 ${100 - Math.round(ratio)}%`);
    }
    function resize() {
      if (destroyed) return;
      narrow = root.getBoundingClientRect().width < 760;
      root.dataset.narrow = String(narrow); divider.tabIndex = narrow ? -1 : 0;
      present();
    }
    divider.addEventListener("keydown", event => {
      const value = { ArrowLeft: ratio - 5, ArrowRight: ratio + 5, Home: 25, End: 70 }[event.key];
      if (value === undefined) return; event.preventDefault(); setRatio(value);
    });
    divider.addEventListener("pointerdown", event => { if (event.button !== 0 || narrow) return; dragging = event.pointerId; divider.setPointerCapture(event.pointerId); event.preventDefault(); });
    divider.addEventListener("pointermove", event => { if (dragging !== event.pointerId) return; const box = columns.getBoundingClientRect(); if (box.width) setRatio((event.clientX - box.left) / box.width * 100); });
    divider.addEventListener("pointerup", () => { dragging = null; });
    divider.addEventListener("lostpointercapture", () => { dragging = null; });
    const observer = new ResizeObserver(resize); observer.observe(root); setRatio(ratio); resize();
    function setPane(name) { root.dataset.pane = name; present(); }
    function dirty() { return !!state.active?.adapter?.snapshot?.().dirty; }
    function refresh() {
      if (destroyed) return;
      pin.setAttribute("aria-pressed", String(state.pinned)); pin.textContent = state.pinned ? "取消固定" : "固定展示";
      current.textContent = state.active ? `${state.active.label} · ${state.active.status}` : "未选择展示";
      history.replaceChildren(new Option("展示历史", ""));
      for (const entry of state.entries) {
        const draft = entry.adapter?.snapshot?.().dirty ? " · 草稿" : "";
        history.add(new Option(`${entry.label} · ${entry.status}${entry.queued ? " · 待查看" : ""}${draft}`, entry.id));
      }
      history.value = state.active?.id || "";
      cite.disabled = !selection || !chat;
    }
    function present() {
      chat?.setPresented(presented && (!narrow || root.dataset.pane === "conversation"));
      for (const entry of state.entries) {
        const visible = presented && state.active === entry && (!narrow || root.dataset.pane === "display");
        entry.root.hidden = state.active !== entry;
        if (entry.adapter && entry.presented !== visible) {
          entry.presented = visible;
          const generation = entry.presentationGeneration = (entry.presentationGeneration || 0) + 1;
          Promise.resolve().then(() => {
            if (destroyed || entry.controller.signal.aborted || generation !== entry.presentationGeneration) return;
            return entry.adapter.setPresented(visible);
          }).then(result => {
            if (destroyed || entry.controller.signal.aborted || generation !== entry.presentationGeneration) return;
            if (result?.error) throw result.error;
            if (entry.presented) {
              entry.status = "已读取"; refresh();
            }
          }).catch(error => {
            if (!destroyed && !entry.controller.signal.aborted && generation === entry.presentationGeneration) { entry.status = "读取失败"; entry.notice.textContent = error.message; entry.retry.hidden = false; refresh(); }
          });
        }
      }
    }
    function select(entry, manual) {
      state.select(entry, manual); selection = entry?.selection || null;
      if (entry) entry.queued = false;
      present(); refresh();
    }
    function back(manual) {
      const index = state.entries.indexOf(state.active);
      if (index > 0) select(state.entries[index - 1], manual);
      else if (manual) state.manual();
    }
    history.onchange = () => select(state.entries.find(entry => entry.id === history.value) || state.active, true);
    async function load(entry) {
      entry.status = "读取中"; refresh();
      try {
        if (!entry.adapter) {
          const api = entry.intent.view === "preview" ? window.PortalHomeDialoguePreview
            : entry.intent.view === "report" ? window.PortalHomeReportView : window.PortalHomeMapView;
          if (!api) throw new Error("展示组件尚未加载，请重试或刷新页面");
          entry.adapter = api.create({ root: entry.content,
            onOpen: intent => { if (presented && state.active === entry) navigate(intent, true); },
            onSelection: value => {
              entry.selection = value;
              if (state.active === entry) { selection = value; state.manual(); refresh(); }
            }, onChange: refresh,
          });
        }
        entry.presented = presented && state.active === entry && (!narrow || root.dataset.pane === "display");
        if (!entry.presented) await entry.adapter.setPresented(false);
        const result = await entry.adapter.show(entry.intent.target, { view: entry.intent.view, signal: entry.controller.signal, requestId: entry.id });
        if (destroyed || entry.controller.signal.aborted) return;
        if (result?.error) throw result.error;
        entry.status = entry.presented ? "已读取" : "待查看"; entry.notice.textContent = `本次展示 · ${new Date().toLocaleString()}`;
        entry.retry.hidden = true;
      } catch (error) {
        if (destroyed || entry.controller.signal.aborted) return;
        entry.status = "读取失败"; entry.notice.textContent = error.message || String(error); entry.retry.hidden = false;
      }
      refresh();
    }
    function navigate(value, manual = false, allowed = true) {
      if (destroyed) return;
      let intent;
      try { intent = window.PortalHomeDialogueState.normalizeIntent(value); }
      catch (error) { status.textContent = error.message; return; }
      if (intent.action === "back") {
        if (allowed) back(manual); else status.textContent = "返回请求已完成；当前展示已固定或手动切换，保持当前内容。";
        return;
      }
      // Independent roots retain drafts, video position and camera across history.
      const entry = { id: window.PortalUIPrimitives.randomUUID(), intent, queued: !allowed,
        label: `${({report:"汇报",map:"地图",frontier:"线头",preview:"预览"})[intent.view]} · ${intent.target.id || intent.target.address}`,
        status: "等待读取", root: node("section", "", "homeDialogueEntry"), controller: new AbortController(),
        content: node("div", "", "homeDialogueEntryContent"), notice: node("p", "", "homeDialogueEntryNotice") };
      entry.root.append(entry.notice); entry.retry = button(entry.root, "重试读取", () => load(entry)); entry.retry.hidden = true; entry.root.append(entry.content);
      entry.root.hidden = true; stage.append(entry.root); state.add(entry);
      if (allowed) select(entry, manual);
      else { refresh(); status.textContent = "新结果已加入展示历史，当前展示保持不变。"; }
      void load(entry);
      if (manual) setPane("display");
      return entry;
    }
    function consume(detail) {
      lastDetail = detail;
      if (!assistant || destroyed) return;
      for (const result of window.PortalHomeDialogueState.results(detail?.snapshot?.messages)) {
        try {
          const accepted = state.accept(result.value, { residentId: assistant.resident_id, messageId: result.messageId, dirty: dirty() });
          if (accepted) navigate(accepted.intent, false, accepted.present);
        } catch (error) { status.textContent = `展示指令未执行：${error.message}`; }
      }
    }
    function submissionContext(submittedTarget) {
      if (!submittedTarget || submittedTarget.resident_id !== assistant?.resident_id) return null;
      const requestId = state.issue(assistant.resident_id, (lastDetail?.snapshot?.messages || []).map(m => m.message_id || m.id));
      submissionRequests.set(submittedTarget, requestId);
      const protocol = { protocol: "portal-home-display/v1", request_id: requestId, version: 1, action: "open", view: "report", target: { kind: "lineage", id: "<真实资源ID>" } };
      return { context_text: [
        "当前入口是 Home 对话与展示。用户明确要求展示、生成汇报、切换、聚焦或返回时，可在最终回复中输出一个 portal-home-display JSON 代码围栏。",
        "此协议只导航现有真实来源，不生成 HTML/JS，不授权修改资源。先确定真实目标及其可读性，未知目标应询问用户。普通回答不输出指令。",
        "示例结构：" + JSON.stringify(protocol),
        "action 允许 open/generate/focus/back；view 允许 report/map/frontier/preview；target 为 {kind:task|lineage|project,id,focus?:节点ID}；task 的 id 必须匹配 /^resident_[a-f0-9]{12}\\/msg_[a-f0-9]{12,64}$/，来自 GetMessage/GetInputState 的一次真实请求，不能使用 task_ 或 MailboxTodo；preview 使用 {address:资源嵌套地址或文件链接}。back 无须 view/target。version 是本次请求内递增整数。",
        "宿主只接受本次 request_id，自动结果不会覆盖固定、草稿或用户手选展示。", 
        "当前展示：" + JSON.stringify(state.active?.intent || null), "当前选择：" + JSON.stringify(selection),
      ].join("\n") };
    }
    async function mountChat() {
      try {
        await context.currentUserReady;
        const target = await window.PortalNav.personalAssistantTarget();
        if (destroyed) return;
        if (!target) throw new Error("当前用户尚未配置个人门面，请先在个人设置中配置");
        assistant = target;
        chat = window.PortalChatWorkspace.panelPackage.create({ root: chatRoot, id: "home-dialogue-assistant", panelId: "home-dialogue-assistant", target,
          title: target.title || "个人门面", submissionContext, onDetail: consume,
          onAcknowledgement: (_result, submitted) => {
            const requestId = submissionRequests.get(submitted.target);
            if (state.acknowledge(requestId, submitted.target.resident_id)) {
              submissionRequests.delete(submitted.target); consume(lastDetail);
            }
          },
          onError: error => { chatStatus.textContent = error.message || String(error); chatStatus.hidden = false; },
        });
        chatStatus.hidden = true; present(); refresh();
      } catch (error) { if (!destroyed) { chatStatus.textContent = error.message || String(error); retryChat.hidden = false; } }
    }
    const retryChat = button(left, "重试个人门面", () => { retryChat.hidden = true; mountChat(); }); retryChat.hidden = true;
    const unregister = window.PortalPreview.registerHost("home-dialogue", request => {
      if (!presented || destroyed) return false;
      navigate({ view: "preview", target: { address: request.resourceAddress || request.path || request.href } }, true); return true;
    }, { priority: 80 });
    function lifecycle(event) { presented = event.detail.phase === "show"; if (!presented) { root.classList.remove("homeDialogueExpanded"); expand.textContent = "展开"; } resize(); }
    function beforeUnload(event) {
      if (state.entries.some(entry => entry.adapter?.snapshot?.().dirty)) { event.preventDefault(); event.returnValue = ""; }
    }
    root.addEventListener("portal:home-panel-lifecycle", lifecycle);
    window.addEventListener("beforeunload", beforeUnload);
    void mountChat(); refresh();
    return { destroy() {
      destroyed = true; unregister(); chat?.destroy();
      for (const entry of state.entries) { entry.controller.abort(); entry.adapter?.destroy(); }
      state.clear(); root.removeEventListener("portal:home-panel-lifecycle", lifecycle);
      observer.disconnect(); window.removeEventListener("beforeunload", beforeUnload);
      root.classList.remove("homeDialogueExpanded"); root.replaceChildren();
    } };
  }
  window.PortalHomeDialogue = Object.freeze({ connect: createDialogue });
})();
