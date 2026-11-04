(() => {
  "use strict";
  function createDialogue({ root, context }) {
    const node = (tag, text, cls) => { const el = document.createElement(tag); if (text) el.textContent = text; if (cls) el.className = cls; return el; };
    const state = window.PortalHomeDialogueState.create();
    let destroyed = false, presented = true, chat = null, selection = null, assistant = null, lastDetail = null;
    root.classList.add("homeDialogue"); root.dataset.pane = "conversation";
    const bar = node("header", "", "homeDialogueToolbar"), tabs = node("div", "", "homeDialogueTabs");
    const columns = node("div", "", "homeDialogueColumns"), left = node("section", "", "homeDialogueConversation"), right = node("section", "", "homeDialogueDisplay");
    const chatRoot = node("div", "", "homeDialogueChat"), chatStatus = node("p", "正在读取个人门面…"), status = node("p", "选择资源或在对话中请求展示", "homeDialogueStatus");
    status.setAttribute("role", "status"); chatStatus.setAttribute("role", "status");
    const heading = node("strong", "对话与展示"), current = node("span", "未选择展示", "homeDialogueCurrent");
    const history = node("select"), stage = node("div", "", "homeDialogueStage"); history.setAttribute("aria-label", "展示历史");
    function button(parent, title, fn) { const el = node("button", title); el.type = "button"; el.addEventListener("click", fn); parent.append(el); return el; }
    bar.append(heading);
    button(bar, "返回上一展示", () => back(true));
    const pin = button(bar, "固定展示", () => { state.pin(!state.pinned); refresh(); });
    const expand = button(bar, "展开", () => { const expanded = root.classList.toggle("homeDialogueExpanded"); expand.textContent = expanded ? "返回原布局" : "展开"; });
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
      const text = selection.text || selection.label || selection.address;
      chat.workspace.addReference({ id: `home-reference:${window.PortalUIPrimitives.randomUUID()}`, kind: "file", name: selection.label || selection.address,
        path: selection.address, content_type: "text/x-portal-preview-reference", reference_text: text,
        preview_selection: { path: selection.address, title: selection.label, text } });
      setPane("conversation");
    }); cite.disabled = true;
    button(tools, "关闭当前展示", async () => {
      const entry = state.active;
      if (!entry || (entry.adapter?.canClose && !await entry.adapter.canClose())) return;
      if (destroyed || entry !== state.active) return;
      state.manual(); entry.controller.abort(); entry.adapter?.destroy(); entry.root.remove();
      state.entries.splice(state.entries.indexOf(entry), 1); state.select(null, false);
      select(state.entries.at(-1) || null, true);
    });
    left.append(chatStatus, chatRoot); right.append(current, form, tools, status, stage); columns.append(left, right); root.append(bar, tabs, columns);
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
      const narrow = window.matchMedia("(max-width: 760px)").matches;
      chat?.setPresented(presented && (!narrow || root.dataset.pane === "conversation"));
      for (const entry of state.entries) {
        const visible = presented && state.active === entry && (!narrow || root.dataset.pane === "display");
        entry.root.hidden = state.active !== entry;
        if (entry.adapter && entry.presented !== visible) {
          entry.presented = visible;
          Promise.resolve(entry.adapter.setPresented(visible)).then(() => {
            if (!destroyed && !entry.controller.signal.aborted && entry.presented) {
              entry.status = "已读取"; refresh();
            }
          }).catch(error => {
            if (!destroyed && !entry.controller.signal.aborted) { entry.status = "读取失败"; entry.notice.textContent = error.message; entry.retry.hidden = false; refresh(); }
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
        entry.presented = presented && state.active === entry && (!window.matchMedia("(max-width: 760px)").matches || root.dataset.pane === "display");
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
    function submissionContext() {
      const requestId = state.issue(assistant.resident_id, (lastDetail?.snapshot?.messages || []).map(m => m.message_id || m.id));
      const protocol = { protocol: "portal-home-display/v1", request_id: requestId, version: 1, action: "open", view: "report", target: { kind: "lineage", id: "<真实资源ID>" } };
      return { context_text: [
        "当前入口是 Home 对话与展示。用户明确要求展示、生成汇报、切换、聚焦或返回时，可在最终回复中输出一个 portal-home-display JSON 代码围栏。",
        "此协议只导航现有真实来源，不生成 HTML/JS，不授权修改资源。先确定真实目标及其可读性，未知目标应询问用户。普通回答不输出指令。",
        "示例结构：" + JSON.stringify(protocol),
        "action 允许 open/generate/focus/back；view 允许 report/map/frontier/preview；target 为 {kind:task|lineage|project,id,focus?:节点ID}；task 的 id 必须是 resident_ID/msg_ID（一次真实请求）；preview 使用 {address:资源嵌套地址或文件链接}。back 无须 view/target。version 是本次请求内递增整数。",
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
          onAcknowledgement: (_result, submitted) => { state.acknowledge(submitted.target.resident_id); consume(lastDetail); },
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
    function lifecycle(event) { presented = event.detail.phase === "show"; if (!presented) root.classList.remove("homeDialogueExpanded"); present(); }
    function beforeUnload(event) {
      if (state.entries.some(entry => entry.adapter?.snapshot?.().dirty)) { event.preventDefault(); event.returnValue = ""; }
    }
    root.addEventListener("portal:home-panel-lifecycle", lifecycle);
    window.addEventListener("resize", present); window.addEventListener("beforeunload", beforeUnload);
    void mountChat(); refresh();
    return { destroy() {
      destroyed = true; unregister(); chat?.destroy();
      for (const entry of state.entries) { entry.controller.abort(); entry.adapter?.destroy(); }
      state.clear(); root.removeEventListener("portal:home-panel-lifecycle", lifecycle);
      window.removeEventListener("resize", present); window.removeEventListener("beforeunload", beforeUnload);
      root.classList.remove("homeDialogueExpanded"); root.replaceChildren();
    } };
  }
  window.PortalHomeDialogue = Object.freeze({ connect: createDialogue });
})();
