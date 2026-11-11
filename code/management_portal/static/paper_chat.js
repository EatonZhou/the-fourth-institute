(() => {
  "use strict";

  if (window.PortalPaperChat) return;

  // The Paper Chat: its panel in the Workbench, the conversation of the
  // current Paper or collection, the context shown and sent with messages,
  // selections added to the draft, sending and stopping.
  const DEPENDENCIES = Object.freeze([
    "$", "alertError", "applyPaneCollapse", "collectionChatScope", "collectionItems", "collectionPaperIds",
    "collectionVisibleCount", "currentPaperWorkbench", "esc", "isCollectionMode", "md", "paperApi", "state",
  ]);

  function createChat(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperChat is missing: ${missing.join(", ")}`);
    const {
      $, alertError, applyPaneCollapse, collectionChatScope, collectionItems, collectionPaperIds,
      collectionVisibleCount, currentPaperWorkbench, esc, isCollectionMode, md, paperApi, state,
    } = dependencies;

    function chatMarkdown(text) {
      return window.PortalChatWorkspace.renderMarkdownWithAttachmentRefs(text, {
        renderMarkdown: md,
        previewUrlForPath: window.PortalFileLinks.imagePreviewUrl,
      });
    }

    let paperChatWorkspace = null;
    let paperChatPanel = null;

    function singlePaperId(target) {
      const match = /^paper:(paper_[0-9a-f]{12})$/.exec(target?.workspace_key || '');
      if (!match) return '';
      if (target.paper_id && target.paper_id !== match[1]) throw Error('论文讨论已变化，请重新发送');
      return match[1];
    }

    function paperChatDescriptor() {
      return {
        id: "chat",
        type: "chat",
        presentation: "chat",
        title: window.PortalI18n.t("Research Chat"),
        onShow: ({ panel }) => bindPaperChatPanel(panel?.runtime),
        options: {
          id: "paperResearchChat",
          autoLoad: false,

          draftOwner: () => state.username || "anonymous",
          title: window.PortalI18n.t("Research Chat"),

          placeholder: window.PortalI18n.t("Ask about architecture, related work, review concerns..."),
          meta: "Ctrl/⌘ + Enter 发送",
          resolveResident: target => target.workspace_key?.startsWith('research:')
            ? paperApi.request('/chat-workspace/api/prepare',{method:'POST',body:JSON.stringify({surface:'paper',workspace_key:target.workspace_key,message:'展示研究助手',read_only:true,context:{expected_principal:state.principalId}})})
            : paperApi.request(`/chat-workspace/api/session?surface=paper&workspace_key=${encodeURIComponent(target.workspace_key || "")}&paper_id=${encodeURIComponent(singlePaperId(target) || target.paper_id || "")}`),
          resolveBeforeSend: (target, payload) => {
            if(!researchReady || (!state.researchSlot && !state.researchLegacyKey)) throw Error(window.PortalI18n.t('请选择或新建持续研究助手'));
            return paperApi.request("/chat-workspace/api/prepare", {
            method: "POST", body: JSON.stringify({ surface: "paper", workspace_key: target.workspace_key,
              message: payload.text || payload.plainText, context: state.researchSlot ? researchPrepareContext() : singlePaperId(target) ? {paper_id:singlePaperId(target)} : { ...activeChatContext(), paper_id: target.paper_id } }),
          }); },
          submissionContext: async target => {
            if(!researchReady) throw Error(window.PortalI18n.t('研究助手暂不可用，请重试'));
            // Chat submits a Resident target; Paper identity belongs to its surface.
            if(state.researchSlot){
              const principal=state.principalId, key=activeChatWorkspaceId();
              const detail=await paperApi.request('/chat-workspace/api/prepare',{method:'POST',body:JSON.stringify({surface:'paper',workspace_key:key,message:'研究材料引用',context:researchPrepareContext()})});
              if(principal!==state.principalId || key!==activeChatWorkspaceId() || detail.resident_id!==target.resident_id) throw Error('研究助手或账号已变化');
              return detail.initial_context;
            }
            const surface = paperChatPanel?.surface?.();
            const paperId = singlePaperId(surface);
            if (!paperId) return null;
            const principal = state.principalId;
            const current = () => principal === state.principalId && surface.workspace_key === activeChatWorkspaceId()
              && paperChatPanel?.surface?.()?.workspace_key === surface.workspace_key
              && paperChatPanel?.target?.()?.resident_id === target.resident_id;
            if (!current()) throw Error('论文讨论已变化，请重新发送');
            const detail = await paperApi.request('/chat-workspace/api/prepare', {method:'POST',body:JSON.stringify({surface:'paper',workspace_key:surface.workspace_key,message:'单篇讨论上下文',context:{paper_id:paperId}})});
            if (!current() || detail.resident_id !== target.resident_id) throw Error('论文讨论已变化，请重新发送');
            return {context_text:detail.initial_context?.context_text||''};
          },
          initialContext: (_target, detail) => state.researchSlot ? detail.initial_context : ({ context_text: singlePaperId(paperChatPanel?.surface?.()) ? (detail.initial_context?.context_text || "") : `${detail.initial_context?.context_text || ""}\n${JSON.stringify(activeChatContext())}` }),

          onDetail: detail => {
            state.chatMetadata = detail;
            state.chatSession = detail?.session || null;
            state.chatSessions = state.chatSession ? [state.chatSession] : [];
            state.chat = Array.isArray(detail?.messages) ? detail.messages : [];
            state.chatHistoryLoadedFor = String(paperChatPanel?.surface?.()?.workspace_key || "");
            renderResearchOptions();
            renderAssistantContext();
            updateChatComposerState();
          },
          onError: alertError,
        },
      };
    }

    function bindPaperChatPanel(panel) {
      if (!panel || paperChatPanel === panel) return;
      paperChatPanel = panel;
      paperChatWorkspace = panel.workspace;
      mountResearchSelector();
      if(researchReady && state.researchSlot) void loadChatHistory().catch(alertError);
    }

    let researchNav = null, researchList = [], legacyList = [], researchSequence = 0;
    let researchReady = false, researchFailure = '';
    function mountResearchSelector() {
      const root = paperChatWorkspace?.root;
      if (!root || researchNav?.isConnected) return;
      researchNav = document.createElement('div');
      researchNav.className = 'paperResearchSelector';
      researchNav.setAttribute('aria-label', window.PortalI18n.t('持续研究助手'));
      window.addEventListener('portal:language-changed', renderResearchOptions);
      const select = document.createElement('select');
      select.className = 'portalControl';
      select.setAttribute('aria-label', window.PortalI18n.t('选择研究助手'));
      select.title = window.PortalI18n.t('选择研究助手');
      select.addEventListener('change', async () => {
        const principal = state.principalId;
        try {
          if (select.value.startsWith('legacy:')) {
            state.researchSlot = null;
            state.researchLegacyKey = select.value.slice(7);
            state.requestedChatWorkspaceId = state.researchLegacyKey;
          } else if (select.value) {
            const endpoint=researchList.find(s=>s.slot_id===select.value)?.state==='unbound'?'retry':'select';
            const value = await paperApi.request('/paper/api/research/chats/'+endpoint, {method:'POST',
              body:JSON.stringify({slot_id:select.value, expected_principal:principal})});
            if (principal !== state.principalId) return;
            state.researchSlot = value;
            state.researchLegacyKey = '';
            state.requestedChatWorkspaceId = '';
          } else return;
          window.dispatchEvent(new CustomEvent('portal:paper-research-selected', {detail:{principal, slot:state.researchSlot}}));
          await loadChatHistory();
        } catch (error) { renderResearchOptions(); alertError(error); }
      });
      const create = document.createElement('button');
      create.type='button'; create.className='portalButton is-compact is-quiet';
      create.textContent=window.PortalI18n.t('新建研究助手');
      create.title=window.PortalI18n.t('创建独立的持续研究会话');
      create.addEventListener('click', async () => {
        const title = window.prompt(window.PortalI18n.t('研究助手名称'));
        if (!title?.trim()) return;
        const principal=state.principalId;
        create.disabled=true;
        try {
          const slot = await paperApi.request('/paper/api/research/chats/create', {method:'POST',
            body:JSON.stringify({title:title.trim(), operation_id:crypto.randomUUID(), expected_principal:principal})});
          if (principal !== state.principalId) return;
          state.researchSlot=slot; state.researchLegacyKey=''; state.requestedChatWorkspaceId='';
          await refreshResearchSelectors();
          window.dispatchEvent(new CustomEvent('portal:paper-research-selected', {detail:{principal,slot}}));
          await loadChatHistory();
        } catch(error) {
          if(principal===state.principalId) {
            try { await refreshResearchSelectors(); } catch (_) { /* Keep the original failure visible. */ }
            alertError(error);
          }
        } finally { create.disabled=false; }
      });
      const error=document.createElement('div'); error.className='paperResearchSelectorError'; error.setAttribute('role','status');
      const retry=document.createElement('button'); retry.type='button';retry.className='portalButton is-compact is-quiet';retry.dataset.researchRetry='';
      retry.addEventListener('click',async()=>{retry.disabled=true;try{await refreshResearchSelectors();}catch(_){/* Local error remains visible. */}finally{retry.disabled=false;}});
      researchNav.append(select,create,error,retry);
      root.parentElement.insertBefore(researchNav,root);
      renderResearchOptions();
    }
    function renderResearchOptions() {
      if (!researchNav) return;
      const select=researchNav.querySelector('select');
      const t=window.PortalI18n.t;
      researchNav.setAttribute('aria-label', t('持续研究助手'));
      select.setAttribute('aria-label', t('选择研究助手')); select.title=t('选择研究助手');
      const create=researchNav.querySelector('button');
      const error=researchNav.querySelector('.paperResearchSelectorError');
      error.textContent=researchFailure ? t('研究助手暂不可用，请重试')+': '+researchFailure : ''; error.hidden=!researchFailure;
      const retry=researchNav.querySelector('[data-research-retry]'); retry.textContent=t('重试'); retry.hidden=!researchFailure;
      create.textContent=t('新建研究助手'); create.title=t('创建独立的持续研究会话');
      const placeholder=new Option(t('请选择或新建研究助手'),''); placeholder.disabled=true;
      select.replaceChildren(placeholder);
      for (const slot of researchList) {
        const option=new Option((slot.title || t('不可用研究助手'))+(slot.state==='unbound'?t('（重试创建）'):''),slot.slot_id);
        option.disabled=!['bound','unbound'].includes(slot.state); select.append(option);
      }
      if (legacyList.length) {
        const group=document.createElement('optgroup'); group.label=t('旧论文会话（历史保留）');
        for(const item of legacyList) group.append(new Option(item.title,'legacy:'+item.workspace_key));
        select.append(group);
      }
      const active=paperChatPanel?.surface?.()?.workspace_key;
      const selected=state.researchSlot?.slot_id || (state.researchLegacyKey?'legacy:'+state.researchLegacyKey:'');
      // A visible historical Chat must never look like no session is selected.
      const visibleLegacy=!selected && active && !active.startsWith('research:') ? 'legacy:'+active : '';
      const value=selected || visibleLegacy;
      if(value && !Array.from(select.options).some(option=>option.value===value)) {
        select.append(new Option(state.researchSlot?.title || t('当前历史会话'),value));
      }
      select.value=value;
    }
    async function refreshResearchSelectors() {
      const seq=++researchSequence, principal=state.principalId;
      researchReady=false;
      try {
        const value=await paperApi.request('/paper/api/research/chats', {timeoutMs:10000});
        if(seq!==researchSequence || principal!==state.principalId)return;
        if(value.principal!==principal)throw Error(window.PortalI18n.t('账号已变化，请重新载入研究助手'));
        researchList=value.slots||[]; legacyList=value.legacy||[];
        if(!state.researchLegacyKey) state.researchSlot=value.selected||null;
        researchReady=true;researchFailure='';renderResearchOptions();
      } catch(error) {
        if(seq===researchSequence && principal===state.principalId) {
          researchFailure=String(error.message || error);renderResearchOptions();
        }
        throw error;
      }
    }
    function researchPrepareContext() {
      const reader=state.detail?.reader_document;
      const document=reader && reader.paper_id===state.selectedId ? reader : undefined;
      return {expected_principal:state.principalId, ...(document?{document}:state.selectedId?{paper_id:state.selectedId}:{})};
    }
    async function loadMe() {
      const currentUser = await window.PortalUI.loadCurrentUser({ fallbackUsername: "" });
      if (!currentUser.ok) return;
      const changed = state.username !== (currentUser.data?.username || "");
      if(changed){state.researchSlot=null;state.researchLegacyKey='';state.researchIndexedPaper='';state.requestedChatWorkspaceId='';researchList=[];legacyList=[];researchSequence++;renderResearchOptions();}
      state.username = currentUser.data?.username || "";
      // This is the authenticated bootstrap's stable ID, not a display name.
      state.principalId = currentUser.data?.principal?.id || "";
      window.dispatchEvent(new CustomEvent('portal:paper-principal-changed'));
      paperChatWorkspace?.restoreDraft?.();
      // Identity completion must not depend on this optional surface catalog.
      // Failed research remains unsendable with its own visible retry control.
      const principal=state.principalId;
      void refreshResearchSelectors().then(() => {
        if(principal===state.principalId && researchReady && state.researchSlot && paperChatPanel) return loadChatHistory();
      }).catch(error => {
        if(principal!==state.principalId)return;
        researchReady=false;researchFailure=String(error.message||error);renderResearchOptions();
      });
    }

    function activeChatPaperId() {
      if(state.researchLegacyKey?.startsWith('paper:paper_')) return state.researchLegacyKey.slice(6);
      return isCollectionMode() ? "" : String(state.selectedId || "");
    }

    function activeChatWorkspaceId() {
      if(state.researchSlot) return 'research:'+state.researchSlot.slot_id;
      if (state.requestedChatWorkspaceId) return state.requestedChatWorkspaceId;
      return isCollectionMode() ? collectionChatScope().key : chatSessionKey(state.selectedId || "");
    }

    function activeChatContext() {
      if (state.requestedChatWorkspaceId) {
        return {
          paper_id: "",
          collection_mode: "history",
          collection_label: "Saved collection chat",
          collection_summary: "Previously saved Paper collection conversation",
          collection_query: "",
          collection_filters: {},
          semantic_search_id: "",
          collector_job_id: "",
          paper_ids: [],
          collection_items: [],
        };
      }
      if (!isCollectionMode()) return { paper_id: activeChatPaperId() };
      const scope = collectionChatScope();
      return {
        paper_id: "",
        collection_mode: scope.payload.mode,
        collection_label: scope.label,
        collection_summary: scope.summaryText,
        collection_query: scope.payload.query,
        collection_filters: scope.payload.filters,
        semantic_search_id: scope.payload.semantic_search_id,
        collector_job_id: scope.payload.collector_job_id,
        paper_ids: collectionPaperIds(),
        collection_items: collectionItems(),
      };
    }

    function chatSessionKey(paperId = state.selectedId) {
      const itemId = String(paperId || "").trim();
      return itemId ? `paper:${itemId}` : "library";
    }

    async function loadChatHistory({ paperId = activeChatPaperId(), workspaceId = activeChatWorkspaceId(), render = true } = {}) {
      if (paperId && paperId === state.deletingPaperId) return null;
      const key = workspaceId || chatSessionKey(paperId);
      state.chatHistoryLoading = true;
      try {
        if(state.researchSlot && paperChatPanel?.surface?.()?.workspace_key===key) {
          // Selection changes material references, not Chat identity or draft.
          const indexedPaper=state.selectedId, principal=state.principalId;
          if(indexedPaper && state.researchIndexedPaper!==indexedPaper) {
            await paperApi.request('/chat-workspace/api/prepare',{method:'POST',body:JSON.stringify({
              surface:'paper',workspace_key:key,message:'更新研究材料索引',context:researchPrepareContext()})});
            if(principal===state.principalId && key===activeChatWorkspaceId() && state.selectedId===indexedPaper) state.researchIndexedPaper=indexedPaper;
          }
          return state.chatMetadata;
        }
        return await paperChatPanel?.openSurface?.({
          surface: "paper",
          workspace_key: key,
          paper_id: String(paperId || ""),
        }, { force: true, render });
      } finally {
        if (key === activeChatWorkspaceId()) state.chatHistoryLoading = false;
      }
    }

    function renderAssistantContext() {
      if (!$("assistantContext")) return;
      const session = state.chatSession || {};
      const count = Number(session.count || state.chat.length || 0);
      const sessionLine = count
        ? `${count} saved turn${count === 1 ? "" : "s"} in this session.`
        : "No saved turns in this session yet.";
      if (isCollectionMode()) {
        const scope = collectionChatScope();
        const visible = collectionVisibleCount();
        $("assistantContext").innerHTML = window.PortalI18n.html`${esc(scope.label)} · ${esc(visible)} visible paper${Number(visible) === 1 ? "" : "s"}.<br>${esc(sessionLine)}`;
        return;
      }
      const paper = state.detail?.paper;
      $("assistantContext").innerHTML = paper
        ? window.PortalI18n.html`Selected paper: <strong>${esc(paper.title)}</strong><br>${esc(paper.venue || "")} ${esc(paper.year || "")}<br>${esc(sessionLine)}`
        : window.PortalI18n.html`Library chat across all papers.<br>${esc(sessionLine)}`;
    }

    function renderChat() {
      paperChatPanel?.updateControls?.();
    }

    function hasPendingChat() {
      return window.PortalResidentProtocol.stateView(paperChatPanel?.snapshot?.()?.state).working;
    }

    function paperChatHasUploadingAttachments() {
      return (paperChatPanel?.attachments?.() || []).some(item => item.uploading);
    }

    function updateChatComposerState() {
      paperChatPanel?.updateControls?.();
    }

    function addSelectionToChatDraft(text) {
      const selected = String(text || "").trim();
      if (!selected) return false;
      if (!currentPaperWorkbench()?.isPanelVisible("chat")) currentPaperWorkbench()?.showPanel("chat");
      applyPaneCollapse();
      const workspace = paperChatWorkspace || paperChatPanel?.workspace;
      if (!workspace?.setDraftText) return false;
      const currentDraft = String(workspace.draftText?.() || "").trimEnd();
      workspace.setDraftText(currentDraft ? `${currentDraft}\n\n${selected}` : selected);
      paperChatPanel?.focusComposer?.();
      return true;
    }

    async function submitChat(payload = null) {
      const data = payload || paperChatWorkspace?.payload?.() || {};
      const message = String(data.text || "").trim();
      if (!message || state.chatSendInFlight) return;
      await submitChatMessage(message, { fromComposer: true });
    }

    async function submitChatMessage(message, options = {}) {
      const text = String(message || "").trim();
      if (!text || state.chatSendInFlight) return;
      state.chatSendInFlight = true;
      updateChatComposerState();
      try {
        await paperChatPanel?.send?.({
          text,
          plainText: text,
          attachments: [],
          refs: window.PortalChatWorkspace.normalizeRefs({}),
        }, { clear: options.fromComposer === true });
      } catch (err) {
        alertError(err);
      } finally {
        state.chatSendInFlight = false;
        updateChatComposerState();
      }
    }

    async function stopPaperChat() {
      if (!hasPendingChat() || state.chatSendInFlight) return;
      state.chatSendInFlight = true;
      updateChatComposerState();
      try {
        await paperChatPanel?.stop?.();
      } finally {
        state.chatSendInFlight = false;
        updateChatComposerState();
      }
    }

    return Object.freeze({
      activeChatPaperId, activeChatWorkspaceId, addSelectionToChatDraft, loadChatHistory, loadMe,
      paperChatDescriptor, renderAssistantContext, renderChat, submitChatMessage, updateChatComposerState,
      currentPaperChatPanel: () => paperChatPanel, currentPaperChatWorkspace: () => paperChatWorkspace,
    });
  }

  window.PortalPaperChat = Object.freeze({ createChat, dependencies: DEPENDENCIES });
})();
