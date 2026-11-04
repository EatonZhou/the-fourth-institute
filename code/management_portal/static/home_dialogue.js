(() => {
  "use strict";
  function createDialogue({ root, context }) {
    const node = (tag, text, cls) => { const el = document.createElement(tag); if (text) el.textContent = text; if (cls) el.className = cls; return el; };
    const state = window.PortalHomeDialogueState.create();
    // ResidentChatPanel creates a fresh submittedTarget for each send and passes
    // that same object to submissionContext and onAcknowledgement. Bind the
    // nonce to that submission, never to a Resident-wide FIFO or mutable target.
    const submissionRequests = new WeakMap();
    const messageActions = new Map();
    let destroyed = false, presented = true, suspended = false, chat = null, selection = null, assistant = null, lastDetail = null;
    let pageState = null, restoring = true, navigationReady = Promise.resolve();
    let narrow = false, ratio = 40, dragging = null;
    root.classList.add("homeDialogue"); root.dataset.pane = "conversation";
    const tabs = node("div", "", "homeDialogueTabs");
    const columns = node("div", "", "homeDialogueColumns"), left = node("section", "", "homeDialogueConversation"), right = node("section", "", "homeDialogueDisplay");
    const divider = node("div", "", "homeDialogueDivider");
    divider.tabIndex = 0; divider.setAttribute("role", "separator"); divider.setAttribute("aria-orientation", "vertical");
    divider.setAttribute("aria-label", "调整对话与展示宽度");
    divider.setAttribute("aria-valuemin", "25"); divider.setAttribute("aria-valuemax", "70");
    const chatRoot = node("div", "", "homeDialogueChat"), chatStatus = node("p", "正在读取个人门面…"), status = node("p", "选择资源或在对话中请求展示", "homeDialogueStatus");
    status.setAttribute("role", "status"); chatStatus.setAttribute("role", "status");
    const current = node("summary", "未选择展示", "homeDialogueCurrent");
    const identityMenu = node("details", "", "homeDialogueIdentity"), identityBody = node("div", "", "homeDialogueIdentityBody");
    identityMenu.append(current, identityBody);
    const contentActions = node("div", "", "homeDialogueContentActions");
    const history = node("select"), stage = node("div", "", "homeDialogueStage"); history.setAttribute("aria-label", "展示历史");
    const viewNames = { report: "汇报", map: "研究地图", frontier: "Lineage 线头", preview: "文件/资源" };
    const displayBar = node("div", "", "homeDialogueDisplayBar"), switchView = node("select");
    switchView.setAttribute("aria-label", "当前视图");
    const opener = node("details", "", "homeDialogueOpener"), openLabel = node("summary", "打开…");
    opener.append(openLabel);
    function button(parent, title, fn) { const el = node("button", title); el.type = "button"; el.addEventListener("click", fn); parent.append(el); return el; }
    button(tabs, "对话", () => setPane("conversation")); button(tabs, "展示", () => setPane("display"));
    const form = node("form", "", "homeDialogueChooser"), view = node("select"), kind = node("select"), target = node("input");
    view.setAttribute("aria-label", "展示类型"); kind.setAttribute("aria-label", "目标类型"); target.setAttribute("aria-label", "目标 ID 或文件资源地址");
    for (const [id, label] of [["report", "汇报"], ["map", "研究地图"], ["frontier", "Lineage 线头"], ["preview", "文件/资源"]]) view.add(new Option(label, id));
    for (const [id, label] of [["task", "任务"], ["lineage", "Lineage"], ["project", "Project"]]) kind.add(new Option(label, id));
    target.placeholder = "目标 ID 或资源地址"; target.required = true;
    form.append(view, kind, target); const openButton = node("button", "展示"); openButton.type = "submit"; form.append(openButton);
    view.onchange = () => { kind.hidden = view.value === "preview"; if (["map", "frontier"].includes(view.value)) kind.value = view.value === "map" ? "project" : "lineage"; };
    form.onsubmit = event => { event.preventDefault(); if (navigate({ view: view.value, target: view.value === "preview" ? { address: target.value } : { kind: kind.value, id: target.value } }, true)) opener.open = false; };
    opener.append(form);
    const tools = node("div", "", "homeDialogueToolbar"), more = node("details", "", "homeDialogueMore");
    more.append(node("summary", "更多"), tools);
    const viewLabel = node("label", "视图"), historyLabel = node("label", "历史");
    viewLabel.append(switchView); historyLabel.append(history); tools.append(viewLabel, historyLabel);
    function closeMore() { more.open = false; more.querySelector("summary").focus(); }
    const previous = button(tools, "返回上一展示", () => { closeMore(); back(true); });
    const pin = button(tools, "固定展示", () => { state.pin(!state.pinned); closeMore(); refresh(); });
    const expand = button(tools, "展开", () => {
      const expanded = root.classList.toggle("homeDialogueExpanded");
      expand.textContent = expanded ? "返回原布局" : "展开";
      closeMore(); resize(); persist();
    });
    const menus = [opener, more, identityMenu], menuGroup = `home-dialogue-${window.PortalUIPrimitives.randomUUID()}`;
    for (const menu of menus) {
      menu.name = menuGroup;
      menu.addEventListener("toggle", () => {
        if (menu.open) for (const other of menus) if (other !== menu) other.open = false;
      });
      menu.addEventListener("keydown", event => {
        if (event.key !== "Escape" || !menu.open) return;
        event.preventDefault(); event.stopPropagation(); menu.open = false; menu.querySelector("summary").focus();
      });
    }
    function dismissMenus(event) {
      for (const menu of menus) if (menu.open && !menu.contains(event.target)) menu.open = false;
    }
    document.addEventListener("pointerdown", dismissMenus, true);
    document.addEventListener("focusin", dismissMenus, true);
    switchView.onchange = () => {
      if (!state.active) return;
      const intent = { view: switchView.value, target: state.active.intent.target };
      const existing = state.entries.find(e => e.intent.view === intent.view && JSON.stringify(e.intent.target) === JSON.stringify(intent.target));
      if (existing) { select(existing, true); setPane("display"); } else navigate(intent, true);
    };
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
      more.open = false;
      setPane("conversation");
    }); cite.disabled = true;
    const closeDisplay = button(tools, "关闭当前展示", async () => {
      closeMore();
      const entry = state.active;
      if (!entry || (entry.adapter?.canClose && !await entry.adapter.canClose())) return;
      if (!entry.adapter?.canClose && entry.adapter?.snapshot?.().dirty && !await window.PortalUI.confirm({ title: "关闭有未保存修改的展示？", message: "关闭后将丢失尚未保存的修改。", confirmLabel: "关闭", tone: "danger" })) return;
      if (destroyed || entry !== state.active) return;
      state.manual(); entry.controller.abort(); entry.adapter?.destroy(); entry.root.remove();
      state.entries.splice(state.entries.indexOf(entry), 1); state.select(null, false);
      select(state.entries.at(-1) || null, true);
    });
    displayBar.append(identityMenu, contentActions, opener, more);
    left.append(chatStatus, chatRoot); right.append(displayBar, status, stage); columns.append(left, divider, right); root.append(tabs, columns);
    function setRatio(value) {
      ratio = Math.max(25, Math.min(70, value));
      root.style.setProperty("--home-dialogue-left", `${ratio}%`);
      divider.setAttribute("aria-valuenow", String(Math.round(ratio)));
      divider.setAttribute("aria-valuetext", `对话 ${Math.round(ratio)}%，展示 ${100 - Math.round(ratio)}%`);
      persist();
    }
    function resize() {
      if (destroyed) return;
      const navigation = document.querySelector(".portalSiteHeader");
      root.style.setProperty("--home-dialogue-top", `${Math.max(0, navigation?.getBoundingClientRect().bottom || 0) + 8}px`);
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
    const observer = new ResizeObserver(resize); observer.observe(root);
    const navigation = document.querySelector(".portalSiteHeader"); if (navigation) observer.observe(navigation);
    setRatio(ratio); resize();
    function setPane(name) { root.dataset.pane = name; present(); persist(); }
    function snapshot() {
      // Page State stores only navigation descriptors. Domain content, drafts,
      // credentials and submitted-request nonces never become restoration input.
      return { schemaVersion: 2, entries: state.entries.map(entry => ({ intent: entry.intent, queued: entry.queued,
        ...(entry.originKey ? { originKey: entry.originKey } : entry.messageOrigin
          ? { messageOrigin: window.PortalHomeDialogueState.normalizeMessageOrigin(entry.messageOrigin) } : {}) })),
        active: state.entries.indexOf(state.active), pinned: state.pinned, ratio,
        expanded: root.classList.contains("homeDialogueExpanded"), pane: root.dataset.pane };
    }
    function persist() { if (!restoring && !destroyed) pageState?.debounceSnapshot(); }
    async function restoreNavigation() {
      try {
        const identity = await context.currentUserReady;
        if (destroyed) return;
        const user = identity?.username || window.PortalIdentity?.username?.();
        if (!user || user === "anonymous" || !window.PortalPageState) return;
        // The owner is captured after current-user validation. Portal Page State
        // provides installation/user isolation and the current state transaction.
        pageState = window.PortalPageState.create("home-dialogue-display", { user: () => user, snapshot });
        const saved = pageState.read();
        if (![1, 2].includes(saved.schemaVersion) || !Array.isArray(saved.entries) || state.entries.length) return;
        root.classList.toggle("homeDialogueExpanded", saved.expanded === true);
        expand.textContent = saved.expanded === true ? "返回原布局" : "展开";
        root.dataset.pane = saved.pane === "display" ? "display" : "conversation";
        if (Number.isFinite(saved.ratio)) setRatio(saved.ratio);
        resize();
        let active = null;
        saved.entries.forEach((item, index) => {
          let intent;
          try { intent = window.PortalHomeDialogueState.normalizeIntent(item?.intent); } catch { return; }
          if (intent.action === "back") return;
          const entry = navigate(intent, false, false, { readOnly: true,
            originKey: window.PortalHomeDialogueState.normalizeOriginKey(item.originKey),
            messageOrigin: window.PortalHomeDialogueState.normalizeMessageOrigin(item.messageOrigin) });
          entry.queued = item.queued === true;
          if (index === saved.active) active = entry;
        });
        state.pin(saved.pinned === true);
        select(active, false);
      } finally { restoring = false; refresh(); }
    }
    function dirty() { return !!state.active?.adapter?.snapshot?.().dirty; }
    function refresh() {
      if (destroyed) return;
      pin.setAttribute("aria-pressed", String(state.pinned)); pin.textContent = state.pinned ? "取消固定" : "固定展示";
      current.textContent = state.active?.label || "展示";
      current.title = state.active?.label || "展示";
      current.setAttribute("aria-label", `展示详情：${current.title}`);
      identityBody.textContent = state.active ? `${current.title}\n${state.active.intent.target.id || state.active.intent.target.address}` : "选择资源或在对话中请求展示";
      switchView.replaceChildren();
      if (state.active) {
        const choices = ["lineage", "project"].includes(state.active.intent.target.kind) ? ["report", "map", "frontier"] : [state.active.intent.view];
        for (const id of choices) switchView.add(new Option(viewNames[id], id));
        switchView.value = state.active.intent.view;
      }
      viewLabel.hidden = !state.active || switchView.options.length < 2;
      historyLabel.hidden = !state.active;
      for (const entry of state.entries) if (entry.controls) {
        entry.controls.primary.hidden = entry.controls.secondary.hidden = state.active !== entry;
      }
      previous.disabled = state.entries.indexOf(state.active) <= 0;
      closeDisplay.disabled = !state.active;
      history.replaceChildren(new Option(`历史 (${state.entries.length})`, ""));
      for (const entry of state.entries) {
        const draft = entry.adapter?.snapshot?.().dirty ? " · 草稿" : "";
        history.add(new Option(`${viewNames[entry.intent.view]} · ${entry.label} · ${entry.status}${entry.queued ? " · 待查看" : ""}${draft}`, entry.id));
      }
      history.value = "";
      cite.disabled = !selection || !chat;
      if (!state.active) status.textContent = "选择资源或在对话中请求展示";
      status.hidden = !!state.active && !status.dataset.notice;
      persist();
    }
    function present() {
      chat?.setPresented(presented && !suspended && (!narrow || root.dataset.pane === "conversation"));
      for (const entry of state.entries) {
        const visible = presented && !suspended && state.active === entry && (!narrow || root.dataset.pane === "display");
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
              entry.status = "已读取"; void updateLabel(entry); refresh();
            }
          }).catch(error => {
            if (!destroyed && !entry.controller.signal.aborted && generation === entry.presentationGeneration) { entry.status = "读取失败"; entry.notice.textContent = error.message; entry.notice.hidden = false; entry.retry.hidden = false; refresh(); }
          });
        }
      }
    }
    function select(entry, manual) {
      state.select(entry, manual); selection = entry?.selection || null; delete status.dataset.notice;
      for (const menu of menus) menu.open = false;
      if (entry) entry.queued = false;
      present(); refresh();
    }
    function back(manual) {
      const index = state.entries.indexOf(state.active);
      if (index > 0) select(state.entries[index - 1], manual);
      else if (manual) state.manual();
    }
    history.onchange = () => select(state.entries.find(entry => entry.id === history.value) || state.active, true);
    async function updateLabel(entry) {
      if (!entry.presented || destroyed || entry.controller.signal.aborted) return;
      if (entry.intent.view === "preview") {
        entry.label = entry.adapter?.snapshot?.().payload?.title || entry.label; refresh(); return;
      }
      if (entry.intent.target.kind !== "lineage" || !window.PortalResourceChips?.loadReference || entry.labelPending) return;
      entry.labelPending = true;
      try {
        const reference = await window.PortalResourceChips.loadReference(entry.intent.target.id, { allowCached: false, signal: entry.controller.signal });
        if (!destroyed && !entry.controller.signal.aborted && entry.presented && reference?.title) { entry.label = reference.title; refresh(); }
      } catch { /* The adapter owns its access/error UI; an optional title cannot replace it. */ }
      finally { entry.labelPending = false; }
    }
    async function load(entry) {
      entry.status = "读取中"; refresh();
      try {
        if (entry.intent.view === "report" && entry.intent.target.kind !== "report") {
          if (!entry.originKey && entry.messageOrigin) entry.originKey = await window.PortalHomeDialogueState.messageOriginKey(entry.messageOrigin, entry.intent);
          await navigationReady;
          if (destroyed || entry.controller.signal.aborted) return;
          // Store a manual request identity before the adapter can POST. A new
          // document only resolves this binding; it never replays the write.
          pageState?.writeSnapshot();
        }
        if (!entry.adapter) {
          const api = entry.intent.view === "preview" ? window.PortalHomeDialoguePreview
            : entry.intent.view === "report" ? window.PortalHomeReportView : window.PortalHomeMapView;
          if (!api) throw new Error("展示组件尚未加载，请重试或刷新页面");
          if (entry.intent.view === "preview") {
            entry.controls = { primary: node("div", "", "homeDialoguePreviewActions"), secondary: node("div", "", "homeDialoguePreviewMenu") };
            entry.controls.primary.hidden = entry.controls.secondary.hidden = state.active !== entry;
            contentActions.append(entry.controls.primary); tools.append(entry.controls.secondary);
          }
          entry.adapter = api.create({ root: entry.content, controls: entry.controls,
            onOpen: intent => { if (presented && !suspended && state.active === entry) navigate(intent, true); },
            onReference: reference => {
              if (destroyed || entry.controller.signal.aborted || entry.intent.view !== "report") return;
              const fixed = window.PortalHomeDialogueState.normalizeIntent(reference);
              if (fixed.target?.kind !== "report" || !fixed.target.revision) return;
              const old = entry.intent.target;
              // A refresh opens a separate entry through onOpen. An already
              // fixed history reference can never silently advance its revision.
              if (old.kind === "report" && (old.id !== fixed.target.id || (old.revision && old.revision !== fixed.target.revision))) return;
              entry.intent = fixed; entry.readOnly = true;
              entry.label = reference.title || `汇报 · ${fixed.target.id} · v${fixed.target.revision}`;
              entry.status = entry.presented ? "已读取" : "待查看";
              entry.notice.hidden = entry.retry.hidden = true;
              pageState?.writeSnapshot(); refresh();
            },
            onSelection: value => {
              entry.selection = value;
              if (state.active === entry) { selection = value; if (value) state.manual(); refresh(); }
            }, onChange: refresh,
          });
        }
        entry.presented = presented && !suspended && state.active === entry && (!narrow || root.dataset.pane === "display");
        if (!entry.presented) await entry.adapter.setPresented(false);
        const result = await entry.adapter.show(entry.intent.target, { view: entry.intent.view, signal: entry.controller.signal, requestId: entry.id,
          ...(entry.intent.view === "report" ? { originKey: entry.originKey, readOnly: entry.readOnly } : {}) });
        if (destroyed || entry.controller.signal.aborted) return;
        if (result?.error) throw result.error;
        entry.status = entry.presented ? "已读取" : "待查看"; entry.notice.textContent = ""; entry.notice.hidden = true;
        void updateLabel(entry);
        entry.retry.hidden = true;
      } catch (error) {
        if (destroyed || entry.controller.signal.aborted) return;
        entry.status = "读取失败"; entry.notice.textContent = error.message || String(error); entry.notice.hidden = false; entry.retry.hidden = false;
      }
      refresh();
    }
    function navigate(value, manual = false, allowed = true, provenance = {}) {
      if (destroyed) return;
      let intent;
      try { intent = window.PortalHomeDialogueState.normalizeIntent(value); }
      catch (error) { status.dataset.notice = "true"; status.hidden = false; status.textContent = error.message; return; }
      if (intent.action === "back") {
        if (allowed) back(manual); else status.textContent = "返回请求已完成；当前展示已固定或手动切换，保持当前内容。";
        return;
      }
      // Independent roots retain drafts, video position and camera across history.
      const sourceReport = intent.view === "report" && intent.target.kind !== "report";
      const entry = { id: window.PortalUIPrimitives.randomUUID(), intent, queued: !allowed,
        originKey: window.PortalHomeDialogueState.normalizeOriginKey(provenance.originKey)
          || (sourceReport && !provenance.messageOrigin ? `home-request/v1:${window.PortalUIPrimitives.randomUUID()}` : null),
        messageOrigin: provenance.messageOrigin, readOnly: provenance.readOnly === true || intent.target.kind === "report",
        label: intent.target.kind === "project" ? intent.target.id : intent.target.kind === "lineage" ? "Lineage" : intent.target.kind === "task" ? "任务请求"
          : intent.target.kind === "report" ? `汇报 · ${intent.target.id}${intent.target.revision ? ` · v${intent.target.revision}` : ""}` : intent.target.address.split("/").filter(Boolean).at(-1),
        status: "等待读取", root: node("section", "", "homeDialogueEntry"), controller: new AbortController(),
        content: node("div", "", "homeDialogueEntryContent"), notice: node("p", "", "homeDialogueEntryNotice") };
      entry.notice.hidden = true; entry.root.append(entry.notice); entry.retry = button(entry.root, "重试读取", () => load(entry)); entry.retry.hidden = true; entry.root.append(entry.content);
      entry.root.hidden = true; stage.append(entry.root); state.add(entry);
      if (allowed) select(entry, manual);
      else { refresh(); }
      void load(entry);
      if (manual) setPane("display");
      return entry;
    }
    function consume(detail) {
      lastDetail = detail;
      if (!assistant || destroyed) return;
      const messageIds = new Set((detail?.snapshot?.messages || []).map(message => message.message_id || message.id));
      for (const id of messageActions.keys()) if (!messageIds.has(id)) messageActions.delete(id);
      for (const result of window.PortalHomeDialogueState.results(detail?.snapshot?.messages)) {
        try {
          const accepted = state.accept(result.value, { residentId: assistant.resident_id, messageId: result.messageId, dirty: dirty() });
          if (accepted) navigate(accepted.intent, false, accepted.present, { messageOrigin: {
            residentId: assistant.resident_id, messageId: result.messageId, fenceIndex: result.fenceIndex } });
        } catch (error) { status.dataset.notice = "true"; status.hidden = false; status.textContent = `展示指令未执行：${error.message}`; }
      }
    }
    // Rendering and automatic consumption share the Markdown fence structure
    // and command schema. A historical click is a new, explicit navigation;
    // it never acknowledges or re-admits the old request nonce.
    const conversationView = {
      renderMessageBody(item, rendering) {
        const id = item.message_id || item.id;
        if (!id || !["assistant", "agent"].includes(item.role)) return null;
        const previousActions = messageActions.get(id) || [], actions = [];
        let recognized = false, fenceIndex = 0;
        const html = rendering.renderContent(rendering.content, { renderCodeFence(block) {
          if (block.info !== "portal-home-display") return null;
          const index = fenceIndex++;
          recognized = true;
          if (!block.complete) return '<span class="homeDialogueMessageNotice" role="status">正在准备展示…</span>';
          const command = window.PortalHomeDialogueState.displayCommand(block);
          if (!command) return '<span class="homeDialogueMessageNotice" role="status">展示操作暂不可用：指令格式无效或当前版本不支持。</span>';
          const prior = previousActions[actions.length];
          const action = { token: prior?.source === block.source ? prior.token : window.PortalUIPrimitives.randomUUID(), source: block.source, intent: command.intent,
            messageOrigin: { residentId: assistant?.resident_id, messageId: id, fenceIndex: index } };
          actions.push(action);
          const intent = action.intent;
          const label = intent.action === "back" ? "返回上一展示" : intent.view === "preview"
            ? /(?:slides?|\.pptx?)(?:[/.?#]|$)/i.test(intent.target.address) ? "预览幻灯片" : "预览文件/资源"
            : ({ report: "查看汇报", map: "打开研究地图", frontier: "查看 Lineage 线头" })[intent.view];
          return `<button type="button" class="homeDialogueMessageAction btn" data-home-display-action="${window.MD.esc(action.token)}">${window.MD.esc(label)}</button>`;
        } });
        messageActions.set(id, actions);
        return recognized ? html : null;
      },
    };
    function openMessageAction(event) {
      const button = event.target.closest?.("button[data-home-display-action]");
      if (!button || !chatRoot.contains(button) || destroyed || !presented || suspended) return;
      const action = [...messageActions.values()].flat().find(value => value.token === button.dataset.homeDisplayAction);
      if (!action) return;
      event.preventDefault();
      navigate(action.intent, true, true, { messageOrigin: action.messageOrigin });
      setPane("display"); current.focus({ preventScroll: true });
    }
    chatRoot.addEventListener("click", openMessageAction);
    function submissionContext(submittedTarget) {
      if (!submittedTarget || submittedTarget.resident_id !== assistant?.resident_id) return null;
      const requestId = state.issue(assistant.resident_id, (lastDetail?.snapshot?.messages || []).map(m => m.message_id || m.id));
      submissionRequests.set(submittedTarget, requestId);
      const protocol = { protocol: "portal-home-display/v1", request_id: requestId, version: 1, action: "open", view: "report", target: { kind: "lineage", id: "<真实资源ID>" } };
      return { context_text: [
        "当前入口是 Home 对话与展示。用户明确要求视觉讲解、直观解释、图解、演示或可视化（例如‘直观解释注意力机制，Transformer的核心’），即使没有现成文件，也应作为右侧内容任务：生成可阅读的解释材料并打开。明确要求展示、生成汇报、切换、聚焦或返回也使用右侧。普通纯文本问答、只要文字或明确不创建文件时，不强制创建内容、不输出展示指令；不要仅因提到某个术语就生成文件。",
        "视觉解释的生成路径：按当前 Resident 身份调用 GetInstructionSchema 核对工具，用 GetResident 查询自己的已登记 Workspace/绑定；只在本次请求允许且自己有写权限的 Workspace 新建独立解释文件。可用 UpdateWorkspaceFile({address:真实workspace_ID/file/相对路径,content:文本}) 写入 Markdown，不猜 Workspace ID/路径、不覆盖无关文件、不借用用户或其他 Resident 身份。没有现成解释文件不是要求用户提供 Project/task 的理由；主题足够明确就制作，确实缺少必要范围时才询问。",
        "Preview 实际能力：优先制作 .md 图文解释页，使用分步示例、表格、公式和 Mermaid 图；可预览 PNG/SVG 等图片、PDF、MP4/WebM 视频与音频。图和媒体只能引用已生成并回读可用的文件/正式内容地址；视频适宜且现有工具可用时再生成，配字幕（可烧录）及独立文字讲解，失败仍保留可读图文。当前 Preview 的 HTML/JS 文件只按文本/代码展示，Markdown 原始 HTML 会转义，不运行任意脚本或生成页面的交互；需要交互时仅使用已支持的原生能力，否则明确说明并提供静态分步图解，不伪称交互已可用。",
        "写入成功后用 ReadResource 回读同一正式文件地址，核对内容/媒体及可读性；成功才在最终回复说明产物并输出 view:preview、action:open、target:{address:真实workspace_ID/file/相对路径} 的 portal-home-display 围栏，沿用下方本次 request_id。每次新解释使用独立持久文件地址以保留历史，不把临时下载凭据写进引用。若工具缺失、权限不足、写入/回读失败，不输出成功打开指令、不声称已经生成；明确失败原因，在对话提供可用的文字解释或已核实的图文入口。",
        "展示协议只导航已存在且可读的目标，不执行协议内 HTML/脚本、不授予写权限；实际生成通过上述文件工具完成，不能把页面正文、脚本或凭据塞进协议。概念解释走 preview，不为它伪造 Project、Lineage 或 task 来源。已有来源的状态汇报仍走 report：首次意图由报告适配器按稳定来源身份持久化，已有报告必须引用确定 report_ID+revision，显式刷新另建版本且保留旧引用。未知的已有资源目标先查询，无法确认才询问。",
        "示例结构：" + JSON.stringify(protocol),
        "action 允许 open/generate/focus/back；view 允许 report/map/frontier/preview；首次汇报/地图 target 为 {kind:task|lineage|project,id,focus?:节点ID}；已有汇报用 view:report 和 target:{kind:report,id:真实report_ID,revision:正整数,focus?:节点ID} 引用固定版本。task 的 id 必须匹配 /^resident_[a-f0-9]{12}\\/msg_[a-f0-9]{12,64}$/，来自 GetMessage/GetInputState 的一次真实请求，不能使用 task_ 或 MailboxTodo；preview 使用 {address:资源嵌套地址或文件链接}。back 无须 view/target。version 是本次请求内递增整数。",
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
          title: target.title || "个人门面", submissionContext, onDetail: consume, conversationView: () => conversationView,
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
      if (!presented || suspended || destroyed) return false;
      navigate({ view: "preview", target: { address: request.resourceAddress || request.path || request.href } }, true); return true;
    }, { priority: 80 });
    function lifecycle(event) { presented = event.detail.phase === "show"; if (!presented) { root.classList.remove("homeDialogueExpanded"); expand.textContent = "展开"; } resize(); }
    function beforeUnload(event) {
      if (state.entries.some(entry => entry.adapter?.snapshot?.().dirty)) { event.preventDefault(); event.returnValue = ""; }
    }
    function pageHide(event) {
      if (!event.persisted) return;
      pageState?.writeSnapshot(); suspended = true; present();
    }
    function pageShow(event) {
      if (!event.persisted || destroyed) return;
      suspended = false; resize();
    }
    root.addEventListener("portal:home-panel-lifecycle", lifecycle);
    window.addEventListener("beforeunload", beforeUnload);
    window.addEventListener("pagehide", pageHide); window.addEventListener("pageshow", pageShow);
    navigationReady = restoreNavigation().catch(() => {});
    void mountChat(); refresh();
    return { destroy() {
      pageState?.writeSnapshot(); pageState?.destroy();
      destroyed = true; unregister(); chat?.destroy();
      chatRoot.removeEventListener("click", openMessageAction); messageActions.clear();
      for (const entry of state.entries) { entry.controller.abort(); entry.adapter?.destroy(); }
      state.clear(); root.removeEventListener("portal:home-panel-lifecycle", lifecycle);
      observer.disconnect(); window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("pointerdown", dismissMenus, true); document.removeEventListener("focusin", dismissMenus, true);
      window.removeEventListener("pagehide", pageHide); window.removeEventListener("pageshow", pageShow);
      root.classList.remove("homeDialogueExpanded"); root.replaceChildren();
    } };
  }
  window.PortalHomeDialogue = Object.freeze({ connect: createDialogue });
})();
