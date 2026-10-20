import { renderMarkdown } from '../shared/markdown.js';
import { lineAnchor, REFS } from './data.js';

export function el(tag, text, cls) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; }
export function btn(text, fn, key, disabled = false) {
  const node = el('button', text, 'portalButton'); node.type = 'button'; node.disabled = disabled;
  if (key) node.dataset.key = key; node.onclick = fn; return node;
}
export function field(label, node) { const wrap = el('label', null, 'evo-field'); wrap.append(el('span', label), node); return wrap; }
export function input(label, value, fn, key, multiline = false) {
  const node = el(multiline ? 'textarea' : 'input'); node.className = 'portalField'; node.value = value; node.dataset.key = key;
  node.setAttribute('aria-label', label); node.maxLength = multiline ? 4000 : 300; node.oninput = () => fn(node.value); return field(label, node);
}
export function disclosure(title, ...children) { const d = el('details'); d.append(el('summary', title), ...children); return d; }

export function sourceView(title, ref, version, text) {
  const d = disclosure(title, el('p', ref, 'evo-source'), el('p', `来源版本：${version || '未知'}`, 'evo-source'));
  const pre = el('pre', text || '正文不可读取。', 'evo-source-body'); d.append(pre); return d;
}

export function narrative(direction) {
  const root = el('aside', null, 'evo-narrative'); root.setAttribute('aria-label', '方向说明');
  for (const [key, label] of [['concept', '构想'], ['future_experience', '未来体验'], ['why_feasible', '为什么可行'], ['first_step', '第一步']]) {
    const section = el('section'); section.dataset.narrative = key; section.append(el('h3', label), el('p', direction[key] || '尚未登记。')); root.append(section);
  }
  return root;
}

export function foundations(direction, facts, openModule, writable) {
  const root = el('div', null, 'evo-foundations');
  const modules = el('div', null, 'evo-modules');
  modules.append(el('p', facts.mapMatches ? '这些当前能力为构想提供起点。' : '当前架构地图不可用或映射声明不匹配，联动暂停。', 'evo-map-status'));
  for (const id of direction.modules || []) {
    const m = facts.modules.find(m => m.id === id), row = el('section'); row.dataset.module = id;
    row.append(btn(m?.title || id, () => openModule(id), `module:${id}`, !writable || !facts.mapMatches || !m || direction.module_mapping === 'needs_review'), el('p', direction.module_roles?.[id] || '未登记扩展位置。'));
    modules.append(row);
  }
  const plans = el('div', null, 'evo-plans');
  if (!direction.plans?.length) plans.append(el('p', '这个方向还没有挂接实施 Plan。可以先讨论构想与第一步。'));
  for (const id of direction.plans || []) {
    const plan = facts.plans.find(p => p.id === id), body = facts.documents[id];
    if (!plan || !body) { plans.append(el('p', '关联的实施资料暂不可读取。')); continue; }
    const content = el('article'); content.dataset.plan = id;
    content.append(el('p', 'proposed · 仅提供部分基础'), el('p', direction.plan_contributions?.[id] || '尚未登记贡献范围。'), el('p', `演化登记版本 ${plan.source?.version || '未知'} · 已读正文版本 ${body.version || '未知'}`, 'evo-source'));
    for (const section of body.sections) {
      const row = el('section'); row.append(el('h4', section.label));
      if (section.available) {
        const md = el('div'); renderMarkdown(md, section.markdown); row.append(md, el('p', `${id}#L${section.line} · ${section.heading} · 正文版本 ${body.version}`, 'evo-source'));
      } else row.append(el('p', '正文未登记可对应的独立章节。'));
      content.append(row);
    }
    content.append(sourceView('阅读完整 Plan 与来源', id, body.version, body.text));
    plans.append(disclosure(plan.title, content));
  }
  const ref = lineAnchor(REFS.evolution, direction.id, facts.texts.evolution);
  const evidence = { id: direction.id, title: direction.title, status: direction.status,
    concept: direction.concept, future_experience: direction.future_experience,
    why_feasible: direction.why_feasible, first_step: direction.first_step, visual: direction.visual };
  root.append(disclosure('它从哪些当前能力长出来', modules), disclosure('实施基础与 Plan', plans),
    sourceView('核对方向与分镜来源', ref || REFS.evolution, facts.sources.evolution.version, JSON.stringify(evidence, null, 2)),
    sourceView('核对当前架构地图', facts.sources.modules.ref, facts.sources.modules.version, facts.texts.modules));
  return root;
}

// Keep real DOM focus and caret through local redraws; keys identify interactions,
// not their ordinal positions. No page-level focus reset on source/watch events.
export function replacePreservingFocus(root, render) {
  const active = document.activeElement, key = root.contains(active) ? active.dataset?.key : null;
  const selection = active && 'selectionStart' in active ? [active.selectionStart, active.selectionEnd] : null;
  const opened = [...root.querySelectorAll('details[open]')].map(n => n.querySelector('summary')?.textContent);
  render();
  for (const d of root.querySelectorAll('details')) if (opened.includes(d.querySelector('summary')?.textContent)) d.open = true;
  if (!key) return;
  const next = [...root.querySelectorAll('[data-key]')].find(n => n.dataset.key === key);
  if (next && !next.disabled) { next.focus({ preventScroll: true }); if (selection && next.setSelectionRange) next.setSelectionRange(...selection); }
}
