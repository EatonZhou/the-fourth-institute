(function () {
  'use strict';
  if (window.PortalHomeMapView) return;

  // Home owns navigation. Native surfaces own their data, edits, permissions and camera.
  function create({ root, onOpen, onSelection } = {}) {
    if (!root) throw new TypeError('PortalHomeMapView.create requires root');
    const entries = new Map();
    let active = null, presented = true, destroyed = false, generation = 0;
    const shell = document.createElement('section'); shell.className = 'homeMapView';
    const toolbar = document.createElement('header'); toolbar.className = 'homeMapToolbar';
    const identity = document.createElement('details'); identity.className = 'homeMapIdentity';
    const title = document.createElement('summary');
    const identityBody = document.createElement('div'); identityBody.className = 'homeMapIdentityBody';
    const fullName = document.createElement('strong'); fullName.className = 'homeMapIdentityName';
    const idLabel = document.createElement('label'); idLabel.textContent = '目标 ID（可复制）';
    const idValue = document.createElement('input'); idValue.readOnly = true; idValue.onfocus = () => idValue.select();
    idLabel.append(idValue); identityBody.append(fullName, idLabel); identity.append(title, identityBody);
    const actions = document.createElement('div'); actions.className = 'homeMapActions';
    const report = document.createElement('button'); report.type = 'button'; report.textContent = '汇报'; report.setAttribute('aria-label', '查看汇报');
    const original = document.createElement('a'); original.textContent = '原页面 ↗'; original.setAttribute('aria-label', '在原页面打开（新标签页）'); original.target = '_blank'; original.rel = 'noopener';
    const status = document.createElement('p'); status.setAttribute('role', 'status'); status.hidden = true;
    const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = '重试'; retry.hidden = true;
    const body = document.createElement('div'); body.className = 'homeMapBody';
    actions.append(report, original); toolbar.append(identity, actions); shell.append(toolbar, status, retry, body); root.append(shell);
    function message(text = '') { status.textContent = text; status.hidden = !text; }
    function selected(entry, value) {
      entry.selection = value;
      if (entry === active && presented && !entry.host.hidden) onSelection?.(value);
    }
    function open(entry, value) { if (entry === active && presented) onOpen?.(value); }
    function heading(entry) {
      const name = entry.label || (entry.target.kind === 'project' ? entry.target.id : '团队线头');
      title.textContent = name;
      title.setAttribute('aria-label', name + '，查看目标信息');
      fullName.textContent = name; idValue.value = entry.target.id;
      original.href = entry.target.kind === 'project' ? '/projects?view=map&project=' + encodeURIComponent(entry.target.id)
        : '/lineage?view=frontier&lineage=' + encodeURIComponent(entry.target.id);
    }
    async function hide(entry) {
      if (!entry) return;
      entry.host.hidden = true;
      await entry.view?.setPresented(false);
    }
    function entryFor(target) {
      const key = JSON.stringify([target.kind, target.id]);
      if (entries.has(key)) return entries.get(key);
      const host = document.createElement('div'); host.className = 'homeMapSurface'; host.hidden = true; body.append(host);
      const entry = { target: { kind: target.kind, id: target.id }, host, selection: null, view: null };
      entries.set(key, entry);
      entry.ready = (async () => {
        if (target.kind === 'project') {
          if (!window.PortalProjectMap) throw new Error('研究地图组件尚未加载');
          entry.view = window.PortalProjectMap.create({ root: host, embedded: true,
            onBack: () => open(entry, { view: 'report', target: entry.target }),
            onOpen: value => open(entry, value), onFrontierSelection: value => selected(entry, value),
            onSelectionChange: value => selected(entry, value ? { address: value.direction_id, label: value.title, kind: 'direction', project_id: target.id } : null) });
          await entry.view.load(target.id);
        } else {
          const { mountFrontier } = await import('/static/frontier.js');
          if (destroyed) return;
          entry.view = mountFrontier(host, { lineage: target.id,
            // Reuse the native, authorized GetLineage result; no second title lookup.
            onTitleChange: label => { entry.label = label; if (entry === active) heading(entry); },
            onSelection: value => selected(entry, value), onOpen: value => open(entry, value) });
          await entry.view.ready;
        }
      })();
      // A failed mount is retryable; no inaccessible content is retained for display.
      entry.ready.catch(() => { entry.view?.destroy(); host.remove(); if (entries.get(key) === entry) entries.delete(key); });
      return entry;
    }
    async function show(target, { signal, requestId, view } = {}) {
      if (destroyed) throw new Error('地图展示已关闭');
      if (!target || !['project', 'lineage'].includes(target.kind) || typeof target.id !== 'string' || !target.id.trim()) throw new TypeError('地图需要明确的 Project 或 Lineage ID');
      signal?.throwIfAborted();
      const sequence = ++generation;
      const current = () => !destroyed && sequence === generation && !signal?.aborted;
      const abort = () => { if (sequence === generation) { generation++; void hide(active); message('展示请求已取消'); } };
      signal?.addEventListener('abort', abort, { once: true });
      try {
        if (active) await hide(active);
        if (!current()) return false;
        const entry = entryFor(target); active = entry; entry.requestId = requestId;
        entry.pendingFocus = target.focus; entry.pendingLayer = view;
        identity.open = false; heading(entry); message('正在读取地图与访问权限…'); retry.hidden = true;
        await entry.ready;
        if (!current()) { if (active !== entry || !presented) await hide(entry); return false; }
        if (!presented) { await hide(entry); return true; }
        await entry.view.setPresented(true);
        if (!current()) return false;
        entry.host.hidden = false;
        message();
        if (target.focus || (target.kind === 'project' && view)) {
          const value = target.kind === 'project' ? (typeof target.focus === 'string' ? target.focus : target.focus?.direction_id || target.focus?.id) : target.focus;
          const found = target.kind === 'project' ? await entry.view.showLayer(view, value) : await entry.view.focus(value);
          entry.pendingFocus = undefined; entry.pendingLayer = undefined;
          if (current() && !found) message('当前可读视图中未找到指定对象，可继续浏览地图。');
        }
        if (current()) onSelection?.(entry.selection);
        return current();
      } catch (error) {
        if (current()) { await hide(active); message(error.message || String(error)); retry.hidden = false; }
        throw error;
      } finally { signal?.removeEventListener('abort', abort); }
    }
    async function setPresented(value) {
      if (destroyed || presented === Boolean(value)) return;
      presented = Boolean(value); shell.hidden = !presented;
      if (!presented) { generation++; await hide(active); }
      // Visibility hooks are often fire-and-forget; show owns the visible retry state.
      else if (active) return show({ ...active.target, focus: active.pendingFocus }, { requestId: active.requestId, view: active.pendingLayer }).catch(error => ({ error }));
    }
    report.onclick = () => { if (active) open(active, { view: 'report', target: active.target }); };
    retry.onclick = () => { if (active) void show(active.target).catch(() => {}); };
    return { show, setPresented, snapshot: () => ({ dirty: !!active?.view?.editing?.() }), destroy() {
      if (destroyed) return; destroyed = true; generation++;
      entries.forEach(entry => entry.view?.destroy()); entries.clear(); shell.remove();
    } };
  }
  window.PortalHomeMapView = Object.freeze({ create });
})();
