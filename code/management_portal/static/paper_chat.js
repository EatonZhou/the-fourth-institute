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
      if (target.paper_id && target.paper_id !== match[1]) throw Error(window.PortalI18n.t('论文讨论已变化，请重新发送'));
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

          conversationView: () => ({ project: contract => ({ ...contract, rendering: {
            ...contract.rendering, empty_text: window.PortalI18n.t('还没有对话，在下方输入问题开始。'),
          } }) }),

          placeholder: window.PortalI18n.t("Ask about architecture, related work, review concerns..."),
          meta: "Ctrl/⌘ + Enter 发送",
          resolveResident: target => target.workspace_key?.startsWith('research:')
            ? paperApi.request('/chat-workspace/api/prepare',{method:'POST',body:JSON.stringify({surface:'paper',workspace_key:target.workspace_key,message:'展示研究助手',read_only:true,context:{expected_principal:state.principalId}})})
            : paperApi.request(`/chat-workspace/api/session?surface=paper&workspace_key=${encodeURIComponent(target.workspace_key || "")}&paper_id=${encodeURIComponent(singlePaperId(target))}`),
          resolveBeforeSend: (target, payload) => {
            if(!researchReady || researchSelecting || unavailableResearch(currentResearchKey()) || (!state.researchSlot && !state.researchLegacyKey)) throw Error(window.PortalI18n.t('请选择或新建持续研究助手'));
            return paperApi.request("/chat-workspace/api/prepare", {
            method: "POST", body: JSON.stringify({ surface: "paper", workspace_key: target.workspace_key,
              message: payload.text || payload.plainText, context: target.workspace_key?.startsWith('research:') ? researchPrepareContext() : singlePaperId(target) ? {paper_id:singlePaperId(target)} : { ...activeChatContext(), paper_id: "" } }),
          }); },
          submissionContext: async target => {
            if(!researchReady || researchSelecting || unavailableResearch(currentResearchKey())) throw Error(window.PortalI18n.t('研究助手暂不可用，请重试'));
            // Chat submits a Resident target; Paper identity belongs to its surface.
            if(state.researchSlot){
              const principal=state.principalId, key=activeChatWorkspaceId();
              const detail=await paperApi.request('/chat-workspace/api/prepare',{method:'POST',body:JSON.stringify({surface:'paper',workspace_key:key,message:'研究材料引用',context:researchPrepareContext()})});
              if(principal!==state.principalId || key!==activeChatWorkspaceId() || detail.resident_id!==target.resident_id) throw Error(window.PortalI18n.t('研究助手或账号已变化'));
              return detail.initial_context;
            }
            const surface = paperChatPanel?.surface?.();
            const paperId = singlePaperId(surface);
            if (!paperId) return null;
            const principal = state.principalId;
            const current = () => principal === state.principalId && surface.workspace_key === activeChatWorkspaceId()
              && paperChatPanel?.surface?.()?.workspace_key === surface.workspace_key
              && paperChatPanel?.target?.()?.resident_id === target.resident_id;
            if (!current()) throw Error(window.PortalI18n.t('论文讨论已变化，请重新发送'));
            const detail = await paperApi.request('/chat-workspace/api/prepare', {method:'POST',body:JSON.stringify({surface:'paper',workspace_key:surface.workspace_key,message:'单篇讨论上下文',context:{paper_id:paperId}})});
            if (!current() || detail.resident_id !== target.resident_id) throw Error(window.PortalI18n.t('论文讨论已变化，请重新发送'));
            return {context_text:detail.initial_context?.context_text||''};
          },
          initialContext: (_target, detail) => state.researchSlot ? detail.initial_context : ({ context_text: singlePaperId(paperChatPanel?.surface?.()) ? (detail.initial_context?.context_text || "") : `${detail.initial_context?.context_text || ""}\n${JSON.stringify(activeChatContext())}` }),

          onDetail: detail => {
            state.chatMetadata = detail;
            // Resident is the title owner, including changes made in shared Chat.
            const resident=detail?.resident;
            if(resident?.resident_id && resident.title){
              for(const item of [...researchList,...legacyList])if(item.resident_id===resident.resident_id)item.title=resident.title;
              if(state.researchSlot?.resident_id===resident.resident_id)state.researchSlot={...state.researchSlot,title:resident.title};
            }
            state.chatSession = detail?.session || null;
            state.chatSessions = state.chatSession ? [state.chatSession] : [];
            state.chat = Array.isArray(detail?.messages) ? detail.messages : [];
            state.chatHistoryLoadedFor = String(paperChatPanel?.surface?.()?.workspace_key || "");
            renderResearchOptions();
            renderAssistantContext();
            updateChatComposerState();
          },
          emit: event => { if(event==='binding-unavailable') void refreshResearchSelectors().catch(alertError); },
          onError: error => { researchFailure=String(error.message||error);renderResearchOptions();alertError(error); },
        },
      };
    }

    function bindPaperChatPanel(panel) {
      if (!panel || paperChatPanel === panel) return;
      paperChatPanel = panel;
      paperChatWorkspace = panel.workspace;
      observeChatLanguage();
      mountResearchSelector();
      if(researchReady && (state.researchSlot || state.researchLegacyKey)) void loadChatHistory().catch(alertError);
    }

    let chatLanguageObserver = null;
    let chatLanguageRefresh = null;
    function observeChatLanguage() {
      chatLanguageObserver?.disconnect();
      if (chatLanguageRefresh) window.removeEventListener("portal:language-changed", chatLanguageRefresh);
      const root = $("paperResearchChatTitle")?.closest(".portalChatWorkspaceRoot");
      if (!root) return;
      // These are server-authored Paper surface labels, not conversation text
      // or Resident titles. The shared Chat workspace and its state stay intact.
      const sources = ["Research Chat", "Library Chat", "Ask across the paper library...",
        "Compare or summarize the papers in this collection.", "Ask about this paper...",
        "Ask about this paper.", "围绕本篇论文提问", "围绕本篇论文提问。", "Ctrl/⌘ + Enter 发送"];
      const canonical = new Map(sources.flatMap(source => [source,
        ...Object.values(window.__PORTAL_TRANSLATIONS__?.[source] || {})].map(value => [value, source])));
      const refresh = () => {
        refreshResearchTitle();
        for (const element of root.querySelectorAll(".chatSubline .paneNote, .portalWorkspaceEmpty .portalAsyncStateMessage, .composerMeta, .chatTitleLine > .paneTitle")) {
          if (element.matches(".paneTitle") && state.researchSlot) continue;
          const source = canonical.get(element.textContent.trim());
          if (source && element.textContent !== window.PortalI18n.t(source)) element.textContent = window.PortalI18n.t(source);
        }
        for (const input of root.querySelectorAll("[data-placeholder], textarea[placeholder]")) {
          const attribute = input.hasAttribute("data-placeholder") ? "data-placeholder" : "placeholder";
          const source = canonical.get(input.getAttribute(attribute));
          if (source && input.getAttribute(attribute) !== window.PortalI18n.t(source)) input.setAttribute(attribute, window.PortalI18n.t(source));
        }
      };
      chatLanguageObserver = new MutationObserver(refresh);
      chatLanguageObserver.observe(root, { subtree: true, childList: true, characterData: true,
        attributes: true, attributeFilter: ["placeholder", "data-placeholder"] });
      refresh();
      chatLanguageRefresh = refresh;
      window.addEventListener("portal:language-changed", refresh);
    }

    let researchNav = null, researchTitleMenu = null, researchList = [], legacyList = [], researchSequence = 0;
    let researchReady = false, researchFailure = '', researchSelectionRevision = 0;
    const deletedResearchKeys = new Set();
    const pendingResearchDeletes = new Map();
    let researchSelecting = false;
    const researchRecents = new Map();
    const researchKey = item => item?.slot_id ? 'research:'+item.slot_id : item?.workspace_key || '';
    const currentResearchKey = () => state.researchSlot ? researchKey(state.researchSlot) : state.researchLegacyKey || '';
    function refreshResearchTitle() {
      if (!paperChatWorkspace) return;
      const selected = state.researchSlot || legacyList.find(item => researchKey(item) === currentResearchKey());
      const title = window.PortalI18n.t('Chat Workspace') + ' - ' + (selected?.title || window.PortalI18n.t('Research Chat'));
      if (paperChatWorkspace.els.title.textContent !== title) paperChatWorkspace.setTitle(title);
    }
    function unavailableResearch(key) {
      if (deletedResearchKeys.has(key)) return true;
      const item = researchList.find(item=>researchKey(item)===key) || legacyList.find(item=>researchKey(item)===key);
      return [...pendingResearchDeletes.values()].some(record=>record.principal===state.principalId && record.keys.includes(key))
        || Boolean(item && window.PortalResidentService?.deletionState?.(item.resident_id)?.deletion_pending);
    }
    function recentResearch(key) {
      // Only navigation metadata, partitioned by authenticated principal. A
      // stored key never grants access: candidates must be in the live catalog.
      const storageKey='portal.paper.research.recent:'+state.principalId;
      let values=researchRecents.get(state.principalId);
      if(!values){try{values=JSON.parse(localStorage.getItem(storageKey)||'[]');}catch(_){values=[];}}
      values=Array.isArray(values)?values.filter(item=>typeof item==='string').slice(0,32):[];
      if(key)values=[key,...values.filter(item=>item!==key)].slice(0,32);
      researchRecents.set(state.principalId,values);
      if(key){try{localStorage.setItem(storageKey,JSON.stringify(values));}catch(_){/* Session MRU still works. */}}
      return values;
    }
    function clearResearchTarget() {
      state.researchSlot=null;state.researchLegacyKey='';state.researchIndexedPaper='';
      state.requestedChatWorkspaceId='';state.chatMetadata=null;
      const principal=state.principalId, draft=paperChatWorkspace?.draftText?.(),attachments=paperChatWorkspace?.attachments?.();
      const closing=paperChatPanel?.closeSurface?.();
      void Promise.resolve(closing).then(()=>{
        if(principal===state.principalId && !currentResearchKey() && !paperChatPanel?.target?.()?.resident_id && draft && !paperChatWorkspace?.draftText?.())
          paperChatWorkspace?.setDraftText?.(draft,attachments);
      });
      window.dispatchEvent(new CustomEvent('portal:paper-research-selected',{detail:{principal,slot:null,workspace_key:''}}));
    }
    async function chooseResearch(key, {optimistic=false}={}) {
      if(!key || unavailableResearch(key))return;
      const revision=++researchSelectionRevision, principal=state.principalId;
      researchSelecting=true;
      if(optimistic){
        clearResearchTarget();
        state.researchSlot=researchList.find(item=>researchKey(item)===key)||null;
        state.researchLegacyKey=state.researchSlot?'':key;
        state.requestedChatWorkspaceId=state.researchLegacyKey;
        renderResearchOptions();
      }
      renderResearchOptions();
      try {
        const legacy=!key.startsWith('research:'), slotId=key.slice(9);
        const endpoint=!legacy&&researchList.find(item=>item.slot_id===slotId)?.state==='unbound'?'retry':'select';
        const value=await paperApi.request('/paper/api/research/chats/'+endpoint,{method:'POST',body:JSON.stringify({
          ...(legacy?{workspace_key:key}:{slot_id:slotId}),expected_principal:principal})});
        if(principal!==state.principalId || revision!==researchSelectionRevision || unavailableResearch(key))return;
        state.researchSlot=legacy?null:value;state.researchLegacyKey=legacy?key:'';
        state.requestedChatWorkspaceId=state.researchLegacyKey;researchSelecting=false;researchReady=true;researchFailure='';
        recentResearch(key);renderResearchOptions();
        window.dispatchEvent(new CustomEvent('portal:paper-research-selected',{detail:{principal,slot:state.researchSlot,workspace_key:state.researchLegacyKey}}));
        await loadChatHistory();
      } catch(error) {
        if(principal===state.principalId && revision===researchSelectionRevision){
          if(optimistic)clearResearchTarget();
          renderResearchOptions();alertError(error);
        }
      } finally {if(principal===state.principalId && revision===researchSelectionRevision){researchSelecting=false;renderResearchOptions();}}
    }
    window.addEventListener('portal:resident-deletion',event=>{
      const {resident_id:rid,phase}=event.detail||{};
      if(!rid)return;
      if(phase==='pending'){
        const entries=[...researchList,...legacyList].filter(item=>item.resident_id===rid);
        if(!entries.length)return;
        const keys=entries.map(researchKey), principal=state.principalId;
        pendingResearchDeletes.set(rid,{principal,keys,entries});
        ++researchSequence; // An old catalog must not restore its old selection.
        if(keys.includes(currentResearchKey())){
          ++researchSelectionRevision;
          const next=recentResearch().find(key=>!unavailableResearch(key)&&[...researchList,...legacyList].some(item=>researchKey(item)===key&&(!item.state||item.state==='bound')));
          if(next)void chooseResearch(next,{optimistic:true});
          else {clearResearchTarget();researchSelecting=false;paperChatWorkspace?.setStatus?.(window.PortalI18n.t('请选择或新建研究助手'));}
          researchNav?.querySelector('select')?.focus({preventScroll:true});
        }
      } else {
        const record=pendingResearchDeletes.get(rid);
        pendingResearchDeletes.delete(rid);
        if(record && record.principal!==state.principalId)return;
        const keys=record?.keys || [...researchList,...legacyList].filter(item=>item.resident_id===rid).map(researchKey);
        if(phase==='committed'){
          keys.forEach(key=>deletedResearchKeys.add(key));
          researchList=researchList.filter(item=>item.resident_id!==rid);
          legacyList=legacyList.filter(item=>item.resident_id!==rid);
          if(keys.includes(currentResearchKey())){++researchSelectionRevision;researchSelecting=false;clearResearchTarget();}
        } else if(phase==='failed' && record) {
          // Restore availability, not focus: the user may have continued work.
          for(const entry of record.entries){const list=entry.slot_id?researchList:legacyList;if(!list.some(item=>researchKey(item)===researchKey(entry)))list.push(entry);}
        }
      }
      renderResearchOptions();
    });
    window.addEventListener('portal:paper-research-refresh',()=>{void refreshResearchSelectors().catch(alertError);});
    window.addEventListener('focus',()=>{if(researchNav)void refreshResearchSelectors().catch(alertError);});
    function mountResearchSelector() {
      const root = paperChatWorkspace?.root;
      if (!root || researchNav?.isConnected) return;
      researchTitleMenu?.destroy();
      researchNav = document.createElement('div');
      researchNav.className = 'paperResearchSelector';
      window.removeEventListener('portal:language-changed', renderResearchOptions);
      window.addEventListener('portal:language-changed', renderResearchOptions);
      const createResearch = async () => {
        if (researchSelecting) return;
        const title = window.prompt(window.PortalI18n.t('研究助手名称'));
        if (!title?.trim()) return;
        const principal=state.principalId;
        researchSelecting=true;renderResearchOptions();
        try {
          const slot = await paperApi.request('/paper/api/research/chats/create', {method:'POST',
            body:JSON.stringify({title:title.trim(), operation_id:crypto.randomUUID(), expected_principal:principal})});
          if (principal !== state.principalId) return;
          state.researchSlot=slot; state.researchLegacyKey=''; state.requestedChatWorkspaceId='';
          recentResearch(researchKey(slot));
          await refreshResearchSelectors();
          window.dispatchEvent(new CustomEvent('portal:paper-research-selected', {detail:{principal,slot}}));
          await loadChatHistory();
        } catch(error) {
          if(principal===state.principalId) {
            try { await refreshResearchSelectors(); } catch (_) { /* Keep the original failure visible. */ }
            alertError(error);
          }
        } finally { if(principal===state.principalId){researchSelecting=false;renderResearchOptions();} }
      };
      const error=document.createElement('div'); error.className='paperResearchSelectorError'; error.setAttribute('role','status');
      const retry=document.createElement('button'); retry.type='button';retry.className='portalButton is-compact is-quiet';retry.dataset.researchRetry='';
      retry.addEventListener('click',async()=>{retry.disabled=true;try{await refreshResearchSelectors();if(state.researchSlot||state.researchLegacyKey)await loadChatHistory({retry:true});}catch(_){/* Local error remains visible. */}finally{retry.disabled=false;}});
      researchNav.append(error,retry);
      // Paper-only adapter: keep the public title node/ID and shared setTitle()
      // owner intact. No Chat Workspace source or other host opts into this.
      const titleElement=paperChatWorkspace.els.title;
      researchTitleMenu=window.PortalPaperResearchControls.createTitleMenu({titleElement,
        onSelect:key=>void chooseResearch(key),onCreate:()=>void createResearch()});
      root.parentElement.classList.add('paperResearchChatHost');
      root.parentElement.insertBefore(researchNav,root);
      renderResearchOptions();
    }
    function renderResearchOptions() {
      if (!researchNav) return;
      const t=window.PortalI18n.t;
      const error=researchNav.querySelector('.paperResearchSelectorError');
      error.textContent=researchFailure ? window.PortalPaperResearchControls.errorText() : ''; error.hidden=!researchFailure;
      const retry=researchNav.querySelector('[data-research-retry]'); retry.textContent=t('重试'); retry.hidden=!researchFailure;
      researchNav.hidden=!researchFailure;
      const items=[...researchList.filter(slot=>window.PortalPaperResearchControls.usable(slot)).map(slot=>({id:researchKey(slot),title:(slot.title||t('不可用研究助手'))+(slot.state==='unbound'?t('（重试创建）'):'')})),
        ...legacyList.map(item=>({id:researchKey(item),title:item.title}))].filter(item=>!unavailableResearch(item.id));
      researchTitleMenu?.update({items,selected:currentResearchKey(),pending:researchSelecting});
      refreshResearchTitle();
    }
    async function refreshResearchSelectors() {
      const seq=++researchSequence, principal=state.principalId, selectionRevision=researchSelectionRevision;
      researchReady=false;
      try {
        const value=await paperApi.request('/paper/api/research/chats', {timeoutMs:10000});
        if(seq!==researchSequence || principal!==state.principalId || selectionRevision!==researchSelectionRevision)return;
        if(value.principal!==principal)throw Error(window.PortalI18n.t('账号已变化，请重新载入研究助手'));
        const deleted=value.deleted||[];
        for(const item of deleted)deletedResearchKeys.add(item.workspace_key);
        const active=paperChatPanel?.surface?.()?.workspace_key;
        const current=state.researchSlot?'research:'+state.researchSlot.slot_id:state.researchLegacyKey;
        const lost=deletedResearchKeys.has(current)||(!current&&deletedResearchKeys.has(active));
        if(lost){
          researchSelectionRevision++; // Invalidate already in-flight select/rename responses.
          clearResearchTarget();
        }
        researchList=(value.slots||[]).filter(item=>!deletedResearchKeys.has('research:'+item.slot_id));
        legacyList=(value.legacy||[]).filter(item=>!deletedResearchKeys.has(item.workspace_key));
        const incomingKey=researchKey(value.selected)||researchKey(value.selected_legacy);
        if(!researchSelecting && !unavailableResearch(incomingKey)){
          state.researchSlot=value.selected&&!deletedResearchKeys.has('research:'+value.selected.slot_id)?value.selected:null;
          state.researchLegacyKey=value.selected_legacy&&!deletedResearchKeys.has(value.selected_legacy.workspace_key)?value.selected_legacy.workspace_key:'';
        }
        state.requestedChatWorkspaceId=state.researchLegacyKey;
        if(!researchSelecting && currentResearchKey())recentResearch(currentResearchKey());
        researchReady=true;researchFailure='';renderResearchOptions();
        if(lost)paperChatWorkspace?.setStatus?.(window.PortalI18n.t('请选择或新建研究助手'));
      } catch(error) {
        if(seq===researchSequence && principal===state.principalId && selectionRevision===researchSelectionRevision) {
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
      if(changed){deletedResearchKeys.clear();pendingResearchDeletes.clear();researchSelecting=false;state.researchSlot=null;state.researchLegacyKey='';state.researchIndexedPaper='';state.requestedChatWorkspaceId='';researchList=[];legacyList=[];researchSequence++;researchSelectionRevision++;renderResearchOptions();}
      state.username = currentUser.data?.username || "";
      // This is the authenticated bootstrap's stable ID, not a display name.
      state.principalId = currentUser.data?.principal?.id || "";
      window.dispatchEvent(new CustomEvent('portal:paper-principal-changed'));
      paperChatWorkspace?.restoreDraft?.();
      // Identity completion must not depend on this optional surface catalog.
      // Failed research remains unsendable with its own visible retry control.
      const principal=state.principalId;
      void refreshResearchSelectors().then(() => {
        if(principal===state.principalId && researchReady && (state.researchSlot||state.researchLegacyKey) && paperChatPanel) return loadChatHistory();
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
      if(state.researchLegacyKey) return state.researchLegacyKey;
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

    async function loadChatHistory({ paperId = activeChatPaperId(), workspaceId = activeChatWorkspaceId(), render = true, retry = false } = {}) {
      if (paperId && paperId === state.deletingPaperId) return null;
      const key = workspaceId || chatSessionKey(paperId);
      if(unavailableResearch(key))return null;
      state.chatHistoryLoading = true;
      try {
        if(!retry && (state.researchSlot || state.researchLegacyKey) && paperChatPanel?.surface?.()?.workspace_key===key) {
          // Selection changes material references, not Chat identity or draft.
          const indexedPaper=state.selectedId, principal=state.principalId;
          if(state.researchSlot && indexedPaper && state.researchIndexedPaper!==indexedPaper) {
            await paperApi.request('/chat-workspace/api/prepare',{method:'POST',body:JSON.stringify({
              surface:'paper',workspace_key:key,message:'更新研究材料索引',context:researchPrepareContext()})});
            if(principal===state.principalId && key===activeChatWorkspaceId() && state.selectedId===indexedPaper) state.researchIndexedPaper=indexedPaper;
          }
          return state.chatMetadata;
        }
        return await paperChatPanel?.openSurface?.({
          surface: "paper",
          workspace_key: key,
          paper_id: singlePaperId({workspace_key:key}),
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
        : window.PortalI18n.t("No saved turns in this session yet.");
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

    let releaseChatMount = null;
    function mountChat(container) {
      releaseChatMount?.();
      if (!currentPaperWorkbench()?.isPanelVisible('chat')) currentPaperWorkbench()?.showPanel('chat');
      const root = paperChatWorkspace?.els.title?.closest('.portalChatWorkspaceRoot');
      if (!root || !container?.isConnected) throw Error(window.PortalI18n.t('研究助手暂不可用，请重试'));
      const host = root.parentElement;
      container.classList.add('paperResearchChatHost');
      // Preserve inherited Paper typography when borrowing the same nodes into
      // the Reader dialog, whose PDF chrome has a different base font.
      container.style.font = getComputedStyle(host).font;
      const menu = document.getElementById(paperChatWorkspace.els.title.getAttribute('aria-controls'));
      if(menu){menu.hidden=true;paperChatWorkspace.els.title.setAttribute('aria-expanded','false');}
      const nodes = [researchNav, root, menu].filter(Boolean);
      const homes = nodes.map(node => { const marker=document.createComment('paper-chat-home');node.before(marker);return {node,marker}; });
      for (const node of nodes) (node===menu ? container.closest('dialog')||container : container).append(node);
      let released=false;
      const release=()=>{
        if(released)return;released=true;
        for(const {node,marker} of homes){if(marker.parentNode){marker.replaceWith(node);}else node.remove();}
        if(menu){menu.hidden=true;paperChatWorkspace.els.title.setAttribute('aria-expanded','false');}
        if(releaseChatMount===release)releaseChatMount=null;
      };
      releaseChatMount=release;
      return release;
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
      mountChat, selectionAnswers: () => (state.chat || []).filter(message => window.PortalChatWorkspace.messageRole(message)==='agent').map(message => Array.isArray(message.content_parts) ? message.content_parts.filter(part=>part.type==='text').map(part=>part.text||'').join('\n') : String(message.content||'')).filter(Boolean),
    });
  }

  window.PortalPaperChat = Object.freeze({ createChat, dependencies: DEPENDENCIES });
})();
