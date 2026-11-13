(() => {
  'use strict';
  const DEPENDENCIES = Object.freeze([
    "state", "request", "activeDocument", "editorValue", "openArticleFile", "saveAllDirtyDocuments", "showToast",
  ]);
  // Resolve quoted source conservatively. Ambiguous/deleted text stays detached.
  function locate(source, anchor) {
    const { quote, prefix, suffix } = anchor;
    if (!quote) return null;
    const hits = [];
    for (let at = source.indexOf(quote); at >= 0; at = source.indexOf(quote, at + 1)) {
      hits.push(at);
      if (hits.length > 1000) return null;
    }
    const contextual = hits.filter(at => source.slice(0, at).endsWith(prefix) && source.slice(at + quote.length).startsWith(suffix));
    const at = contextual.length === 1 ? contextual[0] : hits.length === 1 ? hits[0] : -1;
    return at < 0 ? null : { start: at, end: at + quote.length };
  }

  function createComments({ state, request, activeDocument, editorValue, openArticleFile, saveAllDirtyDocuments, showToast }) {
    const $ = id => document.getElementById(id);
    const panel = $('articleComments'), list = $('articleCommentList'), form = $('articleCommentForm');
    const input = $('articleCommentText'), quoteBox = $('articleCommentQuote'), status = $('articleCommentStatus');
    let articleId = '', threads = [], actor = '', writable = false, generation = 0, pending = null;
    let busy = false, loading = false, timer = null, model = null, decorationIds = [], changedTimer = null;
    const node = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
    const button = (text, action) => { const e = node('button', text, 'portalButton is-quiet'); e.type = 'button'; e.onclick = action; return e; };
    const sourceFor = t => state.documents.get(t.anchor.path)?.source;
    function clearMarks() {
      if (model && !model.isDisposed()) model.deltaDecorations(decorationIds, []);
      model = null; decorationIds = [];
    }
    function mark() {
      clearMarks();
      model = state.editor?.getModel?.() || null;
      if (!model || articleId !== state.selectedId) return;
      const marks = threads.filter(t => !t.resolved && t.anchor.path === state.openFile).flatMap(t => {
        const r = locate(model.getValue(), t.anchor); if (!r) return [];
        const a = model.getPositionAt(r.start), b = model.getPositionAt(r.end);
        return [{ range: { startLineNumber: a.lineNumber, startColumn: a.column, endLineNumber: b.lineNumber, endColumn: b.column },
          options: { inlineClassName: 'articleCommentAnchor', hoverMessage: { value: '评论：' + t.messages[0].text, isTrusted: false },
            stickiness: 1 } }];
      });
      decorationIds = model.deltaDecorations([], marks);
    }
    function compose(target, initial = '') {
      pending = target;
      form.hidden = false; input.value = initial;
      quoteBox.textContent = target.action === 'create' ? target.quote : target.action === 'edit' ? '编辑评论' : '回复讨论';
      input.focus();
    }
    function cancelCompose() { pending = null; input.value = ''; form.hidden = true; }
    async function navigate(t) {
      const id = articleId;
      try {
        await openArticleFile(t.anchor.path);
        if (id !== state.selectedId) return;
        const source = editorValue(), r = locate(source, t.anchor);
        if (!r) { showToast('原文已变化或被删除，无法可靠定位；评论保留原始引用。', 'info'); render(); return; }
        if (state.editor?.getModel()) {
          const m = state.editor.getModel(), a = m.getPositionAt(r.start), b = m.getPositionAt(r.end);
          const range = { startLineNumber: a.lineNumber, startColumn: a.column, endLineNumber: b.lineNumber, endColumn: b.column };
          state.editor.setSelection(range); state.editor.revealRangeInCenter(range); state.editor.focus();
        } else {
          const e = $('articleSourceEditor'); e.focus(); e.setSelectionRange(r.start, r.end);
          e.scrollTop = source.slice(0, r.start).split('\n').length * (parseFloat(getComputedStyle(e).lineHeight) || 20) - e.clientHeight / 2;
          e.dispatchEvent(new Event('scroll'));
        }
        mark();
      } catch (e) { showToast(e.message || e, 'error'); }
    }
    function render() {
      list.replaceChildren();
      $('articleCommentNew').disabled = !writable || busy || !state.openFile;
      const filter = $('articleCommentFilter').value;
      const visible = threads.filter(t => filter === 'all' || t.resolved === (filter === 'resolved'));
      $('articleCommentsToggle').textContent = `评论 (${threads.filter(t => !t.resolved).length})`;
      if (!visible.length) list.append(node('p', '暂无评论。选中源码后点击“添加评论”。', 'articleCommentsEmpty'));
      for (const t of visible) {
        const card = node('article', null, 'articleCommentThread'); card.dataset.threadId = t.id;
        const top = node('div', null, 'articleCommentMeta');
        const src = sourceFor(t), detached = typeof src === 'string' && !locate(src, t.anchor);
        top.append(node('span', `${t.anchor.path} · ${detached ? '原文已变化' : 'L' + t.anchor.line}`), node('span', t.resolved ? '已解决' : '未解决'));
        const link = button(t.anchor.quote, () => navigate(t)); link.classList.add('articleCommentQuote'); link.title = '定位原文';
        card.append(top, link);
        for (const m of t.messages) {
          const message = node('div', null, 'articleCommentMessage');
          const date = new Date(m.created_at).toLocaleString();
          message.append(node('small', `${m.author} · ${date}${m.edited_at && !m.deleted ? ' · 已编辑' : ''}`), node('p', m.deleted ? '此评论已删除' : m.text));
          if (writable && m.author === actor && !m.deleted) {
            const edit = button('编辑', () => compose({ action: 'edit', thread_id: t.id, message_id: m.id, version: t.version }, m.text));
            const del = button('删除', () => { if (window.confirm('删除这条评论？')) void send({ action: 'delete', thread_id: t.id, message_id: m.id, version: t.version }); });
            edit.disabled = del.disabled = busy; message.append(edit, del);
          }
          card.append(message);
        }
        if (writable) {
          const actions = node('div', null, 'articleCommentActions');
          const resolve = button(t.resolved ? '重新打开' : '解决', () => send({ action: t.resolved ? 'reopen' : 'resolve', thread_id: t.id, version: t.version }));
          resolve.disabled = busy; actions.append(resolve);
          if (!t.resolved) { const reply = button('回复', () => compose({ action: 'reply', thread_id: t.id })); reply.disabled = busy; actions.append(reply); }
          card.append(actions);
        }
        list.append(card);
      }
      mark();
    }
    async function refresh() {
      if (!articleId || loading || busy) return;
      const id = articleId, gen = generation; loading = true;
      try {
        const result = await request(`/api/articles/${id}/comments`);
        if (gen !== generation) return;
        threads = result.threads; writable = result.can_write; actor = result.actor;
        status.textContent = writable ? '' : '只读：需要文稿编辑权限才能评论'; render();
      } catch (e) { if (gen === generation) status.textContent = `加载评论失败：${e.message || e}`; }
      finally { if (gen === generation) loading = false; }
    }
    async function send(payload) {
      if (busy || !writable || articleId !== state.selectedId) return;
      const id = articleId, gen = ++generation; loading = false; busy = true; render(); $('articleCommentSubmit').disabled = true;
      try {
        const result = await request(`/api/articles/${id}/comments`, { method: 'POST', body: JSON.stringify(payload) });
        if (gen !== generation) return;
        threads = [...threads.filter(t => t.id !== result.thread.id), result.thread].sort((a,b) => a.created_at.localeCompare(b.created_at));
        if (['create','reply','edit'].includes(payload.action)) cancelCompose();
        status.textContent = '评论已保存';
      } catch (e) { if (gen === generation) status.textContent = `未保存：${e.message || e}。草稿已保留；如有版本冲突，请复制草稿并重新打开编辑。`; }
      finally { if (gen === generation) { busy = false; $('articleCommentSubmit').disabled = false; render(); } }
    }
    function selectArticle() {
      generation++; articleId = state.selectedId; threads = []; writable = false; actor = ''; busy = loading = false;
      cancelCompose(); clearMarks(); status.textContent = ''; $('articleCommentSubmit').disabled = false; render();
      if (!panel.hidden) void refresh();
    }
    async function add() {
      if (!writable || busy || !activeDocument()?.editable || activeDocument()?.resourceAddress) return;
      const id = articleId, path = state.openFile, source = editorValue();
      let start, end;
      if (state.editor?.getModel()) {
        const m = state.editor.getModel(), s = state.editor.getSelection();
        start = m.getOffsetAt(s.getStartPosition()); end = m.getOffsetAt(s.getEndPosition());
      } else { start = $('articleSourceEditor').selectionStart; end = $('articleSourceEditor').selectionEnd; }
      if (end <= start || end - start > 8000) { showToast('请选中 1–8000 个字符再添加评论', 'info'); return; }
      const quote = source.slice(start, end);
      if (!await saveAllDirtyDocuments({ silent: true })) return;
      if (id !== state.selectedId || path !== state.openFile || source !== editorValue()) { showToast('源码已变化，请重新选择文本', 'info'); return; }
      compose({ action: 'create', path, start, end, quote });
    }
    $('articleCommentsToggle').onclick = () => {
      panel.hidden = !panel.hidden; $('articleCommentsToggle').setAttribute('aria-expanded', String(!panel.hidden));
      if (!panel.hidden) { if (articleId !== state.selectedId) selectArticle(); else void refresh(); }
      state.editor?.layout?.();
    };
    $('articleCommentClose').onclick = () => $('articleCommentsToggle').click();
    $('articleCommentNew').onclick = () => void add();
    $('articleCommentRefresh').onclick = () => void refresh();
    $('articleCommentFilter').onchange = render;
    $('articleCommentCancel').onclick = cancelCompose;
    form.onsubmit = event => { event.preventDefault(); if (pending && input.value.trim()) void send({ ...pending, text: input.value }); };
    function changed() { clearTimeout(changedTimer); changedTimer = setTimeout(mark, 150); }
    $('articleSourceEditor').addEventListener('input', changed);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && !panel.hidden) void refresh(); });
    timer = setInterval(() => { if (!document.hidden && !panel.hidden) void refresh(); }, 5000);
    window.addEventListener('pagehide', () => clearInterval(timer));
    window.addEventListener('pageshow', e => { if (e.persisted) timer = setInterval(() => { if (!document.hidden && !panel.hidden) void refresh(); }, 5000); });
    function bindEditor() {
      if (!state.editor) return;
      state.monacoDisposables.push(state.editor.onDidChangeModel(mark), state.editor.onDidChangeModelContent(changed));
      state.monacoDisposables.push(state.editor.onMouseDown(e => {
        if (panel.hidden || !e.target.position) return;
        const offset = state.editor.getModel()?.getOffsetAt(e.target.position);
        const thread = threads.find(t => !t.resolved && t.anchor.path === state.openFile && (() => { const r = locate(editorValue(), t.anchor); return r && offset >= r.start && offset <= r.end; })());
        if (thread) list.querySelector(`[data-thread-id="${thread.id}"]`)?.scrollIntoView({ block: 'nearest' });
      }));
      mark();
    }
    return { selectArticle, bindEditor };
  }
  window.PortalArticleWorkspaceComments = Object.freeze({ createComments, dependencies: DEPENDENCIES });
})();
