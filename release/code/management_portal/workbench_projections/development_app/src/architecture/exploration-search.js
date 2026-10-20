const normalize = value => String(value || '').normalize('NFKC').toLocaleLowerCase().trim();
const labels = { module: '模块', concept: '内部组件', external: '外部边界', protocol: '协议' };

/** Only an admitted, currently authorized bundle may be indexed. */
export function explorationSearchIndex(model, designTitles = {}) {
  const objects = model.objects.map(object => ({ type: 'object', id: object.id,
    label: object.title, category: labels[object.kind], description: object.responsibility,
    route: { kind: object.kind === 'protocol' ? 'protocol' : 'composition', objectId: object.id },
    terms: [object.title, object.id, object.responsibility, ...(object.search_aliases || [])].join(' ') }));
  const designs = new Map();
  for (const evidence of model.evidence) {
    if (evidence.kind !== 'design') continue;
    const key = evidence.frozen.address;
    const label = designTitles[key] || evidence.symbol.replace(/^#+\s*/, '') || evidence.claim;
    if (!designs.has(key)) designs.set(key, { type: 'design', id: evidence.id, label,
      category: '设计依据', description: evidence.claim, terms: label });
    designs.get(key).terms += ` ${evidence.symbol} ${evidence.claim} ${evidence.address}`;
  }
  return [...objects, ...designs.values()].map(row => ({ ...row, terms: normalize(row.terms) }));
}

export function searchExploration(index, query) {
  const words = normalize(query).split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return index.filter(row => words.every(word => row.terms.includes(word)))
    .sort((a, b) => Number(normalize(b.label).startsWith(words[0])) - Number(normalize(a.label).startsWith(words[0]))
      || a.label.localeCompare(b.label)).slice(0, 40);
}

export function createExplorationSearch(root, { authorize, isCurrent, onSelect, onEnter, onSource, onError }) {
  const doc = root.ownerDocument, win = doc.defaultView;
  const element = (tag, text, cls) => { const el = doc.createElement(tag); if (text) el.textContent = text; if (cls) el.className = cls; return el; };
  const trigger = element('button', '搜索', 'ade-search-trigger'); trigger.type = 'button';
  trigger.setAttribute('aria-label', '搜索架构'); trigger.setAttribute('aria-expanded', 'false');
  const panel = element('section', null, 'ade-search-panel'); panel.hidden = true; panel.setAttribute('aria-label', '架构搜索');
  const input = element('input'); input.type = 'search'; input.placeholder = '模块、协议或设计名称'; input.setAttribute('aria-label', '搜索架构对象');
  const help = element('p', 'Enter 选择，Shift+Enter 直接进入；方向键浏览结果，Escape 收起。');
  const status = element('p'); status.setAttribute('role', 'status');
  const results = element('div', null, 'ade-search-results');
  panel.append(input, help, status, results); root.append(trigger, panel);
  let generation = 0, disposed = false, current = null, index = [], matches = [];
  const cleanups = [];
  const listen = (el, event, fn, options) => { el.addEventListener(event, fn, options); cleanups.push(() => el.removeEventListener(event, fn, options)); };
  function close(restoreFocus = false) {
    generation++; panel.hidden = true; trigger.setAttribute('aria-expanded', 'false'); current = null;
    index = matches = []; input.value = ''; results.replaceChildren(); status.textContent = '';
    if (restoreFocus && !disposed) trigger.focus({ preventScroll: true });
  }
  async function activate(row, enter = false) {
    const bundle = current, token = generation;
    if (!bundle || !isCurrent(bundle)) return close();
    try {
      // Authorization results are not cached with the search index.
      const checked = await authorize();
      if (disposed || panel.hidden || token !== generation || checked !== bundle || !isCurrent(bundle)) return;
      close();
      if (row.type === 'design') await onSource(row.id);
      else if (enter) await onEnter(row.route);
      else await onSelect(row.id);
    } catch (error) { close(); onError?.(error); }
  }
  function render() {
    results.replaceChildren();
    matches = searchExploration(index, input.value);
    status.textContent = !input.value.trim() ? '输入名称查找本批可读对象和设计依据。'
      : matches.length ? `${matches.length} 项匹配${matches.length === 40 ? '（最多显示 40 项，可继续输入缩小范围）' : ''}` : '本批可读内容中没有匹配项。';
    for (const row of matches) {
      const item = element('div', null, 'ade-search-result'); item.dataset.resultId = row.id;
      item.append(element('small', row.category));
      const select = element('button', row.label); select.type = 'button'; select.dataset.searchPrimary = 'true';
      select.setAttribute('aria-label', (row.type === 'design' ? '打开设计依据：' : '选择：') + row.label);
      select.addEventListener('click', () => activate(row)); item.append(select);
      if (row.type === 'object') {
        const enter = element('button', '进入'); enter.type = 'button'; enter.setAttribute('aria-label', '进入：' + row.label);
        enter.addEventListener('click', () => activate(row, true)); item.append(enter);
      }
      results.append(item);
    }
  }
  async function open() {
    if (disposed) return;
    if (!panel.hidden) return close();
    const token = ++generation; panel.hidden = false; trigger.setAttribute('aria-expanded', 'true');
    status.textContent = '正在核验本批来源与权限…'; input.focus();
    try {
      const bundle = await authorize();
      if (disposed || token !== generation || panel.hidden || !isCurrent(bundle)) return;
      current = bundle; index = explorationSearchIndex(bundle.model, bundle.designTitles); render();
    } catch (error) { close(); onError?.(error); }
  }
  listen(trigger, 'click', open); listen(input, 'input', render);
  listen(panel, 'keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); return; }
    const primary = [...results.querySelectorAll('[data-search-primary]')];
    if (event.target === input && event.key === 'Enter' && matches[0]) { event.preventDefault(); activate(matches[0], event.shiftKey); }
    if (['ArrowDown', 'ArrowUp'].includes(event.key) && primary.length) {
      event.preventDefault(); const at = primary.indexOf(doc.activeElement);
      primary[at < 0 ? (event.key === 'ArrowDown' ? 0 : primary.length - 1)
        : (at + (event.key === 'ArrowDown' ? 1 : -1) + primary.length) % primary.length].focus();
    }
  });
  listen(doc, 'pointerdown', event => { if (!panel.hidden && !panel.contains(event.target) && !trigger.contains(event.target)) close(); }, true);
  listen(root, 'focusout', event => { if (!panel.hidden && event.relatedTarget && !root.contains(event.relatedTarget)) close(); });
  listen(win, 'blur', () => close());
  return { close, destroy() { if (disposed) return; close(); disposed = true; cleanups.forEach(fn => fn()); root.replaceChildren(); } };
}
