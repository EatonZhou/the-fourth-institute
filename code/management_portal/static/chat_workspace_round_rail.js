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
  const flagIcon = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 17V3m0 1c4-3 6 3 10 0v7c-4 3-6-3-10 0" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  function previewContent(item) {
    const attachments = [], seen = new Set();
    const add = (kind, label, key) => { if (!seen.has(key)) { seen.add(key); attachments.push({ kind, label }); } };
    for (const file of item.input_media || item.attachments || []) {
      const mime = String(file.mime_type || file.mime || file.type || '');
      const kind = ['image', 'video', 'audio'].find(kind => mime.includes(kind)) || 'file';
      const label = String(file.name || file.title || file.filename || '').split(/[\\/]/).pop();
      add(kind, label || t({ image: '图片', video: '视频', audio: '音频', file: '附件' }[kind]), file.url || file.path || kind + ':' + label);
    }
    // Only this non-copying preview view removes renderer attachment tokens.
    // The message and its copy/quote/attachment contracts are not modified.
    const describedKinds = new Set(attachments.map(file => file.kind));
    const text = String(item.content || item.text || '').replace(/\[(image|file|audio|video):([^\]\r\n]+)\]/g, (_token, kind, source) => {
      if (!describedKinds.has(kind)) add(kind, t({ image: '图片', video: '视频', audio: '音频', file: '附件' }[kind]), source);
      return '';
    }).trim();
    return { text, attachments };
  }
  function shortTime(value) {
    if (!value) return '';
    const date = new Date(value); if (!Number.isFinite(date.getTime())) return '';
    const today = new Date().toDateString() === date.toDateString();
    return new Intl.DateTimeFormat(undefined, { ...(today ? {} : { month: 'numeric', day: 'numeric' }), hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }
  function create({ root, viewport, state, scope, keyFor, roleFor, reveal, scrollTo, loadOlder, revalidate }) {
    // This owner projects already accepted requests onto measured message rows.
    // It never derives work state from the workspace's global runtime/busy flag.
    const wrapper = el('div', 'chatRoundViewport');
    viewport.before(wrapper); wrapper.append(viewport);
    const rail = el('nav', 'chatRoundRail'); rail.setAttribute('aria-label', t('轮次导航'));
    const directory = button('chatRoundDirectory', t('浏览已加载轮次'), '⋮');
    const lane = el('div', 'chatRoundLane');
    const marks = el('div', 'chatRoundMarks'); lane.append(marks);
    const scroll = el('div', 'chatRoundScroll');
    const thumb = el('div', 'chatRoundThumb'); thumb.tabIndex = 0;
    thumb.setAttribute('role', 'scrollbar'); thumb.setAttribute('aria-orientation', 'vertical');
    thumb.setAttribute('aria-label', t('滚动对话')); thumb.setAttribute('aria-valuemin', '0');
    if (viewport.id) thumb.setAttribute('aria-controls', viewport.id);
    scroll.append(thumb); rail.append(directory, lane, scroll); wrapper.append(rail);
    const preview = el('section', 'chatRoundPreview'); preview.hidden = true;
    preview.setAttribute('role', 'dialog'); preview.setAttribute('aria-label', t('轮次预览')); wrapper.append(preview);
    const notice = el('div', 'chatRoundNotice'); notice.setAttribute('role', 'status'); wrapper.append(notice);
    const cleanups = [];
    const on = (node, type, fn, options) => { node.addEventListener(type, fn, options); cleanups.push(() => node.removeEventListener(type, fn, options)); };
    let disposed = false, frame = 0, currentScope = '', flags = new Set(), rows = [], search = [];
    let progress = new Map(), cursor = -1, uncertain = false, bindings = new Map(), signature = '';
    let awaitingRefresh = false, refreshEpoch = 0, refreshing = false;
    let layoutDirty = true, statusDirty = true, searchDirty = true, presented = true, intersecting = true, observing = false;
    let coords = new Map(), nodeForKey = new Map(), currentNode = null, tickSignature = '';
    let metrics = { total: 0, client: 0, track: 0, thumb: 0, hit: 26, available: 0 };
    const observedRows = new Set(), sizes = new WeakMap(); let observedList = null;
    let hoverTimer = 0, closeTimer = 0, press = null, suppressed = null, dragging = null, popup = null, navigation = 0, restoringFocus = false;
    let previewFrame = 0;
    const previewSizes = new WeakMap();
    const identity = () => String(window.PortalIdentity?.username?.() || '');
    const scopeKey = () => JSON.stringify([identity(), String(scope())]);
    const storageKey = () => window.PortalState.storage.key('portal.chat.round-flags.v1.' + scopeKey());
    const readFlags = () => {
      try { const value = JSON.parse(localStorage.getItem(storageKey()) || '[]'); return new Set(Array.isArray(value) ? value.filter(id => typeof id === 'string') : []); }
      catch { return new Set(flags); }
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
      currentScope = scopeKey(); flags = readFlags(); progress.clear(); cursor = -1;
      uncertain = false; awaitingRefresh = false; refreshing = false; refreshEpoch++; search = []; navigation++; closePreview(); cancelPress(); signature = ''; layoutDirty = statusDirty = searchDirty = true;
    }
    function toggle(key) {
      resetScope(); const request = requestFor(key); if (!request) return false;
      const before = readFlags(), next = new Set(before);
      if (next.has(request)) next.delete(request); else next.add(request);
      const value = JSON.stringify([...next].sort());
      try {
        if (!identity()) throw new Error(t('请先登录'));
        // PortalState's writers are intentionally best-effort. Personal flags
        // need a confirmed write, using the same installation-scoped key.
        localStorage.setItem(storageKey(), value);
        if (localStorage.getItem(storageKey()) !== value) throw new Error(t('保存未确认'));
        flags = next; notice.textContent = '';
        window.dispatchEvent(new CustomEvent(flagEvent, { detail: { key: storageKey() } }));
      } catch {
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
      for (const input of snapshot.input_states || []) next.set(String(input.message_id), { ...input });
      const relays = snapshot.relays || (snapshot.messages || []).filter(item => item.role === 'user' && item.relay)
        .map(item => ({ ...item.relay, message_id: item.message_id }));
      for (const relay of relays) next.set(String(relay.message_id), { ...relay });
      for (const [id, value] of next) {
        const previous = progress.get(id);
        const previousTime = Date.parse(previous?.updated_at), nextTime = Date.parse(value.updated_at);
        if (previous && ((Number.isFinite(previousTime) && Number.isFinite(nextTime) && nextTime < previousTime)
          || (nextCursor === cursor && terminal.has(previous.state) && !terminal.has(value.state)))) continue;
        progress.set(id, { ...value, verified: !awaitingRefresh });
      }
      cursor = nextCursor; uncertain = navigator.onLine === false || awaitingRefresh; statusDirty = true; schedule();
    }
    const statusFor = item => {
      const value = progress.get(String(item.message_id || keyFor(item)));
      return uncertain || !value || value.verified === false ? 'unknown' : String(value.state || 'unknown');
    };
    const textFor = item => previewContent(item).text;
    const description = row => [textFor(row.item).slice(0, 120) || t('附件消息'), t(labels[statusFor(row.item)] || '状态待确认'),
      flags.has(row.key) ? t('已关注') : ''].filter(Boolean).join(' · ');
    function cancelPress() {
      if (press) { clearTimeout(press.timer); press.node.classList.remove('is-pressing'); press = null; }
    }
    function closePreview(returnFocus = false) {
      clearTimeout(hoverTimer); clearTimeout(closeTimer);
      previewResize.disconnect(); cancelAnimationFrame(previewFrame); previewFrame = 0;
      const trigger = popup?.trigger; popup = null; preview.hidden = true; preview.replaceChildren();
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
    function showPreview(items, trigger, explicit = false) {
      if (disposed || dragging) return;
      const focusedFlag = preview.contains(document.activeElement) ? document.activeElement.dataset.roundFollow : '';
      const expanded = popup?.trigger === trigger ? popup.expanded : new Set();
      clearTimeout(hoverTimer); clearTimeout(closeTimer);
      previewResize.disconnect(); cancelAnimationFrame(previewFrame); previewFrame = 0;
      popup = { items, trigger, explicit, expanded, contents: [] }; preview.replaceChildren(); preview.hidden = false;
      const head = el('div', 'chatRoundPreviewHead', window.PortalI18n.text`${items.length} 条消息`);
      const close = button('portalButton is-icon is-quiet', t('关闭'), '×'); close.onclick = () => closePreview(true); head.append(close); preview.append(head);
      if (trigger === directory && state.messageRenderOptions?.history?.can_load_older) {
        const earlier = button('portalButton is-quiet', t('加载更早消息'));
        earlier.onclick = async () => {
          const expected = scopeKey(); earlier.disabled = true;
          try { await loadOlder(); if (!disposed && expected === scopeKey()) { measure(); showPreview(rows, directory, true); } }
          catch (err) { if (!disposed && expected === scopeKey()) { notice.textContent = t('加载失败') + ': ' + err.message; earlier.disabled = false; } }
        }; preview.append(earlier);
      }
      const list = el('div', 'chatRoundPreviewList');
      for (const row of items) {
        const card = el('article', 'chatRoundPreviewItem');
        const content = previewContent(row.item), main = el('div', 'chatRoundPreviewContent');
        const meta = el('div', 'chatRoundPreviewMeta');
        meta.append(el('time', '', shortTime(row.item.created_at)));
        const status = statusFor(row.item);
        if (status !== 'unknown' || uncertain) {
          const stateLabel = el('span', 'chatRoundPreviewState', t(labels[status] || '状态待确认'));
          stateLabel.classList.toggle('is-running', status === 'running'); meta.append(stateLabel);
        }
        const jump = button('chatRoundPreviewJump', t('定位消息') + ' · ' + description(row), content.text || t('附件消息'));
        jump.onclick = () => locate(row.key); main.append(meta, jump);
        if (content.attachments.length) {
          const icon = el('span', 'chatRoundAttachmentIcon', { image: '▧', video: '▷', audio: '♪', file: '▤' }[content.attachments[0].kind]);
          icon.setAttribute('aria-hidden', 'true'); card.append(icon);
          main.append(el('div', 'chatRoundAttachments', content.attachments.map(file => file.label).join(' · ')));
        }
        if (content.text) {
          popup.contents.push({ key: row.key, jump, meta, expand: null });
        }
        card.append(main);
        const flag = button('chatRoundFollow', t(flags.has(row.key) ? '取消Flag' : 'Flag'), ''); flag.innerHTML = flagIcon;
        const request = requestFor(row.key);
        if (request) {
          flag.dataset.roundFollow = row.key;
          flag.setAttribute('aria-label', t(flags.has(request) ? '取消Flag' : 'Flag'));
          flag.setAttribute('aria-pressed', String(flags.has(request))); flag.onclick = () => toggle(row.key); card.append(flag);
        }
        list.append(card);
      }
      if (!items.length) list.append(el('p', '', t('暂无已加载轮次')));
      preview.append(list); measurePreview();
      for (const { jump } of popup.contents) previewResize.observe(jump);
      if (focusedFlag) preview.querySelector(`[data-round-follow="${CSS.escape(focusedFlag)}"]`)?.focus({ preventScroll: true });
      else if (explicit) (preview.querySelector('.chatRoundPreviewJump') || close).focus({ preventScroll: true });
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
      directory.setAttribute('aria-label', t(state.messageRenderOptions?.history?.can_load_older ? '仅显示已加载范围；可加载更早消息' : '浏览已加载轮次'));
      directory.textContent = state.messageRenderOptions?.history?.can_load_older ? '↑' : '⋮';
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
        if (followed !== node.classList.contains('is-followed')) {
          if (followed) { const flag = el('span', 'chatRoundFlag'); flag.innerHTML = flagIcon; node.append(flag); }
          else node.querySelector('.chatRoundFlag')?.remove();
          node.classList.toggle('is-followed', followed);
        }
        node.setAttribute('aria-label', description(row));
      });
    }
    function pointerRows(node, event) {
      // Keep every marker at its measured location. Overlapping hit areas use
      // a local list only for pointer disambiguation; keyboard targets stay 1:1.
      const row = bindings.get(node); if (!row) return [];
      const pixel = event.clientY - lane.getBoundingClientRect().top;
      let low = 0, high = rows.length;
      while (low < high) { const middle = (low + high) >>> 1; if (rows[middle].pixel < pixel - metrics.hit / 2) low = middle + 1; else high = middle; }
      const nearby = [];
      for (let index = low; index < rows.length && rows[index].pixel <= pixel + metrics.hit / 2; index++) nearby.push(rows[index]);
      return nearby.length ? nearby : [row];
    }
    function paintScroll() {
      const top = viewport.scrollTop, range = Math.max(0, metrics.total - metrics.client);
      thumb.style.top = (range ? (metrics.track - metrics.thumb) * top / range : 0) + 'px';
      thumb.setAttribute('aria-valuenow', String(Math.round(top)));
      // Cached sorted coordinates make ordinary reading O(log rounds), with
      // only the previous/new reading indicators receiving a DOM update.
      let low = 0, high = rows.length;
      while (low < high) { const middle = (low + high) >>> 1; if (rows[middle].y <= top + 24) low = middle + 1; else high = middle; }
      const next = nodeForKey.get(rows[Math.max(0, low - 1)]?.key) || null;
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
    on(directory, 'click', () => showPreview(rows, directory, true));
    on(marks, 'click', event => {
      const node = event.target.closest('.chatRoundNode'); if (!node) return;
      if (suppressed === node) { suppressed = null; return; }
      const row = bindings.get(node); if (!row) return;
      const items = event.detail === 0 ? [row] : pointerRows(node, event);
      if (items.length > 1) showPreview(items, node, true); else void locate(items[0].key);
    });
    on(marks, 'pointerover', event => {
      if (event.pointerType === 'touch') return;
      const node = event.target.closest('.chatRoundNode'); if (!node || node.contains(event.relatedTarget)) return;
      clearTimeout(closeTimer); clearTimeout(hoverTimer);
      const items = pointerRows(node, event);
      hoverTimer = setTimeout(() => showPreview(items, node), 220);
    });
    on(marks, 'pointerout', event => {
      if (event.target.closest('.chatRoundNode')?.contains(event.relatedTarget)) return;
      cancelPress(); clearTimeout(hoverTimer); closeTimer = setTimeout(() => { if (!popup?.explicit) closePreview(); }, 240);
    });
    on(preview, 'pointerenter', () => clearTimeout(closeTimer));
    on(preview, 'pointerleave', () => { closeTimer = setTimeout(() => { if (!popup?.explicit && !preview.contains(document.activeElement)) closePreview(); }, 240); });
    on(marks, 'focusin', event => { const node = event.target.closest('.chatRoundNode'); const row = bindings.get(node); if (row && !restoringFocus) showPreview([row], node); });
    on(marks, 'pointerdown', event => {
      const node = event.target.closest('.chatRoundNode');
      cancelPress(); suppressed = null; if (!bindings.has(node) || event.button !== 0) return;
      const items = pointerRows(node, event);
      node.classList.add('is-pressing');
      press = { node, x: event.clientX, y: event.clientY, timer: setTimeout(() => {
        suppressed = node;
        if (items.length > 1) showPreview(items, node, true); else toggle(items[0].key);
        cancelPress();
      }, 550) };
    });
    on(window, 'pointermove', event => {
      if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > 7) cancelPress();
      if (!dragging || event.pointerId !== dragging.id) return;
      const range = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
      scrollTo(Math.max(0, Math.min(range, dragging.top + (event.clientY - dragging.y) * range / Math.max(1, scroll.clientHeight - thumb.offsetHeight))));
    });
    on(window, 'pointerup', () => { cancelPress(); dragging = null; rail.classList.remove('is-dragging'); });
    on(window, 'pointercancel', () => { cancelPress(); dragging = null; suppressed = null; rail.classList.remove('is-dragging'); });
    on(thumb, 'pointerdown', event => {
      if (event.button !== 0) return; event.preventDefault(); closePreview(); cancelPress();
      dragging = { id: event.pointerId, y: event.clientY, top: viewport.scrollTop }; thumb.setPointerCapture(event.pointerId); rail.classList.add('is-dragging');
    });
    on(scroll, 'pointerdown', event => { if (event.target !== scroll) return; closePreview(); scrollTo((event.clientY - scroll.getBoundingClientRect().top) / scroll.clientHeight * viewport.scrollHeight - viewport.clientHeight / 2); });
    on(wrapper, 'keydown', event => {
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
    on(document, 'pointerdown', event => { if (!preview.contains(event.target) && !rail.contains(event.target)) closePreview(); });
    on(document, 'focusin', event => { if (!preview.contains(event.target) && !rail.contains(event.target)) closePreview(); });
    on(viewport, 'scroll', () => { cancelPress(); if (!popup?.explicit) closePreview(); schedule(); }, { passive: true });
    const lost = () => { uncertain = true; awaitingRefresh = true; for (const value of progress.values()) value.verified = false; cancelPress(); statusDirty = true; if (!visible()) paintStatus(); schedule(); };
    const refresh = async () => {
      if (!visible() || refreshing || !revalidate) return;
      lost(); refreshing = true;
      const epoch = ++refreshEpoch, expected = scopeKey();
      try {
        const snapshot = await revalidate();
        if (disposed || epoch !== refreshEpoch || expected !== scopeKey()) return;
        if (snapshot) { awaitingRefresh = false; updateProgress(snapshot); }
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
