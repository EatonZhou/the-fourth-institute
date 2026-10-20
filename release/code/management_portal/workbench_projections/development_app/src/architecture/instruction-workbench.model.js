import { schemaMatches } from '../shared/format-tags.js';
import {lessons, categoryNotes} from './instruction-workbench.content.js';

export const CONTENT_SCHEMA = 'tfi.hatg-instruction-workbench.v1';
const clone = value => JSON.parse(JSON.stringify(value));
const ownerTitles = {web: 'Portal Web', lineage: 'SocietyService', resource: 'ResourceService', execution: 'ExecutionService', entry: '入口本地'};
export function readContent(content) {
  if (!schemaMatches(content?.schema, CONTENT_SCHEMA) || !Array.isArray(content.catalog?.instructions) || !content.catalog?.schemas
    || !content.provenance?.catalog_ref) throw new Error('指令目录不完整');
  const data = clone(content), names = new Set(), categories = new Map();
  if (!data.catalog.instructions.length) throw new Error('指令目录为空');
  for (const entry of data.catalog.instructions) {
    if (!entry?.instruction || names.has(entry.instruction)) throw new Error('指令身份为空或重复');
    names.add(entry.instruction);
    const schema = data.catalog.schemas[entry.instruction];
    for (const key of ['instruction', 'category', 'owner', 'description', 'mutates', 'streaming']) {
      if (schema?.[key] !== entry[key]) throw new Error(`${entry.instruction} 的目录与定义不属于同一批次`);
    }
    if (!entry.category || !entry.owner || typeof entry.mutates !== 'boolean' || typeof entry.streaming !== 'boolean'
      || !Array.isArray(schema.parameters) || !Array.isArray(schema.result)) throw new Error(`${entry.instruction} 定义不完整`);
    if (!categories.has(entry.category)) categories.set(entry.category, {id: entry.category, title: entry.category_title || entry.category, count: 0, note: categoryNotes[entry.category] || '查看这类对象的正式指令。'});
    categories.get(entry.category).count++;
  }
  return {...data, categories: [...categories.values()]};
}

export function purposeSummary(description) {
  const sentence = String(description || '').split(/[。\n]/, 1)[0].trim();
  const chars = [...sentence];
  return chars.length > 72 ? `${chars.slice(0, 71).join('')}…` : sentence;
}

export function visibleInstructions(data, state) {
  const q = String(state.query || '').toLocaleLowerCase().trim();
  const matches = data.catalog.instructions.filter(item => {
    const schema = data.catalog.schemas[item.instruction];
    return (!state.category || state.category === item.category)
      && (!state.mode || state.mode === (item.streaming ? 'stream' : item.mutates ? 'write' : 'read'))
      && (!q || [item.instruction, item.description, item.category_title, item.owner_title, schema.target,
        ...schema.parameters.map(p => `${p.name} ${p.description || ''}`)].join(' ').toLocaleLowerCase().includes(q));
  });
  if (!q) return matches;
  const rank = item => {
    const name = item.instruction.toLocaleLowerCase();
    return name === q ? 0 : name.startsWith(q) ? 1 : name.includes(q) ? 2 : 3;
  };
  return matches.sort((a, b) => rank(a) - rank(b));
}

export function restoreState(data, value = {}) {
  const state = {category: data.categories.some(c => c.id === value.category) ? value.category : '',
    query: typeof value.query === 'string' ? value.query.slice(0, 240) : '', mode: ['read', 'write', 'stream'].includes(value.mode) ? value.mode : '',
    instruction: '', variant: '', step: value.step === 1 ? 1 : 0};
  const shown = visibleInstructions(data, state);
  state.instruction = shown.some(i => i.instruction === value.instruction) ? value.instruction : shown[0]?.instruction || '';
  const variants = lessons[state.instruction]?.variants || [];
  state.variant = variants.some(v => v.id === value.variant) ? value.variant : variants[0]?.id || '';
  return state;
}

export function declarationOwners(schema) {
  const overrides = (schema.argument_owners || []).map(({argument, owner}) =>
    ({owner, title: ownerTitles[owner] || owner, condition: `带 ${argument}`}));
  return [...overrides, {owner: schema.owner, title: schema.owner_title || ownerTitles[schema.owner] || schema.owner,
    condition: overrides.length ? '其他合法参数组合' : ''}];
}

export function declarationExplanation(schema) {
  const owners = declarationOwners(schema);
  return {
    target: schema.target ? `由参数 ${schema.target} 定位主对象；具体是否必填以参数约定为准。` : '没有单一目标参数；作用范围由参数组合和指令领域决定。',
    route: schema.instruction === 'GetInstructionExecution' ? '入口根据执行 ID 中的服务标记，把查询送回保存记录的原服务。'
      : schema.owner === 'entry' ? '入口直接读取自描述目录，不转发到业务拥有服务。'
        : owners.length > 1 ? `入口保留真实调用者，按参数选择服务：${owners.map(value => `${value.condition}时交给 ${value.title}`).join('；')}。`
          : `入口保留真实调用者，把请求交给 ${schema.owner_title || schema.owner} 的指令内核。`,
    effect: schema.streaming ? '持续提供增量事件与游标；停止观察不等于取消被观察的工作。'
      : schema.mutates ? '此指令声明会改变状态。真实调用受领域前置条件和当前权限约束；本地演示不会执行。'
        : '只读当前信息，不改变业务状态，也不保存修改指令的恢复记录。',
  };
}

export function lessonFor(name) {
  return lessons[name] || null;
}
export function representativeFor(data, category) {
  return data.catalog.instructions.find(i => i.category === category && lessonFor(i.instruction, data.provenance))?.instruction || '';
}
export function sourceForLesson(name) {
  const source = lessons[name]?.source;
  return source ? `repo_5d287fed9c7e/file/${source}` : '';
}

// Only a local teaching view. No result is presented as an actual ISA receipt.
export function experiment(name, variant, reveal = false) {
  const lesson = lessons[name];
  const chosen = lesson?.variants.find(v => v.id === variant);
  if (!chosen) return null;
  return {status: 'simulated', visual: lesson.visual, question: lesson.question, meaning: lesson.meaning,
    variant: chosen.id, label: chosen.label, before: [...chosen.before], after: reveal ? [...chosen.after] : [],
    result: reveal ? clone(chosen.result) : null, explanation: reveal ? chosen.explanation : '选择条件，再观察它怎样改变本例的结果。'};
}
