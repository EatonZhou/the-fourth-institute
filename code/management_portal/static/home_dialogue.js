(() => {
  "use strict";
  function createDialogue({ root, context }) {
    const t = source => window.PortalI18n?.t(source) ?? source;
    const node = (tag, text, cls) => { const el = document.createElement(tag); if (text) el.textContent = text; if (cls) el.className = cls; if (["button", "input", "select", "summary"].includes(tag)) el.classList.add("homeDialogueControl"); return el; };
    const state = window.PortalHomeDialogueState.create();
    // ResidentChatPanel creates a fresh submittedTarget for each send and passes
    // that same object to submissionContext and onAcknowledgement. Bind the
    // nonce to that submission, never to a Resident-wide FIFO or mutable target.
    const submissionRequests = new WeakMap();
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
    function button(parent, title, fn) { const el = node("button", title); el.type = "button"; el.addEventListener("click", fn); parent.append(el); return el; }
    button(tabs, "对话", () => setPane("conversation")); button(tabs, "展示", () => setPane("display"));
    const previewRoot = node("div");
    const preview = window.PortalWorkspaceSurface.panelPackages.preview.create({
      root: previewRoot, id: `home-preview-${window.PortalUIPrimitives.randomUUID()}`, explorer: false,
      onPreviewRequest: request => navigate(request.displayIntent || {view:"preview", target:{address:request.resourceAddress || request.path || request.href}}, true),
    });
    previewRoot.classList.add("homeDialoguePreview");
    const stage = preview.surface.element("preview");
    stage.replaceChildren(status); stage.classList.add("homeDialogueStage");
    left.append(chatStatus, chatRoot); right.append(previewRoot); columns.append(left, divider, right); root.append(tabs, columns);
    const focusPreview = () => (preview.surface.element("tabs").querySelector('.is-active button') || preview.surface.element("toolbarState"))?.focus({preventScroll:true});
    function chooseResource() { preview.surface.element("toolbarState").click(); }
    function expandPreview() { root.classList.toggle("homeDialogueExpanded"); resize(); persist(); }
    async function closeEntry(entry) {
      if (!entry || (entry.adapter?.canClose && !await entry.adapter.canClose())) return;
      if (!entry.adapter?.canClose && entry.adapter?.snapshot?.().dirty && !await window.PortalUI.confirm({title:"关闭有未保存修改的展示？",message:"关闭后将丢失尚未保存的修改。",confirmLabel:"关闭",tone:"danger"})) return;
      if (destroyed || !state.entries.includes(entry)) return;
      const wasActive = state.active === entry;
      state.manual(); entry.controller.abort(); entry.adapter?.destroy(); entry.root.remove();
      state.entries.splice(state.entries.indexOf(entry), 1);
      if (wasActive) select(state.entries.at(-1) || null, true); else refresh();
    }
    const selectionToolbar = preview.surface.element("selectionToolbar");
    selectionToolbar.onclick = () => {
      if (!selection || !chat) return;
      const address = selection.address;
      chat.workspace.addReference({title:selection.label || address || "展示引用",
        ...(state.active?.intent.view === "preview" && address ? {path:address} : {}),
        text:state.active?.intent.view === "preview" ? selection.text || selection.label || address
          : `${selection.text || selection.label || ""}\n来源与目标：${JSON.stringify({target:selection.target || state.active?.intent.target,focus:selection.focus,address,version:selection.version,checked_at:selection.checked_at,sources:selection.sources || []})}`,
        source_message_ids:selection.target?.kind === "task" ? [selection.target.id.split("/")[1]] : []});
      setPane("conversation");
    };
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
        ...(entry.originKey ? { originKey: entry.originKey } : {}),
        ...(entry.messageOrigin ? { messageOrigin: window.PortalHomeDialogueState.normalizeMessageOrigin(entry.messageOrigin) } : {}) })),
        pinned: state.pinned, ratio,
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
        root.dataset.pane = "conversation";
        if (Number.isFinite(saved.ratio)) setRatio(saved.ratio);
        resize();
        saved.entries.forEach(item => {
          let intent;
          try { intent = window.PortalHomeDialogueState.normalizeIntent(item?.intent); } catch { return; }
          if (intent.action === "back") return;
          const entry = navigate(intent, false, false, { readOnly: true, deferred: true,
            originKey: window.PortalHomeDialogueState.normalizeOriginKey(item.originKey),
            messageOrigin: window.PortalHomeDialogueState.normalizeMessageOrigin(item.messageOrigin) });
          entry.queued = item.queued === true;
        });
        state.pin(saved.pinned === true);
        // Saved addresses are history, not a new request to open a file.
        // Create no domain adapter or reads until the user selects an entry.
        select(null, false);
      } finally { restoring = false; refresh(); }
    }
    function dirty() { return !!state.active?.adapter?.snapshot?.().dirty; }
    function refresh() {
      if (destroyed) return;
      const entry = state.active, target = entry?.intent.target;
      const path = target?.kind === "report" ? target.id + (target.revision ? `/revision/${target.revision}` : "") : target?.address || target?.id || "";
      const file = entry?.adapter?.toolbar?.() || {};
      preview.setPresentation({
        tabs: state.entries.map(item => ({key:item.id,label:item.label,title:`${item.label} · ${item.status}${item.queued ? " · 待查看" : ""}`,
          active:item === entry,dirty:item.adapter?.snapshot?.().dirty === true,pinned:item === entry && state.pinned})),
        path, document:file.document, download:file.download, pinned:state.pinned,
        onSelect: key => { select(state.entries.find(item => item.id === key), true); setPane("display"); },
        onClose: key => closeEntry(state.entries.find(item => item.id === key)),
        onPin: entry ? () => {state.pin(!state.pinned); refresh();} : null,
        actions:{
          visual:{active:true,disabled:true},
          open:{run:expandPreview,disabled:!entry},
          back:{run:() => back(true),disabled:state.entries.indexOf(entry) <= 0},
          refresh:{run:() => load(entry),disabled:!entry || entry.status === "读取中"},
          ...file.actions,
        },
      });
      selectionToolbar.hidden = !selection || !chat;
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
            if (result?.error && !entry.adapter.handlesReadErrors) throw result.error;
            if (entry.presented) {
              entry.status = result?.error ? "读取失败" : "已读取"; void updateLabel(entry); refresh();
            }
          }).catch(error => {
            if (!destroyed && !entry.controller.signal.aborted && generation === entry.presentationGeneration) { entry.status = "读取失败"; entry.notice.textContent = error.message; entry.notice.hidden = false; entry.retry.hidden = false; refresh(); }
          });
        }
      }
    }
    function select(entry, manual) {
      state.select(entry, manual); selection = entry?.selection || null; delete status.dataset.notice;
      if (entry) entry.queued = false;
      present(); refresh();
      if (entry?.deferred) { entry.deferred = false; void load(entry); }
    }
    function back(manual) {
      const index = state.entries.indexOf(state.active);
      if (index > 0) select(state.entries[index - 1], manual);
      else if (manual) state.manual();
    }
    async function updateLabel(entry) {
      if (!entry.presented || destroyed || entry.controller.signal.aborted) return;
      if (["preview", "explanation"].includes(entry.intent.view)) {
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
        if (entry.intent.view === "explanation") {
          const resolved = await window.PortalPreview.resolveDisplayIntent(entry.intent, {
            readOnly:entry.readOnly, signal:entry.controller.signal,
          });
          if (destroyed || entry.controller.signal.aborted) return;
          entry.intent = resolved.intent; entry.readOnly = true;
          entry.label = resolved.reference.title;
          pageState?.writeSnapshot(); refresh();
        }
        if (entry.intent.view === "report" && entry.intent.target.kind !== "report") {
          if (!entry.originKey && entry.messageOrigin) entry.originKey = await window.PortalHomeDialogueState.messageOriginKey(entry.messageOrigin, entry.intent);
          await navigationReady;
          if (destroyed || entry.controller.signal.aborted) return;
          // Store a manual request identity before the adapter can POST. A new
          // document only resolves this binding; it never replays the write.
          pageState?.writeSnapshot();
        }
        if (!entry.adapterPromise) {
          entry.adapterPromise = window.PortalPreview.createPresentation(entry.intent.view, { root: entry.content,
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
            }, onChoose: chooseResource, onClose: () => closeEntry(entry), onChange: snapshot => {
              if (["preview", "explanation"].includes(entry.intent.view) && snapshot?.accessStatus) {
                entry.notice.hidden = entry.retry.hidden = true;
                entry.notice.textContent = "";
                entry.status = { checking: "读取中", error: "读取失败", ready: "已读取", hidden: "待查看" }[snapshot.accessStatus];
                if (snapshot.accessStatus === "ready" && snapshot.payload?.title) entry.label = snapshot.payload.title;
              }
              refresh();
            },
          }).catch(error => {entry.adapterPromise = null; throw error;});
        }
        entry.adapter = await entry.adapterPromise;
        if (destroyed || entry.controller.signal.aborted) {entry.adapter?.destroy(); return;}
        entry.presented = presented && !suspended && state.active === entry && (!narrow || root.dataset.pane === "display");
        if (!entry.presented) await entry.adapter.setPresented(false);
        const messageOrigin = window.PortalHomeDialogueState.normalizeMessageOrigin(entry.messageOrigin);
        const result = await entry.adapter.show(entry.intent.target, { view: entry.intent.view, signal: entry.controller.signal, requestId: entry.id,
          ...(entry.intent.view === "report" ? { originKey: entry.originKey, readOnly: entry.readOnly, quality: entry.intent.quality,
            // A locator for server-side message verification, never a claimed
            // author or authorization. Keep it alongside the stable origin.
            ...(messageOrigin ? { originContext: { resident_id: messageOrigin.residentId,
              message_id: messageOrigin.messageId, fence_index: messageOrigin.fenceIndex } } : {}) } : {}) });
        if (destroyed || entry.controller.signal.aborted) return;
        if (result?.error && !entry.adapter.handlesReadErrors) throw result.error;
        entry.status = result?.error ? "读取失败" : entry.presented ? "已读取" : "待查看"; entry.notice.textContent = ""; entry.notice.hidden = true;
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
      const entry = { id: window.PortalUIPrimitives.randomUUID(), intent, queued: !allowed, deferred: provenance.deferred === true,
        originKey: window.PortalHomeDialogueState.normalizeOriginKey(provenance.originKey)
          || (sourceReport && !provenance.messageOrigin ? `home-request/v1:${window.PortalUIPrimitives.randomUUID()}` : null),
        messageOrigin: window.PortalHomeDialogueState.normalizeMessageOrigin(provenance.messageOrigin), readOnly: provenance.readOnly === true || intent.target.kind === "report",
        label: intent.target.kind === "project" ? intent.target.id : intent.target.kind === "lineage" ? "Lineage" : intent.target.kind === "task" ? "任务请求"
          : intent.target.kind === "report" ? `汇报 · ${intent.target.id}${intent.target.revision ? ` · v${intent.target.revision}` : ""}` : intent.target.address.split("/").filter(Boolean).at(-1),
        status: provenance.deferred ? "待打开" : "等待读取", root: node("section", "", "homeDialogueEntry"), controller: new AbortController(),
        content: node("div", "", "homeDialogueEntryContent"), notice: node("p", "", "homeDialogueEntryNotice") };
      entry.notice.hidden = true; entry.root.append(entry.notice); entry.retry = button(entry.root, "重试读取", () => load(entry)); entry.retry.hidden = true; entry.root.append(entry.content);
      entry.root.hidden = true; stage.append(entry.root); state.add(entry);
      if (allowed) select(entry, manual);
      else { refresh(); }
      if (!provenance.deferred) void load(entry);
      if (manual) setPane("display");
      return entry;
    }
    function consume(detail) {
      lastDetail = detail;
      if (!assistant || destroyed) return;
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
    async function submissionContext(submittedTarget) {
      if (!submittedTarget || submittedTarget.resident_id !== assistant?.resident_id) return null;
      const residentId = submittedTarget.resident_id;
      // Establish navigation order before asynchronous settings reads. A slow
      // earlier send cannot become the newest presentation request on return.
      const requestId = state.issue(residentId, (lastDetail?.snapshot?.messages || []).map(m => m.message_id || m.id));
      submissionRequests.set(submittedTarget, requestId);
      let reporting = null, reportingRead = "unavailable", settingsRevision = "", reportingError = "";
      try {
        const result = await window.PortalInstructionClient.execute("GetResidentSettings", { resident_id: residentId });
        const value = result.settings?.reporting;
        if (value && ["off", "low", "medium", "high"].includes(value.initiative) && ["low", "medium", "high"].includes(value.quality)) {
          reporting = { initiative: value.initiative, quality: value.quality }; reportingRead = "verified";
        } else if (result.settings && value === undefined) reportingError = "missing_reporting_settings";
        else reportingError = "invalid_reporting_settings";
        settingsRevision = result.settings_revision || "";
      } catch (error) {
        // Preserve failure without embedding raw server messages or credentials
        // into the conversation. Ordinary chat remains usable.
        reportingError = error?.status === 403 || error?.code === "no_permit" ? "no_permit" : "settings_unavailable";
      }
      if (destroyed || residentId !== assistant?.resident_id) return null;
      const protocol = { protocol: "portal-home-display/v1", request_id: requestId, version: 1, action: "open", view: "report", quality: reporting?.quality, target: { kind: "lineage", id: "<真实资源ID>" } };
      // Reporting policy is materialized by the Resident prompt owner. This
      // host contributes navigation context, never a competing policy prompt.
      return { reporting, reporting_read: reportingRead, settings_revision: settingsRevision,
        ...(reportingError ? { reporting_error: reportingError } : {}), context_text: [
        "当前入口是 Home 对话与展示。提交时读取的汇报设置快照：" + JSON.stringify(reporting) + "；读取状态：" + reportingRead + "。生成规则与质量要求由标准 Resident Prompt 注入提供；本入口不改变规则。",
        "展示协议只导航已存在且可读的目标，不授予权限。实际创作通过正式作者入口完成；解释包创作合同见当前 Portal 同源 /static/home_explanation_authoring.md。",
        "固定汇报引用使用真实 report_ID 和 revision；来源首次生成使用稳定来源身份，重开只读，不重生内容或检查。未知目标先查询，不能伪造资源ID。",
        "示例结构：" + JSON.stringify(protocol),
        "action 允许 open/generate/focus/back；view 允许 report/map/frontier/preview/explanation；首次汇报/地图 target 为 {kind:task|lineage|project,id,focus?:节点ID}；已有汇报用 view:report 和 target:{kind:report,id:真实report_ID,revision:正整数,focus?:节点ID} 引用固定版本。task 的 id 必须匹配 /^resident_[a-f0-9]{12}\\/msg_[a-f0-9]{12,64}$/，来自 GetMessage/GetInputState 的一次真实请求，不能使用 task_ 或 MailboxTodo；preview 使用 {address:资源嵌套地址或文件链接}；explanation 使用 {address:清单的正式Workspace文件地址,sha256:64位小写字节摘要}。back 无须 view/target。version 是本次请求内递增整数。",
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
          title: target.title || "个人门面", submissionContext, onDetail: consume, onDisplayAction: action => {
            if (destroyed || !presented || suspended) return false;
            navigate(action.intent, true, true, {messageOrigin: action.messageOrigin});
            setPane("display"); focusPreview(); return true;
          },
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
      navigate(request.displayIntent || { view: "preview", target: { address: request.resourceAddress || request.path || request.href } }, true, true, {messageOrigin:request.messageOrigin}); setPane("display"); return true;
    }, { priority: 80 });
    function lifecycle(event) { presented = event.detail.phase === "show"; if (!presented) root.classList.remove("homeDialogueExpanded"); resize(); }
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
      for (const entry of state.entries) { entry.controller.abort(); entry.adapter?.destroy(); }
      state.clear(); preview.destroy(); root.removeEventListener("portal:home-panel-lifecycle", lifecycle);
      observer.disconnect(); window.removeEventListener("beforeunload", beforeUnload);
      window.removeEventListener("pagehide", pageHide); window.removeEventListener("pageshow", pageShow);
      root.classList.remove("homeDialogueExpanded"); root.replaceChildren();
    } };
  }
  window.PortalHomeDialogue = Object.freeze({ connect: createDialogue });
})();
