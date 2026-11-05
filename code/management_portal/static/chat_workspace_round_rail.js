(() => {
  if (window.PortalChatWorkspaceRoundRail) return;
  const t = value => window.PortalI18n.t(value);
  const flagEvent = 'portal:chat-round-flags-changed';
  const terminal = new Set(['completed', 'failed', 'cancelled', 'rejected', 'stopped']);
  const labels = { running: '运行中', queued: '排队中', completed: '已完成', failed: '失败',
    cancelled: '已取消', paused: '已暂停', waiting_members: '等待成员', routing: '正在分派',
    delivering: '正在转交', scope_review: '等待分工', quota_review: '等待额度', recovery: '恢复中', unknown: '状态待确认' };
  const el = (tag, className, text) => {
    const node = document.createElement(tag); node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const button = (className, label, text = label) => {
    const node = el('button', className, text); node.type = 'button'; node.setAttribute('aria-label', label);
    node.dataset.portalTooltipOptOut = '1'; return node;
  };
  const markerPaths = {
    flag: 'M5 17V3m0 1c4-3 6 3 10 0v7c-4 3-6-3-10 0',
    star: 'm10 2 2.4 5 5.6.8-4 3.9.9 5.5-4.9-2.6-4.9 2.6.9-5.5-4-3.9 5.6-.8Z',
    turtle: 'M8 5V3a2 2 0 0 1 4 0v2m2 3c3-4 5-1 1 1m-1 4c4 1 3 4 0 2M6 8C3 4 1 7 5 9m1 4c-4 1-3 4 0 2m3 1 1 2 1-2M14.5 10.5a4.5 5.5 0 1 1-9 0 4.5 5.5 0 0 1 9 0Z',
    octopus: 'M5 11V7a5 5 0 0 1 10 0v4m-10 0c-5 5 0 8 2 3m0-3c-2 7 3 8 3 2m0 0c0 6 5 5 3-2m0 3c2 5 7 2 2-3M8 7v1m4-1v1',
  };
  const markerLabels = { flag: '旗帜', star: '星星', turtle: '乌龟', octopus: '章鱼' };
  const markerKind = value => Object.hasOwn(markerPaths, value) ? value : 'flag';
  const markerIcon = value => '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="' + markerPaths[markerKind(value)]
    + '" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const controlIcon = path => '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="' + path
    + '" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  function shortTime(value) {
    if (!value) return '';
    const date = new Date(value); if (!Number.isFinite(date.getTime())) return '';
    const today = new Date().toDateString() === date.toDateString();
    return new Intl.DateTimeFormat(undefined, { ...(today ? {} : { month: 'numeric', day: 'numeric' }), hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }
  function create({ root, viewport, state, scope, keyFor, roleFor, reveal, scrollTo, loadOlder, revalidate,
    previewText, renderPreview, bindPreview, releasePreview }) {
    // This owner projects already accepted requests onto measured message rows.
    // It never derives work state from the workspace's global runtime/busy flag.
    const wrapper = el('div', 'chatRoundViewport');
    viewport.before(wrapper); wrapper.append(viewport);
    const rail = el('nav', 'chatRoundRail'); rail.setAttribute('aria-label', t('轮次导航'));
    const older = button('chatRoundOlder', t('加载更早消息'), '');
    older.innerHTML = controlIcon('M10 16V4m-5 5 5-5 5 5');
    const directory = button('chatRoundDirectory', t('浏览已加载轮次'), '');
    directory.innerHTML = controlIcon('M7 5h9M7 10h9M7 15h9M3 5h.1M3 10h.1M3 15h.1');
    directory.setAttribute('aria-haspopup', 'dialog'); directory.setAttribute('aria-expanded', 'false');
    const lane = el('div', 'chatRoundLane');
    const marks = el('div', 'chatRoundMarks'); lane.append(marks);
    const scroll = el('div', 'chatRoundScroll');
    const thumb = el('div', 'chatRoundThumb'); thumb.tabIndex = 0;
    thumb.setAttribute('role', 'scrollbar'); thumb.setAttribute('aria-orientation', 'vertical');
    thumb.setAttribute('aria-label', t('滚动对话')); thumb.setAttribute('aria-valuemin', '0');
    if (viewport.id) thumb.setAttribute('aria-controls', viewport.id);
    scroll.append(thumb); rail.append(older, directory, lane, scroll); wrapper.append(rail);
    const preview = el('section', 'chatRoundPreview'); preview.hidden = true;
    preview.setAttribute('role', 'dialog'); preview.setAttribute('aria-label', t('轮次预览')); wrapper.append(preview);
    const notice = el('div', 'chatRoundNotice'); notice.setAttribute('role', 'status'); wrapper.append(notice);
    const historyNotice = el('div', 'chatRoundHistoryNotice'); historyNotice.setAttribute('role', 'status'); wrapper.append(historyNotice);
    const cleanups = [];
    const on = (node, type, fn, options) => { node.addEventListener(type, fn, options); cleanups.push(() => node.removeEventListener(type, fn, options)); };
    let disposed = false, frame = 0, currentScope = '', flags = new Map(), rows = [], search = [];
    let progress = new Map(), cursor = -1, uncertain = false, bindings = new Map(), signature = '';
    let awaitingRefresh = false, refreshEpoch = 0, refreshing = false;
    let layoutDirty = true, statusDirty = true, searchDirty = true, presented = true, intersecting = true, observing = false;
    let coords = new Map(), nodeForKey = new Map(), currentNode = null, tickSignature = '';
    let metrics = { total: 0, client: 0, track: 0, thumb: 0, hit: 26, available: 0 };
    const observedRows = new Set(), sizes = new WeakMap(); let observedList = null;
    let hoverTimer = 0, closeTimer = 0, press = null, dragging = null, popup = null, navigation = 0, restoringFocus = false;
    let hoverKey = '', activation = null, pointerRest = null, laneTop = 0;
    let previewFrame = 0, markerMenu = null;
    let historyRequest = null;
    const previewSizes = new WeakMap();
    const identity = () => String(window.PortalIdentity?.username?.() || '');
    const scopeKey = () => JSON.stringify([identity(), String(scope())]);
    const storageKey = () => window.PortalState.storage.key('portal.chat.round-flags.v1.' + scopeKey());
    const readFlags = () => {
      try {
        const value = JSON.parse(localStorage.getItem(storageKey()) || '[]');
        // Keep one installation/user/conversation key. Legacy ID arrays are
        // flags; migrate on the next confirmed edit, never on a read.
        if (Array.isArray(value)) return new Map(value.filter(id => typeof id === 'string').map(id => [id, 'flag']));
        return new Map((value?.version === 2 && Array.isArray(value.markers) ? value.markers : [])
          .filter(item => item && typeof item.id === 'string').map(item => [item.id, markerKind(item.icon)]));
      } catch { return new Map(flags); }
    };
    const human = item => roleFor(item) === 'human' && !item.local_delivery && !item.pending
      && (!item.origin?.sender?.kind || item.origin.sender.kind === 'user');
    const requestFor = key => {
      const item = state.messageByKey.get(String(key));
      if (!item) return '';
      if (human(item)) return keyFor(item);
      const request = item.family_reply?.request_message_id || item.reply_to_message_id;
      const source = state.messages.find(candidate => human(candidate) && (candidate.message_id || keyFor(candidate)) === request);
      return source ? keyFor(source) : '';
    };
    function resetScope() {
      if (currentScope === scopeKey()) return;
      currentScope = scopeKey(); flags = new Map(); flags = readFlags(); progress.clear(); cursor = -1;
      historyRequest = null; historyNotice.textContent = ''; paintHistory();
      uncertain = false; awaitingRefresh = false; refreshing = false; refreshEpoch++; search = []; navigation++; closePreview(); cancelPress(); signature = ''; layoutDirty = statusDirty = searchDirty = true;
    }
    function toggle(key) {
      resetScope(); const request = requestFor(key); if (!request) return false;
      return setMarker(key, readFlags().has(request) ? null : 'flag');
    }
    function setMarker(key, kind) {
      resetScope(); const request = requestFor(key); if (!request) return false;
      const before = readFlags(), next = new Map(before);
      if (kind === null) next.delete(request); else next.set(request, markerKind(kind));
      const value = JSON.stringify({ version: 2, markers: [...next].sort(([a], [b]) => a.localeCompare(b)).map(([id, icon]) => ({ id, icon })) });
      let previousValue, attempted = false;
      try {
        if (!identity()) throw new Error(t('请先登录'));
        // PortalState's writers are intentionally best-effort. Personal flags
        // need a confirmed write, using the same installation-scoped key.
        previousValue = localStorage.getItem(storageKey()); attempted = true;
        localStorage.setItem(storageKey(), value);
        if (localStorage.getItem(storageKey()) !== value) throw new Error(t('保存未确认'));
        flags = next; notice.textContent = '';
        window.dispatchEvent(new CustomEvent(flagEvent, { detail: { key: storageKey() } }));
      } catch {
        // Preserve the previous persisted value as well as the rendered choice
        // when a writer silently drops/corrupts the value or cannot read it back.
        if (attempted) try {
          if (previousValue === null) localStorage.removeItem(storageKey());
          else localStorage.setItem(storageKey(), previousValue);
        } catch { /* Storage remains unavailable; keep the last confirmed UI. */ }
        flags = before;
        notice.textContent = t('关注未保存，请重试');
        window.PortalUI?.toast?.(notice.textContent, 'error');
        statusDirty = true; schedule(); return false;
      }
      statusDirty = true; schedule(); return true;
    }
    function updateProgress(snapshot) {
      resetScope(); if (!snapshot) return;
      const nextCursor = Number(snapshot.cursor || 0);
      if (nextCursor < cursor) return;
      if (snapshot.verified === true) {
        awaitingRefresh = false;
        // A full verified snapshot cannot certify an omitted request using a
        // previous running value. Incremental event projections keep their scope.
        for (const value of progress.values()) value.verified = false;
      }
      const next = new Map();
      const hasWork = value => value && Object.prototype.hasOwnProperty.call(value, 'work');
      const workFact = value => {
        const work = value && typeof value === 'object' ? value : {};
        return { ...work, state: ['queued', 'running', 'completed', 'failed', 'cancelled'].includes(work.state) ? work.state : 'unknown' };
      };
      // Input.state records delivery. Only the separately proven work fact can
      // describe execution (an appended input is often delivered while running).
      for (const input of snapshot.input_states || []) {
        if (!input.message_id) continue;
        next.set(String(input.message_id), hasWork(input) ? workFact(input.work) : null);
      }
      const relays = snapshot.relays || (snapshot.messages || []).filter(item => item.role === 'user' && item.relay)
        .map(item => ({ ...item.relay, message_id: item.message_id }));
      for (const relay of relays) {
        // Family input.work already projects the member's execution. Only an
        // older snapshot without that field needs the relay.work fallback.
        if (relay.message_id && !next.get(String(relay.message_id)) && hasWork(relay)) {
          next.set(String(relay.message_id), workFact(relay.work));
        } else if (relay.message_id && !next.has(String(relay.message_id))) next.set(String(relay.message_id), null);
      }
      let needsRefresh = false;
      for (const [id, value] of next) {
        const previous = progress.get(id);
        const finished = new Set(previous?.finished || []);
        const merge = (before, incoming) => {
          const oldTime = Date.parse(before?.updated_at), newTime = Date.parse(incoming.updated_at);
          const execution = incoming.execution_id && JSON.stringify([incoming.execution_id, incoming.provider_turn_id || '']);
          const endedExecution = execution && finished.has(execution);
          if (before && incoming.state !== 'unknown' && (
            (Number.isFinite(oldTime) && Number.isFinite(newTime) && newTime < oldTime)
            || (endedExecution && !terminal.has(incoming.state)))) return before;
          return incoming;
        };
        const incoming = value || workFact(null);
        const work = merge(previous?.work, incoming);
        const signatureFor = fact => JSON.stringify(['state', 'kind', 'execution_id', 'provider_turn_id', 'primary_message_id', 'delivery', 'updated_at']
          .map(key => fact?.[key] || ''));
        // Re-rendering/history cannot invent an ordering between conflicting
        // facts at one cursor. Certify them through the existing snapshot read.
        const conflict = previous && previous.cursor === nextCursor && signatureFor(previous.work) !== signatureFor(work)
          && !(refreshing && snapshot.verified === true);
        if (conflict) needsRefresh = true;
        const accepted = conflict ? previous.work : work;
        if (!conflict && terminal.has(accepted.state) && accepted.execution_id) {
          finished.add(JSON.stringify([accepted.execution_id, accepted.provider_turn_id || '']));
        }
        progress.set(id, { work: accepted, cursor: nextCursor, finished, state: accepted.state, verified: !awaitingRefresh && !conflict });
      }
      cursor = nextCursor; uncertain = navigator.onLine === false || awaitingRefresh; statusDirty = true; schedule();
      if (needsRefresh && !refreshing) void refresh();
    }
    const statusFor = item => {
      const value = progress.get(String(item.message_id || keyFor(item)));
      return uncertain || !value || value.verified === false ? 'unknown' : String(value.state || 'unknown');
    };
    const textFor = previewText;
    const description = row => [textFor(row.item).slice(0, 120) || t('附件消息'), t(labels[statusFor(row.item)] || '状态待确认'),
      flags.has(row.key) ? t(markerLabels[flags.get(row.key)]) : ''].filter(Boolean).join(' · ');
    function closeMarkerMenu() {
      const current = markerMenu; markerMenu = null;
      if (current) { current.binding.destroy(); current.panel.remove(); }
    }
    function openMarkerMenu(key, trigger) {
      if (markerMenu?.trigger === trigger) { closeMarkerMenu(); return; }
      closeMarkerMenu(); clearTimeout(closeTimer); clearTimeout(hoverTimer);
      if (popup) popup.explicit = true;
      const panel = el('div', 'chatRoundMarkerMenu'); panel.setAttribute('role', 'menu');
      panel.setAttribute('aria-label', t('选择标记')); wrapper.append(panel);
      const selected = flags.get(requestFor(key));
      const choices = el('div', 'chatRoundMarkerChoices'); panel.append(choices);
      const choose = value => {
        closeMarkerMenu(); setMarker(key, value);
        preview.querySelector(`[data-round-follow="${CSS.escape(key)}"]`)?.focus({ preventScroll: true });
      };
      for (const kind of Object.keys(markerPaths)) {
        const item = button('chatRoundMarkerChoice', t(markerLabels[kind]), ''); item.dataset.roundMarker = kind;
        item.setAttribute('role', 'menuitemradio'); item.setAttribute('aria-checked', String(selected === kind));
        item.tabIndex = -1; item.innerHTML = markerIcon(kind); item.append(el('span', '', t(markerLabels[kind])));
        item.onclick = () => choose(kind); choices.append(item);
      }
      const remove = button('chatRoundMarkerRemove', t('取消Flag')); remove.setAttribute('role', 'menuitem'); remove.tabIndex = -1;
      remove.disabled = !selected; remove.onclick = () => choose(null); panel.append(remove);
      const binding = window.PortalOverlay.bindPopover({ trigger, panel, portal: true, placement: 'bottom-end',
        closeOnOutside: true, closeOnEscape: true, onClose: () => { closeMarkerMenu(); } });
      markerMenu = { panel, trigger, binding }; binding.open();
      const enabled = () => [...panel.querySelectorAll('button:not(:disabled)')];
      const focus = item => { enabled().forEach(node => node.tabIndex = node === item ? 0 : -1); item.focus({ preventScroll: true }); };
      focus(panel.querySelector('[aria-checked="true"]') || enabled()[0]);
      panel.addEventListener('keydown', event => {
        if (event.key === 'Tab') { closeMarkerMenu(); trigger.focus({ preventScroll: true }); return; }
        if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); const items = enabled(), index = items.indexOf(document.activeElement);
        focus(items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
          : (index + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1) + items.length) % items.length]);
      });
    }
    function cancelPress() {
      if (press) { clearTimeout(press.timer); press.node.classList.remove('is-pressing'); press = null; }
    }
    function closePreview(returnFocus = false) {
      closeMarkerMenu();
      hoverKey = '';
      clearTimeout(hoverTimer); clearTimeout(closeTimer);
      previewResize.disconnect(); cancelAnimationFrame(previewFrame); previewFrame = 0;
      if (popup?.list) releasePreview(popup.list);
      const trigger = popup?.trigger; popup = null; preview.hidden = true; preview.replaceChildren();
      directory.setAttribute('aria-expanded', 'false');
      if (returnFocus && trigger?.isConnected) { restoringFocus = true; trigger.focus({ preventScroll: true }); restoringFocus = false; }
    }
    async function locate(key) {
      cancelPress(); const revision = ++navigation, expected = scopeKey(); closePreview();
      try {
        const found = await reveal(key, { alignStart: true });
        if (disposed || revision !== navigation || expected !== scopeKey()) return;
        if (!found) notice.textContent = t('无法定位这条消息，请重试');
        else notice.textContent = '';
      } catch (error) {
        if (!disposed && revision === navigation && expected === scopeKey()) notice.textContent = t('定位失败') + ': ' + error.message;
      }
    }
    function paintHistory() {
      const history = state.messageRenderOptions?.history;
      const busy = !!historyRequest || !!history?.loading;
      older.setAttribute('aria-busy', String(busy));
      older.setAttribute('aria-disabled', String(busy || !history?.can_load_older));
      older.setAttribute('aria-label', t(busy ? '正在加载…' : history?.can_load_older ? '加载更早消息' : '没有更早消息'));
    }
    async function requestOlder() {
      resetScope();
      if (historyRequest || state.messageRenderOptions?.history?.loading) return;
      cancelPress(); closePreview();
      historyNotice.classList.remove('is-directory');
      if (!state.messageRenderOptions?.history?.can_load_older) {
        historyNotice.textContent = t('没有更早消息'); return;
      }
      const request = { scope: scopeKey() }; historyRequest = request;
      historyNotice.textContent = t('正在加载…'); paintHistory();
      const current = () => !disposed && historyRequest === request && request.scope === scopeKey();
      try {
        // The existing host/protocol loads exactly one page. The message
        // viewport alone preserves its reading anchor when the page arrives.
        await loadOlder();
        if (current()) {
          historyNotice.textContent = t(state.messageRenderOptions?.history?.can_load_older ? '已加载更早消息' : '没有更早消息');
          invalidate();
        }
      } catch (error) {
        if (current()) historyNotice.textContent = t('加载失败') + ': ' + error.message;
      } finally {
        if (current()) { historyRequest = null; paintHistory(); }
      }
    }
    function showPreview(items, trigger, explicit = false) {
      if (disposed || dragging) return;
      closeMarkerMenu();
      const focusedFlag = preview.contains(document.activeElement) ? document.activeElement.dataset.roundFollow : '';
      const expanded = popup?.trigger === trigger ? popup.expanded : new Set();
      clearTimeout(hoverTimer); clearTimeout(closeTimer);
      previewResize.disconnect(); cancelAnimationFrame(previewFrame); previewFrame = 0;
      if (popup?.list) releasePreview(popup.list);
      popup = { items, trigger, explicit, expanded, contents: [] }; preview.replaceChildren(); preview.hidden = false;
      directory.setAttribute('aria-expanded', String(trigger === directory));
      const head = el('div', 'chatRoundPreviewHead', trigger === directory ? t('浏览已加载轮次') : window.PortalI18n.text`${items.length} 条消息`);
      const close = button('chatRoundClose', t('关闭'), ''); close.innerHTML = controlIcon('m5 5 10 10M15 5 5 15');
      close.onclick = () => closePreview(true); head.append(close); preview.append(head);
      const list = el('div', 'chatRoundPreviewList');
      for (const row of items) {
        const card = el('article', 'chatRoundPreviewItem');
        const content = renderPreview(row.item), main = el('div', 'chatRoundPreviewContent');
        const meta = el('div', 'chatRoundPreviewMeta');
        meta.append(el('time', '', shortTime(row.item.created_at)));
        const status = statusFor(row.item);
        if (status !== 'unknown' || uncertain) {
          const stateLabel = el('span', 'chatRoundPreviewState', t(labels[status] || '状态待确认'));
          stateLabel.classList.toggle('is-running', status === 'running'); meta.append(stateLabel);
        }
        const jump = button('chatRoundPreviewJump', t('定位消息') + ' · ' + description(row), content.text || t('附件消息'));
        jump.onclick = () => locate(row.key); main.append(meta, jump);
        if (content.references.childElementCount) main.append(content.references);
        if (content.text) {
          popup.contents.push({ key: row.key, jump, meta, expand: null });
        }
        card.append(main);
        const request = requestFor(row.key);
        if (request) {
          const kind = flags.get(request);
          const flag = button('chatRoundFollow', t('选择标记') + (kind ? ' · ' + t(markerLabels[kind]) : ''), '');
          flag.innerHTML = markerIcon(kind);
          flag.dataset.roundFollow = row.key;
          flag.setAttribute('aria-haspopup', 'menu'); flag.setAttribute('aria-expanded', 'false');
          flag.setAttribute('aria-pressed', String(!!kind)); flag.onclick = () => openMarkerMenu(row.key, flag); card.append(flag);
        }
        list.append(card);
      }
      if (!items.length) list.append(el('p', 'chatRoundEmpty', t('暂无已加载轮次')));
      preview.append(list); popup.list = list; bindPreview(list); measurePreview();
      for (const { jump } of popup.contents) previewResize.observe(jump);
      if (focusedFlag) preview.querySelector(`[data-round-follow="${CSS.escape(focusedFlag)}"]`)?.focus({ preventScroll: true });
      else if (explicit) { preview.tabIndex = -1; (preview.querySelector('.chatRoundPreviewJump') || preview).focus({ preventScroll: true }); }
    }
    function applyExpanded(content, value) {
      if (value) popup.expanded.add(content.key); else popup.expanded.delete(content.key);
      content.jump.classList.toggle('is-expanded', value);
      if (content.expand) {
        const label = t(value ? '收起' : '展开');
        content.expand.textContent = label; content.expand.setAttribute('aria-label', label);
        content.expand.setAttribute('aria-expanded', String(value));
      }
    }
    function measurePreview() {
      if (disposed || !popup || preview.hidden) return;
      // Measure the clamped original once per preview/width/font change. No
      // duplicate full-text DOM and no measurement on conversation/list scroll.
      for (const content of popup.contents) content.jump.classList.remove('is-expanded');
      const measurements = popup.contents.map(content => {
        const { jump } = content;
        previewSizes.set(jump, { width: jump.clientWidth, lineHeight: getComputedStyle(jump).lineHeight });
        return { content, truncated: jump.scrollHeight > jump.clientHeight + 1 };
      });
      for (const { content, truncated } of measurements) {
        if (truncated && !content.expand) {
          content.expand = button('chatRoundExpand', t('展开'));
          content.expand.onclick = () => { applyExpanded(content, !popup.expanded.has(content.key)); positionPreview(); };
          content.meta.append(content.expand);
        } else if (!truncated && content.expand) {
          if (document.activeElement === content.expand) content.jump.focus({ preventScroll: true });
          content.expand.remove(); content.expand = null;
        }
        applyExpanded(content, truncated && popup.expanded.has(content.key));
      }
      positionPreview();
    }
    const previewResize = new ResizeObserver(entries => {
      if (!popup || disposed || preview.hidden) return;
      const changed = entries.some(({ target }) => {
        const previous = previewSizes.get(target);
        return !previous || previous.width !== target.clientWidth || previous.lineHeight !== getComputedStyle(target).lineHeight;
      });
      if (changed && !previewFrame) previewFrame = requestAnimationFrame(() => { previewFrame = 0; measurePreview(); });
    });
    function positionPreview() {
      if (preview.hidden) return;
      const bounds = wrapper.getBoundingClientRect(), source = popup?.trigger?.getBoundingClientRect();
      const height = Math.min(bounds.height - 8, preview.scrollHeight);
      preview.style.top = Math.max(4, Math.min((source?.top || bounds.top) - bounds.top, bounds.height - height - 4)) + 'px';
    }
    const visible = () => presented && intersecting && !document.hidden;
    function schedule() { if (!disposed && visible() && !frame) frame = requestAnimationFrame(() => { frame = 0; measure(); }); }
    function invalidate() { layoutDirty = true; schedule(); }
    function measure() {
      if (disposed) return; resetScope();
      if (layoutDirty) measureLayout();
      if (statusDirty) { paintStatus(); statusDirty = false; }
      if (searchDirty) { paintSearch(); searchDirty = false; }
      paintScroll();
    }
    function measureLayout() {
      const bounds = viewport.getBoundingClientRect(), height = lane.clientHeight;
      if (!bounds.height || !height) return;
      laneTop = lane.getBoundingClientRect().top;
      const total = Math.max(viewport.scrollHeight, viewport.clientHeight), top = viewport.scrollTop;
      sizes.set(viewport, { width: bounds.width, height: bounds.height });
      const list = viewport.querySelector('.messageList');
      if (list !== observedList) {
        mutation.disconnect(); mutation.observe(viewport, { childList: true });
        if (list) mutation.observe(list, { childList: true }); observedList = list;
      }
      const elements = [...viewport.querySelectorAll('.messageList > [data-message-key]')];
      const liveRows = new Set(elements);
      for (const node of observedRows) if (!liveRows.has(node)) { resize.unobserve(node); observedRows.delete(node); }
      coords = new Map();
      for (const node of elements) {
        if (!observedRows.has(node)) { observedRows.add(node); resize.observe(node); }
        const rect = node.getBoundingClientRect();
        sizes.set(node, { width: rect.width, height: rect.height });
        if (!node.hidden && rect.height) coords.set(node.dataset.messageKey, rect.top - bounds.top + top);
      }
      rows = state.messages.filter(human).map(item => ({ item, key: keyFor(item), y: coords.get(keyFor(item)) }))
        .filter(row => row.y !== undefined).sort((a, b) => a.y - b.y);
      const hit = matchMedia('(pointer: coarse)').matches ? 44 : 26;
      const available = Math.max(0, height - hit);
      for (const row of rows) row.pixel = hit / 2 + Math.max(0, Math.min(1, row.y / total)) * available;
      const nextSignature = JSON.stringify(rows.map(row => row.key));
      if (signature !== nextSignature) {
        closePreview(); signature = nextSignature; bindings.clear(); marks.replaceChildren(); currentNode = null;
        rows.forEach(row => {
          const node = button('chatRoundNode', '', '');
          node.append(el('span', 'chatRoundCore'));
          node.dataset.roundKey = row.key; marks.append(node); bindings.set(node, row);
        });
      }
      nodeForKey.clear();
      [...bindings].forEach(([node], index) => {
        const row = rows[index]; bindings.set(node, row); node.style.top = row.pixel + 'px';
        nodeForKey.set(row.key, node);
      });
      metrics = { total, client: viewport.clientHeight, track: scroll.clientHeight, hit, available,
        thumb: Math.min(scroll.clientHeight, Math.max(32, scroll.clientHeight * viewport.clientHeight / total)) };
      thumb.style.height = metrics.thumb + 'px'; thumb.setAttribute('aria-valuemax', String(Math.max(0, total - metrics.client)));
      paintHistory();
      layoutDirty = false; statusDirty = searchDirty = true;
      positionPreview();
    }
    function paintStatus() {
      bindings.forEach((row, node) => {
        row.item = state.messageByKey.get(row.key) || row.item;
        const status = statusFor(row.item);
        node.classList.toggle('is-running', status === 'running');
        node.classList.toggle('is-queued', status === 'queued');
        const followed = flags.has(row.key);
        const kind = flags.get(row.key) || '';
        if (kind !== (node.dataset.roundMarker || '')) {
          if (followed) {
            let flag = node.querySelector('.chatRoundFlag');
            if (!flag) { flag = el('span', 'chatRoundFlag'); node.append(flag); }
            flag.innerHTML = markerIcon(kind);
          }
          else node.querySelector('.chatRoundFlag')?.remove();
          node.dataset.roundMarker = kind;
          node.classList.toggle('is-followed', followed);
        }
        node.setAttribute('aria-label', description(row));
      });
    }
    function pointerRow(event) {
      // DOM hit boxes overlap, so their paint order is not message identity.
      // Search cached centers; ties use the first row at that actual position.
      const pixel = event.clientY - laneTop;
      let low = 0, high = rows.length;
      while (low < high) { const middle = (low + high) >>> 1; if (rows[middle].pixel < pixel) low = middle + 1; else high = middle; }
      let index = low;
      if (index === rows.length || (index > 0 && pixel - rows[index - 1].pixel <= rows[index].pixel - pixel)) index--;
      if (index < 0) return null;
      const center = rows[index].pixel;
      low = 0; high = index;
      while (low < high) { const middle = (low + high) >>> 1; if (rows[middle].pixel < center) low = middle + 1; else high = middle; }
      return rows[low];
    }
    function hoverPointer(event) {
      if (event.pointerType === 'touch' || activation || dragging || !event.target.closest('.chatRoundNode')) return;
      // Releasing capture/layout under a stationary pointer can emit over
      // again after click. Reopen only after the user actually moves away.
      if (pointerRest && Math.hypot(event.clientX - pointerRest.x, event.clientY - pointerRest.y) < 1) return;
      pointerRest = null;
      clearTimeout(closeTimer);
      const row = pointerRow(event); if (!row || row.key === hoverKey) return;
      if (popup && !popup.explicit && (popup.items.length !== 1 || popup.items[0].key !== row.key)) closePreview();
      clearTimeout(hoverTimer); hoverKey = row.key;
      const expected = scopeKey(), key = row.key;
      hoverTimer = setTimeout(() => {
        if (disposed || activation || hoverKey !== key || expected !== scopeKey()) return;
        const node = nodeForKey.get(key), current = bindings.get(node);
        if (current) showPreview([current], node);
      }, 220);
    }
    function paintScroll() {
      const top = viewport.scrollTop, range = Math.max(0, metrics.total - metrics.client);
      thumb.style.top = (range ? (metrics.track - metrics.thumb) * top / range : 0) + 'px';
      thumb.setAttribute('aria-valuenow', String(Math.round(top)));
      // Cached sorted coordinates make ordinary reading O(log rounds), with
      // only the previous/new reading indicators receiving a DOM update.
      let low = 0, high = rows.length;
      while (low < high) { const middle = (low + high) >>> 1; if (rows[middle].y <= top + 24) low = middle + 1; else high = middle; }
      // At the end of a long final reply the viewport top can still be above
      // its request. Use the last measured user row, tolerating fractional
      // scroll offsets; keep the normal reading anchor everywhere else.
      const index = range > 0 && top >= range - 1 ? rows.length - 1 : Math.max(0, low - 1);
      const next = nodeForKey.get(rows[index]?.key) || null;
      if (next !== currentNode) { currentNode?.classList.remove('is-current'); next?.classList.add('is-current'); currentNode = next; }
    }
    function paintSearch() {
      const hitGroups = [];
      for (const item of search) {
        const key = keyFor(item), y = coords.get(key); if (y === undefined) continue;
        const pixel = metrics.hit / 2 + Math.max(0, Math.min(1, y / metrics.total)) * metrics.available;
        const group = hitGroups.find(candidate => Math.abs(candidate.pixel - pixel) < 8);
        if (group) group.items.push({ item, key, y }); else hitGroups.push({ pixel, items: [{ item, key, y }] });
      }
      const next = JSON.stringify(hitGroups.map(group => [group.pixel, group.items.map(item => item.key)]));
      if (next === tickSignature) return;
      tickSignature = next; rail.querySelectorAll('.chatRoundSearchTick').forEach(node => node.remove());
      for (const group of hitGroups) {
        const tick = button('chatRoundSearchTick', t('查找结果') + ' · ' + group.items.length, ''); tick.style.top = group.pixel + 'px';
        tick.onclick = () => group.items.length > 1 ? showPreview(group.items, tick, true) : locate(group.items[0].key); lane.append(tick);
      }
    }
    on(older, 'click', requestOlder);
    on(directory, 'click', () => {
      cancelPress(); closePreview();
      if (!rows.length) {
        historyNotice.classList.add('is-directory'); historyNotice.textContent = t('暂无已加载轮次'); return;
      }
      historyNotice.textContent = ''; showPreview(rows, directory, true);
    });
    // Refresh only this origin on entry/down (e.g. after outer-page scrolling),
    // never read layout per pointermove or per conversation scroll.
    on(rail, 'pointerenter', () => { laneTop = lane.getBoundingClientRect().top; });
    on(marks, 'click', event => {
      const node = event.target.closest('.chatRoundNode'); if (!node && !activation) return;
      const pending = activation; activation = null; clearTimeout(hoverTimer); hoverKey = '';
      if (event.detail !== 0) pointerRest = { x: event.clientX, y: event.clientY };
      if (event.detail !== 0 && pending && (pending.cancelled || pending.longPressed || pending.scope !== scopeKey())) return;
      const key = event.detail === 0 ? bindings.get(node)?.key : pending?.key || pointerRow(event)?.key;
      if (key && nodeForKey.has(key)) void locate(key);
    });
    on(marks, 'pointerover', hoverPointer);
    on(marks, 'pointermove', hoverPointer);
    on(marks, 'pointerout', event => {
      if (marks.contains(event.relatedTarget)) return;
      if (press && activation) activation.cancelled = true;
      hoverKey = ''; cancelPress(); clearTimeout(hoverTimer); closeTimer = setTimeout(() => { if (!popup?.explicit && !markerMenu) closePreview(); }, 240);
    });
    on(preview, 'pointerenter', () => clearTimeout(closeTimer));
    on(preview, 'pointerleave', () => { closeTimer = setTimeout(() => { if (!popup?.explicit && !preview.contains(document.activeElement)) closePreview(); }, 240); });
    on(marks, 'focusin', event => {
      if (restoringFocus) return;
      clearTimeout(hoverTimer); hoverKey = '';
      if (activation) {
        // Touch default focus may land on the topmost overlapping DOM button.
        restoringFocus = true; nodeForKey.get(activation.key)?.focus({ preventScroll: true }); restoringFocus = false;
        return;
      }
      const node = event.target.closest('.chatRoundNode'), row = bindings.get(node);
      if (row) showPreview([row], node);
    });
    on(marks, 'pointerdown', event => {
      cancelPress(); activation = null; clearTimeout(hoverTimer); hoverKey = '';
      if (!bindings.has(event.target.closest('.chatRoundNode')) || event.button !== 0) return;
      laneTop = lane.getBoundingClientRect().top;
      const row = pointerRow(event); if (!row) return;
      if (popup && (popup.items.length !== 1 || popup.items[0].key !== row.key)) closePreview();
      const node = nodeForKey.get(row.key);
      const pending = activation = { key: row.key, scope: scopeKey(), cancelled: false, longPressed: false };
      // Capture on the stable container: layout/stream updates can move or
      // replace a button between down and up without changing the accepted ID.
      marks.setPointerCapture(event.pointerId);
      if (event.pointerType !== 'touch') {
        event.preventDefault(); restoringFocus = true; node.focus({ preventScroll: true }); restoringFocus = false;
      }
      node.classList.add('is-pressing');
      press = { node, x: event.clientX, y: event.clientY, timer: setTimeout(() => {
        if (pending !== activation || pending.cancelled || pending.scope !== scopeKey() || !nodeForKey.has(pending.key)) return;
        pending.longPressed = true; toggle(pending.key);
        cancelPress();
      }, 550) };
    });
    on(window, 'pointermove', event => {
      if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > 7) { if (activation) activation.cancelled = true; cancelPress(); }
      if (!dragging || event.pointerId !== dragging.id) return;
      const range = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
      scrollTo(Math.max(0, Math.min(range, dragging.top + (event.clientY - dragging.y) * range / Math.max(1, scroll.clientHeight - thumb.offsetHeight))));
    });
    on(window, 'pointerup', event => { if (!marks.contains(event.target)) activation = null; cancelPress(); dragging = null; rail.classList.remove('is-dragging'); });
    on(window, 'pointercancel', () => { activation = null; cancelPress(); dragging = null; rail.classList.remove('is-dragging'); });
    on(thumb, 'pointerdown', event => {
      if (event.button !== 0) return; event.preventDefault(); closePreview(); cancelPress();
      dragging = { id: event.pointerId, y: event.clientY, top: viewport.scrollTop }; thumb.setPointerCapture(event.pointerId); rail.classList.add('is-dragging');
    });
    on(scroll, 'pointerdown', event => { if (event.target !== scroll) return; closePreview(); scrollTo((event.clientY - scroll.getBoundingClientRect().top) / scroll.clientHeight * viewport.scrollHeight - viewport.clientHeight / 2); });
    on(wrapper, 'keydown', event => {
      activation = null; clearTimeout(hoverTimer); hoverKey = '';
      if (event.key === 'Escape' && !preview.hidden) { event.preventDefault(); closePreview(true); return; }
      if (event.target === thumb) {
        const delta = { ArrowDown: 48, ArrowUp: -48, PageDown: viewport.clientHeight * .8, PageUp: -viewport.clientHeight * .8 }[event.key];
        if (delta !== undefined || ['Home', 'End'].includes(event.key)) { event.preventDefault(); scrollTo(event.key === 'Home' ? 0 : event.key === 'End' ? viewport.scrollHeight : viewport.scrollTop + delta); }
      } else if (event.target.closest('.chatRoundNode')) {
        const nodes = [...bindings.keys()], index = nodes.indexOf(event.target.closest('.chatRoundNode'));
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault(); nodes[event.key === 'Home' ? 0 : event.key === 'End' ? nodes.length - 1 : Math.max(0, Math.min(nodes.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus({ preventScroll: true });
        }
      }
    });
    const outsidePreview = target => !preview.contains(target) && !rail.contains(target) && !markerMenu?.panel.contains(target);
    on(document, 'pointerdown', event => { if (outsidePreview(event.target)) closePreview(); });
    on(document, 'focusin', event => { if (outsidePreview(event.target)) closePreview(); });
    on(viewport, 'wheel', () => { if (activation) activation.cancelled = true; cancelPress(); }, { passive: true });
    on(viewport, 'scroll', () => { if (!popup?.explicit) closePreview(); schedule(); }, { passive: true });
    const lost = () => { uncertain = true; awaitingRefresh = true; for (const value of progress.values()) value.verified = false; cancelPress(); statusDirty = true; if (!visible()) paintStatus(); schedule(); };
    const refresh = async () => {
      if (!visible() || refreshing || !revalidate) return;
      lost(); refreshing = true;
      const epoch = ++refreshEpoch, expected = scopeKey();
      try {
        const snapshot = await revalidate();
        if (disposed || epoch !== refreshEpoch || expected !== scopeKey()) return;
        if (snapshot) { awaitingRefresh = false; updateProgress({ ...snapshot, verified: true }); }
      } catch { /* The existing Resident protocol reports and retries access errors. */ }
      finally { if (epoch === refreshEpoch) refreshing = false; }
    };
    on(document, 'visibilitychange', () => { lost(); updateObservation(); if (visible()) void refresh(); });
    // connection-closed is a disposal receipt shared by event streams and
    // instruction reads (normal handover/first batch/leadership release).
    // It is not a health signal and may follow a healthy successor's ready.
    // Invalidate on actual transport failure or the Resident protocol's own
    // connectionLost callback, not on unrelated diagnostic receipts.
    for (const name of ['offline', 'portal:events-error']) on(window, name, lost);
    for (const name of ['online', 'portal:events-ready']) on(window, name, () => { if (awaitingRefresh) void refresh(); });
    on(window, 'portal:identity-changed', () => { resetScope(); schedule(); });
    const syncFlags = event => { if (event.key === storageKey() || event.detail?.key === storageKey() || event.key === null) { flags = readFlags(); statusDirty = true; schedule(); if (popup) showPreview(popup.items, popup.trigger, popup.explicit); } };
    on(window, flagEvent, syncFlags); on(window, 'storage', syncFlags);
    // Content changes that affect row height are observed geometrically. Avoid
    // subtree character/attribute observation on streaming text and decorations.
    const mutation = new MutationObserver(invalidate);
    const resize = new ResizeObserver(entries => {
      let changed = false;
      for (const entry of entries) {
        const box = entry.borderBoxSize?.[0];
        const next = { width: box?.inlineSize ?? entry.contentRect.width, height: box?.blockSize ?? entry.contentRect.height };
        const previous = sizes.get(entry.target); sizes.set(entry.target, next);
        if (previous && (previous.width !== next.width || previous.height !== next.height)) changed = true;
      }
      if (changed) invalidate();
    });
    function updateObservation() {
      const active = visible(); rail.classList.toggle('is-paused', !active);
      if (active === observing) return;
      observing = active;
      if (active) {
        mutation.observe(viewport, { childList: true }); resize.observe(wrapper); resize.observe(viewport);
        observedList = null; observedRows.clear(); invalidate();
      } else {
        mutation.disconnect(); resize.disconnect(); observedRows.clear(); cancelAnimationFrame(frame); frame = 0;
        closePreview(); cancelPress();
      }
    }
    const intersection = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
      const next = entries[0]?.isIntersecting !== false;
      if (next === intersecting) return;
      intersecting = next; lost(); updateObservation(); if (visible()) void refresh();
    }) : null;
    intersection?.observe(wrapper); updateObservation();
    return { requestFor, toggle, isFollowed: key => flags.has(requestFor(key)), updateProgress,
      connectionLost: lost, setSearch: items => { search = Array.isArray(items) ? items : []; searchDirty = true; schedule(); },
      refresh: invalidate,
      setPresented(value) { if (presented === !!value) return; presented = !!value; lost(); updateObservation(); if (visible()) void refresh(); },
      destroy() { disposed = true; navigation++; cancelAnimationFrame(frame); cancelPress(); closePreview(); mutation.disconnect(); resize.disconnect(); intersection?.disconnect(); cleanups.forEach(fn => fn()); wrapper.before(viewport); wrapper.remove(); },
    };
  }
  window.PortalChatWorkspaceRoundRail = Object.freeze({ create });
})();
