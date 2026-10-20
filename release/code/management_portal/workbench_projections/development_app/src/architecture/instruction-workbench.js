import './instruction-workbench.css';
import {readContent, visibleInstructions, restoreState, declarationExplanation, declarationOwners, lessonFor,
  experiment, representativeFor, sourceForLesson, purposeSummary} from './instruction-workbench.model.js';

/** An explanation view, with no transport or business execution capability. */
export function createInstructionWorkbench(root, {subject, content, onSelect, onNavigate, onSource, onChange, onReturn, signal} = {}) {
  const doc = root.ownerDocument;
  let disposed = false, hidden = false, data, state, lastSelection = '';
  const listeners = new AbortController();
  const node = (tag, cls = '', text) => {
    const n = doc.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = String(text); return n;
  };
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: listeners.signal});
  const button = (label, callback, cls = '') => {
    const b = node('button', `portalButton ${cls}`, label); b.type = 'button';
    b.onclick = () => { if (!disposed && !hidden) callback(); }; return b;
  };
  const host = node('section', 'instruction-workbench'); host.dataset.testid = 'instruction-workbench';
  host.setAttribute('aria-label', 'HATG 指令工作台');
  const status = node('p', 'iw-status'); status.setAttribute('role', 'status');
  const invoke = (callback, value) => {
    if (disposed || hidden || typeof callback !== 'function') return;
    try { Promise.resolve(callback(value)).catch(error => { if (!disposed) status.textContent = error.message || '无法打开目标'; }); }
    catch (error) { status.textContent = error.message || '无法打开目标'; }
  };
  const finish = () => {
    if (disposed) return; disposed = true; listeners.abort();
    signal?.removeEventListener('abort', finish); host.remove();
  };
  const api = {
    show() { if (!disposed) { hidden = false; host.hidden = false; } },
    hide() { if (!disposed) { hidden = true; host.hidden = true; } },
    destroy: finish,
    getViewState() { return state ? {...state} : {}; },
    restoreViewState(value) { if (!disposed && data) { state = restoreState(data, value); render(); } },
  };
  if (signal?.aborted) { disposed = true; return api; }
  signal?.addEventListener('abort', finish, {once: true});
  root.append(host);
  try {
    if (!subject?.ref || !subject.kind || !subject.source || !subject.observed_at || subject.status !== 'current') throw new Error('需要父视图提供当前协议身份');
    data = readContent(content);
  } catch (error) {
    host.append(node('h2', '', '无法读取指令目录'), node('p', '', error.message)); return api;
  }
  if (data.lessonWarning) status.textContent = data.lessonWarning;
  state = restoreState(data);
  const header = node('header', 'iw-header');
  if (onReturn) header.append(button('返回架构', () => invoke(onReturn, api.getViewState())));
  header.append(node('h2', '', 'HATG 指令'), node('p', '', `${data.categories.length} 类 · ${data.catalog.instructions.length} 条指令`));
  const workspace = node('div', 'iw-workspace');
  const categories = node('nav', 'iw-categories'); categories.setAttribute('aria-label', '指令类别');
  const catalog = node('section', 'iw-catalog'); catalog.setAttribute('aria-label', '指令目录');
  const search = node('input', 'iw-search'); search.type = 'search'; search.placeholder = '搜索指令、用途、参数'; search.setAttribute('aria-label', '搜索指令');
  const mode = node('select'); mode.setAttribute('aria-label', '指令行为');
  for (const [value, title] of [['', '全部行为'], ['read', '只读'], ['write', '改变状态'], ['stream', '持续观察']]) {
    const option = node('option', '', title); option.value = value; mode.append(option);
  }
  const count = node('p', 'iw-count'); count.setAttribute('aria-live', 'polite');
  const entries = node('div', 'iw-entries');
  const detail = node('article', 'iw-detail'); detail.setAttribute('aria-label', '指令解释');
  catalog.append(search, mode, count, entries); workspace.append(categories, catalog, detail); host.append(header, status, workspace);
  listen(search, 'input', () => { state = restoreState(data, {...state, query: search.value, step: 0}); render(); });
  listen(mode, 'change', () => { state = restoreState(data, {...state, mode: mode.value, step: 0}); render(); });

  function notify() {
    const schema = data.catalog.schemas[state.instruction];
    if (state.instruction && lastSelection !== state.instruction) {
      lastSelection = state.instruction; invoke(onSelect, {instruction: state.instruction, category: schema.category});
    }
    invoke(onChange, {status: 'simulated', protocol_id: 'hatg-isa', instruction: state.instruction,
      viewState: api.getViewState(), catalog_ref: data.provenance.catalog_ref, commit: data.provenance.commit});
  }
  function choose(instruction) {
    state = restoreState(data, {...state, instruction, variant: '', step: 0}); render();
  }
  function sourceButton(label, ref) {
    const b = button(label, () => invoke(onSource, ref), 'iw-source');
    b.disabled = typeof onSource !== 'function' || !ref; return b;
  }
  function focusedControl(scope) {
    return scope.contains(doc.activeElement) ? doc.activeElement.dataset.iwFocus : null;
  }
  function restoreControlFocus(scope, key) {
    if (!key || disposed || hidden) return;
    const replacement = [...scope.querySelectorAll('[data-iw-focus]')].find(control => control.dataset.iwFocus === key);
    replacement?.focus({preventScroll: true});
  }
  function render() {
    if (disposed) return;
    // Only replace focus that this redraw removes. Persistent search/filter
    // controls and focus outside this workbench keep their current position.
    const focusKey = focusedControl(workspace);
    search.value = state.query; mode.value = state.mode; categories.replaceChildren(); entries.replaceChildren();
    const all = [{id: '', title: '全部类别', count: data.catalog.instructions.length}, ...data.categories];
    for (const category of all) {
      const b = button(`${category.title} · ${category.count}`, () => {
        state = restoreState(data, {...state, category: category.id, step: 0}); render();
      }, 'iw-category'); b.dataset.iwFocus = `category:${category.id}`;
      b.setAttribute('aria-pressed', String(state.category === category.id)); categories.append(b);
    }
    const shown = visibleInstructions(data, state); count.textContent = `${shown.length} 条匹配`;
    for (const item of shown) {
      const b = button('', () => choose(item.instruction), 'iw-entry'); b.dataset.instruction = item.instruction;
      b.dataset.iwFocus = `instruction:${item.instruction}`;
      b.setAttribute('aria-pressed', String(state.instruction === item.instruction));
      b.append(node('strong', '', item.instruction), node('span', '', purposeSummary(item.description))); entries.append(b);
    }
    if (!shown.length) entries.append(node('p', '', '没有匹配的指令。清空搜索或更换类别。'));
    renderDetail(); restoreControlFocus(workspace, focusKey); notify();
  }
  function renderDetail() {
    detail.replaceChildren();
    const schema = data.catalog.schemas[state.instruction]; if (!schema) return;
    const explanation = declarationExplanation(schema), lesson = lessonFor(schema.instruction, data.provenance);
    const title = node('h3', 'iw-instruction-name', schema.instruction); detail.append(title);
    const badges = node('p', 'iw-badges');
    badges.append(node('span', '', schema.category_title || schema.category),
      node('span', '', schema.streaming ? '持续观察' : schema.mutates ? '改变状态' : '只读'));
    detail.append(badges, node('p', 'iw-purpose', schema.description));
    for (const owner of declarationOwners(schema)) {
      const ownership = node('div', 'iw-ownership');
      ownership.append(node('span', '', owner.condition || '调用入口'), node('span', 'iw-arrow', '→'), node('strong', '', owner.title));
      const route = data.owner_routes?.[owner.owner];
      if (route?.kind && route.objectId) ownership.append(button(`在架构中定位${owner.condition ? ' ' + owner.title : ''}`, () => invoke(onNavigate, {...route})));
      detail.append(ownership);
    }
    detail.append(node('p', '', explanation.route));
    const lab = node('section', 'iw-lab'); lab.dataset.role = 'experiment'; detail.append(lab);
    if (lesson) {
      renderLab(lab, schema, lesson);
    } else {
      lab.append(node('h4', '', '这条调用怎样被解释'), node('p', '', explanation.target), node('p', '', explanation.effect));
      const rep = representativeFor(data, schema.category);
      if (rep) lab.append(button(`看本类具体实验：${rep}`, () => {
        state = restoreState(data, {category: schema.category, instruction: rep}); render();
      }));
    }
    const params = node('section', 'iw-parameters'); params.append(node('h4', '', '准备哪些信息'));
    if (!schema.parameters.length) params.append(node('p', '', '不需要指令参数。真实调用者身份由入口提供。'));
    for (const parameter of schema.parameters) {
      const row = node('div', 'iw-parameter');
      row.append(node('code', '', parameter.name), node('small', '', `${parameter.required ? '必填' : '可选'} · ${parameter.type}`),
        node('p', '', lesson?.parameters[parameter.name] || parameter.description || (parameter.name === schema.target ? '定位本条指令作用的主对象。' : '含义由本条指令及其领域定义解释。')));
      if (parameter.enum) row.append(node('p', 'iw-enum', `可选：${parameter.enum.join(' · ')}`));
      if (parameter.minimum != null || parameter.maximum != null) row.append(node('p', 'iw-enum', `范围：${parameter.minimum ?? '不限'} 至 ${parameter.maximum ?? '不限'}`));
      params.append(row);
    }
    if (schema.one_of?.length) params.append(node('p', 'iw-condition', `只选一组：${schema.one_of.map(group => group.join(' + ')).join(' 或 ')}`));
    const result = node('section', 'iw-result-contract'); result.append(node('h4', '', '回执能告诉你什么'), node('p', '', explanation.effect));
    for (const field of schema.result) result.append(node('p', '', `${field.name}（${field.type}）：${field.description || '由拥有服务返回的声明字段。'}`));
    detail.append(params, result);
    const sources = node('details', 'iw-evidence'); sources.append(node('summary', '', '正式定义与解释依据'));
    sources.append(node('p', '', '指令说明与本地教学示例；源码链接读取当前工作区。'),
      sourceButton('打开指令目录', data.provenance.catalog_ref));
    const ref = lesson ? sourceForLesson(schema.instruction) : '';
    if (ref) sources.append(sourceButton('查看当前实现来源', ref));
    const raw = node('details'); raw.append(node('summary', '', '查看完整 schema'), node('pre', '', JSON.stringify(schema, null, 2)));
    sources.append(raw); detail.append(sources);
  }
  function renderLab(lab, schema, lesson) {
    // Preserve the keyboard position across this local redraw. Initial async
    // rendering and pointer interactions elsewhere must not take focus.
    const focusKey = focusedControl(lab);
    lab.replaceChildren(node('h4', '', lesson.question), node('p', '', lesson.meaning));
    lab.append(node('p', 'iw-simulated', '本地解释 · simulated · 示例值，不执行指令'));
    const variants = node('div', 'iw-variants'); variants.setAttribute('role', 'group'); variants.setAttribute('aria-label', '实验条件');
    for (const variant of lesson.variants) {
      const b = button(variant.label, () => { state.variant = variant.id; state.step = 0; renderLab(lab, schema, lesson); notify(); });
      b.dataset.iwFocus = `variant:${variant.id}`;
      b.setAttribute('aria-pressed', String(state.variant === variant.id)); variants.append(b);
    }
    lab.append(variants);
    const view = experiment(schema.instruction, state.variant, state.step === 1);
    if (!view) return;
    lab.append(renderMechanism(view, schema));
    const controls = node('div', 'iw-controls');
    const reveal = button(state.step ? '重新观察' : '观察结果', () => { state.step = state.step ? 0 : 1; renderLab(lab, schema, lesson); notify(); });
    reveal.dataset.role = 'reveal'; controls.append(reveal); lab.append(controls);
    reveal.dataset.iwFocus = 'reveal';
    const cause = node('p', 'iw-cause', view.explanation); cause.setAttribute('aria-live', 'polite'); lab.append(cause);
    if (view.result) {
      const receipt = node('details', 'iw-receipt-fields'); receipt.append(node('summary', '', '本例回执要点（示意）'));
      receipt.append(node('pre', '', JSON.stringify(view.result, null, 2))); lab.append(receipt);
    }
    restoreControlFocus(lab, focusKey);
  }
  function renderMechanism(view, schema) {
    const mechanism = node('div', `iw-mechanism iw-${view.visual}`); mechanism.dataset.visual = view.visual;
    const block = (label, items, cls = '') => {
      const section = node('section', cls); section.append(node('p', 'iw-caption', label));
      for (const text of items) section.append(node('div', 'iw-token', text)); return section;
    };
    if (view.visual === 'queue') {
      mechanism.append(block('这次到达', view.before, 'iw-arrival'));
      const lanes = node('div', 'iw-queue-lanes');
      lanes.append(block('执行 / 收件位置', view.after.filter(t => !t.includes('排队') && !t.includes('等待')), 'iw-executing'),
        block('等待队列', view.after.filter(t => t.includes('排队') || t.includes('等待')), 'iw-waiting'));
      mechanism.append(lanes);
    } else if (view.visual === 'stream') {
      mechanism.append(block('恢复位置', view.before));
      const rail = node('ol', 'iw-event-rail');
      for (const text of view.after) rail.append(node('li', text.includes('检查点') ? 'iw-checkpoint' : 'iw-event', text));
      mechanism.append(rail);
    } else if (schema.instruction === 'UpdateSharedState') {
      mechanism.append(block('同一次比较交换', view.before, 'iw-values'));
      const decision = node('div', 'iw-comparison', view.result ? view.result.matched ? '匹配' : '不匹配' : '比较尚未发生');
      decision.dataset.matched = String(view.result?.matched ?? 'pending');
      decision.append(node('p', '', view.result ? `matched: ${view.result.matched} · changed: ${view.result.changed}` : '只有相等才写入'));
      mechanism.append(decision, block('状态单元', view.after, 'iw-cell'));
    } else if (schema.instruction === 'CreateWire') {
      mechanism.append(block('选定关系', view.before));
      const diagram = node('div', 'iw-wire-diagram');
      diagram.append(node('div', 'iw-wire-node', view.variant === 'watch' ? '观察事件源' : view.variant === 'target' ? 'Effect' : '输出节点'));
      diagram.append(node('div', `iw-wire-edge iw-edge-${view.variant}`, state.step ? view.variant : '尚未创建'));
      diagram.append(node('div', 'iw-wire-node', view.variant === 'route' ? '接收节点' : '被引用对象'));
      diagram.dataset.revealed = String(!!state.step); mechanism.append(diagram);
    } else if (view.visual === 'graph') {
      mechanism.append(block('原组成', view.before, 'iw-origin-graph'));
      const group = block(schema.instruction === 'Familize' ? 'Family 的协作职责' : '组织关系', view.after, 'iw-object-group');
      mechanism.append(group);
    } else if (view.visual === 'routing' || view.visual === 'stages') {
      mechanism.append(block(view.visual === 'routing' ? '查找记录' : '本例请求', view.before));
      const stages = node('ol', 'iw-stage-track');
      view.after.forEach((text, index) => { const item = node('li'); item.append(node('b', '', index + 1), node('span', '', text)); stages.append(item); });
      mechanism.append(stages);
    } else if (view.visual === 'receipt') {
      mechanism.append(block('读取请求', view.before), block('服务回执', view.after, 'iw-paper-receipt'));
    } else if (view.visual === 'thread') {
      mechanism.append(block('帖子原状态', view.before));
      const thread = node('div', 'iw-discussion-thread');
      view.after.forEach((text, index) => thread.append(node('p', index === 1 ? 'iw-reply' : 'iw-post', text))); mechanism.append(thread);
    } else if (view.visual === 'relation') {
      mechanism.append(block('签发关系', view.before), block('凭证连接', view.after, 'iw-credential'));
    } else {
      mechanism.append(block('之前', view.before, 'iw-before'), node('div', 'iw-causal-arrow', '→'), block('之后', view.after, 'iw-after'));
    }
    return mechanism;
  }
  render(); return api;
}
