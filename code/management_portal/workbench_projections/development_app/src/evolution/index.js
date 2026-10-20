import './style.css';
import { EvolutionSession } from './session.js';
import { baselineKey, semanticObject, linkedDirections, feedbackPayload, REFS } from './data.js';
import { el, btn, input, disclosure, narrative, foundations, replacePreservingFocus } from './view.js';
import { thumbnail, renderScene } from './scenes.js';

export async function mount(root, context) {
  const { sdk, resources } = context, app = el('section', null, 'evolutionApp'); root.replaceChildren(app);
  let selected = '', search = '', linked = [], incoming = null, retired = null, feedbackOpen = false, feedbackBusy = false;
  let message = '', epoch = 0, destroyed = false, hidden = false, timer, playing = false, started = 0;
  let watchStop, watchKey = '', watchEpoch = 0, stateWrites = Promise.resolve(), focusRevision = -1;
  const session = new EvolutionSession(resources, event => {
    if (event?.purge) { selected = ''; linked = []; retired = null; incoming = null; feedbackOpen = false; cancelPending(); persist(true); }
    if (['frozen', 'revoked'].includes(session.phase)) { pause(); cancelPending(); }
    render();
  });
  const current = () => session.facts?.directions.find(d => d.id === selected);
  const local = () => session.scenes[selected];
  const identity = object => JSON.stringify(object && [object.ref, object.kind, object.status, object.source]);
  function persist(clear = false) {
    const value = clear ? {} : { evolution: session.save(selected) };
    const snapshot = structuredClone(value); stateWrites = stateWrites.catch(() => {}).then(() => sdk.state(snapshot)).catch(() => {});
  }
  function cancelPending() {
    epoch++;
    if (!feedbackBusy) return;
    feedbackBusy = false; message = '情境已变化，旧确认已取消。草稿仍可继续编辑。';
    Promise.resolve(sdk.cancelFeedback?.()).catch(() => {});
  }
  function pause() {
    clearTimeout(timer);
    if (playing && local()) local().positionMs += Math.max(0, Math.round(performance.now() - started));
    playing = false;
  }
  function play() {
    if (!session.writable || !current()) return;
    cancelPending(); playing = true; started = performance.now();
    const frames = current().visual.storyboard, duration = Math.max(1000, frames[local().step].duration_ms || 4000);
    timer = setTimeout(() => {
      if (hidden || !session.writable) return pause();
      playing = false;
      if (local().step >= frames.length - 1) { local().positionMs = duration; persist(); render(); return; }
      session.change(selected, { type: 'step', value: local().step + 1 }); play();
    }, Math.max(50, duration - local().positionMs)); render();
  }
  function change(action, step = null, redraw = true) {
    if (!session.writable) return;
    pause(); cancelPending();
    if (!session.change(selected, action)) return;
    if (step !== null) session.change(selected, { type: 'step', value: step });
    persist(); if (redraw) render();
    else { const reserve = app.querySelector('[data-key="reserve"]'); if (reserve) reserve.disabled = !local().studio.slot || !local().studio.purpose.trim(); }
  }
  function pick(id, publish = true) {
    if (!session.writable || !session.facts.directions.some(d => d.id === id)) return;
    pause(); cancelPending(); selected = id; retired = null; feedbackOpen = false; message = ''; persist(); render();
    // A direction carries its independent module range into architecture; a
    // stale map binding must not publish a misleading current-map highlight.
    if (publish && session.facts.mapMatches) {
      incoming = semanticObject({ kind: 'direction', id }, session.facts);
      sdk.focus(incoming).catch(() => { message = '方向联动暂不可用。'; render(); });
    }
  }
  function applyFocus(object) {
    if (!session.facts || !object) return;
    linked = linkedDirections(object, session.facts);
    if (object.source === REFS.evolution && object.kind?.startsWith('direction:')) {
      const id = object.kind.slice(10), removed = session.facts.retired.find(d => d.id === id);
      if (removed) { pause(); cancelPending(); retired = removed; feedbackOpen = false; }
      else if (id !== selected && session.facts.directions.some(d => d.id === id)) {
        pause(); cancelPending(); selected = id; retired = null; feedbackOpen = false;
      }
    }
  }
  context.onFocus((object, meta = {}) => {
    if (destroyed || (Number.isFinite(meta.revision) && meta.revision < focusRevision)) return;
    if (Number.isFinite(meta.revision)) focusRevision = meta.revision;
    if (identity(incoming) !== identity(object)) { pause(); cancelPending(); }
    incoming = object; applyFocus(object); render();
  });
  async function observe() {
    if (hidden || destroyed || !session.facts) return;
    const refs = [REFS.evolution, REFS.modules, ...session.facts.plans.map(p => p.id)], key = refs.join('|');
    if (key === watchKey) return;
    const generation = ++watchEpoch; watchStop?.(); watchKey = key;
    try {
      const stop = await context.watch(refs, () => { pause(); cancelPending(); refresh(false); });
      if (generation !== watchEpoch || hidden || destroyed) stop(); else watchStop = stop;
    } catch { watchKey = ''; message = '实时更新暂不可用，反馈前会重新核对来源。'; render(); }
  }
  async function refresh(explicit, saved) {
    const focusKey = app.contains(document.activeElement) ? document.activeElement.dataset?.key : null;
    const scroll = { x: scrollX, y: scrollY }, previous = baselineKey(session.facts);
    if (session.facts) app.style.minHeight = `${app.getBoundingClientRect().height}px`;
    if (explicit) { pause(); cancelPending(); }
    const ok = await session.refresh(explicit, saved);
    if (ok) {
      if (!session.facts.directions.some(d => d.id === selected)) selected = session.facts.directions[0]?.id || '';
      if (saved?.selected && session.scenes[saved.selected]) selected = saved.selected;
      if (saved?.selected && session.facts.retired.some(d => d.id === saved.selected)) retired = session.facts.retired.find(d => d.id === saved.selected);
      applyFocus(incoming); persist(); render();
    }
    app.style.minHeight = '';
    if (previous && previous === baselineKey(session.facts)) {
      if (focusKey) [...app.querySelectorAll('[data-key]')].find(n => n.dataset.key === focusKey && !n.disabled)?.focus({ preventScroll: true });
      scrollTo(scroll.x, scroll.y);
    }
    await observe(); return ok;
  }
  async function openModule(id) {
    if (!session.writable || !session.facts.mapMatches) return;
    pause(); cancelPending();
    const object = semanticObject({ kind: 'module', id }, session.facts); if (!object) return;
    try { await sdk.focus(object); await sdk.openPanel('architecture'); }
    catch { message = '架构入口暂不可用，请稍后重试。'; render(); }
  }
  function gallery(facts) {
    const nav = el('nav', null, 'evo-gallery'); nav.setAttribute('aria-label', '探索未来方向');
    const shown = facts.directions.filter(d => !search || `${d.title} ${d.description} ${d.future_experience}`.includes(search));
    for (const d of shown) {
      const b = btn('', () => pick(d.id), `direction:${d.id}`, !session.writable); b.dataset.direction = d.id;
      b.setAttribute('aria-pressed', String(!retired && selected === d.id)); b.append(thumbnail(d.id), el('strong', d.title), el('span', d.description));
      if (linked.includes(d.id)) b.append(el('small', '与当前架构对象相关', 'evo-related')); nav.append(b);
    }
    if (!shown.length) nav.append(el('p', '没有匹配的方向。换一个词试试。'));
    return nav;
  }
  function render() {
    if (destroyed) return;
    replacePreservingFocus(app, () => {
      app.replaceChildren(); app.dataset.phase = session.phase; app.dataset.direction = selected; app.dataset.playing = playing;
      const head = el('header', null, 'evo-heading'); head.append(el('span', 'PORTAL / 未来演化', 'evo-eyebrow'), el('h1', 'Portal 还可以长成什么？'), el('p', '走进一个构想，亲手试一试，再决定值得向哪里生长。'));
      const tools = el('div', null, 'evo-toolbar');
      const filter = input('寻找方向', search, value => { search = value; render(); }, 'search'); tools.append(filter, btn('重新读取来源', () => refresh(true), 'reload', ['checking', 'loading', 'hidden'].includes(session.phase)));
      head.append(tools); app.append(head);
      const banner = el('p', session.message || (session.phase === 'checking' ? '正在核对来源…' : session.phase === 'loading' ? '读取未来构想…' : ''), 'evo-banner'); banner.setAttribute('role', 'status'); if (banner.textContent) app.append(banner);
      const facts = session.facts;
      if (!facts || ['loading', 'checking', 'hidden', 'revoked'].includes(session.phase)) return;
      app.append(gallery(facts));
      if (retired) {
        const retiredView = el('section', null, 'evo-retired'); retiredView.append(el('h2', `${retired.title} · 已退役`), el('p', retired.retired_reason), el('p', '此身份没有语义继承者。请从上方重新选择一个方向。')); app.append(retiredView); return;
      }
      const direction = current(), state = local(); if (!direction || !state) return;
      const frame = direction.visual.storyboard[state.step], main = el('article', null, 'evo-direction');
      const title = el('div', null, 'evo-title'); title.append(el('h2', direction.title), el('span', 'proposed · 未来构想', 'evo-badge')); main.append(title);
      const stage = el('section', null, 'evo-stage'); stage.setAttribute('aria-label', direction.visual.title);
      const stageHead = el('div', null, 'evo-stage-heading'); stageHead.append(el('strong', direction.visual.title), el('span', 'simulated · 本地概念体验', 'evo-badge')); stage.append(stageHead);
      const controls = el('div', null, 'evo-playback');
      controls.append(btn(playing ? '暂停演示' : '播放演示', () => { if (playing) { pause(); persist(); render(); } else play(); }, 'play', !session.writable),
        btn('上一幕', () => change({ type: 'step', value: state.step - 1 }), 'previous', !session.writable || !state.step),
        btn('下一幕', () => change({ type: 'step', value: state.step + 1 }), 'next', !session.writable || state.step >= direction.visual.storyboard.length - 1),
        btn('重新体验', () => change({ type: 'reset' }), 'reset', !session.writable)); stage.append(controls);
      const timeline = el('nav', null, 'evo-timeline'); timeline.setAttribute('aria-label', '分镜');
      direction.visual.storyboard.forEach((f, i) => { const b = btn(`${i + 1} · ${f.title}`, () => change({ type: 'step', value: i }), `frame:${f.id}`, !session.writable); b.setAttribute('aria-current', i === state.step ? 'step' : 'false'); timeline.append(b); }); stage.append(timeline);
      const surface = el('fieldset', null, 'evo-surface'); surface.disabled = !session.writable;
      const scene = el('div'); renderScene(scene, direction, state, change, action => change(action, null, false)); surface.append(scene); stage.append(surface);
      const caption = el('div', null, 'evo-caption'); caption.append(el('strong', frame.caption), el('p', `试一试：${frame.user_action}`, 'evo-hint'), disclosure('分镜说明', el('p', frame.scene), el('p', frame.animation))); stage.append(caption);
      stage.append(btn('讨论这个方向', () => { pause(); feedbackOpen = true; render(); app.querySelector('[data-key="feedback-text"]')?.focus(); }, 'discuss', !session.writable));
      const experience = el('div', null, 'evo-experience'); experience.append(stage, narrative(direction)); main.append(experience);
      if (!facts.mapMatches) main.append(el('p', '地图版本已变化，架构联动暂停；方向叙事与本地体验仍可阅读。', 'evo-map-status'));
      if (feedbackOpen) main.append(feedbackView(direction, state));
      main.append(foundations(direction, facts, openModule, session.writable)); app.append(main);
      if (message) app.append(el('p', message, 'evo-message'));
    });
  }
  function feedbackView(direction, state) {
    const form = el('section', null, 'evo-feedback'); form.setAttribute('aria-label', '方向反馈预览');
    form.append(el('h3', `讨论：${direction.title}`), el('p', `当前是「${direction.visual.storyboard[state.step].title}」的本地概念体验，反馈将交给团队门面。`));
    const text = input('反馈内容', session.drafts[selected] || '', value => { cancelPending(); session.drafts[selected] = value; persist(); const b = app.querySelector('[data-key="send"]'); if (b) b.disabled = !session.writable || !value.trim(); }, 'feedback-text', true);
    const payload = feedbackPayload(session.facts, direction, state, session.drafts[selected] || '');
    const preview = disclosure('核对对象、场景与来源版本', el('pre', JSON.stringify({ object: payload.object, anchor: JSON.parse(payload.value.anchor), scene: JSON.parse(payload.value.scene) }, null, 2)));
    form.append(text, preview, btn(feedbackBusy ? '等待确认…' : '确认对象并发送反馈', submitFeedback, 'send', !session.writable || feedbackBusy || !session.drafts[selected]?.trim()),
      btn('返回体验，保留草稿', () => { cancelPending(); feedbackOpen = false; render(); }, 'close-feedback'));
    form.onkeydown = event => { if (event.key === 'Escape') { event.preventDefault(); cancelPending(); feedbackOpen = false; render(); app.querySelector('[data-key="discuss"]')?.focus(); } };
    return form;
  }
  async function submitFeedback() {
    if (!session.writable || feedbackBusy || !session.drafts[selected]?.trim()) return;
    pause(); const id = selected, pinned = baselineKey(session.facts), token = ++epoch;
    const payload = feedbackPayload(session.facts, current(), local(), session.drafts[id].trim()); feedbackBusy = true; render();
    try {
      if (!await refresh(false) || token !== epoch || pinned !== baselineKey(session.facts) || selected !== id || hidden) return;
      incoming = payload.object; await sdk.focus(payload.object);
      if (token !== epoch || !session.writable || hidden || selected !== id) return;
      await sdk.feedback(payload.value);
      if (token !== epoch || hidden) return;
      session.drafts[id] = ''; message = '反馈已交给团队门面，请在对话中查看回复。'; persist();
    } catch (error) { if (token === epoch) message = String(error.code || error.message).includes('cancel') ? '已取消发送，草稿保留。' : '反馈暂未发送，请重新核对后重试。'; }
    finally { if (token === epoch) { feedbackBusy = false; render(); } }
  }
  let saved;
  try { const value = await sdk.state(); saved = (value?.value || value)?.evolution; const ctx = await sdk.context(); incoming = ctx.focus?.object; } catch { /* Optional resume state. */ }
  if (!context.signal.aborted) await refresh(true, saved);
  return {
    show() { if (!hidden || destroyed) return; hidden = false; session.active = true; refresh(false); },
    hide() { pause(); cancelPending(); persist(); hidden = true; ++watchEpoch; watchStop?.(); watchStop = null; watchKey = ''; session.hide(); },
    destroy() { pause(); cancelPending(); destroyed = true; ++watchEpoch; watchStop?.(); session.destroy(); app.remove(); },
  };
}
